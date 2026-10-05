"""0.1.10.11 LK: a link in a heading goes, the heading keeps its name (the orchestrator's decision #260).

Before: ``### [Driftwatch](https://github.com/...)`` was "contact data (link)" to the import's detector.
``master init --from`` and ``master sync`` did not import the line at all, so the project lost its name and
its bullets went to the entry above; ``resume add`` stored ``### [Driftwatch](``.

The rule (``resume_privacy.heading_links``, ONE function for ``resume add``, ``master init`` from the
profiles' resumes or from a file, and ``master sync``): in a heading or a title line a markdown link keeps
its words and loses its address; an address written out goes and the rest of the line stays; a heading
that is only a link is refused by line number; the name and contact lines above the first section, and
an email or a phone number in a heading, are removed as they always were. No link is stored.

Through the real CLI on scratch homes. Every value is invented (``example.test``, ``555-01xx``).
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import time

from click.testing import CliRunner
import pytest

from gigai.canonical import canonical_json_bytes
from gigai.cli import cli
from gigai.private_records import read_record
from gigai.scout import resume_pii, resume_privacy
from gigai.scout.master_store import load_master, strip_contact
from gigai.scout.target_resolution import home_scout_target

from tests.support.latency import latency_bound
from tests.support.scout_profile_fixtures import default_find_jobs_config
from tests.support.setup_home import setup_home

FIXTURE = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "master" / "shapes" / "linked-headings.md"
#: What the fixture's header and its links hold. None of it may be stored.
PLANTED = ("example.test", "example-org", "jordan-example", "Jordan", "(555)", "010-0142", "linkedin", "github.com", "gitlab.com")
#: Any link at all, in text GigAI stored: a scheme, ``www.``, what is left of a markdown link, a known host.
ANY_LINK = re.compile(r"[a-z][a-z0-9+.-]*://|www\.|\]\(|\bmailto:|(?:github|gitlab|linkedin)\.com|example\.test", re.IGNORECASE)
ONLY_A_LINK = "this heading is only a link: give the project a name"

#: ``(section, heading, role lines, first year, last year, ongoing, bullets)``: every entry of the fixture, each with ITS bullets.
ENTRIES = [
    ("experience", "Quillmark Systems", ("Staff Platform Engineer | Mar 2020 - Present",), 2020, None, True, [
        "Runs the control plane for 3,200 customer clusters across three clouds at 99.98% availability.",
        "Cut the median deploy from 42 minutes to 7 by replacing the release scripts with a reconciler in Go.",
    ]),
    ("experience", "Pendle Works", ("Senior Software Engineer, Pendle platform group | Jun 2015 - Feb 2020",), 2015, 2020, False, [
        "Built the billing ledger in Python on PostgreSQL, handling 9 million entries a day.",
    ]),
    ("projects", "Driftwatch", (), None, None, False, [
        "Finds drift across 900 workspaces in under 4 minutes.",
        "Opens a reviewed pull request for each fix; 70% merge without an edit.",
    ]),
    ("projects", "Ledgerlens — an agent that explains billing anomalies", ("Python and Kafka | 2022 - 2023",), 2022, 2023, False, [
        "Explains 300 anomalies a week with the ledger rows as evidence.",
    ]),
    ("projects", "Kubelint — a manifest linter", (), None, None, False, ["Lints 4,000 manifests a day before they reach a cluster."]),
    # Any other dotted name is not a link to the rule: it is a project's name as often (kept, as it always was).
    ("projects", "Tracehound (tracehound.ai)", (), None, None, False, ["Samples 2% of traces and keeps every error."]),
    ("education", "Northfield State University", ("B.S. Computer Science | 2010 - 2014",), 2010, 2014, False, []),
]
#: ``(file line, the heading's words, where the link stood)`` for every link the rule took out of the fixture.
HEADINGS = [
    (10, "Quillmark Systems", "heading"), (17, "Pendle Works", "line_under_heading"), (23, "Driftwatch", "heading"),
    (27, "Ledgerlens — an agent that explains billing anomalies", "heading"), (32, "Kubelint — a manifest linter", "heading"),
    (44, "Northfield State University", "heading"),
]


def _rows(headings: list[tuple[int, str, str]]) -> list[dict[str, object]]:
    return [
        {
            "kind": "link", "line": line, "heading": words, "where": where,
            "message": f"link removed from {'a line under the heading' if where == 'line_under_heading' else 'the heading'} {words}",
        }
        for line, words, where in headings
    ]


def _home(tmp_path: Path, name: str = "home") -> Path:
    return setup_home(tmp_path / name, workpad_root=tmp_path / f"{name}-workpads")


def _run(home: Path, *args: str, ok: bool = True, as_json: bool = True) -> str:
    result = CliRunner().invoke(cli, ["scout", "resume", *args, "--home", str(home), *(["--json"] if as_json else [])])
    assert result.exit_code == (0 if ok else 1), result.output
    return result.output


def _json(home: Path, *args: str, ok: bool = True) -> dict:
    return json.loads(_run(home, *args, ok=ok).strip().splitlines()[-1])


def _stored(home: Path) -> bytes:
    """Everything GigAI holds for ``home``: every file of the home and of its workpads, and every object of the journals."""

    data = b""
    workpads = home.parent / f"{home.name}-workpads"
    for root in (home, workpads):
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink() and ".git" not in path.parts:
                data += path.read_bytes()
    for repo in sorted(path.parent for path in workpads.rglob(".git")):
        data += subprocess.run(["git", "-C", str(repo), "cat-file", "--batch-all-objects", "--batch"], capture_output=True, check=True).stdout
    return data


def _no_link_is_stored(home: Path, *texts: str) -> None:
    """No link in the stored ``texts``, and none of the fixture's contact values or link addresses anywhere GigAI stores."""

    for text in texts:
        assert ANY_LINK.findall(text) == [], text
    stored = _stored(home)
    assert [value for value in PLANTED if value.encode() in stored] == []


