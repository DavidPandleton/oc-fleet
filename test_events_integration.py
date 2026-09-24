"""JSONL event sink integration test."""

import json
import tempfile
import unittest

from events import JsonlEventSink
from orchestrator import Orchestrator, Task
from test_orchestrator import FakeFleet, fake_clock


class EventSinkIntegrationTest(unittest.TestCase):
    def test_orchestrator_emits_lifecycle_events(self):
        with tempfile.NamedTemporaryFile() as handle:
            fleet = FakeFleet()
            fleet.set_outcome("s-0", "succeeded")
            orchestration = Orchestrator(
                fleet=fleet, max_parallel=1, poll_interval=0.01,
                run_id="run-1", event_sink=JsonlEventSink(handle.name),
            )
            orchestration.add(Task(id="a", prompt="build", workdir="/tmp"))
            with fake_clock():
                orchestration.run()
            handle.seek(0)
            events = [json.loads(line) for line in handle.read().decode().splitlines()]
        self.assertEqual([event["event_type"] for event in events], ["task_started", "task_finished"])
        self.assertTrue(all(event["run_id"] == "run-1" for event in events))


if __name__ == "__main__":
    unittest.main()
