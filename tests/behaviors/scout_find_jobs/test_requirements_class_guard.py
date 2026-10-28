"""0.1.11.12 F2b (gigai-18i, defect 6): a posting whose ONLY qualification list is headed "Preferred" is its requirements.

A Snorkel-shaped posting listed its qualifications under "Preferred Qualifications" alone; the first assessment classed
every row ``nice_to_have``, and nice-to-have rows never ask and never hold, so the job read as a perfect fit with
nothing checked.  ``requirements_list.extracted`` now re-classes such a list ``askable`` (``class_basis`` "only list:
..."), and a later assessment, which must return the list's classes (``check_listed``), is held by the existing gate on
an unclear row.  Kept narrow (an over-ask is a measured failure): a list headed "Bonus", "Bonus points", "Pluses" or
"Extra credit" is genuinely optional and stays ``nice_to_have`` (coordinator ruling), as does a list whose rows' own
class_basis says so; a pay or benefits line ("Bonus: 10% annual target") is not such a heading; a posting with any
must-have row is left as the model classed it.  Synthetic postings only; no model, no GigAI home.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.scout import assessment_core, fit, postings
from gigai.scout import requirements_list as rl
from gigai.scout.master_store import import_master
from gigai.scout.resume_gate import HOLD_QUESTION, SUGGEST, gate

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.pipeline_fixtures import build_pipeline_fixture
from tests.support.posting_fixtures import NOW, PostingsFixture, days_ago, lever_job

from .test_assessment_v9_flow import _SLUG, MASTER, OWN_LINE, PYTHON_LINE, RESUME, _assess, _ids

POSTING = "sha256:" + "3" * 64
AT = "2026-10-27T10:00:00Z"
PREFERRED = (
    "5+ years building production backend systems",
    "Experience with Python and Go",
    "Kubernetes in production",
    "Distributed data processing (Spark or Ray)",
    "Prior work at an ML infrastructure company",
)


def _posting(*sections: tuple[str, tuple[str, ...]]) -> str:
    """A synthetic posting: a role blurb, duties, the given sections, and a pay sentence that names a bonus."""

    parts = ["Senior Software Engineer, Data Platform", "", "About the role", "We build the platform teams use to label and train on data.", "",
             "What you'll do", "- Design and run training data pipelines", ""]
    for heading, bullets in sections:
        parts += [heading, *(f"- {bullet}" for bullet in bullets), ""]
    parts += ["Compensation", "The base salary range is $180,000 - $250,000, plus an annual bonus and equity."]
    return "\n".join(parts)


def _row(requirement: str, klass: str, heading: str, **more: object) -> dict[str, object]:
    return {"requirement": requirement, "class": klass, "status": "met", "resume_evidence": [], "class_basis": f"{heading}: {requirement}", **more}


def _only(heading: str) -> tuple[str, list[dict[str, object]]]:
    """A posting whose only list is ``heading``, with the five rows a first assessment classed nice_to_have (and a row about the candidate)."""

    matrix = [_row(text, "nice_to_have", heading) for text in PREFERRED]
    matrix.append(_row("Based in the United States", "hard", "Location", id="elig-location"))
    return _posting((heading, PREFERRED)), matrix


def _extract(matrix: list[dict[str, object]], **text: object):
    return rl.extracted(POSTING, matrix, extracted_at=AT, prompt_version="assess-prompt-v10", model="fixture-model", profile_id="profile_1", **text)


def test_a_snorkel_shaped_posting_s_only_preferred_list_is_askable_and_says_only_list() -> None:
    text, matrix = _only("Preferred Qualifications")
    found, row_ids = _extract(matrix, posting_text=text)
    assert [row.requirement for row in found.rows] == list(PREFERRED)
    assert [row.requirement_class for row in found.rows] == ["askable"] * 5
    assert [row.class_basis for row in found.rows] == [f"only list: Preferred Qualifications: {item}" for item in PREFERRED]
    # The ids are the words' (unchanged by the class), the candidate's row keeps its fixed id and stays out of the list.
    assert list(found.ids()) == list(rl.assign_ids(POSTING, list(PREFERRED))) and row_ids[-1] == "elig-location"
    assert found.rules_version == rl.RULES_VERSION == "req-rules:2"


def test_the_gate_holds_a_later_assessment_on_an_unclear_row_of_the_re_classed_list() -> None:
    text, matrix = _only("Preferred Qualifications")
    found, _ids = _extract(matrix, posting_text=text)
    # The later answer carries the list's ids; the model calls the rows nice_to_have again, one unclear, no question asked.
    answer = [
        {"id": row.id, "requirement": row.requirement, "class": "nice_to_have", "status": "unclear" if place == 2 else "met", "resume_evidence": []}
        for place, row in enumerate(found.rows)
    ]
    assert gate(answer, [], "matched_above_threshold").decision == SUGGEST  # what the defect stored: nothing checked
    checked = rl.check_listed(answer, found.rows)
    assert [row["class"] for row in checked] == ["askable"] * 5
    held = gate(checked, [], "matched_above_threshold")
    assert held.decision == HOLD_QUESTION and [reason.requirement_id for reason in held.reasons] == [found.rows[2].id]


@pytest.mark.parametrize("heading", ["Preferred Qualifications", "Desired Skills", "Desirable", "Nice to have", "Ideal candidate"])
def test_every_preferred_like_only_list_is_re_classed(heading: str) -> None:
    text, matrix = _only(heading)
    found, _ids = _extract(matrix, posting_text=text)
    assert {row.requirement_class for row in found.rows} == {"askable"} and all(row.class_basis.startswith(f"only list: {heading}: ") for row in found.rows)


@pytest.mark.parametrize("heading", ["Bonus", "Bonus points", "Pluses", "Extra credit"])
def test_a_bonus_or_plus_only_list_is_genuinely_optional_and_stays_nice_to_have(heading: str) -> None:
    text, matrix = _only(heading)
    found, _ids = _extract(matrix, posting_text=text)
    assert {row.requirement_class for row in found.rows} == {"nice_to_have"}
    assert [row.class_basis for row in found.rows] == [f"{heading}: {item}" for item in PREFERRED]


def test_a_posting_with_requirements_and_a_nice_to_have_list_is_unchanged() -> None:
    required = ("Go in production", "8+ years of backend engineering")
    text = _posting(("Requirements", required), ("Nice to have", PREFERRED[3:]))
    matrix = [_row(required[1], "hard", "Requirements"), _row(required[0], "askable", "Requirements"),
              *(_row(item, "nice_to_have", "Nice to have") for item in PREFERRED[3:])]
    found, _ids = _extract(matrix, posting_text=text)
    assert [(row.requirement_class, row.class_basis) for row in found.rows] == [(row["class"], row["class_basis"]) for row in matrix]


def test_a_posting_with_a_required_and_a_preferred_list_is_unchanged() -> None:
    text = _posting(("Required Qualifications", PREFERRED[:2]), ("Preferred Qualifications", PREFERRED[2:]))
    matrix = [*(_row(item, "askable", "Required Qualifications") for item in PREFERRED[:2]),
              *(_row(item, "nice_to_have", "Preferred Qualifications") for item in PREFERRED[2:])]
    found, _ids = _extract(matrix, posting_text=text)
    assert [(row.requirement_class, row.class_basis) for row in found.rows] == [(row["class"], row["class_basis"]) for row in matrix]


def test_with_no_posting_text_nothing_is_re_classed() -> None:
    # A caller without the text cannot tell a "Preferred" only list from a "Bonus" one: the rows stay as the model classed them.
    for heading in ("Preferred Qualifications", "Bonus points"):
        _text, matrix = _only(heading)
        assert {row.requirement_class for row in _extract(matrix)[0].rows} == {"nice_to_have"}


def test_an_optional_heading_is_a_whole_line_of_the_optional_words() -> None:
    for line in ("Bonus", "**Bonus points:**", "## Pluses", "Extra credit:", "It's a plus if you have:", "- Bonus points if you know Rust:",
                 "▪ Bonus points", "– Bonus:", "🎁 Bonus points", "<h3>Bonus Points</h3>", "BONUS POINTS"):
        assert rl.is_optional_heading(line), line
    for line in ("Preferred Qualifications", "Nice to have:", "The base salary range is $180,000 - $250,000, plus an annual bonus and equity.",
                 "Experience with Go is a plus", "Annual bonus", "", "Bonus: 10% annual target", "Plus equity and benefits", "Plus 401(k) match",
                 "Bonus eligible", "Bonus structure:", "Bonus points: Python, Go, Kubernetes", "- Bonus points for Rust"):
        assert not rl.is_optional_heading(line), line


@pytest.mark.parametrize("line", ["Bonus: 10% annual target", "Plus equity and benefits", "Plus 401(k) match", "Bonus eligible", "Bonus structure:"])
def test_a_pay_or_benefits_line_does_not_keep_a_preferred_only_list_optional(line: str) -> None:
    text, matrix = _only("Preferred Qualifications")
    found, _ids = _extract(matrix, posting_text=f"{text}\n{line}")
    assert {row.requirement_class for row in found.rows} == {"askable"}


def test_an_inline_bonus_points_list_stays_optional() -> None:
    # What html_to_text makes of <p><strong>Bonus points:</strong> Python, Go, Kubernetes</p>: one line, no heading line of its own;
    # the rows' own class_basis names the heading.
    matrix = [_row(tool, "nice_to_have", "Bonus points") for tool in ("Python", "Go", "Kubernetes")]
    found, _ids = _extract(matrix, posting_text=_posting() + "\nBonus points: Python, Go, Kubernetes")
    assert {row.requirement_class for row in found.rows} == {"nice_to_have"}


def test_rows_the_posting_calls_a_plus_stay_optional() -> None:
    bullets = tuple(f"{item} is a plus" for item in PREFERRED)
    matrix = [{**_row(item, "nice_to_have", "What we look for"), "class_basis": f"{item} is a plus"} for item in PREFERRED]
    found, _ids = _extract(matrix, posting_text=_posting(("What we look for", bullets)))
    assert {row.requirement_class for row in found.rows} == {"nice_to_have"}


def test_a_basis_that_already_says_only_list_is_not_prefixed_twice() -> None:
    text, matrix = _only("Preferred Qualifications")
    for row in matrix[:-1]:
        row["class_basis"] = f"only list: {row['class_basis']}"
    found, _ids = _extract(matrix, posting_text=text)
    assert [row.class_basis for row in found.rows] == [f"only list: Preferred Qualifications: {item}" for item in PREFERRED]


def test_a_posting_that_states_no_requirements_keeps_its_one_row() -> None:
    lone = [{"requirement": "No stated requirements", "class": "nice_to_have", "status": "met", "resume_evidence": [], "class_basis": ""}]
    found, _ids = _extract(lone, posting_text=_posting())
    assert [(row.requirement_class, row.class_basis) for row in found.rows] == [("nice_to_have", "")]


def test_a_list_frozen_under_req_rules_1_is_re_extracted_and_the_old_one_moved_aside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rl, "project_id", lambda _home, _target: "project_test")
    text, matrix = _only("Preferred Qualifications")
    frozen = rl.PostingRequirements(POSTING, "req-rules:1", AT, rl.ExtractedBy("assess-prompt-v10", "fixture-model", "profile_1"), _extract(matrix)[0].rows)
    path = rl.requirements_path(tmp_path, tmp_path, POSTING)
    path.parent.mkdir(parents=True)
    path.write_bytes(json.dumps(frozen.to_json()).encode())
    before = path.read_bytes()
    # The stored nice_to_have list is not given to the next assessment, which is a first one again and freezes the new list.
    assert rl.read_list(tmp_path, tmp_path, POSTING) is None
    new, _ids = _extract(matrix, posting_text=text)
    assert rl.settle_first(tmp_path, tmp_path, new).kind == "stored" and rl.read_list(tmp_path, tmp_path, POSTING) == new
    assert path.with_name(f"{path.stem}.req-rules-1.json").read_bytes() == before  # never deleted
    assert set(new.ids()) == set(frozen.ids())  # the same words keep their ids


@pytest.fixture
def only_preferred(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    """The ``test_assessment_v9_flow`` fixture, with a posting whose only list is "Preferred Qualifications"."""

    shipped = assessment_core.load_assess_instructions()
    for name in ("id_example", "note_example", "pick_lines", "requirements"):
        shipped = shipped.replace("{{" + name + "}}", "x")
    monkeypatch.setattr(assessment_core, "load_assess_instructions", lambda: shipped)
    monkeypatch.setattr("gigai.scout.assess_strict.strict_rows", lambda *_args, **_kwargs: [])
    base = build_pipeline_fixture(tmp_path, monkeypatch, base=False, resume=RESUME)
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    fixture = PostingsFixture(base, second_profile_id="", deleted_profile_id=None)
    fixture.seed(_SLUG, [lever_job(_SLUG, 1, text=_posting(("Preferred Qualifications", PREFERRED)))], seen_at=days_ago(1))
    postings.refresh(fixture.home_root, fixture.target, now=NOW)
    source = tmp_path / "master.md"
    source.write_text(MASTER, encoding="utf-8")
    assert import_master(home_root=fixture.home_root, target=fixture.target, source=source, gig_id=base.gig.resolved.gig_id).status == "created"
    return fixture


def test_a_first_assessment_stores_an_only_preferred_list_askable(only_preferred: PostingsFixture) -> None:
    # The production wiring (quick_assess passes the posting text to ``extracted``): without it the stored rows stay nice_to_have.
    ids = _ids(only_preferred)
    rows = [{"requirement": item, "class": "nice_to_have", "status": "unclear", "resume_evidence": [], "class_basis": f"Preferred Qualifications: {item}"}
            for item in PREFERRED]
    chosen = {"summary": None, "section_order": ["experience", "projects"], "lines": [ids[OWN_LINE], ids[PYTHON_LINE]]}
    answer = {"verdict": "matched_above_threshold", "matrix": rows, "questions": [], "suggestions": [], "pick": chosen, "not_a_match_reason": None}
    stored, _prompt = _assess(only_preferred, json.dumps(answer))
    listed = rl.read_list(only_preferred.home_root, only_preferred.target, stored.posting_sha256)
    assert listed is not None and [row.requirement for row in listed.rows] == list(PREFERRED)
    assert [(row.requirement_class, row.class_basis) for row in listed.rows] == [("askable", f"only list: Preferred Qualifications: {item}") for item in PREFERRED]


# --- 0.1.11.12 F2c: the same rule on the FIRST assessment's own matrix, before anything reads it -------------------------


def _first(text: str | None, rows: list[dict[str, object]]) -> dict[str, object]:
    """``rows`` as ``assess_once`` reads a first assessment's answer (no list yet; the posting's digest and text)."""

    answer = {"verdict": "matched_above_threshold", "matrix": [dict(row) for row in rows], "questions": [], "suggestions": [], "not_a_match_reason": None}
    boundary = assessment_core.Boundary(retry=False, posting_sha256=POSTING, posting_text=text)
    return assessment_core._normalize_and_strip(answer, boundary=boundary)[0]


def _unclear(rows: list[dict[str, object]], *places: int) -> list[dict[str, object]]:
    return [dict(row, status="unclear") if place in places else row for place, row in enumerate(rows)]


def test_the_matrix_guard_is_the_stored_lists_rule_and_skips_the_rows_about_the_candidate() -> None:
    text, matrix = _only("Preferred Qualifications")
    changes = rl.only_list_changes(matrix, text)
    assert changes == tuple((place, f"only list: Preferred Qualifications: {item}") for place, item in enumerate(PREFERRED))  # not elig-location
    found, _ids = _extract(matrix, posting_text=text)
    assert [row.class_basis for row in found.rows] == [basis for _place, basis in changes]
    assert rl.only_list_changes(matrix, None) == () and rl.only_list_changes(*reversed(_only("Bonus points"))) == ()


def test_a_first_assessment_re_classes_an_only_preferred_matrix_and_asks_its_unclear_row() -> None:
    text, matrix = _only("Preferred Qualifications")
    payload = _first(text, _unclear(matrix[:5], 2))
    assert [(row["class"], row["class_basis"]) for row in payload["matrix"]] == [("askable", f"only list: Preferred Qualifications: {item}") for item in PREFERRED]
    asked = assessment_core.row_question(rl.assign_ids(POSTING, PREFERRED)[2], PREFERRED[2])
    assert payload["structured_questions"] == [asked] and payload["questions"] == [asked["question"]]
    assert payload["verdict"] == "pending_user_answers"
    assert gate(payload["matrix"], payload["structured_questions"], payload["verdict"]).decision == HOLD_QUESTION


def test_with_every_re_classed_row_unclear_the_four_question_cap_still_holds() -> None:
    text, matrix = _only("Preferred Qualifications")
    payload = _first(text, _unclear(matrix[:5], 0, 1, 2, 3, 4))
    ids = rl.assign_ids(POSTING, PREFERRED)
    assert payload["structured_questions"] == [assessment_core.row_question(ids[place], PREFERRED[place]) for place in range(4)]
    assert payload["verdict"] == "pending_user_answers"


def _bonus_only() -> tuple[str, list[dict[str, object]]]:
    text, matrix = _only("Bonus points")
    return text, _unclear(matrix[:5], 2)


def _required_and_preferred() -> tuple[str, list[dict[str, object]]]:
    text = _posting(("Required Qualifications", PREFERRED[:2]), ("Preferred Qualifications", PREFERRED[2:]))
    rows = [_row(item, "askable", "Required Qualifications") for item in PREFERRED[:2]] + [_row(item, "nice_to_have", "Preferred Qualifications") for item in PREFERRED[2:]]
    return text, _unclear(rows, 3)


def _one_hard_row() -> tuple[str, list[dict[str, object]]]:
    text, matrix = _only("Preferred Qualifications")
    return text, _unclear([dict(matrix[0], **{"class": "hard"}), *matrix[1:5]], 2)


@pytest.mark.parametrize("case", [_bonus_only, _required_and_preferred, _one_hard_row], ids=["bonus-only", "required-list", "one-hard-row"])
def test_a_first_assessment_the_rule_does_not_cover_is_unchanged(case) -> None:
    text, rows = case()
    assert rl.only_list_changes(rows, text) == ()
    payload = _first(text, rows)
    assert json.dumps(payload, sort_keys=True) == json.dumps(_first(None, rows), sort_keys=True)  # what it was without the posting's text
    assert [(row["class"], row["class_basis"]) for row in payload["matrix"]] == [(row["class"], row["class_basis"]) for row in rows]
    assert not payload.get("structured_questions") and payload["verdict"] == "matched_above_threshold"


def test_a_first_assessment_of_an_only_preferred_posting_asks_its_unclear_row_and_is_held(only_preferred: PostingsFixture) -> None:
    ids = _ids(only_preferred)
    rows = [{"requirement": item, "class": "nice_to_have", "status": "unclear" if place == 2 else "met", "resume_evidence": [] if place == 2 else [PYTHON_LINE],
             "sources": [] if place == 2 else [ids[PYTHON_LINE]], "class_basis": f"Preferred Qualifications: {item}"} for place, item in enumerate(PREFERRED)]
    chosen = {"summary": None, "section_order": ["experience", "projects"], "lines": [ids[OWN_LINE], ids[PYTHON_LINE]]}
    answer = {"verdict": "matched_above_threshold", "matrix": rows, "questions": [], "suggestions": [], "pick": chosen, "not_a_match_reason": None}
    stored, _prompt = _assess(only_preferred, json.dumps(answer))
    assert len(only_preferred.base.model.assess_prompts) == 1  # the re-classed row's question is code's: no retry is spent on it
    assert [(row.to_json()["class"], row.class_basis) for row in stored.result.matrix] == [("askable", f"only list: Preferred Qualifications: {item}") for item in PREFERRED]
    unclear = stored.result.matrix[2]
    assert [(item.question_id, item.requirement) for item in stored.result.structured_questions] == [(assessment_core.row_question_id(unclear.id), PREFERRED[2])]
    assert stored.result.verdict.value == "pending_user_answers" and stored.resume_gate is not None and stored.resume_gate.decision == HOLD_QUESTION
    shown = fit.shown_fit(stored.result.matrix)
    assert shown is not None and shown <= fit.OPEN_CAP == 69
    listed = rl.read_list(only_preferred.home_root, only_preferred.target, stored.posting_sha256)
    assert listed is not None and [(row.id, row.requirement_class) for row in listed.rows] == [(row.id, "askable") for row in stored.result.matrix]
