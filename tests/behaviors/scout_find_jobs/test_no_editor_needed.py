"""0110-11-01 / ED2: GigAI does not need an editor to start. An absent or empty [editor] argv is a valid config;
only a command that opens a file (`gigai open`) asks for one, by name."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.config import NO_EDITOR_NOTICE, load_config, render_config
from gigai.setup import build_config

NOTICE = (
    "no editor set; GigAI will ask for one only if a command needs to open a file: "
    "gigai setup --editor PROGRAM"
)


@pytest.fixture
def bare_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A machine with no EDITOR, no VISUAL and no editor binary on PATH (only a stand-in model CLI)."""

    user = tmp_path / "user"
    user.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "codex"
    stub.write_text(
        '#!/bin/sh\ncase "$1" in\n  --version) echo "codex-cli 0.0.0-test-stub"; exit 0;;\n'
        '  login) if [ "$2" = status ]; then echo "Logged in (test stub)"; exit 0; fi;;\nesac\nexit 97\n'
    )
    stub.chmod(0o755)
    git = shutil.which("git")
    assert git is not None
    (bin_dir / "git").symlink_to(git)  # the one tool a first run needs besides a model CLI
    monkeypatch.delenv("GIGAI_HOME", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("PATH", str(bin_dir))
    return user / ".gigai"


def test_notice_is_the_one_line_the_spec_names() -> None:
    assert NO_EDITOR_NOTICE == NOTICE


def test_setup_with_no_editor_sets_none_and_prints_one_line(bare_machine: Path) -> None:
    result = CliRunner().invoke(cli, ["setup", "--non-interactive", "--home", str(bare_machine)])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines().count(NOTICE) == 1
    config = load_config(bare_machine)
    assert config.editor_argv == ()
    assert "argv = []" in (bare_machine / "config.toml").read_text()


def test_setup_json_keeps_stdout_one_document(bare_machine: Path) -> None:
    result = CliRunner().invoke(
        cli, ["setup", "--non-interactive", "--home", str(bare_machine), "--json"]
    )

    assert result.exit_code == 0, result.output
    json.loads(result.stdout)
    assert NOTICE not in result.stdout
    assert NOTICE in result.stderr


def test_first_scout_run_starts_with_no_editor_and_says_so_once(bare_machine: Path) -> None:
    from gigai.cli import write_default_setup

    setup = write_default_setup(bare_machine, as_json=False)

    assert setup.config.editor_argv == ()
    assert load_config(bare_machine).editor_argv == ()


def test_scout_run_json_on_a_fresh_home_with_no_editor(
    bare_machine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.behaviors.scout_find_jobs.test_scout_port_takeover import _free_port_18xxx

    port = _free_port_18xxx()
    bare_path = os.environ["PATH"]
    result = CliRunner().invoke(
        cli,
        ["scout", "run", "--no-browser", "--port", str(port), "--home", str(bare_machine), "--json"],
    )
    try:
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["pid"]
        assert result.stderr.splitlines().count(NOTICE) == 1
        assert load_config(bare_machine).editor_argv == ()
    finally:
        from tests.support.scout_servers import scout_test_servers, stop_test_servers

        # Finding the server needs `ps`, which the bare PATH does not have.
        monkeypatch.setenv("PATH", f"{bare_path}{os.pathsep}/bin{os.pathsep}/usr/bin")
        stop_test_servers(scout_test_servers(under=bare_machine.parent.parent))


def test_doctor_reports_editor_not_set_as_information(bare_machine: Path) -> None:
    CliRunner().invoke(cli, ["setup", "--non-interactive", "--home", str(bare_machine)])

    result = CliRunner().invoke(cli, ["doctor", "--home", str(bare_machine), "--json"])

    report = json.loads(result.stdout)
    editor = next(check for check in report["checks"] if check["id"] == "editor.resolved")
    assert editor["status"] == "PASS"
    assert editor["summary"] == "editor: not set"


def test_open_with_no_editor_refuses_there_by_name(bare_machine: Path) -> None:
    CliRunner().invoke(cli, ["setup", "--non-interactive", "--home", str(bare_machine)])

    result = CliRunner().invoke(cli, ["open", "--home", str(bare_machine)])

    assert result.exit_code != 0
    assert "gigai open needs an editor and none is set: gigai setup --editor PROGRAM" in result.output


def test_editor_set_later_is_kept_and_round_trips_byte_identical(
    bare_machine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    CliRunner().invoke(cli, ["setup", "--non-interactive", "--home", str(bare_machine)])
    set_editor = CliRunner().invoke(
        cli, ["setup", "--non-interactive", "--home", str(bare_machine), "--editor", "/usr/bin/true"]
    )
    assert set_editor.exit_code == 0, set_editor.output
    assert NOTICE not in set_editor.output
    before = (bare_machine / "config.toml").read_bytes()
    assert b'argv = ["/usr/bin/true"]' in before

    config = load_config(bare_machine)
    assert config.editor_argv == ("/usr/bin/true",)
    assert render_config(config) == before
    rerun = CliRunner().invoke(cli, ["setup", "--non-interactive", "--home", str(bare_machine)])
    assert rerun.exit_code == 0, rerun.output
    assert (bare_machine / "config.toml").read_bytes() == before


def test_existing_config_with_an_editor_renders_exactly_as_before(tmp_path: Path) -> None:
    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true", "--wait"),
        open_with_target=True,
    )
    rendered = render_config(config)
    assert b'[editor]\nargv = ["/usr/bin/true", "--wait"]\nopen_with_target = true\n' in rendered
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_bytes(rendered)
    assert render_config(load_config(home)) == rendered


def test_a_config_with_no_editor_round_trips_byte_identical(tmp_path: Path) -> None:
    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=(),
        open_with_target=False,
    )
    rendered = render_config(config)
    assert b"argv = []" in rendered
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_bytes(rendered)
    loaded = load_config(home)
    assert loaded.editor_argv == ()
    assert render_config(loaded) == rendered


def test_environment_editor_still_wins_on_a_fresh_home(
    bare_machine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDITOR", "/usr/bin/true")
    result = CliRunner().invoke(cli, ["setup", "--non-interactive", "--home", str(bare_machine)])
    assert result.exit_code == 0, result.output
    assert NOTICE not in result.output
    assert load_config(bare_machine).editor_argv == ("/usr/bin/true",)
