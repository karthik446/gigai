"""uat-bug-018: a job's STATE, derived read-side from the stores that exist.

Nothing here is stored: no new file, no schema, record or identity change.
A job's state is recomputed on every read from three existing sources:

1. application events -- core's committed ``records/applications/events/``
   (``gigai.application_events``), joined on the event's one posting
   identity (``external_ref``, or ``opportunity_ref`` for a Discover event);
2. the stored tailored resume for the job and the resume identity
   (``tailored_resume``'s store, ``resumes/<profile_id|ephemeral>/``);
3. the latest assessment: a run's own (or carried-forward) assessment of
   the posting, and the quick-assess store's item with its verdict history
   (``quick_assess``'s store, ``quick_assess/<profile_id|ephemeral>/``).

Precedence, highest first -- the first source that says anything wins:

    application events  >  tailored resume  >  latest verdict  >  not_assessed

So a job with a tailored resume reads ``tailored`` whatever its verdict is,
and a job marked applied reads ``applied`` (or what followed) whatever else
is stored for it.

States::

    not_assessed
    needs_answers | matched | not_a_match | assessed   (the latest verdict)
    has_gap                                            (0.1.11, below)
    weak_fit                                           (0110-10-02, below)
    thin_posting                                       (0.1.11.2, below)
    tailored
    applied -> interview_scheduled -> offer_received | rejected | withdrawn

``assessed`` is an assessment whose result carries no verdict (a result
shape from before the verdict existed); it is never produced by a current
assessment.

``has_gap`` (0.1.11 N3, OD1; ``scout/resume_gate.py``) is ``matched`` for a job
whose v9 assessment has a must-have that is confirmed unmet and does not
disqualify (an ``askable`` row ``unmet``): the stored gate decision is
``hold_unmet``. The model's verdict stays stored as it came; no resume is
suggested until the user asks for a draft. Only an assessment that stores a
gate can say it, so a job assessed before 0.1.11 never reads ``has_gap``.

``weak_fit`` (0110-10-02, ``scout/fit.py``) is ``needs_answers`` for a job
whose stored assessment has few requirements met AND whose rank score is
low: it waits on answers that could hardly make it a match, so it is not
counted with the jobs that need the user's answers. Only
:class:`JobStateSources` says it (it knows the profile's rank score, from
the posting read model); the pure :func:`derive_job_state` never does.

``thin_posting`` (0.1.11.2, ``scout/fit.py``) is ``matched`` for a job whose
stored assessment has NO matrix row about the job (an empty matrix, a lone
"No stated requirements" row, eligibility rows alone;
``AssessmentFact.real_rows`` is 0): nothing was judged, so it is never shown
or counted as a match. A matched verdict whose stored gate decision is
``not_a_match`` reads ``not_a_match``.

The application state is the LATEST active event of the job, in core's own
order (``occurred_at``, then when it was recorded): an event another event
``supersedes`` is a corrected one and is not active, and a ``saved`` event
is a bookmark, never a state. ``since`` is that event's ``occurred_at``.

The job identity is find-jobs' own: a posting's ``normalized_url``, or
``text:sha256:<hex>`` for a posting whose text was pasted
(``assess_contracts.text_identity``).

:func:`derive_job_state` and :func:`check_transition` are pure.
:class:`JobStateSources` is the one place the stores are read; its journal
read is ``read_committed_snapshot`` scoped to the events family, which
takes no writer lock.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re

from ...canonical import digest_imported_bytes, parse_json_bytes
from ...application_event_index import ApplicationEventIndexUnavailable, committed_application_events
from ...journal import JournalError, read_committed_snapshot
from .assess_contracts import AssessResponse
from .ats_board_clients import posting_content_digest
from .contracts import Verdict, normalize_url

NOT_ASSESSED = "not_assessed"
ASSESSED = "assessed"
NEEDS_ANSWERS = "needs_answers"
WEAK_FIT = "weak_fit"
#: 0.1.11.2: matched by verdict on NO row about the job (``scout/fit.py``): "thin posting", never a match.
THIN_POSTING = "thin_posting"
MATCHED = "matched"
#: 0.1.11 N3 (OD1): matched by verdict, held by the gate: a must-have is confirmed unmet ("Has a gap").
HAS_GAP = "has_gap"
NOT_A_MATCH = "not_a_match"
TAILORED = "tailored"
APPLIED = "applied"
INTERVIEW_SCHEDULED = "interview_scheduled"
OFFER_RECEIVED = "offer_received"
REJECTED = "rejected"
WITHDRAWN = "withdrawn"

#: Every state, in pipeline order.
JOB_STATES: tuple[str, ...] = (
    NOT_ASSESSED,
    ASSESSED,
    NEEDS_ANSWERS,
    WEAK_FIT,
    THIN_POSTING,
    MATCHED,
    NOT_A_MATCH,
    TAILORED,
    APPLIED,
    INTERVIEW_SCHEDULED,
    OFFER_RECEIVED,
    REJECTED,
    WITHDRAWN,
)

#: 0.1.11 N3: states only a v9 assessment can put a job in. Served and accepted like any other
#: (``derive_job_state``, ``next_events``); kept beside ``JOB_STATES`` until the page has their words.
GATE_STATES: tuple[str, ...] = (HAS_GAP,)

#: "Applied and beyond": the states an application event puts a job in. Each
#: is named after the event kind that causes it.
APPLICATION_STATES: tuple[str, ...] = (APPLIED, INTERVIEW_SCHEDULED, OFFER_RECEIVED, REJECTED, WITHDRAWN)

#: The event kind that never changes a job's state.
SAVED = "saved"

_VERDICT_STATES: dict[str, str] = {
    Verdict.PENDING_USER_ANSWERS.value: NEEDS_ANSWERS,
    Verdict.MATCHED_ABOVE_THRESHOLD.value: MATCHED,
    Verdict.NOT_A_MATCH.value: NOT_A_MATCH,
}

# The state-changing event kinds a job accepts next, by its current state.
# A job that has not been applied to accepts ``applied`` only (no interview
# before applied); ``rejected`` and ``withdrawn`` end the pipeline.
_NEXT_EVENTS: dict[str, tuple[str, ...]] = {
    **{state: (APPLIED,) for state in (*JOB_STATES, *GATE_STATES) if state not in APPLICATION_STATES},
    APPLIED: (INTERVIEW_SCHEDULED, OFFER_RECEIVED, REJECTED, WITHDRAWN),
    INTERVIEW_SCHEDULED: (OFFER_RECEIVED, REJECTED, WITHDRAWN),
    OFFER_RECEIVED: (REJECTED, WITHDRAWN),
    REJECTED: (),
    WITHDRAWN: (),
}

_TEXT_IDENTITY = re.compile(r"\Atext:sha256:[0-9a-f]{64}\Z")
#: ``GateRecord.decision`` behind :data:`HAS_GAP` (``assess_contracts.GATE_HOLD_UNMET``).
_GATE_HOLD_UNMET = "hold_unmet"
_GATE_NOT_A_MATCH = "not_a_match"
_EVENTS_PREFIX = "records/applications/events/"


class JobStateError(ValueError):
    """An event the job's current state does not accept; ``code`` is the API error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


