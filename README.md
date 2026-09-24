# oc-fleet

A fleet manager for OpenCode agents over the HTTP API. Create, monitor, and
manage dozens of parallel sessions from one CLI or DAG orchestrator.

## Install in 5 minutes

Prerequisite: `opencode serve --service` must be running.

```bash
pipx install ~/oss/oc-fleet        # from a local checkout
# or: pipx install git+https://github.com/DavidPandleton/oc-fleet.git
oc-fleet stats                     # verify that the server is reachable
```

Every entry point inserts its own directory on `sys.path` before importing
its siblings, so `cli.py`, `mcp_server.py`, and `oc-fleet-wait.py` work
however they are launched - run directly, through `runpy`, from a symlink
in `/tmp`, or by `python3 -m` from another directory. A detached runner
that dies on `ModuleNotFoundError` says nothing, so this is pinned by a
test rather than left to the accident of how CPython treats a script path.

## Quick start

```bash
oc-fleet stats                     # aggregate server statistics
oc-fleet sessions 5                # five most recent sessions
oc-fleet dispatch "TASK" --workdir /path/to/repo
oc-fleet show ses_xxx              # outcome and reply
oc-fleet watch                     # live completions
python3 web/dashboard.py           # web dashboard on :8787
python3 prompt_lint.py "TASK"     # lint a prompt before dispatch
```

## Web dashboard

```bash
python3 web/dashboard.py --port 8787
```

The dashboard uses only the Python standard library. No npm or Flask is
required. Open `http://127.0.0.1:8787` to see:

- summary statistics for sessions, tool calls, and success rate
- a recent-session table that refreshes every five seconds
- live completions through an SSE stream
- a browser form for dispatching tasks directly

## Prompt linter

```bash
python3 prompt_lint.py "Think step by step. Improve everything."
python3 prompt_lint.py --file task.txt
```

The linter checks prompts against harness behavior measured through 16
controlled probes. It reports ineffective fluff such as chain-of-thought,
urgency, and role instructions; unlimited scope; unverifiable output;
em-dashes; and destructive verbs without preservation constraints.

The fluff, `VAGUE-OUTPUT`, and `DESTRUCTIVE-NO-GUARD` rules understand
negation. A prompt that forbids something is not reported as if it requested
that thing. For example, `"Do not use chain of thought"` is not the same as
`"Think step by step"`, and `"Do not delete anything"` is not the same as
`"Delete everything"`. Negation is parsed per sentence, with comma-separated
clauses treated as boundaries, so `"Do not retry, and think step by step"`
still reports the second clause.

Four additional rules cover specification gaps that can cause agents to
invent plausible but incorrect behavior:

| Rule | Detects |
|---|---|
| `SPEC-UNDEFINED-EDGE` | Open-ended enumerations such as `etc` and `and so on` |
| `SPEC-TEST-ONLY-VALID` | Prompts that verify only the happy-path examples |
| `SPEC-NO-INVALID-CONTRACT` | Implementation prompts that omit invalid-input behavior |
| `SPEC-VERIFY-SELF-REFERENTIAL` | Self-referential verification instead of independent evidence |

For example, an agent asked to parse durations may be given valid forms but
not told what `"1h1h"` means. It can invent a rule that units must be in
descending order, reject legal input, and then mark its own tests as passing.

Three more rules measure a task that is too large for one session, the shape
that produced an 18-minute silent `scaffold` in the coffee-catalog run:

| Rule | Detects |
|---|---|
| `BIG-TASK-MANY-STEPS` | Four or more ordered steps chained into one task |
| `BIG-TASK-MANY-ARTEFACTS` | Five or more distinct files named in one task |
| `BIG-TASK-NO-CHECKPOINT` | A large task with no stopping point to verify partway |

