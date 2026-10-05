"""0.1.11 N3 (SPEC section 3): the selection. What code makes of the assessment's pick, or of no pick at all.

ONE ENTRY, ``settle(master, assessment, requirements, today)``: what
``quick_assess.run_quick_assessment``, the pipeline's ``pick`` step, the
re-pick action and the pick eval (``tools/pick_probe.py``) all call.  It never
calls a model.  Pure except the page measurement (the shipped template at the
selector's spacing, ``tailor_master.measure_pages``).

WHAT IS REUSED, AND WHAT IS NEW.  The tree already holds a selector and a fit
(0.1.10.9 master P2/P4, ``sel-4``), and this module adds no second one:

- the code selector (``master_selection.select``): the requirement rows of an
  assessment as ``SelectionPosting.cited`` (a row's supporters are exactly the
  lines it names; ``assess_master.cited_requirements`` now reads a v9 row's
  ``sources``), the Skills section kept whole and matched inside a group
  (``skill_atoms``), every degree, roles in date order, no role without a line;
- the candidate set as numbered markdown with each line's master id, and the
  copy-all answer of it (``tailor_master.JobCandidates``, ``render_selection``);
- the checks a tailoring's answer goes through and the settled result
  (``validate_tailored_output``, ``apply_no_loss``, ``finish_tailoring``,
  ``ensure_skills_line``): every line of a selection is a COPY of a master line;
- the fit and its record (``tailor_master.cut_order``, ``tailor_length.fit_by_cuts``):
  the fewest cuts that fit, a recent role keeps its best line, an old role
  that loses its last line goes whole, what was cut is on the result
  (``TailoredResume.length``) so one Restore puts it back;
- Picked / Left out (``tailor_master.selection_record``) and its conflicts;
- the fallback (``tailor_master.code_only``): the selector's own 2-page selection.

New here: the validation of a model's pick (V1 to V8), the order the fit cuts a
PICK in (3.2: the model's ranking and the rows' sources, where the selector's
own fit reads its own scores), the Other lines and the Skills in that order,
the conflict codes of 3.3, the fill of a pick that leaves room, and the choice
between the pick and the fallback.

VALIDATING THE PICK (3.1), against the master revision the assessment read.
Every finding is a ``Problem`` (a code and the id); nothing here raises.

== ======================================================= ==========================================
V1 every id is a line of that master                       dropped, ``unknown_id``
V2 the id is a summary, a bullet or an Other line          a Skills line or an entry id: ``not_selectable``
V3 each id once                                            later copies dropped, ``duplicate``
V4 ``summary`` is one summary id                           the selector's choice, ``summary_by_code``
V5 ``section_order`` holds experience and projects once    experience first, ``section_order_by_code``
V6 at least ``MIN_PICK`` bullets remain                    the whole pick is refused: ``pick_too_small``
V7 every ``met`` mandatory row with a master-line source   its strongest source line is added,
   has one in the pick                                     ``added_for_coverage``
V8 no recent role without a line                           its strongest source line, else the
                                                           selector's best line, ``recent_role_present``
== ======================================================= ==========================================

A pinned line the pick left out is added too (``pinned_line``): a pin is the
user's own must-keep.  Fixed by code whatever the model returned: roles in date
order, newest first (the model's order decides the lines inside an entry and
the order of the projects); the Skills section whole; every degree; nothing
reworded.

THE 2-PAGE FIT (3.2).  Page fit is a constraint, measured, never estimated.
PROTECTED, cut only when nothing else can go: the last printed source of a
``met`` mandatory row, then the profile's pins.  Everything else, first cut
first:

1. lines that are the source of no requirement row, the model's last-ranked first;
2. lines that are a further source of a row, the weakest evidence first
   (stated, then quantified, then backed), then the model's last-ranked first;
3. between two lines equal on all of that, the one from the older role;
4. Other lines, the model's last-ranked first;
5. Skills, what nothing asks for first (``skills_do_not_fit``).

A role or project left with no line stops printing.  FILL: a pick that leaves
room on the last page gets the unpicked source lines of mandatory rows, then
the selector's best unpicked lines of the recent roles (``room_left``).

CONFLICTS (3.3).  When the protected lines alone do not fit, the selection is
still cut to the page limit and each loss is a ``PickConflict``:
``mandatory_evidence_does_not_fit``, ``pinned_line_does_not_fit``,
``skills_do_not_fit``.  A conflict makes the resume not ready
(``suggestions.check_selection``) and is never resolved silently.

THE FALLBACK (3.4): the code selector's own selection, with the reason:
``no_pick`` (a Matched answer with no usable pick), ``pick_too_small`` (V6),
``pick_failed`` (settling the model's pick raised), ``draft_requested`` (the
user asked for a draft on a held job; ``draft`` is then true) and
``master_revision_unreadable`` (the caller could not load the revision the
assessment read and passes the master as it is).  The fallback goes through the
same final-selection check, and keeps the model's raw pick and its problems.

``picked_by`` keeps its two values, ``model`` and ``code``, and ``candidates``
is ``evidence`` for the assessment's pick and ``view`` for the fallback: no
enum of the stored job resume grows.  What tells this resume from a 0.1.10
tailoring is ``producer.callable``: ``scout.pick``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

from . import suggestions
from . import tailor_master as tm
from .find_jobs.assess_contracts import PICK_SECTIONS, AssessmentPick
from .master_resume import KIND_BULLET, KIND_OTHER, KIND_SKILLS, KIND_SUMMARY, Master
from .master_selection import (
    MAX_PAGES,
    SELECTOR_VERSION,
    LineReason,
    Requirement,
    Selected,
    SelectionPosting,
    SelectionProfile,
    is_old_role,
    render_selection,
    select,
)
from .tailor_length import STATUS_UNMEASURED, Measure, fit_by_cuts
from .tailored_resume import LENGTH_RULE, TailorJob, TailoredResume, apply_no_loss, render_markdown, validate_tailored_output

#: Names the validation rules, the cut order and the fill above; stored with a selection (a change re-picks on request).
PICK_RULES_VERSION = "pick-rules:1"
#: V6: a pick that keeps fewer bullets than this is refused whole (a master with fewer bullets: all of them).
MIN_PICK = 8
#: How many lines the fill offers a pick that leaves room.
FILL_LINES = 12

PRODUCER_CALLABLE = "scout.pick"
PRODUCER_VERSION = "1"
PRODUCER_ACTOR = "scout-pick"

PROBLEM_UNKNOWN_ID = "unknown_id"
PROBLEM_NOT_SELECTABLE = "not_selectable"
PROBLEM_DUPLICATE = "duplicate"
PROBLEM_SUMMARY_BY_CODE = "summary_by_code"
PROBLEM_SECTION_ORDER_BY_CODE = "section_order_by_code"
PROBLEM_PICK_TOO_SMALL = "pick_too_small"

ADDED_FOR_COVERAGE = "added_for_coverage"
ADDED_RECENT_ROLE = "recent_role_present"
ADDED_PINNED = "pinned_line"
ADDED_ROOM_LEFT = "room_left"

FALLBACK_NO_PICK = "no_pick"
FALLBACK_TOO_SMALL = PROBLEM_PICK_TOO_SMALL
FALLBACK_MASTER_REVISION = "master_revision_unreadable"
FALLBACK_PICK_FAILED = "pick_failed"
FALLBACK_DRAFT = "draft_requested"
FALLBACKS: tuple[str, ...] = (FALLBACK_NO_PICK, FALLBACK_TOO_SMALL, FALLBACK_MASTER_REVISION, FALLBACK_PICK_FAILED, FALLBACK_DRAFT)

CONFLICT_EVIDENCE = "mandatory_evidence_does_not_fit"
CONFLICT_PIN = "pinned_line_does_not_fit"
CONFLICT_SKILLS = "skills_do_not_fit"
CONFLICT_OVER = "over_page_limit"

#: ``resume.origin`` of a selection: who made the stored job resume it names.
ORIGIN_PICK = "pick"

_STRENGTH = {"backed": 3, "quantified": 2, "stated": 1}
_UNFITTED = 10**6
_CODE_ORDER = PICK_SECTIONS


class PickError(RuntimeError):
    """No selection could be made (no renderer to measure pages with); ``code`` is recorded, the assessment stays stored."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Problem:
    """One thing validation found in the model's pick: a code and the id it is about (``None``: the pick as a whole)."""

    code: str
    id: str | None = None

    def to_json(self) -> dict[str, object]:
        return {"code": self.code, "id": self.id}


