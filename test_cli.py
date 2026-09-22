"""Tests for cli.py (mocking Fleet) and for the oc-fleet-wait.py helpers."""

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import cli
from fleet import Fleet as RealFleet

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "oc_fleet_wait", os.path.join(_HERE, "oc-fleet-wait.py")
)
wait = importlib.util.module_from_spec(_spec)
sys.modules["oc_fleet_wait"] = wait
_spec.loader.exec_module(wait)


class CliBase(unittest.TestCase):
    def setUp(self):
        self.parser = cli.build_parser()

    def run_cli(self, *argv):
        args = self.parser.parse_args(list(argv))
        return cli.main(list(argv))


class TestStatus(CliBase):
    @mock.patch("cli.Fleet")
    def test_status_prints_snapshot(self, fleet_cls):
        fleet = fleet_cls.return_value
        fleet.list_sessions.return_value = [
            {"id": "s1", "title": "active job"},
            {"id": "s2", "title": "done job", "time": {"updated": 1700000000000}},
        ]
        fleet.status.side_effect = [
            {"outcome": None, "last_assistant_text": None, "started": True},
            {"outcome": "done", "last_assistant_text": "ok", "started": True},
        ]
        fleet.stats.return_value = {"sessions": 2}
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = self.run_cli("status")
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("server: up", text)
        self.assertIn("active sessions: 1", text)
        self.assertIn("s1", text)
        self.assertIn("s2  done", text)
        self.assertIn("sessions=2", text)

    @mock.patch("cli.Fleet")
    def test_status_separates_never_prompted_sessions(self, fleet_cls):
        """Sesi tanpa pesan adalah "dibuat", bukan "aktif".

        Terukur: enam sesi probe kosong membuat `oc-fleet status`
        melaporkan "6 sesi aktif" padahal tidak ada apa pun berjalan.
        Siapa pun yang membaca itu akan menunggui pekerjaan yang tidak ada.

        Bedanya dari deteksi macet: sesi seperti ini tidak punya tool
        sama sekali, jadi `stuck` juga False - hanya jumlah pesan yang
        membedakannya.
        """
        fleet = fleet_cls.return_value
        fleet.list_sessions.return_value = [
            {"id": "s1", "title": "belum pernah"},
            {"id": "s2", "title": "benar-benar jalan"},
        ]
        fleet.status.side_effect = [
            {"outcome": None, "last_assistant_text": None, "started": False},
            {"outcome": None, "last_assistant_text": "bekerja", "started": True},
        ]
        fleet.stats.return_value = {}
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = self.run_cli("status")
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("active sessions: 1", text)
        self.assertIn("created but never prompted: 1", text)

    @mock.patch("cli.Fleet")
    def test_status_marks_stuck_sessions(self, fleet_cls):
        """Sesi yang macet ditandai, bukan diam-diam dihitung aktif."""
        fleet = fleet_cls.return_value
        fleet.list_sessions.return_value = [{"id": "s1", "title": "nyangkut"}]
        fleet.status.return_value = {
            "outcome": None, "last_assistant_text": None, "started": True,
            "tool_running": 1, "stuck_seconds": 1244.0, "stuck": True,
        }
        fleet.stats.return_value = {}
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.run_cli("status")
        text = out.getvalue()
        self.assertIn("MACET", text)
        self.assertIn("1244", text)

    @mock.patch("cli.Fleet")
    def test_status_empty_fleet(self, fleet_cls):
        fleet = fleet_cls.return_value
        fleet.list_sessions.return_value = []
        fleet.stats.return_value = {}
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = self.run_cli("status")
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("server: up", text)
        self.assertIn("active sessions: 0", text)
        self.assertIn("(none)", text)

    @mock.patch("cli.Fleet")
    def test_status_api_error_exits_2(self, fleet_cls):
        fleet = fleet_cls.return_value
        fleet.list_sessions.side_effect = OSError("connection refused")
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = self.run_cli("status")
        self.assertEqual(code, 2)
        self.assertIn("api/connection failure", err.getvalue())


