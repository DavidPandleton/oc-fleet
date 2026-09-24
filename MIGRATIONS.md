# Migrations and compatibility

What stays stable across versions, what changed, and how to roll back.
Read this before upgrading a run in flight.

## Status vocabulary

A task record ends in exactly one of these states. They are the contract a
consumer (Hermes, a dashboard, `oc-fleet show`) reads:

| Status | Meaning |
|---|---|
| `pending` | Not started. Also the state a task returns to when it will be retried. |
| `running` | Dispatched to a session; the orchestrator is still polling. |
| `succeeded` | The agent finished and no `verify` command was required. |
| `verification_passed` | The agent finished and its `verify` command passed. |
| `verification_failed` | The agent finished but `verify` failed, or a boundary was violated. |
| `setup_failed` | A worktree or setup hook failed before the agent ran. |
| `failed` | The agent failed and there was nothing to verify. |
| `timed_out` | The task's own time budget expired while it was running. |
| `skipped` | A dependency failed, so this task never ran. |

Precedence: a boundary violation or a required-but-failed verification
wins over agent failure. A task whose agent failed **and** whose
verification was required and failed is `verification_failed`, not
`failed`, because the verification failure is the actionable fact.

A run record is `running` while it works and `completed` when it stops,
regardless of task outcomes. Run-level success is not implied by
`completed`; read the tasks or use `run_status()`.

Do not treat `completed` as success. Do not treat `succeeded` as
verification passed: that is `verification_passed`.

A task record also carries usage fields. These are additive, so an older
consumer that ignores them keeps working:

| Field | Meaning |
|---|---|
| `stats` | Token counters normalized from the server payload, when reported. Absent if the server reported none. |
| `model_used` | The model actually used for the final attempt. |
| `estimated_cost` | Estimated cost for the reported usage, or `None` when no price table was configured or the model is unknown. |

`estimated_cost` is `None`, never `0.0`, when it cannot be computed. An
unpriced task is unknown, not free.

## Exit-code contract

`run_status()` returns a process exit code and is stable:

| Code | Meaning |
|---|---|
| `0` | Every task met its bar. |
| `1` | At least one agent failure. |
| `2` | At least one verification failure. |
| `3` | Preflight refused the plan; nothing was dispatched. |

`2` outranks `1`: a run with both is reported as a verification failure,
because that is the one a human must look at.

## SQLite store location

There is no implicit global database. The store path is always explicit:

- `oc-fleet approve --store PATH`
- `oc-fleet merge --store PATH`
- `oc-fleet-mcp --store PATH`

The orchestrator persists to the path handed to `RunStore(path)`. If you
do not pass one, nothing is persisted and `resume` has nothing to read.
Pick a path outside the workdir (for example under `~/.local/share/`)
so a worktree cleanup cannot take the run history with it.

The schema is created on first open. There is no migration runner yet:
a database from an older version opens and gains any new tables, but a
column change would require manual migration. This is noted here because
it is the one place an upgrade can silently do the wrong thing.

## Resume semantics

Resume replays a run from its persisted state:

- A task already `succeeded` or `verification_passed` is not re-dispatched.
- A task left `running` when the process died is re-run. Its old session
  is not reattached; a fresh session is created.
- A task left `pending` is run normally, in dependency order.

Consequence to plan for: a task that died mid-flight while having side
effects (a write, a network call) may run its side effect twice. Resume
does not attempt exactly-once. Keep tasks idempotent if you intend to
resume.

## Verifier security

`verify` commands are run by the orchestrator, not by the agent.

- The command string is tokenized with `shlex.split`.
- It is executed with `shell=False`: no shell, so `|`, `&&`, `;`, `$()`,
  and redirection are **not** interpreted. They are passed as literal
  arguments.
- Only the first token is treated as the program.

This is deliberate. A verify command is not a place for shell
conveniences, and refusing to run a shell closes a class of injection
between a task's prompt and the machine. If you need a pipeline, put it
in a script and call the script.

## Approval and merge policy

Approval and merge are explicit, never side effects:

- An agent finishing does not approve anything.
- `oc-fleet approve` records a human decision against a task, into the
  store.
- `oc-fleet merge` refuses unless an approval is recorded and the guard
  passes. There is no auto-merge.

Nothing in the orchestrator writes to a shared branch. Isolation happens
in worktrees; bringing work together is a separate, gated step.

## Config migration

Config lives at `~/.config/oc-fleet/config.json`. Rules:

- The file is optional. Missing or malformed, the built-in defaults apply
  and the tool still runs.
- A partial file is merged on top of the defaults, key by key. You do not
  need to restate every key.
- A nested object (for example `models`) is merged, so adding one model
  does not drop the others.
- A non-dict value for a key replaces the default outright, and a
  non-dict top-level file is ignored in favour of the defaults.

Because a broken config silently falls back to defaults, a typo does not
error. To see what is actually in effect, read the loaded config back
rather than the file on disk.

### v0.1.0 to unreleased

No config keys were added, renamed, or removed. An existing config keeps
working. The run behaviour changes listed in `CHANGELOG.md` under
`Changed` (entry-point launch, and a retry carrying failure context) need
no config change.

## Rollback

To go back to `v0.1.0` from a newer checkout:

```bash
git checkout v0.1.0
```

Then reinstall so the entry points match:

```bash
pipx install --force ~/oss/oc-fleet
```

A SQLite store written by a newer version may contain tables the older
version does not read. The older version ignores unknown tables, so it
opens the database, but it will not see state the newer version added.
Keep a copy of the store before rolling back if the run history matters.
