# SCOUT-06 research persistence integration plan

**Status:** read-only integration design, 2026-09-10. This packet does not
inventory, execute, approve, persist, or expose the candidate research tool;
it identifies the smallest next implementation boundary.

## Executive design

The existing external-recording lane already supplies the right durable
container: a sealed external Plan, an active external Run, append-only
checkpoint artifacts, and a terminal receipt, all written through
`run_with_journal_writer` under the private-Gig writer lock. The research
bridge should use those existing Run paths rather than add `research/`, a
second database, or an unjournaled document store:

```text
run-plans/<run-plan-id>/external-plan.json
runs/<run-id>/external-run.json
runs/<run-id>/checkpoints/<checkpoint-id>.json
runs/<run-id>/artifacts/<checkpoint-id>/<ordinal>.md
runs/<run-id>/artifacts/<checkpoint-id>/<ordinal>.json       # generic envelope
runs/<run-id>/artifacts/<checkpoint-id>/<ordinal>.domain.json # rich domain sidecar
runs/<run-id>/artifacts/<checkpoint-id>/<ordinal>.supporting/<artifact-id>.bin
runs/<run-id>/receipts/<receipt-id>.json
```

The `.md`, generic envelope, rich sidecar, and every supporting capture or
verification artifact must be published in one checkpoint transition. The
terminal receipt references the exact tuple and does not republish it. A
later Run selects the exact immutable tuple by `(run_id, checkpoint_id,
ordinal, kind, digest refs)`, never by scanning for the latest report.

## Current authority map

| Existing component | Current authority/caller | Relevant behavior | Integration consequence |
| --- | --- | --- | --- |
| Candidate `tools/cap_.../research.py` | Pure renderer, currently unregistered | Accepts supplied role data and in-memory `artifact_id -> bytes`; returns Markdown, closed sidecar, canonical JSON; no I/O/network/provider/subprocess/journal | Invoke only through a bounded bridge; preserve its strict validator |
| Candidate `research.schema.json` | Domain schema, not central registry | `scout-research-sidecar:1` binds document digest/size, graph/run, selected inputs, claims/sources/uncertainties/questions/checks, and logical capture refs | Keep complete domain data; map logical IDs to journal refs |
| `scout_template.py` / `SCOUT_GRAPHS` | Compiled Scout authoring source | `research-role` requires `role`, optionally accepts geography/research/source revisions, and declares outputs `research`, `sources`, `uncertainties`, `questions`; candidate files are omitted from inventory | Requires a reviewed source/Graph Set successor, not approved-v1 mutation |
| `scout_materialization.py` | Candidate compiler/materializer | Computes `source_digest`, journals `manifests/software/<version>/source-inventory.json`, compiled contracts and source | New research-capable source inventory must be a new exact approved version |
| `external_cli.py` | Public `gigai external` adapter | `graphs`, `requirements`, `plan`, `start`, `checkpoint`, `submit`, `cancel`, `inspect`; writers accept bounded JSON and route to service | Reuse this caller; add no Scout bypass |
| `external_recording.py` | Generic external Run service | Validates Graph authority, G45/native inputs, output/check contracts, replay/CAS, terminal state; generic output sidecar has exactly four keys | Extend the existing artifact envelope additively; preserve old four-field outputs |
| `scout_inputs.py` | Plan/start input resolver | Resolves committed G45/native revisions and revalidates exact input bytes | Add one exact research-output reference variant for later reuse, or amend contract to use a wrapper record |
| `journal.py` | Private-Gig authority substrate | `read_committed_artifact` proves one publisher, exact handoff refs/digest/size/scope; snapshots verify committed bytes and working mirror; `record` preflights immutable destinations | All output/supporting bytes and refs enter one writer transition |

The accepted SCOUT-00 contract requires readable Markdown plus a strict JSON
sidecar, exact source/claim relationships, source statuses, uncertainty,
selected inputs, originating graph/version/Run, and explicit handoffs. It
also says supplied URLs are not proof of fetch or verification, and external
recording is `execution=unobserved` / `actor_report=declared`. The candidate
implementation and corrections confirm a pure renderer corrected for heading
injection, duplicate evidence bytes, full sidecar regeneration, strict dates,
and bounded compensation relationships; these are renderer claims, not
persisted Run authority.

