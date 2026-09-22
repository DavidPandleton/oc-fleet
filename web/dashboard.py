"""Local web dashboard for OpenCode fleet monitoring.

Single self-contained stdlib-only server (http.server + json + urllib).
Serves a dark monospace dashboard, dispatches tasks through the Fleet
class, lists recent sessions, and proxies live Session-Sent Events from
the OpenCode /api/event stream.

Usage:
    python3 web/dashboard.py [--port 8787] [--base-url ...] [--password-file ...]

Endpoints:
    GET  /                 dashboard HTML (inline CSS + JS)
    POST /api/dispatch     JSON {task, workdir, model, title} -> {session_id}
    GET  /api/sessions     recent sessions as JSON
    GET  /api/events       SSE proxy forwarding session.execution.* events
"""

import argparse
import base64
import html
import json
import os
import re
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fleet import Fleet  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
SSE_READ_CHUNK = 65536

_PASSWORD_RE = re.compile(r"server password\s+(\S+)")

# Extra places to look for the password when no explicit file works.
# The serve log lives in /tmp and can be wiped, so the second entry is
# the durable copy and service.json is the final fallback.
_PASSWORD_SOURCES = [
    "/tmp/oc_serve.log",
    os.path.expanduser("~/.local/share/opencode/serve.log"),
]
_SERVICE_CONFIG = os.path.expanduser("~/.config/opencode/service.json")
MODELS = [
    "",
    "cutad/qwen3-8-flash-next",
    "cutad/deepseek-v4-flash",
    "anthropic/claude",
    "openai/gpt",
]


def load_auth_headers(password_file=None):
    """Read the OpenCode server password and return request headers.

    Mirrors the password loader in fleet.Fleet: Basic opencode:<password>.
    Returns headers with Content-Type and, when a password is found, an
    Authorization header.
    """
    headers = {"Content-Type": "application/json"}
    password = None

    env_pw = os.environ.get("OPENCODE_SERVER_PASSWORD")
    if env_pw:
        password = env_pw
    else:
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
                password = match.group(1)
                break
        if not password:
            try:
                with open(_SERVICE_CONFIG, encoding="utf-8") as fh:
                    password = json.load(fh).get("password")
            except (OSError, ValueError):
                password = None

    if password:
        headers["Authorization"] = (
            "Basic " + base64.b64encode(("opencode:" + password).encode()).decode()
        )
    return headers


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without network)
# ---------------------------------------------------------------------------


def validate_dispatch(data):
    """Validate and normalize a dispatch payload.

    Accepts a parsed JSON object (dict). Returns a dict of
    {task, workdir, model, title} with strings, or raises ValueError when
    the payload is malformed. Only truthy model/title are kept.
    """
    if not isinstance(data, dict):
        raise ValueError("payload must be a JSON object")
    task = str(data.get("task") or "").strip()
    workdir = str(data.get("workdir") or "").strip()
    if not task:
        raise ValueError("task is required")
    if not workdir:
        raise ValueError("workdir is required")
    model = str(data.get("model") or "").strip()
    title = str(data.get("title") or "").strip()
    return {"task": task, "workdir": workdir, "model": model, "title": title}


def is_execution_event(event):
    """Return True only for session.execution.* events."""
    if not isinstance(event, dict):
        return False
    etype = event.get("type") or ""
    return etype.startswith("session.execution.")


def filter_execution_events(events):
    """Filter a sequence of parsed events down to session.execution.* ones."""
    return [e for e in events if is_execution_event(e)]


def _sse_line(line, state):
    """Handle one SSE protocol line, updating state.

    state is a dict {"name": <str|None>, "data": [str, ...]}. Returns a
    parsed event dict when a blank line terminates an event, else None.
    """
    line = line.decode("utf-8", "replace").rstrip("\r\n")
    if line.startswith("event:"):
        state["name"] = line.split(":", 1)[1].strip()
    elif line.startswith("data:"):
        state["data"].append(line.split(":", 1)[1].strip())
    elif line == "":
        return _sse_flush(state)
    return None


