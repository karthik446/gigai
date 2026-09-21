# SCOUT-05 reviewed-manifest containment: final bounded F-A1 acceptance review

Date: 2026-09-10. Bounded read-only review of the F-A1 correction described in
`SCOUT-05-reviewed-manifest-containment-implementation.md`, requested by
`SCOUT-05-reviewed-manifest-guard-corrections-review.md`. Scope is the changed
matching helpers in `capability_successor.py`, the new containment guard tests,
and the two existing normal/recovery guard callsites.

Per dispatch: no pytest run, no schema/lint/build rerun (the focused containment
regression rerun is the root's, and its result was received in-inbox during this
review). Verification here is source and test inspection plus two short
disposable in-memory probes of the projection/containment algebra, run under the
session scratchpad against literal dicts — no repository, workpad, journal,
provider, network or user `.gigai` state was touched. No source, test, schema or
evidence file outside this report was created or edited; no commits.

## Verdict

**Accept.**

F-A1 is closed. The whole-manifest list-equality comparison that the prior
review broke is gone, replaced by genuine per-capability containment, and every
evasion the prior review demonstrated — append a decoy, prepend a decoy (moving
the reviewed capability to a non-zero index), reorder the list — now refuses.
The correction adds no new authority source, no new creator allowlist and no
blanket `passed` heuristic, and it leaves the frozen legacy compatibility intact
for the documented reason rather than by accident.

---

## The correction, as landed

`_stable_reviewed_manifest_identity` (whole-list projection) and
`_committed_generic_reviewed_manifests` are replaced by
`_stable_reviewed_capability_identity` / `_stable_reviewed_capability_identities`
(`capability_successor.py:926-952`) and
`_committed_generic_reviewed_capabilities` (`capability_successor.py:954-1085`).
The comparison at `capability_successor.py:895-905` is now:

```python
candidate_schema = candidate.get("schema_version")
candidate_capabilities = _stable_reviewed_capability_identities(candidate)
for reviewed_schema, reviewed_capability in _committed_generic_reviewed_capabilities(...):
    if candidate_schema == reviewed_schema and reviewed_capability in candidate_capabilities:
        return True
```

This is set containment of one projected capability in the candidate's projected
capabilities, not equality of an ordered projection of the container. That is
exactly the shape the prior review recommended, and it is the minimal change
that closes the finding.

## Dispatch checks

### Any unchanged generic-reviewed capability triggers refusal regardless of append / prepend / order — holds

Containment is over an unordered membership test on a tuple of independent
per-capability projections, so list position is structurally irrelevant. Probed
in memory against the projection helpers directly:

| Candidate capability list | Reviewed capability contained? |
| --- | --- |
| reviewed capability only | `True` |
| reviewed + appended fresh-id clone | `True` |
| prepended fresh-id clone + reviewed (reviewed at index 1) | `True` |
| two unrelated capabilities, reviewed last (reorder) | `True` |
| five unrelated capabilities, reviewed at index 5 | `True` |
| reviewed with all five excluded presentation fields mutated | `True` |
| no reviewed capability present | `False` |
| reviewed with one stable field (`permissions.network`) changed | `False` |
| reviewed with `tool_binding.inventory_sha256` tampered | `False` |

The last three are the correct negatives: containment fires on the reviewed
authority projection and nothing wider. Mutating a projected field is a
genuinely different binding, not an evasion of the same one.

The shipped regressions cover the prior review's exact reproduction on all three
surfaces it broke, parametrized over both decoy positions:

| Test | Surfaces |
| --- | --- |
| `test_renamed_reviewed_capability_containment_refuses_normal_approval[append\|prepend]` | normal approval, pre-Commit-A/tag |
| `test_public_cli_refuses_contained_reviewed_capability[append\|prepend]` | real public Click `gigai approve`, asserts non-zero exit |
| `test_contained_reviewed_capability_is_refused_during_missing_commit_b_recovery[append\|prepend]` | post-tag missing-Commit-B recovery |

The fixture `_renamed_reviewed_manifest` (`tests/test_scout05_reviewed_manifest_guard.py:94-146`)
reproduces the prior review's construction faithfully: a re-serialized clone of
`capabilities[0]` with a fresh `capability_id`, inserted by `append` or
`insert(0, …)`, with `changed_creator=True` rewriting `created_by` to an
operator and mutating `created_at`, `manifest_version`, `compatibility` and
`security_review.reason`. That creator rewrite matters — it defeats
`_claims_generic_review`, so these cases prove containment carries the refusal on
its own rather than being backstopped by the creator clue. The normal-path case
additionally tampers reviewed source bytes before approval, proving the approval
gate refuses ahead of any runtime source check.

Every one of the six asserts `_assert_approval_state`, which compares `HEAD`,
all tag refs (`show-ref --tags`) and active-pointer bytes — so the refusals are
verified to be state-preserving, not merely non-zero-exit.

### Only the exact capability named in the authenticated passed review contributes matching authority — holds