def _master_entries(home: Path) -> tuple[list, str]:
    stored = load_master(home_root=home, target=home_scout_target(home))
    assert stored is not None
    master = stored.master
    entries = [
        (entry.section, entry.heading, entry.sublines, entry.start, entry.end, entry.ongoing, [master.items[item].text for item in entry.bullets])
        for entry in master.entries.values()
    ]
    return entries, master.markdown()


# --- a linked heading keeps its name and its bullets, by every road ------------------------------


def test_master_init_from_keeps_a_linked_heading_and_its_bullets(tmp_path: Path) -> None:
    home = _home(tmp_path)

    dry = _json(home, "master", "init", "--from", str(FIXTURE), "--dry-run")
    done = _json(home, "master", "init", "--from", str(FIXTURE))

    assert (dry["status"], dry["written"], done["status"], done["written"]) == ("dry_run", False, "created", True)
    # The end of it: every entry has its name and ITS bullets (a linked heading used to lose its whole line).
    entries, markdown = _master_entries(home)
    assert entries == ENTRIES
    for answer in (dry, done):
        removed = answer["contact_removed"]
        # The name and the contact line above the first section went whole, as before; no heading did.
        assert removed["lines"] == [{"kind": "name", "line": 1}, {"kind": "email", "line": 3}, {"kind": "link", "line": 3}, {"kind": "phone", "line": 3}]
        assert removed["removed"] == {"name": 1, "email": 1, "link": 1, "phone": 1}
        # ...and each link taken out of a heading is said as a heading: its line, its words, never the address.
        assert removed["headings"] == _rows(HEADINGS)
        (read,) = answer["source_lines"]["resumes"]
        assert {row["reason"]: row["lines"] for row in read["left_out"]} == {"contact": [1, 3]}
        lines = answer["source_lines"]
        assert lines["in"] == lines["kept"] + lines["left_out"] and lines["left_out_by_reason"]["unread"] == 0
        assert ANY_LINK.findall(json.dumps(answer, ensure_ascii=False)) == []
    _no_link_is_stored(home, markdown, (home / "resumes" / "master.md").read_text(encoding="utf-8"))

    said = _run(_home(tmp_path, "text"), "master", "init", "--from", str(FIXTURE), "--dry-run", as_json=False)
    assert "Not imported: line 1: name, line 3: email, line 3: link, line 3: phone." in said
    assert (
        "Kept without its link: line 10: link removed from the heading Quillmark Systems; "
        "line 17: link removed from a line under the heading Pendle Works; line 23: link removed from the heading Driftwatch;"
    ) in said
    assert ANY_LINK.findall(said.replace(str(tmp_path), "")) == []


