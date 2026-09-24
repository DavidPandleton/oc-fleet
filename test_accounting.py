"""Stats normalization tests."""

import unittest

from accounting import normalize_stats


class AccountingTest(unittest.TestCase):
    def test_flat_stats(self):
        result = normalize_stats({"input": 10, "output": 5})
        self.assertEqual(result["input"], 10)
        self.assertEqual(result["output"], 5)
        self.assertEqual(result["attribution"], "estimated")

    def test_nested_envelope_and_tokens(self):
        result = normalize_stats({"data": {"tokens": {"input": 7, "output": 3}}})
        self.assertEqual(result, {"input": 7, "output": 3, "attribution": "estimated"})

    def test_invalid_payload_is_empty(self):
        self.assertEqual(normalize_stats(None), {})
        self.assertEqual(normalize_stats({"tokens": "bad"}), {"attribution": "estimated"})


if __name__ == "__main__":
    unittest.main()
