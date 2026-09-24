"""Artifact evidence integration tests."""

import os
import tempfile
import unittest

from orchestrator import Orchestrator, Task
from test_orchestrator import FakeFleet, fake_clock


class ArtifactLifecycleTest(unittest.TestCase):
    def test_result_contains_changed_file_manifest(self):
        repo = tempfile.mkdtemp(prefix="oc-fleet-artifact-run-")
        subprocess_import = __import__("subprocess")
        subprocess_import.run(["git", "init", "-q", repo], check=True)
        subprocess_import.run(["git", "-C", repo, "config", "user.email", "x@y.z"], check=True)
        subprocess_import.run(["git", "-C", repo, "config", "user.name", "Test"], check=True)
        with open(os.path.join(repo, "base.txt"), "w") as handle:
            handle.write("base\n")
        subprocess_import.run(["git", "-C", repo, "add", "."], check=True)
        subprocess_import.run(["git", "-C", repo, "commit", "-qm", "base"], check=True)
        with open(os.path.join(repo, "changed.txt"), "w") as handle:
            handle.write("changed\n")

        fleet = FakeFleet()
        fleet.set_outcome("s-0", "succeeded")
        orchestration = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
        orchestration.add(Task(id="a", prompt="report", workdir=repo))
        with fake_clock():
            result = orchestration.run()["a"]
        self.assertIn("changed.txt", result["artifacts"]["files_changed"])
        self.assertFalse(result["artifacts"]["clean"])


if __name__ == "__main__":
    unittest.main()
