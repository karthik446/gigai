"""Add or Remove one master line on a job's tailored resume (0.1.10.9 master P5).

A resume tailored from the master resume carries Picked / Left out
(``TailorResponse.selection``, ``tailor_master``).  This module is its
writer: the user takes a picked line OFF this one job's resume (``remove``)
or puts a left-out line ON it (``add``), by master id.

* **Remove** takes the line out of the resume and lists it under Left out
  as ``removed_by_you``.  Nothing else moves.
* **Add** shows the line.  A line the fit had cut for length comes back as
  it was (its sources kept); any other line is a copy of the master line as
  the master words it NOW, under its role (a role or project the resume did
  not show comes with its heading).
* **The fit runs again** when an Add pushes a resume that fitted over the
  page limit.  The lines that would go to make room are the fit's own next
  cuts (``tailor_master.cut_order``: the oldest roles first, then the
  lowest-value last line of a recent role; never the added line, nor one
  the user added before), the fewest that fit.  With ``fit="ask"`` (the default) NOTHING is stored and
  the answer names those lines (``would_cut``); the caller then sends the
  same Add with ``fit="cut"`` (make room) or ``fit="keep"`` (keep both: the
  resume is over the limit and says so).  What is cut goes onto the
  resume's own length record (``TailoredResume.length``), so the "Cut for
  length" line shows it and Restore puts it back.

The stored resume's ``updated_at`` is unchanged, as for a line choice: it is
the same tailoring.  The resume is then the user's (the pipeline's tailor
step keeps any stored resume that is not its own last tailoring, byte for
byte).  No model, no network; the posting is read only where Scout already
holds its text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
import sqlite3

from .master_resume import KIND_BULLET, KIND_OTHER, KIND_SUMMARY, Master, MasterItem
from .master_selection import MAX_PAGES
from .tailor_length import (
    STATUS_CUT,
    STATUS_OVER,
    STATUS_RESTORED,
    LengthFit,
    Measure,
    TrimmedRole,
    _heading_id,
    _left_out,
    role_label,
    shown_whole,
)
from .tailor_master import (
    MasterTailoring,
    SelectedLine,
    SelectionCut,
    TailorSelection,
    _entry_id,
    cut_order,
    line_item_id,
    measure_pages,
)
from .tailored_resume import (
    ENTRY_SECTIONS,
    LineAlternative,
    SourceRef,
    TailorError,
    TailorJob,
    TailorResponse,
    TailoredEntry,
    TailoredLine,
    TailoredResume,
    TailoredSection,
    list_tailored_resumes,
    render_markdown,
    save_tailor_response,
    shown_text,
    tailored_resume_path,
    tailored_resume_write_lock,
)

SELECTION_USES: tuple[str, ...] = ("add", "remove")
FIT_ASK, FIT_CUT, FIT_KEEP = "ask", "cut", "keep"
FITS: tuple[str, ...] = (FIT_ASK, FIT_CUT, FIT_KEEP)

ADDED = ("added_by_you", "you added it to this resume")
REMOVED = ("removed_by_you", "you removed it from this resume")
MADE_ROOM = ("cut_to_make_room", "cut for length: made room for a line you added")

#: Where a section the resume does not show yet goes (the selector's print order).
_SECTION_ORDER = ("summary", "experience", "projects", "skills", "education", "other")


@dataclass(frozen=True)
class RoomCut:
    """One thing that goes (or would go) to make room for an added line: a line, or a whole role."""

    id: str | None
    kind: str  # bullet | role
    text: str
    role: str

    def to_json(self) -> dict[str, object]:
        return {"id": self.id, "kind": self.kind, "text": self.text, "role": self.role}


@dataclass(frozen=True)
class SelectionEdit:
    """What one Add or Remove did.

    ``applied`` false: nothing was stored (an Add that needs room, asked
    with ``fit="ask"``): ``would_cut`` names what making room would cut.
    ``cut``: what was cut to make room (``fit="cut"``).  ``pages``: the
    resume as it is now (or would be, with the line and nothing cut).
    """

    response: TailorResponse
    use: str
    item_id: str
    applied: bool
    changed: bool
    pages: int | None
    max_pages: int
    would_cut: tuple[RoomCut, ...] = ()
    cut: tuple[RoomCut, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "use": self.use, "item_id": self.item_id, "applied": self.applied, "changed": self.changed,
            "needs_choice": not self.applied, "pages": self.pages, "max_pages": self.max_pages,
            "would_cut": [item.to_json() for item in self.would_cut], "cut": [item.to_json() for item in self.cut],
        }


# --- the resume with everything back, and what is left out of it -------------------------------


@dataclass
class _Left:
    """What the length record leaves out, by id: whole roles (heading ids) and single lines per role."""

    roles: list[str]
    #: heading id -> (the line ids left out, the dropped records the old-role trim added)
    lines: dict[str, tuple[list[str], tuple[LineAlternative, ...]]]


def _open(result: TailoredResume) -> tuple[TailoredResume, _Left]:
    """``(the resume with every cut role and line back in place, what the record leaves out)``; nothing for a record that cuts nothing."""

    length = result.length
    if length is None or length.status != STATUS_CUT:
        return replace(result, length=None), _Left([], {})
    left = _Left(
        [heading for heading in (_heading_id(role.entry) for role in length.cut) if heading is not None],
        {role.heading: ([line.id for line in role.bullets if line.id is not None], role.records) for role in length.trimmed},
    )
    return shown_whole(result), left


def _close(whole: TailoredResume, left: _Left) -> tuple[TailoredResume, tuple, tuple[TrimmedRole, ...]]:
    """``whole`` with ``left`` left out again: ``(the shown resume, the cut roles, the trimmed lines)``, as the record holds them."""

    wanted: list[TrimmedRole] = []
    for section in whole.sections:
        for entry in section.entries:
            heading = _heading_id(entry)
            ids, records = left.lines.get(heading or "", ([], ()))
            lines = tuple(line for line in entry.bullets if line.id in ids)
            if heading is not None and lines:
                wanted.append(TrimmedRole(heading, role_label(entry), lines, records))
    return _left_out(whole, left.roles, wanted)


def _next_number(whole: TailoredResume, count: int) -> int:
    """A source line number no line of this resume cites: past the candidate set and past every earlier Add."""

    cited = [ref.line or 0 for section in whole.sections for line in section.all_lines() for ref in (*line.refs, *(line.edited_from.refs if line.edited_from else ()))]
    return max([count, *cited]) + 1


def _next_line_id(whole: TailoredResume) -> int:
    numbers = [int(line.id[1:]) for section in whole.sections for line in section.all_lines() if line.id and line.id[:1] == "L" and line.id[1:].isdigit()]
    return max(numbers, default=0) + 1


class _Maker:
    """New copy lines of master lines, each with the next line id and an unused source number.

    Their origin is ``model``, like every copy code settles (``tailor_master``): who picked a line is the
    selection's to say (``added_by_you``), and ``user`` on a copy line means "the user chose the original"."""

    def __init__(self, whole: TailoredResume, line_count: int) -> None:
        self._id = _next_line_id(whole)
        self._number = _next_number(whole, line_count)

    def line(self, text: str, item_id: str) -> TailoredLine:
        made = TailoredLine("copy", text, (SourceRef("resume", self._number, None, text, (), item_id),), f"L{self._id}", None, "model")
        self._id += 1
        self._number += 1
        return made


