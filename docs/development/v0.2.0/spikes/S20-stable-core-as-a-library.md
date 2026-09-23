# S20 — Stable core as a library: what "0.2.0-stable" would actually require

**Requested:** 2026-09-23 (operator, verbatim intent recorded in
[the v0.2.0 README](../README.md)).
**Status:** Research recorded 2026-09-23. Discussion only — this ticket
proposes nothing as decided. No code, schema, or test file was changed.
**READ vs EXECUTED:** all module/LOC/schema inventories below are READ
(source files, `pyproject.toml`, existing v0.1.8/v0.1.9 spike documents) or
EXECUTED as side-effect-free counting commands (`wc -l`, `find`, `grep`,
`git cat-file`). No production source, schema, or test file was edited. No
`git add`/commit performed.

## Problem

The operator wants 0.2.0 to be "a stable gig core" so that afterward GigAI
can ship 0.2.1, 0.2.2, etc. frequently instead of large, infrequent
releases. Today there is no definition of what "stable" means for this
codebase: no declared public API, no semver policy, no typed surface, no
deprecation policy, and (per S16/S17, researched 2026-09-23 in
`docs/development/v0.1.9/spikes/`) core still imports a specific Gig
(`gigai.scout`) directly at 45 strict-pass call sites, which is the opposite
of a library boundary. This ticket inventories what exists today, with
evidence, so the operator can decide what "stable" requires before any
0.2.0 work is scheduled.

## Intended change (proposed, not decided)

Not proposed here as a design to implement. This ticket's only proposed
"change" is a way of thinking about the problem: treat `gigai` core as a
library with a declared public surface (`gigai.api`/`gigai.sdk` or
`__all__`-scoped modules), a semver contract for that surface, and a
gig-facing registration contract (S16's `GigManifest` proposal is the
leading candidate) — and treat everything not on that surface as free to
change without a version bump. Whether that takes the shape of a new
namespace package, `__all__` lists on existing modules, or something else is
explicitly open (see Investigate §1).

## Tasks

1. Derive today's actual public API surface from what Scout (the one real
   gig) imports from core.
2. Lay out the stability contracts a 0.2.x cadence needs: semver, deprecation,
   CLI-as-API, on-disk format versioning, and the S16 registration interface.
3. Inventory core for cleanup, sized and risk-ranked, without deleting
   anything.
4. Describe what "library packaging" requires: typed surface, docs, an
   example gig, distribution, compatibility testing.
5. Describe what makes 0.2.x releases cheap, citing the CI-speed work
   already done.
6. Sketch a themed (not dated) path from 0.1.9 to 0.2.0.

## Acceptance criteria

- Every claim cites `file:line` or a command actually run.
- Known facts (with evidence) are kept visibly separate from open questions.
- Nothing here is phrased as a decision; every proposal says so explicitly.
- Operator decisions needed are collected in one place at the end.

## Investigate

### 1. The public API surface today

