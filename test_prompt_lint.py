"""Tests for prompt_lint. Pure functions, no network, no server."""
from __future__ import annotations

import pytest

from prompt_lint import Finding, lint, _has_artefact

GOOD_PROMPT = (
    "Create lru_cache.py with a class LRUCache (capacity, get, put) using "
    "OrderedDict. Then create test_lru.py with at least 10 tests. "
    "Run pytest and make all tests pass. Commit with author Rouge "
    "<tarigansdavid@gmail.com>. Do not modify any other file."
)

FLUFF_PROMPT = (
    "Think step by step. This is very important for our critical production "
    "system. You are a senior staff engineer. Please think carefully."
)


def ids(findings: list[Finding]) -> set[str]:
    return {f.rule_id for f in findings}


# ---------------------------------------------------------------- fluff rules

def test_cot_flagged():
    assert "FLUFF-COT" in ids(lint("Think step by step and do the thing"))


def test_urgency_flagged():
    assert "FLUFF-URGENCY" in ids(lint("This is very important, fix it"))


def test_role_flagged():
    assert "FLUFF-ROLE" in ids(lint("You are a senior engineer, fix the bug"))


def test_pleasantry_flagged_as_info():
    findings = lint("Please create a.txt with content OK")
    pleasantry = [f for f in findings if f.rule_id == "FLUFF-PLEASANTRY"]
    assert pleasantry and pleasantry[0].severity == "info"


def test_clean_prompt_has_no_fluff_findings():
    assert not (ids(lint(GOOD_PROMPT)) & {"FLUFF-COT", "FLUFF-URGENCY", "FLUFF-ROLE"})


# ------------------------------------------------------------- shape / scope

def test_vague_output_flagged():
    assert "VAGUE-OUTPUT" in ids(lint("Improve the code and clean up the repo"))


def test_unbounded_scope_flagged():
    assert "UNBOUNDED-SCOPE" in ids(lint("Refactor everything in the codebase"))


def test_missing_artefact_flagged():
    assert "NO-ARTEFACT" in ids(lint("Make the thing faster somehow"))


def test_good_prompt_not_flagged_for_artefact():
    assert "NO-ARTEFACT" not in ids(lint(GOOD_PROMPT))


def test_has_artefact_detects_filename():
    assert _has_artefact("write config.yaml please")


def test_has_artefact_detects_test_command():
    assert _has_artefact("then run pytest until green")


def test_has_artefact_false_for_pure_opinion():
    assert not _has_artefact("which approach do you prefer")


# ------------------------------------------------------------------ em-dash

def test_em_dash_flagged_as_error():
    findings = lint("Write a file a.txt with a description\u2014make it short")
    em = [f for f in findings if f.rule_id == "EM-DASH"]
    assert em and em[0].severity == "error"


def test_hyphen_is_fine():
    assert "EM-DASH" not in ids(lint("Write a.txt with a short description - concise"))


# ------------------------------------------------------------- constraints

def test_no_constraint_info_when_absent():
    assert "NO-CONSTRAINT" in ids(lint("Create a.txt with content hello"))


def test_constraint_word_suppresses_info():
    assert "NO-CONSTRAINT" not in ids(lint("Create a.txt with exactly the text hello"))


def test_commit_instruction_suppresses_info():
    assert "NO-COMMIT-INSTRUCTION" not in ids(lint(GOOD_PROMPT))


def test_long_task_without_commit_gets_info():
    long_prompt = (
        "Build a module that reads a csv file and produces a summary report "
        "with totals per category, handling missing values carefully and "
        "writing the output to summary.md in the working directory. "
    ) * 3
    assert "NO-COMMIT-INSTRUCTION" in ids(lint(long_prompt))


# ------------------------------------------------- destructive / sequencing

def test_destructive_without_guard_flagged():
    bad = "Delete all the old migration files from the repo."
    assert "DESTRUCTIVE-NO-GUARD" in ids(lint(bad))


