# P2-FREEZE-04 Amendment 02 — functional `find-jobs` graph and packet freeze

**Recorded:** 2026-09-22 · **Status:** documentation-only contract freeze
**Amends:** Amendment 01 and the P3 integration trace. They retain the earlier
graph diagnosis and trace; this amendment freezes the corrected implementation seams.

## Intent and boundary

The proof is one sealed linear traversal:
`acquire ──COMPLETE──▶ assess ──COMPLETE──▶ present`. Acquisition visibility is
independent of assessment failure; assessment consumes saved postings and
pinned private evidence; presentation is a derived view. Fixed graph
`find-jobs:functional:1`: exactly those two automatic edges, no branch/custom
outcome, one callable binding per `(graph_id, graph_version, goal_slug)`. This
remains a planned implementation contract, not runtime/live/installed/release
acceptance.

## D10 — operator scope decision (2026-09-22)

> Scout is a Gig, the first of many (find product, track prices, stock
> explainer). v0.1.8's function is getting Scout out and working ASAP. More
> gigs and GigAI foundations wherever needed come in 0.2.0.

Applied as milestone **M1 "usable"** ahead of the installed-wheel/release
gates, plus a cut list and 0.2.0 generalization ledger (both in the roadmap).
D1-D9 remain the functional contract; D10 governs sequencing/cut lines only.

## Decisions — operator intent (2026-09-22)

| ID | Decision |
| --- | --- |
| D1 | Acquire by Exa query search, never hand-picked companies; use `EXA_API_KEY` only from the environment, publish-date filtering, merged role queries, and domains `boards.greenhouse.io`, `job-boards.greenhouse.io`, `jobs.lever.co`, and `jobs.ashbyhq.com`. HiringCafe sitemaps are deferred past M1 (unproven board-token yield; see roadmap cut list). A discovered Greenhouse/Lever/Ashby board token may create the sole new persisted watchlist entry with first-seen provenance. |
| D2 | Per-run `model_target` is explicit: configured `ollama_local` by default, or configured `codex_cli` (Luna) / `openrouter_api`; missing credentials fail loudly and there is no silent fallback. Producer/model identity is in the receipt (`src/gigai/adapters/factory.py:61-124`). |
| D3 | A Run button performs one traversal through a localhost-only Python API and Vite/React UI; it shows acquisition rows/failures, assessment matrix/suggestions/questions, and per-node status. Cron is not part of this proof. |
| D4 | Preserve P3 defaults: new-run continuation, additive node receipts/details, fixed graph binding, aggregate status precedence, and explicit unavailable usage. See "Node receipt and aggregate status" below for the exact fields and precedence this restores. |
| D5 | Before Run, UI shows sources, queries, role filter, assess cap, and model target. Confirm sends exactly `{schema_version, kind, action, actor, source, invocation_id, occurrence_id}` with values `1.0`, `operator_run_consent`, `run`, `{kind: operator, id: local-user}`, and source `direct_local_ui_confirm`; no extra envelope fields. Integration adds this source to `run.py` and accepts it only from the `127.0.0.1` API after a peer loopback check. It is redeemed before allocation like the CLI path; `direct_cli_confirm` and its CLI flow remain unchanged. |
| D6 | Assess only new postings (unseen URL or edited content hash) that pass the role filter and fit cap `N` (default 10); the rest are `not assessed` with a reason. The Run input seals `N` + the selection rule only, since it is sealed before `acquire` runs and cannot name concrete identities; `acquire` selects candidates, and the resolved selected-posting identities go in `AssessInput` and the assess node receipt. |
| D7 | At run start, Integration resolves the newest committed `resume`, refuses before `assess` when none exists, and shows the selected identity in UI. The sealed input pins `record_id`, `revision_id`, and content digest; no latest/unpinned lookup occurs inside B. |
| D8 | Run workflow is click-only for now. Hourly polling and the `4h → 12h → 24h` zero-result backoff are deferred to the cron follow-up; the backoff is a recorded non-goal, not a graph contract. |
| D9 | Operator-authored config is `<target_root>/find-jobs.json`, alongside the bound project files. It contains roles, merged queries, location/remote, `published_after`, source toggles, default assess cap, and default model target. API reads and UI displays it; each Run seals its snapshot and digest in Run input. This is config, not a persisted record type. |
| D10 | Scope decision, recorded verbatim above. Governs the roadmap's M1 milestone, cut list, and 0.2.0 generalization ledger. |
| D11 | Approach A (operator-chosen, 2026-09-23): a minimal G14 scheduler extension admits real node execution for this sealed graph only, via a node registry (see "Real node execution" below). No bypass of the Run/graph/consent path for M1. |

