"""The master resume in the tailor path (0.1.10.9 master P4): a job's candidate set, the fit, what was picked.

When a master resume is stored (``master_store``), one tailoring no longer
reads the profile's own 2-page resume.  Its input is the JOB'S CANDIDATE SET:
lines code picks for that posting from the WHOLE master
(``master_selection``), with the profile as the prior.  Today's one tailor
call orders and words inside that set, code fits the result to
``LENGTH_RULE.max_pages`` pages, and when the call fails or no model is
available the code's own selection is the resume (the fallback).

Without a master nothing here runs: ``master_tailoring`` answers ``None`` and
``tailored_resume.run_tailored_resume`` does exactly what it did.

THE CANDIDATE SET (``job_candidates``; ``CANDIDATES`` names the one a
tailoring uses, settled by the P4 live eval):

- ``view``: the selector's 2-page selection for the posting.
- ``shortlist``: the selector's pick BEFORE its page fit (about twice what
  fits), so the tailor call's order decides which lines stay.
- ``evidence``: the bullets of the evidence view (the lines most relevant to
  the posting, up to the assess prompt's character cap) for every role and
  for the shortlist's projects, under the shortlist's summary, skills and
  Other lines.

It is rendered as resume markdown in GigAI's format and numbered ``R<n>`` like
any resume; every numbered line keeps its master id (``JobCandidates.line_ids``)
and a ref to it carries that id (``SourceRef.item_id``), so each line of a
tailored resume traces to the master.  THE SKILLS LINE is assembled by code
(the posting's must-haves the master lists, its nice-to-haves, then what the
candidate lines name; ``master_selection.MAX_SKILLS`` at most) and the model
copies it: the master's whole Skills list is never forced onto a resume.
``ensure_skills_line`` shows it when the model left it out.

DECISION 2 in a wider set (``_stand_ins``): when only an old role's candidate
line names a posting must-have, a recent role's line that names it is added to
the candidates, so the old line can be cut for length without losing the
evidence.  When the master has no such line the old line is the only evidence
and the fit keeps it.

THE FIT (``fit_selected``; the operator's decision 1: recent roles always
appear, for length the oldest roles go first, inside the budget lines are
picked by relevance).  The cuts, in the one order they may be applied
(``cut_order``), the fewest that fit taken (``tailor_length.fit_by_cuts``):

1. the oldest role's bullets from the END of the list the tailor call
   ordered, then that role; then the next oldest role;
2. then, among the recent roles and the projects that are above their floors
   (``master_selection.FLOORS``; a project keeps one line), the LAST line of
   the list whose last line is worth least for the posting (the selector's
   value), again and again.

A line that is the last shown line naming a posting must-have is never cut,
and a recent role's or a project's line comes back when the last cut left
room for it.  What was cut is recorded on the result exactly as the 0110-10-05 length rule
records it (``TailoredResume.length``), so it is shown and one Restore puts it
back.  The pages are measured at ``master_selection.FIT_SCALE``, the
selector's own budget.

WHAT WAS PICKED (``selection_record``): the stored tailored resume carries
``selection``: every line of the master except its Skills lines, picked or
left out, each with the selector's reason (``master_selection.LineReason``) or
the fit's, the skills the same way, and who made the pick (``model``, or
``code`` for the fallback).  Ids, codes and reasons only: a line's text is in
the result (picked) or in the master revision ``sources.master`` names.

Pure except ``stored_master`` / ``master_tailoring`` (a committed journal
read, kept per journal head) and the page measurement.  No model call here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
import re
import threading

from .find_jobs.contracts import _digest_value, _fail, _object_with_optional, _optional_string, _string
from .master_resume import KIND_BULLET, KIND_OTHER, KIND_SUMMARY, Master, MasterEntry
from .master_selection import (
    FIT_SCALE,
    FLOORS,
    MAX_PAGES,
    SELECTOR_VERSION,
    Selected,
    SelectionPosting,
    SelectionProfile,
    evidence_view,
    is_old_role,
    render_selection,
    select,
)
from .posting_keywords import mentions
from .tailor_length import STATUS_CUT, Measure, fit_by_cuts
from .tailored_resume import (
    ENTRY_SECTIONS,
    AnswerSource,
    MatrixRow,
    TailorContext,
    TailorJob,
    TailoredEntry,
    TailoredLine,
    TailoredResume,
    TailoredSection,
    _is_old_role,
    _resume_ref,
    _span_copy,
    apply_no_loss,
    replaced_line,
    resume_lines,
    tailor_context,
    validate_tailored_output,
)

MODE_VIEW = "view"
MODE_SHORTLIST = "shortlist"
MODE_EVIDENCE = "evidence"
CANDIDATE_MODES: tuple[str, ...] = (MODE_VIEW, MODE_SHORTLIST, MODE_EVIDENCE)
#: The candidate set one tailoring reads (see the module text; the P4 live eval's recommendation).
CANDIDATES = MODE_SHORTLIST
#: Names the candidate rules and the fit's cut order above; the pipeline's tailor digest holds it beside
#: ``SELECTOR_VERSION`` and ``CANDIDATES``, so changing one is a deliberate re-open of the stored tailorings.
CANDIDATES_VERSION = "job-candidates:1"

PICKED_BY_MODEL = "model"
PICKED_BY_CODE = "code"
PICKED_BY: tuple[str, ...] = (PICKED_BY_MODEL, PICKED_BY_CODE)

#: A project always keeps this many lines once shown (the selector's rule).
_PROJECT_FLOOR = 1
_UNFITTED = 10**6
_ID_COMMENT = re.compile(r"\s*<!-- id:(\S+) -->\s*\Z")
_KEY = re.compile(r"\A[a-z][a-z0-9_.:-]{0,63}\Z")


# --- the candidate set --------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateLine:
    """One numbered line of a candidate set: what it is, and the master id it stands for.

    ``item_id`` is a line's own id, an entry's id for its heading and the
    lines under the heading, and ``None`` for a section heading and for the
    Skills line (a list code assembled, not one master line).
    """

    number: int
    kind: str  # section | entry | subline | summary | bullet | skills | other
    section: str
    item_id: str | None


@dataclass(frozen=True)
class JobCandidates:
    """The lines one tailoring may show: the markdown the tailor call reads, numbered, with each line's master id."""

    mode: str
    profile: SelectionProfile
    posting: SelectionPosting
    #: The code's own selection behind the set (the 2-page selection for ``view``, else the pick before its
    #: page fit): the reason for every master line, the posting's keywords, each bullet's value.
    selected: Selected
    summary: tuple[str, ...]
    #: entry id -> its candidate bullets, in the order the markdown prints both.
    entries: Mapping[str, tuple[str, ...]]
    skills: tuple[str, ...]
    other: tuple[str, ...]
    markdown: str
    lines: tuple[CandidateLine, ...]
    #: A recent role's line added so an old role's line is not the only evidence of a must-have: id -> (the old line, the must-haves).
    stand_ins: Mapping[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)

    @property
    def line_ids(self) -> dict[int, str]:
        return {line.number: line.item_id for line in self.lines if line.item_id is not None}

    @property
    def skills_line(self) -> int | None:
        return next((line.number for line in self.lines if line.kind == "skills"), None)

    @property
    def bullet_count(self) -> int:
        return sum(len(bullets) for bullets in self.entries.values())

    def item_ids(self) -> frozenset[str]:
        """Every selectable line in the set (summaries, bullets, Other lines)."""

        return frozenset((*self.summary, *(bullet for bullets in self.entries.values() for bullet in bullets), *self.other))

    def context(self, *, answers: Mapping[str, AnswerSource] | None = None, matrix: tuple[MatrixRow, ...] = ()) -> TailorContext:
        """The tailoring's context over this set: the markdown numbered like any resume, each line's master id kept."""

        return replace(tailor_context(self.markdown, answers=answers, matrix=matrix), line_ids=self.line_ids)

    def copy_all(self, *, withheld: frozenset[int] = frozenset()) -> dict[str, object]:
        """The answer of a tailoring that copies every line in the set's own order (what the fallback validates)."""

        sections: list[dict[str, object]] = []
        entry: dict[str, list[dict[str, int]]] | None = None
        for line in self.lines:
            if line.kind == "section":
                sections.append({"heading": line.section, ("entries" if line.section in ENTRY_SECTIONS else "lines"): []})
                entry = None
            elif line.kind == "entry":
                entry = None if line.number in withheld else {"heading_ref": [{"copy": line.number}], "bullets": []}
                if entry is not None:
                    sections[-1]["entries"].append(entry)  # type: ignore[union-attr]
            elif line.number in withheld:
                continue
            elif line.kind == "subline":
                if entry is not None:
                    entry["heading_ref"].append({"copy": line.number})
            elif line.kind == "bullet":
                if entry is not None:
                    entry["bullets"].append({"copy": line.number})
            else:
                sections[-1]["lines"].append({"copy": line.number})  # type: ignore[union-attr]
        return {"sections": [section for section in sections if section.get("entries") or section.get("lines")]}


