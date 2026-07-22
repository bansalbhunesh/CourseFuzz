from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxExecutionResult:
    """Outcome of isolated subprocess execution."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    memory_exceeded: bool


def run_in_isolated_sandbox(
    python_source: str,
    *,
    timeout_seconds: float = 2.0,
    max_memory_mb: int = 128,
) -> SandboxExecutionResult:
    """Execute Python code in a isolated subprocess with resource and time constraints."""
    # Write code to a temporary file
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tmp_file:
        tmp_file.write(python_source)
        tmp_path = tmp_file.name

    # Sanitized environment with no access to external sensitive vars
    sanitized_env = {
        "PATH": os.getenv("PATH", ""),
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    # Resource restriction wrapper code preamble (for Unix/Linux platforms where resource is available)
    preamble = (
        "import sys\n"
        "try:\n"
        "    import resource\n"
        f"    mem_bytes = {max_memory_mb} * 1024 * 1024\n"
        "    resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))\n"
        "except (ImportError, Exception):\n"
        "    pass\n"
    )

    full_code = preamble + python_source
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(full_code)

    timed_out = False
    memory_exceeded = False
    stdout = ""
    stderr = ""
    returncode = -1

    try:
        proc = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=sanitized_env,
        )
        returncode = proc.returncode
        stdout = proc.stdout[:4096]  # Truncate to 4KB max
        stderr = proc.stderr[:4096]
        if "MemoryError" in stderr or "std::bad_alloc" in stderr:
            memory_exceeded = True
    except subprocess.TimeoutExpired:
        timed_out = True
        stderr = f"Execution timed out after {timeout_seconds} seconds."
    finally:
        if os.path.exists(tmp_path):
            with contextlib.suppress(OSError):
                os.remove(tmp_path)

    return SandboxExecutionResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        memory_exceeded=memory_exceeded,
    )
