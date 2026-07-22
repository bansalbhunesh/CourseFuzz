from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from coursefuzz.adapters.destinations import DestinationCoordinator
from coursefuzz.domain.engine import AssessmentEngine
from coursefuzz.domain.models import (
    ApprovalReceipt,
    AssignmentSpec,
    EvidenceBundle,
    EvidenceContent,
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
            and not self.destinations.github.repository_available(
                assignment.spec.destination.repository,
                tenant_id,
            )
            and tenant_id != LOCAL_TENANT
        ):
            raise ValueError(
                "GitHub destination is not configured or authorized for this workspace"
            )
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

    @property
    def github_destination_auth_mode(self) -> str:
        return self.destinations.github.credential_mode

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
            if run.status == RunStatus.EXTERNAL_CI_PENDING:
                self.poll_external_ci(run.id, tenant_id)
                recovered += 1
                continue
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
                prepared = self.destinations.prepare(run.id, analysis.candidate, tenant_id)
                analysis = analysis.model_copy(update={"candidate": prepared})
            self.repository.append_event(
                run.id,
                "analysis.hypotheses",
                "hypothesize",
                "Attack hypotheses generated; expected outputs remain unknown to the model.",
                {
                    "count": len(analysis.hypothesis_verdicts),
                    # Report the provider that actually produced this run's hypotheses. The
                    # configured mode can be live while a bounded model timeout correctly uses
                    # the deterministic fallback.
                    "provider": "+".join(
                        sorted(
                            {
                                verdict.hypothesis.provider
                                for verdict in analysis.hypothesis_verdicts
                            }
                        )
                    )
                    or self.mode,
                    "providers": sorted(
                        {
                            verdict.hypothesis.provider
                            for verdict in analysis.hypothesis_verdicts
                        }
                    ),
                },
            )
            if analysis.candidate:
                self.repository.append_event(
                    run.id,
                    "analysis.verified",
                    "verify",
                    (
                        "Independent executions verified and selected one "
                        "maximum-coverage counterexample."
                    ),
                    {
                        "inputs": list(analysis.candidate.test.inputs),
                        "expected": analysis.candidate.test.expected,
                        "payload_sha256": analysis.candidate.payload_sha256,
                        # How the expected output was established: provenance, sources, quorum.
                        "oracle": analysis.evidence.get("oracle_evidence"),
                    },
                )
                next_status = RunStatus.APPROVAL_REQUIRED
            else:
                self.repository.append_event(
                    run.id,
                    "analysis.no_finding",
                    "verify",
                    "Execution found no surviving counterexample that requires a test change.",
                    {
                        "survivors": list(analysis.survivors_before),
                        # Records why the oracle abstained, so "no finding" is never a black box.
                        "oracle": analysis.evidence.get("oracle_evidence"),
                    },
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
        applying = run.model_copy(update={"status": RunStatus.APPLYING, "updated_at": utc_now()})
        if not self.repository.claim_approved_apply(
            applying,
            approval_token,
            candidate.payload_sha256,
        ):
            raise ValueError("Approval token is invalid for this exact payload")
        self.repository.append_event(
            run.id,
            "patch.applying",
            "apply",
            "Writing the approved regression test and rerunning every mutant.",
        )

        try:
            applied = self.destinations.apply(run.id, candidate, tenant_id)
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
                    "error": f"{type(exc).__name__}: {exc}; reauthorize the exact payload",
                    "updated_at": utc_now(),
                }
            )
            self.repository.save(retryable)
            self.repository.append_event(
                run.id,
                "patch.failed",
                "apply",
                "The write or read-back failed; the exact payload must be reauthorized.",
                {"error": retryable.error},
            )
            raise
        receipt = applied.receipt
        # A GitHub write is only half-verified here: bytes match and our own rerun passed, but the
        # target repository's CI has not concluded. Hold at external_ci_pending until it does.
        if receipt.kind == "github_pull_request" and receipt.commit_sha:
            receipt = receipt.model_copy(update={"external_ci_started_at": utc_now()})
            pending = applying.model_copy(
                update={
                    "status": RunStatus.EXTERNAL_CI_PENDING,
                    "artifact_sha256": receipt.artifact_sha256,
                    "action_receipt": receipt,
                    "updated_at": utc_now(),
                }
            )
            self.repository.save(pending)
            self.repository.append_event(
                run.id,
                "external_ci.pending",
                "external-ci",
                "Draft PR written and byte read-back matched; awaiting the target repository CI.",
                {
                    "external_url": receipt.external_url,
                    "commit_sha": receipt.commit_sha,
                    "mutation_score": metrics.mutation_score,
                },
            )
            return pending

        verified = applying.model_copy(
            update={
                "status": RunStatus.VERIFIED,
                "artifact_sha256": receipt.artifact_sha256,
                "action_receipt": receipt,
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
                "artifact_sha256": receipt.artifact_sha256,
                "mutation_score": metrics.mutation_score,
                "accepted_solution_pass_rate": metrics.accepted_solution_pass_rate,
                "destination_kind": receipt.kind,
                "external_url": receipt.external_url,
            },
        )
        return verified

    def poll_external_ci(
        self,
        run_id: str,
        tenant_id: str = LOCAL_TENANT,
        *,
        deadline_seconds: float = 600.0,
        now: Callable[[], datetime] = utc_now,
    ) -> RunView:
        """Advance an external_ci_pending run by reading the target repository's CI conclusion.

        Idempotent and resumable: it reads GitHub check-runs for the written commit and moves the
        run to verified only when byte read-back (already done) and target CI both succeed; to
        external_ci_failed on a failing conclusion or once the deadline passes; otherwise it leaves
        the run pending. It never merges or mutates the pull request.
        """

        run = self.require_run(run_id, tenant_id)
        receipt = run.action_receipt
        if (
            run.status != RunStatus.EXTERNAL_CI_PENDING
            or receipt is None
            or not receipt.repository
            or not receipt.commit_sha
        ):
            return run

        status = self.destinations.github.check_runs(
            receipt.repository,
            receipt.commit_sha,
            tenant_id,
        )
        moment = now()
        started = receipt.external_ci_started_at or moment
        elapsed = (moment - started).total_seconds()

        if status.state == "success":
            finalized = receipt.model_copy(
                update={
                    "external_ci_url": status.url,
                    "external_ci_conclusion": "success",
                    "external_ci_completed_at": moment,
                    "external_ci_verified": True,
                }
            )
            verified = run.model_copy(
                update={
                    "status": RunStatus.VERIFIED,
                    "action_receipt": finalized,
                    "updated_at": moment,
                }
            )
            self.repository.save(verified)
            self.repository.append_event(
                run.id,
                "external_ci.verified",
                "external-ci",
                "Target repository CI passed on the draft PR; the external action is verified.",
                {"external_ci_url": status.url},
            )
            return verified

        if status.state == "failure" or elapsed > deadline_seconds:
            conclusion = status.conclusion if status.state == "failure" else "timed_out"
            finalized = receipt.model_copy(
                update={
                    "external_ci_url": status.url,
                    "external_ci_conclusion": conclusion,
                    "external_ci_completed_at": moment,
                }
            )
            failed = run.model_copy(
                update={
                    "status": RunStatus.EXTERNAL_CI_FAILED,
                    "action_receipt": finalized,
                    "error": f"Target repository CI did not pass: {conclusion}",
                    "updated_at": moment,
                }
            )
            self.repository.save(failed)
            self.repository.append_event(
                run.id,
                "external_ci.failed",
                "external-ci",
                "Target repository CI did not pass; the external action is not verified.",
                {"external_ci_url": status.url, "conclusion": conclusion},
            )
            return failed

        return run

    def require_run(self, run_id: str, tenant_id: str = LOCAL_TENANT) -> RunView:
        run = self.repository.get(run_id, tenant_id)
        if not run:
            raise KeyError(run_id)
        return run

    def build_evidence_bundle(
        self, run_id: str, tenant_id: str = LOCAL_TENANT
    ) -> EvidenceBundle:
        """Assemble a self-contained, independently re-hashable record of one run's evidence.

        Tenant-scoped: ``require_run`` raises ``KeyError`` for a missing or foreign run. The bundle
        hash covers only ``content`` (deterministic), so a judge can recompute it offline; the
        generation timestamp is envelope metadata and is deliberately left out of the digest.
        """
        run = self.require_run(run_id, tenant_id)
        events = tuple(self.repository.events_after(run_id, 0))
        oracle_evidence = None
        if run.analysis is not None:
            raw = run.analysis.evidence.get("oracle_evidence")
            oracle_evidence = raw if isinstance(raw, dict) else None
        content = EvidenceContent(
            run=run,
            assignment_snapshot_sha256=run.assignment_snapshot_sha256,
            oracle_evidence=oracle_evidence,
            artifact_sha256=run.artifact_sha256,
            audit_events=events,
        )
        digest = hashlib.sha256(
            json.dumps(
                content.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        return EvidenceBundle(
            bundle_sha256=digest,
            generated_at=utc_now(),
            content=content,
        )

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
