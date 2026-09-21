# SCOUT-05 reviewed-manifest guard corrections: independent acceptance review

Date: 2026-09-10. Bounded read-only acceptance review of the F-A correction
described in `SCOUT-05-reviewed-manifest-guard-corrections.md`: the new
`_uncommitted_manifest_requires_successor`, `_stable_reviewed_manifest_identity`
and `_committed_generic_reviewed_manifests` helpers, the reworked
`reviewed_manifest_requires_successor`, its two existing `lifecycle.py`
callsites, and `tests/test_scout05_reviewed_manifest_guard.py`.

Accepted successor-preparation and review-CLI scope was not reopened. No
source, test, schema or evidence file outside this report was created or
edited. Only the new guard suite and the schema verifier were run at this root;
no other pytest suite, no build/wheel, no providers, no network, no live user
`.gigai` state, no repository commits. All probes were disposable pytest files
under the session scratchpad against `tmp_path` homes/targets/workpads.

## Verdict

**Changes requested (one blocking finding).**

The correction is a genuine and substantial improvement over the identity-keyed
guard. The prior review's exact F-A reproduction — a byte-for-byte reviewed
manifest saved under a fresh `manifest_id` — is now **closed** on all three
surfaces the doc claims (normal approval, post-tag recovery, public Click CLI),
and every authority property I probed holds: authority is taken only from
committed journal provenance, working bytes can neither manufacture nor erase
it, and missing/conflicted/mismatched shapes fail closed. The disputed legacy
`scout-c3-test` fixture is genuinely preserved, and the correction achieves that
without a blanket rejection of `security_review.status == "passed"`.

It does not, however, fully close the equivalence class it defines for itself.
`_stable_reviewed_manifest_identity` projects the **whole capability list** and
compares it for exact equality, so any change to the *shape of the list* —
appending one extra capability, or reordering — yields a different projection
while leaving the reviewed capability byte-identical inside the manifest that
gets bound. The reviewed capability still carries `security_review.status =
"passed"` and `availability_state = "available"`, still binds its exact
`tool_binding`/`inventory_sha256`, and is bound into the active pointer with no
successor authentication and no approval-time source revalidation — with
tampered reviewed source and exit 0.

Blast radius is unchanged from the prior review's residual: the runtime source
gate still refuses execution, single-publisher still holds, and the guard
performs no writes. What is still lost is the approval gate.

---

## F-A1 (Blocking) — the stable projection is whole-manifest, so a decoy capability or a reorder evades the match

**Where.** `src/gigai/capability_successor.py:922-946`
(`_stable_reviewed_manifest_identity`) and its single use at
`src/gigai/capability_successor.py:897-902`. The projection builds

```python
canonical_json_bytes({
    "schema_version": ..., "gig_id": ...,
    "capabilities": [ {k: cap.get(k) for k in _STABLE_REVIEWED_CAPABILITY_FIELDS}
                      for cap in capabilities ],   # ordered, whole list
})
```

and `_uncommitted_manifest_requires_successor` accepts a refusal only on exact
byte equality of that whole projection against each committed reviewed
manifest. The per-capability field selection is well chosen — I verified against
the manifest schema that the eleven `_STABLE_REVIEWED_CAPABILITY_FIELDS` cover
every security-relevant capability property, and that the five excluded ones
(`availability_state`, `security_review`, `compatibility`, `alternatives`,
`options`) are exactly the presentation/review fields the doc says it excludes.
The defect is not *which fields* are projected; it is that the comparison is
**list-equality rather than per-capability containment**.

Because the container is compared, a caller controls the projection without
touching the reviewed capability at all.

**Reproduction — normal path, pre-Commit-A/tag** (disposable `tmp_path`,
service level, reusing the shipped `_reviewed_ordinary_amendment` fixture; the
only edit is one working file):

```python
body = parse_json_bytes((workpad / f"manifests/capabilities/{manifest_id}.json").read_bytes())
rogue = f"capmanifest_{uuid.uuid4()}"
body["manifest_id"] = rogue
body["created_by"] = {"kind": "operator", "id": "local-user", "model_target": None}
decoy = parse_json_bytes(canonical_json_bytes(body["capabilities"][0]))
decoy["capability_id"] = f"cap_{uuid.uuid4()}"       # clone with a fresh id
body["capabilities"].append(decoy)                    # capabilities[0] untouched
(workpad / f"manifests/capabilities/{rogue}.json").write_bytes(canonical_json_bytes(body))
```

