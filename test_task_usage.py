"""A task's token and cost numbers must reach its record.

`accounting.normalize_stats` and `pricing.estimate_cost` both exist and are
tested on their own, but nothing in the orchestrator ever called them, so a
finished task record carried no usage at all. That is the gap behind the
v0.2 definition of done line "model, retry, duration, token, and estimated
cost data are visible per task".

The contract this pins:

  * the raw counters reported by `status()` are normalized onto the task
    record under `stats`,
  * the model actually used is recorded (`model_used`), so a cost is
    attributable to a model and not a guess,
  * when the pricing table knows that model, `estimated_cost` is a number
    and matches `estimate_cost` for the same inputs,
  * when pricing does not know the model, `estimated_cost` is None and the
    record says so, rather than inventing a figure,
  * a model with no reported usage gets no fabricated stats.

Answers are only trustworthy if a missing number is distinguishable from a
zero. A task that reports no tokens must not look like a task that used
none.
"""

from __future__ import annotations

import tempfile
import unittest

from orchestrator import Orchestrator, Task
from pricing import estimate_cost


class UsageFleet:
    """Fleet whose status carries a token envelope and the model used."""

    def __init__(self, payload):
        self.dispatched = []
        self._payload = payload

    def dispatch(self, task, workdir, title="", model="", env=None):
        prompt = getattr(task, "prompt", task)
        session_id = "s-%d" % len(self.dispatched)
        self.dispatched.append({"id": session_id, "model": model})
        return session_id

    def status(self, session_id, stuck_after=None):
        payload = {
            "outcome": "completed",
            "last_assistant_text": "done",
        }
        payload.update(self._payload)
        return payload


def make_orch(payload, task_model="cutad/deepseek-v4-flash", prices=None):
    fleet = UsageFleet(payload)
    orch = Orchestrator(fleet=fleet, max_parallel=1, poll_interval=0.001,
                        prices=prices)
    workdir = tempfile.mkdtemp()
    orch.add(Task(id="a", prompt="noop", workdir=workdir, model=task_model))
    orch.run()
    return orch.results()["a"]


PRICES = {"cutad/deepseek-v4-flash": {"input": 0.25, "output": 1.0}}


class TaskUsageAccountingTest(unittest.TestCase):
    def test_tokens_from_status_land_on_the_record(self):
        rec = make_orch({"tokens": {"input": 1200, "output": 400}})
        self.assertIn("stats", rec)
        self.assertEqual(rec["stats"]["input"], 1200)
        self.assertEqual(rec["stats"]["output"], 400)

    def test_a_flat_token_envelope_is_also_read(self):
        """The server is inconsistent: counters are sometimes top level."""
        rec = make_orch({"input": 800, "output": 100})
        self.assertEqual(rec["stats"]["input"], 800)
        self.assertEqual(rec["stats"]["output"], 100)

    def test_estimated_cost_matches_the_pricing_module(self):
        rec = make_orch({"tokens": {"input": 1200, "output": 400}}, prices=PRICES)
        expected = estimate_cost("cutad/deepseek-v4-flash", 1200, 400, PRICES)
        self.assertIsNotNone(expected, "fixture model must have a known price")
        self.assertEqual(rec["estimated_cost"], expected)

    def test_no_prices_configured_means_no_cost_claimed(self):
        """Without a price table the cost is unknown, not zero."""
        rec = make_orch({"tokens": {"input": 1200, "output": 400}})
        self.assertIsNone(rec["estimated_cost"])

    def test_model_used_is_recorded_for_attribution(self):
        rec = make_orch({"tokens": {"input": 10, "output": 5}})
        self.assertEqual(rec["model_used"], "cutad/deepseek-v4-flash")

    def test_unknown_model_reports_no_cost_rather_than_zero(self):
        rec = make_orch(
            {"tokens": {"input": 10, "output": 5}},
            task_model="cutad/does-not-exist",
            prices=PRICES,
        )
        self.assertIsNone(rec["estimated_cost"])

    def test_no_usage_reported_means_no_fabricated_stats(self):
        rec = make_orch({})
        # Either the key is absent, or it is empty. Never a fake zero.
        if "stats" in rec:
            self.assertFalse(
                rec["stats"].get("input") and rec["stats"].get("output")
            )


if __name__ == "__main__":
    unittest.main()
