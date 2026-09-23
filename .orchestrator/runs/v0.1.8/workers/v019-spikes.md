# Worker: v019-spikes

**Task:** task_dc2e1e6365af — create `docs/development/v0.1.9/` and record
S16, S17, S18 research spikes for the next version; S19 (test-suite diet)
added mid-dispatch as a 4th spike per coordinator follow-up.
**Date:** 2026-09-23.
**READ vs EXECUTED:** all claims in the four spikes are READ (source files,
existing docs, evidence files inspected directly with line numbers cited)
or EXECUTED as side-effect-free analysis scripts (AST inventory, a
heavy-test classifier, a handful of individually time-boxed `pytest` runs on
single files — never the full suite). No production source, schema, or test
file was edited. No git add/commit performed at any point.

## Pre-check (per the task's STOP condition)

Before inventorying, confirmed the re-org worker had actually finished
cleanly:

```
$ ls src/gigai/scout/ | head        # → 34 .py files + data/, find_jobs/, ui/ (populated)
$ ls src/gigai/scout_*.py           # → no matches found (old flat files gone)
$ ls src/gigai/data/scout/          # → no such directory (moved to scout/data/)
```

Clean. Proceeded with the inventory (did not need to `orca orchestration ask`).

## Files created (owned files only; nothing else touched)

- `docs/development/v0.1.9/README.md`
- `docs/development/v0.1.9/spikes/README.md`
- `docs/development/v0.1.9/spikes/S16-core-gig-decoupling.md`
- `docs/development/v0.1.9/spikes/S17-gig-module-structure-classes.md`
- `docs/development/v0.1.9/spikes/S18-scout-interview-prep-graphs.md`
- `docs/development/v0.1.9/spikes/S19-test-suite-diet.md` (added mid-dispatch)
- `.orchestrator/workers/v019-spikes.md` (this file)

## S16 — Core/gig decoupling: AST inventory script and full output

### Script (pasted verbatim; also embedded in S16's own body)

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

### Full output (EXECUTED 2026-09-23; stdout then stderr)

