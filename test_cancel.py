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

import email.message
import io
import unittest
import urllib.error
from unittest import mock

from fleet import Fleet
from orchestrator import Orchestrator, Task


class CancelRecordingFleet:
    """Dispatch always succeeds; every attempt times out; records cancels."""

    def __init__(self):
        self.dispatched = []
        self.cancelled = []
        self.cancel_fails = False
        # Verdict yang dikembalikan cancel(). True/False/None.
        self.cancel_verdict = True

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
        return self.cancel_verdict


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


class CancelVerdictWarningTest(unittest.TestCase):
    """cancel() mengembalikan None -> orchestrator harus memperingatkan.

    Ini inti perbaikan: sebelum ini 'server mati' dan 'sesi sudah selesai'
    sama-sama diam, sehingga retry bisa jalan berdampingan dengan sesi yang
    tidak pernah berhasil dihentikan.
    """

    def _run_dengan_verdict(self, verdict):
        """Jalankan satu task yang timeout, dengan cancel mengembalikan verdict."""
        fleet = CancelRecordingFleet()
        fleet.cancel_verdict = verdict
        o = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
        o.add(Task(id="t", prompt="x", retries=1, timeout=0.05))
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            o.run()
        return out.getvalue()

    def test_none_memunculkan_peringatan(self):
        keluaran = self._run_dengan_verdict(None)
        self.assertIn("could not confirm session", keluaran)
        self.assertIn("retry may run alongside", keluaran)

    def test_false_tidak_memunculkan_peringatan(self):
        """Sesi yang sudah selesai sendiri bukan masalah - jangan berisik."""
        keluaran = self._run_dengan_verdict(False)
        self.assertNotIn("could not confirm session", keluaran)

    def test_true_mencetak_stopped(self):
        keluaran = self._run_dengan_verdict(True)
        self.assertIn("stopped:", keluaran)
        self.assertNotIn("could not confirm session", keluaran)


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

    def test_cancel_returns_none_for_empty_session_id(self):
        """Tidak ada session id berarti tidak ada keputusan - None, bukan False."""
        self.assertIsNone(Fleet().cancel(None))
        self.assertIsNone(Fleet().cancel(""))

    def test_cancel_returns_true_on_interrupted_true(self):
        fleet = Fleet()
        fleet._request = lambda *a, **k: {"interrupted": True}
        self.assertTrue(fleet.cancel("s1"))

    def test_cancel_returns_false_when_server_declines(self):
        """False tetap berarti 'tidak ada yang perlu dihentikan'."""
        fleet = Fleet()
        fleet._request = lambda *a, **k: {"interrupted": False}
        self.assertFalse(fleet.cancel("s1"))

    def test_cancel_returns_none_when_request_fails(self):
        """Kegagalan koneksi bukan 'sudah selesai' - None."""
        fleet = Fleet()

        def boom(*a, **k):
            raise ConnectionError("down")

        fleet._request = boom
        self.assertIsNone(fleet.cancel("s1"))

    def test_cancel_returns_none_on_404(self):
        """Sesi tidak ada adalah kegagalan, bukan 'sudah selesai'."""
        fleet = Fleet()

        def not_found(*a, **k):
            raise urllib.error.HTTPError("u", 404, "Not Found", email.message.Message(), None)

        fleet._request = not_found
        self.assertIsNone(fleet.cancel("s1"))


if __name__ == "__main__":
    unittest.main()