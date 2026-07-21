from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from coursefuzz.data.demo import get_assignment
from coursefuzz.domain.engine import AssessmentEngine
from coursefuzz.domain.models import (
    ApprovalReceipt,
    RunStatus,
    RunView,
    utc_now,
)
from coursefuzz.repositories.sqlite import RunRepository


class RunService:
    def __init__(
        self,
        repository: RunRepository,
        engine: AssessmentEngine,
        artifact_dir: str | Path,
        mode: str,
    ) -> None:
        self.repository = repository
        self.engine = engine
        self.artifact_dir = Path(artifact_dir)
        self.mode = mode

    def create_run(self, assignment_id: str, idempotency_key: str) -> tuple[RunView, bool]:
        get_assignment(assignment_id)
        now = utc_now()
        run = RunView(
            id=f"run_{uuid4().hex[:16]}",
            assignment_id=assignment_id,
            status=RunStatus.QUEUED,
            mode=self.mode,
            created_at=now,
            updated_at=now,
        )
        run, created = self.repository.create(run, idempotency_key)
        if created:
            self.repository.append_event(
                run.id,
                "run.created",
                "ingest",
                "Assignment snapshot accepted; provenance and schema locked.",
                {"assignment_id": assignment_id, "mode": self.mode},
            )
        return run, created

    def analyze_run(self, run_id: str) -> None:
        run = self.require_run(run_id)
        if run.status != RunStatus.QUEUED:
            return
        try:
            run = run.model_copy(update={"status": RunStatus.ANALYZING, "updated_at": utc_now()})
            self.repository.save(run)
            self.repository.append_event(
                run.id,
                "analysis.started",
                "mutate",
                "Executing realistic misconception mutants against instructor tests.",
            )
            assignment = get_assignment(run.assignment_id)
            analysis = self.engine.analyze(assignment)
            self.repository.append_event(
                run.id,
                "analysis.hypotheses",
                "hypothesize",
                "Attack hypotheses generated; expected outputs remain unknown to the model.",
                {
                    "count": len(analysis.hypothesis_verdicts),
                    "provider": self.mode,
                },
            )
            self.repository.append_event(
                run.id,
                "analysis.verified",
                "verify",
                "Independent executions verified and minimized one real counterexample.",
                {
                    "inputs": list(analysis.candidate.test.inputs),
                    "expected": analysis.candidate.test.expected,
                    "payload_sha256": analysis.candidate.payload_sha256,
                },
            )
            run = run.model_copy(
                update={
                    "status": RunStatus.APPROVAL_REQUIRED,
                    "analysis": analysis,
                    "updated_at": utc_now(),
                }
            )
            self.repository.save(run)
            self.repository.append_event(
                run.id,
                "approval.required",
                "approve",
                "Exact test payload is ready for instructor approval.",
                {"payload_sha256": analysis.candidate.payload_sha256},
            )
        except Exception as exc:
            failed = run.model_copy(
                update={
                    "status": RunStatus.FAILED,
                    "error": f"{type(exc).__name__}: {exc}",
                    "updated_at": utc_now(),
                }
            )
            self.repository.save(failed)
            self.repository.append_event(
                run.id,
                "run.failed",
                "failed",
                "Analysis stopped safely and recorded a structured failure.",
                {"error": failed.error},
            )

    def approve(self, run_id: str, payload_sha256: str) -> ApprovalReceipt:
        run = self.require_run(run_id)
        if run.status not in {RunStatus.APPROVAL_REQUIRED, RunStatus.APPROVED} or not run.analysis:
            raise ValueError("Run is not awaiting approval")
        expected_hash = run.analysis.candidate.payload_sha256
        if payload_sha256 != expected_hash:
            raise ValueError("Approval hash does not match the exact proposed payload")
        token, approved_at = self.repository.approve(run_id, payload_sha256)
        approved = run.model_copy(
            update={
                "status": RunStatus.APPROVED,
                "approval_payload_sha256": payload_sha256,
                "updated_at": utc_now(),
            }
        )
        self.repository.save(approved)
        self.repository.append_event(
            run.id,
            "approval.granted",
            "approve",
            "Instructor approved the exact hashed regression test payload.",
            {"payload_sha256": payload_sha256},
        )
        return ApprovalReceipt(
            run_id=run.id,
            approval_token=token,
            payload_sha256=payload_sha256,
            approved_at=approved_at,
        )

    def apply(self, run_id: str, approval_token: str) -> RunView:
        run = self.require_run(run_id)
        if run.status == RunStatus.VERIFIED:
            return run
        if run.status != RunStatus.APPROVED or not run.analysis:
            raise ValueError("Run does not have an approved patch")
        candidate = run.analysis.candidate
        if not self.repository.consume_approval(run.id, approval_token, candidate.payload_sha256):
            raise ValueError("Approval token is invalid for this exact payload")

        applying = run.model_copy(update={"status": RunStatus.APPLYING, "updated_at": utc_now()})
        self.repository.save(applying)
        self.repository.append_event(
            run.id,
            "patch.applying",
            "apply",
            "Writing the approved regression test and rerunning every mutant.",
        )

        try:
            run_dir = (self.artifact_dir / run.id).resolve()
            run_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = run_dir / "test_coursefuzz_regression.py"
            artifact_path.write_bytes(candidate.pytest_source.encode("utf-8"))
            read_back = artifact_path.read_bytes()
            artifact_hash = hashlib.sha256(read_back).hexdigest()
            if read_back.decode("utf-8") != candidate.pytest_source:
                raise RuntimeError("Artifact read-back did not match the approved payload")

            metrics = self.engine.verify_applied_patch(get_assignment(run.assignment_id), candidate)
            if metrics != run.analysis.projected_after:
                raise RuntimeError("Post-write verification diverged from the approved projection")
            self.repository.save_artifact(run.id, artifact_path, artifact_hash)
        except Exception as exc:
            retryable = applying.model_copy(
                update={
                    "status": RunStatus.APPROVED,
                    "error": f"{type(exc).__name__}: {exc}",
                    "updated_at": utc_now(),
                }
            )
            self.repository.save(retryable)
            self.repository.append_event(
                run.id,
                "patch.failed",
                "apply",
                "The write or read-back failed; the approved action remains retryable.",
                {"error": retryable.error},
            )
            raise
        verified = applying.model_copy(
            update={
                "status": RunStatus.VERIFIED,
                "artifact_sha256": artifact_hash,
                "updated_at": utc_now(),
            }
        )
        self.repository.save(verified)
        self.repository.append_event(
            run.id,
            "patch.verified",
            "read-back",
            "Destination read-back matched and the repaired suite killed every viable mutant.",
            {
                "artifact_sha256": artifact_hash,
                "mutation_score": metrics.mutation_score,
                "accepted_solution_pass_rate": metrics.accepted_solution_pass_rate,
            },
        )
        return verified

    def require_run(self, run_id: str) -> RunView:
        run = self.repository.get(run_id)
        if not run:
            raise KeyError(run_id)
        return run
