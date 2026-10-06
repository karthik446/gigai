"""0.1.11.3 item 13: ``gigai scout resume pdf --header FILE`` -- a full PDF with the header, without the browser.

The header comes from the user's own JSON file (``--header FILE``, else the default file when it exists) and is
printed only into a PDF written to ``--out``: never into the resumes folder, never into the command's output.
A missing, invalid or name-less file is one plain sentence and exit 1, never a traceback.  Synthetic values, tmp
homes only (the default file of a scratch home is ``<home>/header.json``).
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner
from pypdf import PdfReader

from gigai.cli import cli
from gigai.scout.pdf_header_file import SPONSORSHIP_DEFAULT

from tests.behaviors.scout_find_jobs.test_pdf_header_file import FILE, MARKERS

MARKDOWN = "## Summary\n\n- Platform engineer with nine years building billing systems.\n\n## Skills\n\n- Python, SQL\n"
CONTACT = "zora.q@example.invalid | 555-0142-ZQ | Quillshire, ZZ | linkedin.com/in/zq-invalid-7731 | github.com/zq-invalid-7731 | zq-invalid-7731.example.invalid"


@pytest.fixture
def fx(tmp_path: Path) -> dict[str, Path]:
    home, work = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    work.mkdir()
    (work / "resume.md").write_text(MARKDOWN, encoding="utf-8")
    return {"home": home, "work": work, "resume": work / "resume.md", "out": work / "out.pdf", "header": work / "my-header.json"}


def _header(path: Path, content: object = FILE, mode: int = 0o600) -> Path:
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    os.chmod(path, mode)
    return path


def _pdf(fx: dict[str, Path], *args: str, as_json: bool = True):
    command = ["scout", "resume", "pdf", "--in", str(fx["resume"]), "--home", str(fx["home"]), *args, *(["--json"] if as_json else [])]
    return CliRunner().invoke(cli, command)


def _lines(path: Path) -> list[str]:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(path.read_bytes())).pages).split("\n")


def _squeezed(path: Path) -> str:
    """The PDF's text with no white space: a long contact line wraps, and where it wraps is the layout's business."""
    return "".join("".join(_lines(path)).split())


def _line(path: Path, index: int = 0) -> str:
    """One line of the PDF's text without white space (a tracked heading extracts as ``S U M M A RY``)."""
    return "".join(_lines(path)[index].split())


def _squeeze(*parts: str) -> str:
    return "".join("".join(parts).split())


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


def test_header_file_makes_the_full_pdf_and_prints_no_value(fx: dict[str, Path]) -> None:
    done = _pdf(fx, "--out", str(fx["out"]), "--header", str(_header(fx["header"])))
    assert done.exit_code == 0, done.output
    payload = json.loads(done.output)
    assert payload["header"] is True and payload["header_file"] == str(fx["header"]) and payload["header_note"] is None
    assert payload["finish_url"] is None and payload["in_resumes_folder"] is False, "nothing is left to finish in the browser"
    assert _line(fx["out"]) == "ZORAQUILLFEATHER"
    assert _squeezed(fx["out"]).startswith(_squeeze("ZORA QUILLFEATHER", CONTACT, "VISA: H1B (ZQ-7731)", "SUMMARY")), _lines(fx["out"])[:5]
    _silent(done.output)
    text = _pdf(fx, "--out", str(fx["out"]), "--header", str(fx["header"]), as_json=False)
    assert text.exit_code == 0 and f"with your name and contact details from {fx['header']}." in text.output
    assert "Open in Scout" not in text.output
    _silent(text.output)
    # Only the PDF the user asked for holds the values: no resumes folder, no store, no log.
    assert not (fx["home"] / "resumes").exists() and not (fx["home"] / "scout").exists()
    _disk_is_clean(fx, fx["out"])


def test_the_default_file_is_used_with_out_and_never_for_the_resumes_folder(fx: dict[str, Path]) -> None:
    _header(fx["home"] / "header.json")
    with_out = _pdf(fx, "--out", str(fx["out"]))
    assert with_out.exit_code == 0, with_out.output
    assert json.loads(with_out.output)["header"] is True and _line(fx["out"]) == "ZORAQUILLFEATHER"
    _silent(with_out.output.replace(str(fx["home"]), ""))

    # Without --out the PDF goes to the resumes folder, which never holds contact details: headerless, with a plain note.
    in_folder = _pdf(fx)
    assert in_folder.exit_code == 0, in_folder.output
    payload = json.loads(in_folder.output)
    assert payload["header"] is False and payload["header_file"] is None and payload["in_resumes_folder"] is True
    assert payload["header_note"].endswith("was not used: pass --out FILE to make the PDF with your name and contact details.")
    assert payload["finish_url"], "the headerless PDF is still finished in Scout"
    saved = [path for path in (fx["home"] / "resumes").iterdir() if path.suffix == ".pdf"]
    assert len(saved) == 1 and _line(saved[0]) == "SUMMARY"
    _silent(in_folder.output.replace(str(fx["home"]), ""))

    # --no-header: headerless to --out too, and the file is not even mentioned.
    fx["out"].unlink()
    plain = _pdf(fx, "--out", str(fx["out"]), "--no-header")
    assert plain.exit_code == 0 and json.loads(plain.output)["header"] is False and json.loads(plain.output)["header_note"] is None
    assert _line(fx["out"]) == "SUMMARY"
    _disk_is_clean(fx)


