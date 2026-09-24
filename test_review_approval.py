"""Tests for explicit approval and guarded merge policy."""

import os
import subprocess
import tempfile
import unittest

from review import approve, merge


class ApprovalReviewTest(unittest.TestCase):
    def _repo(self):
        repo = tempfile.mkdtemp(prefix="oc-fleet-merge-")
        subprocess.run(["git", "init", "-q", repo], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.email", "x@y.z"], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.name", "Test"], check=True)
        with open(os.path.join(repo, "x.txt"), "w") as handle:
            handle.write("base\n")
        subprocess.run(["git", "-C", repo, "add", "."], check=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "base"], check=True)
        return repo

    def test_approval_requires_verification_and_no_conflict(self):
        self.assertTrue(approve({"status": "verification_passed", "artifacts": {"clean": False}}))
        self.assertFalse(approve({"status": "verification_failed", "artifacts": {"clean": False}}))

    def test_merge_is_explicit_and_fast_forward_only(self):
        repo = self._repo()
        branch = "task-branch"
        subprocess.run(["git", "-C", repo, "checkout", "-qb", branch], check=True)
        with open(os.path.join(repo, "feature.txt"), "w") as handle:
            handle.write("feature\n")
        subprocess.run(["git", "-C", repo, "add", "."], check=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "feature"], check=True)
        subprocess.run(["git", "-C", repo, "checkout", "-q", "master"], check=True)
        result = merge(repo, branch, "master")
        self.assertTrue(result["merged"])
        self.assertIn("feature.txt", subprocess.check_output(
            ["git", "-C", repo, "show", "--format=", "--name-only", "HEAD"], text=True
        ))

    def test_merge_refuses_dirty_target(self):
        repo = self._repo()
        with open(os.path.join(repo, "dirty.txt"), "w") as handle:
            handle.write("do not merge\n")
        with self.assertRaises(RuntimeError):
            merge(repo, "master", "master")


if __name__ == "__main__":
    unittest.main()
