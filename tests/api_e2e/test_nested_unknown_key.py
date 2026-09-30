"""0110-007 follow-up: a nested-object ``unknown_key`` 422 lists that object's allowed keys,
and a top-level one still lists the route's."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.api_e2e.harness import run_request_body, setup_and_init, start_server, stop_server

_JOB = {"job_url": "https://example.com/j"}
_JOB_KEYS = ["company", "job_text", "job_url", "title"]


def _assert_unknown(response, *, name: str, allowed: list[str]) -> None:
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "unknown_key"
    assert error["message"].startswith(f"{name} contains unknown key(s): ['bogus']"), error
    assert error["message"].endswith(f"(allowed: {', '.join(allowed)})"), error
    assert error["allowed_keys"] == allowed


def test_nested_unknown_key_lists_the_nested_objects_allowed_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        # assess job and tailor job are the same nested object: not the route's top-level keys.
        _assert_unknown(client.post("/api/assess", json={"job": {**_JOB, "bogus": 1}}), name="assess_job_input", allowed=_JOB_KEYS)
        _assert_unknown(client.post("/api/tailored-resumes", json={"job": {**_JOB, "bogus": 1}}), name="assess_job_input", allowed=_JOB_KEYS)
        # a nested object inside the run request body.
        body = run_request_body("sha256:" + "0" * 64)
        body["consent"] = {**body["consent"], "bogus": 1}  # type: ignore[dict-item]
        response = client.post("/api/run", json=body)
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "unknown_key" and "ui_consent" in error["message"], error
        assert "bogus" in error["message"] and "(allowed: " in error["message"], error
        assert "config_digest" not in error["allowed_keys"] and "action" in error["allowed_keys"], error

        # top-level behaviour is unchanged: the route's keys, listed once.
        top = client.post("/api/assess", json={"job": _JOB, "bogus": 1})
        assert top.status_code == 422, top.text
        error = top.json()["error"]
        assert error["allowed_keys"] == ["job", "model_target", "origin", "preferences", "resume", "schema_version"]
        assert error["message"].count("(allowed:") == 1 and error["message"].startswith("assess_request contains unknown key(s): ['bogus']")
    finally:
        stop_server(server)
