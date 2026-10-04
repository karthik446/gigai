"""0110-10-05 C: what a tailored resume leaves out for LENGTH, kept whole so it can be put back.

The operator's rule (2026-10-04): tailoring may cut for length, but ONLY
whole roles, the oldest first, until the resume fits ``LENGTH_RULE.max_pages``
pages; what was cut is shown, and it can be put back.  The one older length
rule stays beside it (0110-006: a role that ended more than
``LENGTH_RULE.old_role_years`` years ago keeps its first
``LENGTH_RULE.old_role_bullets`` bullets; ``apply_no_loss`` applies it) and
is now shown and put back the same way.  Nothing else is cut for length.

- ``with_trims`` records the old-role bullets ``apply_no_loss`` left out.
- ``fit_to_pages(result, measure=...)`` measures the result and, while it is
  over the limit, leaves out the oldest role of the Experience section
  (``oldest_first``), one whole role at a time, until it fits.  The newest
  role always stays.  When leaving out every role but one still does not
  fit, NO role is cut and the result says it is over the limit.  When the
  pages cannot be measured (``measure`` answers ``None``: no renderer), no
  role is cut and the result says so.
- Everything left out is kept on the result (``TailoredResume.length``, a
  ``LengthFit``): each cut role whole, with its place among the roles; each
  left-out bullet whole, with the role it belongs to.  ``restore_cut`` puts
  all of it back exactly where it was (one action); ``cut_again`` leaves the
  same things out again.  Neither needs a measurement.
- A result that fits with nothing left out carries no ``length`` at all: it
  is stored byte for byte as before.

Pure: no I/O and no model call.  ``measure`` is the caller's (the product's
is ``tailor_length_store.measure_pages``: the PDF renderer's own layout at
the tightest spacing auto fit may choose).  The
candidate source will change (the master resume); this rule only reads a
``TailoredResume`` and a page count.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace

from .find_jobs.contracts import _fail, _object_with_optional, _string
from .tailored_resume import (
    ENTRY_SECTIONS,
    LENGTH_RULE,
    LineAlternative,
    TailoredEntry,
    TailoredLine,
    TailoredResume,
    TailoredSection,
    _ONGOING,
    _YEAR,
    _display,
)

#: The section whose entries are roles.  Projects and education are never cut whole for length.
ROLE_SECTION = "experience"

#: ``LengthFit.status``.
STATUS_CUT = "cut"  # roles and/or old-role bullets are left out
STATUS_RESTORED = "restored"  # the user put everything back; ``cut`` and ``trimmed`` say what ``cut_again`` leaves out
STATUS_OVER = "over"  # over the limit, nothing is left out: leaving out older roles cannot fix it
STATUS_UNMEASURED = "unmeasured"  # the pages could not be measured; nothing is left out
LENGTH_STATUSES: tuple[str, ...] = (STATUS_CUT, STATUS_RESTORED, STATUS_OVER, STATUS_UNMEASURED)

#: The length actions a caller may ask for (``PUT /api/tailored-resumes/length``, ``gigai scout resume length``).
LENGTH_USES: tuple[str, ...] = ("restore", "cut")

_MAX_ROLE_LABEL = 160

#: ``result`` -> the pages it prints on, or ``None`` when that cannot be measured.
Measure = Callable[[TailoredResume], "int | None"]


def role_label(entry: TailoredEntry) -> str:
    """One role as a reader names it: its heading lines without markdown markers, joined."""

    label = " · ".join(_display(line.text) for line in entry.heading if line.text.strip())
    return label if len(label) <= _MAX_ROLE_LABEL else label[: _MAX_ROLE_LABEL - 1].rstrip() + "…"


def _heading_id(entry: TailoredEntry) -> str | None:
    return entry.heading[0].id if entry.heading else None


@dataclass(frozen=True)
class CutRole:
    """One role left out for length: the whole entry, and its place among the roles when none is cut."""

    position: int
    entry: TailoredEntry

    def to_json(self) -> dict[str, object]:
        return {"position": self.position, "role": role_label(self.entry), "entry": self.entry.to_json()}

    @classmethod
    def from_json(cls, obj: object) -> "CutRole":
        value = _object_with_optional(obj, ("position", "entry"), ("role",), "tailored_resume.length.cut[]")
        position = value["position"]
        if type(position) is not int or position < 0:
            _fail("invalid_value", "tailored_resume.length.cut[].position must be a non-negative integer")
        return cls(position, TailoredEntry.from_json(value["entry"]))  # type: ignore[arg-type]


@dataclass(frozen=True)
class TrimmedRole:
    """The bullets one older role leaves out (the old-role rule): whole lines, and the role they belong to.

    ``heading`` is the id of the role's first heading line; ``role`` its
    name for a reader; ``records`` are the entries the trim added to the
    role's ``dropped`` list (they go when the bullets come back).
    """

    heading: str
    role: str
    bullets: tuple[TailoredLine, ...]
    records: tuple[LineAlternative, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "heading": self.heading,
            "role": self.role,
            "bullets": [line.to_json() for line in self.bullets],
            "records": [item.to_json() for item in self.records],
        }

    @classmethod
    def from_json(cls, obj: object) -> "TrimmedRole":
        value = _object_with_optional(obj, ("heading", "role", "bullets"), ("records",), "tailored_resume.length.trimmed[]")
        records = value.get("records", [])
        if type(value["bullets"]) is not list or not value["bullets"] or type(records) is not list:
            _fail("wrong_type", "tailored_resume.length.trimmed[].bullets must be a non-empty array and records an array")
        return cls(
            _string(value["heading"], "tailored_resume.length.trimmed[].heading"),
            _string(value["role"], "tailored_resume.length.trimmed[].role", nonempty=False),
            tuple(TailoredLine.from_json(item) for item in value["bullets"]),  # type: ignore[union-attr]
            tuple(LineAlternative.from_json(item) for item in records),  # type: ignore[union-attr]
        )


def _pages(value: object, name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        _fail("invalid_value", f"tailored_resume.length.{name} must be a positive integer or null")
    return value  # type: ignore[return-value]


@dataclass(frozen=True)
class LengthFit:
    """What the length rule left out of one tailored resume (absent when it fits with nothing left out).

    ``pages`` is the resume with the cut applied, ``full_pages`` the resume
    with everything shown (each ``None`` when it could not be measured).
    ``cut`` (whole roles) and ``trimmed`` (old-role bullets) are what is left
    out while ``status`` is ``cut``, and what ``cut_again`` would leave out
    while it is ``restored``; both are empty for ``over`` and ``unmeasured``.
    """

    max_pages: int
    pages: int | None
    full_pages: int | None
    status: str
    cut: tuple[CutRole, ...] = ()
    trimmed: tuple[TrimmedRole, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "max_pages": self.max_pages,
            "pages": self.pages,
            "full_pages": self.full_pages,
            "status": self.status,
            "cut": [role.to_json() for role in self.cut],
            "trimmed": [role.to_json() for role in self.trimmed],
        }

    @classmethod
    def from_json(cls, obj: object) -> "LengthFit":
        value = _object_with_optional(obj, ("max_pages", "pages", "full_pages", "status", "cut"), ("trimmed",), "tailored_resume.length")
        max_pages = value["max_pages"]
        if type(max_pages) is not int or max_pages < 1:
            _fail("invalid_value", "tailored_resume.length.max_pages must be a positive integer")
        status = value["status"]
        if status not in LENGTH_STATUSES:
            _fail("bad_enum", f"tailored_resume.length.status must be one of {', '.join(LENGTH_STATUSES)}")
        trimmed = value.get("trimmed", [])
        if type(value["cut"]) is not list or type(trimmed) is not list:
            _fail("wrong_type", "tailored_resume.length.cut and trimmed must be arrays")
        length = cls(
            max_pages,  # type: ignore[arg-type]
            _pages(value["pages"], "pages"),
            _pages(value["full_pages"], "full_pages"),
            status,  # type: ignore[arg-type]
            tuple(CutRole.from_json(item) for item in value["cut"]),  # type: ignore[union-attr]
            tuple(TrimmedRole.from_json(item) for item in trimmed),  # type: ignore[union-attr]
        )
        if (status in (STATUS_CUT, STATUS_RESTORED)) != length.leaves_out():
            _fail("invalid_value", "tailored_resume.length holds cut roles or trimmed bullets exactly when status is cut or restored")
        return length

    def leaves_out(self) -> bool:
        return bool(self.cut or self.trimmed)

    def roles(self) -> tuple[str, ...]:
        """The cut roles as a reader names them, in resume order."""

        return tuple(role_label(role.entry) for role in self.cut)

    def trimmed_count(self) -> int:
        return sum(len(role.bullets) for role in self.trimmed)

    def shown_pages(self) -> int | None:
        """The pages of the resume as it is now: with everything shown once restored, else with the cut applied."""

        return self.full_pages if self.status == STATUS_RESTORED else self.pages

    def over(self) -> bool:
        pages = self.shown_pages()
        return pages is not None and pages > self.max_pages


# --- which role is the oldest ---------------------------------------------------------------


def _years(entry: TailoredEntry) -> tuple[int, int] | None:
    """``(end year, start year)`` from the role's heading; an ongoing role ends in 9999; ``None`` when it names no year."""

    text = " ".join(line.text for line in entry.heading)
    years = [int(year) for year in _YEAR.findall(text)]
    if _ONGOING.search(text):
        return 9999, min(years) if years else 9999
    if not years:
        return None
    return max(years), min(years)


