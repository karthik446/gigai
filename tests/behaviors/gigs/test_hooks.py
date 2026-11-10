"""P3a: core reaches a gig's objects through named hooks (`gigai.sdk.hooks`), never by importing the gig.

A hook resolves to a `module:attribute` target: a row of the built-in Scout bridge, else the `[hooks]` table
of the providing gig's gig.toml. Gigs are "installed" the way test_loader.py does it: package files plus a
`.dist-info` folder in a temporary directory on the path. The fixture dists are `tests/fixtures/gigs/<name>/`.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tomllib
from importlib import import_module
from pathlib import Path

import pytest

from gigai.sdk import hooks

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "gigs"
SRC = Path(hooks.__file__).resolve().parents[1]
# The ten core modules P3a moved off direct Scout imports.
CORE_MODULES = (
    "gigai.external_recording",
    "gigai.default_init",
    "gigai.capability_review",
    "gigai.capability_successor",
    "gigai.private_transfer",
    "gigai.application_cli",
    "gigai.application_events",
    "gigai.diagnostics",
    "gigai.native_records",
    "gigai.portability",
)


def _add_dist(site: Path, name: str, version: str, entry_points: dict[str, str], files: dict[str, str]) -> None:
    for relative, text in files.items():
        target = site / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8")
    lines = "".join(f"{entry} = {value}\n" for entry, value in entry_points.items())
    (info / "entry_points.txt").write_text(f"[gigai.gigs]\n{lines}", encoding="utf-8")
    record = [*files, f"{info.name}/METADATA", f"{info.name}/entry_points.txt", f"{info.name}/RECORD"]
    (info / "RECORD").write_text("".join(f"{path},,\n" for path in record), encoding="utf-8")


def _add_fixture(site: Path, fixture: str) -> None:
    """Install `tests/fixtures/gigs/<fixture>` from its own pyproject.toml (entry points, packages)."""

    root = FIXTURES / fixture
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    files = {}
    for package in project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]:
        for path in sorted((root / package).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                files[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    entry_points = dict(project["project"]["entry-points"]["gigai.gigs"])
    _add_dist(site, project["project"]["name"], project["project"]["version"], entry_points, files)


def _add_declaring_gig(site: Path, gig: str, hooks_table: str) -> None:
    """A minimal gig `gig` whose gig.toml carries `hooks_table` as its `[hooks]` body."""

    package = f"gig_{gig}"
    manifest = (
        f'[gig]\nmanifest_format = 1\nname = "{gig}"\ndescription = "Test-only gig"\ncore = ">=0.2.0a1"\n\n'
        f'[commands]\ngroup = "{package}:{gig}"\n\n[hooks]\n{hooks_table}'
    )
    files = {f"{package}/__init__.py": "", f"{package}/gig.toml": manifest}
    _add_dist(site, f"gigai-test-gig-{gig}", "0.1.0", {gig: f"{package}:{gig}"}, files)


def _python(code: str, site: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if site is not None:
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(site), os.environ.get("PYTHONPATH")]))
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120, check=False)


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A temporary site directory on this process's path, with the hook declarations re-read around the test."""

    directory = tmp_path / "site"
    directory.mkdir()
    monkeypatch.syspath_prepend(str(directory))
    hooks._declarations.cache_clear()
    yield directory
    hooks._declarations.cache_clear()
    for module in [name for name in sys.modules if name.startswith("gig_")]:
        del sys.modules[module]


def test_a_hook_a_gig_declares_resolves_to_its_target_and_is_called(site):
    _add_fixture(site, "hookgig")

    doctor = hooks.get("doctor", gig="hookgig")

    checks = import_module("gig_hookgig.checks")
    assert doctor is checks.doctor
    assert doctor("home") == [{"id": "hookgig.ok", "status": "pass"}]
    assert checks.calls == [(("home",), {})]


def test_reading_declarations_runs_no_gig_code(site):
    _add_fixture(site, "hookgig")

    assert hooks.declared("doctor") == {"hookgig": "gig_hookgig.checks:doctor"}
    assert not [name for name in sys.modules if name.startswith("gig_hookgig")]


def test_a_gig_that_declares_no_hooks_is_skipped(site):
    _add_fixture(site, "hello")
    _add_fixture(site, "hookgig")

    assert hooks.declared("doctor") == {"hookgig": "gig_hookgig.checks:doctor"}
    with pytest.raises(hooks.HookUnavailable, match="gig 'hello' provides no hook 'doctor'"):
        hooks.get("doctor", gig="hello")


