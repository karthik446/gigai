"""0110-046: the Generate PDF form's values are rendered and dropped, also when the render FAILS.

An in-process server (``serve``) so the failure can be forced: ``resume_pdf._compile`` is replaced by
one that raises an exception whose message CARRIES the form's values (the worst case: a renderer that
echoes its input).  The 500 answer, every log record of the server logger and the root logger, and every
file under the synthetic home and target hold none of the marker values; the same for the 422 paths and
a success.  Synthetic markers only (``Zora Quillfeather``, ``zora.q@example.invalid``, ``555-0142-ZQ``).
"""

from __future__ import annotations

import io
import logging
import threading
from pathlib import Path

import httpx
import pytest
from pypdf import PdfReader

from gigai.scout import resume_pdf
from gigai.scout.find_jobs.api.server import LOGGER_NAME, serve
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend

FORM = {
    "name": "Zora Quillfeather", "email": "zora.q@example.invalid", "phone": "555-0142-ZQ",
    "location": "Quillshire, ZZ", "linkedin": "linkedin.com/in/zq-invalid", "link": "zq.example.invalid",
}
MARKERS = ("Zora", "Quillfeather", "zora.q@example.invalid", "555-0142-ZQ", "Quillshire", "zq-invalid", "zq.example.invalid")
MARKDOWN = "## Summary\n\n- Platform engineer with nine years building billing systems.\n\n## Skills\n\n- Python, SQL\n"


@pytest.fixture
def server(tmp_path: Path):
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    log_file = home / "logs" / "server.log"
    log_file.parent.mkdir()
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    logger = logging.getLogger(LOGGER_NAME)
    root = logging.getLogger()
    logger.addHandler(handler)
    root.addHandler(handler)
    previous = root.level
    root.setLevel(logging.DEBUG)
    httpd = serve(backend=ScoutFindJobsBackend(home_root=home, target=target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{httpd.server_address[1]}", timeout=60.0) as client:
            yield client, home, target, log_file
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        logger.removeHandler(handler)
        root.removeHandler(handler)
        root.setLevel(previous)
        handler.close()


def _clean(response: httpx.Response) -> None:
    headers = "\n".join(f"{key}: {value}" for key, value in response.headers.items())
    body = "" if response.headers.get("content-type") == "application/pdf" else response.text
    for marker in MARKERS:
        assert marker not in headers and marker not in body, marker


def _holders(*roots: Path) -> list[tuple[Path, str]]:
    return [
        (path, marker)
        for root in roots
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
        for marker in MARKERS
        if marker.encode() in path.read_bytes()
    ]


def test_a_failing_render_echoes_and_logs_none_of_the_form(server, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    client, home, target, log_file = server

    def echoing_compile(template, directory, data, scale, timestamp):  # noqa: ANN001 - the replaced signature
        raise RuntimeError(f"typst failed on {data['name']} {data['contact']}")

    monkeypatch.setattr(resume_pdf, "_compile", echoing_compile)
    with caplog.at_level(logging.DEBUG):
        failed = client.post("/api/resume/pdf", json={"markdown": MARKDOWN, "header": FORM, "auto_fit": False})
    assert failed.status_code == 500 and failed.json()["error"] == {"code": "pdf_render_failed", "message": "the PDF could not be rendered"}
    _clean(failed)
    for marker in MARKERS:
        assert marker not in caplog.text, marker
    assert "POST /api/resume/pdf 500" in log_file.read_text(encoding="utf-8"), "the request line is logged (path and status only)"
    assert _holders(home, target) == []


def test_refusals_and_a_success_keep_nothing(server, caplog: pytest.LogCaptureFixture) -> None:
    client, home, target, log_file = server
    with caplog.at_level(logging.DEBUG):
        for body, code in (
            ({"markdown": MARKDOWN, "header": {**FORM, "fax": "555-0142-ZQ"}}, "unknown_key"),
            ({"markdown": MARKDOWN, "header": {**FORM, "name": "Zora Quillfeather " * 20}}, "invalid_value"),
            ({"markdown": MARKDOWN, "header": {**FORM, "phone": 5550142}}, "wrong_type"),
            ({"markdown": MARKDOWN, "header": FORM, "spacing_scale": 3}, "invalid_value"),
            ({"markdown": "## Hobbies\n- Zora Quillfeather\n", "header": FORM}, "resume_markdown_invalid"),
        ):
            refused = client.post("/api/resume/pdf", json=body)
            assert refused.status_code == 422 and refused.json()["error"]["code"] == code, refused.text
            _clean(refused)
        done = client.post("/api/resume/pdf", json={"markdown": MARKDOWN, "header": FORM})
    assert done.status_code == 200 and done.headers["content-type"] == "application/pdf"
    _clean(done)
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(done.content)).pages)
    assert text.startswith("ZORA QUILLFEATHER\nzora.q@example.invalid | 555-0142-ZQ | Quillshire, ZZ")
    for marker in MARKERS:
        assert marker not in caplog.text, marker
    assert _holders(home, target) == [], "the form's values are in no file: settings, logs, caches"
    assert not (home / "scout" / "resume-display.json").exists()
