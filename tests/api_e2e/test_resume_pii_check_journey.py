"""0.1.10-001: ``POST /api/resume/check`` -- the local contact-details heads-up (no model)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.api_e2e.harness import add_resume, setup_and_init, start_server, stop_server

WITH_CONTACT = "Jordan Rivera\njordan@example.com | (415) 555-0134\nStaff engineer: Go and Kafka.\n"
CLEAN = "Staff engineer: nine years of Go and Kafka.\n"


def test_check_flags_pasted_and_stored_contact_details(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)  # the stored resume is clean
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        flagged = client.post("/api/resume/check", json={"resume_text": WITH_CONTACT})
        assert flagged.status_code == 200, flagged.text
        assert flagged.json()["found"] == ["email", "phone"]
        assert "Jordan" not in flagged.text

        clean = client.post("/api/resume/check", json={"resume_text": CLEAN})
        assert clean.status_code == 200 and clean.json()["found"] == []

        config = client.get("/api/config").json()["resume_preview"]
        stored = client.post("/api/resume/check", json={"resume_ref": {"record_id": config["record_id"], "revision_id": config["revision_id"]}})
        assert stored.status_code == 200, stored.text
        assert stored.json()["found"] == []

        both = client.post("/api/resume/check", json={"resume_text": CLEAN, "resume_ref": {}})
        assert both.status_code == 422
        missing = client.post("/api/resume/check", json={"resume_ref": {"record_id": "nope", "revision_id": "nope"}})
        assert missing.status_code == 404, missing.text
    finally:
        stop_server(server)
