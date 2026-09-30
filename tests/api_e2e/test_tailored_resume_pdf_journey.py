"""0110-003 P3: ``POST /api/tailored-resumes/pdf`` over HTTP against the real supervised server.

Tailor once (offline fixtures), then: (a) empty settings -> a name-only PDF (no contact line printed); (b) after ``PUT /api/resume-display`` the PDF carries the
saved contact line; ``application/pdf`` + ``Content-Disposition: attachment`` named
``<name>-resume-<company>.pdf``; (c) unknown job -> 404 ``tailored_resume_not_found``; (d) 422 shape
errors; (e) CSRF / Host rejections.
"""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
from pypdf import PdfReader

from gigai.scout.resume_pdf import pdf_file_name

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import (
    add_resume,
    resolve_workpad_path,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)

_JOB = {"job_url": "https://boards.greenhouse.io/acme/jobs/101"}


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


def test_tailored_resume_pdf_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        tailored = client.post("/api/tailored-resumes", json={"job": _JOB})
        assert tailored.status_code == 200, tailored.text
        payload = tailored.json()
        key = {"profile_id": payload["resume"]["profile_id"], "job_identity": payload["job"]["job_identity"]}
        assert payload["job"]["company"] == "Acme"

        # (a) empty settings: still a PDF, name-only (legacy header line as the name fallback), no contact line.
        header = payload["result"]["header"]
        fallback = header[0]["text"] if header else ""
        assert client.get("/api/resume-display").json()["saved"] is False
        first = client.post("/api/tailored-resumes/pdf", json=key)
        assert first.status_code == 200, first.text
        assert first.headers["content-type"] == "application/pdf"
        assert first.headers["content-disposition"] == f'attachment; filename="{pdf_file_name(fallback, "Acme")}"'
        assert int(first.headers["content-length"]) == len(first.content) and first.content.startswith(b"%PDF")
        text = _text(first.content)
        assert "|" not in text and "@" not in text
        assert not (home / "scout" / "resume-display.json").exists(), "rendering must not save the prefill"

        # (b) saved settings print.
        saved = client.put(
            "/api/resume-display",
            json={"name": "Kar Ohm", "contact": [{"kind": "email", "value": "kar@example.test"}], "titles": {key["profile_id"]: "Staff Engineer"}},
        )
        assert saved.status_code == 200, saved.text
        second = client.post("/api/tailored-resumes/pdf", json=key)
        assert second.status_code == 200 and second.headers["content-type"] == "application/pdf"
        assert second.headers["content-disposition"] == 'attachment; filename="kar-ohm-resume-acme.pdf"'
        text = _text(second.content)
        assert "kar@example.test" in text and "Staff Engineer" in text

        # (c) not found.
        missing = client.post("/api/tailored-resumes/pdf", json={**key, "job_identity": "https://example.test/none"})
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "tailored_resume_not_found"
        ephemeral = client.post("/api/tailored-resumes/pdf", json={**key, "profile_id": "ephemeral"})
        assert ephemeral.status_code == 404 and ephemeral.json()["error"]["code"] == "tailored_resume_not_found"

        # (d) shape errors.
        for bad in ({}, {"profile_id": "x"}, {**key, "extra": 1}, {"profile_id": 1, "job_identity": "j"}, {**key, "profile_id": "../x"}):
            response = client.post("/api/tailored-resumes/pdf", json=bad)
            assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_value", (bad, response.text)

        # (e) CSRF / Host.
        url = f"{server.base_url}/api/tailored-resumes/pdf"
        wrong_origin = httpx.post(url, json=key, headers={"Origin": "http://evil.example.test"})
        assert wrong_origin.status_code == 403 and wrong_origin.json()["error"]["code"] == "forbidden_origin"
        wrong_type = httpx.post(url, content=b"{}", headers={"Content-Type": "text/plain"})
        assert wrong_type.status_code == 415
        wrong_host = httpx.post(url, json=key, headers={"Host": "evil.example.test"})
        assert wrong_host.status_code == 403 and wrong_host.json()["error"]["code"] == "forbidden_origin"
        assert not wrong_host.content.startswith(b"%PDF")

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
