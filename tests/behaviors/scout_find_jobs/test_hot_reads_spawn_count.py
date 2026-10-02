"""0110-033: the page's hot reads, pinned by git subprocess count, and never stale.

On the operator's machine ``GET /api/profiles`` took 7.0 s, ``/api/config``
5.7 s, ``/api/setup`` 4.0 s and ``/api/applications`` 6.6 s. The time was
git subprocesses: every read resolved the workpad again (13 spawns, up to
three times a request) and read the journal again (16 a snapshot), 15 to 46
spawns a request on an unchanged workpad and 146 for the first
``/api/config``; each spawn costs 7 ms alone and several times that when six
reads and a background check run together.

Pinned here with counts, not wall time:

* a read of an unchanged workpad spawns (almost) nothing;
* the first read after the server starts, or after a write, is bounded;
* a write is seen by the very next read, through every one of these routes.

The bounds are the measured counts (this fixture has no saved preferences,
so its ``/api/setup`` is the 404 with a pre-fill, which reads the config).
Before the fix the same reads spawned 46 / 43 / 16 / 30 on an unchanged
workpad, 46 / 146 / 33 / 30 as a first read, and 238 for the four of a page
load; now 0 / 3 / 1 / 0, 35 / 56 / 31 / 34 and 74. (The first applications
read is 4 more than it was: it reads the runs apart from the rest, so that a
recorded application does not cost every run's outputs again. Its time is
lower: the packaged schemas are no longer parsed for every event.)
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import httpx
import pytest

import gigai.journal as journal
import gigai.workpad as workpad_module
from gigai.scout.find_jobs.api import server as server_module

from .test_read_routes_lock_free import running_server  # noqa: F401 - the in-process server fixture

# What one read of an UNCHANGED workpad may spawn. The three left on
# /api/config (and the one on /api/setup) are the backend's own one-call
# head lookups (``run._cheap_workpad_head``) that key its profile and
# resume caches.
UNCHANGED_SPAWNS_MAX = {"/api/profiles": 0, "/api/config": 3, "/api/setup": 1, "/api/applications": 0}
# What the FIRST read may spawn, nothing kept from an earlier one (a server
# that just started; the read after a journal write).
FIRST_SPAWNS_MAX = {"/api/profiles": 36, "/api/config": 58, "/api/setup": 32, "/api/applications": 34}
PAGE_LOAD_SPAWNS_MAX = 75
HOT = tuple(UNCHANGED_SPAWNS_MAX)


def _forget_everything() -> None:
    """As a server that just started: nothing read earlier in this process is kept."""

    for module, names in (
        (workpad_module, ("_validated_repositories", "_resolved_targets")),
        (journal, ("_validated_workpads", "_snapshot_cache", "_artifact_cache")),
        (server_module, ("_selected_profile_cache", "_profile_resume_details_cache")),
    ):
        for name in names:
            cache = getattr(module, name, None)  # absent before 0110-033
            if cache is not None:
                cache.clear()


@pytest.fixture
def spawns(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every subprocess any thread of this process starts (the server's request threads included)."""

    started: list[str] = []
    real = subprocess.run

    def counting(args, *rest, **kwargs):
        started.append(" ".join(str(word) for word in args))
        return real(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting)
    return started


@pytest.fixture
def client(running_server: SimpleNamespace):  # noqa: F811
    _forget_everything()
    with httpx.Client(base_url=running_server.base_url, timeout=60.0) as http:
        for path in HOT:  # nothing left to migrate, every cache warm
            assert http.get(path).status_code in (200, 404), path
        yield http


@pytest.mark.parametrize("path", HOT)
def test_a_read_of_an_unchanged_workpad_spawns_almost_nothing(client: httpx.Client, spawns: list[str], path: str) -> None:
    for _ in range(3):
        spawns.clear()
        response = client.get(path)
        assert response.status_code in (200, 404), response.text
        assert len(spawns) <= UNCHANGED_SPAWNS_MAX[path], (path, len(spawns), spawns)


@pytest.mark.parametrize("path", HOT)
def test_the_first_read_is_bounded(client: httpx.Client, spawns: list[str], path: str) -> None:
    _forget_everything()
    spawns.clear()
    response = client.get(path)
    assert response.status_code in (200, 404), response.text
    assert 0 < len(spawns) <= FIRST_SPAWNS_MAX[path], (path, len(spawns))


def test_a_whole_page_load_after_a_start_shares_what_it_reads(client: httpx.Client, spawns: list[str]) -> None:
    """The four reads of one page load, one after the other on a server that kept nothing."""

    _forget_everything()
    spawns.clear()
    for path in HOT:
        assert client.get(path).status_code in (200, 404), path
    # Before: 238, each read paying for all of it again.
    assert len(spawns) <= PAGE_LOAD_SPAWNS_MAX, len(spawns)


