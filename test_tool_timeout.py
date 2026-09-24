"""The stuck threshold must be configurable, not baked in.

`STUCK_AFTER_SECONDS = 300` is a reasonable default, but it is a guess
about how long a tool may legitimately run. A task that runs `npm
install` on a cold cache or a full test suite can honestly spend ten
minutes inside one tool; a flaky agent can hang in a trivial one. The
foreman should get to say which is which per run, rather than being
locked to one global number.

`Fleet.status(..., stuck_after=N)` lets the caller set the threshold for
that poll. `Orchestrator(tool_timeout=N)` threads it through every poll
of the run, so a whole run uses one consistent policy.
"""

import unittest

from fleet import Fleet


class ToolActivityThresholdTest(unittest.TestCase):
    def _messages(self, created_ms):
        return [{
            "type": "assistant",
            "content": [{
                "type": "tool",
                "state": {"status": "running"},
                "time": {"created": created_ms},
            }],
        }]

    def test_default_threshold_is_used_when_none_given(self):
        now_ms = Fleet.STUCK_AFTER_SECONDS * 1000 + 100000
        msgs = self._messages(0)  # a tool of age now_ms
        activity = Fleet._tool_activity(msgs, now_ms=now_ms)
        self.assertTrue(activity["stuck"])

    def test_custom_threshold_marks_a_younger_tool_stuck(self):
        """A 40s tool is not stuck by default (300s) but is at 30s."""
        now_ms = 40_000.0
        msgs = self._messages(0)
        default = Fleet._tool_activity(msgs, now_ms=now_ms)
        self.assertFalse(default["stuck"])
        custom = Fleet._tool_activity(msgs, now_ms=now_ms, stuck_after=30.0)
        self.assertTrue(custom["stuck"])

    def test_custom_threshold_can_be_more_patient(self):
        """A 700s tool is stuck by default, not at a 3600s threshold."""
        now_ms = 700_000.0
        msgs = self._messages(0)
        default = Fleet._tool_activity(msgs, now_ms=now_ms)
        self.assertTrue(default["stuck"])
        patient = Fleet._tool_activity(msgs, now_ms=now_ms, stuck_after=3600.0)
        self.assertFalse(patient["stuck"])

    def test_stuck_seconds_is_still_reported_regardless(self):
        now_ms = 40_000.0
        activity = Fleet._tool_activity(self._messages(0), now_ms=now_ms)
        self.assertAlmostEqual(activity["stuck_seconds"], 40.0, places=1)


class OrchestratorToolTimeoutTest(unittest.TestCase):
    def test_orchestrator_threads_tool_timeout_into_status(self):
        """Orchestrator must pass its tool_timeout on each status call."""
        from orchestrator import Orchestrator

        seen = []

        class Fleet:
            def dispatch(self, prompt, workdir, title=None, model=None):
                return "s-1"

            def status(self, session_id, stuck_after=None):
                seen.append(stuck_after)
                return {"outcome": "succeeded", "last_assistant_text": "done"}

            def cancel(self, session_id):
                return True

        import tempfile
        with tempfile.TemporaryDirectory() as workdir:
            orchestration = Orchestrator(fleet=Fleet(), tool_timeout=30.0)
            orchestration.add(
                __import__("orchestrator").Task(id="a", prompt="p", workdir=workdir)
            )
            orchestration.run()

        self.assertTrue(seen, "status was never polled")
        self.assertTrue(
            all(v == 30.0 for v in seen),
            "expected tool_timeout=30.0 on every poll, got %r" % (seen,),
        )


if __name__ == "__main__":
    unittest.main()
