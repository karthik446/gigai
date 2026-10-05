"""0110-039 over HTTP: a stored assessment made under older settings is shown as stale and
"Assess all new" re-assesses it on the click.

Through the real supervisor, a real find-jobs run in its child process and the
real server; the model is the suite's fake Ollama and the boards are the
fixture transport (the same set-up as ``test_assess_all_journey``).

The journey:

1. a run with a cap of 1, then "Assess all new": every other new posting gets
   a stored assessment, each with its basis;
2. the stored files are rewritten as v4-era ones (no recorded basis). Since
   0.1.10.7 P5 (the posting fenced as untrusted in every assess prompt) such
   a record is older wording whatever the profile: even for the plain
   profile (no work mode) every one of them is planned "with older settings";
3. the profile becomes remote only and needs sponsorship. "Assess all new"
   still plans every one of them as "with older settings", and the list, the
   run rows, the run posting and ``GET /api/jobs`` all say so, with the reason;
4. one job is re-assessed with one call (the job page's Re-assess): it is
   current, the rest still stale. No read above called a model or wrote a
   file;
5. the click: the job assesses the stale ones, and only them;
6. changing the countries makes the current ones stale again; changing them
   back makes them current again.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gigai.scout.assessment_basis import BasisCheck
from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import (
    add_resume,
    resolve_workpad_path,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)
from tests.api_e2e.test_assess_all_journey import BULK_ENV, _expected_queue, _run
from tests.support.latency import latency_bound

BASIS_KEYS = ("prompt_version", "constraints_digest", "story_bank")


def _store_files(home: Path) -> list[Path]:
    return sorted(home.glob("scout/*/quick_assess/*/*.json"))


def _store_bytes(home: Path) -> dict[str, bytes]:
    return {str(path): path.read_bytes() for path in _store_files(home)}


def _edit_config(target: Path, **fields: object) -> None:
    path = target / "find-jobs.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config.update(fields)
    path.write_text(json.dumps(config), encoding="utf-8")


def _wait_for_job(client, run_id: str, body: dict) -> dict:
    deadline = time.monotonic() + latency_bound(60.0)
    while body["job"]["status"] == "running":
        assert time.monotonic() < deadline, body
        time.sleep(0.05)
        body = client.post(f"/api/runs/{run_id}/assess-all", json={}).json()
    return body


def _plan(client, run_id: str) -> dict:
    response = client.post(f"/api/runs/{run_id}/assess-all", json={})
    assert response.status_code == 200, response.text
    return response.json()["plan"]


def _items(client) -> dict[str, dict]:
    return {item["job"]["job_identity"]: item for item in client.get("/api/assessments").json()["items"]}


def test_a_stored_assessment_made_under_older_settings_is_stale_and_assess_all_assesses_it_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(BULK_ENV, "8")
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client

        # 1. A run, then "Assess all new": the rest of the run is in the store.
        run_id = _run(client, selection_cap=1)
        payload = client.get(f"/api/runs/{run_id}/results").json()["payload"]
        expected = _expected_queue(payload)
        assert len(expected) >= 4, expected
        started = client.post(f"/api/runs/{run_id}/assess-all", json={"start": True}).json()
        first_job = _wait_for_job(client, run_id, started)["job"]
        assert first_job["status"] == "complete" and first_job["assessed"] == len(expected), first_job
        assert _plan(client, run_id)["count"] == 0

        # 2. The same files as a v4-era store wrote them: no recorded basis.
        files = _store_files(home)
        assert len(files) == len(expected)
        profile_id = files[0].parent.name
        for path in files:
            stored = json.loads(path.read_text(encoding="utf-8"))
            assert stored["resume"]["profile_id"] == profile_id
            for key in BASIS_KEYS:
                stored.pop(key, None)
            path.write_text(json.dumps(stored, indent=2, sort_keys=True), encoding="utf-8")
        plain = BasisCheck(home_root=home, target=target).current(profile_id)
        assert plain is not None and plain.work_mode == "", "this fixture's profile starts with no work mode"
        plan = _plan(client, run_id)
        assert (plan["count"], plan["new_count"], plan["stale_count"]) == (len(expected), 0, len(expected)), (
            "P5: an assessment with no recorded basis was made before the posting was fenced: older wording, even for a plain profile"
        )

        # 3. The profile is now remote only and needs sponsorship.
        _edit_config(target, work_mode="remote", remote=True, visa_sponsorship_required=True)
        plan = _plan(client, run_id)
        assert plan["count"] == len(expected), "the stored assessments made under the old settings are queued again"
        assert (plan["new_count"], plan["stale_count"]) == (0, len(expected))

        items = _items(client)
        assert sorted(items) == sorted(expected)
        for item in items.values():
            assert item["basis_stale"] is True and item["basis_stale_reason"] == "older_prompt"
            assert item["job_state"]["state"] == "needs_answers", "the verdict still reads"
            assert item["job_state"]["assessment_stale"] == {"reason": "older_prompt"}
        rows = {row["posting"]["normalized_url"]: row for row in client.get(f"/api/runs/{run_id}/results").json()["payload"]["rows"]}
        for identity in expected:
            assert rows[identity]["job_state"]["assessment_stale"] == {"reason": "older_prompt"}
        run_assessed = payload["assessments"][0]["posting"]["normalized_url"]
        assert "assessment_stale" not in rows[run_assessed]["job_state"], "the run's own assessment is the run's to refresh"
        target_job = expected[0]
        posting = client.get(f"/api/runs/{run_id}/posting", params={"url": target_job}).json()
        assert posting["row"]["job_state"]["assessment_stale"] == {"reason": "older_prompt"}
        job = client.get("/api/jobs", params={"url": target_job}).json()
        assert job["job_state"]["assessment_stale"] == {"reason": "older_prompt"}
        quick = [entry for entry in job["assessments"] if entry["source"] == "quick"]
        assert len(quick) == 1 and quick[0]["basis_stale"] is True and quick[0]["basis_stale_reason"] == "older_prompt"

        # 4. One click on one job (the job page's Re-assess): that job is current, the rest still stale.
        before_click = _store_bytes(home)
        assert json.loads(next(iter(before_click.values())))["history"], "the stored files carry a verdict history"
        url = rows[target_job]["posting"]["url"]
        clicked = client.post("/api/assess", json={"job": {"job_url": url}, "origin": "job_page"})
        assert clicked.status_code == 200, clicked.text
        fresh = clicked.json()
        assert fresh["basis_stale"] is False and fresh["prompt_version"] == "assess-prompt-v9"
        assert set(BASIS_KEYS) <= set(fresh)
        after_click = _store_bytes(home)
        changed = [path for path in after_click if after_click[path] != before_click[path]]
        assert len(changed) == 1, "one click assessed one job"

        # Every read so far, and these: no model call, nothing written.
        items = _items(client)
        assert items[target_job]["basis_stale"] is False and "assessment_stale" not in items[target_job]["job_state"]
        assert sum(1 for item in items.values() if item["basis_stale"]) == len(expected) - 1
        client.get(f"/api/runs/{run_id}/results")
        client.get("/api/jobs", params={"url": expected[1]})
        plan = _plan(client, run_id)
        assert (plan["count"], plan["new_count"], plan["stale_count"]) == (len(expected) - 1, 0, len(expected) - 1)
        assert _store_bytes(home) == after_click, "opening a page never re-assesses"
        assert len(list((home / "scout").glob("*/assess_all/aa_*"))) == 1, "and starts no job"

        # 5. The click: "Assess all new" assesses the stale ones again, and only them.
        started = client.post(f"/api/runs/{run_id}/assess-all", json={"start": True}).json()
        assert started["job"]["record_id"] != first_job["record_id"] and started["job"]["total"] == len(expected) - 1
        body = _wait_for_job(client, run_id, started)
        job_done = body["job"]
        assert job_done["status"] == "complete", job_done
        assert (job_done["assessed"], job_done["failed"], job_done["skipped"]) == (len(expected) - 1, 0, 0)
        assert body["plan"]["count"] == 0
        after_job = _store_bytes(home)
        assert after_job[changed[0]] == after_click[changed[0]], "the current one was not assessed again"
        for path, raw in after_job.items():
            stored = json.loads(raw)
            assert stored["prompt_version"] == "assess-prompt-v9" and "constraints_digest" in stored
            assert [entry["trigger"] for entry in stored["history"]] == ["assess", "reassess"], "the earlier verdict stays in the history"
        items = _items(client)
        assert all(item["basis_stale"] is False and "assessment_stale" not in item["job_state"] for item in items.values())
        again = client.post(f"/api/runs/{run_id}/assess-all", json={"start": True}).json()
        assert again["plan"]["count"] == 0 and again["job"]["record_id"] == job_done["record_id"], "nothing left: no new job"

        # 6. A settings change makes the current ones stale; changing it back makes them current.
        countries = json.loads((target / "find-jobs.json").read_text(encoding="utf-8")).get("countries") or []
        _edit_config(target, countries=sorted({*countries, "CA", "US"}) if "CA" not in countries else ["US"])
        plan = _plan(client, run_id)
        assert (plan["count"], plan["stale_count"]) == (len(expected), len(expected))
        assert all(item["basis_stale_reason"] == "settings_changed" for item in _items(client).values())
        _edit_config(target, countries=countries)
        assert _plan(client, run_id)["count"] == 0
        assert _store_bytes(home) == after_job

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
