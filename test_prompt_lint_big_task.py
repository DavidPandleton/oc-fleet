"""A task too big for one session should be split, and the lint should say so.

The coffee-catalog run had one task, `scaffold`, that ran 18 minutes in
silence and still came back verification_failed after two attempts. It was
one session asked to lay down a whole app: several artefacts, several
ordered steps, no checkpoint in between. When it failed, there was no
smaller unit to point at, so the whole thing failed as a whole.

The decision to split belongs to the foreman, not the lint. The lint only
measures the prompt and warns when one session is being asked to do what
is really several sessions' work. That is why every rule here is `warn`,
never `error`: a large prompt is a smell, not a syntax error, and the
caller decides whether the split is worth the extra dispatch.

Thresholds are deliberately above the two-artefact, two-step prompts that
already work well, so the rule adds signal without flagging every clean
task. The existing GOOD_PROMPT (two files, one chained step) must stay
clean.
"""

from prompt_lint import lint


def ids(prompt):
    return {f.rule_id for f in lint(prompt)}


# The prompt from test_prompt_lint.py, which is known-good and must not trip
# the new rules. Kept as a literal so a change there cannot silently hide a
# regression here.
GOOD_PROMPT = (
    "Create lru_cache.py with a class LRUCache (capacity, get, put) using "
    "OrderedDict. Then create test_lru.py with at least 10 tests. "
    "Run pytest and make all tests pass. Commit with author Rouge "
    "<tarigansdavid@gmail.com>. Do not modify any other file."
)


class TestGoodPromptUnaffected:
    def test_big_task_rules_leave_the_working_prompt_alone(self):
        found = ids(GOOD_PROMPT)
        assert "BIG-TASK-MANY-STEPS" not in found
        assert "BIG-TASK-MANY-ARTEFACTS" not in found
        assert "BIG-TASK-NO-CHECKPOINT" not in found


class TestManySteps:
    def test_five_numbered_steps_is_flagged(self):
        prompt = (
            "Step 1: scaffold the project. Step 2: add routing. "
            "Step 3: build the data layer. Step 4: wire the UI. "
            "Step 5: deploy it."
        )
        assert "BIG-TASK-MANY-STEPS" in ids(prompt)

    def test_three_chained_steps_is_not_flagged(self):
        prompt = "First read a.py, then edit b.py, finally run pytest."
        assert "BIG-TASK-MANY-STEPS" not in ids(prompt)


class TestManyArtefacts:
    def test_many_distinct_files_is_flagged(self):
        prompt = (
            "Create index.html, styles.css, app.js, api.py, schema.sql, "
            "and README.md for the new service."
        )
        assert "BIG-TASK-MANY-ARTEFACTS" in ids(prompt)

    def test_a_couple_of_files_is_not_flagged(self):
        prompt = "Create cache.py and test_cache.py, then run pytest."
        assert "BIG-TASK-MANY-ARTEFACTS" not in ids(prompt)


class TestNoCheckpoint:
    def test_a_large_task_with_no_checkpoint_is_flagged(self):
        # Big by steps and artefacts at once, and nothing to stop at and
        # verify before the whole thing is done.
        prompt = (
            "Step 1: scaffold backend. Step 2: scaffold frontend. "
            "Step 3: add auth. Step 4: add billing. Step 5: deploy. "
            "Create server.py, client.js, auth.py, billing.py, and infra.tf."
        )
        assert "BIG-TASK-NO-CHECKPOINT" in ids(prompt)

    def test_a_large_task_with_a_named_checkpoint_is_not_flagged(self):
        # The same size, but it names a stopping point to verify first.
        prompt = (
            "Step 1: scaffold backend. Step 2: scaffold frontend. "
            "Step 3: add auth. Step 4: add billing. Step 5: deploy. "
            "Create server.py, client.js, auth.py, billing.py, and infra.tf. "
            "After step 2, stop and run the smoke test before continuing."
        )
        assert "BIG-TASK-NO-CHECKPOINT" not in ids(prompt)


class TestSeverity:
    def test_every_big_task_rule_is_only_a_warning(self):
        prompt = (
            "Step 1: scaffold backend. Step 2: scaffold frontend. "
            "Step 3: add auth. Step 4: add billing. Step 5: deploy. "
            "Create server.py, client.js, auth.py, billing.py, and infra.tf."
        )
        big = [f for f in lint(prompt) if f.rule_id.startswith("BIG-TASK-")]
        assert big, "expected at least one BIG-TASK finding"
        assert all(f.severity == "warn" for f in big)
