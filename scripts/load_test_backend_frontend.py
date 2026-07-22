from __future__ import annotations

import concurrent.futures
import logging
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from coursefuzz.main import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def simulate_api_user(client: TestClient, user_id: int) -> tuple[bool, str | None]:
    try:
        # 1. Health check
        res = client.get("/api/health")
        if res.status_code != 200:
            return False, f"Health status {res.status_code}"

        # 2. Get assignments list
        res = client.get("/api/assignments")
        if res.status_code != 200:
            return False, f"Assignments status {res.status_code}"

        assignments = res.json()
        if not assignments:
            return False, "No assignments returned"

        assignment_id = assignments[0]["id"]

        # 3. Create run
        res = client.post("/api/runs", json={"assignment_id": assignment_id})
        if res.status_code not in (200, 201, 202):
            return False, f"Create run status {res.status_code}: {res.text}"

        run_data = res.json()
        run_id = run_data["id"]

        # 4. Fetch run status
        res = client.get(f"/api/runs/{run_id}")
        if res.status_code != 200:
            return False, f"Get run status {res.status_code}"

        return True, None
    except Exception as exc:
        return False, f"User {user_id} API failure: {type(exc).__name__}: {exc}"


def run_backend_load_test(num_users: int = 500, workers: int = 25):
    logging.info(
        f"Starting Backend API & Serialization Load Test ({num_users} simulated user flows across {workers} workers)..."
    )

    with tempfile.TemporaryDirectory(prefix="cf-loadtest-") as tmp_dir:
        db_path = Path(tmp_dir) / "test.db"
        artifact_dir = Path(tmp_dir) / "artifacts"

        app = create_app(database_path=db_path, artifact_dir=artifact_dir)
        client = TestClient(app)

        start_time = time.monotonic()
        successful = 0
        failed = 0
        errors: list[str] = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(simulate_api_user, client, i): i for i in range(num_users)}

            for future in concurrent.futures.as_completed(futures):
                user_id = futures[future]
                try:
                    ok, err = future.result()
                    if ok:
                        successful += 1
                    else:
                        failed += 1
                        if err:
                            errors.append(err)
                except Exception as exc:
                    failed += 1
                    errors.append(f"Unexpected worker crash on user {user_id}: {exc}")

                if (successful + failed) % 100 == 0:
                    logging.info(
                        f"API Load Test Progress: {successful + failed}/{num_users} completed..."
                    )

        elapsed = time.monotonic() - start_time
        logging.info(f"Backend API Load Test Finished in {elapsed:.2f} seconds.")
        logging.info(
            f"Successful API Flows: {successful}/{num_users} | Failed: {failed}/{num_users}"
        )

        if errors:
            print("\n--- SURFACED BACKEND API ERRORS & BOTTLENECKS ---")
            for err in errors[:10]:
                print(f"- {err}")


if __name__ == "__main__":
    run_backend_load_test()
