"""Tests for the minor findings in review.md that were not defects.

Each one is a small honesty or robustness fix. They are grouped here because
they share a theme: the orchestrator used to absorb caller mistakes and
transient noise silently, which made runs harder to explain after the fact.
"""
from __future__ import annotations

import unittest

from orchestrator import Orchestrator, Task


class NeverFinishingFleet:
    def __init__(self):
        self.n = 0

    def dispatch(self, prompt, workdir, title="", model=""):
        self.n += 1
        return "s%d" % self.n

    def status(self, sid):
        return {"outcome": None, "last_assistant_text": "working"}


class MaxParallelValidationTest(unittest.TestCase):
    def test_zero_is_rejected_not_clamped(self):
        """Silent clamping hid a caller bug: 0 looked accepted, ran serial."""
        with self.assertRaises(ValueError) as ctx:
            Orchestrator(max_parallel=0)
        self.assertIn("max_parallel", str(ctx.exception))

    def test_negative_is_rejected(self):
        with self.assertRaises(ValueError):
            Orchestrator(max_parallel=-3)

    def test_one_is_allowed(self):
        self.assertEqual(Orchestrator(max_parallel=1).max_parallel, 1)

    def test_valid_int_still_coerced(self):
        self.assertEqual(Orchestrator(max_parallel="2").max_parallel, 2)


class ResultsIsolationTest(unittest.TestCase):
    def test_mutating_results_does_not_corrupt_state(self):
        """results() used to hand back the live dict."""
        o = Orchestrator(fleet=NeverFinishingFleet(), max_parallel=1, poll_interval=0.01)
        o.add(Task(id="a", prompt="x", timeout=0.05))
        o.run()
        got = o.results()
        got["a"]["status"] = "succeeded"
        got["a"]["session_id"] = "tampered"
        again = o.results()
        self.assertNotEqual(again["a"]["status"], "succeeded")
        self.assertNotEqual(again["a"]["session_id"], "tampered")

    def test_adding_a_key_to_results_does_not_leak(self):
        o = Orchestrator(fleet=NeverFinishingFleet(), max_parallel=1, poll_interval=0.01)
        o.add(Task(id="a", prompt="x", timeout=0.05))
        o.run()
        o.results()["injected"] = {"status": "succeeded"}
        self.assertNotIn("injected", o.results())


class TransientNoneStatusTest(unittest.TestCase):
    """A None poll response must not wipe the cached assistant text."""

    def test_none_status_keeps_last_text(self):
        class FlakyThenDone:
            def __init__(self):
                self.n = 0
                self.calls = 0

            def dispatch(self, prompt, workdir, title="", model=""):
                self.n += 1
                return "s%d" % self.n

            def status(self, sid):
                self.calls += 1
                if self.calls == 1:
                    return {"outcome": None, "last_assistant_text": "partial work"}
                if self.calls == 2:
                    return None  # transient empty response
                return {"outcome": None, "last_assistant_text": None}

        o = Orchestrator(fleet=FlakyThenDone(), max_parallel=1, poll_interval=0.01)
        o.add(Task(id="a", prompt="x", timeout=0.08))
        o.run()
        # "partial work" was seen, then a None poll, then a deadline timeout.
        self.assertIn("partial work", o.results()["a"]["last_text"])


class PendingInsertIndexTest(unittest.TestCase):
    """A retried task must keep its place in the declared order."""

    def test_retry_preserves_task_order(self):
        class FailOnce:
            """Session ids come from a counter, not the prompt.

            Binding a session id to the prompt text made this test break
            the moment a retry carried context (the prompt is no longer
            the single byte it used to be). Keying on a counter keeps the
            test about task ORDER, which is what it claims to check.
            """

            def __init__(self):
                self.n = 0

            def dispatch(self, prompt, workdir, title="", model=""):
                self.n += 1
                self.last = "s%d" % self.n
                return self.last

            def status(self, sid):
                # The first attempt of task "a" fails; everything else succeeds.
                if sid == "s1":
                    return {"outcome": "crashed", "last_assistant_text": "boom"}
                return {"outcome": "succeeded", "last_assistant_text": "ok"}

        o = Orchestrator(fleet=FailOnce(), max_parallel=1, poll_interval=0.01)
        o.add(Task(id="a", prompt="a", retries=1, timeout=1))
        o.add(Task(id="b", prompt="b", timeout=1))
        o.run()
        res = o.results()
        self.assertEqual(res["a"]["status"], "succeeded")
        self.assertEqual(res["a"]["attempts"], 2)
        self.assertEqual(res["b"]["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()