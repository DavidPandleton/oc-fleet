"""Integration tests for verification status in the orchestrator lifecycle."""

import sys
import unittest

from orchestrator import Orchestrator, Task
from test_orchestrator import FakeFleet, fake_clock


class VerificationLifecycleTest(unittest.TestCase):
    def _run(self, task, fleet):
        orchestration = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
        orchestration.add(task)
        with fake_clock():
            orchestration.run()
        return orchestration.results()[task.id]

    def test_agent_sukses_dan_verifier_lulus(self):
        fleet = FakeFleet()
        fleet.set_outcome("s-0", "succeeded")
        result = self._run(
            Task(
                id="a",
                prompt="build",
                workdir="/tmp",
                verify=[f"{sys.executable} -c 'print(\"ok\")'"],
            ),
            fleet,
        )
        self.assertEqual(result["status"], "verification_passed")
        self.assertEqual(result["outcome"], "succeeded")
        self.assertTrue(result["verification"]["passed"])

    def test_agent_sukses_verifier_gagal(self):
        fleet = FakeFleet()
        fleet.set_outcome("s-0", "succeeded")
        result = self._run(
            Task(
                id="a",
                prompt="build",
                workdir="/tmp",
                verify=[f"{sys.executable} -c 'import sys; sys.exit(4)'"],
            ),
            fleet,
        )
        self.assertEqual(result["status"], "verification_failed")
        self.assertEqual(result["outcome"], "succeeded")
        self.assertFalse(result["verification"]["passed"])
        self.assertEqual(result["verification"]["commands"][0]["returncode"], 4)

    def test_task_tanpa_verifier_tetap_backward_compatible(self):
        fleet = FakeFleet()
        fleet.set_outcome("s-0", "succeeded")
        result = self._run(Task(id="a", prompt="build", workdir="/tmp"), fleet)
        self.assertEqual(result["status"], "succeeded")
        self.assertFalse(result["verification"]["required"])


if __name__ == "__main__":
    unittest.main()