#: ``assessment_stale.reason``: the posting's text is not the text the assessment was made on.
STALE_POSTING_CHANGED = "posting_changed"


@dataclass(frozen=True)
class JobState:
    """One job's derived state, as the API serves it.

    ``assessment_stale`` (ledger 32, additive) is ``"posting_changed"`` when the
    state comes from a stored assessment whose posting text has since changed:
    the verdict still reads, and the job should be re-assessed. ``None`` (and
    absent from the JSON) whenever nothing says so, which includes every
    assessment stored before the digest was comparable.

    0110-039: the same marker carries ``older_prompt``, ``settings_changed``
    or ``story_bank_changed`` (``assessment_basis``) when the state comes from
    a stored quick assessment made with an older prompt, other candidate
    settings or a story bank that has since changed. A changed posting text
    is named first. 0.1.10.9 master P7: and ``resume_changed`` when a master
    resume is stored and the resume changed in a way that concerns it.
    """

    state: str
    since: str | None
    next_events: tuple[str, ...]
    assessment_stale: str | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {"state": self.state, "since": self.since, "next_events": list(self.next_events)}
        if self.assessment_stale is not None:
            value["assessment_stale"] = {"reason": self.assessment_stale}
        return value


@dataclass(frozen=True)
class AssessmentFact:
    """One assessment of a job: when it ran and its verdict.

    ``verdict`` is a :class:`Verdict` value, or ``None`` for a result that
    carries no verdict. ``since`` is when the job first got this verdict
    without a different one in between (the quick store's history); ``None``
    means ``at``.
    """

    at: str | None
    verdict: str | None
    since: str | None = None
    #: ``PostingRow.content_sha256`` the assessment was made on; ``None`` when unknown.
    content_sha256: str | None = None
    #: 0110-039: why its basis is not the profile's now (``assessment_basis``); ``None`` when current or unknown.
    basis_stale: str | None = None
    #: 0.1.11 N3: the gate decision stored with a v9 assessment (``resume_gate``); ``None`` for every other one.
    gate: str | None = None
    #: 0.1.11.2: how many matrix rows are about the job (``assessment_core.requirement_row_count``); ``None``: not known.
    real_rows: int | None = None


