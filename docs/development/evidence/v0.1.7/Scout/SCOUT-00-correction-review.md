# SCOUT-00 — Independent correction re-review

> Documentation rename only: `SCOUT-*` refers to the same historical `JSL-*`
> delivery work. Findings, verdicts, test commands and measured results below
> retain their original scope; they do not review or accept the new
> [user-owned Gig amendment](SCOUT-00-user-owned-gig-amendment.md).
> Historical line-number labels below describe the reviewed snapshot; links
> into renamed/expanded documents now target the corresponding sections.

**Review date:** 2026-09-07  
**Scope:** One read-only re-review of the corrected SCOUT-00 amendments, the
original independent review and caller audit, the approved Scout roadmap, and
the affected current callers/schemas. No provider, test, configuration, secret,
or `.gigai` state was used or changed.

## Verdict

**SCOUT-00 remains open for two narrow contract corrections.** Supplements B1--B4 and M1 address
the four original implementation-blocking findings and the additional successor
acceptance obligation. However, B1's strict external invocation schema makes
the documented direct-user writer path impossible without falsely labelling the
user as an agent, and B4 leaves its request-evidence and operation hashes
conflated. Resolve C1--C2 below, then perform a focused correction re-review;
no runtime implementation, formal schema-resource creation, or G43.1 gate
change is authorized by this review.

## Original findings: resolved in the contract

| Original finding | Re-review result | Why it is now sufficiently frozen |
|---|---|---|
| B1 external Plan/Run family | Resolved | Section 9 defines five strict, versioned resources; mode/effects/state; central identity inputs; bounded operation envelope; commands, statuses, transitions, lock-scoped idempotency, and managed/external family refusal. It binds Graph Set, semantic selector, Goal Graph UUID, selected inputs, output/check contracts, and sealed invocation evidence without representing external work as provider execution. |
| B2 G45 bridge | Resolved | G45 `private-reference:1` / `run-input-record:1` records and their exact paths remain the sole imported-content authority. Scout revisions are immutable wrappers with a content discriminator, normalized Plan input list, no cross-Gig selection, and historical snapshot replay protection. |
| B3 instance binding/migration | Resolved | Registry v3, `template_instances` uniqueness/FK constraints, journaled binding authority, explicit lock order, bounded staged intent, exact-identity recovery, and no-active-Gig replacement are all stated. This meets the caller audit's existing one-package and active-workpad constraints without treating a projection as authority. |
| B4 application request evidence | Resolved in substance; C2 freezes the remaining hash semantics | The strict append-only event and two evidence unions pin scope, private record bytes, message index, user attribution, normalized request, direct-command receipt, atomic publication, and unchanged-state rejection. Bare agent assertion remains invalid and application events grant no outbound authority. |
| M1 successor path | Resolved | The required fixture/UAT chain pins old candidate/posting bytes, checkpoints the question, preserves the waiting Run, requires an explicit successor with selected prior artifacts, and prohibits ambient disclosure. |

## New blocking findings

### C1 — Strict external invocation cannot represent the promised direct-user writer origin

