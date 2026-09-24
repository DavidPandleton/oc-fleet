"""Every entry point must import its own siblings from any cwd.

`python3 /abs/path/cli.py` works by accident: CPython puts the script's
directory on sys.path. That accident does not hold for the ways a runner
or a scheduler actually launches these files - runpy, an import, a
symlink in /tmp, or `python3 -m` from another directory. In all of those
the sibling imports (`from store import RunStore`) raise
ModuleNotFoundError, and because these entry points start detached, the
failure is silent.

`oc-fleet-wait.py` already inserts its own directory before importing
`fleet`. The other entry points did not, so this test pins the rule for
all of them: launching the file from an unrelated cwd must import its
siblings and reach its `--help` without env help.

These run real subprocesses, which is the point: an in-process test would
inherit this test file's sys.path and prove nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))

# Each entry point: the file, and an argv that makes it print help and exit
# before doing any real work.
ENTRY_POINTS = [
    ("cli.py", ["--help"]),
    ("mcp_server.py", ["--help"]),
    ("prompt_lint.py", ["--help"]),
]


class EntryPointCwdIndependenceTest(unittest.TestCase):
    def _run_from_other_dir(self, script, argv, cwd, code=None):
        """Run `script` with cwd elsewhere, via runpy, and return the result.

        runpy executes the file the way an importer would: it does NOT add
        the file's directory to sys.path, which is exactly the case the
        entry points were not built for.
        """
        if code is None:
            code = textwrap.dedent(
                """
                import runpy, sys
                sys.argv = [%r] + %r
                try:
                    runpy.run_path(%r, run_name="__main__")
                except SystemExit as exc:
                    raise SystemExit(exc.code or 0)
                """
            ) % (os.path.join(REPO, script), argv, os.path.join(REPO, script))
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc

    def test_entry_points_reach_help_from_tmp(self):
        import tempfile

        cwd = tempfile.mkdtemp()
        failures = []
        for script, argv in ENTRY_POINTS:
            proc = self._run_from_other_dir(script, argv, cwd)
            if proc.returncode != 0:
                failures.append(
                    "%s from %s: exit %d\n%s"
                    % (script, cwd, proc.returncode, proc.stderr.strip())
                )
        self.assertEqual(failures, [], "\n\n".join(failures))


if __name__ == "__main__":
    unittest.main()