def test_an_unknown_hook_is_unavailable(site):
    with pytest.raises(hooks.HookUnavailable, match="gig 'scout' provides no hook 'no_such_hook'"):
        hooks.get("no_such_hook")
    assert issubclass(hooks.HookUnavailable, LookupError)


def test_a_missing_target_raises_what_the_import_statement_would(site):
    """external_recording tells "Scout is not installed" apart by `ModuleNotFoundError.name`; a hook keeps that."""

    _add_declaring_gig(site, "absent", 'doctor = "gig_absent_module.checks:doctor"\n')
    _add_declaring_gig(site, "noattr", 'doctor = "gig_noattr:doctor"\n')

    with pytest.raises(ModuleNotFoundError) as missing_module:
        hooks.get("doctor", gig="absent")
    assert missing_module.value.name == "gig_absent_module"
    with pytest.raises(ImportError, match="cannot import name 'doctor' from 'gig_noattr'") as missing_name:
        hooks.get("doctor", gig="noattr")
    assert missing_name.value.name == "gig_noattr"
    assert not isinstance(missing_name.value, ModuleNotFoundError)


def test_every_bridge_row_is_the_scout_object_the_import_named():
    for name, target in hooks._BRIDGE.items():
        module, _, attribute = target.partition(":")
        assert module.startswith("gigai.scout."), name
        assert hooks.get(name) is getattr(import_module(module), attribute), name


def test_a_bridge_row_wins_over_any_installed_gig_and_scans_none(site):
    """An installed dist named `scout` (the loader never mounts it: `scout` is a built-in command) replaces nothing."""

    _add_declaring_gig(site, "scout", 'normalize_posting_url = "gig_scout:normalize_url"\n')

    from gigai.scout.find_jobs.contracts import normalize_url

    assert hooks.get("normalize_posting_url") is normalize_url
    assert hooks._declarations.cache_info().currsize == 0


def test_the_bridge_imports_scout_only_when_a_hook_is_asked_for():
    result = _python(
        "import sys; from gigai.sdk import hooks; before = 'gigai.scout' in sys.modules; "
        "hooks.get('normalize_posting_url'); "
        "print(before, 'gigai.scout.find_jobs.contracts' in sys.modules)"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["False", "True"]


def test_the_ten_core_modules_no_longer_import_scout_at_import_time():
    """Every import statement executed while the ten modules load, attributed to the module that ran it.

    `gigai.scout` still ends up in `sys.modules`: `external_recording` imports `gigai.run`, and `gigai.run`
    imports `gigai.scout.find_jobs.contracts` (P3b's part). The claim tested is that the ten files cause no
    Scout import themselves.
    """

    code = f"""
import builtins, importlib.util, json, sys
seen = []
real_import = builtins.__import__

def spy(name, globals=None, locals=None, fromlist=(), level=0):
    importer = (globals or {{}}).get("__name__")
    base = importlib.util.resolve_name("." * level + name, (globals or {{}}).get("__package__")) if level else name
    targets = [base, *(f"{{base}}.{{item}}" for item in fromlist or ())]
    if any(target == "gigai.scout" or target.startswith("gigai.scout.") for target in targets):
        seen.append([importer, base])
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = spy
for module in {list(CORE_MODULES)!r}:
    importlib.import_module(module)
exec("from gigai.scout import tools", {{"__name__": "control"}})  # proves the spy attributes imports
print(json.dumps(seen))
"""
    result = _python(code)

    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout)
    assert [pair for pair in seen if pair[0] in CORE_MODULES] == []
    assert ["control", "gigai.scout"] in seen


def test_the_ten_core_modules_have_no_scout_import_statement_anywhere():
    """Also the imports inside functions, which run only on use."""

    found = []
    for module in CORE_MODULES:
        path = SRC / f"{module.removeprefix('gigai.')}.py"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = "gigai." * bool(node.level) + (node.module or "")
                names = [base, *(f"{base.rstrip('.')}.{alias.name}" for alias in node.names)]
            else:
                continue
            if any(name == "gigai.scout" or name.startswith("gigai.scout.") for name in names):
                found.append(f"{path.name}:{node.lineno}")

    assert found == []