## Node receipt and aggregate status (R1: restores dropped Revision-0 content)

D4 is additive to the existing goal receipt/usage shapes: `_goal_front_matter`
carries `usage`/`actor` (`run.py:3564-3584`); `_ZERO_USAGE` distinguishes
`cost_status: "not_applicable"` from a measured zero (`run.py:129-136`);
`_usage` (`model_execution.py:463-481`) returns `"not_applicable"` with no
invocation result, else `result.cost_status`. Two new fields: `producer`
`{callable, version, actor, model_target, adapter}` (existing code records
actor/adapter separately at `run.py:984,1073,2106`; this nests them per goal),
and `usage.measured: bool` (real token counts vs. a `_ZERO_USAGE` placeholder —
distinct from `cost_status`, which neither current shape carries).

**Aggregate status precedence (real bug).** `_terminal_status`
(`run.py:3491-3496`) checks only `failed` then `blocked`, defaulting
`running`/`pending` to `succeeded`. `_run_rows` (`scout_report_readers.py:315-337`)
independently sets `succeeded` the moment `"complete"` appears in any goal's
state, checked before `"failed"`/`"running"`. `find-jobs:functional:1` fixes
both with one precedence: `interrupted > failed > blocked > cancelled >
running > pending > succeeded`; only all-required-`complete` yields `succeeded`.
I-2 updates `_terminal_status`; C updates `_run_rows`.

## Real node execution (D11/T1: the scheduler must call real nodes)

Today's scheduler rejects every executor except two placeholders:
`_validate_scheduler_policy` (`run.py:3438-3460`) requires `capability` in
`{"gigai.offline", "gigai.deterministic"}` and `effects == ["write_workpad"]`
exactly, for every graph; `_execute_goal` (`run.py:3546-3561`) never calls a
node — it writes a fixed `gigai-offline-ok:<goal_id>` string. Revision 2's two
invented network-effect names were also not in the real effect enum
(`common.schema.json:148-157`: `read_target, write_workpad, write_target,
network_read, external_write, credential_use`); this revision replaces them
with `network_read`/`credential_use` below.

New generic module `src/gigai/graph_node_registry.py` (I-2) maps a sealed
`(graph_id, graph_version, goal_slug)` to an approved callable plus its
capability/effects. `_validate_scheduler_policy` additionally admits a
`local_capability` executor registered for **this sealed graph only** —
`scout.find_jobs.acquire`, `scout.find_jobs.assess`, `scout.find_jobs.present`
— with effects: acquire `["network_read", "credential_use", "write_workpad"]`;
assess the same for a hosted target, else `["write_workpad"]`; present
`["write_workpad"]`. Every other graph keeps today's rule unchanged.
`_execute_goal` dispatches to the registered callable with `NodeContext`,
records result/receipt/failure, keeps the target-change interrupt check
(`run.py:3557`), and fails closed on an unregistered capability or undeclared
effect.

