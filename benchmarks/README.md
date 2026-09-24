# Benchmarks

A reproducible benchmark for the oc-fleet orchestrator, plus the raw JSON
it produces. It exists so that claims about scheduling, recovery, resume,
and cost attribution can be checked against a run instead of asserted.

## Run it

```bash
python3 benchmarks/run_benchmark.py
```

Standard library only. No `opencode serve`, no network, no provider key.

```bash
python3 benchmarks/run_benchmark.py --only serial_vs_parallel
python3 benchmarks/run_benchmark.py --json   # print the results path only
```

Exit code is `0` when every scenario holds and `1` when any does not, so
it can gate a release the same way the test suite does.

## Why an in-process fleet

Each scenario drives the real `Orchestrator` against a `ScriptedFleet`
that stands in for a live server. A benchmark that needed a server, a
provider, and a network would answer a different question every run, and
its numbers could not be compared across commits. Here the orchestrator's
scheduling, retry, resume, and accounting paths run for real, while the
agent outcomes are scripted and identical every time.

What this does **not** measure: model latency, provider throughput, or a
real bill. The cost column checks the orchestrator's own attribution
(does it charge the right model and match the pricing module), not what a
provider would charge.

## What each scenario measures, and the property it asserts

| Scenario | Measures | Asserted property |
|---|---|---|
| `serial_vs_parallel` | Wall time for N tasks at parallelism 1 vs N | Parallel is faster (`speedup_x > 1`) |
| `provider_failure_recovery` | Attempts after a dispatch that raises, then succeeds | Recovered, run exits 0 |
| `verification_failure_recovery` | An agent that succeeds while its verify fails | Status is `verification_failed`, run exits 2 |
| `worktree_setup_cost` | Extra wall time for `isolate=True` (real `git worktree`) | Delta is computable |
| `resume_behavior` | A replay with `resume=True` | Nothing already finished is re-dispatched |
| `token_cost_attribution` | Per-task `stats`, `model_used`, `estimated_cost` | Cost matches pricing; unpriced is `None` |

A scenario that merely runs without raising is not counted as passing.
Each has an explicit expectation in `_check`; a failed expectation is
printed as `PROBLEM` and forces a non-zero exit.

## Results

Raw results are written to `benchmarks/results/<timestamp>.json`, one
object per scenario with its parameters, measurements, and any problems.
The printed table is a convenience; the JSON is the record.

## Determinism and its limits

Timing scenarios use `poll_interval` as the dominant cost, reported
alongside every measurement. Treat the wall-clock columns as
order-of-magnitude evidence and the property columns as the real result.
The `polls_to_finish` parameter in `serial_vs_parallel` is what gives a
parallel schedule something to overlap; with everything finishing on the
first poll, serial and parallel are the same run and the comparison says
nothing.

`worktree_setup_cost` is recorded as `skipped` (not as a zero) when `git`
is not on `PATH`. A scenario that did not run is different from one that
ran cheaply.

## Fixture

`fixture_repo/` is a small real Python project, used when a scenario needs
a Git repository to create worktrees from. See its own README. It is not
collected by the main test suite: `pyproject.toml` sets
`norecursedirs = ["benchmarks"]` so a fixture's tests are never counted as
oc-fleet's.
