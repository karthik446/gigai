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
* **An Add always applies, and nothing else moves** (0.1.11.5 item 1c).  No
  page is counted and nothing is rendered: the page is the user's to fit,
  with the spacing of the job's preview, and the preview says how many pages
  the resume now prints on.  (Until 0.1.11.5 an Add that pushed a resume past
  its page limit stored NOTHING and asked which lines to cut to make room:
  ``needs_choice`` / ``would_cut``, the ``fit`` of the request.  ``fit`` is
  still accepted, and ignored; ``needs_choice`` is always false, ``would_cut``
  and ``cut`` always empty, ``pages`` null.)  An Add may take a resume past
  the pick's cap of bullets: the cap is the pick's, not the user's.
* What an older pick had cut for length stays on the resume's own length
  record (``TailoredResume.length``) through an Add or a Remove, so Restore
  still puts it back.

The stored resume's ``updated_at`` is unchanged, as for a line choice: it is
the same tailoring.  The resume is then the user's (the pipeline's tailor
step keeps any stored resume that is not its own last tailoring, byte for
byte).  No model, no network; the posting is read only where Scout already
holds its text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from .master_resume import KIND_BULLET, KIND_OTHER, KIND_SUMMARY, Master, MasterItem
from .master_selection import MAX_PAGES
from .tailor_length import (
    STATUS_CUT,
    STATUS_RESTORED,
    LengthFit,
    TrimmedRole,
    _heading_id,
    _left_out,
    role_label,
    shown_whole,
)
from .tailor_master import (
    SelectedLine,
    SelectionCut,
    TailorSelection,
    _entry_id,
    line_item_id,
)
from .tailored_resume import (
    ENTRY_SECTIONS,
    LineAlternative,
    SourceRef,
    TailorError,
    TailorResponse,
    TailoredEntry,
    TailoredLine,
    TailoredResume,
    TailoredSection,
    list_tailored_resumes,
    render_markdown,
    save_tailor_response,
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

    ``applied`` is always true and ``would_cut`` / ``cut`` always empty since
    0.1.11.5 (an Add never needs room: no page is counted), and ``pages`` is
    ``None``; the fields stay for callers of the route.
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


def _record(shown: TailoredResume, cut: tuple, trimmed: tuple[TrimmedRole, ...], *, before: LengthFit | None) -> TailoredResume:
    """``shown`` with the length record that says what an OLDER pick still leaves out of it (none when nothing is).

    No page is counted (0.1.11.5): the record keeps the page numbers it had, for the sentence that says what was cut."""

    if cut or trimmed:
        return replace(shown, length=LengthFit(
            before.max_pages if before is not None else MAX_PAGES, before.pages if before is not None else None,
            before.full_pages if before is not None else None, STATUS_CUT, cut, trimmed,
        ))
    if before is not None and before.status == STATUS_RESTORED:
        return replace(shown, length=before)
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
    if added is not None and master is not None and added in master.items:
        # 0.1.11.4 item 9: a role that kept only its heading shows a line again: it is no longer a role cut.
        role = master.items[added].entry_id
        cuts = [cut for cut in cuts if not (cut.kind == "role" and cut.id == role)]
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


def edit_selection(stored: TailorResponse, master: Master, *, use: str, item_id: str, fit: str = FIT_ASK) -> SelectionEdit:
    """One Add or Remove on a stored resume that was tailored from the master (see the module text). Pure: the caller stores it.

    ``master`` is the master as it is now (an added line's wording).  ``fit`` is accepted and ignored (the module
    text): nothing is laid out, and an Add always applies.
    """

    if use not in SELECTION_USES:
        raise TailorError("invalid_value", "use must be add or remove")
    if fit not in FITS:
        raise TailorError("invalid_value", "fit must be ask, cut or keep")
    selection = stored.selection
    if selection is None:
        raise TailorError("selection_unavailable", "this resume was not tailored from a master resume: it has no Picked / Left out to change")
    result = stored.result
    whole, left = _open(result)
    back: list[str] = []  # a role the Add brought back (it was cut whole)

    def done(shown: TailoredResume, cut: tuple, trimmed: tuple[TrimmedRole, ...], *, added: str | None, removed: str | None) -> SelectionEdit:
        final = _record(shown, cut, trimmed, before=result.length)
        response = replace(
            stored, result=final, markdown=render_markdown(final),
            selection=_selection_after(selection, master, added=added, removed=removed, room=(), back=back),
        )
        return SelectionEdit(response, use, item_id, True, True, None, MAX_PAGES)

    if use == "remove":
        line = _find(replace(result, length=None), item_id)
        if line is None or line.id is None:
            raise TailorError("selection_line_not_shown", f"this resume does not show master line {item_id!r}")
        whole = _without(whole, line.id)
        return done(*_close(whole, left), added=None, removed=item_id)

    if _find(replace(result, length=None), item_id) is not None:
        return SelectionEdit(stored, use, item_id, True, False, None, MAX_PAGES)  # already shown: idempotent
    held = _find(whole, item_id)  # a line an older pick cut for length: it comes back as it was
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
    else:
        item = master.items.get(item_id)
        if item is None:
            raise TailorError("master_line_not_found", f"the master has no line {item_id!r} (it may be retired)")
        if item.kind not in (KIND_SUMMARY, KIND_BULLET, KIND_OTHER):
            raise TailorError("selection_line_unsupported", "a Skills line is not added by id: the skills shown follow the lines shown")
        maker = _Maker(whole, stored.sources.resume_line_count)
        whole = _put(whole, master, item, maker)
    # The line is shown, and nothing else moves: no page is counted, so nothing is ever cut to make room.
    return done(*_close(whole, left), added=item_id, removed=None)


# --- the stored resume -----------------------------------------------------------------------------


def change_stored_selection(
    home_root: Path, target: Path, *, profile_id: str, job_identity: str, use: str, item_id: str, fit: str = FIT_ASK,
    updated_at: str | None = None,
) -> SelectionEdit:
    """Add or Remove one master line on a stored tailored resume, and store the result. No model, no layout.

    ``updated_at`` (the API's revision check) must be the stored one, else
    ``tailored_resume_changed``.  The read, the check and the write happen
    under the store's write lock.  Raises ``TailorError``: ``invalid_value``,
    ``tailored_resume_not_found``, ``tailored_resume_changed``,
    ``selection_unavailable``, ``master_not_found``, ``master_line_not_found``,
    ``selection_line_not_shown``, ``selection_line_unsupported``.
    """

    from ..workpad import WorkpadError, resolve_workpad
    from .tailor_master import stored_master

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
        edit = edit_selection(stored, held.master, use=use, item_id=item_id, fit=fit)
        if edit.applied and edit.changed:
            save_tailor_response(edit.response, home_root=home_root)
            from .suggestions import drop_proposal_after_edit

            drop_proposal_after_edit(home_root, target, edit.response)  # 0.1.11.4 E1: it waited beside the resume as it was
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
