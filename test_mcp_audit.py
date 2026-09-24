"""Defects found by auditing the MCP surface against real persisted runs.

Three of these were invisible to the existing tests, each for the same
reason: the test built its own store row by hand, so it never exercised
what the orchestrator actually writes.

  * a run with no tasks reported success,
  * a persisted task record carried no `workdir`, so `oc_fleet_diff` was
    dead for every real run (its test seeded the field itself),
  * `oc_fleet_run_dag` returned results but persisted nothing, so the run
    could not be looked up afterwards.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest

from mcp_server import FastMCP, _exit_code_for, create_server
from orchestrator import Orchestrator, Task
from store import RunStore


class FakeFleet:
    """In-process fleet; never touches the live server."""

    def __init__(self):
        self.n = 0

    def dispatch(self, task, workdir, title="", model="", env=None):
        self.n += 1
        return "s-%d" % self.n

    def status(self, session_id, stuck_after=None):
        return {"outcome": "completed", "last_assistant_text": "ok"}


def _git_repo():
    repo = tempfile.mkdtemp(prefix="ocfleet-audit-")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", "init", "-q", repo], check=True, env=env)
    subprocess.run(["git", "-C", repo, "commit", "-q", "--allow-empty",
                    "-m", "init"], check=True, env=env)
    return repo


class EmptyRunIsNotSuccessTest(unittest.TestCase):
    def test_a_run_with_no_tasks_is_not_reported_as_success(self):
        """A run that produced nothing must not exit 0."""
        orch = Orchestrator(fleet=FakeFleet(), poll_interval=0.001)
        orch.run()
        self.assertEqual(orch.results(), {})
        self.assertEqual(orch.run_status(), 1,
                         "an empty run must not look successful")

    def test_mcp_exit_code_matches_for_an_empty_record_set(self):
        self.assertEqual(_exit_code_for({}), 1)

    def test_a_real_success_still_exits_zero(self):
        repo = _git_repo()
        orch = Orchestrator(fleet=FakeFleet(), poll_interval=0.001)
        orch.add(Task(id="a", prompt="p", workdir=repo))
        orch.run()
        self.assertEqual(orch.run_status(), 0)


class WorkdirIsPersistedTest(unittest.TestCase):
    def test_a_persisted_task_record_carries_its_workdir(self):
        """No MCP needed: the orchestrator must write the field itself."""
        repo = _git_repo()
        db = os.path.join(tempfile.mkdtemp(prefix="ocfleet-audit-db-"), "r.db")
        store = RunStore(db)
        orch = Orchestrator(fleet=FakeFleet(), store=store, run_id="r",
                            poll_interval=0.001)
        orch.add(Task(id="a", prompt="p", workdir=repo))
        orch.run()
        back = store.get_task("r", "a")
        self.assertEqual(back.get("workdir"), repo)


@unittest.skipUnless(FastMCP is not None, "mcp SDK is not installed")
class DiffReadsARealRunTest(unittest.TestCase):
    """MCP-dependent, so guarded: CI does not install the optional SDK."""

    def test_diff_works_on_a_run_the_orchestrator_actually_wrote(self):
        """The diff tool must work off a real persisted run, not a seeded row."""
        repo = _git_repo()
        with open(os.path.join(repo, "made.py"), "w") as handle:
            handle.write("x = 1\n")
        db = os.path.join(tempfile.mkdtemp(prefix="ocfleet-audit-db-"), "r.db")
        store = RunStore(db)
        orch = Orchestrator(fleet=FakeFleet(), store=store, run_id="r",
                            poll_interval=0.001)
        orch.add(Task(id="a", prompt="p", workdir=repo))
        orch.run()

        tools = create_server(db)._tool_manager._tools
        result = tools["oc_fleet_diff"].fn("r", "a")
        self.assertNotIn("error", result, "diff failed on a real persisted run")
        self.assertIn("made.py", result["diff"])


@unittest.skipUnless(FastMCP is not None, "mcp SDK is not installed")
class RunDagPersistsTest(unittest.TestCase):
    def test_run_dag_writes_the_run_to_the_store(self):
        repo = _git_repo()
        db = os.path.join(tempfile.mkdtemp(prefix="ocfleet-audit-db-"), "r.db")
        tools = create_server(db)._tool_manager._tools

        # The tool would dispatch to a live server without a fleet; the
        # point here is persistence, so a fake fleet is injected.
        result = tools["oc_fleet_run_dag"].fn(
            tasks=[{"id": "a", "prompt": "p"}], workdir=repo,
            base_url="", run_id="dag-run", fleet=FakeFleet(),
        )
        self.assertNotIn("error", result, result)
        self.assertIsNotNone(RunStore(db).get_run("dag-run"),
                             "run_dag must persist its run")


if __name__ == "__main__":
    unittest.main()
