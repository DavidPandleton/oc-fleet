"""Tests for orchestrator state persistence."""

import tempfile
import unittest

from orchestrator import Orchestrator, Task
from store import RunStore
from test_orchestrator import FakeFleet, fake_clock


class OrchestratorPersistenceTest(unittest.TestCase):
    def test_run_persists_final_task_result(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            store = RunStore(handle.name)
            fleet = FakeFleet()
            fleet.set_outcome("s-0", "succeeded")
            orchestration = Orchestrator(
                fleet=fleet, max_parallel=1, poll_interval=0.01,
                store=store, run_id="run-1",
            )
            orchestration.add(Task(id="build", prompt="build", workdir="/tmp"))
            with fake_clock():
                orchestration.run()
            saved = store.get_task("run-1", "build")
            self.assertEqual(saved["status"], "succeeded")
            self.assertEqual(store.get_run("run-1")["status"], "completed")

    def test_dispatch_and_finish_events_are_persisted(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            store = RunStore(handle.name)
            fleet = FakeFleet()
            fleet.set_outcome("s-0", "succeeded")
            orchestration = Orchestrator(
                fleet=fleet, max_parallel=1, poll_interval=0.01,
                store=store, run_id="run-2",
            )
            orchestration.add(Task(id="build", prompt="build", workdir="/tmp"))
            with fake_clock():
                orchestration.run()
            events = store.list_events("run-2")
            self.assertIn("task_started", [event["event_type"] for event in events])
            self.assertIn("task_finished", [event["event_type"] for event in events])


if __name__ == "__main__":
    unittest.main()
