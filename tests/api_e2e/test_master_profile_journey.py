"""0.1.10.9 master P3: over HTTP, a new profile gets its own selection of the master resume.

Against the real supervised server, synthetic fixtures (the spike's invented
master and one of its resumes), no model call. Before a master is stored, a
profile made with ``POST /api/profiles`` takes the selected profile's resume,
as always. With a master stored it gets its first selection (code only, from
the local index, else its titles): its ``resume_ref`` is its own view record,
and the selected profile's resume is not touched. P5: that selection is made
after the route has answered (it held the route for 7 to 8 s on a home with
290,000 postings), so the test waits for it to land. A profile that names a
resume of its own keeps that resume.
"""

from __future__ import annotations

import json
from pathlib import Path
import time

from click.testing import CliRunner
import pytest

from gigai.cli import cli

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import add_resume, resolve_workpad_path, setup_and_init, start_server, stop_server, write_offline_find_jobs_config
from tests.support.latency import latency_bound

_MASTER = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "master" / "master.md"


def test_a_new_profile_gets_its_own_selection_once_a_master_is_stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)
        (default,) = client.get("/api/profiles").json()["profiles"]

        # No master yet: the new profile takes the selected profile's resume, as before.
        before = client.post("/api/profiles", json={"label": "Before", "titles": ["staff ai engineer"]})
        assert before.status_code == 201, before.text
        assert before.json()["profile"]["resume_ref"] == default["resume_ref"]

        stored = CliRunner().invoke(cli, ["scout", "resume", "master", "init", "--from", str(_MASTER), "--home", str(home), "--target", str(target), "--json"])
        assert stored.exit_code == 0, stored.output

        created = client.post("/api/profiles", json={"label": "After", "titles": ["staff ai engineer"]})
        assert created.status_code == 201, created.text
        # P5: the answer comes at once, with the selected profile's resume; the profile's own selection is made after it
        # (`pending` in GET /api/master/selection meanwhile) and then IS its resume.
        assert created.json()["profile"]["resume_ref"] == default["resume_ref"] and created.json()["profile"]["revision"] == 1
        deadline = time.monotonic() + latency_bound(60.0)
        while created.json()["profile"]["profile_id"] in client.get("/api/master/selection").json()["pending"]:
            assert time.monotonic() < deadline, "the new profile's first selection never landed"
            time.sleep(0.1)
        listed = {item["profile_id"]: item for item in client.get("/api/profiles").json()["profiles"]}
        made = listed[created.json()["profile"]["profile_id"]]
        assert made["resume_ref"] != default["resume_ref"] and made["revision"] == 2
        assert listed[default["profile_id"]]["resume_ref"] == default["resume_ref"]  # nobody else's resume moved

        # The view is a resume the readers read: the profile's status says where it came from.
        status = CliRunner().invoke(
            cli, ["scout", "resume", "master", "selection", "status", "--profile", made["profile_id"], "--home", str(home), "--target", str(target), "--json"],
        )
        assert status.exit_code == 0, status.output
        (profile,) = json.loads(status.output.strip().splitlines()[-1])["profiles"]
        assert (profile["has_selection"], profile["attached"], profile["source"], profile["stale"]) == (True, True, "titles", False)
        assert profile["shown"] > 10 and profile["skills"] > 5

        # A profile that names its own resume keeps it.
        named = client.post(
            "/api/profiles",
            json={
                "label": "Named", "titles": ["data engineer"],
                "resume_record_id": default["resume_ref"]["record_id"], "resume_revision_id": default["resume_ref"]["revision_id"],
            },
        )
        assert named.status_code == 201, named.text
        assert named.json()["profile"]["resume_ref"] == default["resume_ref"]
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