def test_resume_add_keeps_a_linked_heading_and_the_merge_reads_it(tmp_path: Path) -> None:
    home = _home(tmp_path)

    added = _json(home, "add", str(FIXTURE))

    # The end of it: the stored resume has every heading by its name, with the lines that were under it.
    resume = read_record(home_root=home, requested_target=home_scout_target(home), record_id=added["record_id"], content=True)["content"].decode("utf-8")
    for kept in (
        "### Quillmark Systems\n**Staff Platform Engineer** | Mar 2020 - Present\n",
        "### Pendle Works\nSenior Software Engineer, Pendle platform group | Jun 2015 - Feb 2020\n",
        "### Driftwatch\n- Finds drift across 900 workspaces in under 4 minutes.\n",
        "**Ledgerlens** — an agent that explains billing anomalies\nPython and Kafka | 2022 - 2023\n",
        "### Kubelint — a manifest linter\n- Lints 4,000 manifests a day before they reach a cluster.\n",
        "### Tracehound (tracehound.ai)\n",
        "### Northfield State University\nB.S. Computer Science | 2010 - 2014\n",
    ):
        assert kept in resume, kept
    removed = added["contact_removed"]
    assert removed["removed"] == {"name": 1, "email": 1, "phone": 1, "links": 7}  # a line that lost a link counts, as it always did
    assert removed["headings"] == _rows(HEADINGS)
    said = _run(_home(tmp_path, "text"), "add", str(FIXTURE), as_json=False)
    assert "Kept without its link: line 10: link removed from the heading Quillmark Systems;" in said and "github.com" not in said

    # The master made of the profile's resume holds the same entries, each with its bullets.
    (home_scout_target(home) / "find-jobs.json").write_bytes(canonical_json_bytes(default_find_jobs_config().to_json()))
    merged = _json(home, "master", "init")
    assert (merged["status"], merged["written"], merged["contact_removed"]) == ("created", True, None)
    entries, markdown = _master_entries(home)
    assert entries == ENTRIES
    _no_link_is_stored(home, resume, markdown)


MASTER = """<!-- gigai-master:1 -->

## Experience

### Example Corp <!-- id:r-example -->
Staff Engineer | Jun 2019 - Present
- Cut deploy time from 40 minutes to 6 with a staged pipeline. <!-- id:b-deploy -->

## Projects

### Kubelint <!-- id:p-kubelint -->
- Lints 4,000 manifests a day. <!-- id:b-lint -->
"""


def _synced_home(tmp_path: Path) -> tuple[Path, Path, str]:
    """A home whose master is ``MASTER`` at revision 1, its ``master.md`` and that file's text."""

    home = _home(tmp_path)
    source = tmp_path / "master-in.md"
    source.write_text(MASTER, encoding="utf-8")
    assert _json(home, "master", "init", "--from", str(source))["status"] == "created"
    file = home / "resumes" / "master.md"
    return home, file, file.read_text(encoding="utf-8")


def test_master_sync_keeps_a_linked_heading_and_still_refuses_a_link_in_any_other_line(tmp_path: Path) -> None:
    home, file, text = _synced_home(tmp_path)
    edited = (
        text.replace("### Kubelint <!--", "### [Kubelint](https://github.com/example-org/kubelint) <!--")
        .replace("Staff Engineer | Jun 2019", "Staff Engineer, [platform group](https://corp.example.test/platform) | Jun 2019")
        + "\n### [Driftwatch](https://github.com/example-org/driftwatch)\n- Finds drift across 900 workspaces in under 4 minutes.\n- Opens a reviewed pull request for each fix.\n"
    )
    assert edited != text and edited.count("](") == 3
    file.write_text(edited, encoding="utf-8")
    lines = edited.splitlines()
    at = {key: next(number for number, line in enumerate(lines, 1) if key in line) for key in ("[Kubelint]", "[platform group]", "[Driftwatch]")}

    done = _json(home, "master", "sync")

    assert (done["status"], done["written"], done["master"]["revision"]) == ("imported", True, 2)
    assert done["contact_removed"] == {
        "removed": {}, "lines": [], "message": None,
        "headings": _rows(sorted([
            (at["[Kubelint]"], "Kubelint", "heading"), (at["[platform group]"], "Example Corp", "line_under_heading"), (at["[Driftwatch]"], "Driftwatch", "heading"),
        ])),
    }
    entries, markdown = _master_entries(home)
    assert [(heading, sublines, bullets) for _section, heading, sublines, _start, _end, _ongoing, bullets in entries] == [
        ("Example Corp", ("Staff Engineer, platform group | Jun 2019 - Present",), ["Cut deploy time from 40 minutes to 6 with a staged pipeline."]),
        ("Kubelint", (), ["Lints 4,000 manifests a day."]),
        ("Driftwatch", (), ["Finds drift across 900 workspaces in under 4 minutes.", "Opens a reviewed pull request for each fix."]),
    ]
    # The entry whose heading only lost its link is the same entry (its id, its text): nothing about it changed.
    assert [line["id"] for line in done["added"] if line["what"] == "entry"] != [] and "p-kubelint" not in [line["id"] for line in done["changed"]]
    # GigAI wrote the file again in the stored form: the links are gone from it too.
    assert file.read_text(encoding="utf-8") == markdown
    _no_link_is_stored(home, markdown)
    said = _run(home, "master", "sync", as_json=False)
    assert "Nothing to import" in said

    # A link in a bullet is contact data to the import, as before: refused whole, by line number, nothing imported.
    with_bullet = markdown.replace("- Lints 4,000 manifests a day.", "- Lints 4,000 manifests a day, see [the board](https://corp.example.test/board).")
    file.write_text(with_bullet, encoding="utf-8")
    refused = _json(home, "master", "sync", ok=False)["error"]
    assert refused["code"] == "personal_info_refused" and "link" in refused["message"] and "Nothing was imported" in refused["message"]
    assert "example.test" not in json.dumps(refused) and file.read_text(encoding="utf-8") == with_bullet
    assert _json(home, "master", "show")["master"]["revision"] == 2


