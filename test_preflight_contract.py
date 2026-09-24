"""Contracts the foreman can check before spending a single token.

`validate()` already refuses an empty prompt, a missing dependency, a
cycle, and overlapping ownership. Those are the cheap structural checks.
The expensive class of mistake is a task that looks fine on paper and
only fails once a session is running: a workdir that does not exist, a
retry budget or timeout that cannot mean anything, a fallback chain that
repeats a model already known to fail, or an ownership pattern that can
escape the workdir it is supposed to fence.

Every one of these is knowable before dispatch. Catching them there costs
a line of output; catching them mid-run costs a slot, a session, and the
confusion of a half-written shared tree.
"""

import unittest

from orchestrator import Orchestrator, Task


class NullFleet:
    """A fleet that never dispatches, so a preflight failure is the point."""

    def dispatch(self, *a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("dispatch must not happen for an invalid task")

    def status(self, sid, stuck_after=None):  # pragma: no cover
        return {"outcome": "succeeded"}


def orch(tasks):
    o = Orchestrator(fleet=NullFleet(), poll_interval=0.001)
    for t in tasks:
        o.add(t)
    return o


class PreflightContractTest(unittest.TestCase):
    def _fail(self, task):
        with self.assertRaises(ValueError) as ctx:
            orch([task]).validate()
        return str(ctx.exception)

    # --- validate() checks the plan, not the machine --------------------

    def test_validate_does_not_touch_the_filesystem(self):
        """A plan must be reviewable on a machine that has no workdir.

        The foreman is used to plan a run and print it for a human, often
        long before or far away from the machine that will execute it. If
        validate() stat()ed the workdir, every such plan would fail for a
        reason that has nothing to do with the task's contract.
        """
        o = orch([Task(
            id="a", prompt="p", workdir="/does/not/exist/anywhere",
            owns=["src/**"],
        )])
        o.validate()  # must not raise

    # --- numbers that cannot mean anything -----------------------------

    def test_negative_retries_is_refused(self):
        msg = self._fail(Task(id="a", prompt="p", workdir="/repo", retries=-1))
        self.assertIn("retries", msg.lower())

    def test_zero_or_negative_timeout_is_refused(self):
        msg = self._fail(Task(id="a", prompt="p", workdir="/repo", timeout=0))
        self.assertIn("timeout", msg.lower())

    def test_negative_verify_timeout_is_refused(self):
        msg = self._fail(Task(
            id="a", prompt="p", workdir="/repo",
            verify=["true"], verify_timeout=-5,
        ))
        self.assertIn("verify_timeout", msg.lower())

    # --- a fallback that cannot possibly help --------------------------

    def test_duplicate_fallback_is_refused(self):
        msg = self._fail(Task(
            id="a", prompt="p", workdir="/repo",
            model="provider/one", fallbacks=["provider/one"],
        ))
        self.assertIn("fallback", msg.lower())

    def test_fallback_repeating_the_primary_model_is_refused(self):
        msg = self._fail(Task(
            id="a", prompt="p", workdir="/repo",
            model="provider/one", fallbacks=["provider/one", "provider/two"],
        ))
        self.assertIn("fallback", msg.lower())

    def test_distinct_fallbacks_are_fine(self):
        o = orch([Task(
            id="a", prompt="p", workdir="/repo",
            model="provider/one", fallbacks=["provider/two", "provider/three"],
        )])
        o.validate()  # must not raise

    # --- ownership that can escape the workdir -------------------------

    def test_absolute_ownership_pattern_is_refused(self):
        msg = self._fail(Task(
            id="a", prompt="p", workdir="/repo", owns=["/etc/passwd"],
        ))
        self.assertIn("owns", msg.lower())

    def test_parent_escaping_ownership_pattern_is_refused(self):
        msg = self._fail(Task(
            id="a", prompt="p", workdir="/repo", owns=["../other/**"],
        ))
        self.assertIn("owns", msg.lower())

    def test_normal_ownership_pattern_is_fine(self):
        o = orch([Task(
            id="a", prompt="p", workdir="/repo", owns=["src/**", "tests/**"],
        )])
        o.validate()  # must not raise

    # --- verify commands must be runnable ------------------------------

    def test_blank_verify_command_is_refused(self):
        msg = self._fail(Task(
            id="a", prompt="p", workdir="/repo", verify=["   "],
        ))
        self.assertIn("verify", msg.lower())

    def test_non_string_verify_command_is_refused(self):
        msg = self._fail(Task(
            id="a", prompt="p", workdir="/repo", verify=[123],
        ))
        self.assertIn("verify", msg.lower())


if __name__ == "__main__":
    unittest.main()