def _find(whole: TailoredResume, item_id: str) -> TailoredLine | None:
    return next((line for section in whole.sections for line in section.body_lines() if line_item_id(line) == item_id), None)


def _with_section(whole: TailoredResume, name: str) -> tuple[TailoredResume, int]:
    for index, section in enumerate(whole.sections):
        if section.heading == name:
            return whole, index
    place = _SECTION_ORDER.index(name)
    index = next((i for i, section in enumerate(whole.sections) if _SECTION_ORDER.index(section.heading) > place), len(whole.sections))
    sections = list(whole.sections)
    sections.insert(index, TailoredSection(name))
    return replace(whole, sections=tuple(sections)), index


def _put(whole: TailoredResume, master: Master, item: MasterItem, maker: _Maker) -> TailoredResume:
    """``whole`` showing master line ``item`` as a new copy line: under its entry (made when the resume has none), else in its section."""

    whole, index = _with_section(whole, item.section)
    section = whole.sections[index]
    if item.section not in ENTRY_SECTIONS:
        changed = replace(section, lines=(*section.lines, maker.line(f"- {item.text}", item.id)))
    else:
        entries = list(section.entries)
        at = next((i for i, entry in enumerate(entries) if _entry_id(entry) == item.entry_id), None)
        if at is None:
            source = master.entries[item.entry_id]  # type: ignore[index]
            heading = (maker.line(f"### {source.heading}", source.id), *(maker.line(line, source.id) for line in source.sublines))
            order = [entry.id for entry in master.entries_in(item.section)]
            shown = [_entry_id(entry) for entry in entries]
            before = order[: order.index(source.id)]
            at = next((shown.index(other) + 1 for other in reversed(before) if other in shown), 0)
            entries.insert(at, TailoredEntry(heading, ()))
        entry = entries[at]
        entries[at] = replace(entry, bullets=(*entry.bullets, maker.line(f"- {item.text}", item.id)))
        changed = replace(section, entries=tuple(entries))
    sections = list(whole.sections)
    sections[index] = changed
    return replace(whole, sections=tuple(sections))


