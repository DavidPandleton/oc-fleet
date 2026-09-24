"""Tests for explicit orchestration planning before dispatch."""

import tempfile
import unittest

from orchestrator import Orchestrator, Task
from test_orchestrator import FakeFleet


class OrchestratorPlanTest(unittest.TestCase):
    def test_plan_maps_tasks_waves_and_execution_contract(self):
        fleet = FakeFleet()
        orchestration = Orchestrator(fleet=fleet, max_parallel=2)
        orchestration.add(Task(
            id="backend", prompt="backend", workdir="/tmp",
            model="model-a", fallbacks=["model-b"], retries=1,
            timeout=60, verify=["python3 -m pytest"], setup=["python3 setup.py"],
        ))
        orchestration.add(Task(
            id="frontend", prompt="frontend", workdir="/tmp/frontend",
            depends_on=["backend"], verify=["npm run build"],
        ))
        plan = orchestration.plan()
        self.assertEqual(plan["order"], ["backend", "frontend"])
        self.assertEqual(plan["waves"], [["backend"], ["frontend"]])
        self.assertEqual(plan["tasks"]["backend"]["fallbacks"], ["model-b"])
        self.assertEqual(plan["tasks"]["backend"]["verification"], ["python3 -m pytest"])
        self.assertEqual(plan["tasks"]["frontend"]["depends_on"], ["backend"])
        self.assertEqual(plan["risks"], [])

    def test_plan_does_not_dispatch_or_persist(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            from store import RunStore
            store = RunStore(handle.name)
            fleet = FakeFleet()
            orchestration = Orchestrator(fleet=fleet, store=store, run_id="r")
            orchestration.add(Task(id="a", prompt="a", workdir="/tmp"))
            plan = orchestration.plan()
            self.assertEqual(plan["order"], ["a"])
            self.assertEqual(fleet.dispatched, [])
            self.assertIsNone(store.get_run("r"))

    def test_run_preflights_before_dispatch(self):
        fleet = FakeFleet()
        orchestration = Orchestrator(fleet=fleet)
        orchestration.add(Task(id="a", prompt="a", workdir="/tmp"))
        fleet.set_outcome("s-0", "succeeded")
        original = orchestration.plan
        calls = []

        def tracked_plan():
            calls.append(True)
            return original()

        orchestration.plan = tracked_plan
        # No need to complete the session; the assertion is about ordering.
        orchestration.run()
        self.assertEqual(calls, [True])
        self.assertEqual(fleet.dispatched[0]["id"], "s-0")

    def test_plan_rejects_cycle_before_execution(self):
        orchestration = Orchestrator(fleet=FakeFleet())
        first = Task(id="a", prompt="a", workdir="/tmp")
        second = Task(id="b", prompt="b", workdir="/tmp", depends_on=["a"])
        orchestration.add(first)
        orchestration.add(second)
        orchestration._tasks["a"].depends_on.append("b")
        with self.assertRaises(ValueError):
            orchestration.plan()


if __name__ == "__main__":
    unittest.main()
