"""oc-fleet CLI: thin argparse wrapper over fleet.Fleet.

Commands:
    status                    snapshot: server up? active? last 5 completed
    dispatch TASK --workdir DIR [--model M] [--title T] [--detach]
                              fire a task; --detach spawns a background waiter
    watch [--timeout 3600]    SSE live: print completions as they land
    sessions [N]              table: id, title, time
    show ID                   full: outcome + last reply (sanitized)
    stats                     totals

Exit codes: 0 ok, 1 task failed, 2 api/connection error.
Output: plain text (Hermes-friendly), NO color codes.
"""

import argparse
import json
import os
import select
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

from fleet import Fleet

API_ERRORS = (OSError, json.JSONDecodeError, KeyError)
# Sengaja tetap sempit di sini, dan itu berbeda dari orchestrator/waiter.
#
# `main()` adalah batas teratas program interaktif. Sebuah exception di
# luar daftar ini berarti ada bug di oc-fleet sendiri, bukan gangguan
# jaringan. Menelannya jadi "api/connection failure" akan menyembunyikan
# bug itu. Traceback di terminal adalah sinyal debugging yang berguna.
#
# Proses latar (orchestrator, oc-fleet-wait) berlawanan: mereka jalan
# tanpa yang menonton, jadi biaya salah tebak itu asimetris dan mereka
# menangkap Exception. Di sini, kebalikannya yang benar.
#
FAILED_OUTCOMES = {"failed", "crashed", "error", "cancelled", "canceled"}
SSE_POLL_SECONDS = 1.0
STATUS_FETCH_LIMIT = 20
TITLE_MAX = 40


