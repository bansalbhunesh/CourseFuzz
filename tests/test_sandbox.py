from coursefuzz.adapters.sandbox import SubprocessPythonSandbox
from coursefuzz.domain.models import ProgramVariant
from coursefuzz.domain.models import TestCase as CFTestCase


def test_sandbox_rejects_imports() -> None:
    program = ProgramVariant(
        id="unsafe",
        title="unsafe",
        misconception="attempts external access",
        source="""import os
def solve(value):
    return value
""",
    )
    test = CFTestCase(inputs=(1,), expected="1", label="probe", source="deterministic")

    result = SubprocessPythonSandbox().run_suite(program, "solve", (test,))

    assert result.error is not None
    assert "Unsupported syntax: Import" in result.error


def test_sandbox_enforces_entrypoint_contract() -> None:
    program = ProgramVariant(
        id="wrong-entrypoint",
        title="wrong entrypoint",
        misconception="contract mismatch",
        source="""def other(value):
    return value
""",
    )
    test = CFTestCase(inputs=(1,), expected="1", label="probe", source="deterministic")

    result = SubprocessPythonSandbox().run_suite(program, "solve", (test,))

    assert result.error is not None
    assert "exactly one function named solve" in result.error
