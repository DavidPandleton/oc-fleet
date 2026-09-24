"""A long run must not be silent.

The coffee-catalog run stalled for 18 minutes with nothing on stdout
between "started: scaffold" and the final verdict. A foreman that
dispatches agents and then says nothing for a quarter of an hour
cannot be watched, cannot be trusted to still be alive, and cannot be
told apart from a hang.

A heartbeat closes that gap: every `heartbeat_interval` seconds of a
task still running, print one line with how long it has been going and
what the agent is currently doing (tool calls in flight, oldest age).
It is pure observability: it never changes the control flow, never
touches the result, and can be switched off (`heartbeat_interval=None`)
for quiet machine consumption.
"""

import io
import tempfile
import unittest
from contextlib import redirect_stdout

from fleet import Fleet
from orchestrator import Orchestrator, Task


class FakeFleet:
    """Fleet whose status advances one scripted state per poll."""

    def __init__(self, states):
        self.states = list(states)
        self.polled = 0

    def dispatch(self, prompt, workdir, title=None, model=None):
        return "s-1"

    def status(self, session_id):
        self.polled += 1
        if self.states:
            return self.states.pop(0)
        return {"outcome": "succeeded", "last_assistant_text": "done"}

    def cancel(self, session_id):
        return True


class FakeClock:
    """Deterministic monotonic clock so heartbeat timing is testable."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class HeartbeatTest(unittest.TestCase):
    def _run(self, states, heartbeat_interval, clock, poll_interval=1.0):
        fleet = FakeFleet(states)
        with tempfile.TemporaryDirectory() as workdir:
            orchestration = Orchestrator(
                fleet=fleet, poll_interval=poll_interval,
                heartbeat_interval=heartbeat_interval,
            )
            orchestration._clock = clock
            orchestration.add(Task(
                id="a", prompt="long task", workdir=workdir, timeout=100,
            ))
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                orchestration.run()
        return buffer.getvalue()

    def test_long_task_emits_heartbeat_lines(self):
        clock = FakeClock()
        # Task still running on the first polls, then succeeds. Each poll
        # advances the clock past the heartbeat interval.
        states = [
            {"outcome": None, "last_assistant_text": None,
             "tool_running": 1, "stuck_seconds": 10.0, "stuck": False},
            {"outcome": None, "last_assistant_text": None,
             "tool_running": 2, "stuck_seconds": 40.0, "stuck": False},
            {"outcome": "succeeded", "last_assistant_text": "done"},
        ]

        original_sleep = __import__("orchestrator").time.sleep

        def fake_sleep(seconds):
            clock.advance(max(seconds, 0.0))

        __import__("orchestrator").time.sleep = fake_sleep
        try:
            output = self._run(states, heartbeat_interval=1.0, clock=clock)
        finally:
            __import__("orchestrator").time.sleep = original_sleep

        self.assertIn("heartbeat", output)
        # The line should carry the task id and a running duration.
        self.assertIn("a", output)
        self.assertIn("tool", output)

    def test_no_heartbeat_when_interval_is_none(self):
        clock = FakeClock()
        states = [
            {"outcome": None, "last_assistant_text": None,
             "tool_running": 1, "stuck_seconds": 10.0, "stuck": False},
            {"outcome": "succeeded", "last_assistant_text": "done"},
        ]

        original_sleep = __import__("orchestrator").time.sleep

        def fake_sleep(seconds):
            clock.advance(max(seconds, 0.0))

        __import__("orchestrator").time.sleep = fake_sleep
        try:
            output = self._run(states, heartbeat_interval=None, clock=clock)
        finally:
            __import__("orchestrator").time.sleep = original_sleep

        self.assertNotIn("heartbeat", output)

    def test_short_task_does_not_spam_heartbeat(self):
        """A task that finishes before the interval never prints one."""
        clock = FakeClock()
        states = [
            {"outcome": "succeeded", "last_assistant_text": "done"},
        ]
        output = self._run(states, heartbeat_interval=30.0, clock=clock)
        self.assertNotIn("heartbeat", output)


if __name__ == "__main__":
    unittest.main()