def _numbered(markdown_with_ids: str) -> tuple[CandidateLine, ...]:
    """The non-blank lines of selection markdown that carries id comments, numbered as ``resume_lines`` numbers them."""

    lines: list[CandidateLine] = []
    section = ""
    entry: str | None = None
    for number, text in enumerate((raw.strip() for raw in markdown_with_ids.splitlines() if raw.strip()), 1):
        found = _ID_COMMENT.search(text)
        item_id = found.group(1) if found else None
        if text.startswith("## "):
            section, entry = text[3:].strip().lower(), None
            lines.append(CandidateLine(number, "section", section, None))
        elif text.startswith("### "):
            entry = item_id
            lines.append(CandidateLine(number, "entry", section, item_id))
        elif text.startswith("- "):
            kind = section if section in ("summary", "skills", "other") else "bullet"
            lines.append(CandidateLine(number, kind, section, None if kind == "skills" else item_id))
        else:
            lines.append(CandidateLine(number, "subline", section, entry))
    return tuple(lines)


def _newest_first(entry: MasterEntry) -> tuple[int, int, int]:
    return (-(9999 if entry.ongoing else (entry.end or 0)), -(entry.start or 0), entry.order)


def _stand_ins(master: Master, selected: Selected, entries: Mapping[str, Sequence[str]], today: date) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Recent roles' lines to add so that no old role's candidate line is the only one naming a posting must-have.

    The operator's decision 2, made before the tailor call: a line can only
    be shown when it is in the candidate set.  For each must-have that only
    old roles' candidate lines name, the best unshown line of a recent role
    naming it; nothing for a must-have no recent role's line names.
    """

    if selected.keywords is None:
        return {}
    must = selected.keywords.must
    roles = sorted(master.entries_in("experience"), key=_newest_first)
    old = {entry.id for entry in roles if is_old_role(entry, today)}
    shown = {bullet for bullets in entries.values() for bullet in bullets}
    named: dict[str, tuple[str, ...]] = {}

    def names(item_id: str) -> tuple[str, ...]:
        if item_id not in named:
            named[item_id] = tuple(term for term in must if mentions(master.items[item_id].text, term))
        return named[item_id]

    covered = {term for bullet in shown if master.items[bullet].entry_id not in old for term in names(bullet)}
    pool = [master.items[bullet] for entry in roles if entry.id not in old for bullet in entry.bullets if bullet not in shown]
    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    for entry in reversed(roles):  # oldest first
        if entry.id not in old:
            continue
        for bullet in entries.get(entry.id, ()):
            left = {term for term in names(bullet) if term not in covered}
            while left:
                naming = [item for item in pool if item.id not in out and left & set(names(item.id))]
                if not naming:
                    break  # the master has no recent line for these: the old line stays the only evidence
                best = max(naming, key=lambda item: (len(left & set(names(item.id))), selected.values.get(item.id, 0.0), -item.order))
                out[best.id] = (bullet, tuple(term for term in names(best.id) if term in left))
                covered |= set(names(best.id))
                left -= set(names(best.id))
    return out


def job_candidates(
    master: Master,
    profile: SelectionProfile,
    posting: SelectionPosting,
    *,
    mode: str = CANDIDATES,
    today: date | None = None,
) -> JobCandidates:
    """The candidate set of one posting from the whole master (see the module text). No model, no file."""

    if mode not in CANDIDATE_MODES:
        raise ValueError(f"mode must be one of {', '.join(CANDIDATE_MODES)}")
    today = today or date.today()
    stand_ins: dict[str, tuple[str, tuple[str, ...]]] = {}
    if mode == MODE_VIEW:
        selected = select(master, profile, posting, today=today)
        entries = {entry_id: list(bullets) for entry_id, bullets in selected.entries.items()}
    else:
        # The selector's pick before its page fit: with no page limit nothing is cut, and no layout is run.
        selected = select(master, profile, posting, today=today, measure=lambda _markdown: (1, 0.0), max_pages=_UNFITTED, fill=False)
        entries = {entry_id: list(bullets) for entry_id, bullets in selected.entries.items()}
        if mode == MODE_EVIDENCE:
            view = evidence_view(master, profile, posting, today=today)
            # Every role and degree with the view's bullets (a role's best first, as the selector prints them),
            # and the selector's own few projects: a shown project always keeps a line, so more would not fit.
            entries = {
                entry_id: sorted(bullets, key=lambda bullet: (-selected.values.get(bullet, 0.0), master.items[bullet].order))
                if master.entries[entry_id].section == "experience" else list(bullets)
                for entry_id, bullets in view.entries.items()
                if master.entries[entry_id].section != "projects" or entry_id in selected.entries
            }
        stand_ins = _stand_ins(master, selected, entries, today)
        for added in stand_ins:
            entries.setdefault(master.items[added].entry_id or "", []).append(added)
    item_ids = [*selected.summary, *(item for entry_id, bullets in entries.items() for item in (entry_id, *bullets)), *selected.other]
    markdown = render_selection(master, item_ids, selected.skills)
    lines = _numbered(render_selection(master, item_ids, selected.skills, ids=True))
    if len(lines) != len(resume_lines(markdown)):  # the two renders differ only in the id comments
        raise ValueError("the candidate set's lines do not number as its markdown does")
    printed = [line.item_id for line in lines if line.kind == "entry" and line.item_id is not None]
    return JobCandidates(
        mode=mode, profile=profile, posting=posting, selected=selected, summary=tuple(selected.summary),
        entries={entry_id: tuple(entries[entry_id]) for entry_id in printed},
        skills=tuple(selected.skills), other=tuple(selected.other), markdown=markdown, lines=lines, stand_ins=stand_ins,
    )


# --- the settled result: the skills line, the fit -------------------------------------------------


def _next_id(result: TailoredResume) -> str:
    numbers = [
        int(line.id[1:])
        for section in result.sections
        for line in section.all_lines()
        if line.id is not None and line.id[:1] == "L" and line.id[1:].isdigit()
    ]
    return f"L{max(numbers, default=0) + 1}"


def ensure_skills_line(result: TailoredResume, candidates: JobCandidates, ctx: TailorContext) -> TailoredResume:
    """``result`` showing the Skills line code assembled: added as a copy when no shown Skills line copies or cites it."""

    number = candidates.skills_line
    if number is None or number in ctx.withheld:
        return result
    for section in result.sections:
        if section.heading == "skills" and any(ref.kind == "resume" and ref.line == number for line in section.lines for ref in replaced_line(line).refs):
            return result
    added = _span_copy(_resume_ref(number, ctx), id=_next_id(result), origin="model")
    sections = list(result.sections)
    for index, section in enumerate(sections):
        if section.heading == "skills":
            sections[index] = replace(section, lines=(added, *section.lines))
            break
    else:
        # Where the selector prints Skills: after the roles and projects, before Education and Other.
        after = max((index for index, section in enumerate(sections) if section.heading in ("summary", "experience", "projects")), default=len(sections) - 1)
        sections.insert(after + 1, TailoredSection("skills", (added,)))
    return replace(result, sections=tuple(sections))


def measure_pages(result: TailoredResume) -> int | None:
    """The pages ``result`` prints on at the selector's spacing (``FIT_SCALE``); ``None`` when the renderer cannot say."""

    try:
        from .resume_pdf import pages_at

        return pages_at(result, FIT_SCALE)
    except Exception:  # noqa: BLE001 - no renderer (Typst missing or failing): the length is flagged, never guessed
        return None


