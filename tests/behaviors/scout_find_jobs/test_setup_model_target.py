"""uat-bug-038: ``PUT /api/setup`` takes an optional ``model_target`` and saves it as ``default_model_target``.

The wizard's one model choice drives both the extractor and the run target.
Against a real gig and the real ``ScoutFindJobsBackend`` (in-process server):

* a ModelTarget value (``claude_cli`` included) is saved as find-jobs.json's
  ``default_model_target``; for an existing config nothing else changes;
* absent (or ``null``) leaves ``default_model_target`` as it was;
* anything else is a 422 (``invalid_value`` with ``field_errors``) and saves
  nothing.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs.api.setup import _validate_setup_body
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve

from tests.behaviors.scout_find_jobs.test_setup_profile_id import _body
from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path)


@pytest.fixture
def client(fx: ProfileFixtureGig):
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=20.0) as http:
            yield http
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _config(fx: ProfileFixtureGig) -> dict[str, object]:
    return json.loads((fx.target / "find-jobs.json").read_text(encoding="utf-8"))


def _without_target(config: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in config.items() if key != "default_model_target"}


def test_model_target_is_an_optional_field_of_the_body() -> None:
    assert "model_target" not in _validate_setup_body(_body(["Backend Engineer"]))
    assert _validate_setup_body(_body(["Backend Engineer"], model_target=None)).get("model_target") is None
    assert _validate_setup_body(_body(["Backend Engineer"], model_target="claude_cli"))["model_target"] == "claude_cli"


@pytest.mark.parametrize("target", ["ollama_local", "codex_cli", "claude_cli", "openrouter_api"])
def test_each_model_target_is_saved_as_the_default(fx: ProfileFixtureGig, client: httpx.Client, target: str) -> None:
    response = client.put("/api/setup", json=_body(["Backend Engineer"], model_target=target))
    assert response.status_code == 200, response.text
    assert _config(fx)["default_model_target"] == target


def test_an_existing_config_keeps_everything_else_when_the_target_changes(
    fx: ProfileFixtureGig, client: httpx.Client
) -> None:
    body = _body(["Backend Engineer"])
    assert client.put("/api/setup", json={**body, "model_target": "codex_cli"}).status_code == 200
    before = _config(fx)
    assert before["default_model_target"] == "codex_cli"
    # Values a Finish never sets are made distinctive, then must survive.
    path = fx.target / "find-jobs.json"
    path.write_text(
        json.dumps({**before, "sources": {"exa": True, "ats": False, "hiringcafe": True}, "default_assess_cap": 3}),
        encoding="utf-8",
    )
    before = _config(fx)

    assert client.put("/api/setup", json={**body, "model_target": "claude_cli"}).status_code == 200
    after = _config(fx)
    assert after["default_model_target"] == "claude_cli"
    assert _without_target(after) == _without_target(before)
    assert after["sources"] == {"exa": True, "ats": False, "hiringcafe": True} and after["default_assess_cap"] == 3


@pytest.mark.parametrize("extra", [{}, {"model_target": None}])
def test_without_a_model_target_the_default_is_unchanged(
    fx: ProfileFixtureGig, client: httpx.Client, extra: dict[str, object]
) -> None:
    body = _body(["Backend Engineer"])
    assert client.put("/api/setup", json={**body, "model_target": "claude_cli"}).status_code == 200
    assert client.put("/api/setup", json={**body, **extra}).status_code == 200
    assert _config(fx)["default_model_target"] == "claude_cli"


@pytest.mark.parametrize("bad", ["claude", "", "CLAUDE_CLI", 7, True, ["claude_cli"], {"t": "claude_cli"}])
def test_an_invalid_model_target_is_refused_and_saves_nothing(
    fx: ProfileFixtureGig, client: httpx.Client, bad: object
) -> None:
    body = _body(["Backend Engineer"])
    assert client.put("/api/setup", json={**body, "model_target": "codex_cli"}).status_code == 200
    before = (fx.target / "find-jobs.json").read_bytes()
    response = client.put("/api/setup", json={**body, "roles": ["Someone Else"], "model_target": bad})
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "invalid_value" and "model_target" in error["field_errors"]
    assert (fx.target / "find-jobs.json").read_bytes() == before


def test_a_brand_new_config_takes_the_target(tmp_path: Path) -> None:
    from tests.support.scout_profile_fixtures import build_gig_with_resume as build

    fx = build(tmp_path)
    (fx.target / "find-jobs.json").unlink()
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=20.0) as http:
            assert http.put("/api/setup", json=_body(["Backend Engineer"], model_target="claude_cli")).status_code == 200
        assert _config(fx)["default_model_target"] == "claude_cli"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
