"""0.1.11.4 C2: ``gigai scout cover-letter pdf --in FILE --out FILE`` -- the agent's letter as a ONE-PAGE PDF.

The END outcome is the PDF a user sends and what the command says about it:

- the PDF's text is the letter's paragraphs, under the compact header (the name and ONE contact line) of the user's
  own header file, in the resume's template family;
- one page: a letter a little too long is set tighter, and one that still needs a second page is reported in one
  plain sentence with its page count (never a silent second page);
- the header file is read by the rules of ``gigai scout resume pdf --header``: the same refusals for a missing,
  invalid, placeholder or name-less file, in the same words;
- PRIVACY: no value of the header file is in the command's stdout, stderr, JSON or a log; the letter is never
  written to the resumes folder, nor (0.1.11.4 J4) into the jobs folder, where the letter's markdown lives and
  agents read; a claims trace is never printed.

Synthetic values, tmp homes only (the default header file of a scratch home is ``<home>/header.json``).
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
from importlib import resources
from pathlib import Path

import pytest
from click.testing import CliRunner
from pypdf import PdfReader

from gigai.cli import cli
from gigai.scout import cover_letter

from tests.behaviors.scout_find_jobs.test_pdf_header_file import FILE, MARKERS

GREETING = "Dear Acme hiring team,"
OPENING = (
    "I am writing about the Staff Software Engineer role on your Payments Platform team. I have\n"
    "built backend systems for twelve years, the last five as a technical lead."
)
BRIDGE = "At Initech I led the rewrite of the billing service that moved 40 internal teams off a shared\ndatabase, and cut failed nightly settlement runs from 9 a month to 1."
CLOSE = "I took apart my first radio at eleven and have been asking how things work ever since."
SIGN_OFF = "Thank you for reading,\nPat Example"
LETTER = "\n\n".join((GREETING, OPENING, BRIDGE, CLOSE, SIGN_OFF)) + "\n"
#: What a claims trace beside the letter holds: none of it may reach the PDF or the output.
TRACE = "# Claims trace: Acme\n\n| Sentence | Master line |\n| --- | --- |\n| I led the rewrite | b-7a8b9c: \"Led billing service rewrite (fernquist marker)\" |\n"
#: 0.1.11.3 items 15/16: ONE contact line: location | work authorization | links | email | phone.
CONTACT = "Quillshire, ZZ | VISA: H1B (ZQ-7731) | github.com/zq-invalid-7731 | zq-invalid-7731.example.invalid | linkedin.com/in/zq-invalid-7731 | zora.q@example.invalid | 555-0142-ZQ"
FILLER = "At Initech I measured a great many things over a long time, and every one of them was written down. "
LONG = "\n\n".join((GREETING, *((FILLER * 6).strip() for _ in range(8)), SIGN_OFF)) + "\n"


@pytest.fixture
def fx(tmp_path: Path) -> dict[str, Path]:
    home, work = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    work.mkdir()
    letter = work / "acme-staff-software-engineer-2026-10-06.md"
    letter.write_text(LETTER, encoding="utf-8")
    letter.with_name("acme-staff-software-engineer-2026-10-06.claims.md").write_text(TRACE, encoding="utf-8")
    return {"home": home, "work": work, "letter": letter, "out": work / "letter.pdf", "header": work / "my-header.json"}


def _header(path: Path, content: object = FILE, mode: int = 0o600) -> Path:
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    os.chmod(path, mode)
    return path


def _pdf(fx: dict[str, Path], *args: str, as_json: bool = True, letter: Path | None = None):
    command = ["scout", "cover-letter", "pdf", "--in", str(letter or fx["letter"]), "--home", str(fx["home"]), *args, *(["--json"] if as_json else [])]
    return CliRunner().invoke(cli, command)


def _said(result) -> str:
    """Everything the command printed: stdout and stderr."""
    return result.stdout + result.stderr


def _pages(path: Path) -> list[str]:
    return [page.extract_text() for page in PdfReader(io.BytesIO(path.read_bytes())).pages]


def _squeeze(*parts: str) -> str:
    return "".join("".join(parts).split())


def _squeezed(path: Path) -> str:
    """The PDF's text with no white space: where a line wraps is the layout's business."""
    return _squeeze(*_pages(path))


def _silent(text: str) -> None:
    for marker in MARKERS:
        assert marker not in text, f"{marker!r} is in the command's output: {text}"


def _disk_is_clean(fx: dict[str, Path], *allowed: Path) -> None:
    """No file under the home or the working folder holds a value of the header file, but the file itself and ``allowed``."""
    kept = {fx["header"], fx["home"] / "header.json", *allowed}
    holders = [
        (str(path), marker)
        for root in (fx["home"], fx["work"])
        for path in root.rglob("*")
        if path.is_file() and path not in kept
        for marker in MARKERS
        if marker.encode() in path.read_bytes()
    ]
    assert holders == [], holders


# --- the letter, under the compact header, on one page -----------------------------------------------------------


def test_the_pdf_is_the_letters_paragraphs_under_the_compact_header_on_one_page(fx: dict[str, Path]) -> None:
    done = _pdf(fx, "--out", str(fx["out"]), "--header", str(_header(fx["header"])))
    assert done.exit_code == 0, _said(done)
    payload = json.loads(done.stdout)
    assert payload["ok"] is True and payload["out_path"] == str(fx["out"])
    assert (payload["pages"], payload["one_page"], payload["spacing_scale"], payload["note"]) == (1, True, 1.0, None)
    assert payload["header"] is True and payload["header_file"] == str(fx["header"]) and payload["header_note"] is None
    assert payload["words"] == len(LETTER.split()) and payload["bytes"] == fx["out"].stat().st_size
    pages = _pages(fx["out"])
    assert len(pages) == 1
    # The compact header: the name line, then ONE contact line; then the letter, paragraph by paragraph, in order.
    assert _squeezed(fx["out"]) == _squeeze("ZORA QUILLFEATHER", CONTACT, GREETING, OPENING, BRIDGE, CLOSE, SIGN_OFF)
    lines = pages[0].split("\n")
    assert _squeeze(lines[0]) == "ZORAQUILLFEATHER"
    # A paragraph of short lines keeps them: the sign-off is two lines, the name under the closing words.
    assert [line.strip() for line in lines[-2:]] == ["Thank you for reading,", "Pat Example"]
    # A hard-wrapped paragraph is one paragraph: the source's line break is not a line break of the PDF.
    assert "I have built backend systems" in " ".join(" ".join(lines).split())
    # The claims trace beside the letter is never opened: nothing of it is printed, anywhere.
    assert "fernquist" not in pages[0] and "Claims trace" not in pages[0] and "fernquist" not in _said(done)


def test_the_command_prints_no_value_of_the_header_file_and_writes_only_the_pdf(fx: dict[str, Path], caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    _header(fx["header"])
    as_json = _pdf(fx, "--out", str(fx["out"]), "--header", str(fx["header"]))
    as_text = _pdf(fx, "--out", str(fx["out"]), "--header", str(fx["header"]), as_json=False)
    assert as_json.exit_code == 0 and as_text.exit_code == 0, _said(as_json) + _said(as_text)
    assert as_text.stdout == f"Wrote {fx['out']} (1 page, {len(LETTER.split())} words, spacing 1), with your name and contact details from {fx['header']}.\n"
    for result in (as_json, as_text):
        _silent(_said(result))
        # ... and no word of the letter either: the output is a path, counts and notes.
        assert "Initech" not in _said(result) and "Pat Example" not in _said(result)
    _silent(caplog.text)
    assert "Initech" not in caplog.text
    # Only the PDF the user asked for holds the values: no resumes folder, no store, no copy of the header file.
    assert not (fx["home"] / "resumes").exists() and not (fx["home"] / "scout").exists()
    _disk_is_clean(fx, fx["out"])
    assert sorted(path.name for path in fx["work"].iterdir()) == sorted([fx["letter"].name, fx["letter"].name.replace(".md", ".claims.md"), "letter.pdf", "my-header.json"])
    assert json.loads(fx["header"].read_text(encoding="utf-8")) == FILE, "the header file is read, never rewritten"


def test_the_default_header_file_is_used_and_no_header_leaves_it_unread(fx: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _header(fx["home"] / "header.json")
    done = _pdf(fx, "--out", str(fx["out"]))
    assert done.exit_code == 0, _said(done)
    assert json.loads(done.stdout)["header"] is True and _squeezed(fx["out"]).startswith(_squeeze("ZORA QUILLFEATHER", CONTACT, GREETING))
    _silent(_said(done).replace(str(fx["home"]), ""))

    # --no-header: the same letter with no header, and the file is not even opened.
    from gigai.scout import pdf_header_file

    def never(*_args: object, **_kwargs: object):
        raise AssertionError("--no-header must not read the header file")

    monkeypatch.setattr(pdf_header_file, "read_header_file", never)
    plain = _pdf(fx, "--out", str(fx["out"]), "--no-header")
    assert plain.exit_code == 0, _said(plain)
    payload = json.loads(plain.stdout)
    assert (payload["header"], payload["header_file"], payload["header_note"], payload["pages"]) == (False, None, None, 1)
    assert _squeezed(fx["out"]) == _squeeze(GREETING, OPENING, BRIDGE, CLOSE, SIGN_OFF)
    _disk_is_clean(fx)


def test_without_a_header_file_the_letter_has_no_header_and_the_note_says_where_the_file_goes(fx: dict[str, Path]) -> None:
    done = _pdf(fx, "--out", str(fx["out"]))
    assert done.exit_code == 0, _said(done)
    payload = json.loads(done.stdout)
    assert payload["header"] is False and payload["header_file"] is None and payload["pages"] == 1
    assert payload["header_note"].startswith("There is no header file at ") and "GigAI only reads it when it makes a PDF." in payload["header_note"]
    assert _squeezed(fx["out"]).startswith(_squeeze(GREETING))
    text = _pdf(fx, "--out", str(fx["out"]), as_json=False)
    assert ", without a header.\nThere is no header file at " in text.stdout


def test_a_file_other_users_can_read_is_used_and_warned_about(fx: dict[str, Path]) -> None:
    done = _pdf(fx, "--out", str(fx["out"]), "--header", str(_header(fx["header"], mode=0o644)), as_json=False)
    assert done.exit_code == 0, _said(done)
    assert f"{fx['header']} can be read by other users of this computer. To keep it to yourself: chmod 600 {fx['header']}" in done.stdout
    _silent(_said(done))


def test_placeholders_are_skipped_and_named_never_printed(fx: dict[str, Path]) -> None:
    """The REPLACE rule of the header file (0.1.11.3 item 14), on a letter: real fields print, placeholders do not."""
    partly = {"name": "Zora Quillfeather", "email": "REPLACE: your email", "phone": "", "location": "Quillshire, ZZ"}
    done = _pdf(fx, "--out", str(fx["out"]), "--header", str(_header(fx["header"], partly)))
    assert done.exit_code == 0, _said(done)
    payload = json.loads(done.stdout)
    assert payload["header"] is True and payload["header_note"].endswith("Skipped: email.")
    text = _pages(fx["out"])[0]
    assert "REPLACE" not in text and _squeezed(fx["out"]).startswith(_squeeze("ZORA QUILLFEATHER", "Quillshire, ZZ", GREETING))
    _silent(_said(done))


@pytest.mark.parametrize(
    ("content", "code", "says"),
    [
        (None, "header_file_missing", "There is no header file at"),
        ('{"name": "Zora Quillfeather", ', "header_file_invalid", "is not valid JSON"),
        ({"name": "Zora Quillfeather", "phone": ["555-0142-ZQ"]}, "header_file_invalid", "phone must be text in double quotes"),
        ({"email": "zora.q@example.invalid"}, "header_file_invalid", "has no name yet; a PDF header needs one"),
        ({"name": "REPLACE: your name", "email": "REPLACE: your email"}, "header_file_placeholders", "still has placeholder values"),
        ({"name": "REPLACE: your name", "email": "zora.q@example.invalid"}, "header_file_invalid", "has no name yet; a PDF header needs one"),
    ],
)
def test_a_missing_or_unusable_header_file_is_one_plain_sentence_and_no_pdf(fx: dict[str, Path], content: object, code: str, says: str) -> None:
    if content is not None:
        _header(fx["header"], content)
    for as_json in (True, False):
        failed = _pdf(fx, "--out", str(fx["out"]), "--header", str(fx["header"]), as_json=as_json)
        assert failed.exit_code == 1, _said(failed)
        assert says in _said(failed) and str(fx["header"]) in _said(failed) and "Traceback" not in _said(failed)
        assert failed.exception is None or isinstance(failed.exception, SystemExit), repr(failed.exception)
        _silent(_said(failed))
        if as_json:
            assert json.loads(failed.stdout)["error"]["code"] == code
    assert not fx["out"].exists(), "no PDF is written without the header that was asked for"


def test_the_refusals_are_the_resume_pdf_commands_own(fx: dict[str, Path]) -> None:
    """One reading of the header file for both commands: the same code and the same sentence for the same broken file."""
    resume = fx["work"] / "resume.md"
    resume.write_text("## Summary\n\n- Platform engineer with nine years building billing systems.\n", encoding="utf-8")
    for content in ('{"name": ', {"email": "zora.q@example.invalid"}, {"name": "REPLACE: your name"}):
        _header(fx["header"], content)
        letter = _pdf(fx, "--out", str(fx["out"]), "--header", str(fx["header"]))
        other = CliRunner().invoke(cli, ["scout", "resume", "pdf", "--in", str(resume), "--home", str(fx["home"]), "--out", str(fx["work"] / "r.pdf"), "--header", str(fx["header"]), "--json"])
        assert letter.exit_code == other.exit_code == 1
        assert json.loads(letter.stdout)["error"] == json.loads(other.stdout)["error"]


def test_a_broken_default_file_stops_the_pdf_and_names_no_header(fx: dict[str, Path]) -> None:
    _header(fx["home"] / "header.json", '{"name": "Zora Quillfeather"')
    failed = _pdf(fx, "--out", str(fx["out"]))
    assert failed.exit_code == 1 and json.loads(failed.stdout)["error"]["code"] == "header_file_invalid"
    assert "(--no-header makes the PDF without a header)" in failed.stdout and not fx["out"].exists()
    _silent(_said(failed).replace(str(fx["home"]), ""))
    assert _pdf(fx, "--out", str(fx["out"]), "--no-header").exit_code == 0


def test_header_and_no_header_do_not_go_together_and_out_is_required(fx: dict[str, Path]) -> None:
    _header(fx["header"])
    both = _pdf(fx, "--out", str(fx["out"]), "--header", str(fx["header"]), "--no-header")
    assert both.exit_code == 1 and "do not go together" in both.stdout and not fx["out"].exists()
    no_out = _pdf(fx, "--header", str(fx["header"]))
    assert no_out.exit_code == 2 and "--out" in no_out.stderr, "a letter PDF always names where it goes"
    _silent(_said(both) + _said(no_out))


def test_the_work_authorization_line_comes_from_the_header_file_only(fx: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """A letter's header takes the work authorization line from header.json ONLY: the profile's sponsorship answer never appears in a letter (sponsorship is a label; the skill's rule)."""
    from gigai.scout.find_jobs import resume_input

    (fx["home"] / "scout").mkdir()
    monkeypatch.setattr(resume_input, "read_config_preferences", lambda _path: (True, ()))
    no_key = {key: value for key, value in FILE.items() if key not in ("work_authorization", "links")}

    def contact_line(content: dict[str, object]) -> str:
        done = _pdf(fx, "--out", str(fx["out"]), "--header", str(_header(fx["header"], content)))
        assert done.exit_code == 0, _said(done)
        return _squeeze(_pages(fx["out"])[0].split("\n")[1])

    plain = _squeeze("Quillshire, ZZ | zora.q@example.invalid | 555-0142-ZQ")
    assert contact_line({**no_key, "work_authorization": "VISA: H1B (ZQ-7731)"}) == _squeeze("Quillshire, ZZ | VISA: H1B (ZQ-7731) | zora.q@example.invalid | 555-0142-ZQ")
    assert contact_line(no_key) == plain, "no key in the file: no line, whatever the profile's sponsorship answer says"
    assert contact_line({**no_key, "work_authorization": ""}) == plain, 'an empty key is "no line"'


# --- one page ----------------------------------------------------------------------------------------------------------


def _letter_of(paragraphs: int) -> str:
    return "\n\n".join((GREETING, *((FILLER * 2).strip() for _ in range(paragraphs)), SIGN_OFF)) + "\n"


def test_a_letter_a_little_too_long_is_set_tighter_and_stays_on_one_page(fx: dict[str, Path]) -> None:
    """Shrink before a second page: the spacing goes down, to the floor at most; the type size never changes."""
    _header(fx["header"])
    seen: dict[int, dict] = {}
    for paragraphs in range(8, 20):
        fx["letter"].write_text(_letter_of(paragraphs), encoding="utf-8")
        done = _pdf(fx, "--out", str(fx["out"]), "--header", str(fx["header"]))
        assert done.exit_code == 0, _said(done)
        seen[paragraphs] = json.loads(done.stdout)
        assert len(_pages(fx["out"])) == seen[paragraphs]["pages"], "the reported page count is the PDF's"
    tightened = [item for item in seen.values() if item["pages"] == 1 and item["spacing_scale"] < cover_letter.LETTER_SCALE]
    assert tightened, {key: (item["pages"], item["spacing_scale"]) for key, item in seen.items()}
    assert all(cover_letter.LETTER_FLOOR <= item["spacing_scale"] and item["note"] is None and item["one_page"] for item in tightened)
    # Longer never fits better: once a letter needs two pages, every longer one does.
    counts = [seen[paragraphs]["pages"] for paragraphs in sorted(seen)]
    assert counts == sorted(counts) and counts[0] == 1 and counts[-1] == 2, counts


def test_a_letter_that_is_too_long_says_so_in_one_sentence_with_its_page_count(fx: dict[str, Path]) -> None:
    fx["letter"].write_text(LONG, encoding="utf-8")
    done = _pdf(fx, "--out", str(fx["out"]), "--header", str(_header(fx["header"])))
    assert done.exit_code == 0, _said(done)
    payload = json.loads(done.stdout)
    assert payload["pages"] == len(_pages(fx["out"])) == 2 and payload["one_page"] is False
    assert payload["spacing_scale"] == cover_letter.LETTER_FLOOR, "the tightest readable spacing was tried first"
    assert payload["note"] == "This letter takes 2 pages even with the tightest spacing; a cover letter is one page. Shorten it (about 330-380 words fit), then make the PDF again."
    text = _pdf(fx, "--out", str(fx["out"]), "--header", str(fx["header"]), as_json=False)
    assert text.exit_code == 0 and f"(2 pages, {len(LONG.split())} words, spacing 0.7)" in text.stdout and payload["note"] in text.stdout
    _silent(_said(done) + _said(text))


# --- where the letter may go, and what may be given as the letter -------------------------------------------------


def test_the_letter_is_never_written_to_the_resumes_folder(fx: dict[str, Path]) -> None:
    folder = fx["home"] / "resumes"
    for out in (folder / "letter.pdf", folder / "letters" / "letter.pdf"):
        for as_json in (True, False):
            refused = _pdf(fx, "--out", str(out), "--no-header", as_json=as_json)
            assert refused.exit_code == 1, _said(refused)
            assert "a cover letter is never written to the resumes folder" in _said(refused)
            if as_json:
                assert json.loads(refused.stdout)["error"]["code"] == "letter_not_in_resumes_folder"
    assert not folder.exists(), "nothing was created there"
    # A folder the user chose as the resumes folder is the resumes folder.
    chosen = fx["work"] / "my-resumes"
    set_folder = CliRunner().invoke(cli, ["scout", "resume", "folder", "--set", str(chosen), "--home", str(fx["home"]), "--json"])
    assert set_folder.exit_code == 0, set_folder.output
    refused = _pdf(fx, "--out", str(chosen / "letter.pdf"), "--no-header")
    assert refused.exit_code == 1 and json.loads(refused.stdout)["error"]["code"] == "letter_not_in_resumes_folder"
    assert not list(chosen.rglob("*.pdf"))


def test_the_letters_pdf_is_never_written_into_the_jobs_folder(fx: dict[str, Path]) -> None:
    """0.1.11.4 J4: the letter's markdown lives in the job's folder; its PDF carries the header's contact details, so it does not."""

    jobs = fx["home"] / "jobs"
    job = jobs / "acme" / "staff-software-engineer"
    job.mkdir(parents=True)
    letter = job / "cover-letter.md"
    letter.write_text(LETTER, encoding="utf-8")
    _header(fx["header"])
    for out in (job / "cover-letter.pdf", jobs / "letter.pdf", jobs / "acme" / "new" / "letter.pdf"):
        for as_json in (True, False):
            refused = _pdf(fx, "--out", str(out), "--header", str(fx["header"]), as_json=as_json, letter=letter)
            assert refused.exit_code == 1, _said(refused)
            assert "a cover letter's PDF is never written into the jobs folder" in _said(refused) and "pass another --out FILE" in _said(refused)
            if as_json:
                assert json.loads(refused.stdout)["error"]["code"] == "letter_not_in_jobs_folder"
            _silent(_said(refused))
    assert sorted(path.relative_to(jobs).as_posix() for path in jobs.rglob("*") if path.is_file()) == ["acme/staff-software-engineer/cover-letter.md"]
    for marker in MARKERS:
        for path in jobs.rglob("*"):
            assert not path.is_file() or marker not in path.read_text(encoding="utf-8"), marker
    # A folder the user chose as the jobs folder is the jobs folder.
    chosen = fx["work"] / "my-jobs"
    set_folder = CliRunner().invoke(cli, ["scout", "jobs-folder", "--set", str(chosen), "--home", str(fx["home"]), "--json"])
    assert set_folder.exit_code == 0, set_folder.output
    refused = _pdf(fx, "--out", str(chosen / "acme" / "role" / "cover-letter.pdf"), "--no-header", letter=letter)
    assert refused.exit_code == 1 and json.loads(refused.stdout)["error"]["code"] == "letter_not_in_jobs_folder"
    assert not list(chosen.rglob("*.pdf"))
    # The letter in the job's folder is what --in reads; the PDF goes where the user says, outside it.
    done = _pdf(fx, "--out", str(fx["out"]), "--header", str(fx["header"]), letter=letter)
    assert done.exit_code == 0, _said(done)
    assert json.loads(done.stdout)["pages"] == 1 and fx["out"].is_file() and not list(jobs.rglob("*.pdf")) and not list(chosen.rglob("*.pdf"))


def test_a_claims_trace_is_never_printed_and_the_letter_is_never_overwritten(fx: dict[str, Path]) -> None:
    trace = fx["letter"].with_name(fx["letter"].name.replace(".md", ".claims.md"))
    refused = _pdf(fx, "--out", str(fx["out"]), "--no-header", letter=trace)
    assert refused.exit_code == 1 and json.loads(refused.stdout)["error"]["code"] == "invalid_value"
    assert "claims trace" in refused.stdout and "fernquist" not in _said(refused) and not fx["out"].exists()
    same = _pdf(fx, "--out", str(fx["letter"]), "--no-header")
    assert same.exit_code == 1 and "--out is the letter's own file" in same.stdout
    assert fx["letter"].read_text(encoding="utf-8") == LETTER


@pytest.mark.parametrize(
    ("content", "code", "says"),
    [
        ("", "letter_invalid", "the letter is empty"),
        ("<!-- only a comment -->\n\n---\n", "letter_invalid", "the letter is empty"),
        ("Dear team,\n\nA bell\x07 rang.\n", "letter_invalid", "line 3: control characters are not allowed"),
        ("word " * 20000, "letter_too_large", "larger than 65536 bytes"),
    ],
)
def test_a_letter_that_cannot_be_printed_is_refused_by_line_and_rule_never_by_its_text(fx: dict[str, Path], content: str, code: str, says: str) -> None:
    fx["letter"].write_text(content, encoding="utf-8")
    refused = _pdf(fx, "--out", str(fx["out"]), "--no-header")
    assert refused.exit_code == 1 and json.loads(refused.stdout)["error"]["code"] == code and says in refused.stdout
    assert "bell" not in refused.stdout and not fx["out"].exists()
    missing = _pdf(fx, "--out", str(fx["out"]), "--no-header", letter=fx["work"] / "no-such-letter.md")
    assert missing.exit_code == 1 and json.loads(missing.stdout)["error"]["code"] == "input_file_unreadable"


# --- the letter file's format (pure) ------------------------------------------------------------------------------


def test_what_the_letter_file_prints_and_what_it_never_prints() -> None:
    markdown = (
        "---\ncompany: Acme\nrole: Staff Engineer\n---\n"
        "# Cover letter\n\n"
        "Dear team, <!-- src: b-1a2b3c -->\n\n"
        "<!-- a note to myself\nover two lines -->\n"
        "A long first line of a paragraph that was wrapped by hand at about ninety characters wide,\nand its second line.\n\n"
        "What I bring:\n- Twelve years of backend systems\n  across two companies\n- An on-call rotation I set up\n\n"
        "***\n\n"
        "Thank you,\nPat Example\n"
    )
    assert cover_letter.parse_letter(markdown) == [
        {"lines": ["Cover letter"], "bullet": False},
        {"lines": ["Dear team,"], "bullet": False},
        {"lines": ["A long first line of a paragraph that was wrapped by hand at about ninety characters wide, and its second line."], "bullet": False},
        {"lines": ["What I bring:"], "bullet": False},
        {"lines": ["Twelve years of backend systems across two companies"], "bullet": True},
        {"lines": ["An on-call rotation I set up"], "bullet": True},
        {"lines": ["Thank you,", "Pat Example"], "bullet": False},
    ]
    # Text prints as written: markup characters are data to Typst, never markup.
    assert cover_letter.parse_letter("I use #let, *stars*, _underscores_ and $x$ as words.\n") == [
        {"lines": ["I use #let, *stars*, _underscores_ and $x$ as words."], "bullet": False},
    ]
    assert cover_letter.looks_like_claims_trace("acme-staff-2026-10-06.claims.md") and not cover_letter.looks_like_claims_trace("acme-staff-2026-10-06.md")


def test_markup_characters_in_a_letter_print_as_text(fx: dict[str, Path]) -> None:
    fx["letter"].write_text("I use #let x = 1, *stars*, _underscores_, <angles> and `ticks` as words.\n", encoding="utf-8")
    done = _pdf(fx, "--out", str(fx["out"]), "--no-header")
    assert done.exit_code == 0, _said(done)
    assert _squeezed(fx["out"]) == _squeeze("I use #let x = 1, *stars*, _underscores_, <angles> and `ticks` as words.")


# --- the template family -----------------------------------------------------------------------------------------------


def test_the_letter_template_is_the_resumes_family() -> None:
    """Same page, margins, font, colours, line-box model and compact header: each definition is the resume's own line."""
    root = resources.files("gigai.scout").joinpath("data", "resume")
    letter = root.joinpath("letter.typ").read_text(encoding="utf-8")
    resume = root.joinpath("resume.typ").read_text(encoding="utf-8").splitlines()
    shared = [
        line for line in letter.splitlines()
        if re.match(r"#let (ink|soft|margin-x|margin-y|asc|desc|edges|t|item|head-name|head-line|contact-sizes|contact-line)\b", line) or line.startswith("#set ")
    ]
    assert len(shared) == 18, shared
    for line in shared:
        assert line in resume, f"letter.typ differs from resume.typ: {line}"
    # The header block (the blank block, the name line, the one contact line) is the resume's, line for line.
    for line in letter[letter.index("#if d.at(\"blank_header\""):letter.index("// THE BODY")].splitlines():
        if line.strip() and not line.startswith("//"):
            assert line in resume, f"letter.typ's header differs from resume.typ: {line}"
    assert 'font: "Inter"' in letter and "<fit-end>" in letter