def line_item_id(line: TailoredLine) -> str | None:
    """The master line one shown line stands for: the id on its first resume ref (an edited line: the line it replaced)."""

    return next((ref.item_id for ref in replaced_line(line).refs if ref.kind == "resume" and ref.item_id is not None), None)


def _entry_id(entry: TailoredEntry) -> str | None:
    return line_item_id(entry.heading[0]) if entry.heading else None


def cut_order(result: TailoredResume, candidates: JobCandidates, master: Master, *, today: date) -> tuple[list[tuple[str, str]], frozenset[str]]:
    """The cuts the fit may make on ``result``, in the one order it may make them (the module text's rules).

    ``(cuts, refill)``: each cut is ``("bullet", <line id>)`` or ``("role", <the id of the role's first
    heading line>)``; ``refill`` names the bullet cuts of the recent roles and the projects, which come back
    when the last cut left room (an old role's line never does: the oldest roles go first).
    """

    keywords = candidates.selected.keywords
    must = keywords.must if keywords is not None else ()
    values = candidates.selected.values
    roles = sorted(master.entries_in("experience"), key=_newest_first)
    rank = {entry.id: index for index, entry in enumerate(roles)}
    old = {entry.id for entry in roles if is_old_role(entry, today)}
    named: dict[str, tuple[str, ...]] = {}

    def names(line: TailoredLine) -> tuple[str, ...]:
        key = line.id or ""
        if key not in named:
            named[key] = tuple(term for term in must if mentions(line.text, term))
        return named[key]

    shown = [(section.heading, entry) for section in result.sections if section.heading in ENTRY_SECTIONS for entry in section.entries]
    evidence: dict[str, int] = {}
    for _heading, entry in shown:
        for line in entry.bullets:
            for term in names(line):
                evidence[term] = evidence.get(term, 0) + 1

    def only_evidence(line: TailoredLine) -> bool:
        return any(evidence.get(term, 0) <= 1 for term in names(line))

    def release(line: TailoredLine) -> None:
        for term in names(line):
            evidence[term] -= 1

    def is_old(entry: TailoredEntry) -> bool:
        entry_id = _entry_id(entry)
        return entry_id in old if entry_id in rank else _is_old_role(entry.heading, today)

    cuts: list[tuple[str, str]] = []
    experience = [entry for heading, entry in shown if heading == "experience" and entry.heading and entry.heading[0].id is not None]
    # 1. The old roles, oldest first: bullets from the end of the tailoring's order, then the role.
    olds = [entry for entry in experience if is_old(entry)]
    for entry in sorted(olds, key=lambda item: (-rank.get(_entry_id(item) or "", -1), -experience.index(item))):
        kept = 0
        for line in reversed(entry.bullets):
            if line.id is None or only_evidence(line):
                kept += 1
                continue
            release(line)
            cuts.append(("bullet", line.id))
        if not kept:
            cuts.append(("role", entry.heading[0].id))  # type: ignore[arg-type]
    # 2. The recent roles and the projects: the last line of the list whose last line is worth least, above the floors.
    lists: list[tuple[int, int, list[TailoredLine]]] = []  # (the floor, the place on the page, the lines still shown)
    for place, (heading, entry) in enumerate(shown):
        if heading == "experience" and not is_old(entry) and entry in experience:
            floor = FLOORS[min(rank.get(_entry_id(entry) or "", len(FLOORS) - 1), len(FLOORS) - 1)]
            lists.append((floor, place, list(entry.bullets)))
        elif heading == "projects":
            lists.append((_PROJECT_FLOOR, place, list(entry.bullets)))
    kept_lines: set[str] = set()
    refill: set[str] = set()
    while True:
        best: tuple[float, int, int] | None = None  # (the value, minus the place, the list)
        for index, (floor, place, lines) in enumerate(lists):
            if len(lines) <= floor:
                continue
            for line in reversed(lines):
                if line.id is None or line.id in kept_lines:
                    continue
                item_id = line_item_id(line)
                if item_id is None or only_evidence(line):
                    kept_lines.add(line.id)  # an answer's line, or the only evidence of a must-have: never cut
                    continue
                key = (values.get(item_id, 0.0), -place, index)
                if best is None or key < best:
                    best = key
                break
        if best is None:
            return cuts, frozenset(refill)
        lines = lists[best[2]][2]
        line = next(line for line in reversed(lines) if line.id is not None and line.id not in kept_lines)
        release(line)
        lines.remove(line)
        cuts.append(("bullet", line.id))  # type: ignore[arg-type]
        refill.add(line.id)  # type: ignore[arg-type]


