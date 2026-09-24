"""Small durable SQLite store for resumable foreman runs."""

from __future__ import annotations

import json
import sqlite3

SCHEMA_VERSION = 1


class RunStore:
    """SQLite-backed state store with idempotent task/attempt writes."""

    def __init__(self, path):
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self):
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (run_id, task_id)
            );
            CREATE TABLE IF NOT EXISTS attempts (
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (run_id, task_id, attempt)
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (run_id, task_id)
            );
            CREATE TABLE IF NOT EXISTS approvals (
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (run_id, task_id)
            );
            INSERT OR IGNORE INTO schema_meta(key, value)
                VALUES ('schema_version', '1');
            """
        )
        self.connection.commit()

    @staticmethod
    def _decode(payload):
        try:
            return json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise ValueError("corrupt JSON payload in run store") from exc

    def upsert_run(self, run_id, payload):
        with self.connection:
            self.connection.execute(
                "INSERT INTO runs(run_id, payload) VALUES (?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET payload=excluded.payload",
                (run_id, json.dumps(payload, sort_keys=True)),
            )

    def get_run(self, run_id):
        row = self.connection.execute(
            "SELECT payload FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return self._decode(row["payload"]) if row else None

    def upsert_task(self, run_id, task_id, payload):
        with self.connection:
            self.connection.execute(
                "INSERT INTO tasks(run_id, task_id, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(run_id, task_id) DO UPDATE SET payload=excluded.payload",
                (run_id, task_id, json.dumps(payload, sort_keys=True)),
            )

    def get_task(self, run_id, task_id):
        row = self.connection.execute(
            "SELECT payload FROM tasks WHERE run_id = ? AND task_id = ?",
            (run_id, task_id),
        ).fetchone()
        return self._decode(row["payload"]) if row else None

    def upsert_attempt(self, run_id, task_id, attempt, payload):
        with self.connection:
            self.connection.execute(
                "INSERT INTO attempts(run_id, task_id, attempt, payload) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(run_id, task_id, attempt) DO UPDATE SET payload=excluded.payload",
                (run_id, task_id, attempt, json.dumps(payload, sort_keys=True)),
            )

    def list_attempts(self, run_id, task_id):
        rows = self.connection.execute(
            "SELECT payload FROM attempts WHERE run_id = ? AND task_id = ? ORDER BY attempt",
            (run_id, task_id),
        ).fetchall()
        return [self._decode(row["payload"]) for row in rows]

    def append_event(self, run_id, payload):
        with self.connection:
            self.connection.execute(
                "INSERT INTO events(run_id, payload) VALUES (?, ?)",
                (run_id, json.dumps(payload, sort_keys=True)),
            )

    def list_events(self, run_id):
        rows = self.connection.execute(
            "SELECT payload FROM events WHERE run_id = ? ORDER BY event_id",
            (run_id,),
        ).fetchall()
        return [self._decode(row["payload"]) for row in rows]

    def upsert_artifact(self, run_id, task_id, payload):
        with self.connection:
            self.connection.execute(
                "INSERT INTO artifacts(run_id, task_id, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(run_id, task_id) DO UPDATE SET payload=excluded.payload",
                (run_id, task_id, json.dumps(payload, sort_keys=True)),
            )

    def get_artifact(self, run_id, task_id):
        row = self.connection.execute(
            "SELECT payload FROM artifacts WHERE run_id = ? AND task_id = ?",
            (run_id, task_id),
        ).fetchone()
        return self._decode(row["payload"]) if row else None

    def record_approval(self, run_id, task_id, payload):
        with self.connection:
            self.connection.execute(
                "INSERT INTO approvals(run_id, task_id, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(run_id, task_id) DO UPDATE SET payload=excluded.payload",
                (run_id, task_id, json.dumps(payload, sort_keys=True)),
            )

    def get_approval(self, run_id, task_id):
        row = self.connection.execute(
            "SELECT payload FROM approvals WHERE run_id = ? AND task_id = ?",
            (run_id, task_id),
        ).fetchone()
        return self._decode(row["payload"]) if row else None

    def close(self):
        self.connection.close()
