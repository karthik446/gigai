# Worker report — v020-roadmap-workbench

**Task:** write a PROPOSED, NOT ACCEPTED v0.2.0 roadmap (gigai as a
personal gig workbench), per
`.orchestrator/workers/specs/v020-roadmap-workbench.brief.txt`.

**Status:** Done. Docs only. No code, schema, or test edits. No
`git add`/commit/stash/reset/clean. No live/provider runs. No secret
values read or printed.

## Files written

- `docs/development/v0.2.0/roadmaps/v0.2.0-gig-workbench-roadmap.md` (new)
  — the roadmap. First line after the title is
  `**Status:** Proposed — not accepted.`
- `docs/development/v0.2.0/README.md` (edited) — added a "Roadmap
  (proposed, not accepted)" section linking to the new roadmap, and
  softened the first non-claims bullet ("No v0.2.0 roadmap... authorized")
  to acknowledge the new proposed-not-accepted document instead of
  contradicting its own existence.
- `.orchestrator/workers/v020-roadmap-workbench.md` (this file).

No other file was touched. `git status --short` at finish shows only
these two under my ownership, plus the parallel S21 worker's changes to
`docs/development/v0.2.0/spikes/` and
`.orchestrator/workers/v020-s21-gig-repos.md` (not mine).

## What's in the roadmap

- Operator decisions D-A..D-G recorded as given, not reopened. D-B
  (monorepo for now) is cited from S21's own already-amended text
  (`S21-gig-repos-and-data-ownership.md:44-50`, READ) — the parallel S21
  worker had already folded D-B into the spike by the time I read it, so I
  only cite it; I did not edit `docs/development/v0.2.0/spikes/`.
- Workstreams 0-7 plus "Slimmer S20," each with goal/size/dependencies/an
  observable done-definition (a command an operator could run)/which
  spike(s) it draws on/an explicit "Pull into 0.1.9?" line with a reason.
  Workstream 0 (secrets) gets a concrete proposed packet breakdown (P0-P4:
  known-service map, secrets CLI, lookup-order resolver, Exa through the
  resolver, gig-declared-secrets preflight) with files and tests, still
  labeled proposed.
- A dependency-order table and small ASCII DAG (0 early; 1→2→3; 4/5/6
  parallel after 1; 7 last; Slimmer S20 independent).
