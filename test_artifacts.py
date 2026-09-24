"""Tests for bounded Git artifact manifests."""

import os
import subprocess
import tempfile
import unittest

from artifacts import collect_manifest


def make_repo():
    repo = tempfile.mkdtemp(prefix="oc-fleet-artifacts-")
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "Test"], check=True)
    with open(os.path.join(repo, "tracked.txt"), "w") as handle:
        handle.write("before\n")
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-qm", "initial"], check=True)
    return repo


class ArtifactTest(unittest.TestCase):
    def test_clean_repo(self):
        repo = make_repo()
        manifest = collect_manifest(repo)
        self.assertEqual(manifest.files_changed, [])
        self.assertTrue(manifest.clean)

    def test_modified_and_new_files(self):
        repo = make_repo()
        with open(os.path.join(repo, "tracked.txt"), "a") as handle:
            handle.write("after\n")
        with open(os.path.join(repo, "new.txt"), "w") as handle:
            handle.write("new\n")
        manifest = collect_manifest(repo)
        self.assertIn("tracked.txt", manifest.files_changed)
        self.assertIn("new.txt", manifest.files_changed)
        self.assertFalse(manifest.clean)
        self.assertIn("tracked.txt", manifest.diff_stat)

    def test_staged_file_is_included(self):
        repo = make_repo()
        with open(os.path.join(repo, "tracked.txt"), "a") as handle:
            handle.write("staged\n")
        subprocess.run(["git", "-C", repo, "add", "tracked.txt"], check=True)
        manifest = collect_manifest(repo)
        self.assertIn("tracked.txt", manifest.files_changed)
        self.assertIn("tracked.txt", manifest.diff_stat)

    def test_deleted_file_is_included(self):
        repo = make_repo()
        os.unlink(os.path.join(repo, "tracked.txt"))
        manifest = collect_manifest(repo)
        self.assertIn("tracked.txt", manifest.files_changed)
        self.assertFalse(manifest.clean)

    def test_non_git_workdir_is_bounded_evidence(self):
        workdir = tempfile.mkdtemp(prefix="oc-fleet-not-git-")
        manifest = collect_manifest(workdir)
        self.assertFalse(manifest.clean)
        self.assertLessEqual(len(manifest.diff_stat), 2000)
        self.assertIn("git status failed", manifest.diff_stat)


if __name__ == "__main__":
    unittest.main()
