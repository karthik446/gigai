"""0.1.11.4 item 9 (H1): OLD ROLES MUST NOT VANISH.

The operator's case, on synthetic data: a career of six roles whose three oldest ended long ago.  Every line of
those three was cut for length, so the printed resume started at the fourth role and a career of 14 years read as 8.
The decision: a role none of whose lines is shown still prints, as ONE line (its title, its employer and its dates,
as the master resume has them) under "Earlier experience", at the end of the Experience section, newest first.

THE END OUTCOME, read from the job resume's markdown and from the PDF that comes back (pypdf reads its text):

- a pick that shows no line of an old role prints that role's heading line, in the markdown and in the PDF;
- the PDF with a header at its largest says its real pages (0.1.11.5 item 1c: the pick counts no page, so "the
  pick's pages" is no longer a thing to compare with);
- the heading lines are newest first, after the roles that show lines;
- a role with a line kept prints as before: its own heading, once, and no line in the block;
- a heading line is NEVER left out, whatever the cap of bullets (until 0.1.11.5 the page fit could cut one, oldest
  first, with an ``earlier_roles`` conflict: no pick makes that conflict now);
- no employer, title or date is invented: every part of a heading line is a part of the master's own entry (the
  selector's golden picks included), and the stored result holds the master's heading lines word for word, in the
  shape a reader of before this release already reads.

A fake model (the pipeline fixture's scripted one), one synthetic profile, an invented master. The pipeline is off.
"""

from __future__ import annotations

from datetime import date
import json
import logging
from pathlib import Path
import re

import pytest

from gigai.scout import assessment_core, postings
from gigai.scout import master_selection as ms
from gigai.scout.master_resume import Master, assign_ids, build_master, draft_master, parse_master
from gigai.scout.master_store import import_master
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.resume_pdf import ResumeMarkdownError, parse_resume_markdown, printed_text
from gigai.scout.tailor_length import restore_cut
from gigai.scout.tailored_resume import EARLIER_HEADING, TailorResponse, heading_only, heading_only_line, read_tailored_resume, render_markdown, tailored_resume_path
from gigai.scout.tailored_resume_edit import HandbackRefused, handback_result

from tests.behaviors.scout_find_jobs.test_assessment_v9_flow import V9_PARAGRAPHS
from tests.behaviors.scout_find_jobs.test_pick_header_room import (
    _JOB,
    _POSTING,
    _SLUG,
    _URL,
    KUBERNETES_LINE,
    PYTHON_LINE,
    REACT_LINE,
    SKILLS,
    TERRAFORM_LINE,
    _answer,
    _assess,
    _assess_under,
    _bullets,
    _cli_pdf,
    _no_layout_in_the_pick,
    _header_lines,
    _line,
    _ok,
    _pages,
    _Server,
    _view,
)
from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.pipeline_fixtures import build_pipeline_fixture
from tests.support.posting_fixtures import NOW, PostingsFixture, days_ago, lever_job

GOLDEN = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "master"

#: (employer, title, dates, lines).  The last three ended more than eight years ago: OLD roles.
ROLES = (
    ("Thistledown Market", "Staff Engineer", "Mar 2024 - Present", 14),
    ("Harborlight Health", "Staff Engineer", "Feb 2021 - Feb 2024", 14),
    ("Quillshire Freight", "Senior Engineer", "Feb 2017 - Jan 2021", 14),
    ("Pinecrest Agency", "Senior Full Stack Developer", "Feb 2016 - Jan 2017", 5),
    ("Border Ledger Office", "Full Stack Developer", "Jun 2015 - Jan 2016", 5),
    ("Fennimore Imaging", "Software Developer", "May 2012 - May 2015", 5),
)
OLD = ROLES[3:]
#: The lines the must-have rows rest on: all in the three recent roles, so no line of an old role is anything's evidence.
MUST = (PYTHON_LINE, KUBERNETES_LINE, TERRAFORM_LINE, REACT_LINE)
MUST_AT = {0: (PYTHON_LINE, REACT_LINE), 1: (KUBERNETES_LINE,), 2: (TERRAFORM_LINE,)}
#: A line of an old role a must-have row can rest on (the second test's): the second line of the middle old role, and short,
#: as a line an assessment quotes whole is.
OLD_MUST = "Shipped the React forms the border posts file their ledgers with."
HEADING_LINES = tuple(f"{title}, {employer} | {dates}" for employer, title, dates, _lines in OLD)


