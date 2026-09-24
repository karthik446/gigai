# Worker: v020-s21-gig-repos

**Task:** write the v0.2.0 spike S21 "Gigs in their own repos; gigai as a
facilitation library," handed off from the v0.1.8 coordinator (run
`run_b12de8fdda28`) at the operator's request. Dispatched in this worktree
under run `run_7cba834cb817`.

**Status:** Done. All three owned files written; no other files touched.

## Files written

- `docs/development/v0.2.0/spikes/S21-gig-repos-and-data-ownership.md` (new,
  549 lines — over the suggested ~250-400 but matching S20's evidence
  density across one more Investigate section than S20 has; not trimmed
  because trimming would have meant dropping citations, not prose).
- `docs/development/v0.2.0/spikes/README.md` (added one S21 index bullet in
  the existing style, alongside S20's).
- `.orchestrator/workers/v020-s21-gig-repos.md` (this file).

## What I READ vs EXECUTED

Read: `docs/development/v0.2.0/spikes/S20-stable-core-as-a-library.md` and
`docs/development/v0.2.0/README.md` in full (for tone/format), all of
`docs/development/v0.2.0/spikes/README.md`; the relevant sections of
`S16-core-gig-decoupling.md` and `S17-gig-module-structure-classes.md` cited
in S21; every `src/gigai/*.py` file/line the S21 doc cites, read directly via
`sed -n`/`Read` in this worktree (not trusted from the handoff).

Executed (side-effect-free): `wc -l` on core and Scout `.py` files, `ls`/`wc
-l` on `src/gigai/schemas/*.json`, `grep -n`/`grep -c` for the `from .scout`
coupling count and its reconciliation against S16's 45-row AST count, `grep
-n` for schema-name matches, `find`/`ls` checks for `node_modules`/`dist`
under Scout's UI directory, `find tests -name "test_*.py"` and a scoped
`grep -rl` for scout references, `git status --short`.

No code, schema, test, or storage file was edited. No `git add`/commit/
stash/reset/clean was run. No live/provider run was performed.

## What I found that differed from the handoff's evidence

All corrected in the S21 doc itself (§2, §4, §7, and the change log), not
silently fixed elsewhere:

- `private_records.py`'s workpad-layout-collision tuple is at lines
  **134-137** in this worktree, not the handoff's "136-139."
- `pyproject.toml`'s `gigai = "gigai.cli:cli"` console-script line is
  exactly **line 31**, not "30-31" (line 30 is the `[project.scripts]`
  table header).
- `native_records.py`'s docstring cross-reference to `gigai.scout.tools` is
  at line **462** in this worktree; S16 (and the handoff, citing S16) says
  459. Flagged, not corrected upstream.
- The handoff's F5 claimed Scout's UI directory (`src/gigai/scout/ui/`)
  contains `node_modules` and `dist`. Neither exists in this worktree —
  checked directly, both `ls` calls fail. Recorded as a correction, likely a
  clean-checkout artifact rather than a live discrepancy.
- Test-file counts drifted slightly from the handoff (161 test files under
  `tests/` vs. the handoff's 159; 51 reference `gigai.scout` vs. 54) — noted
  in the change log, not chased further since nothing in S21 depends on the
  exact count.
- **Reconciled the handoff's open "36 vs 45" item exactly:** a direct
  `from .scout`/`from gigai.scout` import grep gives 36 (matching the same
  10 files S16 named); the remaining 9 of S16's 45 are string-literal
  validator-source constants and `exc.name` comparisons in
  `external_recording.py` (8 sites) plus one docstring cross-reference in
  `native_records.py` (1) that a plain import-grep cannot catch. 36 + 9 =
  45. The two counts differ by method, not by disagreement about the code.

Every other `file:line` citation in the handoff (F1-F7) was re-verified and
found to match this worktree exactly.

## Open questions

None blocking — all substantive open questions are the ten the operator
still needs to resolve, collected in the S21 doc's "Open questions for the
operator" section (execution ownership A/B, store shape, physical data
location, CLI shape, UI scope, existing-workpad migration, model-call
routing enforcement, sequencing i/ii/iii, and Scout's repo/distribution
name). I did not need to ask the coordinator anything mid-task.

## r1 (rework after content review)

**Task:** fix items 1-4 of `.orchestrator/reviews/s21-r0.md` (item 5,
trimming toward ~400 lines, is optional and was skipped — the fixes for
1-4 add evidence, which works against trimming, and correctness took
priority over length under this dispatch's scope).

**Status:** Done. Same three owned files; no others touched by me (git
status shows `.orchestrator/decisions.log`, `.orchestrator/status.md`,
`.orchestrator/reviews/s21-r0.md`, and
`.orchestrator/workers/specs/v020-s21-gig-repos-r1.txt` also differ from a
clean baseline, but `git diff --stat` on the first two confirms those edits
are the coordinator's own, made outside this dispatch — I did not write to
any of those four).

### What I fixed (READ vs EXECUTED for each)

1. **§1's "unverified" rows, traced:**
   - Diagnostics: `cli.py:1916` `@cli.command("doctor")`, `cli.py:843`
     `@cli.command("setup")` — both READ directly, both already top-level
     `gigai` commands. Left one narrower unverified point, explained: whether
     `doctor`'s internal checks are already gig-agnostic wasn't traced
     line-by-line.
   - Credentials/config: `src/gigai/credentials.py` (82 lines, EXECUTED
     `wc -l`) and `src/gigai/config.py` (891 lines, EXECUTED `wc -l`), both
     READ (docstrings, credentials.py's import of `config.CredentialReference`
     at line 8). EXECUTED `grep -n scout` on both files → zero matches,
     confirming neither has Scout coupling today.
   - Adapters: `src/gigai/scout/tool_adapter.py` (70 lines, EXECUTED
     `wc -l`), READ in full — its docstring already states an explicit
     gig-code/gigai-publisher boundary.
   - Prompts: `src/gigai/scout/data/goalgraphs/*.md`, confirmed by directory
     listing (EXECUTED `find`), spot-checked `find-jobs.md`'s opening lines
     (READ) to confirm it's actual instruction prose, not a guessed location.
   - Stale/crashed-run reconciliation (the review's main ask): EXECUTED
     `grep -n "recover|abandoned|stale|heartbeat|reconcile" run.py` → 25
     matching lines, all READ. Found two real but narrow mechanisms
     (`_acquire_provider_review_lease`'s `flock`-based local liveness check,
     `run.py:1778`; `_recover_abandoned_provider_review`/
     `_recover_proposal_run_terminal`, `run.py:1804`/`:4580`) and confirmed
     no heartbeat/timeout/cross-process liveness check exists anywhere in
     the file — both mechanisms assume gigai's own process held an flock,
     which doesn't generalize to a gig's out-of-process run.
2. **§3's lean rewritten** to rest explicitly on sealing/scheduling/budget/
   effects (which do exist), not "staleness-detection machinery" (which r0
   had asserted existed, contradicting §1's finding). States plainly that
   stale-run detection is new work under either execution option (A) or
   (B), not something (A) already solves.
3. **Memory link replaced:** `[[gigai_v018_scout_first_020_gigs]]` in §5
   removed; replaced with `.orchestrator/decisions.log:12` (READ), which
   records the operator's own prior decision that "decoupling core from
   scout = 0.2.0 item #1" — an in-repo, resolvable citation.
4. **Entry-points citation added:** EXECUTED
   `grep -n "entry-points|entry_points" pyproject.toml` → no matches (exit
   1), cited directly in §4 rather than deferred solely to S20.

### Self-caught error

My first pass wrote "20 matches" for the `run.py` recovery grep in three
places, from a manual miscount of the first `grep -n` output. Re-ran `grep
-n ... | wc -l` (EXECUTED) → 25. Fixed all three occurrences before
sending `worker_done`.

### Open questions and operator decisions

Untouched except where (1)/(2)'s new evidence made a premise more precise
(Open question 1, on stale/crashed-run detection): the question's text
didn't need to change, since it already correctly stated "no mechanism is
proposed as chosen" — the new evidence makes that a confirmed absence
rather than an unstated preference, which is recorded in the r1 Change log
entry rather than by editing the question. The four operator decisions were
not touched.

### Acceptance check

- No "unverified" left in §1 without an explanation of what was searched
  (one remains, explained: `doctor`'s internal gig-agnosticism).
- Every new citation spot-checked with `sed -n` before and after writing
  (`run.py:1778/1804/4580/902/1007`, `cli.py:1916/843`,
  `credentials.py:1,8`, `config.py:1`, `tool_adapter.py:1-5`,
  `decisions.log:12`, the `pyproject.toml` grep, the goalgraphs directory
  listing).
- Change log has an r1 entry.
- `git status --short` shows the S21 file and README as my only diffs among
  files this dispatch owns; the worker file is new. (Other untracked/
  modified paths in the working tree are the coordinator's own concurrent
  writes, confirmed via `git diff --stat`, not mine.)

## r2 (fold in operator decision D-B)

**Task:** fold D-B ("monorepo for now; split into repos at 4-5 gigs with
clear separation," amending decision 1's timing) into the accepted r1 doc.
r1 itself was not redone — only D-B's consequences were folded in.

**Status:** Done. Same two owned files (S21 doc, this worker file); the
roadmap worker's `docs/development/v0.2.0/roadmaps/` and
`docs/development/v0.2.0/README.md` were left untouched, confirmed via
`git diff --stat docs/development/v0.2.0/README.md` showing a diff that
exists but that I never wrote (concurrent parallel work, not mine).

### What I did (READ vs EXECUTED)

This was a prose-only rework — no new `file:line` citations were
introduced, so no new code citations needed verification beyond what r1
already established (spot-checked `.orchestrator/decisions.log:12` again
regardless, READ, unchanged from r1).

1. **Operator decisions section:** kept decision 1's original wording
   byte-for-byte; added a new paragraph directly under it, "**Amended
   2026-09-23 (D-B): monorepo for now; split into repos at 4-5 gigs with
   clear separation.**" stating the amendment is to decision 1's *timing*,
   not a deletion or reopening. Decisions 2-4 untouched.
2. **Status/Problem:** reworded to state separate repos are the later
   state (at 4-5 gigs with clear separation) and the near-term direction is
   a monorepo with gigs as separately built packages.
3. **§4 (Packaging and repos):** added a framing paragraph up front stating
   explicitly that every "own repo"/"cross-repo" mechanic described is the
   later state, and that the near-term direction is §5's option (ii) — a
   `gigs/scout/`-style in-repo package with its own `pyproject.toml`.
   Reworded the console-script, validator-registration, example-gig, and
   CI-pinning bullets to name both horizons (monorepo-package now,
   separate-repo later) instead of assuming an imminent repo split.
4. **§5 (Sequencing options), the core change:** struck through and marked
   former option (iii) "extract now" as **ruled out by D-B** directly (kept
   visible, not deleted, so the record shows what changed and why); renamed
   former option (ii) from an "interim" step to the monorepo-period target;
   kept former option (i) as the other remaining ordering. Added an
   explicit "Open (not decided): (1) vs (2) ordering" paragraph stating D-B
   settles *where* gigs live during the monorepo period, not *when* to
   build the package split relative to S16/S17's in-repo decoupling work —
   this ordering is still open, matching Open question 9's rewrite (below).
   This framing (ruling out iii, promoting ii) is stated in the doc as
   following from D-B, not from my own judgment, per the task's
   instruction.
5. **§3 (execution ownership) and §7 (relationship to S16/S17/S20):**
   adjusted wording that had assumed a cross-repo Scout as the near-term
   case (e.g. "Scout's own process (in its own repo)," "once S21's
   extraction happens") to state that execution ownership and the
   S16/S17/S20 contracts apply the same way whether Scout is a monorepo
   package now or a separate repo later — these are a different axis from
   the repo-timing question D-B settles.
6. **Open questions 9 and 10, rewritten to match D-B** (numbering kept
   stable, no renumbering needed): Q9 now asks only about (i) vs (ii)
   in-repo ordering, since D-B already resolved the former (iii)
   alternative. Q10 now separates "Scout's own repo name" (deferred to
   whenever the later split happens — not an open question now) from
   "Scout's in-repo distribution/package name" (which may still be needed
   sooner, for §4's entry-point/CI work). Made one minor wording tweak to
   Q6 (UI scope) to say "own package/repo" rather than assuming a repo
   specifically — not one of the two questions the task named, but a small
   accuracy fix in the same spirit; left otherwise untouched per the task's
   instruction to leave open questions alone unless a premise changed.
7. **Change log:** added an r2 entry naming D-B as the source and
   summarizing every change above.

### Acceptance check

- Decision 1's original wording is kept + the D-B amendment sits directly
  under it (verified by re-reading the section after editing).
- Ran `grep -n "own repo"` after all edits (EXECUTED) and listed every
  remaining hit here with why it stays: the file's title (describes the
  eventual/named topic, unedited by task scope); decision 1's preserved
  original text ("each gig can be on it's own repo") and its own D-B
  amendment sentence (which itself explains the repo name is deferred);
  §4's framing note introducing both horizons; three D-B-qualified
  "eventually"/"later state" mentions inside §4's console-script,
  validator, and example-gig bullets; §5's struck-through, explicitly
  "ruled out by D-B" former option 3. No hit asserts Scout moves to its own
  repo **now**.
- Q9 and Q10 updated; numbering kept stable.
- r2 Change log entry added.
- `git status --short`: only the S21 file (already tracked as untracked
  from r0/r1) and this worker file are mine; `docs/development/v0.2.0/
  README.md` and `docs/development/v0.2.0/roadmaps/` are the parallel
  roadmap worker's, confirmed untouched by me via `git diff --stat`.

## r3 (precision fix: correct an r2 over-attribution)

**Task:** fix one precision error the coordinator identified in its own r2
spec (not my error to begin with, but mine to correct): D-B's operator text
is only "monorepo for now; split into repos at 4-5 gigs with clear
separation." It does **not** say gigs become separately built in-repo
packages/distributions — that in-repo package layout (`gigs/<name>/`, each
independently publishable) is the roadmap's **proposed** Workstream 7, not
an operator decision. r2 had stated in several places that D-B itself
settled this. r3 corrects that without touching the roadmap (owned by
another worker, per the task's exclusion).

**Status:** Done. Same two owned files (S21 doc, this worker file); the
roadmap directory and its worker's files were read only (to confirm
Workstream 7's actual wording) and never written.

### What I did (READ vs EXECUTED)

READ `docs/development/v0.2.0/roadmaps/v0.2.0-gig-workbench-roadmap.md`
lines 437-465 (Workstream 7 in full) to confirm its own wording before
citing it — it says "Per D-B, lay out gigs as `gigs/<name>/` packages,"
i.e. the roadmap worker's document itself also frames the layout as
following from D-B in its "Goal" line, but that document is explicitly out
of scope for me to fix (owned by another worker); I only needed to confirm
what the roadmap actually proposes (an in-repo package layout) versus what
D-B's own operator text says (only "monorepo for now; split at 4-5 gigs"),
which are two different things regardless of how the roadmap phrases its
own goal line.

Reworded every site in S21 that had attributed the in-repo package layout
to D-B directly:
1. **Status line:** added "corrected 2026-09-23 (r3, an over-attribution to
   D-B)" and restated that D-B says only the repo-boundary/timing fact, not
   the in-repo layout.
2. **The "Consequence" paragraph** (right after the four decisions):
   reworded to say the near-term in-repo shape is "a proposal (the
   roadmap's Workstream 7), not something D-B itself says."
3. **§4's framing paragraph:** rewritten to say D-B settles only the outer
   time-horizon question (later state = separate repos); the in-repo
   package layout is Workstream 7's proposal, cited directly, and whether
   it's adopted is left open.
4. **§4's validator-registration and CI-pinning bullets:** reworded to stop
   saying "D-B's near term" means separately built packages; now say
   "§5 option (ii), proposed by the roadmap's Workstream 7, not decided."
5. **§4's example-gig bullet:** minor adjacent fix — "monorepo subtree"
   language softened to "if §5 option (ii) is adopted."
6. **§5 (Sequencing options), the core rewrite:** intro paragraph now says
   D-B rules out (iii) only, and does not pick (i) vs (ii); option 2's text
   changed from "is now the monorepo-period target" to "is the roadmap's
   Workstream 7 proposal... whether to adopt it is open"; option 1 updated
   to "then adopt (ii) if adopted"; the "Open (not decided)" paragraph
   rewritten to cover both "whether (2) is adopted at all" and "its
   ordering against (1)," not just ordering; option (3) kept struck through
   and ruled out by D-B, unchanged, per the task's instruction.
7. **§7's S20 relationship bullet:** reworded "during D-B's monorepo period
   that means a `gigs/scout/` package" to "if the roadmap's Workstream 7
   in-repo package layout... is adopted during the D-B monorepo period,
   that would mean...".
8. **Open questions 9 and 10:** Q9 rewritten to ask both "whether" the
   in-repo split is adopted and "when" relative to decoupling, attributing
   the in-repo layout to the roadmap's Workstream 7 rather than D-B. Q10
   rewritten to say the in-repo distribution name is only a live question
   **if** (ii) is adopted — not needed at all if gigs stay as plain modules
   under `src/gigai/scout/` until the later split.
9. **Change log:** added an r3 entry naming the correction and listing
   every site touched, plus the EXECUTED post-edit grep result.

### Acceptance check

- EXECUTED `grep -n "monorepo-period target\|separately built"` after all
  edits: every remaining hit either says "proposed"/"a proposal"/"if
  adopted" explicitly (my own corrected text), or sits inside the r2/r3
  Change log entries describing what r2 had said or explaining the
  correction itself (left as historical record, not restated as current
  fact). No hit attributes the in-repo package layout to D-B as decided.
- §5 option (iii) remains struck through, unchanged.
- Q9 and Q10 updated per the task's exact instructions (Q10 now says the
  distribution name is only needed if (ii) is adopted).
- r3 Change log entry added, naming the correction.
- Roadmap file and directory not touched (confirmed: I only READ
  `v0.2.0-gig-workbench-roadmap.md`, never wrote to it or to any file under
  `docs/development/v0.2.0/roadmaps/`).
- `git status --short`: only the S21 file and this worker file reflect my
  writes; `.orchestrator/decisions.log`, `.orchestrator/status.md`,
  `docs/development/v0.2.0/README.md`, `docs/development/v0.2.0/spikes/
  README.md`, the roadmap directory, and the roadmap/review worker files
  are the coordinator's or the parallel roadmap worker's own concurrent
  activity, not mine.

### Changed paragraphs (pasted per the task's acceptance instruction)

**Status line (top of file):**

> **Status:** Discussion recorded 2026-09-23, amended 2026-09-23 (D-B),
> corrected 2026-09-23 (r3, an over-attribution to D-B). Not a plan, not
> authorized implementation, and not a v0.1.8/0.1.8.x blocker. No code,
> schema, or test file was changed while writing this ticket. Per D-B,
> separate repos are the **later** state (at 4-5 gigs with clear separation)
> and gigs stay in this one repo until then; D-B says only that, not how
> gigs are laid out inside this repo while they stay — whether they become
> separately built in-repo packages is proposed (by the roadmap's Workstream
> 7, not decided) and open here (§4, §5).

**§5 intro:**

> **D-B changes this section directly, but only by ruling out (iii) — it
> does not pick between (i) and (ii).** D-B's own words are "monorepo for
> now; split into repos at 4-5 gigs with clear separation." That settles the
> repo-*boundary* question for now (gigs stay in this repo) and rules out
> extracting a gig to its own repo before then. It says nothing about *how*
> gigs are laid out while they stay in this repo — specifically, it does not
> say gigs become separately built in-repo packages. That layout is
> **proposed** by the roadmap's Workstream 7
> (`docs/development/v0.2.0/roadmaps/v0.2.0-gig-workbench-roadmap.md`), not
> something D-B itself decided. An earlier pass of this ticket (r2) stated
> D-B settles this too; that was an over-attribution, corrected here (r3).

**§5's "Open (not decided)" paragraph:**

> **Open (not decided): whether (2) is adopted at all, and if so, its
> ordering against (1).** D-B rules out (3) and keeps gigs in this repo; it
> does not choose between staying as plain modules under `src/gigai/scout/`
> indefinitely (until the eventual repo split) versus adopting an in-repo
> package layout (2) first. If (2) is adopted, whether S16/S17's decoupling
> seam should land before the package split (1) or the package split happens
> first and decoupling proceeds inside it, is also open (Open question 9).
> Both "whether" and "when" are open; this ticket does not lean toward
> adopting (2) at all, let alone its ordering.

**Open question 9:**

> 9. **Sequencing (narrowed by D-B, but not fully settled by it):** D-B
>    rules out "extract now" (the former option iii) and keeps gigs in this
>    repo until 4-5 gigs show clear separation. D-B does **not** say gigs
>    become separately built in-repo packages while they stay — that in-repo
>    package layout is the roadmap's Workstream 7 proposal (former option
>    ii), not a D-B decision. What remains open is two-layered: *whether* to
>    adopt the in-repo package split at all, and if so, its ordering against
>    S16/S17's decoupling seam (former option i) — decouple first, then
>    split into a `gigs/scout/`-style package; or split first and decouple
>    inside it. Tradeoffs given in §5, no lean stated.

**Open question 10:**

> 10. **Distribution name for Scout's in-repo package, if adopted (repo
>     name deferred either way):** D-B defers the question of Scout's *own
>     repo name* to whenever the later split actually happens — not needed
>     now. The *distribution/package name* for a separately built Scout
>     package (e.g. what `gigs/scout/`'s `pyproject.toml` would call itself,
>     which is what a `gigai>=0.2,<0.3`-style pin and any entry-point
>     registration would reference) only becomes a question **if** the
>     roadmap's Workstream 7 in-repo package layout is adopted (§5, Open
>     question 9) — it is not needed at all if gigs stay as plain modules
>     under `src/gigai/scout/` until the later repo split.
