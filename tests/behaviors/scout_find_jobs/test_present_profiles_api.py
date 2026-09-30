"""F1-c: the Scout profiles API (``/api/profiles``, ``/api/profiles/{id}``,
``/api/profiles/{id}/archive``, ``/api/profiles/selection``).

Design source: ``orchestrator/docs/v0.1.9/spikes/S25-scout-interested-
profiles.md`` (Q6, Amendment A1, the Packet plan's F1-c row). Drives the
real ``ScoutFindJobsBackend``/``serve()`` HTTP server (mirrors
``test_present_logging.py``'s own real-backend server fixture) against a
real, journal-authoritative workpad built by
``tests.support.scout_profile_fixtures.build_gig_with_resume`` -- these
routes call ``gigai.scout.profile_records`` directly (not through the
``Backend`` protocol), so a fake ``Backend`` double (as ``test_present_csrf.
py`` uses for the run/discover/setup routes) cannot exercise them; only a
real gig with a real committed resume can.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest

from gigai.canonical import digest_imported_bytes
from gigai.private_records import create_record, import_reference
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve
from gigai.scout.profile_records import ensure_default_profile

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume, uuids


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path)


@pytest.fixture
def running_server(fx: ProfileFixtureGig):
    backend = ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target)
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}") as client:
            yield client, fx, port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _origin(port: int) -> dict[str, str]:
    return {"Origin": f"http://127.0.0.1:{port}"}


def _add_second_resume(fx: ProfileFixtureGig, *, text: bytes = b"# Second Resume\n\nBackend.\n") -> tuple[str, str]:
    """A second, distinct committed resume record for this gig -- for the
    "edit with an explicit resume_record_id/resume_revision_id" tests."""

    digest = digest_imported_bytes(text)
    resume_path = fx.target.parent / "resume-second.md"
    resume_path.write_bytes(text)
    imported = import_reference(
        home_root=fx.home_root,
        requested_target=fx.target,
        gig_id=fx.created.gig_id,
        kind="resume",
        source=resume_path,
        operation_key=f"scout-resume-add:resume-second.md:{digest}",
        uuid_factory=uuids(90),
    )
    record = create_record(
        home_root=fx.home_root,
        requested_target=fx.target,
        gig_id=fx.created.gig_id,
        kind="imported_reference",
        content_family="g45_reference",
        content_id=imported.item_id,
        actor={"kind": "operator", "id": "local-user"},
        origin="imported",
        operation_key=f"scout-resume-record-second:{imported.item_id}",
        uuid_factory=uuids(91),
    )
    return record.record_id, record.revision_id


# ---------------------------------------------------------------------------
# GET /api/profiles
# ---------------------------------------------------------------------------


def test_list_profiles_migrates_on_first_read(running_server) -> None:
    """``GET /api/profiles`` reads through ``selected_profile()``, which
    itself runs the F1-a migrate-on-first-read (``ensure_default_profile``)
    -- the fixture's gig already has a committed resume + ``find-jobs.json``,
    so the very first ``GET`` sees one migrated default profile, already
    selected, with no separate migration step required."""

    client, fx, port = running_server
    response = client.get("/api/profiles")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "scout-profiles-response:1"
    assert len(body["profiles"]) == 1
    assert body["profiles"][0]["origin"] == "migrated_default"
    assert body["selected_profile_id"] == body["profiles"][0]["profile_id"]


def test_list_profiles_no_find_jobs_json_has_no_profiles_and_no_selection(tmp_path: Path) -> None:
    """A gig with no ``find-jobs.json``/resume yet (so nothing to migrate)
    lists as genuinely empty, never a fabricated profile."""

    fx = build_gig_with_resume(tmp_path, write_config=False)
    backend = ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target)
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}") as client:
            response = client.get("/api/profiles")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["profiles"] == []
            assert body["selected_profile_id"] is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_list_profiles_after_migration_shows_the_default_profile_selected(running_server) -> None:
    client, fx, port = running_server
    migration = ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert migration is not None

    response = client.get("/api/profiles")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["profiles"]) == 1
    assert body["profiles"][0]["profile_id"] == migration.profile_id
    assert body["profiles"][0]["origin"] == "migrated_default"
    assert body["selected_profile_id"] == migration.profile_id
    # Never the resume's own bytes/text.
    resume_ref = body["profiles"][0]["resume_ref"]
    assert set(resume_ref) == {"record_id", "revision_id", "content_sha256"}


# ---------------------------------------------------------------------------
# POST /api/profiles (create)
# ---------------------------------------------------------------------------


def test_create_profile_happy_path_defaults_queries_to_titles(running_server) -> None:
    client, fx, port = running_server
    ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)

    response = client.post(
        "/api/profiles",
        json={"label": "Staff backend", "titles": ["staff backend engineer"]},
        headers=_origin(port),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    profile = body["profile"]
    assert profile["label"] == "Staff backend"
    assert profile["titles"] == ["staff backend engineer"]
    assert profile["queries"] == ["staff backend engineer"]
    assert profile["titles_to_avoid"] == []
    assert profile["origin"] == "operator_created"
    assert profile["state"] == "active"
    # Defaults to the selected profile's own resume_ref (no resume_record_id given).
    assert profile["resume_ref"]["record_id"] == fx.resume_record_id
    assert profile["resume_ref"]["revision_id"] == fx.resume_revision_id

    listed = client.get("/api/profiles").json()
    assert len(listed["profiles"]) == 2
    # Creating never switches selection.
    assert listed["selected_profile_id"] != profile["profile_id"]


def test_create_profile_with_explicit_resume_record(running_server) -> None:
    client, fx, port = running_server
    ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    record_id, revision_id = _add_second_resume(fx)

    response = client.post(
        "/api/profiles",
        json={
            "label": "Second resume profile",
            "titles": ["staff platform engineer"],
            "resume_record_id": record_id,
            "resume_revision_id": revision_id,
        },
        headers=_origin(port),
    )
    assert response.status_code == 201, response.text
    profile = response.json()["profile"]
    assert profile["resume_ref"]["record_id"] == record_id
    assert profile["resume_ref"]["revision_id"] == revision_id
    assert profile["resume_ref"]["record_id"] != fx.resume_record_id


def test_create_profile_no_default_resume_and_none_given_is_400(tmp_path: Path) -> None:
    """No ``find-jobs.json`` -> nothing to migrate -> no selected profile to
    fall back to; with no ``resume_record_id``/``resume_revision_id`` given
    either, create has no resume to pin -- 400, never a 500."""

    fx = build_gig_with_resume(tmp_path, write_config=False)
    backend = ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target)
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}") as client:
            response = client.post(
                "/api/profiles",
                json={"label": "No resume", "titles": ["x"]},
                headers=_origin(port),
            )
            assert response.status_code == 400, response.text
            body = response.json()
            assert body["error"]["code"] == "invalid_value"
            assert "resume_record_id" in body["error"]["field_errors"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_create_profile_empty_titles_is_400(running_server) -> None:
    client, fx, port = running_server
    ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    response = client.post(
        "/api/profiles",
        json={"label": "Bad", "titles": []},
        headers=_origin(port),
    )
    assert response.status_code == 400, response.text
    assert "titles" in response.json()["error"]["field_errors"]


def test_create_profile_missing_label_is_400(running_server) -> None:
    client, fx, port = running_server
    ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    response = client.post(
        "/api/profiles",
        json={"titles": ["x"]},
        headers=_origin(port),
    )
    assert response.status_code == 400, response.text
    assert "label" in response.json()["error"]["field_errors"]


def test_create_profile_non_object_body_is_422(running_server) -> None:
    client, fx, port = running_server
    response = client.post(
        "/api/profiles",
        content=json.dumps(["not", "an", "object"]).encode("utf-8"),
        headers={**_origin(port), "Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "wrong_type"


# ---------------------------------------------------------------------------
# PUT /api/profiles/{profile_id} (edit)
# ---------------------------------------------------------------------------


def test_edit_profile_label_rename(running_server) -> None:
    client, fx, port = running_server
    migration = ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert migration is not None

    response = client.put(
        f"/api/profiles/{migration.profile_id}",
        json={"label": "renamed"},
        headers=_origin(port),
    )
    assert response.status_code == 200, response.text
    profile = response.json()["profile"]
    assert profile["label"] == "renamed"
    assert profile["revision"] == migration.revision  # rename never bumps revision


def test_edit_profile_titles_bumps_revision(running_server) -> None:
    client, fx, port = running_server
    migration = ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert migration is not None

    response = client.put(
        f"/api/profiles/{migration.profile_id}",
        json={"titles": ["new title"]},
        headers=_origin(port),
    )
    assert response.status_code == 200, response.text
    profile = response.json()["profile"]
    assert profile["titles"] == ["new title"]
    assert profile["revision"] == migration.revision + 1


def test_edit_profile_with_explicit_resume(running_server) -> None:
    client, fx, port = running_server
    migration = ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert migration is not None
    record_id, revision_id = _add_second_resume(fx)

    response = client.put(
        f"/api/profiles/{migration.profile_id}",
        json={"resume_record_id": record_id, "resume_revision_id": revision_id},
        headers=_origin(port),
    )
    assert response.status_code == 200, response.text
    resume_ref = response.json()["profile"]["resume_ref"]
    assert resume_ref["record_id"] == record_id
    assert resume_ref["revision_id"] == revision_id


def test_edit_unknown_profile_is_404(running_server) -> None:
    client, fx, port = running_server
    response = client.put(
        "/api/profiles/profile_00000000-0000-4000-8000-000000000000",
        json={"label": "x"},
        headers=_origin(port),
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "scout_profile_unavailable"


def test_edit_profile_empty_titles_is_400(running_server) -> None:
    client, fx, port = running_server
    migration = ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert migration is not None
    response = client.put(
        f"/api/profiles/{migration.profile_id}",
        json={"titles": []},
        headers=_origin(port),
    )
    assert response.status_code == 400, response.text
    assert "titles" in response.json()["error"]["field_errors"]


# ---------------------------------------------------------------------------
# POST /api/profiles/{profile_id}/archive
# ---------------------------------------------------------------------------


def test_archive_refuses_without_replacement_when_selected(running_server) -> None:
    client, fx, port = running_server
    migration = ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert migration is not None

    response = client.post(
        f"/api/profiles/{migration.profile_id}/archive",
        json={},
        headers=_origin(port),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "profile_archive_requires_replacement"

    # Never actually archived.
    listed = client.get("/api/profiles").json()
    assert listed["profiles"][0]["state"] == "active"


def test_archive_last_nonarchived_profile_is_refused(running_server) -> None:
    """The only profile in the gig IS the selected one -- archiving it has
    no possible replacement, so it is refused the same way (409
    ``profile_archive_requires_replacement``), never a 500 or a silent
    archive-with-no-selection."""

    client, fx, port = running_server
    migration = ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert migration is not None
    assert len(client.get("/api/profiles").json()["profiles"]) == 1

    response = client.post(
        f"/api/profiles/{migration.profile_id}/archive",
        json={"replacement_profile_id": "profile_00000000-0000-4000-8000-000000000000"},
        headers=_origin(port),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "profile_archive_requires_replacement"


def test_archive_with_valid_replacement_switches_selection(running_server) -> None:
    client, fx, port = running_server
    migration = ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert migration is not None
    created = client.post(
        "/api/profiles",
        json={"label": "second", "titles": ["staff backend engineer"]},
        headers=_origin(port),
    ).json()["profile"]

    response = client.post(
        f"/api/profiles/{migration.profile_id}/archive",
        json={"replacement_profile_id": created["profile_id"]},
        headers=_origin(port),
    )
    assert response.status_code == 200, response.text
    assert response.json()["profile"]["state"] == "archived"

    listed = client.get("/api/profiles").json()
    assert listed["selected_profile_id"] == created["profile_id"]


def test_archive_unknown_profile_is_404(running_server) -> None:
    client, fx, port = running_server
    response = client.post(
        "/api/profiles/profile_00000000-0000-4000-8000-000000000000/archive",
        json={},
        headers=_origin(port),
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "scout_profile_unavailable"


# ---------------------------------------------------------------------------
# POST /api/profiles/selection (switch)
# ---------------------------------------------------------------------------


def test_switch_selection_happy_path(running_server) -> None:
    client, fx, port = running_server
    migration = ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert migration is not None
    created = client.post(
        "/api/profiles",
        json={"label": "second", "titles": ["staff backend engineer"]},
        headers=_origin(port),
    ).json()["profile"]

    response = client.post(
        "/api/profiles/selection",
        json={"profile_id": created["profile_id"]},
        headers=_origin(port),
    )
    assert response.status_code == 200, response.text
    assert response.json()["selected_profile_id"] == created["profile_id"]

    listed = client.get("/api/profiles").json()
    assert listed["selected_profile_id"] == created["profile_id"]


def test_switch_selection_unknown_profile_is_404(running_server) -> None:
    client, fx, port = running_server
    ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    response = client.post(
        "/api/profiles/selection",
        json={"profile_id": "profile_00000000-0000-4000-8000-000000000000"},
        headers=_origin(port),
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "scout_profile_unavailable"


def test_switch_selection_to_archived_profile_is_409(running_server) -> None:
    client, fx, port = running_server
    migration = ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert migration is not None
    created = client.post(
        "/api/profiles",
        json={"label": "second", "titles": ["staff backend engineer"]},
        headers=_origin(port),
    ).json()["profile"]
    client.post(
        f"/api/profiles/{migration.profile_id}/archive",
        json={"replacement_profile_id": created["profile_id"]},
        headers=_origin(port),
    )

    response = client.post(
        "/api/profiles/selection",
        json={"profile_id": migration.profile_id},
        headers=_origin(port),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "scout_profile_archived"


def test_switch_selection_missing_profile_id_is_400(running_server) -> None:
    client, fx, port = running_server
    response = client.post(
        "/api/profiles/selection",
        json={},
        headers=_origin(port),
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_value"


# ---------------------------------------------------------------------------
# CSRF: every mutating route goes through the existing _check_csrf guard.
# ---------------------------------------------------------------------------


def test_post_profiles_cross_origin_evil_page_is_rejected(running_server) -> None:
    client, fx, port = running_server
    ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    response = client.post(
        "/api/profiles",
        content=json.dumps({"label": "x", "titles": ["x"]}).encode("utf-8"),
        headers={"Content-Type": "text/plain", "Origin": "https://evil.example"},
    )
    assert response.status_code in (403, 415)
    assert len(client.get("/api/profiles").json()["profiles"]) == 1


def test_put_profile_cross_origin_evil_page_is_rejected(running_server) -> None:
    client, fx, port = running_server
    migration = ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert migration is not None
    response = client.put(
        f"/api/profiles/{migration.profile_id}",
        content=json.dumps({"label": "evil rename"}).encode("utf-8"),
        headers={"Content-Type": "text/plain", "Origin": "https://evil.example"},
    )
    assert response.status_code in (403, 415)
    listed = client.get("/api/profiles").json()
    assert listed["profiles"][0]["label"] != "evil rename"


def test_post_profile_archive_cross_origin_evil_page_is_rejected(running_server) -> None:
    client, fx, port = running_server
    migration = ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert migration is not None
    response = client.post(
        f"/api/profiles/{migration.profile_id}/archive",
        content=b"{}",
        headers={"Content-Type": "text/plain", "Origin": "https://evil.example"},
    )
    assert response.status_code in (403, 415)
    listed = client.get("/api/profiles").json()
    assert listed["profiles"][0]["state"] == "active"


def test_post_profiles_selection_cross_origin_evil_page_is_rejected(running_server) -> None:
    client, fx, port = running_server
    migration = ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert migration is not None
    created = client.post(
        "/api/profiles",
        json={"label": "second", "titles": ["staff backend engineer"]},
        headers=_origin(port),
    ).json()["profile"]

    response = client.post(
        "/api/profiles/selection",
        content=json.dumps({"profile_id": created["profile_id"]}).encode("utf-8"),
        headers={"Content-Type": "text/plain", "Origin": "https://evil.example"},
    )
    assert response.status_code in (403, 415)
    listed = client.get("/api/profiles").json()
    assert listed["selected_profile_id"] == migration.profile_id
