"""Tests for persisted human approval records."""

import tempfile
import unittest

from store import RunStore


class ApprovalStoreTest(unittest.TestCase):
    def test_approval_is_idempotent_and_readable(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            store = RunStore(handle.name)
            store.record_approval("run-1", "build", {"approved": True, "by": "human"})
            store.record_approval("run-1", "build", {"approved": True, "by": "human-2"})
            self.assertEqual(
                store.get_approval("run-1", "build"),
                {"approved": True, "by": "human-2"},
            )

    def test_missing_approval_is_none(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            self.assertIsNone(RunStore(handle.name).get_approval("r", "t"))


if __name__ == "__main__":
    unittest.main()
