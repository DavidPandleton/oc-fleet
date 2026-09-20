"""Tests for web/dashboard.py.

Uses pure-function unit tests plus a local loopback stub server (127.0.0.1)
so no external network is required. The Fleet dependency is mocked so no
real OpenCode API is contacted.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
import base64
from unittest import mock

# Make the repo root importable regardless of the working directory.
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TEST_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from http.server import ThreadingHTTPServer  # noqa: E402

import web.dashboard as dash  # noqa: E402
from web.dashboard import DashboardHandler  # noqa: E402


class _StubHandler(DashboardHandler):
    fleet = None

    def log_message(self, *args):
        pass


def _start_server():
    """Start a ThreadingHTTPServer on 127.0.0.1:0 and return (server, port)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    _StubHandler.fleet = None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


class ValidateDispatchTest(unittest.TestCase):
    def test_valid_payload_is_normalized(self):
        payload = dash.validate_dispatch(
            {"task": " fix bugs ", "workdir": "/repo", "model": "cutad/x", "title": "t"}
        )
        self.assertEqual(
            payload, {"task": "fix bugs", "workdir": "/repo", "model": "cutad/x", "title": "t"}
        )

    def test_empty_optionals_become_empty_strings(self):
        payload = dash.validate_dispatch({"task": "go", "workdir": "/w", "model": "", "title": ""})
        self.assertEqual(payload, {"task": "go", "workdir": "/w", "model": "", "title": ""})

    def test_missing_task_raises(self):
        with self.assertRaises(ValueError):
            dash.validate_dispatch({"workdir": "/w"})

    def test_missing_workdir_raises(self):
        with self.assertRaises(ValueError):
            dash.validate_dispatch({"task": "go"})

    def test_non_dict_raises(self):
        with self.assertRaises(ValueError):
            dash.validate_dispatch(["task"])


class EventFilterTest(unittest.TestCase):
    def test_execution_succeeded_is_included(self):
        self.assertTrue(dash.is_execution_event({"type": "session.execution.succeeded"}))

    def test_execution_failed_is_included(self):
        self.assertTrue(dash.is_execution_event({"type": "session.execution.failed"}))

    def test_non_execution_events_excluded(self):
        samples = [
            {"type": "session.idle"},
            {"type": "session.created"},
            {"type": "message"},
            {},
            "not a dict",
        ]
        for event in samples:
            self.assertFalse(dash.is_execution_event(event))

    def test_filter_execution_events(self):
        events = [
            {"type": "session.execution.succeeded"},
            {"type": "session.idle"},
            {"type": "session.execution.failed"},
        ]
        self.assertEqual(
            [e["type"] for e in dash.filter_execution_events(events)],
            ["session.execution.succeeded", "session.execution.failed"],
        )


class SseFrameTest(unittest.TestCase):
    def test_frame_contains_event_and_data_lines(self):
        frame = dash.sse_frame({"type": "session.execution.succeeded", "properties": {"id": "s1"}})
        text = frame.decode("utf-8")
        self.assertTrue(text.startswith("event: session.execution.succeeded\n"))
        self.assertIn("\ndata: ", text)
        self.assertTrue(text.endswith("\n\n"))
        parsed = json.loads(text.split("\ndata: ", 1)[1].strip())
        self.assertEqual(parsed["properties"]["id"], "s1")

    def test_frame_without_type_is_data_only(self):
        frame = dash.sse_frame({"properties": {"id": "s1"}})
        text = frame.decode("utf-8")
        self.assertTrue(text.startswith("data: "))
        self.assertNotIn("event:", text)

    def test_frame_wraps_non_dict(self):
        frame = dash.sse_frame(["nope"])
        self.assertEqual(frame, b"data: {}\n\n")


class SseLineTest(unittest.TestCase):
    def test_sse_lines_accumulate_an_event(self):
        state = {"name": None, "data": []}
        dash._sse_line(b"event: session.execution.succeeded", state)
        dash._sse_line(b'data: {"sessionID": "s1"}', state)
        event = dash._sse_line(b"", state)
        self.assertEqual(event["type"], "session.execution.succeeded")
        self.assertEqual(event["sessionID"], "s1")

    def test_sse_multiple_data_lines_joined(self):
        state = {"name": None, "data": []}
        dash._sse_line(b"data: {\"a\":", state)
        dash._sse_line(b"data: 1}", state)
        event = dash._sse_line(b"", state)
        self.assertEqual(event, {"a": 1})

    def test_sse_comment_and_other_fields_ignored(self):
        state = {"name": None, "data": []}
        result = dash._sse_line(b": a comment", state)
        self.assertIsNone(result)


