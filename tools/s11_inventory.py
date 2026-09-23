"""Build the S11 behavior/lane inventory without importing product code.

The inventory is deliberately source-based.  It records a conservative lane
classification for each test function and each installed verifier, while
leaving the existing pytest collection hook as the authority for its dynamic
``fast_unit``/``integration``/``release`` markers.  Source categories are not
runtime lane claims: test entries receive explicit path/node selectors only,
while installed verifiers retain their explicit commands.  A generated report
is an inventory and selection aid, not proof that every item passed.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tomllib
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOTS = (ROOT / "tests", ROOT / "research/contract_spike/tests", ROOT / "research/phase0_spike/tests")
HISTORICAL_RE = re.compile(r"\b(?:SCOUT[-_]?\d+|SCOUT[-_]?R\d+|G\d{1,2}|BUG[-_]?\d+|S\d{1,2})\b", re.IGNORECASE)
SETUP_NAMES = {
    "tmp_path", "tmpdir", "monkeypatch", "capsys", "capfd", "caplog",
    "subprocess", "CliRunner", "installed_gigai", "scenario_roots",
    "fixture", "resolved", "workpad", "home", "target", "roots",
}
INTEGRATION_NAMES = {
    "tmp_path", "tmpdir", "monkeypatch", "capsys", "capfd", "caplog",
    "subprocess", "multiprocessing", "sqlite", "sqlite3", "socket",
    "http", "httpx", "requests", "urllib", "CliRunner", "Process",
    "Thread", "ScenarioHarness", "run_with_journal_writer", "resolve_workpad",
}
CLI_NAMES = {"CliRunner", "click.testing", "gigai.cli", "application_cli", "answer_cli", "tailor_cli"}
INSTALLED_NAMES = {"InstalledGigAI", "ScenarioHarness", "tests.scenarios", "importlib.metadata", "importlib.resources"}
LIVE_NAMES = {"g30_live", "GIGAI_G30_UAT", "ollama", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}

def _functions(tree: ast.AST) -> Iterable[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            yield node


def _names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


def _arguments(node: ast.AST) -> set[str]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()
    args = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
    return {arg.arg for arg in args} | ({node.args.vararg.arg} if node.args.vararg else set()) | ({node.args.kwarg.arg} if node.args.kwarg else set())


def _imports(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
                modules.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(alias.name for alias in node.names)
            modules.update(alias.asname for alias in node.names if alias.asname)
    return modules


def _historical_ids(text: str) -> list[str]:
    values = {match.upper().replace("_", "-") for match in HISTORICAL_RE.findall(text)}
    return sorted(values, key=lambda value: (value[0], value))


def _marker_name(node: ast.AST) -> str | None:
    """Return a marker name from an actual ``pytest.mark.<name>`` AST node.

    Looking at the AST avoids treating fixture strings, comments, or examples
    such as ``"pytest.mark.g30_live"`` as executable markers.
    """

    if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Attribute):
        return None
    if node.value.attr != "mark":
        return None
    if not isinstance(node.value.value, ast.Name) or node.value.value.id != "pytest":
        return None
    return node.attr


def _module_marker_nodes(tree: ast.AST) -> Iterable[ast.AST]:
    for statement in getattr(tree, "body", []):
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets):
            yield from ast.walk(statement.value)


def _markers(source: str, node: ast.AST | None = None, tree: ast.AST | None = None) -> list[str]:
    """Return actual pytest markers, excluding source-text examples."""

    parsed = tree or ast.parse(source)
    candidates: list[ast.AST] = list(ast.walk(parsed if node is None else node))
    if node is not None:
        candidates.extend(_module_marker_nodes(parsed))
    return sorted({name for candidate in candidates if (name := _marker_name(candidate))})


def _behavior(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("acquisition", "posting", "discovery")):
        return "scout/acquisition-and-discovery"
    if any(token in lowered for token in ("proposal", "research", "tailor", "assessment")):
        return "scout/assessment-and-proposals"
    if any(token in lowered for token in ("application", "tracking", "report", "projection")):
        return "scout/tracking-and-readable-projection"
    if any(token in lowered for token in ("ollama", "model", "provider", "runtime", "invocation")):
        return "runtime/model-boundary"
    if "scheduler" in lowered or "run" in lowered:
        return "runtime/run-and-scheduling"
    if "installed" in lowered or "release" in lowered or "package" in lowered:
        return "release/installed-boundary"
    if "canonical" in lowered or "digest" in lowered:
        return "integrity/canonical-bytes"
    if "bug" in lowered:
        return "regression/bug-fix"
    return "unmapped/needs-feature-owner"


def _levels(path: Path, source: str, tree: ast.AST, node: ast.AST) -> list[str]:
    names = _names(node) | _arguments(node)
    imports = _imports(tree)
    explicit_markers = _markers(source, node, tree)
    lower_names = {name.lower() for name in names}
    levels: list[str] = []
    if any(value.lower() in {name.lower() for name in names | imports} for value in LIVE_NAMES) or "g30_live" in source:
        levels.append("live/provider")
    if any(value.lower() in {name.lower() for name in names | imports} for value in INSTALLED_NAMES) or "installed" in path.name.lower():
        levels.append("installed")
    if any(value.lower() in lower_names for value in CLI_NAMES) or "cli" in path.name.lower() or "cli" in explicit_markers:
        levels.append("cli")
    if any(value.lower() in {name.lower() for name in names | imports} for value in INTEGRATION_NAMES):
        levels.append("integration")
    if not levels:
        levels.append("unit")
    return levels


def _setup_dependencies(path: Path, tree: ast.AST, node: ast.AST) -> list[str]:
    names = _names(node) | _arguments(node) | _imports(tree)
    values = {name for name in names if name in SETUP_NAMES}
    if "tests.scenarios" in names or "ScenarioHarness" in names:
        values.add("installed-scenario-harness")
    if "subprocess" in names:
        values.add("subprocess")
    if any(name in names for name in ("CliRunner", "click.testing")):
        values.add("click-runner")
    if path.name.endswith("_installed_scenarios.py"):
        values.add("installed-package")
    return sorted(values)


def _registered_markers() -> set[str]:
    """Read pytest marker names from the repository's actual configuration."""

    try:
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    configured = config.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("markers", [])
    return {
        str(entry).split(":", 1)[0].strip()
        for entry in configured
        if str(entry).strip()
    }


