"""0.1.10-033b: ``gigai scout resume add`` works as the very first command on an empty HOME.

Same self-setup ``scout run`` / ``scout install`` do (uat-bug-050): a missing
``<home>/config.toml`` is written as an Enter-through ``gigai setup`` writes it,
then the resume is imported. Everything runs under a temp HOME.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.model_discovery import DetectedModel
from gigai.scout.resume_pii import RESUME_WARNING

CREATED_LINE = "Created GigAI settings with defaults at "
RESUME = "Quill Marrowby\nStaff engineer 2019 - 2023: nine years of Rust and NATS. Led a team of 12.\n"


@pytest.fixture
def fresh_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    for name in ("GIGAI_HOME", "GIGAI_WORKPAD_ROOT", "VISUAL", "EDITOR"):
        monkeypatch.delenv(name, raising=False)
    snapshot = SimpleNamespace(
        models=(
            DetectedModel("codex", Path("/runtime/bin/codex"), "detected", "codex 1.2.3", "path", "login_shell", None),
            DetectedModel("claude", None, "unavailable", failure_code="executable_not_found"),
        )
    )
    monkeypatch.setattr("gigai.cli.discover_runtime_snapshot", lambda **_: snapshot)
    monkeypatch.setattr("gigai.cli.persist_discovery_snapshot", lambda *_: user_home / "snapshot.json")
    monkeypatch.setattr("gigai.cli.detect_editor_argv", lambda: ("/usr/bin/true",))
    return user_home


def test_resume_add_first_on_an_empty_home_sets_up_then_imports(fresh_user: Path) -> None:
    source = fresh_user / "resume.txt"
    source.write_text(RESUME, encoding="utf-8")
    home = fresh_user / ".gigai"
    assert not home.exists()

    result = CliRunner().invoke(cli, ["scout", "resume", "add", str(source)])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == f"{CREATED_LINE}~/.gigai/config.toml"
    assert (home / "config.toml").is_file()
    assert "Resume reference" in result.output and "are ready" in result.output
    assert f"Warning: {RESUME_WARNING}" in result.output


def test_resume_add_json_first_on_an_empty_home_stays_parseable(fresh_user: Path) -> None:
    source = fresh_user / "resume.txt"
    source.write_text(RESUME, encoding="utf-8")

    result = CliRunner().invoke(cli, ["scout", "resume", "add", str(source), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["warning"] == RESUME_WARNING
    assert payload["scout_installed"] is True
    for key in ("gig_id", "reference_id", "record_id", "revision_id", "record_created", "profile_id"):
        assert key in payload
    assert payload["reference_created"] is True and payload["record_created"] is True
    assert (fresh_user / ".gigai" / "config.toml").is_file()


def test_resume_add_never_rewrites_an_existing_config(fresh_user: Path) -> None:
    home = fresh_user / ".gigai"
    home.mkdir()
    config = home / "config.toml"
    config.write_text("# operator-owned, not valid\n", encoding="utf-8")
    source = fresh_user / "resume.txt"
    source.write_text(RESUME, encoding="utf-8")

    CliRunner().invoke(cli, ["scout", "resume", "add", str(source)])

    assert config.read_text(encoding="utf-8") == "# operator-owned, not valid\n"