class TestDispatch(CliBase):
    @mock.patch("cli.Fleet")
    def test_dispatch_forwards_args(self, fleet_cls):
        fleet = fleet_cls.return_value
        fleet.dispatch.return_value = "abc123"
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = self.run_cli("dispatch", "TASK", "--workdir", "/tmp/wd", "--title", "T", "--model", "cutad/qwen3-8-flash-next")
        self.assertEqual(code, 0)
        self.assertIn("dispatched: abc123", out.getvalue())
        fleet.dispatch.assert_called_once_with(
            "TASK", "/tmp/wd", title="T", model="cutad/qwen3-8-flash-next"
        )

    @mock.patch("cli.subprocess.Popen")
    @mock.patch("cli.Fleet")
    def test_dispatch_detach_spawns_waiter(self, fleet_cls, popen):
        fleet = fleet_cls.return_value
        fleet.dispatch.return_value = "abc123"
        code = self.run_cli("dispatch", "TASK", "--workdir", "/tmp/wd", "--detach")
        self.assertEqual(code, 0)
        popen.assert_called_once()
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[0], sys.executable)
        self.assertTrue(cmd[1].endswith("oc-fleet-wait.py"))
        self.assertEqual(cmd[2], "abc123")
        self.assertIn("--base-url", cmd)
        self.assertTrue(popen.call_args.kwargs.get("start_new_session"))

    @mock.patch("cli.Fleet")
    def test_dispatch_api_error_exits_2(self, fleet_cls):
        fleet = fleet_cls.return_value
        fleet.dispatch.side_effect = KeyError("id")
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = self.run_cli("dispatch", "TASK", "--workdir", "/tmp/wd")
        self.assertEqual(code, 2)
        self.assertIn("api/connection failure", err.getvalue())

    def test_dispatch_requires_task(self):
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["dispatch", "--workdir", "/tmp/wd"])

    @mock.patch("cli.Fleet")
    def test_dispatch_bad_model_exits_2_without_traceback(self, fleet_cls):
        """Model salah ketik harus jadi pesan jelas, bukan traceback.

        ValueError dari _parse_model() adalah kesalahan pengguna, jadi
        ditangani di cmd_dispatch - bukan lewat API_ERRORS, karena
        'api/connection failure' akan menyesatkan.
        """
        fleet = fleet_cls.return_value
        fleet.dispatch.side_effect = ValueError("model is missing a provider: '/foo'")
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = self.run_cli("dispatch", "TASK", "--workdir", "/tmp/wd", "--model", "/foo")
        self.assertEqual(code, 2)
        self.assertIn("model is missing a provider", err.getvalue())
        self.assertNotIn("api/connection failure", err.getvalue())


class TestSessions(CliBase):
    @mock.patch("cli.Fleet")
    def test_sessions_prints_table(self, fleet_cls):
        fleet = fleet_cls.return_value
        fleet.list_sessions.return_value = [
            {"id": "abc", "title": "first", "time": {"updated": 1700000000000}},
            {"id": "def", "title": "second", "time": {"updated": 1700000060000}},
        ]
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = self.run_cli("sessions", "5")
        self.assertEqual(code, 0)
        fleet.list_sessions.assert_called_once_with(limit=5)
        text = out.getvalue()
        self.assertIn("ID", text)
        self.assertIn("TITLE", text)
        self.assertIn("abc", text)
        self.assertIn("first", text)
        self.assertIn("def", text)

    @mock.patch("cli.Fleet")
    def test_sessions_default_limit(self, fleet_cls):
        fleet = fleet_cls.return_value
        fleet.list_sessions.return_value = []
        code = self.run_cli("sessions")
        self.assertEqual(code, 0)
        fleet.list_sessions.assert_called_once_with(limit=10)


class TestShow(CliBase):
    def setUp(self):
        super().setUp()
        # keep sanitization real so captured output contains plain strings
        self.patcher = mock.patch.object(cli, "Fleet")
        self.fleet_cls = self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.fleet_cls.sanitize = RealFleet.sanitize

    def _fleet(self):
        return self.fleet_cls.return_value

    def test_show_ok_and_sanitized(self):
        fleet = self._fleet()
        fleet.status.return_value = {
            "outcome": "done",
            "last_assistant_text": "ok \u2014 fine \u2013 really",
        }
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = self.run_cli("show", "abc123")
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("session: abc123", text)
        self.assertIn("outcome: done", text)
        self.assertIn("ok - fine - really", text)
        self.assertNotIn("\u2014", text)
        fleet.status.assert_called_once_with("abc123")

    def test_show_failed_outcome_exits_1(self):
        fleet = self._fleet()
        fleet.status.return_value = {"outcome": "crashed", "last_assistant_text": None}
        code = self.run_cli("show", "abc123")
        self.assertEqual(code, 1)

    def test_show_pending_exits_0(self):
        fleet = self._fleet()
        fleet.status.return_value = {"outcome": None, "last_assistant_text": "working"}
        code = self.run_cli("show", "abc123")
        self.assertEqual(code, 0)

    def test_show_api_error_exits_2(self):
        fleet = self._fleet()
        fleet.status.side_effect = ConnectionRefusedError()
        code = self.run_cli("show", "abc123")
        self.assertEqual(code, 2)


