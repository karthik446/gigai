"""The pick report (0110-10-15): the selector's final selection for the newest assessed postings of ANY home, checked.

READ-ONLY.  For the newest ``--limit`` assessed postings of each profile of ``--home`` it makes the selection the
code makes today (``tools/pick_probe.py``: ``select`` and the ``fallback`` tailoring; ``--path`` names others) and
prints, per posting, COUNTS AND CHECKS ONLY:

- the lines picked and left out, by role id; how many roles and projects are shown with NO line; and the roles and
  projects the selector says the posting's TITLE names (``sel-4`` on), each with its lines shown of those it holds;
- the stored assessment's requirement rows whose cited evidence is in the master, each with the master line ids it
  cites and which of them the final selection shows.  A row is one of THREE things, never merged:
  ``in`` (a line it cites is shown); ``cited line cut, supported by <ids>`` (no line it cites is shown, but a shown
  line supports the same requirement by the selector's own word rule, ``master_selection.word_supporters``: the line
  is named; a master that gained lines after the assessment was made gives rows of this kind); ``REAL LOSS`` (no
  line it cites is shown and no shown line supports it);
- the skill names kept of those the master lists, the pages, the conflicts the result reports.

THE SELECTION IS MADE AS THE PRODUCT MAKES IT: each posting is passed with the rows its stored assessment cites
(``assess_master.cited_requirements``, the product's one mapper of an evidence quote to master lines), so a selector
that reads citations (``sel-3`` on) selects from them, as a tailoring of that job does.  ``--no-citations`` selects
by words alone (what a job with no assessment gets).

With ``--baseline TREE`` (the root of an older checkout or of a ``git archive`` of one) the same is computed by that
tree's selector on the same inputs and printed beside it, with one verdict per check: ``better``, ``same`` or
``WORSE``.  The checks are separate and never added up.  ``mandatory coverage`` is judged on REAL LOSSES: a
mandatory row that the old selection covered (a cited line, or another supporting line) and the new one leaves with
nothing is ``WORSE`` whatever else improved, and the exit code is then 1.  ``cited line kept`` is the stricter
check beside it (a cited line shown before and none now, whatever else supports the row).  ``title entry kept``
(0.1.10.11 PICK v5): a role or project the posting's title names showed a line before and shows none now is
``WORSE``, the reverse ``better``; it is not in the exit code.

WHAT IT PRINTS OF A HOME: ids, counts, a posting's title and company, and the assessment's own requirement text
(the posting's words).  NEVER a line of a resume, an answer or a path under the home.

WHAT IT WRITES: nothing.  No selection is stored, no profile or anchor moved, no journal commit, no model call,
no request (sockets are refused while it runs).  Every read goes through the read scope
(``workpad.committed_read_cache``).  HOW EACH FILE IS OPENED (``_read_only``):

- JSON and markdown files (the quick assessments, the workpad's records): ``open`` for reading.
- EVERY SQLite file this process opens is opened with the URI ``mode=ro`` (``_read_only_sqlite`` replaces
  ``sqlite3.connect`` for the run: a plain path becomes ``file:<path>?mode=ro``, a ``BEGIN IMMEDIATE`` becomes a
  read transaction), so SQLite itself refuses a write and never creates a journal.  In practice two are opened:
  ``registry.sqlite`` (rollback-journal mode: which project the home is bound to) and the workpad's
  ``scratch/journal-publishers.sqlite`` cache when it exists.  The last line of the output names what was opened.
- ``pipeline.sqlite`` IS NOT OPENED AT ALL (nor its ``-wal`` / ``-shm``): nothing the report reads is in it.  A
  server running on the same home goes on writing it; that is the server, not this command.
- The workpad's kept-at-head cache files are never created or refreshed (``scratch_cache_path`` answers "cannot be
  used" for a create), and the journal's once-a-process mount probe (three temp files made and removed in the
  workpad's ``scratch/``) is not run: it proves a folder can be WRITTEN safely, and nothing is written here.
- git is run with ``GIT_OPTIONAL_LOCKS=0`` (a read never refreshes the index file).

``tests/behaviors/scout_resume_gate/test_pick_report.py`` asserts a home is byte for byte what it was after a run
(every file's bytes and modification time, every folder's modification time, no file made), the baseline included,
and with a second connection holding a write transaction on the home's ``pipeline.sqlite`` during the run.

    uv run python tools/pick_report.py --home ~/.gigai --limit 50
    git archive --prefix=old/ v0.1.10.10 src | tar -x -C /tmp/pick && uv run python tools/pick_report.py --home ~/.gigai --limit 50 --baseline /tmp/pick/old
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
import os
from pathlib import Path
import socket
import sqlite3
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools import pick_probe  # noqa: E402

DEFAULT_PATHS: tuple[str, ...] = ("select", "fallback")
BETTER, SAME, WORSE = "better", "same", "WORSE"
_STRENGTH = {"backed": 3, "quantified": 2, "stated": 1}
_MAX_REQUIREMENT_CHARS = 110
IN, SUPPORTED, LOST = "in", "supported", "lost"


# --- nothing is written, nothing is requested ------------------------------------------------------


@contextmanager
def _hold_cache_writes():
    """Run the reads without the workpad's kept-at-head cache files, or any probe file, ever being written.

    A journal read after a write normally refreshes ``scratch/journal-publishers.sqlite`` and its like (caches the
    product may always delete).  This report must leave a home byte for byte as it was, so every request to CREATE
    such a file is answered "cannot be used" for the length of the run: the readers then walk the journal instead.
    The journal's mount probe (once a process it makes and removes three temp files in the workpad's ``scratch/`` to
    prove the folder takes an atomic replace and a lock) is not run either: nothing is written here, so there is
    nothing for it to protect, and it would move that folder's modification time.
    """

    from gigai import index, journal, workpad

    real = workpad.scratch_cache_path
    real_probe = journal._require_mount_probes  # noqa: SLF001 - held for the run, put back after

    def read_only(root: Path, filename: str, *, create: bool):
        return None if create else real(root, filename, create=False)

    def no_probe(_root: Path) -> None:
        return None

    modules = [module for module in (workpad, journal, index) if getattr(module, "scratch_cache_path", None) is real]
    for module in modules:
        module.scratch_cache_path = read_only  # type: ignore[attr-defined]
    journal._require_mount_probes = no_probe  # noqa: SLF001
    try:
        yield
    finally:
        for module in modules:
            module.scratch_cache_path = real  # type: ignore[attr-defined]
        journal._require_mount_probes = real_probe  # noqa: SLF001


class _ReadOnlyConnection(sqlite3.Connection):
    """A connection that only reads: a write transaction is begun as a read one (the file is ``mode=ro`` besides)."""

    def execute(self, sql: str, *args: object):  # type: ignore[override]
        if sql.strip().upper() in ("BEGIN IMMEDIATE", "BEGIN EXCLUSIVE"):
            sql = "BEGIN"
        return super().execute(sql, *args)


#: ``(file name, how it was opened)`` for every SQLite file opened while ``_read_only_sqlite`` held (names only, never a path).
OPENED: list[tuple[str, str]] = []


def _read_only_uri(database: object, uri: bool) -> str | None:
    """``database`` as a ``file:`` URI that can only be read; ``None`` for a database that is not a file (in memory)."""

    text = os.fspath(database) if isinstance(database, (str, os.PathLike)) else ""
    if not text or text == ":memory:" or "mode=memory" in text:
        return None
    if not (uri and text.startswith("file:")):
        return f"{Path(text).resolve().as_uri()}?mode=ro"
    base, _, query = text.partition("?")
    kept = [part for part in query.split("&") if part and not part.startswith("mode=")]
    return base + "?" + "&".join([*kept, "mode=ro"])


@contextmanager
def _read_only_sqlite():
    """For the length of the run, EVERY SQLite file this process opens is opened read-only (URI ``mode=ro``).

    SQLite then refuses any write on it and creates no journal; a file that does not exist is not created (the open
    fails).  The product's readers open ``registry.sqlite`` with a plain connection and ``BEGIN IMMEDIATE`` (they
    only read under it); here that connection is a read-only one and the transaction a read transaction.
    """

    real = sqlite3.connect

    def connect(database, *args: object, **kwargs: object):  # noqa: ANN001, ANN202 - sqlite3.connect's own shape
        target = _read_only_uri(database, bool(kwargs.get("uri", False)))
        if target is None:
            return real(database, *args, **kwargs)  # type: ignore[arg-type]
        OPENED.append((Path(target.partition("?")[0]).name, "mode=ro"))
        kwargs["uri"] = True
        kwargs.setdefault("factory", _ReadOnlyConnection)
        return real(target, *args, **kwargs)  # type: ignore[arg-type]

    sqlite3.connect = connect  # type: ignore[assignment]
    try:
        yield
    finally:
        sqlite3.connect = real  # type: ignore[assignment]


@contextmanager
def _read_only():
    """Everything that keeps a run from writing, in one place (the module text says how each file is opened)."""

    with _no_network(), _read_only_sqlite(), _hold_cache_writes():
        yield


@contextmanager
def _no_network():
    real = socket.socket

    class Refused(real):  # type: ignore[misc, valid-type]
        def connect(self, *_args: object, **_kwargs: object) -> None:
            raise OSError("pick_report makes no request")

        connect_ex = connect  # type: ignore[assignment]

    socket.socket = Refused  # type: ignore[misc]
    try:
        yield
    finally:
        socket.socket = real  # type: ignore[misc]


# --- reading a home --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    """One requirement row of a stored assessment whose cited evidence is in the master."""

    id: str  # r<n>: its place in the assessment's matrix
    requirement: str
    status: str
    mandatory: bool
    lines: tuple[str, ...]  # master line ids its evidence cites
    skills: tuple[str, ...]  # master skill names its evidence cites (only when it cites no line)
    #: Every master line that supports the requirement BY WORDS (``master_selection.word_supporters``), cited or not.
    by_words: tuple[str, ...] = ()


@dataclass(frozen=True)
class Posting:
    key: str
    title: str
    company: str
    assessed_at: str
    rows: tuple[Row, ...]
    rows_without_evidence: int
    payload: dict[str, object] = field(repr=False, default_factory=dict)


@dataclass(frozen=True)
class ProfileCase:
    profile_id: str
    label: str
    pins: tuple[str, ...]
    postings: tuple[Posting, ...]
    skipped: int


def read_home(
    home: Path, target: Path, *, limit: int, profile_id: str | None = None, citations: bool = True,
) -> tuple[object, list[ProfileCase], dict[str, object]]:
    """``(the stored master, the profiles' cases, the master's identity)``; raises ``SystemExit`` with a plain reason.

    ``citations``: each posting is passed to the selector with the rows its assessment cites (the product's way).
    """

    from gigai.scout import assess_master, profile_records, quick_assess
    from gigai.scout import master_selection as ms
    from gigai.scout import tailor_master as tm
    from gigai.scout.find_jobs.contracts import MatrixStatus, RequirementClass
    from gigai.scout.master_resume import KIND_SKILLS
    from gigai.workpad import WorkpadError, committed_read_cache, resolve_workpad

    with _hold_cache_writes(), committed_read_cache():
        try:
            resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
        except WorkpadError as exc:
            raise SystemExit(f"pick_report: this home has no Scout gig to read ({exc.code})") from None
        stored = tm.stored_master(home, target, resolved=resolved)
        if stored is None:
            raise SystemExit("pick_report: no master resume is stored in this home; there is no selection to report")
        master = stored.master
        traced = assess_master.MasterCitations(master)  # the product's one mapper of an evidence quote to master lines
        by_words: dict[str, tuple[str, ...]] = {}

        def word_supporters(requirement: str) -> tuple[str, ...]:
            if requirement not in by_words:
                by_words[requirement] = ms.word_supporters(master, requirement)
            return by_words[requirement]

        cases: list[ProfileCase] = []
        for profile in profile_records.list_profiles(resolved):
            if profile.state == "deleted" or (profile_id is not None and profile.profile_id != profile_id):
                continue
            selection = profile.master_selection
            detached = tm.detached(home, profile)
            prior = {
                "titles": list(profile.titles), "profile_id": profile.profile_id, "label": profile.label,
                "base_ids": list(selection.item_ids) if selection is not None and not detached else None,
                "pins": list(selection.pins) if selection is not None else [],
            }
            postings: list[Posting] = []
            skipped = 0
            for assessment in quick_assess.list_quick_assessments(home, target, profile_id=profile.profile_id):
                if len(postings) == limit:
                    break
                if not assessment.posting_text:
                    skipped += 1  # pasted job text is never stored: there is nothing to select against
                    continue
                rows: list[Row] = []
                bare = 0
                for place, row in enumerate(assessment.result.matrix, 1):
                    cited_lines, cited_skills = traced.row(row.resume_evidence)
                    if not cited_lines and not cited_skills:
                        bare += 1
                        continue
                    rows.append(Row(
                        f"r{place}", row.requirement, str(row.status.value if isinstance(row.status, MatrixStatus) else row.status),
                        row.requirement_class != RequirementClass.NICE_TO_HAVE, cited_lines, cited_skills, word_supporters(row.requirement),
                    ))
                key = f"{profile.profile_id}|{len(postings) + 1}"
                # The same rows, as the product passes them to its selector (ids ``r<n>`` are the same places).
                cited = [row.to_json() for row in assess_master.cited_requirements(master, assessment.result.matrix, citations=traced)] if citations else []
                postings.append(Posting(
                    key, assessment.job.title, assessment.job.company, assessment.updated_at or assessment.created_at, tuple(rows), bare,
                    {"key": key, "profile": prior, "posting": {
                        "title": assessment.job.title, "text": assessment.posting_text, "company": assessment.job.company, "location": assessment.job.location,
                        "cited": cited,
                    }},
                ))
            cases.append(ProfileCase(profile.profile_id, profile.label, tuple(prior["pins"]), tuple(postings), skipped))  # type: ignore[arg-type]
        identity = {
            "revision": stored.revision.revision, "lines": sum(item.kind != KIND_SKILLS for item in master.items.values()),
            "entries": len(master.entries), "skills": len(master.skills()),
        }
        return stored, cases, identity


# --- the checks of one final selection -------------------------------------------------------------


@dataclass(frozen=True)
class Checked:
    """One final selection against one posting's assessment rows (see the module text).

    Every row is in exactly one of ``covered`` (a line it cites is shown; a skill-only row: the skill is),
    ``supported`` (no line it cites is shown, but a shown line supports the requirement by the selector's word
    rule: row id -> those lines) and ``lost`` (neither: a REAL LOSS)."""

    error: str | None
    covered: tuple[str, ...] = ()
    supported: dict[str, tuple[str, ...]] = field(default_factory=dict)
    lost: tuple[str, ...] = ()
    mandatory: frozenset[str] = frozenset()
    strength: dict[str, int] = field(default_factory=dict)
    shown_per_row: dict[str, tuple[str, ...]] = field(default_factory=dict)
    pins_missing: tuple[str, ...] = ()
    pages: int | None = None
    max_pages: int = 2
    empty: tuple[str, ...] = ()
    date_order: bool = True
    picked: int = 0
    left_out: int = 0
    by_entry: dict[str, tuple[int, int]] = field(default_factory=dict)
    skills: int = 0
    skills_total: int = 0
    conflicts: tuple[dict[str, object], ...] = ()
    #: The roles and projects the posting's title names (by the NEW selector's reading, for both sides).
    titled: tuple[str, ...] = ()

    @property
    def zero_entries(self) -> tuple[str, ...]:
        """The roles and projects that hold lines and show none."""

        return tuple(entry_id for entry_id, (shown, held) in self.by_entry.items() if held and not shown)

    @property
    def titled_dropped(self) -> tuple[str, ...]:
        return tuple(entry_id for entry_id in self.titled if entry_id in self.zero_entries)

    @property
    def fits(self) -> bool:
        return self.error is None and self.pages is not None and self.pages <= self.max_pages and not self.empty and self.date_order

    def state(self, row: str) -> str:
        return IN if row in self.covered else (SUPPORTED if row in self.supported else LOST)

    def count(self, state: str, *, mandatory: bool | None = None) -> int:
        rows = self.covered if state == IN else (tuple(self.supported) if state == SUPPORTED else self.lost)
        return sum(1 for row in rows if mandatory is None or (row in self.mandatory) == mandatory)


def check(master, posting: Posting, pins: tuple[str, ...], final: dict[str, object], titled: tuple[str, ...] = ()) -> Checked:
    """``titled``: the roles and projects the posting's title names (the new selector's ``title_entries``)."""

    from gigai.scout.master_resume import KIND_SKILLS

    mandatory = frozenset(row.id for row in posting.rows if row.mandatory)
    if "error" in final:
        return Checked(error=str(final["error"]), lost=tuple(row.id for row in posting.rows), mandatory=mandatory, titled=titled)
    entries: dict[str, list[str]] = final["entries"]  # type: ignore[assignment]
    shown = {*final["summary"], *(bullet for bullets in entries.values() for bullet in bullets), *final["other"]}  # type: ignore[misc]
    skills = {str(name).casefold() for name in final["skills"]}  # type: ignore[union-attr]
    covered: list[str] = []
    supported: dict[str, tuple[str, ...]] = {}
    lost: list[str] = []
    strength: dict[str, int] = {}
    per_row: dict[str, tuple[str, ...]] = {}
    # A line that a better line repeats is left out and that line shown in its place: it stands for the cited one.
    twin: dict[str, str] = final.get("duplicates") or {}  # type: ignore[assignment]
    for row in posting.rows:
        kept = tuple(dict.fromkeys(found for item_id in row.lines if (found := item_id if item_id in shown else twin.get(item_id, "")) in shown))
        per_row[row.id] = kept
        if kept or (not row.lines and any(name.casefold() in skills for name in row.skills)):
            covered.append(row.id)
            strength[row.id] = max((_STRENGTH[master.items[item_id].strength] for item_id in kept), default=1)
            continue
        strength[row.id] = 0
        # No line it cites is shown. Does another shown line support the same requirement, by the selector's word rule?
        others = tuple(item_id for item_id in row.by_words if item_id in shown and item_id not in row.lines)
        if others:
            supported[row.id] = others
        else:
            lost.append(row.id)
    by_entry = {
        entry.id: (len(entries.get(entry.id, ())), len(entry.bullets))
        for entry in master.entries.values() if entry.section in ("experience", "projects")
    }
    total = sum(item.kind != KIND_SKILLS for item in master.items.values())
    return Checked(
        error=None, covered=tuple(covered), supported=supported, lost=tuple(lost), mandatory=mandatory,
        strength=strength, shown_per_row=per_row, pins_missing=tuple(item_id for item_id in pins if item_id in master.items and item_id not in shown),
        pages=final["pages"], max_pages=int(final.get("max_pages") or 2), empty=tuple(final["empty_entries"]), date_order=bool(final["date_order"]),  # type: ignore[arg-type]
        picked=len(shown), left_out=total - len(shown), by_entry=by_entry, skills=len(final["skills"]), skills_total=len(master.skills()),  # type: ignore[arg-type]
        conflicts=tuple(final.get("conflicts") or ()),  # type: ignore[arg-type]
        titled=tuple(entry_id for entry_id in titled if entry_id in by_entry),
    )


CHECKS: tuple[str, ...] = (
    "mandatory coverage", "cited line kept", "other coverage", "evidence strength", "must-keep", "page fit", "skills kept", "title entry kept", "overall",
)


def verdicts(posting: Posting, old: Checked, new: Checked) -> dict[str, str]:
    """One verdict per check for ``new`` against ``old``.

    ``mandatory coverage`` is judged on REAL LOSSES: a mandatory row is covered when a line it cites is shown or
    another shown line supports it, and it is ``WORSE`` only when a row covered before has nothing now (it is
    ``WORSE`` whatever else improved).  ``cited line kept`` is the stricter check beside it: a mandatory row whose
    cited line was shown before and is not now, whatever else supports it.  ``title entry kept``: a role or project
    the posting's title names that showed a line before and shows none now is ``WORSE``, the reverse ``better``.
    """

    mandatory = {row.id for row in posting.rows if row.mandatory}
    rows = [row.id for row in posting.rows]
    had = [row for row in rows if old.state(row) != LOST]
    has = [row for row in rows if new.state(row) != LOST]
    lost = [row for row in had if row not in has]
    gained = [row for row in has if row not in had]
    out: dict[str, str] = {}
    out["mandatory coverage"] = WORSE if any(row in mandatory for row in lost) else (BETTER if any(row in mandatory for row in gained) else SAME)
    uncited = [row for row in old.covered if row not in new.covered and row in mandatory]
    recited = [row for row in new.covered if row not in old.covered and row in mandatory]
    out["cited line kept"] = WORSE if uncited else (BETTER if recited else SAME)
    out["other coverage"] = WORSE if any(row not in mandatory for row in lost) else (BETTER if any(row not in mandatory for row in gained) else SAME)
    kept = [row for row in old.covered if row in new.covered]
    weaker = [row for row in kept if new.strength.get(row, 0) < old.strength.get(row, 0)]
    stronger = [row for row in kept if new.strength.get(row, 0) > old.strength.get(row, 0)]
    out["evidence strength"] = WORSE if weaker else (BETTER if stronger else SAME)
    out["must-keep"] = WORSE if set(new.pins_missing) - set(old.pins_missing) else (BETTER if set(old.pins_missing) - set(new.pins_missing) else SAME)
    out["page fit"] = WORSE if old.fits and not new.fits else (BETTER if new.fits and not old.fits else SAME)
    out["skills kept"] = WORSE if new.skills < old.skills else (BETTER if new.skills > old.skills else SAME)
    gone = set(new.titled_dropped) - set(old.titled_dropped)
    back = set(old.titled_dropped) - set(new.titled_dropped)
    out["title entry kept"] = WORSE if gone else (BETTER if back else SAME)
    out["overall"] = WORSE if WORSE in (out["mandatory coverage"], out["page fit"], out["must-keep"]) else (
        WORSE if WORSE in out.values() else (BETTER if BETTER in out.values() else SAME)
    )
    return out


# --- printing --------------------------------------------------------------------------------------


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _MAX_REQUIREMENT_CHARS else flat[: _MAX_REQUIREMENT_CHARS - 1].rstrip() + "…"


def _line(name: str, checked: Checked) -> str:
    if checked.error:
        return f"    {name}: the selection could not be made ({checked.error})"
    roles = ", ".join(f"{entry_id} {shown}/{held}" for entry_id, (shown, held) in checked.by_entry.items() if shown or held)
    fit = "fits" if checked.fits else "DOES NOT FIT" + (f" (no bullet under {', '.join(checked.empty)})" if checked.empty else "") + ("" if checked.date_order else " (roles out of date order)")
    return (
        f"    {name}: pages {checked.pages}/{checked.max_pages} {fit} | lines picked {checked.picked}, left out {checked.left_out} | "
        f"requirement rows: cited line in {checked.count(IN)}, cited line cut but another shown line supports it {checked.count(SUPPORTED)}, "
        f"REAL LOSS {checked.count(LOST)} (mandatory: in {checked.count(IN, mandatory=True)}, supported {checked.count(SUPPORTED, mandatory=True)}, "
        f"REAL LOSS {checked.count(LOST, mandatory=True)}) | "
        f"skills {checked.skills}/{checked.skills_total} | conflicts {len(checked.conflicts)}"
        + (f" | pins missing {len(checked.pins_missing)}" if checked.pins_missing else "")
        + f"\n      by role: {roles}"
        + f"\n      roles and projects with no line shown: {len(checked.zero_entries)}"
        + (" | the title names: " + ", ".join(f"{entry_id} {checked.by_entry[entry_id][0]}/{checked.by_entry[entry_id][1]}" for entry_id in checked.titled) if checked.titled else "")
    )


def _state(checked: Checked, row: Row) -> str:
    """A row's state in one final selection, as the report prints it."""

    state = checked.state(row.id)
    if state == IN:
        return "in"
    if state == SUPPORTED:
        return "cited line cut, supported by " + ", ".join(checked.supported[row.id])
    return "REAL LOSS"


