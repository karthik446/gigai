"""0110-032: an agent changes two bullets and renders a new PDF -- over HTTP against the real supervised
server, and with ``gigai scout resume pdf``.  Offline fixtures, synthetic resume, no model call in the feature.

Tailor once (the fixture model's lossy marker, so one bullet is a fallback with a kept rewrite), then:

(a) the worked example: ``PUT /api/tailored-resumes/lines`` ``use: "custom"`` on two bullets; GET shows both
    as ``kind: custom`` with no refs and ``edited_from``; the stored ``.md`` marks them ``<!-- edited -->``;
    ``POST /api/tailored-resumes/pdf`` prints them;
(b) ``POST /api/resume/pdf`` with that resume's ``markdown`` answers a PDF whose extracted text and page
    count are IDENTICAL to the stored resume's PDF (the bytes differ only by the creation time and the
    document title, which carries the company for a stored resume), with ``X-GigAI-Pages``;
(c) the CLI: ``resume pdf --tailored --job-url`` writes the SAME BYTES the API's stored-resume route serves,
    and ``resume pdf --in`` the same text and page count as the markdown route;
(d) the personal-info check refuses a contact line and the saved name (422 ``personal_info_refused``), and
    nothing changes;
(e) ``use: original`` / ``use: rewritten`` switch an edited line back (a version the replaced line never
    had is refused: ``test_tailored_line_edit.py``);
(f) invalid markdown, an oversize body, an out-of-range spacing, an unknown key and shape errors are 422s
    that say what is wrong; CSRF / Host rejections;
(g) the markdown sent to ``/api/resume/pdf`` is not stored and not logged: its marker is in no file under
    the home (the server log lives there) or the target.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from pypdf import PdfReader

from gigai.cli import cli
from gigai.scout.find_jobs.bindings import TEST_MODEL_LOSSY_MARKER, TEST_MODEL_LOSSY_REWRITES
from gigai.scout.resume_pdf import MAX_MARKDOWN_BYTES

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import resolve_workpad_path, setup_and_init, start_server, stop_server, write_offline_find_jobs_config

_STAFF = "Managed a group of 4 analysts supporting scheduling and billing systems end to end, owning the release calendar, vendor contact, and staff training across 2 hospitals."
_DSAR = "Built the medication reconciliation (MRX) workflow handling admission and discharge lists end to end under HIPAA and OH state requirements."
_WEAKER_STAFF = TEST_MODEL_LOSSY_REWRITES[_STAFF.rstrip(".")]
_RESUME = f"## Experience\n**Clinical Applications Manager — Example Corp** (2020–present)\n- {_STAFF}\n- {_DSAR}\n"
_POSTING = "Acme is hiring a Staff Engineer to lead scheduling and billing systems and medication reconciliation. Requirements: Python."

_EDIT_ONE = "Managed 4 analysts running scheduling and billing systems across 2 hospitals, owning the release calendar and vendor contact."
_EDIT_TWO = "Built the medication reconciliation workflow for admission and discharge lists under HIPAA and OH state requirements."
_MARKER = "Zyxwv-marker-0110-032"
_OWN_MARKDOWN = f"""# Riley Example
riley@example.test | 555-010-0100

## Summary

- Clinical applications manager, {_MARKER}, with ten years in hospital systems.

## Experience

### Example Corp
Clinical Applications Manager | 2020 - Present

- {_EDIT_ONE}
- {_EDIT_TWO}

## Skills

