"""DAG-based task orchestration on top of fleet.Fleet.

A Task is one unit of work: a prompt dispatched to an OpenCode session.
The Orchestrator executes a DAG of Tasks where a task only starts once
every task in depends_on has succeeded. Independent tasks run in
parallel with at most max_parallel sessions in flight. A failed task is
retried up to Task.retries times; once it ultimately fails, its
dependents are marked skipped while unrelated branches keep running.

Waiting on a session follows the same pattern as oc-fleet-wait.py:
poll fleet.status(session_id) every poll_interval seconds (3s by
default) until an outcome is set or the per-task timeout expires, so
the runner never blocks forever.
"""

import time
from dataclasses import dataclass, field

from fleet import Fleet

FAILED_OUTCOMES = {"failed", "crashed", "error", "cancelled", "canceled"}
API_ERRORS = (OSError, ValueError, KeyError)
POLL_INTERVAL = 3.0


@dataclass
class Task:
    """One unit of work: a prompt dispatched to an OpenCode session."""

    id: str
    prompt: str
    workdir: str = "."
    model: str = "cutad/qwen3-8-flash-next"
    title: str = ""
    depends_on: list[str] = field(default_factory=list)
    retries: int = 0
    timeout: int = 1800


def _new_record():
    return {
        "status": "pending",
        "session_id": None,
        "outcome": None,
        "attempts": 0,
        "last_text": None,
        "started_at": None,
        "finished_at": None,
        "duration": None,
    }


