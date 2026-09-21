# SCOUT-05 capability consent caller audit

**Status:** bounded read-only caller audit, 2026-09-10.  This packet traces
the current implementation and recommends the smallest generic completion
path; it does not approve a capability, activate a Gig, invoke a provider, or
change source, schemas, or tests.

## Executive result

The repository has deterministic capability inspection, local installation,
proposal approval, and a fail-closed Scout mutation bridge, but it has no
public or journaled transition that records a reviewer security decision and
explicit effect consent for a pending bundled manifest.  Consequently a
pending Scout manifest cannot safely become an executable `available` binding
today: the bridge correctly refuses it, while `gigai approve --capability-manifest-id`
can currently attach a schema-valid *pending* manifest without proving that
review and effect selection happened.  This is a blocking consent-caller gap,
not evidence that the copied source should bypass the existing generic service.

## Existing path and callers

| Stage | Existing path | Authority and side effect |
| --- | --- | --- |
| Candidate/pending manifest | `scout_materialization.materialize_scout_candidate` -> `scout_bundled_tools.prepared_scout_crud_manifest` -> `capabilities.materialize_capability_manifest` | Journals the sealed source/inventory and writes a derived `manifests/capabilities/capmanifest_*.json`; schema-valid, but `availability_state: missing`, `security_review: pending`, and option decision `pending`. No copied code executes and no active pointer is changed. |
| Deterministic validation | `capabilities.validate_capability_manifest` | Uses the registered capability-manifest schema, canonical JSON, unique IDs/options, ordered options, and closed tool binding checks (entry, inventory digest, wrapper, source identity, operations, and `write_workpad`). Pure validation; no review authority or publication. |
| Inspection | `capabilities.inspect_capability_manifest` | Purely derives `security_rejected`, `incompatible`, `credential_missing`, `available`, `installable`, or `missing`. It reads local bytes and checks declared state/path/source facts; it does not record a reviewer judgment. |
| Local install | `capabilities.install_local_capability` | Generic library function used by G17 tests, with its own installation artifact and atomic isolated staging. There is no CLI capability-review/install caller for the bundled Scout path, and this installer is not a security/effect-consent authority. |
| Graph/version proposal | `lifecycle.propose_graph_set_offline` / `propose_first_graph_set_offline` | Journals a typed pending proposal with exact graph/source identity. Amendments pin the approved base version and parent proposal. Proposing does not approve, activate, or grant capability effects. |
| Explicit approval/publication | `cli.approve_command` -> `lifecycle.approve_offline` | Direct operator command validates and journals the proposal, sealed commit, `gig_accepted`, tag, and active pointer. Optional `--capability-manifest-id` validates the artifact reference but currently does not require `security_review.status == passed`, `availability_state == available`, or an approved selected effect option. |
| Approved mutation execution | copied `data/scout/gig.py` -> `scout_tools.invoke_approved_tool_entry` -> `dispatch_native_record_tool` -> `native_records.*_from_tool` | Authenticates the wrapper's registered Gig, reads the committed active version and capability manifest, revalidates source/inventory under the writer lock, requires `available` + passed security review + exact `write_workpad` permissions, then journals the typed native record/receipt. Agent origin remains `agent_supplied`; it cannot masquerade as operator consent. |
| Read-only wrapper operations | `gig.py list/read/context` -> `native_records.list/read/context` | Uses the authenticated Gig ID and projection/journal snapshot. No capability execution or mutation is implicit. |

The relevant generic G17 path is therefore real but incomplete for this
workflow: `inspect_capability_manifest` and `install_local_capability` are
library services, not a user-facing review/consent transition. G18 provider
execution and G19 target-effect authorization are separate families and must
not be reused as a Scout capability approval. The accepted SCOUT-00 contract
requires a local validated operation service, full Gig/version/source/effect
binding, a new approved Gig version when executable source changes, and
separate authorization for broadened effects.

## Structured findings