Observed, with the reviewed tool source additionally tampered before approval:

| Assertion | Result |
| --- | --- |
| Manifest schema validity | `True` |
| `_claims_generic_review(candidate)` | `False` (creator rewritten to operator) |
| Whole-manifest projection match | `False` — guard returns `False` |
| Outcome | **approved, `version == 2`** |
| Active pointer rebound | yes, to `manifests/capabilities/capmanifest_55f7fb3b-….json` |
| Bound `capabilities[0]` vs reviewed capability | **byte-identical** |
| Bound `capabilities[0].security_review` / `availability_state` | `passed` / `available` |
| Bound `capabilities[0].tool_binding.inventory_sha256` | the reviewed digest, unchanged |
| Reviewed source at approval time | tampered |
| `git status --porcelain` | empty |
| Publishers of rogue manifest | 1 |

**Reproduction — post-tag recovery path.** Same construction, crashing at
`after_approval_tag` on a legitimate ordinary no-manifest approval, then
recovering with `capability_manifest_id=rogue`: **approved, `version == 2`**,
pointer bound to the rogue key.

**Reproduction — real public Click CLI.** `gigai approve <proposal> --gig … \
--capability-manifest-id <rogue> --json` → **exit code 0**, `"status":
"approved"`, `"version": 2`, `"tag": "gig-v000002"`. This is the same surface
the shipped
`test_public_cli_refuses_renamed_reviewed_manifest_without_publication` asserts
refuses; it refuses the exact copy and admits the decoy variant.

**A reorder alone is sufficient.** Inserting the clone at index 0 instead of
appending (`capabilities.insert(0, decoy)`) also evades: schema valid,
**approved `version == 2`**. No field of the reviewed capability is modified in
either variant.

**Why the other defences do not catch it.** `_claims_generic_review`
(`capability_successor.py:914-919`) is the only other refusal path for an
uncommitted manifest, and it reads `created_by`, which the caller rewrites to
an operator — a rewrite the shipped
`creator-and-cosmetics-changed` parametrization already demonstrates is
survivable for the exact copy, and which here is not backstopped by the
projection. The schema-coupling that incidentally refuses several other
mutations does not apply: appending a clone with a fresh `capability_id` is
schema-valid.

**Scope of the equivalence class, stated precisely.** Within the doc's own
framing — "an equivalent fresh-ID copy returns *a successor is required*" — the
correction holds for copies that preserve the capability list exactly, and
fails for copies that preserve every reviewed *capability* while changing the
list. The bypass lives entirely inside "copied reviewed-source equivalence":
the reviewed capability, its source constraints, its declared effects, its
permissions and its exact inventory digest all survive verbatim into the
pointer.

**Suggested shape of the fix** (no new mechanism, no new authority source; the
evidence is already gathered). Compare **per capability, by containment**
rather than per manifest by equality: refuse when any capability of the selected
working manifest projects equal to any capability of any authenticated
committed reviewed manifest (with `gig_id`/`schema_version` still checked). I
confirmed this closes the case directly — for the append variant, whole-manifest
projection equality is `False` while reviewed-capability containment in the
rogue manifest is `True`. Suggested regressions: (a) decoy capability appended;
(b) reviewed capability reordered to a non-zero index; both through normal
approval, recovery and the public CLI, asserting `HEAD`, all tag refs and
pointer bytes unchanged.

---

## The disputed legacy fixture — the correction is right, and the prior review was wrong on the facts

The prior guard review's suggested fix was to refuse any working manifest
asserting `security_review.status == "passed"` for any capability, and it cited
`tests/test_scout05_tool_crud.py`'s operator-authored manifest as being
`security_review.status != "passed"`. **That premise is false.** The actual
legacy CRUD fixture `_manifest` at `tests/test_scout03_c3_tools.py:66-107`
carries:

| Field | Value |
| --- | --- |
| `created_by` | `{"kind": "gigai", "id": "scout-c3-test", "model_target": None}` |
| `capabilities[0].security_review` | `{"status": "passed", "checks": ["inventory"], "reason": None}` |
| `capabilities[0].availability_state` | `"available"` |

