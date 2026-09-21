# SCOUT-03 native-record lane — fresh independent source review

**Date:** 2026-09-08
**Reviewer scope:** Source only. No tests run, no source changed, no provider
calls, no commits. Coordinator is running focused tests separately.
**Files reviewed:** `src/gigai/native_records.py`,
`src/gigai/native_records_cli.py`,
`src/gigai/schemas/native-record-content.schema.json`,
`tests/test_scout03_native_records.py`, and the called C1 seams in
`src/gigai/private_records.py`, `src/gigai/journal.py`, `src/gigai/canonical.py`,
`src/gigai/validators.py`.
**Contracts consulted:** `SCOUT-03-native-lane-implementation.md`,
`SCOUT-03-C2-record-contracts.md` (incl. §7 coordinator disposition),
`SCOUT-03-correction-plan.md` C2 section, `SCOUT-00-contract-amendments.md`
§3 and §B2.
**Method:** Followed the actual code paths; did not infer behavior from the six
focused tests. Where the tests pass only because a check is weak, that is called
out.

---

## Verdict

**Changes requested.** One HIGH-severity delivered-lane bug (F1: the
`create_task_override` / `record native override` explicit-`--task-context`
path raises an uncaught `TypeError` and is entirely untested). One MEDIUM
divergence from the coordinator disposition (F2: employer-sponsorship "known"
facts are accepted on free text alone, with no exact source/record reference).
The remaining findings are LOW severity (extra copy of sensitive payload on
archive; unchecked `conflicting`-fact evidence; `media_type` not bound on
reference authentication; an undocumented 5th schema branch) plus notes for the
C3 resolver.

The core storage discipline is sound: every mutation goes through
`run_with_journal_writer`; receipt lookup, ref authentication, task-context
uniqueness, and parent CAS all run **inside** the writer critical section
(`native_records.py:276-315`); `writer.record` is called with the default
`allow_artifact_replacement=False`, so immutable sidecar/revision/receipt
publication is no-clobber (`journal.py:937-955`); snapshots are taken from a
pinned Git tree with byte-for-byte working-tree verification
(`journal.py:1078-1125`); sidecar↔revision binding is checked on both the outer
`content.content_sha256` and the inner `blob_ref` digest/size/path
(`native_records.py:199-219`). Generated-identity retry is normalized so an
identical replay returns the original IDs and receipt
(`native_records.py:369-379`, `_from_receipt` at `252-263`).

Known-missing items that are **not** counted against this lane: C3
provider/tool binding, Plan/Run selection, and the external executor
(SCOUT-04). Inventory fixtures remain coordinator work per the correction plan.

---

## Findings

### F1 — HIGH — `record native override --task-context <id>` raises an uncaught `TypeError`

**Path:** `src/gigai/native_records.py:382-390` (`create_task_override`),
reached from `src/gigai/native_records_cli.py:79-85` (`override_command`).

**Defect.** `override_command` unconditionally passes `base={...}` to
`create_task_override` (`native_records_cli.py:82`). `create_task_override`
only pops `base` from `kwargs` on the *generated* path:

```python
def create_task_override(**kwargs: Any) -> NativeRecordResult:
    scope = dict(kwargs.pop("scope", {}))
    generated = not scope
    if generated:
        base = kwargs.pop("base")          # popped ONLY when scope is empty
        ...
    return create_native_record(**kwargs, scope=scope, _generated_task_context=generated)
```

When the operator supplies `--task-context`, `override_command` builds a
non-empty `scope` dict (`native_records_cli.py:81`), so `generated` is `False`,
`base` is **not** popped, and `create_native_record(**kwargs, ...)` is invoked
with `base=` still present. `create_native_record` has no `base` parameter, so
Python raises `TypeError: create_native_record() got an unexpected keyword
argument 'base'`. `override_command` catches only
`(PrivateRecordError, WorkpadError, OSError, ValueError)`
(`native_records_cli.py:83`), so the `TypeError` escapes as an unhandled
traceback rather than a typed `{"status":"error",...}` payload.

**Counterexample.**
`gigai record native override --content-file o.json --base-record record_<uuidv4>
--base-revision revision_<uuidv4> --task-context task_context_<uuidv4>
--operation-key k1 --json`
→ unhandled `TypeError`, non-zero exit, Python traceback on stderr. The same
crash occurs for the library call
`create_task_override(..., base={...}, scope={"mode":"run_override",...})`.
A well-formed `--task-context` still crashes; the code never reaches `_scope()`.

**Why the six tests miss it.** `test_task_override_is_separate_record_and_
generated_context_replays` (`tests/test_scout03_native_records.py:94-101`) only
calls `create_task_override(..., base=...)` **without** `scope`/`--task-context`
(generated path). `test_fresh_process_style_cli_group_story` never invokes the
`override` command. No test passes an explicit scope or `--task-context` to
either the library or the CLI, so this entire code path is unexercised.

