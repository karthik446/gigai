# S11 Phase 1 groundwork receipt

Recorded 2026-09-21 for the operator-authorized Phase 1 / Groundwork packet.
This receipt covers test organization and bounded test-support work only. It
does not claim a v0.1.7 ship, an installed release, a provider/model run, or
acceptance of any later v0.1.8 phase.

## Scope and authorization

The original S11 brief was planning-only and asked to wait for v0.1.7. The
operator then explicitly authorized Luna to complete this bounded groundwork
now. The authorization is recorded narrowly: source inventory, deterministic
measurements, explicit lane markers, test-support measurement/inventory tools,
and one representative public-acquisition migration are in scope. Production
source, schemas/storage, UI/acquisition features, provider calls, model
execution/downloads, daemon startup, private resumes/workpads, releases,
commits and pushes remain out of scope.

The pre-existing ten modified v0.1.8 roadmap/brief/index documents were
preserved as the baseline; only S11 status/receipt references were updated.
The current checkout remains at `b01675d` (`Ship GigAI 0.1.7...`), which is
repository history, not a new ship claim.

## Inventory

The source-based inventory was generated without importing product code:

```text
PYTHONPATH=src /Users/kar/Developer/projects/gigai/.venv/bin/python \
  tools/s11_inventory.py \
  --output docs/development/v0.1.8/evidence/S11-test-inventory.json
```

It records 156 test files, 1,133 AST-level test items, and 25 installed
verifiers. Parametrized collection expands the broad source collection to
1,173 collected items before collection errors. Conservative source-level
levels below combine test-item and verifier records (an item may have more than
one level; the installed count includes the 25 verifier scripts):

| Level | Items | Meaning |
| --- | ---: | --- |
| Unit source category | 300 | deterministic behavior with no discovered local boundary seam; not a `fast_unit` runtime claim |
| Integration source category | 831 | filesystem, persistence, subprocess, CLI, concurrency or authority-boundary setup; not an `integration` runtime claim |
| CLI source category | 158 | public command/CLI wrapper coverage; not a `cli` runtime claim |
| Installed source category | 66 | installed/scenario/package boundary coverage; not an `installed` verifier selector |
| End-to-end source category | 1 | Debian/offline installed process verifier |
| Live/provider source category | 5 | explicit provider/local-model-sensitive source; no ordinary live selector |

The machine-readable report preserves each test node name, behavior grouping,
historical identifiers, setup dependencies, declared markers and selection
commands. Each record distinguishes `source_categories` (conservative source
inference) from `runtime_selectable_markers` (always empty for source-inferred
test entries); `classification_authority` explicitly says that source
classification is non-authoritative for runtime markers. Every test entry uses
only an explicit file path or nodeid command: path/node selection finds the
test but does not guarantee lane isolation, offline safety or runtime success.
The existing `tests/conftest.py` source hook remains the sole authority for
dynamic `fast_unit`, `integration` and `release` markers; the inventory emits
no per-entry `pytest -m` or inferred `pytest -k` selector. Live/provider
entries retain source caveats and explicit opt-in review requirements rather
than being made ordinary lane commands. Installed verifiers remain listed with
their real explicit commands (`.wheel-venv/bin/python ...` for wheel verifiers
and `python tools/verify_debian_offline.py` for the Debian offline verifier),
never as a fictitious pytest marker. It also lists every `tools/verify_*.py`
verifier and the workflow line(s) that select it. The registered `installed`
marker remains strict-marker metadata only for this inventory; installed
verifier selection is command-based. Eighty-four generic files remain
`unmapped/needs-feature-owner` rather than receiving an invented behavior
label; the packet does not broaden into a repository-wide taxonomy.

## Behavior tree and lanes

The bounded migration establishes this tree without changing production
runtime ownership:

```text
tests/
  behaviors/
    acquisition/
      test_public_import_state.py
      test_public_acquisition_lifecycle.py
```

The old goal-oriented files remain traceable through
[S11-acquisition-mapping.json](S11-acquisition-mapping.json). New behavior
tests should be placed under the protected behavior/component, while the
boundary remains visible in source categories, declared markers and explicit
path/node commands; source categories do not claim runtime lanes:

