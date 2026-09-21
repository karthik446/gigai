# Scout workspace amendment — review response

**Date:** 2026-09-08.  
**Input:** Operator-supplied review: changes requested; four blocking findings
and one medium finding. The reviewer reported no file changes or runtime tests.  
**Response state:** Accepted for SCOUT-00 workspace-amendment contract scope
in the operator-supplied independent re-review on 2026-09-08. All five findings
are resolved at contract level; no runtime implementation or test acceptance
follows from this verdict.

## Independent re-review supplied by the operator — 2026-09-08

The supplied verdict accepts this contract scope with no remaining
implementation-blocking issue. It confirms resolution of journal/SQLite
ownership, the v2 storage map, validated tool mutation boundaries, general
username-aware all-default initialization, and UI source/output separation.
The reviewer also reports that the primary documents agree, remaining old
initialization references are historical/superseded, local links resolve, and
`git diff --check` passes.

The reviewer changed no files and ran no runtime tests. Unrelated G43.1/runtime
changes are outside the verdict. Strict schemas, caller changes, migrations
and regression cases are outstanding implementation evidence obligations,
not unresolved design choices. This entry records the supplied independent
verdict; it does not claim that the coordinator performed a new independent
review or executed those runtime checks.

## Decisions and disposition

| Finding | Revised decision | Contract location |
|---|---|---|
| 1 — Blocking: SQLite authority and duplicate DB | Journaled revisions/events remain authoritative. Reuse existing `state.sqlite`; Scout-managed tables are wholly rebuildable. CRUD uses validated journal publication; delete is archive/tombstone. Preserve existing G22 trace, do not blindly discard the shared file. | [Data ownership](SCOUT-00-user-owned-gig-amendment.md#journal-authority-and-a-single-rebuildable-scout-view-finding-1) |
| 2 — Blocking: storage mapping | Added canonical table for every proposed path: authority, working copies, private Git/ignore behavior, export/private transfer and updates. Versioned v2 layout/ignore marker; v1 unchanged absent explicit migration. | [Storage map](SCOUT-00-user-owned-gig-amendment.md#canonical-ownership-git-behavior-and-transfer-map-finding-2) |
| 3 — Blocking: editable tools bypass authority | `gig.py` calls the validated in-process operation service. Gig-owned domain logic stays local; only the service publishes SQL projections and authoritative records. Bind version/inventory/tool digests and effects. Executable edits need a new approved Gig version; broadened effects also need separate capability authorization. No same-account Python sandbox claim. | [Tool boundary](SCOUT-00-user-owned-gig-amendment.md#gig-owned-domain-code-shared-validated-persistence-finding-3) |
| 4 — Blocking: initialization/clone ambiguity | Operator superseded the suggested `init scout`: general `gigai init` collects/reuses explicit username and creates every bundled default once. Frozen owner metadata, inventory batch and retry behavior; no clone command, suffix feature or old command aliases. | [Initialization](SCOUT-00-user-owned-gig-amendment.md#general-initialization-not-one-clone-command-per-gig-finding-4) |
| 5 — Medium: generated/custom UI overlap | Editable `ui/` source is separate from generated `reports/scout/` bundles. Stage complete bundle, atomically publish current selector, preserve custom files. Parse/allowlist link schemes and validate local paths including symlinks; escape markup. | [UI publication](SCOUT-00-user-owned-gig-amendment.md#sourceoutput-separation-and-safe-publication-finding-5) |

## Important source detail beyond the original review

[index.py](../../../../../src/gigai/index.py) describes a disposable journal
projection, but `_read_interview_events` and `_write_projection` explicitly
preserve a recognized `interview_events` table during file replacement.
[proposal_interview.py](../../../../../src/gigai/proposal_interview.py) writes
that trace. Therefore "rebuildable Scout data" is not a claim that all existing
contents of `state.sqlite` may be discarded. The revised contract requires
coordinated index/trace writes, preservation/recovery evidence, and refusal
instead of silent trace loss. No second database is proposed.

[workpad.py](../../../../../src/gigai/workpad.py) also checks exact ignore
bytes and a closed top-level allowlist. The new layout cannot ship by merely
creating extra folders or weakening all workpad validation. Its versioned
marker, ignore policy and private transfer rules need explicit implementation
and compatibility tests.

## Operator decision on initialization

The reviewer proposed one default Scout instance via `init scout`. The operator
confirmed that users should instead provide a username during `gigai init`
and automatically receive private instances of all bundled defaults. This
response adopts that explicit product decision rather than claiming to have
accepted the reviewer's exact interface recommendation.

The canonical key remains project/template/default, with full Gig IDs.
Username is display metadata, not a pathname or authentication mechanism.
No new owner on repeated init; no automatic template overwrite, active-Gig
change, provider invocation, copied-tool execution, or implicit approval.
The installed release's explicit default inventory controls what is created;
unrelated packages and historical test Gigs are not swept into the batch.

## Review and implementation boundaries

- The earlier lifecycle/closeout reviews and measured tests remain historical
  evidence for their original scope, not acceptance of these corrections.
- The [roadmap](../../../v0.1.7/roadmaps/v0.1.7-scout-roadmap.md) and
  [ledger](execution-ledger.md) now agree with general initialization and the
  revised five findings. Older accepted sections are explicitly superseded
  where they conflict, not retroactively represented as new review evidence.
- Strict layout, owner, inventory, operation receipt, projection and report
  schemas plus affected callers require implementation, normal code review
  and tests. The amendment names the required regression scenarios; none was
  executed by this contract re-review.
- Existing G43.1 subject/baseline, private Gig state, provider consent, runtime
  code, and tests remain untouched by this documentation pass.
- Hooks, Dolt, broader interface research and effective-memory research stay
  on the v0.1.8 spike list, after Scout.

## Documentation verification

- `git diff --check` passed.
- 118 local link targets and 32 section anchors across 16 related documents
  resolve. Remaining named-init mentions in current decision documents are
  explicit descriptions of superseded proposals, not active entry points.
- The pre/post runtime/tests/tools/research diff SHA-256 is unchanged:
  `2d0d40c42fed7ab26f910c6a40a231239adee4adf293a47bc9e76942770eaa01`.
  The public G44 baseline/subject and existing untracked closeout schema/test
  hashes also match the pre-edit values.
- No runtime tests, live provider calls, or private state mutations were run
  for this documentation-only correction. All changes remain uncommitted.
