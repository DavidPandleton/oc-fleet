"""A retry must tell the agent what went wrong last time.

Today a retry re-sends the identical prompt. If attempt 1 failed because
the agent misread a constraint, attempt 2 repeats the mistake with no way
to know it is repeating it. The foreman knows the failure (it classified
it, it has the agent's last words and the verification stderr) and should
pass that knowledge forward.

The contract:

  * attempt 1 is byte-for-byte the original prompt (no noise),
  * attempt N>1 carries a bounded "previous attempt failed" block with
    the failure class, the model that failed, and the agent's own last
    text,
  * the block is bounded so a runaway transcript cannot drown the task,
  * a task that never fails to begin with is unaffected.
"""

import unittest

from orchestrator import Orchestrator, Task


class RecordingFleet:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.prompts = []
        self.models = []
        self.n = 0

    def dispatch(self, prompt, workdir, title="", model=""):
        self.n += 1
        self.prompts.append(prompt)
        self.models.append(model)
        return "s%d" % self.n

    def status(self, session_id, stuck_after=None):
        idx = int(session_id[1:]) - 1
        value = self.outcomes[idx] if idx < len(self.outcomes) else "succeeded"
        if value is None:
            return {"outcome": None, "last_assistant_text": None}
        if isinstance(value, dict):
            return value
        return {"outcome": value, "last_assistant_text": "text %s" % session_id}


def make_orch(fleet, tasks, **kw):
    import tempfile
    kw.setdefault("poll_interval", 0.001)
    workdir = kw.pop("workdir", None) or tempfile.mkdtemp()
    o = Orchestrator(fleet=fleet, **kw)
    for t in tasks:
        if not t.workdir or t.workdir == ".":
            t.workdir = workdir
        o.add(t)
    return o


class RetryContextTest(unittest.TestCase):
    def test_first_attempt_is_the_bare_prompt(self):
        fleet = RecordingFleet(["succeeded"])
        o = make_orch(fleet, [Task(id="a", prompt="do the thing")])
        o.run()
        self.assertEqual(fleet.prompts[0], "do the thing")

    def test_retry_carries_the_failure_reason(self):
        fleet = RecordingFleet([
            {"outcome": "failed", "last_assistant_text": "I could not parse the input"},
            "succeeded",
        ])
        o = make_orch(fleet, [Task(id="a", prompt="do the thing", retries=1)])
        o.run()

        self.assertEqual(len(fleet.prompts), 2)
        retry = fleet.prompts[1]
        # The original instruction must survive.
        self.assertIn("do the thing", retry)
        # ...and must be joined by what actually happened.
        self.assertIn("previous attempt", retry.lower())
        self.assertIn("I could not parse the input", retry)

    def test_retry_says_which_model_failed(self):
        fleet = RecordingFleet([
            {"outcome": "failed", "last_assistant_text": "boom"},
            "succeeded",
        ])
        o = make_orch(fleet, [
            Task(id="a", prompt="p", retries=1, model="provider/one",
                 fallbacks=["provider/two"]),
        ])
        o.run()
        self.assertIn("provider/one", fleet.prompts[1])
        # The retry must actually run on the fallback, not the failed model.
        self.assertEqual(fleet.models[1], "provider/two")

    def test_retry_block_is_bounded(self):
        huge = "x" * 100000
        fleet = RecordingFleet([
            {"outcome": "failed", "last_assistant_text": huge},
            "succeeded",
        ])
        o = make_orch(fleet, [Task(id="a", prompt="p", retries=1)])
        o.run()
        # The whole prompt is clamped, so a giant transcript cannot bloat it.
        self.assertLess(len(fleet.prompts[1]), 20000)

    def test_a_clean_run_never_mentions_previous_attempts(self):
        fleet = RecordingFleet(["succeeded"])
        o = make_orch(fleet, [Task(id="a", prompt="p")])
        o.run()
        self.assertNotIn("previous attempt", fleet.prompts[0].lower())

    def test_verification_failure_is_reported_to_the_retry(self):
        """A failed verify command is the most actionable thing to pass on."""
        import tempfile

        workdir = tempfile.mkdtemp()
        fleet = RecordingFleet(["succeeded", "succeeded"])
        o = Orchestrator(fleet=fleet, poll_interval=0.001)
        o.add(Task(
            id="a", prompt="write the file", workdir=workdir, retries=1,
            verify=["sh -c 'echo missing-artifact >&2; exit 1'"],
        ))
        o.run()

        self.assertEqual(len(fleet.prompts), 2)
        retry = fleet.prompts[1]
        self.assertIn("missing-artifact", retry)


if __name__ == "__main__":
    unittest.main()
