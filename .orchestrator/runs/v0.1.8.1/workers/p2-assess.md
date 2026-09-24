# P2 — assess node fixes (v0.1.8.1 hotfix): U25, U22, U21, U12

## READ

- `/Users/kar/orca/workspaces/gigai/v0.1.8-uat.md` — U12, U21, U22, U25 (visa sponsorship, unrecorded
  failure cause, one-bad-answer-kills-the-batch, empty posting text sent to the model).
- `src/gigai/scout/proposal_execution.py` (full file, pre-edit) — `assess_node`, `_assess_prompt`,
  `_read_pinned_postings`, `_read_pinned_resume`, `assess_invocation_policy`.
- `src/gigai/scout/proposals.py` — `parse_assessment_proposal`, `_validate_assessment_extensions`,
  `_MAX_ITEMS`/`_MAX_TEXT`/`_MAX_QUESTION` bounds (parse entry stayed untouched; normalization landed in
  `proposal_execution.py` instead, per the dispatch's own preference).
- `src/gigai/scout/find_jobs/contracts.py` — C0's new fields only (`PostingRow.text`/`.sponsorship`,
  `AssessmentResult.sponsorship`, `FindJobsConfig.visa_sponsorship_required`, `NotAssessedReason` additions,
  `AssessInput`/`AssessOutput`/`FindJobsRunInput`/`SelectedPosting` shapes, `_validate_assessment_partition`).
  Read-only; never edited (owned by C0).
- `src/gigai/run.py` — the registered-node execution/failure path (`_execute_goal`,
  `_write_registered_failure_receipt`, `_registered_node_paths`, `NodeReceipt`/`NodeFailure`/`GoalError`
  shapes) to scope the U21 edit to exactly that path.
- `src/gigai/scout/find_jobs/bindings.py` (`_assess_bound`, `_register_nodes`) — read-only, to understand
  why `assess_node`'s own `decoded["posting"]` must be `SelectedPosting`-shaped, not the full `PostingRow`:
  every real registered "assess" node goes through `_assess_bound`, which currently patches
  `scout_proposals.parse_assessment_proposal` process-wide to fix exactly this mismatch before calling
  `assess_node`. Not touched (not owned), but `assess_node` now builds the correct shape itself so it is
  correct even called directly (as the new tests do), independent of that global monkeypatch.
- Real operator run data (read-only): `~/.gigai-scout/workpads/projects/*/gigs/*/runs/run_*/outputs/acquire.json`
  for both UAT runs — confirmed real acquire rows have no `text` key at all (pre-C0 shape), which is the
  exact U25 case `assess_node` must degrade gracefully on.
- `tests/behaviors/scout_find_jobs/test_assess_model_policy.py`, `test_assess_contract.py`,
  `test_run_seam.py` (full files, pre-edit) — existing fixture/harness conventions
  (`_fixture`/`_goal`/`_graph` in `test_run_seam.py`; `_completed_find_jobs` reuse pattern).
- `.orchestrator/workers/c0-contracts.md`, `p1-acquire.txt` — confirmed scope boundary: P1 (acquire) owns
  producing `location_mismatch`/`sponsorship_excluded` exclusions and deriving `PostingRow.sponsorship` at
  acquire time; P2 (this packet) only reads those fields and produces the assess-time
  `AssessmentResult.sponsorship` read plus the visa constraint line in the prompt.

## ASKED

- `AssessInput` (contracts.py, closed via `_object`) has no `config`/`visa_sponsorship_required` field, and
  `assess_node`'s own `config: GigAIConfig` param is credentials/model-target config, not `FindJobsConfig`.
  There is no sealed path today from `FindJobsConfig.visa_sponsorship_required` into `assess_node`'s input.
  Asked the coordinator via `orca orchestration ask`; decision: read the sealed
  `runs/<run_id>/sealed/find-jobs-run-input.json` directly inside `proposal_execution.py` via
  `FindJobsRunInput.from_json` (contracts, read-only), falling back to `visa_sponsorship_required=False`
  when the file is missing (older run dirs, most existing tests/callers). No `contracts.py` change.
