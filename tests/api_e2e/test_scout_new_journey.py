"""0.1.10.7 M3a: the "what is new" journey (``GET /api/new``, ``GET /api/new/yours``, ``POST /api/new``, ``POST /api/new/seen``).

Through the real supervised server, like every journey in this suite. The
company index is synthetic (one Lever board put straight into the board cache
under the journey's home: no request is made) and the model is the fixture
transport (``bindings._test_model_handler``), so no live model call is made.

1. ``GET /api/new``: first use is the last 7 days, the new posting is listed
   once with its profile tag, the response is labelled ``public-untrusted``
   only, it asks (count, estimate), calls no model and NEVER moves the
   anchor, with or without ``peek``.
2. ``GET /api/new/yours``: the separate call, labelled ``user-private`` only;
   nothing is assessed yet, so it has no evidence.
3. ``POST /api/new {"assess": true}``: the yes. One model call, the row has a
   score, and the anchor moves after the response.
4. ``GET /api/new/yours?since=...``: the evidence of what matches, and no
   posting text; ``GET /api/new?since=...`` lists the same posting.
5. ``GET /api/new`` again: nothing new, and the posting that still needs
   attention.
6. ``POST /api/new/seen``: Mark all seen moves the same anchor.
7. What is refused: an unknown key, a bad ``peek``, an unknown profile, a
   body without ``assess``, a body for ``/seen``, a foreign ``Host``.
8. ``gigai scout new --json`` / ``--yours --json`` print the same objects;
   the documented examples have the responses' keys.
9. A recruiter's address in the posting passes (public); nothing was removed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout import data_labels
from gigai.scout.find_jobs.api import openapi
from gigai.scout.find_jobs.ats_board_clients import BoardCache
from gigai.scout.find_jobs.company_index import CompanyIndex, board_list_url, index_stamp, refresh_company
from gigai.scout.find_jobs.watchlist import add_company_from_url
from gigai.scout.scout_new import check_response, response_labels

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import (
    add_resume,
    resolve_workpad_path,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)

_RECRUITER = "talent.rowan@acme-hiring.example"
_JOB = "https://jobs.lever.co/acmenew/acmenew-00001"
_POSTING = (
    "Acme is hiring a Software Engineer to build reliable Python services. "
    f"Requirements: Python in production; GCP experience is a plus. Remote within the US. Questions? Write to {_RECRUITER}."
)


def _seed_index(home: Path, target: Path) -> None:
    seen = datetime.now(UTC) - timedelta(days=1)
    jobs = [{
        "id": "acmenew-00001", "text": "Software Engineer", "hostedUrl": _JOB, "categories": {"location": "Remote - United States"},
        "country": "US", "workplaceType": "remote", "descriptionPlain": _POSTING, "createdAt": int(seen.timestamp() * 1000),
    }]
    add_company_from_url("https://jobs.lever.co/acmenew", home, target)
    cache = BoardCache(home / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)
    cache.store("lever", board_list_url("lever", "acmenew"), body=json.dumps(jobs).encode("utf-8"), etag=None, last_modified=None, marker=None)
    refresh_company(CompanyIndex.for_home(home), cache, ats="lever", slug="acmenew", observed_at=index_stamp(seen))


def _assert_unmixed(response, expected: str) -> None:
    """One response, one label: in its body and in ``X-GigAI-Labels``."""

    body = response.json()
    check_response(body)
    assert set(response_labels(body)) == {expected}
    assert response.headers[data_labels.LABELS_HEADER] == expected


def test_scout_new_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    _seed_index(home, target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)

        # 1. A read: first use, the posting once, public-untrusted only, the question; three times, because a GET moves nothing.
        for url in ("/api/new", "/api/new?peek=1", "/api/new?peek=0"):
            asked = client.get(url)
            assert asked.status_code == 200, asked.text
            _assert_unmixed(asked, data_labels.PUBLIC_UNTRUSTED)
            ask = asked.json()
            assert (ask["schema_version"], ask["status"], ask["since_source"], ask["peek"]) == ("scout-new:1", "ask", "first_use_7_days", True)
            assert ask["anchor"] == {"last_checked_at": None, "advances": False}
        (row,) = ask["postings"]["rows"]
        (profile,) = ask["profiles"]
        assert (row["job_identity"], row["title"], row["company_slug"], row["work_mode"]) == (_JOB, "Software Engineer", "acmenew", "remote")
        assert row["profile_id"] == profile["profile_id"] and [item["profile_id"] for item in row["profiles"]] == [profile["profile_id"]]
        assert (row["state"], row["score"], row["assessment"]) == ("not_assessed", None, None)
        question = ask["question"]
        assert (question["kind"], question["new"], question["to_assess"]) == ("assess_new", 1, 1)
        assert question["by_profile"] == [{"profile_id": profile["profile_id"], "count": 1}]
        assert question["estimate"] == {"calls": 1, "tokens": None, "seconds": None, "cost": None, "basis_calls": 0}  # no history: the count only
        assert question["yes"]["api"] == {"method": "POST", "path": "/api/new", "body": {"assess": True, "since": ask["since"]}}
        assert question["text"].startswith("1 new posting (") and "Assess them? ~1 calls" in question["text"]
        assert ask["yours_hint"]["api"] == {"method": "GET", "path": f"/api/new/yours?since={ask['since']}"} and ask["yours_hint"]["available"] == 0
        assert client.get("/api/metrics").json()["aggregates"] == []

        # 2. The separate call: user-private only, and nothing to show before an assessment.
        mine = client.get("/api/new/yours")
        assert mine.status_code == 200, mine.text
        _assert_unmixed(mine, data_labels.USER_PRIVATE)
        assert (mine.json()["schema_version"], mine.json()["evidence"]) == ("scout-new-yours:1", [])

        # 3. The yes: assessed from the stored posting text by the fixture model; the row has a score; the anchor moves.
        yes = client.post("/api/new", json={"assess": True})
        assert yes.status_code == 200, yes.text
        _assert_unmixed(yes, data_labels.PUBLIC_UNTRUSTED)
        answered = yes.json()
        assert answered["status"] == "new" and answered["question"] is None and answered["since_source"] == "first_use_7_days"
        assert answered["assessed"] == {"requested": 1, "assessed": 1, "failed": [], "stopped": None, "fetched_on_demand": 0}
        assert answered["anchor"] == {"last_checked_at": None, "advances": True}
        (scored,) = answered["postings"]["rows"]
        assert scored["state"] != "not_assessed" and scored["score_kind"] == "assessment" and isinstance(scored["score"], int)
        assert scored["assessment"]["requirements"] >= 1 and "matches" not in scored
        # The combined 0.1.10.8 contract, every side's fields on the one response: U2 (fetched_on_demand, above), U4 (the row's
        # company_name and tag_pending), U3 (the row's group / score text / tailored flag, counts.only_stale, the stale question, ranking).
        assert (scored["company_name"], scored["tag_pending"]) == ("Acmenew", False)  # no index name: the slug rule; a specific title
        assert (scored["sort_group"], scored["tailored"], scored["stale_label"], scored["assessment_detail"]) == ("current", False, None, True)
        assert scored["score_text"] == f"Needs your answers · fit {scored['fit']}% · {scored['assessment']['met']} of {scored['assessment']['requirements']} requirements · not ranked yet"
        assert (answered["counts"]["to_assess"], answered["counts"]["only_stale"]) == (0, 0)
        assert (answered["stale_question"], answered["reassessed"]) == (None, None)
        assert answered["ranking"]["by_profile"] == [{"profile_id": profile["profile_id"], "ranked": 0, "total": 1}]
        (aggregate,) = client.get("/api/metrics?kind=assess").json()["aggregates"]
        assert aggregate["calls"] == 1

        # 4. What matches, for the same posting: the user's own evidence, by job identity, and no posting text.
        hint = answered["yours_hint"]
        assert hint["available"] == 1 and hint["api"] == {"method": "GET", "path": f"/api/new/yours?since={answered['since']}"}
        evidence = client.get(hint["api"]["path"])
        assert evidence.status_code == 200, evidence.text
        _assert_unmixed(evidence, data_labels.USER_PRIVATE)
        (cited,) = evidence.json()["evidence"]
        assert cited["job_identity"] == _JOB and cited["profile_id"] == profile["profile_id"] and cited["lines"]
        assert "Software Engineer" not in evidence.text and "acmenew" not in evidence.text.replace(_JOB, "") and _RECRUITER not in evidence.text
        again = client.get(f"/api/new?since={answered['since']}").json()
        assert [item["job_identity"] for item in again["postings"]["rows"]] == [_JOB] and again["status"] == "new"

        # 5. Nothing new: the yes moved the anchor to the time it read the postings.
        got = client.get("/api/new")
        _assert_unmixed(got, data_labels.PUBLIC_UNTRUSTED)
        nothing = got.json()
        assert nothing["status"] == "nothing_new" and nothing["since_source"] == "anchor" and nothing["since"] == answered["checked_at"]
        assert nothing["message"].startswith("Nothing new since your last check (")
        assert [item["job_identity"] for item in nothing["postings"]["rows"]] == [_JOB]
        assert len(client.get("/api/new/yours").json()["evidence"]) == 1  # the same posting, still

        # 6. Mark all seen moves the same anchor.
        seen = client.post("/api/new/seen", json={})
        assert seen.status_code == 200, seen.text
        marked = seen.json()
        assert (marked["schema_version"], marked["set_by"]) == ("scout-new-seen:1", "mark_all_seen")
        assert marked["previous"] == answered["checked_at"] and marked["last_checked_at"] > marked["previous"]
        assert seen.headers[data_labels.LABELS_HEADER] == data_labels.NO_LABELS
        assert client.get("/api/new").json()["since"] == marked["last_checked_at"]

        # 7. What is refused.
        for url, status, code in (
            ("/api/new?bogus=1", 422, "unknown_key"),
            ("/api/new?peek=maybe", 422, "invalid_value"),
            ("/api/new?profile_id=profile_00000000-0000-4000-8000-00000000dead", 404, "profile_not_found"),
            ("/api/new?since=last-tuesday", 422, "invalid_value"),
            ("/api/new/yours?peek=1", 422, "unknown_key"),
            ("/api/new/yours?profile_id=profile_00000000-0000-4000-8000-00000000dead", 404, "profile_not_found"),
        ):
            refused = client.get(url)
            assert refused.status_code == status and refused.json()["error"]["code"] == code, refused.text
        for payload, code in (({}, "wrong_type"), ({"assess": "yes"}, "wrong_type"), ({"assess": True, "bogus": 1}, "unknown_key"), ({"assess": False, "since": "last tuesday"}, "invalid_value")):
            refused = client.post("/api/new", json=payload)
            assert refused.status_code == 422 and refused.json()["error"]["code"] == code, refused.text
        # 0110-8-08: the second yes is its own key, true or false; with nothing stale it assesses nothing and says so.
        refused = client.post("/api/new", json={"assess": False, "reassess_stale": "yes", "peek": True})
        assert refused.status_code == 422 and refused.json()["error"]["code"] == "wrong_type", refused.text
        again = client.post("/api/new", json={"assess": False, "reassess_stale": True, "peek": True})
        assert again.status_code == 200, again.text
        assert (again.json()["reassessed"], again.json()["stale_question"], again.json()["counts"]["only_stale"]) == (None, None, 0)
        _assert_unmixed(again, data_labels.PUBLIC_UNTRUSTED)
        assert client.post("/api/new/seen", json={"at": "2030-01-01T00:00:00Z"}).status_code == 422
        for url in ("/api/new", "/api/new/yours"):
            assert client.get(url, headers={"Host": "evil.example"}).status_code == 403

        # 8. The CLI prints the same objects (a peek, so nothing moves); the documented examples have their keys.
        def cli_json(*args: str) -> dict[str, object]:
            result = CliRunner().invoke(cli, ["scout", "new", *args, "--home", str(home), "--target", str(target), "--json"])
            assert result.exit_code == 0, result.output
            return json.loads(result.stdout)

        for args, url in ((("--peek",), "/api/new"), (("--yours",), "/api/new/yours")):
            printed, served = cli_json(*args), client.get(url).json()
            printed.pop("checked_at"), served.pop("checked_at")
            assert printed == served, url
        for key in (("GET", "/api/new"), ("POST", "/api/new")):
            documented = next(route for route in openapi.ROUTES if route.key == key).example
            assert set(documented) == set(answered), key
            assert set(documented["postings"]["rows"][0]) == set(scored), key  # type: ignore[index]
            assert set(documented["postings"]) == set(answered["postings"]) and set(documented["yours_hint"]) == set(hint), key  # type: ignore[arg-type]
            assert set(documented["counts"]) == set(answered["counts"]) and set(documented["profiles"][0]) == set(profile), key  # type: ignore[arg-type,index]
        asking = next(route for route in openapi.ROUTES if route.key == ("GET", "/api/new")).example
        assert set(asking["question"]) == set(question) and set(asking["question"]["estimate"]) == set(question["estimate"])  # type: ignore[arg-type,index]
        yours_example = next(route for route in openapi.ROUTES if route.key == ("GET", "/api/new/yours")).example
        assert set(yours_example) == set(evidence.json()) and set(yours_example["evidence"][0]) == set(cited)  # type: ignore[index]
        seen_example = next(route for route in openapi.ROUTES if route.key == ("POST", "/api/new/seen")).example
        assert set(seen_example) == set(marked)

        # 9. The posting's own contact line is public and passes; nothing in these responses was contact-shaped.
        assert _RECRUITER in scored["description"] and "_redactions" not in answered and "_redactions" not in nothing
    finally:
        stop_server(server)
    assert_clean_and_healthy(workpad, home)