## Blocking contract decisions for root

### 1. One composite packet or four output kinds

The compiled `research-role` descriptor currently requires four output kinds:
`research`, `sources`, `uncertainties`, and `questions`. The candidate emits
one coherent Markdown document and one `output_kind: role_research` sidecar
that already contains all four concepts. Current external recording uses the
approved `output_contract.fields` as an exact set, so one packet cannot satisfy
the four-kind contract; pretending that the same bytes are four outputs would
duplicate or falsify provenance.

**Recommended minimal resolution:** in the next reviewed Graph Set/software
successor, make the research-role output contract one composite `research`
artifact and define `role_research` as its strict domain sidecar schema. The
single packet retains all source, uncertainty, and question fields. This is a
new proposal/version and explicit approval, not a rewrite of historical v1.
If four independently consumable artifacts are required, deliberately
redesign the renderer to produce four separately bound packets; do not infer
that larger contract in the persistence lane.

### 2. Typed domain sidecar/supporting artifact channel

`_output_sidecar_is_valid` intentionally accepts exactly
`document_sha256`, `output_kind`, `run_id`, and `selected_inputs`. The
candidate sidecar is richer and closed, with logical capture IDs whose bytes
are supplied to its pure renderer. Passing that rich object as the generic
sidecar either fails strict validation or loses domain data; putting it into
Markdown makes machine authority prose. The current checkpoint input also
has no channel for source capture/verification bytes.

**Recommended minimal resolution:** extend the existing external-recording
output artifact shape (existing invocation/checkpoint/receipt schema
families; no new top-level schema family) with optional typed domain evidence:

```json
{
  "kind": "research",
  "markdown": "# ...",
  "sidecar": {
    "document_sha256": "sha256:<markdown>",
    "output_kind": "research",
    "run_id": "run_<uuid>",
    "selected_inputs": ["<resolved exact input refs>"]
  },
  "domain_sidecar": {
    "schema_id": "urn:gigai:scout:research-packet:1",
    "value": "<complete strict candidate sidecar object>"
  },
  "supporting_artifacts": [
    {"artifact_id": "role_capture", "media_type": "text/plain", "content": "<bounded bytes>"},
    {"artifact_id": "role_review", "media_type": "text/plain", "content": "<bounded bytes>"}
  ]
}
```

This is a typed JSON sidecar channel, not JSON hidden in Markdown. The
research bridge validates `value` with the candidate validator and the exact
supporting-byte map before calling checkpoint. The generic service validates
the closed outer shape, limits, IDs/media types, and canonical digest/size
relationships; it does not invent research semantics. On checkpoint it
derives paths from Run/checkpoint/ordinal and safe `artifact_id` values, writes
the rich sidecar as `<ordinal>.domain.json`, and writes supporting bytes beneath
the derived `supporting/` directory. The persisted checkpoint/receipt output
item gains exact refs to those files. At submit, the generic service rereads
the persisted domain sidecar/supporting refs under the lock through a fixed
local validator for `urn:gigai:scout:research-packet:1`; unknown domain IDs
refuse. No caller callback, import, subprocess, URL, or arbitrary source
execution is allowed. Existing four-field output items remain valid.

The candidate's logical capture/verification IDs remain visible in its closed
sidecar. A deterministic bridge table maps each ID to one journal path, media
type, content digest, and byte count. Every non-reported captured or verified
source must have a matching persisted supporting artifact; reported URLs remain
declared only. Distinct IDs and distinct content digests remain required for
independent verification, and actor/method remain supplied evidence rather
than GigAI attestation.

## Proposed call sequence

1. `gigai external requirements --gig <id> --graph research-role --json`
   resolves approved Graph Set authority and exact output/check contracts. The
   caller must receive the composite-output decision above; current four-kind
   authority should refuse rather than reinterpret it.
2. The caller supplies exact role/input revisions to `external plan`. `plan`
   validates Graph Set, source/current version, input refs, and operation key,
   then seals `run-plans/<id>/external-plan.json`; it does not fetch sources or
   start a Run. The research domain must pin the approved software inventory
   ref/source digest in its metadata (or the approved successor must expose it
   from the Graph Set).
