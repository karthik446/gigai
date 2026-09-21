# SCOUT-06 public Run corrections

Date: 2026-09-10. Coordinator implementation after independent review settled.

## D1: preserve the requested protocol on replay

The [independent review](SCOUT-06-public-run-review.md) correctly identified
cross-protocol replay. Its stated reachability was too narrow: the
coordinator's five real regressions also reproduced an empty checkpoint and
Plan replay on a legacy field-list graph in both version directions. All five
failed before correction (20.96s). The full v2 research-output checkpoint
shape is indeed refused by the v1 invocation schema, but not every checkpoint
or Plan uses that shape.

`_require_replay_protocol` now verifies both the returned record and nested
invocation version before each exact-replay return, including both Plan lookup
paths and the shared Run-operation lookup. Existing digests, deterministic IDs
and same-protocol replay identity are unchanged. Changed-intent conflicts are
not weakened. The five new real regression cases pass in **22.52s**.

## D2 and D3: small consistency corrections

- Version-2 output classification explicitly requires domain fields even
  independently of the schema gate; completion checks retain their existing
  three-field form.
- The v2 approved-field reader retains the existing 32-field/64-character
  bounds. No schema resource or historical version was widened.

## Publication-time resource revalidation

The worker's expected-failure test changed the selected immutable source's
**working-tree mirror**, not its committed Git bytes. This is distinct from a
competing compliant writer, already excluded by the journal lock. The runtime
now performs a final exact domain-binding check inside `_journaled`, before
publication and still under that lock. Its optional callable is an internal
service function constructed by checkpoint/submit; no CLI input, Gig manifest
or external agent can supply a callback or executable module.

The check verifies the pinned source/schema through the existing committed
authority path. Checkpoint and successful submit both install it. Exact replay
returns the already-committed record without rerunning this fresh-publication
check, so later local edits do not invalidate a completed identical retry.

The original expected-failure marker was removed; its source/checkpoint case
now passes as a mandatory test (**1 passed in 5.32s**). Coverage was then
expanded to source and schema mirror mutation at both checkpoint and submit,
plus completed replay after a later mirror edit. A combined real-flow,
adversarial, protocol and transport suite subsequently passed **35 tests in
140.51s**, with no skips or expected failures. Scoped Ruff also passed after
the expanded tests. This is focused integration evidence, not the full suite
or installed-artifact acceptance.

## Review evidence boundaries

The prior reviewer changed no product code. Its broad statement that no
`src/` or `tests/` files changed during the review should not be read as a
worktree freeze: root concurrently added separate discovery code/tests and
protocol regressions, and Luna edited its owned hardening tests. The reviewed
core source was held stable until that review settled. These corrections need
fresh scoped review; historical reuse and installed workflow remain open.