All three are warnings, never errors. A large prompt is a smell, not a syntax
error, and only the foreman knows whether splitting it is worth an extra
dispatch. The thresholds sit above the two-file, two-step prompts that already
work, so the rule adds signal without flagging every clean task. When a task
this size fails, there is no smaller unit to point at, so a failure names one
step instead of the whole build.

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

The orchestrator executes a topological DAG, runs independent branches in
parallel, retries tasks individually, and skips dependents of failed tasks
without stopping unrelated branches.

### Planning before execution

``plan()`` validates the DAG and returns the full execution map without
dispatching anything: topological order, parallel waves, each task's model,
fallbacks, retries, timeout, hooks, verification, isolation, and operator
risks (missing verification, retry with the same model, shared workdir).
``run()`` builds this plan before it persists the run or dispatches the first
agent, so an invalid dependency, cycle, or empty workdir fails fast.
``run(dry_run=True)`` prints the same map.

### A run can resume without redoing finished work

When a run is given a store and a `run_id`, `run(resume=True)` reads the
previous task records back and adopts any task that already finished, so
a run interrupted by a process restart picks up where it stopped instead
of re-dispatching everything:

```python
from store import RunStore
from orchestrator import Orchestrator

store = RunStore("~/.local/share/oc-fleet/runs.sqlite")
orch = Orchestrator(store=store, run_id="nightly")
orch.add(task)
orch.run(resume=True)   # a task already `succeeded` is not run again
```

Resume is opt-in. A plain `run()` still repeats every task, because
silently doing nothing on a second call would be more surprising than
repeating work the caller asked to repeat. Resume is also not
exactly-once: a task that died mid-flight may run its side effect twice,
so keep tasks idempotent if you intend to resume. See `MIGRATIONS.md`.

### A run reports whether it actually succeeded

``run_status()`` condenses the whole run into one exit code a shell script or
CI step can act on:

| code | meaning |
|------|---------|
| 0 | every task succeeded and every required verification passed |
| 1 | an agent failed, timed out, or a setup hook failed (no verification failure) |
| 2 | a verification failed, including a boundary violation |
| 3 | preflight refused the run (validate/plan raised; nothing was dispatched) |

A task that both failed as an agent and broke its artifact contract is
reported as `verification_failed` (code 2), the more actionable condition,
while `agent_status` keeps the agent's own verdict so neither fact is lost.
Calling `run_status()` before `run()` returns 1, not 0: a run that produced
nothing must never look like a success.

### A long run is never silent, and a hung one is named

A run that prints `started: scaffold` and then nothing for eighteen minutes
is indistinguishable from one that died. The poll loop now emits a heartbeat
line on an interval, so a run that is merely slow says so:

```
heartbeat: scaffold (312s running, 1 tool running, oldest 302s)
```

`Orchestrator(heartbeat_interval=...)` controls the cadence (default 30s);
`heartbeat_interval=None` silences it.

The "oldest" figure is what turns a heartbeat into a diagnosis. OpenCode can
leave a tool call in `running` with `executed: false` that never finishes, in
which case `outcome` stays `None` forever and the run looks busy until the
task timeout. `Fleet.status()` therefore reports `stuck_seconds` and a `stuck`
flag when a running tool outlives a threshold. That threshold is a guess
about legitimate work, so it is configurable rather than baked in:

```python
Orchestrator(fleet, tool_timeout=1800)  # a run with honestly slow steps
Orchestrator(fleet, tool_timeout=60)    # a run that should never dawdle
```

`Fleet.STUCK_AFTER_SECONDS` (300s) remains the default. The threshold is
threaded through every poll of the run, and the orchestrator checks the
fleet's signature before passing it, so an older `Fleet.status(session_id)`
still works unchanged.

### Verification is about the artifact, not the agent

Verification commands run whenever the agent had a chance to write to the
workdir - success, failure, or timeout alike. A timeout says something about
the agent, not about the files it left behind, and the coffee-catalog run
showed the cost of conflating the two: a QA agent timed out, verification was
skipped, and a passing build was nearly discarded as a failure.

