"""0110-10-05 C: ``tailor_skills`` -- no Skills bullet inside another, and an answer that satisfies a posting skill is shown.

Pure cases on ``collapse_duplicate_skills``, ``answer_skills`` and
``finish_tailoring`` (what ``tailor_once`` runs after ``apply_no_loss``).
The end-to-end symptoms are in ``test_tailor_part_c_outcomes.py``.
"""

from __future__ import annotations

from datetime import date

from gigai.scout.tailor_skills import ORIGIN_ANSWER, answer_skills, collapse_duplicate_skills, finish_tailoring
from gigai.scout.tailored_resume import (
    AnswerSource,
    MatrixRow,
    TailorJob,
    TailoredLine,
    TailoredResume,
    apply_no_loss,
    render_markdown,
    shown_text,
    tailor_context,
    tailor_line_stats,
    validate_tailored_output,
)

_TODAY = date(2026, 10, 3)
_RESUME = (
    "## Experience\n"  # R1
    "**Platform Engineer — Brightpay** (2021–Present)\n"  # R2
    "- Operated Kubernetes clusters on AWS with Terraform.\n"  # R3
    "\n"
    "## Skills\n"  # R4
    "Languages: Python, Go, TypeScript\n"  # R5 -> R6, R7 (plain lines wrap into one another)
    "Cloud: AWS, Kubernetes, Terraform\n"  # R6 -> R7
    "Data: PostgreSQL, Kafka\n"  # R7
)
_POSTING = (
    "Northgate runs clinics' booking software.\n\nRequirements:\n- Kubernetes in production\n- Helm charts\n- GitOps with ArgoCD\n"
    "- Terraform\n- Observability\n\nNice to have:\n- Go\n"
)
_JOB = TailorJob(title="Platform Engineer", company="Northgate", location="Remote", posting_text=_POSTING)


def _answers(**items: str) -> dict[str, AnswerSource]:
    return {key.replace("__", ":"): AnswerSource(key.replace("__", ":"), text, "rev") for key, text in items.items()}


_HELM_YES = _answers(tooling__helm="Yes, about 3 years of hands-on use.")
_EXPERIENCE = {"heading": "experience", "entries": [{"heading_ref": [{"copy": 2}], "bullets": [{"copy": 3}]}]}


def _finish(skills: list[dict[str, object]] | None, *, answers=None, matrix=(), resume: str = _RESUME, job: TailorJob = _JOB) -> TailoredResume:
    ctx = tailor_context(resume, answers=answers or {}, matrix=tuple(matrix))
    sections: list[dict[str, object]] = [_EXPERIENCE]
    if skills is not None:
        sections.append({"heading": "skills", "lines": skills})
    settled = apply_no_loss(validate_tailored_output({"sections": sections}, job, ctx), job, ctx, today=_TODAY)
    return finish_tailoring(settled, job, ctx)


def _skills(result: TailoredResume) -> list[TailoredLine]:
    return list(next((section.lines for section in result.sections if section.heading == "skills"), ()))


def _surface(phrase: str) -> dict[str, object]:
    return {"kind": "surface", "requirement": None, "posting_phrase": phrase}


# --- 1. no Skills bullet inside another -----------------------------------------------------------

_ALL = "Languages: Python, Go, TypeScript Cloud: AWS, Kubernetes, Terraform Data: PostgreSQL, Kafka"


def test_a_copy_of_a_line_an_earlier_reordered_line_already_holds_is_dropped() -> None:
    # The model reorders the whole wrapped block as one line (a rewrite that keeps every skill), then copies R6 again.
    reordered = "Cloud: Kubernetes, Terraform, AWS; Languages: Python, Go, TypeScript; Data: PostgreSQL, Kafka"
    result = _finish([{"text": reordered, "refs": [{"kind": "resume", "line": 5}], "reason": _surface("Kubernetes in production")}, {"copy": 6}])
    assert [shown_text(line) for line in _skills(result)] == [reordered]
    assert render_markdown(result).count("Kubernetes, Terraform") == 1  # printed once
    assert tailor_line_stats(result).shown_rewritten == 1