3. `external start` revalidates the sealed Plan and every selected input under
   the writer lock, then journals `runs/<run-id>/external-run.json`. This is
   `external_agent_recording`; no provider/model execution is implied.
4. The external agent gathers or receives sources under its own permissions.
   It calls the pure renderer with role data and exact source/verification
   bytes, then the bridge checks Markdown, complete sidecar, selected input
   identity, and source map. URLs are reported evidence unless exact content
   is supplied; “independently verified” remains supplied evidence.
5. `external checkpoint` receives Markdown, the four-field generic envelope,
   complete typed domain sidecar, and supporting bytes. It revalidates sealed
   inputs, parent, limits, domain refs and source map, then atomically publishes
   all artifacts plus the checkpoint JSON in one transition.
6. `external submit` names the exact current checkpoint output tuple and
   completion check. Under the same lock it revalidates Plan/input identity,
   Markdown/domain/supporting bytes, output/check contracts, and the fixed
   domain validator, then publishes only the terminal receipt. It does not
   duplicate/re-journal checkpoint output.
7. A later Run selects an exact `scout_research_revision` reference. The
   resolver derives checkpoint/artifact paths from validated IDs, authenticates
   the committed checkpoint and one-publisher refs, verifies sidecar Run,
   graph/version, selected inputs and source-inventory ref, and returns
   immutable refs. It never picks a latest report or scans directories.

## Atomicity, authority, and replay

* Every writer retains `run_with_journal_writer`; lookup, replay/CAS, source
  revalidation, and publication stay inside the callback. Markdown, generic
  envelope, rich sidecar, supporting bytes, and checkpoint record are one
  transition.
* Paths are derived and checked as relative, forward-slash, bounded,
  non-symlinked, and within the selected Gig. Caller input contains IDs and
  bytes, not arbitrary local paths. `read_committed_artifact` proves one
  publisher, handoff membership, scope, digest, and size; the working tree
  must still equal the committed snapshot.
* Uniqueness remains `(project_id, gig_id, operation, operation_key)`. An
  identical retry returns the exact prior Plan/Run/checkpoint/receipt and refs,
  with no new HEAD. Changed payload, current version, inputs, sidecar,
  supporting bytes, or source-inventory digest returns
  `external_operation_conflict`. Distinct keys cannot replace immutable paths
  or sibling checkpoints.
* Parent CAS remains mandatory. A source/input mutation during publication
  returns typed input/authority mismatch and publishes nothing. Collisions or
  second publishers return journal conflict/reconciliation, never overwrite.
* Submit cannot succeed from a declared rich sidecar alone: it must resolve
  persisted domain/supporting artifacts and run the fixed validator. A declared
  completion check is evidence, not silently GigAI-proven truth.
* Existing waiting-input behavior remains: unchanged sealed inputs may
  continue the same Run, changed required inputs require an explicit successor
  with a pinned predecessor and old artifact refs, and no active Gig/provider
  state changes during recording.

## Source inventory/version authority

The current `scout_source_files()` inventory intentionally excludes the
candidate research files; the current compiled Scout source digest and
approved Gig cannot claim this implementation as shipped software. The next
source-capable successor should include:

```text
tools/cap_00000000-0000-4000-8000-000000000071/research.py
tools/cap_00000000-0000-4000-8000-000000000071/research.schema.json
```

`scout_materialization` then computes a new `source_digest` and journals
`manifests/software/scout-<definition>-<digest-prefix>/source-inventory.json`
with exact member path/digest/size entries. Its v2 template binding pins the
inventory ref and source digest; changed source is a new software successor,
never editable-copy approval. The Graph Set or successor binding must expose
the exact inventory ref to the research Plan/domain envelope. The recorder
does not execute this Python source and inventory does not imply provider,
network, reviewer, or source truth. Historical v1 authority remains immutable.

## Later-Run selection contract

Existing input families (`g45_reference`, `g45_run_input`, `scout_record`)
cannot authenticate a Run output: a research packet is neither G45 imported
content nor a native private-record blob. The smallest honest extension is a
`scout_research_revision` discriminator in the existing external invocation/
Plan input schemas (not a new top-level schema family):

