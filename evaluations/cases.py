from __future__ import annotations

from coursefuzz.domain.models import AssignmentSpec, ProgramVariant, TestCase


def _program(
    case_id: str,
    role: str,
    index: int,
    title: str,
    source: str,
    misconception: str = "none",
) -> ProgramVariant:
    return ProgramVariant(
        id=f"{case_id}-{role}-{index}",
        title=title,
        misconception=misconception,
        source=source.strip() + "\n",
    )


def _test(inputs: tuple[int, ...], expected: str | int | bool, label: str) -> TestCase:
    return TestCase(inputs=inputs, expected=expected, label=label, source="instructor")


def _case(
    *,
    case_id: str,
    title: str,
    summary: str,
    entrypoint: str,
    input_names: tuple[str, ...],
    domain: tuple[int, int],
    reference_source: str,
    control_source: str,
    mutant_sources: tuple[tuple[str, str, str], ...],
    tests: tuple[TestCase, ...],
) -> AssignmentSpec:
    reference = _program(case_id, "accepted", 1, "Reference", reference_source)
    control = _program(case_id, "accepted", 2, "Independent control", control_source)
    mutants = tuple(
        _program(case_id, "wrong", index, title, source, misconception)
        for index, (title, misconception, source) in enumerate(mutant_sources, start=1)
    )
    return AssignmentSpec(
        id=case_id,
        title=title,
        summary=summary,
        entrypoint=entrypoint,
        input_names=input_names,
        domain_min=domain[0],
        domain_max=domain[1],
        reference=reference,
        accepted_solutions=(reference, control),
        mutants=mutants,
        instructor_tests=tests,
    )


