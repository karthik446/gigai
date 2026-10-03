"""0.1.10.8 PA: ``gigai scout resume check|clean`` on SYNTHETIC resumes (fictional example.test / 555-01xx values)."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import resume_gate_cli

NAME = "Jordan Testwell"
EMAIL = "jordan.testwell@example.test"
PHONE = "(555) 010-0142"
STREET = "42 Fictional Street"
LINK = "linkedin.com/in/jordan-testwell"
INLINE_EMAIL = "reviewer.contact@example.test"
PLANTED = (NAME, EMAIL, PHONE, STREET, LINK, INLINE_EMAIL, "555")

RESUME = f"""{NAME}
{EMAIL} | {PHONE}
{STREET}, Faketown
{LINK}

## Summary
Backend engineer who cut CI time by 60%.

## Experience
### Platform engineer, Example Corp
- Built the deploy pipeline; reach {INLINE_EMAIL} for the runbook.
- Reduced incident count by half.
"""

CLEAN = "## Summary\nBackend engineer who cut CI time by 60%.\n\n## Experience\n- Built a deploy pipeline.\n"


def _run(*args: str):
    return CliRunner().invoke(cli, ["scout", "resume", *args])


def _assert_no_values(*outputs: str) -> None:
    for output in outputs:
        for planted in PLANTED:
            assert planted not in output, planted


@pytest.fixture
def resume(tmp_path: Path) -> Path:
    path = tmp_path / "resume.md"
    path.write_text(RESUME, encoding="utf-8")
    return path


def test_check_reports_kinds_and_lines_never_values(resume: Path) -> None:
    result = _run("check", str(resume), "--json")
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True and payload["clean"] is False
    found = {(f["kind"], f["line"]) for f in payload["findings"]}
    assert {("name", 1), ("email", 2), ("phone", 2), ("address", 3), ("link", 4), ("email", 11)} <= found
    assert payload["counts"]["email"] == 2
    _assert_no_values(result.output)
    human = _run("check", str(resume))
    assert human.exit_code == 2
    assert "line 2: email" in human.output and "can't catch" in human.output
    _assert_no_values(human.output)


def test_check_passes_a_clean_file(tmp_path: Path) -> None:
    path = tmp_path / "clean.txt"
    path.write_text(CLEAN, encoding="utf-8")
    result = _run("check", str(path), "--json")
    assert result.exit_code == 0
    assert json.loads(result.output)["clean"] is True and json.loads(result.output)["findings"] == []
    assert _run("check", str(path)).exit_code == 0


def test_the_can_miss_sentence_is_the_import_warning_s_own() -> None:
    assert resume_gate_cli.CAN_MISS.startswith("It can't catch personal details")


def test_clean_writes_a_copy_that_passes_check_and_leaves_the_input(resume: Path, tmp_path: Path) -> None:
    before = resume.read_bytes()
    out = tmp_path / "resume-clean.md"
    result = _run("clean", str(resume), "--out", str(out), "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert {(f["kind"], f["line"]) for f in payload["removed"]} >= {("name", 1), ("email", 2), ("address", 3)}
    _assert_no_values(result.output)
    assert resume.read_bytes() == before
    cleaned = out.read_text(encoding="utf-8")
    _assert_no_values(cleaned)
    assert "Backend engineer who cut CI time by 60%." in cleaned
    assert _run("check", str(out)).exit_code == 0
    human = _run("clean", str(resume), "--out", str(tmp_path / "again.md"))
    assert human.exit_code == 0 and "Glance at the cleaned file" in human.output
    _assert_no_values(human.output)


def test_clean_is_idempotent(resume: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "one.md", tmp_path / "two.md"
    assert _run("clean", str(resume), "--out", str(first)).exit_code == 0
    result = _run("clean", str(first), "--out", str(second), "--json")
    assert result.exit_code == 0 and json.loads(result.output)["removed"] == []
    assert first.read_bytes() == second.read_bytes()


def test_clean_refuses_to_overwrite_or_write_over_the_input(resume: Path, tmp_path: Path) -> None:
    out = tmp_path / "taken.md"
    out.write_text("keep me\n", encoding="utf-8")
    refused = _run("clean", str(resume), "--out", str(out))
    assert refused.exit_code != 0 and out.read_text() == "keep me\n"
    assert "--force" in refused.output
    assert _run("clean", str(resume), "--out", str(out), "--force").exit_code == 0
    assert out.read_text() != "keep me\n"
    same = _run("clean", str(resume), "--out", str(resume), "--force", "--json")
    assert same.exit_code == 1 and json.loads(same.output)["error"]["code"] == "resume_out_is_input"
    assert resume.read_text() == RESUME


def test_odd_inputs_are_named_errors(tmp_path: Path) -> None:
    missing = _run("check", str(tmp_path / "nope.md"), "--json")
    assert missing.exit_code == 1 and json.loads(missing.output)["error"]["code"] == "resume_file_missing"
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    assert json.loads(_run("check", str(pdf), "--json").output)["error"]["code"] == "resume_media_type_unsupported"
    binary = tmp_path / "bin.md"
    binary.write_bytes(b"\x00\x01\x02\xff")
    assert json.loads(_run("check", str(binary), "--json").output)["error"]["code"] == "resume_file_binary"
    latin = tmp_path / "latin.txt"
    latin.write_bytes(b"caf\xe9 engineer\n")
    assert json.loads(_run("check", str(latin), "--json").output)["error"]["code"] == "resume_file_binary"
    (tmp_path / "dir.md").mkdir()
    assert _run("check", str(tmp_path / "dir.md")).exit_code != 0
    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")
    assert _run("check", str(empty)).exit_code == 0
    out = tmp_path / "o.md"
    assert _run("clean", str(empty), "--out", str(out)).exit_code == 0 and out.read_text() == ""
    blank = tmp_path / "crlf.md"
    blank.write_bytes(RESUME.replace("\n", "\r\n").encode())
    result = _run("check", str(blank), "--json")
    assert result.exit_code == 2 and ("email", 2) in {(f["kind"], f["line"]) for f in json.loads(result.output)["findings"]}


def test_no_home_is_needed_and_none_is_created(resume: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("GIGAI_HOME", raising=False)
    out = tmp_path / "nested" / "dir" / "clean.md"
    assert _run("check", str(resume)).exit_code == 2
    assert _run("clean", str(resume), "--out", str(out)).exit_code == 0
    assert _run("check", str(out)).exit_code == 0
    assert list(home.rglob("*")) == []
