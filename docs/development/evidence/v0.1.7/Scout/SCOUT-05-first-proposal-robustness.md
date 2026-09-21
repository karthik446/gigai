# SCOUT-05 first-proposal robustness follow-up

Date: 2026-09-09

## Scope

This bounded follow-up implements the F1--F4 actions from
`SCOUT-05-first-proposal-review.md` in the first Graph Set proposal path. The
only implementation and regression-test surfaces are `src/gigai/lifecycle.py`
and `tests/test_scout05_first_proposal.py`; no init, registry, workpad,
journal, tool, schema, provider, or public API surface was changed.

## Corrections

* F1 now validates the original definition pathname component-by-component
  before canonicalization. Absolute, traversal, and backslash member
  references are refused; symlinked definition members, parents, and the
  definition itself are refused. The macOS `/var` -> `/private/var` and
  `/tmp` -> `/private/tmp` aliases are deliberately accepted only when they
  resolve to those exact known system locations; arbitrary links remain
  refused.
* F2 adds replay regressions for a mutated working
  proposal, altered or missing `first-proposal-inputs.json`, and changed
  source input. Each refusal asserts that the journal commit set is unchanged.
  The sealed source identity remains byte-for-byte unchanged after failed
  attempts. Publisher-authority multiplexing was not broadened: recovery still
  authenticates the committed proposal and its identity artifact under the
  existing journal authority rather than accepting another publisher.
* F3 converts `CanonicalizationError` for noncanonical first-proposal Gig
  Markdown into the stable `LifecycleError` message
  `first Graph Set Gig document is not canonical Markdown`.
* F4 separates pure source verification from staging: locked revalidation uses
  the verifier without appending duplicate `source_members`. It also repeats
  original definition-path component and inode validation inside the journal
  writer lock, then checks bytes, so redirection after initial validation is
  rejected before publication.

The previous review described the definition symlink guard as safe, but that
claim was incomplete: resolving the path before checking `is_symlink()` makes
that check ineffective for the original spelling. The new symlink and
redirection tests demonstrate the gap and the corrected path-first validation.

F5 remains a documentation-level sequential-only compatibility constraint; no
broad concurrency redesign was attempted.

## Verification

Exact focused commands and results:

```text
.venv/bin/pytest -q tests/test_scout05_first_proposal.py
13 passed in 18.00s

ruff check src/gigai/lifecycle.py tests/test_scout05_first_proposal.py && \
python -m py_compile src/gigai/lifecycle.py tests/test_scout05_first_proposal.py
All checks passed!
```

The focused set covers the existing positive first-proposal roundtrip,
no-active and different-active behavior, CLI forwarding, the F1 path/symlink
cases, F2 replay refusal and no-publication assertions, F3 canonicalization
refusals, and F4 definition identity redirection during the journal-locked
operation. No full repository suite, wheel rebuild, provider invocation, real
user `.gigai` state, approval, activation, or commit was performed.
