from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from coursefuzz.adapters.destinations import DestinationCoordinator
from coursefuzz.adapters.hypotheses import DeterministicHypothesisProvider
from coursefuzz.adapters.sandbox import LocalRestrictedRunner
from coursefuzz.domain.engine import AssessmentEngine
from coursefuzz.domain.models import AssignmentCreate, AssignmentSpec, TestCase, LocalArtifactDestination
from coursefuzz.domain.oracle import CompositeOracle
from coursefuzz.repositories.sqlite import RunRepository
from coursefuzz.services.assignment_service import AssignmentService
from coursefuzz.services.run_service import RunService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SCRAPED_DIR = Path("examples/scraped_assignments")

def run_real_data_e2e_verification():
    logging.info("Starting Exhaustive Real-Data E2E System Verification...")
    
    scraped_files = list(SCRAPED_DIR.glob("*.json"))
    if not scraped_files:
        raise FileNotFoundError("No scraped assignment files found in examples/scraped_assignments")

    logging.info(f"Found {len(scraped_files)} real assignment manifests to verify.")

    with tempfile.TemporaryDirectory(prefix="cf-e2e-real-") as tmp_dir:
        db_path = Path(tmp_dir) / "e2e.db"
        artifact_dir = Path(tmp_dir) / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        repo = RunRepository(db_path)
        sandbox = LocalRestrictedRunner()
        assignment_service = AssignmentService(repo, sandbox)
        engine = AssessmentEngine(
            sandbox=sandbox,
            hypotheses=DeterministicHypothesisProvider(),
            oracle=CompositeOracle(),
            max_analysis_seconds=10.0,
        )
        dest_coord = DestinationCoordinator(artifact_dir)

        run_service = RunService(
            repository=repo,
            engine=engine,
            assignments=assignment_service,
            artifact_dir=artifact_dir,
            mode="deterministic-fallback",
            destinations=dest_coord,
        )

        results_summary = []

        for json_file in scraped_files:
            logging.info(f"\n==========================================")
            logging.info(f"Verifying Real Assignment: {json_file.name}")
            logging.info(f"==========================================")

            raw_data = json.loads(json_file.read_text(encoding="utf-8"))
            
            # Ingest assignment through AssignmentService
            create_payload = AssignmentCreate.model_validate(raw_data)
            stored_assignment, _ = assignment_service.create(create_payload)
            assignment_id = stored_assignment.id
            logging.info(f"Ingested Assignment ID: {assignment_id} ({stored_assignment.spec.title})")

            # Create Run
            run_view, created = run_service.create_run(assignment_id, idempotency_key=f"e2e-{json_file.stem}")
            logging.info(f"Created Run ID: {run_view.id} | Initial Status: {run_view.status}")

            # Execute Analysis Engine
            run_service.analyze_run(run_view.id)

            # Fetch updated Run View
            analyzed_run = run_service.require_run(run_view.id)
            logging.info(f"Analysis Finished | Final Status: {analyzed_run.status}")

            if analyzed_run.status == "failed":
                logging.error(f"FAILURE on {json_file.name}: {analyzed_run.error}")
                results_summary.append({"file": json_file.name, "status": "FAILED", "error": analyzed_run.error})
                continue

            analysis = analyzed_run.analysis
            if analysis is None:
                logging.warning(f"No action required or analysis is None for {json_file.name}")
                results_summary.append({"file": json_file.name, "status": "NO_ACTION", "reason": "No surviving mutants or unverified hypotheses"})
                continue

            logging.info(f"Initial Mutation Score: {analysis.before.mutation_score}% ({analysis.before.surviving_mutants} survivors)")
            logging.info(f"Projected After Score: {analysis.projected_after.mutation_score}%")
            
            if analysis.candidate:
                logging.info(f"PROVEN Counterexample Input: {analysis.candidate.test.inputs}")
                logging.info(f"Expected Output: {analysis.candidate.test.expected}")
                logging.info(f"Actual Wrong Output: {analysis.candidate.observed_actual}")
                logging.info(f"Generated Pytest Patch:\n{analysis.candidate.pytest_source}")

                # Verify Patch Application
                verified_metrics = engine.verify_applied_patch(stored_assignment.spec, analysis.candidate)
                logging.info(f"Verified Applied Patch Score: {verified_metrics.mutation_score}%")

            results_summary.append({
                "file": json_file.name,
                "title": stored_assignment.spec.title,
                "status": analyzed_run.status,
                "score_before": analysis.before.mutation_score,
                "score_after": analysis.projected_after.mutation_score,
                "has_candidate": analysis.candidate is not None
            })

        print("\n==========================================")
        print("REAL-DATA END-TO-END VERIFICATION SUMMARY")
        print("==========================================")
        for res in results_summary:
            print(f"- [{res['status']}] {res['file']}: Before={res.get('score_before', 'N/A')}%, After={res.get('score_after', 'N/A')}%, Candidate={res.get('has_candidate', False)}")

if __name__ == "__main__":
    run_real_data_e2e_verification()