def test_a_write_costs_only_the_reads_it_touched(client: httpx.Client, spawns: list[str]) -> None:
    """A recorded application: the applications read takes the new event, the others are not made again."""

    recorded = _post(client, "/api/applications", {"normalized_url": "https://boards.greenhouse.io/acme/jobs/7", "event_kind": "applied"})
    assert recorded.status_code == 201, recorded.text
    after: dict[str, int] = {}
    for path in ("/api/profiles", "/api/config", "/api/setup", "/api/applications"):
        spawns.clear()
        assert client.get(path).status_code in (200, 404), path
        after[path] = len(spawns)
    # One ``git log`` over the commits since says what they touched; the profile reads stay as they were.
    assert after["/api/profiles"] <= 1, after
    # The first application there ever was: its event is taken from the one commit, and the acquired postings are read once.
    # 0110-044: the publishers of that family are listed once (and kept), next to the listing of the new commit.
    assert 1 <= after["/api/applications"] <= 7, after
    # /api/config: the backend's own head-keyed profile and resume caches miss (they key on the head alone).
    assert after["/api/config"] <= FIRST_SPAWNS_MAX["/api/config"], after
    assert len(client.get("/api/applications").json()["applications"]) == 1
    # 0110-036: every application after it is caught up from its one commit
    # (the commits since, and one cat-file for the new event), not read again.
    again = _post(client, "/api/applications", {"normalized_url": "https://boards.greenhouse.io/acme/jobs/8", "event_kind": "applied"})
    assert again.status_code == 201, again.text
    spawns.clear()
    assert len(client.get("/api/applications").json()["applications"]) == 2
    assert 1 <= len(spawns) <= 2, spawns


# --------------------------------------------------------------------------
# Never stale: a write is seen by the next read
# --------------------------------------------------------------------------


def _post(client: httpx.Client, path: str, body: dict[str, object], *, method: str = "POST") -> httpx.Response:
    return client.request(method, path, json=body)


def test_a_new_profile_and_a_new_selection_are_in_the_next_reads(client: httpx.Client) -> None:
    before = client.get("/api/profiles").json()
    assert len(before["profiles"]) == 1
    old_digest = client.get("/api/config").json()["config_digest"]

    created = _post(client, "/api/profiles", {"label": "Data", "titles": ["data engineer"]})
    assert created.status_code == 201, created.text
    profile_id = created.json()["profile"]["profile_id"]

    listed = client.get("/api/profiles").json()
    assert sorted(item["label"] for item in listed["profiles"]) == ["Data", "default"]
    assert listed["selected_profile_id"] == before["selected_profile_id"]

    switched = _post(client, "/api/profiles/selection", {"profile_id": profile_id})
    assert switched.status_code == 200, switched.text
    assert client.get("/api/profiles").json()["selected_profile_id"] == profile_id
    config = client.get("/api/config").json()
    assert config["config"]["roles"] == ["data engineer"]
    assert config["config_digest"] != old_digest

    edited = _post(client, f"/api/profiles/{profile_id}", {"titles": ["analytics engineer"]}, method="PUT")
    assert edited.status_code == 200, edited.text
    assert client.get("/api/config").json()["config"]["roles"] == ["analytics engineer"]
    shown = {item["profile_id"]: item for item in client.get("/api/profiles").json()["profiles"]}
    assert shown[profile_id]["titles"] == ["analytics engineer"] and shown[profile_id]["revision"] == 2


def test_a_recorded_application_is_in_the_next_read(client: httpx.Client) -> None:
    assert client.get("/api/applications").json()["applications"] == []
    url = "https://boards.greenhouse.io/acme/jobs/4242"
    for count, kind in enumerate(("applied", "interview_scheduled"), start=1):
        recorded = _post(client, "/api/applications", {"normalized_url": url, "event_kind": kind})
        assert recorded.status_code == 201, recorded.text
        rows = client.get("/api/applications").json()["applications"]
        assert [row["event_kind"] for row in rows].count(kind) == 1 and len(rows) == count
        assert {row["job_state"]["state"] for row in rows} == {recorded.json()["job_state"]["state"]}


def test_saved_preferences_are_in_the_next_read(client: httpx.Client) -> None:
    from tests.api_e2e.test_discover_fake_provider import _SETUP_BODY

    client.get("/api/setup")
    for city in ("Denver, CO", "Boulder, CO"):
        saved = _post(client, "/api/setup", {**_SETUP_BODY, "city": city}, method="PUT")
        assert saved.status_code == 200, saved.text
        assert client.get("/api/setup").json()["prefs"]["city"] == city
        assert client.get("/api/config").json()["config"]["location"] == city


def test_a_write_made_outside_the_server_is_in_the_next_read(client: httpx.Client, running_server: SimpleNamespace) -> None:  # noqa: F811
    """The CLI and a run's child process write the journal without telling the server."""

    from gigai.scout import profile_records

    gig = running_server.gig
    assert len(client.get("/api/profiles").json()["profiles"]) == 1
    profile_records.create_profile(
        gig.resolved, label="From the CLI", titles=("sre",), titles_to_avoid=(), queries=("sre",), resume_ref=gig.profile.resume_ref,
    )
    assert sorted(item["label"] for item in client.get("/api/profiles").json()["profiles"]) == ["From the CLI", "default"]