The committed set is built strictly, one entry per authenticated decision. AST
inspection of `_committed_generic_reviewed_capabilities` confirms a single
`reviewed.append(...)` (`capability_successor.py:1081`), appending exactly

```python
(manifest.get("schema_version"), _stable_reviewed_capability_identity(reviewed_capability))
```

where `reviewed_capability` is the return of `_validate_review_source_binding`
(`capability_successor.py:1090-1125`). That helper takes `capability_id` from the
schema-validated decision, requires **exactly one** matching capability in the
reviewed manifest (`len(matches) != 1` is a refusal, so an ambiguous or absent id
cannot silently select one), and then requires the decision's `source_binding` to
equal the capability's `inventory_sha256`, `operations`, `effects` and
`permissions`. A mismatch raises rather than skips.

So sibling capabilities that merely rode along in a reviewed manifest contribute
nothing. This is the property that keeps containment from over-matching, and it
is enforced before projection rather than after.

Fail-closed posture is preserved: the enumeration has nine `raise` branches and
three `continue`s, and I checked each `continue` only ever *withholds* authority
— empty `ls-tree` path entry (`:977`), a stem that is not a `capmanifest_<uuid4>`
(`:997`), and a decision whose `project_id`/`gig_id` mismatch or whose
`reviewer_outcome != "passed"` (`:1021-1027`). None can add an entry. A
`rejected` review therefore grants no matching authority, which is correct.

### Schema / Gig context pinned — holds

The match requires `candidate_schema == reviewed_schema`, where the candidate's
version comes from the candidate manifest and the reviewed version from the
reviewed manifest's own committed bytes — not from the decision or any caller
input. Gig context is pinned three times over: `_committed_generic_reviewed_capabilities`
reads only via `read_committed_artifact(project_id=…, gig_id=…)`, filters
decisions on `project_id`/`gig_id`, and re-checks
`manifest.get("gig_id") == gig_id` on the reviewed manifest
(`capability_successor.py:1073-1078`); the candidate is independently pinned at
`capability_successor.py:889-894`. A cross-Gig reviewed capability cannot supply
a match.

### Working claims cannot grant authority — holds

The candidate is read only through `capability_manifest_artifact_ref`
(symlink/non-regular-file rejecting, schema-revalidating, identity/Gig-pinning),
then re-checked for digest and size drift between ref and bytes
(`capability_successor.py:874-882`) — a read-time TOCTOU check the prior review's
matrix did not have to cover, and a good one to keep. Structurally, working bytes
appear on only one side of the comparison: `candidate_capabilities` is the
*haystack*. Authority is always the needle, and the needle comes exclusively from
committed, journal-authenticated, schema-validated decisions plus their pinned
reviewed manifests. There is no code path where a working value is added to the
committed set, so a forged working review artifact remains a refusal rather than
a grant, and deleting or downgrading working bytes cannot erase committed
provenance. The guard is still a pure read predicate that performs no writes.

`_stable_reviewed_capability_identities` fails closed on a malformed
`capabilities` value — probed: a non-list and a list with a non-mapping member
both raise `capability_successor_authority_unavailable` — so a shape-mangled
candidate cannot produce an empty haystack and slip past containment. This is a
real hardening detail, because the projection now runs over caller-controlled
structure rather than a single blob.

### No broadened legacy trust — holds

The projected field set is unchanged from the accepted correction: the same
eleven `_STABLE_REVIEWED_CAPABILITY_FIELDS` (`capability_successor.py:69-81`).
Checked against the manifest schema, the capability object has sixteen
properties; the five not projected are exactly `availability_state`,
`security_review`, `compatibility`, `alternatives` and `options` — the
presentation/review prose the doc says it excludes. No security-relevant field
was dropped to make containment fire, and none was added to widen it.

`_claims_generic_review` (`capability_successor.py:915-923`) is unchanged and
still narrow: `created_by.kind == "gigai"` and `created_by.id ==
"capability-review"` only. No new creator allowlist, and critically no
`security_review.status == "passed"` heuristic — the fix the prior review
proposed and then itself retracted on the facts.

Legacy compatibility is intentionally supported and I did not re-litigate it. I
confirmed only that the reason still holds under the new matching: the
`scout-c3-test` fixture (`tests/test_scout03_c3_tools.py:66-107`) carries
`created_by.id = "scout-c3-test"`, `security_review.status = "passed"` and
`availability_state = "available"`, and `grep` finds no
`manifests/capability-reviews/` artifact in either legacy fixture file. With no
committed generic-review decision, the authenticated set contributed by those
Gigs is empty, so containment cannot fire and the ordinary legacy route is
reached exactly as before. Preservation is structural, not incidental.

## Public / source identity changes are not automatically metadata

The dispatch asked me to keep this boundary honest, and it is the one place the
projection's shape deserves an explicit statement rather than a checkbox.

