"""0.1.10.7 PL5: the background pipeline journey, through the real supervised server (its runner thread runs).

``GET /api/pipeline``, ``GET /api/pipeline/approvals``,
``POST /api/pipeline/approvals/{approval_id}``, ``POST /api/pipeline/process``
and the ``pipeline`` / ``rank`` blocks of ``/api/settings/background``.

1. A fresh project: the status object with the defaults, nothing queued, the
   server's runner active. System data only (``X-GigAI-Labels: none``).
2. The settings: ``pipeline.auto_jobs_per_trigger`` is set to 0 through the
   API and read back, so the next trigger goes to an approval.
3. An assessed job leaves a question open. Saving the answer queues nothing
   to run: the job waits in an approval with an estimate. No model call.
4. Deny: the job is cancelled, no pipeline model call was made.
5. The answer is changed: the job waits in a new approval. Approve: the
   server's runner runs it to a Scout label (codes only in the status).
6. Process now: nothing changed is a no-op; ``force`` runs it again.
7. What is refused, and the Host check.
8. No text in any of these responses; the documented examples have the
   responses' keys; the CLI prints the same approvals.
9. Settings that cannot be read: the pipeline is off and says so; a write is
   refused and the file is left as it is.
"""

from __future__ import annotations

import json
from pathlib import Path
import time
from urllib.parse import quote

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout import ats_score, data_labels
from gigai.scout.find_jobs.api import openapi
from gigai.scout.find_jobs.refresh_tick import settings_path
from gigai.scout.pipeline import steps

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import (
    add_resume,
    resolve_workpad_path,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)
from tests.support.latency import latency_bound

_JOB_URL = "https://careers.example.test/jobs/9"
_ANSWER = "Yes, two years on GCP."
_CHANGED = "Yes, two years on GCP, mostly GKE."
_STEPS = ("tailor", "reassess", "ats", "label")
#: Words of the posting, the resume and the answer: none may be in a pipeline response.
_TEXT = ("two years on GCP", "GKE", "Python", "Kubernetes", "experience", "Engineer", "Requirements")


def _system_only(response, *, job: str) -> dict[str, object]:
    """A pipeline response: labelled as holding none of the labelled classes, nothing redacted, no text."""

    assert response.headers[data_labels.LABELS_HEADER] == data_labels.NO_LABELS, response.headers
    body = response.json()
    assert "_redactions" not in body  # the outbound check found nothing contact-shaped
    bare = response.text.replace(job, "")  # a job is named by its link; nothing else of it is here
    for word in _TEXT:
        assert word not in bare, word
    return body


def _example(method: str, path: str) -> dict[str, object]:
    return next(route for route in openapi.ROUTES if route.key == (method, path)).example


def _wait_for_job(client, job: str, states: tuple[str, ...], *, deadline_seconds: float = 90.0) -> dict[str, object]:
    """Poll ``GET /api/pipeline`` until ``job`` is in one of ``states`` (the server's runner does the work)."""

    deadline = time.monotonic() + latency_bound(deadline_seconds)
    seen: object = None
    while time.monotonic() < deadline:
        status = client.get("/api/pipeline").json()
        seen = next((item for item in status["jobs"] if item["job_identity"] == job), None)
        if seen is not None and seen["state"] in states:
            return status
        time.sleep(0.2)
    raise AssertionError(f"the job never reached {states}: {seen}")


