"""Normalize OpenCode stats into a conservative accounting record."""


def normalize_stats(payload):
    """Extract known numeric counters without assuming one server envelope."""
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return {}
    result = {}
    for key in ("input", "output", "reasoning", "cache", "total", "duration"):
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result[key] = value
    tokens = data.get("tokens")
    if isinstance(tokens, dict):
        for key in ("input", "output", "reasoning", "cache", "total"):
            value = tokens.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                result[key] = value
    result["attribution"] = "estimated"
    return result
