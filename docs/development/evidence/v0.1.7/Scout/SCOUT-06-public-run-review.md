# SCOUT-06 public research Run review (independent, review-only)

Date: 2026-09-10. Independent review of the settled public research Run
runtime: `src/gigai/external_recording.py`, `src/gigai/external_cli.py`,
`graph_set.py` v2 output-contract admission, and the five
`external-recording-*-v2` schemas with their validator/inventory
registrations.

Review only. No source, schema, test, fixture, or golden was modified; no
provider ran; nothing was committed, tagged, or published. `git diff --stat`
over the reviewed source and `find src tests -newermt '-40 minutes'` both
confirm no file under `src/` or `tests/` changed during this review.

Reviewed against [persistence decisions](SCOUT-06-persistence-decisions.md),
[coordinator integration](SCOUT-06-coordinator-integration.md), and the prior
[packet/source review](SCOUT-06-packet-source-review.md).

## Verdict

**One blocking correctness defect (D1) and two non-blocking findings (D2, D3).**

D1 is a real, reproducible protocol-integrity break on the public CLI: the
idempotent-replay short circuit runs *before* the version-dispatch guard, so a
`--protocol-version 1` caller receives a v2 record for `start` and `cancel`.
The same request is correctly refused `external_protocol_downgrade` on a fresh
operation key. This contradicts "version dispatch must be deliberate and fail
closed on unsupported versions."

D2 and D3 are defence-in-depth gaps, not exploitable through the real API.

Everything else in the dispatch's inspection list held under probing: strict
shape, committed graph/contract/source authority, safe exact-ref reads,
writer-lock atomicity, tuple journal publication, replay/CAS on the fresh
path, refusal-without-publication, and meaningful typed error outcomes.

Per the dispatch, Luna's concurrently-edited hardening tests were not run and
the already-recorded packet-review findings F1–F3 are not reopened.

## Method

Focused source reading plus disposable probes in a session scratchpad, each
building a throwaway `tmp` Gig (setup → default init → `approve_offline`) and
driving the real `external_recording` API and the real `gigai external` CLI.
The prohibited suites were **not** rerun: no full suite, no wheel build, no
provider, no private workpad, and no broad test reruns. The coordinator's
cited runs (integration 5 / 15.26s, contracts 61 + 205 subtests / 0.72s, 63
schema verifier) are taken as given and were not duplicated.

Probes wrote only into `tempfile.mkdtemp` directories and the scratchpad.

## D1 — blocking — replay short circuit bypasses v2→v1 version dispatch

**Where.** `src/gigai/external_recording.py:1072` (`start`),
`external_recording.py:1784` (`_progress`, serving `checkpoint`/`cancel`), and
`external_recording.py:2148` (`_submit`). In each, `replay = _replay(snap,
invocation)` returns early at the line shown, while the
`external_protocol_downgrade` guard that compares the committed record's
`schema_version` against the caller's `protocol_version` sits immediately
after, at lines 1080, 1790, and 2154 respectively.

**Root cause.** `_replay` matches on `(operation, operation_key,
payload_sha256)` only. `payload_sha256` is computed in `_invocation` over the
`normalized` dict built at `external_recording.py:330-338`, whose keys are
exactly `operation, project_id, gig_id, origin, actor, input`. It does **not**
include `protocol_version`. Verified by a pure probe mirroring that dict:

```
payload_sha256 (identical for v1 and v2): sha256:54f150424ccbbc...
normalized keys: ['actor','gig_id','input','operation','origin','project_id']
-> excludes protocol_version: True
```

So for any operation whose v1 and v2 *input shapes are identical*, a v1
invocation and a v2 invocation produce the same `payload_sha256`, `_replay`
treats them as the same operation, and the v2 record is returned before the
version check ever runs.

**Reachability is exactly `start` and `cancel`.** Their inputs
(`{run_plan_id}` and `{run_id, reason}`) are shape-identical across versions.
For `plan`, `checkpoint`, and `submit` the v1 invocation schema rejects the
v2-shaped input at `_invocation` before `_replay` is reached — probed, each
refuses `external_invocation_invalid`. So D1 does **not** let a v1 caller
obtain a v2 succeeded receipt or a v2 checkpoint.

**Repro (real public CLI, throwaway Gig).**