@dataclass(frozen=True)
class Added:
    """One line code added to the pick, why, and for which requirement row (when it has one)."""

    id: str
    code: str
    requirement: str | None = None

    def to_json(self) -> dict[str, object]:
        return {"id": self.id, "code": self.code, "requirement": self.requirement}


@dataclass(frozen=True)
class PickConflict:
    """Something the page limit kept out although the rules say it stays (3.3): shown first, never silent."""

    code: str
    requirement: str | None = None
    lines: tuple[str, ...] = ()
    cut: bool = True

    def to_json(self) -> dict[str, object]:
        return {"code": self.code, "requirement": self.requirement, "lines": list(self.lines), "cut": self.cut}


# --- validating the model's pick (3.1) -----------------------------------------------------------------------


@dataclass(frozen=True)
class Validated:
    """A model's pick after V1 to V8: what stays, what code added, what was found. ``refused``: the fallback code, or ``None``."""

    summary: str | None
    section_order: tuple[str, ...]
    #: Bullets and Other lines, the model's order; the lines code added stand after them.
    lines: tuple[str, ...]
    problems: tuple[Problem, ...] = ()
    added: tuple[Added, ...] = ()
    refused: str | None = None


def _roles(master: Master, today: date) -> tuple[list, set[str]]:
    roles = sorted(master.entries_in("experience"), key=tm._newest_first)  # noqa: SLF001 - the tailor fit's own date order
    return roles, {entry.id for entry in roles if is_old_role(entry, today)}


def _strongest(master: Master, candidates: Sequence[str], rank: Mapping[str, int]) -> str:
    """The strongest of ``candidates``: backed over quantified over stated, then the given order, then the newer role."""

    def key(item_id: str) -> tuple[int, int, int]:
        return (-_STRENGTH[master.items[item_id].strength], candidates.index(item_id), rank.get(master.items[item_id].entry_id or "", len(rank)))

    return min(candidates, key=key)


