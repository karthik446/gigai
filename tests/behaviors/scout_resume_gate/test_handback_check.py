"""0.1.11 N2 (SPEC 5.3): the hand-back check reads the MASTER.  A guard, not proof.

On the pure check (``tailored_resume_edit.handback_result`` over ``handback_check``): no home, no model, no
renderer.  The data is the agent-tailoring spike's invented person (``tests/evals/fixtures/handback``: the
58-bullet master, and the 16 resumes its four agent sessions handed back to 0.1.10.9, which stored none).

- the spike's 20-case probe (``proto/guard_probe.py``): one line of a stored resume is edited; 15 edits are
  refused and 5 hand-backs accepted;
- the 16 hand-backs: no old refusal is left (a master role, the master's own Skills lines); 15 are accepted
  and one is refused for the one true reason it has (a 2020 role above the current one);
- one test per row of SPEC 5.3's table, the per-claim answer rule, the level rule;
- a false-refusal set: true rewordings the spike's word comparison refused (RAG, LLM, services, fine-tuning)
  are accepted; CI/CD on a line whose entry never states it is still refused, by design.

What these tests do NOT show: that a reworded line which passes is true.  The check guards numbers, names,
ownership, entries and sources; ``test_a_stretch_the_check_cannot_see_is_accepted`` pins one stretch it lets through.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from gigai.scout import handback_check as hc
from gigai.scout.handback_check import CODES, WHAT_THE_CHECK_IS, Problem, cited_ids, master_lines, names_in, stated, term_keys, text_keys
from gigai.scout.master_resume import Master, assign_ids, build_master, draft_master
from gigai.scout.skill_groups import skill_parts
from gigai.scout.tailored_resume import AnswerSource, TailoredLine, TailoredResume, render_markdown, resume_lines
from gigai.scout.tailored_resume_edit import HandbackRefused, handback_result

FIXTURES = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "handback"
HANDBACKS = sorted((FIXTURES / "handbacks").glob("*.md"))

#: The spike's answers (its brief, "ANSWERS AND STORIES"): two say yes, two say no.
ANSWERS = {
    "ml:fine_tuning": "Yes: fine-tuned a small open model with PyTorch for shipment-exception routing in 2024, a 6-week project.",
    "tooling:cassandra": "No production experience with Cassandra.",
    "tooling:langgraph": "No, I have not used LangGraph.",
    "tooling:temporal": "Yes, about 2 years: Temporal runs the durable agent workflows at Halcyon Freight.",
}

HALCYON = "### Halcyon Freight\nStaff Engineer, AI Platform | Mar 2023 - Present\n"
QUILLON = "### Quillon Health\nStaff Software Engineer | Jan 2020 - Feb 2023\n"
BRASSLINE = "### Brassline Pay\nSenior Software Engineer | Jun 2016 - Dec 2019\n"
PELLUCID = "### Pellucid Search\nSenior Software Engineer | Aug 2013 - May 2016\n"

GUARD = "Built guardrails"
SPEND = "Cut LLM spend"
LEDGER = "Built the ledger service"
RETRIEVAL = "Designed a retrieval pipeline"
HARNESS = "Built an evaluation harness"
CONTROL_PLANE = "Owned the scheduling"
BACKBONE = "Designed the appointment event backbone"
HELM = "Led the migration of 30"
HIPAA = "Worked with compliance"
MIGRATION = "Wrote data migration scripts"
SUMMARY = "Staff engineer with 17 years"


def _master(markdown: str) -> Master:
    draft = draft_master(markdown)
    assign_ids(draft)
    return build_master(draft)


@pytest.fixture(scope="module")
def master() -> Master:
    return _master((FIXTURES / "master.md").read_text(encoding="utf-8"))


def _answers(**more: str) -> dict[str, AnswerSource]:
    return {key: AnswerSource(question_id=key, answer=text, revision_id="rev-1") for key, text in {**ANSWERS, **more}.items()}


def _item(master: Master, start: str):
    (item,) = [item for item in master.items.values() if item.text.startswith(start)]
    return item


def _id(master: Master, start: str) -> str:
    return _item(master, start).id


def _line(master: Master, start: str) -> str:
    return f"- {_item(master, start).text}"


def _entry_id(master: Master, heading: str) -> str:
    (entry,) = [entry for entry in master.entries.values() if entry.heading == heading]
    return entry.id


def _check(markdown: str, master: Master, *, answers: dict[str, AnswerSource] | None = None, previous: TailoredResume | None = None, pages: int | None = None):
    """``(the stored structure or None, the problems)`` of one hand-back."""

    try:
        return handback_result(markdown, master=master, answers=_answers() if answers is None else answers, previous=previous, pages=pages), ()
    except HandbackRefused as exc:
        return None, exc.problems


def _codes(problems) -> list[str]:
    return [problem.code for problem in problems]


def _accepted(markdown: str, master: Master, **kwargs) -> TailoredResume:
    result, problems = _check(markdown, master, **kwargs)
    assert result is not None, [problem.to_json() for problem in problems]
    return result


def _refused(markdown: str, master: Master, **kwargs) -> tuple[Problem, ...]:
    result, problems = _check(markdown, master, **kwargs)
    assert result is None and problems, "the hand-back was accepted"
    return problems


def _number(markdown: str, start: str) -> int:
    """The 1-based line of ``markdown`` that starts with ``start``."""

    (number,) = [number for number, line in enumerate(markdown.splitlines(), 1) if line.startswith(start)]
    return number


def _body(result: TailoredResume) -> list[TailoredLine]:
    return [line for section in result.sections for line in section.body_lines()]


def _shown(result: TailoredResume, start: str) -> TailoredLine:
    (line,) = [line for line in _body(result) if line.text.removeprefix("- ").startswith(start)]
    return line


def _under_halcyon(master: Master, *bullets: str) -> str:
    """A small resume: the summary, the current role with ``bullets`` after one of its own lines, one Skills line."""

    return (
        f"## Summary\n\n{_line(master, SUMMARY)}\n\n## Experience\n\n{HALCYON}\n{_line(master, 'Architected the agent runtime')}\n"
        + "".join(f"{bullet}\n" for bullet in bullets)
        + "\n## Skills\n\n- Python/Go/TypeScript · Java/Kotlin · SQL/Bash\n"
    )


def _under_quillon(master: Master, *bullets: str) -> str:
    return f"## Experience\n\n{QUILLON}\n{_line(master, CONTROL_PLANE)}\n" + "".join(f"{bullet}\n" for bullet in bullets)


# --- the master, numbered -----------------------------------------------------------------------


def test_the_master_is_numbered_as_its_own_markdown_is(master: Master) -> None:
    numbered = master_lines(master)
    assert list(numbered.lines) == list(resume_lines(master.markdown(ids=False)))
    guard = _item(master, GUARD)
    assert numbered.text(numbered.items[guard.id]) == f"- {guard.text}"
    halcyon = _entry_id(master, "Halcyon Freight")
    assert [numbered.text(number) for number in numbered.entries[halcyon]] == ["### Halcyon Freight", "Staff Engineer, AI Platform | Mar 2023 - Present"]
    assert len(numbered.items) == len(master.items) and len(numbered.entries) == len(master.entries)


# --- the spike's 20-case probe ---------------------------------------------------------------------


def _stored_markdown(master: Master) -> str:
    """A stored resume shaped like the spike's design A result (a reworded summary, an assembled Skills line, a
    skill from an answer), without the role it printed with no bullet (G7 refuses that now)."""

    def lines(*starts: str) -> str:
        return "".join(f"{_line(master, start)}\n" for start in starts)

    return (
        "## Summary\n\n"
        "- Staff engineer building LLM agent systems that run in production since 2023, with 17 years of experience building backend platforms; "
        f"led a 7-engineer AI platform team serving 60 internal teams. <!-- src: {_id(master, SUMMARY)} -->\n\n"
        "## Experience\n\n"
        f"{HALCYON}\n"
        + lines(
            "Architected the agent runtime", "Built a multi-agent dispatch", HARNESS, GUARD, "Shipped a Model Context", RETRIEVAL, SPEND,
            "Led a 7-engineer", "Mentored 3 senior",
        )
        + f"\n{QUILLON}\n" + lines(CONTROL_PLANE, HELM)
        + f"\n{BRASSLINE}\n" + lines(LEDGER)
        + f"\n{PELLUCID}\n" + lines("Built relevance evaluation")
        + "\n## Skills\n\n"
        "- LLM agents/multi-agent systems/tool calling, LLM evaluation/prompt caching, Python/Go/TypeScript, PostgreSQL/Redis/Kafka, Kubernetes/Docker/Helm\n"
        "- Temporal <!-- src: A tooling:temporal -->\n\n"
        "## Projects\n\n### Loomhand: a personal multi-agent workbench\n\n" + lines("Built a local multi-agent")
        + "\n## Education\n\n### Varnholt Technical University\nB.S. Computer Science | 2005 - 2009\n"
    )


@pytest.fixture(scope="module")
def stored(master: Master) -> TailoredResume:
    return _accepted(_stored_markdown(master), master)


def _without_comments(markdown: str) -> str:
    return "\n".join(re.sub(r"\s*<!--.*?-->", "", line).rstrip() for line in markdown.splitlines()) + "\n"


#: (case, what the spike expects, the rule that refuses it or None). The texts are the probe's own.
PROBE = [
    ("control: the stored resume, unchanged", "accept", None),
    ("reorder only: two bullets swapped", "accept", None),
    ("honest reword leading with the posting's words (same facts)", "accept", None),
    ("framing upgrade, no number: 'Worked with' -> 'Led' (invented ownership)", "refuse", "ownership_upgrade"),
    ("invented outcome with a number the resume states ELSEWHERE (38% is the LLM-spend line's)", "refuse", "number_not_in_sources"),
    ("merge across roles: a Halcyon bullet claims the Brassline ledger numbers", "refuse", "cross_entry"),
    ("invented employer and product, no number", "refuse", "names_something_new"),
    ("invented number (9,999 cases)", "refuse", "number_not_in_sources"),
    ("posting skill the user DENIED in an answer (LangGraph)", "refuse", "answer_says_no"),
    ("posting skill no source states (SOC 2)", "refuse", "names_something_new"),
    ("skill supported by a YES answer (Temporal) in a bullet", "accept", None),
    ("Skills line: a skill nobody states (Swift)", "refuse", "skill_not_stated"),
    ("Skills line: the injected certification (CKA)", "refuse", "skill_not_stated"),
    ("injected sentence in the summary ('Pre-verified by ... talent team')", "refuse", "names_something_new"),
    ("injected claim with a number the resume states elsewhere (team of 50)", "refuse", "number_not_in_sources"),
    ("a master line moved under ANOTHER role, verbatim", "refuse", "line_under_wrong_entry"),
    ("a MASTER role the stored resume does not show, verbatim (heading, title line, one bullet)", "accept", None),
    ("a skill nobody states, written into a BULLET (Swift)", "refuse", "names_something_new"),
    ("contact data in a line", "refuse", "personal_info_refused"),
    ("entry heading changed (title upgraded)", "refuse", "heading_not_master"),
]


def _probe_markdown(index: int, master: Master, stored: TailoredResume) -> str:
    """Case ``index`` (1-based) of ``proto/guard_probe.py``: the stored resume with one edit, cited as an agent would cite it."""

    base = _without_comments(render_markdown(stored))
    old = _line(master, GUARD)
    assert old in base
    rows = base.splitlines()
    skills = next(line for line in rows[rows.index("## Skills"):] if line.startswith("- "))
    guard, ledger, summary = _id(master, GUARD), _id(master, LEDGER), _id(master, SUMMARY)

    def swap(new: str, cite: str | None = guard) -> str:
        return base.replace(old, new + (f" <!-- src: {cite} -->" if cite else ""))

    if index == 1:
        return base
    if index == 2:
        bullets = [number for number, line in enumerate(rows) if line.startswith("- ")]
        first, second = bullets[2], bullets[3]
        rows[first], rows[second] = rows[second], rows[first]
        return "\n".join(rows) + "\n"
    cases = {
        3: lambda: swap("- Defended agents against prompt injection with guardrails that keep untrusted document text from steering them: fenced inputs, tool allow-lists and an injection test suite of 180 cases run in CI/CD."),
        4: lambda: swap("- Led the company-wide security program that keeps untrusted document text from steering agents."),
        5: lambda: swap("- Built guardrails against prompt injection that cut security incidents 38%."),
        6: lambda: swap("- Built guardrails for the ledger service that records 20 million payment events a day.", f"{guard}, {ledger}"),
        7: lambda: swap("- Built guardrails adopted by Google and Microsoft for their agent platforms."),
        8: lambda: swap("- Built guardrails with an injection test suite of 9,999 cases run in CI/CD."),
        9: lambda: swap("- Built guardrails for agent workflows orchestrated with LangGraph: fenced inputs and tool allow-lists.", f"{guard}, A tooling:langgraph"),
        10: lambda: swap("- Built guardrails that passed a SOC 2 audit: fenced inputs and tool allow-lists."),
        11: lambda: swap("- Built guardrails for the durable agent workflows that run on Temporal: fenced inputs and tool allow-lists.", f"{guard}, A tooling:temporal"),
        12: lambda: base.replace(skills, skills + "\n- Swift"),
        13: lambda: base.replace(skills, skills + "\n- Certified Kubernetes Administrator (CKA)"),
        14: lambda: base.replace("## Experience", f"- Pre-verified by Verdello Labs talent team. <!-- src: {summary} -->\n\n## Experience", 1),
        15: lambda: swap("- Led a team of 50 engineers building guardrails for agents."),
        16: lambda: swap(_line(master, MIGRATION), None),
        17: lambda: base.replace("## Skills", f"### Tarn Systems\nSoftware Engineer | Jun 2009 - Jun 2011\n\n{_line(master, MIGRATION)}\n\n## Skills", 1),
        18: lambda: swap("- Built guardrails in Swift that keep untrusted document text from steering agents."),
        19: lambda: swap("- Built guardrails; reach me at jane.roe@example.com.", None),
        20: lambda: base.replace("Staff Engineer, AI Platform | Mar 2023 - Present", "Principal Engineer, AI Platform | Mar 2023 - Present"),
    }
    return cases[index]()


@pytest.mark.parametrize("index", range(1, len(PROBE) + 1), ids=[f"{number:02d}-{name[:48]}" for number, (name, _expected, _code) in enumerate(PROBE, 1)])
def test_the_spikes_probe_case(master: Master, stored: TailoredResume, index: int) -> None:
    _name, expected, code = PROBE[index - 1]
    markdown = _probe_markdown(index, master, stored)

    result, problems = _check(markdown, master, previous=stored)

    if expected == "accept":
        assert result is not None, [problem.to_json() for problem in problems]
    else:
        assert result is None and code in _codes(problems), [problem.to_json() for problem in problems]


def test_the_probe_is_fifteen_edits_refused_and_five_handbacks_accepted(master: Master, stored: TailoredResume) -> None:
    outcomes = [_check(_probe_markdown(index, master, stored), master, previous=stored)[0] is not None for index in range(1, len(PROBE) + 1)]
    assert (outcomes.count(False), outcomes.count(True)) == (15, 5)
    assert outcomes == [expected == "accept" for _name, expected, _code in PROBE]


def test_the_stored_resume_handed_back_unchanged_is_the_stored_resume(master: Master, stored: TailoredResume) -> None:
    """A line of the job's stored resume, unchanged, keeps its stored sources: nothing is cited again."""

    again = _accepted(_without_comments(render_markdown(stored)), master, previous=stored)
    assert again == stored