def test_no_header_file_changes_nothing(fx: dict[str, Path]) -> None:
    done = _pdf(fx, "--out", str(fx["out"]))
    assert done.exit_code == 0, done.output
    payload = json.loads(done.output)
    assert payload["header"] is False and payload["header_file"] is None and payload["header_note"] is None and payload["finish_url"]
    assert _line(fx["out"]) == "SUMMARY"


def test_a_file_other_users_can_read_is_used_and_warned_about(fx: dict[str, Path]) -> None:
    done = _pdf(fx, "--out", str(fx["out"]), "--header", str(_header(fx["header"], mode=0o644)), as_json=False)
    assert done.exit_code == 0, done.output
    assert f"{fx['header']} can be read by other users of this computer. To keep it to yourself: chmod 600 {fx['header']}" in done.output
    assert _line(fx["out"]) == "ZORAQUILLFEATHER"
    _silent(done.output)
    assert json.loads(_pdf(fx, "--out", str(fx["out"]), "--header", str(fx["header"])).output)["header_note"].startswith(str(fx["header"]))


@pytest.mark.parametrize(
    ("content", "code", "says"),
    [
        (None, "header_file_missing", "There is no header file at"),
        ('{"name": "Zora Quillfeather", ', "header_file_invalid", "is not valid JSON"),
        ({"name": "Zora Quillfeather", "phone": ["555-0142-ZQ"]}, "header_file_invalid", "phone must be text in double quotes"),
        ({"email": "zora.q@example.invalid"}, "header_file_invalid", "has no name; a PDF header needs one"),
    ],
)
def test_a_missing_or_unusable_file_is_one_plain_sentence(fx: dict[str, Path], content: object, code: str, says: str) -> None:
    if content is not None:
        _header(fx["header"], content)
    for as_json in (True, False):
        failed = _pdf(fx, "--out", str(fx["out"]), "--header", str(fx["header"]), as_json=as_json)
        assert failed.exit_code == 1, failed.output
        assert says in failed.output and str(fx["header"]) in failed.output and "Traceback" not in failed.output
        assert failed.exception is None or isinstance(failed.exception, SystemExit), repr(failed.exception)
        _silent(failed.output)
        if as_json:
            assert json.loads(failed.output)["error"]["code"] == code
    assert not fx["out"].exists(), "no PDF is written without the header that was asked for"


def test_a_broken_default_file_stops_an_out_pdf_and_only_notes_a_folder_pdf(fx: dict[str, Path]) -> None:
    _header(fx["home"] / "header.json", '{"name": "Zora Quillfeather"')
    failed = _pdf(fx, "--out", str(fx["out"]))
    assert failed.exit_code == 1 and json.loads(failed.output)["error"]["code"] == "header_file_invalid"
    assert "(--no-header makes the PDF without a header)" in failed.output and not fx["out"].exists()
    _silent(failed.output.replace(str(fx["home"]), ""))
    assert _pdf(fx, "--out", str(fx["out"]), "--no-header").exit_code == 0
    in_folder = _pdf(fx)
    assert in_folder.exit_code == 0 and "was not used" in json.loads(in_folder.output)["header_note"]


def test_header_needs_out_and_does_not_go_with_no_header(fx: dict[str, Path]) -> None:
    _header(fx["header"])
    no_out = _pdf(fx, "--header", str(fx["header"]))
    assert no_out.exit_code == 1 and "--header needs --out FILE" in no_out.output and "never written to the resumes folder" in no_out.output
    both = _pdf(fx, "--out", str(fx["out"]), "--header", str(fx["header"]), "--no-header")
    assert both.exit_code == 1 and "do not go together" in both.output
    assert not fx["out"].exists() and not (fx["home"] / "resumes").exists()


def test_the_work_authorization_line_file_then_the_profiles_sponsorship_answer(fx: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Precedence without a form: the file's line, else (no such key in the file) the profile's sponsorship answer."""
    from gigai.scout.find_jobs import resume_input

    target = tmp_path / "target"
    target.mkdir()
    asked: list[Path] = []
    answers = {"visa": True}

    def preferences(path: Path) -> tuple[bool, tuple[str, ...]]:
        asked.append(path)
        return answers["visa"], ()

    monkeypatch.setattr(resume_input, "read_config_preferences", preferences)
    no_key = {key: value for key, value in FILE.items() if key != "work_authorization"}

    def third_line(content: dict[str, object]) -> str:
        fx["out"].unlink(missing_ok=True)
        short = {**content, "links": []}  # a contact line that does not wrap: the line after it is line 3
        done = _pdf(fx, "--out", str(fx["out"]), "--header", str(_header(fx["header"], short)), "--target", str(target))
        assert done.exit_code == 0, done.output
        return _line(fx["out"], 2)

    assert third_line(FILE) == _squeeze("VISA: H1B (ZQ-7731)"), "the file's line wins over the profile's answer"
    assert third_line(no_key) == _squeeze(SPONSORSHIP_DEFAULT), "no key in the file: the profile says sponsorship is needed"
    assert third_line({**FILE, "work_authorization": ""}) == "SUMMARY", 'an empty key is "no line"'
    answers["visa"] = False
    assert third_line(no_key) == "SUMMARY", "the profile needs no sponsorship: no line"
    assert asked and all(path == target.resolve() for path in asked)