| ID | Severity | Finding | Consequence |
| --- | --- | --- | --- |
| F1 | Blocking | No current public caller persists `security_review` as a reviewer decision or persists selected effect consent for a pending capability manifest. | `default_init` correctly reports `capability_review_required`, but a user cannot complete that state through a typed generic flow. |
| F2 | Blocking safety | `approve_command` exposes `--capability-manifest-id`; `approve_offline` checks the manifest reference/schema but not passed security review, available state, or an approved effect option before adding it to the active pointer. | A direct operator can accidentally publish a pending manifest as a version reference. The existing mutation bridge then refuses, but publication has already represented an unreviewed binding. This path needs a guard or a review service before being used for Scout. |
| F3 | Feature gap | Inspection and installation are deterministic/direct library APIs; there is no CLI/service sequence for inspect -> reviewer decision -> effect consent -> reviewed immutable manifest. | Do not add a Scout-specific flag or call the installer as a substitute for review. |
| F4 | Positive boundary | `scout_tools._binding` and `native_records._publish` provide the reusable authority checks and writer-lock revalidation needed after consent. | The mutation lane can be enabled without bypassing SQL/journal ownership, source checks, operation keys, or receipt binding. |
| F5 | Authority gap | Existing capability-manifest fields are state data; `capability-installation.schema.json` records an install attempt/decision, not who reviewed source semantics or consented to `write_workpad`. | Existing records are insufficient to attest reviewer judgment and effect consent. Add a generic immutable review-decision artifact/transition (or demonstrate an existing equivalent before implementation), with actor, parent manifest digest, Gig/version, selected option/effects, deterministic findings, outcome, and operation key. |
| F6 | Successor gap | A first approved Gig may retain a pending manifest, but there is no caller to produce its reviewed successor. | Never edit the old proposal, manifest, tag, or approved pointer in place; use a new manifest and amendment proposal. |
| F7 | CLI correctness | `revise` declares `--capability-manifest-id` but `revise_command` does not pass that argument to `revise_offline`; the option therefore does not bind a manifest. | Remove/fix the misleading option as a separate reviewed change; it is not a consent path today. |

## Review, consent, and authority are different

The following checks can be deterministic and should be run before any human
decision: canonical/schema validity; exact Gig/project identity; source and
inventory digests; literal wrapper/tool members and wrapper binding; operation
allowlist; `write_workpad` effect and permissions; local compatibility,
credentials, and source availability; and current committed artifact/ref
integrity. These checks produce findings and a candidate state, not approval.

A reviewer judgment is an explicit decision about the inspected source and
declared behavior (pass/reject, with evidence and actor). It must not be
inferred from Graph Set approval, a manifest's existing `security_review`
field, a successful install, or an agent invocation. Effect consent is a
separate direct operator decision to admit `write_workpad` for the exact
capability/version; an `agent_supplied` actor and copied Python cannot create
that consent. If one UI command collects both decisions, it must record both
actors/decisions distinctly and require an explicit confirmation; it must not
turn agent origin into operator consent.

## Safe successor for an approved first Gig

For an approved first version whose manifest is still pending:

1. Read the committed old version, source inventory, proposal, and pending
   manifest. Keep their exact bytes, IDs, tag, and active pointer immutable.
2. Run deterministic inspection and source/inventory revalidation. A reviewer
   explicitly records pass/reject and selected option/effects in a **new
   immutable reviewed manifest/artifact** (new artifact identity or explicit
   versioned content; never overwrite the pending artifact). The reviewed
   artifact must retain the exact source/inventory digest and parent digest.
3. Create an amendment proposal with the old active version as
   `base_gig_version` and the prior proposal as its parent, carrying the exact
   new reviewed manifest reference. If executable source or schema bytes
   changed, first create a new source inventory and require the corresponding
   successor; never reuse the old capability ID/digest as authority.
4. Require direct operator approval of that amendment and reviewed manifest.
   The existing lifecycle approval may then publish the new `gig_accepted`
   pointer and tag as an intentional version transition. Before that approval,
   the old pointer remains active and no capability effect is usable.