def frozen_cases() -> tuple[AssignmentSpec, ...]:
    """Return the frozen synthetic v1 benchmark corpus.

    Witness inputs and pass thresholds intentionally live in a different file and are not
    imported here or by either hypothesis provider.
    """

    return (
        _case(
            case_id="eval-absolute-value",
            title="Absolute value",
            summary="Return the non-negative magnitude of one bounded integer.",
            entrypoint="absolute_value",
            input_names=("value",),
            domain=(-2, 2),
            reference_source="""
def absolute_value(value):
    if value < 0:
        return -value
    return value
""",
            control_source="""
def absolute_value(value):
    if value >= 0:
        return value
    return 0 - value
""",
            mutant_sources=(
                (
                    "Identity",
                    "forgets negative magnitudes",
                    "def absolute_value(value):\n    return value",
                ),
                (
                    "Constant two",
                    "overfits the public example",
                    "def absolute_value(value):\n    return 2",
                ),
                (
                    "Zero negatives",
                    "clips negatives to zero",
                    "def absolute_value(value):\n    if value < 0:\n        return 0\n    return value",
                ),
                (
                    "Always negates",
                    "negates positive inputs",
                    "def absolute_value(value):\n    return -value",
                ),
                (
                    "Squares",
                    "confuses magnitude with square",
                    "def absolute_value(value):\n    return value * value",
                ),
                ("Always zero", "drops the input", "def absolute_value(value):\n    return 0"),
            ),
            tests=(_test((2,), 2, "positive example"),),
        ),
        _case(
            case_id="eval-maximum-pair",
            title="Maximum of a pair",
            summary="Return the larger of two bounded integers.",
            entrypoint="maximum_pair",
            input_names=("left", "right"),
            domain=(-2, 2),
            reference_source="""
def maximum_pair(left, right):
    if left >= right:
        return left
    return right
""",
            control_source="""
def maximum_pair(left, right):
    if right > left:
        return right
    return left
""",
            mutant_sources=(
                (
                    "Returns left",
                    "assumes the first input is larger",
                    "def maximum_pair(left, right):\n    return left",
                ),
                (
                    "Constant two",
                    "overfits the public example",
                    "def maximum_pair(left, right):\n    return 2",
                ),
                (
                    "Zero fallback",
                    "uses zero when right is larger",
                    "def maximum_pair(left, right):\n    if left >= right:\n        return left\n    return 0",
                ),
                (
                    "Returns right",
                    "assumes the second input is larger",
                    "def maximum_pair(left, right):\n    return right",
                ),
                (
                    "Returns minimum",
                    "reverses the comparison",
                    "def maximum_pair(left, right):\n    if left <= right:\n        return left\n    return right",
                ),
                (
                    "Adds inputs",
                    "uses addition instead of selection",
                    "def maximum_pair(left, right):\n    return left + right",
                ),
            ),
            tests=(_test((2, 1), 2, "left is larger"),),
        ),
        _case(
            case_id="eval-minimum-pair",
            title="Minimum of a pair",
            summary="Return the smaller of two bounded integers.",
            entrypoint="minimum_pair",
            input_names=("left", "right"),
            domain=(-2, 2),
            reference_source="""
def minimum_pair(left, right):
    if left <= right:
        return left
    return right
""",
            control_source="""
def minimum_pair(left, right):
    if right < left:
        return right
    return left
""",
            mutant_sources=(
                (
                    "Returns left",
                    "assumes the first input is smaller",
                    "def minimum_pair(left, right):\n    return left",
                ),
                (
                    "Constant minus two",
                    "overfits the public example",
                    "def minimum_pair(left, right):\n    return -2",
                ),
                (
                    "Zero fallback",
                    "uses zero when right is smaller",
                    "def minimum_pair(left, right):\n    if left <= right:\n        return left\n    return 0",
                ),
                (
                    "Returns right",
                    "assumes the second input is smaller",
                    "def minimum_pair(left, right):\n    return right",
                ),
                (
                    "Returns maximum",
                    "reverses the comparison",
                    "def minimum_pair(left, right):\n    if left >= right:\n        return left\n    return right",
                ),
                (
                    "Subtracts inputs",
                    "uses arithmetic instead of selection",
                    "def minimum_pair(left, right):\n    return left - right",
                ),
            ),
            tests=(_test((-2, 1), -2, "left is smaller"),),
        ),
        _case(
            case_id="eval-clamp-unit",
            title="Clamp to the unit interval",
            summary="Clamp one integer to the inclusive interval from minus one to one.",
            entrypoint="clamp_unit",
            input_names=("value",),
            domain=(-2, 2),
            reference_source="""
def clamp_unit(value):
    if value < -1:
        return -1
    if value > 1:
        return 1
    return value
""",
            control_source="""
def clamp_unit(value):
    if value >= -1 and value <= 1:
        return value
    if value < 0:
        return -1
    return 1
""",
            mutant_sources=(
                (
                    "Upper clamp only",
                    "omits the lower bound",
                    "def clamp_unit(value):\n    if value > 1:\n        return 1\n    return value",
                ),
                (
                    "Negative to zero",
                    "clips low values to zero",
                    "def clamp_unit(value):\n    if value < -1:\n        return 0\n    if value > 1:\n        return 1\n    return value",
                ),
                ("Identity", "omits both bounds", "def clamp_unit(value):\n    return value"),
                ("Always one", "returns the upper bound", "def clamp_unit(value):\n    return 1"),
                ("Always zero", "returns the midpoint", "def clamp_unit(value):\n    return 0"),
                (
                    "Lower clamp only",
                    "omits the upper bound",
                    "def clamp_unit(value):\n    if value < -1:\n        return -1\n    return value",
                ),
            ),
            tests=(_test((0,), 0, "inside interval"), _test((2,), 1, "above interval")),
        ),
        _case(
            case_id="eval-parity-label",
            title="Parity label",
            summary="Return even or odd for one bounded integer.",
            entrypoint="parity_label",
            input_names=("value",),
            domain=(-2, 2),
            reference_source="""
def parity_label(value):
    if value % 2 == 0:
        return "even"
    return "odd"
""",
            control_source="""
def parity_label(value):
    if value % 2 != 0:
        return "odd"
    return "even"
""",
            mutant_sources=(
                (
                    "Always even",
                    "overgeneralizes the example",
                    'def parity_label(value):\n    return "even"',
                ),
                (
                    "Only two is even",
                    "memorizes one input",
                    'def parity_label(value):\n    if value == 2:\n        return "even"\n    return "odd"',
                ),
                (
                    "Zero is odd",
                    "special-cases zero incorrectly",
                    'def parity_label(value):\n    if value == 0:\n        return "odd"\n    if value % 2 == 0:\n        return "even"\n    return "odd"',
                ),
                (
                    "Always odd",
                    "reverses the public class",
                    'def parity_label(value):\n    return "odd"',
                ),
                (
                    "Positive means even",
                    "uses sign instead of parity",
                    'def parity_label(value):\n    if value > 0:\n        return "even"\n    return "odd"',
                ),
                (
                    "Modulo reversed",
                    "reverses modulo branches",
                    'def parity_label(value):\n    if value % 2 == 0:\n        return "odd"\n    return "even"',
                ),
            ),
            tests=(_test((2,), "even", "positive even"),),
        ),
        _case(
            case_id="eval-multiple-three",
            title="Multiple of three",
            summary="Report whether one bounded integer is divisible by three.",
            entrypoint="multiple_of_three",
            input_names=("value",),
            domain=(0, 6),
            reference_source="""
def multiple_of_three(value):
    if value % 3 == 0:
        return True
    return False
""",
            control_source="""
def multiple_of_three(value):
    if value % 3 != 0:
        return False
    return True
""",
            mutant_sources=(
                (
                    "Always true",
                    "overgeneralizes the positive example",
                    "def multiple_of_three(value):\n    return True",
                ),
                (
                    "Only three",
                    "memorizes one divisible input",
                    "def multiple_of_three(value):\n    if value == 3:\n        return True\n    return False",
                ),
                (
                    "Zero false",
                    "forgets zero is divisible",
                    "def multiple_of_three(value):\n    if value == 0:\n        return False\n    if value % 3 == 0:\n        return True\n    return False",
                ),
                (
                    "Always false",
                    "drops the positive class",
                    "def multiple_of_three(value):\n    return False",
                ),
                (
                    "Evenness",
                    "checks parity instead",
                    "def multiple_of_three(value):\n    if value % 2 == 0:\n        return True\n    return False",
                ),
                (
                    "Remainder one",
                    "checks the wrong remainder",
                    "def multiple_of_three(value):\n    if value % 3 == 1:\n        return True\n    return False",
                ),
            ),
            tests=(_test((3,), True, "three is divisible"),),
        ),
        _case(
            case_id="eval-sign-category",
            title="Sign category",
            summary="Classify one bounded integer as negative, zero, or positive.",
            entrypoint="sign_category",
            input_names=("value",),
            domain=(-2, 2),
            reference_source="""
def sign_category(value):
    if value < 0:
        return "negative"
    if value > 0:
        return "positive"
    return "zero"
""",
            control_source="""
def sign_category(value):
    if value == 0:
        return "zero"
    if value >= 1:
        return "positive"
    return "negative"
""",
            mutant_sources=(
                (
                    "Always positive",
                    "overgeneralizes the public example",
                    'def sign_category(value):\n    return "positive"',
                ),
                (
                    "Zero positive",
                    "uses a non-strict positive branch",
                    'def sign_category(value):\n    if value >= 0:\n        return "positive"\n    return "negative"',
                ),
                (
                    "Negative zero",
                    "merges zero into negative",
                    'def sign_category(value):\n    if value > 0:\n        return "positive"\n    return "negative"',
                ),
                (
                    "Always zero",
                    "drops sign information",
                    'def sign_category(value):\n    return "zero"',
                ),
                (
                    "Reversed signs",
                    "swaps negative and positive",
                    'def sign_category(value):\n    if value < 0:\n        return "positive"\n    return "negative"',
                ),
                (
                    "Numeric labels",
                    "returns an unrelated encoding",
                    "def sign_category(value):\n    if value > 0:\n        return 1\n    return 0",
                ),
            ),
            tests=(_test((2,), "positive", "positive input"),),
        ),
        _case(
            case_id="eval-inclusive-order",
            title="Inclusive ordering",
            summary="Report whether the left integer is less than or equal to the right.",
            entrypoint="inclusive_order",
            input_names=("left", "right"),
            domain=(-2, 2),
            reference_source="""
def inclusive_order(left, right):
    if left <= right:
        return True
    return False
""",
            control_source="""
def inclusive_order(left, right):
    if left > right:
        return False
    return True
""",
            mutant_sources=(
                (
                    "Always true",
                    "overgeneralizes ordered examples",
                    "def inclusive_order(left, right):\n    return True",
                ),
                (
                    "Strict order",
                    "rejects equality",
                    "def inclusive_order(left, right):\n    if left < right:\n        return True\n    return False",
                ),
                (
                    "Equality only",
                    "checks equality instead of order",
                    "def inclusive_order(left, right):\n    if left == right:\n        return True\n    return False",
                ),
                (
                    "Always false",
                    "drops the positive class",
                    "def inclusive_order(left, right):\n    return False",
                ),
                (
                    "Reversed order",
                    "swaps operands",
                    "def inclusive_order(left, right):\n    if right <= left:\n        return True\n    return False",
                ),
                (
                    "Non-equality",
                    "checks only difference",
                    "def inclusive_order(left, right):\n    if left != right:\n        return True\n    return False",
                ),
            ),
            tests=(_test((1, 2), True, "increasing pair"), _test((2, 2), True, "equal pair")),
        ),
        _case(
            case_id="eval-median-three",
            title="Median of three",
            summary="Return the middle value of three bounded integers.",
            entrypoint="median_three",
            input_names=("a", "b", "c"),
            domain=(1, 3),
            reference_source="""
def median_three(a, b, c):
    if (a <= b and b <= c) or (c <= b and b <= a):
        return b
    if (b <= a and a <= c) or (c <= a and a <= b):
        return a
    return c
""",
            control_source="""
def median_three(a, b, c):
    if a > b:
        if b > c:
            return b
        if a > c:
            return c
        return a
    if a > c:
        return a
    if b > c:
        return c
    return b
""",
            mutant_sources=(
                (
                    "Returns middle position",
                    "confuses position with rank",
                    "def median_three(a, b, c):\n    return b",
                ),
                (
                    "Constant two",
                    "overfits sorted examples",
                    "def median_three(a, b, c):\n    return 2",
                ),
                (
                    "Returns first",
                    "assumes the first input is median",
                    "def median_three(a, b, c):\n    return a",
                ),
                (
                    "Returns last",
                    "assumes the last input is median",
                    "def median_three(a, b, c):\n    return c",
                ),
                (
                    "Returns maximum",
                    "selects the largest",
                    "def median_three(a, b, c):\n    if a >= b and a >= c:\n        return a\n    if b >= c:\n        return b\n    return c",
                ),
                (
                    "Returns minimum",
                    "selects the smallest",
                    "def median_three(a, b, c):\n    if a <= b and a <= c:\n        return a\n    if b <= c:\n        return b\n    return c",
                ),
            ),
            tests=(_test((1, 2, 3), 2, "ascending"), _test((3, 2, 1), 2, "descending")),
        ),
        _case(
            case_id="eval-low-band",
            title="Low score band",
            summary="Classify zero and one as low, and larger bounded scores as high.",
            entrypoint="score_band",
            input_names=("score",),
            domain=(0, 3),
            reference_source="""
def score_band(score):
    if score <= 1:
        return "low"
    return "high"
""",
            control_source="""
def score_band(score):
    if score > 1:
        return "high"
    return "low"
""",
            mutant_sources=(
                (
                    "Strict low",
                    "excludes the inclusive boundary",
                    'def score_band(score):\n    if score < 1:\n        return "low"\n    return "high"',
                ),
                (
                    "Always low",
                    "overgeneralizes the public example",
                    'def score_band(score):\n    return "low"',
                ),
                (
                    "Zero only",
                    "memorizes one low input",
                    'def score_band(score):\n    if score == 0:\n        return "low"\n    return "high"',
                ),
                ("Always high", "drops the low class", 'def score_band(score):\n    return "high"'),
                (
                    "Parity band",
                    "uses parity instead of threshold",
                    'def score_band(score):\n    if score % 2 == 0:\n        return "low"\n    return "high"',
                ),
                (
                    "Upper boundary low",
                    "labels the maximum as low",
                    'def score_band(score):\n    if score == 3:\n        return "low"\n    return "high"',
                ),
            ),
            tests=(_test((0,), "low", "zero score"),),
        ),
    )
