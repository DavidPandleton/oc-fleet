"""Read-only Git review operations for foreman task worktrees."""

import subprocess


def _git(workdir, *args):
    return subprocess.run(
        ["git", "-C", workdir, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def diff(workdir):
    """Stat of what changed, including files git does not track yet.

    `git diff --stat HEAD` alone reports nothing for a brand-new file,
    which is exactly what an agent creates most of the time. An empty
    diff for a task that wrote three new files reads as "no work done",
    so untracked paths are listed explicitly, the same way the artifact
    manifest collects them.
    """
    result = _git(workdir, "diff", "--no-ext-diff", "--stat", "HEAD")
    if result.returncode:
        raise RuntimeError((result.stderr or "git diff failed").strip())
    untracked = _git(
        workdir, "ls-files", "--others", "--exclude-standard"
    )
    if untracked.returncode:
        raise RuntimeError((untracked.stderr or "git ls-files failed").strip())
    new_files = [line for line in untracked.stdout.splitlines() if line]
    if new_files:
        listing = "\n".join(" %s | new file" % path for path in new_files)
        header = result.stdout.rstrip()
        joined = (header + "\n" if header else "") + listing
        return joined + "\n"
    return result.stdout


def changed(workdir):
    result = _git(workdir, "status", "--short")
    if result.returncode:
        raise RuntimeError((result.stderr or "git status failed").strip())
    return [line for line in result.stdout.splitlines() if line]


def conflicts(workdir):
    result = _git(workdir, "diff", "--name-only", "--diff-filter=U")
    if result.returncode:
        raise RuntimeError((result.stderr or "git conflict inspection failed").strip())
    return [line for line in result.stdout.splitlines() if line]


def approve(record):
    """Return whether an explicit human approval may be recorded."""
    return (
        isinstance(record, dict)
        and record.get("status") == "verification_passed"
        and isinstance(record.get("artifacts"), dict)
    )


def merge(repo, source, target):
    """Explicitly fast-forward target after checking it is clean."""
    current = _git(repo, "branch", "--show-current")
    if current.returncode or current.stdout.strip() != target:
        checkout = _git(repo, "checkout", target)
        if checkout.returncode:
            raise RuntimeError((checkout.stderr or "cannot checkout target").strip())
    status = _git(repo, "status", "--porcelain")
    if status.returncode or status.stdout.strip():
        raise RuntimeError("target worktree is dirty")
    result = _git(repo, "merge", "--ff-only", source)
    if result.returncode:
        raise RuntimeError((result.stderr or "fast-forward merge failed").strip())
    return {"merged": True, "source": source, "target": target}
