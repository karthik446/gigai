"""The tests of the files a push changed: the PR lane's selection (0.1.11.1).

    python tools/ci_select_tests.py --diff BASE HEAD [--max-tests 500]    (files, one per line)
    make test-changed BASE=<sha>

The PR run and the release pre-check run the same cut-down lane (lint, the fast unit lane, the wheel, the
core-flow smoke). On a PR push the lane adds the test files that IMPORT a changed module, found with the
AST (imports inside function bodies included), one hop through `tests/support`. A transitive closure is
useless here: `gigai.cli` and `gigai.workpad` are imported, directly or not, by most of the suite.

Prints nothing (and says why on stderr) when the selection cannot be trusted or is too big for the lane:
`pyproject.toml`, `uv.lock`, `tests/conftest.py`, `Makefile` or `tools/run_ci_tests.py` changed, or the
selected files hold more than --max-tests tests. The nightly `full` run covers those.
Standard library only: it runs before `uv sync`.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

ALL = "ALL"
ALL_PATTERNS = ("pyproject.toml", "uv.lock", "tests/conftest.py", "tests/__init__.py", "Makefile", "tools/run_ci_tests.py")
NON_CODE_SUFFIXES = (".md", ".markdown", ".txt")
NON_CODE_PREFIXES = ("docs/", "gigai-docs/", ".orchestrator/", ".claude/")


def module_name(path: Path, root: Path) -> str | None:
    rel = path.relative_to(root)
    if rel.parts[0] == "src":
        parts = list(rel.parts[1:])
    elif rel.parts[0] == "tests":
        parts = list(rel.parts)
    else:
        return None
    parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imports_of(path: Path, name: str | None) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeError):
        return set()
    found: set[str] = set()
    pkg = (name or "").split(".")
    for node in ast.walk(tree):  # includes imports inside function bodies
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level and name:
                parts = name.split(".") if path.name == "__init__.py" else name.split(".")[:-1]
                keep = parts[: len(parts) - (node.level - 1)]
                base = ".".join([*keep, base]) if base else ".".join(keep)
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


class Graph:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.mods: dict[str, Path] = {}
        for sub in ("src/gigai", "tests"):
            for path in (root / sub).rglob("*.py"):
                name = module_name(path, root)
                if name:
                    self.mods[name] = path
        self.edges: dict[str, set[str]] = {}
        for name, path in self.mods.items():
            self.edges[name] = {m for m in imports_of(path, name) if m in self.mods}
        self.rev: dict[str, set[str]] = {}
        for src, dsts in self.edges.items():
            for dst in dsts:
                self.rev.setdefault(dst, set()).add(src)
        self.tests = {n for n, p in self.mods.items() if p.name.startswith("test_")}
        self.text_cache: dict[Path, str] = {}

    def text(self, path: Path) -> str:
        if path not in self.text_cache:
            try:
                self.text_cache[path] = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                self.text_cache[path] = ""
        return self.text_cache[path]

    def dependents(self, name: str, transitive: bool) -> set[str]:
        seen: set[str] = set()
        todo = [name]
        while todo:
            cur = todo.pop()
            for up in self.rev.get(cur, ()):
                if up not in seen:
                    seen.add(up)
                    # direct: stop at test files and tests/support helpers (one hop through a helper)
                    if transitive or not up.startswith("gigai."):
                        todo.append(up)
        return seen

    def select(self, changed: list[str], mode: str = "direct") -> list[str] | str:
        transitive = mode == "transitive"
        chosen: set[str] = set()
        for rel in changed:
            if rel in ALL_PATTERNS:
                return ALL
            if rel.endswith(NON_CODE_SUFFIXES) and not rel.startswith("src/"):
                continue
            if rel.startswith(NON_CODE_PREFIXES):
                continue
            path = self.root / rel
            if rel.startswith("tests/") and path.name.startswith("test_") and rel.endswith(".py"):
                chosen.add(module_name(path, self.root) or "")
                continue
            if rel.endswith(".py"):
                name = module_name(path, self.root) if path.exists() else None
                if name is None and not path.exists():
                    # deleted or moved module: use the path form
                    name = rel[4:-3].replace("/", ".") if rel.startswith("src/") else rel[:-3].replace("/", ".")
                if rel.startswith(("src/gigai/", "tests/")) and name:
                    chosen |= {m for m in self.dependents(name, transitive) if m in self.tests}
                    if name in self.tests:
                        chosen.add(name)
                    continue
                if rel.startswith("tools/"):
                    stem = Path(rel).stem
                    hits = {n for n in self.tests if f"tools.{stem}" in self.text(self.mods[n]) or f"tools/{stem}" in self.text(self.mods[n])}
                    chosen |= hits
                    continue
            # data file, prompt, schema, static, UI dist, workflow, container: tests that name it
            base = Path(rel).name
            if rel.startswith("src/gigai/scout/ui/"):
                # the bundle and its sources: the browser tests, and whatever names the dist folder
                chosen |= {n for n in self.tests if n.startswith("tests.ui.") or "ui/dist" in self.text(self.mods[n])}
                continue
            hits = {n for n in self.tests if base in self.text(self.mods[n])}
            if not hits and rel.startswith("src/gigai/"):
                # a data file is read by a module that names it: that module's tests
                stem = Path(rel).stem
                readers = {m for m, p in self.mods.items() if m.startswith("gigai.") and (base in self.text(p) or stem in self.text(p))}
                hits = {t for m in readers for t in self.dependents(m, False) if t in self.tests} | {n for n in self.tests if stem in self.text(self.mods[n])}
            if not hits and rel.startswith("src/gigai/"):
                folder = "/".join(Path(rel).parts[2:-1])
                if folder:
                    hits = {n for n in self.tests if folder in self.text(self.mods[n])}
            if not hits and rel.startswith("src/gigai/"):
                # fall back to the tests of the owning package directory
                owner = ".".join(Path(rel).parts[1:-1])
                hits = {t for m in self.mods if m.startswith(owner + ".") for t in self.dependents(m, False) if t in self.tests}
            if not hits and rel.startswith(("src/", ".github/", "containers/", "tools/")):
                return ALL
            chosen |= hits
        return sorted(self.mods[n].relative_to(self.root).as_posix() for n in chosen if n in self.mods)


def count_tests(path: Path) -> int:
    """Test functions in a file (a parametrized test counts once: a lower bound)."""

    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.lstrip().startswith(("def test_", "async def test_")))
    except OSError:
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--diff", nargs=2, metavar=("BASE", "HEAD"), required=True)
    ap.add_argument("--max-tests", type=int, default=500)
    ns = ap.parse_args()
    root = Path(ns.root).resolve()
    proc = subprocess.run(["git", "-C", str(root), "diff", "--name-only", *ns.diff], capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"ci_select_tests: git diff {ns.diff[0]} {ns.diff[1]} failed: {proc.stderr.strip()}", file=sys.stderr)
        return 0
    result = Graph(root).select(proc.stdout.split(), "direct")
    if result == ALL:
        print("ci_select_tests: a change that no test file can be mapped to (pyproject.toml, uv.lock, conftest, Makefile or an unmapped source file): no selection; the wheel, core-flow and nightly cover it", file=sys.stderr)
        return 0
    total = sum(count_tests(root / name) for name in result)
    if total > ns.max_tests:
        print(f"ci_select_tests: {len(result)} files, about {total} tests: over the {ns.max_tests} the lane allows; no selection (the nightly runs everything)", file=sys.stderr)
        return 0
    print(f"ci_select_tests: {len(result)} files, about {total} tests", file=sys.stderr)
    print("\n".join(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