def fit_selected(
    result: TailoredResume, candidates: JobCandidates, master: Master, *, today: date | None = None, measure: Measure | None = None,
    max_pages: int = MAX_PAGES,
) -> TailoredResume:
    """A settled tailoring of ``candidates`` cut to ``max_pages`` pages (the module text's fit); what was cut is on the result."""

    cuts, refill = cut_order(result, candidates, master, today=today or date.today())
    return fit_by_cuts(result, cuts, measure=measure or measure_pages, max_pages=max_pages, refill=refill)


def code_only(
    master: Master, candidates: JobCandidates, job: TailorJob, *, answers: Mapping[str, AnswerSource] | None = None,
    matrix: tuple[MatrixRow, ...] = (), today: date | None = None, measure: Measure | None = None,
) -> tuple[TailoredResume, JobCandidates]:
    """The fallback: the code's own 2-page selection as a settled tailored resume, and the candidate set it is made of.

    Every line is a copy of a master line, in the selector's order, put
    through the checks a model's answer goes through (the validator, the
    no-loss pass, the Skills rules: an answer that satisfies a posting skill
    is still shown), then the fit.  No model is called.
    """

    from .tailor_skills import finish_tailoring

    view = candidates if candidates.mode == MODE_VIEW else job_candidates(master, candidates.profile, candidates.posting, mode=MODE_VIEW, today=today)
    ctx = view.context(answers=answers, matrix=matrix)
    settled = finish_tailoring(apply_no_loss(validate_tailored_output(view.copy_all(withheld=ctx.withheld), job, ctx), job, ctx, today=today), job, ctx)
    return fit_selected(ensure_skills_line(settled, view, ctx), view, master, today=today, measure=measure), view