| Marker/selection | Boundary | Command pattern |
| --- | --- | --- |
| Unit source category + `scout_acquisition` declaration | deterministic acquisition state | `pytest tests/behaviors/acquisition/test_public_import_state.py` or an explicit `pytest tests/behaviors/acquisition/test_public_import_state.py::test_...` node; runtime lane remains authoritative in `tests/conftest.py` |
| Integration source category + `scout_acquisition` declaration | disposable workpad, persistence and path safety | `pytest tests/behaviors/acquisition/test_public_acquisition_lifecycle.py` or an explicit node; path selection does not guarantee integration isolation |
| CLI source category + `scout_acquisition` declaration | public Click/copy-wrapper command boundary | explicit file/node path selection; `cli` remains declared metadata, not an inventory lane claim |
| `installed` / verifier script | normally installed package and console script | `.wheel-venv/bin/python tools/verify_installed_g14.py` (explicit) |
| `g30_live` declaration | provider/local-model UAT only | explicit file/node review with operator opt-in; no ordinary inventory command or inferred live selector |

`pyproject.toml` now declares `cli`, `installed` and `scout_acquisition` under
strict markers. Existing dynamic marker logic was not weakened. Inventory
commands do not themselves invoke Codex, Claude, Ollama, a provider, private
workpads or host personal configuration; direct path selection is intentionally
not a proof of offline safety or runtime success.

The residual Terra correction regenerated inventory schema `s11-inventory.v2`
with the command above and was checked by one bounded read-only probe against
the generated JSON and the authoritative `tests/conftest.py` classifier: all
156 source records and 1,133 source nodes carry
`classification_authority: non-authoritative-for-runtime-markers`, have empty
`runtime_selectable_markers`, and emit zero `pytest -m` or inferred `pytest -k`
commands. The Terra counterexample remains visible as source `integration`
while the actual hook classifies all 13 nodes as `release`; no inventory lane
claim is made for either result. The 25 installed-verifier command selections
remain explicit and live/provider entries retain an explicit-opt-in caveat.
No test collection, test body, provider/model call or dependency installation
was performed for this correction.

## Measured baseline and receipts

Before execution, the selected source and fixtures were inspected. The unit
pilot imports only `gigai.scout_discovery_job` and uses in-memory deterministic
rows/clocks. The lifecycle file deliberately uses `tmp_path`, disposable
workpads, symlink fixtures, `CliRunner` and a fixed-argv subprocess, so it is
classified as integration/CLI rather than inferred to be a unit test; it does
not call a network, model, provider or personal configuration. Runs set
`GIGAI_G30_UAT=0` and `PYTHONPATH=src`; no live/provider selection was used.

The measurement runner records collection duration, session duration,
setup/call/teardown timing for every collected test, outcome, collection
errors and pytest exit code. It does not alter pytest fixtures or assertions.

| Probe | Command/selection | Result |
| --- | --- | --- |
| Broad collection | `tools/s11_measure.py -- --collect-only -q` | 1,173 collected; 41 collection errors; exit 2; collection 3.795032s; session 3.820672s |
| Broad collection after move | same selection after migration | 1,173 collected; 41 collection errors; exit 2; collection 3.663596s; same `questionary` error set |
| Broad error semantics | same | all 41 errors are the existing `ModuleNotFoundError: questionary` while importing `gigai.cli`; no test body ran |
| Before migration pilot | `tests/test_scout_discovery_job.py` | 4 collected/passed; collection 0.056906s; session 0.058024s; exit 0 |
| After migration pilot | `tests/behaviors/acquisition/test_public_import_state.py` | 4 collected/passed; collection 0.061023s; session 0.062280s; exit 0 |
| Historical unit marker pilot (pre-correction receipt) | `-m 'fast_unit and scout_acquisition'` on new state file | 4 passed; exit 0; retained as prior evidence, no longer emitted by the inventory |
| Full acquisition pair before (historical shared-interpreter baseline) | old two-file selection | 4 collected plus one collection error; exit 2 (`questionary`) |
| Full acquisition pair after (historical shared-interpreter baseline) | new two-file selection | 4 collected plus one collection error; exit 2 (`questionary`); same blocker, not masked |
| Focused lifecycle after project-local sync | `GIGAI_G30_UAT=0 PYTHONPATH=src uv run python tools/s11_measure.py --output docs/development/v0.1.8/evidence/S11-lifecycle-uv.json -- tests/behaviors/acquisition/test_public_acquisition_lifecycle.py -q` | 13 collected/passed; collection 0.130558s; session 13.927316s; exit 0 |

The exact receipts are [broad collection before](S11-baseline-collection.json),
[broad collection after](S11-collection-after.json),
[before pilot](S11-baseline-pilot-before.json), [after pilot](S11-baseline-pilot-after.json),
[unit marker pilot](S11-unit-marker-pilot.json), and the before/after blocked
pair receipts ([before](S11-baseline-pilot-before-full.json),
[after](S11-pilot-after-full.json)). The new project-local execution is recorded
in [S11-lifecycle-uv.json](S11-lifecycle-uv.json), with interpreter, dependency
sync and environment identity in [S11-lifecycle-uv-environment.json](S11-lifecycle-uv-environment.json).
The measurement JSON includes all 13 collected nodeids and per-test
setup/call/teardown durations; every outcome was `passed`, with no skips,
failures or collection errors. The older `questionary` rows remain truthful
historical measurements of the shared interpreter and are not the current
focused-lifecycle result. Wider test execution, all installed verifiers and
live/provider lanes remain unmeasured rather than inferred from this receipt.