def test_a_fallback_copy_inside_the_first_bullet_is_dropped_and_its_rejected_rewrite_stays_on_record() -> None:
    # R5 copied (it carries R6 and R7), then R6 rewritten with a skill dropped: the fallback copy of R6..R7 repeats bullet 1.
    result = _finish([{"copy": 5}, {"text": "Cloud: Kubernetes, AWS", "refs": [{"kind": "resume", "line": 6}], "reason": _surface("Kubernetes in production")}])
    (line,) = _skills(result)
    assert shown_text(line) == _ALL and line.kind == "copy"
    section = next(section for section in result.sections if section.heading == "skills")
    (rejected,) = section.dropped
    assert rejected.kind == "rewritten" and rejected.text == "Cloud: Kubernetes, AWS" and rejected.dropped_duplicate is True
    stats = tailor_line_stats(result)
    assert stats.fallbacks == 1 and stats.model_rewrites == 1 and stats.fallbacks_by_rule["dropped_duplicate"] == 1


def test_equal_lines_keep_the_first_and_lines_with_a_skill_of_their_own_all_stay() -> None:
    resume = "## Experience\n**Engineer — Acme** (2022–Present)\n- Built things.\n\n## Skills\n- Python, Go\n- Go, Python\n- Python, Rust\n- Kubernetes\n"
    ctx = tailor_context(resume)
    payload = {"sections": [_EXPERIENCE, {"heading": "skills", "lines": [{"copy": 5}, {"copy": 6}, {"copy": 7}, {"copy": 8}]}]}
    settled = apply_no_loss(validate_tailored_output(payload, _JOB, ctx), _JOB, ctx, today=_TODAY)
    kept, dropped = collapse_duplicate_skills(next(section.lines for section in settled.sections if section.heading == "skills"))
    assert [shown_text(line) for line in kept] == ["Python, Go", "Python, Rust", "Kubernetes"] and dropped == []


def test_a_line_the_user_typed_is_never_dropped_and_never_drops_another() -> None:
    lines = (
        TailoredLine("copy", "- Python, Go, Rust", ()),
        TailoredLine("custom", "Python, Go", (), origin="user", edited_from=TailoredLine("copy", "- Python", ())),
        TailoredLine("copy", "- Python, Go", ()),
    )
    kept, _dropped = collapse_duplicate_skills(lines)
    assert [line.kind for line in kept] == ["copy", "custom"]  # the user's line stays; the copy inside bullet 1 goes


def test_only_the_skills_section_is_collapsed() -> None:
    resume = "## Experience\n**Engineer — Acme** (2022–Present)\n- Python, Go\n- Python\n\n## Skills\n- Python\n"
    ctx = tailor_context(resume)
    payload = {"sections": [{"heading": "experience", "entries": [{"heading_ref": [{"copy": 2}], "bullets": [{"copy": 3}, {"copy": 4}]}]}, {"heading": "skills", "lines": [{"copy": 6}]}]}
    result = finish_tailoring(apply_no_loss(validate_tailored_output(payload, _JOB, ctx), _JOB, ctx, today=_TODAY), _JOB, ctx)
    assert [line.text for line in result.sections[0].entries[0].bullets] == ["- Python, Go", "- Python"]


# --- 2. an answer that satisfies a posting skill --------------------------------------------------


