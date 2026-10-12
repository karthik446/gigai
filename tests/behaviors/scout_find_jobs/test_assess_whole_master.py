"""0.1.11.10 fix B: a requirement whose evidence stands anywhere in the master is judged on that evidence.

THE SYMPTOM.  A master of 48 bullets was shown to the assess call as 32: the evidence view ranks lines by the words
they share with the posting and stopped at 12,000 characters.  A requirement worded "a low-level or hardware-adjacent
background" shares no word with "a Zigbee gateway bridging sensors to an MQTT broker", so that line was never in the
prompt; the row read "no resume line states ..." and the pick could not take the line, however often the job was
assessed again.

THE END OUTCOME asserted here, through the product's own path (``run_quick_assessment``) on a synthetic home, with a
SCRIPTED model that answers from what its prompt shows, as a model can: when the gateway line's id is in the prompt it
quotes the line on the hardware row and picks it; when it is not, it answers what the real model answered (an empty
evidence list, a pick without the line).  So the assertions on the STORED row and the STORED pick fail whenever the
brief lacks the line.  No model is called and nothing here is anyone's real resume.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from gigai.scout import assess_master, assessment_core, postings
from gigai.scout.assessment_core import AssessContext, AssessJob, render_assess_prompt
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResponse, AssessResumeInput
from gigai.scout.assess_master import ASSESS_EVIDENCE_CAP
from gigai.scout.master_selection import EVIDENCE_CAP, SelectionPosting, SelectionProfile, evidence_view
from gigai.scout.master_store import import_master, load_master
from gigai.scout.quick_assess import run_quick_assessment

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.pipeline_fixtures import build_pipeline_fixture
from tests.support.posting_fixtures import NOW, PostingsFixture, days_ago, job_url, lever_job

_SLUG = "harborlight"
_URL = job_url(_SLUG, 1)
_TITLE_REQUIREMENTS = ("5+ years of Python in production", "Kubernetes", "A low-level or hardware-adjacent background")
_POSTING = (
    "Harborlight is hiring a Staff AI Engineer to own Python inference services on Kubernetes. Requirements: "
    f"{_TITLE_REQUIREMENTS[0]}; {_TITLE_REQUIREMENTS[1]}. Nice to have: {_TITLE_REQUIREMENTS[2]}, enough to pair on a "
    "kernel driver. Remote within the United States."
)
RESUME = """## Experience

### Acme Corp
Senior Engineer | 2019 - 2023

- Built Python services for six years; cut p99 latency by 40%.

## Skills

