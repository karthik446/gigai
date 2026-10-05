"""The pick report (0110-10-15): the selector's final selection for the newest assessed postings of ANY home, checked.

READ-ONLY.  For the newest ``--limit`` assessed postings of each profile of ``--home`` it makes the selection the
code makes today (``tools/pick_probe.py``: ``select`` and the ``fallback`` tailoring; ``--path`` names others) and
prints, per posting, COUNTS AND CHECKS ONLY:

- the lines picked and left out, by role id;
- the stored assessment's requirement rows whose cited evidence is in the master, each with the master line ids it
  cites and which of them the final selection shows (``in`` / ``NOT IN``);
- the skill names kept of those the master lists, the pages, the conflicts the result reports.

With ``--baseline TREE`` (the root of an older checkout or of a ``git archive`` of one) the same is computed by that
tree's selector on the same inputs and printed beside it, with one verdict per check: ``better``, ``same`` or
``WORSE``.  The checks are separate and never added up; a requirement row that the old selection covered and the new
one does not is ``WORSE`` whatever else improved, and the exit code is then 1.

WHAT IT PRINTS OF A HOME: ids, counts, a posting's title and company, and the assessment's own requirement text
(the posting's words).  NEVER a line of a resume, an answer or a path under the home.

WHAT IT WRITES: nothing.  No selection is stored, no profile or anchor moved, no journal commit, no model call,
no request (sockets are refused while it runs).  Every read goes through the read scope
(``workpad.committed_read_cache``); the workpad's own kept-at-head cache files are read and never written here
(``_hold_cache_writes``).  ``tests/behaviors/scout_resume_gate/test_pick_report.py`` asserts a home is byte for
byte what it was after a run, the baseline included.

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
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools import pick_probe  # noqa: E402

DEFAULT_PATHS: tuple[str, ...] = ("select", "fallback")
BETTER, SAME, WORSE = "better", "same", "WORSE"
_STRENGTH = {"backed": 3, "quantified": 2, "stated": 1}
_MAX_REQUIREMENT_CHARS = 110
#: A piece of an evidence quote names a line word for word only when it is at least this long (a short piece is in many lines).
_MIN_QUOTE_CHARS = 16


# --- nothing is written, nothing is requested ------------------------------------------------------


@contextmanager
def _hold_cache_writes():
    """Run the reads without the workpad's kept-at-head cache files ever being written.

    A journal read after a write normally refreshes ``scratch/journal-publishers.sqlite`` and its like (caches the
    product may always delete).  This report must leave a home byte for byte as it was, so every request to CREATE
    such a file is answered "cannot be used" for the length of the run: the readers then walk the journal instead.
    """

    from gigai import index, journal, workpad

    real = workpad.scratch_cache_path

    def read_only(root: Path, filename: str, *, create: bool):
        return None if create else real(root, filename, create=False)

    modules = [module for module in (workpad, journal, index) if getattr(module, "scratch_cache_path", None) is real]
    for module in modules:
        module.scratch_cache_path = read_only  # type: ignore[attr-defined]
    try:
        yield
    finally:
        for module in modules:
            module.scratch_cache_path = real  # type: ignore[attr-defined]


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


def _cited(master, lines, quote: str) -> tuple[set[str], set[str]]:
    """``(master line ids, skill names)`` one evidence quote cites: word for word, else as a paraphrase by shared words."""

    from gigai.scout import assess_master

    pieces = [piece for piece in assess_master._pieces(quote) if len(piece) >= _MIN_QUOTE_CHARS]  # noqa: SLF001 - the product's own rule for tracing evidence to a line
    words = assess_master._words(quote)  # noqa: SLF001
    found = {item_id for item_id, flat, _line_words in lines if any(piece in flat for piece in pieces)}
    if not found and words:
        for item_id, _flat, line_words in lines:
            shared = words & line_words
            if len(shared) >= assess_master._SHARED_WORDS and len(shared) / len(words) >= assess_master._SHARED_SHARE:  # noqa: SLF001
                found.add(item_id)
    flat_quote = assess_master._flat(quote)  # noqa: SLF001
    skills = {name for name in master.skills() if assess_master._flat(name) and assess_master._flat(name) in flat_quote} if not found else set()  # noqa: SLF001
    return found, skills


def read_home(home: Path, target: Path, *, limit: int, profile_id: str | None = None) -> tuple[object, list[ProfileCase], dict[str, object]]:
    """``(the stored master, the profiles' cases, the master's identity)``; raises ``SystemExit`` with a plain reason."""

    from gigai.scout import assess_master, profile_records, quick_assess
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
        lines = [
            (item.id, assess_master._flat(item.text), assess_master._words(item.text))  # noqa: SLF001
            for item in master.items.values() if item.kind != KIND_SKILLS
        ]
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
                    cited_lines: set[str] = set()
                    cited_skills: set[str] = set()
                    for quote in row.resume_evidence:
                        found, skills = _cited(master, lines, quote)
                        cited_lines |= found
                        cited_skills |= skills
                    if not cited_lines and not cited_skills:
                        bare += 1
                        continue
                    rows.append(Row(
                        f"r{place}", row.requirement, str(row.status.value if isinstance(row.status, MatrixStatus) else row.status),
                        row.requirement_class != RequirementClass.NICE_TO_HAVE, tuple(sorted(cited_lines)), tuple(sorted(cited_skills)) if not cited_lines else (),
                    ))
                key = f"{profile.profile_id}|{len(postings) + 1}"
                postings.append(Posting(
                    key, assessment.job.title, assessment.job.company, assessment.updated_at or assessment.created_at, tuple(rows), bare,
                    {"key": key, "profile": prior, "posting": {
                        "title": assessment.job.title, "text": assessment.posting_text, "company": assessment.job.company, "location": assessment.job.location,
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
    """One final selection against one posting's assessment rows (see the module text)."""

    error: str | None
    covered: tuple[str, ...] = ()
    not_covered: tuple[str, ...] = ()
    mandatory_not_covered: tuple[str, ...] = ()
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

    @property
    def fits(self) -> bool:
        return self.error is None and self.pages is not None and self.pages <= self.max_pages and not self.empty and self.date_order


