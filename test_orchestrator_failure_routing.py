"""Tests for failure-aware model routing."""

import os
import sys
import tempfile
import unittest

from orchestrator import Orchestrator, Task
from test_orchestrator import FakeFleet, fake_clock


class FailureRoutingTest(unittest.TestCase):
    def test_verification_failure_retries_same_model(self):
        with tempfile.TemporaryDirectory() as workdir:
            marker = os.path.join(workdir, "verification-count")
            script = os.path.join(workdir, "verify.py")
            with open(script, "w") as handle:
                handle.write(
                    "import pathlib, sys\n"
                    "p = pathlib.Path(%r)\n"
                    "n = int(p.read_text()) if p.exists() else 0\n"
                    "p.write_text(str(n + 1))\n"
                    "raise SystemExit(n != 1)\n" % marker
                )
            command = f"{sys.executable} {script}"
            fleet = FakeFleet()
            fleet.set_outcome("s-0", "succeeded")
            fleet.set_outcome("s-1", "succeeded")
            orchestration = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.01)
            orchestration.add(Task(
                id="a", prompt="build", workdir=workdir,
                model="cutad/model-a", fallbacks=["cutad/model-b"],
                retries=1, verify=[command],
            ))
            with fake_clock():
                result = orchestration.run()["a"]
            self.assertEqual(result["status"], "verification_passed")
            self.assertEqual(
                [item["model"] for item in fleet.dispatched],
                ["cutad/model-a", "cutad/model-a"],
            )


if __name__ == "__main__":
    unittest.main()
