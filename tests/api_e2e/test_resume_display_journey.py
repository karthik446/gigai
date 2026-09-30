"""0110-003 P3: ``GET`` / ``PUT /api/resume-display`` over HTTP against the real supervised server.

(a) nothing saved -> ``saved: false`` and a local ``suggested`` prefill from the resume header;
(b) PUT saves, GET returns the saved values with no ``suggested`` contact/name, the file is 0600;
(c) validation 422s; (d) CSRF/Host rejections on PUT and the Host check on GET.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from gigai.cli import cli

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import (
    resolve_workpad_path,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)


def add_named_resume(home: Path, target: Path, tmp_path: Path) -> None:
    """A resume whose header block is a name + contact line (the prefill source)."""

    source = tmp_path / "named-resume.md"
    source.write_text(
        "Kar Ohm\nkar@example.test | Denver, CO\n\nSoftware engineer with Python service experience.\n", encoding="utf-8"
    )
    result = CliRunner().invoke(
        cli, ["scout", "resume", "add", str(source), "--home", str(home), "--target", str(target), "--json"]
    )
    assert result.exit_code == 0, result.output


def test_resume_display_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_named_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client

        # (a) nothing saved: saved:false + a local prefill; nothing written.
        first = client.get("/api/resume-display")
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["saved"] is False and body["name"] == "" and body["contact"] == []
        assert body["spacing_scale"] == 1.0 and body["auto_fit"] is True  # 0110-017 defaults before any save
        assert body["suggested"]["name"] == "Kar Ohm"
        assert {"kind": "email", "value": "kar@example.test"} in body["suggested"]["contact"]
        assert not (home / "scout" / "resume-display.json").exists()

        # (b) PUT saves; GET then returns the saved values and no suggested name/contact.
        put = client.put(
            "/api/resume-display",
            json={"name": "Kar Ohm", "contact": [{"kind": "email", "value": " kar@example.test "}, {"kind": "phone", "value": ""}]},
        )
        assert put.status_code == 200, put.text
        assert put.json()["saved"] is True and put.json()["contact"] == [{"kind": "email", "value": "kar@example.test"}]
        path = home / "scout" / "resume-display.json"
        assert path.is_file() and (path.stat().st_mode & 0o777) == 0o600
        after = client.get("/api/resume-display").json()
        assert after["saved"] is True and after["name"] == "Kar Ohm"
        assert "suggested" not in after or (after["suggested"]["name"] == "" and after["suggested"]["contact"] == [])

        # per-profile title merge; an empty value clears it.
        profile_id = client.get("/api/profiles").json()["profiles"][0]["profile_id"]
        titled = client.put("/api/resume-display", json={"titles": {profile_id: "Staff Engineer"}}).json()
        assert titled["titles"] == {profile_id: "Staff Engineer"} and titled["name"] == "Kar Ohm"
        assert client.get("/api/resume-display", params={"profile_id": profile_id}).json()["title"] == "Staff Engineer"
        assert client.put("/api/resume-display", json={"titles": {profile_id: ""}}).json()["titles"] == {}

        # 0110-017: the PDF spacing slider and auto fit, saved in the same file; other keys are kept.
        spaced = client.put("/api/resume-display", json={"spacing_scale": 1.2, "auto_fit": False})
        assert spaced.status_code == 200, spaced.text
        assert spaced.json()["spacing_scale"] == 1.2 and spaced.json()["auto_fit"] is False and spaced.json()["name"] == "Kar Ohm"
        again = client.get("/api/resume-display").json()
        assert again["spacing_scale"] == 1.2 and again["auto_fit"] is False
        assert client.put("/api/resume-display", json={"name": "Kar Ohm"}).json()["spacing_scale"] == 1.2  # left out: kept

        # (c) validation.
        for payload, code in (
            ({"name": 3}, "wrong_type"),
            ({"contact": {}}, "wrong_type"),
            ({"contact": [{"kind": "fax", "value": "1"}]}, "bad_enum"),
            ({"contact": [{"kind": "email"}]}, "wrong_type"),
            ({"titles": {"p": 1}}, "wrong_type"),
            ({"nope": 1}, "unknown_key"),
            ({"spacing_scale": 1.5}, "invalid_value"),
            ({"spacing_scale": 0.69}, "invalid_value"),
            ({"spacing_scale": "1.0"}, "wrong_type"),
            ({"spacing_scale": True}, "wrong_type"),
            ({"auto_fit": "yes"}, "wrong_type"),
            ({"auto_fit": 1}, "wrong_type"),
        ):
            response = client.put("/api/resume-display", json=payload)
            assert response.status_code == 422 and response.json()["error"]["code"] == code, (payload, response.text)

        # (d) CSRF / Host.
        bad_origin = httpx.put(f"{server.base_url}/api/resume-display", json={}, headers={"Origin": "http://evil.example.test"})
        assert bad_origin.status_code == 403 and bad_origin.json()["error"]["code"] == "forbidden_origin"
        bad_type = httpx.put(f"{server.base_url}/api/resume-display", content=b"{}", headers={"Content-Type": "text/plain"})
        assert bad_type.status_code == 415
        bad_host_put = httpx.put(f"{server.base_url}/api/resume-display", json={}, headers={"Host": "evil.example.test"})
        assert bad_host_put.status_code == 403 and bad_host_put.json()["error"]["code"] == "forbidden_origin"
        bad_host_get = httpx.get(f"{server.base_url}/api/resume-display", headers={"Host": "evil.example.test"})
        assert bad_host_get.status_code == 403 and bad_host_get.json()["error"]["code"] == "forbidden_origin"
        assert "kar@example.test" not in bad_host_get.text

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
