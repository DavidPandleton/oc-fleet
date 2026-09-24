"""Failure taxonomy for auditable retry routing."""

import re


_PROVIDER_INVALID = re.compile(
    r"provider[._-](?:invalid[-_]request|error)|invalid[-_ ]request",
    re.IGNORECASE,
)
_RATE_LIMIT = re.compile(r"\b(?:429|rate[- ]limit|too many requests)\b", re.IGNORECASE)
_TRANSPORT = re.compile(
    r"(?:connection(?:error| reset)?|timeout|timed out|dns|http 5\d\d|refused)",
    re.IGNORECASE,
)


def classify_failure(
    *,
    reason=None,
    outcome=None,
    timed_out=False,
    stuck=False,
    verification_failed=False,
):
    """Classify one observed failure without inventing evidence."""
    if verification_failed:
        return "verification_failed"
    if stuck:
        return "tool_stuck"
    if timed_out:
        return "timeout"
    text = str(reason or "")
    if _PROVIDER_INVALID.search(text):
        return "provider_invalid_request"
    if _RATE_LIMIT.search(text):
        return "rate_limited"
    if _TRANSPORT.search(text):
        return "transport_error"
    if outcome in {"failed", "crashed", "error", "cancelled", "canceled"}:
        return "agent_failed"
    return "unknown"
