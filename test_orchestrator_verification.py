"""Tests for independent task verification."""

import os
import sys
import tempfile
import unittest

from orchestrator import Task, run_verification


class VerificationTest(unittest.TestCase):
    def test_task_menerima_verify_dan_timeout(self):
        task = Task(
            id="test",
            prompt="run tests",
            workdir="/tmp",
            verify=[f"{sys.executable} -c 'print(\"ok\")'"],
            verify_timeout=7,
        )
        self.assertEqual(len(task.verify), 1)
        self.assertEqual(task.verify_timeout, 7)

    def test_command_yang_lulus_mengembalikan_evidence(self):
        result = run_verification(
            [f"{sys.executable} -c 'print(\"verified\")'"],
            workdir="/tmp",
        )
        self.assertTrue(result.passed)
        self.assertTrue(result.required)
        self.assertEqual(result.commands[0]["returncode"], 0)
        self.assertIn("verified", result.commands[0]["stdout"])

    def test_command_nonzero_gagal_dan_command_berikutnya_tidak_dijalankan(self):
        marker = os.path.join(tempfile.gettempdir(), "oc-fleet-verifier-marker")
        try:
            os.unlink(marker)
        except FileNotFoundError:
            pass
        result = run_verification(
            [
                f"{sys.executable} -c 'import sys; print(\"bad\"); sys.exit(3)'",
                f"{sys.executable} -c 'open({marker!r}, \"w\").write(\"ran\")'",
            ],
            workdir="/tmp",
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.commands[0]["returncode"], 3)
        self.assertFalse(os.path.exists(marker))

    def test_timeout_dilaporkan_sebagai_verification_failure(self):
        result = run_verification(
            [f"{sys.executable} -c 'import time; time.sleep(1)'"],
            workdir="/tmp",
            timeout=0.01,
        )
        self.assertFalse(result.passed)
        self.assertTrue(result.commands[0]["timed_out"])

    def test_workdir_diteruskan_ke_command(self):
        with tempfile.TemporaryDirectory() as workdir:
            result = run_verification(
                [f"{sys.executable} -c 'import os; print(os.getcwd())'"],
                workdir=workdir,
            )
        self.assertEqual(result.commands[0]["returncode"], 0)
        self.assertIn(workdir, result.commands[0]["stdout"])


if __name__ == "__main__":
    unittest.main()
