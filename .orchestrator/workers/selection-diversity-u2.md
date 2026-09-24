# selection-diversity-u2 — worker report

Task: 0.1.8.1 live-UAT ticket B2 (selection quality) and open UAT U2
(find-jobs resolves the literal model-target name "codex_cli").

READ: `.orchestrator/workers/specs/scout-uat-0181-bugs.brief.txt` (B2),
`.orchestrator/workers/specs/scout-run-command.brief.txt` addendum (U2),
`src/gigai/scout/proposal_execution.py`, `src/gigai/scout/find_jobs/contracts.py`
(`AssessInput`/`SelectedPosting`/`NotAssessedReason`/`PostingRow`),
`src/gigai/scout/find_jobs/filters.py` (`exclusion_reason`, `location_countries`),
`src/gigai/scout/find_jobs/market_acquisition.py` (read-only — the naive
first-N selection loop at `acquire_node`'s row loop, ~line 453-475),
`src/gigai/adapters/factory.py` (`resolve_model_adapter`),
`src/gigai/model_targets.py` (`resolve_model_target`), `src/gigai/config.py`
(`Endpoint`/`ModelTarget`/`Profile`), `src/gigai/setup.py` and `src/gigai/cli.py`
(how `gigai setup` names targets `"{provider}-default"`, e.g. `codex-default`).

EXECUTED: added `src/gigai/scout/find_jobs/selection.py` (new, pure module);
edited `src/gigai/scout/proposal_execution.py` (U2 only); edited
`tests/behaviors/scout_find_jobs/test_assess_model_policy.py` (fixture +
new U2 tests); added `tests/behaviors/scout_find_jobs/test_selection_diversity.py`.
Did not touch `market_acquisition.py`, `contracts.py`, `filters.py`,
`ats_board_clients.py`, `exa_client.py`, `cli.py`, `workpad.py`, `scout_cli.py`,
`present_api.py`, `ui/` — read-only.

## Escalation and scope correction (before writing code)

Traced the sealed-contract chain: `AssessInput.selected_postings` is
validated at construction (`contracts.py::_validate_assess_input` — unique,
`role_match=True`, `len <= selection_cap`) and is built entirely from
acquire's own naive first-N-in-batch-order loop
(`market_acquisition.py:453-475`, no dedupe, no per-company cap — the actual
root cause of the UAT's 5/5 ClickHouse picks). By the time `assess_node`
runs, the sealed `selected_postings` set is already homogeneous; assess can
only look up postings by `normalized_url` in that sealed set (a KeyError
otherwise) and can only demote a selected posting to `not_assessed` — it can
never promote an unselected candidate (Coupang, gen-digital) to fill the gap,
because those never received a sealed `SelectedPosting`/`content_sha256` pin
from acquire. A dedupe/cap/round-robin fix written entirely inside
`proposal_execution.py` could only ever narrow the already-homogeneous 5
down further; it could not deliver company diversity.

Escalated via `orca orchestration ask` before writing code (owned files
listed `market_acquisition.py` as another worker's). Coordinator's answer:
put the diversity **algorithm** in a new pure module I own
(`selection.py`), do not touch `market_acquisition.py`/`contracts.py`
myself, and the coordinator wires it into the acquire selection loop
separately once the acquire-country-filter packet lands. Also: don't add a
new `NotAssessedReason` value now (contract change) — report which label
should show for dropped duplicate/company_cap rows and let the coordinator
decide.

## B2 — `src/gigai/scout/find_jobs/selection.py` (new)

Pure, I/O-free `select_for_assessment(rows, *, cap, per_company=2) ->
SelectionResult(selected, dropped)`. Any object with
`normalized_url`/`company`/`title`/`location`/`published_at` satisfies the
structural `Candidate` protocol (no dependency on `PostingRow` or any
contract dataclass, so it stays independently testable and reusable
wherever it's wired in).

Algorithm (deterministic regardless of input order — verified by a test):

1. **Dedupe**: group by (normalized company, `normalize_title()` — casefold
   + punctuation stripped + whitespace collapsed, so "Senior Software
   Engineer - Cloud Infrastructure" and "senior software engineer, cloud
   infrastructure!" collide, and by same-country-location bucket via
   `filters.location_countries` (reused, not reimplemented) so same
   company+title in different countries is *not* a duplicate. Keeps the
   newest (`published_at` string-sorted; ISO-8601 sorts lexicographically);
   the rest of each group is dropped as `"duplicate"`.
2. **Per-company cap**: among dedupe survivors, keeps at most `per_company`
   (default `DEFAULT_PER_COMPANY_CAP = 2`, a module constant — did not add a
   config field, per the brief's "don't add one without asking") per
   company, newest first; excess dropped as `"company_cap"`.
3. **Round-robin fill**: companies visited in a fixed, deterministic order
   (each company's newest surviving row decides its place, ties broken by
   normalized company name) and filled one-per-company-per-pass up to `cap`.
   Anything left over once `cap` is reached is dropped as `"over_cap"`.

Verified against the UAT shape directly
(`tests/behaviors/scout_find_jobs/test_selection_diversity.py`, built
clickhouse=36/coupang=31/gen-digital=2 fixtures matching the reported
counts, some ClickHouse rows sharing the UAT's exact duplicate title):
cap 5 -> ≤2 per company, all 3 companies represented, no duplicate
normalized titles in the result, every dropped row has a reason, and the
result is order-independent. 11 tests total, including edge cases
(`cap<=0`, cap larger than the whole pool, configurable `per_company`,
same-title-different-country is not deduped).

**What's left for the coordinator**: this module is not wired into
`market_acquisition.py`'s selection loop yet (that edit belongs to whoever
owns that file, per the redirect). Recommended call shape at that
integration point: after computing candidate rows (new/edited, role-matched,
not excluded by `exclusion_reason`), call `select_for_assessment(candidates,
cap=input.selection_cap)` instead of the current first-N walk, and treat its
`dropped` map as the source of not-assessed labels for those rows.

**Reason-label recommendation** (no enum change made): `"duplicate"` maps
1:1 onto the existing `NotAssessedReason.DUPLICATE` (already defined,
currently unused anywhere in scout — safe, additive-free reuse).
`"company_cap"` and `"over_cap"` (this module's) both currently have no
distinct existing enum value other than `NotAssessedReason.OVER_CAP` — my
recommendation is to fold `"company_cap"` into `OVER_CAP` when wiring this
in (both mean "an otherwise-eligible row didn't fit under the cap"; a
UI/API consumer likely doesn't need to distinguish "over the global cap"
from "over this company's share of the cap"). If the coordinator wants that
distinction visible, a new `NotAssessedReason.COMPANY_CAP = "company_cap"`
value is additive (no existing schema file constrains this StrEnum; grepped
for a `.schema.json` referencing `not_assessed`/`over_cap` and found none) —
flagging per the brief's "ask before a contract change" rule rather than
adding it myself.

## U2 — `src/gigai/scout/proposal_execution.py`

Root cause confirmed exactly at the reported location: `assess_node`'s
`adapter_target = target if isinstance(target, str) else model_target`
(old code) was dead — `target` is typed `Path | None` in `assess_node`'s
signature, so `isinstance(target, str)` was always `False`, and
`adapter_target` always fell through to the raw sealed enum value
(`"codex_cli"`, `"ollama_local"`, `"openrouter_api"`). `resolve_model_adapter`
→ `resolve_model_target` looks a target up by its own configured `.name`
(`config.model_targets`), and `gigai setup` always names targets
`"{provider}-default"` (`codex-default`, `claude-default`, ...), so the
literal enum string never matched — exactly the operator's workaround
(`--model-target "codex_cli=codex:default"` renames a target to the literal
enum string to force a match).

Fix: new `_resolve_configured_target_name_for_adapter(config, adapter_kind)`
maps the sealed enum's adapter kind to the name of the one configured,
*enabled* target whose endpoint's `.adapter` equals that kind — through
`config.endpoints`/`config.model_targets`, not a hard-coded string list, so
it keeps working whatever name setup or the operator gave the target.
Fails loudly (`ScoutProposalExecutionError("model_target_unavailable", ...)`,
naming `gigai setup`/`--model-target` as the fix) when zero targets match,
and also when more than one target uses the same adapter kind (ambiguous —
no silent fallback to another provider, per the existing rule; the caller
must disable all but one). `assess_node` now calls this before
`resolve_model_adapter` instead of passing the enum value straight through.

Also cleaned up an adjacent dead branch in the same function: the `root =
Path(target) if isinstance(target, (Path, str)) and not isinstance(target,
str) else ...` line has an always-true-implies-false `str` sub-condition
(same root cause: `target`'s type is `Path | None`, `str` is unreachable);
simplified to `Path(target) if isinstance(target, Path) else ...` with
identical runtime behavior — verified via the full existing test suite
passing unchanged.

`tests/behaviors/scout_find_jobs/test_assess_model_policy.py`'s
`_assess_fixture` previously only configured an `offline-default`
(deterministic) target, so every existing test exercising `assess_node`
worked only because the test itself monkeypatches `resolve_model_adapter`
directly (bypassing real resolution entirely). Added a real
`ollama_local`-adapter endpoint/target (`ollama` / `ollama-default`) to the
fixture's config so the new pre-check has something to actually resolve —
this makes the fixture more faithful to a real `gigai setup`, not looser.

New tests added: `_resolve_configured_target_name_for_adapter` resolves a
setup-named target (`codex-default` for `codex_cli`); fails loudly with no
match; fails loudly with an ambiguous match (two targets, same adapter);
ignores a disabled target when picking among duplicates; and an
`assess_node` end-to-end test asserting the value actually passed into
`resolve_model_adapter` is the configured target's own name
(`"ollama-default"`), never the literal enum string.

## Results

- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_selection_diversity.py tests/behaviors/scout_find_jobs/test_assess_contract.py tests/behaviors/scout_find_jobs/test_assess_model_policy.py -q` — **35 passed**.
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_m1_end_to_end.py -q -m "not live"` — **1 xfailed** (pre-existing, documented: "0.1.8.1: the offline M1 end-to-end run regressed after the acquire/assess changes ... Operator decision: ship 0.1.8.1 and handle it in v0.1.9." — not caused by this change; reproduced before touching any file).
- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/ -q -m "not live"` (full directory, broader regression sweep) — **531 passed, 1 xfailed** (same pre-existing xfail).
- `make unit-tests` — **900 passed, 1282 deselected**.

## Scope / exclusions honored

- Owned files only: `selection.py` (new), `proposal_execution.py` (U2 only),
  the four named test files (only touched three — `test_run_seam.py` wasn't
  needed), this report.
- No schema/storage migration made; the one possible additive enum value
  (`NotAssessedReason.COMPANY_CAP`) was flagged, not added.
- No live provider calls.
- No `git add`/`commit`/`stash`/`reset`/`clean`.
- Focused tests only, plus the two acceptance-listed broader runs
  (`test_m1_end_to_end.py`, `make unit-tests`) and one extra full-directory
  sweep for regression confidence (all offline/mocked, no live network).

## What's left

- `selection.py`'s `select_for_assessment` is not yet wired into
  `market_acquisition.py`'s selection loop — that edit is the
  acquire-country-filter worker's / coordinator's, per the redirect.
- Coordinator decision needed: fold `"company_cap"`/`"over_cap"` (this
  module's drop reasons) onto the existing `NotAssessedReason.OVER_CAP`, or
  add a new additive `COMPANY_CAP` enum value for a finer-grained UI label.
- U2 is otherwise complete: no CLI/setup changes were needed since the fix
  is entirely in how `assess_node` resolves the sealed enum against
  whatever configuration already exists.