- Python, Kubernetes
"""
PYTHON_LINE = "Built Python services for six years; cut p99 latency by 40%."
#: The two lines the hardware row rests on. Neither shares a word with the requirement, so word ranking puts them last.
GATEWAY_LINE = "Assembled a Zigbee gateway on a Raspberry Pi that bridged about ten sensors to a Mosquitto MQTT broker."
TELEMETRY_LINE = "Moved sensor readings from field equipment into dashboards over a Mosquitto MQTT broker."
_OLD_CAP = 12_000

_SYSTEMS = (
    "checkout", "billing", "search", "ranking", "catalog", "identity", "notification", "reporting", "pricing", "inventory", "routing",
)
_ROLES = (
    ("Northwind Labs", "Staff Engineer | 2023 - Present"),
    ("Acme Corp", "Senior Engineer | 2019 - 2023"),
    ("Lakeshore Data", "Engineer | 2016 - 2019"),
    ("Brightwater Health", "Engineer | 2014 - 2016"),
)


def _filler(role: int, index: int) -> str:
    """One long bullet that names what the posting asks for, so word ranking puts it above the gateway line."""

    system = _SYSTEMS[index]
    return (
        f"Owned the Python inference services of the {system} platform on Kubernetes for team {role + 1}{index:02d}, "
        f"with a staged rollout for every {system} model, a rollback path that was rehearsed each quarter, and an on-call "
        f"runbook that the {system} engineers kept current after every production review of that {system} service."
    )


def _master_markdown() -> str:
    parts = ["## Summary", "", "- Engineer with nine years on Python inference services.", "", "## Experience", ""]
    for role, (employer, subline) in enumerate(_ROLES):
        parts += [f"### {employer}", subline, ""]
        if employer == "Acme Corp":
            parts.append(f"- {PYTHON_LINE}")
        parts += [f"- {_filler(role, index)}" for index in range(len(_SYSTEMS))]
        parts.append("")
    parts += ["### Fieldline Systems", "Engineer | 2011 - 2014", "", f"- {GATEWAY_LINE}", f"- {TELEMETRY_LINE}", ""]
    parts += ["## Skills", "", "- Platform: Python, Kubernetes", ""]
    return "\n".join(parts)


MASTER = _master_markdown()


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    base = build_pipeline_fixture(tmp_path, monkeypatch, base=False, resume=RESUME)
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    fixture = PostingsFixture(base, second_profile_id="", deleted_profile_id=None)
    fixture.seed(_SLUG, [lever_job(_SLUG, 1, text=_POSTING)], seen_at=days_ago(1))
    postings.refresh(fixture.home_root, fixture.target, now=NOW)
    source = tmp_path / "master.md"
    source.write_text(MASTER, encoding="utf-8")
    assert import_master(home_root=fixture.home_root, target=fixture.target, source=source, gig_id=base.gig.resolved.gig_id).status == "created"
    return fixture


def _master(fx: PostingsFixture):
    stored = load_master(home_root=fx.home_root, target=fx.target, gig_id=fx.base.gig.resolved.gig_id)
    assert stored is not None
    return stored.master


def _id_in(prompt: str, line: str) -> str | None:
    """The id comment the prompt's RESUME block puts after ``line``, or ``None``: the prompt does not show the line."""

    found = re.search(re.escape(line) + r"\s*<!--\s*id:(\S+)\s*-->", prompt)
    return found.group(1) if found else None


def _scripted(prompt: str) -> str:
    """What a model can answer from THIS prompt: it cites and picks the gateway line only when the prompt shows it."""

    # The met rows rest on the Python line, or on whichever bullet the block shows first when that one is cut too.
    python = _id_in(prompt, PYTHON_LINE) or re.search(r"<!--\s*id:(b-\S+)\s*-->", prompt).group(1)  # type: ignore[union-attr]
    gateway = _id_in(prompt, GATEWAY_LINE)
    hardware: dict[str, object] = {"requirement": _TITLE_REQUIREMENTS[2], "class": "nice_to_have", "class_basis": "Nice to have: A low-level or hardware-adjacent background", "status": "unclear"}
    # Adjacent evidence, as assess.md rule 1 asks: a quote of the closest line and what it does not state.
    hardware["resume_evidence"] = [f"{GATEWAY_LINE[:-1]}; firmware or kernel work not stated"] if gateway else []
    rows = [
        {"requirement": _TITLE_REQUIREMENTS[0], "class": "hard", "class_basis": f"Requirements: {_TITLE_REQUIREMENTS[0]}", "status": "met", "resume_evidence": ["Built Python services for six years"], "sources": [python]},
        {"requirement": _TITLE_REQUIREMENTS[1], "class": "askable", "class_basis": "Requirements: Kubernetes", "status": "met", "resume_evidence": ["Python services on Kubernetes"], "sources": [python]},
        hardware,
    ]
    pick = {"summary": None, "section_order": ["experience", "projects"], "lines": [python, *([gateway] if gateway else [])]}
    return json.dumps({"verdict": "matched_above_threshold", "matrix": rows, "questions": [], "suggestions": [], "pick": pick, "not_a_match_reason": None})


def _assess(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> tuple[AssessResponse, str]:
    model = fx.base.model
    answer = model.answer

    def from_the_prompt(prompt: str):
        model.assessed = _scripted(prompt)
        return answer(prompt)

    monkeypatch.setattr(model, "answer", from_the_prompt)
    model.assess_prompts.clear()
    stored = run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=_URL), resume=AssessResumeInput(profile_id=fx.default_profile_id)),
        home_root=fx.home_root, target=fx.target, config=fixture_config(fx.home_root),
    )
    return stored, model.assess_prompts[0]