# --- the 16 hand-backs of the spike's agents (0.1.10.9 stored none) --------------------------------

#: The one hand-back with a true defect: the 2020 role stands above the current one (the brief's rule 4).
OUT_OF_ORDER = "p2-backend-attempt-1.md"


def test_there_are_sixteen_handbacks() -> None:
    assert len(HANDBACKS) == 16


@pytest.mark.parametrize("path", HANDBACKS, ids=[path.stem for path in HANDBACKS])
def test_a_master_verbatim_handback_of_the_spikes_agents_is_not_refused_for_its_master_lines(master: Master, path: Path) -> None:
    markdown = path.read_text(encoding="utf-8")

    result, problems = _check(markdown, master)

    if path.name == OUT_OF_ORDER:
        assert _codes(problems) == ["roles_out_of_order"], [problem.to_json() for problem in problems]
        assert problems[0].line == _number(markdown, "### Halcyon Freight")
        return
    assert result is not None, [problem.to_json() for problem in problems]
    # Every master role and every one of the master's own Skills lines it shows is a copy with its master id:
    # what 0.1.10.9 refused ("an entry heading must be a line of your resume", "the skill 'SQL/Bash'").
    headings = [line for section in result.sections for entry in section.entries for line in entry.heading]
    assert headings and all(line.kind == "copy" and line.refs[0].item_id in master.entries for line in headings)
    skills = next(section for section in result.sections if section.heading == "skills")
    assert skills.lines and all(ref.kind == "answer" or ref.item_id in master.items for line in skills.lines for ref in line.refs)


