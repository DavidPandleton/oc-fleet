"""Isolasi workdir via git worktree (stdlib only)."""

import os
import subprocess


def create(repo, name, root="/tmp/oc-fleet-worktrees"):
    """Buat worktree baru untuk satu task, kembalikan path-nya.

    `repo` harus working tree git yang valid. `name` dipakai sebagai
    nama direktori sekaligus nama branch baru.
    """
    dest = os.path.join(root, name)
    os.makedirs(root, exist_ok=True)
    subprocess.run(
        ["git", "-C", repo, "worktree", "add", "-b", name, dest],
        check=True, capture_output=True, text=True,
    )
    return dest


def remove(path, repo="."):
    """Hapus worktree. Best-effort: abaikan bila sudah tidak ada."""
    try:
        subprocess.run(
            ["git", "-C", repo, "worktree", "remove", "--force", path],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError:
        pass


def prune(repo="."):
    """Bersihkan metadata worktree yang direktorinya sudah hilang."""
    subprocess.run(
        ["git", "-C", repo, "worktree", "prune"],
        check=True, capture_output=True, text=True,
    )
