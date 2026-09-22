#!/usr/bin/env python3
"""Background waiter for one oc-fleet session.

Usage:
    oc-fleet-wait.py SESSION_ID [--timeout 1800] [--interval 5] [--base-url URL]

Polls Fleet.status(session_id) every 5 seconds until an outcome is set (or
--timeout expires). On exit it writes /tmp/oc_result_<epoch>.json with
{session_id, outcome, last_assistant_text} and appends one line to
~/.hermes/mailbox/opencode/alerts.log.

Exit codes: 0 session completed, 1 timed out, 2 api/connection error.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fleet import Fleet  # noqa: E402

RESULT_PREFIX = "/tmp/oc_result_"
ALERTS_LOG = os.path.join(
    os.path.expanduser("~"), ".hermes", "mailbox", "opencode", "alerts.log"
)
API_ERRORS = (OSError, json.JSONDecodeError, KeyError)
# Polling swallows everything, for the same reason the orchestrator does:
# the network failure space cannot be enumerated. `fleet.status` can raise
# RuntimeError from a malformed response, and http.client.HTTPException
# (IncompleteRead, BadStatusLine) is not an OSError. Either one, caught
# narrowly, killed the waiter - which runs detached, so nothing noticed.
# A wrongly-tolerated error only costs one more poll.
POLL_ERRORS = Exception


def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_alert(line):
    os.makedirs(os.path.dirname(ALERTS_LOG), exist_ok=True)
    with open(ALERTS_LOG, "a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")


def write_result_file(session_id, outcome, last_assistant_text):
    path = RESULT_PREFIX + str(int(time.time())) + ".json"
    payload = {
        "session_id": str(session_id),
        "outcome": outcome,
        "last_assistant_text": last_assistant_text,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def wait_for_outcome(fleet, session_id, timeout=1800.0, interval=5.0):
    """Poll fleet.status until an outcome is set or the deadline passes.

    Returns (outcome, last_assistant_text). outcome is None on timeout.
    """
    deadline = time.monotonic() + timeout
    last = {"outcome": None, "last_assistant_text": None}
    while True:
        try:
            state = fleet.status(session_id) or {}
            last = {"outcome": state.get("outcome"), "last_assistant_text": state.get("last_assistant_text")}
            if last["outcome"] is not None:
                return last["outcome"], last["last_assistant_text"]
        except POLL_ERRORS:
            # transient api blip: keep polling until the deadline
            pass
        if time.monotonic() >= deadline:
            return last["outcome"], last["last_assistant_text"]
        time.sleep(interval)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="oc-fleet-wait.py",
        description="Poll one oc-fleet session until it finishes, then log the result.",
    )
    parser.add_argument("session_id", help="session id to wait on")
    parser.add_argument("--timeout", type=float, default=1800.0, help="max seconds to wait (default: 1800)")
    parser.add_argument("--interval", type=float, default=5.0, help="poll interval in seconds (default: 5)")
    parser.add_argument("--base-url", default="http://127.0.0.1:4096", help="OpenCode server base URL")
    args = parser.parse_args(argv)

    fleet = Fleet(base_url=args.base_url)
    try:
        outcome, last_text = wait_for_outcome(
            fleet, args.session_id, timeout=args.timeout, interval=args.interval
        )
    except API_ERRORS as exc:
        log_alert("ALERT %s session=%s waiter-error: %s" % (_ts(), args.session_id, exc))
        print("error: api/connection failure: %s" % exc, file=sys.stderr)
        return 2

    path = write_result_file(args.session_id, outcome, last_text)
    if outcome is not None:
        line = "ALERT %s session=%s outcome=%s" % (_ts(), args.session_id, outcome)
        if last_text:
            line += " text=%s" % last_text.splitlines()[0][:160]
    else:
        line = "ALERT %s session=%s outcome=timeout after %s" % (
            _ts(),
            args.session_id,
            "%.0fs" % args.timeout,
        )
    log_alert(line)
    print("session=%s outcome=%s result=%s" % (args.session_id, outcome, path))
    return 0 if outcome is not None else 1


if __name__ == "__main__":
    sys.exit(main())
