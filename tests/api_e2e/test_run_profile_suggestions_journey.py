"""0.1.10.7 C: ``GET /api/jobs`` suggests the user's answers for a run's open question, whoever is selected.

0110-037 made a run's suggestions come from the bank of the RUN's profile
rather than the selected one's (two profiles had two banks). Answers are the
user's now, so there is one set: a run made for profile 2 that left a
question open is offered the same near match whichever profile is selected
when the job is read, and ``GET /api/runs/{run_id}/posting`` agrees.

Over HTTP against the real supervised server, fixture boards and model:

1. profile 2 is selected and a run is launched: it is sealed for profile 2;
2. an answer close to the question the run left open is saved (no profile);
3. with profile 1 selected, ``GET /api/jobs?url=`` names profile 2 on the
   run's assessment and suggests that answer; the same with profile 2
   selected; the run's own ``/posting`` read agrees;
4. deleting the answer stops the suggestion at once.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

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

_URL = "https://boards.greenhouse.io/acme/jobs/101"
_QUESTION = "Which cloud platform would you prefer to work on?"  # close to the run's "Which platform would you prefer?"
_SECOND = "GCP, after two years of batch workloads on it."


def _select(client: httpx.Client, profile_id: str) -> None:
    selected = client.post("/api/profiles/selection", json={"profile_id": profile_id})
    assert selected.status_code == 200, selected.text
    assert client.get("/api/profiles").json()["selected_profile_id"] == profile_id


def _answer(client: httpx.Client, answer: str) -> dict[str, object]:
    saved = client.post("/api/answers", json={"question_id": "story:cloud_platform_prefer_which", "question": _QUESTION, "answer": answer})
    assert saved.status_code == 201, saved.text
    return saved.json()["answer"]


def _job(client: httpx.Client) -> dict[str, object]:
    response = client.get("/api/jobs", params={"url": _URL})
    assert response.status_code == 200, response.text
    return response.json()


def _suggested(body: dict[str, object]) -> list[tuple[str, str]]:
    return [(item["bank_question_id"], item["answer"]) for item in body.get("bank_suggestions", [])]  # type: ignore[union-attr]


def test_a_runs_suggestions_are_the_users_answers_whoever_is_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        first = client.get("/api/profiles").json()["selected_profile_id"]
        created = client.post("/api/profiles", json={"label": "Someone else", "titles": ["software engineer"]})
        assert created.status_code == 201, created.text
        second = created.json()["profile"]["profile_id"]
        assert second != first

        # -- 1. a run for profile 2 --------------------------------------------------------------
        _select(client, second)
        digest = client.get("/api/config").json()["config_digest"]
        started = client.post("/api/run", json=run_request_body(digest))
        assert started.status_code == 202, started.text
        run_id = started.json()["run_id"]
        assert poll_until_terminal(client, run_id)["status"] == "succeeded"
        assert [(item["run_id"], item["profile_id"]) for item in client.get("/api/runs").json()["runs"]] == [(run_id, second)]

        # -- 2. an answer close to the open question (the user's: no profile) ----------------------
        own = _answer(client, _SECOND)
        offered = [("story:cloud_platform_prefer_which", _SECOND)]
        assert own["question_id"] == "story:cloud_platform_prefer_which"

        # -- 3. read while profile 1 is selected, then profile 2: the same suggestion ---------------
        _select(client, first)
        job = _job(client)
        assert [item["question_id"] for item in job["open_questions"]] == ["cloud:gcp"]  # type: ignore[union-attr]
        assert _suggested(job) == offered
        assert [(item["source"], item["run_id"], item["profile_id"]) for item in job["assessments"]] == [("run", run_id, second)]  # type: ignore[union-attr]
        posting = client.get(f"/api/runs/{run_id}/posting", params={"url": _URL}).json()
        assert posting["bank_suggestions"] == job["bank_suggestions"], "the run's own read offers the same"

        _select(client, second)
        assert _suggested(_job(client)) == offered, "and the same when the run's own profile is selected"

        # -- 4. deleted: nothing is offered ---------------------------------------------------------
        deleted = client.delete(
            f"/api/answers/{own['question_id']}", params={"revision": own["revision"]}, headers={"Content-Type": "application/json"},
        )
        assert deleted.status_code == 200, deleted.text
        assert _suggested(_job(client)) == []

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
