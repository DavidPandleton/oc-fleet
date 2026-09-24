"""Tests for append-only structured event logging and pricing."""

import json
import tempfile
import unittest

from events import JsonlEventSink
from pricing import estimate_cost


class EventsAndPricingTest(unittest.TestCase):
    def test_jsonl_sink_writes_one_structured_event_per_line(self):
        with tempfile.NamedTemporaryFile() as handle:
            sink = JsonlEventSink(handle.name)
            sink.emit({"event_type": "task_started", "task_id": "a"})
            sink.emit({"event_type": "task_finished", "task_id": "a"})
            handle.seek(0)
            lines = handle.read().decode().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["event_type"], "task_started")
        self.assertEqual(json.loads(lines[1])["task_id"], "a")

    def test_pricing_unknown_model_is_unknown_not_zero(self):
        self.assertIsNone(estimate_cost("cutad/unknown", 1000, 500))

    def test_pricing_known_model_returns_estimate(self):
        self.assertEqual(estimate_cost("test/model", 1000, 500, {"test/model": {"input": 2, "output": 4}}), 0.004)


if __name__ == "__main__":
    unittest.main()