```
src/gigai/application_events.py:606	from-import	from .scout.report_readers import opportunity_reader
src/gigai/capability_review.py:35	from-import	from .scout.tools import ScoutToolError, _inventory
src/gigai/capability_successor.py:41	from-import	from .scout.tools import ScoutToolError, _inventory
src/gigai/cli.py:48	from-import	from .scout.report_cli import report_group
src/gigai/cli.py:49	from-import	from .scout.documents_cli import document_group
src/gigai/cli.py:50	from-import	from .scout.answer_cli import answer_group
src/gigai/cli.py:51	from-import	from .scout.acquisition_cli import acquisition_group
src/gigai/cli.py:52	from-import	from .scout.interview_cli import interview_group
src/gigai/default_init.py:28	from-import	from .scout.materialization import ScoutMaterializationError, is_scout_candidate, materialize_scout_candidate, repair_prepared_scout_capability_manifest
src/gigai/external_recording.py:37	from-import	from .scout.inputs import ScoutInputError, assert_single_override_context, resolve_external_input, revalidate_external_input
src/gigai/external_recording.py:43	from-import	from .scout.tailoring import validate_tailoring_request
src/gigai/external_recording.py:57	string-literal	'gigai.scout.research:validate_research_domain'
src/gigai/external_recording.py:60	string-literal	'gigai.scout.research_v3:validate_research_domain'
src/gigai/external_recording.py:63	string-literal	'gigai.scout.discovery:validate_discovery_domain'
src/gigai/external_recording.py:66	string-literal	'gigai.scout.tailoring:validate_tailoring_domain'
src/gigai/external_recording.py:94	from-import	from .scout.research import validate_research_domain
src/gigai/external_recording.py:96	string-literal	'gigai.scout.research'
src/gigai/external_recording.py:119	from-import	from .scout.research_v3 import validate_research_domain
src/gigai/external_recording.py:121	string-literal	'gigai.scout.research_v3'
src/gigai/external_recording.py:142	from-import	from .scout.discovery import validate_discovery_domain
src/gigai/external_recording.py:144	string-literal	'gigai.scout.discovery'
src/gigai/external_recording.py:167	from-import	from .scout.tailoring import validate_tailoring_domain
src/gigai/external_recording.py:169	string-literal	'gigai.scout.tailoring'
src/gigai/external_recording.py:1661	from-import	from .scout.research import FIXED_DOMAIN_RESOURCES
src/gigai/external_recording.py:1663	from-import	from .scout.research_v3 import FIXED_DOMAIN_RESOURCES
src/gigai/external_recording.py:1665	from-import	from .scout.discovery import FIXED_DOMAIN_RESOURCES
src/gigai/external_recording.py:1667	from-import	from .scout.tailoring import FIXED_DOMAIN_RESOURCES
src/gigai/native_records.py:327	from-import	from .scout.tools import revalidate_tool_binding_at_publication
src/gigai/native_records.py:459	string-literal	'Publish one already-authorized tool request through the shared C1 service.\n\n    This is intentionally not a general tool runner.  The publisher resolves\n    the supplied binding independently through :mod:`gigai.scout.tools` while\n    holding the journal writer lock; callers cannot supply a validator.\n    '
src/gigai/portability.py:98	from-import	from .scout.materialization import read_scout_source_snapshot
src/gigai/private_transfer.py:31	from-import	from .scout.template import scout_source_files
src/gigai/run.py:54	from-import	from .scout.find_jobs.contracts import ASSESS_LOCAL_EFFECTS, AcquireInput, AcquireOutput, ArtifactRef, AssessInput, FindJobsConfig, FindJobsRunInput, GoalError, ModelTarget, NodeContext, NodeFailure, NodeReceipt, NodeStatus, PresentInput, Producer, PinnedResume, RunRequest, SelectionReasonCode, UsageBlock, aggregate_status
src/gigai/run.py:171	from-import	from .scout.inputs import _record_revision
src/gigai/run.py:560	from-import	from .scout.proposal_execution import execute_local_proposal
src/gigai/run.py:586	from-import	from .scout.proposal_records import record_proposal_revision
src/gigai/run.py:1400	from-import	from .scout.interview_records import prepare_interview
src/gigai/run.py:1462	from-import	from .scout.proposal_execution import _committed_run_bytes, _validate_active_goal_bytes
src/gigai/run.py:1482	from-import	from .scout.proposal_execution import _resolve_sources
src/gigai/run.py:1483	from-import	from .scout.tailor_selection import TailorAnswer, TailorProposal, TailorSelection, TailorSource, build_tailoring_request
src/gigai/run.py:1484	from-import	from .scout.tailor_execution import execute_tailor
src/gigai/run.py:1485	from-import	from .scout.document_records import record_document_revision
src/gigai/run.py:1486	from-import	from .scout.documents import prepare_document_revision
src/gigai/run.py:1487	from-import	from .scout.tailoring import decode_tailoring_bundle
src/gigai/run.py:1603	from-import	from .scout.tailor_selection import hydrate_saved_proposal
src/gigai/run.py:1616	from-import	from .scout.proposal_execution import _committed_run_bytes, _validate_active_goal_bytes

# --- BROAD scout-string-mention pass (schema ids, record kinds, resource paths, no dotted import) ---
src/gigai/application_events.py:213	scout-string-mention	'scout_document'
src/gigai/application_events.py:231	scout-string-mention	'records/scout-documents/'
src/gigai/application_events.py:246	scout-string-mention	'scout-document-revision-v1.schema.json'
src/gigai/application_events.py:269	scout-string-mention	'records/scout-documents/'
src/gigai/application_events.py:276	scout-string-mention	'/scout-tailor/result.json'
src/gigai/application_events.py:289	scout-string-mention	'scout-tailor-run-result:1'
src/gigai/application_events.py:329	scout-string-mention	'scout_document'
src/gigai/application_events.py:393	scout-string-mention	'scout_document'
src/gigai/application_events.py:492	scout-string-mention	'scout_document'
src/gigai/cli.py:2041	scout-string-mention	'scout_status'
src/gigai/cli.py:4048	scout-string-mention	'scout-import'
src/gigai/default_init.py:251	scout-string-mention	'scout'
src/gigai/default_init.py:288	scout-string-mention	'scout'
src/gigai/default_init.py:837	scout-string-mention	'scout'
src/gigai/default_init.py:934	scout-string-mention	'scout'
src/gigai/default_init.py:940	scout-string-mention	'scout'
src/gigai/external_recording.py:55	scout-string-mention	'urn:gigai:scout:research-packet:2'
src/gigai/external_recording.py:56	scout-string-mention	'scout-role-research:2'
src/gigai/external_recording.py:58	scout-string-mention	'urn:gigai:scout:research-packet:3'
src/gigai/external_recording.py:59	scout-string-mention	'scout-role-research:3'
src/gigai/external_recording.py:61	scout-string-mention	'urn:gigai:scout:discovery-packet:2'
src/gigai/external_recording.py:62	scout-string-mention	'scout-job-discovery:2'
src/gigai/external_recording.py:64	scout-string-mention	'urn:gigai:scout:tailoring-packet:1'
src/gigai/external_recording.py:65	scout-string-mention	'scout-application-tailoring:1'
src/gigai/external_recording.py:1542	scout-string-mention	'scout-tailoring-request:1'
src/gigai/external_recording.py:1644	scout-string-mention	'scout_research'
src/gigai/external_recording.py:1645	scout-string-mention	'scout_research_v3'
src/gigai/external_recording.py:1646	scout-string-mention	'scout_discovery'
src/gigai/external_recording.py:1647	scout-string-mention	'scout_tailoring'
src/gigai/external_recording.py:1719	scout-string-mention	'scout_research'
src/gigai/external_recording.py:1977	scout-string-mention	'scout_record'
src/gigai/external_recording.py:2014	scout-string-mention	'scout_discovery_posting'
src/gigai/graph_set.py:115	scout-string-mention	'urn:gigai:scout:research-packet:2'
src/gigai/graph_set.py:116	scout-string-mention	'urn:gigai:scout:research-packet:3'
src/gigai/graph_set.py:117	scout-string-mention	'urn:gigai:scout:discovery-packet:2'
src/gigai/graph_set.py:118	scout-string-mention	'urn:gigai:scout:tailoring-packet:1'
src/gigai/graph_set.py:121	scout-string-mention	'urn:gigai:scout:research-packet:2'
src/gigai/graph_set.py:121	scout-string-mention	'scout-role-research:2'
src/gigai/graph_set.py:122	scout-string-mention	'urn:gigai:scout:research-packet:3'
src/gigai/graph_set.py:122	scout-string-mention	'scout-role-research:3'
src/gigai/graph_set.py:123	scout-string-mention	'urn:gigai:scout:discovery-packet:2'
src/gigai/graph_set.py:123	scout-string-mention	'scout-job-discovery:2'
src/gigai/graph_set.py:124	scout-string-mention	'urn:gigai:scout:tailoring-packet:1'
src/gigai/graph_set.py:124	scout-string-mention	'scout-application-tailoring:1'
src/gigai/graph_set.py:130	scout-string-mention	'urn:gigai:scout:research-packet:2'
src/gigai/graph_set.py:130	scout-string-mention	'urn:gigai:scout:research-packet:3'
src/gigai/graph_set.py:132	scout-string-mention	'urn:gigai:scout:discovery-packet:2'
src/gigai/index.py:272	scout-string-mention	'scout_records'
src/gigai/index.py:272	scout-string-mention	'scout_operations'
src/gigai/index.py:272	scout-string-mention	'scout_meta'
src/gigai/index.py:278	scout-string-mention	'scout_records'
src/gigai/index.py:278	scout-string-mention	'scout_operations'
src/gigai/index.py:278	scout-string-mention	'scout_meta'
src/gigai/index.py:283	scout-string-mention	'scout_records'
src/gigai/index.py:283	scout-string-mention	'scout_operations'
src/gigai/index.py:283	scout-string-mention	'scout_meta'
src/gigai/journal.py:120	scout-string-mention	'scout_source_materialized'
src/gigai/journal.py:121	scout-string-mention	'scout_public_acquisition_progress'
src/gigai/model_execution.py:51	scout-string-mention	'scout_discovery_posting'
src/gigai/model_execution.py:51	scout-string-mention	'scout_record'
src/gigai/native_records.py:233	scout-string-mention	'scout-operation-receipt.schema.json'
src/gigai/native_records.py:364	scout-string-mention	'scout-operation-receipt.schema.json'
src/gigai/package_privacy.py:27	scout-string-mention	'reports/scout/'
src/gigai/package_privacy.py:44	scout-string-mention	'scout-operation-receipt.schema.json'
src/gigai/private_records.py:227	scout-string-mention	'scout-operation-receipt.schema.json'
src/gigai/private_records.py:237	scout-string-mention	'scout-operation-receipt.schema.json'
src/gigai/private_records.py:275	scout-string-mention	'scout-operation-receipt.schema.json'
src/gigai/private_records.py:543	scout-string-mention	'scout-operation-receipt.schema.json'
src/gigai/private_records.py:553	scout-string-mention	'scout_records'
src/gigai/private_records.py:553	scout-string-mention	'scout_operations'
src/gigai/private_records.py:553	scout-string-mention	'scout_meta'
src/gigai/private_records.py:555	scout-string-mention	'DELETE FROM scout_records'
src/gigai/private_records.py:556	scout-string-mention	'INSERT INTO scout_records(key,payload) VALUES (?,?)'
src/gigai/private_records.py:557	scout-string-mention	'DELETE FROM scout_operations'
src/gigai/private_records.py:559	scout-string-mention	'INSERT INTO scout_operations(key,payload) VALUES (?,?)'
src/gigai/private_records.py:562	scout-string-mention	'DELETE FROM scout_meta'
src/gigai/private_records.py:564	scout-string-mention	'INSERT INTO scout_meta(key,payload) VALUES (?,?)'
src/gigai/private_transfer.py:64	scout-string-mention	'scout_definition_export'
src/gigai/private_transfer.py:65	scout-string-mention	'scout_private_transfer'
src/gigai/private_transfer.py:249	scout-string-mention	'scout-definition-export-manifest.schema.json'
src/gigai/private_transfer.py:251	scout-string-mention	'scout-private-transfer-manifest.schema.json'
src/gigai/private_transfer.py:272	scout-string-mention	'.scout-transfer-'
src/gigai/private_transfer.py:306	scout-string-mention	'definition/scout-source.json'
src/gigai/private_transfer.py:814	scout-string-mention	'scout_private_restore_binding'
src/gigai/private_transfer_cli.py:30	scout-string-mention	'scout-transfer'
src/gigai/run.py:545	scout-string-mention	'scout_proposal_run_request'
src/gigai/run.py:1197	scout-string-mention	'scout_discovery'
src/gigai/run.py:1209	scout-string-mention	'scout_proposal_run_request'
src/gigai/run.py:1288	scout-string-mention	'scout_discovery'
src/gigai/run.py:1302	scout-string-mention	'scout_tailor_run_request'
src/gigai/run.py:1360	scout-string-mention	'scout_interview_run_request'
src/gigai/run.py:1392	scout-string-mention	'scout_interview_run_request'
src/gigai/run.py:1411	scout-string-mention	'scout_interview_run_result'
src/gigai/run.py:1442	scout-string-mention	'/scout-interview/result.json'
src/gigai/run.py:1476	scout-string-mention	'scout_tailor_run_request'
src/gigai/run.py:1508	scout-string-mention	'scout_record'
src/gigai/run.py:1526	scout-string-mention	'scout/'
src/gigai/run.py:1532	scout-string-mention	'scout_record'
src/gigai/run.py:1569	scout-string-mention	'scout-tailor-run-result:1'
src/gigai/run.py:1612	scout-string-mention	'scout-tailor-execution'
src/gigai/run.py:1618	scout-string-mention	'/scout-tailor/result.json'
src/gigai/run.py:1651	scout-string-mention	'scout-tailor-execution'
src/gigai/run.py:4165	scout-string-mention	'scout-find-jobs-acquire-input:1'
src/gigai/run.py:4196	scout-string-mention	'scout-find-jobs-assess-input:1'
src/gigai/run.py:4205	scout-string-mention	'scout-answer-association:1'
src/gigai/validators.py:69	scout-string-mention	'scout-operation-receipt.schema.json'
src/gigai/validators.py:101	scout-string-mention	'scout-proposal-revision.schema.json'
src/gigai/validators.py:102	scout-string-mention	'scout-answer-association.schema.json'
src/gigai/validators.py:103	scout-string-mention	'scout-proposal-discovery-job.schema.json'
src/gigai/validators.py:104	scout-string-mention	'scout-tailor-selection-v2.schema.json'
src/gigai/validators.py:105	scout-string-mention	'scout-document-revision-v1.schema.json'
src/gigai/validators.py:106	scout-string-mention	'scout-document-selection-v1.schema.json'
src/gigai/validators.py:107	scout-string-mention	'scout-document-selection-v2.schema.json'
src/gigai/validators.py:108	scout-string-mention	'scout-public-import-input.schema.json'
src/gigai/validators.py:109	scout-string-mention	'scout-public-import-progress.schema.json'
src/gigai/validators.py:114	scout-string-mention	'scout-interview-preparation.schema.json'
src/gigai/validators.py:115	scout-string-mention	'scout-private-transfer-manifest.schema.json'
src/gigai/validators.py:116	scout-string-mention	'scout-definition-export-manifest.schema.json'
```