**Method:** `gigai.scout` is the only real gig in the repo, so its imports
of anything under `src/gigai/*` outside `scout/` are the closest thing to
"what a gig actually uses from core" that exists to measure. Known bias:
Scout was built alongside core by the same author in the same repo, so it
may use *more* of core's internals than an arm's-length third-party gig
would, and it can't show what a *second* gig would need that Scout doesn't
(S16/S17 flag this for trader/shopper). Treat the inventory below as
"everything a gig-shaped consumer touches today," not a validated minimal
API. Derived by cross-referencing S16's own AST inventory
(`docs/development/v0.1.9/spikes/S16-core-gig-decoupling.md:242-283`, which
went the *other* direction, core→scout, but incidentally names every core
module Scout's code sits beside) against a direct grep of Scout's imports
(READ; reusing S16's already-verified 45-row inventory is more direct than
re-deriving from scratch). Core surface used by Scout today, grouped by
purpose:

| Core surface used by Scout | What for | Evidence |
| --- | --- | --- |
| `run_with_journal_writer`, `JournalTransition`, `read_committed_artifact` (journal module) | The validate→record→publish→read pattern every `*_records.py` file repeats | S17 table, `docs/development/v0.1.9/spikes/S17-gig-module-structure-classes.md:150-155` |
| `graph_node_registry` (lookup/register) | Node execution binding, though S16 found `run.py` sometimes bypasses this via direct import instead (`run.py:560,1400,1483-1487`) | `S16-core-gig-decoupling.md:271,273,276-279` |
| `NodeContext`, `NodeStatus`, `NodeFailure`, `NodeReceipt`, `GoalError`, `aggregate_status` (currently defined via `run.py`, re-exported through `scout/find_jobs/contracts.py`) | Generic node-execution shapes a graph node's return value must satisfy | `S16-core-gig-decoupling.md:269` — S16 already flags these as "generic node-execution shapes that belong in core," i.e. not really Scout-specific even though they're imported from `run.py` today |
| `ResolvedWorkpad` / workpad resolution | Every records module and CLI group resolves a workpad before reading/writing | Referenced throughout S17's `RecordRepository` sketch, `S17-gig-module-structure-classes.md:211-226` |
| `PrivateRecordError`, `WorkpadError` (core exception types) | CLI error handling (`cli.py:204,228,243,258,292,308,334,352` each catch these alongside Scout/gig-specific errors) | `src/gigai/cli.py:204` (read directly) |
| Schema/resource loading conventions (`validators.py`'s allow-list pattern) | Every domain module's schema validation goes through core's shared validator dispatch | `S16-core-gig-decoupling.md:313` (`validators.py:69,101-116`) |

**What's NOT on any stable surface today:** core's `run.py` (5,067 lines,
`wc -l src/gigai/run.py` → 5067) and `cli.py` (4,054 lines) import *Scout's
specific* names directly — `AcquireInput`, `FindJobsConfig`, `PinnedResume`,
`ScoutToolError`, etc. (S16's full strict-pass table,
`S16-core-gig-decoupling.md:246-283`). That is coupling in the *wrong*
direction for a library boundary: core depending on a gig's internal types,
not a gig depending on a declared core surface. A library-shaped core would
have zero lines like this.

**Proposal (not decided):** a `gigai.api` namespace (or, cheaper, `__all__`
lists on the modules already identified above — `journal.py`,
`graph_node_registry.py`, a new `gig_protocol.py` per S16, `workpad.py`,
top-level exception types) that a gig imports from, with every other core
module treated as implementation detail. S16's proposed `GigManifest`
protocol (`S16-core-gig-decoupling.md:334-345`) is the gig-*facing* half
(what a gig implements so core can call it); this section is the
core-*facing* half (what a gig may import from core). Complementary, not
competing, proposals.

**What becomes private:** everything not named above, starting with S16's
list of core modules that currently import Scout by name —
`application_events.py`, `capability_review.py`, `capability_successor.py`,
`cli.py`, `default_init.py`, `external_recording.py`, `native_records.py`,
`portability.py`, `private_transfer.py`, `run.py`
(`S16-core-gig-decoupling.md:233-236`). None of these should be gig-facing
API; today a gig *could* still import from them, since nothing marks them
private (no `_` prefix, no internal package, no `__all__`). Whether
"private" means a naming convention, a real `_internal` package, or just
documentation is Open question §1.

### 2. Stability contracts

**Semver policy for 0.2.x — not yet defined anywhere in the repo.** No
`CHANGELOG.md` entry, README section, or doc states what's allowed in a
0.2.x patch vs. a 0.2.(x+1) minor (checked: `grep -ri "semver\|semantic
version" CHANGELOG.md docs/development/changelog-internal.md README.md` →
no matches). Standard pre-1.0 semver (SemVer 2.0.0 spec, §4: "anything may
change at any time" below 1.0.0) technically permits breaking changes even
in a patch while `major==0`; if the operator wants 0.2.x to feel stable to a
gig author, that guarantee needs to be written down and be *stricter* than
bare semver allows — the version number alone does not make this decision.

**Deprecation policy — does not exist yet.** No mechanism was found
(checked: `grep -rn "DeprecationWarning\|@deprecated" src/gigai` → no
matches in core or scout). A one-minor-grace-period convention (deprecate in
0.2.x, remove in 0.2.(x+1)+) is a common pattern but isn't proposed as
settled here — it needs an actual mechanism (decorator, warning call,
"removed in" doc convention) that doesn't exist today.

**The CLI as an API — partially already true, not yet documented as a
contract.** `cli.py:169-182`'s `_raise_cli_error` already emits one stable
JSON error shape on `--json`/stdout: `{"status": "error", "error": {"code":
..., "message": ...}}` (read directly, `src/gigai/cli.py:169-182`). Multiple
success-path commands already emit sorted, separator-fixed JSON via
`json.dumps(payload, sort_keys=True, separators=(",", ":"))` (e.g.
`cli.py:208,232,246,261,296,311,338,361`, all read directly). This is a real
existing convention — output is deterministic and separated from prose —
but it is not written down as a promise anywhere, so nothing currently
prevents a future change from breaking a script that depends on a specific
JSON field. Making the CLI a documented, versioned API surface (which flags
are stable, which JSON fields are guaranteed, what "error.code" values
exist) is unstarted work, not a small formatting fix.

**On-disk formats (journal/records/schemas).** S14
(`docs/development/v0.1.8/spikes/S14-schema-inventory-and-consolidation-audit.md`)
already audited `src/gigai/schemas/`: **86 files total** (recounted directly,
`ls src/gigai/schemas/ | wc -l` → 86; S14's own count was "85 files, 82
`.schema.json`" as of 2026-09-22 — the 1-file difference from this ticket's
recount is not reconciled here and is flagged as an open item, not silently
overwritten). S14 found multiple schema families with 2+ packaged versions
and no documented retirement policy (`model-invocation` v1/v2/v3;
`external-recording-*` five sub-concepts each also as `-v2`;
`graph-selection-record`, `gig-proposal`, `active-gig-version`, `run-plan`,
`run-manifest`, `scout-document-selection` each with 2+ versions — S14
ticket, problem statement). S14's stated policy today is "strictly additive
... pinning prior hashes and declaring earlier resources byte-identical" —
i.e. schemas are never edited in place, only superseded by a new versioned
file. That is a real, already-practiced migration discipline, but it is
Scout-era practice discovered by an audit, not a written core contract that
says schema versioning and migration rules apply to *any* gig's on-disk
records, not just Scout's.

**The plugin/registration interface (S16) as the gig-facing contract.**
S16's proposed `GigManifest` protocol
(`docs/development/v0.1.9/spikes/S16-core-gig-decoupling.md:334-345`) is the
clearest existing candidate for "the one thing a gig implements to be a
gig." It does not exist in code yet (S16 is a proposal). If 0.2.0 is
"stable core," this manifest shape — once implemented — becomes exactly the
kind of interface that needs the strictest semver discipline of anything in
this repository, because every third-party gig depends on it directly.

### 3. Cleanup inventory before "stable"

**Size of core**, recounted directly (`for f in src/gigai/*.py; do wc -l
"$f"; done`, summed): **46,264 LOC** across 62 top-level `.py` files in
`src/gigai/` (outside `scout/`). Largest modules:

| Module | LOC |
| --- | --- |
| `run.py` | 5,067 |
| `cli.py` | 4,054 |
| `lifecycle.py` | 3,129 |
| `external_recording.py` | 3,059 |
| `run_plan.py` | 2,229 |
| `journal.py` | 1,305 |
| `capability_successor.py` | 1,277 |
| `validators.py` | 1,235 |
| `registry.py` | 1,142 |
| `package.py` | 1,112 |
| `runtime_comparison.py` | 1,107 |
| `proposal_interview.py` | 1,085 |
| `provider_review.py` | 1,082 |

`run.py` at 5,067 lines is also the single largest concentration of
core→scout coupling (S16: 14 of the 45 strict-pass rows are in `run.py`,
`S16-core-gig-decoupling.md:236`) — size and coupling risk are concentrated
in the same file, not independent problems. Scout itself, for comparison,
is 20,384 LOC across its top-level `.py` files (recounted,
`find src/gigai/scout -name "*.py" | xargs wc -l` sum) — core is more than
double the size of the one gig it hosts.

**Duplicated helpers (S17, already found, not yet fixed).**
`research.py:39-141` and `research_v3.py:39-141` are byte-identical
(verified by S17 via `diff`, zero output —
`docs/development/v0.1.9/spikes/S17-gig-module-structure-classes.md:165-176`).
A third, partial instance of the same private-helper cluster exists in
`tailoring.py:60-99` (same spike, same lines). This is inside Scout, not
core, but it is exactly the kind of duplication a "library-like foundation"
needs a shared base class for (S17's proposed `RecordRepository` protocol,
`S17-gig-module-structure-classes.md:211-226`) so a second gig doesn't
create a third copy.

**Legacy/versioned modules.** Only one `*_v2`/`*_v3`-named *module file*
exists in the whole tree today: `src/gigai/scout/research_v3.py` (checked:
`find src/gigai -iname "*_v2*.py" -o -iname "*_v3*.py"` → exactly one match).
There is no `run_plan_v2.py`, `journal_v2.py`, etc. at the module level —
the versioning problem the operator may be thinking of is almost entirely at
the *schema* level (S14's 82-schema inventory, above), not the Python module
level. This is worth saying plainly since "legacy/versioned modules" sounds
like it should be a bigger list than it is.

**Dead code candidates — listed, not deleted, per S14/S16's own discipline
of not concluding "dead" without evidence.**

- S14 found schemas with "no evident reader or writer" and flagged them for
  operator review rather than calling them dead
  (`S14-schema-inventory-and-consolidation-audit.md` problem statement,
  "Distinguish... (3) no evident reader or writer found — flag (3)
  explicitly for operator review rather than assuming it is dead code or
  safe to remove"). This ticket defers to that existing flag rather than
  re-deriving it.
- `native_records.py:459` is a docstring reference to `gigai.scout.tools`
  that will go stale once S16's migration moves that import — S16 already
  flagged this as "not a functional coupling site... update the docstring
  wording once the real imports it describes are migrated"
  (`S16-core-gig-decoupling.md:283`). Not dead code, but a known
  soon-to-be-stale comment.
- `proposal_cli.py` (Scout) is a naming trap, not dead code: it defines one
  function with no `@click` decorators at all despite its filename
  (`S17-gig-module-structure-classes.md:193-201`). Flagged because a
  "cleanup before stable" pass that renames-by-convention could
  misclassify it if it doesn't check content, not just filenames.
- No AST-based dead-code/unused-symbol scan of core (`vulture`,
  `unimported`, or similar) was run for this ticket — flagged as an open
  item, not claimed done.

**Schemas that could merge.** Deferred entirely to S14, which already did
this work in depth and reached "nothing is recommended for removal" as its
own conclusion (`docs/development/v0.1.8/spikes/README.md:526-533`, S14
status summary: "Seven schemas are flagged for operator review... Nothing
is recommended for removal"). Repeating that audit here would duplicate
work instead of adding to it; this ticket's contribution is only to point
0.2.0 planning at S14's existing findings.

**Rank by risk to stability (proposed ordering, not decided):**

1. **`run.py`'s core→scout coupling** (highest — S16's 14 `run.py` rows,
   concentrated in the largest core file, blocking any second gig).
2. **The undeclared CLI/JSON contract** (`cli.py`, 4,054 lines) — already
   load-bearing behavior (scripts likely already depend on today's JSON
   shapes) with zero written guarantee, so it can break silently.
3. **`external_recording.py`'s hand-rolled validator dispatch** (3,059
   lines, 18 of S16's 45 strict-pass rows) — the "registration by string"
   pattern S16 called "the clearest existing precedent" for a real registry,
   meaning it's already halfway to the right shape but not yet generalized.
4. **Schema multi-version families** (S14, medium risk — already
   understood and stable in practice, mainly a documentation/clarity gap
   per S14's own findings, not a functional risk).
5. **Scout-internal duplication** (S17's `research.py`/`research_v3.py`,
   `tailoring.py` partial) — lowest risk to core's stability specifically,
   since it's inside the one gig, not the platform boundary, but still
   worth fixing before a second gig copies the pattern a third time.

### 4. Library packaging

**Typed API.** No `py.typed` marker file exists anywhere under `src/`
(checked: `find src -iname "py.typed"` → no matches). No `mypy`/`pyright`
config exists (checked: `grep -n "mypy\|pyright" pyproject.toml` → no
matches; no `mypy.ini`/`pyrightconfig.json` present). There is no type
checking at all today, public or private surface — "typed API, strict on
the public surface" starts type checking from zero, not a tightening of an
existing check. Larger lift than the phrase suggests; size it as its own
workstream, not a checkbox inside 0.2.0.

**Docs for gig authors.** No "write your first gig" doc exists yet.
Proposed outline only (not written, not decided):
1. What a gig is (the operator's architecture rule, quoted verbatim in
   [the v0.2.0 README](../README.md)).
2. The `GigManifest` contract (once S16 is implemented) — what a gig
   registers and how core discovers it.
3. The `RecordRepository`/`GigCliGroup` base classes (once S17 is
   implemented) — what a gig subclasses for the validate→record→publish→
   read pattern and CLI wiring.
4. A worked example using the "hello gig" below.
5. What's stable to depend on (§1's surface) vs. what isn't.

**An example minimal gig.** Does not exist today. Proposed (not decided):
a trivial "hello gig" — one node, one record kind, one CLI command — built
*after* S16/S17 land, used both as the guide's worked example and as a
compatibility-test fixture (next paragraph). Building it before the
registration seam exists would just be another one-off special case for
core to import directly, repeating today's problem instead of proving the
fix.

**Distribution: in-repo vs. separate packages.** S16 already checked and
found `pyproject.toml` has no `[project.entry-points]` section at all
(confirmed again for this ticket: `grep -n "entry-points\|entry_points"
pyproject.toml` → no matches; `S16-core-gig-decoupling.md:356`). Entry
points are the standard Python mechanism for an installed third-party
package to register itself with a host library without the host naming it
in source (S16's own recommendation, `S16-core-gig-decoupling.md:367-369`).
Today Scout ships in-repo because it's the only gig and nothing else was
needed; a second gig distributed as a separate installable package is not
possible yet in any form, bundled or not, because there is no registration
mechanism at all (S16 is still a proposal, not implemented).

**Compatibility testing.** Does not exist. There is no test today that
builds a gig against one 0.2.x core version and asserts it still works
against another 0.2.(x+1) core version, because there is only one gig, it
lives in the same repo and commit as core, and there is no versioned public
surface to test compatibility against yet (§1). This is a real gap for the
"frequent 0.2.x releases" goal specifically: without it, nothing catches a
core change that breaks a gig until a human notices.

### 5. Release cadence and mechanics for frequent 0.2.x

**What already makes a release cheap — real, already-done work.** The
CI-speed worker (`.orchestrator/runs/v0.1.8/workers/ci-speed.md`, read in
full) measured the v0.1.7 exact-tag release run
(`gh run view 35660274375`) at **1:22:38 total wall time**, dominated by a
serial Debian-offline job (1:19:33, "zero parallelism, 1886 tests
serially") and three macOS legs each over 1h20m
(`ci-speed.md:20-30`). The same worker's fix
(read: commit `6d69ee4`, "ci: cut release CI time," this branch's most
recent commit per the session's git status) targeted release CI ≤30 min to
publish jobs and PR CI ≤15 min via parallel Debian-offline execution, a
narrowed macOS matrix, timeouts, caching, and a manual full-matrix dispatch
option (`ci-speed.md:1-5`). This is exactly the kind of mechanical cost that
makes "ship 0.2.1, 0.2.2 sooner" actually feasible instead of aspirational —
already landed, not still to do.

**Annotated tags — already the practice.** Checked directly
(`git cat-file -t v0.1.6` and `v0.1.7` → both `tag`, meaning both are
annotated tag objects, not lightweight refs). No change needed here; a
0.2.x cadence can keep doing exactly what v0.1.x already does.

**Changelog discipline — exists, is a two-file split.**
`CHANGELOG.md` (repo root) is explicitly "the external, capability-focused
history... internal technical history lives in
`docs/development/changelog-internal.md`" (`CHANGELOG.md:1-4`, read
directly). This split already works for v0.1.x; a faster 0.2.x cadence
means more, smaller entries in both files more often, not a new mechanism.

**`release_check.py` — exists** (`tools/release_check.py`, confirmed
present, not read in full for this ticket — flagged as an open item: what
gates it currently enforces and whether they still make sense for a 0.2.x
patch-release cadence is unverified here).

**What gates remain (open, not resolved by this ticket):** whether a 0.2.x
patch release requires the same full matrix as a 0.1.x minor, or a lighter
gate now that CI is faster; whether the compatibility test from §4 becomes
a release gate once it exists; whether schema-version additions (S14) need
their own release-note convention distinct from code changes.

### 6. Proposed path from 0.1.9 → 0.2.0 (themes, for discussion — not dates)

1. **Decouple** — implement S16's registration seam (`GigManifest`, node
   registry, domain-validator registry) so core stops importing
   `gigai.scout` by name. This is the literal prerequisite for "core never
   imports a gig" to be true rather than aspirational.
2. **Declare the surface** — once decoupling exists, mark what's left as
   the public API (§1's `gigai.api`/`__all__` proposal) and everything else
   private.
3. **Classes** — implement S17's `RecordRepository`/`GigCliGroup` base
   classes so the decoupling seam has a real shared implementation, not
   just a protocol every gig reimplements from scratch.
4. **Type and gate** — add `py.typed`, a type checker, and CI enforcement
   scoped to the declared public surface first (not all 46k core lines at
   once).
5. **Prove it** — build the "hello gig" example against the declared
   surface; use it as the first compatibility-test fixture.
6. **Write the guide** — "write your first gig," using the hello-gig
   example and the now-real `GigManifest` contract.
7. **Cut 0.2.0** — once 1–6 exist, tag it; 0.2.1+ ship on the faster CI from
   the ci-speed work, gated by whatever §5's open gate question resolves to.

This ordering follows dependency, not urgency: 2–6 each assume the previous
step's artifact exists. Nothing above is a scheduled task; it is a proposed
sequence for the operator to accept, reorder, or reject.

## Non-claims

- No code, schema, test, or config file was changed while writing this
  ticket.
- The "public API surface" in §1 is derived by approximation (what the one
  existing gig happens to import), not by design; a second gig may need
  more or different things. This is stated as a limitation, not resolved.
- This ticket does not re-audit S14's schema findings; it cites and defers
  to them.
- The 46,264-LOC core count and 20,384-LOC Scout count are today's totals
  for this checkout on 2026-09-23; they are not adjusted for the S16/S17
  proposals' own estimated effect on file sizes (neither spike estimates a
  resulting LOC change).
- No ranking, sizing, or path step above is authorized as scheduled work by
  this ticket.

## Open questions for the operator

1. Does "private" (§1) mean a naming convention (`_module.py`), a real
   `_internal` subpackage, `__all__` enforcement, or just documentation? No
   mechanism is proposed as chosen here.
2. Is bare pre-1.0 semver (§2, "anything may change") acceptable for 0.2.x,
   or does the operator want a stricter self-imposed contract starting at
   0.2.0 specifically, given the "ship sooner, more often" goal implies
   consumers will upgrade frequently and need to trust patch releases?
3. Should the CLI's existing JSON conventions (§2) be frozen and documented
   as-is, or revised once (breaking existing scripts once, deliberately)
   before declaring them stable?
4. Does compatibility testing (§4) require a second real gig to exist, or
   is the "hello gig" fixture sufficient proof for 0.2.0's initial release?
5. Which release gates (§5) should a 0.2.x *patch* skip that a 0.2.0 *minor*
   should keep — this ticket does not resolve `tools/release_check.py`'s
   current content, so the tradeoff isn't sized yet.
6. Does the operator want the 6-theme path in §6 treated as strictly
   sequential (block each theme on the last) or allowed to run partly in
   parallel (e.g. typing work starting before decoupling finishes, since
   they touch different files)?

## Change log

- 2026-09-23: Spike recorded. Derived today's core surface from Scout's
  actual imports plus S16's reverse-direction inventory; found zero
  `py.typed`/type-checker config and zero `entry-points` declarations;
  recounted core at 46,264 LOC / 62 files (`run.py` 5,067, `cli.py` 4,054
  largest) versus Scout's 20,384 LOC; confirmed only one `*_v2`/`*_v3`
  module file exists (`research_v3.py`) versus S14's already-audited
  82-schema-version proliferation; cited S17's byte-identical duplication
  finding; documented the existing (undeclared) CLI JSON-error contract at
  `cli.py:169-182`; confirmed annotated tags are already practiced; cited
  the CI-speed worker's measured 1:22:38 → target ≤30min release-CI work;
  proposed a risk ranking, a themed (undated) 0.1.9→0.2.0 path, and six
  open questions for the operator.
