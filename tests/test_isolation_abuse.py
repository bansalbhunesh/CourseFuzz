"""Live hostile probes for the exact Docker/gVisor isolation posture (Milestone 1).

Argument-level tests prove the runner requests memory, PID, read-only-root, tmpfs, no-network, and
privilege restrictions. These daemon-gated tests reuse that production argument builder and prove
the resource limits contain hostile programs under both runc and the intended runsc target. The
runsc cases self-skip outside a host with gVisor registered; CI runs every case.
"""

from __future__ import annotations

import subprocess

import pytest

from coursefuzz.adapters.isolated_runner import DockerIsolatedRunner, GVisorDockerRunner

IMAGE = "coursefuzz:local"
DEFAULT_MEMORY = 256 * 1024 * 1024
DEFAULT_PIDS = 128


def _docker_image_ready() -> bool:
    try:
        info = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if info.returncode != 0:
            return False
        image = subprocess.run(
            ["docker", "image", "inspect", IMAGE],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return image.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _gvisor_ready() -> bool:
    if not _docker_image_ready():
        return False
    try:
        info = subprocess.run(
            ["docker", "info", "--format", "{{range $k,$v := .Runtimes}}{{$k}} {{end}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return info.returncode == 0 and "runsc" in info.stdout.split()
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.fixture(
    params=(
        pytest.param("runc", id="runc"),
        pytest.param(
            "runsc",
            id="runsc",
            marks=pytest.mark.skipif(
                not _gvisor_ready(),
                reason="requires the runsc (gVisor) runtime registered with Docker",
            ),
        ),
    )
)
def isolated_runner(request: pytest.FixtureRequest) -> DockerIsolatedRunner:
    if not _docker_image_ready():
        pytest.skip("requires a running Docker daemon and a built coursefuzz:local image")
    if request.param == "runsc":
        return GVisorDockerRunner()
    return DockerIsolatedRunner()


def _probe_argv(
    runner: DockerIsolatedRunner,
    probe: str,
    *,
    memory_bytes: int = DEFAULT_MEMORY,
    max_pids: int = DEFAULT_PIDS,
) -> list[str]:
    """Replace only the production runner module with a hostile Python probe."""

    argv = runner._container_argv(  # noqa: SLF001 - contract-level proof of exact production flags
        memory_bytes,
        max_pids,
        "coursefuzz.adapters.runner",
    )
    assert argv[-3:] == ["-I", "-m", "coursefuzz.adapters.runner"]
    return [*argv[:-3], "-I", "-c", probe]


def _run_probe(
    runner: DockerIsolatedRunner,
    probe: str,
    *,
    memory_bytes: int = DEFAULT_MEMORY,
    max_pids: int = DEFAULT_PIDS,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        _probe_argv(
            runner,
            probe,
            memory_bytes=memory_bytes,
            max_pids=max_pids,
        ),
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_memory_ceiling_contains_a_memory_hog(
    isolated_runner: DockerIsolatedRunner,
) -> None:
    completed = _run_probe(
        isolated_runner,
        "bytearray(600 * 1024 * 1024); print('allocation-escaped')",
        memory_bytes=64 * 1024 * 1024,
    )

    assert completed.returncode != 0
    assert "allocation-escaped" not in completed.stdout


def test_pids_ceiling_refuses_a_thread_bomb(
    isolated_runner: DockerIsolatedRunner,
) -> None:
    probe = (
        "import threading, time\n"
        "print('probe-started', flush=True)\n"
        "for _ in range(500):\n"
        "    threading.Thread(target=time.sleep, args=(5,), daemon=True).start()\n"
        "print('pid-limit-escaped')\n"
    )
    # Use the production ceiling. The cgroup contains runc tasks; the paired OCI RLIMIT_NPROC
    # contains gVisor's virtual guest tasks. A 500-thread bomb exceeds either 128-task boundary.
    completed = _run_probe(isolated_runner, probe, max_pids=DEFAULT_PIDS)

    assert completed.returncode != 0
    assert "probe-started" in completed.stdout
    assert "pid-limit-escaped" not in completed.stdout


def test_read_only_root_denies_a_write_to_an_owned_directory(
    isolated_runner: DockerIsolatedRunner,
) -> None:
    # /app/data belongs to uid 10001, so this would succeed without the production --read-only flag.
    completed = _run_probe(
        isolated_runner,
        "open('/app/data/isolation-probe', 'w').write('escaped')",
    )

    assert completed.returncode != 0
    assert "read-only" in completed.stderr.lower() or "read only" in completed.stderr.lower()


def test_bounded_tmpfs_remains_writable_scratch(
    isolated_runner: DockerIsolatedRunner,
) -> None:
    completed = _run_probe(
        isolated_runner,
        "open('/tmp/scratch', 'w').write('ok'); print(open('/tmp/scratch').read())",
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok"
