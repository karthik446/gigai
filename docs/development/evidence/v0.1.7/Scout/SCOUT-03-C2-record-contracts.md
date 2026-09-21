# SCOUT-03 C2 — Native record and CRUD contracts

**Date:** 2026-09-08  
**Owner:** Luna  
**Status:** Implementation-design preparation only; not an acceptance result.  
**Boundary:** This note defines the smallest closed native-content contract C2
can implement after C1's storage/journal/index/workpad correction settles. It
does not change the accepted authority model, add approval authority, define a
Plan/Run shape, or implement the deferred external executor/tool binding.

## 1. Contract decisions carried forward

- The private Git journal and typed revisions remain authority. SQLite and
  `indexes/context.json` remain rebuildable views.
- Existing G45 `private-reference:1` and `run-input-record:1` records, including
  their exact `source.txt` bytes and digests, remain the sole content authority
  for imported material. A Scout wrapper stores references to them; it never
  copies or relabels their bytes.
- A native structured record stores one JSON sidecar under the record blob
  area. A native document stores its bytes under
  `docs/<record-id>/<revision-id>/`; its typed sidecar references those exact
  bytes. Other native blobs remain under
  `records/<record-id>/blobs/`. The wrapper's accepted B2 `jsl_blob` content
  reference points to the immutable native sidecar (or document sidecar).
- Every native sidecar is validated by one new closed,
  `native-record-content:1` schema. Its `kind` must equal the enclosing
  `private-record-revision:1` kind. `$defs` and discriminated `oneOf` branches
  live in that one schema; do not create one schema file per record kind.
- All mutations use C1's operation service: create, update with expected
  parent, archive, and replay. C2 does not add a second journal or an
  unvalidated Gig-owned write path.

The proposed schema envelope is:

```json
{
  "schema_version": "1.0",
  "kind": "profile_preferences",
  "payload": {}
}
```

The envelope rejects unknown fields. `payload` is a closed discriminator on
`kind`; it is not a free-form JSON escape hatch. Suggested shared bounds are
one sidecar of at most 262144 UTF-8 bytes, at most 32 list entries, at most 16
artifact/source references, and bounded strings (label 120, IDs 128, prompts
and notes 4096, answer/conversation text 16384). C1's stricter operation and
G45 limits still win where they are tighter.

## 2. Reusable definitions

The one schema should define these local shapes.

### Provenance

```json
{
  "kind": "user_reported",
  "source_refs": []
}
```

`kind` is exactly `user_reported`, `imported`, or `inferred`. `source_refs` is
an array of exact local record/revision or G45 record/snapshot references, not
URLs, ambient session locators, or unbounded text. `user_reported` may have an
empty source-ref list when the operator supplied the answer directly.

An `inferred` value is never rendered or selected as user-confirmed experience.
An imported source is evidence of what was supplied, not evidence that the
claim is true. A verification status must point to a separate exact evidence
record; setting a status string alone cannot establish independent verification.

### Artifact reference

Use one discriminator with exact digest/size/media/path fields:

```json
{
  "family": "g45_reference",
  "reference_id": "ref_<uuidv4>",
  "record_ref": {"path": "references/ref_.../reference.json", "content_sha256": "sha256:...", "size_bytes": 123, "media_type": "application/json"},
  "snapshot_ref": {"path": "references/ref_.../source.txt", "content_sha256": "sha256:...", "size_bytes": 123, "media_type": "text/markdown"}
}
```

The other branches are `g45_run_input` (same shape with `run_input_id` and
`run-inputs/input_...` paths), `native_document` (exact `docs/...` path), and
`native_blob` (exact `records/.../blobs/...` path). All branches require
project/Gig scope to be checked by C1; path, digest, size, and media must agree
with the committed bytes. The sidecar may refer to an artifact but may not
embed a second copy of its bytes.

### Uncertain fact

Use one shape for the three intentionally separate sponsorship/eligibility
facts:

```json
{
  "state": "unknown",
  "value": null,
  "provenance": null,
  "evidence_refs": []
}
```

`state` is `known`, `unknown`, `declined`, or `conflicting`. `value` is a
closed enum for the particular fact and is required only for `known`;
`provenance` is required for `known`, `declined`, and `conflicting` values.
`evidence_refs` contains only exact artifact references. The three facts use
different value enums and different JSON properties; they must not be merged
into one generic sponsorship field.

## 3. Native content branches

### 3.1 `profile_preferences`

