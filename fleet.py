"""oc-fleet core library.

A fleet manager for OpenCode agents over HTTP API.
"""

import base64
import json
import os
import re
import urllib.error
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
        """Replace em-dash and en-dash so they are never emitted.

        Agent output reaches humans through several paths - `oc-fleet show`,
        the result JSON written by `oc-fleet-wait.py`, and the orchestrator's
        `last_text`. Only `show` used to call this, so an em-dash typed by an
        agent leaked into the other two. Rather than remember to call it at
        each call site, `status()` now sanitises as it reads, so every path
        downstream inherits it.
        """
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

    @staticmethod
    def _parse_model(model):
        """Parse "provider/model-id" into a body fragment, or None to omit.

        Returns None when `model` is empty (use the server default). A
        non-empty `model` must be a well formed "provider/model-id": both
        halves non-empty, surrounding whitespace trimmed. Anything else is a
        typo, and it raises rather than being sent or silently dropped.

        Why raising instead of dropping: the server accepts a malformed
        model - `POST /api/session` with `providerID: ""` answers 200 and
        stores it (verified against a live server). The session is then
        created with a provider that cannot resolve, and it only fails much
        later, where nobody connects it back to the typo. Refusing here is
        what keeps a bad model from becoming a bad session.
        """
        model = model.strip()
        if not model:
            return None
        provider, sep, model_id = model.partition("/")
        provider = provider.strip()
        model_id = model_id.strip()
        if not sep:
            raise ValueError(
                f"model must be 'provider/model-id', got {model!r} (no '/' separator)"
            )
        if not provider:
            raise ValueError(f"model is missing a provider: {model!r}")
        if not model_id:
            raise ValueError(f"model is missing a model id: {model!r}")
        return {"providerID": provider, "id": model_id}

    def dispatch(self, task, workdir, title="", model=""):
        """Create a session, post the task prompt, and return its session id.

        Both arguments are validated before any HTTP call, because the
        server accepts a bad value and only fails much later:

        * an empty ``workdir`` creates a session rooted at the wrong place,
          and the first file the agent writes lands somewhere unintended;
        * a response without an ``id`` used to surface as a bare
          ``KeyError: 'id'``, which names neither the call nor the shape
          that came back.

        Both raise here instead, so the failure points at the cause.
        """
        if not isinstance(task, str) or not task.strip():
            raise ValueError(f"task must be non-empty text, got {task!r}")
        if not isinstance(workdir, str) or not workdir.strip():
            raise ValueError(f"workdir must be non-empty text, got {workdir!r}")

        body = {"title": title, "location": {"directory": workdir}}
        if model:
            parsed = self._parse_model(model)
            if parsed is not None:
                body["model"] = parsed
        session = self._unwrap(self._request("POST", "/api/session", body))
        if not isinstance(session, dict) or not session.get("id"):
            raise RuntimeError(
                "POST /api/session did not return a session id; "
                f"got {type(session).__name__} {session!r}"
            )
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
            # Entries are not guaranteed to be objects; a bare string or null
            # in `data` made `message.get` raise AttributeError. Skip them
            # rather than failing the whole status read.
            if not isinstance(message, dict):
                continue
            if message.get("type") == "idle" and message.get("outcome"):
                outcome = message["outcome"]
            assistant_text = self._assistant_text(message)
            if assistant_text is not None:
                # Sanitise at the boundary rather than at each call site.
                # `oc-fleet show` called Fleet.sanitize explicitly, but the
                # result JSON from `oc-fleet-wait.py` and the orchestrator's
                # `last_text` did not, so an em-dash from an agent leaked
                # into both. Doing it here covers every consumer.
                last_text = self.sanitize(assistant_text)
        return {"outcome": outcome, "last_assistant_text": last_text}

    def cancel(self, session_id):
        """Stop a running session. Tri-state return.

        `POST /api/session/{id}/interrupt` sets the session's outcome to
        "interrupted" and stops the model mid-turn (verified against a live
        server: a session told to count slowly stopped after one shell call).

        Why this exists: the orchestrator previously abandoned a timed-out
        session and immediately retried. The old session kept consuming a fleet
        slot and kept writing to the shared workdir while the retry wrote to the
        same files, so a timeout could produce two concurrent writers rather
        than one retry. Cancelling first is what makes "retry" mean "retry".

        Returns:
            True  - the server confirmed the interrupt
            False - nothing to stop: the session had already finished, or was
                    never started. Verified against a live server, which
                    answers 200 `{"interrupted": false}` for both.
            None  - the cancel did not reach a verdict: the session does not
                    exist (404), credentials were rejected (401), or the server
                    could not be reached. These are real failures, not "already
                    finished", and the caller should say so.

        Why tri-state: a plain bool collapsed five different outcomes into
        False. The orchestrator prints "stopped" only on truthy, so a cancel
        that failed because the server was down looked identical to one where
        the session had already finished - and the retry then ran alongside a
        session nobody managed to stop. That is the exact scenario this method
        exists to prevent.

        Never raises: a session that already finished will simply refuse the
        interrupt, and a cancel must never take the run down.
        """
        if not session_id:
            return None
        try:
            response = self._request("POST", f"/api/session/{session_id}/interrupt")
        except urllib.error.HTTPError as exc:
            # 404: no such session. 401: bad credentials. Both are failures to
            # act, and both must stay distinct from "already finished".
            if exc.code in (404, 401):
                return None
            return None
        except Exception:  # noqa: BLE001 - cancel is best-effort by design
            return None
        payload = self._unwrap(response)
        if isinstance(payload, dict):
            # The server answered. "interrupted" false means it had nothing to
            # stop, which is a real answer - not a failure.
            return bool(payload.get("interrupted"))
        # A truthy non-dict (e.g. a bare `{"data": true}`) still means the
        # server confirmed. Falsy means it answered without a verdict.
        return True if payload else None

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
        # Every `.get` here is guarded. The server has been observed to send
        # entries that are not objects (`null`, bare strings, numbers) inside
        # the `data` list, and `content` entries that are plain strings rather
        # than `{"type": ..., "text": ...}` objects. One unguarded `.get`
        # raised AttributeError out of `status()`, which callers like
        # `oc-fleet show` do not catch.
        if not isinstance(message, dict):
            return None
        if message.get("type") != "message":
            return None
        info = message.get("info")
        role = info.get("role") if isinstance(info, dict) else None
        if role not in ("assistant", "user"):
            return None
        content = message.get("content")
        if not isinstance(content, list):
            return None
        parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("text", "reasoning"):
                text = part.get("text")
                # `text` can be a non-string (or missing) on malformed input.
                # Joining a non-string raised TypeError on the `"\n".join`.
                if isinstance(text, str):
                    parts.append(text)
        if not parts:
            return None
        return "\n".join(parts)