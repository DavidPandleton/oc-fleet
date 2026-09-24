"""Tests for worktree isolation (uses temp git repos, no server)."""

import os
import subprocess
import tempfile
import unittest

import worktree


def make_repo():
    d = tempfile.mkdtemp(prefix="oc-fleet-wt-")
    subprocess.run(["git", "init", "-q", d], check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t.t"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    with open(os.path.join(d, "f.txt"), "w") as fh:
        fh.write("x\n")
    subprocess.run(["git", "-C", d, "add", "."], check=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", "init"], check=True)
    return d


class WorktreeTest(unittest.TestCase):
    def test_create_lalu_remove(self):
        repo = make_repo()
        root = tempfile.mkdtemp(prefix="oc-fleet-wt-root-")
        dest = worktree.create(repo, "task-a", root=root)
        self.assertTrue(os.path.isdir(dest))
        self.assertTrue(os.path.isfile(os.path.join(dest, "f.txt")))
        worktree.remove(dest, repo=repo)
        self.assertFalse(os.path.exists(dest))

    def test_daftar_worktree_tercatat(self):
        repo = make_repo()
        root = tempfile.mkdtemp(prefix="oc-fleet-wt-root-")
        dest = worktree.create(repo, "task-b", root=root)
        out = subprocess.run(
            ["git", "-C", repo, "worktree", "list"],
            check=True, capture_output=True, text=True).stdout
        self.assertIn(dest, out)
        worktree.remove(dest, repo=repo)


if __name__ == "__main__":
    unittest.main()

class IsolateTaskTest(unittest.TestCase):
    def test_isolate_mengarahkan_workdir_ke_worktree(self):
        import subprocess
        from orchestrator import Orchestrator, Task
        from test_orchestrator import FakeFleet, fake_clock
        repo = make_repo()
        fleet = FakeFleet()
        o = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
        o.worktree_root = tempfile.mkdtemp(prefix="oc-fleet-wt-iso-")
        o.add(Task(id="job1", prompt="x", workdir=repo, repo=repo, isolate=True))
        fleet.set_outcome("s-0", "succeeded")
        with fake_clock():
            o.run()
        used = fleet.dispatched[0]["workdir"]
        self.assertNotEqual(used, repo)
        self.assertTrue(os.path.isdir(used))
        self.assertEqual(o.results()["job1"]["status"], "succeeded")
