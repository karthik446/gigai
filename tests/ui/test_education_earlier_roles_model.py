"""0.1.11.4 items 9 and 9d (packet H2): the page's rules for "no education" and for the Earlier experience block, without a browser.

`ui/src/masterModel.js`, `jobResumeModel.js` and `tailoredResumeModel.js` run under node on hand-made payloads in the
served shapes:

- the Master page says "Your master has no education" for a master with no Education entry, and never for one that has
  one or for no master; the migration lists the lines that look like education by the server's own sentence;
- the job page's resume card says it only when the server says the master holds none AND the stored resume prints
  none; a server that does not say (an older one) is never read as "none";
- a role with no bullet whose heading is a master line prints as ONE line under "Earlier experience", after the roles
  that show lines, and that line is the one the markdown and the PDF print (`tailored_resume.heading_only_line`);
- the pick conflict "earlier roles do not fit" reads as the pick's own sentence, else as the page's.

Not marked `ui`: no browser. LOUD skip without `node`. Every line below is invented.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.tailored_resume import EARLIER_HEADING, heading_only_line

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

SCRIPT = """
const [master, job, tailored] = await Promise.all(__URLS__.map((url) => import(url)));
const input = JSON.parse(process.argv[1]);
const out = {};
const entry = (section) => ({ id: "e-" + section, section, heading: "x", bullets: [] });
out.masterNotice = [
  master.masterHasNoEducation({ entries: [entry("experience"), entry("projects")] }),
  master.masterHasNoEducation({ entries: [entry("experience"), entry("education")] }),
  master.masterHasNoEducation({ entries: [] }),
  master.masterHasNoEducation(null),
];
out.words = [master.NO_EDUCATION_TEXT, master.ADD_EDUCATION_LABEL];
out.migration = [master.educationRows(input.migration), master.educationRows({ migration: { source_lines: { resumes: [{ profiles: ["A"], education: [] }, { profiles: ["B"] }] } } }), master.educationRows(null)];

const record = (master_education) => job.suggestionsAnswer(input.served, { ...input.view, master_education }).record;
out.flag = [record(false).master_education, record(true).master_education, record(null).master_education, job.suggestionsAnswer(input.served, input.view).record.master_education];
out.cardNotice = {
  noneAnywhere: job.needsEducationNotice(record(false), input.stored),
  masterHasIt: job.needsEducationNotice(record(true), input.stored),
  resumePrintsIt: job.needsEducationNotice(record(false), input.schooled),
  serverSilent: job.needsEducationNotice(job.suggestionsAnswer(input.served, input.view).record, input.stored),
  noMaster: job.needsEducationNotice(record(null), input.stored),
  noResume: job.needsEducationNotice(record(false), null),
  noRecord: job.needsEducationNotice(null, input.stored),
};
out.resumeHasEducation = [job.resumeHasEducation(input.stored), job.resumeHasEducation(input.schooled), job.resumeHasEducation(null)];

out.oneLine = input.headings.map((heading) => tailored.headingOnlyLine(heading));
out.heading = tailored.EARLIER_HEADING;
const rows = (result) => tailored.previewLines(result).filter((line) => line.kind !== "blank").map((line) => [line.kind, line.role || null, line.display, Boolean(line.earlier), Boolean(line.heading), (line.refs || []).map((ref) => ref.item_id || null), line.id || null]);
out.preview = rows(input.stored.result);
out.oldTailor = rows(input.oldTailor);
out.stats = tailored.previewStats(tailored.previewLines(input.stored.result)).total;
out.printed = [job.printedIds(input.stored), job.printedLineCount(input.stored)];