def test_the_text_of_sync_says_which_heading_lost_its_link(tmp_path: Path) -> None:
    home, file, text = _synced_home(tmp_path)
    edited = text.replace("### Kubelint <!--", "### [Kubelint](https://github.com/example-org/kubelint) — a linter <!--")
    file.write_text(edited, encoding="utf-8")
    line = next(number for number, held in enumerate(edited.splitlines(), 1) if "[Kubelint]" in held)

    said = _run(home, "master", "sync", as_json=False)

    assert f"Kept without its link: line {line}: link removed from the heading Kubelint — a linter." in said
    assert "github.com" not in said and "example-org" not in said


# --- a heading that is only a link is refused by line, never dropped ------------------------------

HEAD = "# Jordan Example\njordan.example@example.test | (555) 010-0142\n\n## Projects\n\n### Kubelint\n- Lints 4,000 manifests a day.\n\n"
ONLY_LINKS = (
    "### https://github.com/example-org/driftwatch",
    "### [https://github.com/example-org/driftwatch](https://github.com/example-org/driftwatch)",
    "### [github.com/example-org/driftwatch](https://github.com/example-org/driftwatch)",
    "### <https://driftwatch.example.test>",
    "### www.driftwatch.example.test",
    "### github.com/example-org/driftwatch",
    "#### [![logo](https://img.example.test/logo.png)](https://github.com/example-org/driftwatch)",
    "### GitHub: https://github.com/example-org/driftwatch",
    # A bold title right above bullets names an entry as a '###' line does.
    "**[github.com/example-org/driftwatch](https://github.com/example-org/driftwatch)**",
    # The words of a mail link, of a person's profile page and a handle are the link itself.
    "### [Write to me](mailto:jordan.example@example.test)",
    "### [Jordan Example](https://www.linkedin.com/in/jordan-example)",
    "### [jordan-example](https://github.com/jordan-example)",
    # A dotted name alone on the line: the detector takes such a line whole, so there would be no name either.
    "### driftwatch.dev",
)
BY_LINE = 9  # the heading's line in HEAD + heading


@pytest.mark.parametrize("heading", ONLY_LINKS)
def test_a_heading_that_is_only_a_link_is_refused_by_line(heading: str, tmp_path: Path) -> None:
    source = tmp_path / "resume.md"
    source.write_text(f"{HEAD}{heading}\n- Finds drift across 900 workspaces in under 4 minutes.\n", encoding="utf-8")
    assert source.read_text(encoding="utf-8").splitlines()[BY_LINE - 1] == heading
    message = f"line {BY_LINE}: {ONLY_A_LINK}"

    # master init --from, as a dry run and as a write: refused by line; the terminal shows no text of the line.
    home = _home(tmp_path)
    for extra in (("--dry-run",), ()):
        error = _json(home, "master", "init", "--from", str(source), *extra, ok=False)["error"]
        assert (error["code"], error["message"], error["line"]) == ("master_markdown_invalid", message, BY_LINE)
    said = _run(home, "master", "init", "--from", str(source), ok=False, as_json=False)
    assert message in said and "reads:" not in said and ANY_LINK.findall(said.replace(str(tmp_path), "")) == []
    assert load_master(home_root=home, target=home_scout_target(home)) is None

    # resume add: the same refusal; nothing is imported.
    error = _json(home, "add", str(source), ok=False)["error"]
    assert error["code"] == "resume_heading_only_link" and error["message"].startswith(message) and "Nothing was imported" in error["message"]
    assert ANY_LINK.findall(error["message"]) == []
    stored = _stored(home)
    assert [value for value in ("Finds drift", "Lints 4,000", "driftwatch", *PLANTED) if value.encode() in stored] == []