def validate_pick(
    master: Master, pick: AssessmentPick, requirements: Sequence[suggestions.RequirementRow], *, today: date, code_summary: str | None = None,
    values: Mapping[str, float] | None = None, pins: Sequence[str] = (),
) -> Validated:
    """V1 to V8 (the module text). Pure: nothing is measured here.

    ``code_summary``: the summary the selector would show (V4). ``values``:
    the selector's worth of every line (V8 picks a role's best line by it).
    """

    problems: list[Problem] = []
    lines: list[str] = []
    summary_in_lines: str | None = None
    for item_id in pick.lines:
        item = master.items.get(item_id)
        if item is None:
            problems.append(Problem(PROBLEM_NOT_SELECTABLE if item_id in master.entries else PROBLEM_UNKNOWN_ID, item_id))
        elif item.kind == KIND_SKILLS:
            problems.append(Problem(PROBLEM_NOT_SELECTABLE, item_id))
        elif item_id in lines or item_id == summary_in_lines:
            problems.append(Problem(PROBLEM_DUPLICATE, item_id))
        elif item.kind == KIND_SUMMARY:
            summary_in_lines = summary_in_lines or item_id  # a summary among the lines is the summary the model means
        else:
            lines.append(item_id)

    # V4: one summary.
    summary = pick.summary if pick.summary in master.items and master.items[pick.summary].kind == KIND_SUMMARY else summary_in_lines
    if summary is None and code_summary is not None:
        summary = code_summary
        problems.append(Problem(PROBLEM_SUMMARY_BY_CODE, pick.summary))
    # V5: the two sections the model orders, each once.
    section_order = pick.section_order
    if sorted(section_order) != sorted(_CODE_ORDER):
        section_order = _CODE_ORDER
        problems.append(Problem(PROBLEM_SECTION_ORDER_BY_CODE))
    # V6: enough bullets to be a pick at all.
    entry_bullets = [item_id for item_id in lines if master.items[item_id].kind == KIND_BULLET and master.items[item_id].section in ("experience", "projects")]
    in_master = sum(1 for item in master.items.values() if item.kind == KIND_BULLET and item.section in ("experience", "projects"))
    if len(entry_bullets) < min(MIN_PICK, in_master):
        problems.append(Problem(PROBLEM_PICK_TOO_SMALL))
        return Validated(summary, section_order, tuple(lines), tuple(problems), (), FALLBACK_TOO_SMALL)

    roles, old = _roles(master, today)
    rank = {entry.id: index for index, entry in enumerate(roles)}
    added: list[Added] = []
    shown = set(lines) | ({summary} if summary else set())

    def add(item_id: str, code: str, requirement: str | None = None) -> None:
        lines.append(item_id)
        shown.add(item_id)
        added.append(Added(item_id, code, requirement))

    # V7: every met mandatory row keeps a master line it names.
    for row in requirements:
        if row.status != "met" or not row.mandatory:
            continue
        sources = [source for source in row.master_sources() if source in master.items and master.items[source].kind in (KIND_SUMMARY, KIND_BULLET, KIND_OTHER)]
        selectable = [source for source in sources if master.items[source].kind != KIND_SUMMARY]
        if not selectable or any(source in shown for source in sources):
            continue
        add(_strongest(master, selectable, rank), ADDED_FOR_COVERAGE, row.id)
    # The profile's pins: the user's own must-keep lines.
    for item_id in pins:
        if item_id in master.items and master.items[item_id].kind in (KIND_BULLET, KIND_OTHER) and item_id not in shown:
            add(item_id, ADDED_PINNED)
    # V8 (A3): a recent role always shows a line.
    sourced = [source for row in requirements for source in row.master_sources() if source in master.items]
    for entry in roles:
        if entry.id in old or any(bullet in shown for bullet in entry.bullets) or not entry.bullets:
            continue
        named = [bullet for bullet in dict.fromkeys(sourced) if bullet in entry.bullets]
        if named:
            add(_strongest(master, named, rank), ADDED_RECENT_ROLE)
        else:
            worth = values or {}
            add(max(entry.bullets, key=lambda bullet: (worth.get(bullet, 0.0), -master.items[bullet].order)), ADDED_RECENT_ROLE)
    return Validated(summary, section_order, tuple(lines), tuple(problems), tuple(added))


# --- the candidate set of a pick, and its fit (3.2) ---------------------------------------------------------------


def _requirements(master: Master, rows: Sequence[suggestions.RequirementRow], texts: Mapping[str, str], rank: Mapping[str, int]) -> tuple[Requirement, ...]:
    """The rows as the fit reads them: each row's master-line sources, strongest first; only a ``met`` mandatory row protects one."""

    out: list[Requirement] = []
    for row in rows:
        lines = [source for source in dict.fromkeys(row.master_sources()) if source in master.items and master.items[source].kind in (KIND_BULLET, KIND_OTHER)]
        if not lines:
            continue
        ordered = sorted(lines, key=lambda item_id: (-_STRENGTH[master.items[item_id].strength], lines.index(item_id), rank.get(master.items[item_id].entry_id or "", len(rank))))
        out.append(Requirement(row.id, texts.get(row.id, ""), row.mandatory and row.status == "met", tuple(ordered), cited=True))
    return tuple(out)


def _values(master: Master, lines: Sequence[str], requirements: Sequence[Requirement], rank: Mapping[str, int]) -> dict[str, float]:
    """Each line's place in the cut order of 3.2: a smaller number is cut first (``tailor_master.cut_order`` reads it).

    0.1.11 (C1): a line a row rests on outlives the lines that support nothing, and among those the model's order decides;
    the line's strength no longer reorders them (the judge's T20: a nice-to-have's defining line, 7th in the pick, went while
    the 13th stayed). The last printed source of a met mandatory row is protected by the cut order's own tier.
    """

    sourced = {item_id for requirement in requirements for item_id in requirement.supporters}

    count = len(lines) + 1
    roles = len(rank) + 1
    out: dict[str, float] = {}
    for position, item_id in enumerate(lines):
        item = master.items[item_id]
        # The model's ranking: its first line stays longest. The older role only between two lines otherwise equal.
        place = (count - position) + (roles - rank.get(item.entry_id or "", roles)) / (roles + 1)
        out[item_id] = place + (count if item_id in sourced else 0.0)
    return out