const attention = (conflicts) => job.attentionItems({ record: { ...record(false), selection: { max_pages: 2, conflicts } }, assessment: { matrix: [] } }).map((item) => [item.code, item.text, item.lines]);
out.conflict = [attention([input.conflict]), attention([{ code: "earlier_roles_do_not_fit" }]), attention([{ code: "some_new_code" }])];
console.log(JSON.stringify(out));
"""

ITEM = "r-000009"
MESSAGE = (
    "2 older roles are not listed on this resume, not even by a single heading line: there was no room left on 2 pages "
    "beside the lines your must-have requirements and pins rest on"
)
#: Heading lines as a master holds them: the employer, then the title and dates line.
HEADINGS = [
    ["### Pinecrest Agency", "Senior Full Stack Developer | Feb 2016 - Jan 2017"],
    ["**Harborview Systems**", "Software Developer | Jun 2012 - May 2015", "Portland, Oregon"],
    ["Quillon Labs"],
    ["Marlow & Finch | 2009 - 2011", "Analyst"],
    ["### Tidewater Group", "Engineer | Remote"],
    [],
]


def _copy(text: str, item: str | None = None, line: int = 1) -> dict:
    ref = {"kind": "resume", "line": line, "text": text}
    return {"kind": "copy", "text": text, "refs": [{**ref, "item_id": item} if item else ref]}


def _entry(heading: list[str], *bullets: str, item: str | None = None) -> dict:
    lines = [_copy(text, f"{item}-h{place}" if item and place else item, place + 1) for place, text in enumerate(heading)]
    return {"heading": lines, "bullets": [_copy(text, f"b-{place:06d}") for place, text in enumerate(bullets, 1)]}


def _result(*, education: bool = False, master: bool = True) -> dict:
    marks = ("r-000001", "r-000008", "r-000009") if master else (None, None, None)
    experience = [
        _entry(["### Northwind Labs", "Staff Engineer | 2019 - Present"], "Built the deploy pipeline for 40 services.", item=marks[0]),
        # Newer than the role below it, and with no line shown: listed in the block all the same, in the resume's order.
        _entry(HEADINGS[0], item=marks[1]),
        _entry(HEADINGS[1], item=marks[2]),
    ]
    sections = [
        {"heading": "summary", "lines": [_copy("Platform engineer with 11 years of experience.", "sum-000001")], "entries": []},
        {"heading": "experience", "lines": [], "entries": experience},
        {"heading": "skills", "lines": [_copy("Languages: Go, Python", "s-000001")], "entries": []},
    ]
    if education:
        sections.append({"heading": "education", "lines": [], "entries": [_entry(["### Lakeside University", "B.S. Computer Science | 2010"], item="r-000020")]})
    return {"header": [], "sections": sections}


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD SKIP: node is not on PATH; the education and Earlier experience rules of the page were NOT run")
    urls = [(UI_SRC / name).as_uri() for name in ("masterModel.js", "jobResumeModel.js", "tailoredResumeModel.js")]
    payload = json.dumps({
        "migration": {"migration": {"source_lines": {"resumes": [
            {"profiles": ["Staff Engineer"], "education": [
                {"first": 31, "last": 32, "section": "other", "message": "education: lines 31-32 of the resume of Staff Engineer were kept in Other, not as a degree"},
            ]},
            {"profiles": ["Platform Engineer"], "education": []},
        ]}}},
        "served": {"job_identity": "https://jobs.example.test/a", "profile_id": "profile_1", "gate": {"decision": "suggest", "ready": True, "reasons": []}, "suggestions": []},
        "view": {"gate": {"decision": "suggest", "ready": True, "reasons": []}, "basis": "master", "master_stored": True, "picked": None, "conflicts": [], "stale": []},
        "stored": {"result": _result()}, "schooled": {"result": _result(education=True)}, "oldTailor": _result(master=False),
        "headings": HEADINGS, "conflict": {"code": "earlier_roles_do_not_fit", "message": MESSAGE},
    })
    done = subprocess.run([node, "--input-type=module", "-e", SCRIPT.replace("__URLS__", json.dumps(urls)), payload], capture_output=True, text=True, check=False, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_master_page_says_no_education_only_for_a_master_without_a_school(out: dict) -> None:
    assert out["masterNotice"] == [True, False, True, False]
    assert out["words"] == ["Your master has no education.", "Add education"]


def test_the_migration_lists_the_lines_that_look_like_education_in_the_servers_words(out: dict) -> None:
    listed, none, missing = out["migration"]
    assert listed == [{"key": "0:31-32", "section": "other", "text": "education: lines 31-32 of the resume of Staff Engineer were kept in Other, not as a degree"}]
    assert none == [] and missing == []


def test_the_resume_card_says_it_only_when_neither_the_master_nor_the_resume_has_education(out: dict) -> None:
    assert out["flag"] == [False, True, None, None]
    assert out["resumeHasEducation"] == [False, True, False]
    assert out["cardNotice"] == {
        "noneAnywhere": True,
        # Never a nag: the master has it (this pick just shows none), the resume prints it, or nobody said.
        "masterHasIt": False, "resumePrintsIt": False, "serverSilent": False, "noMaster": False, "noResume": False, "noRecord": False,
    }


def test_a_role_with_no_line_shown_is_one_line_the_way_the_markdown_and_the_pdf_print_it(out: dict) -> None:
    assert out["heading"] == EARLIER_HEADING == "Earlier experience"
    assert out["oneLine"] == [heading_only_line(heading) for heading in HEADINGS]
    assert out["oneLine"][:2] == ["Senior Full Stack Developer, Pinecrest Agency | Feb 2016 - Jan 2017", "Software Developer, Harborview Systems | Jun 2012 - May 2015"]


def test_the_preview_lists_the_earlier_roles_in_one_block_after_the_roles_that_show_lines(out: dict) -> None:
    shown = [row[2] for row in out["preview"]]
    assert shown == [
        "## Summary", "- Platform engineer with 11 years of experience.",
        "## Experience", "### Northwind Labs", "Staff Engineer | 2019 - Present", "- Built the deploy pipeline for 40 services.",
        "### Earlier experience",
        "Senior Full Stack Developer, Pinecrest Agency | Feb 2016 - Jan 2017",
        "Software Developer, Harborview Systems | Jun 2012 - May 2015",
        "## Skills", "- Languages: Go, Python",
    ]
    block = [row for row in out["preview"] if row[3]]
    # The block's heading is the page's own (no source); each role line is a heading, cites the master lines it is made of and has no id to act on.
    assert [(row[0], row[1], row[4]) for row in block] == [("heading", None, False), ("copy", "text", True), ("copy", "text", True)]
    assert [row[5] for row in block[1:]] == [["r-000008", "r-000008-h1"], ["r-000009", "r-000009-h1", "r-000009-h2"]]
    assert all(row[6] is None for row in block)
    # Not content lines: the counts the page shows do not move.
    assert out["stats"] == 3 and out["printed"] == [["sum-000001", "b-000001", "s-000001"], 3]


def test_an_entry_with_no_bullet_in_a_resume_not_made_from_the_master_prints_in_its_place(out: dict) -> None:
    shown = [row[2] for row in out["oldTailor"]]
    assert "### Earlier experience" not in shown and not any(row[3] for row in out["oldTailor"])
    assert shown[shown.index("### Northwind Labs"):shown.index("## Skills")] == [
        "### Northwind Labs", "Staff Engineer | 2019 - Present", "- Built the deploy pipeline for 40 services.",
        "### Pinecrest Agency", "Senior Full Stack Developer | Feb 2016 - Jan 2017",
        "### Harborview Systems", "Software Developer | Jun 2012 - May 2015", "Portland, Oregon",
    ]


def test_a_stored_earlier_roles_conflict_is_no_longer_shown_as_needing_attention(out: dict) -> None:
    """0.1.11.5 item 1c: the conflict came from the page limit a pick no longer knows (every role is listed now, by its
    lines or its heading). A pick stored before that may still hold it; the page does not raise it. (Until 0.1.11.5 it
    read as the pick's own sentence, or "some earlier roles could not be listed on 2 pages".)"""

    with_message, without, unknown = out["conflict"]
    assert with_message == [] and without == []
    assert unknown == [["some_new_code", "some new code", []]], "a code the page has no sentence for still reads as its words"
