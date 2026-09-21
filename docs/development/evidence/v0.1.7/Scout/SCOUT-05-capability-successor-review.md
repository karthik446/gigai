# SCOUT-05 reviewed-capability successor: independent approval/recovery review

Date: 2026-09-10. Independent read-only review of the successor preparation,
approval preflight, and crash recovery surfaces. No source, test, schema or
evidence file outside this report was edited. No full suite, build, wheel or
release verification was run; root owns the combined suite. Candidate research
files were treated as active Terra scope and were not inspected or edited.

## Verdict

**Accept the bounded preparation/replay/publication core; do not accept the
approval gate as written.** The sidecar contract, under-lock binding
authentication, exact replay, stale-base refusal, immutable artifact ownership,
single-publisher property and repeated-recovery idempotence all hold under
adversarial probing. Two defects must be corrected before any public prepare
CLI or approval UX is wired on top of this service:

- **F1 (blocker)** — a crash after Commit A/tag makes recovery of any *first*
  approval permanently impossible via `UnboundLocalError`. This is a regression
  against `HEAD` and is not successor-specific.
- **F2 (blocker)** — the "this proposal requires its successor binding" guard
  reads a **mutable working-tree field**, so a reviewed capability manifest can
  be bound into the active pointer with the successor authentication (including
  source revalidation) skipped entirely.

Neither defect is a whole-SCOUT-05 judgment, and neither implies release
readiness. The public prepare CLI remains next-lane work, not a current failure.

All probes below were bounded and disposable: `tmp_path` workpads, the real
shipped `gigai.data` Scout source, the real review service, no network, no
providers, no repository commits, and no writes outside pytest temp dirs.

---

## F1 (blocker) — first-approval crash recovery raises `UnboundLocalError`

`src/gigai/lifecycle.py:2247-2257`. Inside `_recover_approved_publication.recover`,
`payload` is bound **only** inside `if pointer_path.exists():`, but the
`if not isinstance(payload, dict):` check that follows sits at function scope:

```python
if pointer_path.exists():
    pointer = pointer_path.read_bytes()
    ...
    payload = parse_json_bytes(pointer)
if not isinstance(payload, dict):        # <-- unbound when pointer is absent
    raise LifecycleError("existing active-version pointer is malformed")
```

The absent-pointer case is exactly the canonical crash window this function
exists to serve: Commit A and the tag are durable, Commit B never ran, so
`manifests/active-gig-version.json` does not exist yet.

**Regression, not pre-existing.** `git show HEAD:src/gigai/lifecycle.py`
(lines 1590-1620) returns early inside the `if pointer_path.exists():` block and
falls through cleanly when the pointer is absent. The restructure that added the
`is_previous_pointer` branch hoisted the guard out of the block without hoisting
the assignment.

**Exact reproduction** (disposable probe, no successor involved):

```python
home, target, instance, workpad = _candidate(tmp_path)   # no active pointer yet
def crash(step):
    if step == "after_approval_tag":
        raise RuntimeError("disposable crash after sealed approval tag")
with pytest.raises(RuntimeError):
    approve_offline(home_root=home, requested_target=target,
                    proposal_id=str(instance.proposal_id),
                    gig_id=instance.gig_id, observer=crash)
approve_offline(home_root=home, requested_target=target,      # recovery
                proposal_id=str(instance.proposal_id), gig_id=instance.gig_id)
```

Observed:

```
src/gigai/lifecycle.py:2256: in recover
    if not isinstance(payload, dict):
E   UnboundLocalError: cannot access local variable 'payload'
    where it is not associated with a value
```

**Impact.** A Gig whose *first* approval is interrupted after the tag is
unrecoverable through the supported path: every retry raises an untyped
`UnboundLocalError` rather than publishing the missing Commit B. Blast radius is
wider than this lane — it hits ordinary v1/v2 first approvals with no capability
involvement at all.

**Why the existing suite misses it.** Both recovery tests
(`test_approval_crash_after_tag_recovers_without_duplicate_review_manifest`,
`test_recovery_refuses_swapped_binding_before_commit_b`) build on `_fixture`,
which approves a base version *first*. A v1 pointer therefore always exists and
`pointer_path.exists()` is always true.

**Fix.** Initialize `payload = None` before the `if`, or move the malformed check
inside the block. Add a regression whose fixture has no prior active pointer.

---

## F2 (blocker) — successor binding requirement keyed on a mutable field