I-1 (`scout_materialization.py`) sets the Scout goals' executors/effects above
and raises the graph-set descriptor ceiling so `graph_set.py:238-262` (each
goal's `effects` ⊆ `descriptor.effect_policy`; `executor.capability` ⊆
`descriptor.capability_requirements`) still accepts them: today's ceiling is
`effect_policy: ["write_workpad"]`, `capability_requirements: ["gigai.offline"]`
(`scout_materialization.py:364-365`) and `shared_policy: {"effects":
["write_workpad"], "required_capability_ids": ["gigai.offline"], ...}`
(`:395`); both widen to the three `scout.find_jobs.*` capabilities and the
enum effects above, for this graph only.

I-2 owns `run.py`, `graph_node_registry.py`, `run-details.schema.json`, and
`test_run_seam.py`; the test proves a registered callable is actually reached
(not the placeholder string) and an unregistered capability/undeclared effect
is refused. I-3 now depends on I-2 as well as A-6/B-2/C-2.

## Shared contracts and sealed effects

Integration lands **first (wave 1a)**: new `src/gigai/scout_find_jobs_contracts.py`
defines frozen dataclasses with validators for `NodeContext`, `AcquireInput`,
`AcquireOutput`, `AssessInput`, `AssessOutput`, `PresentInput`, `PresentOutput`,
`WatchlistEntry`, and `FindJobsConfig`. `FindJobsConfig` has exactly the D9
fields; `AssessInput` has selected posting refs/digests (D6), cap/selection
reasons, the pinned resume triple (D7), target, and answer-association version.
All other packets import this module and must not invent duplicate DTOs (test:
`tests/behaviors/scout_find_jobs/test_contracts.py`, owned by Integration).
I-0's same pass also freezes: the A-1..A-5 client call signatures and
return-row shapes (so A-6 orchestrates without waiting on client internals),
and the present API's exact routes plus request/response JSON (so C-4 builds
against a fixed shape without waiting on C-2's internals) — both in
`scout_find_jobs_contracts.py`/`test_contracts.py` alongside the dataclasses.

`NodeContext` seals run/project/Gig identity, digests, target observation, and
redeemed consent; Acquire returns batch/progress refs, URL-set diff, watchlist
refs; Assess returns proposal revision refs, the requirements × resume matrix
(`met|partial|gap`), invocation/usage, producer identity; Present returns a
rebuildable projection/API payload with no tracking mutation. Existing patterns:
`scout_acquisition_records.py:56-99,273-355,378-464`; `scout_proposals.py:366-485`.