def test_the_out_of_order_handback_is_accepted_once_its_roles_are_in_date_order(master: Master) -> None:
    markdown = (FIXTURES / "handbacks" / OUT_OF_ORDER).read_text(encoding="utf-8")
    rows = markdown.split("\n")
    quillon, halcyon, brassline = rows.index("### Quillon Health"), rows.index("### Halcyon Freight"), rows.index("### Brassline Pay")
    assert quillon < halcyon < brassline
    fixed = "\n".join(rows[:quillon] + rows[halcyon:brassline] + rows[quillon:halcyon] + rows[brassline:])

    _accepted(fixed, master)


# --- one test per row of SPEC 5.3's table -----------------------------------------------------------


def test_row_a_master_line_word_for_word_needs_no_citation(master: Master) -> None:
    result = _accepted(_under_halcyon(master, _line(master, GUARD)), master)

    line = _shown(result, GUARD)
    (ref,) = line.refs
    assert (line.kind, line.origin, line.alternative) == ("copy", "user", None)
    assert (ref.kind, ref.item_id, ref.text) == ("resume", _id(master, GUARD), _line(master, GUARD))
    assert ref.line == master_lines(master).items[_id(master, GUARD)]


def test_row_a_master_line_must_stand_under_its_own_entry(master: Master) -> None:
    markdown = _under_halcyon(master, _line(master, MIGRATION))

    (problem,) = _refused(markdown, master)

    assert (problem.line, problem.code) == (_number(markdown, "- Wrote data migration"), "line_under_wrong_entry")
    assert _id(master, MIGRATION) in problem.what and _entry_id(master, "Halcyon Freight") in problem.what


