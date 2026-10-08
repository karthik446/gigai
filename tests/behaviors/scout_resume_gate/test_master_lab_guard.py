"""0.1.11.7 T2: the lab honesty guard (``master_lab``): a personal lab is never production.

The END outcomes:

* ``resume master add --lab --when "Oct 2026"`` adds a line that ends "(personal lab, Oct 2026)", backed ``lab:2026-10``;
  a lab line that claims production work ("Operated DNS infrastructure") is REFUSED, naming the word, nothing stored;
* ``--lab`` needs ``--when`` and a month; a skill added with ``--lab`` is listed ``NAME (lab)``;
* ``stated_check.settle_stated`` never settles a requirement row from a lab line;
* the assess prompt carries the lab rule only when the RESUME block shows a lab line (a master without one renders
  byte for byte as before: ``test_assessment_core`` pins it), and the rule lets a lab support "met" on familiarity only;
* ``resume brief`` marks a row that rests only on lab lines (JSON ``lab`` and a ``[lab]`` mark in the text);
* an old master and an old ``lines.json`` (no lab kind) load as they did.

Synthetic throughout (an invented master); every CLI test runs against a temp ``--home``. No model is called.
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pytest

from gigai.scout import job_brief, master_edit, stated_check
from gigai.scout.assessment_core import AssessContext, AssessJob, render_assess_prompt
from gigai.scout.master_lab import LabError, parse_when, production_word
from gigai.scout.master_resume import parse_master
from gigai.scout.target_resolution import home_scout_target

from tests.behaviors.scout_resume_gate.test_master_edit import SMALL, _items, _master, _setup, _stored

LAB_LINE = "Built a private DNS hierarchy in Docker and traced delegation with dig"
TODAY = date(2026, 10, 8)


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    made = _setup(tmp_path)
    source = tmp_path / "master-in.md"
    source.write_text(SMALL, encoding="utf-8")
    assert _master(made, "init", "--from", str(source))["status"] == "created"
    return made


def _lab(home: Path, text: str, *extra: str, ok: bool = True) -> dict:
    return _master(home, "add", "--entry", "r-example", "--text", text, *extra, ok=ok)


def _revision(home: Path) -> int:
    return _stored(home).revision.revision


# --- acceptance 1: a production word is refused --------------------------------------------------


def test_a_lab_line_that_says_operated_is_refused_naming_the_word_and_nothing_is_stored(home: Path) -> None:
    before = _revision(home)
    refused = _lab(home, "Operated DNS infrastructure", "--lab", "--when", "Oct 2026", ok=False)
    assert refused["error"]["code"] == "master_lab_production"
    assert '"operated"' in refused["error"]["message"] and "DNS infrastructure" not in refused["error"]["message"]
    assert _revision(home) == before
    assert not [item for item in _items(home).values() if "DNS" in item["text"]]


@pytest.mark.parametrize(
    ("text", "word"),
    [
        ("Owned the DNS zone files", "owned"),
        ("Ran the resolver in production", "production"),
        ("Built a resolver that handled traffic at scale", "at scale"),
        ("Led the lab build", "led"),
        ("Spent 3 years on a resolver", "years"),
        ("Built a resolver with a team of 4", "a team size"),
        ("Built a resolver for customers", "customers"),
    ],
)
def test_every_production_word_of_the_rule_is_refused_not_warned(home: Path, text: str, word: str) -> None:
    refused = _lab(home, text, "--lab", "--when", "Oct 2026", ok=False)
    assert refused["error"]["code"] == "master_lab_production" and f'"{word}"' in refused["error"]["message"]


def test_the_same_words_are_fine_without_lab_and_the_label_itself_is_not_a_production_word(home: Path) -> None:
    assert _lab(home, "Operated DNS infrastructure for the office network")["status"] == "revised"
    assert production_word("Built a resolver (personal lab, Oct 2026)") is None
    assert production_word("Built a resolver with dig +trace and a packet capture") is None


def test_a_lab_link_set_by_name_gets_the_same_guard(home: Path) -> None:
    refused = _lab(home, "Operated DNS infrastructure (personal lab, Oct 2026)", "--tag", "dns", ok=False)  # label typed by hand, no --lab
    assert refused["error"]["code"] == "master_lab_production"
    unlabelled = _master(home, "add", "--entry", "r-example", "--text", "Built a resolver", "--revision", str(_revision(home)), ok=True)
    edit = _master(home, "edit", unlabelled["items"][0]["id"], "--revision", str(unlabelled["master"]["revision"]), "--text", "Operated a resolver (personal lab, Oct 2026)", ok=False)
    assert edit["error"]["code"] == "master_lab_production"


# --- --when ---------------------------------------------------------------------------------------


def test_lab_needs_a_month_and_when_needs_lab(home: Path) -> None:
    before = _revision(home)
    for extra in (("--lab",), ("--lab", "--when", "next spring"), ("--lab", "--when", "2026-13"), ("--when", "Oct 2026")):
        refused = _lab(home, LAB_LINE, *extra, ok=False)
        assert refused["error"]["code"] == "master_lab_invalid", extra
    assert "Oct 2026" in _lab(home, LAB_LINE, "--lab", ok=False)["error"]["message"]
    assert _revision(home) == before


@pytest.mark.parametrize("value", ["Oct 2026", "October 2026", "oct. 2026", "2026-10", "10/2026"])
def test_when_is_a_month_and_a_year(value: str) -> None:
    assert parse_when(value) == (2026, 10)


def test_an_unparsable_when_is_a_sentence() -> None:
    with pytest.raises(LabError, match="not a month and a year"):
        parse_when("sometime")


# --- acceptance 4: the round trip ------------------------------------------------------------------


def test_a_lab_line_round_trips_through_add_show_and_the_master_file(home: Path) -> None:
    added = _lab(home, LAB_LINE + ".", "--lab", "--when", "Oct 2026")
    item = added["items"][0]
    assert item["text"] == f"{LAB_LINE} (personal lab, Oct 2026)"
    assert item["backed"] == ["lab:2026-10"] and item["strength"] == "stated"  # a lab is not evidence: never "backed"
    shown = _items(home)[item["id"]]
    assert shown["backed"] == ["lab:2026-10"] and shown["text"].endswith("(personal lab, Oct 2026)")
    master = _stored(home).master
    assert master.items[item["id"]].lab is True and master.items["b-deploy"].lab is False
    assert f"backed:lab:2026-10" in master.markdown()
    assert parse_master(master.markdown()).items[item["id"]].backed == ("lab:2026-10",)  # the file reads back


def test_a_skill_added_with_lab_is_listed_with_the_mark_and_its_stamp_carries_the_lab(home: Path) -> None:
    added = _master(home, "add", "--skill", "DNS", "--to", "s-cloud", "--lab", "--when", "Oct 2026")
    assert added["skills"]["added"] == ["DNS (lab)"]
    line = _items(home)["s-cloud"]
    assert "DNS (lab)" in line["text"] and line["backed"] == []
    assert [(s["name"], s["backed"]) for s in line["skill_sources"]] == [("DNS (lab)", "lab:2026-10")]
    # A real skill of the same name is a different thing: it is not blocked by the lab one, and a lab one is not added over a real one.
    assert _master(home, "add", "--skill", "Docker", "--to", "s-cloud", "--lab", "--when", "Oct 2026")["skills"]["already_listed"] == ["Docker"]


def test_an_old_master_and_an_old_lines_file_still_load(home: Path) -> None:
    master = _stored(home).master
    assert not any(item.lab for item in master.items.values()) and master.items["b-guide"].strength == "backed"
    lines = home_scout_target(home)
    found = list(home.rglob("lines.json"))
    for path in found:  # an old file: the schema and entries as 0.1.11.6 wrote them, no lab anywhere
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["schema_version"] == "scout-master-lines:1"
    assert lines is not None
    added = _lab(home, LAB_LINE, "--lab", "--when", "Oct 2026")  # a write on top of the old file is additive
    assert added["status"] == "revised"
    for path in home.rglob("lines.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == "scout-master-lines:1"
    assert master_edit.provenance(home_root=home, target=home_scout_target(home), master=_stored(home).master)


# --- acceptance 2: settle_stated -------------------------------------------------------------------

ONLY_LAB = f"""## Experience

