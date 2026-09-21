# SCOUT R5/R6 consolidated independent review

Date: 2026-09-12 (America/Denver)  
Reviewer: Luna-high, independent read-only review  
Status: correction list; not release, installed-wheel, provider, model, or
publication acceptance.

## Scope and current evidence

I read the release execution graph, the original Scout roadmap, RUNTIME-01,
`SCOUT-R5-contract.md`, `SCOUT-R6-contract.md`, both implementation reports,
the R5/R6 source and focused tests. Worker reports are treated as claims only;
the following current-checkout evidence is reproducible and is the basis for
the findings:

* `.venv/bin/pytest -q tests/test_scout_r5_interview_transfer.py tests/test_runtime_comparison.py`
  produced **3 failed, 4 passed**. All three R5 failures occur in `_env` at
  `tests/test_scout_r5_interview_transfer.py:39-42`, before interview or
  transfer logic: the fixture uses `payload.questions=[]`, while
  `src/gigai/schemas/native-record-content.schema.json:31-32` requires at
  least one experience question. The reported malformed R6 evaluation-pack
  schema is repaired: the four R6 tests pass.
* `PYTHONPATH=src .venv/bin/python tools/verify_installed_schemas.py` produced
  `verified 78 installed GigAI schemas`; this does not include the three R5
  schema resources, which are absent from `src/gigai/schemas/`.
* `.venv/bin/ruff` is not present in this checkout, so the requested targeted
  lint could not be rerun. No substitute lint result is claimed.
* A disposable real-journal synthetic flow (valid one-question experience
  record, no private user data) produced
  `FLOW True False True True True 1`: initial prepare, idempotent replay,
  feedback revision, revise revision, and fresh-session read all worked.
* The same real-journal probe accepted an opportunity preparation whose
  posting had a nonexistent path/snapshot (`POSTING_MISSING_ACCEPTED`) and
  accepted a story whose `family` was `posting` while its path contained an
  `experience` record (`WRONG_FAMILY_PATH_ACCEPTED`).
* Direct grader probes returned `pass` for empty evidence IDs, a case-wide
  allowlisted evidence ID of the wrong evidence kind, and duplicate criteria
  where the final duplicate overwrote the first:
  `EMPTY_EVIDENCE_STATUS pass`, `WRONG_EVIDENCE_KIND_STATUS pass`,
  `DUPLICATE_CRITERION_STATUS pass`.
* A disposable injected-transport comparison with a deliberately altered
  graph/goal in a valid pack returned
  `HANDCRAFTED_PACK_GRAPH_ACCEPTED ...9999 ...9998`.
* A disposable ZIP probe with two `README.md` members was imported and kept
  the last member (`DUPLICATE_MEMBER_ACCEPTED`). A private-transfer probe
  included `.env.production`, restored
  `manifests/active-gig-version.json`, and preserved an old absolute path.
* Click inspection of the main CLI found `comparison`, but both
  `scout-interview` and `scout-transfer` were absent (`No such command`). The
  copied `data/scout/gig.py` does contain those local subcommands.

The probes used temporary directories and injected invocation services only.
No provider, model, network, live socket, installation, activation, publish,
commit, or production/private user data was used.

## Prioritized corrections — R5 interview

### R5-1 (P0): selected posting and source references are not authenticated

Established bug. `_verify_selected_refs` gathers only research, candidate
evidence, feedback, and prior preparation at
`src/gigai/scout_interview_records.py:152-160`; it omits `selection["posting"]`.
`_selection` only checks an opportunity posting's record/revision IDs and the
presence of an arbitrary `snapshot_id` at `:115-123`; it never proves that the
posting is a committed artifact. `_verify_refs` accepts a caller-supplied
`path` and makes `content_sha256` optional at `:172-194`. `_ref` checks only
that `family` is a nonempty string at `:69-87`; it does not authenticate the
payload family, record/revision identity, project/Gig ownership, snapshot
identity, media type, or size.

Reproduction: the real-journal synthetic probe passed an opportunity
selection with nonexistent posting IDs/path and `snapshot_id="snapshot_fake"`,
then passed a story with `family="posting"` and no digest while the path named
an actual experience record. Both operations committed.

Smallest fix: require a complete artifact reference for posting and every
selected source, include posting in the one selected-reference set, resolve
the path from canonical record/revision identity rather than trusting caller
path, require digest and size, parse the committed payload, and require its
family/kind/record/revision/project/Gig (and posting snapshot) to match the
selection. A role-only selection must continue to reject any posting.