class Orchestrator:
    """Run a DAG of Tasks over a Fleet, with retries and parallel branches.

    Statuses: pending, running, succeeded, failed, skipped.
    """

    def __init__(self, fleet=None, max_parallel=4, poll_interval=POLL_INTERVAL):
        self._fleet = fleet
        self.max_parallel = max(1, int(max_parallel))
        self.poll_interval = poll_interval
        self._tasks = {}
        self._task_order = []
        self._results = {}

    @property
    def fleet(self):
        """The fleet in use; a real Fleet is created lazily if none was passed in."""
        if self._fleet is None:
            self._fleet = Fleet()
        return self._fleet

    # -- graph construction --------------------------------------------------

    def add(self, task):
        """Register a Task. Raises ValueError on duplicate id or unknown dependency."""
        if not isinstance(task, Task):
            raise TypeError("add() expects a Task, got %s" % type(task).__name__)
        if task.id in self._tasks:
            raise ValueError("duplicate task id: %r" % task.id)
        for dep in task.depends_on:
            if dep not in self._tasks:
                raise ValueError("task %r depends on unknown task %r" % (task.id, dep))
        self._tasks[task.id] = task
        self._task_order.append(task.id)
        self._results[task.id] = _new_record()

    def validate(self):
        """Raise ValueError on a missing dependency or a dependency cycle."""
        for tid in self._task_order:
            for dep in self._tasks[tid].depends_on:
                if dep not in self._tasks:
                    raise ValueError("task %r depends on unknown task %r" % (tid, dep))
        cycle = self._find_cycle()
        if cycle:
            raise ValueError("dependency cycle detected: %s" % " -> ".join(cycle))

    def _find_cycle(self):
        """Iterative DFS over dependency edges; return the cycle path or None."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in self._task_order}
        for root in self._task_order:
            if color[root] != WHITE:
                continue
            color[root] = GRAY
            stack = [(root, iter(self._tasks[root].depends_on))]
            path = [root]
            while stack:
                _, deps = stack[-1]
                next_node = None
                for dep in deps:
                    if color[dep] == GRAY:
                        return path[path.index(dep):] + [dep]
                    if color[dep] == WHITE:
                        next_node = dep
                        break
                if next_node is not None:
                    color[next_node] = GRAY
                    stack.append((next_node, iter(self._tasks[next_node].depends_on)))
                    path.append(next_node)
                else:
                    color[stack[-1][0]] = BLACK
                    stack.pop()
                    path.pop()
        return None

    def topological_order(self):
        """Return task ids in dependency order (Kahn's algorithm, insertion
        order among the currently ready tasks)."""
        self.validate()
        indegree = {tid: len(self._tasks[tid].depends_on) for tid in self._task_order}
        children = {tid: [] for tid in self._task_order}
        for tid in self._task_order:
            for dep in self._tasks[tid].depends_on:
                children[dep].append(tid)
        ready = [tid for tid in self._task_order if indegree[tid] == 0]
        order = []
        while ready:
            ready.sort(key=self._task_order.index)
            node = ready.pop(0)
            order.append(node)
            for child in children[node]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        return order

    # -- execution ------------------------------------------------------------

    def run(self, dry_run=False):
        """Execute the DAG.

        Returns the topological order (dry_run) or the results() dict.
        """
        self.validate()
        if dry_run:
            return self._print_plan()
        pending = list(self._task_order)
        running = {}
        while pending or running:
            self._skip_blocked(pending)
            self._dispatch_ready(pending, running)
            if not running and pending:
                # unreachable for a validated DAG; guard against infinite loops
                break
            self._poll_running(pending, running)
            if running:
                time.sleep(self.poll_interval)
        return self.results()

    def _print_plan(self):
        order = self.topological_order()
        print("dry run: %d task(s), max_parallel=%d" % (len(order), self.max_parallel))
        for index, tid in enumerate(order, 1):
            task = self._tasks[tid]
            deps = ", ".join(task.depends_on) if task.depends_on else "-"
            print(
                "  %d. %s  deps: %s  model: %s  workdir: %s"
                % (index, tid, deps, task.model, task.workdir)
            )
        return order

    def _skip_blocked(self, pending):
        for tid in list(pending):
            blocked_by = [
                dep
                for dep in self._tasks[tid].depends_on
                if self._results[dep]["status"] in ("failed", "skipped")
            ]
            if not blocked_by:
                continue
            pending.remove(tid)
            self._results[tid]["status"] = "skipped"
            dep = blocked_by[0]
            print(
                "skipped: %s (dependency %s is %s)"
                % (tid, dep, self._results[dep]["status"])
            )

    def _ready(self, tid):
        return all(
            self._results[dep]["status"] == "succeeded"
            for dep in self._tasks[tid].depends_on
        )

    def _dispatch_ready(self, pending, running):
        for tid in list(pending):
            if len(running) >= self.max_parallel:
                break
            if not self._ready(tid):
                continue
            pending.remove(tid)
            task = self._tasks[tid]
            rec = self._results[tid]
            rec["status"] = "running"
            rec["attempts"] += 1
            if rec["started_at"] is None:
                rec["started_at"] = time.monotonic()
            try:
                session_id = self.fleet.dispatch(
                    task.prompt, task.workdir, title=task.title or tid, model=task.model
                )
            except API_ERRORS as exc:
                session_id = None
                rec["last_text"] = "dispatch failed: %s" % exc
            rec["session_id"] = session_id
            running[tid] = {
                "session_id": session_id,
                "deadline": time.monotonic() + max(float(task.timeout), 0.0),
                "last_state": {"outcome": None, "last_assistant_text": None},
            }
            print("started: %s (attempt %d, session %s)" % (tid, rec["attempts"], session_id or "-"))

    def _poll_running(self, pending, running):
        for tid in list(running):
            info = running.pop(tid)
            session_id = info["session_id"]
            if session_id is None or time.monotonic() >= info["deadline"]:
                self._finish_attempt(tid, info["last_state"], pending)
                continue
            try:
                state = self.fleet.status(session_id) or {}
            except API_ERRORS:
                state = info["last_state"]
            if not isinstance(state, dict):
                state = info["last_state"]
            info["last_state"] = state
            if state.get("outcome") is not None:
                self._finish_attempt(tid, state, pending)
            else:
                running[tid] = info

    def _finish_attempt(self, tid, state, pending):
        task = self._tasks[tid]
        rec = self._results[tid]
        outcome = state.get("outcome") if isinstance(state, dict) else None
        rec["outcome"] = outcome
        if isinstance(state, dict) and state.get("last_assistant_text") is not None:
            rec["last_text"] = state["last_assistant_text"]
        rec["finished_at"] = time.monotonic()
        if rec["started_at"] is not None:
            rec["duration"] = round(rec["finished_at"] - rec["started_at"], 3)
        if outcome is not None and outcome not in FAILED_OUTCOMES:
            rec["status"] = "succeeded"
            print(
                "finished: %s (outcome %s, %s)"
                % (tid, outcome, self._fmt_duration(rec["duration"]))
            )
            return
        if rec["attempts"] <= task.retries:
            rec["status"] = "pending"
            pending.insert(self._task_order.index(tid), tid)
            print("retrying: %s (attempt %d of %d)" % (tid, rec["attempts"] + 1, task.retries + 1))
            return
        rec["status"] = "failed"
        if outcome is None:
            print("failed: %s (timed out after %ss)" % (tid, task.timeout))
        else:
            print("failed: %s (outcome %s)" % (tid, outcome))

    # -- reporting --------------------------------------------------------------

    def results(self):
        """Return the results dict: task_id -> {status, session_id, outcome,
        attempts, last_text, started_at, finished_at, duration}."""
        return self._results

    def summary(self):
        """Return a human readable multi-line status summary."""
        lines = []
        for tid in self._task_order:
            rec = self._results[tid]
            lines.append(
                "%-24s %-10s attempts=%-2d outcome=%-8s time=%s"
                % (
                    tid,
                    rec["status"],
                    rec["attempts"],
                    rec["outcome"] if rec["outcome"] is not None else "-",
                    self._fmt_duration(rec["duration"]),
                )
            )
        counts = {}
        for tid in self._task_order:
            status = self._results[tid]["status"]
            counts[status] = counts.get(status, 0) + 1
        order = ("succeeded", "failed", "skipped", "running", "pending")
        parts = ", ".join("%d %s" % (counts[s], s) for s in order if counts.get(s))
        lines.append("totals: %d task(s) [%s]" % (len(self._task_order), parts or "none"))
        return "\n".join(lines)

    @staticmethod
    def _fmt_duration(seconds):
        if seconds is None:
            return "-"
        return "%.2fs" % seconds