5. Resolve old version 1 by its explicit version/reference as history; do not
   rewrite its pointer or invent `approved` flags. Only the explicit approval
   moves the active pointer, and no provider or copied source runs during
   review/proposal/approval.

The same sequence applies when a current-version/source digest changes: the
working artifact is stale, so refuse and require a successor. A repeated,
byte-identical request for an already committed reviewed successor may return
that committed result by operation key; a changed request, multi-publisher
artifact, changed source, or current-version mismatch must produce a typed
refusal and no additional publication.

## Smallest generic implementation packet

This audit does not implement the packet. The narrowest follow-up should be a
generic capability service, shared by Scout and future local capabilities:

* Add a `review`/`consent` service next to `capabilities.py` (or an existing
  generic lifecycle service), reusing `validate_capability_manifest`,
  `inspect_capability_manifest`, `capability_manifest_artifact_ref`,
  `read_committed_artifact`, `run_with_journal_writer`, and the existing
  lifecycle proposal/approval primitives. Inputs must include exact Gig ID,
  base approved version, manifest/ref and digest, source/inventory digest,
  reviewer actor/outcome, selected option, effects, and an idempotency key.
* Hold the journal writer lock while re-reading committed inputs and current
  version. Reject working-tree drift, source/inventory changes, duplicate or
  conflicting publishers, foreign Gig/version, malformed refs, and broadened
  effects. On pass, journal a review decision and materialize a new immutable
  reviewed manifest; on reject, journal only the typed refusal/decision as
  appropriate. Review must not activate a version.
* Add a generic CLI surface such as `gigai capability inspect` (pure) and
  `gigai capability review` (explicit reviewer decision), followed by the
  existing direct `gigai approve <proposal> --gig ...` for version/effect
  consent. Do not expose a Scout-only bypass, HTTP endpoint, provider call,
  direct SQL/journal writer, self-approval, or implicit activation.
* Because current installation records cannot prove reviewer judgment/effect
  consent, add a narrowly scoped versioned review-decision schema/artifact only
  if no existing generic review record is suitable; root must integrate its
  registration/hash/golden fixtures. The artifact should bind parent manifest
  digest, Gig/version, source inventory, reviewer/operator identities, exact
  decision/effects, evidence/findings, and operation key.

### Proposed ownership and tests for that follow-up

| Area | Smallest ownership | Required focused regressions |
| --- | --- | --- |
| Service/CLI | `src/gigai/capabilities.py` plus `src/gigai/cli.py`, or one new generic capability-consent service and its CLI adapter | Pure inspect/help/import causes no writes; review requires explicit actor/decision; agent cannot approve; valid reviewed manifest is canonical and available. |
| Lifecycle integration | Existing `src/gigai/lifecycle.py` only if approval needs a guard; otherwise reuse it unchanged | Pending manifest cannot be attached; reviewed manifest can attach only to exact successor; old approved pointer/manifest remains byte-identical until direct approval. |
| Scout caller proof | `tests/test_scout05_bundled_tools.py` and `tests/test_scout05_tool_crud.py` | Two disposable Gigs, fresh-process wrapper; reviewed create/update/archive/list/read/context succeed only for exact Gig; source/inventory/current-version mutation, stale replay, multi-publisher conflict, and foreign refs refuse with no new receipt/publication. |
| Generic capability proof | New focused capability/CLI tests alongside existing `tests/test_g17_capabilities.py` | Review/install/replay/recovery, rejected review, malformed/changed artifact, and no active selection/provider execution. |

Writable authority remains the journal service: capability artifacts and
review decisions are published through the normal writer and lifecycle
transitions, with the projection updated by existing recovery. The copied
wrapper only submits typed requests; it never writes SQL, receipts,
manifests, or approvals directly.

## Scope and evidence limits

This was a source/doc audit only. No tests, providers, network calls, actual
approval/activation, real private `.gigai` state, commits, or release actions
were performed; no source or test files were changed. The result is an
operational design packet, not evidence that the pending Scout capability is
reviewed, installed, executable, or safe to expose to an external agent.