Each task result therefore separates the two facts:

| Field | Values |
|---|---|
| `agent_status` | `succeeded`, `failed`, `timed_out` |
| `verification_status` | `passed`, `failed`, `not_required` |

and still collects the artifact manifest when the agent fails, so a failed
run reports what it produced.

### Per-task usage and cost

A finished task carries the token counters the server reported, normalized
onto the record under `stats`, plus the model actually used under
`model_used`. Cost is computed only when a price table was supplied to the
orchestrator, through the `prices` argument or the `prices` key in
`config.json`:

```python
from orchestrator import Orchestrator

prices = {"cutad/deepseek-v4-flash": {"input": 0.25, "output": 1.0}}
orch = Orchestrator(prices=prices)
```

The prices are dollars per million tokens. An unknown model, or a call
with no price table at all, leaves `estimated_cost` as `None` rather than
`0.0`. That distinction is deliberate: a task that was never priced must
not look like a task that cost nothing.

Retries can use automatic model fallbacks. Attempt 1 uses `model`; subsequent
attempts use entries from `fallbacks` in order. This is useful when the error
comes from a provider, such as `provider.invalid-request` with empty content,
rather than from the prompt. Retrying the same model would only waste time.

### A retry carries the reason the last one failed

Re-sending the identical prompt is a coin flip. If attempt 1 failed because
the agent misread a constraint, attempt 2 repeats the same misreading with
no way to know it is repeating. The foreman already holds the evidence - it
classified the failure, it has the agent's last message, and it has the
verification stderr - so attempt N>1 is the original prompt plus a bounded
"previous attempt failed" block:

```
Previous attempt 1 of this task FAILED. Do not repeat it.
failed_model: cutad/deepseek-v4-flash
failure_class: agent_failed
agent_status: failed
agent_last_message:
<the agent's own last words>
verification_failed:
- <the exact command that failed>
  <its stderr>
```

Attempt 1 is byte-for-byte the original prompt, so a run that never fails is
unaffected. The whole prompt is clamped to the same budget as a handoff, so
a runaway transcript cannot drown the actual task.

Tasks running in parallel on the same repository can be isolated with Git
worktrees:

```python
o.add(Task(id="a", prompt="...", workdir="/repo", repo="/repo",
           isolate=True))  # automatically creates an isolated worktree
```

### Ownership boundaries keep parallel agents out of each other's lanes

Context in a prompt is a suggestion; an agent can ignore it. When several
agents work the same repository, each one is confined to a declared set of
paths and the fleet enforces it mechanically:

```python
o.add(Task(id="backend", prompt="build the API", workdir="/repo",
           owns=["backend/**"], isolate=True, repo="/repo",
           verify=["pytest backend"]))
o.add(Task(id="frontend", prompt="build the UI", workdir="/repo",
           owns=["frontend/**"], isolate=True, repo="/repo",
           verify=["npm test"], handoff=True))
```

After an agent finishes, the orchestrator reads the changed files from the
artifact manifest and checks them against `owns`:

- every changed file matches an owned pattern -> the task proceeds as usual;
- any file falls outside `owns` -> the task fails with `verification_failed`
  and `boundary["verdict"] == "boundary_violation"`, naming the offending
  files in `boundary["violations"]`.

`owns` is empty by default, which means no restriction (the old behaviour).
Boundary checks also run when the agent fails or times out, so a failed agent
that overstepped still reports the overstep.

When tasks share a workdir without `isolate=True`, the orchestrator refreshes
the Git baseline before each `owns`-scoped task starts, so one agent's changes
are never attributed to the next. `owns` and `isolate=True` together are the
safe combination: a private worktree per lane, and a mechanical check that
each lane stayed inside its own fences.

