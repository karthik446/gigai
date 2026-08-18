from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from urllib.request import Request, urlopen

from gigai.model_discovery import DetectedModel
from gigai.config import load_config
from gigai.setup_interview import SetupDraft, SetupHTTPServer


def test_setup_page_uses_human_model_labels_and_reports_cli_detection() -> None:
    applied: list[SetupDraft] = []
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
        ),
        detected_models=(
            DetectedModel("codex", Path("/usr/local/bin/codex"), "detected"),
            DetectedModel("claude", None, "unavailable"),
        ),
        folder_chooser=lambda: "/tmp/chosen-storage",
        on_apply=lambda draft: applied.append(draft) or {"status": "ok"},
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
        assert "Enabled model roster" in body
        assert "Machine-wide role defaults" in body
        assert 'type=\'checkbox\' name=\'enabled_model_targets\'' in body
        assert "Reviewer default" in body
        assert "Verifier default" in body
        assert "Researcher default" in body
        assert "Codex" in body
        assert "Detected — adapter available" in body
        assert "Not detected" in body
        assert "openai_api_env" in body
        assert "GigAI never receives the secret value" in body

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
            "reviewer_model_target": "codex-default",
            "verifier_model_target": "codex-default",
            "researcher_model_target": "codex-default",
            "openai_api_env": "OPENAI_API_KEY",
            "openai_api_model": "gpt-test",
            "openrouter_api_env": "",
            "openrouter_api_model": "",
        }
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
            )
        ]
    finally:
        server.close()


def test_setup_command_opens_browser_flow_and_applies_only_after_event(tmp_path: Path) -> None:
    home = tmp_path / "home"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from gigai.cli import cli; cli()",
            "setup",
            "--no-open",
            "--home",
            str(home),
        ],
        cwd=Path(__file__).parents[1],
        env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = process.stderr.readline().strip()
        assert line.startswith("GigAI local setup: http://127.0.0.1:")
        url = line.split(": ", 1)[1]
        payload = {
            "event": "apply",
            "home_root": str(home),
            "workpad_root": str(home / "workpads"),
            "editor": "/usr/bin/true",
            "open_with_target": False,
            "selected_model_target": "openai-default",
            "enabled_model_targets": ["openai-default"],
            "reviewer_model_target": "openai-default",
            "verifier_model_target": "openai-default",
            "researcher_model_target": "openai-default",
            "openai_api_env": "OPENAI_API_KEY",
            "openai_api_model": "gpt-test",
        }
        with urlopen(
            Request(
                url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            timeout=5,
        ) as response:
            assert json.loads(response.read())["status"] == "applied"
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert "configuration updated" in stdout
        assert (home / "config.toml").is_file()
        config = load_config(home)
        default = next(profile for profile in config.profiles if profile.name == "default")
        assert default.reviewer == "openai-default"
        assert default.verifier == "openai-default"
        assert default.researcher == "openai-default"
        assert default.gig_creator == "openai-default"
        assert next(target for target in config.model_targets if target.name == "openai-default").enabled
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
