# SCOUT-05 successor corrections and public prepare adapter: independent acceptance review

Date: 2026-09-10. Independent read-only acceptance review of the settled
reviewed-capability successor corrections (F1/F2) and the public
`gigai capability prepare-successor` adapter. No source, test, schema, or
evidence file outside this report was created or edited. No pytest suite was
rerun, no wheel or full-suite verification was performed; the coordinator owns
the combined run (reported as 47 tests / 189 subtests in 131.07s, scoped Ruff
and the 58-resource verifier passing). Research candidate files and the
unrelated dirty lane work in `lifecycle.py` / `journal.py` were not inspected.

All probes below were bounded and disposable: pytest `tmp_path` homes, targets
and workpads, the real shipped `gigai.data` Scout source, the real review
service and the real public CLI. No live user `.gigai` state, no providers, no
network, no repository commits, and no writes outside pytest temp directories.

## Verdict

**Accept F1 and F2 as corrected. Accept the public prepare adapter.** Both
blockers from `SCOUT-05-capability-successor-review.md` are resolved on both
the ordinary approval and post-tag recovery paths, and every property that
previously held still holds under repeated adversarial probing. The adapter is
a genuinely read-only, effect-free preparation surface with correct identity
validation, exact committed reference reads, faithful separate-approval argv,
and exact replay.

One item is carried forward, and it is **not** a regression of this lane and
**not** a new finding: the legacy `approve --capability-manifest-id` branch can
still bind a *reviewed* manifest into the active pointer through an ordinary
proposal that has no successor sidecar, with no successor authentication and no
approval-time source revalidation. This is exactly the pre-existing gap already
recorded as **F2 (Blocking safety) in `SCOUT-05-capability-consent-caller-audit.md`**.
The corrections closed the *forgeable-authority* half of the original F2 (a
mutable `created_by` string deciding whether successor authority applies); the
*unguarded legacy selection* half was never claimed by this lane and remains
open where the audit already places it. Details, the exact public reproduction,
and the precise blast radius are in "Carried-forward residual" below.

F3 is confirmed unchanged and remains a nonblocking diagnostic-precision item
with no contract breach.

---

## F1 — absent first-version pointer recovery: **RESOLVED**

`src/gigai/lifecycle.py:2229-2277`. `payload` is now initialized to `None`
before the `if pointer_path.exists():` block, the malformed/schema checks apply
only to a pointer that actually exists, and an absent pointer is accepted as the
expected post-Commit-A/tag state **only** for version one
(`if payload is None: if version != 1: raise ... "existing active-version
pointer is unavailable"`). `allow_artifact_replacement` is correspondingly
gated on `pointer_path.exists()`, so the first publication does not request
replacement it does not need.

Independent probe (`tmp_path`, no successor involved, both proposal families),
crashing at `after_approval_tag`, then recovering and repeating three more
times:

| Family | Pointer after crash | Tag | Recovered | Repeats | `gig accepted` commits | Tree |
| --- | --- | --- | --- | --- | --- | --- |
| legacy (`create_offline`) | absent | `gig-v000001` | version 1, sealed commit unchanged | 3 × identical `(version, sealed_commit, publication_commit)` | **1** | clean |
| v2 (`_candidate`) | absent | `gig-v000001` | version 1, sealed commit unchanged | 3 × identical | **1** | clean |

No `UnboundLocalError`, exactly one Commit B, HEAD stable across all repeats,
and the original sealed tag retained. The shipped parameterized regression
`test_first_approval_crash_after_tag_recovers_once_without_prior_pointer`
covers precisely this fixture shape (no prior active pointer) for both families
plus one replay, which is the regression the original review asked for.

Legitimate legacy approvals are preserved: the legacy family above is an
ordinary v1 approval with no capability involvement, and it completes normally.

## F2 — successor authority keyed on committed evidence: **RESOLVED**

`src/gigai/capability_successor.py:695-725` adds `_committed_binding_exists`,
which asks `journal.read_committed_artifact` whether
`manifests/capability-successors/<proposal_id>.json` was immutably published
for this project and Gig. `successor_approval_context` calls it first and
returns `None` only when there is genuinely no committed publication; a proven
publication then proceeds through `_committed_file` (working bytes must equal
HEAD), the pinned committed snapshot, full decision/manifest/parent/source
authentication, the pending-copy comparison, and under-lock source
revalidation. The mutable `created_by` field is gone from the decision entirely
(`grep` finds no `created_by` read in the approval or recovery guards;
`lifecycle.py:2046-2059` and `2216-2230` now call the context unconditionally).
`read_committed_artifact` itself enforces single-publisher, handoff
completeness, and a digest match against the pinned HEAD blob, so it is a sound
authority source for this decision.