### R5-2 (P0): no strict interview schema or main CLI registration

Established integration gap. There is no
`scout-interview-preparation.schema.json`,
`scout-private-transfer-manifest.schema.json`, or
`scout-definition-export-manifest.schema.json` in the schema directory, and
none is present in `validators.py`'s versioned list at
`src/gigai/validators.py:98-113`, `schemas/SHA256SUMS`, or the installed
verifier. `scout_interview_records.py` does not call
`validate_serialized_contract`; it hand-normalizes content and publishes it at
`:218-250` and `:303-360`. The standalone Click adapter is real, but
`src/gigai/cli.py:3927-3934` registers no interview or transfer group (and the
usage text at `:139-145` omits them). The copied wrapper's commands at
`src/gigai/data/scout/gig.py:149-190` are therefore not proof of the supported
main entry point.

Smallest fix: add the three frozen schemas to the central/versioned inventory,
SHA256SUMS, package/verifier resources, and validate every service-produced
record/manifest before journal publication; register the existing service
groups in the main CLI without adding a second writer. Repair the focused test
fixture's valid experience content separately; do not weaken the schema's
`minItems: 1` rule merely to make the fixture pass.

### R5-3 (P0): source validation and publication do not share one snapshot

Established race window. `_verify_refs` captures a writer snapshot in a
`run_with_journal_writer` callback at `:175-178`, then `_publish` starts a new
writer operation at `:342-345` and captures another snapshot at `:314-315`.
Thus source validation is not part of the publication CAS. `save_interview_feedback`
also reads the current revision before calling `revise_interview`, which reads
again at `:448-458`. Journal locking protects each operation individually, but
does not prove that all selected bytes and the published revision were based on
one head. The accepted journal writer API explicitly supports lookup/CAS/
publication in one callback (`src/gigai/journal.py:203-267`).

Smallest fix: perform selection normalization, source resolution, content
source checks, operation-key lookup, parent check, and artifact publication in
one writer callback over one `JournalSnapshot`; retain the snapshot head in
the publication front matter and refuse if the authenticated source set or
parent changed. Keep operation-key replay exact and never replace immutable
artifacts.

### R5-4 (P1): stories require a nonempty reference list, not authentic
criterion-level evidence

Established contract gap. `_story` requires only that `source_refs` is a
nonempty list at `:197-215`; `_verify_content_refs` forwards those refs to the
weak verifier at `:162-169`. Arbitrary `metrics` and `skills` lists are accepted
when they are lists (`:209-212`), without requiring each claim to cite selected
evidence or checking that feedback cannot be used as evidence. The healthy
flow proves record mechanics, not semantic story grounding.

Smallest fix: define a closed story schema in which every factual claim,
metric, and skill has an authenticated selected source reference with the
correct evidence family; reject feedback/practice records as factual evidence.
Keep hypothetical examples explicitly labelled and do not represent a
content PII scanner as a guarantee.

### R5-5 (P1): private transfer has archive and authority-safety defects

The following are established by source or disposable probes:

* Duplicate archive members are not rejected. `_read_archive` checks duplicate
  `manifest.json` only at `src/gigai/private_transfer.py:220-224`; it converts
  member names to a set at `:239-241` and reads `archive.getinfo(path)` at
  `:242-257`, so duplicate non-manifest members can be hidden behind the last
  entry. A two-member `README.md` archive was accepted.
* Uncompressed size is checked after allocation. `archive.read(path)` runs at
  `:251` and only then checks `len(data)`/total at `:252-254`. A hostile ZIP can
  force allocation before the limit is enforced.
* Destination checks are check-then-replace races. `_write_archive` checks
  `destination.exists()` at `:145-151` and calls `os.replace` at `:153-163`;
  `_extract_new` repeats the pattern at `:263-281`. A concurrent creator can
  be overwritten despite the refusal contract.
* Private restore includes authority-bearing files. `_PRIVATE_ROOTS` includes
  `manifests` at `:47-49`, `backup_private` filters only state.sqlite/reports/
  scratch/config at `:195-206`, and `_extract_new` writes every selected file
  unchanged at `:272-286`. A synthetic
  `manifests/active-gig-version.json` was restored even though the manifest
  says `activation: none` at `:138-142`. Imported active pointers, approvals,
  capabilities, provider targets, and consent must not become active authority.
