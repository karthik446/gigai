"""0110-003 P3: ``POST /api/tailored-resumes/pdf`` over HTTP against the real supervised server.

Tailor once (offline fixtures), then: (a) empty settings -> a name-only PDF (no contact line printed); (b) after ``PUT /api/resume-display`` the PDF carries the
saved contact line; ``application/pdf`` + ``Content-Disposition: attachment`` named
``<name>-resume-<company>.pdf``; (c) unknown job -> 404 ``tailored_resume_not_found``; (d) 422 shape
errors; (e) CSRF / Host rejections.  A second journey (0110-006): the
fixture model's lossy marker returns the operator's weaker Staff and DSAR
rewrites, and the PDF prints the ORIGINAL lines.
"""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
from pypdf import PdfReader

from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout.find_jobs.bindings import TEST_MODEL_LOSSY_MARKER, TEST_MODEL_LOSSY_REWRITES
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


_STAFF = "Led a team of 7 engineers delivering platform and product systems end to end, owning technical direction, roadmap, and delivery across 3 domains."
_DSAR = "Built the data-subject access request (DSAR) pipeline handling access and deletion requests end to end under CCPA/CPRA and CO privacy requirements."
_RESUME = f"## Experience\n**Staff Software Engineer — Guild Education** (2021–present)\n- {_STAFF}\n- {_DSAR}\n"
_POSTING = "Acme is hiring a Staff Engineer to lead platform and product systems and privacy deletion requests. Requirements: Python."


def test_the_pdf_prints_the_original_lines_when_the_model_weakened_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    source = tmp_path / "resume.md"
    source.write_text(_RESUME, encoding="utf-8")
    added = CliRunner().invoke(cli, ["scout", "resume", "add", str(source), "--home", str(home), "--target", str(target), "--json"])
    assert added.exit_code == 0, added.output
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        tailored = client.post(
            "/api/tailored-resumes",
            json={"job": {"job_text": _POSTING + " " + TEST_MODEL_LOSSY_MARKER, "title": "Staff Engineer", "company": "Acme"}},
        )
        assert tailored.status_code == 200, tailored.text
        payload = tailored.json()
        weaker = [TEST_MODEL_LOSSY_REWRITES[_STAFF.rstrip(".")], TEST_MODEL_LOSSY_REWRITES[_DSAR.rstrip(".")]]
        experience = next(section for section in payload["result"]["sections"] if section["heading"] == "experience")
        assert [bullet["alternative"]["text"] for bullet in experience["entries"][0]["bullets"]] == weaker

        key = {"profile_id": payload["resume"]["profile_id"], "job_identity": payload["job"]["job_identity"]}
        pdf = client.post("/api/tailored-resumes/pdf", json=key)
        assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf", pdf.text
        text = " ".join(_text(pdf.content).split())
        assert _STAFF in text and _DSAR in text
        assert all(line not in text for line in weaker)
        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
