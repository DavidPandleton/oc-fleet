"""Tests for task teardown hooks."""

import os
import sys
import tempfile
import unittest

from orchestrator import Orchestrator, Task
from test_orchestrator import FakeFleet, fake_clock


class TaskTeardownTest(unittest.TestCase):
    def test_teardown_runs_after_success(self):
        with tempfile.TemporaryDirectory() as workdir:
            marker = os.path.join(workdir, "teardown.txt")
            script = os.path.join(workdir, "teardown.py")
            with open(script, "w") as handle:
                handle.write("open(%r, 'w').write('done')\n" % marker)
            teardown = f"{sys.executable} {script}"
            fleet = FakeFleet()
            fleet.set_outcome("s-0", "succeeded")
            orchestration = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
            orchestration.add(Task(
                id="a", prompt="build", workdir=workdir, teardown=[teardown]
            ))
            with fake_clock():
                result = orchestration.run()["a"]
            self.assertEqual(result["status"], "succeeded")
            self.assertTrue(os.path.exists(marker))

    def test_teardown_failure_is_recorded_without_rewriting_success(self):
        fleet = FakeFleet()
        fleet.set_outcome("s-0", "succeeded")
        orchestration = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
        orchestration.add(Task(
            id="a", prompt="build", workdir="/tmp",
            teardown=[f"{sys.executable} -c 'import sys; sys.exit(8)'"],
        ))
        with fake_clock():
            result = orchestration.run()["a"]
        self.assertEqual(result["status"], "succeeded")
        self.assertIn("teardown", result["last_text"])


if __name__ == "__main__":
    unittest.main()
