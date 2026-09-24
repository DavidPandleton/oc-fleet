"""Tests for the durable SQLite run store."""

import tempfile
import unittest

from store import RunStore


class RunStoreTest(unittest.TestCase):
    def test_creates_run_and_reads_it_back(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            store = RunStore(handle.name)
            store.upsert_run("run-1", {"status": "running", "tasks": 1})
            self.assertEqual(store.get_run("run-1"), {
                "status": "running",
                "tasks": 1,
            })

    def test_upsert_task_is_idempotent(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            store = RunStore(handle.name)
            record = {"status": "succeeded", "attempts": 1}
            store.upsert_task("run-1", "build", record)
            store.upsert_task("run-1", "build", {"status": "verification_passed"})
            self.assertEqual(
                store.get_task("run-1", "build")["status"],
                "verification_passed",
            )

    def test_attempts_events_and_artifacts_are_json(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            store = RunStore(handle.name)
            store.upsert_attempt("run-1", "build", 1, {"model": "cutad/x"})
            store.append_event("run-1", {"event_type": "started", "task_id": "build"})
            store.upsert_artifact("run-1", "build", {"files_changed": ["x.py"]})
            self.assertEqual(store.list_attempts("run-1", "build")[0]["model"], "cutad/x")
            self.assertEqual(store.list_events("run-1")[0]["event_type"], "started")
            self.assertEqual(store.get_artifact("run-1", "build")["files_changed"], ["x.py"])

    def test_malformed_json_fails_closed(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            store = RunStore(handle.name)
            store.connection.execute(
                "INSERT INTO runs(run_id, payload) VALUES (?, ?)",
                ("bad", "{not-json"),
            )
            store.connection.commit()
            with self.assertRaises(ValueError):
                store.get_run("bad")


if __name__ == "__main__":
    unittest.main()
