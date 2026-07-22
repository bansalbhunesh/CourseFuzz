from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from coursefuzz.main import create_app


def verify_frontend_api_contracts():
    with tempfile.TemporaryDirectory(prefix="cf-contract-") as tmp_dir:
        db_path = Path(tmp_dir) / "contract.db"
        artifact_dir = Path(tmp_dir) / "artifacts"
        app = create_app(database_path=db_path, artifact_dir=artifact_dir)
        client = TestClient(app)

        print("Testing frontend API contract expectations...")

        # 1. Health contract expected by App.tsx
        res = client.get("/api/health")
        assert res.status_code == 200
        health = res.json()
        assert "mode" in health
        assert "auth" in health
        print("[OK] Health contract verified:", health)

        # 2. Assignments list contract expected by App.tsx
        res = client.get("/api/assignments")
        assert res.status_code == 200
        assignments = res.json()
        assert len(assignments) > 0
        assignment = assignments[0]
        assert "id" in assignment
        assert "title" in assignment
        print("[OK] Assignments list contract verified:", assignment["title"])

        # 3. Assignment detail contract expected by App.tsx
        res = client.get(f"/api/assignments/{assignment['id']}")
        assert res.status_code == 200
        detail = res.json()
        assert "spec" in detail
        assert "title" in detail["spec"]
        print("[OK] Assignment detail contract verified:", detail["spec"]["title"])

        # 4. Run creation contract expected by App.tsx
        res = client.post("/api/runs", json={"assignment_id": assignment["id"]})
        assert res.status_code in (200, 201, 202)
        run = res.json()
        assert "id" in run
        assert "status" in run
        print("[OK] Run creation contract verified, status:", run["status"])

        print("\nAll Frontend API Contracts 100% Verified!")


if __name__ == "__main__":
    verify_frontend_api_contracts()