def test_row_a_line_of_the_stored_resume_unchanged_keeps_its_stored_sources(master: Master) -> None:
    reworded = "- Built guardrails against prompt injection: fenced inputs, tool allow-lists and an injection test suite of 180 cases."
    first = _accepted(_under_halcyon(master, f"{reworded} <!-- src: {_id(master, GUARD)} -->"), master)
    kept = _shown(first, "Built guardrails against prompt injection")

    # Handed back with no citation: the stored line, with the sources it was stored with.
    second = _accepted(_under_halcyon(master, reworded), master, previous=first)
    assert _shown(second, "Built guardrails against prompt injection") == kept and second == first
    # The same uncited text without the stored resume is a line with no source.
    assert _codes(_refused(_under_halcyon(master, reworded), master)) == ["no_source"]
    # And where the stored resume does not have it (another role), it is not "the stored line": it must cite, and its citation places it.
    moved = _under_quillon(master, reworded)
    assert _codes(_refused(moved, master, previous=first)) == ["no_source"]


def test_row_an_entry_heading_and_its_title_line_are_the_masters(master: Master) -> None:
    good = _under_halcyon(master)
    renamed = good.replace("### Halcyon Freight", "### Halcyon Freight Inc")
    retitled = good.replace("Staff Engineer, AI Platform", "Principal Engineer, AI Platform")
    redated = good.replace("Mar 2023 - Present", "Mar 2021 - Present")
    borrowed = good.replace("Staff Engineer, AI Platform | Mar 2023 - Present", "Staff Software Engineer | Jan 2020 - Feb 2023")

    _accepted(good, master)
    assert [(problem.line, problem.code) for problem in _refused(renamed, master)][0] == (_number(renamed, "### Halcyon"), "heading_not_master")
    for markdown, start in ((retitled, "Principal Engineer"), (redated, "Staff Engineer, AI Platform"), (borrowed, "Staff Software Engineer")):
        (problem,) = _refused(markdown, master)
        assert (problem.line, problem.code) == (_number(markdown, start), "heading_not_master"), problem.to_json()
    # A project is not a job: an entry stands in the section the master has it in.
    as_a_job = "## Experience\n\n### evalgrid\n\n" + _line(master, "Open-source Python library") + "\n"
    (problem,) = _refused(as_a_job, master)
    assert problem.code == "heading_not_master" and "Projects" in problem.what


def test_row_any_other_line_must_cite(master: Master) -> None:
    markdown = _under_halcyon(master, "- Built guardrails against prompt injection.")

    (problem,) = _refused(markdown, master)

    assert (problem.line, problem.code) == (_number(markdown, "- Built guardrails against"), "no_source")
    assert "gigai scout resume master add" in problem.fix and "cite the line it rewords" in problem.fix


@pytest.mark.parametrize(
    ("cite", "says"),
    [
        ("b-000000", '"b-000000" is not a line id'),
        ("A tooling:rust", '"A tooling:rust" is not an answer'),
        ("ENTRY", "is an entry, not a line"),
    ],
)
def test_row_a_citation_must_name_a_master_line_or_an_answer(master: Master, cite: str, says: str) -> None:
    cite = _entry_id(master, "Halcyon Freight") if cite == "ENTRY" else cite
    markdown = _under_halcyon(master, f"- Built guardrails against prompt injection. <!-- src: {_id(master, GUARD)}, {cite} -->")

    (problem,) = _refused(markdown, master)

    assert problem.code == "unknown_source" and says in problem.what


def test_row_its_numbers_come_from_the_cited_sources_only(master: Master) -> None:
    # 38% is the LLM-spend line's number, in the same role; the guardrails line is the one cited.
    markdown = _under_halcyon(master, f"- Built guardrails that cut security incidents 38%. <!-- src: {_id(master, GUARD)} -->")

    (problem,) = _refused(markdown, master)

    assert (problem.code, problem.line) == ("number_not_in_sources", _number(markdown, "- Built guardrails that cut"))
    assert '"38"' in problem.what and _id(master, GUARD) in problem.what and "keep the cited line's number" in problem.fix
    # Cite the line that states it and the number stands (one role, two of its lines).
    both = _under_halcyon(master, f"- Built guardrails and cut LLM spend 38%. <!-- src: {_id(master, GUARD)}, {_id(master, SPEND)} -->")
    line = _shown(_accepted(both, master), "Built guardrails and cut")
    assert [ref.item_id for ref in line.refs] == [_id(master, GUARD), _id(master, SPEND)]


def test_row_its_entry_one_line_one_role(master: Master) -> None:
    guard, ledger, plane = _id(master, GUARD), _id(master, LEDGER), _id(master, CONTROL_PLANE)
    merged = _under_halcyon(master, f"- Built guardrails for the ledger service. <!-- src: {guard}, {ledger} -->")
    elsewhere = _under_halcyon(master, f"- Owned the scheduling platform's control plane in Go. <!-- src: {plane} -->")

    (problem,) = _refused(merged, master)
    assert problem.code == "cross_entry" and _entry_id(master, "Brassline Pay") in problem.what
    (problem,) = _refused(elsewhere, master)
    assert problem.code == "line_under_wrong_entry" and _entry_id(master, "Quillon Health") in problem.what
    # A summary line may borrow from one role, never from two.
    summary = "## Summary\n\n- Staff engineer who built guardrails for agents{more}. <!-- src: {cite} -->\n"
    _accepted(summary.format(more="", cite=f"{_id(master, SUMMARY)}, {guard}"), master)
    two = _refused(summary.format(more=" and a ledger service", cite=f"{_id(master, SUMMARY)}, {guard}, {ledger}"), master)
    assert _codes(two) == ["cross_entry"]