class AuthHeadersTest(unittest.TestCase):
    def test_headers_from_password_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as fh:
            fh.write("server password s3cret\n")
            path = fh.name
        headers = dash.load_auth_headers(path)
        expected = "Basic " + base64.b64encode(b"opencode:s3cret").decode()
        self.assertEqual(headers["Authorization"], expected)

    def test_headers_missing_file(self):
        headers = dash.load_auth_headers("/nonexistent/path/log")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["Content-Type"], "application/json")


class RenderIndexTest(unittest.TestCase):
    def test_html_has_device_viewport_and_no_emdash(self):
        page = dash.render_index(8787)
        self.assertIn('name="viewport"', page)
        self.assertNotIn("\u2014", page)
        self.assertNotIn("\u2013", page)

    def test_html_contains_vital_sections(self):
        page = dash.render_index(8787)
        for token in [
            "recent sessions",
            "dispatch",
            "id=\"st-sessions\"",
            "id=\"rows\"",
            "/api/dispatch",
            "/api/events",
            "setInterval(refresh, 5000)",
            '<option value="cutad/qwen3-8-flash-next"',
        ]:
            self.assertIn(token, page)


class EndpointTest(unittest.TestCase):
    def setUp(self):
        self.server, self.port = _start_server()
        self.addCleanup(self.server.server_close)

    def _post(self, path, payload):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        request = __import__("urllib.request", fromlist=["Request"]).Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return __import__("urllib.request", fromlist=["urlopen"]).urlopen(request)

    def test_dispatch_endpoint_forwards_and_returns_session_id(self):
        fleet = mock.Mock()
        fleet.dispatch.return_value = "ses_123"
        _StubHandler.fleet = fleet
        response = self._post("/api/dispatch", {"task": "do work", "workdir": "/repo"})
        body = json.loads(response.read().decode())
        self.assertEqual(response.status, 200)
        self.assertEqual(body["session_id"], "ses_123")
        fleet.dispatch.assert_called_once_with("do work", "/repo", title="", model="")

    def test_dispatch_endpoint_400_on_missing_task(self):
        _StubHandler.fleet = mock.Mock()
        url = "http://127.0.0.1:%d/api/dispatch" % self.port
        import urllib.request

        request = urllib.request.Request(
            url, data=json.dumps({"workdir": "/repo"}).encode(), headers={}, method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(request)
        self.assertEqual(cm.exception.code, 400)
        body = json.loads(cm.exception.read().decode())
        self.assertIn("task", body["error"])

    def test_dispatch_endpoint_502_when_upstream_fails(self):
        fleet = mock.Mock()
        fleet.dispatch.side_effect = OSError("boom")
        _StubHandler.fleet = fleet
        url = "http://127.0.0.1:%d/api/dispatch" % self.port
        import urllib.request

        request = urllib.request.Request(
            url,
            data=json.dumps({"task": "x", "workdir": "/w"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(request)
        self.assertEqual(cm.exception.code, 502)

    def test_sessions_endpoint_returns_sessions_json(self):
        fleet = mock.Mock()
        fleet.list_sessions.return_value = [{"id": "s1", "title": "t1"}]
        _StubHandler.fleet = fleet
        import urllib.request

        response = urllib.request.urlopen("http://127.0.0.1:%d/api/sessions" % self.port)
        body = json.loads(response.read().decode())
        self.assertEqual(body["sessions"], [{"id": "s1", "title": "t1"}])

    def test_stats_endpoint_returns_stats_json(self):
        fleet = mock.Mock()
        fleet.stats.return_value = {"session_count": 3, "success_rate": 97}
        _StubHandler.fleet = fleet
        import urllib.request

        response = urllib.request.urlopen(
            "http://127.0.0.1:%d/api/experimental/session/stats" % self.port
        )
        body = json.loads(response.read().decode())
        self.assertEqual(body, {"session_count": 3, "success_rate": 97})


if __name__ == "__main__":
    unittest.main()