Effects are narrow, graph-bound, and drawn from the existing enum (see "Real
node execution" above, D11): Acquire declares `network_read`, `credential_use`,
`write_workpad`; Present declares `write_workpad`; Assess adds `network_read`
and `credential_use` only in this sealed graph and only when `model_target` is
non-local, else `write_workpad` alone. D5 consent covers both network-effect
cases. B, in `src/gigai/scout_proposal_execution.py`, builds `InvocationPolicy`
(current caller constructs it at `:206-223`); B sets `network_allowed` only
after verifying the sealed effect and non-local target, with `offline=False`.
The model boundary defines `network_allowed` separately from local permission
at `src/gigai/model_execution.py:74-86` and denies a non-local adapter when
false at `src/gigai/model_execution.py:241-246`.

The UI consent envelope follows the existing validator's closed field set and
actor checks at `src/gigai/run.py:2187-2213`; redeemed consent is recorded before
Run ID allocation at `src/gigai/run.py:280-303`. The API must bind exactly
`127.0.0.1` and reject non-loopback peers, following the existing loopback server
guard/bind pattern at `src/gigai/proposal_interview.py:627-656,783-804`.

Run input sealing follows the existing request boundary: config is resolved once
and request bytes are pinned before `run_started` at `src/gigai/run.py:218-237`,
then manifest/source refs and the canonical input digest are sealed at
`src/gigai/run.py:2313-2400`. Integration adds the find-jobs config snapshot,
D6's cap/selection-rule (not concrete identities — see D6), and resume triple.

## Resume and project-local config trace

`--kind resume` ingestion is at `src/gigai/cli.py:216-227`; the writer records
`kind`/`created_at`/snapshot bytes at `src/gigai/private_records.py:307-326`.
`list_imports`/`_committed_json` read committed rows (`:193-220,376-392`),
`read_import` selects one (`:395-401`). Integration filters to `kind=resume`,
picks the greatest `created_at`, and resolves the private wrapper via
`list_revisions`/`read_record` (`:463-521`) then `scout_inputs._record_revision`
(`scout_inputs.py:37-56`) — the helper explicitly never picks latest itself
(`scout_inputs.py:1-5`), so newest-selection is Integration's (roadmap I-2); B
gets only the pinned triple/digest. No resume produces a clear pre-assess
refusal.

Project resolution: `.gigai/project.toml` binds at `project_binding.py:22-55`;
`resolve_workpad` returns `target_root` at `workpad.py:254-297,641-691,710-752`.
The API reads `<target_root>/find-jobs.json`, validates against the shared
contract, and seals its digest without creating a record.

## Watchlist storage (R3: gives the new record a home)

New journal record, reusing the `_publish` operation/receipt pattern from
private references (`private_records.py:306-326`: builds a record, calls
`_publish(operation, key, payload, artifacts)`, writes `records/<operation>/<key>`
+ receipt validated against `scout-operation-receipt.schema.json`), paralleling
`records/scout-acquisition/{batch_id}/...` (`scout_acquisition_records.py:155-158,284,288`).
Lands at `records/scout-watchlist/{watchlist_id}.json`, written by A-5
(`scout_watchlist.py`) via `_publish(operation="scout_watchlist_add",
key=f"scout_watchlist:{provider}:{board_token}")` — idempotent per token. JSON:
`{"schema_version": "scout-watchlist:1", "watchlist_id", "provider":
"greenhouse|lever|ashby", "board_token", "company", "state": "active",
"first_seen": {"source_kind": "exa|ats", "source_url", "query_key", "batch_id",
"observed_at"}}`. No API key/body/cursor/assessment state stored; `state`
starts `"active"` (deactivation out of scope for M1).

## Local server entry point, ownership, and freeze authority (T2/T3/T5)

**Server (T3):** C-2 owns the process, entry point `python -m
gigai.scout_present_api` (not a `gigai scout serve` subcommand — that needs
`cli.py`, which no packet owns), fixed loopback port `127.0.0.1:8765`, lifetime
= `yarn dev`'s. C-3's `vite.config.js` proxies `/api` there. Route table frozen
by I-0, at minimum: `GET /api/config` (config+resume preview), `POST /api/run`
(consent + run start), `GET /api/runs/{run_id}` (node states, UI polling), `GET
/api/runs/{run_id}/results` (rows/matrix/failures). Loopback-bind precedent:
`InterviewHTTPServer` refuses non-`127.0.0.1` at `proposal_interview.py:655-656`.

**Ownership (T5):** per-row ownership is authoritative in the roadmap's "Small
packet DAG", not here; this amendment states semantics/decisions (D1-D11), the
roadmap states who owns what file in what order — including the I-2 → I-3
registry edge and the fixtures directory under I-0. Roadmap wins on conflict.

**Normative freeze (T2):** I-0's delivered code (`scout_find_jobs_contracts.py`,
`tests/behaviors/scout_find_jobs/fixtures/*.json`, `test_contracts.py`) is the
normative freeze for DTO fields/types, A-1..A-5 signatures, and present-API
routes/JSON — not this prose. `test_contracts.py` must reject field/type/route
drift. This doc keeps the semantics/invariants (D6 sealing, D7 pinned resume,
watchlist idempotency). **Gate:** coordinator + Terra review I-0's actual
output before any wave-1b dispatch.

Every network client is tested with `httpx.MockTransport` (`pyproject.toml:22-28`;
seam at `adapters/http.py:8-29,47-58`, `test_model_invocation_foundation.py:197-207,238-247`).
Focused tests make no live network calls; a live run is separately consented;
cron/backoff remain deferred per D8.

## Evidence boundary

