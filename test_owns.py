"""Task ownership boundaries: keep parallel agents out of each other's lanes.

Two agents in the same repo only work in harmony if each one is confined to
its own territory. A prompt can ask nicely; it cannot guarantee anything.
`owns` turns the request into a check the fleet performs on the files an agent
actually changed, before anything is merged.
"""

import os
import subprocess
import sys
import tempfile
import unittest

from orchestrator import Orchestrator, Task
from test_orchestrator import FakeFleet, fake_clock


def init_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit",
         "--allow-empty", "-q", "-m", "init"],
        cwd=path, check=True,
    )


def commit_all(path):
    """Baseline the workdir so only agent-made changes show up as dirty."""
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit",
         "-q", "-m", "baseline", "--allow-empty"],
        cwd=path, check=True,
    )


class OwnershipTest(unittest.TestCase):
    def _run(self, workdir, task, outcome="succeeded"):
        fleet = FakeFleet()
        fleet.set_outcome("s-0", outcome)
        orchestration = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
        orchestration.add(task)
        orchestration.run()
        return orchestration.results()["a"]

    def _write(self, workdir, relpath):
        path = os.path.join(workdir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write("x\n")

    def _agent_write(self, relpath, content="x\n"):
        """A setup hook standing in for an agent that writes a file.

        It runs AFTER the baseline is taken, so the file counts as this
        agent's work - exactly like a real agent editing the workdir.
        """
        script = tempfile.mkstemp(suffix=".py")[1]
        with open(script, "w") as handle:
            handle.write(
                "import os\n"
                "os.makedirs(os.path.dirname(%r) or '.', exist_ok=True)\n"
                "open(%r, 'w').write(%r)\n"
                % (relpath, relpath, content)
            )
        self.addCleanup(os.remove, script)
        return "%s %s" % (sys.executable, script)

    def test_no_owns_means_no_restriction(self):
        """Backward compatibility: a task without `owns` is unrestricted."""
        with tempfile.TemporaryDirectory() as workdir:
            init_repo(workdir)
            self._write(workdir, "anywhere/at/all.txt")
            result = self._run(workdir, Task(
                id="a", prompt="build", workdir=workdir,
            ))
            self.assertEqual(result["status"], "succeeded")

    def test_files_inside_owned_paths_pass(self):
        with tempfile.TemporaryDirectory() as workdir:
            init_repo(workdir)
            self._write(workdir, "backend/app.py")
            result = self._run(workdir, Task(
                id="a", prompt="build backend", workdir=workdir,
                owns=["backend/**"],
            ))
            self.assertEqual(result["status"], "succeeded")

    def test_file_outside_owned_paths_fails(self):
        with tempfile.TemporaryDirectory() as workdir:
            init_repo(workdir)
            self._write(workdir, "backend/app.py")
            result = self._run(workdir, Task(
                id="a", prompt="build backend", workdir=workdir,
                owns=["backend/**"],
                setup=[self._agent_write("frontend/main.ts")],
            ))
            self.assertEqual(result["status"], "verification_failed")
            self.assertEqual(result["boundary"]["verdict"], "boundary_violation")

    def test_violation_evidence_names_the_offending_file(self):
        with tempfile.TemporaryDirectory() as workdir:
            init_repo(workdir)
            self._write(workdir, "backend/app.py")
            result = self._run(workdir, Task(
                id="a", prompt="build backend", workdir=workdir,
                owns=["backend/**"],
                setup=[self._agent_write("frontend/main.ts")],
            ))
            self.assertIn("frontend/main.ts", result["boundary"]["violations"])
            self.assertNotIn("backend/app.py", result["boundary"]["violations"])

    def test_boundary_violation_recorded_even_when_agent_failed(self):
        """A failed agent that overstepped still reports the overstep.

        The status becomes `verification_failed`: a broken lane is a
        broken artifact contract, and burying it under the agent's own
        failure would hide the more actionable problem. The agent's own
        verdict is preserved separately in `agent_status`, so neither
        fact is lost.
        """
        with tempfile.TemporaryDirectory() as workdir:
            init_repo(workdir)
            result = self._run(workdir, Task(
                id="a", prompt="build backend", workdir=workdir,
                owns=["backend/**"],
                setup=[self._agent_write("frontend/main.ts")],
            ), outcome="failed")
            self.assertEqual(result["status"], "verification_failed")
            self.assertEqual(result["agent_status"], "failed")
            self.assertEqual(result["boundary"]["verdict"], "boundary_violation")

    def test_two_agents_in_disjoint_lanes_both_pass(self):
        """Two agents, two lanes: each confined to its own paths.

        They are checked sequentially against a baselined workdir, so each
        one's changes are attributed to the agent that made them. True
        simultaneous parallel work needs `isolate=True` (separate worktrees);
        `owns` is what keeps each lane honest once the work comes back.
        """
        with tempfile.TemporaryDirectory() as workdir:
            init_repo(workdir)
            self._write(workdir, "backend/app.py")
            self._write(workdir, "frontend/main.ts")
            commit_all(workdir)
            backend = Task(
                id="backend", prompt="backend", workdir=workdir,
                owns=["backend/**"],
                verify=[self._touch(workdir, "backend/app.py")],
            )
            frontend = Task(
                id="frontend", prompt="frontend", workdir=workdir,
                owns=["frontend/**"],
                depends_on=["backend"],
                verify=[self._touch(workdir, "frontend/main.ts")],
            )
            fleet = FakeFleet()
            fleet.set_outcome("s-0", "succeeded")
            fleet.set_outcome("s-1", "succeeded")
            orchestration = Orchestrator(fleet=fleet, max_parallel=2, poll_interval=0.01)
            orchestration.add(backend)
            orchestration.add(frontend)
            orchestration.run()
            results = orchestration.results()
            self.assertIn(results["backend"]["status"], ("succeeded", "verification_passed"))
            self.assertIn(results["frontend"]["status"], ("succeeded", "verification_passed"))
            self.assertIsNone(results["backend"]["boundary"]["verdict"])
            self.assertIsNone(results["frontend"]["boundary"]["verdict"])

    def _touch(self, workdir, relpath):
        """A verification script that marks one owned file as modified.

        The script lives OUTSIDE the workdir on purpose: a helper dropped in
        the workdir would itself show up as an unowned change and trip the
        boundary, which says nothing about the agent.
        """
        script = tempfile.mkstemp(suffix=".py")[1]
        with open(script, "w") as handle:
            handle.write(
                "open(%r, 'a').write('changed\\n')\n"
                % os.path.join(workdir, relpath)
            )
        self.addCleanup(os.remove, script)
        return "%s %s" % (sys.executable, script)

    def test_plan_reports_owns(self):
        fleet = FakeFleet()
        orchestration = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
        orchestration.add(Task(
            id="a", prompt="build", workdir=".", owns=["backend/**"],
        ))
        plan = orchestration.plan()
        self.assertEqual(plan["tasks"]["a"]["owns"], ["backend/**"])


if __name__ == "__main__":
    unittest.main()