def _selection_notes(source_categories: list[str]) -> list[str]:
    notes = [
        "source categories are inferred from source and are non-authoritative for runtime markers",
        "path/node selection finds the test but does not guarantee lane isolation, offline safety, or runtime success",
        "tests/conftest.py remains the sole authority for runtime lane markers; this inventory emits no per-entry marker selectors",
    ]
    if "live/provider" in source_categories:
        notes.append("live/provider requires explicit opt-in review; no live lane selector is advertised")
    if "installed" in source_categories:
        notes.append("installed source classification is not a verifier selector; use the explicit installed-verifier command")
    if "cli" in source_categories:
        notes.append("CLI source classification is not a runtime marker claim")
    return notes


def _selection(path: str, nodeids: list[str]) -> list[str]:
    result = [f"pytest {path}"]
    result.extend(f"pytest {nodeid}" for nodeid in nodeids)
    return result


def _test_inventory() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for root in TEST_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("test_*.py")):
            source = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError as error:
                records.append({
                    "kind": "test-file",
                    "path": str(path.relative_to(ROOT)),
                    "status": "collection-error",
                    "error": f"SyntaxError: {error}",
                })
                continue
            relative = str(path.relative_to(ROOT))
            file_tests: list[dict[str, object]] = []
            for node in _functions(tree):
                levels = _levels(path, source, tree, node)
                markers = _markers(source, node, tree)
                nodeid = f"{relative}::{node.name}"
                file_tests.append({
                    "name": node.name,
                    "nodeid": nodeid,
                    "levels": levels,
                    "source_categories": levels,
                    "classification": "source-inferred",
                    "classification_authority": "non-authoritative-for-runtime-markers",
                    "markers": markers,
                    "runtime_selectable_markers": [],
                    "selection": [f"pytest {nodeid}"],
                    "status": "inventory-only",
                })
            source_categories = sorted({level for item in file_tests for level in item["source_categories"]})
            file_markers = _markers(source, tree=tree)
            nodeids = [str(item["nodeid"]) for item in file_tests]
            records.append({
                "kind": "test-file",
                "path": relative,
                "behavior": _behavior(relative),
                "historical_ids": _historical_ids(f"{relative} {source}"),
                "classification": "source-inferred",
                "classification_authority": "non-authoritative-for-runtime-markers",
                "markers": file_markers,
                "source_categories": source_categories,
                "runtime_selectable_markers": [],
                "setup_dependencies": sorted({dependency for node in _functions(tree) for dependency in _setup_dependencies(path, tree, node)}),
                "selection": _selection(relative, nodeids),
                "selection_status": "path-node-only",
                "selection_notes": _selection_notes(source_categories),
                "test_count": len(file_tests),
                "tests": file_tests,
                "status": "inventory-only",
            })
    return records


