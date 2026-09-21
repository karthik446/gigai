# SCOUT-05 reviewed-manifest legacy-selection guard: independent acceptance review

Date: 2026-09-10. Bounded read-only acceptance review of the new
`reviewed_manifest_requires_successor` guard, its two `lifecycle.py` callsites,
and `tests/test_scout05_reviewed_manifest_guard.py`. Scope was fixed by the
dispatch: the settled successor corrections (F1/F2) and the public
`prepare-successor` adapter were independently accepted in
`SCOUT-05-successor-and-prepare-acceptance-review.md` and were not reopened.
No source, test, schema or evidence file outside this report was created or
edited. No pytest suite was rerun beyond the new guard file, no full suite, no
wheel or build, no providers, no network, no live user `.gigai` state, and no
repository commits. All probes were disposable pytest files under the session
scratchpad against `tmp_path` homes/targets/workpads.

## Verdict

**Changes requested (one blocking finding).**

The guard is correct and well-built for the property it actually enforces:
authority is taken only from committed journal provenance, working-tree bytes
cannot manufacture or suppress it, and every malformed/missing/conflicted
authority shape refuses instead of falling back to the legacy branch. Legacy
compatibility is preserved on the no-manifest and never-reviewed paths, and no
schema family changed.

It does not, however, close the carried-forward residual it is scoped to close.
The guard keys on the **manifest identity**, while the property that must hold
is **content-keyed**. A byte-for-byte copy of the reviewed manifest saved under
a fresh, never-committed `manifest_id` still carries
`security_review.status == "passed"`, `availability_state == "available"` and
`created_by = {kind: "gigai", id: "capability-review"}`, yet
`read_committed_artifact` raises `JournalArtifactMissingError` for that unused
key, the guard returns `False` at its first branch, and the legacy
`--capability-manifest-id` branch binds it into the active pointer with no
successor authentication and no approval-time source revalidation. This
reproduces on both paths the dispatch names — normal pre-Commit-A/tag and
post-tag recovery — and with tampered reviewed source, exit 0 and a clean tree.

The blast radius is unchanged from the prior review's residual: the runtime
source gate still refuses execution and single-publisher still holds. What is
still lost is the approval gate, which is exactly what this lane set out to
restore.

---

## F-A (Blocking) — the guard is identity-keyed, so a renamed copy of the reviewed manifest still takes the legacy branch

**Where.** `src/gigai/capability_successor.py:725-812`. The first lookup is
`read_committed_artifact(path=f"manifests/capabilities/{manifest_id}.json")`.
`src/gigai/capability_successor.py:746-750` treats
`JournalArtifactMissingError` as "no immutable generic-review provenance" and
returns `False`. That inference is sound for *this key* but not for *these
bytes*: the reviewed manifest's content is fully reproducible under any other
key, and `capability_manifest_artifact_ref`
(`src/gigai/capabilities.py:296-319`) then validates only schema, path/identity
agreement and Gig ownership on **working-tree** bytes.

The `created_by` recognition at `src/gigai/capability_successor.py:775-786`
does not help here. It sits inside the *decision*-missing branch, which is
reached only after the manifest itself was proven committed. On the renamed-copy
path the function has already returned at line 750, so `created_by` is never
consulted.

**Reproduction — normal path, pre-Commit-A/tag** (disposable `tmp_path`,
service level, reusing the shipped `_reviewed_ordinary_amendment` fixture; the
only edit is one working file):

```python
home, target, instance, workpad, ordinary, manifest_id, review = \
    _reviewed_ordinary_amendment(tmp_path)
src = workpad / f"manifests/capabilities/{manifest_id}.json"
body = parse_json_bytes(src.read_bytes())
rogue_id = f"capmanifest_{uuid.uuid4()}"
body["manifest_id"] = rogue_id                      # only field changed
(workpad / f"manifests/capabilities/{rogue_id}.json").write_bytes(
    canonical_json_bytes(body))
approve_offline(home_root=home, requested_target=target,
                proposal_id=ordinary.proposal_id, gig_id=instance.gig_id,
                capability_manifest_id=rogue_id)
```

Observed:

