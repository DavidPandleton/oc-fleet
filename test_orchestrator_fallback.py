"""Tests for per-task model fallback on retry (no server needed)."""

import unittest

from orchestrator import Orchestrator, Task
from test_orchestrator import FakeFleet, fake_clock


def run_until_done(o):
    with fake_clock():
        o.poll_interval = 0.01
        return o.run()


class FallbackModelTest(unittest.TestCase):
    def test_task_menerima_fallbacks(self):
        t = Task(id="a", prompt="x", workdir="/tmp", model="cutad/model-a",
                 fallbacks=["cutad/model-b"])
        self.assertEqual(t.fallbacks, ["cutad/model-b"])

    def test_default_fallbacks_kosong(self):
        t = Task(id="a", prompt="x", workdir="/tmp")
        self.assertEqual(t.fallbacks, [])

    def test_retry_memakai_model_fallback(self):
        fleet = FakeFleet()
        o = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
        o.add(Task(id="a", prompt="x", workdir="/tmp",
                   model="cutad/model-a",
                   fallbacks=["cutad/model-b"],
                   retries=1, timeout=10))
        # attempt 1 gagal, attempt 2 sukses
        orig_status = fleet.status

        def scripted_status(session_id):
            idx = fleet._next_idx.get(session_id, 0)
            # sesi pertama (s-0) selalu gagal, sesi kedua (s-1) sukses
            if session_id == "s-0":
                fleet._finished.add(session_id)
                return {"outcome": "failed", "last_assistant_text": "provider error"}
            return orig_status(session_id)

        fleet.status = scripted_status
        fleet.set_outcome("s-1", "succeeded")
        with fake_clock():
            o.run()
        models = [d["model"] for d in fleet.dispatched]
        self.assertEqual(models, ["cutad/model-a", "cutad/model-b"])
        self.assertEqual(o.results()["a"]["status"], "succeeded")

    def test_tanpa_fallback_retry_model_sama(self):
        fleet = FakeFleet()
        o = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
        o.add(Task(id="a", prompt="x", workdir="/tmp",
                   model="cutad/model-a", retries=1, timeout=10))
        fleet.set_outcome("s-0", "failed")
        fleet.set_outcome("s-1", "succeeded")
        with fake_clock():
            o.run()
        models = [d["model"] for d in fleet.dispatched]
        self.assertEqual(models, ["cutad/model-a", "cutad/model-a"])


if __name__ == "__main__":
    unittest.main()
