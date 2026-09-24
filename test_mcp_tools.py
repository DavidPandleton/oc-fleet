"""The MCP surface must match what the plan promised, and actually work.

Phase 6 of the v0.2 plan names ten tools. Five shipped: run_show, results,
events, dispatch, cancel. Missing: status, runs, diff, approve, merge,
run_dag. The old test only asserted the server object had a `_tool_manager`
attribute, which passes whether or not a single tool does anything.

These tests call each tool's function directly (`tool.fn`) against a real
SQLite store, so a tool that exists but returns the wrong shape fails.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest

from mcp_server import FastMCP, create_server
from store import RunStore

EXPECTED_TOOLS = [
    "oc_fleet_status",
    "oc_fleet_runs",
    "oc_fleet_run_show",
    "oc_fleet_results",
    "oc_fleet_diff",
    "oc_fleet_dispatch",
    "oc_fleet_run_dag",
    "oc_fleet_cancel",
    "oc_fleet_approve",
    "oc_fleet_merge",
]


def _tools(store_path):
    return create_server(store_path)._tool_manager._tools


@unittest.skipUnless(FastMCP is not None, "mcp SDK is not installed")
class McpSurfaceTest(unittest.TestCase):
    def test_every_promised_tool_is_exposed(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            tools = _tools(handle.name)
            missing = [name for name in EXPECTED_TOOLS if name not in tools]
            self.assertEqual(missing, [], "missing MCP tools: %s" % missing)

    def test_runs_lists_persisted_runs(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            store = RunStore(handle.name)
            store.upsert_run("r1", {"status": "completed", "tasks": 2})
            store.upsert_run("r2", {"status": "running", "tasks": 1})
            result = _tools(handle.name)["oc_fleet_runs"].fn()
            ids = sorted(r["run_id"] for r in result["runs"])
            self.assertEqual(ids, ["r1", "r2"])

    def test_status_reports_exit_code_for_a_run(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            store = RunStore(handle.name)
            store.upsert_run("r1", {"status": "completed"})
            store.upsert_task("r1", "a", {"status": "verification_failed"})
            result = _tools(handle.name)["oc_fleet_status"].fn("r1")
            # A failed verification is exit code 2, not 0.
            self.assertEqual(result["exit_code"], 2)

    def test_status_errors_structurally_for_an_unknown_run(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            result = _tools(handle.name)["oc_fleet_status"].fn("nope")
            self.assertIn("error", result)

    def test_diff_returns_a_structured_error_without_a_workdir(self):
        """diff must not run git in the server's own cwd."""
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            store = RunStore(handle.name)
            store.upsert_task("r1", "a", {"status": "succeeded"})
            result = _tools(handle.name)["oc_fleet_diff"].fn("r1", "a")
            self.assertIn("error", result)

    def test_diff_works_against_a_real_git_workdir(self):
        repo = tempfile.mkdtemp()
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "init", "-q", repo], check=True, env=env)
        subprocess.run(["git", "-C", repo, "commit", "-q", "--allow-empty",
                        "-m", "init"], check=True, env=env)
        with open(os.path.join(repo, "new.txt"), "w") as fh:
            fh.write("hi\n")
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            store = RunStore(handle.name)
            store.upsert_task("r1", "a", {"status": "succeeded",
                                          "workdir": repo})
            result = _tools(handle.name)["oc_fleet_diff"].fn("r1", "a")
            self.assertNotIn("error", result)
            self.assertIn("new.txt", result["diff"])

    def test_approve_refuses_a_task_that_is_not_verification_passed(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            store = RunStore(handle.name)
            store.upsert_task("r1", "a", {"status": "succeeded"})
            result = _tools(handle.name)["oc_fleet_approve"].fn("r1", "a")
            self.assertIn("error", result)

    def test_approve_records_for_a_verification_passed_task(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            store = RunStore(handle.name)
            store.upsert_task("r1", "a", {"status": "verification_passed",
                                          "artifacts": {"files_changed": []}})
            tools = _tools(handle.name)
            result = tools["oc_fleet_approve"].fn("r1", "a")
            self.assertTrue(result.get("approved"))
            self.assertIsNotNone(RunStore(handle.name).get_approval("r1", "a"))

    def test_merge_refuses_without_a_recorded_approval(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            store = RunStore(handle.name)
            store.upsert_task("r1", "a", {"status": "verification_passed",
                                          "artifacts": {"files_changed": []}})
            result = _tools(handle.name)["oc_fleet_merge"].fn(
                "r1", "a", "/tmp", "master")
            self.assertIn("error", result)

    def test_merge_refuses_an_arbitrary_target_path(self):
        """A target that is not a plain branch name is refused."""
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            result = _tools(handle.name)["oc_fleet_merge"].fn(
                "r1", "a", "/tmp", "/etc/passwd")
            self.assertIn("error", result)

    def test_run_dag_rejects_an_empty_task_list(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            result = _tools(handle.name)["oc_fleet_run_dag"].fn([], "/tmp")
            self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
