"""0.1.11.5 PH: the job page's preview SHOWS A HEADER, on the END outcome (the page pictures and the PDFs that come back).

Until now the preview kept a blank block where the header goes.  Now ``POST /api/tailored-resumes/preview``, with no
``header`` in its body:

1. shows the person's own header from their header file (``<home>/header.json`` here) to Scout's own browser page (a
   request with this server's ``Origin``), by the reader the Generate PDF form uses: empty and ``REPLACE`` values are
   skipped, and a file with no usable name is not a header;
2. otherwise shows a PLACEHOLDER header of the same size ("Your Name" and one invented contact line, in a lighter
   grey), and ``header_shown`` says which (``file`` | ``placeholder`` | ``form``), never a value;
3. has the pages of the downloaded PDF with a header of that size: the route with the form, the CLI with ``--header``
   and with the default file;
4. NEVER lets the placeholder into a PDF: no PDF route and no ``resume pdf`` prints it, the headerless PDF is what it
   was, and the renderer refuses a placeholder header for anything but the preview's pictures;
5. answers the REAL header only to this server's own ``Origin``: any other caller (an agent, curl) gets the
   placeholder pictures, byte for byte the ones of a home with no header file.

A page picture holds no text, so "which header is in it" is checked by a deterministic render: the route's pictures
are compared, byte for byte, with the pictures the renderer makes from a known header.

Synthetic only: an invented person on reserved domains, tmp homes.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from gigai.scout import pdf_header_file
from gigai.scout.resume_display import DisplaySettings, form_header, parse_header_form, save_display
from gigai.scout.resume_pdf import PLACEHOLDER_CONTACT, PLACEHOLDER_NAME, _body, _render, placeholder_header, stored_resume_pdf

from tests.behaviors.scout_find_jobs.test_pdf_header_file import FILE, MARKERS
from tests.behaviors.scout_find_jobs.test_resume_preview_spacing import STAMP, _home_files, _invoke, _pages, _Server, _stored, fx, server  # noqa: F401 - fx and server are fixtures
from tests.support.pipeline_fixtures import JOB, PipelineFixture

#: A form with the placeholder's own words: "a header of that size".
SAME_SIZE = {"name": PLACEHOLDER_NAME, "location": "City, State", "email": "email@example.com", "phone": "555-0100", "github": "you"}
PLACEHOLDER_WORDS = ("Your Name", "YOUR NAME", "email@example.com", "github.com/you", "555-0100", "City, State")
SPACINGS = (0.7, 0.85, 1.0, 1.05, 1.2)


def _page(running: _Server) -> dict[str, str]:
    """What Scout's own browser page sends on every write: this server's own ``Origin``."""
    return {"Origin": str(running.client.base_url).rstrip("/")}


def _preview(running: _Server, *, origin: bool, **body: object) -> tuple[dict, list[bytes]]:
    response = running.client.post("/api/tailored-resumes/preview", json={**running.key, **body}, headers=_page(running) if origin else {})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store", "the pictures may hold the person's header: never cached"
    payload = response.json()
    pictures = [base64.b64decode(image) for image in payload["images"]]
    assert pictures and all(picture.startswith(b"\x89PNG") for picture in pictures) and len(pictures) == payload["pages"]
    for marker in MARKERS:
        assert marker not in json.dumps({key: value for key, value in payload.items() if key != "images"}), f"the answer names {marker!r} outside the pictures"
    return payload, pictures


def _pictures(fx_: PipelineFixture, **how: Any) -> list[bytes]:
    """The page pictures the renderer itself makes of the job's stored resume (no server, no file read)."""
    return list(stored_resume_pdf(_stored(fx_), home_root=fx_.home_root, target=fx_.target, images=True, **how)[0].page_images)


def _header_file(fx_: PipelineFixture, tmp_path: Path, content: object = FILE) -> Path:
    path = pdf_header_file.default_path(fx_.home_root)
    assert tmp_path in path.parents, f"the header file of this test is not in its tmp home: {path}"
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    path.chmod(0o600)
    return path


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


def test_with_no_header_file_the_preview_shows_the_placeholder_header_and_says_so(fx: PipelineFixture, server: _Server, tmp_path: Path) -> None:  # noqa: F811
    assert not pdf_header_file.default_path(fx.home_root).exists() and tmp_path in pdf_header_file.default_path(fx.home_root).parents
    before = _home_files(fx)
    blank = _pictures(fx)  # what the preview showed until now: the header's block kept blank
    for origin in (True, False):
        payload, pictures = _preview(server, origin=origin)
        assert payload["header_shown"] == "placeholder"
        assert pictures == _pictures(fx, placeholder=True), "the pictures are not the placeholder header's"
        assert pictures[0] != blank[0], "the first page still has a blank block where the header goes"
        assert pictures[1:] == blank[1:], "the placeholder is as tall as the blank block it replaces: the pages after it did not move"
    # In a lighter grey than a person's header of the very same words, and nothing else of the page differs in size.
    real = _pictures(fx, form=parse_header_form(SAME_SIZE))
    assert len(real) == len(pictures) and real[0] != pictures[0] and real[1:] == pictures[1:]
    assert _home_files(fx) == before, "showing the preview wrote to the home"


