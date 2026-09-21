from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from gigai.cli import cli


def test_public_create_no_longer_accepts_offline_option() -> None:
    runner = CliRunner()

    help_result = runner.invoke(cli, ["create", "--help"])
    refused = runner.invoke(cli, ["create", "example", "--offline"])

    assert help_result.exit_code == 0, help_result.output
    assert "--offline" not in help_result.output
    assert refused.exit_code != 0
    assert "no such option" in refused.output.lower()


def test_fresh_setup_fails_without_a_real_model_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    workpads = tmp_path / "workpads"
    monkeypatch.setattr(
        "gigai.cli.discover_runtime_snapshot",
        lambda **_: SimpleNamespace(models=()),
    )

    result = CliRunner().invoke(
        cli,
        [
            "setup",
            "--non-interactive",
            "--home",
            str(home),
            "--workpad-root",
            str(workpads),
            "--editor",
            "/usr/bin/true",
            "--json",
        ],
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "setup_invalid"
    assert "no usable model runtime" in payload["error"]["message"]
    assert "offline-default" not in result.output
    assert not (home / "config.toml").exists()