def _without(whole: TailoredResume, line_id: str) -> TailoredResume:
    sections = tuple(
        replace(
            section,
            lines=tuple(line for line in section.lines if line.id != line_id),
            entries=tuple(replace(entry, bullets=tuple(line for line in entry.bullets if line.id != line_id)) for entry in section.entries),
        )
        for section in whole.sections
    )
    return replace(whole, sections=sections)


def _role_of(whole: TailoredResume, line_id: str) -> tuple[str | None, TailoredEntry | None]:
    for section in whole.sections:
        for entry in section.entries:
            if any(line.id == line_id for line in entry.bullets):
                return _heading_id(entry), entry
    return None, None


# --- one change --------------------------------------------------------------------------------


def _record(
    shown: TailoredResume, cut: tuple, trimmed: tuple[TrimmedRole, ...], *, before: LengthFit | None, pages: int | None,
    full_pages: int | None, max_pages: int,
) -> TailoredResume:
    """``shown`` with the length record that says what it now leaves out (none when it fits with nothing left out)."""

    if cut or trimmed:
        return replace(shown, length=LengthFit(max_pages, pages, full_pages, STATUS_CUT, cut, trimmed))
    if before is not None and before.status == STATUS_RESTORED:
        return replace(shown, length=replace(before, full_pages=pages))
    if pages is not None and pages > max_pages:
        return replace(shown, length=LengthFit(max_pages, pages, pages, STATUS_OVER))
    if pages is None and before is not None:
        return replace(shown, length=before if not before.leaves_out() else None)
    return replace(shown, length=None)


def _selection_after(
    selection: TailorSelection, master: Master | None, *, added: str | None, removed: str | None, room: Sequence[tuple[str, str]],
    back: Sequence[str] = (),
) -> TailorSelection:
    """Picked / Left out after one change: the added line picked, the removed and the room-making ones left out, each with why."""

    gone = {item_id: code for item_id, code in room}
    if removed is not None:
        gone[removed] = "removed"
    picked = [line for line in selection.picked if line.id not in gone and line.id != added]
    left = [line for line in selection.left_out if line.id != added and line.id not in gone]
    cuts = [cut for cut in selection.cut_for_length if cut.id != added and cut.id not in back]
    if added is not None:
        picked.append(SelectedLine(added, *ADDED))
    for item_id, kind in room:
        if kind == "bullet":
            left.append(SelectedLine(item_id, *MADE_ROOM))
        cuts.append(SelectionCut(item_id, kind, *MADE_ROOM))
    if removed is not None:
        left.append(SelectedLine(removed, *REMOVED))
    if master is not None:
        order = {item_id: index for index, item_id in enumerate(master.items)}
        left.sort(key=lambda line: order.get(line.id, len(order)))
    return replace(selection, picked=tuple(picked), left_out=tuple(left), cut_for_length=tuple(cuts))