def _reasons(
    master: Master, shown: Sequence[str], requirements: Sequence[Requirement], added: Sequence[Added], *, dropped_other: Sequence[str] = (),
) -> tuple[LineReason, ...]:
    """Why every line of the master is in the pick or not, in the words the job page shows (SPEC section 6, Picked / Left out)."""

    supports: dict[str, list[str]] = {}
    for requirement in requirements:
        for item_id in requirement.supporters:
            supports.setdefault(item_id, []).append(requirement.id)
    by_code = {item.id: item for item in added}
    picked = set(shown)
    out: list[LineReason] = []
    for item in master.items.values():
        if item.kind not in (KIND_SUMMARY, KIND_BULLET, KIND_OTHER):
            continue
        if item.id in picked:
            extra = by_code.get(item.id)
            if extra is not None and extra.code == ADDED_FOR_COVERAGE:
                reason = LineReason(item.id, True, ADDED_FOR_COVERAGE, f"added by Scout: the evidence for {extra.requirement}")
            elif extra is not None and extra.code == ADDED_RECENT_ROLE:
                reason = LineReason(item.id, True, ADDED_RECENT_ROLE, "added by Scout: a recent role always shows a line")
            elif extra is not None and extra.code == ADDED_PINNED:
                reason = LineReason(item.id, True, ADDED_PINNED, "added by Scout: pinned on this profile")
            elif extra is not None:
                reason = LineReason(item.id, True, ADDED_ROOM_LEFT, "added by Scout: room left on the page")
            elif item.id in supports:
                reason = LineReason(item.id, True, "supports_requirement", "supports " + ", ".join(supports[item.id]))
            elif item.kind == KIND_SUMMARY:
                reason = LineReason(item.id, True, "summary_variant", "the summary shown for this job")
            else:
                reason = LineReason(item.id, True, "picked_by_assessment", "picked by the assessment")
            out.append(reason)
        elif item.id in dropped_other:
            out.append(LineReason(item.id, False, "cut_for_length", "cut for length: Other lines go before any line that is a requirement's evidence"))
        elif item.kind == KIND_SUMMARY:
            out.append(LineReason(item.id, False, "summary_other_variant", "another summary is shown for this job"))
        else:
            out.append(LineReason(item.id, False, "not_picked", "not picked by the assessment"))
    return tuple(out)


@dataclass(frozen=True)
class _Built:
    candidates: tm.JobCandidates
    context: object
    settled: TailoredResume


def _candidates(
    master: Master, base: Selected, profile: SelectionProfile, posting: SelectionPosting, *, summary: str | None, lines: Sequence[str], skills: Sequence[str],
    requirements: Sequence[Requirement], added: Sequence[Added], rank: Mapping[str, int], old: set[str], dropped_other: Sequence[str] = (),
) -> tm.JobCandidates:
    """The candidate set of a pick: its lines under their entries, every degree, the Skills section; numbered like any resume."""

    entries: dict[str, list[str]] = {entry.id: [] for entry in master.entries_in("education")}
    other: list[str] = []
    for item_id in lines:
        item = master.items[item_id]
        if item.kind == KIND_OTHER:
            other.append(item_id)
        else:
            entries.setdefault(item.entry_id or "", []).append(item_id)
    protecting = {requirement.supporters[0] for requirement in requirements if requirement.mandatory}
    for entry_id, bullets in entries.items():
        # An old role prints at most ``LENGTH_RULE.old_role_bullets`` lines: the ones a mandatory row rests on first.
        if entry_id in old and len(bullets) > LENGTH_RULE.old_role_bullets:
            entries[entry_id] = sorted(bullets, key=lambda bullet: bullet not in protecting)
    summary_ids = [summary] if summary else []
    item_ids = [*summary_ids, *(item for entry_id, bullets in entries.items() for item in (entry_id, *bullets)), *other]
    markdown = render_selection(master, item_ids, skills)
    numbered = tm._numbered(render_selection(master, item_ids, skills, ids=True))  # noqa: SLF001 - the candidate set's own numbering
    printed = [line.item_id for line in numbered if line.kind == "entry" and line.item_id is not None]
    shown = [*summary_ids, *(bullet for bullets in entries.values() for bullet in bullets), *other]
    selected = replace(
        base,
        summary=tuple(summary_ids), entries={entry_id: tuple(entries[entry_id]) for entry_id in printed}, skills=tuple(skills), other=tuple(other),
        lines=_reasons(master, shown, requirements, added, dropped_other=dropped_other),
        values=_values(master, [item_id for item_id in lines if item_id in master.items], requirements, rank),
        requirements=tuple(requirements), evidence_for={}, conflicts=(), title_entries={},
        skill_reasons=tuple(
            reason if reason.name in skills else replace(reason, picked=False, code="cut_for_length", reason="cut for length: the Skills section did not fit")
            for reason in base.skill_reasons
        ),
    )
    return tm.JobCandidates(
        mode=tm.MODE_EVIDENCE, profile=profile, posting=posting, selected=selected, summary=tuple(summary_ids),
        entries={entry_id: tuple(entries[entry_id]) for entry_id in printed}, skills=tuple(skills), other=tuple(other), markdown=markdown,
        lines=numbered, skill_reasons=selected.skill_reasons,
    )


def _settled(candidates: tm.JobCandidates, job: TailorJob, validated: Validated, *, answers: object, today: date) -> _Built:
    """``candidates`` as a settled resume, every line a copy: the projects in the pick's order, the sections in the model's."""

    from .tailor_skills import finish_tailoring

    ctx = candidates.context(answers=answers)  # type: ignore[arg-type]
    answer = candidates.copy_all(withheld=ctx.withheld)
    sections: list[dict[str, object]] = answer["sections"]  # type: ignore[assignment]
    position = {item_id: index for index, item_id in enumerate(validated.lines)}
    ids = candidates.line_ids

    def first_line(entry: Mapping[str, object]) -> int:
        bullets = [ids.get(ref.get("copy")) for ref in entry.get("bullets", ())]  # type: ignore[union-attr]
        return min((position[item] for item in bullets if item in position), default=len(position))

    for section in sections:
        if section.get("heading") == "projects":
            section["entries"] = sorted(section["entries"], key=first_line)  # type: ignore[arg-type]
    names = [section.get("heading") for section in sections]
    if validated.section_order[:1] == ("projects",) and "experience" in names and "projects" in names:
        first, second = names.index("experience"), names.index("projects")
        sections[first], sections[second] = sections[second], sections[first]
    settled = finish_tailoring(apply_no_loss(validate_tailored_output(answer, job, ctx), job, ctx, today=today), job, ctx)
    return _Built(candidates, ctx, tm.ensure_skills_line(settled, candidates, ctx))