def test_master_sync_refuses_a_heading_that_is_only_a_link_and_changes_nothing(tmp_path: Path) -> None:
    home, file, text = _synced_home(tmp_path)
    edited = text + "\n### https://github.com/example-org/driftwatch\n- Finds drift across 900 workspaces in under 4 minutes.\n"
    file.write_text(edited, encoding="utf-8")
    line = edited.splitlines().index("### https://github.com/example-org/driftwatch") + 1

    error = _json(home, "master", "sync", ok=False)["error"]

    assert (error["code"], error["message"], error["line"]) == ("master_markdown_invalid", f"line {line}: {ONLY_A_LINK}", line)
    assert file.read_text(encoding="utf-8") == edited and _json(home, "master", "show")["master"]["revision"] == 1
    assert b"Finds drift" not in _stored(home).replace(edited.encode(), b"")


def test_a_profiles_resume_whose_heading_is_only_a_link_stops_the_merge_by_line() -> None:
    # `resume add` refuses such a resume now; one stored earlier names the profile and the line, never the text.
    with pytest.raises(Exception) as refused:
        strip_contact("## Projects\n\n### https://github.com/example-org/driftwatch\n- Finds drift.\n")
    assert (getattr(refused.value, "code", None), str(refused.value)) == ("master_markdown_invalid", f"line 3: {ONLY_A_LINK}")
    # What `resume add` stored of a linked heading before 0.1.10.11 (the words, and a bracket where the address was)
    # is read as the words: nothing is removed now, so nothing is said.
    clean, gone = strip_contact("## Projects\n\n### [Driftwatch]( — a drift detector\n- Finds drift.\n")
    assert clean == "## Projects\n\n### Driftwatch — a drift detector\n- Finds drift.\n" and (gone.lines, gone.headings) == ((), ())


def test_a_heading_with_no_link_is_not_the_rules(tmp_path: Path) -> None:
    # A heading that is one word the detector knows as a contact label ('### GitHub', an employer) held no link: the
    # rule neither changes nor refuses it. (The strip takes such a line whole, as it did: a limit of the detector.)
    text = "## Experience\n\n### GitHub\nStaff Engineer | 2019 - 2023\n- Ran the merge queue.\n\n### Blog\n- Wrote 40 posts.\n"
    assert resume_privacy.heading_links(text) == (text, ())
    assert strip_contact(text) == strip_contact(text, headings=False)
    assert resume_pii.strip_contact_lines(text, headings=True) == resume_pii.strip_contact_lines(text)


# --- what was removed whole is still removed whole ------------------------------------------------


def test_the_name_and_contact_lines_above_the_first_section_are_removed_whole_as_before(tmp_path: Path) -> None:
    # A header written with links and headings: the name as a link, a contact row as a '###' line and as a bold line.
    header = (
        "# [Jordan Example](https://jordan.example.test)\n"
        "### [LinkedIn](https://linkedin.com/in/jordan-example) | [GitHub](https://github.com/jordan-example)\n"
        "**jordan.example@example.test** | (555) 010-0142\n\n"
    )
    body = "## Projects\n\n### [Driftwatch](https://github.com/example-org/driftwatch)\n- Finds drift across 900 workspaces in under 4 minutes.\n"
    source = tmp_path / "resume.md"
    source.write_text(header + body, encoding="utf-8")
    # The rule does not touch a line above the first section: the strip sees them as it always did.
    rewritten, found = resume_privacy.heading_links(header + body)
    assert rewritten.splitlines()[:3] == header.splitlines()[:3] and [(link.line, link.head) for link in found] == [(7, 7)]

    home = _home(tmp_path)
    done = _json(home, "master", "init", "--from", str(source))
    removed = done["contact_removed"]
    assert {row["line"] for row in removed["lines"]} == {1, 2, 3} and removed["headings"] == _rows([(7, "Driftwatch", "heading")])
    entries, markdown = _master_entries(home)
    assert [(heading, bullets) for _s, heading, _l, _a, _b, _o, bullets in entries] == [("Driftwatch", ["Finds drift across 900 workspaces in under 4 minutes."])]
    _no_link_is_stored(home, markdown)
    assert [word for word in ("LinkedIn", "GitHub", "Jordan") if word in markdown] == []

    added_home = _home(tmp_path, "added")
    added = _json(added_home, "add", str(source))
    resume = read_record(home_root=added_home, requested_target=home_scout_target(added_home), record_id=added["record_id"], content=True)["content"].decode("utf-8")
    assert resume == "## Projects\n\n### Driftwatch\n- Finds drift across 900 workspaces in under 4 minutes.\n"
    # ...which is what the strip, as it was, makes of the same header above a heading that never had a link.
    assert resume == resume_pii.strip_contact_lines(header + body.replace("[Driftwatch](https://github.com/example-org/driftwatch)", "Driftwatch")).text + "\n"
    _no_link_is_stored(added_home, resume)


