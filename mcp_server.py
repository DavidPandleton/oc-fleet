"""Thin MCP read-only adapter over the persisted oc-fleet store."""

import argparse
import json
import os
import sys

# See the note in cli.py: a launched-any-other-way entry point does not get
# its own directory on sys.path, and this one starts detached, so the
# ModuleNotFoundError below would be silent.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from store import RunStore  # noqa: E402

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    FastMCP = None


def _exit_code_for(tasks):
    """Same rule as Orchestrator.run_status(), applied to stored records.

    Kept in sync with the orchestrator on purpose. A run's exit code is a
    contract a script acts on, so the stored form must mean the same thing
    as the live form: 2 (verification failure) outranks 1 (agent failure),
    and anything unfinished counts against the run rather than for it.
    """
    saw_agent_failure = False
    for record in tasks.values():
        if not isinstance(record, dict):
            continue
        status = record.get("status")
        if status == "verification_failed":
            return 2
        if status in ("failed", "timed_out", "setup_failed", "skipped",
                      "pending", "running"):
            saw_agent_failure = True
    return 1 if saw_agent_failure else 0


def _task_ids(store, run_id):
    rows = store.connection.execute(
        "SELECT task_id FROM tasks WHERE run_id = ? ORDER BY task_id", (run_id,)
    ).fetchall()
    return [row[0] for row in rows]


