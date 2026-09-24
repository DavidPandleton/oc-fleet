"""Resuming a run must not redo work that already finished.

The v0.2 definition of done says a run "survives process restart and
resumes without duplicating completed work". The store records every
task transition, but `run()` had no path that read it back, so replaying
a run re-dispatched every task from scratch - including the ones that
had already succeeded, side effects and all.

The contract this pins:

  * `run(resume=True)` reads the store and skips a task already recorded
    as `succeeded` or `verification_passed`, without dispatching it,
  * a task left `running` or `pending` is dispatched again, because it
    did not finish,
  * a dependency satisfied by a resumed task still counts as satisfied,
  * resume is opt-in: a plain `run()` is unchanged and re-dispatches,
    because silently doing nothing on a second call would be worse than
    repeating work the caller asked to repeat.

Resuming is not exactly-once. A task that died mid-flight may run its
side effect twice; that is documented in MIGRATIONS.md and is not a bug
this test asserts against.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from orchestrator import Orchestrator, Task
from store import RunStore


class TrackFleet:
    """Fleet that records every dispatch and always succeeds."""

    def __init__(self):
        self.dispatched = []

    def dispatch(self, task, workdir, title="", model="", env=None):
        prompt = getattr(task, "prompt", task)
        session_id = "s-%d" % len(self.dispatched)
        self.dispatched.append({"id": session_id, "prompt": prompt})
        return session_id

    def status(self, session_id, stuck_after=None):
        return {"outcome": "completed", "last_assistant_text": "done"}


def fresh_store():
    db = os.path.join(tempfile.mkdtemp(prefix="ocfleet-resume-"), "run.sqlite")
    return RunStore(db)


class ResumeTest(unittest.TestCase):
    def test_a_finished_task_is_not_dispatched_again(self):
        store = fresh_store()
        workdir = tempfile.mkdtemp()

        first = TrackFleet()
        orch1 = Orchestrator(fleet=first, max_parallel=1, poll_interval=0.001,
                             store=store, run_id="r")
        orch1.add(Task(id="a", prompt="noop a", workdir=workdir))
        orch1.run()
        self.assertEqual(len(first.dispatched), 1)

        second = TrackFleet()
        orch2 = Orchestrator(fleet=second, max_parallel=1, poll_interval=0.001,
                             store=store, run_id="r")
        orch2.add(Task(id="a", prompt="noop a", workdir=workdir))
        orch2.run(resume=True)

        self.assertEqual(len(second.dispatched), 0,
                         "a resumed run must not re-dispatch a finished task")
        self.assertEqual(orch2.results()["a"]["status"], "succeeded")

    def test_a_task_left_running_is_dispatched_again(self):
        store = fresh_store()
        workdir = tempfile.mkdtemp()

        # Seed the store with a task that never finished.
        store.upsert_run("r", {"status": "running"})
        store.upsert_task("r", "a", {"id": "a", "status": "running",
                                     "prompt": "noop"})

        fleet = TrackFleet()
        orch = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.001,
                            store=store, run_id="r")
        orch.add(Task(id="a", prompt="noop a", workdir=workdir))
        orch.run(resume=True)

        self.assertEqual(len(fleet.dispatched), 1,
                         "an unfinished task must be retried on resume")

    def test_resume_is_opt_in_and_a_plain_run_repeats(self):
        store = fresh_store()
        workdir = tempfile.mkdtemp()

        first = TrackFleet()
        orch1 = Orchestrator(fleet=first, max_parallel=1, poll_interval=0.001,
                             store=store, run_id="r")
        orch1.add(Task(id="a", prompt="noop a", workdir=workdir))
        orch1.run()

        # No resume=True: the caller wants a fresh run, and gets one.
        second = TrackFleet()
        orch2 = Orchestrator(fleet=second, max_parallel=1, poll_interval=0.001,
                             store=store, run_id="r")
        orch2.add(Task(id="a", prompt="noop a", workdir=workdir))
        orch2.run()
        self.assertEqual(len(second.dispatched), 1)

    def test_a_dependency_satisfied_by_resume_unblocks_downstream(self):
        store = fresh_store()
        workdir = tempfile.mkdtemp()

        first = TrackFleet()
        orch1 = Orchestrator(fleet=first, max_parallel=1, poll_interval=0.001,
                             store=store, run_id="r")
        orch1.add(Task(id="a", prompt="noop a", workdir=workdir))
        orch1.run()

        second = TrackFleet()
        orch2 = Orchestrator(fleet=second, max_parallel=1, poll_interval=0.001,
                             store=store, run_id="r")
        orch2.add(Task(id="a", prompt="noop a", workdir=workdir))
        orch2.add(Task(id="b", prompt="noop b", workdir=workdir,
                       depends_on=["a"]))
        orch2.run(resume=True)

        dispatched_prompts = [d["prompt"] for d in second.dispatched]
        self.assertEqual(dispatched_prompts, ["noop b"],
                         "only the unfinished downstream task should run")
        self.assertEqual(orch2.results()["b"]["status"], "succeeded")

    def test_resume_without_a_store_is_a_plain_run(self):
        workdir = tempfile.mkdtemp()
        fleet = TrackFleet()
        orch = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.001)
        orch.add(Task(id="a", prompt="noop a", workdir=workdir))
        orch.run(resume=True)  # no store: nothing to resume from
        self.assertEqual(len(fleet.dispatched), 1)


if __name__ == "__main__":
    unittest.main()
