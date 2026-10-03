"""0110-039: is a STORED assessment still what the profile would get now?

A quick / job-page / "Assess all new" assessment is stored once and read
many times (``quick_assess``'s store). Since 0110-039 it records its BASIS,
the same three things a find-jobs run seals on its ``AssessOutput``:

- ``prompt_version``: the assess prompt it was made with;
- ``constraints_digest``: ``assessment_core.constraints_digest`` of the
  candidate constraints the prompt carried (sponsorship need, eligible
  countries, own location, work mode);
- ``story_bank``: the bank the prompt was offered (ids and revision marks).

``stale_reason`` compares that with what the SAME resume identity would be
assessed with now, and names why the two differ:

- ``older_prompt``: the prompt version is not one the shipped ``assess.md``
  renders (``CURRENT_ASSESS_PROMPT_VERSIONS``);
- ``settings_changed``: the constraints digest differs (a changed work
  mode, countries, location or sponsorship need);
- ``story_bank_changed``: ``story_bank.bank_matches`` (0110-041, targeted): a
  bank entry added or edited since answers one of ITS OWN open questions
  (the same id, or the model-free near match), or it cites a bank answer
  that was edited, deleted or unshared. Answering one question flags the
  assessments that asked it, not every assessment that asked anything. The
  entries that matched are served as ``basis_stale_bank``, so the reason
  line can name the question that is now answered.

The same three checks, in the same order, as a run's unchanged skip
(``proposal_execution._basis_stale``).

A record with NO basis (written before 0110-039) is judged by what its
prompt can have missed, never re-assessed wholesale:

- quick assess always sent the sponsorship need, the countries and the
  location, but no work mode before 0110-038. So it is ``older_prompt`` when
  the profile has a work mode now (remote / hybrid / onsite), and only then;
- the sponsorship need and the countries it used are in its stored
  ``preferences``: it is ``settings_changed`` when they differ from the
  profile's now. The location it used was not stored, so it is taken as
  unchanged;
- it has no bank stamp, so the bank rule is not applied to it.

A profile with no work mode whose sponsorship need and countries are what
the record says has nothing the old prompt missed: its old verdicts stay.

0.1.10.7 PL2: a new record also carries the rest of what a run seals, so the
provenance survives without runs: ``profile_ref`` (the profile's id,
revision and content digest), ``posting_sha256`` (:func:`posting_sha256`, the
company index's ``content_sha256`` for the same posting) and ``model`` (the
model id that answered). They are recorded, not yet compared: a record
without them (written before 0.1.10.7) reads exactly as before.

Nothing here calls a model, and nothing is written: staleness is derived on
read. ``BasisCheck`` is one request's view: the settings of each resume
identity are read at most once, and the story bank at most once per resume
identity (never per row), and only when a record has an open question or
cites a bank answer. The bank rule itself is in memory: the entries changed
since the record's basis x its open questions.

Cost (the 0110-033 budget: a hot read of an unchanged workpad starts almost
no subprocess). Reading the profiles is a committed journal read (16 git
subprocesses here) and the bank another (65), so what was read is KEPT, per
workpad, for as long as nothing it was read from has changed: the journal
head (profiles, answers), ``find-jobs.json``, the setup's saved preferences
and the story bank's overlay file (the three by size and modification time).
A request on an unchanged workpad then pays one ``git rev-parse`` and three
``stat`` calls, whatever the number of rows; any change is seen by the very
next request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading

from .assessment_core import CURRENT_ASSESS_PROMPT_VERSIONS, assess_prompt_version, constraints_digest, normalize_work_mode
from .find_jobs.assess_contracts import AssessResponse

#: ``basis_stale_reason`` / ``job_state.assessment_stale.reason`` values.
REASON_OLDER_PROMPT = "older_prompt"
REASON_SETTINGS_CHANGED = "settings_changed"
REASON_STORY_BANK_CHANGED = "story_bank_changed"
BASIS_STALE_REASONS: tuple[str, ...] = (REASON_OLDER_PROMPT, REASON_SETTINGS_CHANGED, REASON_STORY_BANK_CHANGED)


def posting_sha256(title: str, text: str) -> str:
    """A posting's content digest: what the company index stores as ``content_sha256``.

    The ATS parsers hash the title and the plain posting text, joined by a
    newline (``ats_board_clients._text_bytes``), so an assessment of an
    indexed posting carries the digest the index has for it, and a changed
    posting can be told from the one that was assessed.
    """

    from .find_jobs.ats_board_clients import _text_bytes
    from .find_jobs.contracts import content_hash

    return content_hash(_text_bytes(title, text or None))


def _countries(values: object) -> frozenset[str]:
    return frozenset(item.strip().upper() for item in (values or ()) if isinstance(item, str) and item.strip())  # type: ignore[union-attr]


@dataclass(frozen=True)
class CurrentBasis:
    """What one resume identity would be assessed with now (no request overrides)."""

    visa_sponsorship_required: bool
    countries: tuple[str, ...]
    location: str
    work_mode: str

    @property
    def prompt_version(self) -> str:
        return assess_prompt_version(self.work_mode)

    @property
    def constraints_digest(self) -> str:
        return constraints_digest(
            visa_sponsorship_required=self.visa_sponsorship_required,
            countries=self.countries,
            location=self.location,
            work_mode=self.work_mode,
        )


@dataclass(frozen=True)
class Staleness:
    """Why a stored assessment should be made again; ``bank`` (``story_bank.BankMatch``) only for ``story_bank_changed``."""

    reason: str
    bank: tuple[object, ...] = ()


def staleness(item: AssessResponse, current: CurrentBasis, bank_now) -> Staleness | None:
    """Why ``item`` is not what ``current`` would produce, or ``None``. Pure.

    ``bank_now`` is called (at most once, and only for a record that has an
    open question or cites a bank answer) for the bank now
    (``story_bank.AssessBank``); it answers ``None`` when there is no bank to
    read.
    """

    from . import story_bank

    if item.prompt_version is None and item.constraints_digest is None:
        if normalize_work_mode(current.work_mode):
            return Staleness(REASON_OLDER_PROMPT)
        said = item.preferences
        if bool(said.visa_sponsorship_required) != current.visa_sponsorship_required or _countries(said.countries) != _countries(current.countries):
            return Staleness(REASON_SETTINGS_CHANGED)
        return None
    if item.prompt_version not in CURRENT_ASSESS_PROMPT_VERSIONS:
        return Staleness(REASON_OLDER_PROMPT)
    if item.constraints_digest != current.constraints_digest:
        return Staleness(REASON_SETTINGS_CHANGED)
    questions = item.result.structured_questions
    evidence = [evidence for row in item.result.matrix for evidence in row.resume_evidence]
    if not questions and not story_bank.cited_ids(evidence):
        return None
    bank = bank_now()
    if bank is None:
        return None
    sealed = None if item.story_bank is None else item.story_bank.entries
    matches = story_bank.bank_matches(questions=questions, evidence=evidence, sealed_marks=sealed, bank=bank)
    return Staleness(REASON_STORY_BANK_CHANGED, matches) if matches else None


def stale_reason(item: AssessResponse, current: CurrentBasis, bank_now) -> str | None:
    """``staleness``'s reason alone."""

    found = staleness(item, current, bank_now)
    return None if found is None else found.reason


