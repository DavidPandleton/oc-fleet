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