def master_markdown() -> str:
    out = ["## Summary", "", "- Engineer with 14+ years building storefronts, data platforms and the services behind them.", "", "## Experience", ""]
    for role, (employer, title, dates, lines) in enumerate(ROLES):
        must = MUST_AT.get(role, ())
        out += [f"### {employer}", f"{title} | {dates}", ""]
        out += [f"- {OLD_MUST if (role, line) == (4, 1) else _line(role, line)}" for line in range(lines - len(must))]
        out += [f"- {line}" for line in must] + [""]
    out += ["## Skills", "", f"- {SKILLS}", "", "## Education", "", "### Example State University", "BS Computer Science | 2008 - 2012", ""]
    return "\n".join(out)


MASTER = master_markdown()
RESUME = "## Experience\n\n### Thistledown Market\nStaff Engineer | Mar 2024 - Present\n\n- " + PYTHON_LINE + "\n\n## Skills\n\n- Python\n"
_COMMENT = re.compile(r"\s*<!--.*?-->")


@pytest.fixture(autouse=True)
def _pipeline_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PIPELINE_ENV, "off")


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> PostingsFixture:
    shipped = assessment_core.load_assess_instructions()
    for name in ("id_example", "note_example", "pick_lines", "requirements"):
        shipped = shipped.replace("{{" + name + "}}", "x")
    monkeypatch.setattr(assessment_core, "load_assess_instructions", lambda: shipped + V9_PARAGRAPHS)
    base = build_pipeline_fixture(tmp_path, monkeypatch, base=False, resume=RESUME)
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    fixture = PostingsFixture(base, second_profile_id="", deleted_profile_id=None)
    fixture.seed(_SLUG, [lever_job(_SLUG, 1, text=_POSTING)], seen_at=days_ago(1))
    postings.refresh(fixture.home_root, fixture.target, now=NOW)
    source = tmp_path / "master.md"
    source.write_text(MASTER, encoding="utf-8")
    assert import_master(home_root=fixture.home_root, target=fixture.target, source=source, gig_id=base.gig.resolved.gig_id).status == "created"
    caplog.set_level(logging.WARNING, logger="gigai.scout.server")
    return fixture


@pytest.fixture
def server(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch):
    made = _Server(fx, monkeypatch)
    yield made
    made.close()


def _plain(markdown: str) -> str:
    """The markdown without its source comments."""

    return "\n".join(_COMMENT.sub("", line) for line in markdown.splitlines())


def _block(markdown: str) -> list[str]:
    """The lines under "Earlier experience", as printed."""

    plain = _plain(markdown)
    if f"### {EARLIER_HEADING}" not in plain:
        return []
    return [line for line in plain.split(f"### {EARLIER_HEADING}", 1)[1].split("\n## ", 1)[0].splitlines() if line.strip()]


def _stored(fx: PostingsFixture) -> TailorResponse:
    stored = read_tailored_resume(tailored_resume_path(fx.home_root, fx.target, fx.default_profile_id, _JOB))
    assert stored is not None
    return stored


# --- the end outcome: the roles are on the page ------------------------------------------------------------------


