"""0.1.10.8 PA: ``gigai scout resume check|clean`` on SYNTHETIC resumes (fictional example.test / 555-01xx values)."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.canonical import canonical_json_bytes
from gigai.cli import cli
from gigai.private_records import read_record
from gigai.scout import resume_gate_cli
from gigai.scout.master_store import load_master
from gigai.scout.target_resolution import home_scout_target

from tests.support.scout_profile_fixtures import default_find_jobs_config
from tests.support.setup_home import setup_home

NAME = "Jordan Testwell"
EMAIL = "jordan.testwell@example.test"
PHONE = "(555) 010-0142"
STREET = "42 Fictional Street"
LINK = "linkedin.com/in/jordan-testwell"
INLINE_EMAIL = "reviewer.contact@example.test"
#: The phone number's pieces as the file writes them, not a bare "555": what a command prints names a file under pytest's
#: temporary folder, and "555" is in that path on some runs (``pytest-555/``; 0.1.10.10 FK).
PLANTED = (NAME, EMAIL, PHONE, STREET, LINK, INLINE_EMAIL, "(555)", "010-0142")

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
    assert "555" not in cleaned, "the cleaned file holds no path, so no piece of the phone number at all"
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


# --- 0.1.10.11 NF: a title that is a link keeps its words, as ``resume add`` keeps them ---------------
#
# ``resume clean`` ran the strip without the import's rule for a link in a title line
# (``resume_privacy.heading_links``): ``**[Dispatch Optimizer](https://...)** *(Python, ...)*`` came out
# as ``**[Dispatch Optimizer]( *(Python, ...)*``, ``resume check`` called that clean, and the broken line
# reached the master and the PDF. Two shapes real resumes have: a bold link title with its stack in
# italics, and a ``###`` heading whose bold name is a link.

LINK_HOSTS = ("northwind-logistics.example.test", "github.com", "jtestwell-synth")
LINKED = f"""{NAME}
{EMAIL} | {PHONE}

## Summary
Backend engineer who cut CI time by 60%.

## Experience
### Northwind Logistics
Staff Engineer | 2020 - Present
- Runs dispatch planning for 40 depots.

**[Dispatch Optimizer](https://northwind-logistics.example.test/engineering/dispatch-optimizer)** *(Python, Kafka, PostgreSQL)*
- Cut route planning time by 40%.

## Projects
### **[Tessera](https://github.com/jtestwell-synth/tessera)** — Open-Source Feature-Flag Service | Go, PostgreSQL
- Serves 2,000 flag checks a second.
"""
#: The two title lines as ``resume add`` stores them: the words, the bold and the italics kept; no bracket, no address.
BOLD_TITLE = "**Dispatch Optimizer** *(Python, Kafka, PostgreSQL)*"
HEADING_TITLE = "### **Tessera** — Open-Source Feature-Flag Service | Go, PostgreSQL"


def _home_run(home: Path, *args: str) -> dict:
    result = CliRunner().invoke(cli, ["scout", "resume", *args, "--home", str(home), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _stored_resume(home: Path, record_id: str) -> str:
    return read_record(home_root=home, requested_target=home_scout_target(home), record_id=record_id, content=True)["content"].decode("utf-8")


def test_clean_keeps_the_words_of_a_title_that_is_a_link(tmp_path: Path) -> None:
    source = tmp_path / "resume.md"
    source.write_text(LINKED, encoding="utf-8")
    out = tmp_path / "resume-clean.md"

    result = _run("clean", str(source), "--out", str(out), "--json")

    assert result.exit_code == 0, result.output
    cleaned = out.read_text(encoding="utf-8")
    # The end of it: both titles read as titles, with no piece of a link left on them.
    assert BOLD_TITLE in cleaned.splitlines() and HEADING_TITLE in cleaned.splitlines(), cleaned
    assert "[" not in cleaned and "](" not in cleaned and "http" not in cleaned
    for planted in (*PLANTED, *LINK_HOSTS):
        assert planted not in cleaned and planted not in result.output, planted
    assert {(f["kind"], f["line"]) for f in json.loads(result.output)["removed"]} >= {("name", 1), ("email", 2), ("link", 12), ("link", 16)}
    checked = _run("check", str(out), "--json")
    assert checked.exit_code == 0 and json.loads(checked.output)["clean"] is True
    again = tmp_path / "again.md"
    assert _run("clean", str(out), "--out", str(again)).exit_code == 0 and again.read_bytes() == out.read_bytes()


def test_the_cleaned_copy_is_what_resume_add_stores_and_the_master_keeps_the_project_names(tmp_path: Path) -> None:
    source = tmp_path / "resume.md"
    source.write_text(LINKED, encoding="utf-8")
    out = tmp_path / "resume-clean.md"
    assert _run("clean", str(source), "--out", str(out)).exit_code == 0
    cleaned = out.read_text(encoding="utf-8")

    # ``resume add`` of the file as it is (the road the quickstart takes) stores exactly the cleaned copy.
    raw_home = setup_home(tmp_path / "raw", workpad_root=tmp_path / "raw-workpads")
    assert _stored_resume(raw_home, _home_run(raw_home, "add", str(source))["record_id"]) == cleaned

    # ``resume clean`` then ``resume add`` (the road Start here takes): nothing more to remove, the same text stored.
    home = setup_home(tmp_path / "home", workpad_root=tmp_path / "home-workpads")
    added = _home_run(home, "add", str(out))
    assert added["contact_removed"] is None
    assert _stored_resume(home, added["record_id"]) == cleaned

    # The master made of it has both projects by name, each with its own bullet.
    (home_scout_target(home) / "find-jobs.json").write_bytes(canonical_json_bytes(default_find_jobs_config().to_json()))
    assert _home_run(home, "master", "init")["status"] == "created"
    stored = load_master(home_root=home, target=home_scout_target(home))
    assert stored is not None
    master = stored.master
    bullets = {entry.heading: [master.items[item].text for item in entry.bullets] for entry in master.entries.values()}
    named = {heading: lines for heading, lines in bullets.items() if "Dispatch Optimizer" in heading or "Tessera" in heading}
    assert sorted(named.values()) == [["Cut route planning time by 40%."], ["Serves 2,000 flag checks a second."]], bullets
    markdown = master.markdown()
    assert "Dispatch Optimizer" in markdown and "Tessera" in markdown
    assert "[" not in markdown.replace("[quantified]", "") and "](" not in markdown, markdown
    for item in master.items.values():
        assert "Dispatch Optimizer" not in item.text and "Tessera" not in item.text, "a project title is an entry, never a bullet"


def test_clean_refuses_a_heading_that_is_only_a_link_as_resume_add_does(tmp_path: Path) -> None:
    source = tmp_path / "resume.md"
    source.write_text("## Projects\n### [GitHub](https://github.com/jtestwell-synth)\n- Serves 2,000 flag checks a second.\n", encoding="utf-8")
    out = tmp_path / "resume-clean.md"

    result = _run("clean", str(source), "--out", str(out), "--json")

    assert result.exit_code == 1, result.output
    error = json.loads(result.output)["error"]
    assert error["code"] == "resume_heading_only_link" and "line 2: this heading is only a link" in error["message"]
    assert "github" not in result.output and not out.exists()