# --- what was picked, and what was left out -------------------------------------------------------


@dataclass(frozen=True)
class MasterSource:
    """The master revision one tailoring was made from (``TailorSources.master``): identities, never text."""

    revision_id: str
    revision: int
    content_sha256: str

    def to_json(self) -> dict[str, object]:
        return {"revision_id": self.revision_id, "revision": self.revision, "content_sha256": self.content_sha256}

    @classmethod
    def from_json(cls, obj: object) -> "MasterSource":
        value = _object_with_optional(obj, ("revision_id", "revision", "content_sha256"), (), "tailor_sources.master")
        revision = value["revision"]
        if type(revision) is not int or revision < 1:
            _fail("invalid_value", "tailor_sources.master.revision must be a positive integer")
        return cls(
            _string(value["revision_id"], "tailor_sources.master.revision_id"),
            revision,  # type: ignore[arg-type]
            _digest_value(value["content_sha256"], "tailor_sources.master.content_sha256"),
        )


@dataclass(frozen=True)
class SelectedLine:
    """One master line of a tailoring's selection: its id and why it is shown or left out (a stable code, and the sentence)."""

    id: str
    code: str
    reason: str

    def to_json(self) -> dict[str, object]:
        return {"id": self.id, "code": self.code, "reason": self.reason}

    @classmethod
    def from_json(cls, obj: object) -> "SelectedLine":
        value = _object_with_optional(obj, ("id", "code", "reason"), (), "tailor_response.selection line")
        return cls(_string(value["id"], "selection line id"), _string(value["code"], "selection line code"), _string(value["reason"], "selection line reason"))


@dataclass(frozen=True)
class SelectedSkill:
    name: str
    code: str
    reason: str

    def to_json(self) -> dict[str, object]:
        return {"name": self.name, "code": self.code, "reason": self.reason}

    @classmethod
    def from_json(cls, obj: object) -> "SelectedSkill":
        value = _object_with_optional(obj, ("name", "code", "reason"), (), "tailor_response.selection skill")
        return cls(_string(value["name"], "selection skill name"), _string(value["code"], "selection skill code"), _string(value["reason"], "selection skill reason"))


@dataclass(frozen=True)
class SelectionCut:
    """One thing the fit left out, in the order it was cut: a line (``bullet``) or a whole role (``role``), by master id."""

    id: str
    kind: str
    code: str
    reason: str

    def to_json(self) -> dict[str, object]:
        return {"id": self.id, "kind": self.kind, "code": self.code, "reason": self.reason}

    @classmethod
    def from_json(cls, obj: object) -> "SelectionCut":
        value = _object_with_optional(obj, ("id", "kind", "code", "reason"), (), "tailor_response.selection cut")
        kind = _string(value["kind"], "selection cut kind")
        if kind not in ("bullet", "role"):
            _fail("bad_enum", "tailor_response.selection cut kind must be bullet or role")
        return cls(_string(value["id"], "selection cut id"), kind, _string(value["code"], "selection cut code"), _string(value["reason"], "selection cut reason"))


def _strings(value: object, name: str) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str or not item for item in value):
        _fail("wrong_type", f"tailor_response.selection.{name} must be an array of non-empty strings")
    return tuple(value)  # type: ignore[arg-type]


def _items(value: object, name: str) -> list[object]:
    if type(value) is not list:
        _fail("wrong_type", f"tailor_response.selection.{name} must be an array")
    return value  # type: ignore[return-value]


