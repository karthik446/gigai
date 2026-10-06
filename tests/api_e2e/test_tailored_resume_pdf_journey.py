"""0110-003 P3 / 0110-046: ``POST /api/tailored-resumes/pdf`` over HTTP against the real supervised server.

Tailor once (offline fixtures), then: (a) no ``header`` -> a PDF with no header (GigAI stores no name or
contact details), named ``<company>-<role>-<YYYY-MM-DD>.pdf``; (b) the Generate PDF form's ``header`` fills
this one PDF (name, saved per-profile title, contact line) and the response carries none of it (the PDF
bytes aside); an old ``PUT /api/resume-display`` name/contact is ignored and never prints; (c) unknown job
-> 404 ``tailored_resume_not_found``; (d) 422 shape errors, also for a bad ``header``, never echoing a
value; (e) CSRF / Host rejections.  A second journey (0110-006): the fixture model's lossy marker returns
the operator's weaker Staff and DSAR rewrites, and the PDF prints the ORIGINAL lines.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from pypdf import PdfReader

from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout.find_jobs.bindings import TEST_MODEL_LOSSY_MARKER, TEST_MODEL_LOSSY_REWRITES

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
#: Synthetic marker values (0110-046).
_FORM = {
    "name": "Zora Quillfeather", "email": "zora.q@example.invalid", "phone": "555-0142-ZQ",
    "location": "Nowhere, ZZ", "linkedin": "linkedin.com/in/zq-invalid", "link": "https://zq.example.invalid/",
}
_MARKERS = ("Zora", "Quillfeather", "zora.q@example.invalid", "555-0142-ZQ", "zq-invalid", "zq.example.invalid", "Nowhere, ZZ")


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


def _no_marker(response: httpx.Response, *, body_too: bool = True) -> None:
    headers = "\n".join(f"{key}: {value}" for key, value in response.headers.items())
    for marker in _MARKERS:
        assert marker not in headers, (marker, headers)
        if body_too:
            assert marker not in response.text, marker


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
        expected_name = re.compile(r'attachment; filename="acme-[a-z0-9-]*\d{4}-\d{2}-\d{2}\.pdf"')

        # (a) no form: no header at all; the file is named after the company, role and date.
        assert client.get("/api/resume-display").json()["saved"] is False
        first = client.post("/api/tailored-resumes/pdf", json=key)
        assert first.status_code == 200, first.text
        assert first.headers["content-type"] == "application/pdf"
        assert expected_name.fullmatch(first.headers["content-disposition"]), first.headers["content-disposition"]
        assert int(first.headers["content-length"]) == len(first.content) and first.content.startswith(b"%PDF")
        text = _text(first.content)
        assert "|" not in text and "@" not in text
        assert not (home / "scout" / "resume-display.json").exists(), "rendering saves nothing"
        # ... and names the Scout page that finishes it (the Generate PDF form for this profile and job).
        finish = first.headers["x-gigai-finish-url"]
        assert finish == f"http://127.0.0.1:{server.port}/#/pdf/{quote(key['profile_id'], safe='')}/{quote(key['job_identity'], safe='')}"
        assert finish.isascii()

        # (b) the Generate PDF form fills this PDF; an old client's saved name/contact are ignored.
        legacy = client.put("/api/resume-display", json={"name": "Kar Ohm", "contact": [{"kind": "email", "value": "kar@example.test"}], "titles": {key["profile_id"]: "Staff Engineer"}})
        assert legacy.status_code == 200 and legacy.json()["ignored"] == ["contact", "name"] and "note" in legacy.json()
        stored = (home / "scout" / "resume-display.json").read_text()
        assert "Kar Ohm" not in stored and "kar@example.test" not in stored and "Staff Engineer" in stored
        headerless = client.post("/api/tailored-resumes/pdf", json=key)
        assert "Kar Ohm".upper() not in _text(headerless.content) and "kar@example.test" not in _text(headerless.content)
        second = client.post("/api/tailored-resumes/pdf", json={**key, "header": _FORM})
        assert second.status_code == 200 and second.headers["content-type"] == "application/pdf"
        assert expected_name.fullmatch(second.headers["content-disposition"])
        _no_marker(second, body_too=False)
        assert "x-gigai-finish-url" not in second.headers, "a PDF with its header needs no finishing"
        text = _text(second.content)
        assert text.startswith("ZORA QUILLFEATHER\nStaff Engineer\nNowhere, ZZ | linkedin.com/in/zq-invalid | zq.example.invalid | zora.q@example.invalid | 555-0142-ZQ\n")
        assert len(PdfReader(io.BytesIO(second.content)).pages) == len(PdfReader(io.BytesIO(headerless.content)).pages)
        name_only = client.post("/api/tailored-resumes/pdf", json={**key, "header": {"name": "Zora Quillfeather"}})
        assert _text(name_only.content).startswith("ZORA QUILLFEATHER\nStaff Engineer\n") and "|" not in _text(name_only.content).split("\n")[2]

        # (b2) 0110-017: with auto fit off the PDF follows the saved spacing scale.
        pdfs = []
        for scale in (0.7, 1.4):
            assert client.put("/api/resume-display", json={"spacing_scale": scale, "auto_fit": False}).status_code == 200
            spaced = client.post("/api/tailored-resumes/pdf", json=key)
            assert spaced.status_code == 200 and spaced.content.startswith(b"%PDF")
            pdfs.append(spaced.content)
        assert pdfs[0] != pdfs[1]

        # (c) not found.
        missing = client.post("/api/tailored-resumes/pdf", json={**key, "job_identity": "https://example.test/none", "header": _FORM})
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "tailored_resume_not_found"
        _no_marker(missing)
        ephemeral = client.post("/api/tailored-resumes/pdf", json={**key, "profile_id": "ephemeral"})
        assert ephemeral.status_code == 404 and ephemeral.json()["error"]["code"] == "tailored_resume_not_found"

        # (d) shape errors, and a bad form: typed 422s that never echo a value.
        for bad in ({}, {"profile_id": "x"}, {**key, "extra": 1}, {"profile_id": 1, "job_identity": "j"}, {**key, "profile_id": "../x"}, {"header": _FORM}):
            response = client.post("/api/tailored-resumes/pdf", json=bad)
            assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_value", (bad, response.text)
            _no_marker(response)
        for header, code in (
            ("Zora Quillfeather", "wrong_type"),
            ({**_FORM, "phone": 5550142}, "wrong_type"),
            ({**_FORM, "fax": "555-0142-ZQ"}, "unknown_key"),
            ({**_FORM, "name": "Zora Quillfeather " * 20}, "invalid_value"),
            ({**_FORM, "email": "zora.q@example.invalid\nX"}, "invalid_value"),
        ):
            response = client.post("/api/tailored-resumes/pdf", json={**key, "header": header})
            assert response.status_code == 422 and response.json()["error"]["code"] == code, (header, response.text)
            _no_marker(response)
            assert "fax" not in response.json()["error"]["message"]

        # (e) CSRF / Host.
        url = f"{server.base_url}/api/tailored-resumes/pdf"
        wrong_origin = httpx.post(url, json=key, headers={"Origin": "http://evil.example.test"})
        assert wrong_origin.status_code == 403 and wrong_origin.json()["error"]["code"] == "forbidden_origin"
        wrong_type = httpx.post(url, content=b"{}", headers={"Content-Type": "text/plain"})
        assert wrong_type.status_code == 415
        wrong_host = httpx.post(url, json={**key, "header": _FORM}, headers={"Host": "evil.example.test"})
        assert wrong_host.status_code == 403 and wrong_host.json()["error"]["code"] == "forbidden_origin"
        assert not wrong_host.content.startswith(b"%PDF")
        _no_marker(wrong_host)

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    # The form's values were rendered and dropped: in no file under the home (the server log lives there) or target.
    holders = [
        (path, marker)
        for root in (home, target)
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
        for marker in _MARKERS
        if marker.encode() in path.read_bytes()
    ]
    assert holders == [], f"the Generate PDF form's values must not be written anywhere: {holders}"
    assert_clean_and_healthy(workpad, home)


_STAFF = "Managed a group of 4 analysts supporting scheduling and billing systems end to end, owning the release calendar, vendor contact, and staff training across 2 hospitals."
_DSAR = "Built the medication reconciliation (MRX) workflow handling admission and discharge lists end to end under HIPAA and OH state requirements."
_RESUME = f"## Experience\n**Clinical Applications Manager — Example Corp** (2020–present)\n- {_STAFF}\n- {_DSAR}\n"
_POSTING = "Acme is hiring a Staff Engineer to lead scheduling and billing systems and medication reconciliation. Requirements: Python."


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
