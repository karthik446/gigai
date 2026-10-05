"""uat-bug-050: ``gigai scout run`` / ``install`` set themselves up on a fresh machine.

Before the fix a clean HOME stopped at "configuration is missing at
~/.gigai/config.toml; run 'gigai setup'". Now a missing ``<home>/config.toml``
is written exactly as an Enter-through ``gigai setup`` writes it (the same
code path, every prompt taking its default) and the command carries on. An
existing config.toml -- valid or not -- is never written by Scout.

The server start is faked (``run_supervisor.start`` runs only its install /
activate step, ``ensure_scout_ready``, which is what needs the config), the
way ``test_scout_run_supervisor.py`` fakes health/Popen for its failure paths.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.config import ConfigurationError, load_config
from gigai.model_discovery import DetectedModel
from gigai.scout import run_supervisor

CREATED_LINE = "Created GigAI settings with defaults at "
# Enter at every `gigai setup` prompt (home, workpad, editor, open-with-target,
# apply): the defaults a new user gets from the README's old setup step.
SETUP_ENTERS = "\n\n\n\n\n"


def _codex_snapshot() -> SimpleNamespace:
    return SimpleNamespace(
        models=(
            DetectedModel(
                "codex",
                Path("/runtime/bin/codex"),
                "detected",
                "codex 1.2.3",
                "path",
                "login_shell",
                None,
            ),
            DetectedModel("claude", None, "unavailable", failure_code="executable_not_found"),
        )
    )


@pytest.fixture
def fresh_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty HOME with one detected runtime and one detected editor."""

    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    for name in ("GIGAI_HOME", "GIGAI_WORKPAD_ROOT", "VISUAL", "EDITOR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("gigai.cli.discover_runtime_snapshot", lambda **_: _codex_snapshot())
    monkeypatch.setattr("gigai.cli.persist_discovery_snapshot", lambda *_: user_home / "snapshot.json")
    monkeypatch.setattr("gigai.cli.detect_editor_argv", lambda: ("/usr/bin/true",))
    return user_home


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Fake the server start: run the real install/activate step, spawn nothing."""

    calls: list[dict[str, object]] = []

    def _fake_start(*, home_root: Path, requested_target: Path | None, port: int | None, **kwargs: object):
        run_supervisor.ensure_scout_ready(home_root=home_root, requested_target=requested_target)
        calls.append({"home_root": home_root, "target": requested_target, "port": port, **kwargs})
        state = run_supervisor.ScoutRunState(
            project_id="project_fake",
            pid=1,
            port=port or 8765,
            url=f"http://127.0.0.1:{port or 8765}/",
            log_path="scout.log",
            started_at="2026-09-29T00:00:00Z",
        )
        return run_supervisor.ScoutRunResult(state=state, reused=False, cleaned_stale=False)

    monkeypatch.setattr(run_supervisor, "start", _fake_start)
    return calls


def _setup_default_bytes(home_args: list[str], config_file: Path) -> bytes:
    """What an Enter-through `gigai setup` writes at this home; the home is removed after."""

    result = CliRunner().invoke(cli, ["setup", *home_args], input=SETUP_ENTERS)
    assert result.exit_code == 0, result.output
    assert "GigAI setup complete" in result.output
    expected = config_file.read_bytes()
    shutil.rmtree(config_file.parent)
    return expected


def _stamp(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


def test_scout_run_on_a_fresh_home_writes_setup_defaults_and_starts(
    fresh_user: Path, started: list[dict[str, object]]
) -> None:
    config_file = fresh_user / ".gigai" / "config.toml"
    expected = _setup_default_bytes([], config_file)
    assert not config_file.parent.exists()

    first = CliRunner().invoke(cli, ["scout", "run", "--no-browser", "--port", "18790"])

    assert first.exit_code == 0, first.output
    assert config_file.read_bytes() == expected
    assert first.output.splitlines()[0] == f"{CREATED_LINE}~/.gigai/config.toml"
    assert first.output.count(CREATED_LINE) == 1
    assert "Scout is running at http://127.0.0.1:18790/" in first.output
    assert len(started) == 1 and started[0]["open_browser"] is False
    assert load_config(fresh_user / ".gigai").home_root == (fresh_user / ".gigai").resolve()

    before = _stamp(config_file)
    second = CliRunner().invoke(cli, ["scout", "run", "--no-browser", "--port", "18790"])

    assert second.exit_code == 0, second.output
    assert CREATED_LINE not in second.output
    assert _stamp(config_file) == before
    assert len(started) == 2


def test_scout_install_on_a_fresh_home_writes_setup_defaults(fresh_user: Path) -> None:
    config_file = fresh_user / ".gigai" / "config.toml"
    expected = _setup_default_bytes([], config_file)

    first = CliRunner().invoke(cli, ["scout", "install"])

    assert first.exit_code == 0, first.output
    assert config_file.read_bytes() == expected
    assert first.output.splitlines()[0] == f"{CREATED_LINE}~/.gigai/config.toml"
    assert "is installed, approved, and active" in first.output

    before = _stamp(config_file)
    second = CliRunner().invoke(cli, ["scout", "install"])

    assert second.exit_code == 0, second.output
    assert CREATED_LINE not in second.output
    assert _stamp(config_file) == before


@pytest.mark.parametrize("command", ["run", "install"])
def test_home_option_is_respected(
    fresh_user: Path, tmp_path: Path, started: list[dict[str, object]], command: str
) -> None:
    home = tmp_path / "elsewhere" / "gigai-home"
    expected = _setup_default_bytes(["--home", str(home)], home / "config.toml")
    extra = ["--no-browser", "--port", "18791"] if command == "run" else []

    result = CliRunner().invoke(cli, ["scout", command, "--home", str(home), *extra])

    assert result.exit_code == 0, result.output
    assert (home / "config.toml").read_bytes() == expected
    assert f"{CREATED_LINE}{home.resolve()}/config.toml" in result.output
    assert not (fresh_user / ".gigai").exists()


def test_gigai_home_environment_is_respected(
    fresh_user: Path, monkeypatch: pytest.MonkeyPatch, started: list[dict[str, object]]
) -> None:
    home = fresh_user / "custom-gigai"
    monkeypatch.setenv("GIGAI_HOME", str(home))
    expected = _setup_default_bytes([], home / "config.toml")

    result = CliRunner().invoke(cli, ["scout", "run", "--no-browser", "--port", "18792"])

    assert result.exit_code == 0, result.output
    assert (home / "config.toml").read_bytes() == expected
    assert f"{CREATED_LINE}~/custom-gigai/config.toml" in result.output
    assert not (fresh_user / ".gigai").exists()


@pytest.mark.parametrize("command", ["run", "install"])
def test_an_existing_valid_config_is_untouched(
    fresh_user: Path, tmp_path: Path, started: list[dict[str, object]], command: str
) -> None:
    home = fresh_user / ".gigai"
    setup = CliRunner().invoke(
        cli,
        [
            "setup",
            "--non-interactive",
            "--workpad-root",
            str(tmp_path / "my-workpads"),
            "--editor",
            "/usr/bin/true",
            "--open-with-target",
        ],
    )
    assert setup.exit_code == 0, setup.output
    before = _stamp(home / "config.toml")
    extra = ["--no-browser", "--port", "18793"] if command == "run" else []

    result = CliRunner().invoke(cli, ["scout", command, *extra])

    assert result.exit_code == 0, result.output
    assert CREATED_LINE not in result.output
    assert _stamp(home / "config.toml") == before


@pytest.mark.parametrize("command", ["run", "install"])
def test_an_invalid_existing_config_still_errors_as_before(
    fresh_user: Path, started: list[dict[str, object]], command: str
) -> None:
    home = fresh_user / ".gigai"
    home.mkdir()
    (home / "config.toml").write_text("schema_version = [not toml\n", encoding="utf-8")
    before = _stamp(home / "config.toml")
    with pytest.raises(ConfigurationError) as current:
        load_config(home)
    extra = ["--no-browser", "--port", "18794"] if command == "run" else []

    result = CliRunner().invoke(cli, ["scout", command, *extra])

    assert result.exit_code == 1, result.output
    assert f"Error: {current.value}" in result.output
    assert CREATED_LINE not in result.output
    assert _stamp(home / "config.toml") == before
    assert started == []


def test_json_output_stays_one_parseable_object(
    fresh_user: Path, started: list[dict[str, object]]
) -> None:
    result = CliRunner().invoke(cli, ["scout", "run", "--no-browser", "--port", "18795", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert (fresh_user / ".gigai" / "config.toml").exists()


def test_no_model_runtime_fails_like_setup_and_writes_nothing(
    fresh_user: Path, monkeypatch: pytest.MonkeyPatch, started: list[dict[str, object]]
) -> None:
    monkeypatch.setattr("gigai.cli.discover_runtime_snapshot", lambda **_: SimpleNamespace(models=()))

    result = CliRunner().invoke(cli, ["scout", "run", "--no-browser", "--port", "18796"])

    assert result.exit_code == 1, result.output
    # 0.1.10.11 NF: the advice fits someone who never ran setup: what to install, then the command they typed.
    assert "no model CLI was found: install Codex or Claude Code, then run `gigai scout run` again." in result.output
    assert "rerun" not in result.output
    assert "`gigai setup --help`" in result.output  # the other way in: an API key or a local Ollama model
    assert not (fresh_user / ".gigai" / "config.toml").exists()
    assert started == []

    as_json = CliRunner().invoke(cli, ["scout", "run", "--no-browser", "--port", "18796", "--json"])
    assert as_json.exit_code == 1, as_json.output
    error = json.loads(as_json.output)["error"]
    assert error["code"] == "setup_invalid" and error["message"].startswith("no model CLI was found: install Codex or Claude Code")
    assert not (fresh_user / ".gigai" / "config.toml").exists()