`src/gigai/lifecycle.py:2058-2066` (approval) and `2238-2246` (recovery). The
guard that forces a successor proposal to carry its committed binding is:

```python
if (
    successor_approval is None
    and is_v2
    and isinstance(proposal.get("created_by"), Mapping)
    and proposal["created_by"].get("id") == "capability-successor"
):
    raise LifecycleError("reviewed capability successor requires its committed binding")
```

`proposal` here is parsed from `manifests/gig-proposal.json` — the **mutable
current-proposal slot**, deliberately writable and never compared to HEAD before
this check. The authority decision is therefore keyed on a string the caller
controls. When both the sidecar is absent *and* `created_by.id` is anything else,
control falls through to the legacy `capability_manifest_id` branch, which
publishes the manifest into the pointer with **no** successor authentication, and
in particular **no approval-time source revalidation**.

**Exact reproduction.** Prepare a legitimate successor, then apply two edits:

```python
prepared = prepare_capability_successor(...)              # legitimate
pid = str(prepared.proposal["proposal_id"])

p = parse_json_bytes((workpad/"manifests/gig-proposal.json").read_bytes())
p["created_by"] = {"kind": "operator", "id": "local-user", "model_target": None}
(workpad/"manifests/gig-proposal.json").write_bytes(canonical_json_bytes(p))

sidecar = workpad / str(prepared.binding_ref["path"])
saved = sidecar.read_bytes()
sidecar.unlink()                                          # working-tree only

src = workpad/"tools"/"cap_...071"/"record_tool.py"
src_saved = src.read_bytes()
src.write_bytes(src_saved + b"\n# TAMPERED post-review\n")

approve_offline(..., proposal_id=pid, capability_manifest_id=manifest_id)
```

Observed: `ACCEPTED 2 with TAMPERED source and NO successor check`. The active
pointer carries the reviewed `capability_manifest` ref.

**Both edits are required, and neither is durable.** Flipping `created_by` alone
is correctly refused (`successor_approval_context` still finds the sidecar and
raises `reviewed capability source is unavailable`). Deleting the sidecar alone
is caught by the `created_by` guard. Only the pair bypasses.

**The dirty-tree mitigation does not survive cleanup.** Immediately after the
bypass the tree is dirty (` D manifests/capability-successors/<id>.json`) and the
runtime refuses execution with `tool_authority_unavailable` because
`index._require_clean_authority` sees uncommitted divergence. But the sidecar is
still present **in HEAD** — the deletion was working-tree only — so an ordinary
`git checkout -- .` restores it, yields a clean tree, and the tool then executes:

```
GIT STATUS AFTER APPROVAL: ' D manifests/capability-successors/gp_....json'
GIT STATUS AFTER RESTORE:  ''
wrapper rc 0 {"ok": true, ...}
```

**Precise blast radius.** Tampered source bytes do *not* execute: restoring a
clean tree also restores the reviewed source, and the runtime source gate
independently refuses modified bytes. What is lost is the **approval gate
itself** — approval succeeds while the source is tampered, the successor
authority (decision, parent manifest, operator consent, source inventory) is
never checked, and the manifest binding is published on the strength of a
working-tree string. Runtime remains the only surviving control. Treat this as a
failure of the authority the lane claims to add, not as arbitrary code execution.

**Fix (evidence is already available).** Both authoritative sources survive the
attack and are committed:

- `git ls-tree -r HEAD -- manifests/capability-successors/` still lists both
  `<pid>.json` and `<pid>/proposal.json` after the bypass.
- The immutable pending copy still reads `created_by.id == "capability-successor"`.

So the guard should key on committed evidence, not the mutable file — e.g. refuse
whenever a committed sidecar or pending copy exists for `proposal_id` and
`successor_approval is None`, and derive `created_by` from the pending copy. A
`read_committed_artifact`/`_committed_file` probe on
`manifests/capability-successors/<pid>.json` is sufficient and is already imported
in this module.

**Regressions to add.** (a) sidecar deleted from the working tree only, with
`created_by` flipped — must refuse; (b) the same, followed by `git checkout -- .`
— must leave the pointer unchanged.

---

## F3 (minor) — stale-base refusals report a review-domain code

`prepare_capability_successor` refuses stale base authority with
`capability_successor_review_invalid`:

