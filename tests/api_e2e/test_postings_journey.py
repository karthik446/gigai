"""0.1.10.7 M4a: the run-free journey (``GET /api/postings``, ``POST /api/postings/assess``, ``POST /api/runs/import``).

Through the real supervised server, like every journey in this suite. The
company index is synthetic (one Lever board put straight into the board
cache: no request is made) and the model is the fixture transport.

1. ``POST /api/run`` still works (it is deprecated, not removed): a real
   offline run is made first, so there is an old run to import.
2. The spec says so: ``deprecated: true`` on ``POST /api/run`` only, and the
   index marks it.
3. ``GET /api/postings``: the live search lists the stored posting with its
   profile tag, labelled ``public-untrusted`` only; no model call.
4. ``POST /api/postings/assess`` without approval asks (count, estimate) and
   makes no model call; with ``approve: true`` it assesses, records the
   approval, and the search shows the result.
5. ``POST /api/runs/import``: the old run's assessments are imported once (a
   second call imports nothing); the run routes keep serving it; the history
   rows carry the provenance the run sealed.
6. What is refused; the CLI prints the same object; the documented examples
   have the responses' keys.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout import data_labels
from gigai.scout.find_jobs.api import openapi
from gigai.scout.scout_new import check_response, response_labels

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import (
    add_resume,
    poll_until_terminal,
    resolve_workpad_path,
    run_request_body,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)
from tests.api_e2e.test_scout_new_journey import _JOB, _seed_index


def _assert_public_only(response) -> dict[str, object]:
    body = response.json()
    check_response(body)
    assert set(response_labels(body)) == {data_labels.PUBLIC_UNTRUSTED}
    assert response.headers[data_labels.LABELS_HEADER] == data_labels.PUBLIC_UNTRUSTED
    return body


def _example(method: str, path: str) -> dict[str, object]:
    return next(route for route in openapi.ROUTES if route.key == (method, path)).example


def test_postings_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)

        # 1. The deprecated route still starts a run, and the run finishes.
        write_offline_find_jobs_config(target, sources_live=True)
        digest = client.get("/api/config").json()["config_digest"]
        started = client.post("/api/run", json=run_request_body(digest))
        assert started.status_code == 202, started.text
        run_id = started.json()["run_id"]
        assert poll_until_terminal(client, run_id)["status"] == "succeeded"
        run_assessed = client.get(f"/api/runs/{run_id}/results").json()["payload"]["assessments"]
        assert run_assessed, "the run assessed the fixture posting"

        # 2. Deprecated in the spec (and only it), with what replaces it; the index marks it too.
        spec = client.get("/api/openapi.json").json()
        deprecated = [(path, method) for path, item in spec["paths"].items() for method, operation in item.items() if operation.get("deprecated")]
        assert deprecated == [("/api/run", "post")]
        assert "GET /api/postings" in spec["paths"]["/api/run"]["post"]["description"]
        flagged = [(route["method"], route["path"]) for route in client.get("/api").json()["routes"] if route.get("deprecated")]
        assert flagged == [("POST", "/api/run")]
        assert "POST /api/run is deprecated" in client.get("/llms.txt").text

        # 3. The live search over the stored index: the seeded posting, its profile tag, no model call.
        write_offline_find_jobs_config(target)
        _seed_index(home, target)
        assess_calls = sum(item["calls"] for item in client.get("/api/metrics?kind=assess").json()["aggregates"])
        listed = client.get("/api/postings")
        # 0110-9-01: how the stored postings are prepared, from memory. A small home is built inside the request above.
        prepared = client.get("/api/postings/status")
        assert prepared.status_code == 200 and prepared.headers["X-GigAI-Labels"] == client.get("/api/health").headers["X-GigAI-Labels"]
        assert prepared.json() == {
            "schema_version": "scout-postings-status:1", "state": "ready", "percent": 100, "phase": "idle", "boards_done": 0,
            "boards_total": 0, "builds": prepared.json()["builds"], "last_boards": prepared.json()["last_boards"],
        }
        assert prepared.json()["builds"] <= 1
        assert client.get("/api/postings/status", headers={"Host": "evil.example"}).status_code == 403
        assert listed.status_code == 200, listed.text
        found = _assert_public_only(listed)
        assert found["schema_version"] == "scout-postings:1" and found["history"] is None
        (profile,) = found["profiles"]
        row = next(item for item in found["postings"]["rows"] if item["job_identity"] == _JOB)
        # 0110-10-03: ``company`` is the name (no index name here: the slug rule); the board token is ``company_slug``.
        assert (row["title"], row["company"], row["company_slug"], row["state"], row["assessment"], row["assessment_basis"]) == ("Software Engineer", "Acmenew", "acmenew", "not_assessed", None, None)
        assert row["profile_id"] == profile["profile_id"] and [item["profile_id"] for item in row["profiles"]] == [profile["profile_id"]]
        assert found["rank"]["calls_today"]["limit"] == 100 and found["rank"]["calls_today"]["warn_at"] == 60
        assert found["anchor"]["last_checked_at"] is None  # a search never moves the "new since" anchor
        only_new = client.get("/api/postings?window=new&state=not_assessed&q=software+engineer").json()
        assert _JOB in [item["job_identity"] for item in only_new["postings"]["rows"]]
        assert client.get("/api/postings?q=nosuchword").json()["counts"]["matched"] == 0

        # 4. Assess these: the question first, and nothing assessed without approval.
        asked = client.post("/api/postings/assess", json={"jobs": [_JOB]})
        assert asked.status_code == 200, asked.text
        ask = _assert_public_only(asked)
        assert (ask["schema_version"], ask["status"], ask["assessed"], ask["approval"]) == ("scout-postings-assess:1", "ask", None, None)
        question = ask["question"]
        assert (question["kind"], question["to_assess"], question["by_profile"]) == ("assess_these", 1, [{"profile_id": profile["profile_id"], "count": 1}])
        assert question["estimate"]["calls"] == 1 and question["yes"]["api"] == {"method": "POST", "path": "/api/postings/assess", "body": {"approve": True, "jobs": [_JOB]}}
        assert sum(item["calls"] for item in client.get("/api/metrics?kind=assess").json()["aggregates"]) == assess_calls
        assert next(item for item in client.get("/api/postings").json()["postings"]["rows"] if item["job_identity"] == _JOB)["state"] == "not_assessed"

        approved = client.post("/api/postings/assess", json=question["yes"]["api"]["body"])
        assert approved.status_code == 200, approved.text
        done = _assert_public_only(approved)
        assert done["status"] == "assessed" and done["assessed"] == {"requested": 1, "assessed": 1, "failed": [], "stopped": None, "fetched_on_demand": 0}
        assert done["approval"]["decided_by"] == "operator" and done["approval"]["jobs"] == 1 and done["approval"]["id"].startswith("apv_")
        assert sum(item["calls"] for item in client.get("/api/metrics?kind=assess").json()["aggregates"]) == assess_calls + 1
        scored = next(item for item in client.get("/api/postings?state=assessed").json()["postings"]["rows"] if item["job_identity"] == _JOB)
        assert scored["state"] != "not_assessed" and scored["score_kind"] == "assessment" and scored["assessment_basis"] == {"origin": "quick_assess"}
        # The combined 0.1.10.8 contract on a posting row: U4's company_name and tag_pending next to U3's group, flag and score text
        # (U2's fetched_on_demand is in the batch above).
        assert (scored["company"], scored["company_slug"], scored["company_name"], scored["tag_pending"]) == ("Acmenew", "acmenew", "Acmenew", False)
        assert (scored["sort_group"], scored["tailored"], scored["stale_label"], scored["assessment_detail"]) == ("current", False, None, True)
        assert scored["score_text"] == "Needs your answers · fit 50% · 1 of 2 requirements · not ranked yet"
        assert client.post("/api/postings/assess", json={"jobs": [_JOB], "approve": True}).json()["status"] == "nothing_to_assess"

        # 5. The old run is read-only history: imported once, still served by the run routes.
        imported = client.post("/api/runs/import", json={})
        assert imported.status_code == 200, imported.text
        assert imported.headers[data_labels.LABELS_HEADER] == data_labels.NO_LABELS
        counts = imported.json()
        assert (counts["schema_version"], counts["runs"], counts["runs_imported"]) == ("scout-run-history:1", 1, 1)
        assert counts["assessments_imported"] == len(run_assessed) and counts["rows_skipped"] == 0
        assert counts["imported"] == [{"run_id": run_id, "profile_id": profile["profile_id"], "assessments": len(run_assessed)}]
        second = client.post("/api/runs/import", json={}).json()
        assert (second["runs_imported"], second["assessments_imported"], second["runs_already_imported"]) == (0, 0, 1)
        history = client.get("/api/postings?history=1").json()["history"]
        assert history["runs_imported"] == 1 and history["hidden"] == 0 and len(history["rows"]) == len(run_assessed)
        old = history["rows"][0]
        assert old["job_identity"] == run_assessed[0]["posting"]["normalized_url"] and old["hidden"] is False
        basis = old["basis"]
        assert basis["origin"] == f"run:{run_id}" and basis["profile_ref"]["profile_id"] == profile["profile_id"]
        assert basis["profile_ref"]["revision"] >= 1 and basis["profile_ref"]["content_digest"].startswith("sha256:")
        assert basis["prompt_version"] and basis["constraints_digest"].startswith("sha256:") and basis["posting_sha256"].startswith("sha256:")
        assert basis["resume"]["record_id"] and basis["resume"]["content_sha256"].startswith("sha256:") and basis["model_target"]
        assert [item["run_id"] for item in client.get("/api/runs").json()["runs"]] == [run_id]
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "succeeded"

        # 6. What is refused.
        for url, status, code in (
            ("/api/postings?bogus=1", 422, "unknown_key"),
            ("/api/postings?window=yesterday", 422, "invalid_value"),
            ("/api/postings?state=great", 422, "invalid_value"),
            ("/api/postings?limit=0", 422, "invalid_value"),
            ("/api/postings?history=maybe", 422, "invalid_value"),
            ("/api/postings?profile_id=profile_00000000-0000-4000-8000-00000000dead", 404, "profile_not_found"),
        ):
            refused = client.get(url)
            assert refused.status_code == status and refused.json()["error"]["code"] == code, refused.text
        for payload, code in (
            ({"jobs": _JOB}, "wrong_type"), ({"jobs": [_JOB], "approve": "yes"}, "wrong_type"), ({"jobs": [_JOB], "bogus": 1}, "unknown_key"),
            ({"jobs": []}, "invalid_value"), ({"jobs": [_JOB], "actor": "somebody"}, "invalid_value"), ({"window": "yesterday"}, "invalid_value"),
        ):
            refused = client.post("/api/postings/assess", json=payload)
            assert refused.status_code == 422 and refused.json()["error"]["code"] == code, refused.text
        assert client.post("/api/runs/import", json={"run_id": run_id}).status_code == 422
        assert client.get("/api/postings", headers={"Host": "evil.example"}).status_code == 403

        # The CLI prints the same objects; the documented examples have the responses' keys.
        def cli_json(*args: str) -> dict[str, object]:
            result = CliRunner().invoke(cli, ["scout", *args, "--home", str(home), "--target", str(target), "--json"])
            assert result.exit_code == 0, result.output
            return json.loads(result.output)

        printed, served = cli_json("jobs", "list"), client.get("/api/postings").json()
        for body in (printed, served):  # before the first check "since" is 7 days before the call itself
            body.pop("checked_at"), body["anchor"].pop("since")  # type: ignore[union-attr]
        assert printed == served
        assert cli_json("jobs", "import-runs")["runs_already_imported"] == 1
        assert cli_json("jobs", "assess", _JOB)["status"] == "nothing_to_assess"
        documented = _example("GET", "/api/postings")
        assert set(documented) == set(found) and set(documented["postings"]["rows"][0]) == set(row)  # type: ignore[index]
        assert set(documented["counts"]) == set(found["counts"]) and set(documented["profiles"][0]) == set(profile)  # type: ignore[arg-type,index]
        assert set(documented["rank"]["calls_today"]) == set(found["rank"]["calls_today"]) and set(documented["filters"]) == set(found["filters"])  # type: ignore[index,arg-type]
        documented_ask = _example("POST", "/api/postings/assess")
        assert set(documented_ask) == set(ask) and set(documented_ask["question"]) == set(question) and set(documented_ask["counts"]) == set(ask["counts"])  # type: ignore[arg-type]
        assert set(_example("POST", "/api/runs/import")) == set(counts)
    finally:
        stop_server(server)
    assert_clean_and_healthy(workpad, home)
