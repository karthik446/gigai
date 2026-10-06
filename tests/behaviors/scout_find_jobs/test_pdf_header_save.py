"""0.1.11.3 item 14: the Save button's writer (``pdf_header_save``) and the ``REPLACE`` placeholder rules, without a server.

THE WRITER: one write of the form's details to the user's own ``header.json``: exactly the typed fields, mode 0600,
atomic (a failure mid-write leaves the file that was there), never over an existing file without ``replace``, a
plain sentence when the folder is missing or cannot be written, and no value in any answer, error or log.

PLACEHOLDERS: a value of the file that is empty or starts with ``REPLACE`` is skipped: it never fills the form and
never prints in a PDF.  The answer names the skipped fields (names only).  A missing or placeholder name is flagged
("has no name yet") and ``gigai scout resume pdf`` refuses with a plain sentence.

Synthetic values and tmp folders only.
"""

from __future__ import annotations

import io
import json
import os
import stat
from pathlib import Path

import pytest
from click.testing import CliRunner
from pypdf import PdfReader

from gigai.cli import cli
from gigai.scout import pdf_header_save
from gigai.scout.pdf_header_file import MAX_BYTES, MAX_VALUE, STATE_FILLED, STATE_PLACEHOLDER, prefill_response, read_header_file, render_form
from gigai.scout.pdf_header_file import HeaderFileError
from gigai.scout.pdf_header_save import STATE_EXISTS, STATE_NOT_WRITABLE, STATE_SAVED, HeaderSaveError, file_content, save_header_file

from tests.behaviors.scout_find_jobs.test_pdf_header_file import MARKERS

#: What the form sends: the header file's own shape.
TYPED = {
    "name": "Zora Quillfeather",
    "email": "zora.q@example.invalid",
    "phone": "555-0142-ZQ",
    "location": "Quillshire, ZZ",
    "links": [{"label": "LinkedIn", "url": "linkedin.com/in/zq-invalid-7731"}, {"label": "GitHub", "url": "https://github.com/zq-invalid-7731"}],
    "work_authorization": "VISA: H1B (ZQ-7731)",
}
OLD = {"name": "Riley Formerfile", "email": "riley.old@example.invalid"}
MARKDOWN = "## Summary\n\n- Platform engineer with nine years building billing systems.\n\n## Skills\n\n- Python, SQL\n"


def _silent(*texts: object) -> None:
    for text in texts:
        for marker in MARKERS:
            assert marker not in str(text), f"{marker!r} is in {text!r}"


def _names(folder: Path) -> list[str]:
    return sorted(item.name for item in folder.iterdir())


# ---------------------------------------------------------------------------------------------------- the writer