def oldest_first(entries: Sequence[TailoredEntry]) -> list[int]:
    """The roles' indexes, oldest first.

    By the years their headings name: the role that ended earliest first,
    then the one that started earliest, then the one lower on the page.  When
    any role names no year the order is the page's alone, from the bottom up
    (a resume lists its roles newest first).
    """

    spans = [_years(entry) for entry in entries]
    if all(span is not None for span in spans):
        return sorted(range(len(entries)), key=lambda index: (*spans[index], -index))  # type: ignore[misc]
    return list(range(len(entries) - 1, -1, -1))


# --- putting back and leaving out -----------------------------------------------------------


def _map_entries(result: TailoredResume, change: Callable[[TailoredSection, tuple[TailoredEntry, ...]], Iterable[TailoredEntry]]) -> TailoredResume:
    sections = tuple(
        replace(section, entries=tuple(change(section, section.entries))) if section.heading in ENTRY_SECTIONS else section
        for section in result.sections
    )
    return replace(result, sections=sections)


def shown_whole(result: TailoredResume) -> TailoredResume:
    """``result`` with every cut role and every trimmed bullet back in place, and no ``length``."""

    length = result.length
    if length is None:
        return result
    if length.status != STATUS_CUT:
        return replace(result, length=None)
    whole = result
    if length.cut:
        def roles_back(section: TailoredSection, entries: tuple[TailoredEntry, ...]) -> list[TailoredEntry]:
            out = list(entries)
            if section.heading == ROLE_SECTION:
                for role in sorted(length.cut, key=lambda item: item.position):
                    out.insert(min(role.position, len(out)), role.entry)
            return out

        if not any(section.heading == ROLE_SECTION for section in whole.sections):  # one role always stays; kept total
            whole = replace(whole, sections=(*whole.sections, TailoredSection(ROLE_SECTION)))
        whole = _map_entries(whole, roles_back)
    trims = {role.heading: role for role in length.trimmed}

    def bullets_back(_section: TailoredSection, entries: tuple[TailoredEntry, ...]) -> list[TailoredEntry]:
        out = []
        for entry in entries:
            role = trims.get(_heading_id(entry) or "")
            if role is not None:
                dropped = tuple(item for item in entry.dropped if item not in role.records)
                entry = replace(entry, bullets=(*entry.bullets, *role.bullets), dropped=dropped)
            out.append(entry)
        return out

    return replace(_map_entries(whole, bullets_back) if trims else whole, length=None)


