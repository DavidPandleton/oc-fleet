"""Collect bounded Git evidence for one task worktree."""

import subprocess

from contracts import ArtifactManifest


def _git(repo, *args):
    completed = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def collect_manifest(workdir):
    """Return changed-file evidence without reading file contents."""
    code, status, error = _git(workdir, "status", "--short")
    if code:
        return ArtifactManifest(
            workdir=workdir,
            clean=False,
            diff_stat=("git status failed: " + error.strip())[:2000],
        )

    files = []
    for line in status.splitlines():
        files.append(line[3:] if len(line) >= 4 else line)

    diff_code, diff_stat, diff_error = _git(workdir, "diff", "--stat")
    if diff_code:
        diff_stat = ("git diff failed: " + diff_error.strip())[:2000]
    else:
        diff_stat = diff_stat.strip()

    staged_code, staged_stat, staged_error = _git(
        workdir, "diff", "--cached", "--stat"
    )
    if staged_code == 0 and staged_stat.strip():
        diff_stat = "\n".join(
            part for part in (diff_stat, staged_stat.strip()) if part
        )
    elif staged_code:
        diff_stat = (
            diff_stat + "\ngit cached diff failed: " + staged_error.strip()
        )[:2000]

    return ArtifactManifest(
        workdir=workdir,
        files_changed=files,
        diff_stat=diff_stat,
        clean=not files,
    )