```json
{
  "family": "scout_research_revision",
  "run_id": "run_<uuid>",
  "checkpoint_id": "checkpoint_<uuid>",
  "ordinal": 1,
  "kind": "research",
  "markdown_ref": {"path":"...","content_sha256":"...","media_type":"text/markdown","size_bytes":123},
  "sidecar_ref": {"path":"...","content_sha256":"...","media_type":"application/json","size_bytes":456},
  "domain_sidecar_ref": {"path":"...","content_sha256":"...","media_type":"application/json","size_bytes":789}
}
```

The normalized resolved form additionally carries supporting refs and the
source-inventory ref authenticated from the checkpoint/domain sidecar. The
resolver requires same project/Gig/run, one exact checkpoint record, one
matching tuple, one publisher per file, valid domain schema, and matching
graph/version/input IDs. Missing, swapped, foreign, stale, changed,
symlinked, traversal-bearing, or guessed refs refuse before Plan allocation.
No output bytes are copied into a later Run; the later Plan points to the
original immutable revision.

This family addition is a contract decision if strict compatibility requires a
new schema version rather than an additive `oneOf` member. The recommended
behavior is to retain version-1 readers for old plans and make old readers
deliberately refuse new research-reference plans, while the new reader
validates the expanded variant. Do not encode research output as
`g45_run_input` or `jsl_blob`; either falsifies its authority family.

## Required regression matrix

* **Domain:** composite packet with known/unknown compensation,
  reported/captured/independently-verified sources, exact role revision,
  uncertainties/questions/claims/checks round-trips Markdown + full sidecar;
  missing/mismatched/duplicate/same-digest evidence, unknown fields,
  malformed dates/locators, dangling claims, and altered bytes refuse.
* **Bridge:** every non-null logical capture/verification ID maps exactly once
  to a journal artifact; rich fields survive; nothing is stripped to generic
  keys or rendered into Markdown.
* **Run:** disposable approved Graph Set → plan → start → checkpoint → submit
  publishes only the expected existing paths/ref digests; no new top-level root.
  Same-key retries return exact IDs/refs and unchanged HEAD; changed payload,
  source/version, limits, collisions, foreign/symlink/traversal paths, bad
  media/digest/size, unsupported domain ID, or duplicate publisher refuses.
* **Compatibility/recovery:** legacy four-field output remains valid; domain
  malformed/agent-declared-only verification cannot succeed; staged-write
  crash follows existing recovery without duplicate artifacts or receipt.
* **Handoff:** later exact research revision selection succeeds; missing,
  swapped, foreign, stale, changed, or guessed tuple/source-inventory refs
  refuse without Run allocation; old Run/output bytes remain unchanged.
  Changed required inputs use an explicit successor; unchanged inputs may
  continue. No active Gig/provider/source-copy mutation occurs.

## Disjoint implementation tasks

1. **Generic external artifact channel — external-recording owner.** Extend
   `external_recording.py` and the existing invocation/checkpoint/receipt
   schemas with bounded domain-sidecar/supporting fields, safe derived paths,
   immutable publication, replay/CAS, and a fixed validator registry keyed by
   domain schema ID. Add backward-compatibility, collision, and recovery
   tests. Do not alter research semantics or graph/source inventory.
2. **Research domain bridge — SCOUT-06 owner.** Add a small adapter/tests that
   call the corrected candidate renderer/validator, normalize logical capture
   IDs to the generic channel, preserve the complete sidecar, and validate at
   checkpoint and submit. No duplicate journal lock or writer.
3. **Graph/source/consumer integration — root/source-inventory owner.** Decide
   and publish the composite output contract, add both candidate files to a
   new reviewed Scout source inventory/successor, expose exact inventory ref/
   digest to the Plan/domain envelope, register central schema/hash/golden
   changes, and add the research-revision resolver plus later tailoring/
   interview selection tests. Preserve approved v1 history.

## Explicit non-claims and acceptance gates

This is not live web research, provider execution, a source-truth or
verification attestation, a sandbox claim, or SCOUT-06 completion. External
recording stores what an agent supplies and what GigAI deterministically
validates; it does not claim GigAI fetched URLs or proved semantic research.
Public caller wiring, fresh-session handoff, central schema registration,
source successor approval, and later consumer selection remain separate gates.
