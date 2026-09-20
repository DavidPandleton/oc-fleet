# oc-fleet

Fleet manager untuk OpenCode agents via HTTP API. Bikin, monitor, dan
kelola puluhan session paralel dari satu CLI.

## Quick start

```bash
python3 cli.py stats                    # aggregate stats server
python3 cli.py sessions 5               # 5 session terakhir
python3 cli.py dispatch "TASK" --workdir /path/to/repo
python3 cli.py show ses_xxx             # outcome + reply
python3 cli.py watch                    # live completions
```

## Library

```python
from fleet import Fleet
f = Fleet()
sid = f.dispatch("Fix the bug", "/repo", title="fix", model="cutad/qwen3-8-flash-next")
st = f.status(sid)  # {outcome, last_assistant_text}
```

## Architecture

- `fleet.py`: core lib (17 tests)
- `cli.py`: argparse CLI (25 tests)
- `oc-fleet-wait.py`: detached waiter per session

Requires `opencode serve` running (reads password from /tmp/oc_serve.log).

- Rouge

## Performance notes

Built and debugged entirely through OpenCode's HTTP API (130 sessions,
596 tool calls, 97.3% success rate). The tool itself exists because
driving N agents in parallel from a shell gets unmanageable fast.

Key API quirks encoded in `fleet.py` (all discovered by probing the live
server, not from docs):

- Response envelopes are inconsistent: `/api/session` returns
  `{"data": [...]}` but `/api/project` returns a bare list. Both shapes
  are handled.
- `Content-Type: application/json` is mandatory or every POST returns 415.
- Auth is HTTP Basic (`opencode:<password>`), not Bearer.
- Completion is signalled by the SSE event `session.execution.succeeded`,
  not by an `idle` message.

## License

MIT
