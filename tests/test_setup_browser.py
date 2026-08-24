from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from urllib.request import Request, urlopen

from click.testing import CliRunner

from gigai.model_discovery import DetectedModel
from gigai.config import Endpoint, ModelTarget, Profile, load_config
from gigai.cli import _enable_probe_target, _model_target_description, cli
from gigai.setup import build_config
from gigai.setup_interview import SetupDraft, SetupHTTPServer


def test_setup_probe_temporarily_enables_a_previously_disabled_target() -> None:
    target = ModelTarget(
        "claude-default", "claude", "default", ("text",), 512, enabled=False
    )

    probed = _enable_probe_target((target,), "claude-default")

    assert target.enabled is False
    assert probed == (ModelTarget(
        "claude-default", "claude", "default", ("text",), 512, enabled=True
    ),)


def test_setup_explains_claude_token_requirement(monkeypatch) -> None:
    config = build_config(
        home_root=Path("/tmp/gigai"),
        workpad_root=Path("/tmp/gigai/workpads"),
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(Endpoint("claude", "claude_cli"),),
        model_targets=(ModelTarget("claude-default", "claude", "default", ("text",), 512),),
        profiles=(Profile("default", "claude-default", "claude-default", "claude-default"),),
    )
    target = config.model_targets[0]

    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    assert "claude setup-token" in _model_target_description(target, config)

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "synthetic-oauth-token")
    assert "CLAUDE_CODE_OAUTH_TOKEN is available" in _model_target_description(target, config)


def test_setup_page_uses_human_model_labels_and_reports_cli_detection() -> None:
    applied: list[SetupDraft] = []
    probed: list[tuple[str, SetupDraft]] = []
    server = SetupHTTPServer(
        SetupDraft(
            "/tmp/gigai",
            "/tmp/gigai/workpads",
            "/usr/bin/true",
            False,
            "codex-default",
            enabled_model_targets=("codex-default",),
            reviewer_model_target="codex-default",
            verifier_model_target="codex-default",
            researcher_model_target="codex-default",
        ),
        model_options=(
            {
                "id": "codex-default",
                "label": "Codex CLI",
                "description": "Bounded local model adapter.",
            },
            {
                "id": "openai-default",
                "label": "OpenAI API",
                "description": "Configure an environment reference first.",
                "kind": "api",
            },
        ),
        detected_models=(
            DetectedModel("codex", Path("/usr/local/bin/codex"), "detected"),
            DetectedModel("claude", None, "unavailable"),
        ),
        folder_chooser=lambda: "/tmp/chosen-storage",
        on_apply=lambda draft: applied.append(draft) or {"status": "ok"},
        on_probe=lambda target, draft: probed.append((target, draft))
        or {"target_name": target, "readiness": "usable"},
    ).start()
    try:
        with urlopen(server.url, timeout=2) as response:
            body = response.read().decode()
        assert ".css" in body
        with urlopen(server.url + ".css", timeout=2) as response:
            stylesheet = response.read().decode()
        assert ".role-grid" in stylesheet
        assert "appearance: none" in stylesheet
        assert "Offline demo mode" not in body
        assert "GigAI setup" in body
        assert "Question 1 of 5" in body
        assert "Question 2 of 5" in body
        assert "Question 3 of 5" in body
        assert "Question 4 of 5" in body
        assert "Question 5 of 5" in body
        assert "CLI only" in body
        assert "API only" in body
        assert "Both CLI and API" in body
        assert "data-api-inline" in body
        assert "Gig definition" in body
        assert "Machine defaults" in body
        assert "data-summary" in body
        assert 'type=\'checkbox\' name=\'enabled_model_targets\'' in body
        assert "Reviewer" in body
        assert "Verifier" in body
        assert "Researcher" in body
        assert 'value=\'openai-default\'  disabled' in body
        assert "Codex" in body
        assert "openai_api_env" in body
        assert "GigAI stores only the environment-variable name" in body
        assert "data-probe='codex-default'" in body

        with urlopen(
            Request(
                server.url,
                data=json.dumps({"event": "choose_folder"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            timeout=2,
        ) as response:
            chosen = json.loads(response.read())
        assert chosen == {"path": "/tmp/chosen-storage", "status": "selected"}

        payload = {
            "event": "apply",
            "home_root": "/tmp/new-home",
            "workpad_root": "/tmp/new-home/workpads",
            "editor": "/usr/bin/true",
            "open_with_target": True,
            "selected_model_target": "codex-default",
            "enabled_model_targets": ["codex-default"],
            "verified_model_targets": ["codex-default"],
            "reviewer_model_target": "codex-default",
            "verifier_model_target": "codex-default",
            "researcher_model_target": "codex-default",
            "openai_api_env": "OPENAI_API_KEY",
            "openai_api_model": "gpt-test",
            "openrouter_api_env": "",
            "openrouter_api_model": "",
        }
        probe_payload = {**payload, "event": "probe", "target_name": "codex-default"}
        with urlopen(
            Request(
                server.url,
                data=json.dumps(probe_payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            timeout=2,
        ) as response:
            assert json.loads(response.read()) == {
                "result": {"readiness": "usable", "target_name": "codex-default"},
                "status": "probed",
            }
        assert probed and probed[0][0] == "codex-default"
        with urlopen(
            Request(
                server.url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            timeout=2,
        ) as response:
            assert json.loads(response.read())["status"] == "applied"
        assert applied == [
            SetupDraft(
                "/tmp/new-home",
                "/tmp/new-home/workpads",
                "/usr/bin/true",
                True,
                "codex-default",
                "OPENAI_API_KEY",
                "gpt-test",
                "",
                "",
                ("codex-default",),
                "codex-default",
                "codex-default",
                "codex-default",
                ("codex-default",),
                )
        ]
    finally:
        server.close()


def test_setup_command_is_terminal_native_and_applies_after_confirmation(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = CliRunner().invoke(
        cli,
        ["setup", "--home", str(home), "--editor", "/usr/bin/true"],
        input="\n\n\nn\n\n\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert "local setup" not in result.output.lower()
    assert "setup complete" in result.output.lower()
    assert (home / "config.toml").is_file()
    assert load_config(home).home_root == home.resolve()
