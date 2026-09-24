"""Ownership overlap is a preflight error, not a runtime surprise.

Two agents in the same repo only stay in harmony if their lanes do not
overlap. If task A owns ``src/**`` and task B owns ``src/db.py``, then
whoever writes ``src/db.py`` might be blamed by both, and the boundary
check that is supposed to prevent collisions instead creates them.

The foreman should refuse the plan up front - before any agent is
dispatched - rather than discover the collision after the fact. These
tests pin that behaviour on ``validate()`` and ``plan()``.
"""

import tempfile
import unittest

from orchestrator import Orchestrator, Task
from test_orchestrator import FakeFleet


class OwnershipOverlapTest(unittest.TestCase):
    def _orchestrator(self, *tasks):
        orchestration = Orchestrator(fleet=FakeFleet(), poll_interval=0.01)
        for task in tasks:
            orchestration.add(task)
        return orchestration

    def test_identical_ownership_is_rejected(self):
        with tempfile.TemporaryDirectory() as workdir:
            orchestration = self._orchestrator(
                Task(id="a", prompt="a", workdir=workdir, owns=["src/**"]),
                Task(id="b", prompt="b", workdir=workdir, owns=["src/**"]),
            )
            with self.assertRaises(ValueError) as caught:
                orchestration.validate()
            self.assertIn("overlap", str(caught.exception).lower())
            self.assertIn("a", str(caught.exception))
            self.assertIn("b", str(caught.exception))

    def test_nested_ownership_is_rejected(self):
        """`src/**` swallows `src/db.py` - that is a collision waiting."""
        with tempfile.TemporaryDirectory() as workdir:
            orchestration = self._orchestrator(
                Task(id="wide", prompt="w", workdir=workdir, owns=["src/**"]),
                Task(id="narrow", prompt="n", workdir=workdir, owns=["src/db.py"]),
            )
            with self.assertRaises(ValueError):
                orchestration.validate()

    def test_disjoint_lanes_are_accepted(self):
        with tempfile.TemporaryDirectory() as workdir:
            orchestration = self._orchestrator(
                Task(id="backend", prompt="b", workdir=workdir, owns=["backend/**"]),
                Task(id="frontend", prompt="f", workdir=workdir, owns=["frontend/**"]),
            )
            plan = orchestration.plan()  # must not raise
            self.assertEqual(plan["tasks"]["backend"]["owns"], ["backend/**"])

    def test_tasks_without_ownership_do_not_collide(self):
        """No `owns` means no claim, so two open-ended tasks are allowed."""
        with tempfile.TemporaryDirectory() as workdir:
            orchestration = self._orchestrator(
                Task(id="a", prompt="a", workdir=workdir),
                Task(id="b", prompt="b", workdir=workdir),
            )
            orchestration.validate()  # must not raise

    def test_overlap_in_different_workdirs_is_allowed(self):
        """Different directories are different territories, even with the
        same pattern."""
        with tempfile.TemporaryDirectory() as first:
            with tempfile.TemporaryDirectory() as second:
                orchestration = self._orchestrator(
                    Task(id="a", prompt="a", workdir=first, owns=["src/**"]),
                    Task(id="b", prompt="b", workdir=second, owns=["src/**"]),
                )
                orchestration.validate()  # must not raise

    def test_plan_refuses_overlap_too(self):
        """`plan()` is the preflight contract, so it refuses as well."""
        orchestration = Orchestrator(fleet=FakeFleet())
        orchestration.add(Task(id="a", prompt="a", workdir="/tmp", owns=["src/**"]))
        orchestration.add(Task(id="b", prompt="b", workdir="/tmp", owns=["src/**"]))
        with self.assertRaises(ValueError):
            orchestration.plan()


if __name__ == "__main__":
    unittest.main()