def check(master, posting: Posting, pins: tuple[str, ...], final: dict[str, object]) -> Checked:
    from gigai.scout.master_resume import KIND_SKILLS

    if "error" in final:
        return Checked(error=str(final["error"]), not_covered=tuple(row.id for row in posting.rows), mandatory_not_covered=tuple(row.id for row in posting.rows if row.mandatory))
    entries: dict[str, list[str]] = final["entries"]  # type: ignore[assignment]
    shown = {*final["summary"], *(bullet for bullets in entries.values() for bullet in bullets), *final["other"]}  # type: ignore[misc]
    skills = {str(name).casefold() for name in final["skills"]}  # type: ignore[union-attr]
    covered: list[str] = []
    missing: list[str] = []
    strength: dict[str, int] = {}
    per_row: dict[str, tuple[str, ...]] = {}
    for row in posting.rows:
        kept = tuple(item_id for item_id in row.lines if item_id in shown)
        per_row[row.id] = kept
        if kept or (not row.lines and any(name.casefold() in skills for name in row.skills)):
            covered.append(row.id)
            strength[row.id] = max((_STRENGTH[master.items[item_id].strength] for item_id in kept), default=1)
        else:
            missing.append(row.id)
            strength[row.id] = 0
    by_entry = {
        entry.id: (len(entries.get(entry.id, ())), len(entry.bullets))
        for entry in master.entries.values() if entry.section in ("experience", "projects")
    }
    total = sum(item.kind != KIND_SKILLS for item in master.items.values())
    mandatory = {row.id for row in posting.rows if row.mandatory}
    return Checked(
        error=None, covered=tuple(covered), not_covered=tuple(missing), mandatory_not_covered=tuple(row for row in missing if row in mandatory),
        strength=strength, shown_per_row=per_row, pins_missing=tuple(item_id for item_id in pins if item_id in master.items and item_id not in shown),
        pages=final["pages"], max_pages=int(final.get("max_pages") or 2), empty=tuple(final["empty_entries"]), date_order=bool(final["date_order"]),  # type: ignore[arg-type]
        picked=len(shown), left_out=total - len(shown), by_entry=by_entry, skills=len(final["skills"]), skills_total=len(master.skills()),  # type: ignore[arg-type]
        conflicts=tuple(final.get("conflicts") or ()),  # type: ignore[arg-type]
    )


