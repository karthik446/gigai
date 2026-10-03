"""0110-003 P3 / 0110-046: ``GET`` / ``PUT /api/resume-display`` over HTTP against the real supervised server.

(a) nothing saved -> ``saved: false`` and a local ``suggested`` title from the resume header (never a name
or contact item: 0110-046);
(b) PUT saves layout and titles, the file is 0600; an old client's ``name`` / ``contact`` are accepted,
ignored with a note, and never stored;
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


def add_titled_resume(home: Path, target: Path, tmp_path: Path) -> None:
    """A resume whose header block holds a title line (the title prefill source)."""

    source = tmp_path / "titled-resume.md"
    source.write_text("Staff Engineer\n\nSoftware engineer with Python service experience.\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli, ["scout", "resume", "add", str(source), "--home", str(home), "--target", str(target), "--json"]
    )
    assert result.exit_code == 0, result.output


def test_resume_display_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_titled_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        path = home / "scout" / "resume-display.json"

        # (a) nothing saved: saved:false + a local title prefill; no name or contact field; nothing written.
        first = client.get("/api/resume-display")
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["saved"] is False and "name" not in body and "contact" not in body
        assert body["spacing_scale"] == 1.0 and body["auto_fit"] is True  # 0110-017 defaults before any save
        assert body["suggested"] == {"title": "Staff Engineer"}
        assert not path.exists()

        # (b) 0110-046: an older client's name/contact are accepted, ignored with a note, and never stored.
        put = client.put(
            "/api/resume-display",
            json={"name": "Kar Ohm", "contact": [{"kind": "email", "value": " kar@example.test "}, {"kind": "phone", "value": ""}]},
        )
        assert put.status_code == 200, put.text
        assert put.json()["saved"] is True and put.json()["ignored"] == ["contact", "name"]
        assert put.json()["note"].startswith("GigAI no longer stores your name or contact details")
        assert "Kar Ohm" not in put.text and "kar@example.test" not in put.text
        assert path.is_file() and (path.stat().st_mode & 0o777) == 0o600
        assert "Kar Ohm" not in path.read_text() and "kar@example.test" not in path.read_text()
        after = client.get("/api/resume-display").json()
        assert after["saved"] is True and "name" not in after and "ignored" not in after
        # Malformed legacy values are ignored too: they are never read.
        assert client.put("/api/resume-display", json={"name": 3, "contact": {}}).status_code == 200

        # per-profile title merge; an empty value clears it.
        profile_id = client.get("/api/profiles").json()["profiles"][0]["profile_id"]
        titled = client.put("/api/resume-display", json={"titles": {profile_id: "Staff Engineer"}}).json()
        assert titled["titles"] == {profile_id: "Staff Engineer"} and "ignored" not in titled
        assert client.get("/api/resume-display", params={"profile_id": profile_id}).json()["title"] == "Staff Engineer"
        assert client.put("/api/resume-display", json={"titles": {profile_id: ""}}).json()["titles"] == {}

        # 0110-017: the PDF spacing slider and auto fit, saved in the same file; other keys are kept.
        spaced = client.put("/api/resume-display", json={"spacing_scale": 1.2, "auto_fit": False})
        assert spaced.status_code == 200, spaced.text
        assert spaced.json()["spacing_scale"] == 1.2 and spaced.json()["auto_fit"] is False
        again = client.get("/api/resume-display").json()
        assert again["spacing_scale"] == 1.2 and again["auto_fit"] is False
        assert client.put("/api/resume-display", json={"titles": {}}).json()["spacing_scale"] == 1.2  # left out: kept

        # (c) validation.
        for payload, code in (
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

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