It has never been produced by the generic review service, and its workpad
contains no committed `manifests/capability-reviews/` artifact at all. A blanket
`passed` rejection would therefore have broken it. The correction's chosen
discriminator — committed generic-review provenance plus a stable projection
match, with `created_by.id == "capability-review"` only as a narrow
belt-and-braces clue — correctly separates the two populations. Verified:
`tests/test_scout03_c3_tools.py` → **19 passed**, and a direct guard call on a
never-reviewed manifest returns `False` without refusal.

I therefore do **not** endorse the prior review's suggested fix as written, and
F-A1 above is deliberately framed so it can be closed without touching legacy
`passed` trust.

## Properties verified to hold (probed, not merely read)

| Property | Result |
| --- | --- |
| Exact fresh-ID reviewed copy refuses in normal approval | **Holds.** Shipped suite + my probes; `HEAD`, all tag refs and pointer bytes unchanged. |
| …with creator rewritten to operator, and changed `created_at`, `manifest_version`, `compatibility`, `security_review.reason` | **Holds.** Projection excludes these; match still fires and approval refuses. |
| …with the creator field removed | **Holds** — refused as schema-invalid before publication. |
| Exact copy refuses during post-tag missing-Commit-B recovery, with tampered reviewed source | **Holds.** |
| Public Click CLI refuses the exact fresh-ID copy | **Holds**, non-zero exit, "authenticated successor". |
| Authority authenticates the journal review, not working bytes | **Holds.** A forged *working* `manifests/capability-reviews/<rogue>.json` naming the rogue manifest is refused (`capability_successor_authority_unavailable`); it never grants approval. |
| Deleting the working decision file cannot erase authority | **Holds** — committed reviewed id still returns `True`. |
| Downgrading working reviewed manifest bytes to `not_run` cannot hide provenance | **Holds** — still `True`. |
| Bound source identity is authenticated | **Holds.** `_validate_review_source_binding` (`capability_successor.py:1080-1120`) requires the decision's `source_binding` to equal the reviewed capability's `inventory_sha256`, `operations`, `effects` and `permissions`; a mismatch is a refusal, not a skip. |
| Missing / conflicted provenance fails closed | **Holds.** Every `JournalArtifactMissingError`/`JournalConflictError` inside `_committed_generic_reviewed_manifests`, and every malformed decision/manifest shape, raises `capability_successor_authority_unavailable`. Only the top-level manifest-missing branch delegates, and it now delegates to the uncommitted path rather than returning `False`. |
| Selected regular-file / path guard | **Holds.** The candidate is read only via `capability_manifest_artifact_ref` (`capabilities.py:296-319`), which rejects symlinks and non-regular files, revalidates schema, and pins path/identity/Gig agreement. Probed: deleted copied source, deleted copied manifest, copied-manifest symlink all refuse before publication. |
| Immutable source; no new authority from working claims | **Holds.** The guard is a pure read predicate — three consecutive calls leave `git status` and approval state byte-identical, and it performs no writes. A working `created_by = gigai/capability-review` claim with no matching committed review is a refusal, never a grant. |
| Identity validated before any path construction | **Holds.** `../../etc/passwd` and `capmanifest_not-a-uuid` both refuse with `capability_successor_manifest_invalid`. |
| Wrong `gig_id` against a committed reviewed manifest | **Holds** — refuses. |
| Review-decision enumeration is bounded and path-safe | **Holds.** `ls-tree -r -z --name-only HEAD -- manifests/capability-reviews/` with a three-part path shape check, a `capmanifest_<uuid4>` stem filter, and exact `manifests/capabilities/<id>.json` composition; no path following from caller data. |
| Callsites unchanged and still inside the writer lock | **Holds.** `preflight_successor` (`lifecycle.py:2049-2071`) still runs as `preflight=` inside `_writer_lock` before Commit A/tag; `recover` (`lifecycle.py:2230-2256`) still runs the guard at the top of the function body, before the `pointer_path.exists()` block and the `journal_commit == sealed_commit` early return. |
| Legacy never-reviewed fixture preserved | **Holds** — see above, 19 passed. |

## Minor, non-blocking