def report(
    home: Path, target: Path, *, limit: int, baseline: Path | None, paths: list[str], profile_id: str | None, today: date, rows: bool,
    citations: bool = True,
) -> tuple[str, bool]:
    """``(the report's text, whether a mandatory-coverage or page regression was found)``."""

    del OPENED[:]
    with _read_only():
        stored, cases, identity = read_home(home, target, limit=limit, profile_id=profile_id, citations=citations)
        master = stored.master  # type: ignore[attr-defined]
        text = master.markdown()
        payload = {
            "today": today.isoformat(), "paths": paths,
            "cases": [{**posting.payload, "master": text} for case in cases for posting in case.postings],
        }
        new = pick_probe.probe(payload) if payload["cases"] else {"selector_version": "", "results": {}}
        old = pick_probe.run_in_tree(payload, baseline) if baseline is not None and payload["cases"] else None
    out = [
        f"Pick report: master revision {identity['revision']} ({identity['lines']} lines, {identity['entries']} entries, {identity['skills']} skill names); "
        f"selector {new['selector_version'] or 'n/a'}" + (f" against baseline {old['selector_version']}" if old is not None else "") + f"; paths {', '.join(paths)}; today {today.isoformat()}.",
        "Ids, counts and the assessments' own requirement text only. Nothing was written, no model was called, no request was made.",
        ("Each posting was selected WITH the rows its stored assessment cites (as a tailoring of that job is)." if citations
         else "Each posting was selected BY WORDS ALONE (--no-citations): what a job with no stored assessment gets."),
    ]
    regressed = False
    totals: dict[tuple[str, str], dict[str, int]] = {}
    row_totals: dict[tuple[str, str, str], dict[str, int]] = {}
    entry_totals: dict[tuple[str, str, str], dict[str, int]] = {}

    def add_rows(profile: str, path: str, side: str, checked: Checked) -> None:
        entries = entry_totals.setdefault((profile, path, side), {"postings": 0, "zero": 0, "titled postings": 0, "titled": 0, "titled dropped": 0, "titled dropped postings": 0})
        if checked.error is None:
            entries["postings"] += 1
            entries["zero"] += len(checked.zero_entries)
            entries["titled postings"] += bool(checked.titled)
            entries["titled"] += len(checked.titled)
            entries["titled dropped"] += len(checked.titled_dropped)
            entries["titled dropped postings"] += bool(checked.titled_dropped)
        tally = row_totals.setdefault((profile, path, side), {})
        for state in (IN, SUPPORTED, LOST):
            for mandatory in (True, False):
                name = f"{state}:{'mandatory' if mandatory else 'other'}"
                tally[name] = tally.get(name, 0) + checked.count(state, mandatory=mandatory)
    for case in cases:
        out.append(f"\n## Profile {case.profile_id}: {len(case.postings)} assessed postings" + (f" ({case.skipped} skipped: no stored posting text)" if case.skipped else ""))
        for place, posting in enumerate(case.postings, 1):
            out.append(f"\n  [{place}] {posting.title} at {posting.company or '?'} (assessed {posting.assessed_at[:10]}): "
                       f"{len(posting.rows)} requirement rows cite master lines, {posting.rows_without_evidence} cite none")
            for path in paths:
                # The roles and projects the posting's title names, as THIS checkout's selector reads it (ids only).
                titled = tuple(new["results"][posting.key][path].get("title_entries") or ())  # type: ignore[union-attr]
                now = check(master, posting, case.pins, new["results"][posting.key][path], titled)
                add_rows(case.profile_id, path, "new", now)
                out.append(_line(f"{path} NEW" if old is not None else path, now))
                for conflict in now.conflicts:
                    out.append(f"      conflict ({conflict.get('kind')}): {_clip(str(conflict.get('requirement') or ''))} {', '.join(conflict.get('ids') or ())}".rstrip())  # type: ignore[arg-type]
                before = check(master, posting, case.pins, old["results"][posting.key][path], titled) if old is not None else None
                if before is not None:
                    add_rows(case.profile_id, path, "old", before)
                    out.append(_line(f"{path} OLD", before))
                    found = verdicts(posting, before, now)
                    out.append("      verdict: " + " | ".join(f"{name} {value}" for name, value in found.items()))
                    tally = totals.setdefault((case.profile_id, path), {})
                    for name, value in found.items():
                        tally[f"{name}:{value}"] = tally.get(f"{name}:{value}", 0) + 1
                    regressed = regressed or found["mandatory coverage"] == WORSE or found["page fit"] == WORSE
                if rows:
                    for row in posting.rows:
                        kept = now.shown_per_row.get(row.id, ())
                        was = "" if before is None else f" (old: {_state(before, row)})"
                        cited = ", ".join(row.lines) or "skills: " + ", ".join(row.skills)
                        out.append(f"      {row.id} [{row.status}{'' if row.mandatory else ', nice to have'}] {_state(now, row)}{was}: {_clip(row.requirement)} <- {cited}" + (f" (shown: {', '.join(kept)})" if kept else ""))
    out.append("\n## Totals: requirement rows by what the final selection shows for them")
    out.append("  (in: a line the row cites is shown | supported: no cited line is shown, another shown line supports the requirement | REAL LOSS: neither)")
    for (profile, path, side), tally in sorted(row_totals.items()):
        out.append(
            f"  {profile} / {path} / {side}: mandatory rows: in {tally.get('in:mandatory', 0)}, supported {tally.get('supported:mandatory', 0)}, "
            f"REAL LOSS {tally.get('lost:mandatory', 0)} | other rows: in {tally.get('in:other', 0)}, supported {tally.get('supported:other', 0)}, REAL LOSS {tally.get('lost:other', 0)}"
        )
    out.append("\n## Totals: roles and projects shown with no line")
    out.append("  (most are roles and projects a posting has no use for; 'the title names' counts the ones this checkout's selector says the posting's title names)")
    for (profile, path, side), tally in sorted(entry_totals.items()):
        out.append(
            f"  {profile} / {path} / {side}: {tally['zero']} with no line over {tally['postings']} postings | the title names {tally['titled']} "
            f"in {tally['titled postings']} postings: {tally['titled dropped']} of them with no line, in {tally['titled dropped postings']} postings"
        )
    if old is not None:
        out.append("\n## Totals, new against old (postings per verdict)")
        for (profile, path), tally in sorted(totals.items()):
            out.append(f"  {profile} / {path}:")
            for name in CHECKS:
                out.append(f"    {name}: " + ", ".join(f"{value} {tally.get(f'{name}:{value}', 0)}" for value in (BETTER, SAME, WORSE)))
        out.append("\nRESULT: " + (
            "A REGRESSION: a posting has a mandatory row with a REAL LOSS it did not have with the baseline, or lost page fit." if regressed
            else "no posting has a new REAL LOSS on a mandatory row, and none lost page fit, against the baseline."
        ))
    opened: dict[str, int] = {}
    for name, _mode in OPENED:
        opened[name] = opened.get(name, 0) + 1
    out.append(
        "\nSQLite files opened, every one read-only (URI mode=ro): " + (", ".join(f"{name} x{count}" for name, count in sorted(opened.items())) or "none")
        + ". pipeline.sqlite " + ("WAS OPENED (read-only)." if any(name.startswith("pipeline.sqlite") for name in opened) else "was not opened.")
    )
    return "\n".join(out) + "\n", regressed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="the GigAI home to read (never written)")
    parser.add_argument("--target", type=Path, help="the Scout target folder (default: <home>/scout)")
    parser.add_argument("--limit", type=int, default=50, help="the newest N assessed postings of each profile (default 50)")
    parser.add_argument("--baseline", type=Path, help="the root of an older checkout, or of a git archive of one (it holds src/gigai): its selector is run beside this one")
    parser.add_argument("--path", action="append", choices=pick_probe.PATHS, help=f"which final selection (repeatable; default {', '.join(DEFAULT_PATHS)})")
    parser.add_argument("--profile", help="only this profile id")
    parser.add_argument("--today", type=date.fromisoformat, help="the day that decides which roles are old (default: today)")
    parser.add_argument("--no-rows", action="store_true", help="leave the per-requirement rows out (totals and one line per posting only)")
    parser.add_argument("--no-citations", action="store_true", help="select by words alone, as for a job with no stored assessment (default: with the rows each assessment cites)")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    home = args.home.expanduser().resolve()
    if not home.is_dir():
        raise SystemExit("pick_report: --home is not a folder")
    from gigai.scout.target_resolution import home_scout_target

    target = args.target.expanduser().resolve() if args.target is not None else home_scout_target(home)
    os.environ.setdefault("GIT_OPTIONAL_LOCKS", "0")  # a git read never refreshes the index file
    text, regressed = report(
        home, target, limit=args.limit, baseline=args.baseline.expanduser().resolve() if args.baseline is not None else None,
        paths=args.path or list(DEFAULT_PATHS), profile_id=args.profile, today=args.today or date.today(), rows=not args.no_rows,
        citations=not args.no_citations,
    )
    sys.stdout.write(text)
    return 1 if regressed else 0


if __name__ == "__main__":
    raise SystemExit(main())