def edit_selection(
    stored: TailorResponse, master: Master, *, use: str, item_id: str, fit: str = FIT_ASK, tailoring: MasterTailoring | None = None,
    measure: Measure | None = None, today: date | None = None, max_pages: int = MAX_PAGES,
) -> SelectionEdit:
    """One Add or Remove on a stored resume that was tailored from the master (see the module text). Pure: the caller stores it.

    ``master`` is the master as it is now (an added line's wording);
    ``tailoring`` is the job's candidate set (``tailor_master.master_tailoring``),
    whose values order the cuts that make room; without it an Add that
    needs room can only keep both.
    """

    if use not in SELECTION_USES:
        raise TailorError("invalid_value", "use must be add or remove")
    if fit not in FITS:
        raise TailorError("invalid_value", "fit must be ask, cut or keep")
    selection = stored.selection
    if selection is None:
        raise TailorError("selection_unavailable", "this resume was not tailored from a master resume: it has no Picked / Left out to change")
    measure = measure or measure_pages
    result = stored.result
    before_pages = measure(result)
    whole, left = _open(result)
    room: list[tuple[str, str]] = []
    room_cuts: list[RoomCut] = []
    back: list[str] = []  # a role the Add brought back (it was cut whole)

    def done(shown: TailoredResume, cut: tuple, trimmed: tuple[TrimmedRole, ...], pages: int | None, *, added: str | None, removed: str | None) -> SelectionEdit:
        full_pages = measure(whole) if (cut or trimmed) else pages
        final = _record(shown, cut, trimmed, before=result.length, pages=pages, full_pages=full_pages, max_pages=max_pages)
        response = replace(
            stored, result=final, markdown=render_markdown(final),
            selection=_selection_after(selection, master, added=added, removed=removed, room=room, back=back),
        )
        return SelectionEdit(response, use, item_id, True, True, pages, max_pages, cut=tuple(room_cuts))

    if use == "remove":
        line = _find(replace(result, length=None), item_id)
        if line is None or line.id is None:
            raise TailorError("selection_line_not_shown", f"this resume does not show master line {item_id!r}")
        whole = _without(whole, line.id)
        shown, cut, trimmed = _close(whole, left)
        return done(shown, cut, trimmed, measure(shown), added=None, removed=item_id)

    if _find(replace(result, length=None), item_id) is not None:
        return SelectionEdit(stored, use, item_id, True, False, before_pages, max_pages)  # already shown: idempotent
    held = _find(whole, item_id)  # a line the fit cut for length: it comes back as it was
    if held is not None and held.id is not None:
        heading, entry = _role_of(whole, held.id)
        if heading in left.roles and entry is not None:  # its role was cut whole: the role comes back with this one line
            left.roles.remove(heading)
            _ids, records = left.lines.get(heading, ([], ()))
            left.lines[heading] = ([line.id for line in entry.bullets if line.id is not None and line.id != held.id], records)
            if _entry_id(entry) is not None:
                back.append(_entry_id(entry))  # type: ignore[arg-type]
        elif heading in left.lines:
            ids, records = left.lines[heading]
            left.lines[heading] = ([line_id for line_id in ids if line_id != held.id], records)
        added_line_id = held.id
    else:
        item = master.items.get(item_id)
        if item is None:
            raise TailorError("master_line_not_found", f"the master has no line {item_id!r} (it may be retired)")
        if item.kind not in (KIND_SUMMARY, KIND_BULLET, KIND_OTHER):
            raise TailorError("selection_line_unsupported", "a Skills line is not added by id: the skills shown follow the lines shown")
        maker = _Maker(whole, stored.sources.resume_line_count)
        whole = _put(whole, master, item, maker)
        added_line_id = _find(whole, item_id).id  # type: ignore[union-attr]
    shown, cut, trimmed = _close(whole, left)
    pages = measure(shown)
    fitted = before_pages is not None and before_pages <= max_pages
    if not (fitted and pages is not None and pages > max_pages) or fit == FIT_KEEP:
        return done(shown, cut, trimmed, pages, added=item_id, removed=None)

    # The resume fitted and the added line pushes it over: the fit's own next cuts, the fewest that fit, never the added line.
    order: list[tuple[str, str]] = []
    if tailoring is not None:
        order = cut_order(shown, tailoring.candidates, tailoring.master, today=today or tailoring.today or date.today())[0]
    by_id = {line.id: line for section in shown.sections for line in section.all_lines() if line.id is not None}
    yours = {line.id for line in selection.picked if line.code == ADDED[0]}  # lines the user added earlier stay, like this one
    trial, trial_cut, trial_trimmed, trial_pages = shown, cut, trimmed, pages
    mine: dict[str, list[str]] = {}  # heading id -> the lines this loop cut: they go with their role when it is cut whole
    for kind, target in order:
        if trial_pages is None or trial_pages <= max_pages:
            break
        if kind == "bullet":
            if target == added_line_id or line_item_id(by_id[target]) in yours:
                continue
            heading, entry = _role_of(whole, target)
            if heading is None or entry is None:
                continue
            ids, records = left.lines.get(heading, ([], ()))
            left.lines[heading] = ([*ids, target], records)
            mine.setdefault(heading, []).append(target)
            line = by_id[target]
            room_cuts.append(RoomCut(line_item_id(line), "bullet", shown_text(line), role_label(entry)))
            if line_item_id(line) is not None:
                room.append((line_item_id(line), "bullet"))  # type: ignore[arg-type]
        else:
            entry = next((entry for section in trial.sections for entry in section.entries if _heading_id(entry) == target), None)
            if entry is None or entry.bullets:
                continue  # a role goes whole only once every line of it is cut (the added line keeps its role)
            left.roles.append(target)
            ids, records = left.lines.get(target, ([], ()))
            left.lines[target] = ([line_id for line_id in ids if line_id not in mine.get(target, ())], records)
            room_cuts.append(RoomCut(_entry_id(entry), "role", role_label(entry), role_label(entry)))
            if _entry_id(entry) is not None:
                room.append((_entry_id(entry), "role"))  # type: ignore[arg-type]
        trial, trial_cut, trial_trimmed = _close(whole, left)
        trial_pages = measure(trial)
    if fit == FIT_ASK:
        return SelectionEdit(stored, use, item_id, False, False, pages, max_pages, would_cut=tuple(room_cuts))
    return done(trial, trial_cut, trial_trimmed, trial_pages, added=item_id, removed=None)