def test_the_placeholder_previews_page_count_is_the_pdfs_with_a_header_of_that_size(fx: PipelineFixture, server: _Server, tmp_path: Path) -> None:  # noqa: F811
    same_size = tmp_path / "same-size.json"
    same_size.write_text(json.dumps(SAME_SIZE), encoding="utf-8")
    same_size.chmod(0o600)
    counts = {}
    for spacing in SPACINGS:
        shown, _pictures_ = _preview(server, origin=True, spacing_scale=spacing)
        assert shown["header_shown"] == "placeholder" and shown["spacing_scale"] == spacing
        pdf = server.pdf(header=SAME_SIZE)
        assert _pages(pdf.content) == int(pdf.headers["x-gigai-pages"]) == shown["pages"], f"placeholder preview and PDF differ at {spacing}"
        out = tmp_path / "out" / f"cli-{spacing}.pdf"
        payload = _invoke(fx, "resume", "pdf", "--job-url", JOB, "--header", str(same_size), "--out", str(out))
        assert (payload["spacing_scale"], payload["pages"], _pages(out.read_bytes())) == (spacing, shown["pages"], shown["pages"])
        counts[spacing] = shown["pages"]
    assert counts == {0.7: 2, 0.85: 2, 1.0: 2, 1.05: 2, 1.2: 3}
    # A saved title prints under the name in a real header, so the placeholder holds that line too: still the PDF's pages.
    save_display(fx.home_root, DisplaySettings(titles={fx.profile_id: "Staff Platform Engineer"}))
    titled, pictures = _preview(server, origin=True, spacing_scale=1.05)
    assert pictures == _pictures(fx, placeholder=True) and titled["pages"] == _pages(server.pdf(header=SAME_SIZE).content)
    assert pictures[0] != _pictures(fx, form=None)[0]


def test_with_a_header_file_scouts_own_page_sees_that_header_and_no_other_caller_does(fx: PipelineFixture, server: _Server, tmp_path: Path) -> None:  # noqa: F811
    placeholder = _pictures(fx, placeholder=True)
    header_file = _header_file(fx, tmp_path)
    before = _home_files(fx)
    form = pdf_header_file.render_form(pdf_header_file.read_header_file(header_file), visa_required=False)

    mine, pictures = _preview(server, origin=True)
    assert mine["header_shown"] == "file"
    assert pictures == _pictures(fx, form=form), "Scout's own page does not see the header of the file"
    assert pictures[0] != placeholder[0]
    # The same pictures the open Generate PDF form gets for those values (it sends them itself).
    typed, typed_pictures = _preview(server, origin=True, header=form)
    assert typed["header_shown"] == "form" and typed_pictures == pictures

    # NOBODY ELSE: no Origin (curl, a script, the user's agent) is the placeholder, byte for byte; a foreign Origin is refused.
    theirs, their_pictures = _preview(server, origin=False)
    assert theirs["header_shown"] == "placeholder" and their_pictures == placeholder, "a caller without this server's Origin got something of the file"
    assert their_pictures[0] != pictures[0]
    for foreign in ("http://evil.example.invalid", "http://127.0.0.1:1"):
        refused = server.client.post("/api/tailored-resumes/preview", json=server.key, headers={"Origin": foreign})
        assert refused.status_code == 403 and "images" not in refused.json(), refused.text
    # The PDF routes are as they were: no header in the body, no header in the PDF, with or without the page's Origin.
    for headers in ({}, _page(server)):
        headerless = server.client.post("/api/tailored-resumes/pdf", json=server.key, headers=headers)
        assert headerless.status_code == 200 and "x-gigai-finish-url" in headerless.headers
        for marker in (*MARKERS, *PLACEHOLDER_WORDS):
            assert marker not in _text(headerless.content), marker
    assert _home_files(fx) == before, "showing the header wrote to the home (the file is read for the render only)"

    # THE PAGE COUNT is the downloaded PDF's with that header: the route with the form, the CLI with --header and the default file.
    for spacing in SPACINGS:
        shown, _ignored = _preview(server, origin=True, spacing_scale=spacing)
        assert shown["header_shown"] == "file" and shown["spacing_scale"] == spacing
        pdf = server.pdf(header=form)
        assert _pages(pdf.content) == int(pdf.headers["x-gigai-pages"]) == shown["pages"], f"preview and PDF differ at {spacing}"
        assert "ZORA QUILLFEATHER" in _text(pdf.content)
        for args in (["--header", str(header_file)], []):  # named, and the default file
            out = tmp_path / "out" / f"cli-{spacing}-{len(args)}.pdf"
            payload = _invoke(fx, "resume", "pdf", "--job-url", JOB, *args, "--out", str(out))
            assert (payload["header"], payload["spacing_scale"], payload["pages"], _pages(out.read_bytes())) == (True, spacing, shown["pages"], shown["pages"])