def test_the_fixture_reproduces_the_cut_under_the_old_cap(fx: PostingsFixture) -> None:
    """What went wrong, on this fixture: under 12,000 characters the two lines are ranked out of the view."""

    master = _master(fx)
    ids = {item.text: item.id for item in master.items.values()}
    posting = SelectionPosting("Staff AI Engineer", _POSTING, "Harborlight", "")
    cut = evidence_view(master, SelectionProfile(), posting, cap=_OLD_CAP, ids=True)
    assert cut.bullets < cut.bullets_total and ids[GATEWAY_LINE] not in cut.item_ids() and ids[TELEMETRY_LINE] not in cut.item_ids()
    assert GATEWAY_LINE not in cut.markdown
    # What an assessment reads now (``assess_master.evidence_text``, the product's own builder): the whole master.
    whole = assess_master.evidence_text(master, SelectionProfile(), title="Staff AI Engineer", posting_text=_POSTING, company="Harborlight", ids=True)
    assert _OLD_CAP < whole.chars <= ASSESS_EVIDENCE_CAP == whole.cap and whole.bullets == whole.bullets_total
    # The selector's own default, which the tailoring's candidate set reads, is what it was.
    assert EVIDENCE_CAP == _OLD_CAP
    assert ids[GATEWAY_LINE] in whole.item_ids() and ids[TELEMETRY_LINE] in whole.item_ids()


def test_a_row_whose_evidence_is_a_low_ranked_master_line_cites_it_and_the_pick_takes_it(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    stored, prompt = _assess(fx, monkeypatch)
    ids = {item.text: item.id for item in _master(fx).items.values()}

    # THE SYMPTOM: the hardware row names the line it rests on. It is not "met" (a gateway is adjacent to firmware, not
    # firmware), and it does not say that no line states it.
    row = next(item for item in stored.result.matrix if item.requirement == _TITLE_REQUIREMENTS[2])
    assert row.status.value == "unclear"
    assert row.resume_evidence and GATEWAY_LINE[:60] in row.resume_evidence[0]
    assert "no resume line" not in " ".join(row.resume_evidence).lower()
    # And the pick took the line.
    assert stored.result.verdict is not None and stored.result.verdict.value == "matched_above_threshold"
    assert stored.result.pick is not None and ids[GATEWAY_LINE] in stored.result.pick.lines
    # Why: the brief holds both lines, with the ids a row or a pick may name.
    assert _id_in(prompt, GATEWAY_LINE) == ids[GATEWAY_LINE] and _id_in(prompt, TELEMETRY_LINE) == ids[TELEMETRY_LINE]


def test_the_prompt_says_to_read_every_line_before_calling_a_row_silent_and_to_quote_adjacent_evidence() -> None:
    text = " ".join(assessment_core.load_assess_instructions().split())
    assert "read EVERY line of the RESUME block" in text
    assert "Never write that no resume line states, mentions or shows X" in text
    assert "never \"met\" just because a line exists" in text
    # The pick may take the line an unclear row quotes.
    assert "the line an \"unclear\" row's resume_evidence quotes as the closest evidence" in text


def test_a_resume_block_past_the_old_cap_is_whole_and_still_loses_its_contact_lines() -> None:
    """The privacy edge at the new size: contact lines are stripped, and nothing is cut before 24,000 characters."""

    body = "\n".join(f"- {_filler(0, index % len(_SYSTEMS))} Line {index}." for index in range(60))
    resume = f"Avery Example\navery.example@example.test | +1 555 010 0199 | linkedin.com/in/avery-example\n\n## Experience\n\n### Northwind Labs\nStaff Engineer | 2023 - Present\n\n{body}\n"
    assert _OLD_CAP < len(resume) < assessment_core._MAX_PROMPT_RESUME_TEXT == ASSESS_EVIDENCE_CAP
    prompt = render_assess_prompt(AssessJob("Staff AI Engineer", "Harborlight", "Remote", _POSTING), AssessContext(resume_text=resume, visa_sponsorship_required=False))
    assert "Line 59." in prompt
    for contact in ("avery.example@example.test", "555 010 0199", "linkedin.com/in/avery-example"):
        assert contact not in prompt