Independent probes, each on a fresh disposable fixture, all refusing **before**
any publication with HEAD, tags and pointer unchanged:

| Attack | Approval | Recovery (post-tag) |
| --- | --- | --- |
| Original F2 pair: `created_by` → operator **and** working sidecar deleted **and** source tampered, explicit `capability_manifest_id` | refused `successor binding is unavailable` | refused `successor binding is unavailable` |
| Same, `capability_manifest_id` omitted | refused `successor binding is unavailable` | refused `successor binding is unavailable` |
| Working sidecar deleted only (`created_by` untouched) | refused `successor binding is unavailable` | — |
| Working sidecar content swapped (`source_binding.operations` narrowed) | refused `successor binding is unavailable` | — |
| Source tampered only | refused `reviewed capability source is unavailable` (under the writer lock, via `_inventory`) | — |

Restoring bytes alone changed no journal state; a later full restoration then
approves legitimately to version 2 on both paths. Source revalidation under the
writer lock is confirmed by the source-tamper-only row and by the shipped
`test_approval_revalidates_source_before_publication`.

**One correction to the original review's blast-radius note.** The original F2
write-up said `git checkout -- .` restores the reviewed source and yields a
clean tree. It does not: `tools/` is git-ignored in the workpad
(`.gitignore:12:/tools/`), `git ls-files tools/` is empty, and a tampered tool
file therefore survives `git checkout -- .` untouched while `git status` reports
clean. The runtime source gate (`scout_tools._inventory` →
`tool_inventory_changed`) is what refuses tampered bytes, not tree cleanliness.
This makes the runtime control stronger than described, and it is why my first
probe run showed a post-restore failure that was a probe artifact, not a defect.

The shipped parameterized regression
`test_committed_successor_binding_refuses_mutable_bypass_before_publication`
(`recovery` ∈ {False, True}) matches the two regressions the original review
requested, and additionally asserts that byte restoration alone publishes
nothing and that the legitimate retry after full restoration succeeds.

## F3 — stale-base diagnostic: **unchanged, nonblocking, no contract breach**

Reprobed on a live fixture:

| Input | Code |
| --- | --- |
| `base_version=2` while pointer is at v1 | `capability_successor_review_invalid` |
| wrong `base_proposal_id` | `capability_successor_review_invalid` |
| foreign `gig_id` | `capability_successor_authority_unavailable` |
| `base_version` 0 / -1 | `capability_successor_base_invalid` |

Identical to the original observation. Every case refuses and publishes
nothing; only the diagnostic is imprecise for a caller trying to distinguish
"your base moved" from "this decision is wrong". The corrections doc states
this was intentionally deferred while the CLI adapter lane was separately
owned, which I accept: no public diagnostic contract is broken, and the public
adapter surfaces `capability_successor_review_invalid` for the stale case with
a message that does not misrepresent the outcome. **Acknowledged as
nonblocking.**

---

## Properties reverified after the corrections

Each was probed adversarially on disposable fixtures, not merely read.

| Property | Result |
| --- | --- |
| Immutable sidecar and pending copy each have exactly **1** publisher, before and after approval | **Holds** (`git log -- <path>` = 1 for sidecar, pending copy, and reviewed manifest) |
| Reviewed manifest is never republished by successor approval | **Holds.** 1 publisher post-approval; `read_committed_artifact` returns cleanly (no multiple-publisher conflict) |
| Repeated recovery after a successful successor publication | **Holds.** 4 consecutive calls return identical `(version, sealed_commit, publication_commit)`; HEAD unchanged; publishers still 1; exactly 2 `gig accepted` commits total |
| Prior v1 history preserved | **Holds.** The pointer at the sealed commit still reads `active_version == 1` while v2 is live |
| Second pending successor refused | **Holds** (`capability_successor_pending_conflict`) |
| Re-prepare against a now-stale base after approval | **Holds** (`capability_successor_current_conflict`, no publication) |
| Foreign-Gig sidecar refused | **Holds** (`successor binding is unavailable`; shipped regression) |
| Swapped `reviewed_manifest_ref` in the working sidecar during recovery | **Holds** (shipped regression; my content-swap probe agrees) |
| Explicit-vs-omitted manifest selection agree; mismatched explicit ID refused | **Holds** (shipped regressions; my probes agree) |
| Newly reviewed manifest bound through a *successor* proposal requires full authority | **Holds** |
| Newly reviewed manifest selected through an *unrelated proposal with no sidecar* | **Carried forward — see below** |

## Public prepare adapter (`capability_cli.prepare_successor_command`)