* Content is copied unchanged. A synthetic record containing
  `/Users/old-home/workpad` retained that absolute path after restore. The
  current code has no path-rewrite or reject pass. Ancestor symlinks are
  checked at read time in `_check_source:72-96`, but concurrent source
  replacement and complete snapshot behavior are untested.
* Secret matching is incomplete. `_secret_path` at `:67-70` catches exact
  names/extensions but not `.env.production`; a synthetic private backup
  included `tools/.env.production`. Configuration/credential inventories need
  an explicit closed allowlist/denylist, not filename heuristics alone.

Smallest fix: reject all duplicate ZIP names; inspect `ZipInfo.file_size` and
compressed-size/ratio limits before bounded streaming allocation; use an
exclusive no-replace destination primitive or locked parent operation; omit
active pointers, approvals, capability/target/consent manifests and other
machine authority from private transfer (or sanitize them into inert records);
reject or canonicalize old absolute links; and use a complete declared secret/
configuration exclusion inventory with descriptor/ancestor rechecks.

### R5-6 (P1): second-home continuation and customization update are not proven

Untested/deferred claim, not a failure of the read-only helper. Restore writes
files but does not bind the destination to a local project/Gig registry or
provide a continuation/status check; the test cannot reach this setup because
of the native-record fixture failure. `compare_scout_template` and
`decide_scout_template_update` are intentionally read-only at
`src/gigai/scout_template.py:42-99`; there is no approved materializer that
journals an adopt decision and preserves customization. The implementation
report expressly does not claim second-home execution or adoption publication.

Smallest fix: add a separately authorized local bind/continuation flow that
proves old pointers and credentials are absent, then add an explicit adopt
materializer which journals the source snapshot and preserves custom files and
prior graph/history. Do not treat `save(content)` or a standalone archive
round-trip as generation, Run, or installed second-home acceptance.

## Prioritized corrections — R6 runtime comparison

### R6-1 (P0): comparison accepts a handcrafted graph/goal instead of active Gig authority

Established bug. `run_comparison` authenticates only that the index has an
active Gig/version at `src/gigai/runtime_comparison.py:223-230`, then uses the
pack's `selected_graph` and creates a fresh unrelated `goal_id` in
`_run_attempt:297-317`. The generated manifest has no graph set, selected graph
ID, selection record, or capability/effect binding, and the manifest is never
validated against `run-manifest-v2.schema.json`. Consent is reduced to exactly
`action` and `actor` at `:381-383`, without project/Gig/version/graph/pack
scope. The custom-pack probe changed graph/version/goal to arbitrary valid
values and the comparison committed.

Smallest fix: resolve the active Graph Set and selected graph through the
existing `run.py` authority reader (`resolve_selected_graph_authority`), bind
the pack's graph/goal/input contract to those exact committed bytes, derive
capabilities/effects from the graph, and bind consent to project/Gig/version/
selection/pack digest. Validate the complete Run manifest and preserve
readable refs to graph set, selection, graph, goal contract, consent, and pack.

### R6-2 (P0): the default/custom pack is not proven Gig-owned

Established bug. `load_evaluation_pack()` loads global installed package data
by default at `:90-105`; schema validation only requires a nonempty arbitrary
`owner` string (`runtime-evaluation-pack.schema.json:7-16`). The shipped pack
declares `owner: "synthetic-gig-definition"`, and no code binds that owner or
pack bytes to the selected Gig's portable definition. A caller-supplied pack
path is accepted before workpad authority is resolved. The CLI help calls this
“Gig-owned” at `cli.py:700-705`, but that is not enforcement.

Smallest fix: require an authenticated pack reference from the selected Gig's
committed definition/source inventory (including owner Gig ID, graph/goal and
digest), or make a global pack explicitly non-Gig-owned and keep it outside
the R6 acceptance claim. Validate the same pack bytes for both attempts and
persist the authenticated pack reference.

### R6-3 (P0): grader has three source/criterion acceptance holes

Established by direct probes. `grade_output` permits an empty `evidence_ids`
list at `:157-178`, so a supported criterion can pass without evidence. It
checks membership only against the case-wide evidence-ID set, not criterion-
specific source kind/allowlist, so a candidate evidence item can support an
unrelated posting criterion. It builds `by_id` with a dict at `:175`, silently
overwriting duplicate criteria; the last duplicate can make a malformed output
pass. Existing known-good/known-bad vectors at `:369-378` prove only one
positive/negative path and do not exercise these negatives.

