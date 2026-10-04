"""0.1.10.9 master P1: the master resume's store, through the real CLI on a scratch home.

``gigai scout resume master init --from FILE | show | history`` on the SYNTHETIC master of the
master-resume spike (``tests/evals/fixtures/master/master.md``: an invented person, 192 ids, no
contact data) and on small synthetic files written here (fictional example.test / 555-01xx values).
Every test runs against a temp ``--home``; nothing reads the operator's home or resumes.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import master_resume
from gigai.scout.master_resume import MasterResumeError, assign_ids, build_master, draft_master, parse_master

FIXTURE = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "master" / "master.md"
_ID = re.compile(r"<!--[^>]*\bid:([A-Za-z0-9_-]+)")
_TRAILING_COMMENT = re.compile(r"\s*<!--.*?-->\s*$")

NAME = "Jordan Testwell"
EMAIL = "jordan.testwell@example.test"
PHONE = "(555) 010-0142"
LINK = "linkedin.com/in/jordan-testwell"
INLINE_EMAIL = "reviewer.contact@example.test"
PLANTED = ("Jordan", "Testwell", EMAIL, PHONE, "010-0142", LINK, "jordan-testwell", INLINE_EMAIL)

SMALL = """<!-- gigai-master:1 -->

## Summary

- Platform engineer with 9 years of experience in payment systems. <!-- id:sum-platform tags:backend -->

## Experience

### Example Corp <!-- id:r-example -->
Staff Engineer | Jun 2019 - Present
- Cut deploy time from 40 minutes to 6 with a staged pipeline. <!-- id:b-deploy tags:delivery -->
- Ran the on-call rotation for 4 teams. <!-- id:b-oncall -->
- Wrote the incident review guide the group still uses. <!-- id:b-guide tags:writing backed:story:incident-guide -->

## Skills

- Languages: Python, Go, SQL <!-- id:s-lang -->
"""


def _setup(tmp_path: Path, name: str = "home") -> Path:
    """Non-interactive ``gigai setup`` only: the master commands install Scout themselves, as ``resume add`` does."""

    home = tmp_path / name
    result = CliRunner().invoke(
        cli,
        ["setup", "--non-interactive", "--home", str(home), "--workpad-root", str(tmp_path / f"{name}-workpads"), "--editor", "/usr/bin/true", "--json"],
    )
    assert result.exit_code == 0, result.output
    return home


def _master(home: Path, *args: str, ok: bool = True) -> dict[str, object]:
    result = CliRunner().invoke(cli, ["scout", "resume", "master", *args, "--home", str(home), "--json"])
    assert result.exit_code == (0 if ok else 1), result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _plain(home: Path, *args: str):
    return CliRunner().invoke(cli, ["scout", "resume", "master", *args, "--home", str(home)])


def _write(tmp_path: Path, text: str, name: str = "master-in.md") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _fixture_lines() -> dict[str, str]:
    """``id -> text`` read from the fixture file by hand (not by the product parser)."""

    lines: dict[str, str] = {}
    for raw in FIXTURE.read_text(encoding="utf-8").splitlines():
        found = _ID.search(raw)
        if found and raw.startswith("- "):
            lines[found.group(1)] = _TRAILING_COMMENT.sub("", raw)[2:].strip()
    return lines


def _workpad(home: Path) -> Path:
    from gigai.scout.target_resolution import home_scout_target
    from gigai.workpad import resolve_workpad

    return resolve_workpad(home_root=home, requested_target=home_scout_target(home), gig_id=None, allow_semantic_state=True).path


def _git_objects(workpad: Path) -> bytes:
    """Every object of the workpad's journal, decompressed (blobs, trees, commits)."""

    return subprocess.run(["git", "-C", str(workpad), "cat-file", "--batch-all-objects", "--batch"], capture_output=True, check=True).stdout


# --- init + show: the master is stored and read back -------------------------------------------