```
# core .py files scanned: 76
# strict import/string hit rows: 45
# broad scout-string-mention rows: 118
```

**Row-count check:** the strict-pass table above has exactly 45 lines (the
lines between the header and the blank line before the BROAD section),
matching `# strict import/string hit rows: 45` reported by the script
itself — recounted directly (`wc -l` on that line range), not assumed. S16's
own body accounts for every one of the 45 rows (43 real coupling sites +
1 docstring false-positive at `native_records.py:459`, called out explicitly,
+ 1 genuine second occurrence of the same import at `run.py:1616`).

## S19 — heavy-test classifier script and full output

### Script

```python
#!/usr/bin/env python3
"""Classify each tests/**/test_*.py file as heavy (subprocess/git/server/real
run process) or not, based on source-text signals."""
import re
from pathlib import Path

ROOT = Path(".")
TEST_FILES = sorted((ROOT / "tests").rglob("test_*.py"))

SIGNALS = {
    "subprocess": re.compile(r"\bsubprocess\.(run|Popen|call|check_output|check_call)\b"),
    "git": re.compile(r"\bshutil\.which\(\s*[\"']git[\"']|\bgit\s+init\b|git\.Repo\(|[\"']git[\"'],"),
    "multiprocessing": re.compile(r"\bmultiprocessing\."),
    "http_server": re.compile(r"HTTPServer|socketserver|ThreadingHTTPServer|present_api|\.serve\("),
    "launch_run": re.compile(r"\blaunch_run\("),
    "real_process_wait": re.compile(r"wait=True"),
}

total = 0
heavy = 0
rows = []
for f in TEST_FILES:
    total += 1
    text = f.read_text(encoding="utf-8", errors="replace")
    hits = [name for name, pat in SIGNALS.items() if pat.search(text)]
    if hits:
        heavy += 1
        rows.append((str(f.relative_to(ROOT)), hits))

for path, hits in rows:
    print(f"{path}\t{','.join(hits)}")

print(f"\n# total test_*.py files: {total}")
print(f"# heavy files (>=1 signal): {heavy}")
print(f"# percent: {round(heavy/total*100)}%")
```