def create_server(store_path):
    if FastMCP is None:
        raise RuntimeError("mcp package is required for oc-fleet-mcp")
    server = FastMCP("oc-fleet")

    def _tasks(store, run_id):
        return {
            task_id: store.get_task(run_id, task_id)
            for task_id in _task_ids(store, run_id)
        }

    @server.tool()
    def oc_fleet_runs() -> dict:
        """List persisted runs with their status. No prompts or secrets."""
        store = RunStore(store_path)
        return {
            "runs": [
                {"run_id": run_id, **(payload if isinstance(payload, dict) else {})}
                for run_id, payload in store.list_runs()
            ]
        }

    @server.tool()
    def oc_fleet_status(run_id: str) -> dict:
        """Report one run's status and exit code, without dispatching."""
        store = RunStore(store_path)
        run = store.get_run(run_id)
        if run is None:
            return {"error": "run not found", "run_id": run_id}
        tasks = _tasks(store, run_id)
        return {
            "run_id": run_id,
            "run": run,
            "exit_code": _exit_code_for(tasks),
            "tasks": {tid: (rec or {}).get("status") for tid, rec in tasks.items()},
        }

    @server.tool()
    def oc_fleet_run_show(run_id: str) -> dict:
        """Return one persisted run without secrets or prompts."""
        store = RunStore(store_path)
        run = store.get_run(run_id)
        if run is None:
            return {"error": "run not found", "run_id": run_id}
        return {"run": run, "tasks": _tasks(store, run_id)}

    @server.tool()
    def oc_fleet_results(run_id: str) -> dict:
        """Return persisted task results for a run."""
        store = RunStore(store_path)
        return _tasks(store, run_id)

    @server.tool()
    def oc_fleet_events(run_id: str) -> list:
        """Return structured lifecycle events for a run."""
        return RunStore(store_path).list_events(run_id)

    @server.tool()
    def oc_fleet_diff(run_id: str, task_id: str) -> dict:
        """Read-only diff of a task's recorded workdir. Never runs in cwd."""
        store = RunStore(store_path)
        record = store.get_task(run_id, task_id)
        if record is None:
            return {"error": "task not found", "run_id": run_id, "task_id": task_id}
        workdir = record.get("workdir")
        if not workdir or not os.path.isdir(workdir):
            return {"error": "task has no readable workdir", "workdir": workdir}
        from review import changed, conflicts, diff

        try:
            return {
                "workdir": workdir,
                "diff": diff(workdir),
                "changed": changed(workdir),
                "conflicts": conflicts(workdir),
            }
        except RuntimeError as exc:
            return {"error": str(exc), "workdir": workdir}

    @server.tool()
    def oc_fleet_dispatch(
        prompt: str,
        workdir: str,
        base_url: str,
        model: str = "",
        title: str = "",
    ) -> dict:
        """Dispatch one explicit OpenCode task; no shell or implicit cwd."""
        if not prompt.strip() or not workdir.strip() or not base_url.strip():
            return {"error": "prompt, workdir, and base_url are required"}
        from fleet import Fleet
        session_id = Fleet(base_url=base_url).dispatch(
            prompt, workdir, title=title, model=model
        )
        return {"session_id": session_id, "workdir": workdir, "model": model}

    @server.tool()
    def oc_fleet_run_dag(tasks: list, workdir: str, base_url: str = "") -> dict:
        """Run a small DAG of tasks through the orchestrator.

        Each task is a dict with at least `id` and `prompt`; optional keys
        mirror Task fields (`depends_on`, `model`, `verify`, `retries`,
        `owns`, `isolate`, `repo`). An explicit workdir is required so the
        server never runs an agent in its own directory.
        """
        if not workdir.strip():
            return {"error": "workdir is required"}
        if not isinstance(tasks, list) or not tasks:
            return {"error": "tasks must be a non-empty list"}
        from orchestrator import Orchestrator, Task

        allowed = {
            "id", "prompt", "depends_on", "model", "title", "retries",
            "timeout", "fallbacks", "verify", "verify_timeout", "owns",
            "isolate", "repo", "env", "setup", "teardown", "handoff",
        }
        try:
            limiter = max(1, len(tasks))
            if base_url.strip():
                from fleet import Fleet

                orch = Orchestrator(fleet=Fleet(base_url=base_url),
                                    max_parallel=limiter)
            else:
                orch = Orchestrator(max_parallel=limiter)
            for spec in tasks:
                if not isinstance(spec, dict) or "id" not in spec or "prompt" not in spec:
                    return {"error": "each task needs `id` and `prompt`"}
                unknown = set(spec) - allowed
                if unknown:
                    return {"error": "unknown task fields: %s" % sorted(unknown)}
                fields = dict(spec)
                fields.setdefault("workdir", workdir)
                orch.add(Task(**fields))
            orch.run()
            return {"results": orch.results(), "exit_code": orch.run_status()}
        except Exception as exc:
            return {"error": "%s: %s" % (type(exc).__name__, exc)}

    @server.tool()
    def oc_fleet_cancel(session_id: str, base_url: str) -> dict:
        """Cancel one explicitly named OpenCode session."""
        if not session_id.strip() or not base_url.strip():
            return {"error": "session_id and base_url are required"}
        from fleet import Fleet
        return {"session_id": session_id, "cancelled": Fleet(base_url=base_url).cancel(session_id)}

    @server.tool()
    def oc_fleet_approve(run_id: str, task_id: str) -> dict:
        """Record a human approval for a task that passed verification.

        Refused unless the task is `verification_passed` and carries an
        artifact manifest, so approval means "a verified change with
        evidence", not "whatever the agent last said".
        """
        store = RunStore(store_path)
        record = store.get_task(run_id, task_id)
        if record is None:
            return {"error": "task not found", "run_id": run_id, "task_id": task_id}
        from review import approve

        if not approve(record):
            return {
                "error": "task is not approvable",
                "status": record.get("status"),
                "requires": "status verification_passed with artifacts",
            }
        store.record_approval(run_id, task_id, {"approved": True})
        return {"approved": True, "run_id": run_id, "task_id": task_id}

    @server.tool()
    def oc_fleet_merge(run_id: str, task_id: str, repo: str, target: str) -> dict:
        """Fast-forward `target` to a task's approved work. Explicit only.

        Requires a recorded approval first, and rejects a `target` that is
        not a plain branch name, so a path cannot be passed where a branch
        is expected.
        """
        import re

        if not re.fullmatch(r"[A-Za-z0-9._/-]+", target or ""):
            return {"error": "target must be a plain branch name", "target": target}
        store = RunStore(store_path)
        if store.get_approval(run_id, task_id) is None:
            return {"error": "no recorded approval for this task",
                    "run_id": run_id, "task_id": task_id}
        record = store.get_task(run_id, task_id)
        if record is None:
            return {"error": "task not found", "run_id": run_id, "task_id": task_id}
        source = record.get("branch") or task_id
        from review import merge

        try:
            return merge(repo, source, target)
        except RuntimeError as exc:
            return {"error": str(exc), "repo": repo, "target": target}

    return server


def main(argv=None):
    parser = argparse.ArgumentParser(prog="oc-fleet-mcp")
    parser.add_argument("--store", required=True)
    args = parser.parse_args(argv)
    create_server(args.store).run()


if __name__ == "__main__":
    main()