def _printed(result: TailoredResume) -> tuple[str, ...]:
    """The master line ids a settled resume shows, in its order (a line with no master id: an answer's skill line)."""

    out: list[str] = []
    for section in result.sections:
        for line in section.body_lines():
            item_id = tm.line_item_id(line)
            if item_id is not None and item_id not in out:
                out.append(item_id)
    return tuple(out)


def _skill_cut_order(base: Selected) -> list[str]:
    """The Skills, first cut first: what nothing asks for and no line names, then what a line names, then what the posting asks for."""

    weight = {"listed": 0, "named_by_line": 1, "posting_nice": 2, "posting_must": 3}
    names = [reason.name for reason in base.skill_reasons if reason.picked]
    return sorted(names, key=lambda name: (weight.get(next(reason.code for reason in base.skill_reasons if reason.name == name), 0), -names.index(name)))


class _Pages:
    """The page measure, each layout made once and counted."""

    def __init__(self, measure: Measure) -> None:
        self._measure = measure
        self._known: dict[str, int | None] = {}
        self.layouts = 0

    def __call__(self, result: TailoredResume) -> int | None:
        key = render_markdown(result)
        if key not in self._known:
            self.layouts += 1
            self._known[key] = self._measure(result)
        return self._known[key]


def _over(result: TailoredResume, pages: _Pages, max_pages: int) -> bool:
    if result.length is not None and result.length.status == STATUS_UNMEASURED:
        raise PickError("pages_unmeasured", "the pages could not be measured (no renderer); no selection was made")
    found = pages(result)
    if found is None:
        raise PickError("pages_unmeasured", "the pages could not be measured (no renderer); no selection was made")
    return found > max_pages


@dataclass(frozen=True)
class Settled:
    """One selection (the module text): the job resume, Picked / Left out, and what the suggestion record keeps of it."""

    result: TailoredResume
    record: tm.TailorSelection
    candidates: tm.JobCandidates
    context: object
    picked_by: str
    fallback: str | None
    draft: bool
    model_pick: AssessmentPick | None
    problems: tuple[Problem, ...]
    added_by_code: tuple[Added, ...]
    conflicts: tuple[PickConflict, ...]
    #: The master line ids the resume shows, in its order.
    printed: tuple[str, ...]
    #: Each printed line's mark at the time (``MasterItem.mark``): what ``picked_line_changed`` compares.
    line_marks: Mapping[str, str]
    pages: int | None
    max_pages: int
    check: suggestions.SelectionCheck
    #: Page layouts this selection cost (the bound of SPEC 1.7 is on the seconds they take).
    layouts: int = 0

    @property
    def markdown(self) -> str:
        return render_markdown(self.result)

    def selection_json(self, *, made_at: str, result_digest: str | None, master_revision_id: str | None, resume: Mapping[str, object] | None = None) -> dict[str, object]:
        """``selection`` of the suggestion record (SPEC 2.1). ``resume``: ``{stored_path, markdown_sha256, origin}`` of the stored job resume."""

        return {
            "picked_by": self.picked_by,
            "fallback": self.fallback,
            "draft": self.draft,
            "pick_rules_version": PICK_RULES_VERSION,
            "selector_version": SELECTOR_VERSION,
            "made_at": made_at,
            "made_from": {"result_digest": result_digest, "master_revision_id": master_revision_id},
            "model_pick": None if self.model_pick is None else self.model_pick.to_json(),
            "problems": [problem.to_json() for problem in self.problems],
            "added_by_code": [item.to_json() for item in self.added_by_code],
            "line_marks": suggestions.marks_json(self.line_marks),
            "pages": self.pages,
            "max_pages": self.max_pages,
            "conflicts": [conflict.to_json() for conflict in self.conflicts],
            "resume": None if resume is None else dict(resume),
        }


def _conflicts(record: tm.TailorSelection, skills_cut: bool, rows: Sequence[suggestions.RequirementRow]) -> tuple[PickConflict, ...]:
    """The record's conflicts under the codes of 3.3; a requirement is named only when it is a row of the assessment."""

    known = {row.id for row in rows}
    out: list[PickConflict] = []
    for conflict in record.conflicts:
        if conflict.kind == "mandatory_evidence":
            out.append(PickConflict(CONFLICT_EVIDENCE, conflict.requirement_id if conflict.requirement_id in known else None, tuple(conflict.ids)))
        elif conflict.kind == "must_keep":
            out.append(PickConflict(CONFLICT_PIN, None, tuple(conflict.ids)))
        elif conflict.kind == "over_budget":
            out.append(PickConflict(CONFLICT_OVER, None, (), cut=False))
    if skills_cut:
        out.append(PickConflict(CONFLICT_SKILLS))
    return tuple(out)


def _finish(
    master: Master, built: _Built, fitted: TailoredResume, rows: Sequence[suggestions.RequirementRow], *, picked_by: str, fallback: str | None, draft: bool,
    pick: AssessmentPick | None, problems: Sequence[Problem], added: Sequence[Added], pins: Sequence[str], excludes: Sequence[str], today: date,
    pages: _Pages, max_pages: int, skills_cut: bool = False, selector_layouts: int = 0,
) -> Settled:
    record = tm.selection_record(master, built.candidates, fitted, picked_by=picked_by, fallback=fallback, pins=pins, excludes=excludes, today=today)
    conflicts = _conflicts(record, skills_cut, rows)
    printed = _printed(fitted)
    shown = set(printed)
    return Settled(
        result=fitted, record=record, candidates=built.candidates, context=built.context, picked_by=picked_by, fallback=fallback, draft=draft,
        model_pick=pick, problems=tuple(problems), added_by_code=tuple(item for item in added if item.id in shown), conflicts=conflicts,
        printed=printed, line_marks={item_id: master.items[item_id].mark for item_id in printed if item_id in master.items},
        pages=pages(fitted), max_pages=max_pages, check=suggestions.check_selection(rows, printed, conflicts=conflicts),
        layouts=pages.layouts + selector_layouts,
    )