**Minimum remedy.** Pop `base` unconditionally in `create_task_override` and
drop the redundant top-level `base` on the explicit-scope path (the base is
already inside the supplied `scope`), e.g.:

```python
def create_task_override(**kwargs: Any) -> NativeRecordResult:
    scope = dict(kwargs.pop("scope", {}) or {})
    base = kwargs.pop("base", None)
    generated = not scope
    if generated:
        factory = kwargs.get("uuid_factory", uuid.uuid4)
        scope = {"mode": "run_override",
                 "task_context_id": _id(EntityPrefix.TASK_CONTEXT, factory),
                 "base": base}
    return create_native_record(**kwargs, scope=scope, _generated_task_context=generated)
```

Add `TypeError` (or a broader guard) to the CLI's caught exceptions so no
library-contract slip can surface as a raw traceback, and add a focused test
for `override` with an explicit `--task-context`, covering both the well-formed
and the malformed identifier.

Secondary: on the generated path `base = kwargs.pop("base")` has no default, so
`create_task_override()` with neither `base` nor `scope` raises `KeyError`
rather than a `PrivateRecordError`. The remedy above (`pop("base", None)`)
covers this; add a typed error if `base` is required and absent.

---

### F2 — MEDIUM — employer-sponsorship / eligibility "known" facts accepted on free text, no exact source required

**Path:** `src/gigai/native_records.py:95-104` (`_fact`), called from
`_semantic_content` at `118-119`; schema `fact` def at
`native-record-content.schema.json:29`.

**Defect.** The C2 coordinator disposition (`SCOUT-03-C2-record-contracts.md`
§7) is explicit:

> Employer sponsorship evidence must identify the particular employer/posting
> with an exact source or record reference. A global candidate preference
> cannot establish an employer fact. Known eligibility must likewise name its
> relevant jurisdiction/context.

`SCOUT-00-contract-amendments.md:148` names the field "employer sponsorship
**evidence**". The delivered check for `employer_sponsorship` and `eligibility`
only requires a **free-text `context` string** when `state == "known"`:

```python
if context_required and state == "known" and not isinstance(context, str):
    raise PrivateRecordError(... "known employer or eligibility facts require context")
```

Nothing requires `provenance.source_refs` to be non-empty, and nothing requires
`context` to name a committed reference. The schema permits
`provenance.kind == "user_reported"` with `source_refs: []`.

**Counterexample.** A `profile_preferences` sidecar with

```json
"employer_sponsorship": {
  "state": "known", "value": "offers_sponsorship",
  "context": "Acme role", "conflict_refs": [],
  "provenance": {"kind": "user_reported", "source_refs": []}
}
```

is accepted and journaled. `context` is arbitrary text; no exact
employer/posting reference is bound. This is precisely the "bare assertion /
global candidate preference establishes an employer fact" case the disposition
forbids. `tests/test_scout03_native_records.py:51` passes only because the
fixture's `context="posting ref_9d4f514e-..."` is treated as opaque text and no
reference is checked.

**Minimum remedy.** For `employer_sponsorship` and `eligibility` with
`state == "known"` (and arguably `declined`/`conflicting`), require at least one
entry in `provenance.source_refs` (or a dedicated `evidence_ref`) that
`_assert_refs_authentic` can bind to committed bytes, and keep `context` as
the human-readable jurisdiction/posting label. Update the fixture accordingly.

---

### F3 — LOW — archive re-serializes the full sensitive payload into a new blob instead of a typed tombstone

**Path:** `src/gigai/native_records.py:408-417` (`archive_native_record`) →
`_publish` at `266-315`, which always writes both `revision_path` **and**
`sidecar_path` (`native_records.py:302`).

**Defect.** `SCOUT-03-C2-record-contracts.md` §4 rule 5 describes archiving as
appending "a typed tombstone/archive event naming the record and expected
current revision". `SCOUT-00-contract-amendments.md:452` states the Scout
revision record "does not duplicate their blob". `archive_native_record`
instead reuses the previous sidecar (`native, _ = _sidecar(...)` at line 414)
and hands it to `_publish`, which writes a **fresh full copy** of the entire
profile / experience / conversation / supplied-source payload to
`records/<record>/blobs/<new-revision-id>.json`. The implementation note says
"preserve old sidecars" but does not disclose that archive also emits a second
identical copy of the sensitive content.