| Assertion | Result |
| --- | --- |
| Outcome | **approved, `version == 2`** (no refusal) |
| Active pointer changed | yes |
| `pointer.capability_manifest.path` | `manifests/capabilities/capmanifest_a87d06e9-….json` (the rogue key) |
| Rogue manifest `security_review` | `{"status": "passed", "checks": [reviewer_evidence, source_containment, source_inventory, native_record_effect_allowlist], …}` |
| Rogue manifest `created_by` | `{"kind": "gigai", "id": "capability-review"}` |

**Reproduction — post-tag recovery path**, same construction plus a tampered
reviewed tool source (`tools/cap_…071/record_tool.py` appended to), crashing at
`after_approval_tag` on a legitimate ordinary no-manifest approval and then
recovering with `capability_manifest_id=rogue_id`:

| Assertion | Result |
| --- | --- |
| Recovery outcome | **approved, `version == 2`** |
| Pointer `capability_manifest` | the rogue key |
| Rogue manifest publishers (`git log -- <path>`) | 1 |
| Reviewed manifest publishers | 1 (unchanged) |
| `git status --porcelain` | empty |

**Direct guard matrix** (calling `reviewed_manifest_requires_successor`
straight, same fixture) isolates the boundary and shows every *other* property
holding:

| Case | Result |
| --- | --- |
| Committed reviewed manifest | `True` — guard engages |
| Rogue id, no file on disk | `False` |
| **Rogue id, reviewed bytes on disk (uncommitted)** | **`False` — the defect** |
| Committed reviewed id, working bytes downgraded to `security_review.status = "not_run"` | `True` — working edits cannot hide committed provenance |
| Committed reviewed id, working decision file deleted | `True` — working decision bytes are not an input |
| `manifest_id = "../../etc/passwd"` | refused, `capability_successor_manifest_invalid` |

Rows 4-6 are precisely the properties the implementation doc claims, and they
hold exactly as written. Row 3 is the gap.

**Why this is blocking for this dispatch.** The dispatch asks whether a
reviewed service manifest can be *newly bound* via an ordinary no-sidecar
proposal on both paths. It still can. The lane's own framing —
"refuses the reviewed binding at approval rather than relying on runtime
rejection" — does not hold against a caller who can write a working manifest
file, and that caller is the same one the guard's shipped tests already assume
(they edit the working decision and tool bytes). The prior review's residual
was recorded as audit **F2 (Blocking safety)**; this lane narrows it but does
not close it.

**Suggested shape of the fix** (no new mechanism required; the evidence is
already reachable). Make the decision content-keyed rather than key-keyed: in
the legacy branch, when the *selected working manifest* asserts
`security_review.status == "passed"` for any capability (or carries
`created_by = {kind: "gigai", id: "capability-review"}`), require committed
generic-review provenance for **that manifest's own id** and refuse when it is
absent — instead of inferring "no provenance ⇒ legacy manifest" from a missing
committed artifact. The existing
`reviewed_manifest_requires_successor` body can stay as the positive proof; the
change is that a reviewed-looking manifest with no committed publication must
become a refusal rather than a `False`. Suggested regressions: (a) renamed copy
of a reviewed manifest through an ordinary no-sidecar proposal refuses with
HEAD/tag/pointer unchanged; (b) the same through post-tag recovery; (c) the
existing `tests/test_scout05_tool_crud.py` operator-authored manifest (schema
valid, `security_review.status != "passed"`, never committed by a review) still
approves unchanged.

---

## What the guard does get right (verified, not merely read)

