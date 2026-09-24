"""Regression tests for verification failures blocking dependents."""

import sys
import unittest

from orchestrator import Orchestrator, Task
from test_orchestrator import FakeFleet, fake_clock


class VerificationDependencyTest(unittest.TestCase):
    def test_verification_failed_skips_dependent(self):
        fleet = FakeFleet()
        fleet.set_outcome("s-0", "succeeded")
        orchestration = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
        orchestration.add(Task(
            id="build",
            prompt="build",
            workdir="/tmp",
            verify=[f"{sys.executable} -c 'import sys; sys.exit(2)'"],
        ))
        orchestration.add(Task(
            id="docs",
            prompt="docs",
            workdir="/tmp",
            depends_on=["build"],
        ))
        with fake_clock():
            result = orchestration.run()
        self.assertEqual(result["build"]["status"], "verification_failed")
        self.assertEqual(result["docs"]["status"], "skipped")
        self.assertEqual(len(fleet.dispatched), 1)


if __name__ == "__main__":
    unittest.main()