_UNREAD = object()


def _stat_mark(path: Path) -> tuple[int, int] | None:
    try:
        found = path.stat()
    except OSError:
        return None
    return (found.st_mtime_ns, found.st_size)


@dataclass
class _Kept:
    """What was read for one workpad while ``key`` held (see the module docstring's "Cost")."""

    key: tuple[object, ...]
    current: dict[str | None, CurrentBasis | None] = field(default_factory=dict)
    #: ``story_bank.AssessBank`` per resume identity (``None``: no bank to read).
    banks: dict[str | None, object | None] = field(default_factory=dict)


_KEPT_LOCK = threading.Lock()
_kept: dict[str, _Kept] = {}
# (home, target) -> the two home files a kept read depends on. Where they are
# never changes for a bound folder, and finding out costs a subprocess each.
_watched: dict[tuple[str, str], tuple[Path, Path, Path]] = {}


def _watched_files(home_root: Path, target: Path) -> tuple[Path, Path, Path]:
    key = (str(home_root), str(target))
    found = _watched.get(key)
    if found is None:
        from . import story_bank
        from .find_jobs.discovery.storage import discovery_dir

        found = _watched[key] = (
            discovery_dir(home_root, target) / "prefs.json",
            story_bank.bank_path(home_root, target),
            story_bank.stories_path(home_root, target),
        )
    return found