| Requirement | Result |
| --- | --- |
| Identity validation precedes any workpad/authority path construction | **Holds.** `_validate_identity` runs on `--gig`, `--base-proposal-id`, `--capability-id`, `--reviewed-manifest-id` before `resolve_workpad`. Probe: an invalid `--gig` with a nonexistent `--home`/`--target` returns `capability_successor_identity_invalid`, i.e. the identity refusal wins over any resolution error |
| Path traversal through the manifest identity | **Holds.** `--reviewed-manifest-id ../../etc/passwd` → `capability_successor_identity_invalid`; the manifest/decision paths are then built only from a regex-validated `capmanifest_<uuid4>` stem |
| Exact committed refs only | **Holds.** `_read_review_authority` reads `manifests/capabilities/<id>.json` and `manifests/capability-reviews/<id>.json` via `read_committed_artifact` (manifest-keyed, not caller-supplied paths), revalidates the manifest schema, and requires `manifest_id`, `gig_id`, `project_id`, `base_version`, `base_proposal_id`, `capability_id`, `reviewer_outcome == "passed"` and `decision.reviewed_manifest_ref == reviewed_ref` to agree before calling the service |
| Typed, redacted errors | **Holds.** `{"ok":false,"error":{"code","message"}}` exactly, no input echo. Probed codes: `capability_successor_identity_invalid`, `capability_successor_operation_invalid`, `capability_successor_review_unavailable` (foreign manifest key), `capability_successor_review_invalid` (stale base), `capability_successor_authority_unavailable` (missing or working-changed manifest), `capability_successor_pending_conflict` (second key). Non-JSON mode raises the equivalent `ClickException` |
| No setup / selection / approval effects at prepare | **Holds.** Pointer bytes unchanged and still `active_version == 1`; `load_project_binding(target).active_gig_id` stays `None`; `resolve_workpad` is called with a required explicit `--gig`, so the selection-mutating branches in `resolve_workpad` (target-derived binding, `select_active_workpad`) are not reachable, and `_resolve_registered` is read-only |
| Separate approval argv, including home and target | **Holds.** `["gigai","approve",<pending proposal_id>,"--gig",<resolved gig>,"--capability-manifest-id",<reviewed manifest id>,"--home",<home>,"--target",<target>,"--json"]`. Invoking `argv[1:]` through the real CLI approves to version 2. `--home` falls back to `default_home_root()` and `--target` to `resolved.target_root`, so the emitted argv is runnable rather than echoing an omitted flag. With `--target` omitted and the CWD not a bound project, the command refuses (`workpad_conflict`) rather than guessing |
| Replay, no publication | **Holds.** 3 consecutive same-key invocations return `replayed: true` with byte-identical `proposal`, `binding`, `binding_ref`, `decision_ref`, `reviewed_manifest_ref` and `approval_argv`; HEAD and the commit count are unchanged |
| Pending-only diagnostics | **Holds.** JSON carries `status: "successor_pending"` plus a `next_action` stating preparation does not approve or activate; normal output prints the pending proposal and the approval command via `shlex.join` |
| Public fixture provenance | **Confirmed.** `_reviewable_candidate` → `_candidate` → `initialize_defaults(inventory=scout_candidate_inventory())` reads the real shipped `gigai.data/scout` source; the review step is the real public `gigai capability review` command, and the successor is prepared through the real public `prepare-successor` command. Nothing is hand-marked passed, and the fresh-process wrapper smoke test in the shipped test runs the real `gig.py` in a subprocess |

## Carried-forward residual (pre-existing, already recorded — not a lane regression)

**What.** `lifecycle.approve_offline` / `_recover_approved_publication` still
take the legacy branch when `successor_approval is None`:
`capability_manifest_artifact_ref(workpad, capability_manifest_id, ...)` reads
working-tree bytes and checks only schema validity, path/identity agreement and
Gig ownership. It does **not** require a passed review decision,
`availability_state == available`, operator effect consent, or approval-time
source revalidation. A *reviewed* manifest therefore becomes active-version
authority through any ordinary proposal that has no committed successor
sidecar.

**Minimal public reproduction** (disposable `tmp_path`, real public CLI, no
`prepare-successor` invoked at all):

```
gigai capability review --gig <gig> --base-version 1 --base-proposal-id <p1> \
    --manifest-id <pending> --capability-id cap_...071 --operation-key k \
    --input review.json --confirm --home <h> --target <t> --json
# -> reviewed manifest capmanifest_<X>, availability=available, review=passed

# hand-author an ordinary amend proposal in the mutable current-proposal slot
#   proposal_id=<rogue>, status=proposed, kind=amend, parent=<p1>
gigai approve <rogue> --gig <gig> --capability-manifest-id capmanifest_<X> \
    --home <h> --target <t> --json
```