# --- the stored resume -----------------------------------------------------------------------------


def _held_posting(home_root: Path, target: Path, stored: TailorResponse) -> TailorJob:
    """The job as the selector reads it, with the posting text Scout already holds (the index, else the stored assessment); no request."""

    text = ""
    try:
        from .find_jobs.job_source import index_posting
        from .quick_assess import find_quick_assessment_by_job_identity

        job = index_posting(home_root, target, stored.job.job_identity)
        if job is not None and job.text.strip():
            text = job.text
        else:
            assessed = find_quick_assessment_by_job_identity(home_root, target, stored.job.job_identity)
            text = (assessed.posting_text or "") if assessed is not None else ""
    except (ValueError, RuntimeError, OSError, LookupError, sqlite3.Error):  # the posting only orders the cuts: without it the profile's prior does
        text = ""
    return TailorJob(stored.job.title, stored.job.company, stored.job.location, text)


def change_stored_selection(
    home_root: Path, target: Path, *, profile_id: str, job_identity: str, use: str, item_id: str, fit: str = FIT_ASK,
    updated_at: str | None = None, measure: Measure | None = None,
) -> SelectionEdit:
    """Add or Remove one master line on a stored tailored resume, and store the result (unless the Add asks first).

    ``updated_at`` (the API's revision check) must be the stored one, else
    ``tailored_resume_changed``.  The read, the check and the write happen
    under the store's write lock.  Raises ``TailorError``: ``invalid_value``,
    ``tailored_resume_not_found``, ``tailored_resume_changed``,
    ``selection_unavailable``, ``master_not_found``, ``master_line_not_found``,
    ``selection_line_not_shown``, ``selection_line_unsupported``.
    """

    from ..workpad import WorkpadError, resolve_workpad
    from . import profile_records
    from .tailor_master import master_tailoring, stored_master

    if use not in SELECTION_USES:
        raise TailorError("invalid_value", "use must be add or remove")
    if fit not in FITS:
        raise TailorError("invalid_value", "fit must be ask, cut or keep")

    def newest() -> TailorResponse:
        items = list_tailored_resumes(home_root, target, profile_id=profile_id, job_identity=job_identity)
        if not items:
            raise TailorError("tailored_resume_not_found", "no stored tailored resume for that profile and job")
        return items[0]

    with tailored_resume_write_lock(tailored_resume_path(home_root, target, profile_id, job_identity)):
        stored = newest()
        if updated_at is not None and stored.updated_at != updated_at:
            raise TailorError("tailored_resume_changed", "a newer tailoring replaced this resume; reload it")
        if stored.selection is None:
            raise TailorError("selection_unavailable", "this resume was not tailored from a master resume: it has no Picked / Left out to change")
        try:
            resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
        except WorkpadError as exc:
            raise TailorError("target_unavailable", "this folder is not bound to a GigAI project") from exc
        held = stored_master(home_root, target, resolved=resolved)
        if held is None:
            raise TailorError("master_not_found", "there is no master resume to take the line from")
        tailoring = None
        if use == "add":
            try:
                profile = next((record for record in profile_records.list_profiles(resolved) if record.profile_id == profile_id and record.state != "deleted"), None)
                tailoring = master_tailoring(
                    home_root=home_root, target=target, profile=profile, job=_held_posting(home_root, target, stored), resolved=resolved,
                )
            except (ValueError, RuntimeError, OSError):
                tailoring = None  # the cuts then cannot be ordered: an Add that needs room can only keep both
        edit = edit_selection(stored, held.master, use=use, item_id=item_id, fit=fit, tailoring=tailoring, measure=measure)
        if edit.applied and edit.changed:
            save_tailor_response(edit.response, home_root=home_root)
    return edit


__all__ = [
    "ADDED",
    "FITS",
    "FIT_ASK",
    "FIT_CUT",
    "FIT_KEEP",
    "MADE_ROOM",
    "REMOVED",
    "RoomCut",
    "SELECTION_USES",
    "SelectionEdit",
    "change_stored_selection",
    "edit_selection",
]