def verdicts(posting: Posting, old: Checked, new: Checked) -> dict[str, str]:
    """One verdict per check for ``new`` against ``old``. Lost mandatory coverage is ``WORSE`` whatever else improved."""

    mandatory = {row.id for row in posting.rows if row.mandatory}
    lost = [row for row in old.covered if row not in new.covered]
    gained = [row for row in new.covered if row not in old.covered]
    out: dict[str, str] = {}
    out["mandatory coverage"] = WORSE if any(row in mandatory for row in lost) else (BETTER if any(row in mandatory for row in gained) else SAME)
    out["other coverage"] = WORSE if any(row not in mandatory for row in lost) else (BETTER if any(row not in mandatory for row in gained) else SAME)
    kept = [row for row in old.covered if row in new.covered]
    weaker = [row for row in kept if new.strength.get(row, 0) < old.strength.get(row, 0)]
    stronger = [row for row in kept if new.strength.get(row, 0) > old.strength.get(row, 0)]
    out["evidence strength"] = WORSE if weaker else (BETTER if stronger else SAME)
    out["must-keep"] = WORSE if set(new.pins_missing) - set(old.pins_missing) else (BETTER if set(old.pins_missing) - set(new.pins_missing) else SAME)
    out["page fit"] = WORSE if old.fits and not new.fits else (BETTER if new.fits and not old.fits else SAME)
    out["skills kept"] = WORSE if new.skills < old.skills else (BETTER if new.skills > old.skills else SAME)
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
        f"requirement rows with evidence in {len(checked.covered)}, NOT IN {len(checked.not_covered)} (mandatory NOT IN {len(checked.mandatory_not_covered)}) | "
        f"skills {checked.skills}/{checked.skills_total} | conflicts {len(checked.conflicts)}"
        + (f" | pins missing {len(checked.pins_missing)}" if checked.pins_missing else "")
        + f"\n      by role: {roles}"
    )


def report(home: Path, target: Path, *, limit: int, baseline: Path | None, paths: list[str], profile_id: str | None, today: date, rows: bool) -> tuple[str, bool]:
    """``(the report's text, whether a mandatory-coverage or page regression was found)``."""

    with _no_network():
        stored, cases, identity = read_home(home, target, limit=limit, profile_id=profile_id)
        master = stored.master  # type: ignore[attr-defined]
        text = master.markdown()
        payload = {
            "today": today.isoformat(), "paths": paths,
            "cases": [{**posting.payload, "master": text} for case in cases for posting in case.postings],
        }
        with _hold_cache_writes():
            new = pick_probe.probe(payload) if payload["cases"] else {"selector_version": "", "results": {}}
        old = pick_probe.run_in_tree(payload, baseline) if baseline is not None and payload["cases"] else None
    out = [
        f"Pick report: master revision {identity['revision']} ({identity['lines']} lines, {identity['entries']} entries, {identity['skills']} skill names); "
        f"selector {new['selector_version'] or 'n/a'}" + (f" against baseline {old['selector_version']}" if old is not None else "") + f"; paths {', '.join(paths)}; today {today.isoformat()}.",
        "Ids, counts and the assessments' own requirement text only. Nothing was written, no model was called, no request was made.",
    ]
    regressed = False
    totals: dict[tuple[str, str], dict[str, int]] = {}
    for case in cases:
        out.append(f"\n## Profile {case.profile_id}: {len(case.postings)} assessed postings" + (f" ({case.skipped} skipped: no stored posting text)" if case.skipped else ""))
        for place, posting in enumerate(case.postings, 1):
            out.append(f"\n  [{place}] {posting.title} at {posting.company or '?'} (assessed {posting.assessed_at[:10]}): "
                       f"{len(posting.rows)} requirement rows cite master lines, {posting.rows_without_evidence} cite none")
            for path in paths:
                now = check(master, posting, case.pins, new["results"][posting.key][path])
                out.append(_line(f"{path} NEW" if old is not None else path, now))
                for conflict in now.conflicts:
                    out.append(f"      conflict ({conflict.get('kind')}): {_clip(str(conflict.get('requirement') or ''))} {', '.join(conflict.get('ids') or ())}".rstrip())  # type: ignore[arg-type]
                before = check(master, posting, case.pins, old["results"][posting.key][path]) if old is not None else None
                if before is not None:
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
                        was = "" if before is None else f" (old: {'in' if row.id in before.covered else 'NOT IN'})"
                        state = "in" if row.id in now.covered else "NOT IN"
                        cited = ", ".join(row.lines) or "skills: " + ", ".join(row.skills)
                        out.append(f"      {row.id} [{row.status}{'' if row.mandatory else ', nice to have'}] {state}{was}: {_clip(row.requirement)} <- {cited}" + (f" (shown: {', '.join(kept)})" if kept else ""))
    if old is not None:
        out.append("\n## Totals, new against old (postings per verdict)")
        for (profile, path), tally in sorted(totals.items()):
            out.append(f"  {profile} / {path}:")
            for name in ("mandatory coverage", "other coverage", "evidence strength", "must-keep", "page fit", "skills kept", "overall"):
                out.append(f"    {name}: " + ", ".join(f"{value} {tally.get(f'{name}:{value}', 0)}" for value in (BETTER, SAME, WORSE)))
        out.append("\nRESULT: " + ("A REGRESSION: a posting lost mandatory coverage or page fit against the baseline." if regressed else "no posting lost mandatory coverage or page fit against the baseline."))
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
    )
    sys.stdout.write(text)
    return 1 if regressed else 0


if __name__ == "__main__":
    raise SystemExit(main())
