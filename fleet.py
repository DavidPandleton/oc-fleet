"""oc-fleet core library.

A fleet manager for OpenCode agents over HTTP API.
"""

import base64
import json
import os
import re
import urllib.request

_PASSWORD_RE = re.compile(r"server password\s+(\S+)")

_PASSWORD_SOURCES = [
    "/tmp/oc_serve.log",
    os.path.expanduser("~/.local/share/opencode/serve.log"),
]
_SERVICE_CONFIG = os.path.expanduser("~/.config/opencode/service.json")


def _find_password(password_file=None):
    """Locate the server password.

    Order: $OPENCODE_SERVER_PASSWORD, an explicit password_file, the
    well-known serve logs, then the service config. The serve log lives in
    /tmp and can be wiped, so the durable copy and service.json are the
    safety net.
    """
    env_pw = os.environ.get("OPENCODE_SERVER_PASSWORD")
    if env_pw:
        return env_pw
    candidates = [password_file] if password_file else []
    candidates += _PASSWORD_SOURCES
    for cand in candidates:
        if not cand:
            continue
        try:
            with open(cand, encoding="utf-8") as fh:
                content = fh.read()
        except OSError:
            continue
        match = _PASSWORD_RE.search(content)
        if match:
            return match.group(1)
    try:
        with open(_SERVICE_CONFIG, encoding="utf-8") as fh:
            return json.load(fh).get("password") or None
    except (OSError, ValueError):
        return None


class Fleet:
    """Client for the OpenCode HTTP API.

    Handles session dispatch, status polling, session listing and stats,
    alongside a utility to sanitize agent output.
    """

    def __init__(self, base_url="http://127.0.0.1:4096", password_file=None):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        password = _find_password(password_file)
        if password:
            self.headers["Authorization"] = (
                "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()
            )

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

    def cancel(self, session_id):
        """Stop a running session. Returns True when the server confirms.

        `POST /api/session/{id}/interrupt` sets the session's outcome to
        "interrupted" and stops the model mid-turn (verified against a live
        server: a session told to count slowly stopped after one shell call).

        Why this exists: the orchestrator previously abandoned a timed-out
        session and immediately retried. The old session kept consuming a fleet
        slot and kept writing to the shared workdir while the retry wrote to the
        same files, so a timeout could produce two concurrent writers rather
        than one retry. Cancelling first is what makes "retry" mean "retry".

        A cancel that fails must never crash the run: the session may have
        finished on its own between the poll and the cancel, in which case the
        server rejects the interrupt and that is fine.
        """
        if not session_id:
            return False
        try:
            response = self._request("POST", f"/api/session/{session_id}/interrupt")
        except Exception:  # noqa: BLE001 - cancel is best-effort by design
            return False
        payload = self._unwrap(response)
        if isinstance(payload, dict):
            return bool(payload.get("interrupted"))
        return bool(payload)

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