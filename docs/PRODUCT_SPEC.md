# Product specification

## Promise

Before an instructor releases an autograder, CourseFuzz finds a plausible wrong program that
still receives full marks, proves the gap with independent execution, proposes the smallest
regression test, requires approval of the exact content and destination, applies the change, and
reads the destination back.

## Primary user and moment

The primary user is a computer-science instructor or course staff member preparing a bounded
function-based Python assignment. The urgent moment is the final autograder review before student
submissions are graded.

## Supported product contract

An immutable assignment snapshot contains:

- one bounded Python entrypoint over one to six integer inputs;
- a declared integer search domain;
- one instructor reference and at least one independently authored accepted control;
- one to sixty-four realistic misconception programs or anonymized wrong submissions;
- one to one hundred instructor tests with scalar JSON outputs;
- either a local artifact target or a GitHub repository, base branch, and test directory.

The current generated pytest contract imports the declared entrypoint from `solution.py`. A target
repository must expose that module convention; configurable invocation/module adapters are a
post-release milestone rather than an unverified claim in this slice.

Snapshot IDs are derived from canonical SHA-256 content. Runs bind to that full hash, so later
assignment edits cannot silently change historical evidence.

## Golden path acceptance criteria

1. Import a manifest and reject malformed code, duplicate controls, invalid arity/domain data, or
   an accepted control that fails the instructor suite.
2. Execute all supplied misconception programs against the instructor suite and persist baseline
   metrics.
3. Generate at most eight bounded hypotheses. GPT-5.6 may propose inputs; it never receives or
   chooses expected outputs.
4. Establish expected results only when independently authored accepted controls agree.
5. Reject hypotheses without an executable disagreement; minimize a verified counterexample.
6. Show the exact regression source, affected misconceptions, accepted-control pass rate,
   destination path, base commit when applicable, and approval SHA-256.
7. Require an approval token bound to that payload. A changed destination or byte changes the
   hash and invalidates approval.
8. Apply locally or create a run-specific GitHub branch and draft pull request.
9. Read destination bytes back, rerun the complete corpus, persist the receipt, and expose the
   artifact or pull-request URL.
10. If no supplied misconception survives, finish as `no_action_required` without fabricating a
    repair.

## Release gates still open

- hardened microVM/container execution for hostile submissions;
- license-reviewed real-course evaluation with independently reviewed labels (synthetic v1 is complete);
- public deployment, live GitHub integration proof, demo video, and captions.

These gates are tracked in the canonical edge-case matrix and security document. They must not be
papered over with demo claims.