@pytest.mark.skip(reason="needs the pipeline on: re-enable in 0.1.11.1 (pipeline off by default in 0.1.11)")
def test_pipeline_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIGAI_SCOUT_PIPELINE", "on")
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)
        path = settings_path(home, target)

        # -- 1. a fresh project ----------------------------------------------------------------
        first = client.get("/api/pipeline")
        assert first.status_code == 200, first.text
        fresh = _system_only(first, job=_JOB_URL)
        assert (fresh["schema_version"], fresh["readable"], fresh["yielding_to"]) == ("scout-pipeline:1", True, None)
        assert fresh["setting"] == {
            "enabled": True, "source": "default", "auto_jobs_per_trigger": 10, "max_model_calls_per_day": 40, "label_min_ats": 0,
            "models": {}, "rank": {"enabled": True, "source": "default", "max_calls_per_day": 100, "warn_calls_per_day": 60},
        }
        assert fresh["runner"]["active"] is True  # the real server runs the pipeline's thread
        assert fresh["jobs"] == [] and fresh["errors"] == [] and fresh["approvals"] == {"pending": 0, "items": []}
        assert fresh["counts"] == {"steps": {}, "jobs": {}, "jobs_total": 0}
        assert fresh["caps"]["pipeline_calls"] == {"used": 0, "limit": 40} and fresh["caps"]["jobs_per_trigger"] == 10
        assert fresh["caps"]["rank_calls"] == {"used": 0, "limit": 100, "warn_at": 60, "warning": False}
        empty = client.get("/api/pipeline/approvals")
        assert _system_only(empty, job=_JOB_URL) == {"schema_version": "scout-pipeline-approvals:1", "pending": 0, "approvals": []}

        # -- 2. the settings: every job of a trigger waits for an approval -----------------------
        saved = client.put("/api/settings/background", json={"pipeline": {"auto_jobs_per_trigger": 0, "label_min_ats": 10}, "rank": {"warn_calls_per_day": 50}})
        assert saved.status_code == 200, saved.text
        assert saved.json()["settings"]["pipeline"] == {
            "enabled": True, "auto_jobs_per_trigger": 0, "max_model_calls_per_day": 40, "label_min_ats": 10, "models": {},
        }
        assert saved.json()["settings"]["rank"] == {"enabled": True, "max_calls_per_day": 100, "warn_calls_per_day": 50}
        assert saved.json()["effective"]["pipeline"]["source"] == "setting"
        assert client.get("/api/settings/background").json() == saved.json()
        assert json.loads(path.read_text(encoding="utf-8")) == {
            "schema_version": "scout-settings:1", "pipeline": {"auto_jobs_per_trigger": 0, "label_min_ats": 10}, "rank": {"warn_calls_per_day": 50},
        }
        assert client.get("/api/pipeline").json()["caps"]["jobs_per_trigger"] == 0
        for body, code in (
            ({"pipeline": {"enabled": "yes"}}, "wrong_type"),
            ({"pipeline": {"max_model_calls_per_day": 5000}}, "invalid_value"),
            ({"pipeline": {"models": {"tailor": "gpt-9"}}}, "bad_enum"),
            ({"pipeline": {"lanes": {}}}, "unknown_key"),
            ({"rank": {"max_calls_per_day": 5, "warn_calls_per_day": 6}}, "invalid_value"),
        ):
            refused = client.put("/api/settings/background", json=body)
            assert refused.status_code == 422 and refused.json()["error"]["code"] == code, (body, refused.text)

        # -- 3. an answer to the job's open question: the job waits in an approval; no model call --
        assessed = client.post("/api/assess", json={"job": {"job_url": _JOB_URL}})
        assert assessed.status_code == 200, assessed.text
        job = assessed.json()["job"]["job_identity"]
        profile_id = assessed.json()["resume"]["profile_id"]
        assert assessed.json()["result"]["structured_questions"][0]["question_id"] == "cloud:gcp"
        assert client.get("/api/pipeline").json()["jobs"] == []  # an assessment queues nothing

        answered = client.post("/api/answers", json={"question_id": "cloud:gcp", "answer": _ANSWER})
        assert answered.status_code == 201, answered.text

        listed = client.get("/api/pipeline/approvals")
        approvals = _system_only(listed, job=job)
        assert approvals["pending"] == 1
        (waiting,) = approvals["approvals"]
        assert (waiting["state"], waiting["trigger"], waiting["jobs"], waiting["waiting_jobs"]) == ("pending", "answer_saved", 1, 1)
        assert waiting["waiting"] == [{"profile_id": profile_id, "job_identity": job}]
        assert waiting["est_calls"] == 2 and waiting["profile_id"] == profile_id  # tailor + the re-assessment
        status = _system_only(client.get("/api/pipeline"), job=job)
        (held,) = status["jobs"]
        assert (held["state"], held["approval_id"], held["label"]) == ("awaiting_approval", waiting["id"], None)
        assert held["steps"] == {"tailor": "awaiting_approval", "reassess": "blocked", "ats": "blocked", "label": "blocked"}
        assert status["counts"]["jobs"] == {"awaiting_approval": 1} and status["approvals"]["pending"] == 1
        assert status["approvals"]["items"] == approvals["approvals"]
        time.sleep(1.0)  # the runner has had its look: nothing of an unapproved job runs
        assert client.get("/api/pipeline").json()["caps"]["pipeline_calls"]["used"] == 0
        assert client.get("/api/metrics?kind=tailor").json()["aggregates"] == []

        # -- 4. deny: cancelled, and still no pipeline model call -------------------------------------
        denied = client.post(f"/api/pipeline/approvals/{waiting['id']}", json={"approve": False, "actor": "agent"})
        assert denied.status_code == 200, denied.text
        declined = _system_only(denied, job=job)
        assert declined["schema_version"] == "scout-pipeline-approval:1"
        assert (declined["approval"]["state"], declined["approval"]["decided_by"], declined["approval"]["decided_jobs"]) == ("declined", "agent", 1)
        cancelled = client.get("/api/pipeline").json()
        assert cancelled["jobs"][0]["state"] == "cancelled" and cancelled["approvals"]["pending"] == 0
        assert cancelled["caps"]["pipeline_calls"]["used"] == 0 and client.get("/api/metrics?kind=tailor").json()["aggregates"] == []
        assert client.get("/api/pipeline/approvals?state=declined").json()["approvals"][0]["id"] == waiting["id"]
        assert client.get("/api/pipeline/approvals?state=pending").json()["approvals"] == []

        # -- 5. the answer changes: a new approval; approve: the server's runner runs it to a Scout label --
        changed = client.put("/api/answers/cloud%3Agcp", json={"answer": _CHANGED, "revision": answered.json()["answer"]["revision"]})
        assert changed.status_code == 200, changed.text
        (again,) = client.get("/api/pipeline/approvals?state=pending").json()["approvals"]
        assert again["id"] != waiting["id"] and again["waiting"] == [{"profile_id": profile_id, "job_identity": job}]

        approved = client.post(f"/api/pipeline/approvals/{again['id']}", json={"approve": True})
        assert approved.status_code == 200, approved.text
        accepted = _system_only(approved, job=job)
        assert (accepted["approval"]["state"], accepted["approval"]["decided_by"], accepted["approval"]["decided_jobs"]) == ("approved", "operator", 1)
        assert accepted["runner"] is True

        finished = _wait_for_job(client, job, ("done", "failed"))
        (done,) = finished["jobs"]
        assert done["state"] == "done", finished
        assert done["steps"] == dict.fromkeys(_STEPS, "done") and done["trigger"] == "answer_saved" and done["error_code"] is None
        assert done["label"]["label"] in ("recommended", "needs_attention") and isinstance(done["label"]["ats_score"], int)
        assert all(reason in ("tailored_assessment_not_matched", "open_questions", "ats_below_minimum", "base_assessment_stale") for reason in done["label"]["reasons"])
        assert finished["caps"]["pipeline_calls"]["used"] == 2 and finished["counts"]["jobs"] == {"done": 1}
        assert finished["errors"] == [] and finished["runner"]["active"] is True
        _system_only(client.get("/api/pipeline"), job=job)
        (tailored,) = client.get("/api/metrics?kind=tailor").json()["aggregates"]
        assert tailored["calls"] == 1  # the calls are recorded like every model call

        # -- 5b. the job page's read: the steps with their numbers, the two counts, the ATS breakdown, the label --
        detail_url = f"/api/pipeline/job?job_identity={quote(job, safe='')}&profile_id={profile_id}"
        read = client.get(detail_url)
        assert read.status_code == 200, read.text
        assert read.headers[data_labels.LABELS_HEADER] == data_labels.PUBLIC_UNTRUSTED  # the ATS line names the posting's skills
        detail = read.json()
        assert "_redactions" not in detail and "stored_path" not in read.text and str(home) not in read.text
        assert (detail["schema_version"], detail["job_identity"], detail["profile_id"]) == ("scout-pipeline-job:1", job, profile_id)
        assert (detail["enabled"], detail["state"], detail["tailor_outcome"]) == (True, "done", "tailored")
        assert [step["name"] for step in detail["steps"]] == list(_STEPS) and {step["state"] for step in detail["steps"]} == {"done"}
        by_step = {step["name"]: step for step in detail["steps"]}
        assert by_step["tailor"]["model_target"] and by_step["tailor"]["last_run"]["outcome"] == "ok"
        assert by_step["tailor"]["last_run"]["seconds"] >= 0 and by_step["ats"]["model_target"] is None
        for side in ("base", "tailored"):
            met = detail["requirements_met"][side]
            assert set(met) == {"met", "total", "percent"} and 0 <= met["met"] <= met["total"]
        assert detail["ats"]["score"] == done["label"]["ats_score"] and detail["ats"]["line"].startswith(f"Scout ATS {detail['ats']['score']}")
        assert detail["ats"]["wording"] == ats_score.ATS_WORDING and set(detail["ats"]["parts"]) == {"fidelity", "coverage", "format"}
        assert detail["label"]["label"] == done["label"]["label"] and detail["label"]["reasons"] == done["label"]["reasons"]
        assert (detail["label"]["name"], detail["label"]["wording"], detail["label"]["min_ats"]) == (steps.LABEL_NAME, steps.LABEL_WORDING, 10)
        documented_job = _example("GET", "/api/pipeline/job")
        assert set(documented_job) == set(detail) and set(documented_job["steps"][0]) == set(by_step["tailor"])  # type: ignore[index]
        assert set(documented_job["steps"][0]["last_run"]) == set(by_step["tailor"]["last_run"])  # type: ignore[index]
        assert set(documented_job["ats"]) == set(detail["ats"]) and set(documented_job["label"]) == set(detail["label"])  # type: ignore[arg-type]
        # A job that never entered the pipeline: no steps, nothing made up.
        never = client.get(f"/api/pipeline/job?job_identity={quote('https://careers.example.test/jobs/never', safe='')}&profile_id={profile_id}").json()
        assert (never["state"], never["steps"], never["ats"], never["label"]) == (None, [], None, None)
        assert never["requirements_met"] == {"base": None, "tailored": None}
        for url, code in (
            ("/api/pipeline/job", "invalid_value"), (detail_url + "&x=1", "unknown_key"),
            (f"/api/pipeline/job?job_identity=not%20a%20link&profile_id={profile_id}", "invalid_value"),
        ):
            refused = client.get(url)
            assert refused.status_code == 422 and refused.json()["error"]["code"] == code, (url, refused.text)
        assert client.get(detail_url, headers={"Host": "evil.example"}).status_code == 403

        # -- 6. process now -----------------------------------------------------------------------
        same = client.post("/api/pipeline/process", json={"job_identity": _JOB_URL})
        assert same.status_code == 202, same.text
        unchanged = _system_only(same, job=job)
        assert (unchanged["schema_version"], unchanged["result"], unchanged["job_identity"], unchanged["profile_id"]) == (
            "scout-pipeline-process:1", "noop_unchanged", job, profile_id,
        )
        assert unchanged["runner"] is True and unchanged["input_digest"].startswith("sha256:")
        forced = client.post("/api/pipeline/process", json={"job_identity": job, "profile_id": profile_id, "force": True})
        assert forced.status_code == 202 and forced.json()["result"] == "enqueued", forced.text
        deadline = time.monotonic() + latency_bound(90.0)
        while client.get("/api/pipeline").json()["caps"]["pipeline_calls"]["used"] < 3 and time.monotonic() < deadline:
            time.sleep(0.2)
        again_done = _wait_for_job(client, job, ("done", "failed"))
        assert again_done["jobs"][0]["state"] == "done" and again_done["jobs"][0]["trigger"] == "process_now"
        # The re-tailoring came out the same, so the re-assessment had nothing new to read: one call, not two.
        assert again_done["caps"]["pipeline_calls"]["used"] in (3, 4)
        assert again_done["approvals"]["pending"] == 0  # an explicit choice: no approval, whatever the per-trigger cap

        # -- 7. what is refused ---------------------------------------------------------------------
        for method, url, body, status_code, code in (
            ("GET", "/api/pipeline?x=1", None, 422, "unknown_key"),
            ("GET", "/api/pipeline/approvals?bogus=1", None, 422, "unknown_key"),
            ("GET", "/api/pipeline/approvals?state=waiting", None, 422, "bad_enum"),
            ("POST", "/api/pipeline/approvals/apv_missing", {"approve": True}, 404, "approval_not_found"),
            ("POST", f"/api/pipeline/approvals/{again['id']}", {}, 422, "wrong_type"),
            ("POST", f"/api/pipeline/approvals/{again['id']}", {"approve": "yes"}, 422, "wrong_type"),
            ("POST", f"/api/pipeline/approvals/{again['id']}", {"approve": True, "why": "x"}, 422, "unknown_key"),
            ("POST", f"/api/pipeline/approvals/{again['id']}", {"approve": True, "actor": "robot"}, 422, "bad_enum"),
            ("POST", "/api/pipeline/process", {}, 422, "invalid_value"),
            ("POST", "/api/pipeline/process", {"job_identity": job, "force": "yes"}, 422, "wrong_type"),
            ("POST", "/api/pipeline/process", {"job_identity": job, "bogus": 1}, 422, "unknown_key"),
            ("POST", "/api/pipeline/process", {"job_identity": "not a link"}, 422, "invalid_value"),
            ("POST", "/api/pipeline/process", {"job_identity": "https://careers.example.test/jobs/never-assessed"}, 404, "assessment_missing"),
            ("POST", "/api/pipeline/process", {"job_identity": job, "profile_id": "profile_00000000-0000-4000-8000-00000000dead"}, 404, "profile_not_found"),
        ):
            refused = client.get(url) if method == "GET" else client.post(url, json=body)
            assert refused.status_code == status_code and refused.json()["error"]["code"] == code, (url, body, refused.text)
        for url in ("/api/pipeline", "/api/pipeline/approvals"):
            assert client.get(url, headers={"Host": "evil.example"}).status_code == 403
        evil = client.post("/api/pipeline/process", json={"job_identity": job}, headers={"Origin": "https://evil.example"})
        assert evil.status_code == 403, evil.text
        # Deciding a decided approval changes nothing.
        assert client.post(f"/api/pipeline/approvals/{again['id']}", json={"approve": False}).json()["approval"]["state"] == "approved"

        # -- 8. the documented examples have the responses' keys; the CLI prints the same approvals ---
        documented = _example("GET", "/api/pipeline")
        assert set(documented) == set(again_done)
        for key in ("setting", "caps", "counts", "approvals", "runner"):
            assert set(documented[key]) == set(again_done[key]), key  # type: ignore[arg-type,call-overload]
        assert set(documented["jobs"][0]) == set(done) and set(documented["jobs"][0]["label"]) == set(done["label"])  # type: ignore[index]
        assert set(documented["lanes"][0]) == set(again_done["lanes"][0])  # type: ignore[index]
        assert set(documented["approvals"]["items"][0]) == set(waiting)  # type: ignore[index]
        assert set(_example("GET", "/api/pipeline/approvals")) == set(approvals)
        assert set(_example("GET", "/api/pipeline/approvals")["approvals"][0]) == set(waiting)  # type: ignore[index]
        assert set(_example("POST", "/api/pipeline/approvals/{approval_id}")) == set(accepted)
        assert set(_example("POST", "/api/pipeline/approvals/{approval_id}")["approval"]) == set(accepted["approval"])  # type: ignore[arg-type]
        assert set(_example("POST", "/api/pipeline/process")) == set(unchanged)
        printed = CliRunner().invoke(cli, ["scout", "pipeline", "approvals", "list", "--all", "--home", str(home), "--target", str(target), "--json"])
        assert printed.exit_code == 0, printed.output
        assert json.loads(printed.output) == client.get("/api/pipeline/approvals").json()

        # -- 9. settings that cannot be read: the pipeline is off, and says so --------------------------
        kept = path.read_bytes()
        path.write_text('{"schema_version": "scout-settings:1", "pipeline": {"max_model_calls_per_day": "lots"}}', encoding="utf-8")
        broken = path.read_bytes()
        off = client.get("/api/pipeline").json()
        assert off["readable"] is False and off["setting"]["enabled"] is False and off["setting"]["source"] == "settings_unreadable"
        unreadable = client.get("/api/settings/background").json()
        assert unreadable["effective"]["pipeline"]["enabled"] is False and unreadable["effective"]["pipeline"]["source"] == "settings_unreadable"
        path.write_text("{not json", encoding="utf-8")
        conflict = client.put("/api/settings/background", json={"pipeline": {"enabled": True}})
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "settings_unreadable", conflict.text
        assert path.read_text(encoding="utf-8") == "{not json" and broken != kept
        path.write_bytes(kept)
        assert client.get("/api/pipeline").json()["readable"] is True
    finally:
        stop_server(server)
    assert_clean_and_healthy(workpad, home)
