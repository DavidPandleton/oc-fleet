"""Tests for stable foreman result contracts."""

import unittest

from contracts import (
    ArtifactManifest,
    AttemptRecord,
    RunEvent,
    TaskResult,
    TaskSpec,
    VerificationResult,
)


class ContractsTest(unittest.TestCase):
    def test_verification_result_serializes_deterministically(self):
        result = VerificationResult(
            required=True,
            passed=True,
            commands=[{"command": "python3 -m pytest -q", "returncode": 0}],
        )
        self.assertEqual(result.to_dict(), {
            "required": True,
            "passed": True,
            "commands": [{"command": "python3 -m pytest -q", "returncode": 0}],
        })

    def test_artifact_manifest_has_stable_defaults(self):
        manifest = ArtifactManifest(workdir="/tmp/task-a")
        self.assertEqual(manifest.to_dict(), {
            "workdir": "/tmp/task-a",
            "files_changed": [],
            "diff_stat": "",
            "clean": True,
        })

    def test_attempt_record_contains_bounded_failure_evidence(self):
        attempt = AttemptRecord(
            attempt=2,
            model="cutad/model-b",
            failure_class="provider_invalid_request",
            reason="HTTP 404",
            session_id="ses_x",
            duration=1.25,
        )
        payload = attempt.to_dict()
        self.assertEqual(payload["attempt"], 2)
        self.assertEqual(payload["failure_class"], "provider_invalid_request")
        self.assertEqual(payload["duration"], 1.25)

    def test_task_result_contains_attempts_and_artifacts(self):
        result = TaskResult(
            task_id="build",
            status="verification_passed",
            agent_outcome="succeeded",
            verification=VerificationResult(required=True, passed=True),
            artifacts=ArtifactManifest(workdir="/tmp/build"),
            attempts=[AttemptRecord(attempt=1, model="cutad/model")],
        )
        payload = result.to_dict()
        self.assertEqual(payload["task_id"], "build")
        self.assertEqual(payload["status"], "verification_passed")
        self.assertEqual(payload["verification"]["passed"], True)
        self.assertEqual(payload["attempts"][0]["model"], "cutad/model")

    def test_run_event_to_dict_omits_nothing_required(self):
        event = RunEvent(
            run_id="run-1",
            task_id="build",
            event_type="task_started",
            status="running",
            model="cutad/model",
        )
        payload = event.to_dict()
        self.assertEqual(payload["run_id"], "run-1")
        self.assertEqual(payload["event_type"], "task_started")
        self.assertIn("timestamp", payload)

    def test_task_spec_defaults_are_compatible(self):
        spec = TaskSpec(task_id="build", prompt="compile")
        self.assertEqual(spec.to_dict(), {
            "task_id": "build",
            "prompt": "compile",
            "workdir": ".",
            "model": "",
            "depends_on": [],
        })


if __name__ == "__main__":
    unittest.main()