| Property | Result |
| --- | --- |
| Authority comes from committed journal provenance, not mutable flags or path existence | **Holds.** Both reads go through `read_committed_artifact`, which reads bytes from `{pinned_head}:{path}` (`journal.py:1082`), requires exactly one publisher (`journal.py:1073`), a complete single-handoff publication (`journal.py:1076-1079`), the right `gig_id`, an unambiguous `artifact_refs` entry, and a digest+size match. Working-tree bytes are never the authority. |
| Editing or deleting working decision bytes cannot suppress the guard | **Holds.** Probe rows 4-5 above; also the shipped `test_reviewed_manifest_cannot_bind_through_ordinary_proposal_or_mutated_decision`, which edits `reviewer_rationale` and tampers tool bytes and still refuses. |
| The old `created_by` forgeability class is not reintroduced | **Holds.** The `created_by` read at `capability_successor.py:775-786` is on **committed** manifest bytes returned by `read_committed_artifact`, and it can only *escalate* to a refusal (a review-produced manifest whose decision is missing), never grant the legacy branch. This is defence-in-depth, not an authority decision. Structurally it is also near-unreachable: `capability_review.review_local_tool` publishes the decision and the reviewed manifest in one atomic `writer.record` transition (`capability_review.py:596-611`), so a committed reviewed manifest always has its manifest-keyed decision. |
| Malformed / missing / conflicted reviewed authority refuses rather than falling back | **Holds.** `JournalConflictError` on either read, a non-`Mapping` parse, `manifest_id`/`gig_id` disagreement, a schema-invalid manifest, a schema-invalid decision, a mismatched `reviewed_manifest_ref`, or `reviewer_outcome != "passed"` each raise `capability_successor_authority_unavailable`. Only the two `JournalArtifactMissingError` branches return `False`, and only the first of those is the defect above. |
| Identity is validated before any path is built | **Holds.** `_id(manifest_id, _MANIFEST_ID, …)` at `capability_successor.py:735` runs first; both paths are then composed from a regex-pinned `capmanifest_<uuid4>` stem. `"../../etc/passwd"` refuses. |
| Guard runs inside the writer lock before Commit A/tag | **Holds.** `preflight_successor` (`lifecycle.py:2049-2071`) is passed as `preflight=` to `record_transition_chain` (`lifecycle.py:2157`), and `journal._record_chain` calls it at `journal.py:346-347` **inside** `_writer_lock` and **before** `_record_transition_locked`. Refusals in my probes and the shipped tests leave HEAD, tags and the pointer byte-identical. |
| Guard runs in recovery before any missing Commit B, including the already-published-pointer early return | **Holds.** `recover()` (`lifecycle.py:2229-2256`) calls the guard at the top of the function body, before the `pointer_path.exists()` block and before the `payload.get("journal_commit") == sealed_commit` early return at `lifecycle.py:2266`. A reviewed manifest cannot be smuggled in through the consistency-check return either. |
| No-manifest ordinary approval unaffected | **Holds.** Guard is short-circuited by `capability_manifest_id is not None` at `lifecycle.py:2060` / `2244`. Probe: ordinary no-manifest approval succeeds to `version == 2`, pointer `capability_manifest` stays `null`. |
| Never-reviewed legacy fixture compatibility | **Holds by construction and by the lane's reported regression set.** An operator-authored working manifest has no committed artifact at its key, so the guard returns `False` and the existing path is untouched — this is the same first branch as F-A, i.e. the compatibility guarantee and the defect are two faces of one decision. `tests/test_scout05_tool_crud.py`'s `_approved_crud_tool` is exactly this shape (`materialize_capability_manifest` then `approve_offline(capability_manifest_id=_MANIFEST)`). *My own hand-built legacy probe was rejected as schema-invalid — that was a probe-construction artifact (I downgraded `availability_state`/`security_review` in a way the manifest schema couples), not a guard behaviour; I did not re-derive a valid one and rely on the CRUD fixture instead.* |
| Safe unchanged inherited reviewed refs | **Holds.** The guard is only consulted when `capability_manifest_id` is supplied. An approval that inherits a previously bound reviewed manifest without re-passing the id never reaches the guard, and the recovery consistency branch compares the existing pointer's ref rather than rebinding. |
| Exact single-publisher preservation | **Holds.** The guard performs no writes at all — it is a pure read predicate. Post-approval publisher counts in my probes: reviewed manifest 1, rogue manifest 1. `read_committed_artifact` continues to return cleanly for the reviewed manifest. |
| No schema family changed by this lane | **Confirmed.** `git diff --stat HEAD` attributes no schema delta to this lane; the dirty schema files are prior accepted work. `tools/verify_installed_schemas.py` → `verified 58 installed GigAI schemas`. |

## Evidence-accuracy note on the implementation doc

The doc states the focused test "uses real disposable Scout candidate
materialization, **the public review service**, and an ordinary Graph Set
amendment with no successor sidecar."

