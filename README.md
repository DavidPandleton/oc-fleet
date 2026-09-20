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
python3 web/dashboard.py                # web dashboard di :8787
python3 prompt_lint.py "TASK"           # cek prompt sebelum dispatch
```

## Web dashboard

```bash
python3 web/dashboard.py --port 8787
```

Stdlib only, no npm, no flask. Buka `http://127.0.0.1:8787`:

- stats header (sessions, tool calls, success rate)
- tabel session terbaru, refresh tiap 5 detik
- live completions via SSE stream
- form dispatch langsung dari browser

## Prompt linter

```bash
python3 prompt_lint.py "Think step by step. Improve everything."
python3 prompt_lint.py --file task.txt
```

Ngecek prompt terhadap perilaku harness yang udah diukur lewat 16 probe
terkontrol. Yang dilaporin: fluff yang nggak ngefek (chain-of-thought,
urgency, role), scope tak terbatas, output yang nggak bisa diverifikasi,
em-dash, dan destructive verb tanpa preservation constraint.

Sejak eksperimen delegasi (2026-09-21) ada empat aturan tambahan yang nyasar
celah spec, karena itu mode kegagalan agent yang sebenarnya: dia ngisi celah
dengan aturan karangan sendiri yang kedengeran masuk akal.

| Rule | Yang ditangkep |
|---|---|
| `SPEC-UNDEFINED-EDGE` | enumerasi terbuka (`etc`, `and so on`) yang ngundang case karangan |
| `SPEC-TEST-ONLY-VALID` | "verify the examples above" cuma ngecek happy path |
| `SPEC-NO-INVALID-CONTRACT` | prompt implementasi tanpa nyebut kelakuan input invalid |
| `SPEC-VERIFY-SELF-REFERENTIAL` | verifikasi mandiri, bukan bukti independen |

Contoh nyata kenapa ini penting: T1 minta parser durasi, nyebut bentuk validnya,
tapi nggak bilang `"1h1h"` harus apa. Agent ngarang aturan "unit harus urut
menurun", nolak input legal, dan verifikasinya SENDIRI lulus karena dia nulis
tesnya dari asumsinya sendiri. Detail: `~/kb/projects/LAPORAN_FINAL.md`.

## Orchestrator

```python
from orchestrator import Orchestrator, Task

o = Orchestrator(max_parallel=4)
o.add(Task(id="a", prompt="Scaffold module", workdir="/repo"))
o.add(Task(id="b", prompt="Write tests", workdir="/repo", depends_on=["a"]))
o.add(Task(id="c", prompt="Update docs", workdir="/repo", depends_on=["a"], retries=2))
o.run()
print(o.summary())
```

DAG topologis, branch independen jalan paralel, retry per task, dependent
dari task gagal di-skip tanpa bunuh branch lain.

## Library

```python
from fleet import Fleet
f = Fleet()
sid = f.dispatch("Fix the bug", "/repo", title="fix", model="cutad/qwen3-8-flash-next")
st = f.status(sid)  # {outcome, last_assistant_text}
```

## Architecture

- `fleet.py`: core lib
- `cli.py`: argparse CLI
- `orchestrator.py`: DAG runner dengan retry dan parallel branch
- `prompt_lint.py`: linter prompt berbasis pengukuran
- `web/dashboard.py`: dashboard stdlib, single file
- `oc-fleet-wait.py`: detached waiter per session (169 tests total)

Requires `opencode serve` running. Password dicari berurutan: env
`OPENCODE_SERVER_PASSWORD`, `/tmp/oc_serve.log`,
`~/.local/share/opencode/serve.log`, lalu `~/.config/opencode/service.json`.

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