def _fill_lines(master: Master, validated: Validated, rows: Sequence[suggestions.RequirementRow], base: Selected, old: set[str]) -> list[str]:
    """What a pick that leaves room is offered, best first: unpicked source lines of mandatory rows, then the selector's best lines of the recent roles."""

    taken = set(validated.lines)
    out: list[str] = []
    for row in rows:
        if row.mandatory:
            out += [source for source in row.master_sources() if source in master.items and master.items[source].kind == KIND_BULLET and source not in taken and source not in out]
    recent = [bullet for entry_id, bullets in base.entries.items() if master.entries[entry_id].section == "experience" and entry_id not in old for bullet in bullets]
    out += [bullet for bullet in sorted(recent, key=lambda bullet: -base.values.get(bullet, 0.0)) if bullet not in taken and bullet not in out]
    return out[:FILL_LINES]


def _unpicked_cuts(
    master: Master, settled: TailoredResume, added: Sequence[Added], pins: Sequence[str], values: Mapping[str, float],
) -> tuple[frozenset[str], list[tuple[str, str]]]:
    """``(the shown line ids code added, the cuts that take them first)``: C1, no unpicked line prints while a picked one is cut.

    A pinned line is the user's own and stays with the picked ones; so does the line given to the CURRENT role (a resume
    without its current job is the worse loss: it is cut by the ordinary order, last). An entry left with nothing but code's
    lines is cut whole (a role with no line does not print), so another recent role the pick left out is not kept by one
    line the code gave it.
    """

    current = {entry.id for entry in master.entries_in("experience") if entry.ongoing}
    code_added = {item.id for item in added if item.id not in pins and master.items[item.id].entry_id not in current}
    gone: set[str] = set()
    cuts: list[tuple[float, tuple[str, str]]] = []
    for section in settled.sections:
        if section.heading not in ("experience", "projects"):
            continue
        for entry in section.entries:
            if not entry.heading or entry.heading[0].id is None:
                continue
            lines = [line for line in entry.bullets if line.id is not None]
            mine = [line for line in lines if tm.line_item_id(line) in code_added]
            if not mine:
                continue
            worth = {line.id: values.get(tm.line_item_id(line) or "", 0.0) for line in mine}
            if len(mine) < len(entry.bullets):
                cuts += [(worth[line.id], ("bullet", line.id)) for line in mine]  # type: ignore[misc]
            else:
                cuts.append((max(worth.values()), ("role", entry.heading[0].id)))
            gone.update(line.id for line in mine)  # type: ignore[misc]
    return frozenset(gone), [cut for _worth, cut in sorted(cuts, key=lambda pair: pair[0])]


def _pick_selection(
    master: Master, pick: AssessmentPick, rows: Sequence[suggestions.RequirementRow], texts: Mapping[str, str], *, profile: SelectionProfile,
    posting: SelectionPosting, job: TailorJob, answers: object, excludes: Sequence[str], today: date, pages: _Pages, max_pages: int,
) -> Settled | Validated:
    """The selection of a model's pick, or the ``Validated`` that refuses it (V6)."""

    # The selector's own pick BEFORE its page fit: no layout is run. Its summary, Skills order, reasons and values are read.
    # 0.1.11 (orchestrator #35): the title-entry floor of the code selector is NOT applied to a model's pick.
    base = select(master, profile, posting, today=today, measure=lambda _markdown: (1, 0.0), max_pages=_UNFITTED, fill=False, title_floor=False)
    validated = validate_pick(
        master, pick, rows, today=today, code_summary=base.summary[0] if base.summary else None, values=base.values, pins=profile.pins,
    )
    if validated.refused is not None:
        return validated
    roles, old = _roles(master, today)
    rank = {entry.id: index for index, entry in enumerate(roles)}
    requirements = _requirements(master, rows, texts, rank)

    def build(lines: Sequence[str], skills: Sequence[str], added: Sequence[Added], dropped_other: Sequence[str] = ()) -> _Built:
        candidates = _candidates(
            master, base, profile, posting, summary=validated.summary, lines=lines, skills=skills, requirements=requirements, added=added, rank=rank,
            old=old, dropped_other=dropped_other,
        )
        return _settled(candidates, job, validated, answers=answers, today=today)

    def fit(built: _Built, *, protected: bool) -> TailoredResume:
        cuts, refill = tm.cut_order(built.settled, built.candidates, master, today=today)
        unpicked, first = _unpicked_cuts(master, built.settled, added, profile.pins, built.candidates.selected.values)
        if not protected:
            # Protected: the last printed source of a met mandatory row, and the pins. A role that holds one is not cut whole.
            shown = [item for section in built.settled.sections for entry in section.entries for line in entry.bullets if (item := tm.line_item_id(line)) is not None]
            kept = set(tm.shown_evidence(shown, built.candidates.selected)) | set(profile.pins)
            line_item = {line.id: tm.line_item_id(line) for section in built.settled.sections for entry in section.entries for line in entry.bullets}
            holds = {
                entry.heading[0].id for section in built.settled.sections for entry in section.entries
                if entry.heading and any(tm.line_item_id(line) in kept for line in entry.bullets)
            }
            cuts = [cut for cut in cuts if (cut[1] not in holds if cut[0] == "role" else line_item.get(cut[1]) not in kept)]
        # 0.1.11 (C1): a line the model did not pick goes before any line it did pick; those cuts are never refilled.
        cuts = [*first, *(cut for cut in cuts if cut not in first and cut[1] not in unpicked)]
        return fit_by_cuts(built.settled, cuts, measure=pages, max_pages=max_pages, refill=refill - unpicked)

    all_skills = list(base.skills)
    lines = list(validated.lines)
    added = list(validated.added)
    built = build(lines, all_skills, added)
    # FILL: a pick that leaves room is offered more lines, best first; the fewest are taken back out.
    if not _over(built.settled, pages, max_pages):
        offered = _fill_lines(master, validated, rows, base, old)
        if offered:
            trial = build([*lines, *offered], all_skills, [*added, *(Added(item_id, ADDED_ROOM_LEFT) for item_id in offered)])
            line_of = {tm.line_item_id(line): line.id for section in trial.settled.sections for entry in section.entries for line in entry.bullets}
            back = [("bullet", line_id) for item_id in reversed(offered) if (line_id := line_of.get(item_id)) is not None]
            fitted_trial = fit_by_cuts(trial.settled, back, measure=pages, max_pages=max_pages)
            if not _over(fitted_trial, pages, max_pages):
                stayed = [item_id for item_id in offered if item_id in _printed(fitted_trial)]
                if stayed:
                    lines = [*lines, *stayed]
                    added = [*added, *(Added(item_id, ADDED_ROOM_LEFT) for item_id in stayed)]
                    built = build(lines, all_skills, added)
    fitted = fit(built, protected=False)
    dropped_other: list[str] = []
    skills_cut = False
    if _over(fitted, pages, max_pages):
        # 4. Other lines, the model's last-ranked first.
        other = [item_id for item_id in lines if master.items[item_id].kind == KIND_OTHER]
        for item_id in reversed(other):
            dropped_other.append(item_id)
            built = build([line for line in lines if line not in dropped_other], all_skills, added, dropped_other)
            fitted = fit(built, protected=False)
            if not _over(fitted, pages, max_pages):
                break
    if _over(fitted, pages, max_pages):
        # 5. Skills, what nothing asks for first: the fewest that fit.
        order = _skill_cut_order(base)
        skills_cut = bool(order)
        kept_lines = [line for line in lines if line not in dropped_other]

        def without(count: int) -> tuple[_Built, TailoredResume]:
            gone = set(order[:count])
            trial = build(kept_lines, [name for name in all_skills if name not in gone], added, dropped_other)
            return trial, fit(trial, protected=False)

        low, high = 1, len(order)
        built, fitted = without(high) if order else (built, fitted)
        if order and not _over(fitted, pages, max_pages):
            while low < high:
                middle = (low + high) // 2
                if _over(without(middle)[1], pages, max_pages):
                    low = middle + 1
                else:
                    high = middle
            built, fitted = without(low)
        elif _over(fitted, pages, max_pages):
            # 3.3: the protected lines alone do not fit. They are cut, weakest first, and each loss is a conflict.
            fitted = fit(built, protected=True)
    return _finish(
        master, built, fitted, rows, picked_by=tm.PICKED_BY_MODEL, fallback=None, draft=False, pick=pick, problems=validated.problems, added=added,
        pins=profile.pins, excludes=excludes, today=today, pages=pages, max_pages=max_pages, skills_cut=skills_cut,
    )


