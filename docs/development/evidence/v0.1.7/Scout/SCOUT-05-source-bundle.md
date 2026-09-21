# SCOUT-05 — Bundled source slice

**Status:** Inert source implemented; initialization and release eligibility
remain open. This is not whole SCOUT-05 acceptance.

`src/gigai/scout_template.py` exposes the five accepted semantic selectors,
explicit authoring inputs/outputs, immutable source inventory, and a catalog
candidate using the existing package inventory/digest machinery. It does not
register Scout as a release-ready default, allocate Gig/Run identities, approve
anything or execute tools. The authoring index is explicitly not a runtime
Goal Graph schema.

Package resources under `src/gigai/data/scout/` include readable goal
instructions, README/changelog, and canonical `ui/template.html` plus
`ui/style.css`. The HTML is local-only, script-free and read-only; this source
slice does not implement report generation or CRUD. The template shows honest
empty states, not fabricated jobs or results. Editable source stays separate
from future `reports/scout/` generated bundles. No per-Gig `gig.py` placeholder
is shipped before its validated persistence/tool-binding implementation exists.

The instructions cover exact input revisions, progressive questions, source
status/compensation uncertainty, sponsorship distinctions, factual tailoring,
explicit application requests and reusable interview preparation. Codex/Claude
do semantic work; these source files do not claim GigAI independently verified
it. Semantic correctness still needs domain implementation and review.

Verification at this source checkpoint:

- Initial source tests: 14 passed, including canonical protocol ID roundtrips,
  five-selector routing, immutable inventory, local links, inert package
  inspection and UI source isolation. Two initial fixture setup mistakes
  (package directory identity and missing collision-check callback) were fixed
  before this passing run; no production acceptance rule was weakened.
- Native plus source tests: 20 passed in 21.71 seconds.
- Source/module tests Ruff passed; compilation passed. Ruff is installed as
  `ruff` on PATH, not `.venv/bin/ruff` (the first attempted path was absent).
- The development-install schema resource verifier reports 55 matching files.
  This is not isolated-wheel or final integrated-schema acceptance.
- Existing G45 `record create/read` and native CRUD have separate additive
  mounts (`record native`) to avoid a Click group-name collision. A further
  CLI mount regression case was added after the 20-test run; the resulting
  15-case source suite passed in 0.16 seconds. Direct installed development
  CLI help for `record native` and `external` works. `git diff --check` passes.

Remaining: Graph Set compilation against the external lane's concrete
contracts, username-aware recoverable default provisioning, approved source
inventory/tool binding, real domain outputs and UI projection/portability,
fresh package/installed workflow verification. All remain in the roadmap.