The profile sidecar has separate arrays for hard constraints and soft
priorities, preventing a priority from silently becoming a filter. Each item
uses a closed `field` discriminator and one typed value branch. C2's initial
field set is `geography`, `work_mode`, `seniority`, `employment_type`, and
`compensation`; adding a field requires a contract/test update.

```json
{
  "schema_version": "1.0",
  "kind": "profile_preferences",
  "payload": {
    "hard_constraints": [
      {
        "field": "geography",
        "value": {"include": ["United States"], "exclude": []},
        "provenance": {"kind": "user_reported", "source_refs": []}
      }
    ],
    "soft_priorities": [
      {
        "priority": 1,
        "field": "work_mode",
        "value": "remote",
        "provenance": {"kind": "user_reported", "source_refs": []}
      },
      {
        "priority": 2,
        "field": "compensation",
        "value": {"minimum": 180000, "currency": "USD", "period": "annual"},
        "provenance": {"kind": "imported", "source_refs": [{"family": "g45_reference", "reference_id": "ref_..."}]}
      }
    ],
    "sponsorship_need": {
      "state": "known",
      "value": "needs_sponsorship",
      "provenance": {"kind": "user_reported", "source_refs": []},
      "evidence_refs": []
    },
    "employer_sponsorship_evidence": {
      "state": "unknown",
      "value": null,
      "provenance": null,
      "evidence_refs": []
    },
    "eligibility": {
      "state": "unknown",
      "value": null,
      "provenance": null,
      "evidence_refs": []
    }
  }
}
```

The schema should use dedicated typed definitions for each initial field rather
than `value: {}`. Compensation requires amount, ISO currency, and `annual`,
`monthly`, or `hourly` period; it must not imply that missing salary evidence
is zero. An unknown or declined fact is not a negative answer, and an inferred
fact is not user confirmation.

### 3.2 `experience_qa`

An experience answer is one question record, not a mutable profile projection.
The question remains inspectable across revisions:

```json
{
  "schema_version": "1.0",
  "kind": "experience_qa",
  "payload": {
    "question_id": "experience.harness_engineering",
    "prompt": "Describe experience relevant to harness engineering.",
    "answer_state": "answered",
    "answer": {
      "text": "I built ...",
      "evidence_refs": []
    },
    "provenance": {"kind": "user_reported", "source_refs": []}
  }
}
```

`answer_state` is exactly `missing`, `answered`, `declined`, or
`not_applicable`. `answer` is required only for `answered` and is otherwise
`null`; `provenance` is required for `answered`, `declined`, and
`not_applicable` when the operator explicitly supplied that state. `question_id`
is a bounded stable identifier, not a prompt-generated future Run ID.

An imported answer points to exact source records. An inferred answer may be
stored for transparency, but its provenance remains `inferred` and downstream
consumers must not treat it as user-reported experience. `missing`, `declined`,
and `not_applicable` must never be collapsed into an empty answer or inferred
from absent text.

### 3.3 `supplied_source`

This branch stores attribution and typed links, not imported bytes. G45
references and Run inputs remain canonical:

```json
{
  "schema_version": "1.0",
  "kind": "supplied_source",
  "payload": {
    "source_kind": "project_evidence",
    "label": "Checkout service notes",
    "status": "captured",
    "artifact_refs": [
      {
        "family": "g45_reference",
        "reference_id": "ref_...",
        "record_ref": {"path": "references/ref_.../reference.json", "content_sha256": "sha256:...", "size_bytes": 123, "media_type": "application/json"},
        "snapshot_ref": {"path": "references/ref_.../source.txt", "content_sha256": "sha256:...", "size_bytes": 123, "media_type": "text/markdown"}
      }
    ],
    "notes": "Operator-supplied evidence; truth and freshness remain unresolved."
  }
}
```

`source_kind` is initially closed to `resume`, `project_evidence`,
`role_history`, `cover_letter`, `job_description`, and `supporting_artifact`.
`status` is `reported`, `captured`, or `independently_verified`; the last
requires an exact verification artifact reference and must not be accepted as a
bare assertion. `artifact_refs` may mix G45 and native document/blob branches,
but no branch may contain inline source text.

### 3.4 `selected_conversation`

Conversation capture requires supplied bounded content and records whether that
content is an exact excerpt or a summary:

```json
{
  "schema_version": "1.0",
  "kind": "selected_conversation",
  "payload": {
    "mode": "excerpt",
    "text": "User: ...\nAssistant: ...",
    "source": {
      "source_kind": "supplied_text",
      "label": "Role preference clarification",
      "selection": {"message_ids": ["msg-17", "msg-18"], "note": "Two supplied messages only."},
      "session_reference": null
    },
    "provenance": {"kind": "imported", "source_refs": []}
  }
}
```

