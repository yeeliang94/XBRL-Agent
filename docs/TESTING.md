# Testing standard

Tests exist to protect observable product behavior and load-bearing repository
contracts. Optimize for confidence per maintenance cost, not test count or line
coverage.

## Admission rule

Before adding a test, identify:

1. The user, business, API, persistence, or artifact outcome it protects.
2. The cheapest layer that can prove that outcome reliably.
3. Why an existing test does not already protect it.
4. The distinct input class, failure mode, or boundary involved.

Prefer extending or parameterizing the existing owner of a behavior. A changed
line alone is not a reason to add a test. A bug fix normally needs one regression
case at the lowest layer that reproduces the visible failure. Add a boundary test
only when wiring, serialization, persistence, or authorization caused the bug.

## Test sizes

- **Small:** Pure domain logic with no database, filesystem, subprocess, thread,
  network, or wall-clock dependency. Use these for edge-case matrices.
- **Medium:** One meaningful local boundary, such as an API route with auth, a
  repository with a temporary database, a workbook operation with a real
  fixture, or a React workflow with realistic API responses.
- **Large:** A small number of critical journeys through the normal pipeline.
  Paid and live-model tests remain opt-in.

Keep strong boundary coverage for the canonical concept path, terminal run and
audit status, migrations, authorization, atomic workbook output, filing-family
routing, notes replacement and source integrity, and critical operator flows.
Do not repeat every lower-level edge case at every higher layer.

## Assertion rules

- Assert returned data, persisted state, emitted artifacts, visible status,
  authorization decisions, or externally meaningful side effects.
- Treat exact helper calls, call order, source text, private structure, DOM
  nesting, CSS literals, and prompt prose as implementation details unless the
  exact representation is an explicit repository contract.
- Test through public seams with real in-process collaborators when cheap. Mock
  paid/network providers, clocks, subprocesses, destructive writes, and rare
  failures at the narrowest owned boundary.
- Use accessible role and name queries for UI behavior. Use test IDs only when
  no stable user-facing semantic exists.
- Replace fixed sleeps with completion signals or bounded polling of observable
  state.
- Parameterize genuine equivalence classes. Remove cases that exercise no new
  branch, boundary, risk, or past regression.

## Deletion and maintenance

Delete or rewrite a test when it:

- cannot name an observable defect it catches;
- protects behavior that is no longer a contract;
- fails under a safe refactor with identical outcomes;
- duplicates stronger or cheaper coverage;
- mirrors production branches or fixture construction;
- permanently depends on retries, ordering, shared state, or real time.

Source and documentation consistency tests are appropriate only for contracts
that cannot be exercised through behavior, such as generated prompt audits or
the standard-library-only mTool patcher. Keep those checks narrow and explain
why source inspection is the available seam.

When a test is flaky, fix its uncontrolled state or timing promptly. Do not make
reruns or quarantine the permanent solution. Measure slow tests before changing
suite architecture. Use focused tests and the invariant-pinning tests during
development; reserve the full parallel backend suite for broad or cross-cutting
changes as required by `AGENTS.md`.
