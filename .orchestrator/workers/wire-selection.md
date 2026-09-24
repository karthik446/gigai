# wire-selection — worker report

Task: 0.1.8.1 UAT ticket B2 (finish) — wire the committed pure helper
`select_for_assessment` (commit 0bf97c0, `src/gigai/scout/find_jobs/selection.py`)
into acquire's selection loop, replacing the first-come-first-served walk,
and make assess's not-assessed labeling agree with it.

READ: `.orchestrator/workers/selection-diversity-u2.md` (B2's recommended
call shape + reason-label recommendation), `src/gigai/scout/find_jobs/selection.py`
(the pure helper, unmodified), `src/gigai/scout/find_jobs/market_acquisition.py`
(`acquire_node`'s selection loop, contracts, `_role_match`/`_digest`),
`src/gigai/scout/proposal_execution.py` (`assess_node`'s candidate
resolution/not-assessed labeling), `src/gigai/scout/find_jobs/contracts.py`
(`NotAssessedReason`, `AssessInput`/`_validate_assess_input`, `SelectedPosting`,
`AcquireOutput`/`DropCount`), `tests/behaviors/scout_find_jobs/test_acquire_network.py`,
`tests/behaviors/scout_find_jobs/test_selection_diversity.py`,
`tests/behaviors/scout_find_jobs/test_assess_contract.py`,
`tests/behaviors/scout_find_jobs/test_assess_model_policy.py` (full file, to
find the existing `over_cap`/exclusion-labeling test this wiring affects).

EXECUTED:
- `src/gigai/scout/find_jobs/market_acquisition.py`: selection loop only.
- `src/gigai/scout/proposal_execution.py`: not-assessed labeling only.
- `tests/behaviors/scout_find_jobs/test_acquire_network.py`: new acquire-level
  UAT-shaped test + fixed a pre-existing test's `dropped_counts` expectation.
- `tests/behaviors/scout_find_jobs/test_assess_model_policy.py`: fixed a
  fixture collision + added a new DUPLICATE-labeling test (scope expanded to
  this file mid-task, coordinator-approved — see Escalation below).
- Did not touch `selection.py` (no fix was needed), `contracts.py`,
  `filters.py`, `test_selection_diversity.py`, `test_assess_contract.py`.

## 1. `market_acquisition.py` — acquire's selection loop

Replaced the old walk (append to `selected` in row order while
`len(selected) < selection_cap`, no dedupe/cap) with: build `candidates`
(new/edited rows passing the same `SelectionRule.NEW_OR_EDITED_ROLE_MATCH`
check as before), then one call `select_for_assessment(candidates,
cap=input.selection_cap)`. `selected_postings` is built from
`selection.selected` (same `SelectedPosting` construction as before, just
sourced from the helper's output instead of the raw walk). Deterministic:
`select_for_assessment` itself is order-independent (verified by its own
test suite), and `candidates`' construction order is `rows`' own stable order.

Kept the sealed contract valid: `select_for_assessment` never returns more
than `cap` rows and never a duplicate URL, so `_validate_assess_input`'s
unique/role_match=True/`len <= selection_cap` invariants hold unchanged
(role_match=True is still set unconditionally on every `SelectedPosting`,
same as before — every candidate already passed role-match to become a
candidate).

DropCount (item 3, additive): added the selection helper's per-URL drop
reasons to the existing `drop_counts` dict (already used for
exclusion-reason drops) — fits additively, no new field on `AcquireOutput`.
`"duplicate"` → `NotAssessedReason.DUPLICATE`; `"company_cap"`/`"over_cap"`
(both selection.py's own reasons) → `NotAssessedReason.OVER_CAP` — per the
coordinator's original fold decision recorded in `selection-diversity-u2.md`
("company_cap" and "over_cap" both mean "an otherwise-eligible row didn't
fit under the cap"). No new enum value.

## 2. `proposal_execution.py` — assess's not-assessed labeling

Old code: every unselected-but-eligible (not excluded, role-matched,
new/edited) candidate got `exclusion_reason(...) or NotAssessedReason.OVER_CAP`
— a blanket `OVER_CAP` fallback that couldn't distinguish "duplicate of the
selected posting" from "genuinely over the cap."

New code: the blanket-`OVER_CAP` rows are now provisional. After the main
loop, if any such row exists, `select_for_assessment` is recomputed over
`[*selected_postings_as_rows, *eligible_postings]` (the sealed selected set,
which must come out selected, plus every provisionally-OVER_CAP row) with
the same `selection_cap` acquire used. Its `dropped` map relabels each
provisional row `DUPLICATE` or `OVER_CAP` (same fold as acquire's). This
recomputes from the sealed `AssessInput`/acquire's own recorded rows — the
same inputs and the same pure helper acquire itself used — so it can never
disagree with what acquire actually dropped, per the ticket's "pick the one
that can't disagree and say why."

Chose recompute over "read what acquire recorded" because
`AcquireOutput`/`AssessInput` carry no per-row selection-drop-reason field
(only the aggregate `dropped_counts`, which has no per-URL detail) — a
sealed `AssessInput` has no place to carry that today, and adding one is a
contract change (excluded). Recomputing needs no new field and is provably
consistent by construction.

Exclusion-reason labeling (location/sponsorship) is checked first, same as
before; only rows that clear it and aren't selected go through the
recompute. A selected posting can never be relabeled away from being
assessed — the recompute only ever overwrites the *provisional* OVER_CAP
entries already in `not_assessed`, never removes/adds rows.

## Escalation (before finishing)

Ran the acceptance selectors early and found
`test_assess_model_policy.py::test_candidate_partition_mixes_assessed_over_cap_and_exclusions`
(not in my owned files) broke: its `_posting()` fixture defaults every
posting to the same company + title + location + `published_at`, so its
`assessed_posting` and `over_cap_posting` are genuinely duplicates under
`select_for_assessment`'s dedupe key — the real algorithm correctly
relabels the dropped one `DUPLICATE`, not `OVER_CAP`.

Escalated via `orca orchestration ask` rather than silently editing an
unowned file or leaving it broken. Coordinator's answer: I now also own
that file for this change — keep the test checking what it was written for
(distinguish an in-cap assessed row, an over-cap row, and the exclusion
reasons), give `over_cap_posting` a distinct company+title so it's
genuinely over-cap rather than a duplicate, and add a separate small test
proving a real duplicate gets labelled `DUPLICATE`. No assertion weakened,
only fixture inputs changed.

Applied exactly that:
- `_posting()` gained optional `company`/`title` parameters (default
  unchanged: `"Acme"`/`"Software Engineer"`), additive to every other
  existing call site.
- `over_cap_posting` now uses `company="Globex", title="Senior Software
  Engineer"` — a distinct dedupe key (still role-matches the fixture's
  `roles=["Software Engineer"]`) — so it's dropped for genuinely being over
  the cap, not a duplicate.
- New test `test_candidate_dropped_as_duplicate_is_labelled_duplicate_not_over_cap`:
  two postings sharing company/title/location/`published_at`, cap=1 — the
  unselected one is asserted `NotAssessedReason.DUPLICATE`.

## Results

- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_acquire_network.py tests/behaviors/scout_find_jobs/test_selection_diversity.py tests/behaviors/scout_find_jobs/test_assess_contract.py tests/behaviors/scout_find_jobs/test_m1_end_to_end.py -q` — **38 passed, 1 xfailed** (same pre-existing M1 xfail as the prior worker report, unrelated to this change).
- Same selectors plus `test_assess_model_policy.py` (the file the escalation
  expanded my ownership to) — **58 passed, 1 xfailed**.
- `make unit-tests` — **922 passed, 1300 deselected**.

New acceptance-level test added per the ticket's exact shape (36 ClickHouse +
31 Coupang + 2 gen-digital role-matched rows, cap 5), in
`test_acquire_network.py::test_uat_shape_selection_is_diverse_and_unselected_rows_are_labelled`:
asserts `len(selected_postings) == 5`, ≤2 per company, no duplicate
normalized titles among the selected, and every dropped row accounted for
under `DUPLICATE`/`OVER_CAP` in `dropped_counts` (no other reason, and at
least one `DUPLICATE` from the 20 shared-title ClickHouse rows).

Also fixed `test_replay_uat_0181_evidence_run_country_filter`'s
`dropped_counts` expectation: its 3 country-filter survivors are all the
same company ("acme", from both Ashby and Greenhouse fixtures), so the new
per-company cap (default 2) now additionally drops the 3rd as `OVER_CAP` —
a real, correct consequence of the fix, not a bug. Updated the assertion to
check the exclusion-stage drops (8, unchanged) and the new selection-stage
drop (+1) separately rather than weakening the total.

## Scope / exclusions honored

- Owned files touched: `market_acquisition.py` (selection loop only),
  `proposal_execution.py` (not-assessed labeling only), the three named
  test files, plus `test_assess_model_policy.py` (coordinator-approved
  scope expansion, see Escalation), plus this report.
- `selection.py`: read-only — no fix was needed.
- No schema/storage migration; no new `NotAssessedReason` value (folded
  onto the existing `DUPLICATE`/`OVER_CAP`, per the original B2 worker's
  recommendation and the coordinator's standing decision).
- No live provider/model calls (`test_assess_model_policy.py`'s
  `_ScriptedBinding`/monkeypatched adapter, same as its existing tests).
- No `git add`/`commit`/`stash`/`reset`/`clean`.
- Test budget honored: only the acceptance-listed files (plus the one
  coordinator-approved addition) were run; never the whole
  `scout_find_jobs/` directory.

## What's left

Nothing outstanding for this ticket. `selection.py`'s recommended fold
(`"company_cap"`/`"over_cap"` → `NotAssessedReason.OVER_CAP`, no new enum
value) is now the wired, tested behavior in both acquire and assess.
