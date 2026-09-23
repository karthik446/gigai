# v020-stable-core worker report

Task: create `docs/development/v0.2.0/` as a discussion space (not a plan,
not a decision) for what it takes to make GigAI's core a stable,
library-like foundation, per the operator's 2026-09-23 verbatim intent
(quoted in the new README). Not a v0.1.8 blocker.

**READ:** `docs/development/v0.1.9/README.md`, `docs/development/v0.1.9/spikes/README.md`,
`docs/development/v0.1.9/spikes/S16-core-gig-decoupling.md`,
`docs/development/v0.1.9/spikes/S17-gig-module-structure-classes.md`,
`docs/development/v0.1.9/spikes/S19-test-suite-diet.md`,
`docs/development/v0.1.8/spikes/README.md` (ticket-format convention),
`docs/development/v0.1.8/spikes/S14-schema-inventory-and-consolidation-audit.md`,
`.orchestrator/runs/v0.1.8/workers/ci-speed.md`, `CHANGELOG.md`,
`src/gigai/cli.py` (CLI JSON-error convention, `_raise_cli_error` at
`cli.py:169-182`), `pyproject.toml`.

**EXECUTED (read-only):** `wc -l` per-module LOC counts across
`src/gigai/*.py` (core, 46,264 LOC / 62 files) and `src/gigai/scout/*.py`
(20,384 LOC); `find`/`grep` checks for `py.typed` (absent), mypy/pyright
config (absent), `[project.entry-points]` in `pyproject.toml` (absent),
`*_v2`/`*_v3` module files (exactly one: `scout/research_v3.py`), and
`DeprecationWarning`/`@deprecated` usage (none); `ls src/gigai/schemas/ |
wc -l` (86, vs. S14's recorded 85 — flagged as an unreconciled 1-file
discrepancy, not silently overwritten); `git cat-file -t v0.1.6 v0.1.7`
(both `tag`, confirming annotated tags are already practiced). No file
outside this task's owned set was modified. No `git add`/commit.

## Files written (all new; nothing else touched)

- `docs/development/v0.2.0/README.md` — purpose, the operator's versioning
  intent verbatim, the gig architecture rule copied verbatim from
  `docs/development/v0.1.9/README.md`, how v0.1.9's S16–S19 feed into
  0.2.0, and a pointer to S20's open questions.
- `docs/development/v0.2.0/spikes/README.md` — ticket-format convention
  (same shape as v0.1.8's, plus an explicit "Open questions" section) and
  the spike index.
- `docs/development/v0.2.0/spikes/S20-stable-core-as-a-library.md` (449
  lines; target was ~250–400, ran ~12% over after one trim pass — every
  section carries `file:line`/command-backed evidence and cutting further
  risked losing citations rather than saving meaningful space) —
  investigates: (1) today's public API surface, derived from Scout's actual
  core imports cross-referenced against S16's inventory, with a proposed
  `gigai.api`/`__all__` surface and what stays private; (2) stability
  contracts — confirmed *no* semver policy, deprecation mechanism, or
  written CLI-JSON-contract exists yet, though the CLI already has a real
  undocumented JSON-error shape (`cli.py:169-182`); on-disk format
  versioning per S14's 82/86-schema audit; the S16 `GigManifest` as the
  gig-facing contract; (3) a cleanup inventory (LOC per module, S17's
  byte-identical `research.py`/`research_v3.py` duplication, the single
  `*_v3` module file, S14's schema findings deferred rather than
  re-audited) ranked by risk; (4) library packaging gaps — no `py.typed`,
  no type checker config, no gig-author docs, no example gig, no entry
  points, no compatibility testing; (5) release cadence — cites the
  ci-speed worker's measured 1:22:38→target-≤30min work as already-done,
  confirms annotated tags and the two-file changelog split are already
  practiced; (6) a six-theme (not dated) proposed path from v0.1.9 to
  v0.2.0, plus six open questions for the operator.

## What's left

Everything in `docs/development/v0.2.0/` is discussion/research, not a
decision — the operator needs to answer S20's six open questions (what
"private" means mechanically; how strict a 0.2.x semver contract should be;
whether the CLI's JSON conventions freeze as-is or get revised once first;
whether compatibility testing needs a second real gig or the proposed
"hello gig" fixture suffices; which release gates a 0.2.x patch can skip
vs. a 0.2.0 minor; how sequential the six-theme path must run) before any
v0.2.0 implementation is scheduled. No code, schema, or test file was
changed by this task.
