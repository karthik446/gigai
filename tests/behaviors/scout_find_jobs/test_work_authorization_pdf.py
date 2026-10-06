"""0.1.11.3 item 6: the Generate PDF form's optional "Work authorization" line, in the renderer and the form parser.

The line is one of the form's values (``resume_display.HEADER_FIELDS``).  Since 0.1.11.3 item 15 the header is
compact: it prints INSIDE the header's one contact line, after the location (location | work authorization | links |
email | phone), and an empty value adds nothing (no separator either).  A resume that fit two pages still does;
through the route, the page-fit packet's failing-size pick (2 pages) stays on 2 pages with it added.  Synthetic
values only.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pypdf import PdfReader

from gigai.scout.resume_display import HEADER_FIELDS, MAX_VALUE, ContactItem, HeaderFormError, PdfHeader, form_header, parse_header_form
from gigai.scout.resume_pdf import render_pdf
from gigai.scout.tailored_resume import TailoredResume

from tests.behaviors.scout_find_jobs.test_pdf_fits_page_limit import fx  # noqa: F401 - the page-fit packet's fixture (a 2-page pick)

FIXTURE = Path(__file__).parent / "fixtures" / "resume_pdf_result.json"
STAMP = datetime(2026, 10, 6, tzinfo=timezone.utc)
LINE = "H-1B, requires sponsorship (ZQ-7731)"
FORM = {"name": "Riley Example", "email": "riley@example.test", "phone": "555-010-0100", "location": "Columbus, Ohio"}
TITLE = "Clinical Applications Manager"


def _pdf(header: PdfHeader | None) -> bytes:
    return render_pdf(TailoredResume.from_json(json.loads(FIXTURE.read_text())), header, company="Northwind", timestamp=STAMP)


def _text(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def _pages(data: bytes) -> int:
    return len(PdfReader(io.BytesIO(data)).pages)


def test_the_form_takes_an_optional_work_authorization_value() -> None:
    assert HEADER_FIELDS[-1] == "work_authorization"
    assert parse_header_form({**FORM, "work_authorization": f"  {LINE}  "})["work_authorization"] == LINE
    assert parse_header_form(FORM)["work_authorization"] == "", "left out: no line"
    for bad in (LINE + "\nsecond line", "x" * (MAX_VALUE + 1), 7):
        with pytest.raises(HeaderFormError) as refused:
            parse_header_form({**FORM, "work_authorization": bad})
        assert "ZQ-7731" not in str(refused.value), "a refusal names the field and the rule, never the value"
        assert "header.work_authorization" in str(refused.value)


def test_the_line_is_an_item_of_the_one_contact_line_after_the_location() -> None:
    header = form_header(parse_header_form({**FORM, "work_authorization": LINE}), TITLE)
    assert [(item.text, item.url) for item in header.contact] == [
        ("Columbus, Ohio", None), (LINE, None), ("riley@example.test", "mailto:riley@example.test"), ("555-010-0100", None),
    ]
    assert header.work_authorization == "", "the form's header has no line apart"
    assert len(form_header(parse_header_form(FORM), TITLE).contact) == 3


def test_the_pdf_prints_the_line_in_the_header_only_and_still_fits_two_pages() -> None:
    without = _pdf(form_header(parse_header_form(FORM), TITLE))
    with_line = _pdf(form_header(parse_header_form({**FORM, "work_authorization": LINE}), TITLE))
    text = _text(with_line)
    # Three header lines, in order: name, title, the ONE contact line with the work authorization in it; then the body.
    assert text.split("\n")[:3] == ["RILEY EXAMPLE", TITLE, f"Columbus, Ohio | {LINE} | riley@example.test | 555-010-0100"]
    assert text.count(LINE) == 1, "the line prints once, in the header"
    plain = _text(without)
    assert LINE not in plain and "sponsorship" not in plain.lower()
    assert plain.split("\n")[:3] == ["RILEY EXAMPLE", TITLE, "Columbus, Ohio | riley@example.test | 555-010-0100"]
    assert plain.split("\n")[3:] == text.split("\n")[3:], "only the one item is added: no line more"
    assert _pages(without) == 2 and _pages(with_line) == 2


def test_an_empty_value_and_a_headerless_pdf_print_no_line() -> None:
    empty = _pdf(form_header(parse_header_form({**FORM, "work_authorization": "   "}), TITLE))
    assert _text(empty) == _text(_pdf(form_header(parse_header_form(FORM), TITLE)))
    assert empty == _pdf(PdfHeader("Riley Example", TITLE, (
        ContactItem("Columbus, Ohio", None), ContactItem("riley@example.test", "mailto:riley@example.test"), ContactItem("555-010-0100", None),
    ))), "no value: the PDF is byte for byte the one without the field"
    headerless = _text(_pdf(None))
    assert LINE not in headerless and "sponsorship" not in headerless.lower()


def test_generate_pdf_with_the_line_still_fits_the_picks_two_pages(fx, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811 - the fixture
    """On the page-fit packet's failing-size resume (a pick of 2 pages, a header whose contact line wraps): the route's
    PDF with the work authorization line added to that header is still 2 pages, with no fit note."""
    import threading

    import httpx

    from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve
    from tests.behaviors.scout_find_jobs.test_pdf_fits_page_limit import FORM as LONG_FORM, JOB

    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=60)
    try:
        key = {"profile_id": fx.profile_id, "job_identity": JOB}
        response = client.post("/api/tailored-resumes/pdf", json={**key, "header": {**LONG_FORM, "work_authorization": LINE}})
        assert response.status_code == 200, response.text
        pages = [page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages]
        header = " ".join(line.strip() for line in pages[0].split("SUMMARY")[0].splitlines())
        assert f"Nowhere Springs, Colorado | {LINE} | linkedin.example.invalid" in header, "the line is in the contact line, after the location"
        assert len(pages) == 2, f"Generate PDF made {len(pages)} pages; the last holds: {pages[-1][:80]!r}"
        assert "x-gigai-fit-note" not in response.headers
        without = client.post("/api/tailored-resumes/pdf", json={**key, "header": LONG_FORM})
        assert len(PdfReader(io.BytesIO(without.content)).pages) == 2 and LINE not in _text(without.content)
    finally:
        client.close()
        server.shutdown()
        server.server_close()
    # Nothing of the line is on disk: the home (the master, the job resume, the resumes folder) and the target.
    holders = [str(path) for root in (fx.home_root, fx.target) for path in root.rglob("*") if path.is_file() and not path.is_symlink() and b"ZQ-7731" in path.read_bytes()]
    assert holders == [], holders