def _sse_flush(state):
    """Emit the buffered SSE event (or None when no data was buffered)."""
    if not state["data"]:
        state["name"] = None
        return None
    payload = "\n".join(state["data"])
    name, state["name"] = state["name"], None
    state["data"] = []
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    if name and "type" not in event:
        event["type"] = name
    return event


def sse_frame(event):
    """Serialize one parsed event into an SSE wire frame for the client.

    Returns a bytes string like::
        event: session.execution.succeeded\\ndata: {...}\\n\\n
    Falls back to a data-only frame when the event has no type. Sanitizes
    the JSON so no em-dash is emitted.
    """
    if not isinstance(event, dict):
        return b"data: {}\n\n"
    etype = event.get("type")
    lines = []
    if etype:
        lines.append("event: " + str(etype))
    payload = json.dumps(event)
    lines.append("data: " + payload.replace("\n", " "))
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def upstream_event_stream(fleet):
    """Yield parsed events from the OpenCode /api/event stream.

    Uses the fleet headers (Basic auth loaded from the password file).
    Blocks on reads, so run it in a worker thread.
    """
    request = urllib.request.Request(
        fleet.base_url + "/api/event", headers=fleet.headers, method="GET"
    )
    with urllib.request.urlopen(request) as response:
        state = {"name": None, "data": []}
        buffer = b""
        while True:
            chunk = response.read(SSE_READ_CHUNK)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                raw_line, buffer = buffer.split(b"\n", 1)
                event = _sse_line(raw_line, state)
                if event is not None:
                    yield event
        if buffer.strip():
            event = _sse_line(buffer, state)
            if event is not None:
                yield event
        event = _sse_flush(state)
        if event is not None:
            yield event


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


def render_index(port):
    """Render the dashboard HTML page (inline CSS + JS, no external CDN)."""
    del port  # reserved for future use (e.g. display the bind port)
    options = "".join(
        '<option value="%s">%s</option>'
        % (html.escape(m, quote=True), (m if m else "default"))
        for m in MODELS
    )
    return _INDEX_TEMPLATE.replace("__MODEL_OPTIONS__", options)


