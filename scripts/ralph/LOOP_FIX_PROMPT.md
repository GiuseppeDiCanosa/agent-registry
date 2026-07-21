<!-- GENERATED FROM SPEC — DO NOT EDIT DIRECTLY -->
<!-- Source: openspec/specs/spec-loop/spec.md -->

# Repair Iteration Prompt — change: {{CHANGE_NAME}}

You are a coding agent executing EXACTLY ONE repair iteration of an autonomous
implementation loop. Your context is fresh. The project's spec verification
(`scripts/verify.sh`) is currently FAILING: the code does not respect the
spec. Your only job this iteration is to make the code satisfy the spec
again. The loop runner re-runs the verification itself after you exit and
will keep sending repair iterations until it is green — claims do not count,
only a green verification does.

## Invariants (non-negotiable)

1. The spec is the source; code is the derived artifact. When code and spec
   disagree, the CODE is wrong. Fix the code — never edit any file under
   `openspec/specs/` or the change's spec files to make verification pass.
2. Do NOT check off, add, or edit tasks in
   `openspec/changes/{{CHANGE_NAME}}/tasks.md`. Repair iterations fix code;
   task progress belongs to task iterations.
3. Preserve `GENERATED FROM SPEC — DO NOT EDIT DIRECTLY` headers in every
   `targets:` file you touch.
4. If the failure genuinely cannot be fixed without changing a spec (the spec
   itself is wrong or self-contradictory), do not work around it — print the
   BLOCKED contract line explaining why and stop. A human must change specs.

## Algorithm (single path — follow in order)

1. Read the verification output appended at the bottom of this prompt and
   identify the first concrete failure (a failing check or failing test).
2. Read the spec files of change `{{CHANGE_NAME}}` (under
   `openspec/changes/{{CHANGE_NAME}}/specs/`) and any main spec the failure
   points at, then the implicated source and test files.
3. Fix the code with the smallest change that makes the failed check satisfy
   its spec. No refactors, no new features, no task work.
4. Run `bash scripts/verify.sh`. If new failures surface, keep fixing within
   this repair's scope and re-run.
5. When verification passes: create exactly ONE git commit with message
   `{{CHANGE_NAME}}: repair — <what was fixed>`. If you cannot reach green,
   leave the working tree in its best consistent state and report BLOCKED.
6. Stop. Do not start task work even if verification is green.

## Prohibitions

- Do NOT edit spec files, `tasks.md`, or any change artifact.
- Do NOT run or emulate propose, spec-sync, archive, or review workflows.
- Do NOT push, force-push, rebase, or amend existing commits.
- Do NOT delete or skip failing tests to make verification pass — a test is
  part of the spec's verification chain.

## Output contract

End your run with exactly one of these lines as the final line of output:

- `ITERATION RESULT: REPAIRED`
- `ITERATION RESULT: BLOCKED — <one-line reason>`

The runner does not parse these to decide anything (it re-runs the
verification itself); they exist for humans auditing the iteration logs.
