# SCOUT-03 native lane — fresh independent corrections re-review

**Date:** 2026-09-09
**Task:** `task_35685f1719f3` / dispatch `ctx_5838f1c9ae49`
**Reviewer scope:** Read-only. No provider calls, no private real workpads, no
source/schema/test/contract edits, no commits. Coordinator runs the
native/C1/source focused suite separately; this review did not run the full
suite. External lane has an active implementation worker — its changing source
was **not** inspected as acceptance evidence.
**Sole writable file:** this document.

**Inputs read in full:** `SCOUT-03-native-lane-review.md` (prior findings
F1–F8), `SCOUT-03-native-lane-implementation.md` (current), and
`SCOUT-03-C2-record-contracts.md` §7 coordinator disposition (plus §1–§6 for
context).

**Source inspected (current, all untracked/uncommitted):**
`src/gigai/native_records.py`, `src/gigai/native_records_cli.py`,
`src/gigai/schemas/native-record-content.schema.json`,
`tests/test_scout03_native_records.py`, and the called C1 seams in
`private_records.py` / `journal.py` / `canonical.py` / `validators.py` /
`cli.py`.

**Method.** Every claim below is either (a) *source inference* — I followed the
code path — or (b) *probe-verified* — I ran a small disposable Python script
against a throwaway offline Gig under a scratchpad `TMPDIR` and observed the
result. Probe scripts live in the session scratchpad; they are disposable and
touch no project or real workpad state. Each finding is tagged
`[source]` or `[probe]`.

---

## Verdict

**Bounded native acceptance.** All six delivered-lane findings from
`SCOUT-03-native-lane-review.md` (F1 HIGH, F2 MEDIUM, F3/F4/F5/F6 LOW) are
resolved in the current source — F1/F2/F4/F5 by real behavioral fixes that
probe-verify, F3/F6 by explicit documentation in the implementation note that
matches observed behavior. No regression found in locked snapshots, CAS,
replay, history, or privacy. F7 (multiple saved defaults) and F8 (update UX)
remain **C3 deferrals**, explicitly recorded as not-selected in the
implementation note and not implemented here — correct.

No new findings that block native acceptance. Two low-severity notes are
carried for coordinator visibility (N1: schema `task_context_id` regex is
looser than the code check; N2: F7's "no per-kind singleton" is reachable and
should be a conscious C3 input). Neither is a correction demand and neither
requires a new tombstone family.

---

## Prior findings — disposition

### F1 — HIGH — explicit `--task-context` override raised uncaught `TypeError` → **RESOLVED** `[probe]` + `[source]`

`create_task_override` (`native_records.py:430-445`) now pops `base`
**unconditionally** (`kwargs.pop("base", None)`) and rejects a missing or
non-`Mapping` base with a typed `PrivateRecordError("native_record_invalid",
"override base is required")` before any further work. Both the generated path
(allocates a `task_context_...` id, `_generated_task_context=True`) and the
explicit path (`scope` supplied) build the scope through `_scope()` and never
forward a stray `base=` kwarg into `create_native_record`. The explicit path
additionally asserts the supplied scope's `base` equals the `base` argument,
raising `native_record_invalid` on mismatch (`:443-444`). The CLI
`override_command` now also catches `TypeError`
(`native_records_cli.py:83`), so no library-contract slip can surface as a raw
traceback.

Probe results (generated + explicit paths, throwaway Gig):

| Case | Result |
|---|---|
| explicit `--task-context` well-formed override | committed, `task_context_id` preserved verbatim |
| explicit override replay (same operation key) | idempotent — same `record_id`, `created=False` |
| malformed `task_context` (`task_context_not-a-uuid`) | typed `native_record_invalid`, no `TypeError`/traceback |
| absent base (`create_task_override` with neither `base` nor `scope`) | typed `native_record_invalid` "override base is required" |
| inconsistent base (scope base ≠ `base` arg) | typed `native_record_invalid` |