def test_a_save_writes_exactly_the_typed_fields_mode_0600_and_nothing_else(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "header.json"
    previous = os.umask(0)  # the loosest umask: the mode is the writer's, not the shell's
    try:
        with caplog.at_level(0):
            saved = save_header_file(path, TYPED)
    finally:
        os.umask(previous)
    assert saved.state == STATE_SAVED and saved.path == path
    assert saved.message == f"Saved your details to {saved.shown}. GigAI keeps no other copy." and saved.to_json() == {
        "schema_version": "scout-pdf-header-save:1", "state": "saved", "shown": saved.shown, "message": saved.message,
    }
    assert json.loads(path.read_text(encoding="utf-8")) == TYPED, "exactly the fields typed"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert _names(tmp_path) == ["header.json"], "no temporary file, no copy"
    assert path.stat().st_nlink == 1, "the temporary name is gone"
    _silent(repr(saved), saved.message, caplog.text)
    assert caplog.records == []
    # The reader takes what the writer wrote.
    found = read_header_file(path)
    assert found.state == STATE_FILLED and found.warning is None and found.placeholders == () and found.name_note is None
    assert found.values["name"] == "Zora Quillfeather" and found.values["linkedin"] == "linkedin.com/in/zq-invalid-7731"


def test_values_are_trimmed_and_an_empty_link_is_left_out(tmp_path: Path) -> None:
    saved = save_header_file(tmp_path / "header.json", {"name": "  Zora Quillfeather ", "links": [{"label": " Site ", "url": " zq-invalid-7731.example.invalid "}, {"label": "GitHub", "url": " "}]})
    assert saved.state == STATE_SAVED
    assert json.loads((tmp_path / "header.json").read_text(encoding="utf-8")) == {
        "name": "Zora Quillfeather", "email": "", "phone": "", "location": "",
        "links": [{"label": "Site", "url": "zq-invalid-7731.example.invalid"}], "work_authorization": "",
    }


def test_an_existing_file_is_not_replaced_without_a_yes(tmp_path: Path) -> None:
    path = tmp_path / "header.json"
    path.write_text(json.dumps(OLD), encoding="utf-8")
    os.chmod(path, 0o644)
    before = path.read_bytes()

    asked = save_header_file(path, TYPED)
    assert asked.state == STATE_EXISTS and asked.message == f"There is already a file at {asked.shown}. It was not changed."
    assert path.read_bytes() == before and _names(tmp_path) == ["header.json"], "the answer 'exists' changes nothing (cancel: the file is as it was)"
    _silent(asked.message)

    replaced = save_header_file(path, TYPED, replace=True)
    assert replaced.state == STATE_SAVED
    assert json.loads(path.read_text(encoding="utf-8")) == TYPED and "Formerfile" not in path.read_text(encoding="utf-8")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600, "a replaced file is the new 0600 file, whatever the old mode was"
    assert _names(tmp_path) == ["header.json"]


def test_a_file_that_appears_after_the_check_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ``replace`` the new file is put in place with a hard link, which cannot overwrite."""
    path = tmp_path / "header.json"
    real_link = os.link

    def racing(source, target, *args, **kwargs):
        Path(target).write_text(json.dumps(OLD), encoding="utf-8")  # another writer got there first
        return real_link(source, target, *args, **kwargs)

    monkeypatch.setattr(pdf_header_save.os, "link", racing)
    asked = save_header_file(path, TYPED)
    assert asked.state == STATE_EXISTS and json.loads(path.read_text(encoding="utf-8")) == OLD and _names(tmp_path) == ["header.json"]


@pytest.mark.parametrize("step", ["write", "fsync", "replace"])
def test_a_failure_mid_write_leaves_the_old_file_intact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    path = tmp_path / "header.json"
    path.write_text(json.dumps(OLD), encoding="utf-8")
    before = path.read_bytes()

    def broken(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    if step == "write":
        real_fdopen = os.fdopen

        class Failing:
            def __init__(self, stream):
                self._stream = stream

            def __enter__(self):
                self._stream.__enter__()
                return self

            def __exit__(self, *exc):
                return self._stream.__exit__(*exc)

            def fileno(self):
                return self._stream.fileno()

            def write(self, data):
                self._stream.write(data[: len(data) // 2])  # half of it reaches the temporary file
                self._stream.flush()
                raise OSError(28, "No space left on device")

        monkeypatch.setattr(pdf_header_save.os, "fdopen", lambda *args, **kwargs: Failing(real_fdopen(*args, **kwargs)))
    else:
        monkeypatch.setattr(pdf_header_save.os, step, broken)

    failed = save_header_file(path, TYPED, replace=True)
    assert failed.state == STATE_NOT_WRITABLE and failed.message.startswith("Your details were not saved: GigAI cannot write to ")
    assert "No space left" not in failed.message, "a plain sentence, not the OS error"
    assert path.read_bytes() == before, "the file that was there is intact"
    assert _names(tmp_path) == ["header.json"], "the half-written temporary file is removed"
    _silent(failed.message)


def test_a_crash_that_is_not_an_os_error_also_leaves_the_old_file_and_no_temporary_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "header.json"
    path.write_text(json.dumps(OLD), encoding="utf-8")

    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(pdf_header_save.os, "fsync", interrupted)
    with pytest.raises(KeyboardInterrupt):
        save_header_file(path, TYPED, replace=True)
    assert json.loads(path.read_text(encoding="utf-8")) == OLD and _names(tmp_path) == ["header.json"]


def test_a_folder_that_is_missing_or_not_writable_is_a_plain_sentence(tmp_path: Path) -> None:
    missing = save_header_file(tmp_path / "nowhere" / "header.json", TYPED)
    assert missing.state == STATE_NOT_WRITABLE and missing.message.startswith("Your details were not saved: there is no folder ") and missing.message.endswith("nowhere. Create it and save again.")
    assert not (tmp_path / "nowhere").exists(), "a folder is not created unless it is the default location"

    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)
    try:
        if os.access(locked, os.W_OK):
            pytest.skip("LOUD: this user can write to a 0500 folder (root?); the not-writable sentence was NOT checked")
        refused = save_header_file(locked / "header.json", TYPED)
        assert refused.state == STATE_NOT_WRITABLE
        assert refused.message.startswith("Your details were not saved: GigAI cannot write to ") and refused.message.endswith("locked. Check that the folder is there and that you may write to it.")
        assert _names(locked) == []
    finally:
        os.chmod(locked, 0o700)

    folder = tmp_path / "header.json"
    folder.mkdir()
    assert save_header_file(folder, TYPED, replace=True).state == STATE_NOT_WRITABLE, "a folder with the file's name is not removed"
    _silent(missing.message, refused.message)


def test_the_default_folder_is_created_0700_and_only_that_one(tmp_path: Path) -> None:
    documents = tmp_path / "Documents"
    documents.mkdir()
    saved = save_header_file(documents / "GigAI" / "header.json", TYPED, create_folder=True)
    assert saved.state == STATE_SAVED and stat.S_IMODE((documents / "GigAI").stat().st_mode) == 0o700
    assert _names(documents / "GigAI") == ["header.json"]
    # Its parent is never created: no ~/Documents, no file.
    deeper = save_header_file(tmp_path / "NoDocuments" / "GigAI" / "header.json", TYPED, create_folder=True)
    assert deeper.state == STATE_NOT_WRITABLE and not (tmp_path / "NoDocuments").exists()


@pytest.mark.parametrize(
    ("details", "says"),
    [
        (["Zora Quillfeather"], "must hold one JSON object"),
        ({"name": ["Zora Quillfeather"]}, "name must be text in double quotes"),
        ({"name": "Zora Quillfeather\nzora.q@example.invalid"}, "name must be one line"),
        ({"phone": "555-0142-ZQ" * 30}, f"phone is longer than {MAX_VALUE} characters"),
        ({"name": "Zora Quillfeather", "nickname": "Zora"}, "nickname is not a field of this file"),
        ({"zora.q@example.invalid": "x"}, "a field is not a field of this file"),
        ({"name": "Zora Quillfeather", "linkedin": "linkedin.com/in/zq-invalid-7731"}, "linkedin is not a field of this file"),
        ({"links": [{"label": "L", "url": f"zq-invalid-7731.example.invalid/{n}"} for n in range(7)]}, "links holds more than 6 links"),
        ({"links": [{"label": "GitHub", "url": "zq-invalid-7731.example.invalid", "note": "Zora"}]}, "links[1].note is not a field"),
        ({"name": "REPLACE: your name", "email": "zora.q@example.invalid"}, "a value still starts with REPLACE"),
        ({}, "there is nothing to save"),
        ({"name": "  ", "links": [{"label": "GitHub", "url": ""}]}, "there is nothing to save"),
    ],
)
def test_details_the_reader_would_not_take_are_refused_and_never_echoed(tmp_path: Path, details: object, says: str) -> None:
    path = tmp_path / "header.json"
    with pytest.raises(HeaderSaveError) as refused:
        save_header_file(path, details)
    assert says in str(refused.value)
    _silent(str(refused.value), repr(refused.value))
    assert refused.value.__cause__ is None and (refused.value.__context__ is None or refused.value.__suppress_context__), "no chained error that could carry the details"
    assert not path.exists() and _names(tmp_path) == []


def test_the_largest_details_the_rules_allow_fit_the_readers_size_limit() -> None:
    wide = "\U0001f600" * MAX_VALUE  # 4 bytes a character
    data = file_content({key: wide for key in ("name", "email", "phone", "location", "work_authorization")} | {"links": [{"label": wide, "url": wide}] * 6})
    assert len(data) <= MAX_BYTES, "what the writer accepts, the reader reads"


# ---------------------------------------------------------------------------------------------------- placeholders


def _file(tmp_path: Path, content: object) -> Path:
    path = tmp_path / "header.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


TEMPLATE = {
    "name": "REPLACE: your full name",
    "email": "REPLACE: you@example.com",
    "phone": "REPLACE: your phone",
    "location": "REPLACE: City, State",
    "links": [{"label": "LinkedIn", "url": "REPLACE: linkedin.com/in/you"}],
    "work_authorization": "REPLACE: e.g. H-1B, or delete this line",
}
ALL = "name, email, phone, location, links, work_authorization"


def _no_placeholder(*texts: object) -> None:
    for text in texts:
        squeezed = "".join(str(text).split())
        for value in ("yourfullname", "you@example.com", "yourphone", "City,State", "linkedin.com/in/you", "deletethisline"):
            assert value not in squeezed, f"a placeholder's text ({value}) is in {text!r}"


def test_a_file_that_is_all_placeholders_fills_nothing_and_says_so(tmp_path: Path) -> None:
    found = read_header_file(_file(tmp_path, TEMPLATE))
    assert found.state == STATE_PLACEHOLDER and not found.filled and found.values is None
    assert found.message == f"{found.shown} still has placeholder values: replace the REPLACE: fields (or save your details here)."
    assert "header.json still has placeholder values: replace the REPLACE: fields (or save your details here)" in found.message
    assert found.notice == found.message and found.placeholders == tuple(ALL.split(", ")) and found.name_note == f"{found.shown} has no name yet."
    answer = prefill_response(found)
    assert answer["state"] == "placeholder" and answer["values"] is None and answer["placeholders"] == ALL.split(", ") and answer["has_work_authorization"] is False
    _no_placeholder(repr(found), json.dumps(answer))
    with pytest.raises(HeaderFileError, match="has no name yet; a PDF header needs one"):
        render_form(found, visa_required=False)


def test_a_mixed_file_fills_the_real_fields_and_names_the_skipped_ones(tmp_path: Path) -> None:
    content = {
        **TEMPLATE, "name": "Zora Quillfeather", "location": "Quillshire, ZZ",
        "links": [
            {"label": "LinkedIn", "url": "REPLACE: linkedin.com/in/you"},  # a placeholder url
            {"label": "REPLACE: label", "url": "zq-invalid-7731.example.invalid/skipped"},  # a placeholder label
            {"label": "GitHub", "url": "github.com/zq-invalid-7731"},
        ],
    }
    found = read_header_file(_file(tmp_path, content))
    assert found.state == STATE_FILLED and found.message == f"Filled from {found.shown}" and found.name_note is None
    assert found.values == {
        "name": "Zora Quillfeather", "email": "", "phone": "", "location": "Quillshire, ZZ", "linkedin": "", "link": "", "work_authorization": "",
        "links": [{"label": "GitHub", "url": "github.com/zq-invalid-7731"}],
    }
    assert found.placeholders == ("email", "phone", "links", "work_authorization"), "names only, in the file's order"
    assert found.notice == f"{found.shown} still has placeholder values: replace the REPLACE: fields (or save your details here). Skipped: email, phone, links, work_authorization."
    # A placeholder work_authorization is a key that is not there: the profile's sponsorship answer fills the line.
    assert found.has_work_authorization is False
    assert render_form(found, visa_required=True)["work_authorization"] == "Requires visa sponsorship"
    assert render_form(found, visa_required=False)["work_authorization"] == ""
    _no_placeholder(repr(found), json.dumps(prefill_response(found)))
    assert "REPLACE" not in json.dumps(prefill_response(found)["values"])


def test_empty_strings_are_skipped_without_a_word(tmp_path: Path) -> None:
    found = read_header_file(_file(tmp_path, {"name": "Zora Quillfeather", "email": "", "phone": "  ", "location": "", "links": [{"label": "GitHub", "url": ""}, {"label": "", "url": " "}], "work_authorization": ""}))
    assert found.state == STATE_FILLED and found.placeholders == () and found.notice is None and found.name_note is None
    assert found.values == {"name": "Zora Quillfeather", "email": "", "phone": "", "location": "", "linkedin": "", "link": "", "work_authorization": "", "links": []}
    assert found.has_work_authorization is True, "an empty work_authorization key still means: no line"
    form = render_form(found, visa_required=True)
    assert form["work_authorization"] == "" and form.get("links", []) == []


@pytest.mark.parametrize("name", ["REPLACE: your full name", "REPLACE_ME", "", None])
def test_a_placeholder_or_missing_name_is_flagged_and_the_real_fields_still_fill(tmp_path: Path, name: str | None) -> None:
    content = {"email": "zora.q@example.invalid", "phone": "555-0142-ZQ"} | ({} if name is None else {"name": name})
    found = read_header_file(_file(tmp_path, content))
    assert found.state == STATE_FILLED and found.values["name"] == "" and found.values["email"] == "zora.q@example.invalid"
    assert found.name_note == f"{found.shown} has no name yet." and prefill_response(found)["name_note"] == found.name_note
    placeholder = bool(name) and name.startswith("REPLACE")
    assert found.placeholders == (("name",) if placeholder else ())
    assert (found.notice is not None) is placeholder and (not placeholder or found.notice.endswith("Skipped: name."))
    with pytest.raises(HeaderFileError) as refused:
        render_form(found, visa_required=False)
    assert f"{found.shown} has no name yet; a PDF header needs one" in str(refused.value)
    _silent(str(refused.value))


def test_a_real_value_that_only_holds_the_word_is_not_a_placeholder(tmp_path: Path) -> None:
    found = read_header_file(_file(tmp_path, {"name": "Zora Quillfeather", "location": "Do not REPLACE: Quillshire"}))
    assert found.placeholders == () and found.values["location"] == "Do not REPLACE: Quillshire"


# ---- the command line: `gigai scout resume pdf --header` / the default file


@pytest.fixture
def fx(tmp_path: Path) -> dict[str, Path]:
    home, work = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    work.mkdir()
    (work / "resume.md").write_text(MARKDOWN, encoding="utf-8")
    return {"home": home, "work": work, "resume": work / "resume.md", "out": work / "out.pdf", "header": work / "my-header.json"}


def _pdf(fx: dict[str, Path], *args: str, as_json: bool = True):
    command = ["scout", "resume", "pdf", "--in", str(fx["resume"]), "--home", str(fx["home"]), *args, *(["--json"] if as_json else [])]
    return CliRunner().invoke(cli, command)


def _text(path: Path) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(path.read_bytes())).pages)


@pytest.mark.parametrize("default_file", [False, True])
@pytest.mark.parametrize(
    ("content", "code", "says"),
    [
        (TEMPLATE, "header_file_placeholders", "header.json still has placeholder values: replace the REPLACE: fields (or save your details here)"),
        ({**TEMPLATE, "email": "zora.q@example.invalid"}, "header_file_invalid", "header.json has no name yet; a PDF header needs one"),
        ({"email": "zora.q@example.invalid", "name": ""}, "header_file_invalid", "header.json has no name yet; a PDF header needs one"),
    ],
)
def test_the_command_refuses_a_file_with_no_real_name_and_prints_no_placeholder(fx: dict[str, Path], content: dict, code: str, says: str, default_file: bool) -> None:
    path = fx["home"] / "header.json" if default_file else fx["work"] / "header.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    os.chmod(path, 0o600)
    which = [] if default_file else ["--header", str(path)]
    for as_json in (True, False):
        failed = _pdf(fx, "--out", str(fx["out"]), *which, as_json=as_json)
        assert failed.exit_code == 1 and says in failed.output and "Traceback" not in failed.output, failed.output
        assert ("--no-header makes the PDF without a header" in failed.output) is default_file
        _silent(failed.output)
        _no_placeholder(failed.output)
        assert not fx["out"].exists(), "no PDF is made from a file without a name"
    assert json.loads(_pdf(fx, "--out", str(fx["out"]), *which).output)["error"]["code"] == code
    # --no-header still makes the headerless PDF.
    plain = _pdf(fx, "--out", str(fx["out"]), "--no-header")
    assert plain.exit_code == 0 and "REPLACE" not in _text(fx["out"]), plain.output


@pytest.mark.parametrize("default_file", [False, True])
def test_the_command_prints_the_real_fields_and_no_placeholder_into_the_pdf(fx: dict[str, Path], default_file: bool) -> None:
    path = fx["home"] / "header.json" if default_file else fx["work"] / "header.json"
    content = {
        **TEMPLATE, "name": "Zora Quillfeather", "phone": "555-0142-ZQ", "email": "",
        "links": [{"label": "LinkedIn", "url": "REPLACE: linkedin.com/in/you"}, {"label": "GitHub", "url": "github.com/zq-invalid-7731"}, {"label": "Site", "url": ""}],
    }
    path.write_text(json.dumps(content), encoding="utf-8")
    os.chmod(path, 0o600)
    made = _pdf(fx, "--out", str(fx["out"]), *([] if default_file else ["--header", str(path)]))
    assert made.exit_code == 0, made.output
    answer = json.loads(made.output)
    assert answer["header"] is True and answer["header_file"].endswith("header.json")
    assert answer["header_note"].endswith("header.json still has placeholder values: replace the REPLACE: fields (or save your details here). Skipped: location, links, work_authorization.")
    text = _text(fx["out"])
    squeezed = "".join(text.split())
    assert squeezed.startswith("ZORAQUILLFEATHER" + "555-0142-ZQ|github.com/zq-invalid-7731" + "SUMMARY"), text[:300]
    assert "REPLACE" not in text, "no placeholder prints"
    _no_placeholder(text, made.output)
    _silent(made.output.replace(str(fx["home"]), "").replace(str(fx["work"]), ""))
    # The plain-text output says the same sentence.
    said = _pdf(fx, "--out", str(fx["out"]), *([] if default_file else ["--header", str(path)]), as_json=False)
    assert said.exit_code == 0 and "still has placeholder values: replace the REPLACE: fields (or save your details here). Skipped: location, links, work_authorization." in said.output
