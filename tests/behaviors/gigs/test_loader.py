"""P1: gigs found through `gigai.gigs` entry points mount lazily as `gigai <gig>`.

Each test "installs" gigs without a venv: it lays the gig package and a `.dist-info` folder (METADATA,
entry_points.txt, RECORD) into a temporary directory and puts that directory on the path, which is all
`importlib.metadata` needs to find the distribution. The fixture dists are `tests/fixtures/gigs/<name>/`.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from gigai.gigs.loader import LazyGig, mount_gigs
from gigai.sdk import GigContext

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "gigs"
CLI = "import sys; from gigai.cli import cli; cli(sys.argv[1:], prog_name='gigai')"
# The gigs the gigai dist itself ships (P3c); they mount first on any group, before the installed ones.
SHIPPED = ("scout",)


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


def _add_fixture(site: Path, fixture: str, *, entry_name: str | None = None) -> None:
    """Install `tests/fixtures/gigs/<fixture>` from its own pyproject.toml (entry points, packages)."""

    root = FIXTURES / fixture
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    entry_points = dict(project["project"]["entry-points"]["gigai.gigs"])
    if entry_name is not None:
        entry_points = {entry_name: value for value in entry_points.values()}
    files = {}
    for package in project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]:
        for path in sorted((root / package).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                files[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    _add_dist(site, project["project"]["name"], project["project"]["version"], entry_points, files)


def _run(
    tmp_path: Path, site: Path, *argv: str, code: str = CLI, path: Path | None = None, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(tmp_path), "PYTHONPATH": os.pathsep.join(filter(None, [str(site), os.environ.get("PYTHONPATH")]))}
    if path is not None:
        env["PATH"] = os.pathsep.join([str(path), env.get("PATH", "")])
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-c", code, *argv], capture_output=True, text=True, env=env, timeout=120, check=False)


def _complete(tmp_path: Path, site: Path, words: str) -> subprocess.CompletedProcess:
    """What bash completion runs on Tab after `words` (click's `_GIGAI_COMPLETE` protocol)."""

    cword = str(len(words.split()) - (0 if words.endswith(" ") else 1))
    return _run(tmp_path, site, env_extra={"_GIGAI_COMPLETE": "bash_complete", "COMP_WORDS": words, "COMP_CWORD": cword})


def _add_malformed_entry_points(site: Path) -> None:
    """An unrelated dist whose entry_points.txt has a line without `=` (importlib.metadata raises on it)."""

    info = site / "unrelated_tool-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: unrelated-tool\nVersion: 1.0\n", encoding="utf-8")
    (info / "entry_points.txt").write_text("[console_scripts]\nbadline\n", encoding="utf-8")


@pytest.fixture
def site(tmp_path: Path) -> Path:
    directory = tmp_path / "site"
    directory.mkdir()
    return directory


@pytest.fixture
def mounted_site(site: Path, monkeypatch: pytest.MonkeyPatch):
    """`site` on this process's path; any gig module imported by the test is forgotten afterwards."""

    monkeypatch.syspath_prepend(str(site))
    yield site
    for module in [name for name in sys.modules if name.startswith("gig_")]:
        del sys.modules[module]


def test_discovery_and_root_help_never_import_the_gig(tmp_path, site):
    _add_fixture(site, "hello")
    code = (
        "import json, sys\n"
        "from click.testing import CliRunner\n"
        "import gigai.cli\n"
        "after_import = 'gig_hello' in sys.modules\n"
        "result = CliRunner().invoke(gigai.cli.cli, ['--help'], prog_name='gigai')\n"
        "print(json.dumps({'after_import': after_import, 'after_help': 'gig_hello' in sys.modules,\n"
        "                  'exit_code': result.exit_code, 'help': result.output,\n"
        "                  'mounted': type(gigai.cli.cli.commands.get('hello')).__name__}))\n"
    )
    completed = _run(tmp_path, site, code=code)
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["after_import"] is False
    assert report["after_help"] is False
    assert report["exit_code"] == 0
    assert report["mounted"] == "LazyGig"
    help_line = next(line for line in report["help"].splitlines() if line.strip().startswith("hello "))
    assert "Test-only gig: says hello" in help_line  # from gig.toml, not from the gig's docstring


def test_gigai_hello_runs_the_gig_command(tmp_path, site):
    _add_fixture(site, "hello")
    greet = _run(tmp_path, site, "hello", "greet", "--name", "p1")
    assert greet.returncode == 0, greet.stderr
    assert greet.stdout == "hello p1\n"
    gig_help = _run(tmp_path, site, "hello", "--help")
    assert gig_help.returncode == 0, gig_help.stderr
    assert "Usage: gigai hello" in gig_help.stdout
    assert "greet" in gig_help.stdout


