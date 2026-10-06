"""0.1.11 N3 (SPEC section 3): the selection. What code makes of the assessment's pick, or of no pick at all.

ONE ENTRY, ``settle(master, assessment, requirements, today)``: what
``quick_assess.run_quick_assessment``, the pipeline's ``pick`` step, the
re-pick action (``settle_stored``, at the end of this module) and the pick
eval (``tools/pick_probe.py``) all call.  It never
calls a model.  Pure except the page measurement (the shipped template at the
selector's spacing, ``tailor_master.measure_pages``).

WHAT IS REUSED, AND WHAT IS NEW.  The tree already holds a selector and a fit
(0.1.10.9 master P2/P4, ``sel-4``; ``sel-5`` keeps room for the PDF's header, ``sel-6`` lists a role with no line by its heading), and this module adds no second one:

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
  that loses its last line keeps its heading line, what was cut is on the result
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

A project left with no line stops printing.  NO EMPLOYER IS DROPPED SILENTLY
(0.1.11.4 item 9): every role of the master is in the selection.  A role with
no line in the pick, or none left after the fit, is an Experience entry with
its heading and no bullet, and prints as ONE line (title, employer, dates)
under ``tailored_resume.EARLIER_HEADING``, after the roles that show lines,
newest first.  Those lines are measured with the page, so the fit cuts one
more line when a heading needs its room.  A heading line is cut only after
the Other lines and the Skills (6 below), the oldest role's first, and then it
is a conflict.

6. the heading lines of the roles with no line left, the oldest first
   (``earlier_roles_do_not_fit``); then the protected lines.

FILL: a pick that leaves
room on the last page gets the unpicked source lines of mandatory rows, then
the selector's best unpicked lines of the recent roles (``room_left``).

CONFLICTS (3.3).  When the protected lines alone do not fit, the selection is
still cut to the page limit and each loss is a ``PickConflict``:
``mandatory_evidence_does_not_fit``, ``pinned_line_does_not_fit``,
``skills_do_not_fit``, ``earlier_roles_do_not_fit``.  A conflict makes the resume not ready
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
import logging
from pathlib import Path

from . import suggestions
from . import tailor_master as tm
from .find_jobs.assess_contracts import PICK_SECTIONS, AssessmentPick
from .job_resume_port import ACTION_DRAFT, ACTION_SHORTEN
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
from .tailored_resume import LENGTH_RULE, TailorJob, TailoredResume, apply_no_loss, render_markdown, tailor_sources, validate_tailored_output

_logger = logging.getLogger("gigai.scout.server")

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
#: 0.1.11.4 item 9: one or more roles are not listed even by their heading line.
CONFLICT_HEADINGS = "earlier_roles_do_not_fit"

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
    #: What a person reads, for a conflict that has its own sentence (``earlier_roles_do_not_fit``); in the JSON only then.
    message: str | None = None

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"code": self.code, "requirement": self.requirement, "lines": list(self.lines), "cut": self.cut}
        if self.message is not None:
            out["message"] = self.message
        return out


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
    """The candidate set of a pick: its lines under their entries, every role and degree, the Skills section; numbered like any resume.

    EVERY role of the master is in the set (0.1.11.4 item 9): one the pick takes no line of is there by its heading
    alone, so the settled resume lists it (``tailored_resume.EARLIER_HEADING``) and no employer is dropped silently."""

    entries: dict[str, list[str]] = {entry.id: [] for section in ("experience", "education") for entry in master.entries_in(section)}
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
    markdown = render_selection(master, item_ids, skills, candidates=True)
    numbered = tm._numbered(render_selection(master, item_ids, skills, ids=True, candidates=True))  # noqa: SLF001 - the candidate set's own numbering
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
        elif conflict.kind == "earlier_roles":
            # (``lines`` are master LINE ids, read against the printed lines; the roles are named by the record's conflict.)
            out.append(PickConflict(CONFLICT_HEADINGS, message=conflict.reason))
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
) -> tuple[frozenset[str], list[tuple[str, str]], list[tuple[str, str]]]:
    """``(the shown line ids code added, the cuts that take them first, the cuts of a recent role's line)``: C1, no unpicked line prints while a picked one is cut.

    The third list is the line code gave a recent role the pick left out (``recent_role_present``): it goes after the other
    code-added lines and the picked lines no row rests on, so a resume does not lose a role between two printed ones while a
    line nothing rests on stays (``_pick_selection`` places it); before any picked line a row rests on.

    A pinned line is the user's own and stays with the picked ones; so does the line given to the CURRENT role (a resume
    without its current job is the worse loss: it is cut by the ordinary order, last). An entry left with nothing but code's
    lines is cut whole (a role with no line does not print), so another recent role the pick left out is not kept by one
    line the code gave it.
    """

    current = {entry.id for entry in master.entries_in("experience") if entry.ongoing}
    code_added = {item.id for item in added if item.id not in pins and master.items[item.id].entry_id not in current}
    gone: set[str] = set()
    recent_code = {item.id for item in added if item.code == ADDED_RECENT_ROLE}
    cuts: list[tuple[float, tuple[str, str]]] = []
    recent: list[tuple[float, tuple[str, str]]] = []
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
            made = [(worth[line.id], ("bullet", line.id)) for line in mine] if len(mine) < len(entry.bullets) else [(max(worth.values()), ("role", entry.heading[0].id))]  # type: ignore[misc]
            (recent if any(tm.line_item_id(line) in recent_code for line in mine) else cuts).extend(made)
            gone.update(line.id for line in mine)  # type: ignore[misc]
    return frozenset(gone), [cut for _worth, cut in sorted(cuts, key=lambda pair: pair[0])], [cut for _worth, cut in sorted(recent, key=lambda pair: pair[0])]


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
        # The heading lines of the roles with no line left (0.1.11.4 item 9) go only in the last stage (``protected``),
        # after the Other lines and the Skills: they are placed below, after every line that is not protected.
        headings = [cut for cut in cuts if cut[0] == "heading"]
        cuts = [cut for cut in cuts if cut[0] != "heading"]
        every = list(cuts)
        unpicked, first, recent = _unpicked_cuts(master, built.settled, added, profile.pins, built.candidates.selected.values)
        if not protected or headings:
            # Protected: the last printed source of a met mandatory row, and the pins. A role that holds one is not cut whole.
            shown = [item for section in built.settled.sections for entry in section.entries for line in entry.bullets if (item := tm.line_item_id(line)) is not None]
            kept = set(tm.shown_evidence(shown, built.candidates.selected)) | set(profile.pins)
            line_item = {line.id: tm.line_item_id(line) for section in built.settled.sections for entry in section.entries for line in entry.bullets}
            holds = {
                entry.heading[0].id for section in built.settled.sections for entry in section.entries
                if entry.heading and any(tm.line_item_id(line) in kept for line in entry.bullets)
            }
            free = [cut for cut in cuts if (cut[1] not in holds if cut[0] == "role" else line_item.get(cut[1]) not in kept)]
            cuts = free if not protected else cuts
        # 0.1.11 (C1): a line the model did not pick goes before any line it did pick; those cuts are never refilled. The line given
        # to a recent role goes after the picked lines no row rests on (a gap between two printed roles is the worse loss), before the rest.
        rest = [cut for cut in cuts if cut not in first and cut[1] not in unpicked]
        rests = {item for requirement in built.candidates.selected.requirements for item in requirement.supporters}
        items = {line.id: tm.line_item_id(line) for section in built.settled.sections for entry in section.entries for line in entry.bullets}
        roles_items = {
            entry.heading[0].id: {items.get(line.id) for line in entry.bullets}
            for section in built.settled.sections for entry in section.entries if entry.heading
        }
        bare = [cut for cut in rest if not ({items.get(cut[1])} if cut[0] == "bullet" else roles_items.get(cut[1], {None})) & rests]
        cuts = [*first, *bare, *recent, *(cut for cut in rest if cut not in bare)]
        if protected and headings:
            held = set(every) - set(free)
            after = max((place for place, cut in enumerate(cuts) if cut not in held), default=-1) + 1
            cuts[after:after] = headings
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


# --- one job's selection, stored (SPEC 1.7 step 4; 2.4: the re-pick and the draft of a STORED job) ---------------------
#
# ONE path for the two callers that store a selection: ``quick_assess.run_quick_assessment`` (a new assessment) and
# ``settle_stored`` (0.1.11.3: ``gigai scout resume pick --refresh | --draft``, ``POST /api/job-resumes/pick``, the job
# page's "Pick it now").  Both read the same inputs (``pick_inputs``) and write through ``settle_and_store``.
#
# Every refusal here is a ``PickError`` whose message is for the USER: what happened and what to do, in plain words.
# It never names a module, a function or a code (the code is the error's ``code``, for a program).

REFUSED_NO_ASSESSMENT = "assessment_missing"
REFUSED_NO_MASTER = "no_master"
REFUSED_PROFILE_RESUME = "profile_resume_in_use"
REFUSED_NO_PROFILE = "profile_not_found"
REFUSED_HELD = "resume_held"
REFUSED_DRAFT_NOT_NEEDED = "draft_not_needed"
REFUSED_UNMEASURED = "pages_unmeasured"
REFUSED_FAILED = "pick_failed"
REFUSED_NOTHING_TO_SHORTEN = "no_resume_to_shorten"
REFUSED_SHORT_ALREADY = "resume_short_already"

_REASSESS = "`gigai scout jobs assess URL --again` (a job assessed by its URL: `gigai scout assess --job-url URL`; one model call, on your yes)"
MESSAGES: Mapping[str, str] = {
    REFUSED_NO_ASSESSMENT: "This job has no stored assessment, so there is nothing to pick a resume from. Assess it first: `gigai scout jobs assess URL` (one model call, on your yes).",
    REFUSED_NO_MASTER: (
        "There is no master resume to pick from, so this profile's own resume is used as it is. Build your master resume to get a resume "
        "picked for each job: `gigai scout resume master init`."
    ),
    REFUSED_PROFILE_RESUME: (
        "This profile uses the resume you put in by hand, so no resume is picked from your master resume for its jobs. To get one picked for "
        "each job, make this profile's resume from your master again: `gigai scout resume master selection refresh --profile ID`."
    ),
    REFUSED_NO_PROFILE: "The profile this job was assessed for is no longer there, so no resume can be picked for it. Assess the job again for a profile you have.",
    REFUSED_HELD: (
        "No resume is suggested for this job yet: a must-have requirement is waiting for your answer or is not met. Answer its questions, or "
        "make a draft anyway: `gigai scout resume pick --job-url URL --draft`."
    ),
    REFUSED_DRAFT_NOT_NEEDED: "A resume is suggested for this job already; a draft is for a job that is held. Pick it again: `gigai scout resume pick --job-url URL --refresh`.",
    REFUSED_UNMEASURED: (
        "The resume could not be picked: its pages could not be measured on this computer (the PDF renderer did not start). Nothing was "
        "changed. Try again; if it keeps happening, run `gigai doctor`."
    ),
    REFUSED_NOTHING_TO_SHORTEN: "There is no resume stored for this job yet, so there is nothing to shorten. Pick one first: `gigai scout resume pick --job-url URL --refresh`.",
    REFUSED_SHORT_ALREADY: "This resume already fits its pages with most of a page to spare, so nothing was left out. If its PDF runs long, look at the spacing you chose for it.",
    REFUSED_FAILED: f"The resume could not be picked for this job. Nothing was changed. Try again, or re-assess the job to get a new pick: {_REASSESS}.",
}


def refusal(code: str) -> PickError:
    """The refusal ``code`` with its plain message (:data:`MESSAGES`)."""

    return PickError(code, MESSAGES[code])


@dataclass(frozen=True)
class PickInputs:
    """What one job's selection reads besides the assessment: the master, the profile as the selector's prior, the answers, the posting."""

    master: Master
    #: The master revision the selection is made from (``tailor_master.MasterSource``).
    source: object
    profile: SelectionProfile
    excludes: tuple[str, ...]
    answers: Mapping[str, object]
    posting: SelectionPosting


def pick_inputs(home_root: Path, target: Path, *, stored: object, prior: SelectionProfile, profile: object, resume: object, job: object) -> PickInputs:
    """The inputs of one job's selection.

    ``stored``: the stored master (``master_store.StoredMaster``).  ``prior``:
    the profile as the selector's prior (``assess_master.profile_prior``).
    ``profile``: its record (the pins and the excluded lines of its
    selection).  ``resume``: the resume identity and text the answers and
    stories are searched for.  ``job``: the posting (title, text, company,
    location).
    """

    revision = stored.revision  # type: ignore[attr-defined]
    selection = getattr(profile, "master_selection", None)
    return PickInputs(
        master=stored.master,  # type: ignore[attr-defined]
        source=tm.MasterSource(revision.revision_id, revision.revision, revision.content_sha256),
        profile=replace(prior, pins=tuple(selection.pins) if selection is not None else ()),
        excludes=tuple(selection.excludes) if selection is not None else (),
        answers=tailor_sources(
            home_root=home_root, target=target, profile_id=resume.profile_id, resume_text=resume.text, title=job.title, posting_text=job.text,  # type: ignore[attr-defined]
        ),
        posting=SelectionPosting(job.title, job.text, job.company, job.location),  # type: ignore[attr-defined]
    )


def settle_and_store(
    home_root: Path, target: Path, assessment: object, *, job: object, inputs: PickInputs | None, now: str, fallback: str | None = None,
    draft: bool = False, repick: bool = False, selection_error: str | None = None, measure: Measure | None = None,
) -> tuple[suggestions.SuggestionRecord, str | None]:
    """Settle one job's selection (with ``inputs``) and write the suggestion record and the job resume. No model call.

    ``inputs`` ``None``: no selection is made (the gate holds, or nothing can
    be picked) and the record alone is written; ``selection_error`` then says
    why none could be made although one was wanted.  A selection that cannot
    be made is RECORDED (``selection: null`` with its error code), never
    raised: the answer is ``(the record, the error code or None)``.  A resume
    the user changed is kept; the new selection then waits as ``proposed``
    (``suggestions.store_assessed``).  ``measure``: the page measure of a
    tighter budget (``shorten_stored``); ``None``: the selector's own.
    """

    settled: Settled | None = None
    if inputs is not None:
        try:
            settled = settle(
                inputs.master, assessment, None, None, profile=inputs.profile, answers=inputs.answers, posting=inputs.posting, excludes=inputs.excludes,
                fallback=fallback, draft=draft, measure=measure,
            )
        except PickError as exc:
            selection_error = exc.code
    record = suggestions.store_assessed(
        home_root, target, assessment=assessment, job=job, resume=assessment.resume, gate_record=assessment.resume_gate, now=now,  # type: ignore[attr-defined]
        suggested=assessment.result.structured_suggestions, settled=settled, selection_error=selection_error,  # type: ignore[attr-defined]
        master_source=None if inputs is None else inputs.source, answers=None if inputs is None else inputs.answers, repick=repick,
    )
    return record, selection_error


def _stored_inputs(home_root: Path, target: Path, assessment: object) -> PickInputs:
    """The inputs of a STORED job's selection, from the stores as they are now; a refusal when nothing can be picked."""

    from ..private_records import PrivateRecordError
    from ..workpad import WorkpadError, resolve_workpad
    from .assess_master import profile_prior, reads_evidence
    from .find_jobs.assess_contracts import AssessResumeInput
    from .find_jobs.contracts import FindJobsContractError
    from .find_jobs.resume_input import resolve_profile, resume_for_profile

    profile_id = assessment.resume.profile_id  # type: ignore[attr-defined]
    if profile_id is None:
        raise refusal(REFUSED_NO_PROFILE)
    try:
        resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
        profile = resolve_profile(AssessResumeInput(profile_id=profile_id), resolved=resolved, home_root=home_root, target=target)
    except (WorkpadError, PrivateRecordError, FindJobsContractError) as exc:
        raise refusal(REFUSED_NO_PROFILE) from exc
    assert profile is not None
    stored = tm.stored_master(home_root, target, resolved=resolved)
    if stored is None:
        raise refusal(REFUSED_NO_MASTER)
    if not reads_evidence(home_root, profile):
        raise refusal(REFUSED_PROFILE_RESUME)
    selection = profile.master_selection
    prior = profile_prior(
        titles=tuple(profile.titles), item_ids=tuple(selection.item_ids) if selection is not None else None, profile_id=profile.profile_id,
        label=profile.label,
    )
    try:
        resume = resume_for_profile(profile, resolved=resolved, home_root=home_root, target=target)
    except FindJobsContractError as exc:
        raise refusal(REFUSED_NO_PROFILE) from exc
    return pick_inputs(home_root, target, stored=stored, prior=prior, profile=profile, resume=resume, job=assessment.job)  # type: ignore[attr-defined]