Overlapping lanes are refused at preflight. If two tasks in the same workdir
declare patterns that can match the same file - `src/**` against `src/db.py`,
or two identical patterns - `validate()` and `plan()` raise before anything is
dispatched, naming both tasks and the clashing patterns. A boundary that two
agents both claim is not a boundary; catching it up front is cheaper than
untangling a collision mid-run.

### A task that cannot work is refused before it costs a session

`validate()` also refuses a task whose own fields cannot mean anything. A
negative `retries`, a non-positive `timeout`, a non-positive `verify_timeout`
on a task that has `verify` commands, an empty `verify` command, an empty
`owns` pattern, a `fallbacks` entry that repeats a model already in the chain,
or an `owns` pattern that is absolute or climbs out of the workdir with `..`.
Each of these is knowable from the task alone, and each one, left uncaught,
buys a whole session before it fails.

The check is deliberately structural: it reads the task, never the machine.
`validate()` does not `stat()` the workdir, so a plan can be written and
reviewed on a machine that will not run it. What a task *promises* is checked
here; whether this machine can *keep* that promise is the executor's problem,
not the plan's.

## Configuration: models by role

`~/.config/oc-fleet/config.json` is used for role-specific models and runtime
settings. A partial configuration is merged with the defaults.

```json
{
  "models": {
    "scaffold": "cutad/qwen3-8-flash-next",
    "logic": "cutad/deepseek-v4-pro",
    "docs": "cutad/glm-5.3-flash",
    "review": "cutad/qwen3-8-flash-next"
  },
  "fallbacks": {
    "logic": ["cutad/qwen3-8-flash-next"]
  },
  "max_parallel": 3,
  "worktree_root": "/tmp/oc-fleet-worktrees"
}
```

## Library

```python
from fleet import Fleet

fleet = Fleet()
session_id = fleet.dispatch(
    "Fix the bug",
    "/repo",
    title="fix",
    model="cutad/qwen3-8-flash-next",
)
status = fleet.status(session_id)  # {outcome, last_assistant_text}
```

## Architecture

- `fleet.py`: core HTTP client
- `endpoint.py`: endpoint and password discovery
- `cli.py`: argparse-based CLI
- `orchestrator.py`: DAG runner with retries and parallel branches
- `prompt_lint.py`: linter based on measured harness behavior
- `config.py`: role-based model and runtime configuration
- `worktree.py`: Git worktree isolation helpers
- `web/dashboard.py`: single-file standard-library dashboard
- `oc-fleet-wait.py`: detached waiter for individual sessions

The only runtime prerequisite is a running `opencode serve` instance. The
password is discovered in this order: `OPENCODE_SERVER_PASSWORD`,
`/tmp/oc_serve.log`, `~/.local/share/opencode/serve.log`, and finally
`~/.config/opencode/service.json`.

## Compatibility and upgrades

Two documents cover what stays stable across versions:

- [`CHANGELOG.md`](CHANGELOG.md) records every change, with the run
  behaviour changes called out separately from the additions.
- [`MIGRATIONS.md`](MIGRATIONS.md) documents the status vocabulary, the
  exit-code contract, the SQLite store location, resume semantics (and
  why a resumed side effect can run twice), verifier security, the
  approval and merge boundary, config migration, and rollback.

Read `MIGRATIONS.md` before upgrading a run that is in flight.

## Performance notes

oc-fleet was built and debugged entirely through OpenCode's HTTP API. It
exists because driving multiple agents in parallel from a shell becomes
unmanageable quickly.

The implementation accounts for several API quirks discovered by probing a
live server rather than relying only on documentation:

- Response envelopes are inconsistent. `/api/session` returns a `data`
  envelope while some endpoints return a bare list. Both shapes are handled.
- `Content-Type: application/json` is mandatory for POST requests.
- Authentication uses HTTP Basic auth in the form `opencode:<password>`, not
  Bearer auth.
- Completion is signaled by the `session.execution.succeeded` SSE event, not
  by an `idle` message.

## License

MIT