def test_an_email_or_a_phone_number_in_a_heading_is_removed_as_before(tmp_path: Path) -> None:
    text = (
        "## Projects\n\n"
        "### Kubelint\n- Lints 4,000 manifests a day.\n\n"
        "### Driftwatch, ask jordan.example@example.test\n- Finds drift across 900 workspaces.\n\n"
        "### Ledgerlens (555) 010-0142\n- Explains 300 anomalies a week.\n\n"
        "### [Tracehound](https://github.com/example-org/tracehound) (555) 010-0142\n- Samples 2% of traces.\n"
    )
    # No link, so nothing for the rule: the two imports do with these lines exactly what they did.
    plain = text.split("### [Tracehound]")[0]
    assert resume_privacy.heading_links(plain) == (plain, ())
    assert resume_pii.strip_contact_lines(plain, headings=True) == resume_pii.strip_contact_lines(plain)

    # The master: a heading that holds an email or a phone number is not imported, by line number and kind. Where it
    # also held a link, the line still goes whole and is counted as before (link and phone), not said as a kept heading.
    clean, gone = strip_contact(text)
    assert gone.lines == (("email", 6), ("phone", 9), ("link", 12), ("phone", 12)) and gone.headings == ()
    assert [line for line in clean.splitlines() if line.startswith("###")] == ["### Kubelint"]
    source = tmp_path / "resume.md"
    source.write_text(text, encoding="utf-8")
    home = _home(tmp_path)
    done = _json(home, "master", "init", "--from", str(source))
    assert done["contact_removed"]["lines"] == [{"kind": "email", "line": 6}, {"kind": "phone", "line": 9}, {"kind": "link", "line": 12}, {"kind": "phone", "line": 12}]
    assert "headings" not in done["contact_removed"]
    entries, markdown = _master_entries(home)
    assert [heading for _s, heading, *_rest in entries] == ["Kubelint"]
    _no_link_is_stored(home, markdown)

    # resume add: the email and the phone number go from the line and the line stays, as before; so does the link now.
    added_home = _home(tmp_path, "added")
    added = _json(added_home, "add", str(source))
    assert added["contact_removed"]["removed"] == {"email": 1, "phone": 2, "links": 1}
    assert added["contact_removed"]["headings"] == _rows([(12, "Tracehound", "heading")])
    resume = read_record(home_root=added_home, requested_target=home_scout_target(added_home), record_id=added["record_id"], content=True)["content"].decode("utf-8")
    assert [line for line in resume.splitlines() if line.startswith("###")] == ["### Kubelint", "### Driftwatch, ask", "### Ledgerlens", "### Tracehound"]
    _no_link_is_stored(added_home, resume)


# --- the rule, line by line ----------------------------------------------------------------------

RULE = (
    # A markdown link keeps its words.
    ("### [Driftwatch](https://github.com/example-org/driftwatch)", "### Driftwatch"),
    ("### [Driftwatch](https://github.com/example-org/driftwatch) — a drift detector", "### Driftwatch — a drift detector"),
    ("### [Driftwatch](<https://driftwatch.example.test> \"the site\") <!-- id:p-drift -->", "### Driftwatch <!-- id:p-drift -->"),
    ("#### Staff Engineer at [Quillmark](https://quillmark.example.test), 2020 - Present", "#### Staff Engineer at Quillmark, 2020 - Present"),
    ("**[Ledgerlens](https://gitlab.com/example-org/ledgerlens)** — explains anomalies", "**Ledgerlens** — explains anomalies"),
    ("[**Ledgerlens**](https://gitlab.com/example-org/ledgerlens) — explains anomalies", "**Ledgerlens** — explains anomalies"),
    ("*Staff Engineer* | [team page](https://corp.example.test/team) | 2021 - Present", "*Staff Engineer* | team page | 2021 - Present"),
    ("### Driftwatch ([source](https://github.com/example-org/driftwatch), [demo](https://demo.example.test))", "### Driftwatch (source, demo)"),
    ("### [Driftwatch](https://example.test/wiki/Drift_(tool)) for Terraform", "### Driftwatch for Terraform"),
    # An address written out goes; what stood around it is tidied; the rest of the line stays.
    ("### Driftwatch (github.com/example-org/driftwatch)", "### Driftwatch"),
    ("### Driftwatch https://driftwatch.example.test", "### Driftwatch"),
    ("### Driftwatch — https://driftwatch.example.test", "### Driftwatch"),
    ("### Driftwatch | www.driftwatch.example.test | 2021 - 2023", "### Driftwatch | 2021 - 2023"),
    ("**Driftwatch — https://driftwatch.example.test**", "**Driftwatch**"),
    ("**Driftwatch** (https://driftwatch.example.test) | 2021", "**Driftwatch** | 2021"),
    # What an earlier import left of a link in a line it kept reads as the words.
    ("### [Driftwatch]( — a drift detector", "### Driftwatch — a drift detector"),
    # Any other dotted name is not a link here: the line is as it was.
    ("### Driftwatch (driftwatch.ai)", None),
    ("### Built on socket.io and node.js", None),
    # Not a heading or a title line: the strip's, as it was.
    ("- Built [the dashboard](https://corp.example.test/dash) in Go.", None),
    ("* Built [the dashboard](https://corp.example.test/dash) in Go.", None),
    ("1. Built [the dashboard](https://corp.example.test/dash) in Go.", None),
    ("Built [the dashboard](https://corp.example.test/dash) in Go, as a paragraph.", None),
    ("**Languages:** Go, [Python](https://python.example.test)", None),
    ("**Portfolio**: [jordan.example.test](https://jordan.example.test)", None),
    ("# [A title above the sections](https://example.test)", None),
    # An email or a phone number is not the rule's: the line reaches the strip with it.
    ("### Driftwatch, ask jordan.example@example.test", None),
    ("### [Driftwatch](https://github.com/example-org/driftwatch) (555) 010-0142", "### Driftwatch (555) 010-0142"),
)


