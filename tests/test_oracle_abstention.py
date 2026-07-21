"""Adversarial tests for the trust boundary.

CourseFuzz's central honesty claim is that a hypothesis provider (GPT-5.6 or the deterministic
fallback) only proposes *inputs* — it never decides correctness. An input becomes a finding only
when two independently authored accepted solutions agree on the expected output and execution then
proves a surviving program disagrees. These tests attack that claim directly: they hand the engine
a situation in which it *could* be tempted to accuse a program, and assert it abstains instead.

If either test ever fails, CourseFuzz is fabricating findings, which is the one failure mode the
product exists to avoid.
"""

from __future__ import annotations

from coursefuzz.adapters.hypotheses import (
    DeterministicHypothesisProvider,
    HypothesisContext,
    HypothesisProvider,
    SurvivorHint,
)
from coursefuzz.adapters.sandbox import LocalRestrictedRunner
from coursefuzz.domain.engine import AssessmentEngine
from coursefuzz.domain.models import AssignmentSpec, AttackHypothesis, ProgramVariant
from coursefuzz.domain.models import TestCase as CFTestCase


def _identity_mutant() -> ProgramVariant:
    return ProgramVariant(
        id="mutant-identity",
        title="Returns the input unchanged",
        misconception="Assumes the transform is the identity everywhere.",
        source="def f(n):\n    return n\n",
    )


def test_engine_abstains_when_controls_disagree_on_the_divergent_region() -> None:
    """A poisoned control set: the two accepted solutions disagree exactly where the surviving
    mutant diverges. The oracle cannot establish truth there, so the engine must produce no
    finding rather than accuse the mutant on an input whose correct answer is unknown.
    """

    # Both controls agree for n >= 0 (return n); they disagree for n < 0 (0 vs 1).
    control_a = ProgramVariant(
        id="control-a",
        title="Clamps negatives to zero",
        misconception="none",
        source="def f(n):\n    if n < 0:\n        return 0\n    return n\n",
    )
    control_b = ProgramVariant(
        id="control-b",
        title="Clamps negatives to one",
        misconception="none",
        source="def f(n):\n    if n < 0:\n        return 1\n    return n\n",
    )
    assignment = AssignmentSpec(
        id="poisoned-controls",
        title="Poisoned control set",
        summary="Two accepted controls that disagree on negative inputs, by construction.",
        entrypoint="f",
        input_names=("n",),
        domain_min=-3,
        domain_max=3,
        reference=control_a,
        accepted_solutions=(control_a, control_b),
        # The identity mutant only diverges from a would-be correct answer on negatives, which is
        # precisely the region where the controls disagree.
        mutants=(_identity_mutant(),),
        instructor_tests=(
            CFTestCase(inputs=(2,), expected=2, label="positive", source="instructor"),
            CFTestCase(inputs=(0,), expected=0, label="zero", source="instructor"),
        ),
    )
    engine = AssessmentEngine(LocalRestrictedRunner(), DeterministicHypothesisProvider())

    result = engine.analyze(assignment)

    # The mutant survived the instructor suite, so the engine did have a target to chase...
    assert result.before.surviving_mutants == 1
    assert result.survivors_before == ("mutant-identity",)
    # ...but it refused to manufacture a counterexample it could not independently verify.
    assert result.candidate is None
    assert result.evidence["finding"] is False
    # And it did so for the right reason: the oracle explicitly abstained on the disputed input.
    abstained = [
        verdict
        for verdict in result.hypothesis_verdicts
        if verdict.status == "rejected" and "abstained" in verdict.reason
    ]
    assert abstained, "expected at least one hypothesis rejected because the oracle abstained"


class _FixedHypothesisProvider(HypothesisProvider):
    """Proposes one attacker-controlled input, to probe the engine's own guards."""

    mode = "deterministic-fallback"

    def __init__(self, inputs: tuple[int, ...]) -> None:
        self._inputs = inputs

    def propose(
        self,
        context: HypothesisContext,
        survivors: tuple[SurvivorHint, ...],
    ) -> tuple[AttackHypothesis, ...]:
        return (
            AttackHypothesis(
                id="hypothesis-1",
                inputs=self._inputs,
                rationale="deliberately outside the declared domain",
                misconception="out-of-domain probe",
                provider="deterministic-fallback",
            ),
        )


def test_out_of_domain_hypothesis_can_never_become_a_finding() -> None:
    """Even a hypothesis that would 'kill' a mutant is rejected if it falls outside the declared
    bounded domain, because the assignment's correctness contract does not cover it.
    """

    reference = ProgramVariant(
        id="ref-abs",
        title="Absolute value",
        misconception="none",
        source="def f(n):\n    if n < 0:\n        return 0 - n\n    return n\n",
    )
    alternate = ProgramVariant(
        id="alt-abs",
        title="Absolute value, independently authored",
        misconception="none",
        source="def f(n):\n    if n >= 0:\n        return n\n    return -n\n",
    )
    assignment = AssignmentSpec(
        id="abs-bounded",
        title="Bounded absolute value",
        summary="Magnitude of one integer, only defined on the declared domain.",
        entrypoint="f",
        input_names=("n",),
        domain_min=-2,
        domain_max=2,
        reference=reference,
        accepted_solutions=(reference, alternate),
        mutants=(
            ProgramVariant(
                id="mutant-identity",
                title="Returns the input unchanged",
                misconception="Negative inputs are already magnitudes.",
                source="def f(n):\n    return n\n",
            ),
        ),
        instructor_tests=(
            CFTestCase(inputs=(0,), expected=0, label="zero", source="instructor"),
            CFTestCase(inputs=(2,), expected=2, label="positive", source="instructor"),
        ),
    )
    # (7,) would expose the identity mutant, but it is far outside the declared domain [-2, 2].
    engine = AssessmentEngine(LocalRestrictedRunner(), _FixedHypothesisProvider((7,)))

    result = engine.analyze(assignment)

    assert result.candidate is None
    assert result.evidence["finding"] is False
    out_of_domain = [
        verdict
        for verdict in result.hypothesis_verdicts
        if verdict.status == "rejected" and "domain" in verdict.reason.lower()
    ]
    assert out_of_domain, "expected the out-of-domain hypothesis to be rejected on domain grounds"
