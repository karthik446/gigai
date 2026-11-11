"""P2: the gig manifest schema, the `[gig] core` range, `gigai gig list | info`, and the `gigai workpad` renames.

Installed gigs are laid into a temporary site directory (a `.dist-info` with entry_points.txt and RECORD next
to the package), as in test_loader.py, and the CLI runs in a subprocess with that directory on PYTHONPATH.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib import resources
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.gigs import manifest as gig_manifest
from gigai.gigs.manifest import core_problem, manifest_errors, read_manifest_file

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "gigs"
CLI = "import sys; from gigai.cli import cli; cli(sys.argv[1:], prog_name='gigai')"
VALID_FIXTURES = sorted(FIXTURES.glob("*.toml"))


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


def _run(tmp_path: Path, site: Path, *argv: str, code: str = CLI) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(tmp_path), "PYTHONPATH": os.pathsep.join(filter(None, [str(site), os.environ.get("PYTHONPATH")]))}
    return subprocess.run([sys.executable, "-c", code, *argv], capture_output=True, text=True, env=env, timeout=120, check=False)


def _manifest(name: str, core: str, *, extra: str = "") -> str:
    module = f"gig_{name.replace('-', '_')}"
    return (
        f'[gig]\nmanifest_format = 1\nname = "{name}"\ndescription = "Test-only gig {name}"\ncore = "{core}"\n{extra}\n'
        f'[commands]\ngroup = "{module}:{module}"\nprovides = ["run"]\n'
    )


def _add_gig(site: Path, name: str, manifest: str | None, *, entry_name: str | None = None) -> None:
    """A gig whose module raises on import, so any test that lists or inspects it proves it was not imported."""

    module = f"gig_{name.replace('-', '_')}"
    files = {f"{module}/__init__.py": "raise ImportError('a listed gig must not be imported')\n"}
    if manifest is not None:
        files[f"{module}/gig.toml"] = manifest
    _add_dist(site, f"gigai-test-{name}", "1.2.3", {entry_name or name: f"{module}:{module}"}, files)


@pytest.fixture
def site(tmp_path: Path) -> Path:
    directory = tmp_path / "site"
    directory.mkdir()
    return directory


# --- the schema ---------------------------------------------------------------------------------------


def test_the_schema_ships_as_package_data_and_drops_workpad() -> None:
    schema = json.loads((resources.files("gigai.schemas") / "gig-manifest.schema.json").read_text(encoding="utf-8"))

    assert schema["$id"] == "https://gigai.dev/schemas/gig-manifest/1.json"
    assert "workpad" not in schema["properties"]["gig"]["properties"]
    assert "evals" in schema["properties"]


@pytest.mark.parametrize("path", VALID_FIXTURES, ids=lambda path: path.name)
def test_every_top_level_fixture_manifest_is_valid(path: Path) -> None:
    assert manifest_errors(read_manifest_file(path)) == []


def test_a_manifest_that_declares_a_workpad_is_invalid() -> None:
    errors = manifest_errors(read_manifest_file(FIXTURES / "invalid" / "workpad-true.toml"))

    assert errors == ["gig: Additional properties are not allowed ('workpad' was unexpected)"]


def test_a_manifest_without_a_description_is_invalid() -> None:
    document = read_manifest_file(FIXTURES / "scout.toml")
    del document["gig"]["description"]

    assert manifest_errors(document) == ["gig: 'description' is a required property"]


# --- the core range -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("installed", "problem"),
    [
        ("0.2.0a1", None),
        ("0.2.0a2", None),
        ("0.2.0", None),
        ("0.2.7.post1", None),
        ("0.1.11.13", "needs gigai >=0.2,<0.3, you have 0.1.11.13"),
        ("0.3.0a1", "needs gigai >=0.2,<0.3, you have 0.3.0a1"),
        ("0.3.0", "needs gigai >=0.2,<0.3, you have 0.3.0"),
    ],
)
def test_a_pre_release_of_a_version_in_range_is_compatible(installed: str, problem: str | None) -> None:
    assert core_problem(">=0.2,<0.3", installed) == problem


@pytest.mark.parametrize(
    ("core", "installed", "compatible"),
    [
        (">=0.2.0a1", "0.2.0a1", True),
        ("~=0.2.0", "0.2.5", True),
        ("~=0.2.0", "0.3.0", False),
        ("==0.2.*", "0.2.0a2", True),
        ("==0.2.*", "0.20.0", False),
        ("!=0.2.1", "0.2.1", False),
        (">=3", "0.2.0a1", False),
    ],
)
def test_core_ranges_follow_pep_440(core: str, installed: str, compatible: bool) -> None:
    assert (core_problem(core, installed) is None) is compatible


@pytest.mark.parametrize("core", ["2", ">=two", ">=0.2,,<0.3", "~=1"])
def test_a_malformed_core_range_raises(core: str) -> None:
    with pytest.raises(ValueError):
        core_problem(core, "0.2.0a1")


@pytest.mark.parametrize(
    ("installed", "status"), [("0.2.0a1", "ok"), ("0.2.0a2", "ok"), ("0.3.0", "ok"), ("0.4.0a1", "incompatible")]
)
def test_gig_list_status_follows_the_installed_core(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, installed: str, status: str
) -> None:
    site = tmp_path / "site"
    _add_gig(site, "ranged", _manifest("ranged", ">=0.2,<0.4"))
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.setattr(gig_manifest, "installed_core_version", lambda: installed)

    (gig,) = [gig for gig in gig_manifest.discover() if gig.name == "ranged"]

    assert gig.status(installed)[0] == status
    result = CliRunner().invoke(cli, ["gig", "info", str(FIXTURES / "scout.toml")])
    assert result.exit_code == 0, result.output
    assert f"  status: {status}" in result.stdout


# --- gig list / gig info --------------------------------------------------------------------------------


def test_gig_list_reports_each_status_without_importing_any_gig(tmp_path: Path, site: Path) -> None:
    _add_gig(site, "fine", _manifest("fine", ">=0.1"))
    _add_gig(site, "future", _manifest("future", ">=3"))
    _add_gig(site, "stale", _manifest("stale", ">=0.1", extra="workpad = true\n"))
    _add_gig(site, "bare", None)
    _add_gig(site, "taken", _manifest("taken", ">=0.1"), entry_name="status")

    result = _run(tmp_path, site, "gig", "list", "--json")

    assert result.returncode == 0, result.stderr
    assert '"status": "incompatible"' in result.stdout
    rows = {row["name"]: row for row in json.loads(result.stdout)["gigs"]}
    assert rows.pop("scout")["dist"] == "gigai"  # the gig gigai ships (P3c); its status follows the core version
    assert {name: rows[name]["status"] for name in rows} == {
        "bare": "cannot load",
        "fine": "ok",
        "future": "incompatible",
        "stale": "invalid",
        "status": "not mounted",
    }
    assert rows["future"]["detail"].startswith("needs gigai >=3, you have ")
    assert rows["stale"]["detail"] == "gig: Additional properties are not allowed ('workpad' was unexpected)"
    assert rows["status"]["detail"] == "'status' is a gigai command"
    assert rows["fine"] | {"detail": None} == {
        "name": "fine",
        "dist": "gigai-test-fine",
        "version": "1.2.3",
        "entry_point": "gig_fine:gig_fine",
        "description": "Test-only gig fine",
        "core": ">=0.1",
        "mounted": True,
        "status": "ok",
        "detail": None,
    }

    human = _run(tmp_path, site, "gig", "list")
    assert human.returncode == 0, human.stderr
    assert human.stdout.splitlines()[0] == "NAME  STATUS  VERSION  DIST  DETAIL"
    assert "fine  ok  1.2.3  gigai-test-fine  Test-only gig fine" in human.stdout


def test_gig_list_with_no_installed_gigs_lists_only_the_gigs_gigai_ships(tmp_path: Path, site: Path) -> None:
    result = _run(tmp_path, site, "gig", "list")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "NAME  STATUS  VERSION  DIST  DETAIL"
    assert [line.split("  ")[0::3] for line in lines[1:]] == [["scout", "gigai"]]


def test_gig_info_shows_an_installed_gig_without_importing_it(tmp_path: Path, site: Path) -> None:
    extra = 'version = "1.2.3"\n'
    manifest = _manifest("future", ">=3", extra=extra) + (
        '\n[[keys]]\nservice = "openrouter"\nenv = "OPENROUTER_API_KEY"\nrequired = false\n'
        '\n[data]\ndirs = ["runs", "notes"]\n'
        '\n[docs]\npage = "docs/index.md"\n'
        '\n[permissions]\nnetwork = ["openrouter.ai"]\n'
    )
    _add_gig(site, "future", manifest)

    result = _run(tmp_path, site, "gig", "info", "future")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "future 1.2.3 (gigai-test-future)"
    assert lines[1] == "  Test-only gig future"
    assert lines[2].startswith("  status: incompatible (needs gigai >=3, you have ")
    assert "  core: >=3" in lines
    assert "  commands: group gig_future:gig_future" in lines
    assert "  provides: run" in lines
    assert "  keys: openrouter (env OPENROUTER_API_KEY, optional)" in lines
    assert "  data dirs: runs, notes" in lines
    assert "  docs page: docs/index.md" in lines
    assert "  permissions (network): openrouter.ai" in lines


def test_gig_info_reads_a_manifest_file(tmp_path: Path, site: Path) -> None:
    result = _run(tmp_path, site, "gig", "info", str(FIXTURES / "incompatible-core.toml"))

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "far-future (manifest file, not installed)"
    assert "needs gigai >=3, you have " in result.stdout


def test_gig_info_names_an_unknown_gig(tmp_path: Path, site: Path) -> None:
    result = _run(tmp_path, site, "gig", "info", "nope")

    assert result.returncode == 1
    assert "no installed gig named 'nope'; `gigai gig list` shows the installed gigs" in result.stderr


# --- the workpad renames ----------------------------------------------------------------------------------


def test_gig_help_lists_its_verbs() -> None:
    result = CliRunner().invoke(cli, ["gig", "--help"])

    assert result.exit_code == 0, result.output
    commands = result.stdout.split("Commands:\n", 1)[1]
    assert [line.split()[0] for line in commands.splitlines() if line.strip()] == ["check", "info", "install", "list", "new", "uninstall", "update"]


def test_workpad_owns_list_and_use_and_the_old_spellings_are_hidden() -> None:
    workpad = CliRunner().invoke(cli, ["workpad", "--help"])
    root = CliRunner().invoke(cli, ["--help"])

    commands = workpad.stdout.split("Commands:\n", 1)[1]
    assert [line.split()[0] for line in commands.splitlines() if line.strip()] == ["list", "path", "use"]
    assert not [line for line in root.stdout.splitlines() if line.strip().startswith("gigs ")]


def _unbound(tmp_path: Path) -> tuple[str, str]:
    target = tmp_path / "target"
    target.mkdir()
    return str(tmp_path / "home"), str(target)


def test_gigs_prints_one_deprecation_line_then_behaves_like_workpad_list(tmp_path: Path) -> None:
    home, target = _unbound(tmp_path)
    runner = CliRunner()

    old = runner.invoke(cli, ["gigs", "--home", home, "--target", target])
    new = runner.invoke(cli, ["workpad", "list", "--home", home, "--target", target])

    assert old.stdout == "`gigai gigs` is deprecated; use `gigai workpad list`.\n" + new.stdout
    assert (old.exit_code, old.stderr) == (new.exit_code, new.stderr)
    assert old.stdout.count("deprecated") == 1


def test_gigs_json_keeps_stdout_json_and_warns_on_stderr(tmp_path: Path) -> None:
    home, target = _unbound(tmp_path)
    runner = CliRunner()

    old = runner.invoke(cli, ["gigs", "--home", home, "--target", target, "--json"])
    new = runner.invoke(cli, ["workpad", "list", "--home", home, "--target", target, "--json"])

    assert old.stdout == new.stdout
    json.loads(old.stdout)
    assert old.stderr == "`gigai gigs` is deprecated; use `gigai workpad list`.\n" + new.stderr
    assert old.exit_code == new.exit_code


def test_gig_use_prints_one_deprecation_line_then_behaves_like_workpad_use(tmp_path: Path) -> None:
    home, target = _unbound(tmp_path)
    runner = CliRunner()
    argv = ["gig_00000000-0000-4000-8000-000000000000", "--home", home, "--target", target, "--json"]

    old = runner.invoke(cli, ["gig", "use", *argv])
    new = runner.invoke(cli, ["workpad", "use", *argv])

    assert old.stdout == new.stdout
    assert json.loads(old.stdout)["error"]["code"] == json.loads(new.stdout)["error"]["code"]
    assert old.stderr == "`gigai gig use` is deprecated; use `gigai workpad use`.\n" + new.stderr
    assert old.exit_code == new.exit_code != 0
