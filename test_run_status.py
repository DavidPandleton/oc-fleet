"""A run must be able to tell its caller it failed.

The orchestrator already knows, per task, whether the agent failed and
whether verification failed. What it could not do was summarise that
into a single integer a CI step or a shell script can act on. A run
that ends with a failed agent and still returns 0 is the worst kind of
bug: the caller believes work succeeded.

These tests pin the exit-code contract:

- 0: every task succeeded and every required verification passed
- 1: an agent failed or timed out, but no verification failed
- 2: a verification failed (including a boundary violation)
- 3: preflight refused the run (raised before any dispatch)

Precedence matters: a run that both had an agent failure and a
verification failure reports the more severe condition (2 over 1).
"""

import tempfile
import unittest

from orchestrator import Orchestrator, Task
from test_orchestrator import FakeFleet, fake_clock


class RunStatusTest(unittest.TestCase):
    def _script(self, workdir, name, exit_code):
        import os
        import sys
        path = os.path.join(workdir, name)
        with open(path, "w") as handle:
            handle.write("import sys; sys.exit(%d)\n" % exit_code)
        return "%s %s" % (sys.executable, path)

    def test_clean_run_is_zero(self):
        fleet = FakeFleet()
        orchestration = Orchestrator(fleet=fleet, poll_interval=0.01)
        orchestration.add(Task(id="a", prompt="a", workdir="/tmp"))
        fleet.set_outcome("s-0", "succeeded")
        with fake_clock():
            orchestration.run()
        self.assertEqual(orchestration.run_status(), 0)

    def test_agent_failure_is_one(self):
        fleet = FakeFleet()
        orchestration = Orchestrator(fleet=fleet, poll_interval=0.01)
        orchestration.add(Task(id="a", prompt="a", workdir="/tmp"))
        fleet.set_outcome("s-0", "failed")
        with fake_clock():
            orchestration.run()
        self.assertEqual(orchestration.run_status(), 1)

    def test_verification_failure_is_two(self):
        with tempfile.TemporaryDirectory() as workdir:
            failing = self._script(workdir, "check_fail.py", 1)
            fleet = FakeFleet()
            orchestration = Orchestrator(fleet=fleet, poll_interval=0.01)
            orchestration.add(Task(
                id="a", prompt="a", workdir=workdir, verify=[failing],
            ))
            fleet.set_outcome("s-0", "succeeded")
            with fake_clock():
                orchestration.run()
            self.assertEqual(orchestration.run_status(), 2)

    def test_verification_outranks_agent_failure(self):
        """Both failed; the more severe code (2) wins over (1)."""
        with tempfile.TemporaryDirectory() as workdir:
            failing = self._script(workdir, "check_fail.py", 1)
            fleet = FakeFleet()
            orchestration = Orchestrator(fleet=fleet, poll_interval=0.01)
            orchestration.add(Task(
                id="a", prompt="a", workdir=workdir, verify=[failing],
            ))
            fleet.set_outcome("s-0", "failed")
            with fake_clock():
                orchestration.run()
            self.assertEqual(orchestration.run_status(), 2)

    def test_preflight_failure_is_three(self):
        fleet = FakeFleet()
        orchestration = Orchestrator(fleet=fleet, poll_interval=0.01)
        orchestration.add(Task(id="a", prompt="a", workdir="/tmp", owns=["src/**"]))
        orchestration.add(Task(id="b", prompt="b", workdir="/tmp", owns=["src/**"]))
        with self.assertRaises(ValueError):
            with fake_clock():
                orchestration.run()
        self.assertEqual(orchestration.run_status(), 3)
        self.assertEqual(fleet.dispatched, [])

    def test_run_before_any_dispatch_is_pending_not_success(self):
        """Calling run_status() before run() must not claim success."""
        fleet = FakeFleet()
        orchestration = Orchestrator(fleet=fleet)
        orchestration.add(Task(id="a", prompt="a", workdir="/tmp"))
        self.assertEqual(orchestration.run_status(), 1)


if __name__ == "__main__":
    unittest.main()
