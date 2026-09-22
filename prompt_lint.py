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
    # The rules below come from an A/B experiment (2026-09-21) measuring whether
    # delegating to the agent was faster than doing the work directly. It never
    # was, but the experiment found something more useful: the agent's failure
    # mode is not incompetence, it is filling interpretive gaps with its own
    # plausible-sounding rules. A prompt that leaves a hole gets an invented
    # constraint inside that hole. See ~/kb/projects/LAPORAN_FINAL.md.
    (
        "SPEC-UNDEFINED-EDGE",
        "warn",
        r"\b(and so on|etc\.?|other cases|similar cases|and the like|"
        r"such cases)\b",
        "Open-ended enumerations invite the agent to invent the remaining cases, "
        "and it will invent them plausibly rather than ask.",
        "Enumerate the edge cases explicitly, or say what to do when unsure "
        "('raise an error for anything not listed').",
    ),
    (
        "SPEC-TEST-ONLY-VALID",
        "warn",
        # Two shapes seen in real prompts: "verify all the examples" and
        # "check that all the valid cases above return the right number". The
        # first draft of this pattern only matched a narrow ordering and both
        # failed; test_prompt_lint_spec_gaps.py caught it.
        r"(\bverify\b[^.]*\b(all|the|these)\b[^.]*\b(examples?|cases?)\b"
        r"|\bcheck\b[^.]*\b(all|the)\b[^.]*\b(examples?|valid cases?)\b"
        r"|\bmake sure\b[^.]*\b(examples?|cases?)\b[^.]*\b(work|pass)\b)",
        "Asking to verify only the listed cases confirms the happy path. In the "
        "experiment the agent ran its own verification, it passed, and the "
        "result was still wrong on an unlisted input.",
        "Also specify what must FAIL, or ask for the boundary cases explicitly.",
    ),
    (
        "SPEC-NO-INVALID-CONTRACT",
        "info",
        r"^\s*(?!.*\b(raise|error|invalid|reject|must not|fail|except)\b).*"
        r"\b(function|method|def |implement|write a)\b.*$",
        "The prompt asks for an implementation but never states what invalid "
        "input should do, so the error contract is left to the agent.",
        "State the error type and the cases that trigger it.",
    ),
    (
        "SPEC-VERIFY-SELF-REFERENTIAL",
        "info",
        r"\b(run your own|verify your( own)? work|test it yourself|"
        r"make sure (it|your) works?|check your work)\b",
        "Self-verification is not independent evidence: the same model writes "
        "the check and the code, so it validates its own assumptions. In the "
        "experiment this produced a confident 'tests pass' on wrong output.",
        "Verify against a grader written before the implementation, by a "
        "different party, or against an external ground truth.",
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


# Negation handling, kept deliberately small. The lesson from LintLang's
# H2 is that a naive "is there a negation word nearby" check produces the
# opposite error: it suppresses real findings. So this makes two narrow
# claims and nothing more:
#
#   1. The negator must be in the same sentence as the fluff phrase.
#   2. The sentence must not contain a clause break between them.
#
# A fixed character window was tried first and failed both ways: too wide
# and it reaches across sentence boundaries ("Never commit secrets. Think
# step by step." lost its real finding), too narrow and ordinary wording
# is missed ("Never ask for step-by-step reasoning.").
_NEGATOR = (
    r"(?:never|do\s+not|don['\u2019]?t|does\s+not|doesn['\u2019]?t|"
    r"must\s+not|mustn['\u2019]?t|should\s+not|shouldn['\u2019]?t|"
    r"avoid|avoiding|refrain\s+from|no\s+need\s+to|without|forbidden|prohibited|"
    # Copula-bound "not" only: "this is not mission critical" prohibits,
    # while a bare "not" ("not just the answer") does not. Binding it to a
    # verb keeps the common bare form out of the negator set.
    r"is\s+not|are\s+not|was\s+not|were\s+not|isn['\u2019]?t|aren['\u2019]?t)"
)
_NEGATOR_RE = re.compile(rf"\b{_NEGATOR}\b", re.IGNORECASE)
# Sentence boundaries. A semicolon, a newline, or terminal punctuation ends
# the scope of a negator: "Never commit secrets. Think step by step." is a
# request, not a prohibition.
_BOUNDARY_RE = re.compile(r"[.!?;](?=\s|$)|\n")
# Within the sentence, a comma followed by a conjunction starts a new
# clause: "do not retry, and think step by step" orders the second thing,
# so the earlier negator does not reach it.
#
# Only a *comma-joined* conjunction splits. Without the comma, "do not say
# improve or enhance" coordinates two objects of one verb and the negation
# still covers both. Requiring the comma is what separates the two, and it
# is the rule LintLang's `_COORDINATED_CLAUSE` uses for the same reason.
_COORD_RE = re.compile(r",\s*(?:and|but|or|so|then|yet)\b", re.IGNORECASE)


def _sentence_before(text: str, position: int) -> str:
    """Return the text of the sentence containing ``position``, up to it."""
    starts = [m.end() for m in _BOUNDARY_RE.finditer(text, 0, position)]
    start = starts[-1] if starts else 0
    return text[start:position]


def _sentence_after(text: str, position: int) -> str:
    """Return the text from ``position`` to the end of its sentence."""
    end = _BOUNDARY_RE.search(text, position)
    return text[position : end.start()] if end else text[position:]


# A prohibition can also trail the phrase: "Think step by step is
# forbidden." The right-hand side is checked only up to the next clause
# boundary, so an unrelated later sentence cannot suppress a finding.
#
# A bare "not" is deliberately NOT a left-hand negator: "Think step by
# step, not just the answer" requests the thinking. It is only read as a
# prohibition when it is bound to a predicative form ("is not allowed",
# "is not mission critical"), which is a shape, not a bare word.
_TRAILING_PROHIBITION = re.compile(
    r"\b(?:is|are|was|were)\s+(?:strictly\s+)?"
    r"(?:forbidden|prohibited|banned|not\s+allowed|not\s+\w+|"
    r"unnecessary|unwanted)\b",
    re.IGNORECASE,
)


def _is_negated(text: str, pattern: str) -> bool:
    """True if every match of ``pattern`` sits inside a prohibition.

    Returns False when any match is not negated, so a prompt that both
    forbids and requests the same fluff is still reported. That asymmetry
    is deliberate: a missed finding costs more than an extra one.
    """
    matches = list(re.finditer(pattern, text, flags=re.IGNORECASE | re.MULTILINE))
    if not matches:
        return False
    for match in matches:
        sentence = _sentence_before(text, match.start())
        trailing = _sentence_after(text, match.end())
        negated_before = bool(_NEGATOR_RE.search(sentence))
        # A negator in an earlier coordinated clause does not reach this
        # one: "do not retry, and think step by step".
        if negated_before and not _NEGATOR_RE.search(_COORD_RE.split(sentence)[-1]):
            negated_before = False
        negated_after = bool(_TRAILING_PROHIBITION.search(trailing))
        if not (negated_before or negated_after):
            return False
    return True


def lint(prompt: str) -> list[Finding]:
    """Lint a dispatch prompt. Returns findings, most severe first."""
    findings: list[Finding] = []

    if not prompt.strip():
        return findings

    for rule_id, severity, pattern, message, suggestion in RULES:
        if pattern == "^":
            continue
        if re.search(pattern, prompt, flags=re.IGNORECASE | re.MULTILINE):
            # Rules whose pattern names something the prompt may instead be
            # forbidding. "Do not say improve or enhance" is the opposite of
            # asking to improve something, and reporting it as VAGUE-OUTPUT
            # inverts the author's meaning. Same for "Do not delete anything"
            # against DESTRUCTIVE-NO-GUARD, and for "Do not run your own
            # tests" against SPEC-VERIFY-SELF-REFERENTIAL.
            #
            # SPEC-NO-INVALID-CONTRACT is deliberately absent: it fires when
            # the prompt *omits* an invalid-input contract, so it names
            # nothing that could be negated.
            #
            # Scope stays per-rule. The check is only applied where the rule
            # reports a phrase, not where it reads the whole prompt as
            # evidence.
            if rule_id.startswith(
                (
                    "FLUFF-",
                    "VAGUE-OUTPUT",
                    "DESTRUCTIVE-NO-GUARD",
                    "SPEC-UNDEFINED-EDGE",
                    "SPEC-TEST-ONLY-VALID",
                    "SPEC-VERIFY-SELF-REFERENTIAL",
                )
            ) and (_is_negated(prompt, pattern)):
                continue
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