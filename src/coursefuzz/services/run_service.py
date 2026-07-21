from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from coursefuzz.adapters.destinations import DestinationCoordinator
from coursefuzz.domain.engine import AssessmentEngine
from coursefuzz.domain.models import (
    ApprovalReceipt,
    AssignmentSpec,
    GitHubPullRequestDestination,
    RunStatus,
    RunView,
    utc_now,
)
from coursefuzz.repositories.protocol import Repository
from coursefuzz.security.access import LOCAL_TENANT
from coursefuzz.services.assignment_service import AssignmentService


class RunService:
    def __init__(
        self,
        repository: Repository,
        engine: AssessmentEngine,
        assignments: AssignmentService,
        artifact_dir: str | Path,
        mode: str,
        destinations: DestinationCoordinator | None = None,
    ) -> None:
        self.repository = repository
        self.engine = engine
        self.assignments = assignments
        self.artifact_dir = Path(artifact_dir)
        self.destinations = destinations or DestinationCoordinator(self.artifact_dir)
        self.mode = mode

    def create_run(
        self,
        assignment_id: str,
        idempotency_key: str,
        tenant_id: str = LOCAL_TENANT,
    ) -> tuple[RunView, bool]:
        assignment = self.assignments.require(assignment_id, tenant_id)
        if (
            isinstance(assignment.spec.destination, GitHubPullRequestDestination)
            and not self.github_destination_available
        ):
            raise ValueError("GitHub destination is not configured on this CourseFuzz instance")
        now = utc_now()
        run = RunView(
            id=f"run_{uuid4().hex[:16]}",
            assignment_id=assignment_id,
            assignment_snapshot_sha256=assignment.snapshot_sha256,
            status=RunStatus.QUEUED,
            mode=self.mode,
            created_at=now,
            updated_at=now,
        )
        run, created = self.repository.create(run, idempotency_key, tenant_id)
        if not created and run.assignment_id != assignment_id:
            raise ValueError("Idempotency key is already bound to a different assignment")
        if created:
            self.repository.append_event(
                run.id,
                "run.created",
                "ingest",
                "Assignment snapshot accepted; provenance and schema locked.",
                {"assignment_id": assignment_id, "mode": self.mode},
            )
        return run, created

    def list_runs(
        self,
        assignment_id: str | None = None,
        tenant_id: str = LOCAL_TENANT,
    ) -> list[RunView]:
        return self.repository.list_runs(assignment_id, tenant_id)

    @property
    def github_destination_available(self) -> bool:
        return self.destinations.github.available

    def recover_incomplete_runs(self, limit: int = 10) -> int:
        recovered = 0
        for tenant_id, run in self.repository.list_recoverable_runs(limit):
            if run.status == RunStatus.APPLYING:
                retryable = run.model_copy(
                    update={
                        "status": RunStatus.APPROVED,
                        "error": "Recovered an interrupted apply; reauthorize the exact payload.",
                        "updated_at": utc_now(),
                    }
                )
                self.repository.save(retryable)
                self.repository.append_event(
                    run.id,
                    "run.recovered",
                    "recover",
                    "Interrupted apply restored to the approved retry boundary.",
                )
                recovered += 1
                continue
            if run.status == RunStatus.ANALYZING:
                queued = run.model_copy(
                    update={"status": RunStatus.QUEUED, "updated_at": utc_now()}
                )
                self.repository.save(queued)
                self.repository.append_event(
                    run.id,
                    "run.recovered",
                    "recover",
                    "Interrupted analysis returned to the durable queue.",
                )
                run = queued
            if run.status == RunStatus.QUEUED:
                self.analyze_run(run.id, tenant_id)
                recovered += 1
        return recovered

    def analyze_run(self, run_id: str, tenant_id: str = LOCAL_TENANT) -> None:
        run = self.require_run(run_id, tenant_id)
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
            assignment = self._assignment_for_run(run, tenant_id)
            analysis = self.engine.analyze(assignment)
            if analysis.candidate:
                prepared = self.destinations.prepare(run.id, analysis.candidate)
                analysis = analysis.model_copy(update={"candidate": prepared})
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
            if analysis.candidate:
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
                next_status = RunStatus.APPROVAL_REQUIRED
            else:
                self.repository.append_event(
                    run.id,
                    "analysis.no_finding",
                    "verify",
                    "Execution found no surviving counterexample that requires a test change.",
                    {"survivors": list(analysis.survivors_before)},
                )
                next_status = RunStatus.NO_ACTION_REQUIRED
            run = run.model_copy(
                update={
                    "status": next_status,
                    "analysis": analysis,
                    "updated_at": utc_now(),
                }
            )
            self.repository.save(run)
            if analysis.candidate:
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

    def approve(
        self,
        run_id: str,
        payload_sha256: str,
        tenant_id: str = LOCAL_TENANT,
    ) -> ApprovalReceipt:
        run = self.require_run(run_id, tenant_id)
        if (
            run.status not in {RunStatus.APPROVAL_REQUIRED, RunStatus.APPROVED}
            or not run.analysis
            or not run.analysis.candidate
        ):
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

    def apply(
        self,
        run_id: str,
        approval_token: str,
        tenant_id: str = LOCAL_TENANT,
    ) -> RunView:
        run = self.require_run(run_id, tenant_id)
        if run.status == RunStatus.VERIFIED:
            return run
        if run.status != RunStatus.APPROVED or not run.analysis or not run.analysis.candidate:
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
            applied = self.destinations.apply(run.id, candidate)
            metrics = self.engine.verify_applied_patch(
                self._assignment_for_run(run, tenant_id), candidate
            )
            if metrics != run.analysis.projected_after:
                raise RuntimeError("Post-write verification diverged from the approved projection")
            if applied.local_path:
                self.repository.save_artifact(
                    run.id,
                    applied.local_path,
                    applied.receipt.artifact_sha256,
                )
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
                "artifact_sha256": applied.receipt.artifact_sha256,
                "action_receipt": applied.receipt,
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
                "artifact_sha256": applied.receipt.artifact_sha256,
                "mutation_score": metrics.mutation_score,
                "accepted_solution_pass_rate": metrics.accepted_solution_pass_rate,
                "destination_kind": applied.receipt.kind,
                "external_url": applied.receipt.external_url,
            },
        )
        return verified

    def require_run(self, run_id: str, tenant_id: str = LOCAL_TENANT) -> RunView:
        run = self.repository.get(run_id, tenant_id)
        if not run:
            raise KeyError(run_id)
        return run

    def _assignment_for_run(
        self,
        run: RunView,
        tenant_id: str = LOCAL_TENANT,
    ) -> AssignmentSpec:
        snapshot = self.assignments.require(run.assignment_id, tenant_id)
        if (
            run.assignment_snapshot_sha256
            and snapshot.snapshot_sha256 != run.assignment_snapshot_sha256
        ):
            raise RuntimeError("Assignment snapshot hash no longer matches the run binding")
        return snapshot.spec