`capability_id` and `name` are inside the projection, so a copy that renames
either does **not** match the reviewed projection. Probed: changing
`capability_id` or `name` on an otherwise byte-identical capability yields
containment `False`. That is not a residual F-A1-style hole, and it should not be
described as one, because such a copy is not the reviewed capability wearing a
different label — it is a different capability that no longer resolves to the
reviewed source. Source materialization keys the on-disk root off the id:
`_capability_root` is `tools/{capability_id}` (`capabilities.py:89-93`) and
`_source_path` is `tools/.sources/{capability_id}.artifact`
(`capabilities.py:322-326`). A renamed copy therefore points at a different
source root, and `_validate_tool_binding` (`capabilities.py:165-215`) still
independently binds `tool_binding.inventory_sha256` to the canonical inventory
digest and the entry digest/basename to
`source_constraints.required_digest`/`required_identity`. It cannot inherit the
reviewed source by renaming.

The correct framing, which the implementation doc gets right: the equivalence
class is *scoped copied-capability equivalence* — a capability whose full
authority projection (identity, goals, source constraints, declared effects,
permissions, credential/network constraints, complete tool binding) is
unchanged, wherever it sits in whatever manifest. It is deliberately not
"any arbitrary different legacy manifest," and the guard makes no claim that
legacy history is generic-review proof. Both the doc's "Limits" section and its
projection description state this accurately.

## Callsites — unchanged, and still correctly placed

Both existing callsites are untouched by this correction and remain correct:

- `preflight_successor` (`lifecycle.py:2049-2071`) still runs as the `preflight=`
  hook inside `_writer_lock`, before Commit A and the tag.
- `recover` (`lifecycle.py:2230-2256`) still runs the guard at the top of the
  function body under the same writer lock, before the `pointer_path.exists()`
  block and before any early return.

Both guard on `successor_approval is None and capability_manifest_id is not None`
and raise the same `LifecycleError("reviewed capability manifest requires its
authenticated successor")`, wrapping `CapabilitySuccessorError`. No public
signature, lifecycle sequencing, schema, provider, activation or consent
mechanism changed.

## Minor, non-blocking

- **Refusal-message drift persists.** A `capability_manifest_id` typo with no
  file on disk still surfaces as "selected capability manifest is unavailable"
  with an authority-shaped code rather than a manifest-not-found message. Carried
  forward unchanged from the prior review; behaviourally equivalent (approval
  refuses, state unchanged), still worth a clearer operator message eventually.
- **Doc wording is now accurate.** The prior review asked that the equivalence
  class be stated as whole-capability-list equality until F-A1 closed. F-A1 is
  closed, and the implementation doc correctly describes per-capability
  containment and names append, prepend and reorder explicitly. No correction
  needed.
- **Line-length style.** A few new lines in the guard suite and the
  `_stable_reviewed_capability_identities` signature run long relative to the
  surrounding file. The doc reports `ruff` clean on both files, so this is
  cosmetic observation only — I did not rerun lint.

## Verification performed here

| Activity | Detail |
| --- | --- |
| Source inspection | `capability_successor.py:69-81, 739-1125`; `lifecycle.py:2040-2080, 2230-2265`; `capabilities.py:85-95, 160-220, 296-326`; `capability_review.py:223-235` |
| Test inspection | `tests/test_scout05_reviewed_manifest_guard.py` in full; legacy fixture fields in `tests/test_scout03_c3_tools.py:66-107` |
| Schema inspection | `capability-manifest.schema.json` capability properties; `capability-review-decision.schema.json` required fields |
| Probe 1 (disposable, in-memory) | Nine candidate-list shapes against `_stable_reviewed_capability_identity`/`_identities`, plus two malformed-shape fail-closed cases. Literal dicts only; no workpad. |
| Probe 2 (disposable, in-memory) | `capability_id`- and `name`-renamed copies vs the reviewed projection. Literal dicts only. |
| AST check | Single `append` site, its appended tuple, and every `continue`/`raise` branch in `_committed_generic_reviewed_capabilities` |

Not run here, by dispatch: pytest (including the containment regressions), ruff,
`py_compile`, the schema verifier, and any build. The implementation doc's
reported focused runs are repeated as its claim, not verified by me; the root
owns the focused containment regression rerun, and its "six containment cases
independently passed" status was received in-inbox and is consistent with the
six parametrized cases I inspected.

## Acceptance limits

This review covers only the F-A1 containment correction: the projection and
matching helpers, `_uncommitted_manifest_requires_successor`'s comparison, the
committed-capability enumeration and its source-binding validation, the two
`lifecycle.py` callsites, and the new guard regressions. Accepted successor
preparation, the `prepare-successor` adapter, review CLI, schema, tool execution
and default-init scope were not reopened. The frozen legacy `passed`/`available`
compatibility was checked only for continued preservation, not re-litigated. The
unrelated dirty lane work elsewhere in the tree was not inspected.

Not assessed and not implied: full-suite status, build/wheel/release readiness,
package or release validation, schema registration, default capability
eligibility, effect-consent presentation, the capability-review UI, provider
execution, OS-account sandboxing, real private user state, installed-workflow
dogfood, and overall SCOUT-05 completion. **No release-readiness claim is made
here.** No approval, activation, installation or execution was performed against
any real user state.