def test_a_role_none_of_whose_lines_is_shown_still_prints_its_heading_in_the_markdown_and_the_pdf(
    fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server: _Server,
) -> None:
    _no_layout_in_the_pick(monkeypatch)
    _assess(fx, _answer(fx, must=MUST))
    view = _view(fx)
    markdown = view["resume"]["markdown"]
    # The pick held more than its 20 bullets, and EVERY line of the three old roles is past them (nothing rests on them).
    assert len(_bullets(markdown)) == ms.MAX_PICK_BULLETS and view["picked"]["picked_by"] == "model" and view["conflicts"] == []
    assert view["resume"]["counts"]["cut_for_length"] == 0 and view["picked"]["pages"] is None  # nothing is "cut for length", no page counted
    assert not any(f"Role {role} line" in markdown for role in (3, 4, 5)), "the fixture kept a line of an old role"
    assert all(line in markdown for line in MUST)

    # THE END OUTCOME, the job resume's markdown: each of the three is listed, one line each, newest first, at the
    # end of the Experience section (before: the resume ended at Quillshire Freight, 2017).
    assert _block(markdown) == list(HEADING_LINES), "an old role with no line left is not on the resume"
    experience = _plain(markdown).split("## Experience", 1)[1].split("\n## ", 1)[0]
    assert experience.index("### Quillshire Freight") < experience.index(f"### {EARLIER_HEADING}")
    assert "Feb 2016 - Jan 2017" in markdown and "May 2012 - May 2015" in markdown, "the dates do not reach back to the first role"
    # A role with a line kept prints as before: its own heading, once, and no line in the block.
    for employer, title, dates, _lines in ROLES[:3]:
        assert markdown.count(f"### {employer}") == 1 and markdown.count(employer) == 1 and f"{title} | {dates}" in markdown
    for employer, _title, _dates, _lines in OLD:
        assert f"### {employer}" not in markdown and markdown.count(employer) == 1, "an old role is printed twice, or under its own heading with no line"

    # THE END OUTCOME, the PDF with a header at its largest, at the automatic fit: it says its REAL pages (the pick
    # counted none), and the three roles are in its text, newest first.
    payload, pages = _cli_pdf(fx, tmp_path)
    text = "\n".join(pages)
    assert len(_header_lines(pages[0])) == 3, f"the header is not at its largest: {_header_lines(pages[0])}"
    assert len(pages) == payload["pages"] >= 2, f"the PDF with the header is {len(pages)} pages; it says {payload['pages']}"
    assert EARLIER_HEADING.upper() in text
    places = [text.index(f"{title}, {employer}") for employer, title, _dates, _lines in OLD]
    assert places == sorted(places) and text.index("QUILLSHIRE FREIGHT") < places[0] < text.index("SKILLS")
    assert all(dates in text for _employer, _title, dates, _lines in OLD)
    assert all(text.count(employer.upper()) + text.count(employer) == 1 for employer, _title, _dates, _lines in ROLES), "an employer is printed twice, or not at all"
    # ... and the page's Generate PDF, over HTTP, with the form: the same pages, the same roles.
    response = server.pdf()
    assert response.status_code == 200, response.text
    served = _pages(response.content)
    assert len(served) == len(pages) and all(f"{title}, {employer}" in "\n".join(served) for employer, title, _dates, _lines in OLD)
    # However many pages that is, the resume is ready: the page count is never a reason.
    assert view["gate"]["ready"] is True

    # THE STORED RESULT: each such role is an Experience entry with the master's own heading lines, word for word,
    # and no bullet. Nothing is invented, and the shape is the one a reader of before 0.1.11.4 reads: no new key.
    stored = _stored(fx)
    experience_section = next(section for section in stored.result.sections if section.heading == "experience")
    bare = heading_only(experience_section)
    assert [[line.text for line in entry.heading] for entry in bare] == [[f"### {employer}", f"{title} | {dates}"] for employer, title, dates, _lines in OLD]
    assert all(line.kind == "copy" and line.refs and line.refs[0].item_id for entry in bare for line in entry.heading)
    raw = stored.to_json()["result"]
    assert set(raw) <= {"schema_version", "header", "sections", "length"}
    assert all(set(entry) <= {"heading", "bullets", "dropped"} for section in raw["sections"] for entry in section.get("entries", ()))
    assert TailorResponse.from_json(json.loads(json.dumps(stored.to_json()))) == stored
    # Nothing was cut for length: no length record (so nothing to Restore), no cut and no conflict on the selection.
    assert stored.result.length is None and restore_cut(stored.result) == stored.result
    selection = stored.to_json()["selection"]
    assert selection["cut_for_length"] == [] and "conflicts" not in selection
    # The old roles' lines are under Left out, each with why: past an old role's 3 lines, or past the 20 bullets.
    master = _plain_master()
    old_lines = {item.id for item in master.items.values() if item.kind == "bullet" and (item.text.startswith(("Role 3 ", "Role 4 ", "Role 5 ")) or item.text == OLD_MUST)}
    left = {line["id"]: line["code"] for line in selection["left_out"]}
    assert len(old_lines) == 15 and {left[item_id] for item_id in old_lines} == {"old_role_limit", "over_cap"}
    assert sum(left[item_id] == "old_role_limit" for item_id in old_lines) == 6  # 5 lines each, at most 3 offered