Read precisely, this is **service-level, not public-CLI**. The shipped test
drives `gigai.capability_review.review_local_tool`,
`gigai.lifecycle.propose_graph_set_offline` and
`gigai.lifecycle.approve_offline` directly; it never invokes the `gigai`
Click CLI. That is a fair description of "the public review service" as a
service API, and the fixture provenance is genuinely real (`_candidate` →
`initialize_defaults(inventory=scout_candidate_inventory())` reads the shipped
`gigai.data/scout` source; nothing is hand-marked passed). But it is a weaker
evidence level than the prior acceptance review's residual reproduction, which
ran the **real public CLI** end to end. Callers comparing the two should not
read the new test as CLI-level proof. Recommend the doc say "the review
service" rather than "the public review service", or add a CLI-level case.

This is a wording/evidence-level note, **not** a defect and **not** part of the
blocking finding — my own F-A reproductions are service-level too, and the
legacy branch they exercise is the same code the CLI reaches through
`cli.py:2451-2463`.

Two smaller accuracy notes, both minor and non-blocking:

- "An operator-authored legacy manifest with no committed generic-review
  provenance remains on its existing path" is accurate, but it is the *same*
  branch that produces F-A. The doc presents it as a scoped exclusion; it is
  better described as the guard's load-bearing assumption, and F-A is the
  case where that assumption is false.
- The doc's claim that the guard "refuses the reviewed binding at approval
  rather than relying on runtime rejection" is true only for the original
  identity. For a renamed copy the system is still relying on runtime
  rejection (`scout_tools._inventory` → `tool_inventory_changed`), exactly as
  the prior review described the residual.

`revise --capability-manifest-id` (audit F7) is confirmed **already gone**:
`grep -n capability_manifest_id src/gigai/cli.py` shows the flag only on
`approve` (`cli.py:2451`, `cli.py:2463`). The doc's decision to leave F7 out of
scope is correct because there is nothing left to guard there.

## Commands run at this root

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q tests/test_scout05_reviewed_manifest_guard.py --tb=short` | `2 passed in 6.22s` |
| `.venv/bin/python tools/verify_installed_schemas.py` | `verified 58 installed GigAI schemas` |
| `uv run ruff check src/gigai/lifecycle.py src/gigai/capability_successor.py tests/test_scout05_reviewed_manifest_guard.py` | `All checks passed!` |

The implementer's reported `20 passed in 97.87s` across
`tests/test_scout05_capability_successor.py`,
`tests/test_scout05_capability_prepare_cli.py` and
`tests/test_scout05_tool_crud.py` was **not** rerun here — the dispatch reserves
that to the implementer's report and limits this root to the new guard and
schema checks. It is repeated as their claim, not verified by me.

## Probe inventory

All probes were disposable pytest files under the session scratchpad, never in
the repository, run against the working tree at `fda4857` plus the dirty lane.

| Probe | Cases | Result |
| --- | --- | --- |
| A — reviewed bytes under a fresh uncommitted `manifest_id`, ordinary no-sidecar proposal, normal path | 1 | **bypass confirmed**, version 2, pointer rebound |
| B — direct `reviewed_manifest_requires_successor` matrix (committed reviewed, absent rogue, rogue-with-reviewed-bytes, working-bytes downgrade, working-decision delete, path traversal) | 6 | 5 hold as documented; rogue-with-reviewed-bytes is F-A |
| C — same bypass through post-tag recovery with tampered reviewed source; publisher counts; tree state | 1 | **bypass confirmed**, 1 publisher each, clean tree |
| D — hand-built never-reviewed operator manifest | 1 | inconclusive (probe-construction artifact: schema-invalid manifest); deferred to the CRUD fixture |
| E — no-manifest ordinary approval and inherited pointer ref | 1 | holds, pointer `capability_manifest` stays `null` |

## Acceptance limits

This review covers only: `reviewed_manifest_requires_successor` in
`src/gigai/capability_successor.py`, its two callsites in
`src/gigai/lifecycle.py` (`preflight_successor` and `recover`), and
`tests/test_scout05_reviewed_manifest_guard.py`. The already-accepted successor
corrections and prepare adapter were not reopened. The unrelated dirty lane work
in `lifecycle.py` / `journal.py`, the research candidate directory, and other
dirty files were not inspected.

Not assessed and not implied: full-suite status, build/wheel/release readiness,
package or release validation, schema registration beyond the 58-resource
verifier run, default capability eligibility, effect-consent presentation, the
capability-review UI, provider execution, OS-account sandboxing, real private
user state, installed-workflow dogfood, and overall SCOUT-05 completion. No
approval, activation, installation or execution was performed against any real
user state, and no broad safety or release claim is made here.
