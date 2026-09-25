"""F1-c: the profiles API journey, over HTTP against the real supervisor.

create -> switch -> edit -> list -> archive (with replacement), plus the
error cases (unknown-profile 404, CSRF 403) driven the same way every other
journey in this suite is: through the real, supervised server
(``run_supervisor.start``), never a library call or an in-process fake
server thread. Ends with the after-journey checks (clean workpad, doctor
``journal.index``, per-route latency budget).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.api_e2e.after_journey import assert_clean_and_healthy, timed_request
from tests.api_e2e.harness import (
    add_resume,
    resolve_workpad_path,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)


def test_profiles_create_switch_edit_list_archive_journey(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client

        # -- list: migrate-on-first-read sees one default profile, selected --
        list_response, list_latency = timed_request(
            "GET /api/profiles", lambda: client.get("/api/profiles")
        )
        assert list_response.status_code == 200, list_response.text
        list_body = list_response.json()
        assert len(list_body["profiles"]) == 1
        default_profile = list_body["profiles"][0]
        assert default_profile["origin"] == "migrated_default"
        assert list_body["selected_profile_id"] == default_profile["profile_id"]
        list_latency.assert_within_budget()

        # -- create: a second, operator-named profile ------------------------
        create_response, create_latency = timed_request(
            "POST /api/profiles",
            lambda: client.post(
                "/api/profiles",
                json={"label": "Staff platform engineer", "titles": ["staff platform engineer"]},
            ),
        )
        assert create_response.status_code == 201, create_response.text
        created = create_response.json()["profile"]
        assert created["origin"] == "operator_created"
        assert created["label"] == "Staff platform engineer"
        # Never the resume's own bytes/text.
        assert set(created["resume_ref"]) == {"record_id", "revision_id", "content_sha256"}
        # Creating never switches selection.
        assert client.get("/api/profiles").json()["selected_profile_id"] == default_profile["profile_id"]
        create_latency.assert_within_budget()

        # -- switch: select the newly created profile -------------------------
        switch_response, switch_latency = timed_request(
            "POST /api/profiles/selection",
            lambda: client.post("/api/profiles/selection", json={"profile_id": created["profile_id"]}),
        )
        assert switch_response.status_code == 200, switch_response.text
        assert switch_response.json()["selected_profile_id"] == created["profile_id"]
        switch_latency.assert_within_budget()

        # -- edit: rename + retitle the now-selected profile -------------------
        edit_response, edit_latency = timed_request(
            f"PUT /api/profiles/{created['profile_id']}",
            lambda: client.put(
                f"/api/profiles/{created['profile_id']}",
                json={"label": "Staff platform (renamed)", "titles": ["staff platform engineer", "principal platform engineer"]},
            ),
        )
        assert edit_response.status_code == 200, edit_response.text
        edited = edit_response.json()["profile"]
        assert edited["label"] == "Staff platform (renamed)"
        assert edited["titles"] == ["staff platform engineer", "principal platform engineer"]
        assert edited["revision"] == created["revision"] + 1  # titles edit bumps revision
        edit_latency.assert_within_budget()

        # -- list: both profiles visible, edited one selected -------------------
        relist_response, relist_latency = timed_request(
            "GET /api/profiles", lambda: client.get("/api/profiles")
        )
        assert relist_response.status_code == 200, relist_response.text
        relist_body = relist_response.json()
        assert len(relist_body["profiles"]) == 2
        assert relist_body["selected_profile_id"] == created["profile_id"]
        by_id = {item["profile_id"]: item for item in relist_body["profiles"]}
        assert by_id[created["profile_id"]]["label"] == "Staff platform (renamed)"
        relist_latency.assert_within_budget()

        # -- archive (with replacement): archive the selected profile back
        # onto the default, which must become the new selection in the same
        # request ----------------------------------------------------------
        archive_response, archive_latency = timed_request(
            f"POST /api/profiles/{created['profile_id']}/archive",
            lambda: client.post(
                f"/api/profiles/{created['profile_id']}/archive",
                json={"replacement_profile_id": default_profile["profile_id"]},
            ),
        )
        assert archive_response.status_code == 200, archive_response.text
        assert archive_response.json()["profile"]["state"] == "archived"
        archive_latency.assert_within_budget()

        final_list = client.get("/api/profiles").json()
        assert final_list["selected_profile_id"] == default_profile["profile_id"]
        archived_entry = next(item for item in final_list["profiles"] if item["profile_id"] == created["profile_id"])
        assert archived_entry["state"] == "archived"

        # -- error cases -------------------------------------------------------

        # Unknown profile -> 404, never a 500.
        not_found_response = client.put(
            "/api/profiles/profile_00000000-0000-4000-8000-000000000000", json={"label": "x"}
        )
        assert not_found_response.status_code == 404, not_found_response.text
        assert not_found_response.json()["error"]["code"] == "scout_profile_unavailable"

        # Archiving the last non-archived (selected) profile with no
        # replacement is refused, never a 500 or a silent archive.
        refused_response = client.post(
            f"/api/profiles/{default_profile['profile_id']}/archive", json={}
        )
        assert refused_response.status_code == 409, refused_response.text
        assert refused_response.json()["error"]["code"] == "profile_archive_requires_replacement"

        # CSRF: a no-cors-shaped, evil-origin POST must never mutate state.
        csrf_response = client.post(
            "/api/profiles",
            content=b'{"label": "evil", "titles": ["x"]}',
            headers={"Content-Type": "text/plain", "Origin": "https://evil.example"},
        )
        assert csrf_response.status_code in (403, 415)
        assert len(client.get("/api/profiles").json()["profiles"]) == 2  # unchanged

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
