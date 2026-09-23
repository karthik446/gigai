# Scout package reorg — worker report

Operator decision 2026-09-23: Scout is its own gig; core should not (yet) stop
importing it, but Scout's code/UI/data now live inside `src/gigai/scout/`.
This is a **physical move + import updates only**. No behavior changes beyond
paths/names. Executed with plain `mv`/`Write`/`Edit` — no `git add/mv/commit`.

## Old → new path mapping (full)

### `src/gigai/scout_<name>.py` → `src/gigai/scout/<name>.py`

| old | new |
|---|---|
| `scout_acquisition_cli.py` | `scout/acquisition_cli.py` |
| `scout_acquisition_records.py` | `scout/acquisition_records.py` |
| `scout_answer_cli.py` | `scout/answer_cli.py` |
| `scout_bundled_tools.py` | `scout/bundled_tools.py` |
| `scout_checks.py` | `scout/checks.py` |
| `scout_discovery_job.py` | `scout/discovery_job.py` |
| `scout_discovery.py` | `scout/discovery.py` |
| `scout_document_records.py` | `scout/document_records.py` |
| `scout_documents_cli.py` | `scout/documents_cli.py` |
| `scout_documents.py` | `scout/documents.py` |
| `scout_inputs.py` | `scout/inputs.py` |
| `scout_interview_cli.py` | `scout/interview_cli.py` |
| `scout_interview_records.py` | `scout/interview_records.py` |
| `scout_interview.py` | `scout/interview.py` |
| `scout_materialization.py` | `scout/materialization.py` |
| `scout_posting_inputs.py` | `scout/posting_inputs.py` |
| `scout_projection.py` | `scout/projection.py` |
| `scout_proposal_cli.py` | `scout/proposal_cli.py` |
| `scout_proposal_execution.py` | `scout/proposal_execution.py` |
| `scout_proposal_records.py` | `scout/proposal_records.py` |
| `scout_proposals.py` | `scout/proposals.py` |
| `scout_report_cli.py` | `scout/report_cli.py` |
| `scout_report_readers.py` | `scout/report_readers.py` |
| `scout_report.py` | `scout/report.py` |
| `scout_research_inputs.py` | `scout/research_inputs.py` |
| `scout_research_v3.py` | `scout/research_v3.py` |
| `scout_research.py` | `scout/research.py` |
| `scout_tailor_cli.py` | `scout/tailor_cli.py` |
| `scout_tailor_execution.py` | `scout/tailor_execution.py` |
| `scout_tailor_selection.py` | `scout/tailor_selection.py` |
| `scout_tailoring.py` | `scout/tailoring.py` |
| `scout_template.py` | `scout/template.py` |
| `scout_tool_adapter.py` | `scout/tool_adapter.py` |
| `scout_tools.py` | `scout/tools.py` |

### find-jobs subpackage → `src/gigai/scout/find_jobs/`

| old | new |
|---|---|
| `scout_find_jobs_contracts.py` | `scout/find_jobs/contracts.py` |
| `scout_find_jobs_bindings.py` | `scout/find_jobs/bindings.py` |
| `scout_exa_client.py` | `scout/find_jobs/exa_client.py` |
| `scout_ats_board_clients.py` | `scout/find_jobs/ats_board_clients.py` |
| `scout_market_acquisition.py` | `scout/find_jobs/market_acquisition.py` |
| `scout_watchlist.py` | `scout/find_jobs/watchlist.py` |
| `scout_present_api.py` | `scout/find_jobs/present_api.py` |

New `__init__.py` files added at `src/gigai/scout/__init__.py` and
`src/gigai/scout/find_jobs/__init__.py` (both empty; `setuptools.packages.find`
auto-discovers them from `src`, no `pyproject.toml` include-list needed).

No naming collisions/renames beyond the task's own find-jobs list, **except
one same-name ambiguity that caused real bugs during the move** (see
"Non-mechanical decisions" below): `scout_discovery.py` → `scout/discovery.py`
collides in *name* (not path) with the pre-existing, unrelated root module
`gigai/discovery.py`. Both are legitimate, unrelated modules; no rename was
needed once the collision was understood, but it required manual repair of a
script-driven mis-rewrite (below).

### UI: Vite app `ui/` → `src/gigai/scout/ui/`