@pytest.mark.parametrize(("line", "kept"), RULE)
def test_the_rule_line_by_line(line: str, kept: str | None) -> None:
    text = f"Jordan Example\n\n## Projects\n\n{line}\n- Finds drift across 900 workspaces.\n"

    rewritten, found = resume_privacy.heading_links(text)

    if kept is None:
        assert (rewritten, found) == (text, ())
        return
    assert rewritten == text.replace(line, kept)
    # A link that went is said by its line; what an earlier import left of one is only read as its words.
    assert [(link.line, link.head, link.under) for link in found] == ([] if "]( " in line else [(5, 5, False)])
    # Nothing of a link is left for the strip to find in the line, and the words are said without marks or comments.
    rest = kept.replace(" (555) 010-0142", "")
    assert ANY_LINK.findall(kept) == [] and resume_privacy.redact_inline(rest) == rest
    assert ANY_LINK.findall(resume_privacy.heading_words(kept)) == [] and "<!--" not in resume_privacy.heading_words(kept)


def test_the_lines_under_a_heading_are_its_role_lines_and_a_role_line_that_is_only_a_link_goes_whole() -> None:
    text = (
        "## Experience\n\n"
        "### Quillmark Systems\n"
        "Staff Engineer, [platform group](https://corp.example.test/platform) | 2021 - Present\n"
        "https://quillmark.example.test\n"
        "[quillmark](https://github.com/quillmark)\n"
        "[The fourth line](https://corp.example.test/four) is not a role line.\n"
        "- Runs the control plane.\n\n"
        "A paragraph with [a link](https://corp.example.test/p) after the bullets.\n"
    )

    rewritten, found = resume_privacy.heading_links(text)

    lines = rewritten.splitlines()
    assert lines[3] == "Staff Engineer, platform group | 2021 - Present" and [(link.line, link.head, link.under) for link in found] == [(4, 3, True)]
    # A line that is only an address, or only a link to a profile, reaches the strip as an address alone: it goes whole
    # (no refusal: it is not the entry's name), and nothing of the link's own words is left behind.
    assert lines[4:6] == ["https://quillmark.example.test", "https://github.com/quillmark"]
    assert lines[6] == text.splitlines()[6] and lines[9] == text.splitlines()[9]
    stored = resume_pii.strip_contact_lines(text, headings=True)
    assert "quillmark.example" not in stored.text and "github" not in stored.text and "### Quillmark Systems\nStaff Engineer, platform group | 2021 - Present\n" in stored.text
    clean, gone = strip_contact(text)
    assert [line for _kind, line in gone.lines] == [5, 6, 7, 10] and gone.headings == ((4, "Quillmark Systems", True),)
    assert ANY_LINK.findall(clean) == []


