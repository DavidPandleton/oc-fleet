"""oc-fleet core library.

A fleet manager for OpenCode agents over HTTP API.
"""

import base64
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
        self.headers = {"Content-Type": "application/json"}
        try:
            with open(password_file, encoding="utf-8") as fh:
                content = fh.read()
            match = _PASSWORD_RE.search(content)
            if match:
                password = match.group(1)
                self.headers["Authorization"] = (
                    "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()
                )
        except OSError:
            self.headers = {"Content-Type": "application/json"}

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

    @staticmethod
    def _unwrap(payload):
        """Unwrap a {'data': ...} response envelope when present, else return as-is."""
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    def dispatch(self, task, workdir, title="", model=""):
        """Create a session, post the task prompt, and return its session id."""
        body = {"title": title, "location": {"directory": workdir}}
        if model:
            provider, _, model_id = model.partition("/")
            if _ and model_id:
                body["model"] = {"providerID": provider, "id": model_id}
        session = self._request("POST", "/api/session", body)
        session = self._unwrap(session) or {}
        session_id = session["id"]
        self._request("POST", f"/api/session/{session_id}/prompt", {"text": task})
        return session_id

    def status(self, session_id):
        """Return the outcome and last assistant text for a session."""
        response = self._request("GET", f"/api/session/{session_id}/message")
        messages = self._unwrap(response)
        if not isinstance(messages, list):
            messages = []
        outcome = None
        last_text = None
        for message in messages:
            if message.get("type") == "idle" and message.get("outcome"):
                outcome = message["outcome"]
            assistant_text = self._assistant_text(message)
            if assistant_text is not None:
                last_text = assistant_text
        return {"outcome": outcome, "last_assistant_text": last_text}

    def list_sessions(self, limit=10):
        """Return a list of session dicts, newest first."""
        response = self._request("GET", f"/api/session?limit={limit}&order=desc")
        sessions = self._unwrap(response)
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