Smallest fix: reject duplicate criterion IDs before dict construction; require
nonempty evidence for supported claims (and define the exact rule for
unsupported/unknown); freeze per-criterion evidence IDs/kinds in the pack and
validate every cited evidence item against that mapping; add known-good and
known-bad vectors for all three cases plus empty, wrong-kind, duplicate,
unsupported-claim, and malformed-output negatives.

### R6-4 (P1): readiness and actual model identity are not gated

Established source gap/untested live claim. Setup validation checks only
adapter family and, for Ollama, presence of a configured digest at
`runtime_comparison.py:235-240`; it does not perform or require the existing
recorded/readiness check before creating attempts. The default test uses an
injected `invocation_service` (`tests/test_runtime_comparison.py:57-65`), not
the actual Ollama or Codex adapter. Attempt identity records configured target
name/model at `:305-317`; observed adapter/model/digest identity is not
promoted into the comparison attempt contract. Thus focused fixture passes do
not prove target readiness, installed entry points, model identity, or live
socket behavior.

Smallest fix: gate each attempt on the existing target readiness/capability
contract, retain configured and observed identities/digests separately, and
reject a mismatch. Keep injected transports as allowed synthetic tests, but
add separate installed and provider-consented evidence in the R7 lane.

### R6-5 (P1): durable errors exist only in case payloads; retries/restart are absent

Established source gap. `_run_attempt` catches `Exception` and writes a case
error at `:331-345`, but hard-codes `retries: 0` in both the case payload and
comparison row at `:345` and `:351`. `_details` always emits empty `errors`,
`tool_errors`, and `model_errors` at `:398-403`, and `wait` is passed into the
attempt at `:297` but never used for asynchronous or resume behavior. A crash
after a case transition leaves a partial Run with no comparison publication or
resume/status operation; a later invocation allocates new Runs. The focused
failure test proves one setup's case work is preserved, not restart/retry
semantics.

Smallest fix: persist an attempt state machine/checkpoint with structured
errors, retry policy/count, and exact case input/output references; add a
readable comparison/Run status and resume operation that reuses the pinned
attempt and skips terminal cases. Preserve interruption and partial results;
never erase the other setup.

### R6-6 (P1): readers authenticate only the outer comparison artifact

Established source gap. `show_comparison` calls `read_committed_artifact` and
checks only payload type and comparison ID at `:274-284`. It does not validate
the comparison schema on read or dereference/re-authenticate both `run_ref`s,
case result refs, graph/goal/pack refs, or their digests. The Markdown renderer
at `:287-294` displays fields without proving the linked Runs are readable.

Smallest fix: use standard Run/graph/pack readers from the pinned journal head,
validate the outer schema and every nested reference/digest, and render only
the authenticated projection. Add a negative fixture for missing, foreign,
and mismatched Run links.

## Shared integration, evidence boundaries, and deferred R7 work

1. **Shared P0:** repair the valid native experience fixture/schema setup,
   register the three R5 schemas/resources, and register the existing R5
   service groups in the main CLI. This is the minimum needed to rerun the
   blocked R5 focused tests once; no broad suite should be repeated in this
   review.
2. **Shared P0:** converge R5 source authentication and R6 Run/pack authority
   on the existing journal snapshot/CAS, Graph Set/selection readers, and
   standard artifact-reference contracts. Avoid a parallel DTO-only store or
   hand-built authority manifests.
3. The copied wrapper and standalone adapters are useful bounded seams, but
   they do not establish main-CLI support, installed package behavior, actual
   agent graph execution, model/provider execution, privacy logs, or second-
   home continuation.
4. The R5 healthy synthetic flow and R6 focused/injected tests are fixture
   evidence only. They do not prove live sockets, Ollama/Codex readiness,
   provider consent, installed-wheel/package-data behavior, or whole-feature
   acceptance. The current schema verifier's 78-resource pass is not R5
   registration proof.
5. R7 remains the correct home for exact-wheel isolated installation, supported
   installed entry points, live/consented Qwen+Ollama versus Luna+Codex
   attempts, hardware/setup identity, restart dogfood, and final matrix. R7
   must not be marked complete based on the current injected transport or
   global-pack result.

## Disposition

R5/R6 are not accepted as release-ready. The narrow R5 journal revision/replay
mechanics, R6 focused pack/grader execution, and preservation of one injected
setup when the other fails are demonstrated bounded slices. The prioritized
P0 items above must be corrected and independently rechecked before a
consolidated correction wave can claim integrated R5/R6 evidence.
