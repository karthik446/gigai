"""`gigai gig new NAME [--server process|mount]`: the tree it writes, the pins in it, its refusals, and that the tree
installs as a conformant gig.

"Installed" here never touches an index: the generated project goes on PYTHONPATH with a synthetic dist-info next to
it, either as an editable install (the source tree on the path, a RECORD without the package; `gig check <project>`
then runs the project's tests as item 13) or as a wheel install (the package copied into a site directory and listed in
RECORD; what the D1 validation checks read). Checks run in a subprocess so no generated package enters this process.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tomllib
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.gigs import manifest as gig_manifest
from gigai.gigs.cli import gig_group
from gigai.sdk.process import ServerSpec

CLI = "import sys; from gigai.cli import cli; cli(sys.argv[1:], prog_name='gigai')"
DEFAULT_FILES = [
    "AGENTS.md",
    "README.md",
    "pyproject.toml",
    "src/gigai_demo/__init__.py",
    "src/gigai_demo/cli.py",
    "src/gigai_demo/config.schema.json",
    "src/gigai_demo/docs/commands.yaml",
    "src/gigai_demo/docs/index.md",
    "src/gigai_demo/gig.toml",
    "tests/test_contract.py",
]

# The checks of D1's validate.py (research/gigai-framework/D1), run against an installed generated gig: discovery and
# the manifest read through the distribution's file list without importing the gig, then load, `--help`, `describe`
# == gig.toml, config defaults against the config schema, the shipped docs, and the project's own contract tests.
VALIDATE = """\
import json, subprocess, sys, tomllib
from importlib.metadata import entry_points
from click.testing import CliRunner
from jsonschema import Draft202012Validator

name, package, tests = sys.argv[1:4]
(ep,) = [e for e in entry_points(group="gigai.gigs") if e.name == name]
dist = ep.dist
assert dist is not None
assert package not in sys.modules
(manifest_file,) = [p for p in dist.files if p.name == "gig.toml"]
via_dist = tomllib.loads(dist.locate_file(manifest_file).read_text())
assert package not in sys.modules, "reading gig.toml must not import the gig"
assert via_dist["gig"]["name"] == ep.name
assert via_dist["commands"]["group"] == ep.value
assert dist.version == "0.1.0" and "version" not in via_dist["gig"]

group = ep.load()
assert group.name == ep.name
runner = CliRunner()
assert runner.invoke(group, ["--help"]).exit_code == 0
assert json.loads(runner.invoke(group, ["describe"]).output) == via_dist, "describe JSON must equal gig.toml"

pkg_dir = dist.locate_file(manifest_file).parent
config_schema = json.loads((pkg_dir / via_dist["config"]["schema"]).read_text())
Draft202012Validator(config_schema).validate(via_dist["config"]["defaults"])
for key in ("page", "cli_manual"):
    assert (pkg_dir / via_dist["docs"][key]).is_file(), key

result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", tests], capture_output=True, text=True)
assert result.returncode == 0, result.stdout + result.stderr
print("validated")
"""


def _new(directory: Path, *argv: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(directory)
    return CliRunner().invoke(gig_group, ["new", *argv], prog_name="gigai gig")


@pytest.fixture
def make_gig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``make_gig(name, *options)``: the project ``gigai gig new`` wrote under a fresh directory."""

    def make(name: str = "demo", *options: str) -> Path:
        parent = tmp_path / f"new-{name}-{'-'.join(option.strip('-') for option in options) or 'default'}"
        parent.mkdir()
        result = _new(parent, name, *options, monkeypatch=monkeypatch)
        assert result.exit_code == 0, result.output
        return parent / f"gigai-{name}"

    return make


@pytest.fixture
def scaffold(make_gig) -> Path:
    return make_gig()


def _manifest(project: Path, package: str = "gigai_demo") -> dict:
    return tomllib.loads((project / "src" / package / "gig.toml").read_text(encoding="utf-8"))


def _files(project: Path) -> list[str]:
    return sorted(path.relative_to(project).as_posix() for path in project.rglob("*") if path.is_file())


def _dist_info(site: Path, project: Path, record: list[str]) -> None:
    pyproject = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    info = site / f"{pyproject['name'].replace('-', '_')}-{pyproject['version']}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {pyproject['name']}\nVersion: {pyproject['version']}\n", encoding="utf-8")
    lines = "".join(f"{name} = {value}\n" for name, value in pyproject["entry-points"]["gigai.gigs"].items())
    (info / "entry_points.txt").write_text(f"[gigai.gigs]\n{lines}", encoding="utf-8")
    entries = [*record, f"{info.name}/METADATA", f"{info.name}/entry_points.txt", f"{info.name}/RECORD"]
    (info / "RECORD").write_text("".join(f"{path},,\n" for path in entries), encoding="utf-8")


