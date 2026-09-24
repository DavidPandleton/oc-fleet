"""Creating a worktree must fail clearly, not cryptically, on a name clash.

`worktree.create(repo, name)` names both the branch and the directory
after the task. Called twice with the same name - a re-run, or two runs
in flight - git refuses because the branch (or the path) already exists.
The old code let git's exit 128 surface as a raw CalledProcessError from
a nested `worktree add`, which tells the operator nothing about what to
do and looks like a git problem rather than a naming one.

The contract this pins:

  * a second create with the same name raises a ValueError whose message
    names the clash, so the caller can tell "pick another name" from
    "git is broken",
  * the original worktree is left untouched (we do not silently reuse a
    stale tree, which could carry another task's changes),
  * distinct names still succeed.

Reusing a stale worktree is deliberately NOT the behaviour: a worktree
named after a task and left behind may contain that task's half-written
state, and adopting it would corrupt the next run silently.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

import worktree


def _git_repo():
    repo = tempfile.mkdtemp(prefix="wt-test-repo-")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", "init", "-q", repo], check=True, env=env)
    subprocess.run(["git", "-C", repo, "commit", "-q", "--allow-empty",
                    "-m", "init"], check=True, env=env)
    return repo


@unittest.skipUnless(shutil.which("git"), "git not found")
class WorktreeNameClashTest(unittest.TestCase):
    def setUp(self):
        self.repo = _git_repo()
        self.root = tempfile.mkdtemp(prefix="wt-test-root-")

    def test_second_create_with_same_name_raises_a_clear_error(self):
        first = worktree.create(self.repo, "task-a", self.root)
        self.assertTrue(os.path.isdir(first))
        with self.assertRaises(ValueError) as ctx:
            worktree.create(self.repo, "task-a", self.root)
        message = str(ctx.exception)
        self.assertIn("task-a", message)

    def test_the_stale_worktree_is_left_in_place(self):
        first = worktree.create(self.repo, "task-a", self.root)
        with self.assertRaises(ValueError):
            worktree.create(self.repo, "task-a", self.root)
        self.assertTrue(os.path.isdir(first),
                        "a refused create must not delete the existing tree")

    def test_distinct_names_both_succeed(self):
        a = worktree.create(self.repo, "task-a", self.root)
        b = worktree.create(self.repo, "task-b", self.root)
        self.assertTrue(os.path.isdir(a))
        self.assertTrue(os.path.isdir(b))
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