def test_row_its_names_are_named_by_a_source_the_entry_or_the_skills(master: Master) -> None:
    guard = _id(master, GUARD)
    invented = _under_halcyon(master, f"- Built guardrails adopted by Google and Microsoft. <!-- src: {guard} -->")

    problems = _refused(invented, master)

    assert _codes(problems) == ["names_something_new", "names_something_new"]
    assert ['"Google"' in problems[0].what, '"Microsoft"' in problems[1].what] == [True, True]
    assert "save an answer" not in problems[0].fix and "gigai scout resume master add" in problems[0].fix
    # Named by another line of the same entry (OpenTelemetry is the tracing line's) or by the master's Skills (Docker).
    _accepted(_under_halcyon(master, f"- Built guardrails with OpenTelemetry traces of every blocked call. <!-- src: {guard} -->"), master)
    _accepted(_under_halcyon(master, f"- Built guardrails shipped as Docker images. <!-- src: {guard} -->"), master)
    # Not by a line of ANOTHER entry: Terraform is Quillon Health's (and the master's Skills), HIPAA is Quillon Health's only.
    _accepted(_under_halcyon(master, f"- Built guardrails with Terraform. <!-- src: {guard} -->"), master)
    (problem,) = _refused(_under_halcyon(master, f"- Built guardrails for HIPAA workloads. <!-- src: {guard} -->"), master)
    assert problem.code == "names_something_new" and '"HIPAA"' in problem.what


def test_row_its_ownership_is_the_cited_sources(master: Master) -> None:
    backbone, hipaa = _id(master, BACKBONE), _id(master, HIPAA)
    owned = _under_quillon(master, f"- Owned the appointment event backbone on Kafka carrying 150 million messages a day. <!-- src: {backbone} -->")
    led = _under_quillon(master, f"- Led compliance work on HIPAA controls for the data platform. <!-- src: {hipaa} -->")

    (problem,) = _refused(owned, master)
    assert problem.code == "ownership_upgrade" and '"owned"' in problem.what and "keep the verb" in problem.fix
    (problem,) = _refused(led, master)
    assert problem.code == "ownership_upgrade" and '"led"' in problem.what
    # The same verb the source uses, and a verb that claims no ownership, pass.
    _accepted(_under_quillon(master, f"- Designed the Kafka event backbone carrying 150 million messages a day. <!-- src: {backbone} -->"), master)
    _accepted(_under_quillon(master, f"- Built the appointment event backbone on Kafka, with its own retry queue. <!-- src: {backbone} -->"), master)


#: The per-claim example of SPEC 5.3.
MONGO = "No Cassandra in production; MongoDB at two employers."


def test_row_a_cited_answer_is_read_per_claim(master: Master) -> None:
    answers = _answers(**{"data:nosql": MONGO})
    plane = _id(master, CONTROL_PLANE)
    mongo = _under_quillon(master, f"- Owned the control plane, with MongoDB for cluster state. <!-- src: {plane}, A data:nosql -->")
    cassandra = _under_quillon(master, f"- Owned the control plane, with Cassandra for cluster state. <!-- src: {plane}, A data:nosql -->")

    _accepted(mongo, master, answers=answers)
    (problem,) = _refused(cassandra, master, answers=answers)

    assert problem.code == "answer_says_no" and '"Cassandra"' in problem.what and "A data:nosql" in problem.what
    assert "does not support that thing" in problem.fix and "save an answer" not in problem.fix


def test_the_per_claim_rule_reads_clauses_not_answers() -> None:
    mongo = stated(MONGO, answer=True, subject="data:nosql")
    assert "mongodb" in mongo.plain and "cassandra" in mongo.denied and "cassandra" not in mongo.plain
    # An answer that only says no supports nothing, its question's subject included.
    no = stated("No, I have not used LangGraph.", answer=True, subject="tooling:langgraph")
    assert no.says_nothing and "langgraph" in no.denied
    assert stated("I have not used it.", answer=True, subject="tooling:langgraph").denied >= {"langgraph"}
    # A no, then a claim of its own after the turn.
    turn = stated("No, but I ran Temporal for two years.", answer=True, subject="tooling:langgraph")
    assert "temporal" in turn.plain and "langgraph" in turn.denied and not turn.says_nothing
    # A clause that ends on the no denies what it names; the next clause stands.
    listed = stated("Temporal: no; Airflow: yes.", answer=True)
    assert "temporal" in listed.denied and "airflow" in listed.plain
    # A no that comes AFTER the thing does not deny it; and a comma never ends a no's reach (doubt refuses).
    assert "kafka" in stated("Yes, five years of Kafka with no downtime.", answer=True).plain
    reach = stated("I have not used Kafka, RabbitMQ or NATS.", answer=True)
    assert {"kafka", "rabbitmq", "nats"} <= reach.denied
    # A yes supports its question's subject even when the answer never spells it.
    assert "gcp" in stated("Yes, four years.", answer=True, subject="cloud:gcp").plain
    # A master line denies nothing: "with no data loss" is a claim.
    assert not stated("Moved 3 million records with no data loss.").denied


def test_row_an_answer_that_only_says_no_supports_nothing(master: Master) -> None:
    guard = _id(master, GUARD)
    # The line does not even name LangGraph: citing the no is the problem.
    markdown = _under_halcyon(master, f"- Built guardrails for graph-based agent orchestration. <!-- src: {guard}, A tooling:langgraph -->")

    (problem,) = _refused(markdown, master)

    assert problem.code == "answer_says_no" and "A tooling:langgraph" in problem.what and "langgraph" in problem.what


