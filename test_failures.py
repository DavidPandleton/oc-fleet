"""Tests for failure classification used by retry routing."""

import unittest

from failures import classify_failure


class FailureClassificationTest(unittest.TestCase):
    def test_provider_invalid_request(self):
        self.assertEqual(
            classify_failure(reason="provider.invalid-request HTTP 404"),
            "provider_invalid_request",
        )

    def test_rate_limit(self):
        self.assertEqual(
            classify_failure(reason="HTTP 429 rate limit exceeded"),
            "rate_limited",
        )

    def test_timeout_and_stuck(self):
        self.assertEqual(classify_failure(timed_out=True), "timeout")
        self.assertEqual(classify_failure(stuck=True), "tool_stuck")

    def test_verification_failure(self):
        self.assertEqual(
            classify_failure(verification_failed=True),
            "verification_failed",
        )

    def test_transport_and_agent_failure(self):
        self.assertEqual(
            classify_failure(reason="ConnectionError: refused"),
            "transport_error",
        )
        self.assertEqual(classify_failure(outcome="failed"), "agent_failed")

    def test_unknown_keeps_bounded_reason(self):
        self.assertEqual(classify_failure(reason="something odd"), "unknown")
        self.assertEqual(classify_failure(), "unknown")


if __name__ == "__main__":
    unittest.main()