def test_an_old_role_with_a_line_kept_prints_as_before_and_only_the_others_are_listed_by_their_heading(fx: PostingsFixture) -> None:
    """The must-have row rests on a line of the MIDDLE old role: that role keeps its own heading and its line."""

    _assess(fx, _answer(fx, must=(PYTHON_LINE, KUBERNETES_LINE, TERRAFORM_LINE, OLD_MUST)))
    markdown = _view(fx)["resume"]["markdown"]
    plain = _plain(markdown)

    assert OLD_MUST in markdown and "### Border Ledger Office\nFull Stack Developer | Jun 2015 - Jan 2016" in plain
    assert markdown.count("Border Ledger Office") == 1, "the role is printed under its heading AND listed in the block"
    # The other two, newest first; the block stands after every role that shows a line.
    assert _block(markdown) == [HEADING_LINES[0], HEADING_LINES[2]]
    assert plain.index("### Border Ledger Office") < plain.index(f"### {EARLIER_HEADING}")


def test_a_heading_line_is_never_left_out_however_small_the_cap(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """Until 0.1.11.5 a page with room for one heading line kept the newest and said "2 older roles are not listed"
    (``earlier_roles_do_not_fit``). A heading line is not a bullet: no cap leaves one out, and no pick makes that conflict."""

    _no_layout_in_the_pick(monkeypatch)
    _assess_under(fx, monkeypatch, len(MUST), answer=_answer(fx, must=MUST))  # room for the four must-have lines only
    view = _view(fx)
    markdown = view["resume"]["markdown"]

    assert _block(markdown) == list(HEADING_LINES), "a heading line was left out"
    assert sorted(_bullets(markdown)) == sorted(MUST), "a must-have line went, or another line stayed in its place"
    assert view["conflicts"] == [] and view["gate"]["ready"] is True
    stored = _stored(fx)
    assert "conflicts" not in stored.to_json()["selection"] and stored.result.length is None
    # A re-pick under the shipped cap lists all three still.
    again = _ok(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    assert _block(again["resume"]["markdown"]) == list(HEADING_LINES) and again["conflicts"] == [] and len(_bullets(again["resume"]["markdown"])) == 20


def _plain_master() -> Master:
    """The fixture's master as an import makes it of a plain resume: every line given its id."""

    draft = draft_master(MASTER)
    assign_ids(draft)
    return build_master(draft)


# --- the selector (a profile's own resume, the fallback): the same lines, under its cap -------------------------------


def _golden_posting(posting_id: str) -> ms.SelectionPosting:
    head, _, body = (GOLDEN / "postings" / f"{posting_id}.md").read_text(encoding="utf-8").partition("\n\n")
    meta = dict(line.split(": ", 1) for line in head.splitlines())
    return ms.SelectionPosting(meta["title"], body.strip() + "\n", meta["company"], meta.get("location", ""))


def test_the_code_selector_lists_a_role_it_shows_no_line_of_and_counts_no_page() -> None:
    """The selector's own selection (a profile's resume, the fallback), on the synthetic master of its golden picks."""

    master = parse_master((GOLDEN / "master.md").read_text(encoding="utf-8"))
    raw = next(item for item in json.loads((GOLDEN / "profiles.json").read_text(encoding="utf-8"))["profiles"] if item["profile_id"] == "profile-swe")
    profile = ms.SelectionProfile(tuple(raw["titles"]), tuple(raw["focus_tags"]), None, raw["profile_id"], raw["label"])
    selected = ms.select(master, profile, _golden_posting("p1-staff-ai-agent-platform"), today=date(2026, 10, 3))

    assert selected.selector_version == "sel-7" and selected.over_cap and selected.conflicts == ()
    assert (selected.pages, selected.pages_before_fit, selected.layout_queries, selected.cut_for_length) == (None, None, 0, ())
    assert sum(len(bullets) for entry_id, bullets in selected.entries.items() if master.entries[entry_id].section != "education") == ms.MAX_PICK_BULLETS
    bare = [master.entries[entry_id] for entry_id in selected.earlier]
    assert bare and all(entry.id not in selected.entries for entry in bare)
    # One line each, newest first.
    assert _block(selected.markdown) == [heading_only_line((entry.heading, *entry.sublines)) for entry in bare]
    assert [entry.end or 0 for entry in bare] == sorted((entry.end or 0 for entry in bare), reverse=True)
    # Every role of the master is on the page: under its own heading, or in the block.
    assert {entry.id for entry in master.entries_in("experience")} == {entry_id for entry_id in selected.entries if master.entries[entry_id].section == "experience"} | set(selected.earlier)
    # A stored selection keeps them (an entry id with no line of its own), and prints the same markdown again.
    assert set(selected.earlier) <= set(selected.stored_ids())
    assert ms.render_selection(master, selected.stored_ids(), selected.skills) == selected.markdown
    # A selection stored before this release names no such role: it prints as it did, with no block.
    before = tuple(item for item in selected.stored_ids() if item not in selected.earlier)
    assert EARLIER_HEADING not in ms.render_selection(master, before, selected.skills)


def test_the_code_selector_lists_every_role_however_small_its_cap() -> None:
    """Until ``sel-7`` a page with room for one heading line gave an ``earlier_roles`` conflict naming the two that went."""

    master = _plain_master()
    posting = ms.SelectionPosting("Staff Software Engineer, Fullstack", _POSTING, "Thistledown")
    for cap in (20, 6, 3):
        selected = ms.select(master, ms.SelectionProfile(), posting, today=date(2026, 10, 6), max_bullets=cap)
        shown = {entry_id for entry_id, bullets in selected.entries.items() if master.entries[entry_id].section == "experience" and bullets}
        assert shown | set(selected.earlier) == {entry.id for entry in master.entries_in("experience")} and not shown & set(selected.earlier), cap
        assert [conflict for conflict in selected.conflicts if conflict.kind in ("earlier_roles", "over_budget")] == [] and selected.cut_for_length == (), cap
        assert len(_block(selected.markdown)) == len(selected.earlier) and selected.to_json(master)["earlier"] == list(selected.earlier), cap
    # At a cap of one line a recent role, the three old roles are all listed by their heading, newest first.
    assert [master.entries[entry_id].heading for entry_id in selected.earlier] == ["Pinecrest Agency", "Border Ledger Office", "Fennimore Imaging"]


# --- the markdown reads back: the PDF of a file, and a hand-back -------------------------------------------------------


def _one_role_and_the_rest_by_heading(master: Master) -> str:
    first, *rest = sorted(master.entries_in("experience"), key=lambda entry: -(entry.end or 9999))
    return ms.render_selection(master, [first.id, first.bullets[0], *(entry.id for entry in rest)], master.skills())


def test_the_markdown_of_the_block_reads_back_and_prints_one_line_for_each_role() -> None:
    master = _plain_master()
    markdown = _one_role_and_the_rest_by_heading(master)
    listed = [f"{title}, {employer} | {dates}" for employer, title, dates, _lines in ROLES[1:]]
    assert _block(markdown) == listed, "five roles: more lines than an entry heading takes"

    _name, sections = parse_resume_markdown(markdown)
    experience = next(section for section in sections if section["heading"] == "EXPERIENCE")
    block = experience["entries"][-1]  # type: ignore[index]
    assert block["bullets"] == [] and block["heading"][0] == {"text": EARLIER_HEADING, "dates": ""}
    assert block["heading"][1:] == [{"text": f"{title}, {employer}", "dates": dates} for employer, title, dates, _lines in ROLES[1:]]
    assert all(line.rpartition(" | ")[0] in printed_text(markdown) for line in listed)
    # Any other entry is still held to its four heading lines.
    with pytest.raises(ResumeMarkdownError, match="an entry heading has at most 4 lines"):
        parse_resume_markdown("## Experience\n### Acme\na\nb\nc\nd\n")


def test_a_hand_back_that_keeps_the_block_is_accepted_and_a_line_that_is_no_role_of_the_master_is_refused() -> None:
    master = _plain_master()
    markdown = _one_role_and_the_rest_by_heading(master)

    result = handback_result(markdown, master=master, answers={})
    experience = next(section for section in result.sections if section.heading == "experience")
    # Each role of the block is stored as the master's own heading lines, with no bullet: nothing of the one line is kept as text.
    assert [[line.text for line in entry.heading] for entry in heading_only(experience)] == [[f"### {employer}", f"{title} | {dates}"] for employer, title, dates, _lines in ROLES[1:]]
    assert _block(render_markdown(result)) == _block(markdown)

    invented = markdown.replace("Senior Full Stack Developer, Pinecrest Agency", "Chief Architect, Pinecrest Agency")
    with pytest.raises(HandbackRefused) as refused:
        handback_result(invented, master=master, answers={})
    assert [problem.code for problem in refused.value.problems] == ["heading_not_master"]
    redated = markdown.replace("Feb 2016 - Jan 2017", "Feb 2014 - Jan 2017")
    with pytest.raises(HandbackRefused) as refused:
        handback_result(redated, master=master, answers={})
    assert [problem.code for problem in refused.value.problems] == ["heading_not_master"]


# --- nothing is invented ---------------------------------------------------------------------------------------------


def test_no_employer_title_or_date_of_a_heading_line_is_invented_in_any_golden_pick() -> None:
    master = parse_master((GOLDEN / "master.md").read_text(encoding="utf-8"))
    golden = json.loads((GOLDEN / "selection-golden.json").read_text(encoding="utf-8"))
    assert golden["selector_version"] == ms.SELECTOR_VERSION
    roles = {entry.id: entry for entry in master.entries_in("experience")}
    listed = 0
    for name, case in golden["cases"].items():
        shown = {entry_id for entry_id, bullets in case["entries"].items() if entry_id in roles and bullets}
        # Every role of the master is under its own heading or in the block: none is dropped, none is there twice.
        assert shown | set(case["earlier"]) == set(roles) and not shown & set(case["earlier"]), name
        ends = [roles[entry_id].end or 9999 for entry_id in case["earlier"]]
        assert ends == sorted(ends, reverse=True), f"{name}: not newest first"
        markdown = ms.render_selection(master, [*case["summary"], *(item for entry_id, bullets in case["entries"].items() for item in (entry_id, *bullets)), *case["earlier"], *case["other"]], case["skills"])
        lines = _block(markdown)
        assert len(lines) == len(case["earlier"])
        for entry_id, line in zip(case["earlier"], lines):
            entry = roles[entry_id]
            what, _, dates = line.rpartition(" | ")
            title, _, employer = what.rpartition(", ")
            own = (entry.heading, *entry.sublines)
            assert employer == entry.heading and any(sub == f"{title} | {dates}" for sub in entry.sublines), f"{name}: {line!r} is not {own!r}"
            listed += 1
        assert all(line in printed_text(markdown) for line in (line.rpartition(" | ")[0] for line in lines))
    assert listed, "no golden pick lists a role by its heading: this proves nothing"
