# S21 — Gigs in their own repos; gigai as a facilitation library

**Requested:** 2026-09-23 (operator, in chat; handed to this worktree by the
v0.1.8 coordinator per the operator's request "it needs to be stored in 1.9
worktree").
**Status:** Discussion recorded 2026-09-23, amended 2026-09-23 (D-B),
corrected 2026-09-23 (r3, an over-attribution to D-B). Not a plan, not
authorized implementation, and not a v0.1.8/0.1.8.x blocker. No code,
schema, or test file was changed while writing this ticket. Per D-B,
separate repos are the **later** state (at 4-5 gigs with clear separation)
and gigs stay in this one repo until then; D-B says only that, not how gigs
are laid out inside this repo while they stay — whether they become
separately built in-repo packages is proposed (by the roadmap's Workstream
7, not decided) and open here (§4, §5).
**READ vs EXECUTED:** module/line citations below are READ directly in this
worktree (`gigai-v0.1.9` @ `aa9d06a`) unless marked EXECUTED for a counting
command (`wc -l`, `grep -c`, `find`, `ls`). The originating dispatch's
evidence was gathered against `gigai-v0.1.8 @ de9b0d6`; every citation below
was re-verified against this worktree rather than trusted from the handoff —
several line numbers drifted by 1-3 lines and are corrected here (noted
inline where they differ from the handoff).

## Problem

Today gigai and Scout are one distribution, one repo, and — more importantly
— Scout's domain data is authoritative *inside gigai's own workpad journal*
(`private_records.py`), not inside anything Scout owns. The operator has
decided gigs eventually move to their own repos, own their own data, and
stay Python only — but per D-B, the repo split is deferred until there are
4-5 gigs with clear separation; until then, gigs stay in this one repo. That
is a bigger change than "split the repo, later": it reverses which system
holds authority over a gig's domain data (independent of which repo the
code lives in), and it moves execution (today `run.py` runs find-jobs
directly) somewhere not yet decided. This ticket records the decisions,
traces what they touch in today's code with evidence, and lists what's
still open — it does not design or authorize the migration.

## Operator decisions (recorded, not re-opened)

Verbatim-in-intent from the 2026-09-23 chat that originated this ticket:

1. **Gigs get their own repos.** "each gig can be on it's own repo.. gigai
   facilitates all the tracking, db tracking, run tracking on local.. we
   could also strip out pii by default later on." Scout moves to its own
   repo; this generalizes to any future gig.

   **Amended 2026-09-23 (D-B): monorepo for now; split into repos at 4-5
   gigs with clear separation.** Gigs live in one repo (this repo) until
   there are 4-5 gigs with clear separation between them, then split into
   separate repos. This amends decision 1's *timing* — separate repos are
   the later state, not the current direction — without deleting or
   reopening the original decision that gigs eventually get their own
   repos.
2. **Gig domain data lives in the gig's own store, not gigai's.** "they
   should live in scouts own.. scout runs it's own gigai just facilitates..
   like a cli.. again scout is our gig." gigai becomes something a gig
   depends on and drives (a library + CLI), not the thing that holds a gig's
   records.
3. **Python only.** "we can stick to python tbh it's fine.. not any stack."
   No polyglot protocol, no cross-language SDK generation.
4. **PII stripping by default is later**, not 0.2.0 scope unless the
   operator says otherwise. §6 records what 0.2.0 needs to preserve so this
   stays possible, not what to build now.