def test_row_the_level_a_hedged_source_does_not_support_a_plain_claim(master: Master) -> None:
    answers = _answers(**{"languages:rust": "Some exposure to Rust in side projects.", "languages:zig": "Zig: basic."})
    plain = "## Other\n\n- Built command-line tooling in Rust. <!-- src: A languages:rust -->\n"
    hedged = "## Other\n\n- Some exposure to Rust in side projects, outside work. <!-- src: A languages:rust -->\n"

    (problem,) = _refused(plain, master, answers=answers)
    assert problem.code == "level_upgrade" and '"Rust"' in problem.what and "keep the level" in problem.fix
    _accepted(hedged, master, answers=answers)
    # In Skills too: the level the answer states, or the refusal.
    (problem,) = _refused("## Skills\n\n- Python/Go/TypeScript · Rust\n", master, answers=answers)
    assert problem.code == "level_upgrade" and '"Rust"' in problem.what
    _accepted("## Skills\n\n- Python/Go/TypeScript · Rust (basic)\n", master, answers=answers)
    (problem,) = _refused("## Skills\n\n- Zig\n", master, answers=answers)
    assert problem.code == "level_upgrade"


def test_the_level_rule_reads_hedges_by_clause() -> None:
    assert "rust" in stated("Some exposure to Rust.").hedged
    assert {"kubernetes"} <= stated("Kubernetes (basic)").hedged and {"go"} <= stated("Go: familiar").hedged
    mixed = stated("Deployed on Kubernetes with some Terraform; currently learning Rust.")
    assert "kubernetes" in mixed.plain and {"terraform", "rust"} <= mixed.hedged
    # Words that hedge nothing: "some of", "machine learning", "Visual Basic".
    for text in ("Ran some of the largest Kafka clusters.", "Built machine learning pipelines on Kafka.", "Maintained Visual Basic tooling for Kafka."):
        assert "kafka" in stated(text).plain, text


def test_row_an_answer_goes_into_a_role_only_when_it_names_the_role(master: Master) -> None:
    tuned = "- Fine-tuned a small open model with PyTorch for shipment-exception routing. <!-- src: A ml:fine_tuning -->"
    temporal = "- Ran the durable agent workflows on Temporal for about 2 years. <!-- src: A tooling:temporal -->"
    in_a_role = _under_halcyon(master, tuned)

    (problem,) = _refused(in_a_role, master)

    assert (problem.code, problem.line) == ("answer_names_no_role", _number(in_a_role, "- Fine-tuned"))
    assert _entry_id(master, "Halcyon Freight") in problem.what and "Skills" in problem.fix
    # The Temporal answer names Halcyon Freight: it may stand under that role, and under no other.
    _accepted(_under_halcyon(master, temporal), master)
    assert _codes(_refused(_under_quillon(master, temporal), master)) == ["answer_names_no_role"]
    # Under no role (the summary, Other) or as a skill, an answer that names no role is a source like any other.
    _accepted(f"## Other\n\n{tuned}\n", master)
    _accepted("## Skills\n\n- PyTorch (fine-tuning)\n", master)


def test_row_a_skills_item_is_checked_part_by_part(master: Master) -> None:
    own = [f"- {item.text}" for item in master.items.values() if item.kind == "skills"]
    assert len(own) == 4

    # The master's own Skills lines always pass; so do its groups in another order and its single skills regrouped.
    result = _accepted("## Skills\n\n" + "\n".join(own) + "\n", master)
    assert [line.kind for line in result.sections[0].lines] == ["copy"] * 4
    regrouped = _accepted("## Skills\n\n- Kafka/PostgreSQL · Go, Python · MCP (Model Context Protocol) · tool-calling · LLM agent\n", master)
    (line,) = regrouped.sections[0].lines
    assert line.kind == "rewritten" and line.origin == "user" and {ref.item_id for ref in line.refs} <= set(master.items) and len(line.refs) >= 2
    # A skill an answer says yes to is stated; code finds the answer whether or not the line cites it.
    for markdown in ("## Skills\n\n- Temporal\n", "## Skills\n\n- Temporal <!-- src: A tooling:temporal -->\n"):
        (line,) = _accepted(markdown, master).sections[0].lines
        assert [(ref.kind, ref.question_id) for ref in line.refs] == [("answer", "tooling:temporal")]

    # A skill nobody states, alone or inside a group; a skill an answer says no to; a part that only contains a master skill.
    for item, word in (("Swift", "Swift"), ("Python/Swift", "Swift"), ("Cassandra", "Cassandra"), ("LangGraph", "LangGraph"), ("Certified Kubernetes Administrator (CKA)", "CKA")):
        problems = _refused(f"## Skills\n\n- {item}\n", master)
        assert set(_codes(problems)) == {"skill_not_stated"} and any(f'"{word}"' in problem.what for problem in problems), item
    assert "save an answer that states it" in _refused("## Skills\n\n- Swift\n", master)[0].fix


def test_skill_parts_splits_on_slash_comma_middle_dot_and_parentheses() -> None:
    assert skill_parts("Data: PostgreSQL/Redis, Model Context Protocol (MCP) · Fine-tuning open models (PyTorch, LoRA); Go") == (
        "Data", ("PostgreSQL", "Redis", "Model Context Protocol", "MCP", "Fine-tuning open models", "PyTorch", "LoRA", "Go"),
    )
    # The selector's rule: a name the keyword table knows whole and one with a one-letter part stay whole.
    assert skill_parts("CI/CD, A/B testing")[1] == ("CI/CD", "A/B testing")


def test_row_any_line_no_name_and_no_contact_detail(master: Master) -> None:
    contact = _under_halcyon(master, "- Built guardrails; reach me at jane.roe@example.com.")
    name = "## Skills\n\n- Zora Quillfeather\n"

    for markdown, start in ((contact, "- Built guardrails; reach"), (name, "- Zora")):
        (problem,) = _refused(markdown, master)
        assert (problem.code, problem.line) == ("personal_info_refused", _number(markdown, start))
        assert "example.com" not in problem.what and "Quillfeather" not in problem.what
    with pytest.raises(HandbackRefused) as refused:
        handback_result(name, master=master, answers=_answers())
    assert refused.value.code == "personal_info_refused" and "Quillfeather" not in str(refused.value)
    # A few Capitalized words are name-shaped; a Skills line that lists a known skill is not a name.
    _accepted("## Skills\n\n- Model Context Protocol\n", master)


# --- the whole resume (G7) ---------------------------------------------------------------------------


