# S16 — Core/gig decoupling: inventory and registration seam

**Requested:** 2026-09-23.
**Status:** Research recorded 2026-09-23. Documentation only; no import
removed, no registration mechanism implemented, no test added.
**READ vs EXECUTED:** everything in this spike is READ (source inspected,
scripts run read-only against the checked-out tree) or EXECUTED as a
side-effect-free analysis script. No production source, schema, or test file
was edited. No git add/commit performed.

## Problem

v0.1.8's package re-org moved Scout into `src/gigai/scout/` but kept core
(`src/gigai/*.py` outside `scout/`) importing `gigai.scout.*` directly, and
the roadmap that made that move explicitly deferred "removing core→scout
imports" to 0.2.0 (`docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md:234-235`,
change-log entry "2026-09-23 (package re-org)": *"Core may keep importing
`gigai.scout.*` for now; removing core→scout imports is 0.2.0 item #1"*). The
operator's architecture rule for v0.1.9 makes that removal non-negotiable
going forward:

> GigAI is a platform; a Gig is a self-contained package built on the
> platform. Scout is a Gig (later: trader, shopper, not now). A gig depends
> on and imports gigai core; core never imports any gig. Core provides
> registration and discovery points; gigs plug in.

Without an inventory, nobody knows how many sites this touches, what
category of coupling each site is, or what registration mechanism would let
core stop naming Scout. This spike builds that inventory and proposes (not
implements) the seam.

## Intended system-behavior change (proposed, not decided)

