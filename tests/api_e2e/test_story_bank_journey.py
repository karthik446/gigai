"""0110-034: the story bank journey over HTTP against the real supervised server.

Synthetic data only; the model is the fixture transport (``bindings.
_test_model_handler``). A posting carrying ``GIGAI-TEST-MODEL: bank <id> else
<asked id>`` gets a model that follows the STORY BANK paragraph of the assess
prompt: with a bank line for ``<id>`` it settles the requirement, cites
``Story bank <id>: ...`` and asks nothing; without one it asks ``<asked id>``.
``... always`` asks even with the line (a model that did not reuse it).

1. An agent adds a STAR story (``POST /api/story-bank``, actor agent); a later
   assessment of a posting whose requirement it covers asks nothing and cites
   the story; the entry then lists that job as ``reused``.
2. An answer given on posting A (``POST /api/answers``) settles a reworded
   question on posting B.
3. A near match comes back as ``bank_suggestions`` (and from ``/match``);
   confirming it stores it as the answer to the new question.
4. A second profile (another person) sees none of it (list, match, answers,
   its own assessment) until sharing is set, and loses it when unset.
5. Two writers: a stale ``PUT`` / ``DELETE`` answers 409 with the current entry;
   personal information is refused; edit and delete round trip; CSRF.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import (
    add_resume,
    resolve_workpad_path,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)

_STORY = (
    "Situation: a 4 TB Postgres primary was close to its disk limit. Task: move it to a new cluster with no downtime. "
    "Action: led three engineers through a dual-write cut-over. Result: zero lost writes."
)
_STORY_ID = "story:database_led_migration"
_GCP = "Yes: two years of batch workloads on GCP."


def _posting(marker: str, note: str) -> dict[str, object]:
    return {"job": {"job_text": f"Platform role ({note}). Requirements: Python; hands-on platform experience. GIGAI-TEST-MODEL: {marker}"}}


def _error(response: httpx.Response, *, status: int, code: str) -> dict[str, object]:
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code, error
    return error


def _assess(client: httpx.Client, marker: str, note: str, profile_id: str | None = None) -> dict[str, object]:
    body = _posting(marker, note)
    if profile_id is not None:
        body["resume"] = {"profile_id": profile_id}
    response = client.post("/api/assess", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _questions(assessed: dict[str, object]) -> list[str]:
    return [item["question_id"] for item in assessed["result"].get("structured_questions", [])]  # type: ignore[index, union-attr]


def _evidence(assessed: dict[str, object]) -> list[str]:
    return [text for row in assessed["result"]["matrix"] for text in row["resume_evidence"]]  # type: ignore[index]


def test_story_bank_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        default = client.get("/api/profiles").json()["selected_profile_id"]

        empty = client.get("/api/story-bank")
        assert empty.status_code == 200, empty.text
        assert empty.json()["entries"] == [] and empty.json()["profile_id"] == default
        assert empty.json()["sharing"] == {"share_with": None, "read_by": [], "profiles": []}

        # --- 1. an agent adds a STAR story; a later assessment reuses it -------------------------
        before = _assess(client, f"bank {_STORY_ID} else skill:database_migration", "before the story")
        assert _questions(before) == ["skill:database_migration"], "nothing in the bank yet: the model asks"

        added = client.post(
            "/api/story-bank",
            json={"question": "Tell me about a database migration you led", "answer": _STORY, "actor": "agent"},
        )
        assert added.status_code == 201, added.text
        story = added.json()["entry"]
        assert (story["question_id"], story["tag"], story["written_by"], story["revision"]) == (_STORY_ID, "leadership", "agent", 1)
        assert story["postings"] == [] and story["history"][0]["action"] == "added"

        after = _assess(client, f"bank {_STORY_ID} else skill:database_migration", "after the story")
        assert _questions(after) == [], "the story covers the requirement: no new question"
        assert after["result"]["verdict"] == "matched_above_threshold"  # type: ignore[index]
        assert any(text.startswith(f"Story bank {_STORY_ID}: Situation: a 4 TB Postgres primary") for text in _evidence(after)), _evidence(after)
        shown = client.get(f"/api/story-bank/{_STORY_ID}")
        assert shown.status_code == 200, shown.text
        assert [(p["job_identity"], p["kind"]) for p in shown.json()["entry"]["postings"]] == [(after["job"]["job_identity"], "reused")]  # type: ignore[index]
        _error(client.post("/api/story-bank", json={"question": "Tell me about a database migration you led", "answer": "Again."}), status=409, code="story_exists")

        # --- 2. an answer on posting A settles a reworded question on posting B --------------------
        posting_a = client.post("/api/assess", json={"job": {"job_url": "https://careers.example.test/jobs/9"}}).json()
        assert _questions(posting_a) == ["cloud:gcp"]
        answered = client.post(
            "/api/answers",
            json={
                "question_id": "cloud:gcp", "question": "Which platform would you prefer?", "answer": _GCP,
                "reassess": {"job_identity": posting_a["job"]["job_identity"]},
            },
        )
        assert answered.status_code == 201, answered.text
        assert answered.json()["profile_id"] == default and answered.json()["reassessed"]["result"]["verdict"] == "matched_above_threshold"
        posting_b = _assess(client, "bank cloud:gcp else tooling:google_cloud_platform", "posting B")
        assert _questions(posting_b) == [] and f"Story bank cloud:gcp: {_GCP}" in _evidence(posting_b)
        gcp = client.get("/api/story-bank/cloud:gcp").json()["entry"]
        assert gcp["question"] == "Which platform would you prefer?" and gcp["written_by"] == "operator"
        assert sorted(p["kind"] for p in gcp["postings"]) == ["answered", "reused"]

        # --- 3. a near match is suggested; confirming stores it for the new question ---------------
        asked_anyway = _assess(client, "bank cloud:gcp else tooling:google_cloud_platform always", "posting C")
        assert _questions(asked_anyway) == ["tooling:cloud_google_platform"]
        (suggestion,) = asked_anyway["bank_suggestions"]  # type: ignore[misc]
        assert (suggestion["bank_question_id"], suggestion["answer"], suggestion["shared"]) == ("cloud:gcp", _GCP, False)
        match = client.get("/api/story-bank/match", params={"question_id": "tooling:google_cloud_platform", "question": "Do you have hands-on Google Cloud Platform experience?"})
        assert match.status_code == 200 and match.json()["match"]["bank_question_id"] == "cloud:gcp"
        listed = client.get("/api/assessments").json()["items"]
        assert [item.get("bank_suggestions", [{}])[0].get("bank_question_id") for item in listed if item["job"]["job_identity"] == asked_anyway["job"]["job_identity"]] == ["cloud:gcp"]
        confirmed = client.post(
            "/api/answers",
            json={
                "question_id": "tooling:cloud_google_platform", "question": "Do you have hands-on Google Cloud Platform experience?",
                "answer": suggestion["answer"], "from_bank": suggestion["bank_question_id"],
            },
        )
        assert confirmed.status_code == 201, confirmed.text
        stored = client.get("/api/story-bank/tooling:cloud_google_platform").json()["entry"]
        assert (stored["answer"], stored["confirmed_from"]) == (_GCP, "cloud:gcp")
        assert client.get("/api/story-bank/match", params={"question_id": "tooling:google_cloud_platform"}).json()["match"] is None

        # --- 4. another person's profile sees none of it until shared ----------------------------
        created = client.post("/api/profiles", json={"label": "Someone else", "titles": ["staff backend engineer"]})
        assert created.status_code == 201, created.text
        other = created.json()["profile"]["profile_id"]

        def other_sees() -> dict[str, object]:
            assessed = _assess(client, "bank cloud:gcp else tooling:google_cloud_platform", "the other person's posting", other)
            return {
                "list": [entry["question_id"] for entry in client.get("/api/story-bank", params={"profile_id": other}).json()["entries"]],
                "answers": [item["question_id"] for item in client.get("/api/answers", params={"profile_id": other}).json()["answers"]],
                "match": client.get("/api/story-bank/match", params={"profile_id": other, "question_id": "cloud:gcp_bigquery"}).json()["match"] is not None,
                "asked": _questions(assessed),
                "suggested": [item["bank_question_id"] for item in assessed.get("bank_suggestions", [])],  # type: ignore[union-attr]
            }

        hidden = {"list": [], "answers": [], "match": False, "asked": ["tooling:cloud_google_platform"], "suggested": []}
        assert other_sees() == hidden
        _error(client.get(f"/api/story-bank/{_STORY_ID}", params={"profile_id": other}), status=404, code="not_found")
        _error(client.put("/api/story-bank/cloud:gcp", json={"profile_id": other, "updated_at": gcp["updated_at"], "answer": "X"}), status=404, code="not_found")

        shared = client.put("/api/story-bank/sharing", json={"profile_id": other, "share_with": default})
        assert shared.status_code == 200 and shared.json()["sharing"]["share_with"] == default
        assert client.get("/api/story-bank").json()["sharing"]["read_by"] == [other]
        seen = other_sees()
        assert seen["list"] == ["cloud:gcp", _STORY_ID, "tooling:cloud_google_platform"] and seen["asked"] == [] and seen["match"] is True
        assert all(entry["shared"] for entry in client.get("/api/story-bank", params={"profile_id": other}).json()["entries"])
        assert [e["question_id"] for e in client.get("/api/story-bank").json()["entries"]] == seen["list"], "one way: the owner reads nothing new"

        unshared = client.put("/api/story-bank/sharing", json={"profile_id": other, "share_with": None})
        assert unshared.status_code == 200 and unshared.json()["sharing"]["share_with"] is None
        assert other_sees() == hidden

        # --- 5. two writers, privacy, edit and delete ------------------------------------------
        read = client.get("/api/story-bank/cloud:gcp").json()["entry"]
        by_agent = client.put("/api/story-bank/cloud:gcp", json={"updated_at": read["updated_at"], "answer": "Yes: three years on GCP.", "tag": "cloud platforms"}, headers={"X-GigAI-Actor": "agent"})
        assert by_agent.status_code == 200, by_agent.text
        entry = by_agent.json()["entry"]
        assert (entry["answer"], entry["tag"], entry["written_by"], entry["edited"]) == ("Yes: three years on GCP.", "cloud platforms", "agent", True)
        assert entry["revision"] == read["revision"] + 1 and entry["updated_at"] != read["updated_at"]
        # The UI still holds what it read before the agent wrote.
        stale = _error(client.put("/api/story-bank/cloud:gcp", json={"updated_at": read["updated_at"], "answer": "Mine."}), status=409, code="story_bank_changed")
        assert stale["entry"]["answer"] == "Yes: three years on GCP." and stale["entry"]["written_by"] == "agent"  # type: ignore[index]
        _error(client.delete("/api/story-bank/cloud:gcp", params={"updated_at": read["updated_at"]}, headers={"Content-Type": "application/json"}), status=409, code="story_bank_changed")
        _error(client.put("/api/story-bank/cloud:gcp", json={"answer": "No updated_at."}), status=422, code="invalid_value")

        for personal in ("Write to zq7731@example.test.", "Call 555-013-7731.", "See https://zq7731.example/gcp."):
            for refused in (
                client.put("/api/story-bank/cloud:gcp", json={"updated_at": entry["updated_at"], "answer": personal}),
                client.post("/api/story-bank", json={"question": "A new one", "answer": personal}),
                client.post("/api/answers", json={"question_id": "cloud:azure", "answer": personal}),
            ):
                error = _error(refused, status=422, code="personal_info_refused")
                assert "zq7731" not in str(error) and "555-013" not in str(error)
        assert client.get("/api/story-bank/cloud:gcp").json()["entry"]["answer"] == "Yes: three years on GCP."
        assert "cloud:azure" not in [item["question_id"] for item in client.get("/api/answers").json()["answers"]]

        searched = client.get("/api/story-bank", params={"q": "postgres"}).json()
        assert [e["question_id"] for e in searched["entries"]] == [_STORY_ID] and searched["total"] == 3
        assert [e["question_id"] for e in client.get("/api/story-bank", params={"tag": "cloud platforms"}).json()["entries"]] == ["cloud:gcp"]

        deleted = client.delete("/api/story-bank/cloud:gcp", params={"updated_at": entry["updated_at"]}, headers={"Content-Type": "application/json"})
        assert deleted.status_code == 200 and deleted.json()["deleted"] == "cloud:gcp"
        _error(client.get("/api/story-bank/cloud:gcp"), status=404, code="not_found")
        assert "cloud:gcp" not in [item["question_id"] for item in client.get("/api/answers").json()["answers"]]
        again = _assess(client, "bank cloud:gcp else cloud:gcp", "after the delete")
        assert _questions(again) == ["cloud:gcp"], "a deleted answer is never offered again"

        # CSRF and unknown keys, like every write.
        _error(client.delete("/api/story-bank/x:y", params={"updated_at": "t"}), status=415, code="unsupported_media_type")
        assert client.post("/api/story-bank", content=b"{}", headers={"Content-Type": "text/plain"}).status_code == 415
        _error(client.post("/api/story-bank", json={"question": "Q", "answer": "A", "nope": 1}), status=422, code="unknown_key")
        _error(client.get("/api/story-bank", params={"nope": "1"}), status=422, code="unknown_key")
        _error(client.get("/api/story-bank", params={"profile_id": "profile_nobody"}), status=404, code="profile_not_found")
        _error(client.post("/api/story-bank", json={"question": "Q", "answer": "A", "actor": "robot"}), status=422, code="invalid_value")
    finally:
        stop_server(server)
    assert_clean_and_healthy(resolve_workpad_path(home, target), home)
