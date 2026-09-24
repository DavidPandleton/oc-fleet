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


class ReviewUntrackedTest(unittest.TestCase):
    """A new file is what an agent creates most; the diff must show it."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="oc-fleet-review-untracked-")
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        subprocess.run(["git", "-C", self.repo, "config", "user.email", "x@y.z"], check=True)
        subprocess.run(["git", "-C", self.repo, "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", self.repo, "commit", "-q", "--allow-empty",
                        "-m", "base"], check=True)

    def test_diff_includes_a_brand_new_file(self):
        with open(os.path.join(self.repo, "brand_new.py"), "w") as handle:
            handle.write("print('hi')\n")
        rendered = diff(self.repo)
        self.assertIn("brand_new.py", rendered)

    def test_diff_is_empty_when_nothing_changed(self):
        self.assertEqual(diff(self.repo).strip(), "")

    def test_new_files_inside_a_new_directory_are_listed(self):
        os.makedirs(os.path.join(self.repo, "pkg"))
        with open(os.path.join(self.repo, "pkg", "mod.py"), "w") as handle:
            handle.write("x = 1\n")
        rendered = diff(self.repo)
        self.assertIn("pkg/mod.py", rendered)


if __name__ == "__main__":
    unittest.main()