def _code_selection(
    master: Master, rows: Sequence[suggestions.RequirementRow], *, profile: SelectionProfile, posting: SelectionPosting, job: TailorJob, answers: object,
    excludes: Sequence[str], today: date, pages: _Pages, max_pages: int, code: str, draft: bool, pick: AssessmentPick | None, problems: Sequence[Problem],
) -> Settled:
    """The fallback (3.4): the code selector's own selection, through the same record and the same final check."""

    candidates = tm.job_candidates(master, profile, posting, mode=tm.MODE_VIEW, today=today)
    result, view = tm.code_only(master, candidates, job, answers=answers, today=today, measure=pages)  # type: ignore[arg-type]
    _over(result, pages, max_pages)  # no renderer: no selection, said by its code
    built = _Built(view, view.context(answers=answers), result)  # type: ignore[arg-type]
    return _finish(
        master, built, result, rows, picked_by=tm.PICKED_BY_CODE, fallback=code, draft=draft, pick=pick, problems=problems, added=(), pins=profile.pins,
        excludes=excludes, today=today, pages=pages, max_pages=max_pages, selector_layouts=view.selected.layout_queries,
    )


def settle(
    master: Master,
    assessment: object,
    requirements: Sequence[suggestions.RequirementRow] | None = None,
    today: date | None = None,
    *,
    profile: SelectionProfile | None = None,
    posting: SelectionPosting | None = None,
    answers: Mapping[str, object] | None = None,
    excludes: Sequence[str] = (),
    fallback: str | None = None,
    draft: bool = False,
    measure: Measure | None = None,
    max_pages: int = MAX_PAGES,
) -> Settled:
    """The selection for one assessed job (the module text): the model's pick validated and fitted, or the code selector's.

    ``master``: the master revision the assessment read. ``assessment``: the
    stored assessment (``AssessResponse``) or its answer (``AssessmentBody``):
    its ``pick`` and matrix are read, nothing else of a model. ``requirements``:
    its rows by id (``suggestions.requirement_rows``; ``None``: read from the
    matrix). ``profile``: the selector's prior and the pins. ``posting``: the
    posting as the selector reads it (``None``: from the stored assessment's
    job and posting text). ``answers``: what a line may cite besides the
    master (``tailored_resume.tailor_sources``). ``fallback``: a reason to
    use the code selector whatever the pick is (``draft_requested`` with
    ``draft``, ``master_revision_unreadable``).

    Raises ``PickError`` only when the pages cannot be measured at all.
    """

    today = today or date.today()
    body = getattr(assessment, "result", assessment)
    matrix = tuple(getattr(body, "matrix", ()) or ())
    rows = tuple(requirements) if requirements is not None else suggestions.requirement_rows(matrix)
    texts = {row.id: str(getattr(item, "requirement", "") or "") for row, item in zip(suggestions.requirement_rows(matrix), matrix)}
    profile = profile or SelectionProfile()
    if posting is None:
        job_of = getattr(assessment, "job", None)
        posting = SelectionPosting(
            getattr(job_of, "title", "") or "", getattr(assessment, "posting_text", None) or "", getattr(job_of, "company", "") or "",
            getattr(job_of, "location", "") or "",
        )
    if not posting.cited:
        from .assess_master import cited_requirements

        posting = replace(posting, cited=cited_requirements(master, matrix))
    job = TailorJob(posting.title, posting.company, posting.location, posting.text)
    pages = _Pages(measure or tm.measure_pages)
    pick: AssessmentPick | None = getattr(body, "pick", None)
    problems: tuple[Problem, ...] = ()
    code = fallback
    if code is None and pick is None:
        code = FALLBACK_NO_PICK
    if code is None:
        assert pick is not None
        try:
            found = _pick_selection(
                master, pick, rows, texts, profile=profile, posting=posting, job=job, answers=answers, excludes=excludes, today=today, pages=pages,
                max_pages=max_pages,
            )
        except PickError:
            raise
        except Exception:  # noqa: BLE001 - a pick that cannot be settled never fails the job: the code selector's selection stands in (3.4, pick_failed)
            code = FALLBACK_PICK_FAILED
        else:
            if isinstance(found, Settled):
                return found
            code, problems = found.refused, found.problems
    assert code is not None
    return _code_selection(
        master, rows, profile=profile, posting=posting, job=job, answers=answers, excludes=excludes, today=today, pages=pages, max_pages=max_pages,
        code=code, draft=draft, pick=pick, problems=problems,
    )


