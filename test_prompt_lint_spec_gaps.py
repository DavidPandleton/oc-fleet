"""Tests for the four spec-gap rules added after the delegation experiment.

The experiment (2026-09-21, see ~/kb/projects/LAPORAN_FINAL.md) showed the
agent's failure mode is not incompetence but gap-filling: a prompt that leaves a
hole gets a plausible invented rule inside that hole. T1 failed because the spec
listed valid inputs and never said what "1h1h" should do, so the agent invented
"units must be strictly descending" and rejected a legal input.

These rules exist to catch that class of hole before the prompt is dispatched.
They are behavioural assertions, not just regex smoke tests: each one is checked
against a prompt that should trigger it and one that should not.
"""
from __future__ import annotations

import unittest

from prompt_lint import lint


def rule_ids(text):
    return {finding.rule_id for finding in lint(text)}


class UndefinedEdgeTest(unittest.TestCase):
    def test_etc_triggers(self):
        self.assertIn(
            "SPEC-UNDEFINED-EDGE",
            rule_ids("Parse durations like 1h30m, 45s, etc."),
        )

    def test_and_so_on_triggers(self):
        self.assertIn(
            "SPEC-UNDEFINED-EDGE",
            rule_ids("Handle integer, float, and so on."),
        )

    def test_other_cases_triggers(self):
        self.assertIn(
            "SPEC-UNDEFINED-EDGE",
            rule_ids("Handle '1h30m' and other cases."),
        )

    def test_fully_enumerated_spec_does_not_trigger(self):
        self.assertNotIn(
            "SPEC-UNDEFINED-EDGE",
            rule_ids(
                "Accept 1h30m and 45s. Reject everything else with ValueError, "
                "including '1h1h' and '1.5h'."
            ),
        )


class VerifyOnlyValidTest(unittest.TestCase):
    def test_verify_all_examples_triggers(self):
        self.assertIn(
            "SPEC-TEST-ONLY-VALID",
            rule_ids("Verify all the examples above return the right value."),
        )

    def test_the_exact_t1_wording_triggers(self):
        """This is the instruction whose verification passed on wrong output."""
        self.assertIn(
            "SPEC-TEST-ONLY-VALID",
            rule_ids("Run your own check that all the valid cases above return the right number."),
        )

    def test_check_the_examples_triggers(self):
        self.assertIn(
            "SPEC-TEST-ONLY-VALID",
            rule_ids("Check the examples in the docstring pass."),
        )

    def test_make_sure_examples_pass_triggers(self):
        self.assertIn(
            "SPEC-TEST-ONLY-VALID",
            rule_ids("Make sure the examples pass before you finish."),
        )

    def test_asking_about_invalid_cases_does_not_trigger(self):
        self.assertNotIn(
            "SPEC-TEST-ONLY-VALID",
            rule_ids("Confirm each invalid input raises ValueError, and each valid one returns the sum."),
        )


class InvalidContractTest(unittest.TestCase):
    def test_implementation_without_error_contract_triggers(self):
        self.assertIn(
            "SPEC-NO-INVALID-CONTRACT",
            rule_ids("Write a function that parses duration strings into seconds."),
        )

    def test_implementation_with_error_contract_does_not_trigger(self):
        self.assertNotIn(
            "SPEC-NO-INVALID-CONTRACT",
            rule_ids("Write a function that parses durations. Raise ValueError on invalid input."),
        )

    def test_non_implementation_prompt_does_not_trigger(self):
        self.assertNotIn(
            "SPEC-NO-INVALID-CONTRACT",
            rule_ids("Summarise this article in five bullets."),
        )


class SelfReferentialVerifyTest(unittest.TestCase):
    def test_run_your_own_triggers(self):
        self.assertIn(
            "SPEC-VERIFY-SELF-REFERENTIAL",
            rule_ids("Run your own tests before finishing."),
        )

    def test_test_it_yourself_triggers(self):
        self.assertIn(
            "SPEC-VERIFY-SELF-REFERENTIAL",
            rule_ids("Implement it and test it yourself."),
        )

    def test_check_your_work_triggers(self):
        self.assertIn(
            "SPEC-VERIFY-SELF-REFERENTIAL",
            rule_ids("Write the module, then check your work."),
        )

    def test_external_grader_does_not_trigger(self):
        self.assertNotIn(
            "SPEC-VERIFY-SELF-REFERENTIAL",
            rule_ids("Implement it. The test suite in tests/ is the acceptance criterion."),
        )


class RegressionTest(unittest.TestCase):
    """The new rules must not have broken the pre-existing ones."""

    def test_fluff_rules_still_fire(self):
        ids = rule_ids("You are a senior engineer. Please think step by step.")
        self.assertIn("FLUFF-ROLE", ids)
        self.assertIn("FLUFF-COT", ids)

    def test_vague_output_still_fires(self):
        self.assertIn("VAGUE-OUTPUT", rule_ids("Improve the parser."))

    def test_existing_rule_count_preserved(self):
        """Nothing was replaced; four rules were appended."""
        from prompt_lint import RULES

        ids = [r[0] for r in RULES]
        for expected in (
            "FLUFF-COT",
            "FLUFF-URGENCY",
            "FLUFF-ROLE",
            "FLUFF-PLEASANTRY",
            "NO-VERIFY-TARGET",
            "VAGUE-OUTPUT",
            "HINT-ONLY",
            "SENTENCE-CASE-QUESTION",
        ):
            self.assertIn(expected, ids)
        self.assertEqual(len(ids), len(set(ids)), "duplicate rule id")


if __name__ == "__main__":
    unittest.main()