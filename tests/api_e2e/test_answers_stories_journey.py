"""0.1.10.7 C: the user-level answers and stories journey over HTTP against the real supervised server.

Synthetic data only; the model is the fixture transport (``bindings.
_test_model_handler``). A posting carrying ``GIGAI-TEST-MODEL: bank <id> else
<asked id>`` gets a model that follows the STORY BANK paragraph of the assess
prompt: with a line for ``<id>`` it settles the requirement, cites
``Story bank <id>: ...`` and asks nothing; without one it asks ``<asked id>``.
``... always`` asks even with the line (a model that did not reuse it).

1. An agent saves a factual reply as an ANSWER (``POST /api/answers``, actor
   agent); a different posting, assessed for ANOTHER profile, that asks the
   same thing is not asked again and cites the answer; the answer then lists
   that job as ``reused``.
2. An agent saves a STORY (``POST /api/stories``); a posting it matches gets
   it in the prompt (the model cites it) and the story lists the job as
   ``used``; a posting it does not match does not get it.
3. A near match comes back as ``bank_suggestions`` (and from
   ``/api/answers/match``); confirming it stores it for the new question.
4. Two writers: a stale ``PUT`` / ``DELETE`` answers 409 with the current
   answer or story; contact-shaped text is refused; edit and delete round
   trip; ``/api/stories/prep`` pools the questions; CSRF and unknown keys.
5. The CLI prints the same shapes as the API (acceptance g).
6. The 0.1.10.5 ``/api/story-bank`` routes are gone.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from gigai.scout.scout_cli import scout_group

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import (
    add_resume,
    resolve_workpad_path,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)

_GCP = "Yes, 4 years, GKE + BigQuery"
_ANSWER_KEYS = {"question_id", "question", "answer", "tag", "jobs", "written_by", "created_at", "updated_at", "revision", "history"}
_STORY_KEYS = {
    "story_id", "title", "company", "role", "period", "raw", "narrative", "tags", "answers_questions", "sources", "jobs",
    "written_by", "created_at", "updated_at", "revision", "history",
}
_STORY = {
    "title": "Cut CI time 60% at Acme",
    "company": "Acme",
    "role": "Staff Engineer",
    "period": "2023",
    "raw": "Our builds took forty minutes, so I moved the runners to Kubernetes and cached the layers.",
    "narrative": {"situation": "Builds took forty minutes.", "action": "Moved the runners to Kubernetes.", "result": "Build time fell 60 percent."},
    "tags": ["ci", "delivery"],
    "answers_questions": ["Tell me about a time you improved a slow process"],
    "sources": [{"question_id": "tooling:kubernetes"}],
}
_JSON = {"Content-Type": "application/json"}


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


def _cli(home: Path, target: Path, *args: str, ok: bool = True) -> dict[str, object]:
    result = CliRunner().invoke(scout_group, [*args, "--json", "--home", str(home), "--target", str(target)])
    assert (result.exit_code == 0) is ok, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def test_answers_and_stories_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        created = client.post("/api/profiles", json={"label": "Second profile", "titles": ["staff backend engineer"]})
        assert created.status_code == 201, created.text
        other = created.json()["profile"]["profile_id"]

        empty = client.get("/api/answers")
        assert empty.status_code == 200 and empty.json() == {"schema_version": "scout-answers-response:1", "answers": [], "total": 0, "tags": []}
        assert client.get("/api/stories").json() == {"schema_version": "scout-stories-response:1", "stories": [], "total": 0, "tags": []}

        # --- 1. an agent's answer on posting A is reused by posting B, for another profile ---------
        # A fetched posting (the fixture's): its answer can be re-assessed at once.
        posting_a = client.post("/api/assess", json={"job": {"job_url": "https://careers.example.test/jobs/9"}}).json()
        assert _questions(posting_a) == ["cloud:gcp"], "nothing answered yet: the model asks"
        saved = client.post(
            "/api/answers",
            json={"question_id": "cloud:gcp", "question": "Do you have GCP experience?", "answer": _GCP, "actor": "agent", "reassess": {"job_identity": posting_a["job"]["job_identity"]}},
        )
        assert saved.status_code == 201, saved.text
        answer = saved.json()["answer"]
        assert set(answer) == _ANSWER_KEYS and saved.json()["schema_version"] == "scout-answers-response:1"
        assert (answer["question_id"], answer["answer"], answer["written_by"], answer["revision"]) == ("cloud:gcp", _GCP, "agent", 1)
        assert [(job["job_identity"], job["kind"]) for job in answer["jobs"]] == [(posting_a["job"]["job_identity"], "answered")]
        assert saved.json()["reassessed"]["result"]["verdict"] == "matched_above_threshold"

        posting_b = _assess(client, "bank cloud:gcp else tooling:google_cloud_platform", "posting B, another company", other)
        assert _questions(posting_b) == [] and f"Story bank cloud:gcp: {_GCP}" in _evidence(posting_b), "a different job, another profile: not asked again"
        assert posting_b["resume"]["profile_id"] == other
        assert "cloud:gcp" in [entry["question_id"] for entry in posting_b["story_bank"]["entries"]], "the answer is in the assessment's basis"
        shown = client.get("/api/answers/cloud%3Agcp")
        assert shown.status_code == 200, shown.text
        assert sorted(job["kind"] for job in shown.json()["answer"]["jobs"]) == ["answered", "reused"]

        # --- 2. an agent's story reaches the posting it matches, and only that one ------------------
        added = client.post("/api/stories", json={**_STORY, "actor": "agent"})
        assert added.status_code == 201, added.text
        story = added.json()["story"]
        story_id = story["story_id"]
        assert set(story) == _STORY_KEYS and story_id.startswith("story:") and added.json()["schema_version"] == "scout-stories-response:1"
        assert {key: story[key] for key in _STORY} == _STORY and (story["written_by"], story["revision"], story["jobs"]) == ("agent", 1, [])

        matching = _assess(client, f"bank {story_id} else tooling:kubernetes", "we run Kubernetes in production")
        assert _questions(matching) == [], "the story is in the prompt: the model settles the requirement with it"
        assert any(text.startswith(f"Story bank {story_id}: Cut CI time 60% at Acme") for text in _evidence(matching)), _evidence(matching)
        unrelated = _assess(client, f"bank {story_id} else skill:figma", "a design systems role with Figma")
        assert _questions(unrelated) == ["skill:figma"], "a posting the story does not match never gets it"
        used = client.get(f"/api/stories/{story_id}").json()["story"]
        assert [(job["job_identity"], job["kind"]) for job in used["jobs"]] == [(matching["job"]["job_identity"], "used")]
        assert used["revision"] == 1, "noting a job is not a write of the story"
        _error(client.post("/api/stories", json={"title": _STORY["title"]}), status=409, code="story_exists")

        # --- 3. a near match is suggested; confirming stores it for the new question ---------------
        asked_anyway = _assess(client, "bank cloud:gcp else tooling:google_cloud_platform always", "posting C")
        assert _questions(asked_anyway) == ["tooling:cloud_google_platform"]
        (suggestion,) = asked_anyway["bank_suggestions"]  # type: ignore[misc]
        assert set(suggestion) == {"question_id", "bank_question_id", "bank_question", "answer", "score"}
        assert (suggestion["bank_question_id"], suggestion["answer"]) == ("cloud:gcp", _GCP)
        match = client.get("/api/answers/match", params={"question_id": "tooling:google_cloud_platform", "question": "Hands-on Google Cloud Platform experience?"})
        assert match.status_code == 200 and match.json()["match"]["bank_question_id"] == "cloud:gcp"
        confirmed = client.post(
            "/api/answers",
            json={"question_id": "tooling:cloud_google_platform", "question": "Hands-on Google Cloud Platform experience?", "answer": suggestion["answer"], "from_bank": "cloud:gcp"},
        )
        assert confirmed.status_code == 201 and confirmed.json()["answer"]["written_by"] == "operator"
        assert client.get("/api/answers/match", params={"question_id": "tooling:google_cloud_platform"}).json()["match"] is None

        # --- 4. two writers, contact data, edit and delete -----------------------------------------
        read = client.get("/api/answers/cloud%3Agcp").json()["answer"]
        by_agent = client.put("/api/answers/cloud%3Agcp", json={"revision": read["revision"], "answer": "Yes, 5 years, GKE and BigQuery", "tag": "cloud platforms"}, headers={"X-GigAI-Actor": "agent"})
        assert by_agent.status_code == 200, by_agent.text
        current = by_agent.json()["answer"]
        assert (current["answer"], current["tag"], current["written_by"], current["revision"]) == ("Yes, 5 years, GKE and BigQuery", "cloud platforms", "agent", read["revision"] + 1)
        stale = _error(client.put("/api/answers/cloud%3Agcp", json={"revision": read["revision"], "answer": "Mine."}), status=409, code="revision_conflict")
        assert stale["answer"] == current, "the 409 carries the answer as it is now"
        assert _error(client.post("/api/answers", json={"question_id": "cloud:gcp", "answer": "Mine.", "revision": read["revision"]}), status=409, code="revision_conflict")["answer"] == current
        _error(client.delete("/api/answers/cloud%3Agcp", params={"revision": read["revision"]}, headers=_JSON), status=409, code="revision_conflict")
        _error(client.put("/api/answers/cloud%3Agcp", json={"answer": "No revision."}), status=422, code="invalid_value")
        _error(client.put("/api/answers/cloud%3Aazure", json={"revision": 1, "answer": "Never answered."}), status=404, code="not_found")

        story_now = client.put(f"/api/stories/{story_id}", json={"revision": 1, "period": "2022-2023", "actor": "agent"})
        assert story_now.status_code == 200 and (story_now.json()["story"]["period"], story_now.json()["story"]["revision"]) == ("2022-2023", 2)
        assert story_now.json()["story"]["title"] == _STORY["title"], "only the given field changed"
        stale_story = _error(client.put(f"/api/stories/{story_id}", json={"revision": 1, "role": "Engineer"}), status=409, code="revision_conflict")
        assert stale_story["story"] == story_now.json()["story"]
        _error(client.delete(f"/api/stories/{story_id}", params={"revision": 1}, headers=_JSON), status=409, code="revision_conflict")
        _error(client.put(f"/api/stories/{story_id}", json={"role": "No revision."}), status=422, code="invalid_value")

        for personal in ("Write to zq7731@example.test.", "Call 555-013-7731.", "See https://zq7731.example/gcp."):
            for refused in (
                client.put("/api/answers/cloud%3Agcp", json={"revision": current["revision"], "answer": personal}),
                client.post("/api/answers", json={"question_id": "cloud:azure", "answer": personal}),
                client.post("/api/stories", json={"title": "Another story", "raw": personal}),
                client.put(f"/api/stories/{story_id}", json={"revision": 2, "narrative": {"result": personal}}),
            ):
                error = _error(refused, status=422, code="personal_info_refused")
                assert "zq7731" not in str(error) and "555-013" not in str(error)
        assert client.get("/api/answers/cloud%3Agcp").json()["answer"] == {**current, "jobs": client.get("/api/answers/cloud%3Agcp").json()["answer"]["jobs"]}
        assert [item["story_id"] for item in client.get("/api/stories").json()["stories"]] == [story_id]

        searched = client.get("/api/answers", params={"q": "bigquery"}).json()
        assert sorted(item["question_id"] for item in searched["answers"]) == ["cloud:gcp", "tooling:cloud_google_platform"] and searched["total"] == 2
        assert [item["question_id"] for item in client.get("/api/answers", params={"tag": "cloud platforms"}).json()["answers"]] == ["cloud:gcp"]
        assert [item["story_id"] for item in client.get("/api/stories", params={"tag": "ci", "q": "forty minutes"}).json()["stories"]] == [story_id]
        assert client.get("/api/stories", params={"q": "figma"}).json()["stories"] == []
        prep = client.get("/api/stories/prep")
        assert prep.status_code == 200 and prep.json() == {
            "schema_version": "scout-stories-response:1",
            "questions": [{"question": "Tell me about a time you improved a slow process", "stories": [{"story_id": story_id, "title": _STORY["title"]}]}],
        }

        # --- 5. the CLI prints the same shapes (acceptance g) ----------------------------------------
        def same(cli: dict[str, object], api: dict[str, object]) -> None:
            assert cli.pop("ok") is True
            assert cli == api

        same(_cli(home, target, "answers", "list"), client.get("/api/answers").json())
        same(_cli(home, target, "answers", "list", "--search", "bigquery", "--tag", "cloud platforms"), client.get("/api/answers", params={"q": "bigquery", "tag": "cloud platforms"}).json())
        same(_cli(home, target, "answers", "show", "cloud:gcp"), client.get("/api/answers/cloud%3Agcp").json())
        same(_cli(home, target, "story", "list"), client.get("/api/stories").json())
        same(_cli(home, target, "story", "show", story_id), client.get(f"/api/stories/{story_id}").json())
        same(_cli(home, target, "story", "prep"), prep.json())
        # A CLI write, read back over HTTP: the same object both ways.
        cli_answer = _cli(home, target, "answers", "save", "language:rust", "--answer-text", "Two years of Rust services.", "--question", "Rust?", "--actor", "agent")
        same(cli_answer, client.get("/api/answers/language%3Arust").json())
        cli_story = _cli(home, target, "story", "save", "--title", "Rewrote the ingest service in Rust", "--raw-text", "I rewrote it and halved memory use.", "--tag", "rust", "--answers", "Tell me about a rewrite", "--actor", "agent")
        same(dict(cli_story), client.get(f"/api/stories/{cli_story['story']['story_id']}").json())  # type: ignore[index]
        # A stale CLI write is refused like a stale PUT, with the same current object.
        cli_stale = _cli(home, target, "answers", "save", "cloud:gcp", "--answer-text", "Mine.", "--revision", str(read["revision"]), ok=False)
        assert cli_stale["error"]["code"] == "revision_conflict" and cli_stale["error"]["answer"]["answer"] == current["answer"]  # type: ignore[index]
        cli_story_stale = _cli(home, target, "story", "save", story_id, "--role", "Engineer", "--revision", "1", ok=False)
        assert cli_story_stale["error"]["code"] == "revision_conflict" and cli_story_stale["error"]["story"]["revision"] == 2  # type: ignore[index]
        assert _cli(home, target, "answers", "save", "cloud:azure", "--answer-text", "Call 555-013-7731.", ok=False)["error"]["code"] == "personal_info_refused"  # type: ignore[index]
        assert _cli(home, target, "answers", "migrate")["migrated"] is False, "already user-level: nothing to do"

        # --- delete: never offered again -------------------------------------------------------------
        deleted = client.delete("/api/answers/cloud%3Agcp", params={"revision": current["revision"]}, headers=_JSON)
        assert deleted.status_code == 200 and deleted.json() == {"schema_version": "scout-answers-response:1", "deleted": "cloud:gcp"}
        _error(client.get("/api/answers/cloud%3Agcp"), status=404, code="not_found")
        again = _assess(client, "bank cloud:gcp else cloud:gcp", "after the delete")
        assert _questions(again) == ["cloud:gcp"], "a deleted answer is never offered again"
        gone = client.delete(f"/api/stories/{story_id}", params={"revision": 2}, headers=_JSON)
        assert gone.status_code == 200 and gone.json() == {"schema_version": "scout-stories-response:1", "deleted": story_id}
        _error(client.get(f"/api/stories/{story_id}"), status=404, code="not_found")
        assert _questions(_assess(client, f"bank {story_id} else tooling:kubernetes", "Kubernetes again, after the delete")) == ["tooling:kubernetes"]

        # CSRF and unknown keys, like every write.
        _error(client.delete("/api/answers/x%3Ay", params={"revision": 1}), status=415, code="unsupported_media_type")
        assert client.post("/api/stories", content=b"{}", headers={"Content-Type": "text/plain"}).status_code == 415
        _error(client.post("/api/stories", json={"title": "T", "nope": 1}), status=422, code="unknown_key")
        _error(client.post("/api/stories", json={"title": "T", "profile_id": other}), status=422, code="unknown_key")
        _error(client.post("/api/answers", json={"question_id": "a:b", "answer": "A", "profile_id": other}), status=422, code="unknown_key")
        _error(client.get("/api/answers", params={"profile_id": other}), status=422, code="unknown_key")
        _error(client.get("/api/stories", params={"nope": "1"}), status=422, code="unknown_key")
        _error(client.post("/api/stories", json={"title": "T", "actor": "robot"}), status=422, code="invalid_value")
        _error(client.post("/api/stories", json={"raw": "No title."}), status=422, code="invalid_value")

        # --- 6. the 0.1.10.5 per-profile routes are gone ---------------------------------------------
        assert client.get("/api/story-bank").status_code == 404
        assert client.post("/api/story-bank", json={"question": "Q", "answer": "A"}).status_code == 404
        assert client.put("/api/story-bank/sharing", json={"share_with": None}).status_code == 404
    finally:
        stop_server(server)
    assert_clean_and_healthy(resolve_workpad_path(home, target), home)
