#!/usr/bin/env python3
"""oc-prompt-lint: check a dispatch prompt against measured harness behaviour.

Findings come from 16 controlled probes (see ~/kb/opencode-prompt-anatomy.md):
  * fluff ("think step by step", "critical", "you are a senior X") has ZERO
    measurable effect on a tool-using agent
  * negative constraints ARE obeyed
  * verification is built in; asking for it is only useful when you want a
    verifiable artefact (a test file, a report)
  * preservation constraints must name what to keep
  * strategy hints (use subagents) can be silently ignored

Usage:
    oc-prompt-lint "your prompt text"
    oc-prompt-lint --file prompt.txt
    echo "prompt" | oc-prompt-lint
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass

# Each rule: (id, severity, pattern, message, suggestion)
# severity: 'error' = will likely hurt, 'warn' = wasted tokens / weak task,
#           'info' = observation about the prompt.
RULES: list[tuple[str, str, str, str, str]] = [
    (
        "FLUFF-COT",
        "warn",
        r"\b(think step by step|step-by-step reasoning|reason carefully|"
        r"think carefully|take a deep breath)\b",
        "Chain-of-thought prompting had no measurable effect on task outcome "
        "in probes P2 vs P1.",
        "Drop it. Spend the tokens on a concrete, checkable instruction instead.",
    ),
    (
        "FLUFF-URGENCY",
        "warn",
        r"\b(this is (very )?(important|critical|urgent)|critical (production|system)|"
        r"extremely important|mission critical)\b",
        "Urgency framing had no measurable effect on task outcome (probe P3).",
        "Drop it, or replace with a concrete acceptance criterion.",
    ),
    (
        "FLUFF-ROLE",
        "warn",
        r"\b(you are (a|an) (senior|expert|staff|principal|world-class)|"
        r"act as (a|an) (senior|expert)|as a senior)\b",
        "Role assignment had no measurable effect on task outcome (probe P4).",
        "Drop it. Name the required output shape or the constraint instead.",
    ),
    (
        "FLUFF-PLEASANTRY",
        "info",
        r"\b(please|kindly|if you (could|would)|thank you|thanks in advance)\b",
        "Politeness markers carry no task information.",
        "Optional. Keeping them is harmless; removing them saves tokens.",
    ),
    (
        "NO-VERIFY-TARGET",
        "info",
        r"^",
        "The harness already instructs the agent to verify by execution.",
        "Only ask for verification when you want a durable artefact "
        "(test file, report, exit code). Otherwise it is redundant.",
    ),
    (
        "VAGUE-OUTPUT",
        "error",
        r"\b(improve|enhance|clean ?up|make it (better|nicer)|do something about|"
        r"fix (it|things|everything))\b",
        "The requested outcome is not checkable, so the agent cannot know when "
        "it is done.",
        "State the observable end state: which file changes, what the test "
        "asserts, what the command should print.",
    ),
    (
        "NO-ARTEFACT",
        "warn",
        r"^",
        "No file, command, or exit condition is named anywhere in the prompt.",
        "Name at least one concrete artefact so completion is verifiable.",
    ),
    (
        "EM-DASH",
        "error",
        "\u2014",
        "An em-dash is present. The user rule is that em-dashes must never "
        "appear in any output.",
        "Replace with a hyphen or restructure the sentence.",
    ),
    (
        "UNBOUNDED-SCOPE",
        "warn",
        r"\b(everywhere|everywhere in|all files|entire (repo|codebase)|"
        r"the whole project|refactor everything)\b",
        "Unbounded scope makes completion and review both unverifiable.",
        "Bound it: name directories, file counts, or a stopping condition.",
    ),
    (
        "DESTRUCTIVE-NO-GUARD",
        "warn",
        r"\b(delete|remove|wipe|drop)\b(?![\s\S]{0,120}\b(keep|preserve|backup|"
        r"except|only|byte-identical|do not touch)\b)",
        "A destructive verb appears with no stated preservation constraint.",
        "Add what must survive (probe P16 shows the agent honours explicit "
        "preservation if you name it).",
    ),
    (
        "MULTI-STEP-UNORDERED",
        "warn",
        r"\b(then|and then|after that|next|finally)\b(?![\s\S]{0,80}\b(step|first|"
        r"in order|order)\b)",
        "Steps are chained with sequence words but no ordering instruction.",
        "Number them ('Step 1: ... Step 2: ...') so dependent steps are not "
        "fired in parallel.",
    ),
    (
        "NO-COMMIT-INSTRUCTION",
        "info",
        r"^",
        "The harness tells the agent never to commit unless explicitly asked.",
        "If you want a commit, ask for it and give author name and email; "
        "otherwise expect no commit (probe P9).",
    ),
    (
        "HINT-ONLY",
        "warn",
        r"\b(if (that|it) helps|feel free to|you may want to|consider using)\b",
        "Soft strategy hints can be silently ignored if the model judges them "
        "unnecessary (probe P13 dropped a subagent hint).",
        "Make it a requirement ('use grep'), or drop the hint entirely.",
    ),
    (
        "SENTENCE-CASE-QUESTION",
        "info",
        r"^\s*(what|how|why|when|where|who)\b.*\?\s*$",
        "Pure questions are fine, but they produce an answer, not a change.",
        "If you need an artefact, add the deliverable explicitly.",
    ),
]


@dataclass
class Finding:
    rule_id: str
    severity: str
    message: str
    suggestion: str

    def render(self) -> str:
        tag = {"error": "ERROR", "warn": "WARN ", "info": "INFO "}[self.severity]
        return f"  [{tag}] {self.rule_id}: {self.message}\n         -> {self.suggestion}"


# Substrings that count as a named artefact / acceptance condition.
ARTEFACT_PATTERNS = [
    r"\.(py|js|ts|rs|go|md|txt|json|yaml|yml|toml|sh)\b",
    r"\b(pytest|npm (run )?test|cargo test|go test|ruff|mypy|eslint|tsc)\b",
    r"\b(commit|git add|git commit)\b",
    r"\b(file|directory|folder|path)\b",
    r"\bexit code\b",
    r"\bassert",
    r"\b\d+\s+(test|tests|line|lines|word|words)\b",
    r"^(reply|output|return|print|write|create|add|delete|remove|run|report)\b",
]


def _has_artefact(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(p, lowered) for p in ARTEFACT_PATTERNS)


def lint(prompt: str) -> list[Finding]:
    """Lint a dispatch prompt. Returns findings, most severe first."""
    findings: list[Finding] = []

    if not prompt.strip():
        return findings

    for rule_id, severity, pattern, message, suggestion in RULES:
        if pattern == "^":
            continue
        if re.search(pattern, prompt, flags=re.IGNORECASE | re.MULTILINE):
            findings.append(Finding(rule_id, severity, message, suggestion))

    # Contextual rules that need more than a regex on their own.
    if not _has_artefact(prompt):
        findings.append(
            Finding(
                "NO-ARTEFACT",
                "warn",
                "No file, command, exit condition, or deliverable verb found.",
                "Name at least one concrete artefact so completion is verifiable.",
            )
        )

    if not re.search(
        r"\b(must|shall|always|never|do not|don't|exactly|only|at least|at most)\b",
        prompt,
        flags=re.IGNORECASE,
    ):
        findings.append(
            Finding(
                "NO-CONSTRAINT",
                "info",
                "No hard constraint (must/never/exactly/only) in the prompt.",
                "Constraints are the part this harness honours most reliably. "
                "Add the ones that matter.",
            )
        )

    if not re.search(r"\bcommit\b", prompt, flags=re.IGNORECASE) and len(prompt) > 200:
        findings.append(
            Finding(
                "NO-COMMIT-INSTRUCTION",
                "info",
                "Long, substantial task with no commit instruction.",
                "The agent will not commit on its own. Ask explicitly and give "
                "author name plus email if you want one.",
            )
        )

    order = {"error": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: order[f.severity])
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="oc-prompt-lint",
        description="Check a dispatch prompt against measured harness behaviour.",
    )
    src = ap.add_mutually_exclusive_group()
    src.add_argument("prompt", nargs="?", help="prompt text (omit to read stdin)")
    src.add_argument("--file", "-f", help="read the prompt from a file")
    ap.add_argument("--quiet", "-q", action="store_true", help="only errors and warnings")
    args = ap.parse_args()

    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            prompt = fh.read()
    elif args.prompt is not None:
        prompt = args.prompt
    else:
        prompt = sys.stdin.read()

    if not prompt.strip():
        print("oc-prompt-lint: empty prompt", file=sys.stderr)
        return 2

    findings = lint(prompt)
    if args.quiet:
        findings = [f for f in findings if f.severity != "info"]

    words = len(prompt.split())
    if not findings:
        print(f"oc-prompt-lint: clean ({words} words, 0 findings)")
        return 0

    errors = sum(1 for f in findings if f.severity == "error")
    warns = sum(1 for f in findings if f.severity == "warn")
    infos = sum(1 for f in findings if f.severity == "info")
    print(f"oc-prompt-lint: {words} words, {errors} error(s), {warns} warning(s), {infos} info")
    print()
    for f in findings:
        print(f.render())
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())