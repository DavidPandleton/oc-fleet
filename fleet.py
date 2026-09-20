"""oc-fleet core library.

A fleet manager for OpenCode agents over HTTP API.
"""

import json
import re
import urllib.request

_PASSWORD_RE = re.compile(r"server password\s+(\S+)")


class Fleet:
    """Client for the OpenCode HTTP API.

    Handles session dispatch, status polling, session listing and stats,
    alongside a utility to sanitize agent output.
    """

    def __init__(self, base_url="http://127.0.0.1:4096", password_file="/tmp/oc_serve.log"):
        self.base_url = base_url.rstrip("/")
        self.headers = {}
        try:
            with open(password_file, encoding="utf-8") as fh:
                content = fh.read()
            match = _PASSWORD_RE.search(content)
            if match:
                self.headers["Authorization"] = "Bearer " + match.group(1)
        except OSError:
            self.headers = {}

    @staticmethod
    def sanitize(text):
        """Replace em-dash and en-dash so they are never emitted."""
        return text.replace("\u2014", "-").replace("\u2013", "-")

    def _request(self, method, path, data=None):
        url = self.base_url + path
        payload = None
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
        request = urllib.request.Request(url, data=payload, headers=self.headers, method=method)
        with urllib.request.urlopen(request) as response:
            body = response.read()
        if not body:
            return None
        return json.loads(body)

    def dispatch(self, task, workdir, title="", model=""):
        """Create a session, post the task prompt, and return its session id."""
        session = self._request("POST", "/api/session", {"title": title, "model": model, "workdir": workdir})
        session_id = session["id"]
        self._request("POST", f"/api/session/{session_id}/prompt", {"prompt": task})
        return session_id

    def status(self, session_id):
        """Return the outcome and last assistant text for a session."""
        messages = self._request("GET", f"/api/session/{session_id}/message")
        outcome = None
        last_text = None
        for message in messages:
            if message.get("type") == "idle" and message.get("result"):
                outcome = message["result"]
            assistant_text = self._assistant_text(message)
            if assistant_text is not None:
                last_text = assistant_text
        return {"outcome": outcome, "last_assistant_text": last_text}

    def list_sessions(self, limit=10):
        """Return a list of session dicts, newest first."""
        sessions = self._request("GET", f"/api/session?limit={limit}&order=desc")
        return sessions if isinstance(sessions, list) else []

    def stats(self):
        """Return the session stats dict."""
        response = self._request("GET", "/api/experimental/session/stats")
        if isinstance(response, dict) and "data" in response:
            return response["data"]
        return response if isinstance(response, dict) else {}

    @staticmethod
    def _assistant_text(message):
        if message.get("type") != "message":
            return None
        role = message.get("info", {}).get("role")
        if role not in ("assistant", "user"):
            return None
        parts = []
        for part in message.get("content", []):
            if part.get("type") in ("text", "reasoning"):
                parts.append(part.get("text", ""))
        if not parts:
            return None
        return "\n".join(parts)