@pytest.mark.parametrize(
    "content",
    [
        {key: value for key, value in FILE.items() if key != "name"},  # no name
        {**FILE, "name": ""},
        {**FILE, "name": "REPLACE: your full name"},
        {"name": "REPLACE: your full name", "email": "REPLACE: you@example.com"},  # every value still a placeholder
        '{"name": "Zora Quillfeather", "email": ',  # not JSON
        {**FILE, "phone": ["555-0142-ZQ"]},  # invalid
    ],
    ids=["no-name", "empty-name", "placeholder-name", "all-placeholders", "not-json", "invalid"],
)
def test_a_file_that_cannot_make_a_header_shows_the_placeholder(fx: PipelineFixture, server: _Server, tmp_path: Path, content: object) -> None:  # noqa: F811
    placeholder = _pictures(fx, placeholder=True)
    _header_file(fx, tmp_path, content)
    shown, pictures = _preview(server, origin=True)
    assert shown["header_shown"] == "placeholder" and pictures == placeholder, "a file with no usable header printed something"


def test_a_files_empty_and_replace_values_are_skipped_in_the_preview(fx: PipelineFixture, server: _Server, tmp_path: Path) -> None:  # noqa: F811
    _header_file(fx, tmp_path, {"name": "Zora Quillfeather", "email": "REPLACE: you@example.com", "phone": "", "location": "Quillshire, ZZ", "github": "REPLACE: your-id"})
    shown, pictures = _preview(server, origin=True)
    assert shown["header_shown"] == "file"
    assert pictures == _pictures(fx, form=parse_header_form({"name": "Zora Quillfeather", "location": "Quillshire, ZZ"})), "a skipped value printed, or a real one did not"


def test_no_downloaded_pdf_ever_holds_the_placeholder(fx: PipelineFixture, server: _Server, tmp_path: Path) -> None:  # noqa: F811
    """No header form and no header file: every PDF is the headerless one it was, and the placeholder is in none."""
    assert not pdf_header_file.default_path(fx.home_root).exists()
    stored = _stored(fx)
    assert _preview(server, origin=True)[0]["header_shown"] == "placeholder"  # the preview has just shown it
    route = server.client.post("/api/tailored-resumes/pdf", json=server.key, headers=_page(server))
    markdown = server.client.post("/api/resume/pdf", json={"markdown": stored.markdown}, headers=_page(server))
    out = tmp_path / "cli.pdf"
    to_file = _invoke(fx, "resume", "pdf", "--job-url", JOB, "--out", str(out))
    in_folder = _invoke(fx, "resume", "pdf", "--job-url", JOB)
    assert to_file["header"] is False and in_folder["header"] is False and to_file["finish_url"] and in_folder["finish_url"]
    pdfs = {"the PDF route": route.content, "the markdown PDF route": markdown.content, "resume pdf --out": out.read_bytes(), "resume pdf": Path(in_folder["out_path"]).read_bytes()}
    for name, pdf in pdfs.items():
        assert pdf.startswith(b"%PDF"), name
        text = _text(pdf)
        assert "EXPERIENCE" in text, f"{name}: the PDF's text was not read"
        for word in PLACEHOLDER_WORDS:
            assert word not in text and word.encode() not in pdf, f"{name} holds the placeholder's {word!r}"
    for response in (route, markdown):
        assert response.status_code == 200 and "x-gigai-finish-url" in response.headers, "the headerless PDF no longer names the page that finishes it"
    # The headerless PDF is the renderer's own headerless render, with the blank block it always kept.
    assert route.content == stored_resume_pdf(stored, home_root=fx.home_root, target=fx.target)[0].pdf
    # BY CONSTRUCTION: the renderer makes no PDF with a placeholder header.
    with pytest.raises(ValueError, match="a PDF never prints it"):
        stored_resume_pdf(stored, home_root=fx.home_root, target=fx.target, placeholder=True)
    with pytest.raises(ValueError, match="a PDF never prints it"):
        _render(_body(stored.result), placeholder_header(), company="", timestamp=STAMP, spacing_scale=1.0, auto_fit=False)
    assert placeholder_header().placeholder and not form_header(parse_header_form(SAME_SIZE)).placeholder
    assert (placeholder_header("Staff Engineer").name, placeholder_header("Staff Engineer").title) == (PLACEHOLDER_NAME, "Staff Engineer")
    assert " | ".join(item.text for item in placeholder_header().contact) == "City, State | email@example.com | 555-0100 | github.com/you" == " | ".join(PLACEHOLDER_CONTACT)