- Python, SQL, HL7
"""


def _pages(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


def _flat(pdf: bytes) -> str:
    return " ".join(_text(pdf).split())


def _bullets(payload: dict) -> list[dict]:
    experience = next(section for section in payload["result"]["sections"] if section["heading"] == "experience")
    return experience["entries"][0]["bullets"]


def _error(response: httpx.Response) -> dict:
    assert response.headers["content-type"] == "application/json", response.content[:80]
    return response.json()["error"]


def test_an_agent_changes_two_bullets_and_renders_a_new_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        key = {"profile_id": payload["resume"]["profile_id"], "job_identity": payload["job"]["job_identity"]}
        stamp = payload["updated_at"]
        first, second = _bullets(payload)
        assert first["alternative"]["text"] == _WEAKER_STAFF and first["origin"] == "fallback"
        lines_url, md_url, stored_url = "/api/tailored-resumes/lines", "/api/resume/pdf", "/api/tailored-resumes/pdf"
        saved = client.put("/api/resume-display", json={"name": "Riley Example", "contact": [{"kind": "email", "value": "riley@example.test"}]})
        assert saved.status_code == 200, saved.text

        def shown() -> dict:
            items = client.get("/api/tailored-resumes", params=key).json()["items"]
            assert len(items) == 1
            return items[0]

        def edit(line_id: str, text: object, **extra: object) -> httpx.Response:
            return client.put(lines_url, json={**key, "updated_at": stamp, "line_id": line_id, "use": "custom", "text": text, **extra})

        before_pdf = client.post(stored_url, json=key)
        assert before_pdf.status_code == 200 and _STAFF in _flat(before_pdf.content)

        # (a) the worked example: change two bullets, then render.
        one = edit(first["id"], _EDIT_ONE)
        assert one.status_code == 200, one.text
        two = edit(second["id"], f"- {_EDIT_TWO}")  # a leading bullet marker is dropped
        assert two.status_code == 200, two.text
        edited = two.json()
        for line, text, was in zip(_bullets(edited), (_EDIT_ONE, _EDIT_TWO), (first, second)):
            assert (line["kind"], line["text"], line["refs"], line["origin"], line["id"]) == ("custom", text, [], "user", was["id"])
            assert "alternative" not in line and "reason" not in line, "no source and no no-loss claim on an edited line"
            assert line["edited_from"] == was
        assert edited["updated_at"] == stamp and shown() == edited
        markdown = edited["markdown"]
        assert f"- {_EDIT_ONE} <!-- edited -->" in markdown and f"- {_EDIT_TWO} <!-- edited -->" in markdown
        assert Path(edited["markdown_path"]).read_text(encoding="utf-8") == markdown
        stored_pdf = client.post(stored_url, json=key)
        assert stored_pdf.status_code == 200 and stored_pdf.content.startswith(b"%PDF")
        flat = _flat(stored_pdf.content)
        assert _EDIT_ONE in flat and _EDIT_TWO in flat and _STAFF not in flat and _DSAR not in flat and "edited" not in flat
        assert edit(first["id"], _EDIT_ONE).json() == edited, "the same edit again changes nothing"

        # (b) the same resume as markdown through POST /api/resume/pdf: same text, same page count.
        from_markdown = client.post(md_url, json={"markdown": markdown, "profile_id": key["profile_id"]})
        assert from_markdown.status_code == 200, from_markdown.text
        assert from_markdown.headers["content-type"] == "application/pdf" and from_markdown.content.startswith(b"%PDF")
        assert from_markdown.headers["content-disposition"] == 'attachment; filename="riley-example-resume.pdf"'
        assert int(from_markdown.headers["content-length"]) == len(from_markdown.content)
        assert _text(from_markdown.content) == _text(stored_pdf.content), "text-identical to the UI path's PDF"
        assert _pages(from_markdown.content) == _pages(stored_pdf.content) == int(from_markdown.headers["x-gigai-pages"]) == 1
        assert _text(from_markdown.content).startswith("RILEY EXAMPLE\nriley@example.test\n")

        # An agent's own markdown: the saved header prints, the markdown's own name/contact lines do not.
        own = client.post(md_url, json={"markdown": _OWN_MARKDOWN, "spacing_scale": 0.8})
        assert own.status_code == 200 and own.headers["x-gigai-spacing-scale"] == "0.8", own.text
        own_text = _text(own.content)
        assert own_text.startswith("RILEY EXAMPLE\nriley@example.test\nSUMMARY") and own_text.count("riley@example.test") == 1
        assert "555-010-0100" not in own_text and _MARKER in own_text and "Python SQL HL7" in own_text
        looser = client.post(md_url, json={"markdown": _OWN_MARKDOWN, "spacing_scale": 1.4, "auto_fit": False})
        assert looser.status_code == 200 and looser.headers["x-gigai-spacing-scale"] == "1.4" and looser.content != own.content
        fitted = client.post(md_url, json={"markdown": _OWN_MARKDOWN, "auto_fit": True})
        assert fitted.status_code == 200 and 0.7 <= float(fitted.headers["x-gigai-spacing-scale"]) <= 1.4

        # (c) the CLI, against the same home and target.
        cli_stored, cli_md, md_file = tmp_path / "stored.pdf", tmp_path / "md.pdf", tmp_path / "tailored.md"
        ran = CliRunner().invoke(cli, ["scout", "resume", "pdf", "--tailored", "--job-url", key["job_identity"], "--out", str(cli_stored), "--home", str(home), "--target", str(target), "--json"])
        assert ran.exit_code == 0, ran.output
        assert json.loads(ran.output)["source"] == "tailored" and json.loads(ran.output)["pages"] == 1
        assert cli_stored.read_bytes() == stored_pdf.content, "the CLI writes the bytes the UI's Download PDF gets"
        md_file.write_text(markdown, encoding="utf-8")
        ran = CliRunner().invoke(cli, ["scout", "resume", "pdf", "--in", str(md_file), "--profile", key["profile_id"], "--out", str(cli_md), "--home", str(home), "--target", str(target), "--json"])
        assert ran.exit_code == 0, ran.output
        assert cli_md.read_bytes().startswith(b"%PDF") and json.loads(ran.output)["pages"] == 1
        assert _text(cli_md.read_bytes()) == _text(from_markdown.content) == _text(stored_pdf.content)
        missing = CliRunner().invoke(cli, ["scout", "resume", "pdf", "--tailored", "--job-url", "https://example.test/none", "--out", str(tmp_path / "x.pdf"), "--home", str(home), "--target", str(target), "--json"])
        assert missing.exit_code == 1 and json.loads(missing.output)["error"]["code"] == "tailored_resume_not_found"

        # (d) the personal-info check refuses contact details and the saved name; nothing changes.
        for text, found in (
            ("Reach me at riley@example.test for references.", "email"),
            ("Call 555-010-0100 after 5pm.", "phone"),
            ("Code samples at github.com/riley-example", "links"),
            ("Mentored by Riley Example on the platform team.", "name"),
        ):
            refused = edit(first["id"], text)
            assert refused.status_code == 422 and _error(refused)["code"] == "personal_info_refused", (text, refused.text)
            assert f"({found})" in _error(refused)["message"] and text not in refused.text
        assert shown() == edited

        # shape errors of the edit.
        for bad in ("", "   ", 7, "two\nlines", "x" * 401):
            response = edit(first["id"], bad)
            assert response.status_code == 422 and _error(response)["code"] == "invalid_value", (bad, response.text)
        no_text = client.put(lines_url, json={**key, "updated_at": stamp, "line_id": first["id"], "use": "custom"})
        stray_text = client.put(lines_url, json={**key, "updated_at": stamp, "line_id": first["id"], "use": "original", "text": "x"})
        for response in (no_text, stray_text):
            assert response.status_code == 422 and _error(response)["code"] == "invalid_value"
        stale = edit(first["id"], "A newer wording.", updated_at="2020-01-01T00:00:00+00:00")
        assert stale.status_code == 409 and _error(stale)["code"] == "tailored_resume_changed"
        assert shown() == edited

        # (e) the way back: the rewrite each line kept (both are fallbacks here), then the original.
        rewrite = client.put(lines_url, json={**key, "updated_at": stamp, "line_id": first["id"], "use": "rewritten"})
        assert rewrite.status_code == 200, rewrite.text
        swapped = _bullets(rewrite.json())[0]
        assert (swapped["text"], swapped["kind"], swapped["origin"]) == (_WEAKER_STAFF, "rewritten", "user") and "edited_from" not in swapped
        assert swapped["alternative"]["text"] == first["text"] and swapped["alternative"]["lost"] == first["alternative"]["lost"]
        assert _bullets(rewrite.json())[1]["kind"] == "custom", "the other edited line is untouched"
        for line in (first, second):
            back = client.put(lines_url, json={**key, "updated_at": stamp, "line_id": line["id"], "use": "original"})
            assert back.status_code == 200, back.text
        restored = shown()
        assert [bullet["text"] for bullet in _bullets(restored)] == [f"- {_STAFF}", f"- {_DSAR}"]
        assert _bullets(restored)[1] == second and "edited_from" not in _bullets(restored)[0] and "edited" not in restored["markdown"]
        assert _text(client.post(stored_url, json=key).content) == _text(before_pdf.content)

        # (f) clear 422s on the markdown route.
        invalid = client.post(md_url, json={"markdown": "## Hobbies\n- Chess\n"})
        assert invalid.status_code == 422 and _error(invalid)["code"] == "resume_markdown_invalid"
        assert _error(invalid)["message"].startswith("line 1: unknown section; use ## Summary") and "Chess" not in invalid.text
        empty = client.post(md_url, json={"markdown": "no sections here"})
        assert empty.status_code == 422 and _error(empty)["code"] == "resume_markdown_invalid" and "no resume content" in _error(empty)["message"]
        for size in (MAX_MARKDOWN_BYTES + 1, 20 * MAX_MARKDOWN_BYTES):  # past the markdown cap; past the body cap
            oversize = client.post(md_url, json={"markdown": "## Summary\n" + "x" * size})
            assert oversize.status_code == 422 and _error(oversize)["code"] == "resume_markdown_too_large", size
            assert str(MAX_MARKDOWN_BYTES) in _error(oversize)["message"]
        for spacing in (0.69, 1.41, 3):
            out_of_range = client.post(md_url, json={"markdown": _OWN_MARKDOWN, "spacing_scale": spacing})
            assert out_of_range.status_code == 422 and _error(out_of_range) == {"code": "invalid_value", "message": "spacing_scale must be between 0.7 and 1.4"}
        unknown = client.post(md_url, json={"markdown": _OWN_MARKDOWN, "company": "Acme"})
        assert unknown.status_code == 422 and _error(unknown)["code"] == "unknown_key"
        assert _error(unknown)["allowed_keys"] == ["auto_fit", "markdown", "profile_id", "spacing_scale"]
        for bad_body, code in (
            ({}, "wrong_type"),
            ({"markdown": 7}, "wrong_type"),
            ({"markdown": _OWN_MARKDOWN, "spacing_scale": "1.0"}, "wrong_type"),
            ({"markdown": _OWN_MARKDOWN, "spacing_scale": True}, "wrong_type"),
            ({"markdown": _OWN_MARKDOWN, "auto_fit": "yes"}, "wrong_type"),
            ({"markdown": _OWN_MARKDOWN, "profile_id": "../x"}, "invalid_value"),
        ):
            response = client.post(md_url, json=bad_body)
            assert response.status_code == 422 and _error(response)["code"] == code, (bad_body, response.text)
        not_object = client.post(md_url, json=["## Summary"])
        assert not_object.status_code == 422 and _error(not_object)["code"] == "wrong_type"

        # CSRF / Host, as on every other POST route.
        full = f"{server.base_url}{md_url}"
        wrong_origin = httpx.post(full, json={"markdown": _OWN_MARKDOWN}, headers={"Origin": "http://evil.example.test"})
        assert wrong_origin.status_code == 403 and _error(wrong_origin)["code"] == "forbidden_origin"
        wrong_type = httpx.post(full, content=json.dumps({"markdown": _OWN_MARKDOWN}).encode(), headers={"Content-Type": "text/plain"})
        assert wrong_type.status_code == 415
        wrong_host = httpx.post(full, json={"markdown": _OWN_MARKDOWN}, headers={"Host": "evil.example.test"})
        assert wrong_host.status_code == 403 and not wrong_host.content.startswith(b"%PDF")

        # The server still answers after the refusals (the oversize body did not poison the connection).
        assert client.post(md_url, json={"markdown": _OWN_MARKDOWN}).status_code == 200

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    # (g) the markdown sent to /api/resume/pdf was rendered, never stored and never logged.
    holders = [
        path
        for root in (home, target)
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink() and _MARKER.encode() in path.read_bytes()
    ]
    assert holders == [], f"the posted markdown must not be written anywhere: {holders}"
    assert (home / "logs").is_dir() and any((home / "logs").iterdir()), "the server log this checks is under the home"

    assert_clean_and_healthy(workpad, home)