Observed: exit 0, `active_version` 2, pointer
`capability_manifest.path = manifests/capabilities/capmanifest_<X>.json`, tree
clean. The same holds through the post-tag recovery path (crash at
`after_approval_tag`, then recover) — symmetric, version 2, 1 publisher.

**Precise blast radius, and what still protects the system.**

- Tampered source does **not** execute. With the reviewed source modified before
  this approval, the approval still succeeds (version 2, clean tree) but the
  runtime refuses: `{"ok": false, "error": {"code": "tool_inventory_changed",
  "message": "approved tool source changed"}}` with
  `next_action.kind = "proposal_required"`. `scout_tools._inventory` is the
  surviving control and it holds.
- Single-publisher is **not** broken. The reviewed manifest still has exactly 1
  publisher after this approval and `read_committed_artifact` returns cleanly —
  the legacy branch's identical-artifact replacement adds no second publisher.
- What is lost is the **approval gate**: the active pointer can represent a
  capability binding that was never authenticated as a successor, and no
  approval-time source revalidation occurred at the moment of publication.

**Why this is not a blocker for this dispatch.** It is the pre-existing
`SCOUT-05-capability-consent-caller-audit.md` finding **F2 (Blocking safety)**,
recorded verbatim there: "`approve_command` exposes `--capability-manifest-id`;
`approve_offline` checks the manifest reference/schema but not passed security
review, available state, or an approved effect option before adding it to the
active pointer… This path needs a guard or a review service before being used
for Scout." The corrections lane explicitly scoped itself to the successor
authority and the shared recovery hook, and it delivered exactly that: the
forgeable `created_by` authority decision is gone and successor authority is
now journal-proven. Closing the legacy selection path is a distinct guard on
`approve_offline`'s legacy branch (and, per audit F7, the misleading `revise
--capability-manifest-id` option), owned where the audit already places it —
not a defect in what was handed to me for review.

**Recommendation for the owning lane** (evidence is already available, no new
mechanism needed): in the legacy branch, when the selected manifest carries
`security_review.status == "passed"`, require a committed review decision at
`manifests/capability-reviews/<manifest_id>.json` proving the same Gig and a
`passed` outcome, plus the same under-lock source revalidation the successor
path performs; or refuse a reviewed manifest outright outside the successor
path. Suggested regressions: (a) reviewed manifest + ordinary no-sidecar
proposal must refuse with the pointer unchanged; (b) the same through post-tag
recovery; (c) an ordinary operator-authored (never-reviewed) manifest still
approves, preserving the existing legacy behavior exercised by
`tests/test_scout05_tool_crud.py`.

---

## Probe inventory

All probes were disposable pytest files under the session scratchpad, never in
the repository, run against the settled working tree at `fda4857`.

| Probe | Cases | Result |
| --- | --- | --- |
| F1 absent-pointer first recovery, legacy + v2, 3 repeats each | 2 | both resolved, 1 publication each |
| F2 mutable-bypass matrix (approval and recovery, explicit and omitted manifest, sidecar delete, sidecar swap, source tamper, restore-then-legitimate) | 5 | all refuse pre-publication; legitimate retry works |
| Reviewed manifest through a no-sidecar proposal (service-level, public CLI, recovery path, tampered source, publisher count) | 5 | residual confirmed; runtime gate and single-publisher hold |
| Public adapter: argv fidelity, no side effects, separate approval to v2, omitted `--target`, identity/path/operation-key validation (6 cases), refusal shape | 9 | all hold |
| Public adapter replay ×3 + second-key conflict | 1 | identical payloads, no new commits |
| Immutability, single publisher, 4× post-publication recovery, v1 history, re-prepare refusal | 2 | all hold |
| F3 diagnostic codes | 1 | unchanged as documented |

## Acceptance limits

This review covers only: `src/gigai/capability_successor.py`, the successor
approval/recovery portions of `src/gigai/lifecycle.py`,
`src/gigai/capability_cli.py`'s prepare adapter, and
`tests/test_scout05_capability_successor.py` /
`tests/test_scout05_capability_prepare_cli.py`. The unrelated dirty lane work
in `lifecycle.py` and `journal.py` (layout migration, graph-set proposal,
external recording), the research candidate directory, and other dirty files
were not inspected.

Not assessed and not implied: full-suite status (coordinator-owned, reported
passing), build/wheel/release readiness, schema registration changes beyond
those already accepted, default capability eligibility, provider execution,
OS-account sandboxing, real private user state, installed-workflow dogfood, the
capability-review UI/effect-consent presentation, and overall SCOUT-05
completion. No approval, activation, installation or execution was performed
against any real user state.