@dataclass(frozen=True)
class TailorSelection:
    """Picked / Left out for one tailored resume, as it was when the resume was tailored (``TailorResponse.selection``).

    ``picked``: the master lines the resume shows, in its order; ``left_out``:
    every other summary, bullet and Other line of the master, in the master's
    order.  ``picked_by`` says who chose inside the candidate set: ``model``
    (the tailor call), or ``code`` when the call failed or no model was
    available (``fallback`` then holds the error code).  ``pins`` and
    ``excludes`` are the profile's, carried as stored; nothing reads them yet.
    A later line edit, Restore or attached edit does not rewrite this record.
    """

    selector_version: str
    candidates_version: str
    candidates: str
    picked_by: str
    fallback: str | None
    picked: tuple[SelectedLine, ...]
    left_out: tuple[SelectedLine, ...]
    skills_picked: tuple[SelectedSkill, ...]
    skills_left_out: tuple[SelectedSkill, ...]
    cut_for_length: tuple[SelectionCut, ...]
    pins: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "selector_version": self.selector_version,
            "candidates_version": self.candidates_version,
            "candidates": self.candidates,
            "picked_by": self.picked_by,
            "fallback": self.fallback,
            "counts": {"picked": len(self.picked), "left_out": len(self.left_out), "cut_for_length": len(self.cut_for_length)},
            "picked": [line.to_json() for line in self.picked],
            "left_out": [line.to_json() for line in self.left_out],
            "skills": {"picked": [skill.to_json() for skill in self.skills_picked], "left_out": [skill.to_json() for skill in self.skills_left_out]},
            "cut_for_length": [cut.to_json() for cut in self.cut_for_length],
            "pins": list(self.pins),
            "excludes": list(self.excludes),
        }

    @classmethod
    def from_json(cls, obj: object) -> "TailorSelection":
        value = _object_with_optional(
            obj,
            ("selector_version", "candidates_version", "candidates", "picked_by", "fallback", "picked", "left_out", "skills", "cut_for_length", "pins", "excludes"),
            ("counts",),
            "tailor_response.selection",
        )
        candidates = _string(value["candidates"], "tailor_response.selection.candidates")
        picked_by = _string(value["picked_by"], "tailor_response.selection.picked_by")
        if candidates not in CANDIDATE_MODES:
            _fail("bad_enum", f"tailor_response.selection.candidates must be one of {', '.join(CANDIDATE_MODES)}")
        if picked_by not in PICKED_BY:
            _fail("bad_enum", "tailor_response.selection.picked_by must be model or code")
        fallback = _optional_string(value["fallback"], "tailor_response.selection.fallback")
        if fallback is not None and not _KEY.fullmatch(fallback):
            _fail("invalid_value", "tailor_response.selection.fallback must be an error code")
        skills = _object_with_optional(value["skills"], ("picked", "left_out"), (), "tailor_response.selection.skills")
        return cls(
            selector_version=_string(value["selector_version"], "tailor_response.selection.selector_version"),
            candidates_version=_string(value["candidates_version"], "tailor_response.selection.candidates_version"),
            candidates=candidates,
            picked_by=picked_by,
            fallback=fallback,
            picked=tuple(SelectedLine.from_json(item) for item in _items(value["picked"], "picked")),
            left_out=tuple(SelectedLine.from_json(item) for item in _items(value["left_out"], "left_out")),
            skills_picked=tuple(SelectedSkill.from_json(item) for item in _items(skills["picked"], "skills.picked")),
            skills_left_out=tuple(SelectedSkill.from_json(item) for item in _items(skills["left_out"], "skills.left_out")),
            cut_for_length=tuple(SelectionCut.from_json(item) for item in _items(value["cut_for_length"], "cut_for_length")),
            pins=_strings(value["pins"], "pins"),
            excludes=_strings(value["excludes"], "excludes"),
        )


_CUT_ROLE = ("cut_oldest_role_dropped", "cut for length: oldest role dropped")
_CUT_OLD = ("cut_oldest_role_shortened", "cut for length: oldest role shortened")
_CUT_VALUE = ("cut_lowest_value", "cut for length: lowest value for this posting")
_NOT_SHOWN = ("left_out_by_tailoring", "offered to the tailoring, which did not show it")
_NOT_OFFERED = ("not_offered", "scores lower for this posting than the lines offered to the tailoring")
_OFFERED = ("offered", "among the lines of the master most relevant to this posting")


