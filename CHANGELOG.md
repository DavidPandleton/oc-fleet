# Changelog

All notable changes to oc-fleet are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Because the tool is a control plane over `opencode serve`, "breaking" also
covers a change that makes a previously valid run behave differently: a
task that used to succeed may now be refused at preflight, or a status a
consumer used to read may be renamed. Those are called out under `Changed`.

## [Unreleased]

### Added

- Level-based `NO-VAGUE-VERIFY` prompt lint, with a test that rejects a
  bad lint by measuring the good config against a corpus of weak prompts.
- Bounded retry preamble: an attempt after the first is told the reason
  the previous one failed (agent status, last message, verifier error,
  models already tried), truncated so it cannot become a second prompt.
- Process heartbeat and a configurable per-tool timeout, so a silent run
  reports the tool that is stuck and for how long instead of going quiet.
- Task contract preflight: non-positive `retries`/`timeout`/`verify_timeout`,
  a `fallbacks` entry that duplicates `model`, an `owns` pattern that is
  absolute or contains `..`, and an empty verify command are refused
  before anything is dispatched. `validate()` stays filesystem-free so a
  plan is still reviewable on a machine without the workdirs.
- Three "task is too big for one session" lint rules, all warnings:
  `BIG-TASK-MANY-STEPS`, `BIG-TASK-MANY-ARTEFACTS`, `BIG-TASK-NO-CHECKPOINT`.
- Per-task usage and cost. `normalize_stats` and `estimate_cost` existed
  but nothing called them, so a finished task carried no token data. The
  record now has `stats`, `model_used`, and `estimated_cost` (the last
  only when a price table is configured, otherwise `None`).
- `CHANGELOG.md` and `MIGRATIONS.md`.
- `benchmarks/`, a reproducible in-process benchmark with a fixture repo
  and raw JSON results.
- `run(resume=True)`: a run with a store and a `run_id` adopts tasks a
  previous run already finished instead of dispatching them again.

### Changed

- `run()` gained the `resume` keyword. It defaults to `False`, so existing
  calls behave exactly as before and no run silently changes meaning.
- Entry points insert their own directory on `sys.path` before importing
  siblings, so `cli.py` and `mcp_server.py` work when launched by `runpy`,
  from a symlink, or by `python3 -m` from another directory, not only via
  `python3 cli.py`.
- A retry now carries failure context. The prompt sent on attempt N is no
  longer identical to attempt 1.

## [0.1.0] - 2026-09-24

First public release. Headless foreman for OpenCode: a stdlib-only core
that plans tasks, dispatches them to `opencode serve`, verifies results
against independent commands, and records evidence.

### Added

- `Fleet` HTTP client over `opencode serve`, with dispatch, status, and
  session lifecycle.
- `Orchestrator` with dependency ordering, parallel dispatch, retries,
  fallback models, and a preflight `validate() -> plan()` gate.
- Independent verification: `verify` commands run by the orchestrator
  (not the agent), tokenized with `shlex` and executed without a shell.
- `Task.owns` ownership patterns and preflight rejection of overlapping
  ownership between tasks in the same workdir.
- Exit-code contract via `run_status()`: `0` full success, `1` agent
  failure, `2` verification failure, `3` preflight failure.
- Artifact manifest collection per task (`files_changed`) independent of
  verification status.
- Durable SQLite run store, JSONL event sink, cost and model accounting,
  review/approval/merge boundary, MCP read-only plus guarded mutation
  tools, worktree isolation with setup hooks and optional reserved ports.
- `prompt_lint.py` and the `oc-prompt-lint` entry point.
- `oc-fleet-wait.py`, a detached waiter that polls one session and writes
  a result file.

[Unreleased]: https://github.com/DavidPandleton/oc-fleet/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/DavidPandleton/oc-fleet/releases/tag/v0.1.0
