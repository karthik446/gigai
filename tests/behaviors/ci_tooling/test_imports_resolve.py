"""uat-bug-021 prevention: every import under ``src/gigai`` must resolve.

The defect this guards against: ``market_acquisition._read_resume_text_for_rank``
held ``from ..private_records import read_record`` inside a function. That
resolved to ``gigai.scout.private_records``, which does not exist; the
``ModuleNotFoundError`` was raised on every call and swallowed by a blind
``except Exception`` for three weeks. No test imported the name, and the
project runs no linter or type checker, so nothing looked.

What is checked, for every ``.py`` under ``src/gigai``:

* every ``import x`` / ``from x import y`` / relative ``from ..x import y``,
  at any depth (module level, inside functions, inside ``try``, inside
  ``if TYPE_CHECKING:``), names a module that exists;
* for ``from pkg import name`` where ``pkg.name`` is not itself a module,
  ``name`` is defined by ``pkg``'s source (top-level definitions, imports and
  ``__all__``).

How, without running any GigAI code: ``gigai.*`` modules are resolved against
the scanned source files themselves, never through ``sys.modules``, so the
answer is about the tree on disk and not about whatever is installed. Every
other module goes through ``importlib.util.find_spec``, which imports parent
packages only. Names are checked by reading the target's source with ``ast``.

Not checked (each is counted and printed, never silently dropped):

* a third-party or stdlib import that does not resolve AND sits in a ``try``
  whose handlers name ``ImportError`` / ``ModuleNotFoundError``: that is an
  optional dependency. A ``gigai.*`` import gets no such pass, and neither
  does an import guarded only by ``except Exception``;
* names imported from a module with no readable source (builtin, extension)
  or one that defines names dynamically (module ``__getattr__``).

Files are found by walking ``src/gigai`` rather than by ``git ls-files``: the
fast_unit lane (tests/conftest.py) excludes any test file that shells out,
and the walk also covers a new file before it is first committed.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
import importlib.util
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"
FIRST_PARTY = "gigai"
_SKIPPED_DIRECTORIES = frozenset({"__pycache__", "node_modules"})
_OPTIONAL_GUARDS = frozenset({"ImportError", "ModuleNotFoundError"})
_IMPLICIT_MODULE_NAMES = frozenset(
    {
        "__name__", "__doc__", "__file__", "__path__", "__package__",
        "__spec__", "__loader__", "__builtins__", "__dict__",
    }
)


@dataclass(frozen=True)
class ImportSite:
    """One imported module, as written at one place in one file."""

    path: str
    line: int
    statement: str
    module: str | None
    level: int
    names: tuple[str, ...]
    is_from: bool
    optional: bool


@dataclass
class Report:
    files: int = 0
    statements: int = 0
    names_verified: int = 0
    problems: list[str] = field(default_factory=list)
    skipped_optional: list[str] = field(default_factory=list)
    unverified_names: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        unverified: dict[str, int] = {}
        for module, name in self.unverified_names:
            key = f"{module}.{name}"
            unverified[key] = unverified.get(key, 0) + 1
        lines = [
            f"scanned {self.files} files, {self.statements} imported modules, "
            f"{self.names_verified} imported names verified against source",
            f"optional imports skipped ({len(self.skipped_optional)}):",
            *(f"  {entry}" for entry in self.skipped_optional),
            f"names not verifiable from source ({len(self.unverified_names)} imports; "
            "the module has no readable source or defines names at import time):",
            *(f"  {key} ({count} imports)" for key, count in sorted(unverified.items())),
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reading the tree
# ---------------------------------------------------------------------------


def load_sources(src_root: Path) -> dict[str, str]:
    """Every ``.py`` under ``<src_root>/gigai``, keyed by its posix path
    relative to ``src_root`` (``gigai/scout/profile_records.py``)."""

    sources: dict[str, str] = {}
    for path in sorted((src_root / FIRST_PARTY).rglob("*.py")):
        relative = path.relative_to(src_root)
        if _SKIPPED_DIRECTORIES.intersection(relative.parts):
            continue
        sources[relative.as_posix()] = path.read_text(encoding="utf-8")
    return sources


def _module_for_path(path: str, sources: Mapping[str, str]) -> tuple[str, bool] | None:
    """``(dotted name, is a package)`` for a source file, or ``None`` when some
    directory above it has no ``__init__.py`` (a script, not a module)."""

    parts = path.split("/")
    is_package = parts[-1] == "__init__.py"
    directories = parts[:-1]
    for depth in range(1, len(directories) + 1):
        if "/".join([*directories[:depth], "__init__.py"]) not in sources:
            return None
    names = directories if is_package else [*directories, parts[-1][: -len(".py")]]
    return ".".join(names), is_package


def _first_party_path(module: str, sources: Mapping[str, str]) -> str | None:
    base = module.replace(".", "/")
    for candidate in (f"{base}/__init__.py", f"{base}.py"):
        if candidate in sources:
            return candidate
    return None


def _is_first_party(module: str) -> bool:
    return module == FIRST_PARTY or module.startswith(f"{FIRST_PARTY}.")


# ---------------------------------------------------------------------------
# Collecting imports, with the guard each one sits under
# ---------------------------------------------------------------------------


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    caught = handler.type
    if caught is None:
        return set()
    members = caught.elts if isinstance(caught, ast.Tuple) else [caught]
    names: set[str] = set()
    for member in members:
        if isinstance(member, ast.Name):
            names.add(member.id)
        elif isinstance(member, ast.Attribute):
            names.add(member.attr)
    return names


def _walk_imports(node: ast.AST, optional: bool) -> Iterator[tuple[ast.stmt, bool]]:
    """Yield every import statement under ``node`` with whether it sits in a
    ``try`` body guarded by ``ImportError`` / ``ModuleNotFoundError``."""

    if isinstance(node, (ast.Import, ast.ImportFrom)):
        yield node, optional
        return
    if isinstance(node, (ast.Try, ast.TryStar)):
        guarded = optional or any(
            _handler_names(handler) & _OPTIONAL_GUARDS for handler in node.handlers
        )
        for child in node.body:
            yield from _walk_imports(child, guarded)
        for child in (*node.handlers, *node.orelse, *node.finalbody):
            yield from _walk_imports(child, optional)
        return
    for child in ast.iter_child_nodes(node):
        yield from _walk_imports(child, optional)


def collect_imports(path: str, source: str) -> list[ImportSite]:
    tree = ast.parse(source, filename=path)
    sites: list[ImportSite] = []
    for node, optional in _walk_imports(tree, False):
        statement = ast.unparse(node)
        if isinstance(node, ast.Import):
            for alias in node.names:
                sites.append(
                    ImportSite(path, node.lineno, statement, alias.name, 0, (), False, optional)
                )
        elif isinstance(node, ast.ImportFrom):
            names = tuple(alias.name for alias in node.names)
            sites.append(
                ImportSite(path, node.lineno, statement, node.module, node.level, names, True, optional)
            )
    return sites


def resolve_module_name(site: ImportSite, sources: Mapping[str, str]) -> tuple[str | None, str]:
    """The absolute module an import names, or ``(None, why not)``."""

    if site.level == 0:
        return site.module, ""
    located = _module_for_path(site.path, sources)
    if located is None:
        return None, "is a relative import in a file that is not inside a package"
    name, is_package = located
    package = name.split(".") if is_package else name.split(".")[:-1]
    if site.level - 1 >= len(package):
        return None, f"climbs above the top-level package (from {name})"
    base = package[: len(package) - (site.level - 1)]
    return ".".join([*base, *(site.module.split(".") if site.module else [])]), ""


# ---------------------------------------------------------------------------
# Does a module exist, and what does it define
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _find_spec(module: str):
    try:
        return importlib.util.find_spec(module)
    except (ImportError, ValueError, AttributeError):
        return None


@lru_cache(maxsize=1)
def _stdlib_directory() -> Path:
    return Path(ast.__file__).resolve().parent


def _external_source_path(module: str) -> Path | None:
    spec = _find_spec(module)
    if spec is None:
        return None
    origin = spec.origin
    if origin and origin.endswith(".py") and Path(origin).is_file():
        return Path(origin)
    # Frozen stdlib modules (os, codecs, ...) report origin "frozen", and an
    # alias (collections.abc is _collections_abc) reports the real name.
    for name in dict.fromkeys((module, spec.name)):
        base = _stdlib_directory() / Path(*name.split("."))
        for candidate in (base / "__init__.py", base.with_suffix(".py")):
            if candidate.is_file():
                return candidate
    return None


class Modules:
    """Module existence and defined names over one source tree."""

    def __init__(self, sources: Mapping[str, str]) -> None:
        self.sources = sources
        self._names: dict[str, tuple[frozenset[str], bool]] = {}

    def exists(self, module: str) -> bool:
        if _is_first_party(module):
            return _first_party_path(module, self.sources) is not None
        return _find_spec(module) is not None

    def near_misses(self, module: str) -> list[str]:
        """First-party modules with the same last name: the likely intent."""

        leaf = module.rsplit(".", 1)[-1]
        found = set()
        for path in self.sources:
            located = _module_for_path(path, self.sources)
            if located is not None and located[0].rsplit(".", 1)[-1] == leaf:
                found.add(located[0])
        return sorted(found)

    def _source(self, module: str) -> tuple[str, str] | None:
        if _is_first_party(module):
            path = _first_party_path(module, self.sources)
            return None if path is None else (path, self.sources[path])
        external = _external_source_path(module)
        if external is None:
            return None
        try:
            return str(external), external.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None

    def defined_names(self, module: str) -> tuple[frozenset[str], bool]:
        """``(names, complete)``: the names ``module`` defines at top level as
        far as its source shows, and whether that is all of them. It is not
        all of them when there is no source to read (builtin, extension), when
        the module answers ``__getattr__``, or when it star-imports from a
        module like that."""

        if module not in self._names:
            self._names[module] = (frozenset(), False)  # cycle guard for star imports
            self._names[module] = self._defined_names(module)
        return self._names[module]

    def _defined_names(self, module: str) -> tuple[frozenset[str], bool]:
        located = self._source(module)
        if located is None:
            return frozenset(), False
        path, source = located
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError:
            return frozenset(), False
        names: set[str] = set(_IMPLICIT_MODULE_NAMES)
        stars: list[ast.ImportFrom] = []
        for statement in _top_level_statements(tree):
            _collect_definitions(statement, names, stars)
        complete = "__getattr__" not in names
        is_package = path.endswith("__init__.py")
        for star in stars:
            package = module.split(".") if is_package else module.split(".")[:-1]
            if star.level - 1 >= len(package):
                complete = False
                continue
            if star.level:
                package = package[: len(package) - (star.level - 1)]
                target = ".".join([*package, *(star.module.split(".") if star.module else [])])
            else:
                target = star.module or ""
            inherited, inherited_complete = self.defined_names(target)
            names.update(inherited)
            complete = complete and inherited_complete
        return frozenset(names), complete


def _top_level_statements(node: ast.AST) -> Iterator[ast.stmt]:
    """Statements that run at import time: the module body and the bodies of
    its ``if`` / ``try`` / ``with`` / loops, never a function or class body."""

    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield child
        elif isinstance(child, ast.stmt):
            yield child
            yield from _top_level_statements(child)
        elif isinstance(child, (ast.ExceptHandler, ast.match_case)):
            yield from _top_level_statements(child)


def _target_names(target: ast.AST) -> Iterator[str]:
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from _target_names(element)
    elif isinstance(target, ast.Starred):
        yield from _target_names(target.value)


def _collect_definitions(statement: ast.stmt, names: set[str], stars: list[ast.ImportFrom]) -> None:
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.add(statement.name)
    elif isinstance(statement, ast.Assign):
        for target in statement.targets:
            names.update(_target_names(target))
            if isinstance(target, ast.Name) and target.id == "__all__":
                names.update(_string_elements(statement.value))
    elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
        names.update(_target_names(statement.target))
        if (
            isinstance(statement.target, ast.Name)
            and statement.target.id == "__all__"
            and statement.value is not None
        ):
            names.update(_string_elements(statement.value))
    elif isinstance(statement, (ast.For, ast.AsyncFor)):
        names.update(_target_names(statement.target))
    elif isinstance(statement, (ast.With, ast.AsyncWith)):
        for item in statement.items:
            if item.optional_vars is not None:
                names.update(_target_names(item.optional_vars))
    elif isinstance(statement, ast.Import):
        for alias in statement.names:
            names.add(alias.asname or alias.name.split(".", 1)[0])
    elif isinstance(statement, ast.ImportFrom):
        for alias in statement.names:
            if alias.name == "*":
                stars.append(statement)
            else:
                names.add(alias.asname or alias.name)


def _string_elements(value: ast.AST) -> Iterator[str]:
    for child in ast.walk(value):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            yield child.value


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


def check_tree(sources: Mapping[str, str]) -> Report:
    report = Report(files=len(sources))
    modules = Modules(sources)
    for path in sorted(sources):
        for site in collect_imports(path, sources[path]):
            report.statements += 1
            _check_site(site, modules, report)
    return report


def _check_site(site: ImportSite, modules: Modules, report: Report) -> None:
    where = f"src/{site.path}:{site.line}: `{site.statement}`"
    module, why_not = resolve_module_name(site, modules.sources)
    if module is None:
        report.problems.append(f"{where} {why_not}")
        return
    if not modules.exists(module):
        if site.optional and not _is_first_party(module):
            report.skipped_optional.append(f"{where} ({module} is not installed)")
            return
        hint = ""
        if _is_first_party(module):
            nearby = [name for name in modules.near_misses(module) if name != module]
            if nearby:
                hint = f"; did you mean {' or '.join(nearby)}?"
        report.problems.append(
            f"{where} names module {module}, which does not exist{hint}"
        )
        return
    if not site.is_from:
        return
    for name in site.names:
        if name == "*" or modules.exists(f"{module}.{name}"):
            continue
        defined, complete = modules.defined_names(module)
        if name in defined:
            report.names_verified += 1
        elif not complete:
            report.unverified_names.append((module, name))
        else:
            report.problems.append(
                f"{where} imports `{name}`, which {module} does not define "
                "(not a submodule, not a top-level name, not in __all__)"
            )


@lru_cache(maxsize=1)
def _live_report() -> Report:
    return check_tree(load_sources(SRC_ROOT))


def _failure(report: Report, what: str) -> str:
    return (
        f"{len(report.problems)} {what} under src/gigai "
        "(uat-bug-021: an import that cannot resolve raises on every call and is "
        "easily swallowed by a blind except):\n  " + "\n  ".join(report.problems)
    )


# ---------------------------------------------------------------------------
# The live tree
# ---------------------------------------------------------------------------


def test_every_import_under_src_gigai_names_a_module_that_exists() -> None:
    report = _live_report()
    print(report.summary())
    assert report.files > 100, f"scanned only {report.files} files under {SRC_ROOT}"
    missing = [problem for problem in report.problems if "does not define" not in problem]
    assert not missing, _failure(
        Report(problems=missing), "import(s) do not resolve to a module"
    )


def test_every_name_imported_from_a_module_is_defined_by_it() -> None:
    report = _live_report()
    assert report.names_verified > 100, report.summary()
    undefined = [problem for problem in report.problems if "does not define" in problem]
    assert not undefined, _failure(
        Report(problems=undefined), "imported name(s) are not defined by their module"
    )


# ---------------------------------------------------------------------------
# The checker itself, on in-memory trees shaped like the defect
# ---------------------------------------------------------------------------

_PACKAGES = {
    "gigai/__init__.py": "",
    "gigai/private_records.py": "def read_record(target, reference):\n    return None\n",
    "gigai/scout/__init__.py": "",
    "gigai/scout/find_jobs/__init__.py": "",
}


def _tree(body: str) -> dict[str, str]:
    return {**_PACKAGES, "gigai/scout/find_jobs/market_acquisition.py": body}


def test_the_uat_bug_021_import_is_reported_with_its_line_and_the_likely_module() -> None:
    report = check_tree(
        _tree(
            "def _read_resume_text_for_rank(resolved):\n"
            "    try:\n"
            "        from ..private_records import read_record\n"
            "        return read_record(resolved, 'resume')\n"
            "    except Exception:\n"
            "        return None\n"
        )
    )

    assert report.problems == [
        "src/gigai/scout/find_jobs/market_acquisition.py:3: "
        "`from ..private_records import read_record` names module "
        "gigai.scout.private_records, which does not exist; "
        "did you mean gigai.private_records?"
    ]


def test_the_corrected_import_passes() -> None:
    report = check_tree(
        _tree(
            "def _read_resume_text_for_rank(resolved):\n"
            "    from ...private_records import read_record\n"
            "    return read_record(resolved, 'resume')\n"
        )
    )

    assert report.problems == []
    assert report.names_verified == 1


def test_a_name_the_module_does_not_define_is_reported() -> None:
    report = check_tree(
        _tree(
            "def _read(resolved):\n"
            "    from ...private_records import import_reference\n"
            "    return import_reference(resolved)\n"
        )
    )

    assert report.problems == [
        "src/gigai/scout/find_jobs/market_acquisition.py:2: "
        "`from ...private_records import import_reference` imports "
        "`import_reference`, which gigai.private_records does not define "
        "(not a submodule, not a top-level name, not in __all__)"
    ]


def test_a_submodule_and_a_re_exported_name_both_count_as_defined() -> None:
    sources = _tree(
        "from .. import find_jobs\n"
        "from ... import private_records\n"
        "from ...reexport import read_record, listed_only\n"
    )
    sources["gigai/reexport.py"] = (
        "from .private_records import *\n__all__ = ['listed_only']\n"
    )

    report = check_tree(sources)

    assert report.problems == []


def test_an_import_under_type_checking_is_checked() -> None:
    report = check_tree(
        _tree(
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from ..workpad_types import Resolved\n"
        )
    )

    assert report.problems == [
        "src/gigai/scout/find_jobs/market_acquisition.py:3: "
        "`from ..workpad_types import Resolved` names module "
        "gigai.scout.workpad_types, which does not exist"
    ]


def test_only_an_import_error_guard_makes_a_third_party_import_optional() -> None:
    report = check_tree(
        _tree(
            "try:\n"
            "    import gigai_no_such_distribution_a\n"
            "except ModuleNotFoundError:\n"
            "    gigai_no_such_distribution_a = None\n"
            "try:\n"
            "    import gigai_no_such_distribution_b\n"
            "except (OSError, ImportError):\n"
            "    gigai_no_such_distribution_b = None\n"
            "try:\n"
            "    import gigai_no_such_distribution_c\n"
            "except Exception:\n"
            "    gigai_no_such_distribution_c = None\n"
        )
    )

    assert [entry.split(": ", 1)[1] for entry in report.skipped_optional] == [
        "`import gigai_no_such_distribution_a` (gigai_no_such_distribution_a is not installed)",
        "`import gigai_no_such_distribution_b` (gigai_no_such_distribution_b is not installed)",
    ]
    assert report.problems == [
        "src/gigai/scout/find_jobs/market_acquisition.py:10: "
        "`import gigai_no_such_distribution_c` names module "
        "gigai_no_such_distribution_c, which does not exist"
    ]


def test_an_import_error_guard_does_not_excuse_a_missing_first_party_module() -> None:
    report = check_tree(
        _tree(
            "try:\n"
            "    from .. import profile_records\n"
            "except ImportError:\n"
            "    profile_records = None\n"
        )
    )

    assert report.skipped_optional == []
    assert report.problems == [
        "src/gigai/scout/find_jobs/market_acquisition.py:2: "
        "`from .. import profile_records` imports `profile_records`, which "
        "gigai.scout does not define "
        "(not a submodule, not a top-level name, not in __all__)"
    ]


def test_a_relative_import_that_leaves_the_package_is_reported() -> None:
    sources = _tree("from ..... import cli\n")
    sources["gigai/scout/data/gig.py"] = "from . import sibling\n"

    report = check_tree(sources)

    assert report.problems == [
        "src/gigai/scout/data/gig.py:1: `from . import sibling` "
        "is a relative import in a file that is not inside a package",
        "src/gigai/scout/find_jobs/market_acquisition.py:1: `from ..... import cli` "
        "climbs above the top-level package (from gigai.scout.find_jobs.market_acquisition)",
    ]


def test_stdlib_and_installed_names_are_checked_against_their_source() -> None:
    report = check_tree(
        _tree(
            "from pathlib import Path, NoSuchPathKind\n"
            "from collections.abc import Mapping\n"
            "import json.decoder\n"
            "import json.no_such_submodule\n"
        )
    )

    assert report.problems == [
        "src/gigai/scout/find_jobs/market_acquisition.py:1: "
        "`from pathlib import Path, NoSuchPathKind` imports `NoSuchPathKind`, "
        "which pathlib does not define "
        "(not a submodule, not a top-level name, not in __all__)",
        "src/gigai/scout/find_jobs/market_acquisition.py:4: "
        "`import json.no_such_submodule` names module json.no_such_submodule, "
        "which does not exist",
    ]


if __name__ == "__main__":
    # Point the same check at another tree (a scratch copy, a checkout of an
    # older commit): python tests/behaviors/ci_tooling/test_imports_resolve.py <src dir>
    chosen = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC_ROOT
    outcome = check_tree(load_sources(chosen))
    print(outcome.summary())
    print(f"problems ({len(outcome.problems)}):")
    for line in outcome.problems:
        print(f"  {line}")
    raise SystemExit(1 if outcome.problems else 0)