- Mid-task, the coordinator relayed a status message from P1: P1 was adding
  `src/gigai/scout/find_jobs/filters.py` (`exclusion_reason(posting, config)`, pure, shared by acquire's
  selection loop and this packet's not-assessed labeling) and asked assess to label non-selected candidate
  postings with `exclusion_reason(...)` (location_mismatch/sponsorship_excluded) instead of leaving them
  out of `candidate_rows` entirely. That's a real contract-shape expansion beyond my original TARGET scope
  (candidate_rows currently == exactly the selected set), so asked the coordinator for the exact partition
  semantics before guessing. Decision: candidates = acquire rows with outcome new/edited AND role-matched
  (import `_role_match` from `market_acquisition.py`, don't re-implement); assess reads the full acquire
  rows from the same `acquire.json` it already opens. Partition priority for a non-selected candidate:
  `exclusion_reason` (location_mismatch/sponsorship_excluded) → `over_cap` (eligible but beyond the cap) →
  (existing: `model_output_invalid` after retry, `model_unavailable`/`model_denied`, missing-text `failed`
  for the selected set). `candidate_rows` = every candidate. Unchanged/duplicate/failed rows and
  role-mismatched rows are never candidates. Added the T4-partition test this decision explicitly asked
  for (mixed: in-cap assessed, over_cap, location_mismatch, sponsorship_excluded, an unchanged row
  excluded from candidates).

## EXECUTED

- `src/gigai/scout/proposal_execution.py`:
  - `_read_pinned_postings` replaced by `_read_acquire_rows(root, batch_ref)`, returning every
    `(PostingRow, RowOutcome)` pair from the acquire batch in acquire's own order (not just the selected
    set) — needed for the candidate-partition expansion below — plus a small `_posting_text_bytes(posting)`
    helper (C0's `PostingRow.text`, encoded, or `None`).
  - New `_read_sealed_config(root, run_id)`: reads the sealed `FindJobsRunInput` (per the ask above) and
    returns its `FindJobsConfig`, or `None` on any missing/invalid file (older run dirs, most direct-call
    tests/callers) — generalized from an earlier visa-only helper once the candidate-partition ask made the
    whole config (roles, countries) necessary too.
  - `assess_node`'s candidate resolution (new, per the second ASKED decision above): for every acquire row
    with outcome new/edited, a *selected* posting is always a candidate (trusts `AssessInput`'s own sealed
    `role_match=True`, never re-derives it — this matters because `_role_match` on an empty roles tuple is
    fail-closed/`False`, so re-deriving it for the selected set would silently stop assessing everything
    whenever no sealed config is present, which is the common case in direct-call tests and older run
    dirs). A *non-selected* new/edited row is a candidate only when a sealed config exists and
    `_role_match(posting, config.roles)` (imported from `market_acquisition.py`, not re-implemented) is
    true; it is then labeled `exclusion_reason(posting, config)` (from `find_jobs/filters.py`, imported,
    not re-implemented) when that returns a reason, else `NotAssessedReason.OVER_CAP`. Unchanged/duplicate/
    failed rows, and role-mismatched rows, are skipped entirely — never added to `candidate_rows`.
    `to_assess` (only the selected set) then proceeds through the existing per-posting model loop below.
  - `_assess_prompt` rewritten: role/title/company/location, the bounded posting text (12k chars) and
    resume text (12k chars), the visa-sponsorship candidate constraint line, and a precise JSON schema
    with a short worked example (`matrix` rows with `requirement`/`resume_evidence`/`status`,
    `suggestions`, `questions`, `sponsorship`); asks for 5–12 concrete requirements drawn from the posting
    text, not generic ones. On a retry, the prior validation error (bounded to 300 chars) is appended so
    the model can self-correct.
  - New `_extract_json_object`: tolerant boundary parsing — accepts a bare object, strips ``` fences
    (with or without a `json` tag), and falls back to the first `{`..last `}` span for prose-wrapped
    output. Raises `ValueError` (never silently returns partial/garbage) when no JSON object can be found.
  - New `_normalize_assessment_payload` (the U22 tolerant-normalization step, run **before** strict
    contract validation, never inside `contracts.py`): `resume_evidence` string → `[string]`,
    `null`/missing → `[]`; matrix `status` synonyms (`yes`/`partially`/`no`, `meets`/`partial`/`missing`,
    any case) → `met`/`partial`/`gap`; `suggestions`/`questions` string → `[string]`, `null`/missing →
    `[]`; `sponsorship` synonyms (`yes`/`available`/`no`/`unavailable`/`unclear`/`n/a`, any case) →
    `offered`/`not_offered`/`unknown`; drops any other unknown top-level keys so the frozen contract's
    closed-object check still applies cleanly on the normalized shape. `parse_assessment_proposal` itself
    (`proposals.py`) is untouched — normalization happens strictly before it is called, and strict
    validation still runs immediately after.
  - `assess_node`'s per-posting loop rewritten for isolation (U22): a posting with no text is
    `not_assessed(FAILED)` without calling the model (closest existing reason to "could not attempt
    assessment" — `model_output_invalid` would misstate the cause, since the model was never invoked;
    confirmed no other reason is a better fit and none of the existing reasons are used for this
    condition elsewhere). A posting whose model answer fails normalization+parse gets exactly one retry
    with the validation error fed back into the prompt; if the retry also fails,
    `not_assessed(MODEL_OUTPUT_INVALID)` and the loop continues to the next posting instead of re-raising
    (previously a bare `except Exception: raise` killed the whole node on the first malformed answer —
    this was the exact U22 root cause). Model invocation failures (`ModelInvocationError`/`OSError`/
    `TimeoutError`, or an exception exposing `.code` in `{model_denied, network_denied, model_unavailable}`)
    are still recorded per-posting as `MODEL_DENIED`/`MODEL_UNAVAILABLE` with no retry, matching prior
    behavior. If every posting that had text and reached the model fails there,
    `assess_node` raises `ScoutProposalExecutionError("assess_all_postings_failed", ...)` — a specific,
    node-level failure (per the dispatch's "the node fails only if EVERY selected posting fails at the
    model" rule) rather than silently returning an empty-assessments output.
  - Fixed a real latent bug while wiring this: the decoded model answer's `"posting"` key was being set to
    `posting.to_json()` (the full `PostingRow`, with `provider`/`board_token`/`text`/etc.), but
    `AssessmentResult.posting` is the narrower, closed-object `SelectedPosting` DTO
    (`normalized_url`/`url`/`content_sha256`/`role_match` only) — parsing would always fail on
    `PostingRow`'s extra keys. In production this was silently papered over by `bindings.py`'s
    `_assess_bound`, which monkeypatches `parse_assessment_proposal` process-wide to rewrite exactly this
    field before parsing. `assess_node` now builds `selected_by_url[posting.normalized_url].to_json()`
    itself, so it is correct on its own (as the new direct-call tests exercise), independent of that
    global patch. Did not touch `bindings.py` (not owned); its monkeypatch remains harmless (re-applies
    the same already-correct value).
  - Added `import re` and a top-level `from .find_jobs.contracts import FindJobsContractError` (verified no
    circular-import issue by importing the module standalone before adding it).
- `src/gigai/run.py` (only the registered-node failure path):
  - New `_redacted_failure_message(exc)`: `f"{ClassName}: {str(exc)}"` (or just the class name if
    `str(exc)` is empty), hard-truncated to 300 chars with a trailing `…`. Per an explicit user decision
    (asked mid-task): this is intentionally *bounded*, not content-guaranteed-safe — it trusts that
    exceptions reaching this call site don't deliberately embed secrets, and relies on the 300-char bound
    to cut off any long quoted fragment (resume/posting text) before it can survive into the receipt.
  - New `_write_node_failure_log(resolved, run_id, goal, exc)`: appends the full
    `traceback.format_exception(...)` (timestamped) to `runs/<run_id>/logs/<goal_slug>.log`, local-only,
    never surfaced through the receipt/run-details/API. Reuses `_reject_symlinked_components` for the same
    path-safety guarantee as every other run-dir write; swallows `OSError` (best-effort, matching the
    existing failure-receipt swallow pattern) so a logging failure never masks the real error.
  - `_write_registered_failure_receipt` gained an optional `exc: BaseException | None` parameter; when
    given, the receipt's `NodeFailure.message` and the `GoalError.message` (same string) use
    `_redacted_failure_message(exc)` instead of the previous hardcoded `"registered node execution
    failed"`. No contract change: `NodeFailure`/`GoalError` already had free-text `message` fields.
  - The `except Exception as exc:` handler around registered-node execution now calls
    `_write_node_failure_log` unconditionally (outside the "receipt write may itself fail, swallow it"
    try/except, so a receipt-write failure never suppresses the log) and passes `exc=exc` into
    `_write_registered_failure_receipt`. The outer `raise RunError("registered node execution failed")
    from exc` string is unchanged, so nothing depending on that message (e.g. existing `pytest.raises(...,
    match=...)` call sites) breaks.
  - Added `import traceback`.
- `tests/behaviors/scout_find_jobs/test_assess_model_policy.py`: added a `_ScriptedPort`/`_ScriptedBinding`
  pair (scripts one model response — success text or a raised exception — per `invoke()` call) and an
  `_assess_fixture`/`_run_assess` harness (real `resolve_workpad` + a real pinned resume record + a
  hand-written `acquire.json`/sealed `find-jobs-run-input.json` under the target root, monkeypatching only
  `gigai.scout.proposal_execution.resolve_model_adapter`; no live network — `httpx` is never imported by
  this seam). New tests, calling `assess_node` directly:
  - `test_posting_text_reaches_the_prompt` — the posting text, title, and company all appear in the
    literal prompt string sent to the model.
  - `test_missing_posting_text_is_not_assessed_without_calling_the_model` — `PostingRow.text=None` →
    `not_assessed(FAILED)`, zero model calls.
  - `test_visa_sponsorship_required_reaches_the_prompt` — sealed `visa_sponsorship_required=True` →
    `"visa sponsorship required = yes"` appears in the prompt.
  - `test_string_resume_evidence_is_normalized_and_passes` — the exact U22 real failure shape
    (`resume_evidence` as a bare string, plus `status: "yes"` and `sponsorship: "not offered"`) normalizes
    and parses successfully, with the normalized values asserted.
  - `test_garbage_answer_retries_once_then_not_assessed_while_others_succeed` — a non-JSON answer for one
    posting retries once (asserts the retry prompt contains the validation-error text), still fails →
    `not_assessed(MODEL_OUTPUT_INVALID)` for that posting only, while a second posting in the same batch
    still succeeds.
  - `test_all_postings_failing_at_the_model_raises_a_specific_error` — every selected posting garbage →
    `ScoutProposalExecutionError` (not a silent empty-assessments output).
  - `test_fenced_json_output_is_extracted_before_normalization` — a ```json fenced, prose-wrapped answer
    still parses.
  - `test_model_denied_is_not_assessed_without_retry` — a `ModelInvocationError` with `.code =
    "model_denied"` is recorded once (no retry) as `not_assessed(MODEL_DENIED)`.
  - `test_candidate_partition_mixes_assessed_over_cap_and_exclusions` — the T4-partition test the
    coordinator's second decision explicitly asked for: 5 acquire rows (1 selected+assessed, 1 role-matched
    new row left unselected by a cap=1 → `OVER_CAP`, 1 role-matched new row in a non-US location with
    `countries=["US"]` sealed → `LOCATION_MISMATCH`, 1 role-matched new row with
    `sponsorship=NOT_OFFERED` and `visa_sponsorship_required=True` sealed → `SPONSORSHIP_EXCLUDED`, 1
    `unchanged`-outcome row). Asserts the reasons, that the unchanged row never appears in
    `candidate_rows`, that assessed/not-assessed partition `candidate_rows` completely and
    non-overlappingly, and that `AssessOutput.from_json(output.to_json()) == output` (the frozen
    contract's own T4 partition validation accepts the output on a round-trip).
- `tests/behaviors/scout_find_jobs/test_run_seam.py`: added
  `test_registered_node_failure_receipt_carries_redacted_message_and_writes_a_log`, calling
  `_execute_goal` directly against a stub that raises `ValueError` with a long embedded fragment standing
  in for posting/resume text. Asserts: the receipt's `failure.code == "node_execution_failed"`, its
  `message` contains `"ValueError"` and is `<= 300` chars, and the long fragment's tail marker does not
  survive truncation into the receipt; `goal.errors[0]` carries the same message; the projected
  `run-details` `detail["errors"][0]["message"]` (via `_apply_registered_receipt_to_detail`) matches; and
  `runs/<run_id>/logs/assess.log` exists, contains `"Traceback"` plus the full unredacted fragment.
- Wrote this file.

## NOT EXECUTED / OUT OF SCOPE

- Did not touch `contracts.py` (asked instead; see ASKED above) or `bindings.py` (not owned; its
  process-wide `parse_assessment_proposal` monkeypatch is now redundant but harmless).
- Did not implement acquire-time `location_mismatch`/`sponsorship_excluded` filtering or
  `PostingRow.sponsorship` derivation from posting text — that is P1's scope (`p1-acquire.txt`), confirmed
  by reading its dispatch before starting.
- The original TARGET line for U12 was "sponsorship in the assessment" only; the exclusion-labeling of
  non-selected candidates was added afterward per the coordinator's explicit mid-task decision (see ASKED)
  and is scoped exactly as that decision specified — not a broader reinterpretation of U12.
- No `git add`/`git commit` — left for the coordinator to verify and commit, per task instructions.

## ACCEPTANCE

`uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_assess_model_policy.py tests/behaviors/scout_find_jobs/test_assess_contract.py tests/behaviors/scout_find_jobs/test_run_seam.py -q`
→ **28 passed** (18 pre-existing + 10 new: 9 in `test_assess_model_policy.py`, 1 in `test_run_seam.py`,
`test_assess_contract.py` unchanged at 4).