def _left_out(
    whole: TailoredResume, cut_ids: Sequence[str | None], trimmed: Sequence[TrimmedRole]
) -> tuple[TailoredResume, tuple[CutRole, ...], tuple[TrimmedRole, ...]]:
    """``whole`` with ``trimmed``'s bullets and the roles ``cut_ids`` name left out, and both records as they now are.

    The records are rebuilt from ``whole``: a line the user edited while the
    resume was restored is kept as edited.
    """

    wanted = {role.heading: role for role in trimmed}
    applied: list[TrimmedRole] = []

    def trim(_section: TailoredSection, entries: tuple[TailoredEntry, ...]) -> list[TailoredEntry]:
        out = []
        for entry in entries:
            role = wanted.get(_heading_id(entry) or "")
            ids = {line.id for line in role.bullets} if role is not None else set()
            gone = tuple(line for line in entry.bullets if line.id in ids)
            if role is not None and gone:
                applied.append(replace(role, bullets=gone))
                entry = replace(entry, bullets=tuple(line for line in entry.bullets if line.id not in ids), dropped=(*entry.dropped, *role.records))
            out.append(entry)
        return out

    result = _map_entries(whole, trim) if wanted else whole
    ids = {item for item in cut_ids if item is not None}
    cut: list[CutRole] = []

    def without_roles(section: TailoredSection, entries: tuple[TailoredEntry, ...]) -> list[TailoredEntry]:
        if section.heading != ROLE_SECTION:
            return list(entries)
        for position, entry in enumerate(entries):
            if _heading_id(entry) in ids:
                cut.append(CutRole(position, entry))
        return [entry for entry in entries if _heading_id(entry) not in ids]

    if ids:
        result = _map_entries(result, without_roles)
    return result, tuple(cut), tuple(applied)


