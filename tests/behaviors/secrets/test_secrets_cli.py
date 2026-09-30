from __future__ import annotations

import json
import stat
from pathlib import Path

from click.testing import CliRunner

from gigai import secrets_store
from gigai.cli import cli


SECRET_VALUE = "sk-super-secret-value-should-never-leak"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_add_via_stdin_stores_value_and_never_echoes_it(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = CliRunner().invoke(
        cli,
        ["secrets", "add", "exa", "--stdin", "--home", str(home)],
        input=f"{SECRET_VALUE}\n",
    )

    assert result.exit_code == 0, result.output
    assert SECRET_VALUE not in result.output
    assert secrets_store.get("EXA_API_KEY", home_root=home) == SECRET_VALUE
    assert _mode(secrets_store.secrets_path(home)) == 0o600


def test_add_json_output_never_contains_the_value(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = CliRunner().invoke(
        cli,
        ["secrets", "add", "openrouter", "--stdin", "--home", str(home), "--json"],
        input=f"{SECRET_VALUE}\n",
    )

    assert result.exit_code == 0, result.output
    assert SECRET_VALUE not in result.output
    payload = json.loads(result.output)
    assert payload == {"ok": True, "service": "openrouter"}


def test_add_unknown_service_fails_and_lists_known_services(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = CliRunner().invoke(
        cli,
        ["secrets", "add", "not-a-real-service", "--stdin", "--home", str(home)],
        input="whatever\n",
    )

    assert result.exit_code != 0
    assert "exa" in result.output
    assert "openrouter" in result.output
    assert "openai" in result.output


def test_list_shows_set_unset_and_source_never_a_value(tmp_path: Path) -> None:
    home = tmp_path / "home"
    CliRunner().invoke(
        cli,
        ["secrets", "add", "exa", "--stdin", "--home", str(home)],
        input=f"{SECRET_VALUE}\n",
    )

    result = CliRunner().invoke(cli, ["secrets", "list", "--home", str(home)])

    assert result.exit_code == 0, result.output
    assert SECRET_VALUE not in result.output
    assert "exa: set (.env)" in result.output
    assert "openrouter: unset" in result.output
    assert "openai: unset" in result.output


def test_list_json_reports_environment_source_and_never_a_value(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("EXA_API_KEY", SECRET_VALUE)

    result = CliRunner().invoke(cli, ["secrets", "list", "--home", str(home), "--json"])

    assert result.exit_code == 0, result.output
    assert SECRET_VALUE not in result.output
    payload = json.loads(result.output)
    rows = {row["service"]: row for row in payload["secrets"]}
    assert rows["exa"] == {
        "service": "exa",
        "env_var": "EXA_API_KEY",
        "set": True,
        "source": "environment",
    }
    assert rows["openrouter"]["set"] is False
    assert rows["openrouter"]["source"] is None


def test_rm_removes_the_secret(tmp_path: Path) -> None:
    home = tmp_path / "home"
    CliRunner().invoke(
        cli,
        ["secrets", "add", "exa", "--stdin", "--home", str(home)],
        input=f"{SECRET_VALUE}\n",
    )

    result = CliRunner().invoke(cli, ["secrets", "rm", "exa", "--home", str(home)])

    assert result.exit_code == 0, result.output
    assert secrets_store.get("EXA_API_KEY", home_root=home) is None


def test_rm_unknown_service_fails(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = CliRunner().invoke(
        cli, ["secrets", "rm", "not-a-real-service", "--home", str(home)]
    )

    assert result.exit_code != 0


def test_add_empty_value_fails_loudly(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = CliRunner().invoke(
        cli,
        ["secrets", "add", "exa", "--stdin", "--home", str(home)],
        input="\n",
    )

    assert result.exit_code != 0
    assert secrets_store.get("EXA_API_KEY", home_root=home) is None


def test_add_via_stdin_round_trips_a_value_with_dollar_signs_and_braces(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    value = "a$b${HOME}c"
    result = CliRunner().invoke(
        cli,
        ["secrets", "add", "exa", "--stdin", "--home", str(home)],
        input=f"{value}\n",
    )

    assert result.exit_code == 0, result.output
    assert value not in result.output
    assert secrets_store.get("EXA_API_KEY", home_root=home) == value


def test_list_treats_an_empty_dot_env_line_as_unset(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("EXA_API_KEY=\n")
    (home / ".env").chmod(0o600)

    result = CliRunner().invoke(cli, ["secrets", "list", "--home", str(home), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    rows = {row["service"]: row for row in payload["secrets"]}
    assert rows["exa"] == {
        "service": "exa",
        "env_var": "EXA_API_KEY",
        "set": False,
        "source": None,
    }
    assert "exa: unset" in CliRunner().invoke(
        cli, ["secrets", "list", "--home", str(home)]
    ).output


def test_list_treats_an_empty_environment_variable_as_unset(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("EXA_API_KEY", "")

    result = CliRunner().invoke(cli, ["secrets", "list", "--home", str(home), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    rows = {row["service"]: row for row in payload["secrets"]}
    assert rows["exa"]["set"] is False
    assert rows["exa"]["source"] is None