def settle_stored(
    home_root: Path, target: Path, profile_id: str | None, job_identity: str, *, action: str, now: str, measure: Measure | None = None,
) -> suggestions.SuggestionRecord:
    """Pick a STORED job's resume again (``action``: refresh) or make its draft (draft), and write the record. No model call.

    The stored assessment's pick is settled against the master AS IT IS NOW.
    ``refresh`` is for a job whose gate suggests a resume (and for a draft
    that is there already: it stays a draft); ``draft`` for a job whose gate
    holds, picked by the code selector and marked as a draft.  A stored job
    resume that is the user's is kept: the new selection waits as
    ``proposed``.  A job with no suggestion record yet (its assessment could
    not write one) gets it here.  ``measure``: a tighter page budget
    (``shorten_stored``, which is this with ``action`` shorten).

    Raises ``PickError`` with a plain message (:data:`MESSAGES`):
    ``assessment_missing``, ``no_master``, ``profile_resume_in_use``,
    ``profile_not_found``, ``resume_held``, ``draft_not_needed``,
    ``pages_unmeasured`` (recorded as the record's ``selection_error``) and
    ``pick_failed``.  Whether the stored assessment is stale is the caller's
    rule (``job_actions.pick_action`` refuses a refresh then).
    """

    return _settle_stored(Path(home_root), Path(target), profile_id, job_identity, action=action, now=now, measure=measure)[0]


