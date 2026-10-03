"""0110-034: a profile's story bank -- every answered question, kept and reused.

The bank is a VIEW, not a second copy of the answers. The answer text lives
where it always did, in the gig's ``experience_qa`` native records
(``experience_answers.py``); nothing is migrated. On top of that sits one
small local file, the overlay, that holds what the records cannot (their
schema is closed and has no profile field):

    <home>/scout/<project_id>/story_bank/bank.json

    {"schema_version": "scout-story-bank:1",
     "records":  {"<record_id>": "<profile_id>"},          # who owns a record
     "profiles": {"<profile_id>": {
         "share_with": "<profile_id>" | null,              # whose bank it also reads
         "entries": {"<question_id>": {
             "tag": "<tag>" | null,                        # the user's own tag
             "first_answered_at": "...", "updated_at": "...",
             "revision": 3, "written_by": "operator" | "agent",
             "history": [{"at", "by", "action"}],          # newest last, capped
             "edited": true|false, "confirmed_from": "<question_id>" | null,
             "postings": [{"job_identity", "title", "company", "url", "kind", "at"}]}}}}}

WHO OWNS AN ANSWER.  A record named in ``records`` belongs to that profile:
every answer saved since this module exists goes to a record of the profile
that saved it (``experience_answers.AnswerScope``), so two profiles can
answer the same ``question_id`` differently.  A record NOT named there was
written before the bank ("legacy"): each of its answers belongs to the
profile whose stored assessment recorded ``answer:<question_id>`` in its
history (the newest such entry), else to the gig's default profile.  Legacy
answers are listed like any other; editing one writes a new revision of its
own record through the same answer path.

WHAT A PROFILE SEES.  Its own answers, plus the OWN answers of the one
profile named by ``share_with`` (one hop: that profile's own ``share_with``
is not followed).  Its own answer wins an id both hold.  Nothing else: a
profile never reads another profile's answers unless ``share_with`` says so.

REUSE.  ``answers_for_profile`` is what the assess prompt, the tailoring and
``GET /api/answers`` read (exact ``question_id`` reuse, as before, now per
profile).  ``prompt_summaries`` is the second, short list the assess prompt
gets (id, the question as asked, a one-line answer) so the model can reuse an
answer for a requirement worded differently, in the same call.  ``near_match``
is the model-free check behind "We already know: ..., use it?": token overlap
between the new question and each bank entry (ids and question words).

TWO WRITERS.  The user (UI, CLI) and an agent (API, CLI ``--actor agent``)
both write the bank.  Every write bumps the entry's ``revision`` and
``updated_at`` and records who wrote it (``written_by``, ``history``).  An
edit or a delete names the ``updated_at`` it read (``expected_updated_at``);
when the entry has changed since, the write is refused with
``story_bank_changed`` and the current entry (``StoryBankError.entry``), so
neither silently overwrites the other.

PRIVACY.  ``personal_info_in_answer`` runs on every save and edit (email,
phone, links, street address, the saved name): such an answer is refused.
What goes to a model is redacted again (``resume_privacy.redact_inline``) and
an entry that still shows personal information is left out of the summaries.

Local and model-free: nothing here calls a model or the network.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
import json
from pathlib import Path
import re
import threading

from ..native_records import NativeRecordResult
from ..private_records import PrivateRecordError
from . import experience_answers
from .experience_answers import AnswerScope, PriorAnswer
from .find_jobs.discovery.storage import atomic_write, project_id
from ..canonical import digest_imported_bytes
from .assessment_core import BankAnswer
from .assessment_core import PriorAnswer as CorePriorAnswer
from .question_ids import normalize_question_id
from .resume_pii import detect_contact_details
from .resume_privacy import guard_name, is_name_line, redact_inline

SCHEMA_VERSION = "scout-story-bank:1"

#: The bank entries the assess prompt is offered, at most.
MAX_PROMPT_SUMMARIES = 40
#: One summary line's answer part, at most.
MAX_SUMMARY_CHARS = 160
_MAX_SUMMARY_QUESTION_CHARS = 160
#: The question's own words, as kept with the answer (the record's ``prompt`` bound is 4,096).
MAX_QUESTION_CHARS = 700
MAX_TAG_CHARS = 40
_MAX_POSTINGS = 50
_MAX_HISTORY = 20

ACTOR_OPERATOR = "operator"
ACTOR_AGENT = "agent"
ACTORS: tuple[str, ...] = (ACTOR_OPERATOR, ACTOR_AGENT)
#: A near match needs at least this score (0..1); 1.0 is the same id.
NEAR_MATCH_THRESHOLD = 0.5

POSTING_ANSWERED = "answered"
POSTING_REUSED = "reused"
POSTING_CONFIRMED = "confirmed"

_PROFILE_ID = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")
_TAG = re.compile(r"\A[a-z0-9][a-z0-9 _-]{0,39}\Z")
_ANSWER_TRIGGER = "answer:"

_LOCK = threading.Lock()
#: Held across an edit/delete/add (the stale check and the write it guards).
#: In-process only: the server's threads. A CLI write racing a server write
#: can still slip between the check and the write.
_WRITE_LOCK = threading.RLock()


class StoryBankError(ValueError):
    """A story bank call was refused; ``code`` is the API/CLI error code."""

    def __init__(self, code: str, message: str, *, entry: "BankEntry | None" = None) -> None:
        super().__init__(message)
        self.code = code
        #: ``story_bank_changed`` / ``story_exists``: the entry as it is now.
        self.entry = entry


def actor_value(actor: str | None) -> str:
    """``operator`` (the default) or ``agent``; anything else is ``invalid_value``."""

    if actor is None or not str(actor).strip():
        return ACTOR_OPERATOR
    clean = str(actor).strip().lower()
    if clean not in ACTORS:
        raise StoryBankError("invalid_value", "actor must be operator or agent")
    return clean


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# --- the personal-info check -----------------------------------------------------------


def personal_info_in_answer(text: str, *, names: Iterable[str] = ()) -> list[str]:
    """What looks personal in an answer: ``[]`` when nothing does.

    The same parts as the tailored custom line's check
    (``tailored_resume.personal_info_found``): the paste path's contact
    shapes (``resume_pii.detect_contact_details``: email, phone,
    linkedin/github links, a street address), any other link
    ``resume_privacy.redact_inline`` would remove, and ``name`` when the text
    holds one of ``names`` (the name saved for the PDF header). One
    difference: with no name known there is no name-shape fallback. A resume
    line that is two capitalised words is a name; an answer that is
    ("Apache Kafka", "Google Cloud") is usually the answer.
    """

    found = list(detect_contact_details(text))
    if not found and any(redact_inline(line) != line for line in text.splitlines() or [text]):
        found.append("links")
    flat = " ".join(text.split()).casefold()
    known = [" ".join(name.split()).casefold() for name in names if isinstance(name, str) and name.strip()]
    if any(name in flat for name in known):
        found.append("name")
    return found


def _refuse_personal_info(text: str, *, names: Iterable[str], what: str) -> None:
    found = personal_info_in_answer(text, names=names)
    if found:
        raise StoryBankError(
            "personal_info_refused",
            f"{what} looks like it holds personal information ({', '.join(found)}); the story bank holds experience, never "
            "a name or contact details (GigAI stores none: you type them only when you generate a PDF). Remove it and save again",
        )


# --- tags ------------------------------------------------------------------------------

#: Checked in order against the question's words (then its id): the first hit is the tag.
_KEYWORD_TAGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("leadership", ("lead", "led", "leading", "leadership", "mentor", "mentored", "mentoring", "manage", "managed", "managing", "manager", "reports", "hiring")),
    ("conflict", ("conflict", "disagree", "disagreed", "disagreement", "pushback", "difficult")),
    ("failure", ("fail", "failed", "failure", "mistake", "outage", "incident", "postmortem")),
    ("collaboration", ("stakeholder", "stakeholders", "cross-functional", "collaborate", "collaborated", "collaboration", "partner", "partnered")),
    ("system-design", ("architecture", "architect", "architected", "scale", "scaled", "scaling", "distributed", "design", "designed")),
    ("delivery", ("shipped", "ship", "launch", "launched", "deadline", "delivered", "delivery", "roadmap")),
)
_CATEGORY_TAGS: dict[str, str] = {
    "years": "experience-level", "seniority": "experience-level",
    "cloud": "technical", "language": "technical", "framework": "technical", "tooling": "technical", "tool": "technical",
    "database": "technical", "skill": "skill", "pattern": "technical",
    "domain": "domain", "industry": "domain",
    "location": "eligibility", "authorization": "eligibility", "eligible": "eligibility", "auth": "eligibility",
    "visa": "eligibility", "sponsorship": "eligibility", "clearance": "eligibility", "remote": "eligibility",
    "education": "education",
}
_WORD = re.compile(r"[a-z0-9][a-z0-9+#.-]*")


def tag_for(question_id: str, question: str = "") -> str:
    """The competency/theme tag of one question: model-free and stable.

    Behavioural themes first, from the question's words (leadership,
    conflict, failure, collaboration, system-design, delivery); else the
    theme of the id's category (``cloud:gcp`` -> ``technical``,
    ``years:python`` -> ``experience-level``, ``location:us_region`` ->
    ``eligibility``); else ``other``.
    """

    normalized = normalize_question_id(question_id)
    category = normalized.split(":", 1)[0] if ":" in normalized else ""
    by_category = next((tag for token in category.split("_") if (tag := _CATEGORY_TAGS.get(token))), None)
    if by_category in ("eligibility", "experience-level", "education"):
        return by_category
    words = set(_WORD.findall(f"{question} {normalized.replace(':', ' ').replace('_', ' ')}".lower()))
    for tag, keywords in _KEYWORD_TAGS:
        if words & set(keywords):
            return tag
    return by_category or "other"


# --- the overlay file --------------------------------------------------------------------


def bank_path(home_root: Path, target: Path) -> Path:
    return Path(home_root) / "scout" / project_id(Path(home_root), Path(target)) / "story_bank" / "bank.json"


def _empty_overlay() -> dict[str, object]:
    return {"schema_version": SCHEMA_VERSION, "records": {}, "profiles": {}}


def _read_overlay(home_root: Path, target: Path) -> dict[str, object]:
    """The overlay, tolerantly: missing, unreadable or another schema -> empty."""

    try:
        path = bank_path(home_root, target)
    except Exception as exc:  # noqa: BLE001 - any failure to name the project is one typed refusal
        raise StoryBankError("target_unavailable", "this folder is not bound to a GigAI project") from exc
    if path.is_symlink() or not path.is_file():
        return _empty_overlay()
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return _empty_overlay()
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return _empty_overlay()
    records = value.get("records")
    profiles = value.get("profiles")
    return {
        "schema_version": SCHEMA_VERSION,
        "records": {str(k): str(v) for k, v in records.items()} if isinstance(records, dict) else {},
        "profiles": {str(k): v for k, v in profiles.items() if isinstance(v, dict)} if isinstance(profiles, dict) else {},
    }


def _write_overlay(home_root: Path, target: Path, overlay: Mapping[str, object]) -> None:
    atomic_write(bank_path(home_root, target), json.dumps(overlay, indent=2, sort_keys=True).encode("utf-8"))


def _profile_block(overlay: dict[str, object], profile_id: str) -> dict[str, object]:
    profiles = overlay["profiles"]
    assert isinstance(profiles, dict)
    block = profiles.setdefault(profile_id, {})
    if not isinstance(block.get("entries"), dict):
        block["entries"] = {}
    block.setdefault("share_with", None)
    return block


def _entry_meta(overlay: Mapping[str, object], profile_id: str, question_id: str) -> dict[str, object]:
    profiles = overlay.get("profiles")
    block = profiles.get(profile_id) if isinstance(profiles, dict) else None
    entries = block.get("entries") if isinstance(block, dict) else None
    meta = entries.get(question_id) if isinstance(entries, dict) else None
    return meta if isinstance(meta, dict) else {}


# --- profiles and stored assessments -------------------------------------------------------


@dataclass(frozen=True)
class _Gig:
    home_root: Path
    target: Path
    gig_id: str
    profile_ids: tuple[str, ...]
    default_profile_id: str | None
    labels: Mapping[str, str] = field(default_factory=dict)


def _gig(home_root: Path, target: Path) -> _Gig:
    from ..workpad import resolve_workpad
    from . import profile_records

    try:
        resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
    except Exception as exc:  # noqa: BLE001 - any failure to resolve the gig is one typed refusal
        raise StoryBankError("target_unavailable", "no Scout gig is available for this folder") from exc
    try:
        profiles = profile_records.list_profiles(resolved)
    except Exception:  # noqa: BLE001 - a gig with no readable profiles has an empty bank, not an error
        profiles = ()
    default = profile_records.default_profile(profiles)
    return _Gig(
        home_root=Path(home_root),
        target=Path(target),
        gig_id=resolved.gig_id,
        profile_ids=tuple(item.profile_id for item in profiles),
        default_profile_id=None if default is None else default.profile_id,
        labels={item.profile_id: item.label for item in profiles},
    )


def reader_profile_id(*, home_root: Path, target: Path, profile_id: str | None) -> str | None:
    """Whose bank an assessment or a tailoring reads: ``profile_id``, or for a
    pasted resume (no profile) the gig's SELECTED profile -- the person at the
    keyboard. ``None`` when the gig has no profile to read from."""

    if profile_id is not None:
        return profile_id
    from ..workpad import resolve_workpad
    from . import profile_records

    try:
        resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
        selected = profile_records.selected_profile(resolved, home_root=Path(home_root), target=Path(target))
    except Exception:  # noqa: BLE001 - no gig or no selection: nothing to read
        return None
    return None if selected is None else selected.profile_id


def _check_profile(gig: _Gig, profile_id: str) -> str:
    if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id) or profile_id not in gig.profile_ids:
        raise StoryBankError("profile_not_found", f"profile {profile_id!r} is not a profile of this gig")
    return profile_id


@dataclass(frozen=True)
class _AnswerEvent:
    """One ``answer:<question_id>`` history entry of a stored assessment."""

    profile_id: str
    question_id: str
    at: str
    job_identity: str
    title: str
    company: str
    url: str | None


def _answer_events(gig: _Gig) -> list[_AnswerEvent]:
    """Every answer a stored assessment's history recorded, oldest first.

    This is how an answer saved before the bank knows its profile and its
    postings: ``POST /api/answers`` re-assessed the job with the trigger
    ``answer:<question_id>``, stored under the profile that was assessed.
    """

    from .quick_assess import list_quick_assessments

    events: list[_AnswerEvent] = []
    try:
        items = list_quick_assessments(gig.home_root, gig.target)
    except Exception:  # noqa: BLE001 - no readable store means no history, not an error
        return events
    for item in items:
        profile_id = item.resume.profile_id
        if not profile_id:
            continue
        for entry in item.history:
            if not entry.trigger.startswith(_ANSWER_TRIGGER):
                continue
            events.append(
                _AnswerEvent(
                    profile_id=profile_id,
                    question_id=normalize_question_id(entry.trigger[len(_ANSWER_TRIGGER):]),
                    at=entry.at,
                    job_identity=item.job.job_identity,
                    title=item.job.title,
                    company=item.job.company,
                    url=item.job.source_url,
                )
            )
    events.sort(key=lambda event: event.at)
    return events


# --- the view --------------------------------------------------------------------------


@dataclass(frozen=True)
class BankPosting:
    job_identity: str
    title: str
    company: str
    url: str | None
    kind: str
    at: str

    def to_json(self) -> dict[str, object]:
        return {"job_identity": self.job_identity, "title": self.title, "company": self.company, "url": self.url, "kind": self.kind, "at": self.at}


@dataclass(frozen=True)
class BankEntry:
    """One answered question, as the bank shows it."""

    question_id: str
    question: str
    answer: str
    tag: str
    owner_profile_id: str
    #: True when this entry is read from the profile named by ``share_with``.
    shared: bool
    #: True for an answer saved before the bank existed (no overlay entry).
    legacy: bool
    edited: bool
    confirmed_from: str | None
    first_answered_at: str | None
    #: Changes on every write; an edit or delete sends the value it read.
    updated_at: str | None
    postings: tuple[BankPosting, ...]
    record_id: str
    revision_id: str
    #: How many times the bank wrote this entry (0: saved before the bank).
    revision: int = 0
    #: Who wrote it last: ``operator`` or ``agent``.
    written_by: str = ACTOR_OPERATOR
    #: The last writes, oldest first: ``{"at", "by", "action"}``.
    history: tuple[Mapping[str, str], ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "answer": self.answer,
            "tag": self.tag,
            "owner_profile_id": self.owner_profile_id,
            "shared": self.shared,
            "legacy": self.legacy,
            "edited": self.edited,
            "confirmed_from": self.confirmed_from,
            "first_answered_at": self.first_answered_at,
            "updated_at": self.updated_at,
            "revision": self.revision,
            "written_by": self.written_by,
            "history": [dict(item) for item in self.history],
            "postings": [posting.to_json() for posting in self.postings],
            "record_id": self.record_id,
            "revision_id": self.revision_id,
        }


def _owned_rows(gig: _Gig, overlay: Mapping[str, object], events: list[_AnswerEvent] | None) -> dict[str, dict[str, PriorAnswer]]:
    """``profile_id -> question_id -> row``: every answered question under its owner."""

    rows = experience_answers.list_answer_rows(home_root=gig.home_root, requested_target=gig.target, gig_id=gig.gig_id)
    records = overlay.get("records")
    assert isinstance(records, dict)
    owned: dict[str, dict[str, PriorAnswer]] = {}
    legacy_owner: dict[str, str] | None = None
    for row in rows:
        owner = records.get(row.record_id)
        if owner is None:
            if legacy_owner is None:
                legacy_owner = {}
                # Only more than one profile makes the history worth reading.
                if len(gig.profile_ids) > 1:
                    for event in events if events is not None else _answer_events(gig):
                        if event.profile_id in gig.profile_ids:
                            legacy_owner[event.question_id] = event.profile_id  # the newest entry wins
            owner = legacy_owner.get(row.question_id) or gig.default_profile_id
            if owner is None:
                continue
        owned.setdefault(owner, {})[row.question_id] = row
    return owned


def _share_with(gig: _Gig, overlay: Mapping[str, object], profile_id: str) -> str | None:
    profiles = overlay.get("profiles")
    block = profiles.get(profile_id) if isinstance(profiles, dict) else None
    other = block.get("share_with") if isinstance(block, dict) else None
    if isinstance(other, str) and other != profile_id and other in gig.profile_ids:
        return other
    return None


def _postings(meta: Mapping[str, object], events: Iterable[_AnswerEvent], profile_id: str, question_id: str) -> tuple[BankPosting, ...]:
    seen: dict[tuple[str, str], BankPosting] = {}
    for event in events:
        if event.profile_id == profile_id and event.question_id == question_id:
            seen[(event.job_identity, POSTING_ANSWERED)] = BankPosting(
                event.job_identity, event.title, event.company, event.url, POSTING_ANSWERED, event.at
            )
    stored = meta.get("postings")
    for item in stored if isinstance(stored, list) else ():
        if not isinstance(item, dict) or not isinstance(item.get("job_identity"), str):
            continue
        kind = str(item.get("kind") or POSTING_ANSWERED)
        seen[(item["job_identity"], kind)] = BankPosting(
            item["job_identity"],
            str(item.get("title") or ""),
            str(item.get("company") or ""),
            item.get("url") if isinstance(item.get("url"), str) else None,
            kind,
            str(item.get("at") or ""),
        )
    return tuple(sorted(seen.values(), key=lambda posting: (posting.at, posting.job_identity, posting.kind)))


def _entry(row: PriorAnswer, *, owner: str, shared: bool, overlay: Mapping[str, object], records: Mapping[str, str], events: Iterable[_AnswerEvent]) -> BankEntry:
    meta = _entry_meta(overlay, owner, row.question_id)
    question = row.prompt if row.prompt and row.prompt != row.question_id else row.question_id
    own_tag = meta.get("tag")
    postings = _postings(meta, events, owner, row.question_id)
    first = meta.get("first_answered_at") if isinstance(meta.get("first_answered_at"), str) else None
    updated = meta.get("updated_at") if isinstance(meta.get("updated_at"), str) else None
    confirmed = meta.get("confirmed_from")
    raw_history = meta.get("history")
    history = tuple(
        {"at": str(item.get("at") or ""), "by": str(item.get("by") or ACTOR_OPERATOR), "action": str(item.get("action") or "")}
        for item in (raw_history if isinstance(raw_history, list) else ())
        if isinstance(item, dict)
    )
    return BankEntry(
        question_id=row.question_id,
        question=question,
        answer=row.answer,
        tag=own_tag if isinstance(own_tag, str) and own_tag else tag_for(row.question_id, question),
        owner_profile_id=owner,
        shared=shared,
        legacy=row.record_id not in records,
        edited=bool(meta.get("edited")),
        confirmed_from=confirmed if isinstance(confirmed, str) else None,
        first_answered_at=first or (postings[0].at if postings else None),
        updated_at=updated or row.recorded_at or None,
        postings=postings,
        record_id=row.record_id,
        revision_id=row.revision_id,
        revision=meta["revision"] if type(meta.get("revision")) is int else 0,
        written_by=meta["written_by"] if meta.get("written_by") in ACTORS else ACTOR_OPERATOR,  # type: ignore[arg-type]
        history=history,
    )


def read_bank(
    *, home_root: Path, target: Path, profile_id: str, include_shared: bool = True, with_postings: bool = True
) -> tuple[BankEntry, ...]:
    """The bank ``profile_id`` sees: its own answers, then the shared profile's.

    Sorted by question id, own entries first. ``with_postings=False`` skips
    reading the stored assessments when no answer needs them (the assess and
    tailoring paths: they only need the answers).
    """

    gig = _gig(Path(home_root), Path(target))
    _check_profile(gig, profile_id)
    overlay = _read_overlay(gig.home_root, gig.target)
    events = _answer_events(gig) if with_postings else None
    owned = _owned_rows(gig, overlay, events)
    records = overlay["records"]
    assert isinstance(records, dict)
    shown_events = events if events is not None else []
    entries = [
        _entry(row, owner=profile_id, shared=False, overlay=overlay, records=records, events=shown_events)
        for row in owned.get(profile_id, {}).values()
    ]
    other = _share_with(gig, overlay, profile_id) if include_shared else None
    if other is not None:
        own_ids = set(owned.get(profile_id, {}))
        entries.extend(
            _entry(row, owner=other, shared=True, overlay=overlay, records=records, events=shown_events)
            for row in owned.get(other, {}).values()
            if row.question_id not in own_ids
        )
    entries.sort(key=lambda entry: (entry.shared, entry.question_id))
    return tuple(entries)


def answers_for_profile(
    *, home_root: Path, target: Path, profile_id: str | None, names: Iterable[str] = (), strict: bool = False
) -> dict[str, PriorAnswer]:
    """``question_id -> answer`` for everything ``profile_id`` may reuse.

    What the assess prompt's PRIOR ANSWERS, the tailoring's answer sources
    and ``GET /api/answers`` read. ``None`` has no bank. Contact details are
    redacted from the text and the words of ``names`` (the candidate's name,
    when the caller knows it) removed: an answer saved before the
    personal-info check may hold some. ``strict`` raises ``StoryBankError``
    (an unknown profile, no gig) instead of answering ``{}``.
    """

    names = tuple(names)

    if profile_id is None:
        return {}
    try:
        entries = read_bank(home_root=home_root, target=target, profile_id=profile_id, with_postings=False)
    except StoryBankError:
        if strict:
            raise
        return {}
    return {
        entry.question_id: PriorAnswer(
            question_id=entry.question_id,
            prompt=entry.question,
            answer=_redacted(entry.answer, names),
            record_id=entry.record_id,
            revision_id=entry.revision_id,
            recorded_at=entry.updated_at or "",
        )
        for entry in entries
    }


def sharing(*, home_root: Path, target: Path, profile_id: str) -> dict[str, object]:
    """Whose bank ``profile_id`` also reads, and which profiles read its own."""

    gig = _gig(Path(home_root), Path(target))
    _check_profile(gig, profile_id)
    overlay = _read_overlay(gig.home_root, gig.target)
    return {
        "share_with": _share_with(gig, overlay, profile_id),
        "read_by": [other for other in gig.profile_ids if other != profile_id and _share_with(gig, overlay, other) == profile_id],
        "profiles": [{"profile_id": other, "label": gig.labels.get(other, other)} for other in gig.profile_ids if other != profile_id],
    }


def set_sharing(*, home_root: Path, target: Path, profile_id: str, share_with: str | None) -> dict[str, object]:
    """Make ``profile_id`` also read the bank of ``share_with`` (``None``: only its own).

    Explicit and one hop. It never changes what ``share_with`` itself reads.
    """

    gig = _gig(Path(home_root), Path(target))
    _check_profile(gig, profile_id)
    if share_with is not None:
        _check_profile(gig, share_with)
        if share_with == profile_id:
            raise StoryBankError("invalid_value", "a profile cannot share its story bank with itself")
    with _LOCK:
        overlay = _read_overlay(gig.home_root, gig.target)
        _profile_block(overlay, profile_id)["share_with"] = share_with
        _write_overlay(gig.home_root, gig.target, overlay)
    return sharing(home_root=home_root, target=target, profile_id=profile_id)


# --- writes --------------------------------------------------------------------------------


def _posting_json(posting: Mapping[str, object] | None, kind: str, at: str) -> dict[str, object] | None:
    if not posting or not isinstance(posting.get("job_identity"), str) or not posting["job_identity"]:
        return None
    return {
        "job_identity": posting["job_identity"],
        "title": str(posting.get("title") or "")[:300],
        "company": str(posting.get("company") or "")[:300],
        "url": posting.get("url") if isinstance(posting.get("url"), str) else None,
        "kind": kind,
        "at": at,
    }


def _add_posting(meta: dict[str, object], item: dict[str, object] | None) -> None:
    if item is None:
        return
    postings = [
        existing
        for existing in (meta.get("postings") if isinstance(meta.get("postings"), list) else [])
        if isinstance(existing, dict) and (existing.get("job_identity"), existing.get("kind")) != (item["job_identity"], item["kind"])
    ]
    postings.append(item)
    meta["postings"] = postings[-_MAX_POSTINGS:]


def _stamp(meta: dict[str, object], *, at: str, actor: str, action: str) -> None:
    """One more write of this entry: revision, when, who, what."""

    meta["revision"] = (meta["revision"] if type(meta.get("revision")) is int else 0) + 1  # type: ignore[operator]
    meta["updated_at"] = at
    meta["written_by"] = actor
    history = [item for item in (meta.get("history") if isinstance(meta.get("history"), list) else []) if isinstance(item, dict)]
    history.append({"at": at, "by": actor, "action": action})
    meta["history"] = history[-_MAX_HISTORY:]


def _current_entry(home_root: Path, target: Path, profile_id: str, question_id: str) -> "BankEntry | None":
    return next(
        (
            entry
            for entry in read_bank(home_root=home_root, target=target, profile_id=profile_id, include_shared=False)
            if entry.question_id == question_id
        ),
        None,
    )


def _refuse_stale(home_root: Path, target: Path, profile_id: str, question_id: str, expected_updated_at: str | None) -> None:
    """``story_bank_changed`` (with the current entry) when the entry moved on since it was read."""

    if expected_updated_at is None:
        return
    current = _current_entry(home_root, target, profile_id, question_id)
    if current is not None and current.updated_at != expected_updated_at:
        raise StoryBankError(
            "story_bank_changed",
            f"this story bank entry changed since you read it (last written by {current.written_by}); read it again and retry",
            entry=current,
        )


def _clean_question(question: str | None) -> str:
    if question is None:
        return ""
    return " ".join(question.split())[:MAX_QUESTION_CHARS]


def _clean_tag(tag: str) -> str:
    clean = " ".join(tag.split()).lower()
    if not _TAG.fullmatch(clean):
        raise StoryBankError("invalid_value", f"tag must be 1 to {MAX_TAG_CHARS} lowercase letters, digits, spaces, '-' or '_'")
    return clean


def save_answer(
    *,
    home_root: Path,
    target: Path,
    profile_id: str,
    question_id: str,
    answer: str,
    question: str | None = None,
    posting: Mapping[str, object] | None = None,
    confirmed_from: str | None = None,
    names: Iterable[str] = (),
    edited: bool = False,
    actor: str | None = None,
    tag: str | None = None,
    action: str | None = None,
) -> NativeRecordResult:
    """Save one answer into ``profile_id``'s bank: the one write path for every surface.

    The answer text goes through ``experience_answers.record_answer`` (a new
    revision of the profile's own record, or of the legacy record that
    already holds its answer); the overlay gets the dates, the posting that
    asked (``posting``: ``job_identity``, ``title``, ``company``, ``url``)
    and, for a confirmed near match, the bank question it came from.
    Raises ``StoryBankError`` (``personal_info_refused``, ``profile_not_found``,
    ``target_unavailable``) or ``PrivateRecordError`` (``answer_invalid``...).
    """

    names = tuple(names)
    writer = actor_value(actor)
    clean_tag = None if tag is None or not tag.strip() else _clean_tag(tag)
    _refuse_personal_info(answer, names=names, what="this answer")
    clean_question = _clean_question(question)
    if clean_question:
        _refuse_personal_info(clean_question, names=names, what="this question")
    gig = _gig(Path(home_root), Path(target))
    _check_profile(gig, profile_id)
    normalized = normalize_question_id(question_id)
    overlay = _read_overlay(gig.home_root, gig.target)
    records = overlay["records"]
    assert isinstance(records, dict)
    owned = _owned_rows(gig, overlay, None)
    existing = owned.get(profile_id, {}).get(normalized)
    own_records = frozenset(record_id for record_id, owner in records.items() if owner == profile_id)
    result = experience_answers.record_answer(
        home_root=gig.home_root, requested_target=gig.target, gig_id=gig.gig_id,
        question_id=normalized, prompt=clean_question, answer=answer,
        scope=AnswerScope(
            existing_record=None if existing is None else existing.record_id,
            append_records=own_records,
            owner=profile_id,
        ),
    )
    at = _now()
    with _LOCK:
        overlay = _read_overlay(gig.home_root, gig.target)
        records = overlay["records"]
        assert isinstance(records, dict)
        if existing is None or existing.record_id in own_records:
            records[result.record_id] = profile_id
        entries = _profile_block(overlay, profile_id)["entries"]
        assert isinstance(entries, dict)
        meta = entries.setdefault(normalized, {})
        meta.setdefault("first_answered_at", at if existing is None else None)
        _stamp(meta, at=at, actor=writer, action=action or ("answered" if existing is None else "answer changed"))
        if clean_tag is not None:
            meta["tag"] = clean_tag
        if edited:
            meta["edited"] = True
        if confirmed_from:
            meta["confirmed_from"] = normalize_question_id(confirmed_from)
        _add_posting(meta, _posting_json(posting, POSTING_CONFIRMED if confirmed_from else POSTING_ANSWERED, at))
        _write_overlay(gig.home_root, gig.target, overlay)
    return result


def _own_entry(gig: _Gig, overlay: Mapping[str, object], profile_id: str, question_id: str) -> PriorAnswer:
    normalized = normalize_question_id(question_id)
    row = _owned_rows(gig, overlay, None).get(profile_id, {}).get(normalized)
    if row is None:
        raise StoryBankError("not_found", f"this profile's story bank has no answer for {normalized!r} (a shared answer is edited in its own profile)")
    return row


def edit_entry(
    *,
    home_root: Path,
    target: Path,
    profile_id: str,
    question_id: str,
    answer: str | None = None,
    question: str | None = None,
    tag: str | None = None,
    names: Iterable[str] = (),
    actor: str | None = None,
    expected_updated_at: str | None = None,
) -> BankEntry:
    """Change an OWN entry's answer, question words and/or tag; the updated entry.

    A new answer or question is a new revision through the answer path; a tag
    is overlay only (``""`` puts the automatic tag back). A shared entry is
    edited in the profile that owns it. ``expected_updated_at``: the
    ``updated_at`` the caller read; ``story_bank_changed`` when it is stale.
    """

    with _WRITE_LOCK:
        return _edit_entry(
            home_root=home_root, target=target, profile_id=profile_id, question_id=question_id, answer=answer,
            question=question, tag=tag, names=names, actor=actor, expected_updated_at=expected_updated_at,
        )


def _edit_entry(
    *, home_root: Path, target: Path, profile_id: str, question_id: str, answer: str | None, question: str | None,
    tag: str | None, names: Iterable[str], actor: str | None, expected_updated_at: str | None,
) -> BankEntry:
    if answer is None and question is None and tag is None:
        raise StoryBankError("invalid_value", "give at least one of answer, question or tag")
    gig = _gig(Path(home_root), Path(target))
    _check_profile(gig, profile_id)
    overlay = _read_overlay(gig.home_root, gig.target)
    row = _own_entry(gig, overlay, profile_id, question_id)
    writer = actor_value(actor)
    clean_tag = None if tag is None else ("" if not tag.strip() else _clean_tag(tag))
    if question is not None and not _clean_question(question):
        raise StoryBankError("invalid_value", "question must not be empty")
    _refuse_stale(home_root, target, profile_id, row.question_id, expected_updated_at)
    if answer is not None or question is not None:
        save_answer(
            home_root=home_root, target=target, profile_id=profile_id, question_id=row.question_id,
            answer=row.answer if answer is None else answer,
            question=question, names=names, edited=True, actor=writer,
            action="answer changed" if answer is not None else "question changed",
        )
    if clean_tag is not None:
        with _LOCK:
            overlay = _read_overlay(gig.home_root, gig.target)
            entries = _profile_block(overlay, profile_id)["entries"]
            assert isinstance(entries, dict)
            meta = entries.setdefault(row.question_id, {})
            meta["tag"] = clean_tag or None
            if answer is None and question is None:
                _stamp(meta, at=_now(), actor=writer, action="tag changed")
            _write_overlay(gig.home_root, gig.target, overlay)
    updated = _current_entry(home_root, target, profile_id, row.question_id)
    assert updated is not None
    return updated


_SLUG_WORDS = 6


def story_id_for(question: str) -> str:
    """The id a new story gets when the caller names none: ``story:<its first words>``."""

    words = [word.strip(".-+#") for word in _WORD.findall(question.lower())]
    kept = [word for word in words if word and word not in _STOPWORDS][:_SLUG_WORDS]
    slug = "_".join(kept)[:100].strip("_")
    if not slug:
        raise StoryBankError("invalid_value", "question must hold at least one word to name the story; or pass question_id")
    return normalize_question_id(f"story:{slug}")


def add_story(
    *,
    home_root: Path,
    target: Path,
    profile_id: str,
    question: str,
    answer: str,
    question_id: str | None = None,
    tag: str | None = None,
    names: Iterable[str] = (),
    actor: str | None = None,
) -> BankEntry:
    """Add a NEW entry nobody asked for yet: a story, or an answer ahead of the question.

    ``question`` is what the story answers ("Tell me about a migration you
    led"); ``question_id`` is optional (``story:<first words>`` otherwise).
    ``story_exists`` (with the entry) when the profile already has that id:
    change it with ``edit_entry``. Raises like ``save_answer``.
    """

    if not _clean_question(question):
        raise StoryBankError("invalid_value", "question must not be empty")
    chosen = normalize_question_id(question_id) if question_id and question_id.strip() else story_id_for(question)
    with _WRITE_LOCK:
        existing = _current_entry(home_root, target, profile_id, chosen)
        if existing is not None:
            raise StoryBankError("story_exists", f"this profile's story bank already has {chosen!r}; edit that entry instead", entry=existing)
        save_answer(
            home_root=home_root, target=target, profile_id=profile_id, question_id=chosen, answer=answer,
            question=question, names=names, actor=actor, tag=tag, action="added",
        )
        created = _current_entry(home_root, target, profile_id, chosen)
    assert created is not None
    return created


def delete_entry(*, home_root: Path, target: Path, profile_id: str, question_id: str, expected_updated_at: str | None = None) -> str:
    """Remove an OWN entry: it is never listed, offered or sent again. The id removed.

    ``expected_updated_at`` as in ``edit_entry``: a stale delete is refused.
    """

    with _WRITE_LOCK:
        gig = _gig(Path(home_root), Path(target))
        _check_profile(gig, profile_id)
        overlay = _read_overlay(gig.home_root, gig.target)
        row = _own_entry(gig, overlay, profile_id, question_id)
        _refuse_stale(home_root, target, profile_id, row.question_id, expected_updated_at)
        experience_answers.remove_answer(
            home_root=gig.home_root, requested_target=gig.target, gig_id=gig.gig_id,
            record_id=row.record_id, question_id=row.question_id,
        )
        with _LOCK:
            overlay = _read_overlay(gig.home_root, gig.target)
            entries = _profile_block(overlay, profile_id)["entries"]
            assert isinstance(entries, dict)
            entries.pop(row.question_id, None)
            _write_overlay(gig.home_root, gig.target, overlay)
        return row.question_id


def record_reuse(*, home_root: Path, target: Path, entries: Iterable[BankEntry], evidence: Iterable[str], posting: Mapping[str, object]) -> list[str]:
    """Note which bank answers an assessment cited, on the posting it assessed.

    The assess prompt asks for ``Story bank <question_id>: ...`` in a row's
    evidence when it reuses a bank answer; every bank id found in
    ``evidence`` gets ``posting`` added as ``reused``. Returns the ids noted.
    Never raises: a failed note must not fail an assessment.
    """

    try:
        by_id = {entry.question_id: entry for entry in entries}
        cited = [question_id for question_id in cited_ids(evidence) if question_id in by_id]
        if not cited:
            return []
        at = _now()
        with _LOCK:
            overlay = _read_overlay(Path(home_root), Path(target))
            for question_id in cited:
                entry = by_id[question_id]
                block_entries = _profile_block(overlay, entry.owner_profile_id)["entries"]
                assert isinstance(block_entries, dict)
                _add_posting(block_entries.setdefault(question_id, {}), _posting_json(posting, POSTING_REUSED, at))
            _write_overlay(Path(home_root), Path(target), overlay)
        return cited
    except Exception:  # noqa: BLE001 - bookkeeping only
        return []


_BANK_CITATION = re.compile(r"story bank\s+([a-z0-9._-]+:[a-z0-9._-]+)")


def cited_ids(evidence: Iterable[str]) -> list[str]:
    """The bank ids a matrix's evidence cites (``Story bank <question_id>: ...``), in order, once each."""

    cited: list[str] = []
    for text in evidence:
        for token in _BANK_CITATION.findall(text.lower()):
            normalized = normalize_question_id(token)
            if normalized not in cited:
                cited.append(normalized)
    return cited


# --- what one assessment reads (quick assess and a find-jobs run alike) ----------------------


@dataclass(frozen=True)
class AssessBank:
    """A profile's bank as ONE assessment reads it.

    Built once by ``assess_bank`` for every path that renders the assess
    prompt (the job page's quick assessment, assess-all, a find-jobs run's
    assess node), so they offer the model the same thing: ``prior_answers``
    (PRIOR ANSWERS, exact ``question_id`` reuse) and ``bank_answers`` (the
    STORY BANK paragraph: at most ``MAX_PROMPT_SUMMARIES`` redacted one-line
    summaries). ``entries`` is the bank itself, for the reuse note and the
    near match. ``marks`` maps every entry's id to an opaque revision mark
    (record ids only, never answer text) and ``digest`` is the digest of that
    map: what a run seals, and what the next run compares against.
    ``record_marks`` is each entry's mark as it was sealed before 0110-041
    (see ``_record_mark``), only so a basis recorded then still compares.
    """

    profile_id: str | None
    entries: tuple[BankEntry, ...] = ()
    prior_answers: tuple[CorePriorAnswer, ...] = ()
    bank_answers: tuple[BankAnswer, ...] = ()
    marks: Mapping[str, str] = field(default_factory=dict)
    record_marks: Mapping[str, str] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        return digest_imported_bytes("".join(f"{key}\t{self.marks[key]}\n" for key in sorted(self.marks)).encode("utf-8"))


def _mark(entry: BankEntry) -> str:
    """An opaque mark that changes when THIS entry is edited, re-answered or comes from another profile.

    0110-041: built from the entry's own write count and time (the overlay's
    ``revision`` / ``updated_at``, bumped by every bank write of this entry
    and by no other), its record id and its owner. Not from the record's
    revision id: a profile's answers share one record, so that id changes
    for EVERY entry when any one of them is written (``_record_mark``), which
    made every cited or nearly matching entry look edited. An answer saved
    before the bank (no overlay entry, write count 0) keeps one mark until
    the bank writes it. No answer text and nothing derived from it.
    """

    written = f"{entry.revision}\n{entry.updated_at or ''}" if entry.revision > 0 else "0\n"
    return digest_imported_bytes(
        f"entry\n{entry.owner_profile_id}\n{entry.record_id}\n{entry.question_id}\n{written}".encode("utf-8")
    )[len("sha256:"):][:16]


def _record_mark(entry: BankEntry) -> str:
    """The mark as sealed before 0110-041: owner, record and the RECORD's revision (shared by all its entries).

    Kept only to compare with a basis recorded then: such a basis reads as
    unchanged while the record is, and after the next write of that record
    every entry of it reads as changed to it (it cannot say which one was).
    """

    return digest_imported_bytes(f"{entry.owner_profile_id}\n{entry.record_id}\n{entry.revision_id}".encode("utf-8"))[len("sha256:"):][:16]


def assess_bank(*, home_root: Path, target: Path, profile_id: str | None, resume_text: str = "") -> AssessBank:
    """The bank ``profile_id`` reads for one assessment. Never raises.

    ``None`` (no profile), an unknown profile, no gig or an unreadable bank
    read as an empty bank: the prompt then renders as it did before the bank
    existed. ``resume_text``'s own name line, when it has one, has its words
    removed from every line a model is offered (as ``model_resume`` removes
    them from the resume); contact details are always redacted.
    """

    if profile_id is None:
        return AssessBank(None)
    first_line = next((line.strip() for line in resume_text.splitlines() if line.strip()), "")
    names = (first_line,) if is_name_line(first_line) else ()
    try:
        entries = read_bank(home_root=home_root, target=target, profile_id=profile_id, with_postings=False)
    except (StoryBankError, PrivateRecordError):
        return AssessBank(profile_id)
    except Exception:  # noqa: BLE001 - a bank that cannot be read answers nothing; it never fails an assessment
        return AssessBank(profile_id)
    return AssessBank(
        profile_id=profile_id,
        entries=entries,
        prior_answers=tuple(
            CorePriorAnswer(question_id=entry.question_id, prompt=entry.question, answer=_redacted(entry.answer, names)) for entry in entries
        ),
        bank_answers=tuple(
            BankAnswer(question_id=item.question_id, question=item.question, summary=item.summary)
            for item in prompt_summaries(entries, names=names)
        ),
        marks={entry.question_id: _mark(entry) for entry in entries},
        record_marks={entry.question_id: _record_mark(entry) for entry in entries},
    )


#: ``BankMatch.match``: how a changed bank entry concerns one assessment.
MATCH_EXACT = "exact"
MATCH_NEAR = "near"
MATCH_CITED = "cited"


@dataclass(frozen=True)
class BankMatch:
    """Why the bank makes ONE assessment stale: a changed entry that concerns it.

    ``exact`` / ``near``: ``bank_question_id`` was added or edited since the
    assessment's basis and answers its open ``question_id`` (the same id, or
    ``near_match``). ``cited``: the assessment's evidence cites
    ``bank_question_id`` and that entry was edited, deleted or is no longer
    visible (``question_id`` is then ``None``; ``bank_question`` is empty when
    the entry is gone). Ids and question words only, never an answer.
    """

    match: str
    bank_question_id: str
    bank_question: str = ""
    question_id: str | None = None
    question: str = ""

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {"match": self.match, "bank_question_id": self.bank_question_id}
        if self.bank_question:
            value["bank_question"] = self.bank_question
        if self.question_id is not None:
            value["question_id"] = self.question_id
            if self.question:
                value["question"] = self.question
        return value


def changed_entries(sealed_marks: Mapping[str, str] | None, bank: AssessBank) -> tuple[BankEntry, ...]:
    """The entries of ``bank`` an assessment with ``sealed_marks`` never saw as they are now (added, or edited since)."""

    sealed = sealed_marks or {}
    return tuple(entry for entry in bank.entries if not _as_sealed(sealed, bank, entry.question_id))


def _as_sealed(sealed: Mapping[str, str], bank: AssessBank, question_id: str) -> bool:
    """Whether the bank's entry ``question_id`` is the one ``sealed`` recorded (by either mark; both absent: nothing changed)."""

    mark = sealed.get(question_id)
    if mark is None:
        return question_id not in bank.marks
    return mark == bank.marks.get(question_id) or mark == bank.record_marks.get(question_id)


def bank_matches(
    *,
    questions: Iterable[object],
    evidence: Iterable[str],
    sealed_marks: Mapping[str, str] | None,
    bank: AssessBank,
) -> tuple[BankMatch, ...]:
    """The changed bank entries that concern ONE assessment (0110-041); empty: the bank leaves it current.

    ``questions`` are the questions it left open (anything with a
    ``question_id`` and a ``question``, e.g. ``AssessmentQuestion``),
    ``evidence`` its matrix evidence, ``sealed_marks`` the bank it was made
    with (``None``: made before the bank was recorded, read as an empty bank)
    and ``bank`` the bank now. Targeted: answering one question concerns the
    assessments that asked it, not every assessment that asked anything.

    - An entry ADDED or EDITED since (``changed_entries``) that answers one of
      ITS OWN open questions: the same id (``exact``), or the model-free
      ``near_match`` behind ``bank_suggestions`` (``near``).
    - It cites ``Story bank <id>`` and that entry was edited, deleted or is no
      longer visible (sharing turned off): ``cited``.

    An assessment with no open question that cites nothing is never stale,
    neither is any assessment while the bank is unchanged (a model that saw
    the bank and still asked is not asked again until an entry that concerns
    its question changes), and neither is one whose questions the changed
    entries do not answer. In memory: changed entries x open questions, no
    read.
    """

    sealed = sealed_marks or {}
    found: list[BankMatch] = []
    asked = [(normalize_question_id(str(getattr(item, "question_id", "") or "")), str(getattr(item, "question", "") or "")) for item in questions]
    if asked:
        for entry in changed_entries(sealed, bank):
            for question_id, question in asked:
                if not question_id:
                    continue
                if entry.question_id == question_id:
                    kind = MATCH_EXACT
                elif near_match((entry,), question_id=question_id, question=question) is not None:
                    kind = MATCH_NEAR
                else:
                    continue
                found.append(BankMatch(kind, entry.question_id, _shown_question(entry), question_id, question))
    by_id: dict[str, BankEntry] | None = None
    for cited in cited_ids(evidence):
        if _as_sealed(sealed, bank, cited):
            continue
        if by_id is None:
            by_id = {entry.question_id: entry for entry in bank.entries}
        entry = by_id.get(cited)
        found.append(BankMatch(MATCH_CITED, cited, "" if entry is None else _shown_question(entry)))
    return tuple(found)


def _shown_question(entry: BankEntry) -> str:
    """The entry's question words for a reason line ("answered in your story bank: ..."): contact details out, one line."""

    return one_line(_redacted(entry.question or entry.question_id), _MAX_SUMMARY_QUESTION_CHARS)


def bank_makes_stale(
    *,
    questions: Iterable[object],
    evidence: Iterable[str],
    sealed_marks: Mapping[str, str] | None,
    bank: AssessBank,
) -> bool:
    """Whether an assessment must be made again because the bank changed: ``bank_matches`` found something.

    One rule for a stored assessment (``assessment_basis``) and a run's
    unchanged skip (``proposal_execution._basis_stale``).
    """

    return bool(bank_matches(questions=questions, evidence=evidence, sealed_marks=sealed_marks, bank=bank))


# --- reuse: the prompt summaries and the near match ------------------------------------------


@dataclass(frozen=True)
class BankSummary:
    """One bank entry as the assess prompt gets it: id, question, one-line answer."""

    question_id: str
    question: str
    summary: str


def _redacted(text: str, names: Iterable[str] = ()) -> str:
    """``text`` as a model may see it: contact details out (``redact_inline``), name words out (``guard_name``)."""

    tokens = {word.lower() for name in names if isinstance(name, str) for word in re.findall(r"[^\W\d_]+", name) if len(word) > 1}
    out = "\n".join(redact_inline(line) for line in text.split("\n"))
    return guard_name(out, tokens) if tokens else out


def one_line(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    """``text`` on one line, cut at a word boundary with ``...`` when over ``limit``."""

    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    cut = flat[: limit - 3].rsplit(" ", 1)[0] or flat[: limit - 3]
    return cut.rstrip(" ,;:") + "..."


def prompt_summaries(entries: Iterable[BankEntry], *, names: Iterable[str] = (), limit: int = MAX_PROMPT_SUMMARIES) -> tuple[BankSummary, ...]:
    """What the assess prompt is offered: at most ``limit`` entries, privacy-checked.

    Each answer is redacted (contact details, and the words of ``names``)
    and cut to one line; an entry whose line still shows a contact detail is
    left out. Own entries first, then newest.
    """

    names = tuple(names)
    ordered = sorted(entries, key=lambda entry: (entry.shared, -_sort_time(entry.updated_at), entry.question_id))
    out: list[BankSummary] = []
    for entry in ordered:
        if len(out) >= limit:
            break
        summary = one_line(_redacted(entry.answer, names))
        question = "" if entry.question == entry.question_id else one_line(_redacted(entry.question, names), _MAX_SUMMARY_QUESTION_CHARS)
        if not summary or personal_info_in_answer(f"{question} {summary}"):
            continue
        out.append(BankSummary(entry.question_id, question, summary))
    return tuple(out)


def _sort_time(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


_STOPWORDS = frozenset(
    "a an and any are as at be been being but by can could did do does for from had has have how i if in into is it its "
    "many me much my of on or our role so some than that the their them then there these they this those to us was we "
    "were what when where which while who whom why will with would you your experience experienced hands worked working "
    "work used using use familiar knowledge know background years year professional production prior please describe "
    "tell about other".split()
)
#: One word for one thing, so two wordings of it overlap.
_WORD_ALIASES: dict[str, str] = {
    "k8s": "kubernetes", "postgres": "postgresql", "golang": "go", "javascript": "js", "typescript": "ts",
    "node.js": "node", "nodejs": "node",
}
#: A product named by several words (in any order, as a normalized id sorts them) -> its one token.
_WORD_SETS: tuple[tuple[frozenset[str], frozenset[str], str], ...] = (
    (frozenset({"google", "cloud"}), frozenset({"platform"}), "gcp"),
    (frozenset({"amazon", "web", "service"}), frozenset(), "aws"),
    (frozenset({"microsoft", "azure"}), frozenset(), "azure"),
    (frozenset({"machine", "learning"}), frozenset(), "ml"),
    (frozenset({"continuous", "integration"}), frozenset(), "cicd"),
)
_FAMILY: dict[str, str] = {
    **{name: "technical" for name in ("cloud", "language", "framework", "tooling", "tool", "database", "skill", "pattern", "other")},
    **{name: "level" for name in ("years", "seniority")},
    **{name: "domain" for name in ("domain", "industry")},
}


@lru_cache(maxsize=4096)
def _tokens(text: str) -> frozenset[str]:
    flat = text.lower().replace("ci/cd", "cicd")
    for separator in "_:/-":
        flat = flat.replace(separator, " ")
    out: set[str] = set()
    for word in _WORD.findall(flat):
        word = _WORD_ALIASES.get(word.strip("."), word.strip("."))
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        if word and word not in _STOPWORDS:
            out.add(word)
    for needed, also_dropped, token in _WORD_SETS:
        if needed <= out:
            out = (out - needed - also_dropped) | {token}
    return frozenset(out)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _id_parts(question_id: str) -> tuple[str, frozenset[str]]:
    normalized = normalize_question_id(question_id)
    category, _, value = normalized.partition(":")
    return (category, _tokens(value)) if value else ("", _tokens(normalized))


def similarity(question_id: str, question: str, entry: BankEntry) -> float:
    """How alike a new question and a bank entry are: 0..1, model-free.

    The larger of two overlaps (Jaccard over word tokens, stop words and
    plurals removed, a few product names unified): the two ids' value parts
    (scaled by 0.8 when their categories are not of one family: ``cloud`` and
    ``tooling`` are, ``years`` and ``language`` are not), and everything known
    about each question (id value plus its words). 1.0 for the same id.
    """

    if normalize_question_id(question_id) == entry.question_id:
        return 1.0
    category, value = _id_parts(question_id)
    entry_category, entry_value = _id_parts(entry.question_id)
    same_family = category == entry_category or (_FAMILY.get(category) is not None and _FAMILY.get(category) == _FAMILY.get(entry_category))
    id_score = _jaccard(value, entry_value) * (1.0 if same_family else 0.8)
    entry_words = entry_value | (_tokens(entry.question) if entry.question != entry.question_id else frozenset())
    text_score = _jaccard(value | _tokens(question), entry_words)
    return round(max(id_score, text_score), 3)


@dataclass(frozen=True)
class BankSuggestion:
    """A near match: "We already know: <answer>, use it?"."""

    question_id: str
    bank_question_id: str
    bank_question: str
    answer: str
    score: float
    owner_profile_id: str
    shared: bool

    def to_json(self) -> dict[str, object]:
        return {
            "question_id": self.question_id,
            "bank_question_id": self.bank_question_id,
            "bank_question": self.bank_question,
            "answer": self.answer,
            "score": self.score,
            "owner_profile_id": self.owner_profile_id,
            "shared": self.shared,
        }


def near_match(entries: Iterable[BankEntry], *, question_id: str, question: str = "") -> BankSuggestion | None:
    """The bank entry closest to this question, when it is close enough.

    ``None`` when the bank already answers this exact id (that is plain
    reuse, not a suggestion) or nothing scores ``NEAR_MATCH_THRESHOLD``.
    """

    normalized = normalize_question_id(question_id)
    best: tuple[float, BankEntry] | None = None
    for entry in entries:
        if entry.question_id == normalized:
            return None
        score = similarity(normalized, question, entry)
        if score >= NEAR_MATCH_THRESHOLD and (best is None or (score, not entry.shared) > (best[0], not best[1].shared)):
            best = (score, entry)
    if best is None:
        return None
    score, entry = best
    return BankSuggestion(normalized, entry.question_id, entry.question, entry.answer, score, entry.owner_profile_id, entry.shared)


def suggestions_for(questions: Iterable[Mapping[str, object]], entries: Iterable[BankEntry]) -> list[dict[str, object]]:
    """One near match per open question that has one (``structured_questions`` items)."""

    bank = tuple(entries)
    out: list[dict[str, object]] = []
    for item in questions:
        question_id = item.get("question_id")
        if not isinstance(question_id, str):
            continue
        match = near_match(bank, question_id=question_id, question=str(item.get("question") or ""))
        if match is not None:
            out.append(match.to_json())
    return out


def attach_suggestions(payload: dict[str, object], *, home_root: Path, target: Path, cache: dict[str, tuple[BankEntry, ...]] | None = None) -> dict[str, object]:
    """Add ``bank_suggestions`` to one assess-response JSON, in place; returns it.

    Computed when the response is read, never stored: a deleted answer or a
    sharing that was turned off stops showing at once. Only added when a
    question has a near match; a pasted resume (no profile) has no bank.
    ``cache`` (profile id -> bank) lets a list read each bank once.
    """

    try:
        result = payload.get("result")
        resume = payload.get("resume")
        questions = result.get("structured_questions") if isinstance(result, dict) else None
        profile_id = resume.get("profile_id") if isinstance(resume, dict) else None
        if not questions or not isinstance(profile_id, str):
            return payload
        bank = None if cache is None else cache.get(profile_id)
        if bank is None:
            bank = read_bank(home_root=home_root, target=target, profile_id=profile_id, with_postings=False)
            if cache is not None:
                cache[profile_id] = bank
        found = suggestions_for(questions, bank)
        if found:
            payload["bank_suggestions"] = found
    except (StoryBankError, PrivateRecordError):
        pass
    return payload


__all__ = [
    "ACTORS",
    "ACTOR_AGENT",
    "ACTOR_OPERATOR",
    "MAX_PROMPT_SUMMARIES",
    "MAX_SUMMARY_CHARS",
    "NEAR_MATCH_THRESHOLD",
    "SCHEMA_VERSION",
    "BankEntry",
    "BankMatch",
    "BankPosting",
    "BankSuggestion",
    "AssessBank",
    "BankSummary",
    "StoryBankError",
    "actor_value",
    "add_story",
    "answers_for_profile",
    "assess_bank",
    "attach_suggestions",
    "bank_makes_stale",
    "bank_matches",
    "bank_path",
    "changed_entries",
    "cited_ids",
    "delete_entry",
    "edit_entry",
    "near_match",
    "one_line",
    "personal_info_in_answer",
    "prompt_summaries",
    "read_bank",
    "reader_profile_id",
    "record_reuse",
    "save_answer",
    "set_sharing",
    "sharing",
    "similarity",
    "story_id_for",
    "suggestions_for",
    "tag_for",
]