def test_broken_gig_reports_cannot_load_and_the_root_cli_still_works(tmp_path, site):
    _add_fixture(site, "broken")
    _add_fixture(site, "hello")
    broken = _run(tmp_path, site, "broken")
    assert broken.returncode == 1
    assert "gigai broken: cannot load (ImportError: gig_broken needs a dependency that is not installed)" in broken.stderr
    assert "Traceback" not in broken.stderr
    root_help = _run(tmp_path, site, "--help")
    assert root_help.returncode == 0, root_help.stderr
    assert "broken" in root_help.stdout
    assert _run(tmp_path, site, "--version").returncode == 0
    other_gig = _run(tmp_path, site, "hello", "greet")
    assert other_gig.returncode == 0, other_gig.stderr
    assert other_gig.stdout == "hello world\n"


def test_any_exception_from_gig_code_is_a_cannot_load(mounted_site):
    _add_dist(mounted_site, "gig-boom", "1.0", {"boom": "gig_boom:boom"}, {
        "gig_boom/__init__.py": "raise RuntimeError('boom at import')\n",
        "gig_boom/gig.toml": '[gig]\nname = "boom"\ndescription = "explodes"\n[commands]\ngroup = "gig_boom:boom"\n',
    })
    group = click.Group("gigai")
    assert mount_gigs(group) == (*SHIPPED, "boom")
    runner = CliRunner()
    result = runner.invoke(group, ["boom"], prog_name="gigai")
    assert result.exit_code == 1
    assert "gigai boom: cannot load (RuntimeError: boom at import)" in result.stderr
    assert runner.invoke(group, ["--help"], prog_name="gigai").exit_code == 0


def test_gig_without_a_manifest_is_listed_as_cannot_load(mounted_site):
    _add_dist(mounted_site, "gig-bare", "1.0", {"bare": "gig_bare:bare"}, {
        "gig_bare/__init__.py": "raise AssertionError('must not be imported')\n",
    })
    group = click.Group("gigai")
    mount_gigs(group)
    runner = CliRunner()
    listing = runner.invoke(group, ["--help"], prog_name="gigai")
    assert listing.exit_code == 0
    assert "cannot load (no gig.toml" in listing.output
    result = runner.invoke(group, ["bare"], prog_name="gigai")
    assert result.exit_code == 1
    assert "gigai bare: cannot load (no gig.toml next to package 'gig_bare' in gig-bare 1.0)" in result.stderr
    assert "gig_bare" not in sys.modules


def test_a_gig_named_like_a_core_verb_is_skipped_and_core_wins(tmp_path, site):
    _add_fixture(site, "hello", entry_name="doctor")
    code = (
        "import json\n"
        "import gigai.cli\n"
        "from gigai.gigs.loader import LazyGig\n"
        "print(json.dumps(isinstance(gigai.cli.cli.commands['doctor'], LazyGig)))\n"
    )
    completed = _run(tmp_path, site, code=code)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) is False
    warnings = completed.stderr.splitlines()
    assert warnings == ["gigai: gig 'doctor' (gigai-test-gig-hello 0.1.0) not mounted: 'doctor' is already a gigai command"]


def test_collision_rule_on_any_group(mounted_site, capsys):
    _add_fixture(mounted_site, "hello")
    group = click.Group("gigai")

    @group.command("hello")
    def core_hello():
        click.echo("core hello")

    assert mount_gigs(group) == SHIPPED
    assert group.commands["hello"] is core_hello
    assert capsys.readouterr().err.count("not mounted") == 1


def test_executable_gig_forwards_argv_and_describes_itself(tmp_path, site):
    _add_dist(site, "gig-ext", "1.0", {"ext": "gig_ext"}, {
        "gig_ext/__init__.py": "raise AssertionError('must not be imported')\n",
        "gig_ext/gig.toml": '[gig]\nname = "ext"\n[commands]\nexecutable = "gigai-test-ext"\n',
    })
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "gigai-test-ext"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = describe ]; then echo \'{"gig": {"name": "ext", "description": "described out of process"}}\'; exit 0; fi\n'
        'echo "ext got: $*"\n'
        "exit 3\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    root_help = _run(tmp_path, site, "--help", path=bin_dir)
    assert root_help.returncode == 0, root_help.stderr
    assert "described out of process" in root_help.stdout
    forwarded = _run(tmp_path, site, "ext", "one", "--two", path=bin_dir)
    assert forwarded.returncode == 3
    assert forwarded.stdout == "ext got: one --two\n"


