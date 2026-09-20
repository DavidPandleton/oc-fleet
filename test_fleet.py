"""Tests for fleet.Fleet using mocked urllib.request.urlopen."""

import json
import unittest
from unittest import mock

from fleet import Fleet


def _context(payload):
    """Return a mock HTTPResponse context whose read() yields the JSON payload."""
    raw = json.dumps(payload).encode("utf-8")
    context = mock.Mock()
    context.read.return_value = raw
    context.__enter__ = lambda s: context
    context.__exit__ = lambda *a: None
    return context


class FleetTestCase(unittest.TestCase):
    def setUp(self):
        self.fleet = Fleet(base_url="http://127.0.0.1:4096", password_file="/nonexistent")
        self.fleet.headers["Authorization"] = "Bearer testpass"

    @mock.patch("urllib.request.urlopen")
    def test_dispatch_returns_session_id(self, urlopen):
        responses = [{"id": "abc123", "title": "t"}, None]
        urlopen.side_effect = [_context(r) for r in responses]
        sid = self.fleet.dispatch("do the thing", "/tmp/work", title="TT", model="deepseek")
        self.assertEqual(sid, "abc123")
        calls = urlopen.call_args_list
        self.assertEqual(calls[0].args[0].full_url, "http://127.0.0.1:4096/api/session")
        self.assertEqual(calls[1].args[0].full_url, "http://127.0.0.1:4096/api/session/abc123/prompt")

    @mock.patch("urllib.request.urlopen")
    def test_dispatch_posts_prompt_and_workdir(self, urlopen):
        responses = [{"id": "abc123", "title": "t"}, None]
        urlopen.side_effect = [_context(r) for r in responses]
        self.fleet.dispatch("hello", "/tmp/wd", title="X", model="y")
        prompt_call = urlopen.call_args_list[1].args[0]
        payload = json.loads(prompt_call.data.decode())
        self.assertEqual(payload["prompt"], "hello")
        session_call = urlopen.call_args_list[0].args[0]
        session_body = json.loads(session_call.data.decode())
        self.assertEqual(session_body["workdir"], "/tmp/wd")
        self.assertEqual(session_body["title"], "X")
        self.assertEqual(session_body["model"], "y")

    @mock.patch("urllib.request.urlopen")
    def test_status_outcome(self, urlopen):
        messages = [
            {"type": "message", "info": {"role": "assistant"}, "content": [{"type": "text", "text": "first"}]},
            {"type": "message", "info": {"role": "user"}, "content": [{"type": "text", "text": "prompt"}]},
            {"type": "idle", "result": "crashed"},
        ]
        urlopen.return_value = _context(messages)
        result = self.fleet.status("abc123")
        self.assertEqual(result["outcome"], "crashed")

    @mock.patch("urllib.request.urlopen")
    def test_status_last_assistant_text(self, urlopen):
        messages = [
            {"type": "message", "info": {"role": "assistant"}, "content": [{"type": "text", "text": "first"}]},
            {"type": "message", "info": {"role": "assistant"}, "content": [{"type": "text", "text": "second"}]},
            {"type": "idle", "result": "done"},
        ]
        urlopen.return_value = _context(messages)
        result = self.fleet.status("abc123")
        self.assertEqual(result["last_assistant_text"], "second")
        self.assertEqual(result["outcome"], "done")

    @mock.patch("urllib.request.urlopen")
    def test_status_no_idle_message(self, urlopen):
        messages = [
            {"type": "message", "info": {"role": "assistant"}, "content": [{"type": "text", "text": "work"}]},
        ]
        urlopen.return_value = _context(messages)
        result = self.fleet.status("abc123")
        self.assertIsNone(result["outcome"])
        self.assertEqual(result["last_assistant_text"], "work")

    @mock.patch("urllib.request.urlopen")
    def test_list_sessions_parses_bare_list(self, urlopen):
        sessions = [{"id": "s2"}, {"id": "s1"}]
        urlopen.return_value = _context(sessions)
        result = self.fleet.list_sessions(limit=5)
        self.assertEqual(result, sessions)
        url = urlopen.call_args.args[0].full_url
        self.assertIn("limit=5", url)
        self.assertIn("order=desc", url)

    @mock.patch("urllib.request.urlopen")
    def test_list_sessions_default_limit(self, urlopen):
        urlopen.return_value = _context([])
        self.fleet.list_sessions()
        url = urlopen.call_args.args[0].full_url
        self.assertIn("limit=10", url)

    @mock.patch("urllib.request.urlopen")
    def test_stats_parses_wrapped_data(self, urlopen):
        urlopen.return_value = _context({"data": {"sessions": 3}})
        result = self.fleet.stats()
        self.assertEqual(result, {"sessions": 3})
        url = urlopen.call_args.args[0].full_url
        self.assertIn("/api/experimental/session/stats", url)

    def test_sanitize_replaces_emdash(self):
        self.assertEqual(Fleet.sanitize("ok \u2014 fine"), "ok - fine")

    def test_sanitize_replaces_endash(self):
        self.assertEqual(Fleet.sanitize("10 \u2013 20"), "10 - 20")

    def test_sanitize_both_and_clean(self):
        self.assertEqual(Fleet.sanitize("a \u2014 b \u2013 c"), "a - b - c")
        self.assertEqual(Fleet.sanitize("already clean"), "already clean")


if __name__ == "__main__":
    unittest.main()