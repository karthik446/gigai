"""P2: `gigai gig check` items 1-8 and 13 against a synthetic conformant gig, then one broken copy per item.

The gig ("tidy") is laid into a temporary site directory (as in test_loader.py) and checked in a subprocess
with that directory on PYTHONPATH.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from gigai.gigs.check import check_manifest
from gigai.gigs.manifest import read_manifest_file

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "gigs"
CLI = "import sys; from gigai.cli import cli; cli(sys.argv[1:], prog_name='gigai')"

MANIFEST = """\
[gig]
manifest_format = 1
name = "tidy"
description = "Test-only gig: conformant"
core = ">=0.1"

[commands]
group = "gig_tidy:tidy"
provides = ["hello"]

[config]
schema = "config.schema.json"

[config.defaults]
greeting = "hi"

[docs]
page = "docs/index.md"
cli_manual = "docs/commands.yaml"
"""

MODULE = '''\
import json
import tomllib
from importlib.resources import files

import click


@click.group("tidy")
def tidy():
    """Test-only conformant gig."""


@tidy.command("hello")
def hello():
    """Say hello."""
    click.echo("hello")


@tidy.command("describe", hidden=True)
def describe():
    """Print gig.toml as JSON."""
    click.echo(json.dumps(tomllib.loads(files("gig_tidy").joinpath("gig.toml").read_text(encoding="utf-8"))))
'''

CONFIG_SCHEMA = json.dumps(
    {"type": "object", "properties": {"greeting": {"type": "string"}}, "additionalProperties": False}
)
CLI_MANUAL = json.dumps({"commands": {"tidy hello": {"summary": "Say hello.", "examples": ["gigai tidy hello"]}}})


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


def _run(tmp_path: Path, site: Path, *argv: str, path: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(tmp_path), "PYTHONPATH": os.pathsep.join(filter(None, [str(site), os.environ.get("PYTHONPATH")]))}
    if path is not None:
        env["PATH"] = os.pathsep.join([str(path), env.get("PATH", "")])
    return subprocess.run([sys.executable, "-c", CLI, *argv], capture_output=True, text=True, env=env, timeout=300, check=False)


def _tidy_files(**replace: str) -> dict[str, str]:
    files = {
        "gig_tidy/__init__.py": MODULE,
        "gig_tidy/gig.toml": MANIFEST,
        "gig_tidy/config.schema.json": CONFIG_SCHEMA,
        "gig_tidy/docs/index.md": "# tidy\n",
        "gig_tidy/docs/commands.yaml": CLI_MANUAL,
    }
    files.update({f"gig_tidy/{name}": text for name, text in replace.items()})
    return files


def _add_tidy(site: Path, *, entry_points: dict[str, str] | None = None, **replace: str) -> None:
    _add_dist(site, "gigai-test-tidy", "0.1.0", entry_points or {"tidy": "gig_tidy:tidy"}, _tidy_files(**replace))


def _outcomes(stdout: str) -> dict[str, set[str]]:
    """``{item: {PASS, FAIL, ...}}`` from the check report."""

    found: dict[str, set[str]] = {}
    for line in stdout.splitlines():
        match = re.match(r"^(PASS|FAIL|WARN|SKIP)\s+(\S+)\s", line)
        if match:
            found.setdefault(match[2], set()).add(match[1])
    return found


@pytest.fixture
def site(tmp_path: Path) -> Path:
    directory = tmp_path / "site"
    directory.mkdir()
    return directory


def test_a_conformant_gig_passes_items_1_to_8(tmp_path: Path, site: Path) -> None:
    _add_tidy(site)

    result = _run(tmp_path, site, "gig", "check", "tidy")

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0] == "gigai gig check tidy (gigai-test-tidy)"
    outcomes = _outcomes(result.stdout)
    assert {item: outcomes[item] for item in ("1", "2", "3", "4", "5", "6", "7", "8")} == dict.fromkeys(
        ("1", "2", "3", "4", "5", "6", "7", "8"), {"PASS"}
    )
    assert outcomes["13"] == {"SKIP"}
    assert result.stdout.splitlines()[-1] == "9 PASS, 0 FAIL, 0 WARN, 1 SKIP"


def test_checking_the_installed_manifest_file_runs_the_full_check(tmp_path: Path, site: Path) -> None:
    _add_tidy(site)

    result = _run(tmp_path, site, "gig", "check", str(site / "gig_tidy" / "gig.toml"))

    assert result.returncode == 0, result.stdout + result.stderr
    assert _outcomes(result.stdout)["4"] == {"PASS"}


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.toml")), ids=lambda path: path.name)
def test_every_top_level_fixture_manifest_checks_clean(tmp_path: Path, site: Path, path: Path) -> None:
    result = _run(tmp_path, site, "gig", "check", str(path))

    assert result.returncode == 0, result.stdout + result.stderr
    assert _outcomes(result.stdout)["2"] == {"SKIP"}


def test_a_manifest_that_declares_a_workpad_fails_the_check(tmp_path: Path, site: Path) -> None:
    result = _run(tmp_path, site, "gig", "check", str(FIXTURES / "invalid" / "workpad-true.toml"))

    assert result.returncode == 1
    assert "FAIL     1  schema: gig: Additional properties are not allowed ('workpad' was unexpected)" in result.stdout


def test_an_executable_gig_without_a_description_gets_the_describe_cost_warning() -> None:
    document = read_manifest_file(FIXTURES / "incompatible-core.toml")
    del document["gig"]["description"]
    document["commands"] = {"executable": "gigai-far-future"}

    results = check_manifest(document, installed="3.0.0")

    assert [(result.outcome, result.message) for result in results if result.outcome == "WARN"] == [
        ("WARN", "no [gig] description: every `gigai --help` runs `gigai-far-future describe` to get one")
    ]


BROKEN = {
    "2-name": (
        {"gig.toml": MANIFEST.replace('name = "tidy"', 'name = "tidier"')},
        "2",
        "entry point name 'tidy' != [gig] name 'tidier'",
    ),
    "4-missing-verb": (
        {"gig.toml": MANIFEST.replace('provides = ["hello"]', 'provides = ["hello", "wave"]')},
        "4",
        "provides 'wave' but the group has no such command",
    ),
    "4-no-help": (
        {"__init__.py": MODULE.replace('    """Say hello."""\n', "")},
        "4",
        "'hello' has no help text",
    ),
    "4-cannot-load": (
        {"__init__.py": "raise ImportError('broken on purpose')\n"},
        "4",
        "gig_tidy:tidy cannot load: ImportError: broken on purpose",
    ),
    "5-core-verb-listed": (
        {"gig.toml": MANIFEST.replace('provides = ["hello"]', 'provides = ["hello", "status"]')},
        "5",
        "[commands] provides lists core verb(s) status: core adds them to every gig",
    ),
    "6-visible-describe": (
        {"__init__.py": MODULE.replace('"describe", hidden=True', '"describe"')},
        "6",
        "`describe` is not hidden",
    ),
    "6-describe-differs": (
        {"__init__.py": MODULE.replace('read_text(encoding="utf-8"))))', 'read_text(encoding="utf-8")) | {"extra": 1}))')},
        "6",
        "`describe` JSON differs from gig.toml",
    ),
    "7-defaults": (
        {"gig.toml": MANIFEST.replace('greeting = "hi"', "greeting = 3")},
        "7",
        "[config.defaults] greeting: 3 is not of type 'string'",
    ),
    "7-no-schema-file": (
        {"gig.toml": MANIFEST.replace("config.schema.json", "missing.json")},
        "7",
        "[config] schema 'missing.json' is not a file in the gig's package",
    ),
    "8-no-page": (
        {"gig.toml": MANIFEST.replace("docs/index.md", "docs/missing.md")},
        "8",
        "[docs] page 'docs/missing.md' is not a file in the gig's package",
    ),
    "8-manual-misses-a-verb": (
        {"docs/commands.yaml": json.dumps({"commands": {"tidy other": {}}})},
        "8",
        "[docs] cli_manual 'docs/commands.yaml' has no entry for: hello",
    ),
    "8-manual-not-json": (
        {"docs/commands.yaml": "commands:\n  tidy hello: {}\n"},
        "8",
        "[docs] cli_manual 'docs/commands.yaml': not JSON-compatible YAML",
    ),
}


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_each_broken_item_fails_the_check(tmp_path: Path, site: Path, case: str) -> None:
    replace, item, message = BROKEN[case]
    _add_tidy(site, **replace)

    result = _run(tmp_path, site, "gig", "check", "tidy")

    assert result.returncode == 1, result.stdout + result.stderr
    assert any(line.startswith("FAIL") and f" {item}  {message}" in line for line in result.stdout.splitlines()), result.stdout


def test_two_entry_points_in_one_dist_fail_item_2(tmp_path: Path, site: Path) -> None:
    _add_tidy(site, entry_points={"tidy": "gig_tidy:tidy", "neat": "gig_tidy:tidy"})

    result = _run(tmp_path, site, "gig", "check", "tidy")

    assert result.returncode == 1
    assert "gigai-test-tidy declares 2 gigai.gigs entry points, exactly one allowed: neat = gig_tidy:tidy, tidy = gig_tidy:tidy" in result.stdout


def test_a_core_verb_defined_in_code_is_reported_as_an_override(tmp_path: Path, site: Path) -> None:
    module = MODULE + '\n\n@tidy.command("status")\ndef status():\n    """Tidy status."""\n'
    manual = json.dumps({"commands": {"tidy hello": {}, "tidy status": {}}})
    _add_tidy(site, **{"__init__.py": module, "docs/commands.yaml": manual})

    result = _run(tmp_path, site, "gig", "check", "tidy")

    assert result.returncode == 0, result.stdout
    assert "WARN     5  override: status" in result.stdout


def test_the_gigs_own_tests_run_when_its_project_is_next_to_the_package(tmp_path: Path, site: Path) -> None:
    _add_tidy(site)
    (site / "pyproject.toml").write_text('[project]\nname = "gigai-test-tidy"\nversion = "0.1.0"\n', encoding="utf-8")
    (site / "tests").mkdir()
    (site / "tests" / "test_tidy.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    passing = _run(tmp_path, site, "gig", "check", "tidy")
    (site / "tests" / "test_tidy.py").write_text("def test_bad():\n    assert False\n", encoding="utf-8")
    failing = _run(tmp_path, site, "gig", "check", "tidy")

    assert passing.returncode == 0, passing.stdout
    assert re.search(r"^PASS\s+13  .*tests: 1 passed", passing.stdout, re.M), passing.stdout
    assert failing.returncode == 1
    assert re.search(r"^FAIL\s+13  .*tests: pytest exited 1: .*1 failed", failing.stdout, re.M), failing.stdout


def _executable_gig(site: Path, bin_dir: Path, *, describe: str) -> None:
    manifest = MANIFEST.split("\n[config]", 1)[0].replace('group = "gig_tidy:tidy"', 'executable = "gigai-tidy"')
    _add_dist(site, "gigai-test-tidy", "0.1.0", {"tidy": "gig_tidy"}, {"gig_tidy/__init__.py": "", "gig_tidy/gig.toml": manifest})
    bin_dir.mkdir()
    script = bin_dir / "gigai-tidy"
    payload = describe or json.dumps(read_manifest_file(site / "gig_tidy" / "gig.toml"))
    script.write_text(
        "#!/bin/sh\n"
        f"if [ \"$1\" = describe ]; then cat <<'EOF'\n{payload}\nEOF\nexit 0; fi\n"
        'case "$1" in --help|hello) exit 0;; esac\nexit 2\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)


def test_an_executable_gig_is_checked_through_its_executable(tmp_path: Path, site: Path) -> None:
    _executable_gig(site, tmp_path / "bin", describe="")

    result = _run(tmp_path, site, "gig", "check", "tidy", path=tmp_path / "bin")

    assert result.returncode == 0, result.stdout + result.stderr
    outcomes = _outcomes(result.stdout)
    assert (outcomes["4"], outcomes["6"]) == ({"PASS"}, {"PASS"})


def test_an_executable_gig_whose_describe_differs_fails_item_6(tmp_path: Path, site: Path) -> None:
    _executable_gig(site, tmp_path / "bin", describe="{}")

    result = _run(tmp_path, site, "gig", "check", "tidy", path=tmp_path / "bin")

    assert result.returncode == 1
    assert "FAIL     6  `describe` JSON differs from gig.toml" in result.stdout


def test_a_gig_is_checked_by_its_distribution_name_too(tmp_path: Path, site: Path) -> None:
    _add_tidy(site)

    result = _run(tmp_path, site, "gig", "check", "Gigai_Test.Tidy")

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0] == "gigai gig check tidy (gigai-test-tidy)"


def test_checking_an_unknown_gig_names_it(tmp_path: Path, site: Path) -> None:
    result = _run(tmp_path, site, "gig", "check", "nope")

    assert result.returncode == 1
    assert "no installed gig named 'nope'" in result.stderr


@pytest.mark.parametrize(("module", "watched"), [
    ("gig_tidy", ["gig_tidy"]),
    ("gig_tidy.cli", ["gig_tidy.cli", "gig_tidy"]),
    ("acme.sub.cli", ["acme.sub.cli", "acme.sub", "acme"]),
    ("gigai.scout.gig", ["gigai.scout.gig", "gigai.scout"]),
])
def test_item_3_watches_the_gigs_module_package_and_top_package_but_never_core_gigai(module: str, watched: list[str]) -> None:
    # Reading any manifest imports `gigai` (the loader lives there), so a gig inside the gigai dist must not be
    # judged by its top-level package; any other gig's top-level package is its own code.
    from gigai.gigs.check import _gig_modules

    assert _gig_modules(module) == watched


def test_the_scout_gig_the_gigai_dist_ships_checks_without_a_fail(tmp_path: Path, site: Path) -> None:
    result = _run(tmp_path, site, "gig", "check", "scout")

    assert result.returncode == 0, result.stdout + result.stderr
    outcomes = _outcomes(result.stdout)
    assert {item: outcomes[item] for item in ("1", "2", "3", "4", "6")} == dict.fromkeys(("1", "2", "3", "4", "6"), {"PASS"})
    assert "PASS     3  manifest read without importing 'gigai.scout'" in result.stdout
    assert " 0 FAIL, " in result.stdout.splitlines()[-1]