def with_trims(
    result: TailoredResume, trims: Sequence[tuple[int, int, Sequence[TailoredLine], Sequence[LineAlternative]]]
) -> TailoredResume:
    """A settled result with its old-role trims on record (``apply_no_loss`` calls this; ids are already assigned).

    ``trims``: ``(section index, entry index, the bullets left out, the dropped records the trim added)``.
    The left-out bullets get the next free line ids, so a bullet put back can be chosen and edited like any other.
    """

    numbers = [
        int(line.id[1:])
        for section in result.sections
        for line in section.all_lines()
        if line.id is not None and line.id[:1] == "L" and line.id[1:].isdigit()
    ]
    counter = max(numbers, default=0)
    trimmed: list[TrimmedRole] = []
    for section_index, entry_index, lines, records in trims:
        entry = result.sections[section_index].entries[entry_index]
        heading = _heading_id(entry)
        if heading is None or not lines:
            continue
        bullets = []
        for line in lines:
            counter += 1
            bullets.append(replace(line, id=f"L{counter}"))
        trimmed.append(TrimmedRole(heading, role_label(entry), tuple(bullets), tuple(records)))
    if not trimmed:
        return result
    return replace(result, length=LengthFit(LENGTH_RULE.max_pages, None, None, STATUS_CUT, (), tuple(trimmed)))


def fit_to_pages(result: TailoredResume, *, measure: Measure, max_pages: int = LENGTH_RULE.max_pages) -> TailoredResume:
    """A settled result cut to ``max_pages`` by leaving out whole roles, oldest first (see the module text).

    The old-role trims on record (``with_trims``) stay applied; roles a
    previous fit cut are put back first, so the cut is always decided on the
    whole resume.
    """

    record = result.length
    whole = shown_whole(result)
    if record is not None and record.status == STATUS_CUT and not record.cut:
        base, trimmed = replace(result, length=None), record.trimmed  # as ``apply_no_loss`` settled it
    else:
        base, _cut, trimmed = _left_out(whole, (), record.trimmed if record is not None else ())

    def marked(shown: TailoredResume, pages: int | None, full_pages: int | None, cut: tuple[CutRole, ...] = ()) -> TailoredResume:
        if cut or trimmed:
            return replace(shown, length=LengthFit(max_pages, pages, full_pages, STATUS_CUT, cut, trimmed))
        if pages is None:
            return replace(shown, length=LengthFit(max_pages, None, None, STATUS_UNMEASURED))
        if pages > max_pages:
            return replace(shown, length=LengthFit(max_pages, pages, pages, STATUS_OVER))
        return shown

    pages = measure(base)
    if pages is None:
        return marked(base, None, None)
    full_pages = measure(whole) if trimmed else pages
    if pages <= max_pages:
        return marked(base, pages, full_pages)
    roles = next((section.entries for section in base.sections if section.heading == ROLE_SECTION), ())
    cut_ids: list[str | None] = []
    for index in oldest_first(roles)[:-1]:  # the newest role always stays
        if _heading_id(roles[index]) is None:
            break  # a role with no line id cannot be named for the way back: no role is cut
        cut_ids.append(_heading_id(roles[index]))
        candidate, cut, _trimmed = _left_out(base, cut_ids, ())
        now = measure(candidate)
        if now is None:
            break
        if now <= max_pages:
            return marked(candidate, now, full_pages, cut)
    return marked(base, pages, full_pages)