### Full output (EXECUTED 2026-09-23; 74 files)

```
tests/behaviors/acquisition/test_public_acquisition_lifecycle.py	subprocess,git
tests/behaviors/cli_surface/test_cli_and_scenario_harness.py	subprocess
tests/behaviors/cli_surface/test_setup_browser.py	http_server
tests/behaviors/installed_release/test_g04_installed_scenarios.py	subprocess,git
tests/behaviors/installed_release/test_g05_installed_scenarios.py	git
tests/behaviors/installed_release/test_g06_installed_scenarios.py	subprocess
tests/behaviors/installed_release/test_g07_installed_scenarios.py	git
tests/behaviors/installed_release/test_g08_installed_scenarios.py	subprocess
tests/behaviors/installed_release/test_g10_phase1_audit.py	git
tests/behaviors/installed_release/test_g41_package_boundary.py	subprocess,git
tests/behaviors/installed_release/test_scout_manifest_publication.py	subprocess,git
tests/behaviors/integrity_state/test_bug_001_gigs_listing.py	subprocess,git
tests/behaviors/integrity_state/test_project_registry_and_target_binding.py	subprocess,git
tests/behaviors/integrity_state/test_registry_v2_migration.py	subprocess,git
tests/behaviors/integrity_state/test_workpad_private_git.py	subprocess,git
tests/behaviors/runtime_model_boundary/test_g43_provider_run_status.py	subprocess,git,multiprocessing,launch_run,real_process_wait
tests/behaviors/runtime_model_boundary/test_runtime_comparison.py	subprocess
tests/behaviors/runtime_run_authority/test_g13_run.py	subprocess,git,launch_run,real_process_wait
tests/behaviors/runtime_run_authority/test_g14_scheduler.py	subprocess,git,launch_run,real_process_wait
tests/behaviors/runtime_run_authority/test_g16_review_loop.py	launch_run,real_process_wait
tests/behaviors/runtime_run_authority/test_g19_target_effect.py	subprocess,git,launch_run,real_process_wait
tests/behaviors/runtime_run_authority/test_g19_target_effect_contract.py	git
tests/behaviors/runtime_run_authority/test_g21_comparison.py	real_process_wait
tests/behaviors/runtime_run_authority/test_g21_occurrence.py	subprocess,git,real_process_wait
tests/behaviors/runtime_run_authority/test_g23_portability.py	subprocess,git
tests/behaviors/runtime_run_authority/test_g40_runtime.py	subprocess
tests/behaviors/runtime_run_authority/test_g42_catalog.py	subprocess,git
tests/behaviors/runtime_run_authority/test_journal_locking_recovery.py	subprocess,git,multiprocessing
tests/behaviors/runtime_run_authority/test_jsl_closeout_regressions.py	multiprocessing
tests/behaviors/scout_assessment/test_g22_approval.py	subprocess,git
tests/behaviors/scout_assessment/test_g22_http_approval.py	http_server
tests/behaviors/scout_assessment/test_g22_lifecycle.py	subprocess,git
tests/behaviors/scout_assessment/test_g22_proposal_interview.py	http_server
tests/behaviors/scout_assessment/test_g26_review_actions.py	http_server
tests/behaviors/scout_discovery/test_scout07_discovery_run_flow.py	subprocess,git
tests/behaviors/scout_discovery/test_scout07_inventory_members.py	subprocess,git
tests/behaviors/scout_discovery/test_scout07_posting_inputs.py	git
tests/behaviors/scout_find_jobs/test_m1_end_to_end.py	subprocess,git,multiprocessing,http_server
tests/behaviors/scout_find_jobs/test_present_ui.py	http_server
tests/behaviors/scout_proposals_tools/test_scout02_acceptance_negative_paths.py	subprocess,git,launch_run,real_process_wait
tests/behaviors/scout_proposals_tools/test_scout02_graph_set_flow.py	launch_run,real_process_wait
tests/behaviors/scout_proposals_tools/test_scout02_review_corrections.py	subprocess,git
tests/behaviors/scout_proposals_tools/test_scout03_c1_acceptance.py	subprocess,git
tests/behaviors/scout_proposals_tools/test_scout03_c3_inputs.py	subprocess
tests/behaviors/scout_proposals_tools/test_scout03_c3_tools.py	subprocess,git
tests/behaviors/scout_proposals_tools/test_scout03_private_records.py	multiprocessing
tests/behaviors/scout_proposals_tools/test_scout04_input_integration.py	launch_run
tests/behaviors/scout_proposals_tools/test_scout05_bundled_tools.py	subprocess,git
tests/behaviors/scout_proposals_tools/test_scout05_capability_cli.py	subprocess,git
tests/behaviors/scout_proposals_tools/test_scout05_capability_prepare_cli.py	subprocess,git
tests/behaviors/scout_proposals_tools/test_scout05_capability_review.py	subprocess,git
tests/behaviors/scout_proposals_tools/test_scout05_capability_successor.py	subprocess,git
tests/behaviors/scout_proposals_tools/test_scout05_first_proposal.py	subprocess,git
tests/behaviors/scout_proposals_tools/test_scout05_first_version_tools.py	subprocess,git
tests/behaviors/scout_proposals_tools/test_scout05_init.py	subprocess,git
tests/behaviors/scout_proposals_tools/test_scout05_init_corrections.py	subprocess,git
tests/behaviors/scout_proposals_tools/test_scout05_init_recovery.py	subprocess,git
tests/behaviors/scout_proposals_tools/test_scout05_materialization.py	subprocess,git
tests/behaviors/scout_proposals_tools/test_scout05_reviewed_manifest_guard.py	subprocess,git
tests/behaviors/scout_proposals_tools/test_scout05_tool_crud.py	subprocess,git
tests/behaviors/scout_proposals_tools/test_scout05_tool_scaffold.py	subprocess,git
tests/behaviors/scout_proposals_tools/test_scout_proposal_execution.py	subprocess,git,launch_run,real_process_wait
tests/behaviors/scout_proposals_tools/test_scout_proposal_run.py	subprocess,git,launch_run,real_process_wait
tests/behaviors/scout_research/test_scout06_iterative_research_reuse.py	subprocess,git
tests/behaviors/scout_research/test_scout06_legacy_research_reuse.py	subprocess,git
tests/behaviors/scout_research/test_scout06_research_input_integration.py	git
tests/behaviors/scout_research/test_scout06_research_inputs.py	subprocess,git
tests/behaviors/scout_research/test_scout06_research_run_flow.py	subprocess,git
tests/behaviors/scout_tracking_reporting/test_scout09_application_events.py	git
tests/behaviors/scout_tracking_reporting/test_scout_r3_report.py	subprocess,git
tests/behaviors/scout_tracking_reporting/test_scout_r4_journey.py	launch_run,real_process_wait
tests/behaviors/scout_tracking_reporting/test_scout_r5_interview_transfer.py	subprocess,git
tests/behaviors/scout_tracking_reporting/test_scout_r5_transfer_corrections.py	subprocess,git
tests/behaviors/system_contracts/test_g08_offline_create_lifecycle.py	subprocess,git

# total test_*.py files: 159
# heavy files (>=1 signal): 74
# percent: 47%
```

