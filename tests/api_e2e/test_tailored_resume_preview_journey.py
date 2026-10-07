"""0.1.11.5 (a): ``POST /api/tailored-resumes/preview`` over HTTP against the real supervised server.

Tailor once (offline fixtures), then: (a) no ``spacing_scale`` -> the stored resume as page pictures (PNG), the
PDF's own page count, the fitted spacing, ``saved`` false, and nothing written; (b) the Generate PDF form's
``header`` is used for the pictures and comes back nowhere (no header, no JSON field: a picture holds no text);
(c) ``spacing_scale`` (the job page's slider) is saved for the job: the preview, a later preview that names none,
and ``POST /api/tailored-resumes/pdf`` use it; it is one small file beside the stored resume, which is not
written, and the saved display layout is not written; (d) unknown job -> 404; (e) 422 shape errors; (f) CSRF / Host rejections, as for the PDF route.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import httpx
import pytest
from pypdf import PdfReader

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import add_resume, resolve_workpad_path, setup_and_init, start_server, stop_server, write_offline_find_jobs_config

_JOB = {"job_url": "https://boards.greenhouse.io/acme/jobs/101"}
_FORM = {"name": "Zora Quillfeather", "email": "zora.q@example.invalid", "phone": "555-0142-ZQ", "location": "Nowhere, ZZ"}
_MARKERS = ("Zora", "Quillfeather", "zora.q@example.invalid", "555-0142-ZQ", "Nowhere, ZZ")
_ROUTE = "/api/tailored-resumes/preview"


def _pages(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def test_tailored_resume_preview_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        stored_file = Path(payload["stored_path"])
        before = stored_file.read_bytes()

        # (a) the preview as it opens: pictures of the PDF's pages, the fitted spacing, nothing saved.
        first = client.post(_ROUTE, json=key)
        assert first.status_code == 200, first.text
        assert first.headers["content-type"].startswith("application/json")
        opened = first.json()
        assert sorted(opened) == ["image_type", "images", "max_pages", "note", "pages", "saved", "spacing", "spacing_scale"]
        pictures = [base64.b64decode(image) for image in opened["images"]]
        assert opened["image_type"] == "image/png" and pictures and all(picture.startswith(b"\x89PNG\r\n") for picture in pictures)
        pdf = client.post("/api/tailored-resumes/pdf", json=key)
        assert len(pictures) == opened["pages"] == _pages(pdf.content) == int(pdf.headers["x-gigai-pages"])
        assert float(pdf.headers["x-gigai-spacing-scale"]) == opened["spacing_scale"]
        assert (opened["saved"], opened["note"], opened["max_pages"]) == (False, None, 2) and opened["spacing"] == {"min": 0.7, "max": 1.4, "step": 0.05}
        assert stored_file.read_bytes() == before and not (home / "scout" / "resume-display.json").exists(), "reading the preview saves nothing"

        # (b) the form's header is drawn into the pictures and is nowhere else in the answer.
        with_header = client.post(_ROUTE, json={**key, "header": _FORM})
        assert with_header.status_code == 200 and with_header.json()["pages"] == _pages(client.post("/api/tailored-resumes/pdf", json={**key, "header": _FORM}).content)
        shown = "\n".join(f"{name}: {value}" for name, value in with_header.headers.items()) + "\n" + "\n".join(
            str(value) for name, value in with_header.json().items() if name != "images"
        )
        for marker in _MARKERS:
            assert marker not in shown, marker
        assert with_header.json()["images"] != opened["images"], "the header was not drawn"
        assert stored_file.read_bytes() == before

        # (c) the slider: saved for this job, and used from then on.
        moved = client.post(_ROUTE, json={**key, "spacing_scale": 1.2, "header": _FORM})
        assert moved.status_code == 200 and (moved.json()["spacing_scale"], moved.json()["saved"]) == (1.2, True)
        again = client.post(_ROUTE, json=key).json()
        assert (again["spacing_scale"], again["saved"]) == (1.2, True)
        listed = client.get("/api/tailored-resumes", params=key).json()["items"][0]
        assert "spacing_percent" not in listed and listed["updated_at"] == payload["updated_at"] and listed["markdown"] == payload["markdown"]
        # ... in one small file beside the stored resume, which is itself byte for byte what it was.
        assert stored_file.read_bytes() == before and stored_file.with_suffix(".layout").read_text(encoding="utf-8") == '{"spacing_percent": 120}\n'
        saved_pdf = client.post("/api/tailored-resumes/pdf", json=key)
        assert float(saved_pdf.headers["x-gigai-spacing-scale"]) == 1.2 and _pages(saved_pdf.content) == again["pages"]
        # A spacing named for one PDF is used for it and not saved.
        one_off = client.post("/api/tailored-resumes/pdf", json={**key, "spacing_scale": 0.8})
        assert float(one_off.headers["x-gigai-spacing-scale"]) == 0.8 and client.post(_ROUTE, json=key).json()["spacing_scale"] == 1.2
        assert stored_file.read_bytes() == before
        assert not (home / "scout" / "resume-display.json").exists(), "the job's spacing is not the saved layout"

        # (d) unknown job, (e) shape errors: nothing is echoed.
        missing = client.post(_ROUTE, json={**key, "job_identity": "https://example.test/none", "spacing_scale": 0.9})
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "tailored_resume_not_found"
        for bad in ({}, {"profile_id": key["profile_id"]}, {**key, "extra": 1}, {**key, "spacing_scale": 0.2}, {**key, "spacing_scale": "1.0"}, {**key, "profile_id": ""}):
            response = client.post(_ROUTE, json=bad)
            assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_value", (bad, response.text)
        unusable = client.post(_ROUTE, json={**key, "header": {**_FORM, "fax": "555-0142-ZQ"}})
        assert unusable.status_code == 422 and unusable.json()["error"]["code"] == "unknown_key" and "555-0142-ZQ" not in unusable.text

        # (f) the write guards of every POST: another origin, another host.
        url = f"{server.base_url}{_ROUTE}"
        cross = httpx.post(url, json=key, headers={"Origin": "http://evil.example"}, timeout=30)
        assert cross.status_code == 403, cross.text
        rebound = httpx.post(url, json=key, headers={"Host": "evil.example"}, timeout=30)
        assert rebound.status_code in (400, 403), rebound.text
        assert client.post(_ROUTE, json=key).json()["spacing_scale"] == 1.2
        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)
    assert_clean_and_healthy(workpad, home)
