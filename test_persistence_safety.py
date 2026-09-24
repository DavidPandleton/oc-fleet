"""Persistence safety regression tests."""

import tempfile
import unittest

from orchestrator import Orchestrator, Task
from store import RunStore


class PersistenceSafetyTest(unittest.TestCase):
    def test_unfinished_pending_tasks_are_not_persisted_as_completed(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            store = RunStore(handle.name)
            orchestration = Orchestrator(
                fleet=None, max_parallel=1, poll_interval=0.01,
                store=store, run_id="run-deadlock",
            )
            # Bypass add's early dependency check to exercise the run guard.
            orchestration._tasks["blocked"] = Task(
                id="blocked", prompt="x", workdir="/tmp", depends_on=["waiting"]
            )
            orchestration._tasks["waiting"] = Task(
                id="waiting", prompt="x", workdir="/tmp", depends_on=["blocked"]
            )
            orchestration._task_order.extend(["blocked", "waiting"])
            orchestration._results["blocked"] = {
                "status": "pending", "session_id": None, "outcome": None,
                "attempts": 0, "last_text": None, "started_at": None,
                "finished_at": None, "duration": None,
                "verification": {"required": False, "passed": None, "commands": []},
                "artifacts": None, "failure_class": None, "attempts_detail": [],
            }
            orchestration._results["waiting"] = {
                "status": "pending", "session_id": None, "outcome": None,
                "attempts": 0, "last_text": None, "started_at": None,
                "finished_at": None, "duration": None,
                "verification": {"required": False, "passed": None, "commands": []},
                "artifacts": None, "failure_class": None, "attempts_detail": [],
            }
            orchestration.validate = lambda: None
            with self.assertRaises(RuntimeError):
                orchestration.run()
            self.assertEqual(store.get_run("run-deadlock")["status"], "aborted")


if __name__ == "__main__":
    unittest.main()