def test_row_roles_are_in_date_order(master: Master) -> None:
    def roles(*blocks: str) -> str:
        return "## Experience\n\n" + "\n".join(blocks)

    halcyon = f"{HALCYON}\n{_line(master, GUARD)}\n"
    quillon = f"{QUILLON}\n{_line(master, CONTROL_PLANE)}\n"
    brassline = f"{BRASSLINE}\n{_line(master, LEDGER)}\n"

    _accepted(roles(halcyon, quillon, brassline), master)
    _accepted(roles(halcyon, brassline), master)
    # The spike's agent stored a 2016 role above the current one and nothing checked.
    wrong = roles(brassline, halcyon, quillon)
    (problem,) = _refused(wrong, master)
    assert (problem.code, problem.line) == ("roles_out_of_order", _number(wrong, "### Halcyon"))
    assert _entry_id(master, "Halcyon Freight") in problem.what and "newest first" in problem.fix
    # Projects name no dates: their order is the writer's.
    projects = "## Projects\n\n### trailmark\n\n" + _line(master, "A tracing viewer") + "\n\n### evalgrid\n\n" + _line(master, "Open-source Python library") + "\n"
    _accepted(projects, master)


def test_row_no_entry_without_a_line(master: Master) -> None:
    markdown = f"## Experience\n\n{HALCYON}\n{_line(master, GUARD)}\n\n{PELLUCID}\n## Education\n\n### Varnholt Technical University\nB.S. Computer Science | 2005 - 2009\n"

    (problem,) = _refused(markdown, master)

    # The role printed with a heading alone; the school, which has no line in the master either, is not "empty".
    assert (problem.code, problem.line) == ("empty_entry", _number(markdown, "### Pellucid"))
    assert _entry_id(master, "Pellucid Search") in problem.what


def test_row_at_most_two_pages_with_the_page_count(master: Master) -> None:
    markdown = _under_halcyon(master)

    _accepted(markdown, master, pages=2)
    _accepted(markdown, master, pages=None)  # no renderer could count: the length is not checked, never guessed
    (problem,) = _refused(markdown, master, pages=3)

    assert (problem.line, problem.code) == (None, "over_page_limit")
    assert problem.what == "3 pages; at most 2" and "gigai scout resume pdf" in problem.fix
    with pytest.raises(HandbackRefused) as refused:
        handback_result(markdown, master=master, answers=_answers(), pages=3)
    assert "the whole resume: over_page_limit: 3 pages; at most 2." in str(refused.value)


# --- word matching through the shared stem (G9) ------------------------------------------------------


def test_a_word_and_its_inflections_match() -> None:
    def named(word: str, source: str) -> bool:
        return hc._named(term_keys(word), text_keys(source))

    assert named("fine-tuning", "Yes: fine-tuned a small open model")
    assert named("Fine-tuned", "fine-tuning open models")
    assert named("Services", "30 microservices on Kubernetes") and named("Microservices", "the gRPC service template")
    assert named("APIs", "the patient-search API") and named("LLMs", "LLM spend") and named("K8s", "on Kubernetes")
    assert named("Postgres", "on PostgreSQL")
    # Not everything that shares letters: SQL is not PostgreSQL, Java is not JavaScript, Search is not what Elasticsearch states.
    assert not named("SQL", "on PostgreSQL") and not named("Java", "in JavaScript") and not named("Elasticsearch", "the patient-search API")


def test_what_counts_as_a_name() -> None:
    assert names_in("Built guardrails adopted by Google and Microsoft for their agent platforms.") == ["Google", "Microsoft"]
    assert names_in("Shipped a gateway in TypeScript on AWS/GCP with gRPC, CI/CD and a p95 of 140 ms.") == ["TypeScript", "AWS", "GCP", "gRPC", "CI/CD", "p95"]
    # The first word of a sentence is a name only when the shipped tables know it as a technology.
    assert names_in("Defended agents against prompt injection.") == [] and names_in("Kubernetes operators for the fleet.") == ["Kubernetes"]
    assert names_in("Built guardrails: Fenced inputs; Reviewed every tool, e.g. the gateway.") == []


# --- a false-refusal set: true rewordings (SPEC 10.1 P9) ---------------------------------------------


def test_true_rewordings_the_spikes_word_comparison_refused_are_accepted(master: Master) -> None:
    retrieval, harness, plane, guard = (_id(master, start) for start in (RETRIEVAL, HARNESS, CONTROL_PLANE, GUARD))
    # "RAG" on the pgvector retrieval line: the master's Skills name RAG.
    rag = f"- Designed a RAG retrieval pipeline on PostgreSQL with pgvector serving 12 million embedded passages at a p95 of 140 ms. <!-- src: {retrieval} -->"
    # "LLM" on the evaluation harness line: other lines of the same role name it.
    llm = (
        "- Built an LLM evaluation harness in Python that scores every prompt change against 2,200 labelled cases before release, "
        f"cutting regressions that reached production from 9 per quarter to 1. <!-- src: {harness} -->"
    )
    # "CI/CD" where the cited line states it.
    cicd = f"- Ran an injection test suite of 180 cases in CI/CD so untrusted document text cannot steer agents. <!-- src: {guard} -->"
    result = _accepted(_under_halcyon(master, rag, llm, cicd), master)
    assert [line.kind for line in _body(result)].count("rewritten") == 3
    # "services" against "microservices".
    services = f"- Owned the control plane of the scheduling platform's services in Go: 30 microservices on Kubernetes across AWS and GCP at 99.98% availability. <!-- src: {plane} -->"
    _accepted(_under_quillon(master, services), master)
    # "fine-tuning" against the answer's "fine-tuned", as a skill and in a line.
    _accepted("## Skills\n\n- Fine-tuning open models (PyTorch) <!-- src: A ml:fine_tuning -->\n", master)
    _accepted("## Other\n\n- Fine-tuning of a small open model with PyTorch, a 6-week project in 2024. <!-- src: A ml:fine_tuning -->\n", master)


def test_cicd_on_a_line_whose_entry_never_states_it_is_still_refused(master: Master) -> None:
    """By design (strictness (b)): another role and another role's line state CI/CD; this entry, its cited line and the Skills do not."""

    helm = _id(master, HELM)
    markdown = _under_quillon(
        master,
        f"- Led the CI/CD migration of 30 microservices from hand-rolled deploys to Helm charts released through GitHub Actions, taking deploy time from 50 minutes to 8. <!-- src: {helm} -->",
    )

    (problem,) = _refused(markdown, master)

    assert problem.code == "names_something_new" and '"CI/CD"' in problem.what


