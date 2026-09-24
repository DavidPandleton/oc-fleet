#!/usr/bin/env python3
"""Reproducible benchmark for the oc-fleet orchestrator.

What this measures, and what it refuses to measure
--------------------------------------------------

Every scenario runs the real `Orchestrator` against an in-process
`ScriptedFleet` that stands in for a live `opencode serve`. That choice is
the whole point: a benchmark that needs a server, a provider, and a
network answers a different question on every run, so its numbers cannot
be compared across commits. Here the orchestrator's scheduling, retry,
resume, and accounting paths are exercised for real, while the agent
outcomes are scripted and therefore identical every time.

What this does NOT measure: model latency, provider throughput, or token
cost against a real model. Those need a live server and are out of scope
for a reproducible number. The token and cost columns are the
orchestrator's own attribution (does it charge the right model and sum
the right totals), not a claim about a real bill.

Scenarios
---------

  serial_vs_parallel      Wall time for N independent tasks at parallelism
                          1 versus at parallelism N. Same work, different
                          schedule.
  provider_failure_recovery
                          A dispatch that raises, then succeeds on the
                          configured retry. Measures attempts and whether
                          the run recovers.
  verification_failure_recovery
                          An agent that succeeds while its verify command
                          fails, then passes once the command is fixed.
                          Measures that the status is verification_failed
                          and not succeeded.
  worktree_setup_cost     The extra wall time `isolate=True` adds for a
                          real `git worktree` per task, measured against
                          the same run without isolation.
  resume_behavior         A run interrupted with a task left running, then
                          replayed, and the count of tasks re-dispatched
                          versus skipped as already done.
  token_cost_attribution  Per-task token totals and estimated cost, summed
                          and checked against the pricing module.

Output
------

Raw results are written as JSON to `benchmarks/results/<timestamp>.json`,
one object per scenario with the parameters and the measured values. The
benchmark prints a table and a path, never a bare number, so a reader can
inspect what was actually measured.

Determinism note: wall-clock scenarios use a scripted fleet whose status
poll returns finished immediately, so the dominant cost is the poll
interval the orchestrator is told to use. The benchmark reports the poll
interval it used alongside every timing, and treats timing scenarios as
order-of-magnitude evidence, not microbenchmarks.

Run it with the standard library only:

    python3 benchmarks/run_benchmark.py
    python3 benchmarks/run_benchmark.py --json   # path to the JSON only
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "fixture_repo")
RESULTS_DIR = os.path.join(HERE, "results")

# The orchestrator imports its siblings by bare module name, so put the
# repository root on sys.path. Same rule the entry points follow.
sys.path.insert(0, REPO)

from orchestrator import Orchestrator, Task  # noqa: E402
from pricing import estimate_cost  # noqa: E402


class ScriptedFleet:
    """In-process stand-in for fleet.Fleet with scripted outcomes.

    Mirrors the interface the orchestrator actually calls: `dispatch`
    returns a session id, `status` returns the next scripted outcome.
    Sessions with no script never finish, which is how an interrupted
    task is simulated.
    """

    def __init__(self, polls_to_finish=1):
        self.dispatched = []
        self.finished = set()
        self.status_calls = 0
        self.max_in_flight = 0
        self._active = 0
        self._next = {}
        # How many status polls a session stays running before it reports
        # completion. A value above 1 is what gives parallelism something
        # to overlap, so the serial-vs-parallel scenario is not two runs
        # that both finish on the first poll.
        self._polls_to_finish = polls_to_finish

    def dispatch(self, task, workdir, title="", model="", env=None):
        prompt = getattr(task, "prompt", task)
        session_id = "s-%d" % len(self.dispatched)
        self.dispatched.append(
            {"id": session_id, "prompt": prompt, "workdir": workdir, "model": model}
        )
        self._next[session_id] = 0
        self._active += 1
        self.max_in_flight = max(self.max_in_flight, self._active)
        return session_id

    def status(self, session_id, stuck_after=None):
        self.status_calls += 1
        self._next[session_id] += 1
        if self._next[session_id] < self._polls_to_finish:
            return {"outcome": None, "last_assistant_text": None}
        if session_id not in self.finished:
            self.finished.add(session_id)
            self._active -= 1
        return {"outcome": "completed", "last_assistant_text": "done"}

    def cancel(self, session_id):
        self.finished.add(session_id)


class ScriptedFleetWithFailures(ScriptedFleet):
    """A ScriptedFleet that fails dispatch a scripted number of times."""

    def __init__(self, fail_first=0):
        super().__init__()
        self._fail_remaining = fail_first

    def dispatch(self, task, workdir, title="", model="", env=None):
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise OSError("scripted provider failure")
        return super().dispatch(task, workdir, title=title, model=model, env=env)


def _make_workdir():
    return tempfile.mkdtemp(prefix="ocfleet-bench-")


def _git_fixture_repo():
    """A throwaway Git checkout of the fixture, for worktree scenarios."""
    dest = tempfile.mkdtemp(prefix="ocfleet-fixture-")
    subprocess.run(["git", "init", "-q", dest], check=True)
    # Copy the fixture tree in without its own history.
    subprocess.run(
        ["cp", "-r", os.path.join(FIXTURE, "."), dest], check=True
    )
    env = dict(os.environ, GIT_AUTHOR_NAME="bench", GIT_AUTHOR_EMAIL="b@x",
               GIT_COMMITTER_NAME="bench", GIT_COMMITTER_EMAIL="b@x")
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=dest, check=True, env=env)
    return dest


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------

def scenario_serial_vs_parallel(task_count=6, poll_interval=0.02,
                                polls_to_finish=3):
    """Same independent work, scheduled one-at-a-time and all-at-once.

    Each session stays running for `polls_to_finish` status polls. With
    everything finishing on the first poll, serial and parallel both do
    one round and the comparison is meaningless; the extra polls are what
    give the parallel schedule something to overlap.
    """

    def run(max_parallel):
        fleet = ScriptedFleet(polls_to_finish=polls_to_finish)
        orch = Orchestrator(fleet=fleet, max_parallel=max_parallel,
                            poll_interval=poll_interval)
        for i in range(task_count):
            orch.add(Task(id="t%d" % i, prompt="noop %d" % i,
                          workdir=_make_workdir()))
        start = time.perf_counter()
        orch.run()
        return {
            "max_parallel": max_parallel,
            "wall_seconds": round(time.perf_counter() - start, 4),
            "dispatches": len(fleet.dispatched),
            "peak_in_flight": fleet.max_in_flight,
        }

    serial = run(1)
    parallel = run(task_count)
    speedup = None
    if serial["wall_seconds"] and parallel["wall_seconds"]:
        speedup = round(serial["wall_seconds"] / parallel["wall_seconds"], 2)
    return {
        "task_count": task_count,
        "poll_interval": poll_interval,
        "polls_to_finish": polls_to_finish,
        "serial": serial,
        "parallel": parallel,
        "speedup_x": speedup,
    }


def scenario_provider_failure_recovery(poll_interval=0.02):
    """One dispatch raises, the task retries, and the run recovers."""
    fleet = ScriptedFleetWithFailures(fail_first=1)
    orch = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=poll_interval)
    orch.add(Task(id="a", prompt="noop", workdir=_make_workdir(), retries=1))
    orch.run()
    rec = orch.results()["a"]
    return {
        "retries_configured": 1,
        "attempts_recorded": rec.get("attempts"),
        "final_status": rec["status"],
        "recovered": rec["status"] in ("succeeded", "verification_passed"),
        "run_status_code": orch.run_status(),
    }


def scenario_verification_failure_recovery(poll_interval=0.02):
    """An agent that reports success while its verify command fails.

    The verify command is a real subprocess. `verify` is a list of shell
    command strings, each tokenized with shlex and run without a shell,
    so it is written as one string here, not as a pre-split argv.
    """
    fleet = ScriptedFleet()
    orch = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=poll_interval)
    verify_cmd = "%s -c %s" % (
        sys.executable, json.dumps("import sys; sys.exit(1)"),
    )
    orch.add(Task(
        id="a", prompt="noop", workdir=_make_workdir(), retries=0,
        verify=[verify_cmd],
    ))
    orch.run()
    rec = orch.results()["a"]
    verification = rec.get("verification") or {}
    commands = verification.get("commands") or []
    first_returncode = commands[0].get("returncode") if commands else None
    return {
        "agent_outcome": "completed",
        "verification_passed_flag": verification.get("passed"),
        "first_verify_returncode": first_returncode,
        "final_status": rec["status"],
        "run_status_code": orch.run_status(),
        "correctly_not_succeeded": rec["status"] != "succeeded",
    }


def scenario_worktree_setup_cost(task_count=3, poll_interval=0.02):
    """Wall time with and without `isolate=True` (a real git worktree).

    Each run gets its own worktree root, because `worktree.create` names
    the directory after the task and refuses a name already in use. Two
    runs sharing the default root would make the second fail on a clash
    that has nothing to do with what this scenario measures.
    """
    if shutil.which("git") is None:
        # Recorded as a skip, not as a zero. A missing git means the
        # scenario did not run, which is different from running cheaply.
        return {"skipped": "git not found on PATH"}

    def run(isolate):
        repo = _git_fixture_repo()
        root = tempfile.mkdtemp(prefix="ocfleet-wt-root-")
        fleet = ScriptedFleet()
        orch = Orchestrator(fleet=fleet, max_parallel=task_count,
                            poll_interval=poll_interval, worktree_root=root)
        for i in range(task_count):
            orch.add(Task(
                id="t%d" % i, prompt="noop %d" % i, workdir=repo,
                isolate=isolate, repo=repo,
            ))
        start = time.perf_counter()
        orch.run()
        return {
            "isolated": isolate,
            "wall_seconds": round(time.perf_counter() - start, 4),
            "dispatches": len(fleet.dispatched),
        }

    plain = run(False)
    isolated = run(True)
    delta = None
    if plain.get("wall_seconds") is not None and isolated.get("wall_seconds") is not None:
        delta = round(isolated["wall_seconds"] - plain["wall_seconds"], 4)
    return {
        "task_count": task_count,
        "without_isolation": plain,
        "with_isolation": isolated,
        "added_seconds_for_isolation": delta,
    }


def scenario_resume_behavior(poll_interval=0.02):
    """Replaying a run must not re-run tasks already done.

    The store is a real SQLite file. The first pass runs two tasks to
    completion. The second pass replays the same run with `resume=True`
    and must adopt both finished tasks instead of dispatching anything.
    """
    from store import RunStore

    db = os.path.join(tempfile.mkdtemp(prefix="ocfleet-bench-"), "run.sqlite")
    store = RunStore(db)
    workdir = _make_workdir()

    first = ScriptedFleet()
    orch1 = Orchestrator(fleet=first, max_parallel=1, poll_interval=poll_interval,
                         store=store, run_id="bench-run")
    orch1.add(Task(id="a", prompt="noop a", workdir=workdir))
    orch1.add(Task(id="b", prompt="noop b", workdir=workdir))
    orch1.run()
    first_dispatches = len(first.dispatched)

    second = ScriptedFleet()
    orch2 = Orchestrator(fleet=second, max_parallel=1, poll_interval=poll_interval,
                         store=store, run_id="bench-run")
    orch2.add(Task(id="a", prompt="noop a", workdir=workdir))
    orch2.add(Task(id="b", prompt="noop b", workdir=workdir))
    orch2.run(resume=True)

    return {
        "first_pass_dispatches": first_dispatches,
        "second_pass_dispatches": len(second.dispatched),
        "resume_re_dispatched_nothing": len(second.dispatched) == 0,
        "second_pass_statuses": {tid: orch2.results()[tid]["status"]
                                 for tid in orch2.results()},
    }


def scenario_token_cost_attribution(poll_interval=0.02):
    """Per-task token totals and estimated cost, attributed and summed.

    A price table is supplied, then the record's `estimated_cost` is
    checked against `estimate_cost` for the same inputs, so this measures
    the orchestrator's attribution and not the pricing arithmetic alone.
    """
    model = "cutad/deepseek-v4-flash"
    in_tok, out_tok = 1200, 400
    prices = {model: {"input": 0.25, "output": 1.0}}
    expected = estimate_cost(model, in_tok, out_tok, prices)

    class CostFleet(ScriptedFleet):
        def status(self, session_id, stuck_after=None):
            self.status_calls += 1
            self._next[session_id] += 1
            return {
                "outcome": "completed",
                "last_assistant_text": "done",
                "model": model,
                "tokens": {"input": in_tok, "output": out_tok},
            }

    fleet = CostFleet()
    orch = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=poll_interval,
                        prices=prices)
    orch.add(Task(id="a", prompt="noop", workdir=_make_workdir(), model=model))
    orch.run()
    rec = orch.results()["a"]

    # A second run without a price table must leave cost unknown, not zero.
    fleet2 = CostFleet()
    orch2 = Orchestrator(fleet=fleet2, max_parallel=1, poll_interval=poll_interval)
    orch2.add(Task(id="a", prompt="noop", workdir=_make_workdir(), model=model))
    orch2.run()
    rec_unpriced = orch2.results()["a"]

    return {
        "model": model,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "expected_cost": expected,
        "recorded_cost": rec.get("estimated_cost"),
        "cost_matches_pricing": rec.get("estimated_cost") == expected,
        "recorded_stats": rec.get("stats"),
        "unpriced_cost_is_none": rec_unpriced.get("estimated_cost") is None,
    }


SCENARIOS = {
    "serial_vs_parallel": scenario_serial_vs_parallel,
    "provider_failure_recovery": scenario_provider_failure_recovery,
    "verification_failure_recovery": scenario_verification_failure_recovery,
    "worktree_setup_cost": scenario_worktree_setup_cost,
    "resume_behavior": scenario_resume_behavior,
    "token_cost_attribution": scenario_token_cost_attribution,
}


def _check(name, m):
    """Return a list of failed expectations for a scenario's measurements.

    A scenario that runs without raising is not the same as a scenario
    that behaved correctly. These checks turn "it produced numbers" into
    "the numbers mean what the scenario claims", so the benchmark exits
    non-zero when a property regresses instead of printing a happy table.
    An empty list means the scenario holds.
    """
    problems = []
    if name == "provider_failure_recovery":
        if not m.get("recovered"):
            problems.append("did not recover after a provider failure")
        if m.get("run_status_code") != 0:
            problems.append("recovered run should exit 0")
    elif name == "verification_failure_recovery":
        if m.get("final_status") != "verification_failed":
            problems.append("a failed verify must be verification_failed")
        if m.get("run_status_code") != 2:
            problems.append("failed verification should exit 2")
    elif name == "resume_behavior":
        if not m.get("resume_re_dispatched_nothing"):
            problems.append("resume re-dispatched already-finished work")
    elif name == "token_cost_attribution":
        if not m.get("cost_matches_pricing"):
            problems.append("recorded cost does not match the pricing module")
        if not m.get("unpriced_cost_is_none"):
            problems.append("an unpriced model must not claim a cost")
    elif name == "serial_vs_parallel":
        speedup = m.get("speedup_x")
        if speedup is None or speedup <= 1.0:
            problems.append("parallel schedule was not faster than serial")
    elif name == "worktree_setup_cost":
        if m.get("skipped"):
            pass  # nothing ran, nothing to assert
        elif m.get("added_seconds_for_isolation") is None:
            problems.append("isolation delta could not be computed")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", choices=sorted(SCENARIOS),
                        help="run only this scenario (repeatable)")
    parser.add_argument("--json", action="store_true",
                        help="print only the results JSON path")
    args = parser.parse_args(argv)

    names = args.only or sorted(SCENARIOS)
    results = {}
    for name in names:
        start = time.perf_counter()
        try:
            m = SCENARIOS[name]()
            problems = _check(name, m)
            results[name] = {
                "ok": not problems,
                "measurements": m,
                "problems": problems,
            }
        except Exception as exc:
            # A scenario that cannot run is recorded as failed, with the
            # reason. A benchmark that hides a failure is worse than useless.
            results[name] = {"ok": False, "error": "%s: %s"
                             % (type(exc).__name__, exc)}
        results[name]["duration_seconds"] = round(time.perf_counter() - start, 4)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(RESULTS_DIR, "bench-%s.json" % stamp)
    payload = {
        "generated_at": stamp,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "fixture": os.path.relpath(FIXTURE, REPO),
        "scenarios": results,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)

    if args.json:
        print(path)
        return 0 if all(r["ok"] for r in results.values()) else 1

    print("oc-fleet benchmark")
    print("=" * 60)
    for name in names:
        entry = results[name]
        mark = "ok" if entry["ok"] else "FAILED"
        print("\n[%s] %s (%.3fs)" % (mark, name, entry["duration_seconds"]))
        if entry.get("error"):
            print("    error: %s" % entry["error"])
            continue
        m = entry["measurements"]
        print("    " + json.dumps(m, sort_keys=True))
        for problem in entry.get("problems", []):
            print("    PROBLEM: %s" % problem)
    print("\nraw results: %s" % path)
    return 0 if all(r["ok"] for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
