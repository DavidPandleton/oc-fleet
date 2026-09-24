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

Retries can use automatic model fallbacks. Attempt 1 uses `model`; subsequent
attempts use entries from `fallbacks` in order. This is useful when the error
comes from a provider, such as `provider.invalid-request` with empty content,
rather than from the prompt. Retrying the same model would only waste time.

Tasks running in parallel on the same repository can be isolated with Git
worktrees:

```python
o.add(Task(id="a", prompt="...", workdir="/repo", repo="/repo",
           isolate=True))  # automatically creates an isolated worktree
```

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