def test_a_link_line_under_a_project_goes_whole_and_is_counted_and_the_project_keeps_its_name() -> None:
    # The usual shape: the project's name as the heading, its links on the line under it.
    text = (
        "## Projects\n\n"
        "### Driftwatch\n[GitHub](https://github.com/example-org/driftwatch)\n- Finds drift.\n\n"
        "### Kubelint\n[GitHub](https://github.com/example-org/kubelint) | [Demo](https://demo.example.test)\n- Lints.\n\n"
        "### Ledgerlens\n[Write to me](mailto:jordan.example@example.test)\n- Explains.\n"
    )

    clean, gone = strip_contact(text)

    # A line that is nothing but a link is not imported, by line number (as before); a line with other words keeps them;
    # a mail link is the strip's, as before. Nothing is taken without being counted.
    assert gone.lines == (("link", 4), ("email", 12)) and gone.headings == ((8, "Kubelint", True),)
    assert clean == (
        "## Projects\n\n### Driftwatch\n\n- Finds drift.\n\n### Kubelint\nGitHub | Demo\n- Lints.\n\n### Ledgerlens\n\n- Explains.\n"
    )
    stored = resume_pii.strip_contact_lines(text, headings=True)
    assert stored.removed == {"email": 1, "links": 2} and stored.headings == ((8, "Kubelint", True),)
    assert "### Driftwatch\n- Finds drift.\n" in stored.text and "### Kubelint\nGitHub | Demo\n- Lints.\n" in stored.text
    assert [found for found in ANY_LINK.findall(stored.text) if found != "]("] == ["mailto:"]  # what the strip always left of a mail link


def test_a_contact_section_and_a_resume_without_sections_are_left_to_the_strip() -> None:
    contact = "## Experience\n\n### Quillmark\n- Runs it.\n\n## Contact\n\n### [jordan-example](https://github.com/jordan-example)\n**[Site](https://jordan.example.test)**\n"
    assert resume_privacy.heading_links(contact) == (contact, ())
    gone = strip_contact(contact)[1]
    assert {8, 9} <= {line for _kind, line in gone.lines} and gone.headings == ()
    unsectioned = "### [Driftwatch](https://github.com/example-org/driftwatch)\n- Finds drift.\n"
    assert resume_privacy.heading_links(unsectioned) == (unsectioned, ())


def test_line_numbers_and_line_endings_are_kept_and_text_without_such_a_link_comes_back_as_it_is() -> None:
    text = "## Projects\r\n\r\n### [Driftwatch](https://github.com/example-org/driftwatch)\r\n- Finds drift.\r\n\r\n  ### [Kubelint](https://github.com/example-org/kubelint)"
    rewritten, found = resume_privacy.heading_links(text)
    assert rewritten == "## Projects\r\n\r\n### Driftwatch\r\n- Finds drift.\r\n\r\n  ### Kubelint" and [link.line for link in found] == [3, 6]
    clean = FIXTURE.read_text(encoding="utf-8").split("## Skills")[1]
    assert resume_privacy.heading_links("## Skills" + clean.split("## Education")[0])[0] is not None
    untouched = "## Summary\nPlatform engineer.\n\n## Experience\n\n### Quillmark Systems\n**Staff Engineer** | 2021 - Present\n- Runs the control plane on socket.io.\n"
    assert resume_privacy.heading_links(untouched) == (untouched, ())
    assert resume_pii.strip_contact_lines(untouched, headings=True) == resume_pii.ContactStrip(untouched)


def test_a_change_to_the_stored_master_still_refuses_a_link_in_a_heading(tmp_path: Path) -> None:
    # `master edit` is not an import: the rule is not applied there, and a link is refused as it was.
    home, _file, _text = _synced_home(tmp_path)
    refused = _json(home, "master", "edit", "p-kubelint", "--heading", "[Kubelint](https://github.com/example-org/kubelint)", "--revision", "1", ok=False)["error"]
    assert refused["code"] in ("personal_info_refused", "master_contact_data") and "example-org" not in json.dumps(refused)
    assert _json(home, "master", "show")["master"]["revision"] == 1


@pytest.mark.parametrize("piece", ["[a](", "[", "](", "[a](b ", "[a](b \"", "https://", "<!-- ", "**", "| ", "[a](https://x.example.test) "])
def test_the_rule_reads_a_long_heading_and_a_long_file_in_linear_time(piece: str) -> None:
    def cost(text: str) -> float:
        started = time.process_time()
        try:
            resume_privacy.heading_links(text)
        except resume_privacy.HeadingOnlyLink:
            pass
        return time.process_time() - started

    # One heading at the longest length the rule reads, a file of such headings, and one line far beyond it.
    heading = "### " + (piece * 2000)[:1990]
    assert cost("## Projects\n" + heading + "\n") < latency_bound(0.5)
    assert cost("## Projects\n" + (heading + "\n\n") * 400) < latency_bound(8.0)
    assert cost("## Projects\n### " + piece * 100_000 + "\n") < latency_bound(1.0)