def test_lazy_gig_imports_only_on_use(mounted_site):
    _add_fixture(mounted_site, "hello")
    group = click.Group("gigai")
    mount_gigs(group)
    gig = group.commands["hello"]
    assert isinstance(gig, LazyGig)
    assert gig.manifest["gig"]["name"] == "hello"
    assert "gig_hello" not in sys.modules
    result = CliRunner().invoke(group, ["hello", "greet", "--name", "lazy"], prog_name="gigai")
    assert result.exit_code == 0, result.output
    assert result.stdout == "hello lazy\n"
    assert "gig_hello" in sys.modules


def test_gig_context_has_exactly_four_fields(tmp_path):
    assert [field.name for field in dataclasses.fields(GigContext)] == ["home", "name", "manifest", "log"]
    context = GigContext(home=tmp_path, name="hello", manifest={"gig": {"name": "hello"}}, log=logging.getLogger("gigai.gig.hello"))
    assert context.name == "hello"


def test_malformed_entry_points_of_any_dist_never_take_core_down(tmp_path, site):
    _add_fixture(site, "hello")
    _add_malformed_entry_points(site)
    for argv in (("--help",), ("--version",)):
        completed = _run(tmp_path, site, *argv)
        assert completed.returncode == 0, completed.stderr
        assert "Traceback" not in completed.stderr
        assert len(completed.stderr.splitlines()) <= 1
        assert completed.stderr.startswith("gigai: no gigs mounted: cannot read installed entry points (")


def test_malformed_entry_points_mount_nothing(mounted_site, capsys):
    _add_fixture(mounted_site, "hello")
    _add_malformed_entry_points(mounted_site)
    group = click.Group("gigai")
    assert mount_gigs(group) == ()
    assert group.commands == {}
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_unparsable_gig_toml_is_listed_as_cannot_load(mounted_site):
    _add_dist(mounted_site, "gig-badtoml", "1.0", {"badtoml": "gig_badtoml:badtoml"}, {
        "gig_badtoml/__init__.py": "raise AssertionError('must not be imported')\n",
        "gig_badtoml/gig.toml": "[gig\nname = \n",
    })
    group = click.Group("gigai")
    assert mount_gigs(group) == (*SHIPPED, "badtoml")
    runner = CliRunner()
    listing = runner.invoke(group, ["--help"], prog_name="gigai")
    assert listing.exit_code == 0
    assert "cannot load (TOMLDecodeError" in listing.output
    result = runner.invoke(group, ["badtoml"], prog_name="gigai")
    assert result.exit_code == 1
    assert "gigai badtoml: cannot load (TOMLDecodeError: " in result.stderr
    assert "gig_badtoml" not in sys.modules


def test_two_gigs_with_one_name_mount_one_and_name_the_winner(mounted_site, capsys):
    for dist in ("gig-dup-a", "gig-dup-b"):
        package = dist.replace("-", "_")
        _add_dist(mounted_site, dist, "1.0", {"dup": f"{package}:dup"}, {
            f"{package}/__init__.py": "",
            f"{package}/gig.toml": f'[gig]\nname = "dup"\ndescription = "from {dist}"\n[commands]\ngroup = "{package}:dup"\n',
        })
    group = click.Group("gigai")
    assert mount_gigs(group) == (*SHIPPED, "dup")
    winner = group.commands["dup"].entry_point.dist.name
    loser = ({"gig-dup-a", "gig-dup-b"} - {winner}).pop()
    assert capsys.readouterr().err.splitlines() == [
        f"gigai: gig 'dup' ({loser} 1.0) not mounted: 'dup' is already mounted from {winner} 1.0"
    ]


def test_completion_through_gigs_has_no_traceback_and_no_clash_warning(tmp_path, site):
    _add_fixture(site, "broken")
    _add_fixture(site, "hello")
    _add_dist(site, "gig-doctor", "1.0", {"doctor": "gig_doctor:doctor"}, {"gig_doctor/gig.toml": '[gig]\nname = "doctor"\n'})
    broken = _complete(tmp_path, site, "gigai broken ")
    assert broken.returncode == 0, broken.stderr
    assert broken.stderr == ""
    good = _complete(tmp_path, site, "gigai hello ")
    assert good.returncode == 0, good.stderr
    assert "greet" in good.stdout
    assert good.stderr == ""  # the `doctor` clash warning stays out of Tab presses


def test_unknown_gig_subcommand_is_a_usage_error_of_the_gig(tmp_path, site):
    _add_fixture(site, "hello")
    completed = _run(tmp_path, site, "hello", "unknown-cmd")
    assert completed.returncode == 2
    assert "Usage: gigai hello" in completed.stderr
    assert "No such command 'unknown-cmd'" in completed.stderr


