"""Tiny integer helpers used by the oc-fleet benchmark fixture repo."""


def add(a, b):
    return a + b


def clamp(value, low, high):
    if low > high:
        raise ValueError("low must not exceed high")
    return max(low, min(high, value))