def selection_record(
    master: Master, candidates: JobCandidates, result: TailoredResume, *, picked_by: str = PICKED_BY_MODEL, fallback: str | None = None,
    pins: Sequence[str] = (), excludes: Sequence[str] = (), today: date | None = None,
) -> TailorSelection:
    """Picked / Left out for ``result``, a settled and fitted tailoring of ``candidates`` (see ``TailorSelection``)."""

    today = today or date.today()
    reasons = {line.id: line for line in candidates.selected.lines}
    old = {entry.id for entry in master.entries_in("experience") if is_old_role(entry, today)}
    shown: list[str] = []
    for section in result.sections:
        for line in section.body_lines():
            item_id = line_item_id(line)
            if item_id in master.items and item_id not in shown:
                shown.append(item_id)  # type: ignore[arg-type]
    # What the fit left out, by master id: a whole role's lines, and single lines (the old-role trim included).
    cut: dict[str, tuple[str, str]] = {}
    cuts: list[SelectionCut] = []
    length = result.length
    if length is not None and length.status == STATUS_CUT:
        gone_roles = {_entry_id(role.entry) for role in length.cut}
        for role in length.cut:
            entry_id = _entry_id(role.entry)
            if entry_id is not None:
                cuts.append(SelectionCut(entry_id, "role", *_CUT_ROLE))
            for line in role.entry.bullets:
                item_id = line_item_id(line)
                if item_id is not None:
                    cut[item_id] = _CUT_ROLE
        headings = {
            entry.heading[0].id: _entry_id(entry)
            for entry in (*(e for s in result.sections for e in s.entries), *(role.entry for role in length.cut)) if entry.heading
        }
        for trimmed in length.trimmed:
            entry_id = headings.get(trimmed.heading)
            why = _CUT_ROLE if entry_id in gone_roles else (_CUT_OLD if entry_id in old else _CUT_VALUE)
            for line in trimmed.bullets:
                item_id = line_item_id(line)
                if item_id is not None:
                    cut[item_id] = why
                    cuts.append(SelectionCut(item_id, "bullet", *why))
    offered = candidates.item_ids()

    picked: list[SelectedLine] = []
    for item_id in shown:
        known = reasons.get(item_id)
        if item_id in candidates.stand_ins:
            _old_line, terms = candidates.stand_ins[item_id]
            picked.append(SelectedLine(item_id, "shown_instead", f"names {', '.join(terms)}; offered in place of an older role's line"))
        elif known is not None and known.picked:
            picked.append(SelectedLine(item_id, known.code, known.reason))
        else:
            picked.append(SelectedLine(item_id, *_OFFERED))
    left: list[SelectedLine] = []
    seen = set(shown)
    for item in master.items.values():
        if item.kind not in (KIND_SUMMARY, KIND_BULLET, KIND_OTHER) or item.id in seen:
            continue
        known = reasons.get(item.id)
        if item.id in cut:
            left.append(SelectedLine(item.id, *cut[item.id]))
        elif item.id in offered:
            left.append(SelectedLine(item.id, *_NOT_SHOWN))
        elif known is not None and not known.picked:
            left.append(SelectedLine(item.id, known.code, known.reason))
        else:
            left.append(SelectedLine(item.id, *_NOT_OFFERED))
    skills = candidates.selected.skill_reasons
    return TailorSelection(
        selector_version=candidates.selected.selector_version,
        candidates_version=CANDIDATES_VERSION,
        candidates=candidates.mode,
        picked_by=picked_by,
        fallback=fallback,
        picked=tuple(picked),
        left_out=tuple(left),
        skills_picked=tuple(SelectedSkill(skill.name, skill.code, skill.reason) for skill in skills if skill.picked),
        skills_left_out=tuple(SelectedSkill(skill.name, skill.code, skill.reason) for skill in skills if not skill.picked),
        cut_for_length=tuple(cuts),
        pins=tuple(pins),
        excludes=tuple(excludes),
    )


# --- one tailoring from the stored master ---------------------------------------------------------

_KEPT_LOCK = threading.Lock()
#: workpad path -> (the journal head it was read at, the master stored then or ``None``).
_kept: dict[str, tuple[str, object]] = {}


def stored_master(home_root: Path, target: Path, *, resolved: object | None = None):
    """The stored master (``master_store.StoredMaster``) of this Scout home, or ``None``: no master, or no gig to hold one.

    A committed journal read, kept per journal head. ``resolved`` is the
    gig's workpad when the caller holds it (else it is resolved here, which
    starts git): with it, a second call on an unchanged journal reads two
    small files and starts no subprocess.
    """

    from ..private_records import PrivateRecordError
    from ..workpad import WorkpadError, resolve_workpad, workpad_head_without_git
    from .master_store import MasterStoreError, load_master

    if resolved is None:
        try:
            resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
        except WorkpadError:
            return None  # nothing is bound here (a pasted resume with no gig): there is no master either
    head = workpad_head_without_git(Path(resolved.path))  # type: ignore[attr-defined]
    key = str(resolved.path)  # type: ignore[attr-defined]
    if head is not None:
        with _KEPT_LOCK:
            kept = _kept.get(key)
        if kept is not None and kept[0] == head:
            return kept[1]
    try:
        stored = load_master(home_root=home_root, target=target, gig_id=resolved.gig_id)  # type: ignore[attr-defined]
    except (WorkpadError, MasterStoreError, PrivateRecordError):
        return None  # a master that cannot be read is not tailored from: the profile's own resume is, as before
    if head is not None:
        with _KEPT_LOCK:
            _kept[key] = (head, stored)
    return stored


def detached(home_root: Path, profile: object) -> bool:
    """True when the profile's resume was put there by hand after its selection was made (``master_profiles``).

    Such a profile shows a resume the user chose over the selection: a
    tailoring reads that resume, as before, until the selection is refreshed.
    """

    selection = getattr(profile, "master_selection", None)
    if selection is None:
        return False
    from .contact_cleanup import same_resume_revision

    return not same_resume_revision(home_root, selection.resume_revision_id, profile.resume_ref.revision_id)  # type: ignore[attr-defined]