def test_destructive_with_guard_not_flagged():
    good = (
        "Delete only the remove_me function from target.py and keep everything "
        "else byte-identical."
    )
    assert "DESTRUCTIVE-NO-GUARD" not in ids(lint(good))


def test_chained_steps_warned():
    assert "MULTI-STEP-UNORDERED" in ids(lint("Create a.py then create b.py then c.py"))


def test_numbered_steps_not_warned():
    prompt = "Step 1: create a.py. Step 2: create b.py. Step 3: run pytest."
    assert "MULTI-STEP-UNORDERED" not in ids(lint(prompt))


# ------------------------------------------------------------------ ordering

def test_findings_sorted_most_severe_first():
    findings = lint(FLUFF_PROMPT)
    severities = [f.severity for f in findings]
    assert severities == sorted(severities, key=lambda s: {"error": 0, "warn": 1, "info": 2}[s])


def test_soft_hint_warned():
    assert "HINT-ONLY" in ids(lint("Search the repo for TODOs, use subagents if that helps"))


def test_multiline_input_is_scanned():
    assert "FLUFF-COT" in ids(lint("line one\nthink step by step\nline three"))


def test_empty_prompt_returns_no_findings():
    assert lint("") == []


@pytest.mark.parametrize("prompt", [GOOD_PROMPT, FLUFF_PROMPT, "", "short"])
def test_lint_never_raises(prompt):
    assert isinstance(lint(prompt), list)

# ---------------------------------------------------------------------------
# CLI: prompt_lint.py dijalankan sebagai program
# ---------------------------------------------------------------------------
# Fungsi lint() sudah diuji di atas, tapi jalan masuk CLI-nya belum pernah.
# Yang diuji di sini adalah penanganan file yang gagal dibaca - sebelum
# diperbaiki, keempat kasus di bawah mencetak traceback Python.


def _run_cli(argv, **kwargs):
    """Jalankan prompt_lint.py sebagai subprocess, kembalikan (code, out, err)."""
    import subprocess
    import sys
    import os

    root = os.path.dirname(os.path.abspath(__file__))
    proc = subprocess.run(
        [sys.executable, os.path.join(root, "prompt_lint.py")] + argv,
        capture_output=True,
        text=True,
        **kwargs,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_cli_missing_file_reports_clearly(tmp_path):
    """File tidak ada: pesan jelas, exit 2, TANPA traceback."""
    missing = str(tmp_path / "nope.txt")
    code, out, err = _run_cli(["--file", missing])
    assert code == 2
    assert "Traceback" not in err
    assert "cannot read" in err
    assert missing in err


def test_cli_directory_reports_clearly(tmp_path):
    """Direktori: pesan jelas, bukan IsADirectoryError mentah."""
    code, out, err = _run_cli(["--file", str(tmp_path)])
    assert code == 2
    assert "Traceback" not in err
    assert "cannot read" in err


def test_cli_binary_file_reports_clearly(tmp_path):
    """File biner: pesan jelas, bukan UnicodeDecodeError mentah."""
    p = tmp_path / "bin.dat"
    p.write_bytes(b"\xff\xfe\x00binary")
    code, out, err = _run_cli(["--file", str(p)])
    assert code == 2
    assert "Traceback" not in err
    assert "not UTF-8" in err


def test_cli_empty_file_reports_empty(tmp_path):
    """File kosong: pesan 'empty prompt' yang sudah ada, tetap exit 2."""
    p = tmp_path / "empty.txt"
    p.write_text("   \n")
    code, out, err = _run_cli(["--file", str(p)])
    assert code == 2
    assert "empty prompt" in err
    assert "Traceback" not in err


def test_cli_reads_a_real_prompt(tmp_path):
    """Jalan normal: file prompt yang sah dibaca dan dilint."""
    p = tmp_path / "task.txt"
    p.write_text("Fix the parser in /tmp/p.py, run pytest -q, and report the result.")
    code, out, err = _run_cli(["--file", str(p)])
    assert code == 0
    assert "Traceback" not in err