class BasisCheck:
    """One request's staleness check over stored assessments. Never raises.

    ``resolved`` is the gig's workpad when the caller holds it (else it is
    resolved on first use). A resume identity whose settings cannot be read
    (a deleted profile, no gig) has no current basis: its records are not
    called stale.
    """

    def __init__(self, *, home_root: Path, target: Path, resolved: object | None = None) -> None:
        self._home_root = Path(home_root)
        self._target = Path(target)
        self._resolved = resolved
        self._profiles: object = _UNREAD
        self._kept_here: _Kept | None = None
        # Never kept past this request: a read that failed (it may work next
        # time), and a pasted resume's bank (the SELECTED profile's, and the
        # selection is not one of the things a kept read is keyed on).
        self._failed: set[str | None] = set()
        self._pasted_bank: object = _UNREAD

    def _workpad(self):
        if self._resolved is None:
            from ..workpad import resolve_workpad

            self._resolved = resolve_workpad(
                home_root=self._home_root, requested_target=self._target, gig_id=None, allow_semantic_state=True
            )
        return self._resolved

    def _kept(self) -> _Kept:
        """This request's reads: the ones kept for the workpad while nothing they depend on changed."""

        if self._kept_here is not None:
            return self._kept_here
        kept = _Kept(key=())
        try:
            from ..run import _cheap_workpad_head

            workpad = Path(self._workpad().path)  # type: ignore[attr-defined]
            head = _cheap_workpad_head(workpad)
            if head is not None:
                prefs, bank, stories = _watched_files(self._home_root, self._target)
                key = (
                    head,
                    str(self._home_root),
                    str(self._target),
                    _stat_mark(self._target / "find-jobs.json"),
                    _stat_mark(prefs),
                    _stat_mark(bank),
                    _stat_mark(stories),
                )
                with _KEPT_LOCK:
                    found = _kept.get(str(workpad))
                    if found is None or found.key != key:
                        found = _kept[str(workpad)] = _Kept(key=key)
                    kept = found
        except Exception:  # noqa: BLE001 - no gig, no head: nothing is kept past this request
            kept = _Kept(key=())
        self._kept_here = kept
        return kept

    def _profile(self, profile_id: str):
        if self._profiles is _UNREAD:
            from . import profile_records

            self._profiles = {record.profile_id: record for record in profile_records.list_profiles(self._workpad())}
        return self._profiles.get(profile_id)  # type: ignore[union-attr]

    def current(self, profile_id: str | None) -> CurrentBasis | None:
        """The basis ``profile_id`` (``None``: a pasted resume) would be assessed with now."""

        if profile_id in self._failed:
            return None
        current = self._kept().current
        if profile_id not in current:
            try:
                current[profile_id] = self._read_current(profile_id)
            except Exception:  # noqa: BLE001 - settings that cannot be read say nothing; never a failed read
                self._failed.add(profile_id)
                return None
        return current[profile_id]

    def _read_current(self, profile_id: str | None) -> CurrentBasis | None:
        from .find_jobs.resume_input import resolve_preferences
        from .quick_assess import candidate_location_and_work_mode

        profile = None
        if profile_id is not None:
            profile = self._profile(profile_id)
            if profile is None:
                return None
        preferences = resolve_preferences(None, target=self._target, profile=profile)
        location, work_mode = candidate_location_and_work_mode(preferences, profile, home_root=self._home_root, target=self._target)
        return CurrentBasis(
            visa_sponsorship_required=bool(preferences.visa_sponsorship_required),
            countries=tuple(preferences.countries or ()),
            location=location,
            work_mode=normalize_work_mode(work_mode),
        )

    def _bank(self, profile_id: str | None):
        """The bank ``profile_id`` reads now: ONE read per resume identity, kept like the settings."""

        from . import story_bank

        def read():
            # 0.1.10.7 C: the bank is the user's; every resume identity reads the same one.
            bank = story_bank.assess_bank(home_root=self._home_root, target=self._target, profile_id=profile_id)
            return None if bank.profile_id is None else bank

        if profile_id is None:
            if self._pasted_bank is _UNREAD:
                self._pasted_bank = read()
            return self._pasted_bank
        banks = self._kept().banks
        if profile_id not in banks:
            banks[profile_id] = read()
        return banks[profile_id]

    def staleness(self, item: AssessResponse) -> Staleness | None:
        """Why ``item`` should be assessed again, with the bank entries that say so; ``None``: current, or it cannot be said."""

        profile_id = item.resume.profile_id
        current = self.current(profile_id)
        if current is None:
            return None
        try:
            return staleness(item, current, lambda: self._bank(profile_id))
        except Exception:  # noqa: BLE001 - display-only: a check that fails marks nothing
            return None

    def reason(self, item: AssessResponse) -> str | None:
        """Why ``item`` should be assessed again, or ``None`` (current, or it cannot be said)."""

        found = self.staleness(item)
        return None if found is None else found.reason

    def served(self, item: AssessResponse) -> dict[str, object]:
        """The additive keys a served assessment carries: ``basis_stale`` and, when true, ``basis_stale_reason``.

        0110-041: a ``story_bank_changed`` one also carries ``basis_stale_bank``,
        the bank entries that made it stale (``story_bank.BankMatch.to_json``:
        ids and question words, never an answer).
        """

        found = self.staleness(item)
        if found is None:
            return {"basis_stale": False}
        served: dict[str, object] = {"basis_stale": True, "basis_stale_reason": found.reason}
        if found.bank:
            served["basis_stale_bank"] = [match.to_json() for match in found.bank]  # type: ignore[attr-defined]
        return served


__all__ = [
    "BASIS_STALE_REASONS",
    "REASON_OLDER_PROMPT",
    "REASON_SETTINGS_CHANGED",
    "REASON_STORY_BANK_CHANGED",
    "BasisCheck",
    "CurrentBasis",
    "Staleness",
    "posting_sha256",
    "stale_reason",
    "staleness",
]