def test_init_stores_the_synthetic_master_and_show_reads_every_line_back(tmp_path: Path) -> None:
    home = _setup(tmp_path)

    created = _master(home, "init", "--from", str(FIXTURE))

    assert created["ok"] is True and created["status"] == "created" and created["scout_installed"] is True
    assert created["contact_removed"] is None, "the synthetic master holds no contact data"
    assert created["ids_assigned"] == 0 and created["changes"] == {"added": 192, "removed": 0, "changed": 0}
    counts = created["master"]["counts"]  # type: ignore[index]
    assert counts["ids"] == 192 and counts["entries"] == 16 and counts["skills"] == 79
    assert counts["by_kind"] == {"summary": 4, "bullet": 148, "skills": 10, "other": 14}
    assert created["master"]["revision"] == 1 and created["master"]["written_by"] == "operator"  # type: ignore[index]

    shown = _master(home, "show")["master"]
    assert shown["revision"] == 1 and shown["format"] == 1  # type: ignore[index]
    assert shown["sections"] == ["summary", "experience", "projects", "skills", "education", "other"]  # type: ignore[index]
    items = {item["id"]: item for item in shown["items"]}  # type: ignore[index, union-attr]
    expected = _fixture_lines()
    assert {key: item["text"] for key, item in items.items()} == expected, "every line comes back under its own id, word for word"
    bullets = [item for item in items.values() if item["kind"] == "bullet"]
    assert len(bullets) == 148
    # The spike's own numbers (DESIGN.md 3.1): 112 of the 148 bullets state a number, 36 do not, none is backed.
    assert sum(item["strength"] == "quantified" for item in bullets) == 112
    assert sum(item["strength"] == "stated" for item in bullets) == 36
    assert all(re.fullmatch(r"[0-9a-f]{16}", item["mark"]) for item in items.values()) and len({item["mark"] for item in items.values()}) == len(items)
    first = items["b-lum-01"]
    assert first["section"] == "experience" and first["entry_id"] == "r-lum" and first["tags"] == ["agents", "llm"] and first["backed"] == []
    infra = items["s-infra"]
    assert infra["label"] == "Cloud and infrastructure" and infra["skills"][:3] == ["Kubernetes", "Docker", "Terraform"]
    entries = {entry["id"]: entry for entry in shown["entries"]}  # type: ignore[index, union-attr]
    assert len(entries) == 16
    assert entries["r-lum"]["heading"] == "Lumenfold" and entries["r-lum"]["sublines"] == ["Staff AI Engineer | Feb 2023 - Present"]
    assert (entries["r-lum"]["start"], entries["r-lum"]["end"], entries["r-lum"]["ongoing"]) == (2023, None, True)
    assert (entries["e-bs"]["start"], entries["e-bs"]["end"], entries["e-bs"]["ongoing"]) == (2006, 2010, False)
    assert entries["r-lum"]["bullets"][0] == "b-lum-01" and all(items[b]["entry_id"] == "r-lum" for b in entries["r-lum"]["bullets"])

    text = _plain(home, "show")
    assert text.exit_code == 0, text.output
    assert "Master resume, revision 1" in text.output and "176 lines, 16 entries, 79 skills" in text.output
    assert "b-lum-01  [quantified]  Architected the agent runtime" in text.output and "## Experience" in text.output


