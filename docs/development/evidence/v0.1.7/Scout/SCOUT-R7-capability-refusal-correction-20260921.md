# SCOUT R7 capability-refusal correction — 2026-09-21

Status: **offline correction verified; release acceptance remains outside this
report.** The one retained source-matrix failure was corrected in the owned
capability-review regression test. No provider/model call, private-data access,
workpad outside disposable pytest fixtures, commit, tag, push, or release
activity was performed.

## Cause established

The retained matrix traceback was:

```text
tests/test_scout05_capability_review.py:352
AssertionError: after != before
At index 265: '.git/objects/pack/tmp_pack_OwK63L' != '.git/refs'
Left contains one more item: 'ui/template.html'
```

The expected refusal code was already correct:
`capability_review_operator_consent_required`. In
`src/gigai/capability_review.py`, the `operator_confirmed is not True` guard is
at lines 359–360, before the nested operation and before the call to
`run_with_journal_writer`; therefore this denied request cannot publish a
decision or reviewed manifest. The original assertion compared every recursive
entry, including transient `.git/objects/pack` maintenance state, and treated a
fixture/working-copy `ui/template.html` appearance as product mutation without
an authority-path check.

The source path was reproduced three times in isolation with the original test
selection: **1 passed** each run. The focused capability-review module passed
**27 passed** before the correction in the rebuilt matrix environment, showing
the failure was not a stable product failure.

## Minimal correction

`tests/test_scout05_capability_review.py` now uses the uniquely named helper
`_non_git_workpad_snapshot`, which preserves every non-Git directory path and
every non-Git regular-file path and exact bytes while excluding only `.git`
internals. The existing parameterized refusal test retains its expected error,
semantic before/after equality, and no-review-publication assertion.

The new regression
`test_operator_consent_refusal_is_preflight_and_preserves_committed_authority`
also replaces the module's `run_with_journal_writer` with a failing guard,
asserts that the guard is never reached, compares the semantic snapshot, checks
that `manifests/capability-reviews/` is absent, and re-reads the committed HEAD
bytes for:

```text
manifests/active-gig-version.json
manifests/gig-proposal.json
manifests/capabilities/capmanifest_00000000-0000-4000-8000-000000000072.json
manifests/template-instance-binding.json
```

The product source file `src/gigai/capability_review.py` was not changed: its
pre-writer refusal boundary is already correct.

## Verification

Using the rebuilt matrix environment and `python -I`:

```text
corrected target parameter, sequential repetitions: 1 passed, 1.89s; 1 passed, 1.87s; 1 passed, 1.95s
targeted parameter + new writer-guard regression: 2 passed in 3.73s
tests/test_scout05_capability_review.py: 28 passed in 53.32s
```

Additional checks:

```text
ruff check tests/test_scout05_capability_review.py src/gigai/capability_review.py: passed
```

The original full offline matrix remains the recorded **1564 passed, 1
skipped, 1 failed** run; it was not rerun because this correction is limited to
the owned test and the bounded evidence above is sufficient for diagnosis.

## Exact changed files

- `tests/test_scout05_capability_review.py` — semantic non-Git snapshot helper,
  corrected refusal assertion, and preflight/authority regression.
- `docs/development/evidence/v0.1.7/Scout/SCOUT-R7-capability-refusal-correction-20260921.md`
  — this report.