def _merged_trims(new: Sequence[TrimmedRole], old: Sequence[TrimmedRole], order: Sequence[str | None]) -> tuple[TrimmedRole, ...]:
    """One record per role, in the page's order (``order``: the roles' heading ids as the resume lists them).

    A role's bullets: the ones ``new`` leaves out (earlier in its list), then those ``old`` had already left out. The
    page's order is the order ``cut_again`` rebuilds the record in, so a Restore and a cut again give the same record."""

    by_heading = {role.heading: role for role in new}
    merged = [
        replace(role, bullets=(*by_heading.pop(role.heading).bullets, *role.bullets)) if role.heading in by_heading else role
        for role in old
    ]
    merged += [role for role in new if role.heading in by_heading]
    place = {heading: index for index, heading in enumerate(order)}
    return tuple(sorted(merged, key=lambda role: place.get(role.heading, len(place))))


#: How many cut lines ``fit_by_cuts`` tries to put back into the room the last cut left.
REFILL_TRIES = 12


def fit_by_cuts(
    result: TailoredResume, cuts: Sequence[tuple[str, str]], *, measure: Measure, max_pages: int = LENGTH_RULE.max_pages,
    refill: Iterable[str] = (),
) -> TailoredResume:
    """A freshly settled result cut to ``max_pages`` by the FEWEST of ``cuts``, taken in the order given.

    The master resume's fit (0.1.10.9 master P4, ``tailor_master``): the
    caller decides the order (the oldest role's bullets, that role, the next
    oldest, then recent roles' lowest-value lines); this applies it and
    keeps the record ``fit_to_pages`` keeps.  A cut is ``("bullet", <line
    id>)`` or ``("role", <the id of the role's first heading line>)``.  A
    cut role goes to ``LengthFit.cut`` whole; a cut bullet of a role that
    stays goes to ``LengthFit.trimmed`` beside the old-role bullets
    ``apply_no_loss`` left out, so ``restore_cut`` puts everything back and
    ``cut_again`` leaves the same things out.  When every cut is applied and
    the result is still over the limit they all stay applied and the record
    says so (``LengthFit.over``).  A result that fits, a result with no cut
    to make and an unmeasurable one are marked exactly as ``fit_to_pages``
    marks them.

    ``refill``: the line ids among the bullet cuts that may come back when
    room is left.  A cut frees a whole line or more, so the fewest cuts that
    fit can leave room: each such line, from the last one cut back
    (``REFILL_TRIES`` at most), is put back when the result still fits.
    """

    record = result.length
    if record is not None and (record.status != STATUS_CUT or record.cut):
        raise ValueError("fit_by_cuts takes a result as apply_no_loss settled it")
    base = replace(result, length=None)
    old_trims = record.trimmed if record is not None else ()
    order = [_heading_id(entry) for section in base.sections for entry in section.entries]

    def marked(shown: TailoredResume, pages: int | None, full_pages: int | None, cut: tuple[CutRole, ...] = (), new: Sequence[TrimmedRole] = ()) -> TailoredResume:
        trimmed = _merged_trims(new, old_trims, order)
        if cut or trimmed:
            return replace(shown, length=LengthFit(max_pages, pages, full_pages, STATUS_CUT, cut, trimmed))
        if pages is None:
            return replace(shown, length=LengthFit(max_pages, None, None, STATUS_UNMEASURED))
        if pages > max_pages:
            return replace(shown, length=LengthFit(max_pages, pages, pages, STATUS_OVER))
        return shown

    def applied(chosen: Sequence[tuple[str, str]]) -> tuple[TailoredResume, tuple[CutRole, ...], tuple[TrimmedRole, ...]]:
        roles = [target for kind, target in chosen if kind == "role"]
        gone = {target for kind, target in chosen if kind == "bullet"}
        wanted: list[TrimmedRole] = []
        for section in base.sections:
            for entry in section.entries:
                heading = _heading_id(entry)
                if heading is None or heading in roles:
                    continue  # a role cut whole carries its bullets with it
                lines = tuple(line for line in entry.bullets if line.id in gone)
                if lines:
                    wanted.append(TrimmedRole(heading, role_label(entry), lines))
        return _left_out(base, roles, wanted)

    pages = measure(base)
    if pages is None:
        return marked(base, None, None)
    full_pages = measure(shown_whole(result)) if old_trims else pages
    if pages <= max_pages or not cuts:
        return marked(base, pages, full_pages)
    measured: dict[int, int | None] = {}

    def pages_after(count: int) -> int | None:
        if count not in measured:
            measured[count] = measure(applied(cuts[:count])[0])
        return measured[count]

    count = len(cuts)
    fits = (pages_after(count) or max_pages + 1) <= max_pages
    if fits:
        low, high = 1, count  # the fewest cuts that fit: every cut removes lines, so the pages never grow with the count
        while low < high:
            middle = (low + high) // 2
            if (pages_after(middle) or max_pages + 1) <= max_pages:
                high = middle
            else:
                low = middle + 1
        count = low
    now = pages_after(count)
    if now is None:
        return marked(base, pages, full_pages)  # the renderer stopped answering: nothing is cut on a guess
    chosen = list(cuts[:count])
    if fits:
        allowed = set(refill)
        tries = 0
        for index in range(count - 1, -1, -1):
            kind, target = cuts[index]
            if kind != "bullet" or target not in allowed:
                continue
            if tries == REFILL_TRIES:
                break
            tries += 1
            trial = [item for item in chosen if item != (kind, target)]
            back = measure(applied(trial)[0])
            if back is not None and back <= max_pages:
                chosen, now = trial, back
    shown, cut, new = applied(chosen)
    return marked(shown, now, full_pages, cut, new)