def test_an_answered_posting_skill_the_resume_lacks_is_added_to_skills_with_the_answer_as_its_only_source() -> None:
    matrix = (MatrixRow("Kubernetes in production", "met"), MatrixRow("Helm charts", "met"))
    result = _finish([{"copy": 5}], answers=_HELM_YES, matrix=matrix)
    added = _skills(result)[-1]
    assert (added.kind, added.text, added.origin) == ("rewritten", "Helm", ORIGIN_ANSWER)
    assert [(ref.kind, ref.question_id) for ref in added.refs] == [("answer", "tooling:helm")]
    assert added.reason is not None and (added.reason.kind, added.reason.requirement, added.reason.posting_phrase) == ("answer", "M2", "Helm")
    assert added.id is not None and added.id not in {line.id for line in _skills(result)[:-1]}
    assert "- Helm <!-- A tooling:helm -->" in render_markdown(result)
    assert TailoredResume.from_json(result.to_json()) == result  # the new origin is part of the stored contract
    # GigAI's own line is not counted as a model rewrite; it is an answer-only line.
    stats = tailor_line_stats(result)
    assert stats.model_rewrites == 0 and stats.shown_rewritten == 1 and stats.answer_only_lines == 1


def test_the_skill_is_spelled_as_the_answer_spells_it_when_only_an_alias_is_in_the_answer() -> None:
    answers = _answers(experience__gitops="We deploy everything through Argo CD.")
    (skill,) = answer_skills(_JOB, tailor_context(_RESUME, answers=answers))
    assert (skill.keyword, skill.text, skill.question_id, skill.posting_phrase) == ("ArgoCD", "Argo CD", "experience:gitops", "ArgoCD")
    assert [line.text for line in _skills(_finish([{"copy": 5}], answers=answers))][-1] == "Argo CD"


def test_nothing_is_added_when_the_answer_denies_the_skill_or_the_assessment_says_unmet() -> None:
    for answer in ("No.", "No, I have not used Helm.", "I have never used Helm in production.", "Not yet; I only read about Helm.", "N/A"):
        assert answer_skills(_JOB, tailor_context(_RESUME, answers=_answers(tooling__helm=answer))) == (), answer
    # An affirmative sentence about another tool beside a denial of this one: still nothing.
    mixed = _answers(tooling__delivery="Yes, GitHub Actions daily. I have not used Helm.")
    assert answer_skills(_JOB, tailor_context(_RESUME, answers=mixed)) == ()
    # The stored assessment marks the requirement unmet: the answer did not satisfy it.
    unmet = tailor_context(_RESUME, answers=_HELM_YES, matrix=(MatrixRow("Helm charts", "unmet"),))
    assert answer_skills(_JOB, unmet) == ()
    assert [line.origin for line in _skills(_finish([{"copy": 5}], answers=_answers(tooling__helm="No.")))] == ["model"]


def test_nothing_is_added_for_a_skill_the_resume_has_the_posting_does_not_ask_for_or_the_model_already_showed() -> None:
    # The resume names Terraform; the posting does not ask for Rust; nothing to add from either answer.
    answers = _answers(tooling__terraform="Yes, five years.", language__rust="Yes, two years of Rust.")
    assert answer_skills(_JOB, tailor_context(_RESUME, answers=answers)) == ()
    # The model already added Helm (citing the answer): GigAI adds no second line.
    helm_line = {"text": "Helm", "refs": [{"kind": "answer", "question_id": "tooling:helm"}], "reason": {"kind": "answer", "requirement": None, "posting_phrase": "Helm charts"}}
    result = _finish([{"copy": 5}, helm_line], answers=_HELM_YES)
    assert [(line.text, line.origin) for line in _skills(result)][-1] == ("Helm", "model")
    assert sum("Helm" in shown_text(line) for line in _skills(result)) == 1
    # No answers at all: nothing to do.
    assert answer_skills(_JOB, tailor_context(_RESUME)) == ()


def test_a_skills_section_is_created_when_the_tailoring_has_none() -> None:
    result = _finish(None, answers=_HELM_YES)
    assert [section.heading for section in result.sections] == ["experience", "skills"]
    assert [(line.text, line.origin) for line in _skills(result)] == [("Helm", ORIGIN_ANSWER)]