def _settle_stored(
    home_root: Path, target: Path, profile_id: str | None, job_identity: str, *, action: str, now: str, measure: Measure | None,
) -> tuple[suggestions.SuggestionRecord, PickInputs]:
    from .quick_assess import read_quick_assessment
    from .resume_gate import SUGGEST, gate

    assessment = read_quick_assessment(home_root, target, profile_id, job_identity)
    if assessment is None:
        raise refusal(REFUSED_NO_ASSESSMENT)
    if assessment.resume_gate is None:
        # An answer stored without a gate (the shape of before 0.1.11): the gate of its rows, questions and verdict, by the same rules.
        body = assessment.result
        assessment = replace(assessment, resume_gate=gate(body.matrix, body.structured_questions, body.verdict).record())
    record = suggestions.read_suggestions(home_root, target, profile_id, job_identity)
    held = assessment.resume_gate.decision != SUGGEST
    drafting = held and (action == ACTION_DRAFT or bool(((record.selection if record is not None else None) or {}).get("draft")))
    if action == ACTION_DRAFT and not held:
        raise refusal(REFUSED_DRAFT_NOT_NEEDED)
    if held and not drafting:
        raise refusal(REFUSED_HELD)
    inputs = _stored_inputs(home_root, target, assessment)
    try:
        record, error = settle_and_store(
            home_root, target, assessment, job=assessment.job, inputs=inputs, now=now, fallback=FALLBACK_DRAFT if drafting else None, draft=drafting,
            repick=True, measure=measure,
        )
    except Exception as exc:  # noqa: BLE001 - whatever stopped the pick, the user is told in plain words what to do; the cause is in the log
        _logger.warning("a stored job's resume could not be picked", exc_info=True)
        raise refusal(REFUSED_FAILED) from exc
    if error is not None:
        raise refusal(error if error in MESSAGES else REFUSED_FAILED)
    return record, inputs