def restore_cut(result: TailoredResume) -> TailoredResume:
    """``result`` with everything left out for length back in place, marked ``restored``.

    One action for the cut roles and the trimmed old-role bullets.  A result
    with nothing left out is returned unchanged.  The record stays, so
    ``cut_again`` can leave the same things out again.
    """

    length = result.length
    if length is None or length.status != STATUS_CUT:
        return result
    return replace(shown_whole(result), length=replace(length, status=STATUS_RESTORED))


def cut_again(result: TailoredResume) -> TailoredResume:
    """A ``restored`` result with the same roles and bullets left out again; anything else is returned unchanged."""

    length = result.length
    if length is None or length.status != STATUS_RESTORED:
        return result
    shown, cut, trimmed = _left_out(replace(result, length=None), [_heading_id(role.entry) for role in length.cut], length.trimmed)
    if not cut and not trimmed:  # nothing of the record is in the resume any more
        return result
    return replace(shown, length=replace(length, status=STATUS_CUT, cut=cut, trimmed=trimmed))


def apply_length_use(result: TailoredResume, use: str) -> TailoredResume:
    """One length action on a stored result: ``restore`` what was left out, or ``cut`` it again."""

    if use == "restore":
        return restore_cut(result)
    if use == "cut":
        return cut_again(result)
    raise ValueError("use must be restore or cut")


def length_note(length: LengthFit | None) -> str:
    """What the length rule did, in one line (the CLI's; the UI words its own from the same fields); ``""`` when nothing."""

    if length is None:
        return ""
    limit = f"{length.max_pages}-page limit"
    pages = length.shown_pages()
    if length.status == STATUS_UNMEASURED:
        return f"Length not checked: the pages could not be measured, so no role was cut (the {limit} applies)."
    if length.status == STATUS_OVER:
        return f"Length: {pages} pages, over the {limit}; leaving out older roles would not fix it, so no role was cut."
    parts = list(length.roles())
    if length.trimmed:
        count = length.trimmed_count()
        bullets = ", ".join(f"{len(role.bullets)} of {role.role}" for role in length.trimmed)
        parts.append(f"{count} older bullet{'' if count == 1 else 's'} ({bullets})")
    what = "; ".join(parts)
    if length.status == STATUS_RESTORED:
        size = f" The resume is {pages} pages, over the {limit}." if length.over() else ""
        return f"Put back (was cut for length): {what}.{size}"
    was = f" ({length.full_pages} pages -> {length.pages})" if length.full_pages and length.pages and length.full_pages != length.pages else ""
    tail = ""
    if pages is None:
        tail = f" The pages could not be measured, so no role was cut (the {limit} applies)."
    elif length.over():
        tail = f" Still {pages} pages, over the {limit}: leaving out older roles would not fix it."
    return f"Cut for length{was}: {what}.{tail}"


__all__ = [
    "LENGTH_STATUSES",
    "LENGTH_USES",
    "REFILL_TRIES",
    "ROLE_SECTION",
    "STATUS_CUT",
    "STATUS_OVER",
    "STATUS_RESTORED",
    "STATUS_UNMEASURED",
    "CutRole",
    "LengthFit",
    "Measure",
    "TrimmedRole",
    "apply_length_use",
    "cut_again",
    "fit_by_cuts",
    "fit_to_pages",
    "length_note",
    "oldest_first",
    "restore_cut",
    "role_label",
    "shown_whole",
    "with_trims",
]