def _workflow_selectors(filename: str) -> list[str]:
    selectors: list[str] = []
    workflow_root = ROOT / ".github/workflows"
    for path in sorted(workflow_root.glob("*.y*ml")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if filename in line:
                selectors.append(f"{path.relative_to(ROOT)}:{line_number}")
    return selectors


def _verifier_inventory() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    tool_paths = sorted((ROOT / "tools").glob("verify_*.py"))
    for path in tool_paths:
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as error:
            records.append({"kind": "installed-verifier", "path": str(path.relative_to(ROOT)), "status": "syntax-error", "error": str(error)})
            continue
        modules = sorted(_imports(tree))
        relative = str(path.relative_to(ROOT))
        stem = path.stem.removeprefix("verify_")
        behavior = _behavior(stem)
        if stem.startswith("installed_"):
            behavior = _behavior(stem.removeprefix("installed_"))
        levels = ["installed"]
        if "debian" in stem:
            levels.append("end-to-end")
        if "cli" in stem:
            levels.append("cli")
        if "debian" in stem:
            selection = ["python tools/verify_debian_offline.py"]
            selection_status = "explicit-container-command"
            selection_notes = [
                "not selected through pytest markers",
                "requires the Debian offline container/mount contract",
            ]
        else:
            selection = [f".wheel-venv/bin/python {relative}"]
            selection_status = "explicit-installed-wheel-command"
            selection_notes = [
                "not selected through pytest markers",
                "requires a built and installed wheel in .wheel-venv",
            ]
        records.append({
            "kind": "installed-verifier",
            "path": relative,
            "behavior": behavior,
            "levels": levels,
            "source_categories": levels,
            "classification": "source-inferred",
            "classification_authority": "non-authoritative-for-runtime-markers",
            "runtime_selectable_markers": [],
            "historical_ids": _historical_ids(stem),
            "setup_dependencies": [name for name in modules if name in {"subprocess", "httpx", "importlib", "setuptools", "wheel", "pytest"}],
            "selection": selection,
            "selection_status": selection_status,
            "selection_notes": selection_notes,
            "workflow_selectors": _workflow_selectors(path.name),
            "status": "inventory-only",
        })
    return records


def build_report() -> dict[str, object]:
    registered_markers = _registered_markers()
    test_files = _test_inventory()
    verifiers = _verifier_inventory()
    level_counts: Counter[str] = Counter()
    test_items = [test for file in test_files for test in file.get("tests", [])]
    for item in test_items + verifiers:
        for level in item.get("levels", []):
            level_counts[level] += 1
    return {
        "schema_version": "s11-inventory.v2",
        "kind": "gigai-test-and-installed-verifier-inventory",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "registered_pytest_markers": sorted(registered_markers),
        "selection_contract": {
            "source_categories_are_selectable": False,
            "source_classification_authoritative_for_runtime_markers": False,
            "runtime_lane_markers_advertised": [],
            "runtime_marker_authority": "tests/conftest.py",
            "installed_marker_is_not_a_verifier_selector": True,
            "installed_verifiers_use_explicit_commands": True,
        },
        "scope": {
            "test_roots": [str(path.relative_to(ROOT)) for path in TEST_ROOTS if path.is_dir()],
            "verifier_glob": "tools/verify_*.py",
            "classification": "conservative source inventory; source categories are non-authoritative for runtime markers; tests/conftest.py remains the runtime marker authority",
        },
        "counts": {
            "test_files": len(test_files),
            "test_items": len(test_items),
            "installed_verifiers": len(verifiers),
            "by_level": dict(sorted(level_counts.items())),
        },
        "test_files": test_files,
        "installed_verifiers": verifiers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="JSON report path")
    args = parser.parse_args()
    report = build_report()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), "counts": report["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
