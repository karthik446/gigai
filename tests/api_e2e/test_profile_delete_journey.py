"""0110-047: delete (archive) a profile -- over HTTP against the real supervised server.

Synthetic profiles. A second profile that has a run is deleted: it is gone from
``GET /api/profiles``, ``gigai scout profile list`` and the demand set background
tagging reads, and its run leaves the default ``GET /api/runs`` listing, while the
run itself and the profile's journal records stay readable. The default profile is
refused (alone, and with others); deleting the selected profile selects the default.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout.find_jobs.model_tag import load_demand
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

_TITLE = "synthetic delete-me director"


def _delete(client, path: str):
    # A DELETE carries no body but is a write: the CSRF check wants the JSON content type.
    return client.request("DELETE", path, headers={"Content-Type": "application/json"})


def _cli_profile_ids(home: Path, target: Path) -> list[str]:
    result = CliRunner().invoke(cli, ["scout", "profile", "list", "--home", str(home), "--target", str(target), "--json"])
    assert result.exit_code == 0, result.output
    return [item["profile_id"] for item in json.loads(result.output)["profiles"]]


def test_delete_profile_hides_it_everywhere_and_keeps_its_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        default_id = client.get("/api/profiles").json()["default_profile_id"]

        # (c) the default profile, alone: refused with a clear error, nothing changed.
        alone = _delete(client, f"/api/profiles/{default_id}")
        assert alone.status_code == 409, alone.text
        assert alone.json()["error"]["code"] == "scout_profile_default_delete"
        assert [item["profile_id"] for item in client.get("/api/profiles").json()["profiles"]] == [default_id]

        # A second profile with a unique title, selected, with one run.
        created = client.post("/api/profiles", json={"label": "Director", "titles": [_TITLE]})
        assert created.status_code == 201, created.text
        second_id = created.json()["profile"]["profile_id"]
        assert client.post("/api/profiles/selection", json={"profile_id": second_id}).status_code == 200
        digest = client.get("/api/config").json()["config_digest"]
        started = client.post("/api/run", json=run_request_body(digest))
        assert started.status_code == 202, started.text
        run_id = started.json()["run_id"]
        assert poll_until_terminal(client, run_id)["status"] == "succeeded"
        assert [item["run_id"] for item in client.get("/api/runs").json()["runs"]] == [run_id]
        assert _TITLE in load_demand(home, target).roles
        assert second_id in _cli_profile_ids(home, target)

        # (d) deleting the SELECTED profile selects the default.
        deleted = _delete(client, f"/api/profiles/{second_id}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["deleted"] == second_id
        assert deleted.json()["selected_profile_id"] == default_id
        listed = client.get("/api/profiles").json()
        assert listed["selected_profile_id"] == default_id

        # (a) gone from the profile list, the CLI list, the demand set and the default runs list.
        assert [item["profile_id"] for item in listed["profiles"]] == [default_id]
        assert _cli_profile_ids(home, target) == [default_id]
        assert _TITLE not in load_demand(home, target).roles
        assert client.get("/api/runs").json()["runs"] == []

        # (b) its history is still readable: the run, by name, by id and with include_deleted.
        assert [item["run_id"] for item in client.get("/api/runs", params={"profile_id": second_id}).json()["runs"]] == [run_id]
        assert [item["run_id"] for item in client.get("/api/runs", params={"include_deleted": "1"}).json()["runs"]] == [run_id]
        assert client.get(f"/api/runs/{run_id}/results").status_code == 200
        workpad = resolve_workpad_path(home, target)
        profile_dir = workpad / "records" / "scout-profiles" / second_id / "writes"
        states = [json.loads(path.read_text(encoding="utf-8"))["state"] for path in sorted(profile_dir.glob("*.json"))]
        assert states[0] == "active" and states[-1] == "deleted"  # the journal keeps both writes

        # A deleted profile cannot be selected, edited, deleted again or run against.
        assert client.post("/api/profiles/selection", json={"profile_id": second_id}).status_code == 409
        assert client.put(f"/api/profiles/{second_id}", json={"label": "x"}).status_code == 409
        assert _delete(client, f"/api/profiles/{second_id}").status_code == 409
        assert _delete(client, "/api/profiles/profile_00000000-0000-4000-8000-000000000000").status_code == 404

        # (c) the default profile with others around is refused too.
        other = client.post("/api/profiles", json={"label": "Other", "titles": ["synthetic other"]})
        assert other.status_code == 201, other.text
        refused = _delete(client, f"/api/profiles/{default_id}")
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "scout_profile_default_delete"

        # Deleting a profile that is not selected leaves the selection alone.
        other_id = other.json()["profile"]["profile_id"]
        assert _delete(client, f"/api/profiles/{other_id}").json()["selected_profile_id"] == default_id

        # The CLI deletes too, and refuses the default.
        third = client.post("/api/profiles", json={"label": "Third", "titles": ["synthetic third"]}).json()["profile"]["profile_id"]
        cli_args = ["--home", str(home), "--target", str(target), "--json"]
        done = CliRunner().invoke(cli, ["scout", "profile", "delete", third, *cli_args])
        assert done.exit_code == 0, done.output
        assert json.loads(done.output)["deleted"] == third
        assert _cli_profile_ids(home, target) == [default_id]
        refused_cli = CliRunner().invoke(cli, ["scout", "profile", "delete", default_id, *cli_args])
        assert refused_cli.exit_code != 0
        assert "scout_profile_default_delete" in refused_cli.output
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