This exactly matches the operator's cited "74/159 (~47%)" and "159 test
files" figures — recounted, not assumed.

## Summary of the four spikes

- **S16** (`docs/development/v0.1.9/spikes/S16-core-gig-decoupling.md`): 45
  strict-pass rows across 10 core files (34 distinct real coupling sites +
  1 docstring false positive + 1 duplicate-import row), 118 broad-pass
  string-mention rows. Proposes a `GigManifest` protocol, entry-points vs.
  bundled-list discovery, ties registration timing to the existing
  `find_jobs/bindings.py:497-541` spawn-child worker-hook precedent (the
  fp2/I-3 lesson), an import-linter-style enforcement test (currently would
  fail with 34 violations — proposed as an allow-list gate), a 7-step
  phased migration, size estimates per category, and risks (pinned digests,
  installed verifiers, pickle/multiprocessing names, the v0.1.7 install
  path).
- **S17** (`docs/development/v0.1.9/spikes/S17-gig-module-structure-classes.md`):
  all 34 top-level `src/gigai/scout/` files + 8 `find_jobs/` files assigned
  to 13 families. Found a byte-identical duplicated private-helper block
  (`research.py:39-141` == `research_v3.py:39-141`, verified via `diff`,
  zero output) and a third partial instance in `tailoring.py:60-99`; 6
  concrete write/read site pairs of the "validate → build → publish →
  read-back" pattern; flagged `proposal_cli.py` as a misnamed non-CLI
  service module. Proposes a core `RecordRepository`/`GigCliGroup` base
  class pair (the S16 registration seam), a before/after sketch for
  `document_records.py`, and explicit "classes don't help" cases
  (`checks.py`, `projection.py`, `find_jobs/contracts.py`, single-function
  CLI modules).