def test_system_exit_at_gig_import_is_a_cannot_load(mounted_site):
    _add_dist(mounted_site, "gig-sysexit", "1.0", {"sysexit": "gig_sysexit:sysexit"}, {
        "gig_sysexit/__init__.py": "raise SystemExit(7)\n",
        "gig_sysexit/gig.toml": '[gig]\nname = "sysexit"\ndescription = "exits"\n[commands]\ngroup = "gig_sysexit:sysexit"\n',
    })
    group = click.Group("gigai")
    mount_gigs(group)
    result = CliRunner().invoke(group, ["sysexit"], prog_name="gigai")
    assert result.exit_code == 1
    assert "gigai sysexit: cannot load (SystemExit: 7)" in result.stderr


def test_malformed_entry_point_value_is_named(mounted_site):
    _add_dist(mounted_site, "gig-badval", "1.0", {"badval": "not a valid!! value"}, {})
    group = click.Group("gigai")
    assert mount_gigs(group) == (*SHIPPED, "badval")
    runner = CliRunner()
    listing = runner.invoke(group, ["--help"], prog_name="gigai")
    assert listing.exit_code == 0
    assert "cannot load (invalid entry point" in listing.output
    result = runner.invoke(group, ["badval"], prog_name="gigai")
    assert result.exit_code == 1
    assert "gigai badval: cannot load (invalid entry point value 'not a valid!! value')" in result.stderr


def test_the_gigai_dists_own_gig_wins_over_an_impostor_earlier_on_the_path(mounted_site):
    from importlib.metadata import entry_points

    from gigai.gigs.loader import mountable

    _add_dist(mounted_site, "gig-impostor", "9.9", {"scout": "gig_impostor:cli"}, {
        "gig_impostor/__init__.py": "raise AssertionError('must not be imported')\n",
        "gig_impostor/gig.toml": '[gig]\nname = "scout"\n[commands]\ngroup = "gig_impostor:cli"\n',
    })
    found = tuple(entry_points(group="gigai.gigs"))
    assert [ep.dist.name for ep in found if ep.name == "scout"][0] == "gig-impostor"  # first on the path

    assert mountable(found)["scout"].dist.name == "gigai"
    group = click.Group("gigai")
    mount_gigs(group)
    assert group.commands["scout"].entry_point.value == "gigai.scout.gig:cli"
    assert "gig_impostor" not in sys.modules


@pytest.mark.parametrize(("manifest_dir", "module"), [
    ("gig_outer/inner", "gig_outer.inner.cli"),  # the entry point module's own package
    ("gig_outer", "gig_outer.inner.cli"),  # the nearest parent package that has one
    ("gig_outer/inner/cli", "gig_outer.inner.cli"),  # the module is itself a package
])
def test_a_nested_gig_manifest_is_found_without_importing_any_of_its_packages(mounted_site, manifest_dir, module):
    from importlib.metadata import entry_points

    from gigai.gigs.loader import manifest_location, read_manifest

    raising = "raise AssertionError('must not be imported')\n"
    package_files = {"gig_outer/__init__.py": raising, "gig_outer/inner/__init__.py": raising}
    if manifest_dir.endswith("/cli"):
        package_files["gig_outer/inner/cli/__init__.py"] = raising
    else:
        package_files["gig_outer/inner/cli.py"] = raising
    package_files[f"{manifest_dir}/gig.toml"] = f'[gig]\nname = "nested"\n[commands]\ngroup = "{module}:cli"\n'
    _add_dist(mounted_site, "gig-nested", "1.0", {"nested": f"{module}:cli"}, package_files)
    (entry_point,) = [ep for ep in entry_points(group="gigai.gigs") if ep.name == "nested"]

    assert manifest_location(entry_point) == mounted_site / manifest_dir / "gig.toml"
    assert read_manifest(entry_point)["gig"]["name"] == "nested"
    assert not [name for name in sys.modules if name.startswith("gig_outer")]


def test_a_gig_the_gigai_dist_ships_is_walked_by_group_commands_and_another_dists_is_not(mounted_site):
    """agent-context walks `Group.commands`; core's commands.yaml documents Scout's verbs, never a third party's."""

    _add_fixture(mounted_site, "hello")
    group = click.Group("gigai")
    mount_gigs(group)

    assert group.commands["hello"].commands == {}
    assert "gig_hello" not in sys.modules
    assert {"install", "resume", "learning"} <= set(group.commands["scout"].commands)


def test_mountable_before_any_mount_keeps_core_names_and_imports_no_gig():
    """A process that never ran `mount_gigs` (a hook or `gig check` path) still refuses core's names, Scout unloaded."""

    code = (
        "import sys; from importlib.metadata import EntryPoint; from gigai.gigs import loader; "
        "assert loader._core_commands is None; "
        "found = [EntryPoint('gig', 'x:cli', 'gigai.gigs'), EntryPoint('tidy', 'y:cli', 'gigai.gigs')]; "
        "print(sorted(loader.mountable(found))); print(sorted(m for m in sys.modules if m.startswith('gigai.scout')))"
    )
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "['tidy']\n[]\n"