## Implemented migration and identity proof

The cohesive public-acquisition slice moved ten cases:

- `test_scout_acquisition_progress.py` →
  `behaviors/acquisition/test_public_acquisition_lifecycle.py` (six
  integration/CLI cases; historical SCOUT-R4).
- `test_scout_discovery_job.py` →
  `behaviors/acquisition/test_public_import_state.py` (four deterministic unit
  cases; historical SCOUT-07).

All original positive, negative, deadline, duplicate/failure/exclusion,
private-field, immutable-input, symlink/path-safety, CLI and copied-wrapper
assertions remain. The only fixture correction was making the copied wrapper
resolve `src/gigai/data/scout/gig.py` from the repository root after its move;
the protected subprocess boundary remains real. The explicit old→new nodeid
mapping and case semantics are in [S11-acquisition-mapping.json](S11-acquisition-mapping.json).

The comparable pilot proves identity by exact function-name/node count and
outcome: four cases collected and passed before and after. The historical
before/after full-pair receipts show the same shared-interpreter collection
blocker, with exit code 2 and one `questionary` collection error; they were not
deleted, skipped or rewritten to appear green. After the project-local locked
sync, the lifecycle module's six functions expanded to 13 collected
parameterized cases and all 13 passed in the focused receipt above.

An AST comparison against the original `HEAD` blobs also found 6→6 and 4→4
test-function sets for the two moved files; the command and result are retained
in the worker receipt and the explicit mapping JSON above.

## CI and installed-verifier impact

- No `.github` workflow or verifier directly selected either old test path;
  the broad `pytest` workflow continues to discover the new tree through the
  existing `testpaths` setting.
- The installed verifier set and workflow selection remain unchanged. The
  inventory names all 25 scripts, including `verify_installed_g14.py` and
  `verify_installed_g28.py`, but no installed verifier was run because this
  checkout has no `.wheel-venv` and dependency installation was not authorized.
- Inventory test selectors now use explicit file/node paths only; these locate
  the test but do not claim lane isolation, offline safety or runtime success.
- Installed-verifier selections point at the real wheel-verifier command
  (`.wheel-venv/bin/python tools/verify_installed_*.py`) or the real Debian
  verifier command; no CI marker or runtime hook was changed to make a
  fictitious `installed` selector work.
- Historical v0.1.7 evidence documents retain old path references as history;
  the S11 mapping is the current discoverability bridge.

## Gate disposition and residuals

**Bounded Phase 1 gate: met for the authorized deterministic groundwork and
focused lifecycle execution, with broader-lane limitations.** Inventory, lane
definition, measured collection/pilot receipts, strict markers, historical
mapping, cohesive behavior migration and the project-local 13-case lifecycle
receipt are complete. Terra independently reviewed the migration/selector
correction and exact execution receipt under task `task_6795b28e61ad` /
dispatch `ctx_b6f405f97b53`, confirming 13/13, exit 0, no skips/errors and exact
identities in the local environment. The gate is not a release, installed,
broad-suite or live/provider acceptance claim.

Residuals are exact and out of scope for this packet:

1. The reused `/Users/kar/Developer/projects/gigai/.venv` still has no
   `questionary`; that historical interpreter blocks the integration/CLI pilot
   and 41 broad collection items. The project-local `.venv` was prepared with
   locked declared extras and the focused lifecycle now passes; the shared
   environment was not changed.
2. Installed-package receipts are unmeasured because no `.wheel-venv` exists in
   this checkout; run them only through an explicitly prepared installed lane.
3. Live/provider/model execution was not attempted and remains opt-in.
4. Source categories remain a behavior-discovery aid, not runtime lane proof;
   runtime marker claims must come from `tests/conftest.py` or observed pytest
   collection in an authorized environment.
5. The v0.1.7 ship prerequisite, S10 caller audit, interface freeze, scheduled
   vertical proof and later feature packets remain planned roadmap work; Terra's
   bounded S11 review is complete and does not widen those gates.

Next incremental step: use the planned [Phase 2 audit/interface-freeze
ticket index](../phase-2/tickets/README.md), while retaining this focused
receipt as bounded evidence; do not broaden to a repository-wide run or fix
unrelated runtime defects.