The delivered test
`test_mounted_native_override_cli_normalizes_generated_and_explicit_scope`
(`tests/test_scout03_native_records.py:182-204`) drives the mounted
`record native override` CLI for the generated path, the explicit
`--task-context` path, both replays, and the malformed-context case
(asserting `"Traceback" not in invalid.output`). The prior review's "entire
code path unexercised" no longer holds.

### F2 — MEDIUM — employer/eligibility "known" facts accepted on free text → **RESOLVED** `[probe]` + `[source]`

`_semantic_content` now calls
`_fact(payload["employer_sponsorship"], context_required=True,
evidence_required=True)` and `_fact(payload["eligibility"],
context_required=True)` (`native_records.py:125-126`). `_fact`
(`:95-111`):

- `context_required and state=="known"` → requires a non-blank string
  `context`.
- `evidence_required and state=="known"` → requires
  `provenance.source_refs` to be a **non-empty list**.
- Those `source_refs` are `artifact_ref`s and flow through
  `_assert_refs_authentic` at publish time (`_publish` transaction calls
  `_assert_refs_authentic(sidecar, snapshot)` at `:319`), which binds
  `path`/`digest`/`size`/`media` to the pinned C1 snapshot's committed bytes.

So an employer-sponsorship "known" fact now needs an *exact authenticated
employer-evidence reference*, while a legitimate user-reported eligibility
"known" fact needs its jurisdiction/context but **not** external proof —
matching the §7 disposition ("Known eligibility must likewise name its relevant
jurisdiction/context" but "may remain legitimately user-reported without an
external proof").

Probe results:

| Case | Result |
|---|---|
| employer known, `context` only, `source_refs=[]` | refused `native_record_invalid` "known employer sponsorship requires exact source evidence" |
| employer known, `context` + authentic committed run-input snapshot ref | accepted |
| employer known, `context` + fabricated digest ref | refused `native_record_scope_refused` "not committed exact evidence" |
| eligibility known, `context` present, `user_reported`, no external ref | accepted |
| eligibility known, `context=null` | refused `native_record_invalid` |

Delivered coverage:
`test_known_employer_needs_committed_evidence_but_eligibility_can_be_user_reported`
(`:142-150`).

### F3 — LOW — archive re-serializes the full payload instead of a typed tombstone → **RESOLVED (documented)** `[source]` + `[probe]`

Behavior is unchanged from the prior review: `archive_native_record`
(`native_records.py:463-472`) reuses the current sidecar and `_publish` writes
a fresh full `jsl_blob` copy at `records/<record>/blobs/<archive-rev>.json`.
The implementation note now **explicitly documents this** (§"Saved defaults…",
lines 39-43): *"Current archive revisions deliberately write a new immutable
`jsl_blob` copy of the selected payload so every archived tail keeps the
accepted uniform content shape and exact-content read behavior… This is a
bounded at-rest duplication tradeoff, not a tombstone family or migration;
dedup/tombstone design is deferred."* This satisfies the prior review's remedy
option (a). No new tombstone family is demanded.

Probe: after archive, `read --revision <archive-rev> --content` returns bytes
byte-for-byte equal to the pre-archive selected revision's content; default
`list` hides the archived record; `list --include-archived` shows it with
`state="archived"`; the original revision remains exactly readable.

### F4 — LOW — `conflicting` fact accepted with empty `conflict_refs` → **RESOLVED** `[probe]` + `[source]`

`_fact` (`native_records.py:109-111`): `state=="conflicting"` now requires
`conflict_refs` to be a non-empty list, and those refs are authenticated by
`_assert_refs_authentic` at publish. This applies to all three separate facts
via `_fact` and to soft/hard priority facts via the `_fact(fact)` loop
(`:122-123`).

Probe results:

| Case | Result |
|---|---|
| `conflicting` with `conflict_refs=[]` | refused `native_record_invalid` "conflicting facts require referenced conflict evidence" |
| `conflicting` with fabricated-digest ref | refused `native_record_scope_refused` |
| `conflicting` with authentic committed ref | accepted |

Delivered coverage:
`test_conflicting_facts_require_committed_conflict_evidence` (`:153-165`).

### F5 — LOW — reference authentication ignored `media_type` → **RESOLVED** `[probe]` + `[source]`

`_assert_refs_authentic` (`native_records.py:268-287`) now cross-checks
`media_type` against `_authoritative_media_type(snapshot, path)`
(`:239-265`), which resolves the declared media from the **committed owning
record's metadata** — the G45 `reference.json` / run-input `input.json`
`snapshot.media_type`, or a native blob's `blob_ref.media_type` on the owning
revision — and raises `native_record_scope_refused` "native source media has no
authoritative owner" when no committed owner exists. It does **not** guess from
the filename extension; the docstring is explicit ("filename suffixes are not
authority"). A relabel that disagrees with the owner is rejected with "native
source media does not match committed evidence". Arbitrary relabels of authentic
bytes are therefore blocked while the exact bytes stay pinned by digest.

Probe results (imported `resume.md`, owner media `text/markdown`):

| Case | Result |
|---|---|
| `supplied_source` artifact ref with correct `media_type` | accepted |
| same ref, `media_type` relabeled to `application/json` | refused `native_record_scope_refused` "media does not match committed evidence" |
| fabricated digest | refused `native_record_scope_refused` "not committed exact evidence" |

Delivered coverage: `test_supplied_source_requires_a_real_committed_reference`
(`:121-139`) asserts both the digest-mismatch and media-mismatch refusals.

### F6 — LOW / design note — schema ships a 5th branch `imported_reference` → **RESOLVED (documented)** `[source]` + `[probe]`

The branch is retained. The implementation note now gives an **explicit
rationale** (lines 15-21): *"`imported_reference` is retained as the native
compatibility wrapper for an exact, already-journaled G45 reference or
run-input artifact: it adds no content store, does not relabel imported bytes,
and remains distinct from `supplied_source`'s typed source-status record."*
This satisfies the prior review's remedy option ("fold it into the C2 record
with an explicit rationale").

Source: `_semantic_content` has no `imported_reference` clause, so the branch
gets schema validation (`refs: [artifact_ref]` min 1, `provenance`) plus the
generic `_assert_refs_authentic` sweep over the whole sidecar. Probe: an
authentic ref is accepted; a fabricated-digest ref is refused
`native_record_scope_refused`; the stored sidecar contains only the refs, no
inline source bytes and no relabel. Harmless and now documented; not a
correction demand.

### F7 — NOTE (C3) — multiple `saved_default` records of one kind permitted → **DEFERRAL confirmed**

Still true in source: distinct `operation_key`s with `saved_default` scope
produce independent same-`kind` records; no per-kind singleton constraint
exists and the C2 contract does not require one. The implementation note
(lines 55-59) explicitly records: *"C3 must define how to resolve multiple
saved defaults of one kind… Neither is silently treated as accepted
selection/default behavior here."* Correctly **not implemented in this lane**.
Carried as N2 below only so the C3 resolver treats it as a conscious input.

### F8 — NOTE — `update` requires the caller to reproduce the exact `scope` block → **DEFERRAL confirmed**

Still true: `update_native_record` (`native_records.py:456`) rejects any
content whose `scope` ≠ the previous revision's, and `_validate_content`
requires `scope` in the content file; `update_command` exposes no
`--scope`/`--task-context` option. The implementation note (lines 55-59)
records that C3's "update UX may later derive the immutable scope from the
selected current revision rather than requiring the content file to carry it."
Correctly a **C3 deferral**, not implemented here.

---

## Regression checks — locked snapshots / CAS / replay / history / privacy

All `[probe]` on a throwaway offline Gig, corroborated by `[source]`:

| Property | Result |
|---|---|
| Create replay under same operation key | idempotent — original `record_id`/`revision_id`/receipt returned, `created=False` |
| Same operation key, changed payload | `native_operation_conflict` (payload_sha covers kind, normalized content_sha, scope, origin, actor; +parent for update) |
| Parent CAS on update — stale parent | `stale_parent`, no journal commit |
| Update cannot change kind or scope | `native_record_scope_refused` |
| Archive appends; old revision still exactly readable | archived revision + original revision both readable byte-for-byte |
| Default `list` hides archived; `--include-archived` shows it | confirmed |
| `list` / `native_context` payload-free | no prompt text, answer text, or fact values (`needs_sponsorship`, question prompts/answers) in either projection |
| Task-context isolation | base record byte-for-byte unchanged after override; override is a separate `record_id`; a second distinct record claiming an existing `task_context_id` is refused `native_record_scope_refused` (in-lock scan, `_publish` `:322-334`) |
| Generated-identity retry | normalized `task_context_id="allocated"` in the receipt intent keeps an identical replay idempotent (`native_records.py:417-424`) |
| Storage discipline (`[source]`) | every mutation through `run_with_journal_writer`; receipt lookup, ref auth, override-uniqueness scan, and parent CAS all inside the writer critical section; `writer.record` default `allow_artifact_replacement=False`; snapshot from pinned Git tree; sidecar↔revision bound on both outer `content.content_sha256` and inner `blob_ref` digest/size/path |
| Schema registration (`[source]`) | `native-record-content.schema.json` in `validators.SCHEMA_NAMES:63`; SHA in `SHA256SUMS:55` and `tools/verify_installed_schemas.py:11` = on-disk `cfde09a9…cb1dfe` |
| CLI mount (`[source]`) | `cli.py:3651` `record_group.add_command(native_record_group, name="native")` → `record native <sub>` |

No regression found.

---

## New notes (non-blocking, coordinator visibility)

### N1 — `[source]` — schema `task_context_id` / base-id patterns are looser than the code check

`native-record-content.schema.json:27` validates `task_context_id` as
`^task_context_[0-9a-f-]{36}$` and base ids as `^record_[0-9a-f-]{36}$` /
`^revision_[0-9a-f-]{36}$` — these accept any 36-char hex/hyphen string, not
strictly a UUIDv4 (wrong version nibble, wrong hyphen positions, all-hyphens).
The authoritative check is in code: `_TASK_CONTEXT` (`native_records.py:28`)
is the strict RFC-4122 v4 regex and `_scope` runs `validate_entity_id(...,
expected_prefix=...)` on the base ids. So malformed ids are still refused
(`native_record_invalid`), and the probe confirms `task_context_not-a-uuid` is
rejected. The schema is only a weak backstop here. Not a defect — the code
gate is correct — but if the schema is meant to be self-sufficient (e.g. for
external validators of committed sidecars) the pattern should be tightened to
the v4 shape. No action required for native acceptance.

### N2 — `[source]` — F7 "no per-kind singleton" is reachable; make it an explicit C3 input

Confirmed reachable in source and correctly out of scope for this lane. Flag
for the C3 selected-default resolver: it must define which revision a task
selects when several `saved_default` records of one `kind` exist. Recorded
here so it is a conscious C3 decision, per §7 ("Neither is silently treated as
accepted selection/default behavior here").

---

## Source inference vs. probe — summary

- **Probe-verified** (throwaway offline Gig, scratchpad `TMPDIR`, disposable
  scripts): F1 all five cases; F2 all five cases; F4 three cases; F5 three
  cases; F6 three cases; every row of the regression table except the four
  marked `[source]`.
- **Source inference only** (followed the code path, not run): storage-
  discipline internals (writer critical section, `allow_artifact_replacement`,
  pinned-tree snapshot), schema/SHA/CLI-mount registration, N1, N2, and the
  F3/F6 documentation-match assessment (behavior itself was probed; the
  "matches the note" judgment is a read).
- **Not inspected:** external lane source (active implementation worker); full
  test suite (coordinator-run); any private/real workpad.