**Impact.** Not a leak or corruption — same scope, immutable, `private_sensitive`
— but it doubles the at-rest copies of PII-adjacent content and makes
`read --revision <archive-rev> --content` return the full payload again. The
design appears deliberate (keeping every chain tail a uniform `jsl_blob` so
`_native_rows`/`_sidecar` don't special-case a tombstone), so this is a
conscious trade-off that should be **documented** rather than left implicit, or
replaced with a typed tombstone `content` family that `list --include-archived`
and `read` handle explicitly.

**Minimum remedy.** Either (a) add one sentence to the implementation note and
C2 record stating archive re-copies the payload by design, or (b) give the
archive revision a distinct `content.family` (e.g. `archive_tombstone`
referencing the prior revision id) and teach `_sidecar`/`_native_rows`/`read`
to resolve archived rows to their last non-archived sidecar.

---

### F4 — LOW — `conflicting` uncertain fact is accepted with empty `conflict_refs`

**Path:** `src/gigai/native_records.py:95-104` (`_fact`); schema `fact` at
`native-record-content.schema.json:29` (`conflict_refs` required but
`minItems` unset).

**Defect.** The disposition (`SCOUT-03-C2-record-contracts.md` §7) says for
`conflicting` "conflict evidence remains separately referenced". `_fact` only
checks that a non-`known` state has a null primary `value`; it never verifies
that `state == "conflicting"` carries at least one `conflict_refs` entry. A
fact can therefore claim a conflict with zero supporting evidence.

**Counterexample.**
`{"state":"conflicting","value":null,"context":null,"conflict_refs":[],
"provenance":{"kind":"user_reported","source_refs":[]}}` is accepted.

**Minimum remedy.** In `_fact`, when `state == "conflicting"` require
`len(value["conflict_refs"]) >= 1`; those refs already flow through
`_assert_refs_authentic` for byte binding.

---

### F5 — LOW — reference authentication binds path/digest/size but not `media_type`

**Path:** `src/gigai/native_records.py:232-249` (`_assert_refs_authentic`).

**Defect.** `SCOUT-03-C2-record-contracts.md` §2 ("Artifact reference"):
"path, digest, size, and media must agree with the committed bytes."
`_assert_refs_authentic` matches on `path`, `content_sha256`, and
`size_bytes`, but ignores the `media_type` field that the `artifact_ref`
schema requires. A sidecar can therefore declare
`media_type: "application/json"` for a committed `source.txt` that is actually
`text/markdown`.

**Impact.** Low — the digest still pins the exact bytes, so no content
substitution is possible; only the declared media label can be wrong, which a
downstream consumer keying on `media_type` could mishandle.

**Minimum remedy.** Cross-check `media_type` against the committed record's
`snapshot.media_type` (for `references/`/`run-inputs/` paths) or reject a
reference whose `media_type` disagrees with the owning record's declared type.

---

### F6 — LOW / design note — schema ships a 5th branch (`imported_reference`) not enumerated by the C2 contract

**Path:** `native-record-content.schema.json:9,16,33`;
`src/gigai/native_records.py:29` (`_KIND`).

**Observation.** `SCOUT-03-C2-record-contracts.md` §3 enumerates four native
content branches: `profile_preferences`, `experience_qa`, `supplied_source`,
`selected_conversation`. The delivered schema and `_KIND` add
`imported_reference` (`{refs: [artifact_ref], provenance}`), overlapping
`supplied_source`'s "attribution and typed links" purpose. The implementation
note (`SCOUT-03-native-lane-implementation.md:19`) mentions it, but it is not
in Luna's contract §3 nor called out in the §7 disposition. `_semantic_content`
has **no** branch for `imported_reference`, so it gets schema + generic
`_assert_refs_authentic` validation only.

**Impact.** Harmless (refs are authenticated), but it is contract scope creep:
a branch exists that was never specified while the specified branches are the
ones that needed delivering. Either fold it into the C2 record with an explicit
rationale or drop it in favour of `supplied_source`.

---

### F7 — NOTE (C3, not this lane) — multiple `saved_default` records of one kind are permitted

`create_native_record` with distinct `operation_key`s and `saved_default`
scope produces independent saved-default records of the same `kind`; there is
no per-kind singleton constraint, and the C2 contract as written does not
require one. `native_context` / `list_native_records` will surface all of them.
The C3 selected-default resolver must define which revision a task selects when
several saved defaults of a kind exist; flagging so it is a conscious decision,
not a silent one.

---

### F8 — NOTE — `update` requires the caller to reproduce the exact `scope` block, with no CLI affordance

`update_native_record` (`native_records.py:393-405`) rejects any content whose
`scope` does not byte-equal the previous revision's
(`native.get("scope") != old.get("scope")` at line 401), and `_validate_content`
requires `scope` to be present in the content file. `update_command` exposes no
`--scope` / `--task-context` option, so to update a `run_override` record the
operator must hand-write the full `run_override` scope (mode, exact
`task_context_id`, `base.record_id`, `base.revision_id`) into the content JSON.
Workable, but error-prone; consider deriving `scope` for `update`/`archive`
from the current revision server-side rather than requiring it in the payload.

---

## Checks performed and found sound

- **Genuine default/override semantics.** `_scope` (`native_records.py:335-343`)
  admits exactly the closed `saved_default` triple or a `run_override` with a
  regex-checked `task_context_id` and a `{record_id, revision_id}` base. Update
  and archive refuse any `scope` or `kind` change
  (`native_records.py:401-402`). Override creation records a
  `task_override_base` relationship pointing at the exact committed base
  revision, verified via `_chain` (`native_records.py:360-368`).
- **Provenance.** Schema `provenance.kind` closed to
  `user_reported|imported|inferred`; `source_refs` are `artifact_ref`s bound to
  committed bytes by `_assert_refs_authentic`. `inferred` is stored but carries
  its own kind; nothing in the lane promotes it (rendering/selection is C3).
- **Hard vs soft preferences.** `hard_constraints` is a closed 5-key object;
  `soft_priorities` is a bounded (`maxProperties: 5`) map. `_semantic_content`
  (`native_records.py:110-119`) rejects any key appearing in both, so a soft
  priority cannot shadow a hard filter.
- **Separate sponsorship / employer / eligibility facts.** Three distinct
  `fact` refs, each independently `_fact`-checked; `unknown/declined/
  conflicting` force a null primary value ("unknown stays unknown"). (Evidence
  strength gap is F2/F4.)
- **Four-state questions.** Schema `state` enum is
  `missing|answered|declined|not_applicable`; `_semantic_content:120-132`
  enforces answer-text presence only for `answered`, forbids answer text for
  the other three, requires `provenance` for `answered|declined|
  not_applicable`, and rejects duplicate `question_id`s.
- **Archive / history / replay / CAS / concurrency.** Parent CAS
  (`native_records.py:297-300`) and receipt replay
  (`_existing_receipt` at `222-229`, `_from_receipt` at `252-263`) both run
  inside the `run_with_journal_writer` critical section. `_chain`
  (`161-196`) follows the authenticated `parent_revision` links and rejects
  ambiguous roots, branches, cycles, and orphaned revisions — no lexical UUID
  ordering. Concurrent same-key creates collapse to one commit + one replayed
  result; concurrent different-key creates produce distinct records (see F7).
  Historical `read --revision` works after archive
  (`native_records.py:420-430`); `list` hides archived rows unless
  `--include-archived`.
- **Exact-reference and blob binding.** `_sidecar` (`199-219`) requires
  `content.family == "jsl_blob"`, an exact
  `records/<record>/blobs/<revision>.json` path, and the committed bytes'
  digest to equal **both** `blob_ref.content_sha256` and the outer
  `content.content_sha256`, plus `blob_ref.size_bytes == len(data)`.
- **Invalid content refusal.** `_validate_content` (`80-92`) runs the closed
  Draft 2020-12 schema, a 262 144-byte cap, a kind/payload type gate, then
  `_semantic_content`; it also runs on **read** via `_sidecar`, so a tampered
  committed sidecar is refused at read time. Missing schema file →
  `native_record_invalid`.
- **Metadata privacy.** `read_native_record` without `--content`
  (`428-430`), `list_native_records` (`448-451`), and `native_context`
  (`454-467`) return only IDs, kind, state, scope, timestamps, relationships,
  and — for context — `{record_id, question_id, state}` for `missing`
  questions. No prompt text, answer text, conversation text, or payload bytes.
  `session_reference` and `message_ids` are never dereferenced;
  `selected_conversation.text` is stored verbatim as supplied with no ambient
  read.
- **Same operation key, different payload.** `_existing_receipt` raises
  `native_operation_conflict` when a matching key carries a different
  `payload_sha256`. The `payload_sha` covers `kind`, normalized
  `content_sha256`, `scope`, `origin`, and `actor` (plus `parent_revision` for
  update); the generated-`task_context` normalization
  (`native_records.py:369-379`) keeps an identical retry idempotent while a
  changed request under the same key conflicts. A `run_override` cannot be
  split across two records: the in-lock scan at `native_records.py:284-296`
  rejects a second distinct record claiming an existing `task_context_id`.
- **CLI registration state.** `native_record_group` is now mounted at
  `cli.py:3651` as `record native <sub>` (not the standalone `record` group the
  implementation note describes — that note is stale). Central schema
  `native-record-content.schema.json` is registered in
  `validators.SCHEMA_NAMES:63` and its SHA in `SHA256SUMS:55` /
  `tools/verify_installed_schemas.py:11` matches the file on disk
  (`cfde09a9…cb1dfe`). The delivered CLI has no `--kind` flag (kind comes from
  the content file) — a documented reconciliation difference from the C2
  acceptance-story shorthand, not a defect.