def _editable(tmp_path: Path, project: Path) -> list[Path]:
    """PYTHONPATH entries of ``project`` installed editable: its ``src/`` and a dist-info that lists no package file."""

    site = tmp_path / f"site-editable-{project.name}"
    _dist_info(site, project, [])
    return [site, project / "src"]


def _wheel(tmp_path: Path, project: Path) -> list[Path]:
    """PYTHONPATH entries of ``project`` installed from a wheel: its package copied into a site dir and in RECORD."""

    site = tmp_path / f"site-wheel-{project.name}"
    (package,) = (project / "src").iterdir()
    shutil.copytree(package, site / package.name)
    _dist_info(site, project, [path.relative_to(site).as_posix() for path in sorted((site / package.name).rglob("*")) if path.is_file()])
    return [site]


def _run(home: Path, pythonpath: list[Path], *argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home), "PYTHONPATH": os.pathsep.join([*map(str, pythonpath), *filter(None, [os.environ.get("PYTHONPATH")])])}
    return subprocess.run([sys.executable, *argv], capture_output=True, text=True, env=env, cwd=cwd, timeout=300, check=False)


def _outcomes(stdout: str) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for line in stdout.splitlines():
        match = re.match(r"^(PASS|FAIL|WARN|SKIP)\s+(\S+)\s", line)
        if match:
            found.setdefault(match[2], set()).add(match[1])
    return found


def test_the_default_tree_is_the_scaffold_with_its_pins(scaffold: Path) -> None:
    assert _files(scaffold) == DEFAULT_FILES
    manifest = _manifest(scaffold)
    assert gig_manifest.manifest_errors(manifest) == []
    assert manifest["gig"]["name"] == "demo"
    assert manifest["gig"]["core"] == ">=0.2,<0.3"
    assert manifest["commands"] == {"group": "gigai_demo.cli:demo", "provides": ["capture", "show"]}
    assert "server" not in manifest
    pyproject = tomllib.loads((scaffold / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["name"] == "gigai-demo"
    assert "gigai>=0.2.0a1,<0.3" in pyproject["project"]["dependencies"]
    assert pyproject["project"]["entry-points"] == {"gigai.gigs": {"demo": "gigai_demo.cli:demo"}}
    assert pyproject["project"]["requires-python"] == manifest["dependencies"]["python"]
    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/gigai_demo"]


def test_the_d1_negative_control_still_fails_the_manifest_schema() -> None:
    bad = {"gig": {"manifest_format": 1, "name": "Bad Name", "description": "x", "core": ">=2"}, "commands": {"group": "a:b"}, "extra": {}}

    assert len(gig_manifest.manifest_errors(bad)) == 2


def test_no_generated_file_declares_a_workpad(scaffold: Path) -> None:
    assert "workpad" not in _manifest(scaffold)["gig"]
    assert [name for name in _files(scaffold) if "workpad" in (scaffold / name).read_text(encoding="utf-8")] == []


def test_a_dashed_name_gives_an_underscored_package_and_group(make_gig) -> None:
    project = make_gig("my-gig")

    manifest = _manifest(project, "gigai_my_gig")
    assert gig_manifest.manifest_errors(manifest) == []
    assert manifest["commands"]["group"] == "gigai_my_gig.cli:my_gig"
    assert "def my_gig() -> None:" in (project / "src" / "gigai_my_gig" / "cli.py").read_text(encoding="utf-8")


def test_workpad_is_an_unknown_option(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _new(tmp_path, "demo", "--workpad", monkeypatch=monkeypatch)

    assert result.exit_code == 2
    assert "No such option '--workpad'" in result.output
    assert list(tmp_path.iterdir()) == []


def test_server_process_writes_a_process_server_table_and_module(make_gig) -> None:
    project = make_gig("demo", "--server", "process")

    manifest = _manifest(project)
    assert gig_manifest.manifest_errors(manifest) == []
    assert manifest["server"] == {
        "mode": "process",
        "start": ["python", "-m", "gigai_demo.server", "--port", "{port}", "--data", "{data}"],
        "health": "/health",
        "mount_path": "/gigs/demo/",
    }
    ServerSpec.from_manifest("demo", manifest, "0.1.0")
    assert _files(project) == sorted([*DEFAULT_FILES, "src/gigai_demo/server.py"])
    assert "ThreadingHTTPServer" in (project / "src" / "gigai_demo" / "server.py").read_text(encoding="utf-8")


def test_server_mount_writes_a_mount_server_table_and_app(make_gig) -> None:
    project = make_gig("demo", "--server", "mount")

    manifest = _manifest(project)
    assert gig_manifest.manifest_errors(manifest) == []
    assert manifest["server"] == {"mode": "mount", "app": "gigai_demo.server:app", "mount_path": "/gigs/demo/"}
    assert "def app(environ, start_response):" in (project / "src" / "gigai_demo" / "server.py").read_text(encoding="utf-8")


def test_an_unknown_server_mode_is_a_usage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _new(tmp_path, "demo", "--server", "thread", monkeypatch=monkeypatch)

    assert result.exit_code == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("Demo", "invalid gig name 'Demo': use 2-32 lowercase letters, digits and '-', starting with a letter"),
        ("d", "invalid gig name 'd': use 2-32 lowercase letters, digits and '-', starting with a letter"),
        ("1demo", "invalid gig name '1demo': use 2-32 lowercase letters, digits and '-', starting with a letter"),
        ("my_gig", "invalid gig name 'my_gig': use 2-32 lowercase letters, digits and '-', starting with a letter"),
        ("a" * 33, f"invalid gig name '{'a' * 33}': use 2-32 lowercase letters, digits and '-', starting with a letter"),
        ("class", "invalid gig name 'class': it is a Python keyword"),
        ("doctor", "'doctor' is a gigai command"),
        ("gig", "'gig' is a gigai command"),
        ("scout", "a gig named 'scout' is already installed"),
        ("tidy", "a gig named 'tidy' is already installed"),
    ],
)
def test_a_name_it_cannot_take_is_refused_with_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, reason: str
) -> None:
    installed = gig_manifest.entry_points()
    tidy = EntryPoint(name="tidy", value="gig_tidy:tidy", group="gigai.gigs")
    monkeypatch.setattr(gig_manifest, "entry_points", lambda: (*installed, tidy))

    result = _new(tmp_path, name, monkeypatch=monkeypatch)

    assert result.exit_code == 2
    assert result.stderr == f"gigai gig new: {reason}\n"
    assert list(tmp_path.iterdir()) == []