# --- "Shorten automatically" (0.1.11.3 item 15) -------------------------------------------------------------------------
#
# When the PDF still does not fit its page limit (``resume_pdf.over_limit_note``) the user is not told to edit by hand:
# the same pick is made again under a TIGHTER page budget, so the same fit drops the next lines of the SAME cut order
# (3.2: a line no requirement rests on first; the last source of a met must-have and a pin only when nothing else can
# go, and then it is a conflict and the answer says so).  No model call, and nothing of the PDF's header is read: the
# budget is tightened in header lines (``resume_pdf.pages_at``'s ``header_lines``), the unit the estimate keeps room in.

#: One shorten makes room for this many more lines than the stored resume needs to run past its pages.
SHORTEN_STEP_LINES = 1
#: A resume that still fits with this many header lines kept blank (most of a page) is not shortened (``resume_short_already``).
_SHORTEN_MOST_LINES = 36
_SHOWN_CHARS = 90


@dataclass(frozen=True)
class Shortened:
    """What one shorten did: the record as stored, and what a person is told (``message``)."""

    record: suggestions.SuggestionRecord
    #: The text of each line the shorter resume leaves out, in the resume's order.
    left_out: tuple[str, ...]
    #: A line that backs a must-have requirement (or a pinned line) had to go: nothing else was left.
    must_have_cut: bool
    #: The stored resume is the user's and was kept; the shorter one waits as the new suggested resume.
    waiting: bool

    @property
    def message(self) -> str:
        if not self.left_out:
            text = "Nothing more could be left out of this resume, so it is as it was."
        else:
            count = len(self.left_out)
            text = f"Left out {count} line{'' if count == 1 else 's'}: " + "; ".join(f'"{line}"' for line in self.left_out) + "."
            if self.must_have_cut:
                text += " Nothing else was left to cut, so a line that backs a must-have requirement (or a line you pinned) was left out too."
        if self.waiting:
            return text + " The resume you edited was kept as it is; the shorter one is waiting as the new suggested resume."
        return text + (" Generate the PDF again." if self.left_out else "")

    def to_json(self) -> dict[str, object]:
        return {"left_out": list(self.left_out), "must_have_cut": self.must_have_cut, "waiting": self.waiting, "message": self.message}


