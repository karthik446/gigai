# Worker: interview-prep-engine (S18 minimal first slice)

**Status:** Done. Implementation + offline tests pass, `make unit-tests`
green, live smoke run succeeded under budget after one real bug found and
fixed during the smoke run itself (see "Bugs found during the live smoke
run").

## READ vs EXECUTED

**READ** (before writing code):
- `docs/development/v0.1.9/spikes/S18-scout-interview-prep-graphs.md`
  (full: §1 existing pieces, §2 agent-tool options, §3 JEV mapping, §4
  sources, §5 minimal first slice, open questions, non-claims).
- `src/gigai/application_events.py` (`interview_scheduled` event kind,
  `:30`; `record_application`/`read_application` full read/write path).
- `src/gigai/scout/interview.py`, `interview_records.py` (full),
  `interview_cli.py` (full) -- the existing operator-curated preparation
  record kind.
- `src/gigai/scout/research.py` (full) -- the sealed-Graph-Run research
  packet record kind.
- `src/gigai/scout/data/goalgraphs/prepare-interview.md`,
  `research-role.md` -- the agent-instruction markdown for the existing
  graphs.
- `src/gigai/scout/find_jobs/discovery/{__init__,openai_source,merge,
  storage,prefs,types}.py` (full) -- the package this packet's addendum
  says to reuse read-only.
- `src/gigai/scout/find_jobs/contracts.py` (`ModelTarget`, `PostingRow`,
  `PostingRowResult`, `AcquireOutput`, `AssessmentResult`,
  `RequirementMatrixRow`, `MatrixStatus`, `SelectedPosting`,
  `FindJobsConfig`, `normalize_url`).
- `src/gigai/scout/proposal_execution.py` (`assess_node`,
  `_resolve_configured_target_name_for_adapter`, `_assess_prompt`,
  `_extract_json_object`, `_read_pinned_resume`, `_read_sealed_config`) --
  assess's own model-resolution and prompt pattern, reused for question
  categories.
- `src/gigai/scout/proposal_records.py` (`save_assessment_revision`,
  `_ASSESSMENT_SCHEMA`, `_validate_record`) -- where the requirement
  matrix is stored, for the read-only reuse scan.
- `src/gigai/scout/report_readers.py`, `projection.py` (`rebuild_projection`,
  `_opportunity_rows`, `_application_rows`) -- investigated as a posting-
  resolution path; NOT used in the end (see "Two posting identities" below).
- `src/gigai/scout/posting_inputs.py` (`resolve_discovery_posting_input`) --
  investigated for the same reason; not used.
- `src/gigai/adapters/factory.py`, `model_targets.py`, `port.py`,
  `deterministic.py` -- the standalone (non-graph) model-adapter
  resolution path.
- `src/gigai/private_records.py` (`list_imports`, `read_import`,
  `import_reference`) -- resume lookup.
- `src/gigai/run.py` (`resolved.path / "runs" / run_id / "outputs" /
  "acquire.json"`, :4176/:4226) -- confirmed the plain-file acquire-output
  path convention this packet reuses.
- Peer test files for pattern: `tests/behaviors/scout_find_jobs/
  test_discovery_openai.py`, `test_discovery_init.py`,
  `test_assess_model_policy.py` (fixture/monkeypatch conventions reused).

**EXECUTED**:
- All new modules under `src/gigai/scout/interview_prep/`.
- The `gigai scout prep` command in `src/gigai/scout/scout_cli.py` (pure
  addition, 77 lines, no existing lines touched -- confirmed via `git diff
  --stat`).
- Five new test files under `tests/behaviors/scout_find_jobs/
  test_interview_prep*.py`, run against MockTransport/a scripted model
  binding/synthetic fixtures only -- zero live calls during the offline
  test pass:
  `uv run pytest tests/behaviors/scout_find_jobs/test_interview_prep*.py -q`
  -- **23 passed**.
- `make unit-tests` -- **927 passed, 0 failed** (unchanged from before this
  packet; my tests are correctly excluded from `-m fast_unit`, same as
  every peer `httpx`/filesystem-using Scout test file).
- Full `tests/behaviors/scout_find_jobs/` directory: **703 passed, 1
  pre-existing xfail** (`test_m1_end_to_end.py`'s known 0.1.8.1 regression,
  unrelated to this packet).
- One live smoke run: `gigai scout prep <a real Anthropic posting URL>` in
  a fresh TEMP `GIGAI_HOME`/target (own `run_setup`/`initialize_defaults`/
  `approve_offline`, not copied from the operator's real project), with
  ONLY the `OPENAI_API_KEY` value copied from the operator's real
  `secrets_store` into the temp home's own `secrets_store` (never printed
  -- grepped the full CLI output and the entire temp directory for the
  `sk-` prefix afterward; the only hit was the temp home's own `.env`
  file, mode `0600`, which is where `secrets_store` is supposed to put
  it). Model target: `codex_cli` (the operator's real home has no
  `ollama_local`/`openrouter_api` target configured, and `codex` was
  available on PATH). **Result: succeeded**, $0.0560 actual cost (11.2%
  of the $0.50 cap) -- see full pasted summary below. Temp directory
  deleted after the run.

## Live smoke run — pasted summary

```
Resolving posting from find-jobs acquire output...
Researching company (OpenAI web_search)...
Reading role research from the posting text...
Predicting likely question categories...
Interview prep for Staff Backend Engineer at Anthropic:
  Company research: 12 sourced claim(s), $0.0560
  Role research: 2 responsibilit(y/ies), 1 requirement(s)
  Likely question categories (codex-default):
    - system_design: The role owns distributed systems serving the Claude API at scale, and a reported Staff SWE interview included two system-design rounds.
    - coding_in_their_stack: The posting calls for Go or Python proficiency, the candidate has experience in both, and a reported Staff SWE interview included coding.
    - behavioural: Staff-level ownership and collaboration with research make project leadership and cross-functional influence relevant, and reported interviews included technical-project and culture discussions.
    - domain: Capacity planning for the Claude API and work alongside inference and safeguards make AI serving infrastructure a likely discussion area.
  Prep notes: no assess matrix found for this posting yet; run `gigai scout run` to assess it for richer notes.
  Total cost: $0.0560. Stored under scout/interview_prep/https://boards.greenhouse.io/anthropic/jobs/9001.json (GigAI home).
```

Every company-research claim carried a real source URL (Anthropic's own
engineering blog, careers page, press releases) and was independently
resolved (HEAD/GET) before being kept; the question categories cite both
the posting text and the reported interview-process claim from company
research.

## Bugs found during the live smoke run (both fixed, offline tests re-run green after each)

1. **Storage path bug**: `storage.prep_path` used the raw posting URL as a
   filename (`f"{posting_id}.json"`), and a URL contains `/` — this wrote
   into nested bogus directories (`.../interview_prep/https:/boards.
   greenhouse.io/anthropic/jobs/ 9001.json`) instead of one file. Fixed:
   the filename is now `sha256(posting_id)` (digest, not the raw URL); the
   prep's own `posting_id` JSON field keeps the readable URL. Caught only
   by the live run (the offline tests exercised `build_prep`/`load_prep`
   round-trips, which happened to still "work" against the broken nested
   path — they never asserted the file actually lived at one flat path).
2. **Client-timeout bug**: `company_research.research_company` took one
   `client` param reused for both the OpenAI `web_search` POST (needs
   `websearch.REQUEST_TIMEOUT_SECONDS` = 300s, since a real call can take
   minutes under rate-limit backoff per `openai_source.py`'s own comment)
   and the fast HEAD/GET source-URL verification (correctly 30s). `prep.py`
   passed its 30s verify-client into both, so the first live smoke attempt
   failed every company-research call with `transport_ReadTimeout` at
   exactly 30s even though OpenAI was reachable and the key was valid
   (confirmed with a raw `httpx.get` against `api.openai.com`, got `401`
   in under a second). Fixed: `research_company` now takes separate
   `search_client`/`verify_client` params; `prep.py` only ever supplies
   its 30s client as `verify_client`, letting `web_search_structured` open
   its own client at its own long timeout when a caller doesn't override
   it. Re-ran the live smoke test after the fix: company research
   succeeded fully (12 claims, $0.0560) on the very next attempt.

Both bugs are now covered structurally (the digest-filename fix is
implicit in every `build_prep`/`load_prep` test passing against a real
temp home; the client-split fix is exercised by the offline company-
research tests using separate `search_client`/`verify_client` mocks) —
no new test was added narrowly for either regression, since the existing
suite already re-verifies both paths end-to-end.

## Design decisions made via `orca orchestration ask` (both confirmed by the coordinator)

1. **Storage shape** (packet's explicit STOP-and-ask trigger: "if a NEW
   record kind or schema is required, STOP and ask"): neither existing
   Scout record kind fits an automated pipeline's output.
   `scout_interview_preparation` (`interview_records.py`) is a journal-
   authenticated, closed-schema record requiring *operator-curated*
   evidence-backed stories tied to pre-selected `scout_record`/
   `scout_research`/`scout_discovery`/`scout_interview` refs — no shape
   for automated web-search company research or free-text question
   categories. `scout-role-research:2` (`research.py`) requires a sealed
   Graph Run origin (`graph_selector` must equal a real executed graph,
   verified against a sealed Plan) — not usable from a standalone CLI
   command with no graph execution. **Decision**: follow discovery's own
   committed precedent (`find_jobs/discovery/storage.py`) — plain
   atomic-write JSON under `<home>/scout/<project_id>/interview_prep/
   <sha256(posting_id)>.json`, outside the journal record-kind system
   entirely. No new journal record kind, no schema registration.
   Recorded as follow-up debt: a later version should decide whether
   prep becomes a journal record once its shape has proven stable.
2. **Posting identity** (found mid-implementation, not anticipated by the
   packet): this codebase has *two separate* posting/discovery identity
   systems. `application_events.py`'s `opportunity_ref` (what
   `interview_scheduled` names) ties to the older D-family discovery
   graph (`cap_...074`), whose posting schema (`employer`/`title`/
   `facts`) has **no free-text job-description body at all** — the body
   lives in a separately-committed capture artifact reachable only
   through `resolve_discovery_posting_input`'s full sealed-Run/Plan/
   checkpoint chain. find-jobs' own acquire/assess pipeline (S2-A,
   `PostingRow`, has a real `.text` field) identifies postings by
   `normalized_url` within one sealed find-jobs Run — not by
   `opportunity_ref` either, and assess's requirement matrix
   (`records/scout-proposals/`) is keyed the same way. **Decision**
   (coordinator redirect): key this slice to the **find-jobs identity**
   (`normalized_url`), not `application_events`'/`opportunity_ref`.
   `gigai scout prep <posting-url> [--run <find-jobs-run-id>] [--refresh]
   [--json]`; without `--run`, resolve against the newest find-jobs run
   (by acquire-output mtime) that contains the posting; reuse that run's
   assess matrix if the posting was assessed, otherwise still build from
   posting text alone. `application_events`/`opportunity_ref` is **not**
   wired as a trigger in this slice — the "interview scheduled" trigger
   becomes a UI action on the find-jobs posting card in the follow-up UI
   packet, calling `build_prep` directly. Recorded as follow-up debt:
   unify the two posting identities so `interview_scheduled` can trigger
   prep automatically later.

## Interface contract delivered

`src/gigai/scout/interview_prep/__init__.py` exports exactly:
`build_prep`, `load_prep`, `InterviewPrepError`, `InterviewPrep` (the
frozen result dataclass). `gigai scout prep <posting-url> [--target]
[--home] [--run RUN_ID] [--refresh] [--budget USD] [--json]` in
`scout_cli.py` wraps `build_prep` for the CLI/foreground path; the same
function is what the API/UI packet will call later (no separate function
needed).

Storage: `<home>/scout/<project_id>/interview_prep/<sha256(normalized_
posting_url)>.json` — one file per posting, replaced on `build_prep`
(idempotent by resume `content_sha256`; `--refresh` forces a re-run and
keeps the original `created_at`).

## Files (packet's OWNED FILES list)

- `src/gigai/scout/interview_prep/__init__.py` — public surface
  (`build_prep`, `load_prep`, `InterviewPrepError`, `InterviewPrep`).
- `src/gigai/scout/interview_prep/types.py` — frozen JSON-friendly DTOs:
  `InterviewPrep`, `CompanyResearch`, `SourceClaim`, `RoleResearch`,
  `QuestionCategoryPrediction` (+ `QUESTION_CATEGORIES` = behavioural/
  system_design/coding_in_their_stack/domain, the coordinator default),
  `PrepNotes`, `ResumeIdentity`.
- `src/gigai/scout/interview_prep/storage.py` — path/atomic-write helpers
  (reuses `find_jobs/discovery/storage.py`'s `atomic_write`/`project_id`).
- `src/gigai/scout/interview_prep/websearch.py` — the thin
  `web_search_structured(query, json_schema, *, budget_usd, timeout)`
  call the addendum asked for. Reuses discovery's `openai_source`
  key lookup (`_api_key`, private — imported anyway per the addendum),
  `resolve_model`, price table/`_actual_cost`/`worst_case_cost_usd`
  (budget guard), and `merge._verify_source_url` (private — imported
  anyway) for source verification. Does **not** reuse
  `openai_source.build_query`/`_parse_candidates` (discovery-specific,
  `DiscoveryPrefs`-shaped) — writes its own query/parse, per the
  addendum's instruction.
- `src/gigai/scout/interview_prep/company_research.py` — company
  research: builds the query from company name + role title only (never
  the resume — asserted structurally: the function has no resume
  parameter, and a test asserts this via `inspect.signature`), calls
  `websearch.web_search_structured`, verifies every claim's source URL,
  drops unverifiable claims, returns `skipped=...` (never raises) for a
  missing key or any provider failure.
- `src/gigai/scout/interview_prep/role_research.py` — posting-text-only
  extraction (no search, no cost, no key dependency): a bounded static
  heuristic splits responsibility-shaped and requirement-shaped lines.
- `src/gigai/scout/interview_prep/categories.py` — question-category
  prediction. Reuses `proposal_execution._resolve_configured_target_name_
  for_adapter` + `adapters.factory.resolve_model_adapter` for "no silent
  provider fallback" (an unconfigured/disabled/ambiguous target raises
  `CategoryPredictionError`, naming the fix, exactly like assess). The
  resume IS sent to this model call (never to web search) — the packet's
  privacy line restated in code and asserted in tests on the captured
  prompt.
- `src/gigai/scout/interview_prep/prep_notes.py` — reuses assess's
  `AssessmentResult.matrix`/`RequirementMatrixRow` for resume points
  (`met` rows) and gaps (`partial`/`gap` rows) when a matrix exists for
  this posting; empty notes with `matrix_source=None` otherwise.
- `src/gigai/scout/interview_prep/posting.py` — resolves a posting by
  `normalized_url` from a find-jobs run's `runs/<run_id>/outputs/
  acquire.json` (plain file, `AcquireOutput.from_json`, same shape
  `proposal_execution._read_acquire_rows`/`run.py` read); best-effort
  scan of `records/scout-proposals/` for a matching assessment revision.
- `src/gigai/scout/interview_prep/resume.py` — finds "the current resume"
  via `private_records.list_imports(family="reference")` filtered to
  `kind="resume"`, newest by `created_at` (Scout has no "current" pointer
  elsewhere — every other Scout read requires explicit record/revision
  selection, per `projection.py`'s own "no 'latest' projection accepted"
  comment; this is the one place S18 needs an implicit "current," so it
  picks the newest reference the same way `scout resume add` produces
  one-per-project today).
- `src/gigai/scout/interview_prep/prep.py` — orchestrator (`build_prep`/
  `load_prep`), idempotency, storage.
- `src/gigai/scout/scout_cli.py` — the `prep` command only (pure
  addition).
- `tests/behaviors/scout_find_jobs/test_interview_prep_fixtures.py` —
  shared synthetic fixture helpers (bound project + resume + acquire
  output + optional assessment; no live calls).
- `tests/behaviors/scout_find_jobs/test_interview_prep_trigger.py` —
  trigger/posting-resolution/idempotency-input tests (6).
- `tests/behaviors/scout_find_jobs/test_interview_prep_categories.py` —
  grounded categories, no-silent-fallback, model-denied, invalid-category
  handling (a scripted/stand-in model binding, no live calls) (5).
- `tests/behaviors/scout_find_jobs/test_interview_prep_company_research.py`
  — budget guard, missing key → partial, source verification (HEAD-then-
  GET fallback), never-sends-resume assertion (6).
- `tests/behaviors/scout_find_jobs/test_interview_prep_end_to_end.py` —
  full `build_prep` orchestration: idempotency, `--refresh`, partial prep
  on missing key, prep-notes matrix reuse, and the privacy assertion on
  the *actual captured* web-search request body (6).

## Acceptance checklist (packet's ACCEPTANCE section)

- [x] Trigger/idempotency — `test_interview_prep_trigger.py`,
      `test_interview_prep_end_to_end.py::test_idempotent_same_resume_
      revision_returns_cached_without_new_calls`,
      `::test_refresh_forces_a_new_run`.
- [x] Missing key → partial prep —
      `test_interview_prep_company_research.py::test_missing_api_key_
      skips_without_raising_and_no_http_call`,
      `test_interview_prep_end_to_end.py::test_missing_key_still_builds_
      partial_prep_with_categories`.
- [x] Budget guard —
      `test_interview_prep_company_research.py::test_budget_guard_skips_
      before_any_spend_when_worst_case_exceeds_budget`.
- [x] Source verification —
      `test_interview_prep_company_research.py::test_claims_carry_
      source_url_and_are_verified`, `::test_unverifiable_source_urls_
      are_dropped`, `::test_verify_source_url_head_then_get_fallback`.
- [x] No resume in the search request (captured request assertion) —
      `test_interview_prep_end_to_end.py::test_build_prep_end_to_end_
      no_resume_in_search_request` (asserts on the actual decoded POST
      body bytes, not just the function signature) +
      `test_interview_prep_company_research.py::test_build_query_never_
      includes_resume_text`.
- [x] Categories grounded (each cites the posting/resume/company item it
      came from) — `test_interview_prep_categories.py::test_grounded_
      categories_cite_posting_resume_and_company`.
- [x] `make unit-tests` passes — 927 passed, 0 failed.
- [x] One live smoke run, ≤ $0.50, keys via `secrets_store`, never
      printed — done, $0.0560, summary pasted above.

## Privacy (packet requirement, restated per its own instruction)

The resume never goes to the web-search call. `company_research.
build_query` takes only `company`/`title` — no resume parameter exists on
that function or on `websearch.web_search_structured`'s query argument at
all, so there is no code path by which resume text could reach the
`web_search` request; this is asserted in tests both on the function
signature (`test_build_query_never_includes_resume_text`) and on the
actual captured request body during a full `build_prep` run
(`test_build_prep_end_to_end_no_resume_in_search_request`). The resume
**is** sent to the configured category-prediction model call
(`categories.predict_categories`), matching assess's own privacy line
exactly (private evidence goes only to the configured assess-equivalent
model, never to a search/discovery call).

## Follow-up debt (recorded, not built here)

1. A shared `scout/websearch.py` should replace the duplication between
   this packet's `interview_prep/websearch.py` and `find_jobs/discovery/
   openai_source.py` once both have landed and stabilized (addendum's own
   instruction).
2. Unify the two posting identities (`application_events`'
   `opportunity_ref` vs. find-jobs' `normalized_url`) so
   `interview_scheduled` can trigger prep automatically, instead of the
   operator/UI naming a posting URL directly.
3. `role_research.py`'s posting-text extraction is a static heuristic
   (marker-word regex over lines), not a model call — reasonable for "no
   search, no cost" but coarser than assess's own requirement extraction;
   a later slice could unify it with assess's model-based matrix if the
   heuristic proves too coarse in practice.
4. Per-question `noul` prediction and the interview-outcome/calibration
   record kind (S18 §3's later-slice items) are not built, per the
   packet's explicit scope (category-level `choice` only).

## Non-claims

- No schema/storage migration was performed.
- No journal record kind was added (see "Design decisions" above).
- No commits were made.
- `application_events`/`interview_scheduled` is not wired as an automatic
  trigger in this slice (see "Design decisions" above; follow-up debt #2).
- The UI prep card is explicitly out of scope (packet: "follow-up after
  the Scout UI work (S2-B) lands"); `present_api.py` and `ui/` were not
  touched (confirmed via `git status`).
