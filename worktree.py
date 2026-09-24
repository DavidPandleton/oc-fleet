"""Isolasi workdir via git worktree (stdlib only)."""

import os
import subprocess


def create(repo, name, root="/tmp/oc-fleet-worktrees"):
    """Buat worktree baru untuk satu task, kembalikan path-nya.

    `repo` harus working tree git yang valid. `name` dipakai sebagai
    nama direktori sekaligus nama branch baru.

    Gagal dengan pesan yang jelas kalau `name` sudah dipakai. Sebelumnya
    ini menyerahkan penolakan ke git, yang keluar dengan kode 128 dari
    `worktree add` bersarang - pesannya soal git, padahal masalahnya
    penamaan, dan operator jadi bingung harus berbuat apa. Worktree lama
    sengaja TIDAK dipakai ulang: mungkin masih berisi setengah pekerjaan
    task sebelumnya, dan mengadopsinya akan merusak run berikutnya diam
    diam.
    """
    dest = os.path.join(root, name)
    if os.path.exists(dest):
        raise ValueError(
            "worktree name %r is already in use: %s exists. Pick another "
            "name, or remove the stale worktree first." % (name, dest)
        )
    os.makedirs(root, exist_ok=True)
    try:
        subprocess.run(
            ["git", "-C", repo, "worktree", "add", "-b", name, dest],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        # Dest belum ada tapi git tetap menolak: hampir selalu karena
        # branch `name` sudah ada. Terjemahkan supaya penyebabnya jelas.
        detail = (exc.stderr or exc.stdout or "").strip().splitlines()
        hint = detail[-1] if detail else "exit %s" % exc.returncode
        raise ValueError(
            "could not create worktree %r in %s: %s" % (name, repo, hint)
        ) from exc
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
