from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from coursefuzz.domain.models import ProgramVariant, SuiteExecution, TestCase


class SubprocessPythonSandbox:
    """Executes the restricted demo language in an isolated Python process."""

    def __init__(self, timeout_seconds: float = 1.5) -> None:
        self.timeout_seconds = timeout_seconds
        self.runner_path = Path(__file__).with_name("runner.py").resolve()

    def run_suite(
        self,
        program: ProgramVariant,
        entrypoint: str,
        tests: tuple[TestCase, ...] | list[TestCase],
    ) -> SuiteExecution:
        payload = {
            "source": program.source,
            "entrypoint": entrypoint,
            "tests": [test.model_dump(mode="json") for test in tests],
        }
        environment = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            with tempfile.TemporaryDirectory(prefix="coursefuzz-") as workdir:
                completed = subprocess.run(  # noqa: S603
                    [sys.executable, "-I", str(self.runner_path)],
                    input=json.dumps(payload),
                    text=True,
                    capture_output=True,
                    cwd=workdir,
                    env=environment,
                    timeout=self.timeout_seconds,
                    check=False,
                    creationflags=creation_flags,
                )
        except subprocess.TimeoutExpired:
            return SuiteExecution(
                program_id=program.id,
                passed=0,
                failed=len(tests),
                timed_out=True,
                error="Execution exceeded the total deadline",
            )

        if len(completed.stdout) > 1_000_000:
            return SuiteExecution(
                program_id=program.id,
                passed=0,
                failed=len(tests),
                error="Execution output exceeded the 1 MB limit",
            )
        try:
            result = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            result = {"ok": False, "error": "Runner returned malformed JSON"}
        if not result.get("ok"):
            return SuiteExecution(
                program_id=program.id,
                passed=0,
                failed=len(tests),
                error=str(result.get("error", "Runner failed")),
            )
        return SuiteExecution(
            program_id=program.id,
            passed=int(result["passed"]),
            failed=int(result["failed"]),
            outputs=result["outputs"],
        )