**Consequence stated by the operator's framing, not itself a new decision:**
gigai becomes a Python library + `gigai` CLI that a gig depends on (e.g.
`gigai>=0.2,<0.3`), and core never imports a gig — the existing rule quoted
verbatim in
[`docs/development/v0.2.0/README.md`](../README.md#the-gig-architecture-rule).
This keeps [S20](S20-stable-core-as-a-library.md)'s "core as a library"
framing and adds two things S20 didn't need to decide: gigs eventually ship
as *separate* distributions/repos (not just a declared API inside one
repo) — though D-B defers the repo split to a later, unscheduled point
(§4, §5), and whether the near-term shape inside this one repo is
separately built packages is a proposal (the roadmap's Workstream 7), not
something D-B itself says — and a gig's domain data belongs to the gig, not
to gigai, which is independent of which repo the gig's code lives in.

## Intended change (proposed, not decided)

No migration is proposed as ready to build. The only thing this ticket
proposes is a way to keep the four decisions above and today's code
consistent while more is decided: treat "gigai" as two roles that are
currently fused in one repo — (a) a generic run/journal/model-call/CLI
*facilitation* layer, and (b) Scout's own domain logic, schemas, records, and
UI — and use the evidence in §§1-4 to find where that fusion actually lives
in code before proposing how to unfuse it. Which of §3's execution options
(A/B) to pick, and which of §5's remaining sequencing options (i) vs (ii) to
pick — (iii) is ruled out by D-B, see §5 — is explicitly not decided here.

## Tasks

1. Draw the gigai/gig line under the four decisions, evidenced against
   today's modules, labeling each item required/optional/alternative.
2. Trace the F1 data-authority reversal: what moves out of core, what
   `state.sqlite`'s split must not lose, and what happens to existing
   workpads.
3. Lay out execution-ownership alternatives (Scout-calls-gigai-as-library vs
   Scout-owns-execution-and-reports), with a stated lean and its cost.
4. Lay out packaging/repo mechanics: distributions, entry points, schema and
   UI ownership, an in-repo example gig for core's own CI.
5. Give sequencing options for discussion, noting the 0.1.8.x constraint.
6. State the PII-by-default chokepoint under decision 2, deferred to later.
7. State S21's relationship to S16/S17/S20 without editing them.

## Acceptance criteria

- Every claim cites `file:line` (re-verified in this worktree) or a command
  actually run.
- The four operator decisions are recorded, not re-litigated.
- Open questions are collected in one place, phrased as questions.
- Counts that differ between the handoff's evidence and this ticket's
  recount are reconciled or explicitly flagged, not silently overwritten.

## Investigate

### 1. Where the gigai/gig line falls today

**Method:** for each decision-relevant capability, cite the module that
implements it today and say whether decisions 1-3 make it (a) something
gigai must keep providing generically, (b) something that moves wholly into
a gig, or (c) genuinely undecided pending §3/§5. This is not a proposed API
surface (S20 §1 already did that exercise for "core as a library" in-repo);
it's the same core-vs-gig line redrawn for the separate-repo, gig-owns-data
world.

| Capability | Today's home | Under decisions 1-3 | Label |
| --- | --- | --- | --- |
| Journal mechanics (validate→record→publish→read; `run_with_journal_writer`, `read_committed_artifact`) | `src/gigai/journal.py` (cited by S17, `S17-gig-module-structure-classes.md:150-155`, re-verified below in §7) | gigai keeps this as a library primitive a gig's *own* store uses; decision 2 says the gig owns *what* is stored, not that gigai stops offering *how* to store it durably | required |
| Run sealing, scheduling, budget/effects enforcement | `src/gigai/run.py` (5,067 lines, confirmed `wc -l src/gigai/run.py` → 5067, EXECUTED) | gigai keeps a generic version; today's find-jobs-specific parts (§2 below) move to Scout | required (generic part), moves (specific part) |
| Stale/crashed-run reconciliation | **What exists:** narrow, in-process crash recovery, not general staleness detection. `run.py:1778` `_acquire_provider_review_lease` takes an OS `flock` on a per-run lock file; its docstring (re-verified, lines 1781-1785) states "Local liveness only... Held from before run_started through terminal publication. Process death releases it; independent status readers contend on the same stable inode." `_recover_abandoned_provider_review` (`run.py:1804`, re-verified) is called when that lease's holder died mid-review (call sites `run.py:902` and `:1007`, both re-verified: `:1007` is reached from a Run-details read path that checks `status in {"preparing","running"}` and `_provider_review_active`, i.e. it fires on read, not on a timer). `_recover_proposal_run_terminal` (`run.py:4580`, re-verified; docstring lines 4591-4594: "This narrow recovery preserves the domain Goal/result state... when target observation or journal publication loses the normal scheduler finish race") handles a different narrow case — a proposal Run whose result evidence committed but whose terminal status write lost a race. **What does NOT exist:** neither mechanism detects a crashed run whose process gigai never started or is not co-located to check the lock file against (EXECUTED grep for `recover\|abandoned\|stale\|heartbeat\|reconcile` in `run.py`: 25 matching lines, re-read in full — every one is either these two functions' definitions/bodies/call-sites, unrelated journal-conflict "reconcile" language (e.g. lines 1043, 1057, 3589), or an unrelated "recovery edges" graph-validation check at lines 3951-3952; zero `heartbeat` hits and zero `stale` hits outside one code comment at line 1733 describing an unrelated race-window re-read — no heartbeat, timeout, or cross-process/cross-machine liveness check exists anywhere in the file). A gig running as a separate installed package's own process (decision 1) is exactly the case these mechanisms don't cover: the `flock`-based lease assumes gigai's own process tree held the lock, which only holds when gigai itself launched the run | Becomes more important, not less, and is largely new work: decision 1 makes the gig its own process/repo, so the one liveness mechanism that exists (an `flock` gigai's own process held) no longer applies once the gig's process is not gigai's child. Whether gigai detects staleness by polling a run record's heartbeat the gig writes, or the gig must explicitly report failure/exit, is Open question 1 | required, new mechanism needed (not just "undecided" — no reusable staleness-detection code exists to adapt) |
| Model calls (recording + redaction) | `src/gigai/model_execution.py` (`DEFAULT_REDACTION_POLICY` line 46, `redaction_values` field line 78, `redact_text` call line 233, `required_sensitive_values` check lines 234-236, EXECUTED `sed -n` spot-check) | gigai keeps this; it's the natural chokepoint for §6's later PII work, and decision 2 doesn't say gigs make their own model calls outside gigai — but nothing enforces that a gig *must* route calls through gigai once it's a separate process (see §6) | required, enforcement undecided |
| Credentials/config | `src/gigai/credentials.py` exists (82 lines, confirmed `wc -l`, EXECUTED), docstring (re-verified, line 1) "Credential-reference validation without secret-value access" — it imports `CredentialReference` from `src/gigai/config.py` (re-verified, line 8). `src/gigai/config.py` exists (891 lines, confirmed `wc -l`, EXECUTED), docstring (re-verified, line 1) "Typed, versioned, canonical machine configuration for GigAI." Both are already generic (no Scout import found in either file's docstring or opening imports checked here) | gigai keeps both; a gig-owned process still needs to resolve credentials and machine config through *something*, and these are already provider-agnostic by design, not Scout-specific | required |
| Per-gig directory provisioning | `src/gigai/workpad.py:90-95` (`ResolvedWorkpad(project_id, gig_id, path, target_root, target_kind)`, dataclass starts line 90 through line 95, re-verified — matches handoff exactly), `provision_workpad` at line 114 (re-verified, exact) | gigai keeps provisioning a directory per (project, gig) on local disk; decision 2 changes what gigai puts inside it (no more Scout-shaped subtrees), not that it provisions one | required (provisioning), changes (contents — see §2) |
| Diagnostics (`doctor`-style) | `@cli.command("doctor")` exists at `cli.py:1916` (re-verified exact) and `@cli.command("setup")` at `cli.py:843` (re-verified exact) — both already top-level `gigai` commands, not nested under a gig-specific group | gigai keeps both as cross-gig CLI surface; whether `doctor`'s current checks are gig-agnostic or contain Scout-specific checks was not traced line-by-line in this pass — flagged as a narrower unverified point, not the command's existence | required (commands exist); unverified whether doctor's internals are already gig-agnostic |
| Cross-gig CLI (`gigai runs`, `gigai export`, etc.) | `cli.py` exists (4,054 lines, confirmed `wc -l src/gigai/cli.py` → 4054, EXECUTED) but its current commands are largely Scout-specific per F6 below, not yet a generic cross-gig surface | required as a *shape* (gigai should offer generic run/export/doctor commands any gig benefits from); today's `cli.py` is not yet that — it imports Scout CLI groups directly (`cli.py:48-52` per S16, re-verified in §7) | required, not yet built |
| Graph + nodes (find-jobs' acquire/assess/present) | `src/gigai/scout/find_jobs/bindings.py` registers nodes (`bindings.py:441-450`, re-verified exact — `acquire`/`assess`/`present` registrations for `GRAPH_ID`, then a loop re-registering for other `graph_ids`) | moves wholly to Scout under decision 1; which half of *running* the graph stays in gigai is §3's open execution question | alternative (depends on §3) |
| Domain schemas (find-jobs config, tailoring, discovery, etc.) | `src/gigai/schemas/` holds 82 `*.schema.json` files total (EXECUTED `ls src/gigai/schemas/*.json \| wc -l` → 82; 13 match `scout-*.schema.json` by a name-only grep, 1 matches `application-event.schema.json` — EXECUTED, name-grep only, see §7 for why this undercounts) | moves wholly to Scout; core's validator dispatch (`validators.py:101-116`, re-verified — an allow-list of schema names that already includes many `scout-*` entries alongside generic ones) must accept gig-supplied schema names instead of a fixed core allow-list | required (gigai accepts registered schemas), moves (the schemas themselves) |
| Domain store (Scout's own records/projections) | `src/gigai/private_records.py`, `src/gigai/scout/projection.py` — see §2, this is the core of the F1 reversal | moves wholly to Scout | moves |
| Prompts, adapters | Prompts: `src/gigai/scout/data/goalgraphs/*.md` (confirmed present by directory listing, EXECUTED — `find-jobs.md`, `proposal-assessment.md`, `research-role.md`, `record-application.md`, `tailor-application.md`, `prepare-interview.md`, plus a `README.md`); spot-checked `find-jobs.md`'s first lines (re-verified) — it is instruction prose handed to the graph's nodes ("First read the shared handoff rules... Select the user's saved preferences explicitly..."), i.e. the actual prompt content, not just a filename guess. Adapters: `src/gigai/scout/tool_adapter.py` exists (70 lines, confirmed `wc -l`, EXECUTED), docstring (re-verified, lines 1-5) "Tiny tool-facing constructor for the C3 native-record operation shape. Gig-owned Python may call this only after an explicit invocation. It performs no authority lookup, source loading, SQL work, or journal publication; those remain in :mod:`gigai.scout.tools` and the native C1 publisher" — already an explicit adapter boundary between gig code and gigai's native record publisher | moves wholly to Scout (both are already under `src/gigai/scout/`, consistent with the gig-architecture rule) | moves |
| UI (Scout's Vite app) | `src/gigai/scout/ui/` exists with `index.html`, `package.json`, `vite.config.js`, `yarn.lock`, `src/` (confirmed by directory listing, EXECUTED `ls src/gigai/scout/ui`). **Correction to the handoff:** the handoff's F5 claimed `node_modules` and `dist` are present under this path; neither exists in this worktree (`ls src/gigai/scout/ui/node_modules` and `.../dist` both fail, EXECUTED) — likely `.gitignore`d build/install artifacts absent from a clean checkout, not a live discrepancy, but stated as re-verified rather than repeated unchecked | moves wholly to Scout | moves |
| Its own CLI (`scout ...` today reached via `gigai`) | `cli.py:48-52` imports Scout's CLI groups directly (`report_group`, `document_group`, `answer_group`, `acquisition_group`, `interview_group` — re-verified directly in this worktree; also tabulated by S16 at `S16-core-gig-decoupling.md:251-255`) | moves to Scout's own console script under decision 1; see §4 for the "own script vs `gigai scout …` dispatch" choice | alternative (§4 lean given, not decided) |

### 2. Data ownership migration (the F1 authority reversal)

**Today, Scout's domain data is authoritative *inside gigai's own private
journal*, not inside anything Scout owns.** This is the load-bearing fact
decision 2 reverses — not just a location change but an authority change.

- `src/gigai/scout/projection.py:1-8` docstring, re-verified verbatim: "The
  private Git journal and committed record artifacts are authority;
  `state.sqlite` is only a disposable cache of the value returned here."
  This is Scout's own module saying gigai's journal — not Scout — is
  authoritative.
- `src/gigai/private_records.py` — a **core** module — implements Scout's
  publish/import/record functions and rebuilds Scout's projection tables.
  Re-verified exact line numbers (the handoff's were within 1-2 lines of
  these): `_publish` at line 246, `import_reference` at line 307,
  `import_run_input` at line 339, `create_record` at line 431,
  `rebuild_scout_projection` at line 524, its `CREATE TABLE` statement at
  line 554, and `scout-operation-receipt.schema.json` validated at line 543
  (handoff said "~540" — re-verified at 543 exactly, also referenced at
  lines 227, 237, and 275). This is a core module that is Scout-specific in
  practice, and it is also the place today's authority actually lives.
- `state.sqlite` is **shared** between Scout's rebuildable projection and
  core's own G22 append-only interview trace — a real cross-cutting
  constraint on any split, re-verified:
  - `src/gigai/lifecycle.py:3003` `_persist_interview_trace`, whose comment
    (re-verified, lines 3004-3006) reads: "G22 shares `state.sqlite` with
    rebuildable SCOUT projections. It takes the same database lock as index
    publication; this function never takes the journal writer lock,
    preserving the documented journal -> database order."
  - `src/gigai/index.py:213-216` docstring, re-verified verbatim: "`
    state.sqlite` is currently shared by the rebuildable projection and the
    append-only interview trace. A malformed database can be safely
    replaced, but a recognized trace table must never be silently
    discarded."
  - `src/gigai/proposal_interview.py:578` `CREATE TABLE IF NOT EXISTS
    interview_events (...)`, re-verified exact.
  - `src/gigai/scout/projection.py:333-340`, re-verified: the in-memory
    projection tables are `opportunities`, `proposals`, `questions`,
    `documents`, `evidence`, `applications`, `runs`, `scout_cursor` — eight
    tables, all Scout-shaped, sitting in the same physical database file as
    core's `interview_events` trace.

  **Implication:** splitting the store is not "move a file" — the G22
  interview trace and Scout's projection currently live in the same SQLite
  file under the same lock discipline. Whichever design replaces this must
  either keep the trace in a gigai-owned database file that Scout's own
  store sits beside (not inside), or explicitly re-home the trace — this
  ticket does not decide which, see Open question 2.

- **Correction to the handoff:** the workpad-layout collision tuple the
  handoff cited as `private_records.py:136-139` is at lines **134-137** in
  this worktree (the `for name in (` opener is line 134; the tuple's two
  content lines are 135-136; the closing `):` is 137) — re-verified exact,
  content matches (`"README.md", "CHANGELOG.md", "gig.py", "goalgraphs",
  "ui", "docs", "references", "run-inputs", "records", "indexes"`).
- `src/gigai/package_privacy.py:27` `"reports/scout/"` and `:30`
  `_PRIVATE_EXACT_PATHS = frozenset({"indexes/context.json",
  "state.sqlite"})` — both re-verified exact, unchanged from the handoff.
  `package_privacy.py:1-6` docstring, re-verified verbatim: "This is
  intentionally a narrow provenance gate, not a content classifier."

**What this means for migration, stated as consequences rather than a
design:**

- **What moves out of core:** the Scout-authoritative parts of
  `private_records.py` (its record/import/publish functions and
  `rebuild_scout_projection`), `application_events.py` and
  `external_recording.py`'s Scout-domain dispatch (see §7's F6
  reconciliation for exactly which lines), and `scout/projection.py`
  wholesale. What of `private_records.py` is *not* Scout-specific (the
  journal-mechanics parts, if any survive as distinct from the
  Scout-record-shaped functions cited above) is not separated out in this
  pass — flagged as unverified, not claimed.
- **Correlation, not just co-location:** if a gig owns its own store, that
  store still needs to record which gigai `run_id` produced each entry, so
  gigai's run history and the gig's domain history can be joined without
  gigai reading the gig's schema. No mechanism for this is proposed here —
  Open question 2 asks what it looks like.
- **Existing workpads:** operators today have workpads with Scout data
  living inside gigai's journal (per the authority statement above). Whether
  a 0.2.0-era migration reads and re-homes that history into Scout's new
  store, or operators start fresh and old workpads become read-only
  archives, is **not decided** — Open question 6.

### 3. Execution ownership

Today core **hosts** Scout's execution; decision 2's "Scout runs it's own
gigai just facilitates" is a real change from that, not a restatement of the
status quo. Re-verified:

- `src/gigai/run.py:913` `def launch_find_jobs_run(` — re-verified exact,
  docstring at line 921: "Seal and launch one local find-jobs Run from the
  API boundary." `invocation_argv=("gigai", "find-jobs", "run")` at line 964
  — re-verified exact (handoff cited "~964," confirmed exact match).
  Find-jobs-specific logic also appears at lines 156 (`_FindJobsRunExecution`
  docstring), 337-338, 371, 493-494, 2580-2656 (sealing find-jobs run-input
  and config artifacts), and 4069 (`_read_find_jobs_run_input`) — all
  re-verified present at those lines.
- `src/gigai/run.py:53` `from .graph_node_registry import lookup as
  lookup_graph_node` — re-verified exact. Scout registers its nodes in
  `src/gigai/scout/find_jobs/bindings.py:441-450` (re-verified exact —
  `register(GRAPH_ID, GRAPH_VERSION, "acquire"/"assess"/"present", ...)`
  calls). `bindings.py:1-6` docstring, re-verified verbatim: "The graph-node
  registry is deliberately process-local. `launch_run` starts the
  deterministic scheduler in a spawned child, so registration also installs
  a module-level worker wrapper; the wrapper re-registers the nodes in the
  child before handing control to the existing scheduler entry point." —
  i.e. today's registry is explicitly designed around Scout's execution
  happening in a child process of the *same* codebase, not a separate repo's
  process.
- Scout's UI API calls back into core's `run` module: `src/gigai/scout/
  find_jobs/present_api.py` re-verified — line 24 `from ...run import
  RunError`; lines 154, 169, 205 each `from ... import run`.

**Two alternatives, not decided:**

- **(A) Scout's CLI calls gigai's generic graph runner as a library.**
  `run.py` loses every find-jobs-specific name (`AcquireInput`,
  `FindJobsConfig`, `PinnedResume`, `ScoutToolError`, etc. — S16's full
  strict-pass table already names these as core→Scout coupling that
  shouldn't exist, `S16-core-gig-decoupling.md:246-283`, re-verified present
  at those lines) and exposes only a generic
  `launch_run(graph_id, graph_version, node_bindings, config_bytes, ...)`
  shape. Scout's own process — running as its own installed package,
  whether that's a `gigs/scout/` subtree of this monorepo per D-B's near
  term or Scout's own eventual repo — imports `gigai` as a library and
  calls that generic entry point with its own node bindings and config.
  This execution-ownership question (§3) is independent of the repo
  question D-B settles (§5): (A)/(B) is about which process runs the
  graph, not which repo that process's code lives in.
- **(B) Scout owns execution and only reports run lifecycle/events to
  gigai.** Scout runs its own scheduler entirely; gigai receives
  start/heartbeat/finish/fail events over some interface (a CLI call, a
  local API, a written record) purely to maintain run tracking, without
  ever calling into Scout's graph itself.

**Lean (not decided): (A), resting on sealing/scheduling/effects/budget, not
staleness detection.** The sealing, scheduling, and budget/effects
enforcement machinery that makes up the bulk of "run tracking" already
exists in `run.py` as of today. **Stale/crashed-run detection is not part of
that existing machinery** — §1's table traces exactly what `run.py` has
(`_acquire_provider_review_lease`'s `flock`-based liveness check and two
narrow crash/race-recovery functions, `run.py:1778,1804,4580`) and what it
doesn't (no heartbeat, timeout, or cross-process liveness check for a run
gigai didn't itself launch). So the lean toward (A) is not "gigai already
solves staleness, so use it" — it is that (A) reuses the sealing/scheduling/
budget machinery that *does* exist, and staleness detection is new work
*regardless of which option is chosen*, since neither run.py's existing
liveness mechanism nor Scout's own execution today assumes a gig running as
a separate installed package's out-of-process job. Under (B), every gig
would additionally need to reimplement sealing/scheduling/budget/effects
itself and then translate it into events gigai can understand, which
duplicates exactly the machinery decision 1 says gigai should keep
providing ("gigai facilitates all the tracking, db tracking, run
tracking"). (A)'s cost: `run.py`'s generic/find-jobs-specific split has to
actually happen first (this is S16's unfinished decoupling work, not new to
this ticket), a library-mode graph runner has to work correctly when called
from a separate installed package's process (untested today — Scout
currently only ever runs inside the same repo/process tree as core), and
staleness detection has to be designed from scratch either way (Open
question 1).

### 4. Packaging and repos

**Per D-B, the mechanics below split into two time horizons — but D-B
itself only settles the outer one.** Everything that names "Scout's own
repo," "cross-repo," or "separate repo" describes the **later** state (at
4-5 gigs with clear separation), not work proposed for now; D-B is explicit
about that much. What D-B does *not* say is how gigs are laid out while
they stay in this one repo. §5 option (ii) — gigs as separately built
packages inside this repo (e.g. `gigs/scout/` with its own
`pyproject.toml`) — is a **proposal** (the roadmap's Workstream 7,
`docs/development/v0.2.0/roadmaps/v0.2.0-gig-workbench-roadmap.md`), not
something D-B decided; §5 states this distinction directly. If (ii) is
adopted, it would prove the same packaging mechanics (entry points, a
declared distribution name, schema/UI ownership) without a second repo or
cross-repo CI — but whether it is adopted, and when relative to decoupling,
is open (§5). This section is left describing both horizons because the
packaging *mechanics* (entry-point group, console-script shape, schema
registration) are the same either way — only the repo boundary differs.

- **One distribution today.** `pyproject.toml:31` (re-verified — the
  handoff cited "30-31"; line 30 is the `[project.scripts]` table header,
  the script line itself is exactly 31) `gigai = "gigai.cli:cli"` — a single
  console script. `pyproject.toml:46` (re-verified exact, unchanged from the
  handoff) declares `gigai.scout` package-data: `data/*.md`,
  `data/goalgraphs/*.md`, `data/ui/*.html`, `data/ui/*.css`,
  `data/tools/*/*.json`.
- **Schemas: 82 files total** (EXECUTED `ls src/gigai/schemas/*.json | wc
  -l` → 82, matching S14's prior count exactly — no drift found here,
  unlike S20's own recount which found an 86-vs-85 discrepancy for a
  different `ls` invocation; not re-litigated here). A name-only grep found
  13 files matching `scout-*.schema.json` and 1 matching
  `application-event.schema.json` (EXECUTED). **This is a name-grep only, as
  the handoff itself flagged** — several schemas Scout actually validates
  against use generic names, not a `scout-` prefix (`validators.py:101-116`,
  re-verified, lists `model-invocation-v2.schema.json`,
  `runtime-evaluation-pack.schema.json`, and others alongside
  `scout-proposal-revision.schema.json` etc. in one flat allow-list with no
  naming convention distinguishing "Scout's" from "core's"). Tracing which
  of the 82 are actually Scout-owned by field name, not filename, was not
  done in this pass — flagged as required before packaging work, not
  claimed done.
- **Scout's UI is a separate Vite app** under `src/gigai/scout/ui/`
  (`index.html`, `package.json`, `vite.config.js`, `yarn.lock`, `src/` —
  confirmed present; `node_modules`/`dist` are not present in this checkout,
  correcting the handoff's F5 claim as noted in §1's table).
- **Entry-point discovery.** No `[project.entry-points]` section exists in
  `pyproject.toml` today — re-verified directly for this ticket (EXECUTED
  `grep -n "entry-points\|entry_points" pyproject.toml` → no matches, exit
  code 1), consistent with S20's own prior finding
  (`S20-stable-core-as-a-library.md:312-315`). A `gigai.gigs` entry-point
  group (the standard Python mechanism for an installed package to register
  itself with a host library) is the natural discovery point for gigai to
  find an
  installed gig without importing it by name — proposed, not decided.
- **Scout's own console script vs. `gigai scout …` plugin dispatch.**
  Today `cli.py:48-52` (re-verified — five `from .scout.*_cli import
  *_group` lines) wires Scout's CLI groups directly into `gigai`'s own
  `click` group. Under decision 1 (Scout in its own repo, eventually — D-B),
  the natural shape is Scout ships its own console script (e.g.
  `scout = "scout.cli:cli"` in Scout's own `pyproject.toml`, whether that
  `pyproject.toml` lives in a `gigs/scout/` subtree of this monorepo per
  D-B's near-term direction, or in Scout's own eventual repo) that imports
  and calls `gigai` as a library, rather than `gigai`'s CLI reaching into an
  installed `scout` package's CLI groups. **Lean (not decided): Scout's own
  script** — this matches decision 2's framing ("scout runs it's own gigai
  just facilitates... like a cli") more directly than a `gigai scout …`
  dispatch model, which would require gigai to import Scout by name again,
  the exact coupling direction the architecture rule forbids. This lean
  does not depend on which repo horizon is in effect.
- **Core validators must accept gig-supplied schemas.** `validators.py:69`
  (re-verified — part of the same allow-list block cited above) and
  `:101-116` currently hard-code every accepted schema name as a fixed
  tuple in core. Whether gigs eventually become separately built in-repo
  packages (§5 option (ii), a proposal, not decided) or move straight to
  their own repos (the later state, per D-B), this must become a
  registration call a gig makes (adds its schema names to the accepted set
  at import/register time) rather than a fixed list core ships with baked
  in — this is the schema-side analog of S16's `GigManifest` proposal
  (`S16-core-gig-decoupling.md:335-343`, re-verified — the `GigManifest`
  Protocol already includes a `schema_names: Sequence[str]` field for
  exactly this).
- **A tiny example gig kept in the gigai repo.** Proposed (not decided): so
  core's own CI can test the gig-facing contract (registration, schema
  acceptance, journal usage) directly, in-repo, without depending on
  Scout's own build at all. S20 already proposed a "hello gig" for a
  similar reason (proving the in-repo decoupling seam,
  `S20-stable-core-as-a-library.md:304-310`); this ticket's version of the
  same idea is unaffected by D-B either way — it lives in the gigai repo
  regardless of whether Scout itself becomes an in-repo package (if §5
  option (ii) is adopted) or stays directly under `src/gigai/scout/` until
  the later repo split. Testing the *cross-repo* installation path
  specifically (gigai installed as a dependency, not a sibling module) is a
  later-horizon concern once a gig actually moves to its own repo, per D-B.
- **A separately built gig's CI pins a gigai version range** (e.g.
  `gigai>=0.2,<0.3`) and optionally runs a second job against gigai's
  `main` branch to catch breaking changes early — proposed, not decided;
  applies once a gig has its own build, whether that's a monorepo subtree
  with its own `pyproject.toml` (§5 option (ii), proposed by the roadmap's
  Workstream 7, not decided) or a separate repo (the later state, per
  D-B); depends on S20's semver contract (S20 Open question 2) actually
  being answered first.

### 5. Sequencing options (for discussion)

**D-B changes this section directly, but only by ruling out (iii) — it does
not pick between (i) and (ii).** D-B's own words are "monorepo for now;
split into repos at 4-5 gigs with clear separation." That settles the
repo-*boundary* question for now (gigs stay in this repo) and rules out
extracting a gig to its own repo before then. It says nothing about *how*
gigs are laid out while they stay in this repo — specifically, it does not
say gigs become separately built in-repo packages. That layout is
**proposed** by the roadmap's Workstream 7
(`docs/development/v0.2.0/roadmaps/v0.2.0-gig-workbench-roadmap.md`), not
something D-B itself decided. An earlier pass of this ticket (r2) stated
D-B settles this too; that was an over-attribution, corrected here (r3).

1. **Decouple in-repo first (S16), then adopt (ii) if adopted.** Finish
   S16's registration seam and S17's base classes while Scout still lives
   directly under `src/gigai/scout/`, prove the seam works with a same-repo
   "hello gig," then move Scout into its own in-repo package (e.g.
   `gigs/scout/`) once the seam is real rather than theoretical — if the
   in-repo package layout (2) is adopted at all.
2. **Lay out gigs as separately built in-repo packages** — e.g. a
   `gigs/scout/` subtree with its own `pyproject.toml`, built and versioned
   separately but still living in this git repository — and let the
   decoupling seam (S16/S17) land inside that structure rather than before
   it. This is the roadmap's Workstream 7 proposal, not a D-B decision: it
   would prove packaging/entry-point mechanics (a real second distribution,
   in-repo) without the added friction of cross-repo CI or a separate
   release process, but whether to adopt it is open.
3. ~~**Extract now.**~~ **Ruled out by D-B.** Moving Scout to its own repo
   before there are 4-5 gigs with clear separation contradicts D-B's
   "monorepo for now" directly. Kept here, struck through, so the option
   this ticket previously listed as live is not silently deleted from the
   record.

**Open (not decided): whether (2) is adopted at all, and if so, its
ordering against (1).** D-B rules out (3) and keeps gigs in this repo; it
does not choose between staying as plain modules under `src/gigai/scout/`
indefinitely (until the eventual repo split) versus adopting an in-repo
package layout (2) first. If (2) is adopted, whether S16/S17's decoupling
seam should land before the package split (1) or the package split happens
first and decoupling proceeds inside it, is also open (Open question 9).
Both "whether" and "when" are open; this ticket does not lean toward
adopting (2) at all, let alone its ordering.

**When the later split (3) actually happens:** at 4-5 gigs with clear
separation, per D-B verbatim. No count or timeline is proposed here beyond
what D-B states; this ticket does not add a number or date the operator
didn't give.

**Constraint that holds regardless of choice:** `.orchestrator/decisions.log:12`
(re-verified) records the operator's prior decision: "Scout is its own gig →
move to `src/gigai/scout/`... in this PR; decoupling core from scout = 0.2.0
item #1" — i.e. decoupling was already scoped to 0.2.0, not 0.1.8.x, before
this ticket, and before D-B. Nothing in this ticket or its eventual
resolution disrupts 0.1.8.x work. Both remaining sequencing options are
0.2.0-line work.

### 6. PII-by-default (later)

Decision 4 is explicit: PII-by-default is not 0.2.0 scope. What matters now
is what 0.2.0 must not foreclose.

- **Today's redaction is explicit value-list redaction of model-provider
  input, not default PII detection.** `src/gigai/model_execution.py:46`
  `DEFAULT_REDACTION_POLICY = "g18-explicit-redaction-1"` (re-verified
  exact), `:78` `redaction_values: tuple[str, ...] = ()` (re-verified
  exact, a field the caller populates explicitly — nothing scans for PII on
  its own), `:233-236` `redact_text(provider_input or "",
  policy.redaction_values)` and the `required_sensitive_values` fail-closed
  check (re-verified — call at line 233, check at line 234-236).
  `package_privacy.py:1-6`, re-verified verbatim: "This is intentionally a
  narrow provenance gate, not a content classifier."
- **The chokepoint under decision 2 is gigai-owned records** — run records,
  events, model-call logs, exports — because those are the artifacts that
  stay inside gigai's boundary even after a gig's *domain* data moves out.
  A gig's own store (Scout's database, in Scout's repo) is Scout's
  responsibility, not gigai's, under decision 2 — gigai cannot strip PII
  from data it never sees.
- **What 0.2.0 must preserve:** the convention that model calls go through
  gigai's model-execution path (so redaction has one place to live), and
  that gigai's own run/event/export records stay structured enough that a
  future default-redaction pass can target them mechanically. **This is a
  convention in a library model, not an enforceable one** — once a gig is a
  separate installed package calling gigai as a library, nothing stops that
  gig's code from calling a model provider directly without going through
  gigai. Saying so plainly here rather than implying gigai can guarantee
  it.

### 7. Relationship to S16, S17, S20

This ticket does not edit S16, S17, or S20; it states how S21 relates to
each without re-deriving their findings.

- **S16 (core/gig decoupling)** proposed the `GigManifest` protocol
  (`S16-core-gig-decoupling.md:335-343`, re-verified) as the seam a gig
  registers through. Under execution option (A) in §3, this proposal still
  applies directly regardless of D-B's repo timing — a gig built as a
  separate package (whether a `gigs/scout/` subtree now, per D-B, or a
  separate repo later) registers through the same kind of manifest an
  in-repo gig would have. S16's 45-row strict-pass coupling
  inventory is **reconciled against this ticket's own recount**, not just
  cited: a direct grep in this worktree for `from \.scout|from gigai\.scout`
  across `src/gigai/*.py` returns exactly **36** matches across the same 10
  files S16 named (`application_events.py`, `capability_review.py`,
  `capability_successor.py`, `cli.py`, `default_init.py`,
  `external_recording.py`, `native_records.py`, `portability.py`,
  `private_transfer.py`, `run.py` — EXECUTED, re-verified file list matches
  exactly). The remaining **9** of S16's 45 are non-`from`-import
  string-literal or docstring hits that a plain `from .scout` grep cannot
  catch: 8 in `external_recording.py` (validator-source string constants at
  lines 57, 60, 63, 66, and `exc.name` comparison strings at lines 96, 121,
  144, 169 — all EXECUTED, re-verified present) plus 1 docstring
  cross-reference in `native_records.py` (S16 cited line 459; re-verified in
  this worktree at line **462** — a small drift, not reconciled further
  here, flagged rather than silently corrected). **36 + 9 = 45**,
  reconciling the handoff's "36 vs 45" open item exactly: the two counts
  differ by method (import-statement grep vs. AST scan including string
  literals and docstrings), not by disagreement about the code.
- **S17 (gig module classes)** proposed `RecordRepository`/`GigCliGroup`
  base classes for the validate→record→publish→read pattern
  (`S17-gig-module-structure-classes.md:211-226`, not re-quoted here). Under
  decision 2, these classes become more important, not less: if Scout's own
  store must implement this pattern itself (no longer inside
  `private_records.py`), a shared base class in the gigai library is what
  keeps every future gig from reimplementing the pattern from scratch. S21
  does not change S17's proposal; it adds that the base classes now need to
  work when the concrete store lives in a *different distribution* from the
  classes themselves — a `gigs/scout/` package during the D-B monorepo
  period, or a fully separate repository once the later split happens; the
  distinction that matters to S17's classes is the distribution boundary,
  not specifically the repo boundary.
- **S20 (stable core as a library)** derived today's core→gig API surface
  from Scout's actual imports and proposed a `gigai.api`/`__all__`-scoped
  public surface with a semver contract. That surface is what any
  separately built Scout package would pin against — if the roadmap's
  Workstream 7 in-repo package layout (§5 option (ii)) is adopted during
  the D-B monorepo period, that would mean a `gigs/scout/` package pinning
  a `gigai` version range within this repo's own build; once the later
  repo split happens (per D-B), the same pinning applies across repos.
  S20's semver/deprecation/CLI-as-API work becomes the literal contract
  either way a separate build exists, not just a nice-to-have once repos
  split. S21 does not change S20's proposal or its six open questions; it
  depends on them being answered before any separately built Scout package
  — in-repo or cross-repo — could safely pin a gigai version range.

## Non-claims

- No code, schema, test, or storage change was made while writing this
  ticket.
- No git `add`/`commit`/`stash`/`reset`/`clean` was run.
- No live or provider run was performed; all evidence is static reads and
  side-effect-free counting commands.
- Execution option (A) is stated as a lean with a cost, not a decision;
  option (B) remains live.
- The sequencing options in §5 are not ranked as a recommendation beyond
  the tradeoffs stated; picking one is the operator's decision.
- This ticket does not re-audit S14's schema inventory, S16's or S17's
  proposals, or S20's API-surface derivation; it cites and, where a count
  overlapped, reconciles against them (§7).
- §1's r0 draft marked "credentials/config," "diagnostics," and "prompts,
  adapters" unverified without tracing them; r1 traced all three to
  `credentials.py`/`config.py`, `cli.py`'s `doctor`/`setup` commands, and
  `scout/data/goalgraphs/*.md`/`scout/tool_adapter.py` respectively (§1).
  The one remaining unverified point is narrower and stated as such:
  whether `doctor`'s internal checks are already gig-agnostic or contain
  Scout-specific logic was not traced line-by-line.
- Stale/crashed-run reconciliation (§1, §3) was checked by grepping
  `run.py` for `recover|abandoned|stale|heartbeat|reconcile` (25 matches,
  EXECUTED) rather than asserted either way: two narrow, already-existing
  recovery functions were found and traced, and no heartbeat/timeout/
  cross-process liveness mechanism was found anywhere in the file.
- The 46,264-core-LOC / 20,384-Scout-LOC figures (re-verified, EXECUTED)
  and the 82-schema count (re-verified, EXECUTED) match prior spikes'
  counts exactly; the two corrected citations (§2's `private_records.py`
  tuple lines, §4's `pyproject.toml` script line) and the one small
  citation drift (§7's `native_records.py` docstring line) are the only
  discrepancies found against the originating handoff's evidence.
- This ticket is not a v0.1.8 or v0.1.8.x blocker; nothing here changes
  that work's scope or schedule.

## Open questions for the operator

1. **Stale/crashed-run reconciliation mechanism:** once a gig owns its own
   process (decision 1), does gigai detect a stale/crashed run by polling a
   heartbeat the gig writes, or must the gig explicitly report failure/exit
   to gigai? No mechanism is proposed as chosen (§1).
2. **Store shape and correlation:** is Scout's own store "plain SQLite as
   authority" or "gigai's journal library, instantiated in a Scout-owned
   directory"? Either way, how does a record in Scout's store reference the
   gigai `run_id` that produced it, so the two histories can be joined
   without gigai reading Scout's schema (§2)?
3. **Where does a gig's data live physically?** A gigai-provisioned
   per-(project, gig) directory (extending today's `workpad.py` model), or
   anywhere the gig chooses, with gigai only tracking a pointer to it (§1,
   §2)?
4. **Execution ownership:** option (A) — Scout calls gigai's generic graph
   runner as a library — or option (B) — Scout owns execution and only
   reports lifecycle/events to gigai? A lean toward (A) is given in §3, not
   a decision.
5. **CLI shape:** does Scout ship its own console script (`scout = ...`),
   or does `gigai` dispatch to an installed Scout package's CLI groups
   (`gigai scout ...`)? A lean toward "Scout's own script" is given in §4,
   not a decision.
6. **UI scope:** does Scout's UI move wholly into Scout's own package/repo
   (implied by decisions 1-2, on whichever D-B horizon applies when it
   happens), or does gigai eventually grow a generic run-viewer UI of its
   own that any gig's runs could appear in? Not addressed by the operator's
   2026-09-23 decisions; open.
7. **Existing-workpad migration:** do operators' current workpads (with
   Scout data inside gigai's journal, per §2's authority finding) get
   migrated into Scout's new store, or does the operator start fresh and
   treat old workpads as read-only archives (§2)?
8. **Must gigs route model calls through gigai?** Decision 4 defers
   PII-by-default, but §6 notes gigai cannot enforce that a separately
   installed gig calls its model-execution path at all. Does the operator
   want this as a documented convention only, or is some enforcement
   mechanism (e.g. a required dependency injection point) worth deciding
   now even though the redaction work itself is later?
9. **Sequencing (narrowed by D-B, but not fully settled by it):** D-B rules
   out "extract now" (the former option iii) and keeps gigs in this repo
   until 4-5 gigs show clear separation. D-B does **not** say gigs become
   separately built in-repo packages while they stay — that in-repo package
   layout is the roadmap's Workstream 7 proposal (former option ii), not a
   D-B decision. What remains open is two-layered: *whether* to adopt the
   in-repo package split at all, and if so, its ordering against S16/S17's
   decoupling seam (former option i) — decouple first, then split into a
   `gigs/scout/`-style package; or split first and decouple inside it.
   Tradeoffs given in §5, no lean stated.
10. **Distribution name for Scout's in-repo package, if adopted (repo name
    deferred either way):** D-B defers the question of Scout's *own repo
    name* to whenever the later split actually happens — not needed now.
    The *distribution/package name* for a separately built Scout package
    (e.g. what `gigs/scout/`'s `pyproject.toml` would call itself, which is
    what a `gigai>=0.2,<0.3`-style pin and any entry-point registration
    would reference) only becomes a question **if** the roadmap's
    Workstream 7 in-repo package layout is adopted (§5, Open question 9) —
    it is not needed at all if gigs stay as plain modules under
    `src/gigai/scout/` until the later repo split.

## Change log

- 2026-09-23: Spike recorded from a coordinator handoff (v0.1.8 run
  `run_b12de8fdda28`, evidence originally gathered against `gigai-v0.1.8 @
  de9b0d6`). Every cited `file:line` was independently re-verified against
  this worktree (`gigai-v0.1.9 @ aa9d06a`); two citations were corrected
  (`private_records.py`'s workpad-layout tuple is at lines 134-137, not
  136-139; `pyproject.toml`'s console-script line is 31, not "30-31") and
  one small drift was flagged without correcting the source spike
  (`native_records.py`'s docstring cross-reference is at line 462 in this
  worktree vs. S16's cited 459). The handoff's F5 claim that Scout's UI
  directory contains `node_modules`/`dist` was checked and found false in
  this checkout — corrected in §1's table rather than repeated. Reconciled
  the handoff's open "36 vs 45" core→Scout coupling count exactly: 36 are
  `from .scout`-style import statements (re-verified, same 10 files S16
  named), the remaining 9 are string-literal validator-source constants and
  `exc.name` comparisons in `external_recording.py` (8) plus one docstring
  cross-reference in `native_records.py` (1). Test-file counts were
  recounted and found to differ slightly from the handoff's figures (161
  test files under `tests/`, not 159; 51 reference `gigai.scout`, not 54) —
  not material to any claim in this ticket, so not chased further. Recorded
  the four operator decisions verbatim-in-intent; drew the gigai/gig line
  per capability with required/optional/alternative/unverified labels;
  traced the F1 data-authority reversal including the shared-`state.sqlite`
  G22-interview-trace constraint; gave execution alternatives (A)/(B) with
  a lean toward (A) and its cost; laid out packaging/repo mechanics
  including a corrected schema-name-grep caveat; gave three sequencing
  options with tradeoffs and no lean; stated the PII-by-default chokepoint
  and its unenforceability in a library model; stated S21's relationship to
  S16/S17/S20 without editing them; collected ten open questions.
- 2026-09-23 (r1): reworked per coordinator content review
  (`.orchestrator/reviews/s21-r0.md`). Traced every §1 row the r0 draft had
  marked "unverified": diagnostics resolved to `cli.py:1916`
  (`doctor`) and `:843` (`setup`), both already top-level commands;
  credentials/config resolved to `src/gigai/credentials.py` (82 lines) and
  `src/gigai/config.py` (891 lines), both already provider-agnostic (no
  Scout import in either file, re-verified by grep); adapters resolved to
  `src/gigai/scout/tool_adapter.py` (70 lines); prompts resolved to
  `src/gigai/scout/data/goalgraphs/*.md`, spot-checked as actual
  instruction prose, not just a filename guess. Stale/crashed-run
  reconciliation was traced in full via `grep -n
  "recover|abandoned|stale|heartbeat|reconcile" run.py` (25 matches,
  EXECUTED): found two narrow, already-existing recovery mechanisms
  (`_acquire_provider_review_lease`'s `flock`-based local liveness check at
  `run.py:1778`, and `_recover_abandoned_provider_review`/
  `_recover_proposal_run_terminal` at `run.py:1804`/`:4580`), and confirmed
  no heartbeat/timeout/cross-process liveness mechanism exists anywhere in
  the file — both recovery functions assume gigai's own process held the
  lock, which does not generalize to a gig's out-of-process run under
  decision 1. Rewrote §3's lean to rest explicitly on
  sealing/scheduling/budget/effects machinery, not on "staleness-detection
  machinery" (which r0 had claimed existed, contradicting §1); stated
  plainly that stale-run detection is new work under either execution
  option, not something (A) already solves. Replaced the unresolvable
  `[[gigai_v018_scout_first_020_gigs]]` memory link in §5 with
  `.orchestrator/decisions.log:12`, an in-repo citation recording the same
  "decoupling core from scout = 0.2.0 item #1" scoping. Re-verified (rather
  than deferred to S20 alone) that `pyproject.toml` has no
  `[project.entry-points]` section, citing the grep directly in §4. No
  operator decision or open question's text needed to change — the new
  evidence made Open question 1's premise more precisely true (a
  mechanism gap, not just an unstated preference) rather than changing
  what it asks.
- 2026-09-23 (r2): folded in operator decision D-B ("monorepo for now;
  split into repos at 4-5 gigs with clear separation"), which amends
  decision 1's *timing* — r1 was accepted as-is and not otherwise
  redone. Decision 1's original wording was kept verbatim; D-B was added
  directly under it as an explicit amendment, not a replacement. Reworded
  Status and Problem to state that separate repos are now the later state
  (at 4-5 gigs with clear separation) and the near-term direction is a
  monorepo with gigs as separately built packages inside it. In §4
  (Packaging and repos), added a framing note that every "own repo"/
  "cross-repo" mechanic described is the later state, and that the
  near-term direction is §5 option (ii) — a `gigs/scout/`-style in-repo
  package with its own `pyproject.toml` — proving the same packaging
  mechanics without a second repo; reworded the console-script,
  validator-registration, example-gig, and CI-pinning bullets to name both
  horizons instead of assuming an imminent repo split. Rewrote §5
  (Sequencing options) directly from D-B: struck through and marked former
  option (iii) "extract now" as ruled out; renamed former option (ii) from
  an "interim" step to the monorepo-period target; kept former option (i)
  as one of two remaining orderings; stated explicitly that (i) vs (ii)
  ordering is still open (this ticket does not pick one) and that D-B
  settles *where* gigs live during the monorepo period, not *when* to
  build the package split relative to decoupling. Adjusted §3's execution-
  ownership option (A) and §7's S16/S17/S20 relationship bullets so they no
  longer describe a cross-repo Scout as the near-term case, stating instead
  that execution ownership and the S16/S17/S20 contracts apply the same way
  whether Scout is a monorepo package now or a separate repo later.
  Rewrote Open questions 9 and 10 to match D-B: Q9 now asks only about (i)
  vs (ii) in-repo ordering, since D-B already resolved the former (iii)
  alternative; Q10 now separates "Scout's own repo name" (deferred to
  whenever the later split happens, not an open question now) from "Scout's
  in-repo distribution/package name" (which may still be needed sooner, for
  §4's entry-point/CI work). Numbering kept stable (still Q9/Q10; no
  question was removed or renumbered). Made a minor wording adjustment to
  Q6 (UI scope) to say "own package/repo" rather than assuming a repo
  specifically. Ran `grep -n "own repo"` after all edits and listed every
  remaining hit in the worker file with why each one stays (the file's
  title, decision 1's original verbatim text and its own D-B amendment,
  the §4 framing note itself, and three D-B-qualified "eventually"/
  "later state" mentions in §4's console-script/validator/example-gig
  bullets and §5's struck-through option 3) — none asserts Scout moves to
  its own repo now.
- 2026-09-23 (r3): corrected an over-attribution introduced in r2. D-B's
  operator text is only "monorepo for now; split into repos at 4-5 gigs
  with clear separation" — it rules out extracting a gig to its own repo
  before then, and says nothing about how gigs are laid out while they stay
  in this repo. r2 had stated, in several places (the Status line, the
  "Consequence" paragraph, §4's framing note, the validator and CI-pinning
  bullets in §4, §5's intro/option-2/"Open (not decided)" paragraph, §7's
  S20 bullet, and Open questions 9-10), that D-B itself settles gigs as
  "separately built packages" inside the monorepo. That in-repo package
  layout (`gigs/<name>/`, each independently publishable) is the roadmap's
  proposed Workstream 7
  (`docs/development/v0.2.0/roadmaps/v0.2.0-gig-workbench-roadmap.md`), not
  something D-B said — a coordinator-spec over-attribution in r2, not an
  error in the operator's own words. Reworded every site above to say: D-B
  rules out the former option (iii) "extract now" and keeps gigs in this
  repo; whether they become separately built in-repo packages (former
  option (ii)) is the roadmap's Workstream 7 proposal, not decided; and if
  (ii) is adopted, its ordering against S16/S17's decoupling seam (former
  option (i)) is also open. §5's option (iii) remains struck through and
  explicitly ruled out by D-B, unchanged. Q9 now asks both "whether" and
  "when" for the in-repo package split, not just "when." Q10 now states the
  in-repo distribution name is only a live question if (ii) is adopted at
  all — it does not arise if gigs stay as plain modules under
  `src/gigai/scout/` until the later repo split. Ran `grep -n
  "monorepo-period target\|separately built"` after all edits (EXECUTED):
  every remaining hit either says "proposed"/"a proposal"/"if adopted"
  explicitly, or is inside the r2 Change log entry describing what r2 had
  said (left as historical record, not restated as current fact) — no hit
  attributes the in-repo package layout to D-B as decided.
