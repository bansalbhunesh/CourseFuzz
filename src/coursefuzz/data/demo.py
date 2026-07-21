from coursefuzz.domain.models import AssignmentSpec, ProgramVariant, TestCase

REFERENCE = ProgramVariant(
    id="reference",
    title="Instructor reference",
    misconception="none",
    source="""def classify_triangle(a, b, c):
    if a <= 0 or b <= 0 or c <= 0:
        return "invalid"
    if a + b <= c or a + c <= b or b + c <= a:
        return "invalid"
    if a == b and b == c:
        return "equilateral"
    if a == b or b == c or a == c:
        return "isosceles"
    return "scalene"
""",
)


ALTERNATE_CORRECT = ProgramVariant(
    id="accepted-alternative",
    title="Accepted independent solution",
    misconception="none",
    source="""def classify_triangle(a, b, c):
    if a <= 0 or b <= 0 or c <= 0:
        return "invalid"
    if a + b <= c or a + c <= b or b + c <= a:
        return "invalid"
    if a == b:
        if b == c:
            return "equilateral"
        return "isosceles"
    if b == c or a == c:
        return "isosceles"
    return "scalene"
""",
)


MUTANTS = (
    ProgramVariant(
        id="mutant-ab-only",
        title="Only checks the first equal pair",
        misconception="Isosceles means a == b, ignoring side permutations.",
        source="""def classify_triangle(a, b, c):
    if a <= 0 or b <= 0 or c <= 0:
        return "invalid"
    if a + b <= c or a + c <= b or b + c <= a:
        return "invalid"
    if a == b and b == c:
        return "equilateral"
    if a == b:
        return "isosceles"
    return "scalene"
""",
    ),
    ProgramVariant(
        id="mutant-no-degenerate-check",
        title="Accepts degenerate triangles",
        misconception="Uses strict inequality and accepts a + b == c.",
        source="""def classify_triangle(a, b, c):
    if a <= 0 or b <= 0 or c <= 0:
        return "invalid"
    if a + b < c or a + c < b or b + c < a:
        return "invalid"
    if a == b and b == c:
        return "equilateral"
    if a == b or b == c or a == c:
        return "isosceles"
    return "scalene"
""",
    ),
    ProgramVariant(
        id="mutant-equilateral-shadowed",
        title="Labels equilateral as isosceles",
        misconception="Checks any equality before all-three equality.",
        source="""def classify_triangle(a, b, c):
    if a <= 0 or b <= 0 or c <= 0:
        return "invalid"
    if a + b <= c or a + c <= b or b + c <= a:
        return "invalid"
    if a == b or b == c or a == c:
        return "isosceles"
    if a == b and b == c:
        return "equilateral"
    return "scalene"
""",
    ),
    ProgramVariant(
        id="mutant-no-bc-pair",
        title="Never checks the last equal pair",
        misconception="Covers a=b and a=c but omits b=c.",
        source="""def classify_triangle(a, b, c):
    if a <= 0 or b <= 0 or c <= 0:
        return "invalid"
    if a + b <= c or a + c <= b or b + c <= a:
        return "invalid"
    if a == b and b == c:
        return "equilateral"
    if a == b or a == c:
        return "isosceles"
    return "scalene"
""",
    ),
    ProgramVariant(
        id="mutant-bc-only",
        title="Only checks the last equal pair",
        misconception="Isosceles means b == c, ignoring side permutations.",
        source="""def classify_triangle(a, b, c):
    if a <= 0 or b <= 0 or c <= 0:
        return "invalid"
    if a + b <= c or a + c <= b or b + c <= a:
        return "invalid"
    if a == b and b == c:
        return "equilateral"
    if b == c:
        return "isosceles"
    return "scalene"
""",
    ),
    ProgramVariant(
        id="mutant-ac-only",
        title="Only checks the outer equal pair",
        misconception="Isosceles means a == c, ignoring side permutations.",
        source="""def classify_triangle(a, b, c):
    if a <= 0 or b <= 0 or c <= 0:
        return "invalid"
    if a + b <= c or a + c <= b or b + c <= a:
        return "invalid"
    if a == b and b == c:
        return "equilateral"
    if a == c:
        return "isosceles"
    return "scalene"
""",
    ),
    ProgramVariant(
        id="mutant-guarded-bc",
        title="Checks b=c only in descending order",
        misconception="Adds an accidental a > b guard to one side permutation.",
        source="""def classify_triangle(a, b, c):
    if a <= 0 or b <= 0 or c <= 0:
        return "invalid"
    if a + b <= c or a + c <= b or b + c <= a:
        return "invalid"
    if a == b and b == c:
        return "equilateral"
    if a == b or a == c or b == c and a > b:
        return "isosceles"
    return "scalene"
""",
    ),
    ProgramVariant(
        id="mutant-scalene-is-invalid",
        title="Rejects scalene triangles",
        misconception="Treats a valid unequal triangle as invalid.",
        source="""def classify_triangle(a, b, c):
    if a <= 0 or b <= 0 or c <= 0:
        return "invalid"
    if a + b <= c or a + c <= b or b + c <= a:
        return "invalid"
    if a == b and b == c:
        return "equilateral"
    if a == b or b == c or a == c:
        return "isosceles"
    return "invalid"
""",
    ),
)


TRIANGLE_ASSIGNMENT = AssignmentSpec(
    id="triangle-classifier",
    title="Triangle classifier",
    summary=(
        "Classify three integer side lengths as invalid, equilateral, isosceles, or scalene. "
        "The instructor suite looks complete but misses a positional misconception."
    ),
    entrypoint="classify_triangle",
    input_names=("a", "b", "c"),
    domain_min=0,
    domain_max=8,
    reference=REFERENCE,
    accepted_solutions=(REFERENCE, ALTERNATE_CORRECT),
    mutants=MUTANTS,
    instructor_tests=(
        TestCase(
            inputs=(3, 3, 3), expected="equilateral", label="equilateral", source="instructor"
        ),
        TestCase(inputs=(3, 4, 5), expected="scalene", label="scalene", source="instructor"),
        TestCase(inputs=(1, 2, 3), expected="invalid", label="degenerate", source="instructor"),
        TestCase(inputs=(0, 2, 2), expected="invalid", label="non-positive", source="instructor"),
        TestCase(
            inputs=(2, 2, 3), expected="isosceles", label="isosceles a=b", source="instructor"
        ),
    ),
)


ASSIGNMENTS = {TRIANGLE_ASSIGNMENT.id: TRIANGLE_ASSIGNMENT}


def get_assignment(assignment_id: str) -> AssignmentSpec:
    try:
        return ASSIGNMENTS[assignment_id]
    except KeyError as exc:
        raise KeyError(f"Unknown assignment: {assignment_id}") from exc
