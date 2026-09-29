"""uat-bug-049: a NEW config's ``default_assess_cap`` follows its model target.

Rule: a local target (codex_cli, claude_cli, ollama_local) saves ``"all"``;
openrouter_api keeps 10. With no ``model_target`` sent the new config's
default target (ollama_local) decides, so ``"all"``. An EXISTING file keeps
its saved value exactly. The starter config (``gigai scout install``) is
ollama_local, so it is ``"all"`` too. ConfigPanel says "Default full
assessments".
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve
from gigai.scout.scout_cli import STARTER_FIND_JOBS_CONFIG, write_starter_find_jobs_config
from tests.behaviors.scout_find_jobs.test_setup_profile_id import _body
from tests.support.scout_profile_fixtures import build_gig_with_resume


def _put(tmp_path: Path, body: dict[str, object], *, existing_cap: object = None) -> dict[str, object]:
    fx = build_gig_with_resume(tmp_path)
    path = fx.target / "find-jobs.json"
    if existing_cap is None:
        path.unlink()
    else:
        saved = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**saved, "default_assess_cap": existing_cap}), encoding="utf-8")
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=20.0) as http:
            assert http.put("/api/setup", json=body).status_code == 200
        return json.loads(path.read_text(encoding="utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("target", "cap"),
    [("codex_cli", "all"), ("claude_cli", "all"), ("ollama_local", "all"), ("openrouter_api", 10)],
)
def test_a_new_config_saves_the_cap_for_its_target(tmp_path: Path, target: str, cap: object) -> None:
    config = _put(tmp_path, _body(["Backend Engineer"], model_target=target))
    assert config["default_model_target"] == target and config["default_assess_cap"] == cap


def test_a_new_config_without_a_target_uses_the_default_target(tmp_path: Path) -> None:
    config = _put(tmp_path, _body(["Backend Engineer"]))
    assert config["default_model_target"] == "ollama_local" and config["default_assess_cap"] == "all"


@pytest.mark.parametrize(("target", "saved"), [("codex_cli", 10), ("openrouter_api", "all"), ("claude_cli", 7)])
def test_an_existing_config_keeps_its_saved_cap(tmp_path: Path, target: str, saved: object) -> None:
    config = _put(tmp_path, _body(["Backend Engineer"], model_target=target), existing_cap=saved)
    assert config["default_assess_cap"] == saved


def test_the_starter_config_is_local_and_assesses_all(tmp_path: Path) -> None:
    assert STARTER_FIND_JOBS_CONFIG.default_model_target.value == "ollama_local"
    assert STARTER_FIND_JOBS_CONFIG.default_assess_cap == "all"
    assert write_starter_find_jobs_config(tmp_path) is True
    assert json.loads((tmp_path / "find-jobs.json").read_text(encoding="utf-8"))["default_assess_cap"] == "all"


def test_config_panel_label_is_default_full_assessments() -> None:
    source = (Path(static_module.__file__).resolve().parents[2] / "ui" / "src" / "components" / "ConfigPanel.jsx").read_text(encoding="utf-8")
    assert "Default full assessments</div>" in source and "Default cap" not in source
