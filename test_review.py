"""Tests for read-only review operations."""

import os
import subprocess
import tempfile
import unittest

from review import changed, conflicts, diff


class ReviewTest(unittest.TestCase):
    def test_changed_and_diff_are_read_only(self):
        repo = tempfile.mkdtemp(prefix="oc-fleet-review-")
        subprocess.run(["git", "init", "-q", repo], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.email", "x@y.z"], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.name", "Test"], check=True)
        path = os.path.join(repo, "x.txt")
        with open(path, "w") as handle:
            handle.write("before\n")
        subprocess.run(["git", "-C", repo, "add", "."], check=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "base"], check=True)
        with open(path, "a") as handle:
            handle.write("after\n")
        before = subprocess.check_output(["git", "-C", repo, "status", "--porcelain"], text=True)
        self.assertIn("x.txt", changed(repo)[0])
        self.assertIn("x.txt", diff(repo))
        self.assertEqual(conflicts(repo), [])
        after = subprocess.check_output(["git", "-C", repo, "status", "--porcelain"], text=True)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
