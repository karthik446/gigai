"""0110-037: ``GET /api/jobs`` suggests answers from the bank of the RUN's profile.

Two profiles on one machine are two people. A run made for profile 2 leaves
a question open; the job is then read while profile 1 is selected. The
suggestions for that run's assessment used to come from the bank of the
profile selected at the time of the read (profile 1's: the reader's own
answers, so nothing leaked, but they are not that run's). They are the
run's profile's now, as on ``GET /api/runs/{run_id}/posting``.

Over HTTP against the real supervised server, fixture boards and model:

1. profile 2 is selected and a run is launched: it is sealed for profile 2;
2. each profile's bank gets a story close to the question the run left open,
   with a different answer;
3. with profile 1 selected, ``GET /api/jobs?url=`` names profile 2 on the
   run's assessment and suggests profile 2's answer only; the same with
   profile 2 selected; the run's own ``/posting`` read agrees;
4. sharing is unchanged (0110-034): without an entry of its own, profile 2
   gets no suggestion from profile 1's bank until profile 2 reads profile
   1's bank, and loses it when that is turned off.
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
_FIRST = "Profile one: AWS, after four years of running services on it."
_SECOND = "Profile two: GCP, after two years of batch workloads on it."


def _select(client: httpx.Client, profile_id: str) -> None:
    selected = client.post("/api/profiles/selection", json={"profile_id": profile_id})
    assert selected.status_code == 200, selected.text
    assert client.get("/api/profiles").json()["selected_profile_id"] == profile_id


def _story(client: httpx.Client, profile_id: str, answer: str) -> dict[str, object]:
    added = client.post("/api/story-bank", json={"profile_id": profile_id, "question": _QUESTION, "answer": answer})
    assert added.status_code == 201, added.text
    return added.json()["entry"]


def _job(client: httpx.Client) -> dict[str, object]:
    response = client.get("/api/jobs", params={"url": _URL})
    assert response.status_code == 200, response.text
    return response.json()


def _suggested(body: dict[str, object]) -> list[tuple[str, str, bool]]:
    return [(item["owner_profile_id"], item["answer"], item["shared"]) for item in body.get("bank_suggestions", [])]  # type: ignore[union-attr]


def test_a_runs_suggestions_come_from_the_runs_profile_whoever_is_selected(
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

        # -- 2. both banks hold a story close to the open question, with different answers -------
        _story(client, first, _FIRST)
        own = _story(client, second, _SECOND)

        # -- 3. read while profile 1 is selected: the run's profile's bank, never the reader's ---
        _select(client, first)
        job = _job(client)
        assert [item["question_id"] for item in job["open_questions"]] == ["cloud:gcp"]  # type: ignore[union-attr]
        assert _suggested(job) == [(second, _SECOND, False)], "profile 2's run is offered profile 2's answer"
        assert [(item["source"], item["run_id"], item["profile_id"]) for item in job["assessments"]] == [("run", run_id, second)]  # type: ignore[union-attr]
        assert _FIRST not in client.get("/api/jobs", params={"url": _URL}).text, "nothing of profile 1's bank is in the response"
        posting = client.get(f"/api/runs/{run_id}/posting", params={"url": _URL}).json()
        assert posting["bank_suggestions"] == job["bank_suggestions"], "the run's own read offers the same"

        _select(client, second)
        assert _suggested(_job(client)) == [(second, _SECOND, False)], "and the same when its own profile is selected"

        # -- 4. sharing rules are 0110-034's ----------------------------------------------------
        _select(client, first)
        deleted = client.delete(
            f"/api/story-bank/{own['question_id']}",
            params={"profile_id": second, "updated_at": own["updated_at"]},
            headers={"Content-Type": "application/json"},
        )
        assert deleted.status_code == 200, deleted.text
        assert _suggested(_job(client)) == [], "profile 2 has no entry: nothing is offered, though the reader's bank has one"

        shared = client.put("/api/story-bank/sharing", json={"profile_id": second, "share_with": first})
        assert shared.status_code == 200, shared.text
        assert _suggested(_job(client)) == [(first, _FIRST, True)], "profile 2 reads profile 1's bank now: offered, marked shared"
        unshared = client.put("/api/story-bank/sharing", json={"profile_id": second, "share_with": None})
        assert unshared.status_code == 200, unshared.text
        assert _suggested(_job(client)) == []

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