def make_fleet(args):
    return Fleet(base_url=args.base_url, password_file=args.password_file)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="oc-fleet",
        description="Manage an OpenCode agent fleet.",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:4096",
        help="OpenCode server base URL (default: http://127.0.0.1:4096)",
    )
    parser.add_argument(
        "--password-file",
        default="/tmp/oc_serve.log",
        help="file containing the server password (default: /tmp/oc_serve.log)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("status", help="snapshot: server up? active? last 5 done")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("dispatch", help="create a session and post a task")
    p.add_argument("task", help="task prompt text")
    p.add_argument("--workdir", required=True, help="working directory for the session")
    p.add_argument("--model", default="", help="model id, e.g. cutad/qwen3-8-flash-next")
    p.add_argument("--title", default="", help="session title")
    p.add_argument(
        "--detach",
        action="store_true",
        help="spawn a background waiter (oc-fleet-wait.py) for the session",
    )
    p.set_defaults(func=cmd_dispatch)

    p = sub.add_parser("watch", help="SSE live: print completions as they land")
    p.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="stop after N seconds (default: 3600)",
    )
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("sessions", help="table of recent sessions: id, title, time")
    p.add_argument("limit", nargs="?", type=int, default=10, help="how many sessions (default: 10)")
    p.set_defaults(func=cmd_sessions)

    p = sub.add_parser("show", help="full detail for one session")
    p.add_argument("session_id", help="session id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("stats", help="totals")
    p.set_defaults(func=cmd_stats)

    return parser


def _session_id(session):
    return session.get("id") or "?"


def _fmt_epoch_ms(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return "-"
    try:
        stamp = datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return "-"
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _session_time(session):
    stamps = session.get("time") or {}
    return _fmt_epoch_ms(stamps.get("updated") or stamps.get("created"))


def _clip(text, limit=TITLE_MAX):
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def cmd_status(args):
    fleet = make_fleet(args)
    sessions = fleet.list_sessions(limit=STATUS_FETCH_LIMIT) or []
    stats = fleet.stats() or {}
    states = {}
    active = []
    completed = []
    for session in sessions:
        sid = _session_id(session)
        state = fleet.status(sid) or {}
        states[sid] = state
        if state.get("outcome") is None:
            active.append(session)
        else:
            completed.append(session)
    lines = ["server: up", "active sessions: %d" % len(active)]
    for session in active:
        lines.append("  %s  %s  %s" % (_session_id(session), _clip(session.get("title")), _session_time(session)))
    lines.append("last 5 completed:")
    if completed:
        for session in completed[:5]:
            sid = _session_id(session)
            outcome = states[sid].get("outcome")
            lines.append(
                "  %s  %s  %s  %s" % (sid, _clip(session.get("title")), outcome, _session_time(session))
            )
    else:
        lines.append("  (none)")
    lines.append("stats:")
    if stats:
        for key, value in stats.items():
            lines.append("  %s=%s" % (key, value))
    else:
        lines.append("  (none)")
    print("\n".join(lines))
    return 0


def cmd_dispatch(args):
    fleet = make_fleet(args)
    try:
        session_id = fleet.dispatch(args.task, args.workdir, title=args.title, model=args.model)
    except ValueError as exc:
        # Kesalahan format model dari pengguna, bukan kegagalan API. Pesannya
        # sudah menjelaskan apa yang salah, jadi tampilkan apa adanya.
        print("error: %s" % exc, file=sys.stderr)
        return 2
    print("dispatched: %s" % session_id)
    if args.detach:
        waiter = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oc-fleet-wait.py")
        cmd = [sys.executable, waiter, str(session_id), "--base-url", str(fleet.base_url)]
        with open(os.devnull, "wb") as devnull:
            subprocess.Popen(cmd, stdout=devnull, stderr=devnull, start_new_session=True)
        print("waiter spawned for %s" % session_id)
    return 0


def cmd_sessions(args):
    fleet = make_fleet(args)
    sessions = fleet.list_sessions(limit=args.limit) or []
    if not sessions:
        print("(no sessions)")
        return 0
    rows = [
        (_session_id(session), _clip(session.get("title")), _session_time(session))
        for session in sessions
    ]
    width_id = max(len("ID"), max(len(row[0]) for row in rows))
    width_title = max(len("TITLE"), max(len(row[1]) for row in rows))
    lines = ["%-*s  %-*s  TIME" % (width_id, "ID", width_title, "TITLE")]
    for rid, title, stamp in rows:
        lines.append("%-*s  %-*s  %s" % (width_id, rid, width_title, title, stamp))
    print("\n".join(lines))
    return 0


def cmd_show(args):
    fleet = make_fleet(args)
    state = fleet.status(args.session_id) or {}
    outcome = state.get("outcome")
    text = state.get("last_assistant_text")
    lines = [
        "session: %s" % args.session_id,
        "outcome: %s" % (outcome if outcome is not None else "(none yet)"),
    ]
    if text:
        lines.append("last reply:")
        lines.append(Fleet.sanitize(text))
    else:
        lines.append("last reply: (none)")
    print("\n".join(lines))
    return 1 if outcome in FAILED_OUTCOMES else 0


def cmd_stats(args):
    fleet = make_fleet(args)
    stats = fleet.stats() or {}
    if not stats:
        print("(no stats)")
        return 0
    for key, value in stats.items():
        print("%s=%s" % (key, value))
    return 0


def _is_completion(event):
    if not isinstance(event, dict):
        return False
    return event.get("type") in (
        "session.idle",
        "session.execution.succeeded",
        "session.execution.failed",
    )


def _event_session_id(event):
    props = event.get("properties") or {}
    return props.get("sessionID") or props.get("id") or event.get("sessionID") or event.get("session_id") or ""


def _event_outcome(event):
    etype = event.get("type") or ""
    props = event.get("properties") or {}
    if etype == "session.execution.succeeded":
        return "done"
    if etype == "session.execution.failed":
        return "failed"
    outcome = props.get("outcome")
    return outcome if outcome else ""


def _flush_sse(state):
    """Drain buffered data lines into a parsed event (or None)."""
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


def _handle_sse_line(raw_line, state):
    line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
    if line.startswith("event:"):
        state["name"] = line.split(":", 1)[1].strip()
    elif line.startswith("data:"):
        state["data"].append(line.split(":", 1)[1].strip())
    elif line == "":
        return _flush_sse(state)
    # comment lines and other SSE fields are ignored
    return None


def _sse_events(fleet, deadline=None):
    """Yield parsed SSE events from the OpenCode /api/event stream.

    Reads the socket in non-blocking mode so an overall deadline can be
    honoured even when the server goes quiet.
    """
    request = urllib.request.Request(fleet.base_url + "/api/event", headers=fleet.headers, method="GET")
    with urllib.request.urlopen(request) as response:
        fd = response.fileno()
        os.set_blocking(fd, False)
        state = {"name": None, "data": []}
        buffer = b""
        while True:
            if deadline is not None and time.monotonic() > deadline:
                break
            ready, _, _ = select.select([fd], [], [], SSE_POLL_SECONDS)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                continue
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                raw_line, buffer = buffer.split(b"\n", 1)
                event = _handle_sse_line(raw_line, state)
                if event is not None:
                    yield event
        if buffer.strip():
            event = _handle_sse_line(buffer, state)
            if event is not None:
                yield event
        event = _flush_sse(state)
        if event is not None:
            yield event


def cmd_watch(args):
    fleet = make_fleet(args)
    deadline = time.monotonic() + args.timeout
    print("watching (timeout %ds)" % args.timeout, file=sys.stderr)
    for event in _sse_events(fleet, deadline=deadline):
        if not _is_completion(event):
            continue
        line = "completed: %s (%s" % (_event_session_id(event), event.get("type"))
        outcome = _event_outcome(event)
        if outcome:
            line += ", %s" % outcome
        print(line + ")")
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except API_ERRORS as exc:
        print("error: api/connection failure: %s" % exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