### Example Corp <!-- id:r-example -->
Engineer | Jun 2019 - Present

- Deployed the billing export on Kubernetes. <!-- id:b-export -->
- {LAB_LINE} (personal lab, Oct 2026) <!-- id:b-dns -->

## Skills

- Infra: Linux, DNS (lab) <!-- id:s-infra -->
"""
REAL = ONLY_LAB.replace(f"- {LAB_LINE} (personal lab, Oct 2026) <!-- id:b-dns -->", "- Ran the DNS resolvers for the office network. <!-- id:b-dns -->").replace("DNS (lab)", "Postfix")


def _row(requirement: str) -> dict[str, object]:
    return {"requirement": requirement, "class": "askable", "class_basis": "Requirements", "status": "unclear", "resume_evidence": ["Not shown."]}


def _question(requirement: str) -> dict[str, object]:
    return {"question_id": "tooling:dns", "question": "Do you?", "requirement": requirement}


@pytest.mark.parametrize("requirement", ["Hands-on experience with DNS", "Experience with DNS", "DNS"])
def test_settle_stated_leaves_a_row_unclear_when_only_lab_lines_name_it(requirement: str) -> None:
    facts = stated_check.read_master(ONLY_LAB, today=TODAY)
    assert [line.id for line in facts.lines] == ["b-export"] and "dns" not in facts.skill_names and "lab" not in facts.skill_names
    rows, questions = [_row(requirement)], [_question(requirement)]
    kept, dropped, settled = stated_check.settle_stated(rows, questions, facts)
    assert rows[0]["status"] == "unclear" and "sources" not in rows[0] and settled == [] and dropped == [] and kept == questions
    # The same master with a real line (the control) is settled: the lab, not the check, is what changed.
    real_rows = [_row(requirement)]
    stated_check.settle_stated(real_rows, [_question(requirement)], stated_check.read_master(REAL, today=TODAY))
    assert real_rows[0]["status"] == "met" and real_rows[0]["sources"] == ["b-dns"]


def test_a_skills_line_keeps_its_real_skills_when_a_lab_skill_is_dropped() -> None:
    facts = stated_check.read_master(ONLY_LAB.replace("Linux, DNS (lab)", "Linux, DNS (lab), Go"), today=TODAY)
    assert [line.text for line in facts.skills] == ["Infra: Linux, Go"] and {"linux", "go"} <= facts.skill_names and "dns" not in facts.skill_names


# --- acceptance 3: the assess prompt ---------------------------------------------------------------


def _prompt(resume: str) -> str:
    job = AssessJob(title="Infra Engineer", company="Example Co", location="Remote", posting_text="Hands-on DNS. 3+ years operating DNS.")
    return render_assess_prompt(job, AssessContext(resume_text=resume, visa_sponsorship_required=False, resume_ids=("b-export",)))


def test_the_prompt_carries_the_lab_rule_only_with_a_lab_line_and_the_rule_limits_met_to_familiarity() -> None:
    plain = _prompt(REAL)
    assert "PERSONAL LAB LINES" not in plain  # a master with no lab renders as before
    with_lab = _prompt(ONLY_LAB)
    paragraph = next(block for block in with_lab.split("\n\n") if block.startswith("PERSONAL LAB LINES"))
    assert '"(personal lab, Oct 2026)"' in paragraph and "{{" not in paragraph
    # familiarity / hands-on wording may be met; years, scale, production, owning, leading, a team or customers may not
    assert "familiarity, hands-on exposure or a named tool" in paragraph
    assert 'never supports "met" on a requirement that states years, scale, production, operating, owning, leading, a team or customers' in paragraph
    assert "not in production; lab <month year>; closest real work" in paragraph and "unclear" in paragraph
    # and a lab skill alone brings the rule too
    assert "PERSONAL LAB LINES" in _prompt(REAL.replace("Postfix", "DNS (lab)"))


# --- the brief ---------------------------------------------------------------------------------------


def _yours(rows: tuple[job_brief.RowIds, ...]) -> dict[str, object]:
    master = parse_master(
        "<!-- gigai-master:1 -->\n\n## Experience\n\n### Example Corp <!-- id:r-example -->\nEngineer | Jun 2019 - Present\n"
        "- Deployed the billing export on Kubernetes. <!-- id:b-export -->\n"
        f"- {LAB_LINE} (personal lab, Oct 2026) <!-- id:b-dns backed:lab:2026-10 -->\n"
        f"- Built a toy zone signer (personal lab, Oct 2026) <!-- id:b-sign backed:lab:2026-10 -->\n"
    )
    inputs = job_brief.YoursInputs(job_identity="job-1", profile_id="p-1", rows=job_brief._with_lab(rows, master), master=master, master_revision=1)  # noqa: SLF001
    return job_brief.yours_part(inputs)


def test_a_row_backed_only_by_lab_lines_shows_the_lab_mark_in_the_json_and_the_text() -> None:
    part = _yours((
        job_brief.RowIds("req-lab", "askable", "met", ("b-dns", "b-sign")),
        job_brief.RowIds("req-mixed", "askable", "met", ("b-dns", "b-export")),
        job_brief.RowIds("req-answer", "askable", "met", ("b-dns", "A tooling:dns")),
        job_brief.RowIds("req-real", "askable", "met", ("b-export",)),
        job_brief.RowIds("req-none", "hard", "unclear"),
    ))
    assert {row["id"]: row["lab"] for row in part["requirements"]} == {  # type: ignore[union-attr]
        "req-lab": True, "req-mixed": False, "req-answer": False, "req-real": False, "req-none": False,
    }
    text = job_brief.render(part)
    marked = [line for line in text.splitlines() if "[lab:" in line]
    assert len(marked) == 1 and marked[0].startswith("req-lab")
    lines = {line["id"]: line for line in part["master"]["lines"]}  # type: ignore[index]
    assert (lines["b-dns"]["lab"], lines["b-export"]["lab"]) == (True, False) and lines["b-dns"]["strength"] == "stated"
    assert any("b-dns [s] [lab]" in line for line in text.splitlines())