`mode` is `excerpt` or `summary`. An excerpt is exact supplied text; a summary
must say what bounded selection it summarizes and is never presented as an
exact transcript. `source_kind` is `supplied_text` or
`declared_session_reference`; a declared session reference is opaque metadata
only. C2 must require the bounded `text` in both modes, never follow a session
locator, inspect ambient history, or capture hidden reasoning. `message_ids`
and `note` are optional, bounded selection metadata; they are not read
capabilities.

## 4. Saved defaults, task overrides, and archive behavior

Scope belongs to the record operation/revision metadata, not to a future Run
ID. Use this closed scope definition:

```json
{"mode": "saved_default", "task_context_id": null, "base": null}
```

or:

```json
{
  "mode": "run_override",
  "task_context_id": "task_<uuidv4>",
  "base": {"record_id": "record_...", "revision_id": "revision_..."}
}
```

Rules:

1. A `saved_default` has no task context and is eligible for future task
   selection. Updating it requires the current revision as the CAS parent.
2. A `run_override` receives a new logical record identity (or an equivalent
   explicitly scoped branch identity) and must name the existing default
   record/revision it started from. Its `task_context_id` is allocated before
   the first override operation by the local operation service; it is not a
   future Run ID and does not require a Plan/Run contract.
3. Subsequent updates to that override require its own current parent and the
   same task context. They cannot change the saved default or become visible
   to another task's resolver. A different task must explicitly create its own
   override from a selected default revision.
4. Promoting an override to a saved default is a separate explicit
   `saved_default` create/update request with the current default parent; there
   is no implicit promotion at task completion.
5. Archiving appends a typed tombstone/archive event naming the record and
   expected current revision. It removes the record from default listing but
   does not delete bytes, rewrite a revision, or invalidate an exact historical
   selection. `list --include-archived`, exact `read`, and history can still
   inspect it. Archiving a default never silently selects another revision;
   archiving an override cannot affect its base default.

The C1 operation key must include scope mode, task context, base revision, actor,
origin, relationships, and normalized native payload. An identical retry returns
the original IDs and receipt; a changed request under that key conflicts. A
task context is an isolation label, not an approval, provider grant, or
execution identity.

## 5. CLI/library acceptance stories

These are C2 command-boundary stories only; they do not define Plan/Run shapes.
Each CLI writer must call the same validated library operation service.

### Saved profile and isolated override

```text
gigai record create --kind profile_preferences --scope saved_default \
  --content-file profile.json --operation-key pref-001 --json
  -> record_id, revision_id, scope=saved_default, committed receipt

gigai record create --kind profile_preferences --scope run_override \
  --task-context task_<uuidv4> --base-record record_... \
  --base-revision revision_... --content-file nyc-search.json \
  --operation-key task-nyc-001 --json
  -> a separate scoped record/revision; saved default is byte-for-byte unchanged
```

The library equivalent accepts a typed sidecar, expected parent, scope object,
actor, origin, and operation key. A fresh process listing saved defaults must
not show the override as a default; listing with the exact task context must
show only that task's override and its selected base metadata.

### Experience lifecycle and archive

```text
gigai record create --kind experience_qa --scope saved_default \
  --content-file unanswered.json --operation-key qa-001 --json
gigai record update --id record_... --parent revision_... \
  --content-file answered.json --operation-key qa-002 --json
gigai record archive --id record_... --revision revision_... \
  --operation-key qa-archive-001 --json
```

The acceptance check starts a fresh process between commands, observes the
unanswered revision, saves an answer, verifies the old revision and exact
source refs remain readable, archives the logical record, and verifies default
listing hides it while exact historical read/selection still succeeds. A stale
parent and a changed operation payload must fail without a new journal commit.

### Supplied source and conversation

`supplied_source` creation accepts only typed G45/native artifact references;
it never accepts an inline duplicate of G45 bytes. Conversation creation
accepts one bounded supplied JSON sidecar with `mode=excerpt` or `mode=summary`.
The acceptance check proves metadata listing omits `text`, explicit read names
one record/revision, and a session locator or unrelated ambient conversation is
never opened.

### Context and rebuild

Provide explicit read-only commands such as:

```text
gigai context rebuild --gig GIG_ID --json
gigai context show --gig GIG_ID --task-context task_<uuidv4> --json
gigai record list --kind experience_qa --include-archived --json
gigai record read --id record_... --revision revision_... --content
```

