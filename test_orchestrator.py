"""Tests for orchestrator.Orchestrator using a fake fleet (no server needed)."""

import contextlib
import io
import time
import unittest

from fleet import Fleet
from orchestrator import Orchestrator, Task


class FakeTime:
    """Deterministic clock so per-task timeouts and polling are testable."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@contextlib.contextmanager
def fake_clock():
    """Patch time.monotonic/time.sleep with a deterministic clock."""
    saved_monotonic = time.monotonic
    saved_sleep = time.sleep
    fake = FakeTime()
    time.monotonic = fake.monotonic
    time.sleep = fake.sleep
    try:
        yield fake
    finally:
        time.monotonic = saved_monotonic
        time.sleep = saved_sleep


class FakeFleet:
    """In-memory stand-in for fleet.Fleet.

    Per-session scripted outcomes drive the poll loop:
      * a string (e.g. "done", "failed"): the session finishes on the next
        status poll,
      * None: still running (never finishes on its own),
      * a list: one outcome per status poll,
      * a callable: invoked as fn(poll_index) -> outcome, so one session can
        fail once and succeed on a later poll.
    """

    def __init__(self):
        self.dispatched = []
        self.events = []
        self.status_calls = 0
        self.max_in_flight = 0
        self.dispatch_count_at_first_status = None
        self.fail_dispatch = False
        self.fail_dispatch_with: BaseException | None = None
        self._outcomes = {}
        self._finished = set()
        self._next_idx = {}

    def set_outcome(self, session_id, outcome):
        self._outcomes[session_id] = outcome

    def dispatch(self, prompt, workdir, title="", model=""):
        if self.fail_dispatch:
            raise OSError("boom")
        if self.fail_dispatch_with is not None:
            raise self.fail_dispatch_with
        session_id = "s-%d" % len(self.dispatched)
        self.dispatched.append(
            {"id": session_id, "prompt": prompt, "workdir": workdir, "title": title, "model": model}
        )
        self.events.append(("dispatch", session_id))
        self.max_in_flight = max(self.max_in_flight, len(self.dispatched) - len(self._finished))
        self._next_idx[session_id] = 0
        return session_id

    def status(self, session_id):
        self.status_calls += 1
        if self.dispatch_count_at_first_status is None:
            self.dispatch_count_at_first_status = len(self.dispatched)
        index = self._next_idx[session_id]
        self._next_idx[session_id] = index + 1
        outcome = self._outcome_for(session_id, index)
        if outcome is None:
            return {"outcome": None, "last_assistant_text": None}
        self._finished.add(session_id)
        self.events.append(("finish", session_id))
        return {"outcome": outcome, "last_assistant_text": "text for %s" % session_id}

    def _outcome_for(self, session_id, index):
        script = self._outcomes.get(session_id)
        if callable(script):
            return script(index)
        if isinstance(script, list):
            return script[index] if index < len(script) else script[-1]
        return script


def make_orch(fleet, tasks, poll_interval=0.01, **kwargs):
    orch = Orchestrator(fleet=fleet, poll_interval=poll_interval, **kwargs)
    for task in tasks:
        orch.add(task)
    return orch


class AddAndValidateTestCase(unittest.TestCase):
    def test_add_duplicate_id_raises(self):
        orch = Orchestrator(fleet=FakeFleet())
        orch.add(Task(id="a", prompt="p"))
        with self.assertRaises(ValueError) as ctx:
            orch.add(Task(id="a", prompt="q"))
        self.assertIn("duplicate", str(ctx.exception))
        self.assertIn("a", str(ctx.exception))

    def test_add_unknown_dependency_raises(self):
        orch = Orchestrator(fleet=FakeFleet())
        with self.assertRaises(ValueError) as ctx:
            orch.add(Task(id="b", prompt="p", depends_on=["ghost"]))
        self.assertIn("ghost", str(ctx.exception))

    def test_add_rejects_non_task(self):
        orch = Orchestrator(fleet=FakeFleet())
        with self.assertRaises(TypeError):
            orch.add("not a task")

    def test_validate_detects_two_node_cycle(self):
        orch = Orchestrator(fleet=FakeFleet())
        orch.add(Task(id="a", prompt="p"))
        orch.add(Task(id="b", prompt="p"))
        orch._tasks["a"].depends_on = ["b"]
        orch._tasks["b"].depends_on = ["a"]
        with self.assertRaises(ValueError) as ctx:
            orch.validate()
        self.assertIn("cycle", str(ctx.exception))
        self.assertIn("a -> b -> a", str(ctx.exception))

    def test_validate_detects_three_node_cycle(self):
        orch = Orchestrator(fleet=FakeFleet())
        orch.add(Task(id="a", prompt="p"))
        orch.add(Task(id="b", prompt="p"))
        orch.add(Task(id="c", prompt="p"))
        orch._tasks["a"].depends_on = ["b"]
        orch._tasks["b"].depends_on = ["c"]
        orch._tasks["c"].depends_on = ["a"]
        with self.assertRaises(ValueError) as ctx:
            orch.validate()
        self.assertIn("cycle", str(ctx.exception))
        self.assertIn("a -> b -> c -> a", str(ctx.exception))

    def test_validate_detects_missing_dependency(self):
        orch = Orchestrator(fleet=FakeFleet())
        orch.add(Task(id="a", prompt="p"))
        orch._tasks["a"].depends_on = ["ghost"]
        with self.assertRaises(ValueError) as ctx:
            orch.validate()
        self.assertIn("ghost", str(ctx.exception))

    def test_validate_accepts_diamond(self):
        orch = Orchestrator(fleet=FakeFleet())
        orch.add(Task(id="a", prompt="p"))
        orch.add(Task(id="b", prompt="p", depends_on=["a"]))
        orch.add(Task(id="c", prompt="p", depends_on=["a"]))
        orch.add(Task(id="d", prompt="p", depends_on=["b", "c"]))
        orch.validate()  # must not raise

    def test_validate_rejects_empty_workdir(self):
        """workdir kosong ditangkap di validate(), bukan di tengah run.

        Fleet.dispatch menolaknya juga, tapi kalau baru ketahuan saat
        dispatch, sesi lain sudah berjalan dan menulis ke tree yang sama.
        """
        orch = Orchestrator(fleet=FakeFleet())
        orch.add(Task(id="a", prompt="p", workdir="  "))
        with self.assertRaises(ValueError) as ctx:
            orch.validate()
        self.assertIn("workdir", str(ctx.exception))
        self.assertIn("'a'", str(ctx.exception))

    def test_validate_rejects_empty_prompt(self):
        orch = Orchestrator(fleet=FakeFleet())
        orch.add(Task(id="a", prompt=""))
        with self.assertRaises(ValueError) as ctx:
            orch.validate()
        self.assertIn("prompt", str(ctx.exception))

    def test_validate_rejects_bad_task_before_any_dispatch(self):
        """validate() menolak sebelum satu sesi pun dibuat."""
        fleet = FakeFleet()
        orch = Orchestrator(fleet=fleet)
        orch.add(Task(id="good", prompt="p", workdir="/tmp/x"))
        orch.add(Task(id="bad", prompt="p", workdir=""))
        with self.assertRaises(ValueError):
            orch.run()
        self.assertEqual(fleet.dispatched, [])

    def test_run_rejects_cycle(self):
        orch = Orchestrator(fleet=FakeFleet())
        orch.add(Task(id="a", prompt="p"))
        orch.add(Task(id="b", prompt="p"))
        orch._tasks["a"].depends_on = ["b"]
        orch._tasks["b"].depends_on = ["a"]
        with self.assertRaises(ValueError):
            orch.run()
        self.assertEqual(orch.results()["a"]["status"], "pending")


class TopologyTestCase(unittest.TestCase):
    def test_topological_order_respects_dependencies(self):
        orch = Orchestrator(fleet=FakeFleet())
        orch.add(Task(id="a", prompt="p"))
        orch.add(Task(id="b", prompt="p"))
        orch.add(Task(id="c", prompt="p", depends_on=["a"]))
        orch.add(Task(id="d", prompt="p", depends_on=["b"]))
        orch.add(Task(id="e", prompt="p", depends_on=["c", "d"]))
        order = orch.topological_order()
        self.assertEqual(sorted(order), ["a", "b", "c", "d", "e"])
        pos = {tid: i for i, tid in enumerate(order)}
        self.assertLess(pos["a"], pos["c"])
        self.assertLess(pos["a"], pos["e"])
        self.assertLess(pos["b"], pos["d"])
        self.assertLess(pos["b"], pos["e"])
        self.assertLess(pos["c"], pos["e"])
        self.assertLess(pos["d"], pos["e"])

    def test_run_executes_in_topological_order(self):
        fleet = FakeFleet()
        for i in range(3):
            fleet.set_outcome("s-%d" % i, "done")
        orch = make_orch(
            fleet,
            [
                Task(id="a", prompt="pa"),
                Task(id="b", prompt="pb"),
                Task(id="c", prompt="pc", depends_on=["a", "b"]),
            ],
        )
        orch.run()
        self.assertEqual([t["prompt"] for t in fleet.dispatched], ["pa", "pb", "pc"])
        results = orch.results()
        # c could only start once both a and b had finished
        self.assertTrue(results["a"]["started_at"] < results["c"]["started_at"])
        self.assertTrue(results["a"]["finished_at"] <= results["c"]["started_at"])
        self.assertTrue(results["b"]["finished_at"] <= results["c"]["started_at"])

    def test_run_executes_diamond_in_order(self):
        fleet = FakeFleet()
        for i in range(4):
            fleet.set_outcome("s-%d" % i, "done")
        orch = make_orch(
            fleet,
            [
                Task(id="a", prompt="pa"),
                Task(id="b", prompt="pb", depends_on=["a"]),
                Task(id="c", prompt="pc", depends_on=["a"]),
                Task(id="d", prompt="pd", depends_on=["b", "c"]),
            ],
        )
        orch.run()
        self.assertEqual([t["prompt"] for t in fleet.dispatched], ["pa", "pb", "pc", "pd"])
        results = orch.results()
        for tid in ("a", "b", "c", "d"):
            self.assertEqual(results[tid]["status"], "succeeded")


class ParallelAndFailureTestCase(unittest.TestCase):
    def test_parallel_branch_execution_up_to_max_parallel(self):
        fleet = FakeFleet()
        # s-0 (task a) stays in flight for 2 polls so the other tasks cannot
        # all start in the first sweep; the rest finish on their first poll
        fleet.set_outcome("s-0", [None, "done"])
        for i in range(1, 5):
            fleet.set_outcome("s-%d" % i, "done")
        orch = make_orch(
            fleet,
            [Task(id=t, prompt="p-" + t) for t in ("a", "b", "c", "d", "e")],
            max_parallel=2,
        )
        orch.run()
        self.assertEqual(
            [t["id"] for t in fleet.dispatched], ["s-0", "s-1", "s-2", "s-3", "s-4"]
        )
        # when the first status poll happened, only 2 sessions were in
        # flight: the cap held back c, d and e until earlier work finished
        self.assertEqual(fleet.dispatch_count_at_first_status, 2)
        self.assertLessEqual(fleet.max_in_flight, 2)
        # and parallelism really happened: s-1 started before s-0 finished
        events = fleet.events
        self.assertEqual(events[0], ("dispatch", "s-0"))
        self.assertEqual(events[1], ("dispatch", "s-1"))
        self.assertGreater(events.index(("finish", "s-0")), 1)

    def test_two_independent_tasks_run_in_parallel(self):
        fleet = FakeFleet()
        for i in range(3):
            fleet.set_outcome("s-%d" % i, "done")
        orch = make_orch(
            fleet,
            [
                Task(id="a", prompt="pa"),
                Task(id="b", prompt="pb", depends_on=["a"]),
                Task(id="c", prompt="pc"),
            ],
            max_parallel=4,
        )
        orch.run()
        # c (independent) starts in the same sweep as a, before a finishes;
        # b (dependent on a) starts only after a's completion
        self.assertEqual([t["prompt"] for t in fleet.dispatched], ["pa", "pc", "pb"])
        self.assertEqual(fleet.dispatch_count_at_first_status, 2)

    def test_retry_on_failure_then_success(self):
        fleet = FakeFleet()
        fleet.set_outcome("s-0", lambda i: "failed" if i == 0 else "done")
        fleet.set_outcome("s-1", "done")
        fleet.set_outcome("s-2", "done")
        orch = make_orch(
            fleet,
            [
                Task(id="a", prompt="pa", retries=1),
                Task(id="b", prompt="pb", depends_on=["a"]),
            ],
            max_parallel=1,
        )
        orch.run()
        results = orch.results()
        self.assertEqual(results["a"]["status"], "succeeded")
        self.assertEqual(results["a"]["attempts"], 2)
        self.assertEqual(results["a"]["outcome"], "done")
        self.assertEqual(results["a"]["session_id"], "s-1")  # retried in a fresh session
        self.assertEqual(results["b"]["status"], "succeeded")
        self.assertEqual([t["id"] for t in fleet.dispatched], ["s-0", "s-1", "s-2"])

    def test_retry_exhaustion_marks_task_failed(self):
        fleet = FakeFleet()
        for i in range(3):
            fleet.set_outcome("s-%d" % i, "crashed")
        orch = make_orch(fleet, [Task(id="a", prompt="pa", retries=2)], max_parallel=1)
        orch.run()
        results = orch.results()
        self.assertEqual(results["a"]["status"], "failed")
        self.assertEqual(results["a"]["attempts"], 3)  # 1 + 2 retries
        self.assertEqual(results["a"]["outcome"], "crashed")
        self.assertEqual(len(fleet.dispatched), 3)

    def test_dependents_skipped_when_dependency_fails(self):
        fleet = FakeFleet()
        fleet.set_outcome("s-0", "failed")  # a: first attempt
        fleet.set_outcome("s-1", "done")  # b
        fleet.set_outcome("s-2", "failed")  # a: retry
        orch = make_orch(
            fleet,
            [
                Task(id="a", prompt="pa", retries=1),
                Task(id="b", prompt="pb"),
                Task(id="c", prompt="pc", depends_on=["a"]),
                Task(id="d", prompt="pd", depends_on=["c"]),
            ],
        )
        orch.run()
        results = orch.results()
        self.assertEqual(results["a"]["status"], "failed")
        self.assertEqual(results["c"]["status"], "skipped")
        self.assertEqual(results["d"]["status"], "skipped")
        self.assertEqual(results["b"]["status"], "succeeded")
        self.assertEqual(results["b"]["session_id"], "s-1")
        # c and d were never dispatched
        dispatched_prompts = [t["prompt"] for t in fleet.dispatched]
        self.assertNotIn("pc", dispatched_prompts)
        self.assertNotIn("pd", dispatched_prompts)

    def test_independent_branch_survives_retry_of_another(self):
        fleet = FakeFleet()
        fleet.set_outcome("s-0", "done")  # a
        fleet.set_outcome("s-1", lambda i: "failed" if i == 0 else "done")  # b: fails once
        fleet.set_outcome("s-2", "done")  # c
        fleet.set_outcome("s-3", "done")  # b retry
        orch = make_orch(
            fleet,
            [
                Task(id="a", prompt="pa"),
                Task(id="b", prompt="pb", retries=1),
                Task(id="c", prompt="pc"),
            ],
        )
        orch.run()
        results = orch.results()
        self.assertEqual(results["a"]["status"], "succeeded")
        self.assertEqual(results["b"]["status"], "succeeded")
        self.assertEqual(results["b"]["attempts"], 2)
        self.assertEqual(results["b"]["session_id"], "s-3")
        self.assertEqual(results["c"]["status"], "succeeded")
        self.assertEqual(results["c"]["session_id"], "s-2")

    def test_timeout_marks_task_failed_and_does_not_block_forever(self):
        fleet = FakeFleet()
        fleet.set_outcome("s-0", None)  # session never reports an outcome
        orch = make_orch(fleet, [Task(id="a", prompt="pa", timeout=2)], max_parallel=1)
        with fake_clock():
            orch.run()  # must return on its own once the 2s deadline passes
        results = orch.results()
        self.assertEqual(results["a"]["status"], "failed")
        self.assertIsNone(results["a"]["outcome"])
        self.assertEqual(results["a"]["attempts"], 1)
        self.assertEqual(results["a"]["session_id"], "s-0")
        self.assertGreaterEqual(fleet.status_calls, 190)
        self.assertLessEqual(fleet.status_calls, 210)
        self.assertAlmostEqual(results["a"]["duration"], 2.0, places=1)

    def test_timeout_of_dependency_skips_dependents(self):
        fleet = FakeFleet()
        fleet.set_outcome("s-0", None)
        orch = make_orch(
            fleet,
            [
                Task(id="a", prompt="pa", timeout=2),
                Task(id="b", prompt="pb", depends_on=["a"]),
            ],
        )
        with fake_clock():
            orch.run()
        results = orch.results()
        self.assertEqual(results["a"]["status"], "failed")
        self.assertEqual(results["b"]["status"], "skipped")
        self.assertEqual([t["prompt"] for t in fleet.dispatched], ["pa"])


class ResultsAndSummaryTestCase(unittest.TestCase):
    def test_results_shape(self):
        fleet = FakeFleet()
        fleet.set_outcome("s-0", "done")
        fleet.set_outcome("s-1", "failed")
        orch = make_orch(
            fleet,
            [
                Task(id="a", prompt="pa"),
                Task(id="b", prompt="pb"),
                Task(id="c", prompt="pc", depends_on=["b"]),
            ],
        )
        orch.run()
        results = orch.results()
        self.assertEqual(set(results), {"a", "b", "c"})
        for rec in results.values():
            for key in ("status", "session_id", "outcome", "attempts", "last_text"):
                self.assertIn(key, rec)
        self.assertEqual(results["a"]["status"], "succeeded")
        self.assertEqual(results["a"]["session_id"], "s-0")
        self.assertEqual(results["a"]["outcome"], "done")
        self.assertEqual(results["a"]["attempts"], 1)
        self.assertEqual(results["a"]["last_text"], "text for s-0")
        self.assertEqual(results["b"]["status"], "failed")
        self.assertEqual(results["b"]["outcome"], "failed")
        self.assertEqual(results["c"]["status"], "skipped")
        self.assertIsNone(results["c"]["session_id"])
        self.assertEqual(results["c"]["attempts"], 0)
        self.assertIsNone(results["c"]["last_text"])

    def test_summary_format(self):
        fleet = FakeFleet()
        fleet.set_outcome("s-0", "done")
        fleet.set_outcome("s-1", "done")
        for i in (2, 3):
            fleet.set_outcome("s-%d" % i, "crashed")
        orch = make_orch(
            fleet,
            [
                Task(id="a", prompt="pa"),
                Task(id="b", prompt="pb"),
                Task(id="c", prompt="pc", retries=1),
                Task(id="d", prompt="pd", depends_on=["c"]),
            ],
        )
        orch.run()
        lines = orch.summary().splitlines()
        # one line per task plus a totals line
        self.assertEqual(len(lines), 5)
        self.assertIn("a", lines[0])
        self.assertIn("succeeded", lines[0])
        self.assertIn("b", lines[1])
        self.assertIn("succeeded", lines[1])
        self.assertIn("c", lines[2])
        self.assertIn("failed", lines[2])
        self.assertIn("d", lines[3])
        self.assertIn("skipped", lines[3])
        self.assertTrue(lines[4].startswith("totals: 4 task(s)"))
        self.assertIn("2 succeeded", lines[4])
        self.assertIn("1 failed", lines[4])
        self.assertIn("1 skipped", lines[4])

    def test_run_dry_run_prints_plan_without_dispatching(self):
        orch = Orchestrator(max_parallel=2)  # no fleet needed for a dry run
        orch.add(Task(id="a", prompt="pa", workdir="/wd/a", title="A"))
        orch.add(Task(id="b", prompt="pb", depends_on=["a"]))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            order = orch.run(dry_run=True)
        out = buf.getvalue()
        self.assertEqual(order, ["a", "b"])
        self.assertIn("dry run", out)
        self.assertIn("a", out)
        self.assertIn("b", out)
        self.assertIn("/wd/a", out)

    def test_run_with_no_tasks_is_a_noop(self):
        orch = Orchestrator(fleet=FakeFleet())
        self.assertEqual(orch.run(), {})
        self.assertEqual(orch.summary(), "totals: 0 task(s) [none]")


class DispatchConfigTestCase(unittest.TestCase):
    def test_run_passes_task_config_to_dispatch(self):
        fleet = FakeFleet()
        fleet.set_outcome("s-0", "done")
        orch = make_orch(
            fleet,
            [
                Task(
                    id="a",
                    prompt="p-a",
                    workdir="/wd/a",
                    model="cutad/qwen3-8-flash-next",
                    title="t-a",
                )
            ],
        )
        orch.run()
        call = fleet.dispatched[0]
        self.assertEqual(call["prompt"], "p-a")
        self.assertEqual(call["workdir"], "/wd/a")
        self.assertEqual(call["title"], "t-a")
        self.assertEqual(call["model"], "cutad/qwen3-8-flash-next")

    def test_task_defaults(self):
        task = Task(id="a", prompt="p")
        self.assertEqual(task.workdir, ".")
        self.assertEqual(task.model, "cutad/qwen3-8-flash-next")
        self.assertEqual(task.title, "")
        self.assertEqual(task.depends_on, [])
        self.assertEqual(task.retries, 0)
        self.assertEqual(task.timeout, 1800)

    def test_default_poll_interval_is_three_seconds(self):
        self.assertEqual(Orchestrator(fleet=FakeFleet()).poll_interval, 3.0)

    def test_dispatch_error_marks_task_failed(self):
        fleet = FakeFleet()
        fleet.fail_dispatch = True
        orch = make_orch(fleet, [Task(id="a", prompt="pa")])
        orch.run()
        results = orch.results()
        self.assertEqual(results["a"]["status"], "failed")
        self.assertIsNone(results["a"]["session_id"])
        self.assertIn("dispatch failed", results["a"]["last_text"])

    def test_dispatch_exception_outside_api_errors_does_not_crash_run(self):
        """Exception di luar API_ERRORS tidak boleh menumbangkan run.

        Cacat yang ditemukan review (review.md temuan #4) dan dikonfirmasi:
        `_dispatch_ready` dulu hanya menangkap API_ERRORS, jadi RuntimeError
        dari respons server yang rusak lolos dan membunuh run(), meninggalkan
        task lain "running" selamanya. Suite tidak punya tes untuk ini -
        `test_dispatch_error_marks_task_failed` hanya memakai OSError.
        """
        for exc in (
            RuntimeError("malformed response"),
            TypeError("unexpected shape"),
            AttributeError("no attribute"),
            Exception("anything at all"),
        ):
            with self.subTest(exc=type(exc).__name__):
                fleet = FakeFleet()
                fleet.fail_dispatch_with = exc
                orch = make_orch(
                    fleet,
                    [Task(id="a", prompt="pa"), Task(id="b", prompt="pb")],
                )
                # Tidak boleh raise: run harus selesai.
                orch.run()
                results = orch.results()
                for tid in ("a", "b"):
                    self.assertEqual(results[tid]["status"], "failed")
                    self.assertNotEqual(
                        results[tid]["status"],
                        "running",
                        "task tidak boleh ditinggal dalam keadaan 'running'",
                    )

    def test_dispatch_failure_log_shows_reason_not_none(self):
        """Log kegagalan dispatch menyebut sebabnya, bukan '(None)'."""
        import contextlib
        import io

        fleet = FakeFleet()
        fleet.fail_dispatch_with = OSError("connection refused")
        orch = make_orch(fleet, [Task(id="a", prompt="pa")])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            orch.run()
        output = buf.getvalue()
        self.assertIn("connection refused", output)
        self.assertNotIn("(None)", output)

    def test_fleet_is_lazily_created_when_not_provided(self):
        orch = Orchestrator(max_parallel=1)
        self.assertIsNone(orch._fleet)
        self.assertIsInstance(orch.fleet, Fleet)
        self.assertIs(orch._fleet, orch.fleet)


class SelfLoopTestCase(unittest.TestCase):
    """A task depending on itself must never reach the executor.

    Found by cross-model review: the suite covered 2-node and 3-node cycles but
    not the self-edge, so a regression limited to that case would have passed
    CI. The add() guard and the DFS both need to keep catching it.
    """

    def test_self_dependency_rejected_at_add(self):
        orch = Orchestrator()
        with self.assertRaises(ValueError) as ctx:
            orch.add(Task(id="a", prompt="x", depends_on=["a"]))
        self.assertIn("unknown task", str(ctx.exception))

    def test_self_loop_introduced_by_mutation_is_caught(self):
        """Post-add mutation is the only way to build a self-edge."""
        orch = Orchestrator()
        orch.add(Task(id="a", prompt="x"))
        orch._tasks["a"].depends_on = ["a"]
        with self.assertRaises(ValueError) as ctx:
            orch.validate()
        self.assertIn("cycle", str(ctx.exception))

    def test_cycle_on_an_otherwise_isolated_node_is_caught(self):
        """A cycle unreachable from any root must still be detected."""
        orch = Orchestrator()
        orch.add(Task(id="x", prompt="x"))
        orch.add(Task(id="p", prompt="x"))
        orch.add(Task(id="q", prompt="x", depends_on=["p"]))
        orch._tasks["p"].depends_on = ["q"]
        with self.assertRaises(ValueError) as ctx:
            orch.validate()
        self.assertIn("cycle", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
