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


# local-llm coupling: a local URL sent without an explicit model_target is
# itself a choice -- the saved default becomes local_llm, so an assess
# never dispatches to the stale prior provider (e.g. claude_cli) after a
# local URL is saved. An explicit model_target still wins over a local URL.


def _local_body(roles: list[str]) -> dict[str, object]:
    """``_body`` plus the wizard's local-LLM keys (a loopback URL)."""

    return _body(
        roles,
        local_llm_url="http://127.0.0.1:8000",
        local_llm_protocol="openai",
        local_llm_model="qwen",
    )


def test_a_local_llm_url_without_a_model_target_saves_local_llm(
    fx: ProfileFixtureGig, client: httpx.Client
) -> None:
    assert client.put("/api/setup", json=_local_body(["Backend Engineer"])).status_code == 200
    config = _config(fx)
    assert config["default_model_target"] == "local_llm"
    assert config["local_llm_url"] == "http://127.0.0.1:8000"
    assert config["local_llm_model"] == "qwen"


def test_a_local_llm_url_after_a_claude_target_flips_to_local_llm(
    fx: ProfileFixtureGig, client: httpx.Client
) -> None:
    assert client.put("/api/setup", json=_body(["Backend Engineer"], model_target="claude_cli")).status_code == 200
    assert _config(fx)["default_model_target"] == "claude_cli"
    assert client.put("/api/setup", json=_local_body(["Backend Engineer"])).status_code == 200
    assert _config(fx)["default_model_target"] == "local_llm"


def test_an_explicit_model_target_still_wins_over_a_local_url(
    fx: ProfileFixtureGig, client: httpx.Client
) -> None:
    assert client.put("/api/setup", json={**_local_body(["Backend Engineer"]), "model_target": "claude_cli"}).status_code == 200
    assert _config(fx)["default_model_target"] == "claude_cli"


def test_a_local_url_stays_local_llm_on_a_re_save_without_its_url(
    fx: ProfileFixtureGig, client: httpx.Client
) -> None:
    assert client.put("/api/setup", json=_local_body(["Backend Engineer"])).status_code == 200
    assert _config(fx)["default_model_target"] == "local_llm"
    assert client.put("/api/setup", json=_body(["Backend Engineer"])).status_code == 200
    assert _config(fx)["default_model_target"] == "local_llm"


# GET /api/setup round-trip: the saved model choice (and its local-LLM keys)
# rides beside prefs, so the Settings UI shows the last-saved model target
# and the wizard round-trips it on the next save.


def test_get_setup_carries_the_saved_model_target(fx: ProfileFixtureGig, client: httpx.Client) -> None:
    assert client.put("/api/setup", json=_local_body(["Backend Engineer"])).status_code == 200
    response = client.get("/api/setup")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["model_target"] == "local_llm"
    assert payload["local_llm_url"] == "http://127.0.0.1:8000"
    assert payload["local_llm_protocol"] == "openai"
    assert payload["local_llm_model"] == "qwen"


def test_get_setup_carries_a_claude_target_after_a_non_local_save(fx: ProfileFixtureGig, client: httpx.Client) -> None:
    assert client.put("/api/setup", json=_body(["Backend Engineer"], model_target="claude_cli")).status_code == 200
    response = client.get("/api/setup")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["model_target"] == "claude_cli"
    assert "local_llm_url" not in payload


def test_get_setup_prefill_carries_the_model_keys_on_a_missing_config(tmp_path: Path) -> None:
    from tests.support.scout_profile_fixtures import build_gig_with_resume as build

    fx = build(tmp_path)
    (fx.target / "find-jobs.json").unlink()
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=20.0) as http:
            missing = http.get("/api/setup")
            assert missing.status_code == 404, missing.text
            prefill = missing.json()["error"]["prefill"]
            assert prefill["model_target"] == "ollama_local"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