Moved everything except `node_modules/` and `dist/` (`index.html`,
`package.json`, `vite.config.js`, `yarn.lock`, `src/`, `.gitignore`). Old root
`ui/` (including `node_modules/` and `dist/`, both gitignored/untracked) was
then deleted entirely. Updated one stale doc-comment path in
`src/gigai/scout/ui/src/api.js` (`gigai.scout_find_jobs_contracts` →
`gigai/scout/find_jobs/contracts.py`).

**`src/gigai/data/scout/ui/` (old static v0.1.7 assets) is NOT this Vite app.**
It is `style.css` + `template.html`: a static HTML report template that
`scout/report.py` and `scout/template.py` use to render Scout's markdown
tracking report as a standalone HTML file (via `_source_paths()` /
`scout_source_files()`), unrelated to the Vite find-jobs control UI. It moved
along with the rest of `data/scout/` (see next section) to
`src/gigai/scout/data/ui/`, staying a sibling — not a child — of the new
`src/gigai/scout/ui/` Vite app. No collision.

### Data: `src/gigai/data/scout/` → `src/gigai/scout/data/`

Checked first per instructions: all resource-path strings
(`"data/scout/tools/cap_.../*.schema.json"`, `"data/scout/tools/.../*.py"`,
`gigai.data.scout.tools.cap_...` dynamic import strings) are used only in
plain path/module-name equality checks and `importlib.resources` /
`importlib.import_module` lookups — never baked into a `canonical_json_bytes`
hash, sealed/pinned digest, or schema-validated manifest as anything other
than a literal path string. Confirmed via `grep` for `canonical_json_bytes`/
`digest`/`sha256` near every `SCHEMA_RESOURCE` constant: digests are computed
over domain *payloads* (packets, graphs, contracts), never over the resource
path string itself. One hardcoded exact-value test assertion existed
(`test_scout06_research_bridge.py::test_fixed_bridge_accepts_exact_role_only_packet_and_exposes_fixed_resources`)
and was updated automatically by the import-rewrite pass since it asserts on
`FIXED_DOMAIN_RESOURCES["schema_resource"]`, a plain string. Three
`docs/development/v0.1.8/phase-2/evidence/*.json` files also contain the
literal string `data/scout` but are pure historical evidence, referenced by
no code or test — left untouched (see "historical docs" note below).
**Moved.** `src/gigai/data/scout/` (22 files: README/CHANGELOG/gig.py,
goalgraphs/*.md, tools/cap_*/{*.py,*.schema.json}, ui/{style.css,template.html})
→ `src/gigai/scout/data/` intact, same internal structure.

### `graph_node_registry.py`

Confirmed already at `src/gigai/graph_node_registry.py` (never had a `scout_`
prefix) — generic, stays in core. Untouched.

## Import / reference updates (mechanical, EXECUTED)

- Every `from gigai.scout_X import ...`, `import gigai.scout_X`, and bare
  `gigai.scout_X` dotted reference across `src/`, `tests/`, `tools/` rewritten
  to the new dotted path (`gigai.scout.X` or `gigai.scout.find_jobs.X`).
- Every `from .scout_X import ...` relative import, in both the 34+7 moved
  Scout files and the 10 untouched core modules (`run.py`, `cli.py`,
  `external_recording.py`, `application_events.py`, `capability_review.py`,
  `capability_successor.py`, `default_init.py`, `native_records.py`,
  `portability.py`, `private_transfer.py`), rewritten to the correct relative
  depth for the importer's new/unchanged package location.
- `gigai.data.scout.tools.cap_*` dynamic `import_module()` strings (in
  `scout_discovery.py`/`scout_research.py`/`scout_research_v3.py`/
  `scout_tailoring.py` and 10 test files) → `gigai.scout.data.tools.cap_*`.
- `resources.files("gigai.data").joinpath("scout")` in `scout/template.py` →
  `resources.files("gigai.scout").joinpath("data")`.