def test_an_existing_non_empty_directory_is_refused_and_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "gigai-demo").mkdir()
    (tmp_path / "gigai-demo" / "notes.txt").write_text("mine\n", encoding="utf-8")

    result = _new(tmp_path, "demo", monkeypatch=monkeypatch)

    assert result.exit_code == 2
    assert result.stderr == f"gigai gig new: {tmp_path / 'gigai-demo'} already exists and is not an empty directory\n"
    assert _files(tmp_path / "gigai-demo") == ["notes.txt"]


def test_a_file_in_the_way_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "gigai-demo").write_text("mine\n", encoding="utf-8")

    result = _new(tmp_path, "demo", monkeypatch=monkeypatch)

    assert result.exit_code == 2
    assert (tmp_path / "gigai-demo").read_text(encoding="utf-8") == "mine\n"


def test_an_existing_empty_directory_is_filled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "gigai-demo").mkdir()

    result = _new(tmp_path, "demo", monkeypatch=monkeypatch)

    assert result.exit_code == 0, result.output
    assert _files(tmp_path / "gigai-demo") == DEFAULT_FILES


@pytest.mark.parametrize("options", [(), ("--server", "process"), ("--server", "mount")], ids=["default", "process", "mount"])
def test_the_tree_installed_editable_passes_gig_check(tmp_path: Path, make_gig, options: tuple[str, ...]) -> None:
    project = make_gig("demo", *options)

    result = _run(tmp_path, _editable(tmp_path, project), "-c", CLI, "gig", "check", ".", cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0] == "gigai gig check demo (gigai-demo)"
    outcomes = _outcomes(result.stdout)
    items = ("1", "core", "2", "3", "4", "5", "6", "7", "8", "13")
    assert {item: outcomes.get(item) for item in items} == dict.fromkeys(items, {"PASS"})
    assert result.stdout.splitlines()[-1] == "10 PASS, 0 FAIL, 0 WARN, 0 SKIP"


def test_the_tree_installed_editable_runs_its_verbs(tmp_path: Path, scaffold: Path) -> None:
    pythonpath = _editable(tmp_path, scaffold)

    help_result = _run(tmp_path, pythonpath, "-c", CLI, "demo", "capture", "--help")
    capture = _run(tmp_path, pythonpath, "-c", CLI, "demo", "capture", "--label", "first")
    show = _run(tmp_path, pythonpath, "-c", CLI, "demo", "show")

    assert help_result.returncode == 0, help_result.stderr
    assert "Record a new snapshot" in help_result.stdout
    assert capture.returncode == 0, capture.stderr
    (snapshot,) = (tmp_path / ".gigai" / "gigs" / "demo" / "snapshots").iterdir()
    assert show.returncode == 0, show.stderr
    assert show.stdout == f"{snapshot.stem}  first\n"


def test_the_tree_installed_from_a_wheel_passes_the_d1_validation(tmp_path: Path, scaffold: Path) -> None:
    result = _run(tmp_path, _wheel(tmp_path, scaffold), "-c", VALIDATE, "demo", "gigai_demo", str(scaffold / "tests"))

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "validated\n"