_INDEX_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>oc-fleet dashboard</title>
<style>
:root {
  --bg: #0b0d10;
  --panel: #14171c;
  --border: #2a2f37;
  --fg: #d7dae0;
  --muted: #8a919c;
  --accent: #3fa7ff;
  --ok: #36d399;
  --bad: #ff6b6b;
  --warn: #ffca3a;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 14px;
  line-height: 1.5;
}
main { max-width: 960px; margin: 0 auto; padding: 16px; }
h1 { font-size: 18px; margin: 0 0 16px; letter-spacing: .5px; }
h1 .dim { color: var(--muted); font-weight: 400; }
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
  margin-bottom: 20px;
}
.stat {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px;
}
.stat .num { font-size: 24px; color: var(--accent); }
.stat .lbl { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .5px; }
.panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 14px;
  margin-bottom: 20px;
}
.panel h2 { font-size: 15px; margin: 0 0 12px; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted); font-weight: 400; font-size: 12px; text-transform: uppercase; letter-spacing: .5px; }
td.id { font-size: 12px; word-break: break-all; }
td.slot, td.responsive { min-width: 120px; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; }
.ok { background: rgba(54, 211, 153, .15); color: var(--ok); }
.bad { background: rgba(255, 107, 107, .15); color: var(--bad); }
.run { background: rgba(255, 202, 58, .15); color: var(--warn); }
.muted { color: var(--muted); }
label { display: block; margin: 10px 0 4px; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .5px; }
textarea, input, select {
  width: 100%;
  background: var(--bg);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 8px;
  font-family: inherit;
  font-size: 14px;
}
textarea { min-height: 90px; resize: vertical; }
.row { display: flex; gap: 10px; }
.row > div { flex: 1; }
button {
  margin-top: 12px;
  background: var(--accent);
  color: #04121f;
  border: none;
  border-radius: 4px;
  padding: 10px 18px;
  font-family: inherit;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
button:hover { filter: brightness(1.1); }
button:disabled { opacity: .5; cursor: wait; }
#notice { margin-top: 12px; color: var(--ok); }
#notice.error { color: var(--bad); }
#live { list-style: none; margin: 0; padding: 0; }
#live li { padding: 4px 0; border-bottom: 1px solid var(--border); font-size: 13px; word-break: break-all; }
</style>
</head>
<body>
<main>
  <h1>oc-fleet dashboard <span class="dim">(stdlib only)</span></h1>

  <section class="stats">
    <div class="stat"><div class="num" id="st-sessions">-</div><div class="lbl">sessions</div></div>
    <div class="stat"><div class="num" id="st-tools">-</div><div class="lbl">tool calls</div></div>
    <div class="stat"><div class="num" id="st-rate">-</div><div class="lbl">success rate</div></div>
  </section>

  <section class="panel">
    <h2>recent sessions</h2>
    <table>
      <thead>
        <tr><th>id</th><th>title</th><th>model</th><th>time</th><th>outcome</th></tr>
      </thead>
      <tbody id="rows"><tr><td colspan="5" class="muted">loading...</td></tr></tbody>
    </table>
  </section>

  <section class="panel">
    <h2>live completions</h2>
    <ul id="live"><li class="muted">waiting for events...</li></ul>
  </section>

  <section class="panel">
    <h2>dispatch</h2>
    <form id="dispatch">
      <label for="task">task</label>
      <textarea id="task" name="task" placeholder="describe the work to run"></textarea>
      <label for="workdir">workdir</label>
      <input id="workdir" name="workdir" type="text" spellcheck="false" value="/repo" placeholder="/path/to/repo">
      <div class="row">
        <div>
          <label for="model">model</label>
          <select id="model" name="model">__MODEL_OPTIONS__</select>
        </div>
        <div>
          <label for="title">title</label>
          <input id="title" name="title" type="text" spellcheck="false" placeholder="optional">
        </div>
      </div>
      <button type="submit">dispatch</button>
      <div id="notice"></div>
    </form>
  </section>
</main>

<script>
"use strict";
function el(id) { return document.getElementById(id); }

function fmt(n) {
  if (n === null || n === undefined || n === "") { return "-"; }
  return String(n);
}

function outcomeBadge(value) {
  var v = String(value || "").toLowerCase();
  if (!v) { return '<span class="muted">-</span>'; }
  if (v === "succeeded" || v === "done" || v === "completed" || v === "ok") {
    return '<span class="badge ok">' + value + "</span>";
  }
  if (v === "failed" || v === "error" || v === "crashed" || v === "cancelled" || v === "canceled") {
    return '<span class="badge bad">' + value + "</span>";
  }
  return '<span class="badge run">' + value + "</span>";
}

function esc(text) {
  var div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

function sessionTime(session) {
  // The API returns time as an object: {created, updated, idle}.
  var t = session.time;
  if (t && typeof t === "object") { t = t.updated ?? t.created ?? t.idle; }
  if (t === undefined || t === null) { t = session.created ?? session.updated; }
  if (!t) { return "-"; }
  var ms = Number(t);
  if (!isFinite(ms) || ms <= 0) { return String(t); }
  var d = new Date(ms);
  return d.toISOString().slice(0, 19).replace("T", " ");
}

function renderRows(sessions) {
  var rows = el("rows");
  if (!sessions || sessions.length === 0) {
    rows.innerHTML = '<tr><td colspan="5" class="muted">(no sessions)</td></tr>';
    return;
  }
  rows.innerHTML = sessions.map(function (s) {
    var model = s.model || "-";
    if (model && model.id) { model = (s.model.providerID || "") + "/" + s.model.id; }
    return '<tr>' +
      '<td class="id">' + esc(s.id || "-") + "</td>" +
      '<td class="responsive">' + esc(s.title || "") + "</td>" +
      '<td class="responsive">' + esc(model) + "</td>" +
      '<td>' + esc(sessionTime(s)) + "</td>" +
      '<td>' + outcomeBadge(s.outcome) + "</td>" +
      "</tr>";
  }).join("");
}

async function refresh() {
  try {
    var res = await fetch("/api/sessions");
    var data = await res.json();
    renderRows(data.sessions || []);
  } catch (err) {
    el("rows").innerHTML = '<tr><td colspan="5" class="muted">session list unavailable</td></tr>';
  }
}

async function refreshStats() {
  try {
    var res = await fetch("/api/stats");
    var raw = await res.json();
    var st = raw.data ?? raw;
    el("st-sessions").textContent = fmt(st.sessions ?? st.session_count ?? st.total ?? "-");
    // tools is an object: {mode, totals: {calls, succeeded, failed, unfinished}}
    var tools = st.tools;
    var calls = "-";
    var rate = "-";
    if (tools && typeof tools === "object" && tools.totals) {
      var t = tools.totals;
      calls = fmt(t.calls ?? "-");
      var done = Number(t.succeeded ?? 0) + Number(t.failed ?? 0);
      rate = done > 0 ? (Number(t.succeeded) / done * 100).toFixed(1) + "%" : "-";
    } else if (typeof tools === "number") {
      calls = fmt(tools);
    }
    el("st-tools").textContent = calls;
    if (rate === "-") {
      rate = st.success_rate ?? st.successRate ?? st.rate ?? "-";
    }
    el("st-rate").textContent = fmt(rate);
  } catch (err) {
    el("st-sessions").textContent = "-";
    el("st-tools").textContent = "-";
    el("st-rate").textContent = "-";
  }
}

function addLive(text) {
  var list = el("live");
  var empty = list.querySelector(".muted");
  if (empty) { list.removeChild(empty); }
  var li = document.createElement("li");
  li.textContent = text;
  list.prepend(li);
  while (list.children.length > 12) { list.removeChild(list.lastChild); }
}

function setupSSE() {
  var source = new EventSource("/api/events");
  ["session.execution.succeeded", "session.execution.failed",
   "session.execution.in_progress", "session.execution.started"].forEach(function (t) {
    source.addEventListener(t, function (ev) {
      try {
        var e = JSON.parse(ev.data);
        var sid = (e.properties && (e.properties.sessionID || e.properties.id)) ||
                  e.sessionID || e.session_id || "";
        var line = t + " " + sid;
        if (e.properties && e.properties.outcome) { line += " [" + e.properties.outcome + "]"; }
        addLive(line);
        refresh();
      } catch (_) {}
    });
  });
  source.onerror = function () {
    addLive("event stream dropped; reconnecting...");
  };
}

el("dispatch").addEventListener("submit", async function (ev) {
  ev.preventDefault();
  var btn = el("dispatch").querySelector("button");
  var notice = el("notice");
  btn.disabled = true;
  notice.className = "";
  notice.textContent = "dispatching...";
  try {
    var payload = {
      task: el("task").value,
      workdir: el("workdir").value,
      model: el("model").value,
      title: el("title").value
    };
    var res = await fetch("/api/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    var data = await res.json();
    if (!res.ok) {
      notice.className = "error";
      notice.textContent = "error: " + (data.error || res.status);
    } else {
      notice.className = "";
      notice.textContent = "dispatched: " + (data.session_id || "?");
      el("task").value = "";
      refresh();
    }
  } catch (err) {
    notice.className = "error";
    notice.textContent = "error: " + err;
  } finally {
    btn.disabled = false;
  }
});

refresh();
refreshStats();
setInterval(refresh, 5000);
setInterval(refreshStats, 15000);
setupSSE();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class DashboardHandler(BaseHTTPRequestHandler):
    fleet = None

    server_version = "ocfleet-dashboard/1.0"

    def log_message(self, fmt, *args):  # keep logs quiet
        sys.stderr.write("[dashboard] %s %s\n" % (self.address_string(), fmt % args))

    def _send_json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            body = render_index(DEFAULT_PORT).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/sessions":
            self._handle_sessions()
        elif self.path == "/api/events":
            self._handle_events()
        elif self.path in ("/api/stats", "/api/experimental/session/stats"):
            self._handle_stats()
        else:
            self._send_json(404, {"error": "not found"})

    def _read_content_length(self):
        """Return the request body length, or None if the header is unusable.

        The header arrives from the network, so it is not trusted input.
        `Content-Length: abc` used to hit `int(...)` directly and raise
        ValueError out of the handler: the client got no response at all and
        hung until timeout, while the server printed a traceback. Reproduced
        with a raw socket. Anything unparseable or negative is now a 400.
        """
        raw = self.headers.get("Content-Length")
        if raw is None:
            return 0
        try:
            length = int(raw.strip())
        except (TypeError, ValueError):
            return None
        if length < 0:
            return None
        return length

    def do_POST(self):
        if self.path != "/api/dispatch":
            self._send_json(404, {"error": "not found"})
            return
        length = self._read_content_length()
        if length is None:
            self._send_json(400, {"error": "invalid Content-Length header"})
            return
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Both are reachable from the network. JSONDecodeError covers a
            # well-formed-but-invalid body; UnicodeDecodeError covers raw
            # bytes that are not UTF-8 at all. Catching only the first left
            # a non-UTF-8 body raising out of the handler with no response,
            # the same failure shape as the Content-Length bug above.
            self._send_json(400, {"error": "invalid JSON body"})
            return
        try:
            payload = validate_dispatch(data)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        try:
            session_id = self.fleet.dispatch(
                payload["task"],
                payload["workdir"],
                title=payload["title"],
                model=payload["model"],
            )
        except Exception as exc:  # upstream api failure
            self._send_json(502, {"error": "upstream dispatch failed: %s" % exc})
            return
        self._send_json(200, {"session_id": session_id})

    def _handle_sessions(self):
        try:
            sessions = self.fleet.list_sessions(limit=20) or []
        except Exception:
            self._send_json(502, {"error": "upstream session list failed"})
            return
        self._send_json(200, {"sessions": sessions})

    def _handle_stats(self):
        try:
            stats = self.fleet.stats() or {}
        except Exception:
            self._send_json(502, {"error": "upstream stats failed"})
            return
        self._send_json(200, stats)

    def _handle_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            for event in upstream_event_stream(self.fleet):
                if not is_execution_event(event):
                    continue
                self.wfile.write(sse_frame(event))
                self.wfile.flush()
        except Exception as exc:
            try:
                self.wfile.write(b"event: error\ndata: {}\n\n")
                self.wfile.flush()
            except Exception:
                pass


def build_parser():
    parser = argparse.ArgumentParser(
        prog="dashboard",
        description="Local web dashboard for OpenCode fleet monitoring.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to bind (default: 8787)")
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help="bind address (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:4096", help="OpenCode server base URL"
    )
    parser.add_argument(
        "--password-file",
        default="/tmp/oc_serve.log",
        help="file containing the server password (default: /tmp/oc_serve.log)",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.host != DEFAULT_HOST:
        print("error: refusing to bind to non-loopback address %s" % args.host, file=sys.stderr)
        return 2
    fleet = Fleet(base_url=args.base_url, password_file=args.password_file)
    DashboardHandler.fleet = fleet
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print("dashboard listening on http://%s:%d" % (args.host, args.port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())