class TestStats(CliBase):
    @mock.patch("cli.Fleet")
    def test_stats_prints_pairs(self, fleet_cls):
        fleet = fleet_cls.return_value
        fleet.stats.return_value = {"sessions": 7, "prompts": 9}
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = self.run_cli("stats")
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("sessions=7", text)
        self.assertIn("prompts=9", text)

    @mock.patch("cli.Fleet")
    def test_stats_api_error_exits_2(self, fleet_cls):
        fleet = fleet_cls.return_value
        fleet.stats.side_effect = OSError("boom")
        code = self.run_cli("stats")
        self.assertEqual(code, 2)


class TestSseHelpers(unittest.TestCase):
    def test_is_completion_types(self):
        self.assertTrue(cli._is_completion({"type": "session.idle"}))
        self.assertTrue(cli._is_completion({"type": "session.execution.succeeded"}))
        self.assertTrue(cli._is_completion({"type": "session.execution.failed"}))
        self.assertFalse(cli._is_completion({"type": "session.message"}))
        self.assertFalse(cli._is_completion("garbage"))

    def test_event_session_id_and_outcome(self):
        event = {"type": "session.execution.succeeded", "properties": {"sessionID": "s42"}}
        self.assertEqual(cli._event_session_id(event), "s42")
        self.assertEqual(cli._event_outcome(event), "done")
        event2 = {"type": "session.idle", "properties": {"outcome": "crashed"}}
        self.assertEqual(cli._event_outcome(event2), "crashed")