- **S18** (`docs/development/v0.1.9/spikes/S18-scout-interview-prep-graphs.md`):
  read the JEV/TypeSafe evidence doc in full; inventoried existing Scout
  pieces to reuse (`prepare-interview`/`research-role` goal graphs,
  `InterviewGraph` facade, interview/research records, pinned resume and
  requirements-matrix contracts, the loopback-gated `find_jobs` localhost
  API); compared CLI/localhost-API/MCP/Claude-Code-skill agent-integration
  options; mapped `choice`/`score`/`noul` onto question-category/question/
  topic-centrality prediction; adopted JEV's confidence/abstention contract
  verbatim; proposed a new interview-outcome record for calibration
  measurement; flagged Glassdoor/Blind/LeetCode Discuss as scraping/ToS
  risk; proposed a minimal category-level-only first slice.
- **S19** (`docs/development/v0.1.9/spikes/S19-test-suite-diet.md`, added
  mid-dispatch): confirmed 159 test files, 1,209 test functions, 74/159
  (47%) heavy files, 24 installed verifiers — all matching the operator's
  cited numbers exactly except the collected-case count (this checkout:
  1,845 vs. the brief's 1,871 — reported as a discrepancy, not reconciled).
  Found `pyproject.toml`'s marker vocabulary exists but is applied to only
  1/159 files. Compared `test_g03_installed_scenarios.py` against
  `verify_installed_g03.py` in depth (partial overlap, not full
  duplication). Checked CLI-subprocess files for `CliRunner` migration
  candidates and found the sampled cases were legitimate process-boundary
  tests or non-Click standalone scripts, not confirmed candidates. Reported,
  without resolving, an unexplained ~17s-vs-0.14s timing discrepancy on an
  identical pure-unit test file as an open blocker to certifying the
  proposed `make unit-tests` target's under-60s goal. Proposed a 5-packet
  migration plan.

## Non-claims (repo-wide, across all four spikes)

- No production source, schema, test, or Makefile change was made.
- No git add/commit was performed.
- No full test suite was run; S19's timing samples were individual files
  only, per the brief's explicit instruction.
- Every table row in all four spikes cites a file:line actually read; where
  a claim could not be fully verified in the time available (e.g. S16's
  pinned-digest risk, S17's `answer` family's downstream dependencies,
  S19's full g04-g28 overlap table), the spike says so explicitly as an
  open item rather than asserting it.