- **Refusal-message drift for a nonexistent manifest.** A fresh `manifest_id`
  with no file on disk previously returned `False` and failed later; it now
  refuses inside the guard with "selected capability manifest is unavailable".
  Behaviourally equivalent (approval refuses either way, state unchanged), but
  the operator-visible message for a simple typo is now an authority-shaped
  refusal rather than a manifest-not-found one. Worth a clearer message; not a
  defect.
- **Evidence-level wording.** As in the prior review, the shipped suite is
  service-level plus one real Click CLI case; the doc's "the real Click `gigai
  approve` surface" claim is accurate for the exact-copy case only. My F-A1
  CLI reproduction shows that surface is not yet a general proof.
- **Doc claim scope.** "An equivalent fresh-ID copy therefore returns the
  existing result of this predicate — *a successor is required*" is true for
  list-preserving copies only. Recommend the doc state the equivalence class as
  *whole-capability-list* equality until F-A1 is closed.

## Commands run at this root

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q tests/test_scout05_reviewed_manifest_guard.py --tb=short` | `10 passed in 27.50s` (matches the doc's claim) |
| `.venv/bin/pytest -q tests/test_scout03_c3_tools.py --tb=short` | `19 passed in 39.27s` (legacy fixture preservation) |
| `.venv/bin/python tools/verify_installed_schemas.py` | `verified 58 installed GigAI schemas` |
| `uv run ruff check src/gigai/capability_successor.py src/gigai/lifecycle.py tests/test_scout05_reviewed_manifest_guard.py` | `All checks passed!` |

The doc's other reported runs
(`tests/test_scout05_capability_prepare_cli.py::…`,
`tests/test_scout05_tool_crud.py::…`, `2 passed`) were **not** rerun here — the
dispatch reserves those to the implementer's report. They are repeated as their
claim, not verified by me.

## Probe inventory

All probes were disposable pytest files under the session scratchpad, never in
the repository, run against the working tree at `fda4857` plus the dirty lane.

| Probe | Cases | Result |
| --- | --- | --- |
| A — copies changing one excluded (`availability_state`, `security_review`, `compatibility`, `alternatives`, `options`) or one stable (`operations`, `permissions`, `name`) field | 8 | all refuse; no bypass |
| B — operator-creator copies isolating projection vs schema coupling, incl. appended capability | 5 | 4 refuse; **appended capability = bypass** |
| C — appended-decoy bypass detail: pointer, bound bytes, source tampering, publishers, tree | 1 | **bypass confirmed**, cap[0] byte-identical, 1 publisher, clean tree |
| D — same bypass through post-tag recovery and through the real Click CLI | 2 | **bypass confirmed on both**, CLI exit 0 |
| E — minimal evasion variants (decoy marked unavailable; reviewed capability reordered) | 2 | unavailable-decoy schema-invalid → refuses; **reorder = bypass** |
| F — authority matrix (committed id, forged working decision, deleted working decision, downgraded working bytes, bad ids, ghost id, wrong gig) | 8 | all as documented |
| G — guard purity across repeated calls; nonexistent-manifest behaviour | 2 | pure; refuses with changed message |
| H — projection granularity boundary (whole-list equality vs per-capability containment) | 1 | list-equality `False`, containment `True` — root cause isolated |

## Acceptance limits

This review covers only the F-A correction listed in the dispatch:
`reviewed_manifest_requires_successor`, `_uncommitted_manifest_requires_successor`,
`_stable_reviewed_manifest_identity`, `_committed_generic_reviewed_manifests`,
`_claims_generic_review`, `_validate_review_source_binding`, the two
`lifecycle.py` callsites, and
`tests/test_scout05_reviewed_manifest_guard.py`. Accepted successor
preparation, the `prepare-successor` adapter, review CLI, schema, tool
execution and default-init scope were not reopened. The unrelated dirty lane
work elsewhere in the tree was not inspected.

Not assessed and not implied: full-suite status, build/wheel/release readiness,
package or release validation, schema registration beyond the 58-resource
verifier run, default capability eligibility, effect-consent presentation, the
capability-review UI, provider execution, OS-account sandboxing, real private
user state, installed-workflow dogfood, and overall SCOUT-05 completion. No
approval, activation, installation or execution was performed against any real
user state, and no release-readiness or broad safety claim is made here.
