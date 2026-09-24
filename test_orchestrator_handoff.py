"""Tests for bounded typed handoff from dependencies."""

import os
import subprocess
import tempfile
import unittest

from orchestrator import Orchestrator, Task
from test_orchestrator import FakeFleet, fake_clock


class HandoffTest(unittest.TestCase):
    def test_dependent_task_menerima_manifest_upstream(self):
        repo = tempfile.mkdtemp(prefix="oc-fleet-handoff-")
        subprocess.run(["git", "init", "-q", repo], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.email", "x@y.z"], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.name", "Test"], check=True)
        with open(os.path.join(repo, "base.txt"), "w") as handle:
            handle.write("base\n")
        subprocess.run(["git", "-C", repo, "add", "."], check=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "base"], check=True)
        with open(os.path.join(repo, "artifact.txt"), "w") as handle:
            handle.write("artifact\n")

        fleet = FakeFleet()
        fleet.set_outcome("s-0", "succeeded")
        fleet.set_outcome("s-1", "succeeded")
        orchestration = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
        orchestration.add(Task(id="build", prompt="build", workdir=repo))
        orchestration.add(Task(
            id="review",
            prompt="review upstream work",
            workdir=repo,
            depends_on=["build"],
            handoff=True,
        ))
        with fake_clock():
            orchestration.run()

        review_prompt = fleet.dispatched[1]["prompt"]
        self.assertIn("Upstream task: build", review_prompt)
        self.assertIn("artifact.txt", review_prompt)
        self.assertIn("verification", review_prompt)
        self.assertNotIn("text for s-0" * 100, review_prompt)

    def test_handoff_default_off_preserves_prompt(self):
        fleet = FakeFleet()
        fleet.set_outcome("s-0", "succeeded")
        fleet.set_outcome("s-1", "succeeded")
        orchestration = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
        orchestration.add(Task(id="a", prompt="exact prompt", workdir="/tmp"))
        orchestration.add(Task(
            id="b", prompt="child prompt", workdir="/tmp", depends_on=["a"]
        ))
        with fake_clock():
            orchestration.run()
        self.assertEqual(fleet.dispatched[1]["prompt"], "child prompt")


if __name__ == "__main__":
    unittest.main()