def next_events(state: str) -> tuple[str, ...]:
    """The state-changing event kinds a job in ``state`` accepts next."""

    return _NEXT_EVENTS.get(state, ())


def _instant(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


_EPOCH = datetime.min.replace(tzinfo=UTC)


def _event_order(event: Mapping[str, object]) -> tuple[datetime, str, str]:
    return (
        _instant(event.get("occurred_at")) or _EPOCH,
        str(event.get("recorded_at") or ""),
        str(event.get("event_id") or ""),
    )


def current_application_event(events: Iterable[Mapping[str, object]]) -> Mapping[str, object] | None:
    """The event that decides one job's application state, or ``None``.

    ``events`` are one job's events. Superseded events and ``saved`` events
    are left out; of the rest, the latest in core's order wins.
    """

    group = [event for event in events if isinstance(event, Mapping)]
    superseded = {event.get("supersedes") for event in group if event.get("supersedes")}
    active = [
        event
        for event in group
        if event.get("event_id") not in superseded and event.get("event_kind") in APPLICATION_STATES
    ]
    if not active:
        return None
    return max(active, key=_event_order)


def application_state(events: Iterable[Mapping[str, object]]) -> JobState | None:
    """One job's state from its application events alone, or ``None`` when
    no event puts it in an application state."""

    event = current_application_event(events)
    if event is None:
        return None
    state = str(event["event_kind"])
    since = event.get("occurred_at")
    return JobState(state, since if isinstance(since, str) else None, next_events(state))


def latest_assessment(assessments: Iterable[AssessmentFact | None]) -> AssessmentFact | None:
    """The newest of ``assessments``; on a tie the LATER item in the
    iterable wins, so a caller lists the run's assessment before the quick
    store's (a job-page re-assessment at the run's own instant is newer)."""

    latest: AssessmentFact | None = None
    for fact in assessments:
        if fact is None:
            continue
        if latest is None or (_instant(fact.at) or _EPOCH) >= (_instant(latest.at) or _EPOCH):
            latest = fact
    return latest


def derive_job_state(
    *,
    events: Iterable[Mapping[str, object]] = (),
    tailored_at: str | None = None,
    has_tailored_resume: bool = False,
    assessments: Iterable[AssessmentFact | None] = (),
    current_content_sha256: str | None = None,
) -> JobState:
    """One job's state; see the module docstring for the precedence.

    ``events`` are this job's application events; ``has_tailored_resume``
    (or a ``tailored_at``) says a tailored resume is stored for the job and
    the resume identity, ``tailored_at`` when it was first written;
    ``assessments`` are the assessments known for it, in any order.
    ``current_content_sha256`` is the posting's stored content digest now; an
    assessment made on a different, known digest marks the state stale.
    """

    applied = application_state(events)
    if applied is not None:
        return applied
    if has_tailored_resume or tailored_at is not None:
        return JobState(TAILORED, tailored_at, next_events(TAILORED))
    assessment = latest_assessment(assessments)
    if assessment is None:
        return JobState(NOT_ASSESSED, None, next_events(NOT_ASSESSED))
    state = ASSESSED if assessment.verdict is None else _VERDICT_STATES.get(str(assessment.verdict), ASSESSED)
    if state == MATCHED and assessment.gate == _GATE_HOLD_UNMET:
        state = HAS_GAP  # 0.1.11 N3 (OD1): the gate, not the verdict, is what every reader uses
    elif state == MATCHED and assessment.gate == _GATE_NOT_A_MATCH:
        state = NOT_A_MATCH  # 0.1.11.2, defensive: a matched verdict the gate refused never reads as a match
    elif state == MATCHED and assessment.real_rows == 0:
        state = THIN_POSTING  # 0.1.11.2: a match on no requirement at all is not shown as one
    stale = None
    if (
        current_content_sha256
        and assessment.content_sha256
        and assessment.content_sha256 != current_content_sha256
    ):
        stale = STALE_POSTING_CHANGED
    return JobState(state, assessment.since or assessment.at, next_events(state), stale or assessment.basis_stale)


def check_transition(
    *,
    events: Iterable[Mapping[str, object]],
    event_kind: str,
    occurred_at: str | None = None,
) -> None:
    """Refuse an event the job's current application state does not accept.

    ``events`` are the job's events so far. ``saved`` is accepted until the
    job is applied to and never changes its state. Every other kind must be
    one of the current state's next events, and must not be dated before
    the event that put the job in its current state (the state is the
    latest event by date, so an earlier one would be recorded and ignored).
    Raises :class:`JobStateError`, code ``application_transition_refused``
    or ``application_event_out_of_order``.
    """

    current = application_state(events)
    state_words = NOT_ASSESSED if current is None else current.state
    allowed = (APPLIED,) if current is None else current.next_events
    if event_kind == SAVED:
        if current is None:
            return
        raise JobStateError(
            "application_transition_refused",
            f"this job is already {state_words}; it cannot be saved",
        )
    if event_kind not in allowed:
        if current is None:
            detail = "this job has not been marked applied yet"
        elif allowed:
            detail = f"this job is {state_words}"
        else:
            detail = f"this job is {state_words}, which ends its pipeline"
        accepted = ", ".join(allowed) if allowed else "none"
        raise JobStateError(
            "application_transition_refused",
            f"{detail}; {event_kind} is not accepted (accepted next: {accepted})",
        )
    if current is None or occurred_at is None:
        return
    new_at, current_at = _instant(occurred_at), _instant(current.since)
    if new_at is not None and current_at is not None and new_at < current_at:
        raise JobStateError(
            "application_event_out_of_order",
            f"occurred_at {occurred_at} is before this job became {state_words} ({current.since})",
        )


# --- identities ------------------------------------------------------------------------


def is_text_identity(value: object) -> bool:
    """True for a pasted posting's identity, ``text:sha256:<64 hex>`` exactly."""

    return isinstance(value, str) and _TEXT_IDENTITY.fullmatch(value) is not None


def normalize_job_identity(value: str) -> str:
    """A job identity as the stores and the application events key it.

    A pasted posting's ``text:sha256:<64 hex>`` is returned as it is;
    anything else must be a posting URL and goes through ``normalize_url``
    (which raises ``FindJobsContractError`` for what is not one).
    """

    if is_text_identity(value):
        return value
    return normalize_url(value)


def event_identity(event: Mapping[str, object]) -> str | None:
    """An application event's one posting identity (see core's ``_event_ref``)."""

    value = event.get("opportunity_ref") if "opportunity_ref" in event else event.get("external_ref")
    return value if isinstance(value, str) and value else None


def group_events(events: Iterable[Mapping[str, object]]) -> dict[str, list[Mapping[str, object]]]:
    """``events`` by posting identity; an event with no identity is left out."""

    grouped: dict[str, list[Mapping[str, object]]] = {}
    for event in events:
        if not isinstance(event, Mapping):
            continue
        identity = event_identity(event)
        if identity is not None:
            grouped.setdefault(identity, []).append(event)
    return grouped


# --- the quick store's item as a fact --------------------------------------------------


def quick_assessment_fact(item: AssessResponse, *, basis_stale: str | None = None) -> AssessmentFact:
    """The quick store's item as an :class:`AssessmentFact`.

    ``content_sha256`` is recomputed the way an ATS board row hashes itself
    (title + text) from the text the item stored, and only for a posting read
    from an ATS board row: any other fetch reads its text differently from a
    run's row, so its digest would never match and would only raise a false
    alarm. An item without stored text (an older record) carries none.

    ``since`` comes from the item's verdict history: the oldest entry of the
    unbroken run of entries, newest first, that carry the current verdict.
    """

    verdict = None if item.result.verdict is None else item.result.verdict.value
    at = item.updated_at or item.created_at
    since = at
    for entry in reversed(item.history):
        entry_verdict = None if entry.verdict is None else entry.verdict.value
        if entry_verdict != verdict:
            break
        since = entry.at
    content = None
    if item.job.fetch_kind == "ats_board" and item.posting_text:
        content = posting_content_digest(item.job.title, item.posting_text)
    gate = None if item.resume_gate is None else item.resume_gate.decision
    from ..assessment_core import requirement_row_count

    return AssessmentFact(
        at=at, verdict=verdict, since=since, content_sha256=content, basis_stale=basis_stale, gate=gate,
        real_rows=requirement_row_count(item.result),
    )


# --- reading the stores ----------------------------------------------------------------


def read_application_events(resolved: object) -> dict[str, list[Mapping[str, object]]]:
    """Every committed application event of this gig, by posting identity.

    One committed read scoped to the events family, without the journal
    writer lock. An event file that is not a JSON object is left out: this
    is a display read, and ``GET /api/applications`` (the projection) is
    where an event is validated against its hashes.
    """

    # 0110-036: the events kept at the journal head (proven and validated
    # once, caught up by the commits since) when they can answer; the
    # snapshot below otherwise, as before.
    try:
        kept = committed_application_events(resolved)
    except (ApplicationEventIndexUnavailable, JournalError):
        kept = None
    if kept is not None:
        return group_events(dict(event) for event in kept.events.values())  # in path order, as below
    snapshot = read_committed_snapshot(
        workpad=resolved.path,  # type: ignore[attr-defined]
        project_id=resolved.project_id,  # type: ignore[attr-defined]
        gig_id=resolved.gig_id,  # type: ignore[attr-defined]
        prefixes=(_EVENTS_PREFIX,),
    )
    events: list[Mapping[str, object]] = []
    for path in sorted(snapshot.artifacts):
        if not (path.startswith(_EVENTS_PREFIX) and path.endswith(".json")):
            continue
        try:
            value = parse_json_bytes(snapshot.artifacts[path])
        except ValueError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return group_events(events)


def _identity_digest(job_identity: str) -> str:
    return digest_imported_bytes(job_identity.encode("utf-8")).removeprefix("sha256:")


def _stored_names(directory: Path) -> frozenset[str]:
    try:
        return frozenset(path.stem for path in directory.glob("*.json"))
    except OSError:
        return frozenset()


class JobStateSources:
    """One request's view of the three stores, read at most once each.

    ``events`` is every application event by identity (pass the rows a route
    has already read, or leave it out to read them lock-free on first use).
    The quick-assess and tailored-resume stores are read per job, by the
    file name the store itself derives from the identity, so a request pays
    for the jobs it serves and not for the whole store.
    """

    def __init__(
        self,
        *,
        home_root: Path,
        target: Path,
        resolved: object | None = None,
        events: Mapping[str, list[Mapping[str, object]]] | None = None,
    ) -> None:
        self._home_root = home_root
        self._target = target
        self._resolved = resolved
        self._events = events
        self._roots: dict[str, Path | None] = {}
        self._names: dict[Path, frozenset[str]] = {}
        self._basis: object | None = None
        #: 0110-10-02: job -> its BEST rank score among the roles that tag it (0.1.11.9) and the fit setting, read once
        #: on the first needs-answers job.
        self._ranks: dict[str, int] | None = None
        self._fit: object | None = None

    @property
    def basis(self):
        """0110-039: this request's ``assessment_basis.BasisCheck`` (settings read once per resume identity)."""

        if self._basis is None:
            from ..assessment_basis import BasisCheck

            self._basis = BasisCheck(home_root=self._home_root, target=self._target, resolved=self._resolved)
        return self._basis

    def events_for(self, job_identity: str) -> list[Mapping[str, object]]:
        if self._events is None:
            self._events = {} if self._resolved is None else read_application_events(self._resolved)
        return list(self._events.get(job_identity, ()))

    def _store(self, kind: str, resume: str) -> Path | None:
        if kind not in self._roots:
            try:
                if kind == "quick_assess":
                    from ..quick_assess import quick_assess_dir

                    self._roots[kind] = quick_assess_dir(self._home_root, self._target)
                else:
                    from ..tailored_resume import tailored_resume_dir

                    self._roots[kind] = tailored_resume_dir(self._home_root, self._target)
            except Exception:  # noqa: BLE001 - an unbound folder has no store; that is "nothing stored"
                self._roots[kind] = None
        root = self._roots[kind]
        return None if root is None else root / resume

    def _stored_file(self, kind: str, profile_id: str | None, job_identity: str) -> Path | None:
        """The stored file of this job: the JOB's own (0.1.11.9: ``<store>/job/``, whichever role ``profile_id``
        names), a pasted resume's for ``None``.  On a home whose stores were not migrated, a job nothing has written
        since is still in a role's folder: the store's own path rule says which one (``job_store_migration``)."""

        from ..job_store_layout import JOB_FOLDER, is_profile_folder
        from ..quick_assess import is_pasted, resume_key

        pasted = is_pasted(profile_id)
        directory = self._store(kind, resume_key(None) if pasted else JOB_FOLDER)
        if directory is None:
            return None
        if directory not in self._names:
            self._names[directory] = _stored_names(directory)
        digest = _identity_digest(job_identity)
        path = directory / f"{digest}.json"
        if digest not in self._names[directory]:
            if pasted:
                return None
            # Listed once a request: the names every role's folder holds. A migrated home's are all in ``job/`` too.
            store = directory.parent
            if store not in self._names:
                try:
                    folders = [item for item in store.iterdir() if is_profile_folder(item.name) and item.is_dir()]
                except OSError:
                    folders = []
                self._names[store] = frozenset().union(*(_stored_names(item) for item in folders))
            if digest not in self._names[store]:
                return None
            from ..job_store_migration import role_record

            held = role_record(store, job_identity, home_root=self._home_root, target=self._target)
            if held is None:
                return None
            path = held
        return None if path.is_symlink() or not path.is_file() else path

    def quick_assessment(self, job_identity: str, profile_id: str | None) -> AssessResponse | None:
        """The quick store's item for this job and resume identity, if one parses."""

        path = self._stored_file("quick_assess", profile_id, job_identity)
        if path is None:
            return None
        try:
            return AssessResponse.from_json(parse_json_bytes(path.read_bytes()))
        except Exception:  # noqa: BLE001 - the store skips a file that no longer parses; so does this
            return None

    def tailored_at(self, job_identity: str, profile_id: str | None) -> tuple[bool, str | None]:
        """``(stored, created_at)`` of the tailored resume for this job and resume identity."""

        path = self._stored_file("resumes", profile_id, job_identity)
        if path is None:
            return False, None
        from ..tailored_resume import TailorResponse

        try:
            stored = TailorResponse.from_json(parse_json_bytes(path.read_bytes()))
        except Exception:  # noqa: BLE001 - the store skips a file that no longer parses; so does this
            return False, None
        return True, stored.created_at or stored.updated_at or None

    def state_for(
        self,
        job_identity: str,
        *,
        profile_id: str | None,
        run_assessment: AssessmentFact | None = None,
        quick: AssessResponse | None = None,
        current_content_sha256: str | None = None,
    ) -> JobState:
        """The state of ``job_identity`` for the resume identity ``profile_id``.

        ``run_assessment`` is the run's own assessment of the posting when
        the caller has one; ``quick`` is the quick store's item when the
        caller already holds it (else it is read from the store).
        ``current_content_sha256`` is the posting's stored content digest, when
        the caller has the posting row (it marks an older assessment stale).
        """

        events = self.events_for(job_identity)
        if current_application_event(events) is not None:
            return derive_job_state(events=events)
        stored, tailored_at = self.tailored_at(job_identity, profile_id)
        if stored:
            return derive_job_state(has_tailored_resume=True, tailored_at=tailored_at)
        item = quick if quick is not None else self.quick_assessment(job_identity, profile_id)
        facts = (run_assessment, None if item is None else quick_assessment_fact(item, basis_stale=self.basis.reason(item)))
        state = derive_job_state(assessments=facts, current_content_sha256=current_content_sha256)
        if state.state == NEEDS_ANSWERS and item is not None and profile_id is not None and self._weak_fit(job_identity, profile_id, item):
            return JobState(WEAK_FIT, state.since, next_events(WEAK_FIT), state.assessment_stale)
        return state

    def _weak_fit(self, job_identity: str, profile_id: str, item: AssessResponse) -> bool:
        """0110-10-02: is this needs-answers assessment a weak fit (``scout/fit.py``)?

        0.1.11.9: at the job's BEST rank score, the highest any role that tags it has. Rank stays a role's own
        (a job two roles found has two scores), the verdict is the job's, so one role's low score never makes a
        weak fit of a job another role ranks well. ``profile_id`` is not used: until 0.1.11.9 it chose the score.
        """

        from .. import fit

        del profile_id
        if self._ranks is None:
            best: dict[str, int] = {}
            for (_role, job), score in fit.stored_rank_scores(self._home_root, self._target).items():
                if score is not None and score > best.get(job, -1):
                    best[job] = score
            self._ranks = best
            self._fit = fit.fit_setting(self._home_root, self._target)
        return fit.assessment_is_weak_fit(item, self._ranks.get(job_identity), self._fit)  # type: ignore[arg-type]


__all__ = [
    "APPLICATION_STATES",
    "GATE_STATES",
    "HAS_GAP",
    "JOB_STATES",
    "AssessmentFact",
    "JobState",
    "JobStateError",
    "JobStateSources",
    "STALE_POSTING_CHANGED",
    "THIN_POSTING",
    "WEAK_FIT",
    "application_state",
    "check_transition",
    "current_application_event",
    "derive_job_state",
    "event_identity",
    "group_events",
    "is_text_identity",
    "latest_assessment",
    "next_events",
    "normalize_job_identity",
    "quick_assessment_fact",
    "read_application_events",
]