This freeze claims no implementation, focused-test success, UI usability, cron
replacement, provider/model execution, source permission, installed acceptance,
release readiness, or live freshness. Watchlist is the only new persisted
record; config/run-input snapshots are not record types. Assessment never
changes tracking/application state; tailoring never implies an application event.

**READ:** coordinator/Terra reviews `.orchestrator/reviews/{w0-amendment-02,w0c-roadmap-and-w0r-rev1,w0d-reconcile,terra-w0,terra-w0-triage}.md`;
project instructions/RTK; Amendment 01; P3 trace; `scout_report_readers.py:295-342`;
`run.py:100-145,1600-1630,2187-2213,280-303,218-237,2313-2400,3438-3460,3491-3497,3546-3561,3557,3564-3584,3700-3722,984,1073,2106`;
`schemas/common.schema.json:140-160`; `schemas/goal-graph.schema.json:122-148`;
`graph_set.py:230-262`; `scout_materialization.py:364-365,395`;
`model_execution.py:440-484`; `private_records.py:246-335,307-326,193-220,376-401,463-521`;
`scout_acquisition_records.py:1-10,56-99,155-158,273-355,284,288,378-464`;
`scout_inputs.py:1-5,37-56`; `workpad.py:254-297`; `project_binding.py:22-55`;
`cli.py:216-227`; `proposal_interview.py:627-656,783-804`; other paths cited
above; the focused-test/ownership inventory.
**EXECUTED:** read-only `git status`, `rg`/`grep`, `sed`/`nl`, Orca skill/status
checks, and this documentation edit plus worker handoff. No tests, network,
provider/model calls, source/schema/test implementation, or edits outside the
two owned docs and this worker's handoff file.

## Revision 1-2.1 change notes (condensed)

- **Rev 1 (B1-B3, G1-G4, M1-M3):** exact consent envelope/peer check/redemption;
  a hosted-model network effect (renamed in Revision 3) and its admission;
  frozen contracts module in wave 1a; D6 cap sealing; D9 config home; D7
  newest-resume reader; D8 click-only/deferred cron; both Greenhouse domains;
  `httpx.MockTransport`; A split into named sub-items.
- **Rev 2/2.1 (D10, R1-R5, F1-F8):** D10 scope decision (M1, cut list, 0.2.0
  ledger); restored node-receipt fields/aggregate precedence (R1); D6 sealing
  fix (R2); watchlist storage home/writer (R3); `test_run_seam.py`/
  `test_assess_model_policy.py` added (R4/R5); HiringCafe deferred; A/binding
  ownership prose fixed (F4); board-token extraction assigned to A-3 (F7);
  effect-check citation fixed (F8).

## Revision 3 change note (Terra review, operator T1 approach A, 2026-09-23)

- **D11/T1 (blocker):** scheduler cannot run real nodes today; added "Real
  node execution": `graph_node_registry.py` (I-2) admits
  `scout.find_jobs.{acquire,assess,present}` for this graph only, real-enum
  effects; I-1 raises the graph-set ceiling; I-3 depends on I-2. Invented
  network effect names replaced with `network_read`/`credential_use`; no
  schema change; registry added to the 0.2.0 ledger.
- **T2 (blocker):** I-0's code (contracts + fixtures + `test_contracts.py`) is
  now the normative freeze, not prose; added a coordinator+Terra pre-wave-1b gate.
- **T3 (major):** added "Local server entry point" (C-2 owns `python -m
  gigai.scout_present_api`, fixed loopback port, C-3 proxies, I-0 freezes routes).
- **T4 (major):** M1 restated as the already-consented traversal (roadmap fix;
  this doc had no "then obtain consent" text to correct).
- **T5 (major):** added "Ownership authority" pointing to the roadmap's DAG as
  sole per-row authority, replacing this doc's packet-level table.
- **T6 (major):** full-suite cadence lives in the roadmap only; nothing here to
  correct.
- **T7 (minor):** citation `:315-331` → `:315-337`.
