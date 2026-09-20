"""Tests for session cancellation on timeout and retry.

The defect these pin down: the orchestrator abandoned a timed-out session and
immediately retried without stopping the old one. The abandoned session kept
its fleet slot and kept writing to the shared workdir while the retry wrote to
the same files, so a "retry" could be two concurrent writers.

Endpoint verified against a live server before this was written:
POST /api/session/{id}/interrupt returns {"interrupted": true} and sets the
session outcome to "interrupted".
"""
from __future__ import annotations

import unittest

from fleet import Fleet
from orchestrator import Orchestrator, Task


class CancelRecordingFleet:
    """Dispatch always succeeds; every attempt times out; records cancels."""

    def __init__(self):
        self.dispatched = []
        self.cancelled = []
        self.cancel_fails = False

    def dispatch(self, prompt, workdir, title="", model=""):
        sid = "s%d" % (len(self.dispatched) + 1)
        self.dispatched.append(sid)
        return sid

    def status(self, sid):
        return {"outcome": None, "last_assistant_text": "working"}

    def cancel(self, sid):
        if self.cancel_fails:
            raise ConnectionError("cancel endpoint unreachable")
        self.cancelled.append(sid)
        return True


class NoCancelFleet:
    """A fleet that predates the cancel API. Must not break the orchestrator."""

    def __init__(self):
        self.n = 0

    def dispatch(self, prompt, workdir, title="", model=""):
        self.n += 1
        return "s%d" % self.n

    def status(self, sid):
        return {"outcome": None, "last_assistant_text": "working"}


class CancelOnRetryTest(unittest.TestCase):
    def test_session_is_cancelled_before_retry(self):
        fleet = CancelRecordingFleet()
        o = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
        o.add(Task(id="t", prompt="x", retries=1, timeout=0.05))
        o.run()
        # Two attempts means one retry, so exactly the first session is cancelled.
        self.assertEqual(len(fleet.dispatched), 2)
        self.assertEqual(fleet.cancelled, [fleet.dispatched[0]])

    def test_every_abandoned_session_is_cancelled(self):
        fleet = CancelRecordingFleet()
        o = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
        o.add(Task(id="t", prompt="x", retries=2, timeout=0.05))
        o.run()
        self.assertEqual(len(fleet.dispatched), 3)
        # 3 attempts, 2 retries: the last attempt is never cancelled.
        self.assertEqual(fleet.cancelled, fleet.dispatched[:2])

    def test_no_cancel_when_attempt_succeeds(self):
        class SuccessFleet(CancelRecordingFleet):
            def status(self, sid):
                return {"outcome": "succeeded", "last_assistant_text": "ok"}

        fleet = SuccessFleet()
        o = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
        o.add(Task(id="t", prompt="x", retries=3, timeout=1))
        o.run()
        self.assertEqual(o.results()["t"]["status"], "succeeded")
        self.assertEqual(fleet.cancelled, [])


class CancelFailureToleranceTest(unittest.TestCase):
    def test_cancel_exception_does_not_crash_the_run(self):
        """A failing cancel must degrade to old behaviour, not take the run down."""
        fleet = CancelRecordingFleet()
        fleet.cancel_fails = True
        o = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
        o.add(Task(id="t", prompt="x", retries=1, timeout=0.05))
        try:
            o.run()
        except Exception as exc:  # noqa: BLE001
            self.fail("run() crashed when cancel raised: %r" % (exc,))
        self.assertEqual(o.results()["t"]["status"], "failed")

    def test_fleet_without_cancel_still_works(self):
        """Backward compatibility: no cancel attribute is not an error."""
        o = Orchestrator(fleet=NoCancelFleet(), max_parallel=1, poll_interval=0.01)
        o.add(Task(id="t", prompt="x", retries=1, timeout=0.05))
        o.run()
        self.assertEqual(o.results()["t"]["status"], "failed")
        self.assertEqual(o.results()["t"]["attempts"], 2)


class FleetCancelMethodTest(unittest.TestCase):
    """Fleet.cancel talks to the interrupt endpoint and never raises."""

    def test_cancel_returns_false_for_empty_session_id(self):
        self.assertFalse(Fleet().cancel(None))
        self.assertFalse(Fleet().cancel(""))

    def test_cancel_returns_true_on_interrupted_true(self):
        fleet = Fleet()
        fleet._request = lambda *a, **k: {"interrupted": True}
        self.assertTrue(fleet.cancel("s1"))

    def test_cancel_returns_false_when_server_declines(self):
        fleet = Fleet()
        fleet._request = lambda *a, **k: {"interrupted": False}
        self.assertFalse(fleet.cancel("s1"))

    def test_cancel_swallows_request_errors(self):
        fleet = Fleet()

        def boom(*a, **k):
            raise ConnectionError("down")

        fleet._request = boom
        self.assertFalse(fleet.cancel("s1"))


if __name__ == "__main__":
    unittest.main()