**Severity: blocking (identity/provenance and replay).** B1 says every strict
object rejects unknown fields and defines `external-recording-invocation` with
only `invocation_id`, `operation`, `project_id`, `gig_id`, `actor`, `input`,
`operation_key`, `payload_sha256`, and `created_at` beyond schema version
([amendment:310-322](SCOUT-00-contract-amendments.md#b1-external-protocol-identities-and-state)). It also fixes
`actor` as the existing agent actor ([amendment:313-314](SCOUT-00-contract-amendments.md#b1-external-protocol-identities-and-state)). Yet the same supplement permits a direct user's CLI input to construct an
envelope and requires that adapter to label the direct origin without inferring
that an agent is the user ([amendment:354-361](SCOUT-00-contract-amendments.md#b1-external-protocol-identities-and-state)). No strict field or allowed actor variant can carry that required origin.

The current invocation validator reinforces the incompatibility: its actor
parser accepts only `kind: agent`, requires agent ID/session ID, and its strict
top-level fields have no origin field ([invocation.py:19-40](../../../../../src/gigai/invocation.py#L19-L40); [invocation.py:162-170](../../../../../src/gigai/invocation.py#L162-L170)). An implementation must therefore either reject the documented direct CLI path or forge an agent provenance record, which makes operation payload/replay identity untrustworthy.

**Minimal correction:** Add a required discriminated `origin` (for example,
`agent_invocation` or `direct_cli`) to `external-recording-invocation`, and make
`actor` a matching strict union: the existing bounded agent actor only for
`agent_invocation`; a bounded direct operator/CLI actor with no agent session
for `direct_cli`. Define the normalized actor identity used by
`payload_sha256`/Plan identity and the receipt display for both cases. State
that `direct_cli` is provenance only and does not relax the separately required
direct `--confirm` for the B4 direct application-event command or any managed
provider Run.

### C2 — Application request proof and event-operation idempotency share one under-specified digest

**Severity: blocking (request validation and replay).** The B4 evidence digest covers the requested opportunity/event/date/documents,
while `application-event:1` also contains notes and `supersedes`
([amendment:501-523](SCOUT-00-contract-amendments.md#b3-registry-v3-and-recoverable-instance-creation)). Freeze a separate
`requested_event_sha256` for the selected-message/direct-command evidence, and
define `payload_sha256` as the canonical hash of every semantic event field
(excluding allocated IDs/recording time). This prevents a changed note or
correction target from sharing an idempotency identity while preserving the
narrow user-request binding. This is a required correction, not a claim that a
full application-event implementation exists.

## Accepted limitations and later evidence obligations

- `external_agent_recording` remains declared/validated private-work recording,
  not authenticated agent identity, observed tools/cost, source verification,
  or GigAI-managed provider execution.
- Only bounded UTF-8 Markdown/text and JSON are in scope; no URL fetching,
  PDF/DOCX/OCR/binary import, application submission, outreach, scheduler, or
  hidden-reasoning capture is authorized.
- Default-only named instances, no active-Gig replacement, historical v1/G45
  byte preservation, and detached imported authority are accepted constraints.
- Later goals must supply the listed strict schema resources, schema dispatch
  and package inventory integration, semantic validators, migration failpoint
  recovery, replay/foreign/tamper tests, installed Codex/Claude recording
  evidence, and fresh-session M1 UAT. This review ran no tests because SCOUT-00
  is a contract/caller re-review.

## Existing gate

The direct G43.1 public provider-review dogfood remains a separate live gate:
its approved subject and baseline are unchanged, and Scout corrections cannot be
presented as a replacement re-review or as provider/Run consent.

## Final correction check — 2026-09-07

**Verdict: accepted for SCOUT-00 contract closure.** The focused corrections
resolve C1 and C2; no remaining implementation-blocking defect was found in
the corrected B1/B4 text.

- **C1 resolved.** `origin` is now a required strict discriminator, its actor
  union prevents `direct_cli` from impersonating an agent, direct construction
  is limited to the CLI path, and the normalized operation/Plan identity
  includes origin plus `{kind,id}` while preserving the original sealed
  invocation on replay ([amendment:310-348](SCOUT-00-contract-amendments.md#b1-external-protocol-identities-and-state)).
  The confirmation boundary remains explicit for approval, managed provider
  Runs, and direct application events.
- **C2 resolved.** `application-event:1` now carries both
  `requested_event_sha256` and `payload_sha256`; the first binds the selected
  user request and the second covers every semantic event field for operation
  conflict detection, with both recomputed at publication
  ([amendment:507-542](SCOUT-00-contract-amendments.md#b3-registry-v3-and-recoverable-instance-creation)). This preserves
  the narrow, honestly agent-reported request provenance without allowing a
  changed note, correction target, or evidence reference to replay silently.

### Required SCOUT-04 reuse rule (non-blocking to this correction verdict)

Because the Plan identity seals the complete Graph Set/selection digest while
the normalized invocation intentionally excludes session/timestamps
([amendment:337-348](SCOUT-00-contract-amendments.md#b1-external-protocol-identities-and-state)), implementation
must state the reuse branch before writing the external Plan service: a retry
with the same `(project_id, gig_id, operation, operation_key)` and matching
payload returns the original Plan and its original selection/invocation
evidence; a different operation key/session either explicitly references that
same sealed selection record or receives a new `agent_explicit` selection and a
new Plan. It must not create a new session-specific selection record while
claiming the original Plan identity. This is a minimal SCOUT-04 implementation
rule, not a new SCOUT-00 blocker.

No tests or runtime/provider/user-state operations were run for this focused
documentation check. The separate direct G43.1 public dogfood gate remains
unchanged.

## Evidence inspected

- `SCOUT-00-contract-amendments.md`, including Section 9 B1--B4/M1;
  `SCOUT-00-independent-review.md`; and `SCOUT-00-caller-audit.md`.
- Approved Scout roadmap and G43.2/G45 contracts.
- Current `invocation.py`, `registry.py`, `journal.py`, `workpad.py`,
  `run.py`, `run_plan.py`, `package.py`, `catalog.py`, `lifecycle.py`, and CLI
  seams. Concurrent G43.1 closeout edits were observed but not assessed as Scout
  implementation evidence.
