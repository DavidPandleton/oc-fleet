"""Adversarial probes for orchestrator.py. Not part of the shipped suite.

Each test here documents a DEFECT that was reproduced before the fix and must
still fail-closed after it. They are written as regression tests so the fixes
cannot silently rot.
"""
from __future__ import annotations

import http.client
import time
import unittest

from orchestrator import Orchestrator, Task


class FlakyStatusFleet:
    """Dispatch works, but status() raises a non-OSError HTTP client error."""

    def __init__(self, error=None):
        self.error = error or http.client.IncompleteRead(b"partial")
        self.n = 0

    def dispatch(self, prompt, workdir, title="", model=""):
        self.n += 1
        return "s%d" % self.n

    def status(self, sid):
        raise self.error


class LateSuccessFleet:
    """First poll reports still-running, second poll reports success."""

    def __init__(self):
        self.polls = 0

    def dispatch(self, prompt, workdir, title="", model=""):
        return "s1"

    def status(self, sid):
        self.polls += 1
        if self.polls == 1:
            return {"outcome": None, "last_assistant_text": ""}
        return {"outcome": "succeeded", "last_assistant_text": "SUCCESS-REAL"}


class DispatchErrorFleet:
    """dispatch() always raises, so no session id is ever produced."""

    def dispatch(self, prompt, workdir, title="", model=""):
        raise ConnectionError("network down")

    def status(self, sid):
        return {"outcome": "succeeded", "last_assistant_text": "unused"}


class TestStatusFailuresDoNotCrash(unittest.TestCase):
    """A single bad poll must not abandon the whole run."""

    def test_http_client_exception_does_not_crash_run(self):
        o = Orchestrator(fleet=FlakyStatusFleet(), max_parallel=2, poll_interval=0.01)
        o.add(Task(id="a", prompt="x", timeout=0.3))
        o.add(Task(id="b", prompt="x", timeout=0.3))
        try:
            o.run()
        except Exception as exc:  # noqa: BLE001 - this is exactly what we guard
            self.fail("run() crashed on a bad poll: %r" % (exc,))
        states = {k: v["status"] for k, v in o.results().items()}
        self.assertNotIn("running", states.values(), states)

    def test_bad_status_leaves_task_terminal_not_running(self):
        o = Orchestrator(fleet=FlakyStatusFleet(), max_parallel=1, poll_interval=0.01)
        o.add(Task(id="a", prompt="x", timeout=0.3))
        o.run()
        self.assertIn(o.results()["a"]["status"], ("failed", "succeeded", "skipped"))

    def test_unexpected_exception_type_also_survives(self):
        class Weird(Exception):
            pass

        o = Orchestrator(fleet=FlakyStatusFleet(Weird("boom")), max_parallel=1,
                         poll_interval=0.01)
        o.add(Task(id="a", prompt="x", timeout=0.3))
        try:
            o.run()
        except Exception as exc:  # noqa: BLE001
            self.fail("run() crashed on an unexpected exception: %r" % (exc,))
        self.assertNotEqual(o.results()["a"]["status"], "running")


class TestTimeoutDoesNotDiscardSuccess(unittest.TestCase):
    """A session that already succeeded must never be recorded as timed out."""

    def test_success_on_the_expiring_poll_is_kept(self):
        fleet = LateSuccessFleet()
        o = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.25)
        o.add(Task(id="t", prompt="x", timeout=0.2))
        o.run()
        rec = o.results()["t"]
        self.assertEqual(
            rec["status"], "succeeded",
            "a completed session was recorded as %r (%s)" % (
                rec["status"], rec["last_text"]),
        )
        self.assertEqual(rec["last_text"], "SUCCESS-REAL")


class TestDispatchFailureReporting(unittest.TestCase):
    """A dispatch that raises must not be reported as a timeout."""

    def test_no_timed_out_message_when_dispatch_failed(self):
        o = Orchestrator(fleet=DispatchErrorFleet(), max_parallel=1, poll_interval=0.01)
        o.add(Task(id="t", prompt="x", retries=1))
        o.run()
        rec = o.results()["t"]
        self.assertEqual(rec["status"], "failed")
        self.assertNotIn("timed out", rec["last_text"])
        self.assertIn("network down", rec["last_text"])

    def test_dispatch_failure_is_not_counted_as_elapsed_time(self):
        o = Orchestrator(fleet=DispatchErrorFleet(), max_parallel=1, poll_interval=0.01)
        o.add(Task(id="t", prompt="x"))
        start = time.monotonic()
        o.run()
        wall = time.monotonic() - start
        self.assertLess(wall, 5.0, "dispatch failure blocked the loop")
        # The reported duration must not claim the full task timeout elapsed.
        self.assertLess(o.results()["t"].get("duration", 0.0), 5.0)


if __name__ == "__main__":
    unittest.main()