# --- the checks a selection is judged on (3.5) --------------------------------------------------------------------


@dataclass(frozen=True)
class Checks:
    """The four separate checks of SPEC 3.5 for one selection. Never one number; recency is not a check."""

    #: 1. ``met`` mandatory rows whose master-line sources are all unprinted.
    lost: tuple[str, ...]
    #: 2. covered mandatory rows whose strongest source (backed over quantified over stated) is not printed.
    weak: tuple[str, ...]
    #: 3. pinned lines (and, in an eval, labelled must-keep lines) that are not printed.
    omitted: tuple[str, ...]
    #: 4. the page constraint.
    pages: int | None
    max_pages: int
    empty_entries: tuple[str, ...]
    roles_in_date_order: bool

    @property
    def fits(self) -> bool:
        return self.pages is not None and self.pages <= self.max_pages and not self.empty_entries and self.roles_in_date_order

    def to_json(self) -> dict[str, object]:
        return {
            "lost": list(self.lost), "weak": list(self.weak), "omitted": list(self.omitted), "pages": self.pages, "max_pages": self.max_pages,
            "empty_entries": list(self.empty_entries), "roles_in_date_order": self.roles_in_date_order, "fits": self.fits,
        }


def checks(master: Master, settled: Settled, requirements: Sequence[suggestions.RequirementRow], *, must_keep: Sequence[str] = ()) -> Checks:
    """``settled`` on the four checks (the eval's, and the re-pick rule's). Pure: the pages are the ones ``settle`` measured."""

    shown = set(settled.printed)
    lost: list[str] = []
    weak: list[str] = []
    for row in requirements:
        lines = [source for source in row.master_sources() if source in master.items]
        if row.status != "met" or not row.mandatory or not lines:
            continue
        if not shown & set(lines):
            lost.append(row.id)
        elif max(lines, key=lambda item_id: (_STRENGTH[master.items[item_id].strength], -lines.index(item_id))) not in shown:
            weak.append(row.id)
    entries: list[tuple[str, list[str]]] = []
    for section in settled.result.sections:
        for entry in section.entries:
            entry_id = tm.line_item_id(entry.heading[0]) if entry.heading else None
            if entry_id in master.entries and master.entries[entry_id].section in ("experience", "projects"):
                entries.append((entry_id, [line.id or "" for line in entry.bullets]))  # type: ignore[arg-type]
    dates = [tm._newest_first(master.entries[entry_id]) for entry_id, _bullets in entries if master.entries[entry_id].section == "experience"]  # noqa: SLF001
    return Checks(
        lost=tuple(lost), weak=tuple(weak), omitted=tuple(item_id for item_id in dict.fromkeys((*settled.record.pins, *must_keep)) if item_id not in shown),
        pages=settled.pages, max_pages=settled.max_pages, empty_entries=tuple(entry_id for entry_id, bullets in entries if not bullets),
        roles_in_date_order=dates == sorted(dates),
    )


def worse(new: Checks, previous: Checks) -> tuple[str, ...]:
    """The checks ``new`` is worse on than ``previous`` (the keep-the-previous rule of SPEC 2.4): a lower one never buys back a higher one."""

    found: list[str] = []
    if set(new.lost) - set(previous.lost):
        found.append("lost_mandatory_coverage")
    if set(new.weak) - set(previous.weak) - set(new.lost):
        found.append("evidence_strength")
    if set(new.omitted) - set(previous.omitted):
        found.append("important_omissions")
    if previous.fits and not new.fits:
        found.append("page_fit")
    return tuple(found)


__all__ = [
    "ADDED_FOR_COVERAGE",
    "ADDED_PINNED",
    "ADDED_RECENT_ROLE",
    "ADDED_ROOM_LEFT",
    "CONFLICT_EVIDENCE",
    "CONFLICT_OVER",
    "CONFLICT_PIN",
    "CONFLICT_SKILLS",
    "FALLBACKS",
    "FALLBACK_DRAFT",
    "FALLBACK_MASTER_REVISION",
    "FALLBACK_NO_PICK",
    "FALLBACK_PICK_FAILED",
    "FALLBACK_TOO_SMALL",
    "FILL_LINES",
    "MIN_PICK",
    "ORIGIN_PICK",
    "PICK_RULES_VERSION",
    "PRODUCER_ACTOR",
    "PRODUCER_CALLABLE",
    "PRODUCER_VERSION",
    "Added",
    "Checks",
    "PickConflict",
    "PickError",
    "Problem",
    "Settled",
    "Validated",
    "checks",
    "settle",
    "validate_pick",
    "worse",
]