- One multi-arg `resources.files("gigai.data").joinpath("scout", "tools",
  "cap_...")` in `test_scout06_legacy_research_reuse.py` (not caught by the
  string-literal rewrite pass since it isn't a single path string) →
  `resources.files("gigai.scout").joinpath("data", "tools", "cap_...")`.
- `pyproject.toml` `[tool.setuptools.package-data]`: split the old
  `"gigai.data" = [..., "scout/*.md", "scout/goalgraphs/*.md", "scout/ui/*.html",
  "scout/ui/*.css", "scout/tools/*/*.json"]` entry into a new
  `"gigai.scout" = ["data/*.md", "data/goalgraphs/*.md", "data/ui/*.html",
  "data/ui/*.css", "data/tools/*/*.json"]` key, leaving `"gigai.data"` with
  only its unrelated `runtime-comparison-pack-v1.json` glob.
  `[tool.setuptools.packages.find]` uses bare `where = ["src"]` (auto-discovery,
  no explicit include list), so `gigai.scout` and `gigai.scout.find_jobs` are
  picked up automatically once they have `__init__.py` — no pyproject change
  needed there.
- `docs/development/v0.1.8/runbooks/M1-find-jobs.md`: `from gigai.scout_template
  import` → `from gigai.scout.template import`; `python -m gigai.scout_present_api`
  → `python -m gigai.scout.find_jobs.present_api`; `cd ui` → `cd src/gigai/scout/ui`.
- `tools/s11_inventory.py`: `CLI_NAMES` set referenced the old bare module
  basenames `scout_answer_cli`/`scout_tailor_cli` for AST-based test
  classification; updated to `answer_cli`/`tailor_cli` (the names that now
  actually appear as imported identifiers after the move). Low-risk: this is
  a dev-only heuristic script with no test coverage on the exact string.
- `Makefile`, `CONTRIBUTING.md`, `.github/workflows/*`, `tools/run_ci_tests.py`:
  no Scout-path references found; nothing to change.
- Pytest marker `scout_acquisition` (a marker name, not a module) confirmed
  untouched in `pyproject.toml` and its two usage sites.

## Non-mechanical decisions (READ, judged, then EXECUTED)

1. **`discovery` name collision (real bug, self-inflicted then fixed).** A
   generic script pass that bumped relative-import depth for every reference
   to a "root module" name incorrectly treated `from .discovery import
   validate_discovery_domain` inside `scout/posting_inputs.py` (originally
   `from .scout_discovery import ...`, correctly rewritten to `.discovery` by
   an earlier pass since `scout_discovery` → `scout.discovery`, a *sibling*)
   as if it meant the real root-level `gigai/discovery.py` (an unrelated,
   pre-existing module), bumping it to `..discovery`. This caused a real
   `ImportError: cannot import name 'validate_discovery_domain' from
   'gigai.discovery'` and cascaded into ~30 failing tests across
   `scout_discovery`, `scout_proposals_tools`, and `scout_tracking_reporting`
   (proposal execution/run/records depend on posting-input resolution, which
   depends on this import). Fixed by hand: `posting_inputs.py:209` reverted to
   `from .discovery import validate_discovery_domain` (the real target,
   `gigai.scout.discovery`, one level up from `posting_inputs.py`'s own
   `gigai.scout` package). Verified this is the *only* occurrence of this
   collision (`discovery` is the only stripped Scout module name that also
   matches a real root module name).

2. **`_INTERVIEW_SCHEMA` path in `scout/interview_records.py` (real bug,
   fixed).** `Path(__file__).with_name("schemas") / "scout-interview-
   preparation.schema.json"` pointed at `gigai/schemas/` correctly when the
   file lived at `gigai/interview_records.py`; after the move to
   `gigai/scout/interview_records.py`, `.with_name("schemas")` resolved to a
   nonexistent `gigai/scout/schemas/`. The actual schema lives in the shared,
   un-moved `gigai/schemas/` package (used by every Gig, not Scout-specific
   data). Fixed to `Path(__file__).parent.parent / "schemas" / "..."`, i.e.
   walked back up to `gigai/schemas/` — a plain path-depth fix, not a new
   resource-loading pattern, since it was the only occurrence of this
   construction anywhere in the moved tree.

3. **`from gigai import external_recording, scout_materialization` (real bug,
   fixed).** One test file (`test_scout06_legacy_research_reuse.py`) imported
   the old module as a bare name via `from gigai import X, Y` rather than
   `from gigai.X import` / `from gigai import X` singly, which the mechanical
   rewrite pass's regexes did not match. It's used as a `monkeypatch.setattr`
   target object. Fixed to `from gigai.scout import materialization as
   scout_materialization` so the local name and monkeypatch usage are
   unchanged.

4. **`test_package_data_declares_every_inert_non_python_scout_asset` (real
   test, updated).** This test in `test_scout05_source_bundle.py` hardcodes
   the *old* package-data key/pattern shape (`configuration["tool"]
   ["setuptools"]["package-data"]["gigai.data"]`, matched against
   `f"scout/{path}"`). Since the reorg legitimately moves this glob to a new
   `"gigai.scout"` key with `data/`-prefixed patterns (see pyproject change
   above), the test's structural assumption changed with it — updated to
   read the `"gigai.scout"` key and match `f"data/{path}"`. This is a direct,
   intended consequence of the physical move, not a new bug.

5. **`test_existing_scout_graphs_are_byte_identical_to_pre_change_output`
   (flagged, then resolved 2026-09-23 follow-up: converted to a pinned golden
   digest test, not retired).** Originally flagged as a one-time,
   git-history-diffing regression proof for the I-1 wave (Amendment 02 /
   P2-FREEZE-04) that could not survive any module rename by design (see the
   prior write-up this replaces, preserved below for history). The
   coordinator's decision: its green run *before* this reorg already proved
   the six pre-existing Scout graphs' compiled artifacts were byte-identical
   to pre-I-1 HEAD, so pin that verified baseline as golden digests instead of
   retiring the protection.

   **What I did:** renamed the test to
   `test_existing_scout_graphs_match_pinned_pre_i1_digests` in
   `tests/behaviors/scout_find_jobs/test_integration_graph.py`. Removed the
   `git show HEAD:...` / `importlib.util.spec_from_file_location` mechanism
   entirely (dropped the now-unused `importlib.util`, `json`, `sys`, `Path`
   imports along with it). In its place:
   - A deterministic UUIDv4 sequence (`_deterministic_uuid4_sequence`, seeded
     `random.Random(20260923)`, 200 values, version nibble forced to 4 since
     `generate_entity_id` requires real UUIDv4 shapes) replaces the shared
     `_seed_uuids` fixture *for this test only* — the other four tests in the
     file keep using the random per-run `_seed_uuids` fixture unchanged, since
     they only assert structural properties that hold for any valid sequence.
   - `_PINNED_UNCHANGED_ARTIFACT_DIGESTS`: a hardcoded dict of `sha256:...`
     digests (via `digest_imported_bytes`, the same helper the pre-existing
     `descriptor["goal_graph"]["content_sha256"]` assertion in this file
     already used) for all 50 compiled artifact keys that are NOT part of the
     new `find-jobs-functional` graph and NOT
     `compiled/first-graph-set-definition.json` (which legitimately gains one
     more descriptor and is checked separately): the 6 pre-existing graphs'
     `goal-graph.json`/`goal-contract.md`/`input_contract.json`/
     `output_contract.json`/`permitted_reference_contract.json`/
     `evaluation_contract.json`/`completion_evidence_contract.json`/
     `review-contract.json`, plus the Gig-wide `compiled/creation-manifest.json`
     and `compiled/gig.md`.
   - `_PINNED_OTHER_DESCRIPTORS_DIGEST`: one `canonical_json_digest` of the
     six pre-existing graph-set descriptors (order preserved, confirming
     `find-jobs-functional` is appended last rather than inserted/reordered).
   - `_PINNED_DEFINITION_FIELD_DIGESTS` / `_PINNED_DEFINITION_LITERAL_FIELDS`:
     digests/literal values for the graph-set definition's Gig-wide fields
     (`gig_document`, `creation_manifest`, `name`, `commission`,
     `schema_version`, `gig_id`) that must not shift when a graph is added —
     the same six fields the old test compared field-by-field against the git
     baseline.

   **How the pinned values were derived and verified (not just typed in):**
   computed all of the above from the *current* `_compiled_snapshot` output
   using the fixed seed, then cross-checked them byte-for-byte against the
   pre-reorg baseline by extracting `git show HEAD:src/gigai/scout_materialization.py`
   (HEAD is still the pre-reorg commit, `b01675d`, since nothing was
   `git add`ed/committed), exec'ing it under an isolated module name with
   `sys.modules["gigai.scout_template"]` / `sys.modules["gigai.scout_bundled_tools"]`
   temporarily aliased to the new, moved-but-content-unchanged modules (so the
   old file's own `from .scout_template import ...` relative import could
   resolve), and running `_compiled_snapshot` on both with the identical fixed
   seed. Confirmed **zero diffs** across all 50 keys — i.e. the pinned digests
   in the test genuinely equal the pre-I-1, pre-reorg baseline, not just
   "whatever the current code happens to produce." This one-time verification
   script is not committed (scratchpad-only); the pinned test itself carries
   no git dependency going forward.

   **Prior write-up (superseded, kept for history):** This test was a
   one-time regression proof for the I-1 wave that shelled out to
   `git show HEAD:src/gigai/scout_materialization.py`, exec'd that historical
   module body under an isolated name, and diffed its compiled graph bytes
   against the current module. Its own docstring said "HEAD being the last
   commit before this worker's edits" — i.e. it was never meant to survive a
   future module rename. It broke immediately once the file was physically
   moved (`ModuleNotFoundError: No module named 'gigai.scout_template'`,
   since the OLD module's own relative import could no longer resolve to
   anything on disk), and was the single remaining failure after the initial
   reorg PR. The coordinator's follow-up (this dispatch) resolved it as
   described above.

## Historical planning docs — left untouched

Per instructions, did not rewrite old-path citations with line numbers in
roadmap/amendment/spike evidence docs. Added the required mapping table and
0.2.0 ledger row to
`docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md`
under a new "2026-09-23 package re-org" change-log entry (see that file for
the full old→new table restated there per the task's instruction, plus the
new first 0.2.0 ledger row: "decouple core from gigai.scout (registry/plugin
discovery for CLI, graph nodes, records)").

## READ vs EXECUTED

- **READ (investigation only, no edits):** `pyproject.toml` original
  package-data/packages-find shape; every `SCHEMA_RESOURCE`/digest call site
  in `scout_discovery.py`/`scout_research.py`/`scout_research_v3.py`/
  `scout_tailoring.py`/`scout_posting_inputs.py` to rule out pinned-digest
  risk; the `docs/development/v0.1.8/phase-2/evidence/*.json` files (grepped,
  confirmed no code/test loads them); `Makefile`, `CONTRIBUTING.md`, CI
  workflow YAMLs (grepped, no hits); `tools/run_ci_tests.py` (grepped, no
  hits); the full `.orchestrator/logs/*.log` outputs from every test run.
- **EXECUTED (edits/moves):** all `mv` operations listed above; all import/
  reference rewrites (script-driven regex passes plus 5 hand fixes for the
  non-mechanical cases above); `pyproject.toml` package-data split; the
  runbook edits; the roadmap change-log/ledger addition; `tools/
  s11_inventory.py` CLI_NAMES update; `src/gigai/scout/ui/src/api.js` comment
  fix.

## Acceptance outputs

### 1. grep sweep (clean; only intentional hits)

```
$ grep -rn -E "gigai\.scout_|from \.scout_|import scout_|scout_[a-z_]+\.py" \
    src tests tools Makefile pyproject.toml CONTRIBUTING.md \
    docs/development/v0.1.8/runbooks
```
Remaining hits, all intentional:
- `src/gigai/private_transfer.py:31` and 9 test files / the runbook:
  `from gigai.scout.template import scout_source_files` /
  `scout_candidate_inventory` / `scout_catalog_candidate` — these are
  **function names** defined in `scout/template.py`, correctly imported from
  the new module path. Not stale module references.
- `tests/behaviors/scout_find_jobs/test_integration_graph.py:4,214,217` —
  the git-history-diffing test's own docstring prose and its deliberate
  `git show HEAD:src/gigai/scout_materialization.py` / local tmp-file name.
  Flagged above as a known, currently-failing, judgment-call item.

### 2. Import smoke

```
$ uv run --locked python -c "import gigai.cli, gigai.run, gigai.scout, \
    gigai.scout.find_jobs.present_api, gigai.scout.find_jobs.bindings"
```
→ succeeds, no output (success).

Every module under `src/gigai/scout` (42, excluding the `data/` namespace
package) plus all 5 `data/tools/cap_*/*.py` renderer modules imported via
`importlib.import_module` in a loop:
```
Attempted 42 module imports under gigai.scout (excluding .data.*)
ALL OK
OK gigai.scout.data.tools.cap_00000000-0000-4000-8000-000000000071.research
OK gigai.scout.data.tools.cap_00000000-0000-4000-8000-000000000071.record_tool
OK gigai.scout.data.tools.cap_00000000-0000-4000-8000-000000000074.discovery
OK gigai.scout.data.tools.cap_00000000-0000-4000-8000-000000000075.tailoring
OK gigai.scout.data.tools.cap_00000000-0000-4000-8000-000000000076.research
```

### 3. `yarn install && yarn build`

```
$ cd src/gigai/scout/ui && yarn install && yarn build
...
✓ 23 modules transformed.
dist/index.html                   0.40 kB │ gzip:  0.27 kB
dist/assets/index-Csxw5ire.css    3.78 kB │ gzip:  1.30 kB
dist/assets/index-gvDKuT5O.js   233.76 kB │ gzip: 72.42 kB
✓ built in 331ms
=== EXIT 0 ===
```

### 4. Full behavior suite

**Latest (2026-09-23, after converting the pinned-digest test — coordinator
follow-up dispatch):**
```
$ uv run --locked --extra test pytest tests/behaviors -q -n 8
...
1844 passed, 1 skipped, 8 warnings in 622.30s (0:10:22)
=== EXIT 0 ===
```
**0 failed.** The known g04 two-process-init flake did not appear; no reruns
needed.

History for this file's evolution across both dispatches:
- First full run (pre-fixes, initial reorg dispatch): `35 failed, 1808 passed,
  1 skipped, 8 warnings, 1 error in 585.71s`.
- After fixing the discovery-name collision, the `__file__`-relative schema
  path, the bare `from gigai import` form, and the package-data test:
  `1 failed, 1843 passed, 1 skipped, 8 warnings in 641.12s` — the sole
  remaining failure was `test_existing_scout_graphs_are_byte_identical_to_pre_change_output`
  (flagged for coordinator judgment; see non-mechanical decision 5 above).
- This dispatch: coordinator decided to convert that test into a pinned
  golden-digest test rather than retire it (see decision 5's updated
  write-up). After the conversion: **`1844 passed, 1 skipped, 0 failed`** —
  the full suite is clean.

### Pinned golden-digest test (this dispatch, standalone)

```
$ uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_integration_graph.py -q
.....                                                                    [100%]
5 passed in 0.12s
```

### 5. `uv build` + wheel contents

```
$ uv build
...
Successfully built dist/gigai-0.1.7.tar.gz
Successfully built dist/gigai-0.1.7-py3-none-any.whl
=== EXIT 0 ===
```
Wheel contents (via the build log's `adding '...'` lines) confirm:
- `gigai/scout/**` present: all 34 top-level Scout modules,
  `gigai/scout/find_jobs/{__init__,ats_board_clients,bindings,contracts,
  exa_client,market_acquisition,present_api,watchlist}.py`,
  `gigai/scout/data/**` (gig.py, README/CHANGELOG, goalgraphs/*.md,
  tools/cap_*/{*.py,*.schema.json}, ui/{style.css,template.html}).
- **No `node_modules` or `dist`** anywhere in the wheel (`grep -c
  "node_modules" build.log` → 0; `grep -c "scout/ui/" build.log` → 0 — the
  Vite app source itself is correctly absent from the Python wheel entirely,
  since nothing in `package-data` references `scout/ui/*`).
- Data present exactly where the code loads it from
  (`resources.files("gigai.scout").joinpath("data", ...)`).

## Summary

Physical move complete: 41 Scout modules relocated into `src/gigai/scout/`
(34 direct + 7 into `scout/find_jobs/`), the Vite UI moved to
`src/gigai/scout/ui/`, and the bundled data moved to `src/gigai/scout/data/`
after confirming no pinned digest depends on the old path. All import/
reference sites across `src/`, `tests/`, `tools/`, `pyproject.toml`, and the
M1 runbook were updated; `pyproject.toml` packaging config and the roadmap's
change-log/0.2.0-ledger were updated per instructions.

**2026-09-23 coordinator follow-up dispatch:** the one remaining test failure
from the initial reorg (`test_existing_scout_graphs_are_byte_identical_to_pre_change_output`,
a git-history-diffing regression proof structurally incompatible with any
module rename) was converted — not retired — into
`test_existing_scout_graphs_match_pinned_pre_i1_digests`: a pinned
golden-digest test with no git or path dependency. The pinned digests were
derived from the current materialization with a fixed, deterministic UUID
sequence and cross-verified byte-for-byte against the pre-reorg
`git show HEAD:src/gigai/scout_materialization.py` baseline before being
hardcoded, so they carry forward the same verified guarantee the original
proof established. The full behavior suite is now **1844 passed, 1 skipped,
0 failed** — clean. `yarn build` succeeds and `uv build` produces a correct
wheel with no `node_modules`/`dist` leakage (verified in the initial dispatch;
unaffected by this test-only change).
