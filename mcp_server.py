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


def create_server(store_path):
    if FastMCP is None:
        raise RuntimeError("mcp package is required for oc-fleet-mcp")
    server = FastMCP("oc-fleet")

    @server.tool()
    def oc_fleet_run_show(run_id: str) -> dict:
        """Return one persisted run without secrets or prompts."""
        store = RunStore(store_path)
        run = store.get_run(run_id)
        if run is None:
            return {"error": "run not found", "run_id": run_id}
        return {
            "run": run,
            "tasks": {
                task_id: store.get_task(run_id, task_id)
                for task_id in _task_ids(store, run_id)
            },
        }

    @server.tool()
    def oc_fleet_results(run_id: str) -> dict:
        """Return persisted task results for a run."""
        store = RunStore(store_path)
        return {
            task_id: store.get_task(run_id, task_id)
            for task_id in _task_ids(store, run_id)
        }

    @server.tool()
    def oc_fleet_events(run_id: str) -> list:
        """Return structured lifecycle events for a run."""
        return RunStore(store_path).list_events(run_id)

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
    def oc_fleet_cancel(session_id: str, base_url: str) -> dict:
        """Cancel one explicitly named OpenCode session."""
        if not session_id.strip() or not base_url.strip():
            return {"error": "session_id and base_url are required"}
        from fleet import Fleet
        return {"session_id": session_id, "cancelled": Fleet(base_url=base_url).cancel(session_id)}

    return server


def _task_ids(store, run_id):
    rows = store.connection.execute(
        "SELECT task_id FROM tasks WHERE run_id = ? ORDER BY task_id", (run_id,)
    ).fetchall()
    return [row[0] for row in rows]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="oc-fleet-mcp")
    parser.add_argument("--store", required=True)
    args = parser.parse_args(argv)
    create_server(args.store).run()


if __name__ == "__main__":
    main()