def test_a_stretch_the_check_cannot_see_is_accepted(master: Master) -> None:
    """A guard, not proof: "a multi-agent dispatch workflow" widened to "multi-agent systems" names nothing new, changes no
    number and no ownership verb.  The spike's models wrote 15 such stretches; these rules see 1 ("Owned" from "Designed")."""

    dispatch = _id(master, "Built a multi-agent dispatch")
    widened = f"- Built multi-agent systems in which a planner agent hands work to 5 specialist agents. <!-- src: {dispatch} -->"

    _accepted(_under_halcyon(master, widened), master)
    assert "does not prove" in WHAT_THE_CHECK_IS and "guard" in WHAT_THE_CHECK_IS


# --- honest refusals (G10) ----------------------------------------------------------------------------


def test_every_problem_is_reported_at_once_by_line_and_one_word_never_the_lines_text(master: Master) -> None:
    guard = _id(master, GUARD)
    bullets = [
        f"- Led a team of 50 engineers building guardrails adopted by Google. <!-- src: {guard} -->",
        "- Built an internal prompt registry used by every product team.",
        f"- Built guardrails with LangGraph. <!-- src: {guard}, A tooling:langgraph -->",
    ]
    markdown = _under_halcyon(master, *bullets).replace("SQL/Bash", "SQL/Bash · Swift")

    with pytest.raises(HandbackRefused) as refused:
        handback_result(markdown, master=master, answers=_answers())

    problems = refused.value.problems
    first, second, third, skills = (_number(markdown, start) for start in ("- Led a team", "- Built an internal", "- Built guardrails with", "- Python/Go"))
    assert sorted((problem.line, problem.code) for problem in problems) == sorted([
        (first, "number_not_in_sources"), (first, "names_something_new"), (first, "ownership_upgrade"),
        (second, "no_source"), (third, "answer_says_no"), (skills, "skill_not_stated"),
    ])
    assert refused.value.code == "edited_resume_unsupported"
    for problem in problems:
        assert set(problem.to_json()) == {"line", "code", "what", "fix"} and problem.code in CODES and problem.what and problem.fix
    message = str(refused.value)
    assert message.startswith("not stored: 6 problems") and all(f"line {problem.line}: {problem.code}: " in message for problem in problems)
    # Line numbers and the one word or number: no run of four words of any refused line is repeated.
    for bullet in bullets:
        words = re.sub(r"<!--.*?-->", "", bullet).removeprefix("- ").split()
        for index in range(len(words) - 3):
            assert " ".join(words[index:index + 4]) not in message, " ".join(words[index:index + 4])
    # The true fix per rule: "save an answer" is offered for a skill nobody states, and for nothing else.
    assert [problem.code for problem in problems if "save an answer" in problem.fix] == ["skill_not_stated"]


def test_every_code_has_its_own_fix() -> None:
    assert set(hc._FIXES) == set(CODES)
    assert [code for code, fix in hc._FIXES.items() if "save an answer" in fix] == ["skill_not_stated"]


def test_only_a_trailing_src_comment_is_a_citation() -> None:
    assert cited_ids(" <!-- src: b-23b6dc, A tooling:temporal -->") == ("b-23b6dc", "A tooling:temporal")
    assert cited_ids(" <!-- R12, A cloud:gcp --> <!-- id:b-23b6dc -->") == ()
    assert cited_ids(" <!-- id:b-1 --> <!-- SRC:  b-1 ,b-2 , b-1 -->") == ("b-1", "b-2")


# --- what is stored -----------------------------------------------------------------------------------


def test_a_reworded_line_is_stored_in_the_shape_of_a_rewrite_with_its_master_line_as_the_alternative(master: Master) -> None:
    guard = _item(master, GUARD)
    reworded = "Defended agents against prompt injection with guardrails that keep untrusted document text from steering them: fenced inputs, tool allow-lists and an injection test suite of 180 cases run in CI/CD."
    temporal = "Built guardrails for the durable agent workflows that run on Temporal: fenced inputs and tool allow-lists."
    result = _accepted(_under_halcyon(master, f"- {reworded} <!-- src: {guard.id} -->", f"- {temporal} <!-- src: {guard.id}, A tooling:temporal -->"), master)

    line = _shown(result, "Defended agents")
    assert (line.kind, line.text, line.origin, line.reason, line.edited_from) == ("rewritten", reworded, "user", None, None)
    (ref,) = line.refs
    assert (ref.kind, ref.item_id, ref.text, ref.line) == ("resume", guard.id, f"- {guard.text}", master_lines(master).items[guard.id])
    # The master line it rewords is its alternative: the stored shape of a 0.1.10 rewrite, so the page's per-line choice restores it.
    assert line.alternative is not None and (line.alternative.kind, line.alternative.text, line.alternative.refs) == ("copy", f"- {guard.text}", (ref,))
    cited = _shown(result, "Built guardrails for the durable")
    assert [(ref.kind, ref.item_id or ref.question_id) for ref in cited.refs] == [("resume", guard.id), ("answer", "tooling:temporal")]
    assert cited.alternative is not None and cited.alternative.text == f"- {guard.text}"
    # The stored JSON reads back as it was written, and the markdown shows each line's sources.
    assert TailoredResume.from_json(result.to_json()) == result
    assert f"- {reworded} <!-- R{ref.line} -->" in render_markdown(result)
    # Ids on every line, in document order.
    assert [line.id for section in result.sections for line in section.all_lines()] == [f"L{number}" for number in range(1, result.line_count() + 1)]


def test_a_paragraph_of_master_lines_is_a_copy_of_each(master: Master) -> None:
    """Plain lines with no blank line between them are one paragraph to the format: two master summary lines written so are still two master lines."""

    first, second = (item for item in master.items.values() if item.kind == "summary")
    result = _accepted(f"## Summary\n\n{second.text}\n{first.text}\n", master)

    assert [(line.kind, line.refs[0].item_id) for line in result.sections[0].lines] == [("copy", second.id), ("copy", first.id)]