class TestWaitForOutcome(unittest.TestCase):
    def _fleet(self, outcomes):
        fleet = mock.Mock()
        fleet.status.side_effect = [
            {"outcome": outcome, "last_assistant_text": "t"} for outcome in outcomes
        ]
        return fleet

    def test_resolves_on_first_poll(self):
        fleet = self._fleet(["done"])
        with mock.patch("oc_fleet_wait.time.sleep") as sleep, \
             mock.patch("oc_fleet_wait.time.monotonic", return_value=1000.0):
            outcome, text = wait.wait_for_outcome(fleet, "abc", timeout=1800, interval=5)
        self.assertEqual(outcome, "done")
        self.assertEqual(text, "t")
        self.assertEqual(fleet.status.call_count, 1)
        sleep.assert_not_called()

    def test_keeps_polling_until_outcome(self):
        fleet = self._fleet([None, None, "done"])
        with mock.patch("oc_fleet_wait.time.sleep") as sleep, \
             mock.patch("oc_fleet_wait.time.monotonic", side_effect=[1000.0, 1005.0, 1010.0, 1015.0]):
            outcome, text = wait.wait_for_outcome(fleet, "abc", timeout=1800, interval=5)
        self.assertEqual(outcome, "done")
        self.assertEqual(fleet.status.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_times_out_with_none_outcome(self):
        fleet = self._fleet([None, None])
        # deadline 2900: calls at 100 (setup), 100.5 (poll 1), 3000 (poll 2 -> expired)
        with mock.patch("oc_fleet_wait.time.sleep") as sleep, \
             mock.patch("oc_fleet_wait.time.monotonic", side_effect=[100.0, 100.5, 3000.0]):
            outcome, text = wait.wait_for_outcome(fleet, "abc", timeout=2800, interval=5)
        self.assertIsNone(outcome)
        self.assertEqual(text, "t")
        self.assertEqual(sleep.call_count, 1)

    def test_transient_api_errors_are_tolerated(self):
        fleet = mock.Mock()
        fleet.status.side_effect = [OSError("blip"), {"outcome": "done", "last_assistant_text": "t"}]
        with mock.patch("oc_fleet_wait.time.sleep"), \
             mock.patch("oc_fleet_wait.time.monotonic", side_effect=[100.0, 105.0]):
            outcome, _ = wait.wait_for_outcome(fleet, "abc", timeout=1800, interval=5)
        self.assertEqual(outcome, "done")

    def test_poll_errors_outside_api_errors_are_tolerated(self):
        """Blip di luar API_ERRORS tidak boleh mematikan waiter.

        Bentuk yang sama dengan cacat orchestrator #4: exception di luar
        tuple yang dijaga lolos dan mematikan proses. Waiter ini detached,
        jadi kalau mati tidak ada yang tahu. Tes lama hanya memakai OSError.
        """
        for exc in (
            RuntimeError("malformed response"),
            TypeError("unexpected shape"),
            Exception("anything at all"),
        ):
            with self.subTest(exc=type(exc).__name__):
                fleet = mock.Mock()
                fleet.status.side_effect = [
                    exc,
                    {"outcome": "done", "last_assistant_text": "t"},
                ]
                with mock.patch("oc_fleet_wait.time.sleep"), \
                     mock.patch("oc_fleet_wait.time.monotonic", side_effect=[100.0, 105.0]):
                    outcome, _ = wait.wait_for_outcome(fleet, "abc", timeout=1800, interval=5)
                self.assertEqual(outcome, "done")


class TestWaitFileHelpers(unittest.TestCase):
    def test_write_result_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(wait, "RESULT_PREFIX", tmp + "/oc_result_"):
                path = wait.write_result_file("abc", "done", "hello")
            self.assertEqual(os.path.dirname(path), tmp)
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        self.assertEqual(payload["session_id"], "abc")
        self.assertEqual(payload["outcome"], "done")
        self.assertEqual(payload["last_assistant_text"], "hello")

    def test_log_alert_appends_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "alerts.log")
            with mock.patch.object(wait, "ALERTS_LOG", log_path):
                wait.log_alert("ALERT line one")
                wait.log_alert("ALERT line two")
            with open(log_path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        self.assertEqual(lines, ["ALERT line one", "ALERT line two"])


class TestWaitMain(unittest.TestCase):
    def test_main_success_writes_result_and_alert(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "alerts.log")
            with mock.patch.object(wait, "ALERTS_LOG", log_path), \
                 mock.patch.object(wait, "RESULT_PREFIX", tmp + "/oc_result_"), \
                 mock.patch.object(
                     wait,
                     "wait_for_outcome",
                     return_value=("done", "the answer"),
                 ) as wait_fn, \
                 mock.patch.object(wait, "Fleet") as fleet_cls, \
                 mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                code = wait.main(["abc123", "--timeout", "60", "--base-url", "http://example:1"])
            self.assertEqual(code, 0)
            wait_fn.assert_called_once()
            fleet_cls.assert_called_once()
            self.assertIn("result=", out.getvalue())
            result_files = [f for f in os.listdir(tmp) if f.startswith("oc_result_")]
            self.assertEqual(len(result_files), 1)
            with open(os.path.join(tmp, result_files[0]), encoding="utf-8") as fh:
                payload = json.load(fh)
            self.assertEqual(payload["session_id"], "abc123")
            self.assertEqual(payload["outcome"], "done")
            with open(log_path, encoding="utf-8") as fh:
                line = fh.read().strip()
            self.assertIn("session=abc123", line)
            self.assertIn("outcome=done", line)
            self.assertIn("text=the answer", line)

    def test_main_timeout_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "alerts.log")
            with mock.patch.object(wait, "ALERTS_LOG", log_path), \
                 mock.patch.object(wait, "RESULT_PREFIX", tmp + "/oc_result_"), \
                 mock.patch.object(wait, "wait_for_outcome", return_value=(None, None)), \
                 mock.patch.object(wait, "Fleet"), \
                 mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                code = wait.main(["abc123", "--timeout", "60"])
            self.assertEqual(code, 1)
            self.assertIn("outcome=None", out.getvalue())
            with open(log_path, encoding="utf-8") as fh:
                line = fh.read().strip()
            self.assertIn("outcome=timeout after 60s", line)

    def test_main_rejects_non_positive_interval(self):
        """--interval 0 atau negatif ditolak, bukan traceback atau spam.

        Negatif mencapai time.sleep() dan melempar ValueError, yang TIDAK
        dicakup `except API_ERRORS` (bukan OSError) - jadi CLI mencetak
        traceback. Nol lebih buruk diam-diam: tidak pernah tidur, dan
        terukur 17.202 poll per detik.
        """
        for bad in ("0", "-1", "-0.5"):
            with self.subTest(interval=bad):
                with mock.patch.object(wait, "Fleet"), \
                     mock.patch.object(wait, "wait_for_outcome") as wait_fn, \
                     mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                    code = wait.main(["abc123", "--interval", bad])
                self.assertEqual(code, 2)
                self.assertIn("--interval", err.getvalue())
                wait_fn.assert_not_called()

    def test_main_rejects_non_finite_timeout(self):
        """--timeout nan tidak boleh menggantung selamanya.

        nan lolos parse dan semua perbandingannya False, jadi
        `time.monotonic() >= deadline` tidak pernah menyala dan waiter
        poll tanpa henti. Direproduksi: ia melewati timeout eksternal 400
        detik tanpa tanda berhenti.
        """
        for bad in ("nan", "inf"):
            with self.subTest(timeout=bad):
                with mock.patch.object(wait, "Fleet"), \
                     mock.patch.object(wait, "wait_for_outcome") as wait_fn, \
                     mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                    code = wait.main(["abc123", "--timeout", bad])
                self.assertEqual(code, 2)
                self.assertIn("--timeout", err.getvalue())
                wait_fn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