```
external plan  --protocol-version 2 (role_request)  -> exit 0, schema_version 2.0
external start --protocol-version 2 --operation_key K2 -> exit 0, schema_version 2.0

# same operation_key, downgraded protocol:
external start --protocol-version 1 --operation_key K2
  -> exit 0
  -> schema_version returned to the v1 caller: 2.0     <-- v2 record on a v1 request

# control, fresh operation_key:
external start --protocol-version 1 --operation_key K2-fresh
  -> exit 1
  -> {"error":{"code":"external_protocol_downgrade",
       "message":"Plan protocol version does not match this operation",
       "next_action":"use_matching_external_protocol"}}
```

`cancel` reproduces identically at the API level: a v2 cancel under key `KC`
followed by a v1 cancel under the same key returns `created=False,
outcome=cancelled, schema_version=2.0` to the v1 caller, whereas a v1 cancel
of the same v2 Run under a *fresh* key correctly refuses
`external_protocol_downgrade`.

**Why it matters.** A v1 caller is contractually entitled to a v1 record. Here
it receives a v2 document whose nested `invocation`, `run_plan` and (for
cancel) receipt carry `schema_version: "2.0"`, without any error. A v1 reader
that validates the response against the v1 schema will fail on evidence the
service reported as success; one that does not validate will silently consume
a shape it does not understand. The guard exists and is correct — it is simply
unreachable on the replay path.

**Severity.** No journal corruption occurs and nothing new is published: the
replay returns committed bytes unchanged, and the writer lock, CAS, and
immutability are unaffected. The break is confined to the response contract
and to the fail-closed version-dispatch requirement. It is nonetheless a
blocking correctness defect against the stated decision, because the refusal
that the decision requires is bypassed by an ordinary, legitimate retry.