def _tighter_measure(current: TailoredResume, max_pages: int) -> Measure:
    """The page measure of the next tighter budget: room for ``SHORTEN_STEP_LINES`` more header lines than ``current`` can take."""

    from .master_selection import FIT_SCALE
    from .resume_pdf import HEADER_RESERVE_LINES, pages_at

    try:
        over = next((count for count in range(HEADER_RESERVE_LINES, _SHORTEN_MOST_LINES) if pages_at(current, FIT_SCALE, header_lines=count) > max_pages), None)
    except Exception as exc:  # noqa: BLE001 - no renderer (Typst missing or failing): nothing is measured, nothing is changed
        raise refusal(REFUSED_UNMEASURED) from exc
    if over is None:
        raise refusal(REFUSED_SHORT_ALREADY)
    lines = over + SHORTEN_STEP_LINES

    def measure(result: TailoredResume) -> int | None:
        try:
            return pages_at(result, FIT_SCALE, header_lines=lines)
        except Exception:  # noqa: BLE001 - as ``tailor_master.measure_pages``: the length is flagged, never guessed
            return None

    return measure


def shorten_stored(home_root: Path, target: Path, profile_id: str | None, job_identity: str, *, now: str) -> Shortened:
    """Make a STORED job's resume shorter (the text above) and write the record. No model call.

    Raises ``PickError`` as ``settle_stored`` does, ``no_resume_to_shorten`` for a job with no stored resume and
    ``resume_short_already`` for one that fits its pages with most of a page to spare."""

    from .tailored_resume import read_tailored_resume, tailored_resume_path

    home_root, target = Path(home_root), Path(target)
    stored = read_tailored_resume(tailored_resume_path(home_root, target, profile_id, job_identity))
    if stored is None:
        raise refusal(REFUSED_NOTHING_TO_SHORTEN)
    length = getattr(stored.result, "length", None)
    max_pages = length.max_pages if length is not None else MAX_PAGES
    before = _printed(stored.result)
    previous = suggestions.read_suggestions(home_root, target, profile_id, job_identity)
    record, inputs = _settle_stored(
        home_root, target, profile_id, job_identity, action=ACTION_SHORTEN, now=now, measure=_tighter_measure(stored.result, max_pages),
    )
    waiting = record.proposed is not None and (previous is None or record.proposed != previous.proposed)
    made = record.proposed if waiting else record.selection
    after = set(suggestions.recorded_marks(made))
    protected = {CONFLICT_EVIDENCE, CONFLICT_PIN}

    def conflicts(selection: Mapping[str, object] | None) -> set[tuple[object, ...]]:
        found = (selection or {}).get("conflicts")
        return {
            (item.get("code"), item.get("requirement"), *item.get("ids", ()))
            for item in (found if type(found) is list else ()) if type(item) is dict and item.get("code") in protected
        }

    def shown(item_id: str) -> str:
        item = inputs.master.items.get(item_id)
        text = " ".join(item.text.split()) if item is not None else "a line that is no longer in your master resume"
        return text if len(text) <= _SHOWN_CHARS else text[: _SHOWN_CHARS - 3].rstrip() + "..."

    return Shortened(
        record=record, left_out=tuple(shown(item_id) for item_id in before if item_id not in after),
        must_have_cut=bool(conflicts(made) - conflicts(previous.selection if previous is not None else None)), waiting=waiting,
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
        # (A role with no bullet is its one heading line, 0.1.11.4 item 9: only a project can be empty.)
        pages=settled.pages, max_pages=settled.max_pages,
        empty_entries=tuple(entry_id for entry_id, bullets in entries if not bullets and master.entries[entry_id].section == "projects"),
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
    "CONFLICT_HEADINGS",
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
    "MESSAGES",
    "MIN_PICK",
    "ORIGIN_PICK",
    "PICK_RULES_VERSION",
    "PRODUCER_ACTOR",
    "PRODUCER_CALLABLE",
    "PRODUCER_VERSION",
    "REFUSED_DRAFT_NOT_NEEDED",
    "REFUSED_FAILED",
    "REFUSED_HELD",
    "REFUSED_NO_ASSESSMENT",
    "REFUSED_NO_MASTER",
    "REFUSED_NOTHING_TO_SHORTEN",
    "REFUSED_NO_PROFILE",
    "REFUSED_PROFILE_RESUME",
    "REFUSED_SHORT_ALREADY",
    "REFUSED_UNMEASURED",
    "Added",
    "Checks",
    "PickConflict",
    "PickError",
    "PickInputs",
    "Problem",
    "SHORTEN_STEP_LINES",
    "Settled",
    "Shortened",
    "Validated",
    "checks",
    "pick_inputs",
    "refusal",
    "settle",
    "settle_and_store",
    "settle_stored",
    "shorten_stored",
    "validate_pick",
    "worse",
]
