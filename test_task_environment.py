"""Tests for explicit per-task environment and lifecycle hooks."""

import os
import sys
import tempfile
import unittest

from orchestrator import Orchestrator, Task
from test_orchestrator import FakeFleet, fake_clock


class TaskEnvironmentTest(unittest.TestCase):
    def test_setup_environment_is_visible_to_agent_workdir(self):
        with tempfile.TemporaryDirectory() as workdir:
            setup = f"{sys.executable} -c 'open(\"setup.txt\", \"w\").write(\"done\")'"
            fleet = FakeFleet()
            fleet.set_outcome("s-0", "succeeded")
            orchestration = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
            orchestration.add(Task(
                id="a", prompt="build", workdir=workdir,
                env={"OC_FLEET_TEST": "yes"}, setup=[setup],
            ))
            with fake_clock():
                result = orchestration.run()["a"]
            self.assertEqual(result["status"], "succeeded")
            self.assertTrue(os.path.exists(os.path.join(workdir, "setup.txt")))

    def test_setup_failure_does_not_dispatch_agent(self):
        fleet = FakeFleet()
        orchestration = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
        orchestration.add(Task(
            id="a", prompt="build", workdir="/tmp",
            setup=[f"{sys.executable} -c 'import sys; sys.exit(9)'"],
        ))
        with fake_clock():
            result = orchestration.run()["a"]
        self.assertEqual(result["status"], "setup_failed")
        self.assertEqual(fleet.dispatched, [])


if __name__ == "__main__":
    unittest.main()