`context show` returns scoped metadata, relationships/backlinks, safe summaries,
question states, current work, and archive state; it never returns raw native
payloads or source bytes. The explicit named content read is the sole C2
payload-returning inspection path and is not provider disclosure authority.

## 6. C2 acceptance checklist and unresolved choices

Required C2 fixtures should cover:

- schema rejection of unknown fields, wrong branch/kind, missing state/value
  pairings, unbounded strings/lists, invalid provenance, and artifact digest or
  path mismatches;
- hard versus soft preference selection, and independent unknown values for
  sponsorship need, employer evidence, and eligibility;
- all four experience states plus user-reported/imported/inferred provenance,
  with inferred facts never displayed as confirmed experience;
- G45 reference/run-input wrappers that preserve exact original bytes and
  native docs/blobs that use canonical paths without duplicate content;
- excerpt versus summary, explicit supplied selection, opaque session metadata,
  and refusal to follow a locator or read ambient history;
- saved-default/task-override isolation across two task contexts, explicit
  promotion only, stale-parent update, archive, exact historical read, and
  unchanged old selection metadata;
- fresh-process CLI and library recovery of an unanswered question, saved
  answer, override, archive state, scoped context, and redacted listing.

Two details are not fully numerically frozen by the accepted contracts and
should remain visible implementation decisions rather than silent assumptions:

1. The exact native sidecar/list/string limits above are proposed bounded
   defaults. C2 should record them in the schema/CLI contract when implemented;
   changing them later requires an explicit versioned contract update.
2. The initial preference field vocabulary and the lifetime/cleanup policy for
   `task_context_id` are not product-wide decisions. C2 should implement the
   closed initial set and retain historical overrides; it should not invent
   automatic promotion, expiry, deletion, sharing, or cross-Gig behavior.

No C2 shape grants approval, invokes a provider, authorizes a tool, allocates a
Run, or changes the accepted G45/B2 authority boundary. C3/SCOUT-04 can later
consume exact record/revision/content references without changing these native
content semantics.

## 7. Coordinator implementation disposition

The bounded design is suitable as C2 implementation guidance, subject to these
clarifications. This is not runtime acceptance or a replacement product
baseline.

- Keep one closed native-content schema with local reusable definitions. The
  listed limits and initial preference vocabulary are acceptable bounded
  implementation defaults. Preserve accepted outer record kind names and ID
  families from the settled C1 contracts; example shorthand is not a reason to
  rename them or loosen an existing schema.
- The JSON examples above are illustrative, not valid golden fixtures: some
  references deliberately contain ellipses or omit required fields. Committed
  fixtures must carry real canonical IDs, complete references and matching
  bytes, and validate through both schema and semantic checks.
- Employer sponsorship evidence must identify the particular employer/posting
  with an exact source or record reference. A global candidate preference
  cannot establish an employer fact. Known eligibility must likewise name its
  relevant jurisdiction/context. Unknown states need no invented positive or
  negative value. This adds context to the three separate facts; it does not
  decide legal eligibility or introduce automated legal advice.
- Define every uncertain-fact branch explicitly. For `unknown`, `declined` and
  `conflicting`, the primary value is null; conflict evidence remains separately
  referenced. Never infer `false` from absent evidence. A verification artifact
  reference records claimed evidence, not automatic proof of truth.
- Use a separate logical record for a task override, not a branch in the C1
  single-parent revision chain. Task context is an opaque, canonical local
  isolation label, not an Orca Task identity or future Run ID. Provide a usable
  allocation path that returns the label before reuse, and include retry tests
  proving generated context/record IDs do not defeat operation idempotency.
  Do not add a scheduler or automatic context expiration.
- `context rebuild` writes disposable views; it is non-authorizing but is not
  literally read-only. Plain list/context commands return an explicit metadata
  allowlist, not arbitrary payload fragments called "safe summaries". Answer
  text, prompt text, conversation text and document bytes stay behind explicit
  exact content reads; question IDs and answer states are sufficient for the
  fresh-session outstanding-question index.
- Preserve question identity across answer revisions. Exact base/source links
  and old content remain readable after archive. A saved-default/override
  promotion is an explicit new operation with current-parent CAS, never a scope
  rewrite of existing history.
- CLI examples must be reconciled with actual Gig resolution and consistent
  option names during implementation. Prove fresh-process commands, not only
  library construction. No private user workspace is needed for this story.

Design task `task_1eee31ba7393`, dispatch `ctx_a5e2c599de48`, completed with a
report-only handoff. The coordinator read this note in full, released the
settled dispatch (`retained/external_terminal`, no process action), and
acknowledged its completion before further work. C1 remains the sole runtime
writer until its handoff.
