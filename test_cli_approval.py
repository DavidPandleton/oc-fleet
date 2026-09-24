"""Tests for explicit approval/merge CLI commands."""

import tempfile
import unittest

from cli import build_parser
from store import RunStore


class ApprovalCliTest(unittest.TestCase):
    def test_commands_parse_with_store(self):
        parser = build_parser()
        approve = parser.parse_args(["approve", "run-1", "build", "--store", "/tmp/x.sqlite"])
        merge = parser.parse_args([
            "merge", "run-1", "build", "--repo", "/tmp/repo",
            "--source", "task-branch", "--target", "master",
            "--store", "/tmp/x.sqlite",
        ])
        self.assertEqual(approve.command, "approve")
        self.assertEqual(merge.command, "merge")

    def test_approval_gate_reads_task_result(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            store = RunStore(handle.name)
            store.upsert_task("run-1", "build", {
                "status": "verification_passed",
                "artifacts": {"clean": False},
            })
            self.assertEqual(store.get_task("run-1", "build")["status"], "verification_passed")


if __name__ == "__main__":
    unittest.main()
