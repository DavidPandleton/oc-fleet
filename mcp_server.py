"""Thin MCP read-only adapter over the persisted oc-fleet store."""

import argparse
import json

from store import RunStore

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