| Input | Code returned |
| --- | --- |
| `base_version=2` while pointer is at v1 | `capability_successor_review_invalid` |
| wrong `base_proposal_id` | `capability_successor_review_invalid` |
| foreign `gig_id` | `capability_successor_authority_unavailable` |
| `base_version` 0 / -1 | `capability_successor_base_invalid` |

The refusals are correct and nothing is published; only the diagnostic is
misleading. `_review_authority` compares the caller's base against the decision
before `_current_base` runs, so a stale *base* surfaces as a *review* fault. A
caller cannot distinguish "your base moved, re-read the pointer" from "this
review decision is wrong." Prefer `capability_successor_current_conflict` (or
`_base_invalid`) for base mismatches. Evidence precision only — no behavior
change required for this lane.

---

## What was verified and holds

Each item was probed adversarially, not merely read.

| Property | Result |
| --- | --- |
| Under-lock base/proposal/source/decision binding | **Holds.** `_review_authority` re-runs in full under the writer lock in both `prepare` and `successor_approval_context`. |
| Committed binding cannot be replaced by working bytes | **Holds.** A discriminating `decision_ref.content_sha256` swap is refused (`successor binding is unavailable`) by `_committed_file`'s HEAD comparison. *(An earlier probe that appeared to pass was writing byte-identical content — the committed sidecar already lists all three operations — so it was a no-op, not a finding.)* |
| Immutable artifact ownership / single publisher | **Holds.** `git log` shows exactly **1** publisher for the reviewed manifest after approval, and still **1** after four repeated recoveries. `_preflight_successor_artifacts` exempts only `manifests/gig-proposal.json`. |
| Exact replay, no publication | **Holds.** Same operation key returns `replayed=True` with identical proposal/binding, unchanged HEAD and unchanged successor tree. |
| Stale base refusal | **Holds** (diagnostic code aside — see F3). |
| Second pending successor refused | **Holds** (`capability_successor_pending_conflict`). |
| Omitted vs explicit manifest selection | **Holds.** Omitting `capability_manifest_id` still binds the reviewed manifest from the sidecar; explicit selection agrees; a mismatched explicit ID is refused with no new handoff. |
| Crash after Commit A/tag, repeated recovery | **Holds for successors** (a base version exists). Four consecutive recoveries return identical `(version, publication_commit, sealed_commit)`, 1 manifest publisher, 2 pointer commits. **Fails for first approvals — see F1.** |
| Prior v1 history and legacy approvals | **Holds.** The v1 pointer remains readable at the old publication commit; `active_version == 1` there while the successor is live at 2. |
| New reviewed manifest cannot bypass binding via an unrelated *normal* proposal | **Fails — see F2.** |
| Sidecar schema strictness | **Holds.** `additionalProperties: false` at every level, closed operation enum, `effects` pinned `const ["write_workpad"]`, permissions const-pinned. Registered in `validators.SCHEMA_NAMES` and `SHA256SUMS` (`3924222…a2bf8`). |
| Root wrapper test uses shipped source + real review service | **Confirmed.** `scout_candidate_inventory()` → `scout_source_files()` reads `files("gigai.data")/scout`; the fixture calls the real `review_local_tool`. Nothing is hand-marked passed. |
| Index projection tolerates the new transition | **Holds.** `read_index` succeeds after `capability_successor_prepared`. |

`test_approval_crash_after_tag_recovers_without_duplicate_review_manifest`
passes on its own (1 passed in 4.42s), consistent with the handoff.

---

## Priorities

1. **F1** — fix the unbound `payload`; add a no-prior-pointer recovery
   regression. Blocks first-approval crash recovery generally.
2. **F2** — re-key the successor-binding guard onto committed evidence; add the
   two regressions above. Blocks any public approval UX built on this service.
3. **F3** — diagnostic precision for stale-base refusals. Evidence-only.

## Acceptance limits

This review covers only the files in scope: `capability_successor.py`, the
sidecar schema, the successor-related `lifecycle.py` preflight/publication/
recovery paths, the successor-related `journal.py` preflight/callback/locking
changes, and `tests/test_scout05_capability_successor.py`. The `journal.py` and
`lifecycle.py` diffs also carry substantial unrelated lane work (layout
migration, graph-set proposal, external recording) that was **not** reviewed.

Not assessed and not implied: full-suite status, build/wheel/release readiness,
default capability eligibility, provider execution, OS-account sandboxing, real
private state, the public prepare CLI, and overall SCOUT-05 completion. The
public prepare CLI is next-lane work, not a current failure.