**Minimal fix (owner's call; not applied).** Move the `schema_version` /
`protocol_version` comparison so it runs *before* the `_replay` short circuit
in all three call sites, or fold `protocol_version` into the `normalized` dict
that produces `payload_sha256`. The first is narrower and preserves existing
exact-replay digests; the second changes every `payload_sha256` and would
invalidate committed replay identities, so it should be avoided.

## D2 — non-blocking — the Plan-pinned domain requirement is enforced only in schema

The persistence decision states: "Checkpoint and submit enforce the
Plan-pinned domain requirement even if a caller tries to submit a legacy
envelope without the domain fields."

In the executable layer this is not the case. At
`external_recording.py:1889-1897`, a v2 checkpoint item is accepted if it is
*either* `is_domain_item` (five keys) *or* `is_check_item` (three keys), with
no cross-check that an item whose `kind` is a sealed **output** kind must
carry the domain channel. Classification at `external_recording.py:1949-1965`
then only asks whether the sidecar is output-shaped and the kind is in
`requested_outputs`; a domain-less `research` item satisfies both.

Probed by neutralizing only the two JSON-schema gates in memory (no file
edited) and re-running a real checkpoint:

```
=== schema gate bypassed: domain-less 'research' output ===
*** ACCEPTED by the executable layer: ['research']
*** artifact keys: ['kind','markdown','sidecar']
```

**Not exploitable through the real API.** Both the v2 invocation schema
(`artifact_refs.items.oneOf` → `artifact_input` requires `domain_sidecar` and
`supporting_artifacts`) and the v2 checkpoint schema (`$defs.output`, same
required list) refuse it unconditionally on write. Probed through the normal
path, the attempt refuses `external_invocation_invalid`. The loop is closed
again on read: continuing the bypassed probe, the *next* operation refuses
`external_reconciliation_required` — "recorded evidence is not a valid
envelope" — because `_recorded_dispatch` revalidates the committed checkpoint
bytes. So the committed record cannot reach a succeeded receipt either.

Recorded so the owner can decide deliberately whether the decision's wording
requires an executable check as well as the schema one. This is the same
class of gap as the already-recorded F1/F2 (schema-vs-code parity), inverted:
here the *schema* is the strict layer and the *code* is the permissive one,
which is the weaker direction, since a future schema edit would silently
remove the only enforcement. A minimal executable guard would be to require
`is_domain_item` whenever `item["kind"]` is in `requested_outputs` and the
Plan's contract declares a `domains` entry for it.

## D3 — non-blocking — `_approved_fields` v2 branch drops v1's field bounds

At `external_recording.py:541-560` the v2 (`allow_domains`) branch validates
`fields` with only: non-empty list, each item a non-empty alnum/`_`/`-`
string, and uniqueness. The v1 branch at `external_recording.py:594-607`
additionally enforces `len(fields) <= LIMITS["max_artifacts_per_operation"]`
(32) and `len(field) <= 64`. Probed against mirrored predicates:

```
    200-char field: v2_branch=True   v1_branch=False  graph_set=False
         64 fields: v2_branch=True   v1_branch=False  graph_set=False
   Uppercase field: v2_branch=True   v1_branch=True   graph_set=False
     digit-leading: v2_branch=True   v1_branch=True   graph_set=False
```

**Not exploitable.** `graph_set._domain_output_contract_valid`
(`graph_set.py:88-135`) is strictly tighter — `1 <= len(fields) <= 32` and
`re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", item)` — and it gates Graph Set
admission, so no such contract can be approved in the first place. The v2
branch also pins `schema_id`/`validator_id` to the two research constants and
requires both refs to resolve to `research.schema.json` / `research.py`,
which independently bounds the reachable field set to `["research"]`.

Recorded as an inconsistency worth closing when the v2 branch is next
touched, not as a defect requiring action now.

## What was verified as correct

### Version dispatch and v1 compatibility (apart from D1)

`_recorded_dispatch` (`external_recording.py:362-380`) reads
`schema_version`, maps `"1.0"`/`"2.0"` to distinct schema resources, and
refuses anything else with `external_protocol_unsupported` /
`use_supported_external_protocol`. It never coerces a v2 record through a v1
schema. `_invocation` refuses any `protocol_version` outside `{1, 2}` with
the same typed code. The CLI's `--protocol-version` is a
`click.Choice(("1","2"))` defaulting to `"1"`, and `_call` resolves the `_v2`
function by exact name, refusing `external_protocol_unsupported` when an
operation has no v2 form. On the fresh (non-replay) path the downgrade guard
fires correctly for `start`, `checkpoint`, `cancel`, and `submit` — verified
for `cancel` and `start` by probe.

v1 remains intact: a v1 plan with a `g45_run_input` on a fresh Gig succeeds
with `schema_version: "1.0"`, and v1 callers refuse the `role_request` family
by default (`allow_role_request=protocol_version == 2` at the four call
sites).

### Strict shape and typed refusals

The v2 invocation schema is a genuine gate, not decoration. Probed the submit
v2 path with `domain_sidecar` set to a string, an int, and `None`, and
`supporting_artifacts` set to an int and to a list of strings — all five
refuse `external_invocation_invalid` before reaching
`external_recording.py:2313-2317`, where `dict(item["domain_sidecar"])` and
`[dict(entry) for entry in item["supporting_artifacts"]]` would otherwise
raise an unhandled `TypeError`/`ValueError`. That matters because
`external_cli._call` catches only `ExternalRecordingError`, so an escape there
would surface as a raw traceback; a pure probe confirmed `dict("SECRET…")`
leaks a length derived from the payload into its `ValueError`. The schema
closes this, and the malformed-value probe found no reachable escape.

An unknown domain `schema_id` refuses `external_domain_unsupported` /
`use_supported_external_domain` — the dispatch table
`_FIXED_DOMAIN_VALIDATORS` (`external_recording.py:108`) is closed, with no
caller registration, dynamic import, subprocess, or network lookup.

### Committed graph / contract / source authority

`_graph_context` (`external_recording.py:1321-1345`) reads the selected graph
through `_committed_ref` — i.e. `_approved_ref` byte/symlink revalidation plus
`read_committed_artifact` journal provenance — and requires the path to be
under `manifests/`, `graph_id` to equal the Plan's `goal_graph_id`, and
`graph_version` to be a strict `int >= 1` (`type(x) is not int`, so `bool` is
refused). This matches the coordinator's note that the broad manifests
snapshot was removed in favour of the exact committed reference.

`_validate_domain_binding` (`external_recording.py:1363-1432`) authenticates
both the schema ref and the validator source ref as committed artifacts, then
compares their bytes against the packaged `FIXED_DOMAIN_RESOURCES` read from
`resources.files("gigai")`. A mismatch refuses `external_authority_mismatch`.
No Gig-owned Python is imported or executed by the recorder.

### Safe exact-ref reads

`_safe_ref` refused every hostile variant probed: absolute path, `..`
traversal, backslash separator, size mismatch, digest mismatch, extra key,
missing path, and `size_bytes=True` (correctly rejected despite `bool` being
an `int` subclass, via `type(size) is not int`). Only the exact valid ref was
accepted.

`_approved_ref` walks every path component checking `is_symlink()`. On a
throwaway tree it accepted a real file and refused both a symlinked file and a
traversal through a symlinked directory, each with "approved contract path is
redirected".

### Writer-lock atomicity and tuple publication

A valid mixed research/check checkpoint published its complete tuple in a
single journal transition: one handoff
(`handoffs/000000000009-external-recording-checkpointed.txt`) plus
`01.md`, `01.json`, `01.domain.json`,
`01.supporting/role_capture.bin`, `01.supporting/role_review.bin`,
`02.md`, `02.json`, and the checkpoint record — all inside one
`run_with_journal_writer` call, with the domain sidecar and supporting bytes
persisted alongside the Markdown and generic envelope as the decision
requires.

### Refusal publishes nothing

Two hostile checkpoints were driven against a real Run:

* a domain packet whose `research.role_title` no longer matches the sealed
  role request — refused `research_domain_invalid`;
* supporting bytes swapped between `role_capture` and `role_review` while
  keeping both digests internally consistent — refused
  `research_domain_invalid`.

In both cases the workpad gained **zero** files. The fixed packaged validator,
not the generic recorder, made the semantic judgement, and no partial
transition was left behind.

### Replay / CAS on the fresh path

An exact v2 submit replayed under the same operation key returned
`created=False` with a byte-identical receipt. A v1 submit under that key with
a *different* payload correctly refused `external_operation_conflict`
("operation key already records a different payload"), so changed intent still
conflicts. Immutable-artifact conflicts are mapped to
`external_operation_conflict` and other journal conflicts to
`external_reconciliation_required` / `reconcile_journal` in `_journaled`.

### `check` versus domain enforcement

Beyond D2, the check path is sound: `_check_sidecar_is_valid` pins the exact
four-key shape, a `sha256:`-prefixed 71-char digest, and `result in {pass,
fail}`; submit additionally requires `result == "pass"`, that the check's
`output_sha256` binds a *submitted* output digest, that the check kind is in
the sealed completion contract, and that `check_kinds` equals the required
set exactly. Submit re-reads and revalidates the committed tuple under the
lock (`recorded[0] != expected_recorded` compares the full tuple including
`domain_sidecar` and `supporting_artifacts`), and it republishes only a
receipt — never the checkpoint artifacts.

### Graph Set v2 admission

`_domain_output_contract_valid` admits only the closed five-key descriptor
with `schema_version "2.0"`, `kind "run_output_contract"`, matching `gig_id`,
`domains` keyed exactly by `fields`, the two pinned research identity
constants, and per-ref path hygiene (no absolute path, no `..`, no NUL, no
backslash, POSIX-normalized, exact basename, exact media type, `sha256:` +
64 hex, strict `int` size). It performs no execution and no import, matching
the coordinator's description. v1 field-list contracts retain their exact
prior behaviour via the `schema_version == "2.0"` branch in
`_attached_contract_valid`.

## Remaining gates (not delivered, not claimed)

These are open by design at this checkpoint and are **not** implied to be done
by anything above:

* **Historical research reuse.** The distinct research-output input variant
  described in the persistence decisions ("Reusing completed research") is not
  implemented. There is no input family that resolves a prior succeeded
  receipt plus Plan/Run/checkpoint identity and output tuple, and no code path
  authenticates historical source/schema identity from committed records.
  Grep over the reviewed source found no such family.
* **Installed workflow verification.** The 63-resource verifier result is a
  development-environment run, not an isolated-wheel proof, and no
  fresh-session installed-caller integration was exercised in this review.
* **User UAT and release acceptance.** Not performed here; the source and
  contract remain a candidate with no default promotion.
* **Luna's real failure-path tests** (`task_722bebf61b6d`) were active during
  this review and were deliberately not run or read for verdict purposes.

## Explicitly out of scope

* Discovery lane and packet-only re-review; F1–F3 from the
  [packet review](SCOUT-06-packet-source-review.md) are recorded and were not
  reopened, and no cosmetic schema-parity change is requested here.
* Prohibited suites, full suite, wheel builds, providers, private workpads,
  and commits — none were run.
* Generic architecture expansion: no restructuring is proposed, only the
  three concrete findings above.
