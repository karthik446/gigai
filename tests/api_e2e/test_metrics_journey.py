"""0.1.10.7 E: the model call metrics journey (``GET /api/metrics``).

Through the real supervised server, like every journey in this suite; the
model is the fixture transport (``bindings._test_model_handler``), so no live
model call is made.

1. A fresh project: no aggregates, and reading creates no metrics file.
2. ``POST /api/assess`` twice (an assessment and a re-assessment), then one
   whose fixture answer is not JSON: ``GET /api/metrics`` answers the average
   tokens and time of the calls for the model that ran, and the error rate.
3. ``?kind=`` and ``?model=`` narrow it; a kind it does not know, a model
   that is not shaped like one and an unknown key are 422.
4. ``gigai scout metrics --json`` prints the same object, filters included.
5. The body has the documented shape, and carries no word of the posting or
   the resume.
6. A foreign ``Host`` is refused like every other read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout.find_jobs.api import openapi
from gigai.scout.find_jobs.bindings import TEST_MODEL_GARBAGE_MARKER, TEST_MODEL_NAME
from gigai.scout.pipeline.store import pipeline_path

from tests.api_e2e.after_journey import assert_clean_and_healthy, timed_request
from tests.api_e2e.harness import (
    add_resume,
    resolve_workpad_path,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)

_POSTING = (
    "Acme is hiring a Software Engineer to build reliable Python services. "
    "Requirements: Python in production; GCP experience is a plus. Remote within the US."
)
_RESUME = "Software engineer with Python service experience."
_ROW_KEYS = {
    "kind", "model_target", "calls", "errors", "error_rate", "items", "avg_input_tokens", "avg_output_tokens",
    "avg_cached_tokens", "avg_tokens", "avg_seconds", "avg_cost_usd", "last_at",
}


def _cli(home: Path, target: Path, *extra: str) -> dict[str, object]:
    result = CliRunner().invoke(cli, ["scout", "metrics", "--home", str(home), "--target", str(target), *extra, "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_metrics_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)

        # 1. Nothing recorded: an empty answer, and no file.
        first, latency = timed_request("GET /api/metrics", lambda: client.get("/api/metrics"))
        assert first.status_code == 200, first.text
        assert first.json() == {"schema_version": "scout-metrics:1", "kind": None, "model": None, "aggregates": [], "comparison": []}
        assert not pipeline_path(home, target).exists()
        assert _cli(home, target) == first.json()

        # 2. Two assessments of one job, then an answer that is not JSON (the call, and its one retry).
        body = {"job": {"job_text": _POSTING, "title": "Software Engineer", "company": "Acme"}}
        for _ in range(2):
            assessed = client.post("/api/assess", json=body)
            assert assessed.status_code == 200, assessed.text
        garbage = client.post("/api/assess", json={"job": {"job_text": f"{_POSTING} {TEST_MODEL_GARBAGE_MARKER}", "title": "Software Engineer"}})
        assert garbage.status_code == 502 and garbage.json()["error"]["code"] == "model_output_invalid", garbage.text

        answered = client.get("/api/metrics")
        assert answered.status_code == 200, answered.text
        report = answered.json()
        (aggregate,) = report["aggregates"]
        (compared,) = report["comparison"]
        assert (aggregate["kind"], aggregate["model_target"], aggregate["model"]) == ("assess", "ollama_local", TEST_MODEL_NAME)
        assert (aggregate["calls"], aggregate["errors"], aggregate["error_rate"], aggregate["items"]) == (4, 2, 0.5, 4)
        # The fixture transport's own counts: 10 tokens in; 20 out for an assessment, 12 for the non-JSON answer.
        assert (aggregate["avg_input_tokens"], aggregate["avg_output_tokens"], aggregate["avg_tokens"]) == (10, 16, 26)
        assert aggregate["avg_cached_tokens"] is None and aggregate["avg_cost_usd"] is None
        assert isinstance(aggregate["avg_seconds"], float) and 0 < aggregate["avg_seconds"] < 60
        assert set(aggregate) == _ROW_KEYS | {"model"} and set(compared) == _ROW_KEYS | {"models"}
        assert compared["models"] == [TEST_MODEL_NAME] and {key: compared[key] for key in _ROW_KEYS} == {key: aggregate[key] for key in _ROW_KEYS}

        # 3. The filters, and what they refuse.
        for query in ("?kind=assess", "?model=ollama_local", f"?kind=assess&model={TEST_MODEL_NAME}"):
            narrowed = client.get(f"/api/metrics{query}").json()
            assert narrowed["aggregates"] == report["aggregates"] and narrowed["comparison"] == report["comparison"], query
        assert client.get("/api/metrics?kind=assess").json()["kind"] == "assess"
        for query in ("?kind=rank", "?model=codex_cli"):
            assert client.get(f"/api/metrics{query}").json()["aggregates"] == [], query
        for query in ("?kind=summarize", "?model=not%20a%20model"):
            refused = client.get(f"/api/metrics{query}")
            assert refused.status_code == 422 and refused.json()["error"]["code"] == "invalid_value", refused.text
        unknown = client.get("/api/metrics?bogus=1")
        assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "unknown_key", unknown.text
        assert "kind, model" in unknown.json()["error"]["message"]

        # 4. The CLI prints the same object.
        assert _cli(home, target) == report
        assert _cli(home, target, "--kind", "assess", "--model", "ollama_local") == client.get("/api/metrics?kind=assess&model=ollama_local").json()
        assert _cli(home, target, "--kind", "tag") == client.get("/api/metrics?kind=tag").json()

        # 5. The documented shape; no text of the posting or the resume.
        documented = next(route for route in openapi.ROUTES if route.key == ("GET", "/api/metrics")).example
        assert set(documented) == set(report)
        assert set(documented["aggregates"][0]) == set(aggregate) and set(documented["comparison"][0]) == set(compared)  # type: ignore[index]
        served = answered.text.lower()
        for word in ("hiring", "python", "requirements", "gcp", "remote", "software engineer with"):
            assert word not in served, word
        metrics_files = b"".join(path.read_bytes() for path in pipeline_path(home, target).parent.iterdir()).decode("latin-1").lower()
        for word in ("hiring", "python in production", "gcp experience", _RESUME.lower()):
            assert word not in metrics_files, word

        # 6. A foreign Host is refused.
        assert client.get("/api/metrics", headers={"Host": "evil.example"}).status_code == 403
    finally:
        stop_server(server)
    latency.assert_within_budget()
    assert_clean_and_healthy(workpad, home)