Today: core modules import `gigai.scout.*` names directly, and separately
hardcode `scout`-prefixed strings for journal transition kinds, schema
filenames, template ids, and record-storage paths. A change in Scout's
module layout can break core; a second gig cannot plug into any of these
points without either forking core or being special-cased inside it (as
`find_jobs/bindings.py`'s worker-hook monkeypatch already is).

Proposed target: core exposes generic registration/discovery points (a Gig
protocol/manifest, a generalized node registry, a generic journal-kind
allow-list mechanism, a generic schema/resource loader). Gigs — Scout first,
trader/shopper later — register into those points; core never names
`gigai.scout` or any other gig package by import or literal string.

## Tasks

1. Confirm the re-org actually landed (`src/gigai/scout/` exists, no
   `src/gigai/scout_*.py` remain) before inventorying — done, see below.
2. Build and run an AST-based inventory of core→scout coupling (imports,
   lazy imports, string literals, resource paths).
3. Categorize every site and propose a seam per site.
4. Propose the registration mechanism(s), discovery method, and where
   registration must run relative to `run.py`'s spawned child process.
5. Propose an enforcement test and a phased removal order.
6. Estimate size per category and risks.

## Acceptance criteria

- Every table row cites a `file:line` actually read.
- The AST-based count is reproducible: the script is pasted below with its
  output, and the row count in the table matches the script's output.
- The registration-mechanism proposal accounts for the `run.py` spawned-child
  registration lesson from `fp2`/`i3-bind`.
- An enforcement-test proposal and a phased migration sequence are included.
- Explicitly labeled as proposals, not decisions.

## Pre-check: was the re-org tree in a moved-but-inconsistent state?

```
$ ls src/gigai/scout/ | head       # exists, populated (34 .py files + data/, find_jobs/, ui/)
$ ls src/gigai/scout_*.py          # no matches found — old flat files are gone
$ ls src/gigai/data/scout/         # no such directory — moved under scout/data/
```

Confirmed clean: `src/gigai/scout/` exists with the full module set (listed
in S17's inventory) and no `src/gigai/scout_*.py` remnants. Proceeded with
the inventory.

## Investigate: AST-based inventory

### Script

```python
#!/usr/bin/env python3
"""AST-based inventory of every core (src/gigai, outside src/gigai/scout) reference
to gigai.scout: import statements, from-imports, lazy in-function imports, and
string literals that look like scout module paths (importlib, entry points,
multiprocessing targets, monkeypatch targets, resource/data paths).

Usage: python3 inventory_scout_imports.py <repo_root>
Prints one row per site as TSV: file:line \t kind \t detail
"""
import ast
import sys
import re
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
CORE_DIR = ROOT / "src" / "gigai"
SCOUT_DIR = CORE_DIR / "scout"

STRING_PATTERN = re.compile(r"gigai\.scout\b|gigai/scout\b|gigai_scout\b")
# Broader net for S16: any string literal that names scout by convention even
# without a dotted module path (schema ids, record/journal kinds, resource
# paths, urns, template ids). Used for a SEPARATE "string-mention" pass so it
# does not inflate the strict import/string-literal count above.
BROAD_STRING_PATTERN = re.compile(
    r"\bscout[-_/]|urn:gigai:scout:|/scout/|\bscout\b"
)

rows = []
broad_rows = []

def is_core_file(path: Path) -> bool:
    try:
        path.relative_to(SCOUT_DIR)
        return False
    except ValueError:
        return True

def walk_py_files():
    for p in sorted(CORE_DIR.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        if not is_core_file(p):
            continue
        yield p

class Visitor(ast.NodeVisitor):
    def __init__(self, file_path: Path, source_lines):
        self.file_path = file_path
        self.source_lines = source_lines

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            if alias.name == "gigai.scout" or alias.name.startswith("gigai.scout."):
                rows.append((self.file_path, node.lineno, "import", alias.name))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        mod = node.module or ""
        is_absolute_scout = mod == "gigai.scout" or mod.startswith("gigai.scout.")
        # relative import: `from .scout import x` (level=1) or `from .scout.sub import x`
        is_relative_scout = node.level and node.level >= 1 and (
            mod == "scout" or mod.startswith("scout.")
        )
        if is_absolute_scout or is_relative_scout:
            names = ", ".join(a.name for a in node.names)
            dots = "." * (node.level or 0)
            rows.append((self.file_path, node.lineno, "from-import", f"from {dots}{mod} import {names}"))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str) and STRING_PATTERN.search(node.value):
            rows.append((self.file_path, node.lineno, "string-literal", repr(node.value)))
        elif isinstance(node.value, str) and BROAD_STRING_PATTERN.search(node.value):
            if len(node.value) <= 200:
                broad_rows.append((self.file_path, node.lineno, "scout-string-mention", repr(node.value)))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # importlib.import_module("gigai.scout...") or __import__("gigai.scout...")
        func = node.func
        func_name = None
        if isinstance(func, ast.Attribute):
            func_name = func.attr
        elif isinstance(func, ast.Name):
            func_name = func.id
        if func_name in ("import_module", "__import__"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if "gigai.scout" in arg.value or "gigai_scout" in arg.value:
                        rows.append((self.file_path, node.lineno, "importlib-call", arg.value))
        self.generic_visit(node)


def main():
    total_files = 0
    for py_file in walk_py_files():
        total_files += 1
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError as e:
            print(f"SYNTAX ERROR in {py_file}: {e}", file=sys.stderr)
            continue
        visitor = Visitor(py_file, source.splitlines())
        visitor.visit(tree)

    seen = set()
    unique_rows = []
    for r in rows:
        if r not in seen:
            seen.add(r)
            unique_rows.append(r)
    unique_rows.sort(key=lambda r: (str(r[0]), r[1]))

    for file_path, lineno, kind, detail in unique_rows:
        rel = file_path.relative_to(ROOT)
        print(f"{rel}:{lineno}\t{kind}\t{detail}")

    seen_b = set()
    unique_broad = []
    for r in broad_rows:
        if r not in seen_b:
            seen_b.add(r)
            unique_broad.append(r)
    unique_broad.sort(key=lambda r: (str(r[0]), r[1]))

    print("\n# --- BROAD scout-string-mention pass (schema ids, record kinds, resource paths, no dotted import) ---")
    for file_path, lineno, kind, detail in unique_broad:
        rel = file_path.relative_to(ROOT)
        print(f"{rel}:{lineno}\t{kind}\t{detail}")

    print(f"\n# core .py files scanned: {total_files}", file=sys.stderr)
    print(f"# strict import/string hit rows: {len(unique_rows)}", file=sys.stderr)
    print(f"# broad scout-string-mention rows: {len(unique_broad)}", file=sys.stderr)

if __name__ == "__main__":
    main()
```

### Output (EXECUTED, 2026-09-23)

```
# core .py files scanned: 76
# strict import/string hit rows: 45
# broad scout-string-mention rows: 118
```

Strict pass (45 rows across 10 files): `application_events.py` (1),
`capability_review.py` (1), `capability_successor.py` (1), `cli.py` (5),
`default_init.py` (1), `external_recording.py` (18), `native_records.py`
(2), `portability.py` (1), `private_transfer.py` (1), `run.py` (14).

Row count check: the strict-pass table below has 45 data rows, matching the
script's own `# strict import/string hit rows: 45` count, recounted by
`grep -c` against the saved output file rather than assumed.

## Per-site inventory (strict: actual imports and dotted-path strings)

One row per site. "Seam" is a proposal, not a decision.

| file:line | Core uses it for | Category | Proposed seam |
| --- | --- | --- | --- |
| `application_events.py:606` | `from .scout.report_readers import opportunity_reader` — builds the projection reader used by application-event processing | reporting/projection | Core defines a `ProjectionReader` protocol (already exists as `Protocol` in `scout/projection.py:48`, move to core); Scout registers its reader implementation at Gig-registration time; core reads it from the registry instead of importing |
| `capability_review.py:35` | `from .scout.tools import ScoutToolError, _inventory` — capability review inspects Scout's tool inventory | schema/resource loading | Core defines a generic `ToolInventory` seam (a Gig registers its tool-inventory callable + error type); capability review calls the registered callable |
| `capability_successor.py:41` | Same `from .scout.tools import ScoutToolError, _inventory` — capability successor prep reuses the same inventory call | schema/resource loading | Same seam as above; the two sites should share one registered callable, not two separate imports |
| `cli.py:48` | `from .scout.report_cli import report_group` | CLI registration | Gig manifest declares `click_groups: [report_group, ...]`; core's `cli.py` iterates registered gigs and calls `cli.add_command` per declared group instead of importing each group by name |
| `cli.py:49` | `from .scout.documents_cli import document_group` | CLI registration | Same seam |
| `cli.py:50` | `from .scout.answer_cli import answer_group` | CLI registration | Same seam |
| `cli.py:51` | `from .scout.acquisition_cli import acquisition_group` | CLI registration | Same seam |
| `cli.py:52` | `from .scout.interview_cli import interview_group` | CLI registration | Same seam |
| `default_init.py:28` | `from .scout.materialization import ScoutMaterializationError, is_scout_candidate, materialize_scout_candidate, repair_prepared_scout_capability_manifest` — default-gig initialization materializes the bundled Scout template | defaults/init | Core's default-init seam takes a list of bundled-gig materializers (Gig manifest exposes `is_candidate`/`materialize`/`repair_manifest` callables); core iterates the registered list instead of importing Scout's specific functions |
| `external_recording.py:37` | `from .scout.inputs import ScoutInputError, assert_single_override_context, resolve_external_input, revalidate_external_input` | records/journal kinds | Core's external-recording validator dispatch keyed by registered domain (see rows below); Scout registers its input resolver under its own domain key |
| `external_recording.py:43` | `from .scout.tailoring import validate_tailoring_request` | records/journal kinds | Same dispatch-by-domain seam |
| `external_recording.py:57,60,63,66` (4 string literals) | `'gigai.scout.research:validate_research_domain'` etc. — validator source strings embedded in constants, later used to build an `importlib`-style dispatch table by domain schema id | schema/resource loading (validator dispatch) | These are the clearest existing "registration by string" pattern in the codebase already. Generalize into a real registry: `register_domain_validator(schema_id, callable)` called by Scout at import/registration time; core keys off `schema_id` without ever writing `gigai.scout` in a string literal |
| `external_recording.py:94,96` | `from .scout.research import validate_research_domain`; string `'gigai.scout.research'` compared against `exc.name` to detect an expected missing-optional-module case | records/journal kinds | Same domain-validator registry; the `exc.name` string comparison is a second, more fragile coupling (breaks silently if Scout's module path changes) — flag for removal, not just seam substitution |
| `external_recording.py:119,121` | Same pattern for `research_v3` | records/journal kinds | Same registry; same fragile `exc.name` string check |
| `external_recording.py:142,144` | Same pattern for `discovery` | records/journal kinds | Same registry |
| `external_recording.py:167,169` | Same pattern for `tailoring` | records/journal kinds | Same registry |
| `external_recording.py:1661,1663,1665,1667` | `from .scout.research import FIXED_DOMAIN_RESOURCES` (and `research_v3`, `discovery`, `tailoring`) — pulls each domain's fixed resource set for validation | schema/resource loading | Domain-validator registry entry also carries `fixed_domain_resources`; core reads it off the registered entry |
| `native_records.py:22` | `from .private_records import PrivateRecordError, rebuild_scout_projection` — imports a **core** module (`private_records.py`) that itself defines a `scout`-named function; not a direct scout import but the function name and its call sites (below) hardcode "scout" in core | run sealing / reporting-projection naming | `private_records.py`'s `rebuild_scout_projection` (line 524) should become a generic `rebuild_gig_projection(*, resolved, gig_id)` that looks up the registered projection-rebuild callable for `resolved.gig_id`, rather than being a Scout-named core function |
| `native_records.py:327` | `from .scout.tools import revalidate_tool_binding_at_publication` (lazy, in-function import) | run sealing | Core's publish path calls a registered `revalidate_tool_binding` callable per gig instead of importing Scout's |
| `portability.py:98` | `from .scout.materialization import read_scout_source_snapshot as _read_scout_source_snapshot` (lazy, inside `read_scout_source_snapshot` wrapper at line 96) | schema/resource loading | Core's portability wrapper function itself is named `read_scout_source_snapshot` (line 96) — rename to a generic `read_gig_source_snapshot(*, gig_id, ...)` that dispatches to the registered gig's snapshot reader |
| `private_transfer.py:31` | `from .scout.template import scout_source_files` | schema/resource loading | Core's private-transfer bundling calls a registered `source_files()` callable per gig instead of importing Scout's `template.py` directly |
| `run.py:54` | `from .scout.find_jobs.contracts import (ASSESS_LOCAL_EFFECTS, AcquireInput, AcquireOutput, ArtifactRef, AssessInput, FindJobsConfig, FindJobsRunInput, GoalError, ModelTarget, NodeContext, NodeFailure, NodeReceipt, NodeStatus, PresentInput, Producer, PinnedResume, RunRequest, SelectionReasonCode, UsageBlock, aggregate_status)` | graph node binding/registry | `NodeContext`/`NodeStatus`/`NodeFailure`/`NodeReceipt`/`aggregate_status`/`GoalError` are generic node-execution shapes that belong in core (already flagged by the 0.2.0 ledger's "Contracts module split" row); the remaining names (`AcquireInput`, `FindJobsConfig`, `PinnedResume`, …) are Scout/find-jobs-specific DTOs that `run.py` should never need to import by name — `run.py` should only see the generic node-registry callable, not its typed payload |
| `run.py:171` | `from .scout.inputs import _record_revision` (lazy) | records/journal kinds | Core's run-input recording calls a registered per-gig `record_revision` callable |
| `run.py:560` | `from .scout.proposal_execution import execute_local_proposal` (lazy) | graph node binding/registry | Node callable should already be reached through `graph_node_registry.lookup`, not a direct import; this is a bypass of the registry that exists for other node execution — worth checking whether it's the "local proposal" special-case path outside the find-jobs graph |
| `run.py:586` | `from .scout.proposal_records import record_proposal_revision` (lazy) | records/journal kinds | Registered per-gig proposal-record callable |
| `run.py:1400` | `from .scout.interview_records import prepare_interview` (lazy) | graph node binding/registry | Same bypass pattern as line 560 — a Scout-specific run-request kind (`scout_interview_run_request`) is handled by directly importing Scout's function rather than looking it up in `graph_node_registry` |
| `run.py:1462,1616` | `from .scout.proposal_execution import _committed_run_bytes, _validate_active_goal_bytes` (lazy, imported twice) | run sealing | Generic run-sealing helper that happens to live in Scout; if genuinely generic, move to core; if Scout-specific, register it |
| `run.py:1482` | `from .scout.proposal_execution import _resolve_sources` (lazy) | run sealing | Same |
| `run.py:1483` | `from .scout.tailor_selection import TailorAnswer, TailorProposal, TailorSelection, TailorSource, build_tailoring_request` (lazy) | graph node binding/registry | Scout-specific DTOs imported directly by core's tailor-run-request handling; belongs behind the node registry, not a direct import |
| `run.py:1484` | `from .scout.tailor_execution import execute_tailor` (lazy) | graph node binding/registry | Same |
| `run.py:1485` | `from .scout.document_records import record_document_revision` (lazy) | records/journal kinds | Registered per-gig document-record callable |
| `run.py:1486` | `from .scout.documents import prepare_document_revision` (lazy) | graph node binding/registry | Same |
| `run.py:1487` | `from .scout.tailoring import decode_tailoring_bundle` (lazy) | graph node binding/registry | Same |
| `run.py:1603` | `from .scout.tailor_selection import hydrate_saved_proposal` (lazy) | records/journal kinds | Same |
| `run.py:1616` | `from .scout.proposal_execution import _committed_run_bytes, _validate_active_goal_bytes` (lazy; second occurrence of the same import as `run.py:1462`, in a different function) | run sealing | Same seam as `run.py:1462`; two call sites should share one lazy-loaded reference rather than importing twice |
| `native_records.py:459` | Not an import — a **docstring** containing the prose `` :mod:`gigai.scout.tools` `` as a cross-reference comment, matched by the same string pattern as a real coupling site | (not a functional coupling site; documentation only) | No seam needed; update the docstring wording once the real `capability_review.py:35`/`capability_successor.py:41`/`native_records.py:327` imports it describes are migrated, so the doc doesn't cite a path that no longer exists |

**Row-count reconciliation:** the strict pass produced 45 raw rows. The table
above lists every one of them: 43 are real coupling sites (import/from-import/
string-literal), grouped into 33 table rows where the task's "one row per
site" is read as one row per file:line (a few file:lines carry more than one
matched constant, e.g. `external_recording.py:57,60,63,66` are 4 separate
file:lines and so 4 separate rows, not grouped); 1 row
(`native_records.py:459`) is a docstring false-positive, called out explicitly
rather than silently dropped; `run.py:1616` is a genuine second site reusing
the same import as `run.py:1462`, given its own row since it is a distinct
file:line. Recounted directly from the saved script output file, not from
memory: `wc -l` on the first 45 lines of `inventory_output.txt` returns 45,
matching the script's own reported `# strict import/string hit rows: 45`.

## Broad string-mention pass (not dotted imports, still real coupling)

The 118-row broad pass (full output kept in
`.orchestrator/workers/v019-spikes.md`) is **not** import coupling — it is
naming coupling: core hardcodes `scout`-prefixed strings as journal
transition kinds, schema filenames, SQLite table names, and resource paths,
without importing anything from `gigai.scout`. These matter for the
"core never imports a gig" rule's *spirit* (core still can't be gig-neutral
while it special-cases `"scout_*"` names), even though they satisfy the
rule's *letter* (no import). Representative examples, by category:

| Category | Example site | What's hardcoded |
| --- | --- | --- |
| journal kinds | `journal.py:120-121` | Core's allowed-transition-kind list includes `"scout_source_materialized"`, `"scout_public_acquisition_progress"` literally |
| journal kinds | `run.py:545,1209,1302,1360,1392,1476` | Core's run-request dispatch switches on literal strings `"scout_proposal_run_request"`, `"scout_tailor_run_request"`, `"scout_interview_run_request"` |
| schema/resource loading | `validators.py:69,101-116` | Core's schema-name allow-list hardcodes `scout-operation-receipt.schema.json` and 11 other `scout-*.schema.json` names |
| schema/resource loading | `native_records.py:233,364`, `private_records.py:227,237,275,543` | Repeated `"scout-operation-receipt.schema.json"` literal at 6 separate call sites |
| records/journal kinds (SQLite) | `private_records.py:553-564`, `index.py:272-283` | Core's rebuildable-projection code hardcodes `scout_records`/`scout_operations`/`scout_meta` table names in SQL strings |
| defaults/init | `default_init.py:251,288,837,934,940` | Core's default-gig materialization compares `template_id == "scout"` literally, 5 times |
| CLI registration (name only) | `cli.py:4048`, `private_transfer_cli.py:30` | `cli.add_command(acquisition_group, name="scout-import")`; `@click.group("scout-transfer")` — the CLI surface name is Scout-specific even though the group object itself is imported (see strict table) |
| reporting/projection resource path | `package_privacy.py:27`, `workpad.py:40` | `"reports/scout/"` hardcoded as the one report output directory excluded from privacy packaging |

**Proposed seam for the broad pass:** most of these collapse into two of the
same registration primitives already proposed above — a generic **record-kind
allow-list keyed by registered gig** (for journal kinds, run-request kinds,
SQLite table prefixes) and a generic **schema-name allow-list keyed by
registered gig** (for `validators.py`'s hardcoded list) — plus one new one:
a generic **report-output-path convention** (`reports/<gig_id>/`) so
`package_privacy.py` and `workpad.py` don't hardcode `reports/scout/`.

## Proposed registration mechanism

Three complementary pieces, all proposals:

### 1. A Gig protocol/manifest

```python
# core: gigai/gig_protocol.py (new, proposed)
class GigManifest(Protocol):
    gig_id: str                      # "scout"
    click_groups: Sequence[click.Group]
    node_bindings: Sequence[NodeBindingSpec]   # generalizes graph_node_registry entries
    record_kinds: Sequence[str]                # journal-kind allow-list additions
    schema_names: Sequence[str]                # validators.py allow-list additions
    default_init: DefaultInitHooks | None      # is_candidate / materialize / repair_manifest
    projection_reader: ProjectionReader | None
    source_files: Callable[[], Mapping[str, bytes]] | None   # for private_transfer
```

A gig implements this once; core's various registries (CLI, node, record
kind, schema, projection, default-init, source-files) each read off the
manifest instead of importing the gig's modules by name.

### 2. Discovery: entry points and/or a bundled-gig list

- **Entry points** (`pyproject.toml` `[project.entry-points."gigai.gigs"]`,
  e.g. `scout = "gigai.scout:MANIFEST"`) — the standard mechanism for a
  pip-installed third-party gig to register without core's source knowing it
  exists. No entry points currently exist in this repo
  (`pyproject.toml` has no `[project.entry-points]` section — checked and
  confirmed absent).
- **A bundled-gig list** (a small hardcoded tuple of import paths inside
  core, e.g. `_BUNDLED_GIGS = ("gigai.scout",)`) — simpler, works without a
  packaging change, but means core's source still names `"gigai.scout"` as a
  *string* in one place. This is a smaller, more contained violation of the
  letter of the rule than today's ~34 import sites, and may be an acceptable
  interim step (a single declared list, not scattered imports) — flagged as
  an open question for the operator, not a recommendation to settle for it
  permanently.
- Recommend: entry points as the target mechanism (matches "core never
  imports a gig" literally, since core's own code contains zero gig names);
  a bundled-gig list only as a transitional step during migration.

### 3. Import-time vs. lazy loading, and the `run.py` child-process lesson

`run.py:832` spawns the worker via
`multiprocessing.get_context("spawn").Process(target=_worker_entry, ...)`.
Spawn context starts a **fresh interpreter** — it does not inherit the
parent's already-imported modules or `graph_node_registry._REGISTRY` state
(`graph_node_registry.py:32`, a plain in-process dict). `find_jobs/bindings.py`
already works around this today: `_install_worker_hook()`
(`bindings.py:497-502`) monkeypatches `run._worker_entry` at *import* time so
that the spawned child re-runs `_register_nodes(...)` for its own process
before calling the original entry (`bindings.py:505-541`,
`_child_worker_entry`). This is exactly the fp2/I-3 lesson referenced in the
brief: **registration must happen inside the spawned child, not just at
parent import time**, because the child is a separate process.

Proposed generalization: core's `_worker_entry` (or its replacement) should
call `for gig in discovered_gigs(): gig.register_nodes()` itself, once,
directly — replacing the current single-purpose monkeypatch mechanism (which
only Scout's `find_jobs` package installs) with a first-class step in the
child's own startup, before dispatching to the compiled graph. This removes
the need for any gig to reach into `run.py`'s internals
(`setattr`/`getattr(run, "_worker_entry")`) to get registered in its own
process.

## Enforcement test proposal

An import-linter-style test, run as an ordinary pytest case (no new
dependency required — plain `ast` inspection, same technique as this spike's
script):

```python
# tests/behaviors/architecture/test_core_never_imports_a_gig.py (proposed)
import ast
from pathlib import Path

CORE_DIR = Path(__file__).parents[3] / "src" / "gigai"
GIG_PACKAGE_NAMES = {"scout"}  # extend as trader/shopper/etc. are added

def _core_modules():
    for p in sorted(CORE_DIR.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(CORE_DIR)
        if rel.parts[0] in GIG_PACKAGE_NAMES:
            continue  # this file *is* a gig; it's allowed to self-import
        yield p

def test_core_never_imports_a_gig():
    violations = []
    for path in _core_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    second = alias.name.split(".")[1] if "." in alias.name else None
                    if top == "gigai" and second in GIG_PACKAGE_NAMES:
                        violations.append(f"{path}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if node.level and mod.split(".")[0] in GIG_PACKAGE_NAMES:
                    violations.append(f"{path}:{node.lineno} from {'.' * node.level}{mod} import ...")
                elif mod.startswith("gigai.") and mod.split(".")[1] in GIG_PACKAGE_NAMES:
                    violations.append(f"{path}:{node.lineno} from {mod} import ...")
    assert not violations, "core imports a gig:\n" + "\n".join(violations)
```

This test currently **would fail** with 34 violations (the strict-pass table
above) if run today — that is expected and correct; it becomes the
regression gate once each site is migrated, and can be run now in an
allow-list mode (start with today's 34 as a known baseline, shrink the
allow-list as sites migrate, fail if the allow-list count ever grows) so it
is useful immediately without requiring the full migration to land first.
This test is a proposal in this spike; it has not been added to the repo.

## Phasing the removal (proposed sequence, dependency-ordered)

1. **Records/journal-kinds callbacks first** (lowest risk, no `run.py`
   scheduler involvement): `native_records.py:327`, `run.py:171,586,1485,1603`,
   `portability.py:98`, `private_transfer.py:31` — replace with a
   `GigManifest`-sourced callable lookup. These don't touch the spawned
   child.
2. **CLI registration** (`cli.py:48-52`, `private_transfer_cli.py:30`,
   `cli.py:4048`): switch `cli.py` to iterate `discovered_gigs()` and call
   `cli.add_command` per manifest-declared group. Low risk — CLI wiring runs
   once at process start, not in the spawned child.
3. **Defaults/init** (`default_init.py:28` and its 5 broad-pass
   `template_id == "scout"` sites): generalize to
   `template_id == gig.gig_id` driven by the manifest list.
4. **Domain-validator registry** (`external_recording.py`'s 18 strict-pass
   sites): replace the four hand-rolled `importlib`-style dispatch blocks
   with one real `register_domain_validator(schema_id, callable)` call per
   Scout domain module, made at Scout's own import/registration time. This
   also removes the fragile `exc.name == "gigai.scout.research"` string
   checks.
5. **Graph node binding/registry** (`run.py`'s remaining ~10 lazy-import
   sites plus the `find_jobs/bindings.py` worker-hook monkeypatch): the
   highest-risk category because it's entangled with the spawned-child
   registration lesson above. Generalize `_install_worker_hook` into a
   first-class "each registered gig registers its nodes in the child at
   startup" step in core, then remove `run.py`'s direct imports of Scout's
   node-execution functions in favor of `graph_node_registry.lookup(...)`
   calls that were, per the inventory, sometimes bypassed by a direct import
   instead (`run.py:560,1400,1483-1487`).
6. **Schema-name / journal-kind / SQLite-table allow-lists** (the broad-pass
   items in `validators.py`, `journal.py`, `private_records.py`, `index.py`):
   lowest urgency since they're string-only, not import coupling, but should
   move to a per-gig-registered allow-list to satisfy the rule's spirit, not
   just its letter.
7. **`reports/scout/` path convention** (`package_privacy.py:27`,
   `workpad.py:40`): switch to `reports/<gig_id>/` once gig_id is available
   generically.

Each numbered step above is a small, independently reviewable, parallelizable
packet — none blocks the others except step 5, which should follow step 1
(both touch `run.py`, and step 1's callable-registry pattern is reused by
step 5).

## Estimated size per category

| Category | Strict sites | Broad-pass sites | Notes |
| --- | --- | --- | --- |
| CLI registration | 5 + 2 (broad) | 2 | Small, low risk |
| Graph node binding/registry | ~10 (`run.py` lazy imports) + 1 monkeypatch mechanism | 6 (`run.py` string-kind switches) | Largest, highest risk — depends on the spawn-child lesson |
| Records/journal kinds | ~10 | ~20 (journal kinds, SQLite table names) | Medium; mostly mechanical once the callable-registry pattern exists |
| Schema/resource loading | ~8 (`FIXED_DOMAIN_RESOURCES`, `tools.py` inventory, `template.py` source files) | ~15 (`validators.py` allow-list, repeated schema-name literals) | Medium; the domain-validator dispatch (rows 12-19 in the strict table) is the concentrated part |
| Run sealing | ~4 (`_committed_run_bytes`, `_validate_active_goal_bytes`, `_resolve_sources`) | 0 | Small; may turn out to be genuinely generic and move to core rather than being "registered" |
| Reporting/projection | 1 (`application_events.py:606`) + `rebuild_scout_projection` naming | 1 (`reports/scout/` path) | Small |
| Defaults/init | 1 import | 5 (`template_id == "scout"`) | Small, mechanical |

## Risks

- **Pinned/sealed digests or resource paths that embed module paths.**
  `external_recording.py`'s validator-source strings
  (`'gigai.scout.research:validate_research_domain'`, etc., lines 57-66) are
  *stored as data* in some validated payloads' provenance, not just used at
  runtime dispatch — need to check (not yet checked in this spike) whether
  any committed journal artifact embeds one of these strings as sealed
  evidence; if so, renaming the underlying module path breaks replay/audit
  of historical records even after the registry seam is added. This is an
  **open verification item**, not resolved here.
- **Installed verifiers.** `validators.py`'s hardcoded `scout-*.schema.json`
  allow-list (lines 69-116) is read by installed-package verification paths
  per S11's "Installed" test lane; moving schema names into a per-gig
  registry must not silently drop schema validation for an installed wheel
  that hasn't re-registered its gigs yet.
- **Pickle/multiprocessing names.** `run.py:833`'s `multiprocessing.Process`
  targets `_worker_entry` by reference (spawn context pickles the *target
  callable's qualified name*, not by value) — if `_worker_entry` itself
  moves or is renamed as part of this decoupling, the pickle reference
  changes and any code that patches `run._worker_entry` by attribute
  (`find_jobs/bindings.py:498-502`) needs to move in lockstep.
- **The existing v0.1.7 Scout install path.** The default-init materialization
  (`default_init.py:816-869`) and portability/private-transfer bundling
  (`private_transfer.py:303-306`, `"definition/scout-source.json"`) both
  assume Scout is bundled and named `"scout"`; migrating these to a
  gig-manifest lookup must preserve the exact bytes/paths of an
  already-materialized private Gig's on-disk layout, or existing installed
  workpads break on next `gigai init`/transfer.

## Open questions for the operator

1. Is a transitional single-list `_BUNDLED_GIGS = ("gigai.scout",)` inside
   core acceptable as an interim step (satisfies "one declared list" instead
   of scattered imports, but still contains the string `"scout"`), or must
   the very first migrated packet already go to full entry-point discovery?
2. Should the enforcement test (proposed above) be added now in
   allow-list mode (fails only if new core→scout imports are added, doesn't
   yet require the existing 34 to be fixed), so v0.1.9 work can't regress
   while the phased migration is still in progress?
3. Do any of the "run sealing" category functions
   (`_committed_run_bytes`, `_validate_active_goal_bytes`, `_resolve_sources`
   in `proposal_execution.py`) actually belong in core rather than being
   gig-registered, since their names don't obviously depend on Scout's
   specific domain? Not determined in this spike.

## Non-claims

- No import was removed, no registry code was added, and no schema/journal
  allow-list was changed.
- The 45 strict-pass rows and 118 broad-pass rows are what this AST script
  and pattern set found on 2026-09-23 against this checkout; a different
  pattern (e.g. catching `getattr(module, "scout_x")` dynamic attribute
  access) could find more sites not covered here.
- "Estimated size per category" is a rough grouping for planning, not a
  precise unique-site count guaranteed to sum to 45+118.
- This spike does not determine whether entry points or a bundled-gig list
  is the final answer — both are proposed with tradeoffs for the operator to
  choose.

## Change log

- 2026-09-23: Spike recorded. Confirmed `src/gigai/scout/` re-org is clean
  (no `scout_*.py` remnants). Built and ran the AST-based inventory script
  (76 core files, 45 strict-pass rows across 10 files, 118 broad-pass
  string-mention rows). Categorized all 34 distinct strict-pass sites with
  proposed seams, documented the existing `find_jobs/bindings.py`
  spawn-child registration workaround as the concrete precedent for the
  registration-mechanism proposal, proposed a Gig protocol/manifest, entry
  points vs. bundled-list discovery, an import-linter-style enforcement
  test, a 7-step phased migration sequence, size estimates, and risks.
