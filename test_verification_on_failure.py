"""Verification must run even when the agent fails or times out.

From the coffee-catalog run: the QA agent timed out, so verification was
never executed, yet an independent build+test pass was possible all along.
A failed agent is evidence about the agent, not about the artifact.
"""

import os
import sys
import tempfile
import unittest

from orchestrator import Orchestrator, Task
from test_orchestrator import FakeFleet, fake_clock


class VerificationAfterAgentFailureTest(unittest.TestCase):
    def _script(self, workdir, name, exit_code):
        path = os.path.join(workdir, name)
        with open(path, "w") as handle:
            handle.write("import sys; sys.exit(%d)\n" % exit_code)
        return "%s %s" % (sys.executable, path)

    def test_verification_runs_after_agent_failure(self):
        with tempfile.TemporaryDirectory() as workdir:
            check = self._script(workdir, "check.py", 0)
            record = os.path.join(workdir, "ran.txt")
            witness = os.path.join(workdir, "witness.py")
            with open(witness, "w") as handle:
                handle.write(
                    "open(%r, 'w').write('yes')\n" % record
                )
            fleet = FakeFleet()
            fleet.set_outcome("s-0", "failed")
            orchestration = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
            orchestration.add(Task(
                id="a", prompt="build", workdir=workdir,
                verify=["%s %s" % (sys.executable, witness)],
            ))
            with fake_clock():
                result = orchestration.run()["a"]
            self.assertEqual(result["status"], "failed")
            self.assertTrue(
                os.path.exists(record),
                "verification did not run after agent failure",
            )
            self.assertTrue(result["verification"]["required"])

    def test_verification_runs_after_agent_timeout(self):
        with tempfile.TemporaryDirectory() as workdir:
            witness = os.path.join(workdir, "witness.py")
            record = os.path.join(workdir, "ran.txt")
            with open(witness, "w") as handle:
                handle.write("open(%r, 'w').write('yes')\n" % record)
            fleet = FakeFleet()
            fleet.set_outcome("s-0", None)  # never completes -> timeout
            orchestration = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
            orchestration.add(Task(
                id="a", prompt="build", workdir=workdir, timeout=0.05,
                verify=["%s %s" % (sys.executable, witness)],
            ))
            with fake_clock():
                result = orchestration.run()["a"]
            self.assertEqual(result["status"], "failed")
            self.assertTrue(
                os.path.exists(record),
                "verification did not run after agent timeout",
            )

    def test_passing_verification_is_recorded_even_when_agent_fails(self):
        with tempfile.TemporaryDirectory() as workdir:
            good = self._script(workdir, "ok.py", 0)
            fleet = FakeFleet()
            fleet.set_outcome("s-0", "failed")
            orchestration = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
            orchestration.add(Task(
                id="a", prompt="build", workdir=workdir, verify=[good],
            ))
            with fake_clock():
                result = orchestration.run()["a"]
            # The agent failed, but the artifact verifies: both facts must be
            # visible, so the split status fields have to survive.
            self.assertEqual(result["status"], "failed")
            self.assertTrue(result["verification"]["passed"])
            self.assertEqual(result["agent_status"], "failed")
            self.assertEqual(result["verification_status"], "passed")


    def test_artifact_manifest_collected_when_agent_fails(self):
        with tempfile.TemporaryDirectory() as workdir:
            import subprocess
            subprocess.run(["git", "init", "-q"], cwd=workdir, check=True)
            with open(os.path.join(workdir, "artifact.txt"), "w") as handle:
                handle.write("evidence\n")
            fleet = FakeFleet()
            fleet.set_outcome("s-0", "failed")
            orchestration = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
            orchestration.add(Task(
                id="a", prompt="build", workdir=workdir,
                verify=[self._script(workdir, "ok.py", 0)],
            ))
            with fake_clock():
                result = orchestration.run()["a"]
            self.assertEqual(result["status"], "failed")
            self.assertIsNotNone(result["artifacts"])
            self.assertIn("artifact.txt", result["artifacts"].get("files_changed", []))


if __name__ == "__main__":
    unittest.main()