def digest_parts(home_root: Path, target: Path, profile: object, *, resolved: object | None = None) -> tuple[object, ...]:
    """What the pipeline's tailor digest adds when this profile's tailoring reads the master; ``()`` when it does not.

    The master's revision, the selector's version and the candidate rule:
    a write of the master, a change of the selector's weights or of the
    candidate set each re-open the stored tailorings.  Empty without a master
    and for a detached profile, so such a digest is exactly what it was.
    ``resolved``: the gig's workpad when the caller holds it (``stored_master``).
    """

    stored = stored_master(home_root, target, resolved=resolved)
    if stored is None or detached(home_root, profile):
        return ()
    return ("master", stored.revision.revision_id, SELECTOR_VERSION, CANDIDATES_VERSION, CANDIDATES)


@dataclass(frozen=True)
class MasterTailoring:
    """One job's tailoring from the stored master: the candidate set, and how its result is finished."""

    master: Master
    source: MasterSource
    candidates: JobCandidates
    pins: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
    today: date | None = None

    def context(self, *, answers: Mapping[str, AnswerSource] | None = None, matrix: tuple[MatrixRow, ...] = ()) -> TailorContext:
        return self.candidates.context(answers=answers, matrix=matrix)

    def finish(self, result: TailoredResume, ctx: TailorContext) -> tuple[TailoredResume, TailorSelection]:
        """The model's settled answer with the code's Skills line shown, fitted; and what was picked."""

        fitted = fit_selected(ensure_skills_line(result, self.candidates, ctx), self.candidates, self.master, today=self.today)
        return fitted, selection_record(self.master, self.candidates, fitted, pins=self.pins, excludes=self.excludes, today=self.today)

    def fall_back(self, job: TailorJob, ctx: TailorContext, code: str) -> tuple[TailoredResume, TailorSelection, TailorContext]:
        """The code's own selection when the tailor call failed with ``code`` (no model): the resume, what was picked, its context."""

        result, view = code_only(self.master, self.candidates, job, answers=ctx.answers, matrix=ctx.matrix, today=self.today)
        record = selection_record(
            self.master, view, result, picked_by=PICKED_BY_CODE, fallback=code, pins=self.pins, excludes=self.excludes, today=self.today,
        )
        return result, record, view.context(answers=ctx.answers, matrix=ctx.matrix)


def master_tailoring(
    *, home_root: Path, target: Path, profile: object | None, job: TailorJob, today: date | None = None, resolved: object | None = None,
) -> MasterTailoring | None:
    """How one tailoring reads the master, or ``None`` when it does not (see the module text).

    ``None``: no master is stored, the resume is not a profile's (pasted
    text), or the profile's resume was replaced by hand after its selection
    (``detached``).  The profile's prior is the lines its selection shows,
    else its titles.
    """

    if profile is None:
        return None
    stored = stored_master(home_root, target, resolved=resolved)
    if stored is None or detached(home_root, profile):
        return None
    selection = getattr(profile, "master_selection", None)
    prior = SelectionProfile(
        titles=tuple(profile.titles),  # type: ignore[attr-defined]
        base_ids=tuple(selection.item_ids) if selection is not None else None,
        profile_id=profile.profile_id,  # type: ignore[attr-defined]
        label=profile.label,  # type: ignore[attr-defined]
    )
    candidates = job_candidates(stored.master, prior, SelectionPosting(job.title, job.posting_text, job.company, job.location), today=today)
    return MasterTailoring(
        master=stored.master,
        source=MasterSource(stored.revision.revision_id, stored.revision.revision, stored.revision.content_sha256),
        candidates=candidates,
        pins=tuple(selection.pins) if selection is not None else (),
        excludes=tuple(selection.excludes) if selection is not None else (),
        today=today,
    )


def tailoring_for_resume(resume: object, job: TailorJob, *, home_root: Path, target: Path, today: date | None = None) -> MasterTailoring | None:
    """``master_tailoring`` for the resume one tailoring resolved (``ResolvedResume``); ``None`` for a pasted resume.

    Nothing but the master is read until one is found: without a master
    this costs one kept read (``stored_master``).
    """

    profile_id = getattr(resume, "profile_id", None)
    if profile_id is None:
        return None
    from ..workpad import WorkpadError, resolve_workpad
    from . import profile_records

    try:
        resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
        if stored_master(home_root, target, resolved=resolved) is None:
            return None
        profile = next((record for record in profile_records.list_profiles(resolved) if record.profile_id == profile_id and record.state != "deleted"), None)
    except (WorkpadError, profile_records.ProfileRecordError):
        return None
    return master_tailoring(home_root=home_root, target=target, profile=profile, job=job, today=today, resolved=resolved)


__all__ = [
    "CANDIDATES",
    "CANDIDATES_VERSION",
    "CANDIDATE_MODES",
    "MODE_EVIDENCE",
    "MODE_SHORTLIST",
    "MODE_VIEW",
    "PICKED_BY",
    "PICKED_BY_CODE",
    "PICKED_BY_MODEL",
    "CandidateLine",
    "JobCandidates",
    "MasterSource",
    "MasterTailoring",
    "SelectedLine",
    "SelectedSkill",
    "SelectionCut",
    "TailorSelection",
    "code_only",
    "cut_order",
    "detached",
    "digest_parts",
    "ensure_skills_line",
    "fit_selected",
    "job_candidates",
    "line_item_id",
    "master_tailoring",
    "measure_pages",
    "selection_record",
    "stored_master",
    "tailoring_for_resume",
]
