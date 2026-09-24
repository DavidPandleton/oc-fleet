"""Stable data contracts for oc-fleet foreman runs.

These dataclasses are deliberately side-effect free. They define the shape
shared by the orchestrator, persistence layer, CLI, and future MCP adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

MAX_REASON_LENGTH = 2000
MAX_OUTPUT_LENGTH = 8000


def _bounded(value: Any, limit: int) -> Any:
    """Keep evidence bounded while preserving its type for simple values."""
    if isinstance(value, str):
        return value[:limit]
    return value


def _serialize(value: Any) -> Any:
    """Convert contract values into JSON-compatible plain values."""
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value


@dataclass
class TaskSpec:
    """Serializable task identity and scheduling inputs."""

    task_id: str
    prompt: str
    workdir: str = "."
    model: str = ""
    depends_on: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "prompt": self.prompt,
            "workdir": self.workdir,
            "model": self.model,
            "depends_on": list(self.depends_on),
        }


@dataclass
class VerificationResult:
    """Independent verifier outcome for one task attempt or final result."""

    required: bool = False
    passed: Optional[bool] = None
    commands: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "passed": self.passed,
            "commands": _serialize(self.commands),
        }


@dataclass
class ArtifactManifest:
    """Bounded Git/worktree evidence produced after an attempt."""

    workdir: str
    files_changed: list[str] = field(default_factory=list)
    diff_stat: str = ""
    clean: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "workdir": self.workdir,
            "files_changed": list(self.files_changed),
            "diff_stat": self.diff_stat,
            "clean": self.clean,
        }


@dataclass
class AttemptRecord:
    """One execution attempt, including bounded failure evidence."""

    attempt: int
    model: str
    failure_class: Optional[str] = None
    reason: Optional[str] = None
    session_id: Optional[str] = None
    duration: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "model": self.model,
            "failure_class": self.failure_class,
            "reason": _bounded(self.reason, MAX_REASON_LENGTH),
            "session_id": self.session_id,
            "duration": self.duration,
        }


@dataclass
class TaskResult:
    """Final auditable result for one DAG task."""

    task_id: str
    status: str
    agent_outcome: Optional[str] = None
    verification: VerificationResult = field(default_factory=VerificationResult)
    artifacts: Optional[ArtifactManifest] = None
    attempts: list[AttemptRecord] = field(default_factory=list)
    last_text: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "agent_outcome": self.agent_outcome,
            "verification": self.verification.to_dict(),
            "artifacts": self.artifacts.to_dict() if self.artifacts else None,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "last_text": _bounded(self.last_text, MAX_OUTPUT_LENGTH),
        }


@dataclass
class RunEvent:
    """One structured transition in a foreman run."""

    run_id: str
    task_id: Optional[str]
    event_type: str
    status: str
    model: Optional[str] = None
    session_id: Optional[str] = None
    attempt: Optional[int] = None
    reason: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "event_type": self.event_type,
            "status": self.status,
            "model": self.model,
            "session_id": self.session_id,
            "attempt": self.attempt,
            "reason": _bounded(self.reason, MAX_REASON_LENGTH),
        }