def test_show_filters_by_section_and_entry(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    _master(home, "init", "--from", str(FIXTURE))

    skills = _master(home, "show", "--section", "Skills")["master"]
    assert {item["kind"] for item in skills["items"]} == {"skills"} and len(skills["items"]) == 10 and skills["entries"] == []  # type: ignore[index, union-attr]
    role = _master(home, "show", "--entry", "r-lum")["master"]
    assert [entry["id"] for entry in role["entries"]] == ["r-lum"]  # type: ignore[index, union-attr]
    assert role["items"] and {item["entry_id"] for item in role["items"]} == {"r-lum"}  # type: ignore[index, union-attr]
    assert role["counts"]["ids"] == 192, "counts describe the whole master"  # type: ignore[index]

    assert _master(home, "show", "--entry", "r-nope", ok=False)["error"]["code"] == "master_entry_not_found"  # type: ignore[index]
    assert _master(home, "show", "--section", "hobbies", ok=False)["error"]["code"] == "master_section_unknown"  # type: ignore[index]


def test_show_and_history_say_how_to_start_when_there_is_no_master(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    for command in ("show", "history"):
        error = _master(home, command, ok=False)["error"]
        assert error["code"] == "master_not_found" and "master init --from FILE" in error["message"]  # type: ignore[index]
    assert _master(home, "init", ok=False)["error"]["code"] == "master_source_missing"  # type: ignore[index]


# --- ids are assigned at import -----------------------------------------------------------------


def test_lines_without_an_id_get_one_at_import_and_the_same_file_gets_the_same_ids(tmp_path: Path) -> None:
    plain_lines = [_TRAILING_COMMENT.sub("", line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
    source = _write(tmp_path, "\n".join(plain_lines) + "\n")
    assert "id:" not in source.read_text(encoding="utf-8")
    home = _setup(tmp_path)

    created = _master(home, "init", "--from", str(source))

    assert created["status"] == "created" and created["ids_assigned"] == 192 and created["ids_restored"] == 0
    shown = _master(home, "show")["master"]
    ids = [item["id"] for item in shown["items"]] + [entry["id"] for entry in shown["entries"]]  # type: ignore[index, union-attr]
    assert len(ids) == 192 and len(set(ids)) == 192
    prefix = {"summary": "sum-", "bullet": "b-", "skills": "s-", "other": "o-"}
    assert all(re.fullmatch(prefix[item["kind"]] + r"[0-9a-f]{6}(-\d+)?", item["id"]) for item in shown["items"])  # type: ignore[index, union-attr]
    entry_prefix = {"experience": "r-", "projects": "p-", "education": "e-"}
    assert all(entry["id"].startswith(entry_prefix[entry["section"]]) for entry in shown["entries"])  # type: ignore[index, union-attr]
    assert sorted(item["text"] for item in shown["items"]) == sorted(_fixture_lines().values()), "no text changed"  # type: ignore[index, union-attr]

    other = _setup(tmp_path, "other-home")
    _master(other, "init", "--from", str(source))
    again = _master(other, "show")["master"]
    assert [item["id"] for item in again["items"]] == [item["id"] for item in shown["items"]]  # type: ignore[index, union-attr]
    assert again["content_sha256"] == shown["content_sha256"]  # type: ignore[index]


# --- the privacy strip at import ----------------------------------------------------------------


def test_contact_data_is_not_imported_and_is_named_by_kind_and_line_only(tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        f"{NAME}\n"  # line 1
        f"{EMAIL} | {PHONE}\n"  # line 2
        f"{LINK}\n"  # line 3
        "\n"
        "## Experience\n"
        "### Example Corp\n"
        "Staff Engineer | Jun 2019 - Present\n"
        "- Cut deploy time from 40 minutes to 6 with a staged pipeline.\n"
        f"- Built the runbook site; reach {INLINE_EMAIL} for access.\n"  # line 9
        "- Ran the on-call rotation for 4 teams.\n",
    )
    home = _setup(tmp_path)

    result = CliRunner().invoke(cli, ["scout", "resume", "master", "init", "--from", str(source), "--home", str(home), "--json"])

    assert result.exit_code == 0, result.output
    assert [marker for marker in PLANTED if marker in result.output] == [], "the command prints no contact value"
    created = json.loads(result.output.strip().splitlines()[-1])
    removed = created["contact_removed"]
    assert removed["removed"] == {"name": 1, "email": 2, "phone": 1, "link": 1}
    assert removed["lines"] == [
        {"kind": "name", "line": 1}, {"kind": "email", "line": 2}, {"kind": "phone", "line": 2},
        {"kind": "link", "line": 3}, {"kind": "email", "line": 9},
    ]
    shown = _master(home, "show")["master"]
    assert [item["text"] for item in shown["items"]] == [  # type: ignore[index, union-attr]
        "Cut deploy time from 40 minutes to 6 with a staged pipeline.",
        "Ran the on-call rotation for 4 teams.",
    ], "a line that looks like contact data is not imported; the rest is"

    plain = _plain(home, "init", "--from", str(source), "--revision", "1")
    assert plain.exit_code == 0 and "line 9: email" in plain.output and [marker for marker in PLANTED if marker in plain.output] == []

    # END outcome: no planted value in any file of the home (the Scout folder is inside it), the
    # workpad, nor in any object of the workpad's git journal.
    workpad = _workpad(home)
    holders = sorted(
        str(path)
        for root in (home, workpad)
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink() and any(marker.encode() in path.read_bytes() for marker in PLANTED)
    )
    assert holders == []
    objects = _git_objects(workpad)
    assert b"Cut deploy time from 40 minutes" in objects, "the journal holds the master"
    assert [marker for marker in PLANTED if marker.encode() in objects] == []


# --- one record, a revision per change ----------------------------------------------------------


def test_a_second_import_is_a_new_revision_of_the_same_record_and_history_lists_both(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    first = _master(home, "init", "--from", str(_write(tmp_path, SMALL)))
    assert first["status"] == "created" and first["master"]["revision"] == 1  # type: ignore[index]

    edited = (
        SMALL.replace("from 40 minutes to 6 with", "from 40 minutes to 5 with")  # same id, new text
        .replace("- Ran the on-call rotation for 4 teams. <!-- id:b-oncall -->\n", "")  # a line removed
        .replace("## Skills", "### Northwind Labs\nEngineer | 2015 - 2019\n- Shipped the billing export used by 300 customers.\n\n## Skills")  # typed without ids
    )
    source = _write(tmp_path, edited, "edited.md")

    # Replacing a master needs the revision that was read.
    blocked = _master(home, "init", "--from", str(source), ok=False)["error"]
    assert blocked["code"] == "master_exists" and blocked["current"]["revision"] == 1  # type: ignore[index]
    assert _master(home, "history")["revision"] == 1, "a refused write stores nothing"

    second = _master(home, "init", "--from", str(source), "--revision", "1", "--as", "agent")
    assert second["status"] == "revised" and second["master"]["revision"] == 2 and second["master"]["written_by"] == "agent"  # type: ignore[index]
    assert second["master"]["record_id"] == first["master"]["record_id"], "one record"  # type: ignore[index]
    assert second["master"]["parent_revision"] == first["master"]["revision_id"]  # type: ignore[index]
    assert second["ids_assigned"] == 2 and second["changes"] == {"added": 2, "removed": 1, "changed": 1}

    shown = _master(home, "show")["master"]
    items = {item["id"]: item["text"] for item in shown["items"]}  # type: ignore[index, union-attr]
    assert items["b-deploy"] == "Cut deploy time from 40 minutes to 5 with a staged pipeline.", "an edited line keeps its id"
    assert "b-oncall" not in items and "Shipped the billing export used by 300 customers." in items.values()
    assert {item["id"]: item["strength"] for item in shown["items"]}["b-guide"] == "backed"  # type: ignore[index, union-attr]

    history = _master(home, "history")
    assert history["revision"] == 2
    newest, oldest = history["revisions"]  # type: ignore[misc]
    assert (newest["revision"], newest["written_by"], newest["items"], newest["entries"]) == (2, "agent", 5, 2)
    assert (newest["added"], newest["removed"], newest["changed"]) == (2, 1, 1)
    assert (oldest["revision"], oldest["written_by"], oldest["items"], oldest["entries"]) == (1, "operator", 5, 1)
    assert newest["parent_revision"] == oldest["revision_id"] and oldest["parent_revision"] is None
    assert newest["content_sha256"] != oldest["content_sha256"]
    text = _plain(home, "history")
    assert text.exit_code == 0 and "Master resume: 2 revisions." in text.output and "(+2 -1 ~1)" in text.output

    # A writer that read revision 1 is refused once the master moved on, and is told where it is now.
    stale = _master(home, "init", "--from", str(_write(tmp_path, SMALL.replace("9 years", "10 years"), "stale.md")), "--revision", "1", ok=False)["error"]
    assert stale["code"] == "revision_conflict" and stale["current"]["revision"] == 2  # type: ignore[index]
    assert _master(home, "history")["revision"] == 2

    # The same file again writes nothing, with or without --revision.
    assert _master(home, "init", "--from", str(source))["status"] == "unchanged"
    assert _master(home, "init", "--from", str(source), "--revision", "2")["status"] == "unchanged"
    assert _master(home, "history")["revision"] == 2

    # The journal holds exactly one master record with two revisions (write-once files).
    from gigai.private_records import list_revisions
    from gigai.workpad import resolve_workpad
    from gigai.scout.target_resolution import home_scout_target

    resolved = resolve_workpad(home_root=home, requested_target=home_scout_target(home), gig_id=None, allow_semantic_state=True)
    chain = list_revisions(resolved=resolved, record_id=str(first["master"]["record_id"]))  # type: ignore[index]
    assert [item["revision_id"] for item in chain] == [oldest["revision_id"], newest["revision_id"]]
    assert sorted(path.name for path in (resolved.path / "records").iterdir() if path.name.startswith("record_")) == [first["master"]["record_id"]]  # type: ignore[index]


def test_an_id_comment_deleted_by_hand_comes_back_when_the_text_still_matches(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    _master(home, "init", "--from", str(_write(tmp_path, SMALL)))
    without = SMALL.replace(" <!-- id:b-oncall -->", "").replace(" <!-- id:r-example -->", "")
    assert without != SMALL

    again = _master(home, "init", "--from", str(_write(tmp_path, without, "no-ids.md")), "--revision", "1")

    assert again["status"] == "unchanged" and again["ids_restored"] == 2 and again["ids_assigned"] == 0
    assert again["changes"] == {"added": 0, "removed": 0, "changed": 0} and _master(home, "history")["revision"] == 1


def test_a_file_that_does_not_parse_leaves_the_last_good_revision_and_names_the_line_not_its_text(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    _master(home, "init", "--from", str(_write(tmp_path, SMALL)))
    before = _master(home, "show")["master"]

    broken = {
        "unknown section": (SMALL + "\n## Hobbies\n\n- Zymurgy at weekends.\n", "line 19", "Zymurgy"),
        "an id used twice": (SMALL.replace("id:b-oncall", "id:b-deploy"), "line 12", "on-call"),
        "text after the bullets": (SMALL.replace("\n\n## Skills", "\n\nQuixotic trailing paragraph.\n\n## Skills"), "line 15", "Quixotic"),
        "an entry in Skills": (SMALL + "### Xylograph tools\n", "line 18", "Xylograph"),
    }
    for case, (text, line, word) in broken.items():
        result = CliRunner().invoke(
            cli, ["scout", "resume", "master", "init", "--from", str(_write(tmp_path, text, "broken.md")), "--revision", "1", "--home", str(home), "--json"]
        )
        assert result.exit_code == 1, (case, result.output)
        error = json.loads(result.output.strip().splitlines()[-1])["error"]
        assert error["code"] == "master_markdown_invalid" and line in error["message"], (case, error)
        assert word not in result.output, f"{case}: the error names the line, never its text"

    newer = _master(home, "init", "--from", str(_write(tmp_path, SMALL.replace("gigai-master:1", "gigai-master:2"), "v2.md")), "--revision", "1", ok=False)
    assert newer["error"]["code"] == "master_format_unsupported"  # type: ignore[index]
    pdf = _master(home, "init", "--from", str(_write(tmp_path, "%PDF-1.7", "resume.pdf")), "--revision", "1", ok=False)
    assert pdf["error"]["code"] == "resume_media_type_unsupported"  # type: ignore[index]
    missing = _master(home, "init", "--from", str(tmp_path / "nope.md"), "--revision", "1", ok=False)
    assert missing["error"]["code"] == "master_file_missing"  # type: ignore[index]

    assert _master(home, "history")["revision"] == 1
    assert _master(home, "show")["master"] == before


# --- it changes no existing reader --------------------------------------------------------------


def test_the_master_is_never_the_resume_an_existing_reader_resolves(tmp_path: Path) -> None:
    from gigai import private_records, run
    from gigai.scout.target_resolution import home_scout_target

    home = _setup(tmp_path)
    target = home_scout_target(home)
    _master(home, "init", "--from", str(FIXTURE))

    # A home with a master and no resume still has no resume.
    with pytest.raises(run.RunError, match="no committed resume is available"):
        run.resolve_newest_resume_details(home, target)

    resume = _write(tmp_path, "## Summary\nBackend engineer who cut CI time by 60%.\n", "resume.md")
    added = CliRunner().invoke(cli, ["scout", "resume", "add", str(resume), "--home", str(home), "--json"])
    assert added.exit_code == 0, added.output
    added_record = json.loads(added.output.strip().splitlines()[-1])["record_id"]
    # A master revision written AFTER the resume: the newest resume is still the resume.
    _master(home, "init", "--from", str(_write(tmp_path, SMALL)), "--revision", "1")

    details = run.resolve_newest_resume_details(home, target)
    assert details.pinned.record_id == added_record and details.label == "resume.md"
    references = private_records.list_imports(home_root=home, requested_target=target, family="reference")
    assert sorted((item["kind"], item["label"]) for item in references) == [
        ("resume", "resume.md"), ("role_history", "master.md"), ("role_history", "master.md"),
    ]
    listed = CliRunner().invoke(cli, ["scout", "profile", "list", "--home", str(home), "--json"])
    assert listed.exit_code == 0, listed.output
    assert _master(home, "show")["master"]["record_id"] not in listed.output, "no profile pins the master"  # type: ignore[index]


def test_the_stored_master_is_a_printable_resume_in_the_shipped_format(tmp_path: Path) -> None:
    from gigai.scout.master_store import load_master
    from gigai.scout.resume_pdf import parse_resume_markdown, render_markdown_pdf
    from gigai.scout.target_resolution import home_scout_target

    home = _setup(tmp_path)
    _master(home, "init", "--from", str(FIXTURE))
    stored = load_master(home_root=home, target=home_scout_target(home))
    assert stored is not None
    markdown = stored.master.markdown()

    assert markdown.splitlines()[0] == "<!-- gigai-master:1 -->"
    assert parse_master(markdown) == stored.master, "the stored form reads back as the same master"
    _name, sections = parse_resume_markdown(markdown)
    assert [section["heading"] for section in sections] == ["SUMMARY", "EXPERIENCE", "PROJECTS", "SKILLS", "EDUCATION", "OTHER"]
    assert "id:" not in json.dumps(sections), "the shipped parser drops the id comments"
    # The spike measured the synthetic master at 8 pages with the shipped template (DESIGN.md 3.1).
    assert render_markdown_pdf(markdown, None, timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc)).pages == 8
    assert "<!--" not in stored.master.markdown(ids=False)


# --- the format (pure) --------------------------------------------------------------------------


def test_the_format_reads_what_a_person_types() -> None:
    draft = draft_master(
        "# A title above the first section is never kept\n"
        "\n"
        "## Summary\n"
        "Platform engineer with 9 years\n"
        "of experience in payment systems. <!-- a note, not an id -->\n"
        "\n"
        "- A second variant. <!-- tags:backend,c++ -->\n"
        "\n"
        "## Experience\n"
        "### Example Corp\n"
        "Staff Engineer | Jun 2019 - Present\n"
        "* Cut deploy time from 40 minutes to 6\n"
        "  with a staged pipeline. <!-- id:b-deploy backed:story:deploy,answer:technical:ci -->\n"
        "\n"
        "## Other\n"
        "- Certification: Certified Kubernetes Administrator (2020)\n"
    )
    assigned = assign_ids(draft)
    master = build_master(draft)

    assert (assigned.assigned, assigned.restored) == (4, 0)
    summary = master.in_section("summary")
    assert [item.text for item in summary] == ["Platform engineer with 9 years of experience in payment systems.", "A second variant."]
    assert summary[1].tags == ("backend", "c++") and summary[0].kind == "summary"
    bullet = master.items["b-deploy"]
    assert bullet.text == "Cut deploy time from 40 minutes to 6 with a staged pipeline."
    assert bullet.backed == ("story:deploy", "answer:technical:ci") and bullet.strength == "backed"
    assert master.in_section("other")[0].kind == "other", "a certification is an Other line"
    assert parse_master(master.markdown()) == master
    assert "A title above" not in master.markdown()

    edited = master_resume.MasterItem(bullet.id, bullet.section, bullet.kind, bullet.text + " Twice.", bullet.tags, bullet.backed, bullet.entry_id, bullet.order)
    retagged = master_resume.MasterItem(bullet.id, bullet.section, bullet.kind, bullet.text, ("x",), bullet.backed, bullet.entry_id, bullet.order)
    assert edited.mark != bullet.mark and retagged.mark == bullet.mark, "the mark follows the id and the text"


@pytest.mark.parametrize(
    ("markdown", "code", "says"),
    [
        ("## Summary\n- One. <!-- id:a -->\n- Two. <!-- id:a -->\n", "master_markdown_invalid", "line 3: this id is already used on line 2"),
        ("## Summary\n- One. <!-- id:bad/id -->\n", "master_markdown_invalid", "line 2: an id holds"),
        ("## Summary\n- One. <!-- tags:a,,é -->\n", "master_markdown_invalid", "line 2: tags are"),
        ("## Summary\n- One. <!-- backed:essay:1 -->\n", "master_markdown_invalid", "line 2: backed names"),
        ("## Experience\n- A bullet before any entry.\n", "master_markdown_invalid", "line 2: start the entry"),
        ("## Experience\n### Co <!-- id:r tags:x -->\n", "master_markdown_invalid", "line 2: an entry heading takes an id only"),
        ("## Summary\n## Summary\n- One.\n", "master_markdown_invalid", "line 2: the Summary section appears twice"),
        ("## Summary\n- Mid <!-- note --> line.\n", "master_markdown_invalid", "line 2: a comment belongs at the end"),
        ("Just a paragraph with no section.\n", "master_markdown_invalid", "no resume content"),
        ("<!-- gigai-master:7 -->\n## Summary\n- One.\n", "master_format_unsupported", "line 1: this is master format 7"),
    ],
)
def test_the_format_refuses_by_line_number_and_rule(markdown: str, code: str, says: str) -> None:
    with pytest.raises(MasterResumeError) as refused:
        draft = draft_master(markdown)
        assign_ids(draft)
        build_master(draft)
    assert refused.value.code == code and says in str(refused.value)


def test_the_stored_form_must_carry_every_id() -> None:
    with pytest.raises(MasterResumeError, match="line 2: this line has no id"):
        parse_master("## Summary\n- A line the store would never write.\n")
