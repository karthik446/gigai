"""0.1.11.3 item 13: the header file -- the user's own name and contact details for a PDF, read and never kept.

``pdf_header_file`` alone (no server, no CLI): where the file is, what it may hold, how its values become the
Generate PDF form's values, and that every answer about a missing, unreadable, invalid or widely readable file
is one plain sentence that never repeats a value of the file.  Synthetic values only, tmp folders only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gigai.scout import pdf_header_file
from gigai.scout.pdf_header_file import (
    SPONSORSHIP_DEFAULT, STATE_FILLED, STATE_INVALID, STATE_MISSING, default_path, prefill_response, read_header_file, with_sponsorship_default,
)
from gigai.scout.resume_display import HEADER_FIELDS, MAX_VALUE, ContactItem, HeaderFormError, form_header, parse_header_form
from gigai.scout.resumes_folder import default_folder

FILE = {
    "name": "Zora Quillfeather",
    "email": "zora.q@example.invalid",
    "phone": "555-0142-ZQ",
    "location": "Quillshire, ZZ",
    "links": [
        {"label": "GitHub", "url": "https://github.com/zq-invalid-7731"},
        {"label": "LinkedIn", "url": "linkedin.com/in/zq-invalid-7731"},
        {"label": "", "url": "zq-invalid-7731.example.invalid"},
    ],
    "work_authorization": "VISA: H1B (ZQ-7731)",
}
MARKERS = ("Zora", "Quillfeather", "zora.q@", "555-0142-ZQ", "Quillshire", "zq-invalid-7731", "ZQ-7731")


def _write(path: Path, content: object, mode: int = 0o600) -> Path:
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    os.chmod(path, mode)
    return path


def _silent(*texts: object) -> None:
    for text in texts:
        for marker in MARKERS:
            assert marker not in str(text), f"{marker!r} is in {text!r}"


def test_the_default_file_is_beside_the_resumes_folder_never_inside_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    assert default_path(tmp_path / "user" / ".gigai") == tmp_path / "user" / "Documents" / "GigAI" / "header.json"
    assert default_path(tmp_path / "scratch-home") == tmp_path / "scratch-home" / "header.json"
    for home in (tmp_path / "user" / ".gigai", tmp_path / "scratch-home"):
        folder = default_folder(home)
        assert default_path(home).parent == folder.parent and not default_path(home).is_relative_to(folder)


def test_a_file_fills_the_forms_values_and_its_links(tmp_path: Path) -> None:
    found = read_header_file(_write(tmp_path / "header.json", FILE))
    assert found.state == STATE_FILLED and found.filled and found.warning is None and found.has_work_authorization
    assert found.message == f"Filled from {found.shown}"
    assert found.values == {
        "name": "Zora Quillfeather", "email": "zora.q@example.invalid", "phone": "555-0142-ZQ", "location": "Quillshire, ZZ",
        # the link labelled LinkedIn fills the form's own LinkedIn field; the others are rows under their labels
        "linkedin": "linkedin.com/in/zq-invalid-7731", "link": "", "work_authorization": "VISA: H1B (ZQ-7731)",
        "links": [{"label": "GitHub", "url": "https://github.com/zq-invalid-7731"}, {"label": "Link", "url": "zq-invalid-7731.example.invalid"}],
    }
    assert set(found.values) == {*HEADER_FIELDS, "links"}
    # The form's own parser takes them, and the header prints every link's address in the contact line.
    header = form_header(parse_header_form(found.values), "Staff Engineer")
    # 0.1.11.3 items 15/16: ONE contact line: location, work authorization, the links, email, phone.
    assert header.name == "Zora Quillfeather" and header.title == "Staff Engineer" and header.work_authorization == ""
    assert header.contact == (
        ContactItem("Quillshire, ZZ", None), ContactItem("VISA: H1B (ZQ-7731)", None),
        ContactItem("github.com/zq-invalid-7731", "https://github.com/zq-invalid-7731"),
        ContactItem("zq-invalid-7731.example.invalid", "https://zq-invalid-7731.example.invalid"),
        ContactItem("linkedin.com/in/zq-invalid-7731", "https://linkedin.com/in/zq-invalid-7731"),
        ContactItem("zora.q@example.invalid", "mailto:zora.q@example.invalid"), ContactItem("555-0142-ZQ", None),
    )
    # A link to linkedin.com is the LinkedIn field whatever its label; a second one is a row.
    two = read_header_file(_write(tmp_path / "two.json", {"links": [{"label": "Profile", "url": "https://www.linkedin.com/in/a"}, {"label": "LinkedIn", "url": "x.invalid/b"}]}))
    assert two.values["linkedin"] == "https://www.linkedin.com/in/a" and two.values["links"] == [{"label": "LinkedIn", "url": "x.invalid/b"}]


def test_every_field_is_optional_and_an_empty_object_fills_nothing(tmp_path: Path) -> None:
    found = read_header_file(_write(tmp_path / "header.json", {}))
    assert found.filled and found.values == {**{key: "" for key in HEADER_FIELDS}, "links": []} and not found.has_work_authorization


def test_the_values_are_in_no_repr_and_the_reader_writes_and_logs_nothing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = _write(tmp_path / "header.json", FILE)
    before = {item.name: (item.stat().st_mtime_ns, item.read_bytes()) for item in tmp_path.iterdir()}
    with caplog.at_level(0):
        found = read_header_file(path)
    _silent(repr(found), str(found), found.message, caplog.text)
    assert {item.name: (item.stat().st_mtime_ns, item.read_bytes()) for item in tmp_path.iterdir()} == before, "the file is only read"
    assert caplog.records == []
    source = Path(pdf_header_file.__file__).read_text(encoding="utf-8")
    for banned in ("import logging", "getLogger", "atomic_write", "write_text", "write_bytes", "open(", "print("):
        assert banned not in source, f"pdf_header_file must not {banned}"


def test_a_missing_file_is_a_plain_sentence_that_says_where_it_goes(tmp_path: Path) -> None:
    found = read_header_file(tmp_path / "header.json")
    assert found.state == STATE_MISSING and found.values is None and found.warning is None
    assert found.message.startswith(f"There is no header file at {found.shown}. ") and "keep your details in" in found.message
    assert '"work_authorization"' in found.message, "the sentence shows the file's shape"
    assert prefill_response(found)["values"] is None and prefill_response(found)["state"] == "missing"


@pytest.mark.parametrize(
    ("content", "says"),
    [
        ('{"name": "Zora Quillfeather", "email": ', "is not valid JSON"),
        ("Zora Quillfeather <zora.q@example.invalid>", "is not valid JSON"),
        ('["Zora Quillfeather"]', "must hold one JSON object"),
        ({"name": ["Zora Quillfeather"]}, "name must be text in double quotes"),
        ({"name": "Zora Quillfeather\nzora.q@example.invalid"}, "name must be one line"),
        ({"phone": "555-0142-ZQ" * 30}, f"phone is longer than {MAX_VALUE} characters"),
        ({"name": "Zora Quillfeather", "nickname": "Zora"}, "nickname is not a field of this file"),
        ({"zora.q@example.invalid": "x"}, "a field is not a field of this file"),
        ({"links": {"label": "GitHub", "url": "github.com/zq-invalid-7731"}}, "links must be a list"),
        ({"links": ["github.com/zq-invalid-7731"]}, "links[1] must be an object"),
        ({"links": [{"label": "GitHub", "url": "x.invalid", "note": "Zora"}]}, "links[1].note is not a field"),
        ({"links": [{"label": "L", "url": f"x.invalid/{n}"} for n in range(7)]}, "links holds more than 6 links"),
        ({"work_authorization": 7}, "work_authorization must be text"),
    ],
)
def test_an_invalid_file_says_what_is_wrong_and_never_a_value(tmp_path: Path, content: object, says: str) -> None:
    found = read_header_file(_write(tmp_path / "header.json", content))
    assert found.state == STATE_INVALID and found.values is None, found
    assert says in found.message and found.shown in found.message, found.message
    _silent(found.message, repr(found), json.dumps(prefill_response(found)))


def test_a_file_that_cannot_be_read_a_folder_and_a_huge_file_are_plain_sentences(tmp_path: Path) -> None:
    locked = _write(tmp_path / "locked.json", FILE, mode=0o000)
    try:
        found = read_header_file(locked)
        if os.geteuid() != 0:  # root reads anything
            assert found.state == STATE_INVALID and "could not be read" in found.message
            _silent(found.message)
    finally:
        os.chmod(locked, 0o600)
    folder = tmp_path / "header.json"
    folder.mkdir()
    assert read_header_file(folder).state == STATE_INVALID and "is not a file" in read_header_file(folder).message
    huge = read_header_file(_write(tmp_path / "huge.json", json.dumps({"name": "Zora Quillfeather " * 2000})))
    assert huge.state == STATE_INVALID and "so it was not read" in huge.message
    _silent(huge.message)


@pytest.mark.parametrize(("mode", "warned"), [(0o600, False), (0o400, False), (0o640, True), (0o604, True), (0o644, True), (0o666, True)])
def test_a_file_other_users_can_read_is_used_with_a_plain_warning(tmp_path: Path, mode: int, warned: bool) -> None:
    found = read_header_file(_write(tmp_path / "header.json", FILE, mode=mode))
    assert found.filled, "the warning never stops the PDF"
    if warned:
        assert found.warning == f"{found.shown} can be read by other users of this computer. To keep it to yourself: chmod 600 {found.shown}"
        _silent(found.warning)
    else:
        assert found.warning is None


def test_the_work_authorization_line_file_over_the_profiles_sponsorship_answer(tmp_path: Path) -> None:
    """Precedence below the form: the file's line; the profile's answer only when the file has no such key at all."""
    with_line = read_header_file(_write(tmp_path / "a.json", FILE))
    empty_line = read_header_file(_write(tmp_path / "b.json", {**FILE, "work_authorization": ""}))
    no_key = read_header_file(_write(tmp_path / "c.json", {key: value for key, value in FILE.items() if key != "work_authorization"}))
    assert SPONSORSHIP_DEFAULT == "Requires visa sponsorship"

    def line(found, visa: bool) -> object:
        return with_sponsorship_default(found.values, has_work_authorization=found.has_work_authorization, visa_required=visa)["work_authorization"]

    assert [line(with_line, True), line(with_line, False)] == ["VISA: H1B (ZQ-7731)"] * 2, "the file's line wins over the profile"
    assert [line(empty_line, True), line(empty_line, False)] == ["", ""], 'an empty key means "no line", whatever the profile says'
    assert [line(no_key, True), line(no_key, False)] == [SPONSORSHIP_DEFAULT, ""], "no key: the profile's answer"
    assert no_key.values["work_authorization"] == "", "the default is added to a copy, never to what was read"


def test_the_forms_parser_takes_links_and_refuses_bad_ones_without_echoing_them() -> None:
    form = {"name": "Zora Quillfeather"}
    assert "links" not in parse_header_form(form), "left out: the form is what it was"
    assert "links" not in parse_header_form({**form, "links": [{"label": "GitHub", "url": "  "}]}), "a row left empty is dropped"
    kept = parse_header_form({**form, "links": [{"label": " GitHub ", "url": " github.com/zq-invalid-7731 "}]})
    assert kept["links"] == [{"label": "GitHub", "url": "github.com/zq-invalid-7731"}]
    for bad, code in (
        ({"label": "GitHub", "url": "github.com/zq-invalid-7731"}, "wrong_type"),
        (["github.com/zq-invalid-7731"], "wrong_type"),
        ([{"label": "GitHub", "url": 7}], "wrong_type"),
        ([{"label": "GitHub", "url": "github.com/zq-invalid-7731", "zq-invalid-7731": 1}], "unknown_key"),
        ([{"label": "GitHub", "url": "github.com/zq-invalid-7731\nx"}], "invalid_value"),
        ([{"label": "GitHub", "url": "github.com/zq-invalid-7731" * 20}], "invalid_value"),
        ([{"label": "L", "url": "x.invalid"}] * 7, "invalid_value"),
    ):
        with pytest.raises(HeaderFormError) as refused:
            parse_header_form({**form, "links": bad})
        assert refused.value.code == code and "header.links" in str(refused.value)
        _silent(str(refused.value))