- An "Open questions" section with the five items from the brief (cost
  units vs. dollars; one hub app vs. many gig UIs; workpad migration;
  ceremony visibility; S21's execution-ownership open question).
- A "Relationship to spikes" section stating explicitly that this roadmap
  sequences S16/S17/S19/S20/S21 and does not replace them.

## Evidence re-verification (Workstream 0)

Re-checked every citation the brief gave, in this worktree, with `sed -n`
and targeted `grep`/`stat`/`cut`:

- `setup.py:47-48` (home root) and `:199` (creates `home_root/credentials`,
  unread/unwritten elsewhere) — **confirmed exact**, brief's line numbers
  correct.
- `private_transfer.py:102` (`.env` excluded from transfers) —
  **confirmed exact**.
- `credentials.py:55-72` in the brief — corrected to **`:55-73`** for the
  full `resolve_reference_value` function; the non-`"environment"` reject
  is at `:63-67`, the `os.environ.get` call at `:68` (brief's `:55-72`
  span was close but imprecise about which lines do what; substance
  confirmed).
- `credentials.py:12` in the brief (keychain/op/vault regex) — corrected
  to **`:11`** (`REFERENCE_NAME`); one line off, substance confirmed.
- `exa_client.py:38,66-73` — **confirmed exact**, matches the brief.
- Runbook `M1-find-jobs.md:151,162` — **confirmed exact**.
- `~/.gigai/.env`: EXECUTED `stat -f "mode=%Lp size=%z" ~/.gigai/.env`
  (mode 600) and `cut -d= -f1 ~/.gigai/.env` (variable names only:
  `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`,
  `CLAUDE_CODE_OAUTH_TOKEN`, `TAVILY_API_KEY`, `EXA_API_KEY`,
  `EXA_SECRET_KEY`, plus header comments). No value read or printed.
- One correction beyond line numbers: the brief's "36 grep lines / 45 S16
  AST sites" for the core→Scout import count doesn't match either S16's
  own figures or a fresh grep in this worktree. S16 itself records 45
  strict-pass **rows** across 10 files and 34 distinct strict-pass
  **sites**, plus 118 broad-pass string-mention rows
  (`S16-core-gig-decoupling.md:565-566`, READ) — I cited S16's own numbers
  in Workstream 1 instead of carrying the brief's mismatched figure
  forward, and said so in the roadmap's evidence note and change log.

## READ vs EXECUTED

READ: all source files cited (`setup.py`, `private_transfer.py`,
`credentials.py`, `exa_client.py`, `run.py`, `bindings.py` reference),
the M1 runbook, S16/S17/S19/S20/S21 spike files, the v0.1.8 roadmap, the
v0.2.0 README, `AGENTS.md`/orchestrator skill context.

EXECUTED (all read-only, no writes, no secret values emitted): `sed -n`
spot-checks of every cited line range (re-run after drafting, to verify
the final doc's citations); `grep -n`/`grep -c` for import-coupling counts
and credential-directory usage; `find` for file locations; `stat -f` on
`~/.gigai/.env` for mode only; `cut -d= -f1` on `~/.gigai/.env` for
variable names only.

## What's left (r0)

Nothing outstanding on this task. Open items are recorded in the
roadmap's "Open questions" section for the operator, not resolved here.
The roadmap itself remains proposed, not accepted — no pull-into-0.1.9
decision was made.

---

## r1 — rework per coordinator review

**Task:** fix items 1-5 from `.orchestrator/reviews/roadmap-workbench-r0.md`
in the roadmap. Status: done. Docs only; no code/schema/test edits, no
`git add`/commit/stash/reset/clean, no live/provider runs, no secret
values read.

### Item 1 — wrong import count (MAJOR)

The r0 draft's "corrected" count was itself wrong: it replaced the
brief's correct "36 grep lines / 45 S16 AST sites" with S16's raw
internal figures, after a mis-patterned grep (`gigai\.scout|from \.scout
import` misses `from .scout.<submodule> import`, which is most of
Scout's actual import style — e.g. `from .scout.find_jobs.contracts
import (...)`).

Re-ran with the coordinator's corrected pattern. EXECUTED:
`grep -c 'from \.scout\|from gigai\.scout' src/gigai/*.py` (filtered to
non-zero matches), then summed with `awk -F: '{s+=$2} END {print s}'`.
Result: **36** matching lines across exactly **10 files** — the same
10-file set S16 named:

| File | Count |
| --- | --- |
| `application_events.py` | 1 |
| `capability_review.py` | 1 |
| `capability_successor.py` | 1 |
| `cli.py` | 5 |
| `default_init.py` | 1 |
| `external_recording.py` | 10 |
| `native_records.py` | 1 |
| `portability.py` | 1 |
| `private_transfer.py` | 1 |
| `run.py` | 14 |
| **Sum** | **36** |

READ S21 §7 "Relationship to S16, S17, S20"
(`S21-gig-repos-and-data-ownership.md`, section heading cited, not line
number — S21 is being revised in parallel and its line numbers move
under me; confirmed this happened mid-task, see below). S21 §7 already
reconciles the count: the remaining 9 of S16's 45 strict-pass rows are
non-`from`-import string-literal/docstring hits a plain import grep
can't catch (8 in `external_recording.py`, 1 docstring cross-reference in
`native_records.py`). **36 + 9 = 45.** S16's separate "34 distinct
strict-pass sites" figure is S16's own internal inconsistency (flagged by
S21 §7, not cited here as a count) — the roadmap now states this
explicitly instead of citing "34 distinct" as if it were an agreed
number. Rewrote Workstream 1's "Draws on" paragraph and its "Done"
grep pattern (now the corrected `from \.scout\|from gigai\.scout`
pattern) to match.

### Item 2 — wrong v0.1.9-scope premise (MAJOR)

READ `.orchestrator/status.md` "Goal" section and "Coordinator" section
(the v0.1.8-UAT-parallel note); READ `.orchestrator/decisions.log:14`
(10:40, S16/S17 deferred until find-jobs works live) and `:24` (15:20,
"fix the workflow and tests first, as I do UAT" — wave 1 scope: git
workflow, release pipeline, CI docs-only skip/job renames, test lanes;
S16/S17/S18 wait; UAT fix requests jump the queue).

Every "Pull into 0.1.9?" line in the r0 draft said v0.1.9's scope was
"spike research (S16 itself, plus S17/S19/S20/S21)" — wrong. v0.1.9's
actual scope is wave 1 (now done and merged) plus UAT fix requests from
the parallel v0.1.8 coordinator's live operator testing; S16/S17
implementation is separately and explicitly deferred until find-jobs
works live, and the spikes themselves are already-written research docs,
not in-progress work v0.1.9 is "doing." Rewrote every Workstream's "Pull
into 0.1.9?" line (0, 1, 2, 3, 4, 5, Slimmer S20 — Workstreams 6 and 7
already gave scope-independent reasons and needed no premise fix) on the
corrected basis. Workstream 0 now states its real tradeoff: it removes
the manual `export EXA_API_KEY` step the operator hits *during the same
UAT* (runbook `M1-find-jobs.md:151,162`), against the risk of adding a
new implementation packet — touching `credentials.py`'s shared resolution
path — while UAT is actively running. Slimmer S20 now notes its trim is
documentation-only against an already-finished spike (S20's own status
line: "Research recorded 2026-09-23"), so it's low-risk but still not a
UAT fix request.

### Item 3 — diagram/table disagreement (minor)

The ASCII DAG drew Workstreams 4/5/6 branching from under 3; the
dependency table said each depends only on 1. Redrew the diagram so 4, 5,
6 branch directly from 1, in parallel with the 2→3 chain, and added a
one-line note stating explicitly that 4/5/6 do not wait for 3 and that 7
is the one item needing all of 1, 2, and 3.

### Item 4 — W0 "done" test selector (minor)

Replaced the directory-wide `pytest tests/behaviors/secrets/ -q` with the
three exact packet test files from the packet table:
`test_secrets_catalog.py`, `test_secrets_cli.py`,
`test_secrets_resolution_order.py` (all still proposed/not-yet-created
paths, per the operator's test-budget rule of naming exact files rather
than a directory glob).

### Item 5 — S21 line-number citations (minor)

Replaced every S21 citation that used a line number
(`S21-gig-repos-and-data-ownership.md:44-50`, `:133`, `:224`, `:297`)
with a section-heading citation (§1 "Where the gigai/gig line falls
today", §3 "Execution ownership", §4 "Packaging and repos", §7
"Relationship to S16, S17, S20", and "Operator decisions (recorded, not
re-opened)"). Confirmed this was the right call, not overcaution: while
this task was in progress, S21 §7's heading moved from line 477 (as read
during the earlier r0 pass) to line 489 (re-checked at the end of this
r1 pass) because the parallel S21 worker is actively editing the file —
a line-number citation written mid-task would already have gone stale by
worker_done.

### Verification

- EXECUTED `sed -n` spot-checks on every new/changed citation: S16
  `:565-566`, S20 `:5`, `.orchestrator/status.md` "Goal"/"Coordinator",
  `.orchestrator/decisions.log:14,24`, and every S21 section heading (§1,
  §3, §4, §7, "Operator decisions") — all resolve.
- EXECUTED `grep` checks confirming no "only 9" / stray wrong-count phrase
  remains as a live claim, no "v0.1.9's scope is spike research" premise
  remains, and no directory-wide `tests/behaviors/secrets/ -q` selector
  remains outside the change-log's own description of what was replaced.
- EXECUTED `git status --short`: only `docs/development/v0.2.0/roadmaps/`
  and this worker file are mine; `.orchestrator/decisions.log`,
  `.orchestrator/status.md`, `docs/development/v0.2.0/spikes/`, and other
  `.orchestrator/workers/`/`reviews/`/`specs/` entries belong to the
  coordinator or the parallel S21 worker. Did not touch
  `docs/development/v0.2.0/spikes/`.

### What's left

Nothing outstanding. All five review items applied; everything the
review said "holds up" (Status line, D-A..D-G, D-B citation, Workstream 0
evidence, `.env` handling, packet breakdown, the five open questions) was
left as-is. Roadmap remains proposed, not accepted.

---

## r2 — factual fix: wave 1 not merged

Replaced all three "done and merged" claims (Workstream 0, Workstream 1,
r1 change-log entry) with "done, in draft PR #37 (not merged:
https://github.com/karthik446/gigai/pull/37)"; `grep -n merged` now
shows only correctly-phrased "not merged" text; added one r2 Change log
line. Docs only, no git add/commit. Nothing left outstanding.
