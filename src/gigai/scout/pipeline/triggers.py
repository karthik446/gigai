"""0.1.10.7 PL5 (DESIGN 8): what puts a job into the pipeline, the caps on it, and the approvals over the cap.

NO POSTING ENTERS THE PIPELINE BY ITSELF. A (posting, profile) pair is
queued only when the user engaged with it:

===============  ==============================================================================
trigger          the pairs it queues
===============  ==============================================================================
answer_saved     every (posting, active profile) whose stored assessment left OPEN a question
                 this answer answers: the same question id, or the model-free near match
                 (``story_bank.near_match``). Answers are the user's, so one answer answers
                 that question for every job that asked it.
story_saved      every (posting, active profile) whose stored assessment left open a question
                 the story is about (``stories.answers_question``).
process_now      the one job the user named (CLI, API). Not capped per trigger: it is an
                 explicit choice. It also takes that job out of a pending approval.
profile_changed  the steps of THAT profile already in the pipeline whose input digest is no
                 longer the one they were done with. A new resume starts the job again from
                 the tailoring (its digest is part of the tailoring's); changed candidate
                 settings (the profile's own, or the setup's) re-open the re-assessment only;
                 a renamed profile or changed titles re-open nothing. Never a job that was
                 not in the pipeline.
===============  ==============================================================================

A WEAK FIT IS NEVER QUEUED by a saved answer or story (0110-10-02,
``scout/fit.py``): a pair whose stored assessment waits on answers while few
requirements are met and the rank score is low is ``skipped`` with the code
``weak_fit``. It is judged when the trigger fires, so a job the answer just
turned into a match is queued. ``process_now`` is the user naming one job and
is not held back.

New postings in the index trigger nothing: 500 new postings are 0 queued jobs
and 0 model calls. A first assessment is asked for by ``gigai scout new``
(approval-gated there, no daily cap) and is not a pipeline step.

A pair needs a stored assessment with the posting's text (``steps.enqueue_job``'s
rule); one without is reported as skipped, by code. Re-queueing a job whose
tailoring inputs are what they were is a no-op (the digest, DESIGN 5.2).

CAPS (``pipeline.*`` and ``rank.*`` in the project's settings, one count for
the whole install, never per profile):

- ``pipeline.auto_jobs_per_trigger`` (10): one trigger queues at most this many
  jobs. The rest wait in an APPROVAL (``awaiting_approval``) with the number of
  model calls and, when the recorded calls can say, tokens
  (``call_metrics.estimate``). Nothing of theirs runs until it is approved;
  denying cancels them with no model call.
- ``pipeline.max_model_calls_per_day`` (40): the runner's
  (``runner.PipelineRunner``); the call over it waits for the next day.
- ``rank.max_calls_per_day`` (100), warning at ``rank.warn_calls_per_day`` (60):
  the one counter is here (:func:`spend_rank_calls`); the background rank lane
  (``rank_lane.py``) takes every call from it.

The pipeline switched off, or settings that cannot be read: an answer or story
trigger queues nothing (``disabled``, with what said so).

Everything returned here is ids, codes, counts and timestamps: no posting,
resume, answer or story text. The trigger entries for the write paths
(:func:`pending_answer`, :func:`pending_story`, :func:`profile_changed`,
``Pending.fire``) never raise: a save never fails because of the pipeline.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
import functools
import logging
from pathlib import Path

from ...workpad import committed_read_cache
from ..call_metrics import lane_for
from . import steps
from .settings import SOURCE_UNREADABLE, PipelineSetting, pipeline_setting
from .store import (
    APPROVAL_PENDING,
    CAP_PIPELINE_CALLS,
    CAP_RANK_CALLS,
    ENQUEUED,
    STATE_AWAITING_APPROVAL,
    STATE_BLOCKED,
    STATE_DONE,
    STATE_FAILED,
    STATE_READY,
    STATE_RUNNING,
    STEPS,
    Approval,
    Claim,
    PipelineStore,
    PipelineStoreError,
    Step,
    _downstream,
    pipeline_path,
)

TRIGGER_SCHEMA = "scout-pipeline-trigger:1"
APPROVALS_SCHEMA = "scout-pipeline-approvals:1"

TRIGGER_ANSWER = "answer_saved"
TRIGGER_STORY = "story_saved"
TRIGGER_PROCESS = "process_now"
TRIGGER_PROFILE = "profile_changed"

FIRED = "fired"
NOTHING = "nothing"
DISABLED = "disabled"
#: ``skipped[].error_code`` of a pair an answer or story trigger left out because it is a weak fit (0110-10-02).
SKIPPED_WEAK_FIT = "weak_fit"

_QUEUED_STATES = frozenset({STATE_BLOCKED, STATE_READY, STATE_RUNNING, STATE_AWAITING_APPROVAL})
_logger = logging.getLogger("gigai.scout.pipeline")

Pair = tuple[str, str]  # (profile_id, job identity)


def _reads_journal(trigger):
    """0.1.10.11 S2: a trigger runs inside ``workpad.committed_read_cache``, whoever calls it.

    A trigger only READS the journal (the profiles, the answers and stories,
    the master) and writes the queue (``pipeline.sqlite``), which is not in
    it. It runs inside a save, after the save's own journal write, and looks
    at every job of a profile: outside the scope each job's digest resolved
    and checked the workpad again and read the same committed files again
    (about 98 git processes a digest; a rename of a profile with 100 finished
    jobs was 22,599 processes and 166 s). Inside it a check that passed and a
    read that was made hold for as long as the workpad is unchanged, so the
    save's write is seen (the head moved) and the jobs share what they read.
    """

    @functools.wraps(trigger)
    def wrapper(*args, **kwargs):
        with committed_read_cache():
            return trigger(*args, **kwargs)

    return wrapper


# --- what a trigger did ---------------------------------------------------------------------


@dataclass(frozen=True)
class TriggerResult:
    """What one trigger did. Ids, codes and counts only."""

    trigger: str
    state: str = NOTHING
    #: Why nothing was queued when ``disabled``: the settings' ``source``.
    reason: str | None = None
    #: Queued to run: ``{profile_id, job_identity, step}``.
    enqueued: tuple[dict[str, object], ...] = ()
    #: Over the per-trigger cap, waiting for the approval: ``{profile_id, job_identity, step}``.
    awaiting: tuple[dict[str, object], ...] = ()
    #: Already done or queued with these inputs.
    unchanged: int = 0
    #: Could not enter the pipeline: ``{profile_id, job_identity, error_code}``.
    skipped: tuple[dict[str, object], ...] = ()
    approval: dict[str, object] | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": TRIGGER_SCHEMA,
            "trigger": self.trigger,
            "state": self.state,
            "reason": self.reason,
            "enqueued": list(self.enqueued),
            "awaiting_approval": list(self.awaiting),
            "unchanged": self.unchanged,
            "skipped": list(self.skipped),
            "approval": self.approval,
        }

    def line(self) -> str | None:
        """One sentence for a terminal, or ``None`` when the trigger queued nothing."""

        if not self.enqueued and not self.awaiting:
            return None
        parts = []
        if self.enqueued:
            parts.append(f"{len(self.enqueued)} job(s) queued")
        if self.awaiting:
            calls = (self.approval or {}).get("est_calls")
            parts.append(f"{len(self.awaiting)} wait for your approval (~{calls} model calls): `gigai scout pipeline approvals`")
        return "Pipeline: " + "; ".join(parts) + ". The Scout server runs them, or run `gigai scout new --process`."


@dataclass(frozen=True)
class _Item:
    """One step to (re-)open: where the pipeline of ``(profile_id, job)`` starts again."""

    profile_id: str
    job: str
    name: str
    digest: str
    lane: str
    model_target: str | None
    downstream_lanes: Mapping[str, tuple[str, str | None]]
    force: bool = False

    def to_json(self) -> dict[str, object]:
        return {"profile_id": self.profile_id, "job_identity": self.job, "step": self.name}


def _model_calls(name: str) -> tuple[int, int]:
    """``(tailor calls, assess calls)`` a re-opened ``name`` and what depends on it make, at one call per model step."""

    rerun = {name, *_downstream(name)}
    return int("tailor" in rerun), int("reassess" in rerun)


def estimate_calls(
    home_root: Path, target: Path, *, tailor: int, assess: int, setting: PipelineSetting | None = None
) -> dict[str, object]:
    """What ``tailor`` tailoring calls and ``assess`` re-assessment calls would take, from the recorded calls.

    ``calls`` is always a number (one call per model step when the history
    cannot say more); ``tokens`` is ``None`` unless every kind asked for has
    recorded calls that reported tokens (``call_metrics.estimate``).
    """

    from ..call_metrics import KIND_ASSESS, KIND_TAILOR, CallMetricsError, estimate

    home_root, target = Path(home_root), Path(target)
    ctx = steps.StepContext(home_root, target, setting=setting if setting is not None else pipeline_setting(home_root, target))
    calls, tokens, known, basis = 0, 0, True, 0
    for kind, step, count in ((KIND_TAILOR, "tailor", tailor), (KIND_ASSESS, "reassess", assess)):
        if count <= 0:
            continue
        try:
            found = estimate(kind, steps.model_for(ctx, step), count, home_root=home_root, target=target)
        except (CallMetricsError, PipelineStoreError, OSError):
            found = {"calls": None, "tokens": None, "basis_calls": 0}
        calls += found["calls"] if type(found["calls"]) is int else count  # type: ignore[operator]
        basis += found["basis_calls"] if type(found["basis_calls"]) is int else 0  # type: ignore[operator]
        if type(found["tokens"]) is int:
            tokens += found["tokens"]  # type: ignore[operator]
        else:
            known = False
    return {"calls": calls, "tokens": tokens if known and calls else None, "basis_calls": basis}


def _would_open(step: Step | None, digest: str) -> bool:
    """Whether ``store.enqueue`` with ``digest`` would open the step (not a no-op): read before the cap is applied."""

    if step is None:
        return True
    if step.state == STATE_DONE:
        return step.done_digest != digest
    if step.state in _QUEUED_STATES or step.state == STATE_FAILED:
        return step.input_digest != digest
    return True


def _enqueue_items(
    home_root: Path,
    target: Path,
    items: Sequence[_Item],
    *,
    trigger: str,
    setting: PipelineSetting,
    store: PipelineStore,
    skipped: Sequence[dict[str, object]] = (),
    capped: bool = True,
) -> TriggerResult:
    """Open ``items`` in order: the first ``auto_jobs_per_trigger`` run, the rest wait in one approval."""

    fresh = [item for item in items if item.force or _would_open(store.step(item.profile_id, item.job, item.name), item.digest)]
    unchanged = len(items) - len(fresh)
    limit = setting.auto_jobs_per_trigger if capped else len(fresh)
    now, later = fresh[:limit], fresh[limit:]
    approval: dict[str, object] | None = None
    approval_id: str | None = None
    if later:
        counts = [_model_calls(item.name) for item in later]
        found = estimate_calls(
            home_root, target, tailor=sum(count[0] for count in counts), assess=sum(count[1] for count in counts), setting=setting
        )
        profiles = {item.profile_id for item in later}
        approval_id = store.create_approval(
            trigger=trigger, jobs=len(later), est_calls=found["calls"], est_tokens=found["tokens"],  # type: ignore[arg-type]
            profile_id=next(iter(profiles)) if len(profiles) == 1 else None,
        )
        approval = {"id": approval_id, "jobs": len(later), "est_calls": found["calls"], "est_tokens": found["tokens"]}
    enqueued: list[dict[str, object]] = []
    awaiting: list[dict[str, object]] = []
    for item, gate in [(item, None) for item in now] + [(item, approval_id) for item in later]:
        result = store.enqueue(
            item.profile_id, item.job, item.name, input_digest=item.digest, trigger=trigger, lane=item.lane,
            model_target=item.model_target, downstream_lanes=item.downstream_lanes, approval_id=gate, force=item.force,
        )
        if result != ENQUEUED:
            unchanged += 1  # another writer got there between the look and the write
        elif gate is None:
            enqueued.append(item.to_json())
        else:
            awaiting.append(item.to_json())
    return TriggerResult(
        trigger, FIRED if enqueued or awaiting else NOTHING, None, tuple(enqueued), tuple(awaiting), unchanged, tuple(skipped), approval
    )


def _skip(pair: Pair, exc: BaseException) -> dict[str, object]:
    return {"profile_id": pair[0], "job_identity": pair[1], "error_code": steps.error_code(exc)}


@_reads_journal
def enqueue_pairs(
    home_root: Path,
    target: Path,
    pairs: Iterable[Pair],
    *,
    trigger: str,
    setting: PipelineSetting | None = None,
    store: PipelineStore | None = None,
) -> TriggerResult:
    """Queue the pipeline of each ``(profile_id, job)`` from its tailor step, in order, under the per-trigger cap.

    The pipeline switched off, or settings that cannot be read: ``disabled``,
    nothing is queued. A pair that cannot enter the pipeline (no assessment,
    no stored posting text, no such profile) is ``skipped`` with its code.
    """

    home_root, target = Path(home_root), Path(target)
    setting = setting if setting is not None else pipeline_setting(home_root, target)
    if not setting.enabled:
        return TriggerResult(trigger, DISABLED, setting.source)
    wanted = list(dict.fromkeys(pairs))
    if not wanted:
        return TriggerResult(trigger)
    ctx = steps.StepContext(home_root, target, setting=setting)
    tailor_model, reassess_model = steps.model_for(ctx, "tailor"), steps.model_for(ctx, "reassess")
    lanes = {"reassess": (lane_for(reassess_model), reassess_model)}
    items: list[_Item] = []
    skipped: list[dict[str, object]] = []
    shared = steps.SharedInputs(ctx)  # 0.1.10.11 S2: what the pairs read alike is read once
    for pair in wanted:
        try:
            digest = steps.tailor_digest(ctx, pair[0], pair[1], tailor_model, shared=shared)
        except Exception as exc:  # noqa: BLE001 - a pair whose inputs cannot be read is skipped with a bounded code, the others go on
            skipped.append(_skip(pair, exc))
            continue
        items.append(_Item(pair[0], pair[1], "tailor", digest, lane_for(tailor_model), tailor_model, lanes))
    if not items:
        return TriggerResult(trigger, skipped=tuple(skipped))
    opened = store if store is not None else PipelineStore(pipeline_path(home_root, target))
    try:
        return _enqueue_items(home_root, target, items, trigger=trigger, setting=setting, store=opened, skipped=skipped)
    finally:
        if store is None:
            opened.close()


# --- process now ----------------------------------------------------------------------------


@_reads_journal
def process_now(
    home_root: Path,
    target: Path,
    profile_id: str,
    job: str,
    *,
    force: bool = False,
    store: PipelineStore | None = None,
) -> dict[str, object]:
    """Queue one job now: ``steps.enqueue_job``'s answer (``{result, profile_id, job, input_digest}``).

    The user named this job, so the per-trigger cap does not apply and a job
    waiting for an approval is taken out of it and opened. Raises
    ``StepError`` when the job cannot enter the pipeline.
    """

    home_root, target = Path(home_root), Path(target)
    if not pipeline_setting(home_root, target).enabled:
        # 0.1.11: the pipeline is off (the default): this queues nothing and no model call follows.
        raise steps.StepError(steps.ERROR_PIPELINE_OFF, "the background pipeline is off; set pipeline.enabled in the settings file to use it")
    if store is None and not pipeline_path(home_root, target).is_file():
        # No queue yet, so nothing waits for an approval; a job that cannot enter the pipeline creates no file.
        return steps.enqueue_job(profile_id, job, force=force, home_root=home_root, target=target, trigger=TRIGGER_PROCESS)
    opened = store if store is not None else PipelineStore(pipeline_path(home_root, target))
    try:
        step = opened.step(profile_id, job, "tailor")
        waiting = step is not None and step.state == STATE_AWAITING_APPROVAL
        return steps.enqueue_job(
            profile_id, job, force=force or waiting, home_root=home_root, target=target, trigger=TRIGGER_PROCESS, store=opened
        )
    finally:
        if store is None:
            opened.close()


# --- an answer or a story was saved ---------------------------------------------------------


@dataclass
class Pending:
    """The pairs a saved answer or story concerns, found when it was saved; ``fire`` queues them.

    Two steps because the surface that saved an answer may re-assess the job
    first: the pairs are the ones that ASKED (read before the re-assessment
    answers the question), and they are queued after it.
    """

    home_root: Path
    target: Path
    trigger: str
    pairs: tuple[Pair, ...] = ()
    fired: TriggerResult | None = field(default=None, repr=False)

    @_reads_journal
    def fire(self) -> TriggerResult:
        """Queue the pairs (once). Never raises: a failure is logged by its type and queues nothing."""

        if self.fired is None:
            try:
                if self.pairs:
                    # 0110-10-02: judged now, after any re-assessment the save started, not when the pairs were found.
                    weak = _weak_fits(self.home_root, self.target, self.pairs)
                    kept = tuple(pair for pair in self.pairs if pair not in weak)
                    fired = (
                        enqueue_pairs(self.home_root, self.target, kept, trigger=self.trigger) if kept else TriggerResult(self.trigger)
                    )
                    left_out = tuple(
                        {"profile_id": pair[0], "job_identity": pair[1], "error_code": SKIPPED_WEAK_FIT}
                        for pair in self.pairs if pair in weak
                    )
                    self.fired = replace(fired, skipped=fired.skipped + left_out) if left_out else fired
                else:
                    self.fired = TriggerResult(self.trigger)
            except Exception as exc:  # noqa: BLE001 - a save never fails because of the pipeline: logged by type, nothing queued
                _logger.warning("pipeline: the %s trigger failed (%s); nothing was queued", self.trigger, type(exc).__name__)
                self.fired = TriggerResult(self.trigger, reason="trigger_failed")
        return self.fired


def _weak_fits(home_root: Path, target: Path, pairs: Sequence[Pair]) -> frozenset[Pair]:
    """0110-10-02: the ``pairs`` that are a weak fit now (``fit.is_weak_fit``).

    The verdict and the fit number are the stored assessment's as it is at
    this moment; the rank score is the posting read model's row (a pair with
    no row, or no rank score yet, is never a weak fit). No model, nothing written.
    """

    from .. import fit
    from ..quick_assess import read_quick_assessment

    ranks = fit.stored_rank_scores(home_root, target) if pairs else {}
    if not ranks:
        return frozenset()
    setting = fit.fit_setting(home_root, target)
    weak: set[Pair] = set()
    for pair in pairs:
        rank_score = ranks.get(pair)
        if rank_score is None:
            continue
        if fit.assessment_is_weak_fit(read_quick_assessment(home_root, target, pair[0], pair[1]), rank_score, setting):
            weak.add(pair)
    return frozenset(weak)


def _active_profile_ids(home_root: Path, target: Path) -> frozenset[str]:
    from .. import profile_records
    from ..postings import STATE_ACTIVE
    from ..quick_assess import _resolve_workpad

    resolved = _resolve_workpad(home_root, target)
    return frozenset(record.profile_id for record in profile_records.list_profiles(resolved) if record.state == STATE_ACTIVE)


def _selected_active_profile(home_root: Path, target: Path, active: frozenset[str]) -> str | None:
    from .. import profile_records
    from ..quick_assess import _resolve_workpad

    try:
        selected = profile_records.selected_profile(_resolve_workpad(home_root, target), home_root=home_root, target=target)
    except Exception:  # noqa: BLE001 - no selected role to stand in: the pair is left out, as it was
        return None
    return selected.profile_id if selected is not None and selected.profile_id in active else None


def _asking_pairs(home_root: Path, target: Path, answers: Callable[[str, str], bool], first_job: str | None) -> tuple[Pair, ...]:
    """The ``(profile, job)`` pairs of active profiles whose stored assessment left open a question ``answers`` accepts.

    The job the answer was given for first, then the newest assessment first.

    0.1.11.9: a job has ONE assessment, and the role on it is the one that asked LAST. When that role is no longer
    active (archived or deleted since), the job is still the user's and still asks: its pair is the SELECTED role's
    (left out, as before, when that one is not active either).  Until 0.1.11.9 each active role that had assessed
    the job had its own pair.
    """

    from ..quick_assess import list_quick_assessments

    found: list[tuple[bool, Pair]] = []
    for item in list_quick_assessments(home_root, target):  # newest first
        profile_id = item.resume.profile_id
        if profile_id is None:
            continue  # a pasted resume has no profile: it is never in the pipeline
        if any(answers(question.question_id, question.question) for question in item.result.structured_questions):
            found.append((item.job.job_identity != first_job, (profile_id, item.job.job_identity)))
    if not found:
        return ()  # nothing asked: the profiles are not read at all
    active = _active_profile_ids(home_root, target)
    found.sort(key=lambda entry: entry[0])  # stable: the named job first, the rest newest first
    pairs: list[Pair] = []
    stand_in: list[str | None] = []  # the selected role, read once and only when a role on an assessment is not active
    for _later, (profile_id, job) in found:
        if profile_id not in active:
            if not stand_in:
                stand_in.append(_selected_active_profile(home_root, target, active))
            if stand_in[0] is None:
                continue
            profile_id = stand_in[0]
        pairs.append((profile_id, job))
    return tuple(pairs)


@_reads_journal
def _pending(home_root: Path, target: Path, trigger: str, answers: Callable[[str, str], bool], first_job: str | None) -> Pending:
    home_root, target = Path(home_root), Path(target)
    try:
        pairs = _asking_pairs(home_root, target, answers, first_job)
    except Exception as exc:  # noqa: BLE001 - a save never fails because of the pipeline: logged by type, nothing queued
        _logger.warning("pipeline: the %s trigger could not read who asked (%s); nothing is queued", trigger, type(exc).__name__)
        pairs = ()
    return Pending(home_root, target, trigger, pairs)


def pending_answer(home_root: Path, target: Path, entry: object, *, job_identity: str | None = None) -> Pending:
    """The pairs the saved answer ``entry`` (``story_bank.BankEntry``) concerns. Never raises; no model, nothing written.

    ``job_identity``: the job the answer was given for, queued first.
    """

    from .. import story_bank
    from ..question_ids import normalize_question_id

    def answers(question_id: str, question: str) -> bool:
        if normalize_question_id(question_id) == entry.question_id:  # type: ignore[attr-defined]
            return True
        return story_bank.near_match((entry,), question_id=question_id, question=question) is not None  # type: ignore[arg-type]

    return _pending(home_root, target, TRIGGER_ANSWER, answers, job_identity)


def pending_story(home_root: Path, target: Path, story: object) -> Pending:
    """The pairs the saved ``story`` (``stories.Story``) concerns: the ones with an open question it is about. Never raises."""

    from .. import stories

    def answers(question_id: str, question: str) -> bool:
        return stories.answers_question(story, question_id=question_id, question=question)  # type: ignore[arg-type]

    return _pending(home_root, target, TRIGGER_STORY, answers, None)


# --- a profile's resume or settings changed -------------------------------------------------


def _stale_item(ctx: steps.StepContext, store: PipelineStore, rows: Mapping[str, Step], shared: steps.SharedInputs) -> _Item | None:
    """The first step of one job that is ``done`` with a digest its inputs no longer give, or ``None``.

    Only a finished pipeline is looked at step by step: a step that is
    queued gets its digest when it is claimed, and a failed or cancelled one
    is the user's to retry. ``shared``: what the trigger's jobs read alike.
    """

    for name in STEPS:
        step = rows.get(name)
        if step is None or step.state != STATE_DONE:
            return None
        if name == "tailor":
            digest = steps.tailor_digest(ctx, step.profile_id, step.job, step.model_target, shared=shared)
        else:
            digest = steps.input_digest(
                ctx, store,
                Claim(step.profile_id, step.job, name, step.lane, step.model_target, step.input_digest, step.done_digest, step.generation, step.attempts, "", 0.0, 0.0),
                shared=shared,
            )
        if digest != step.done_digest:
            lanes = {other: (rows[other].lane, rows[other].model_target) for other in _downstream(name) if other in rows}
            return _Item(step.profile_id, step.job, name, digest, step.lane, step.model_target, lanes)
    return None


@_reads_journal
def profile_changed(home_root: Path, target: Path, profile_id: str | None = None) -> TriggerResult:
    """Re-open the steps of ``profile_id`` (``None``: every profile) whose inputs changed. Never raises.

    Only jobs already in the pipeline, each from its first step whose digest
    is no longer the one it was done with: a new resume re-opens the
    tailoring, changed candidate settings the re-assessment, a renamed
    profile or changed titles nothing. Under the per-trigger cap like
    every trigger. With no pipeline file there is nothing to look at and
    nothing is created.
    """

    home_root, target = Path(home_root), Path(target)
    try:
        path = pipeline_path(home_root, target)
        if not path.is_file():
            return TriggerResult(TRIGGER_PROFILE)
        setting = pipeline_setting(home_root, target)
        if not setting.enabled:
            return TriggerResult(TRIGGER_PROFILE, DISABLED, setting.source)
        ctx = steps.StepContext(home_root, target, setting=setting)
        store = PipelineStore(path)
        try:
            jobs: dict[Pair, dict[str, Step]] = {}
            for step in store.steps(profile_id=profile_id):
                jobs.setdefault((step.profile_id, step.job), {})[step.name] = step
            items: list[_Item] = []
            skipped: list[dict[str, object]] = []
            shared = steps.SharedInputs(ctx)  # 0.1.10.11 S2: what the jobs read alike is read once
            for pair, rows in jobs.items():
                try:
                    item = _stale_item(ctx, store, rows, shared)
                except Exception as exc:  # noqa: BLE001 - a job whose inputs cannot be read is skipped with a bounded code, the others go on
                    skipped.append(_skip(pair, exc))
                    continue
                if item is not None:
                    items.append(item)
            if not items:
                return TriggerResult(TRIGGER_PROFILE, skipped=tuple(skipped))
            return _enqueue_items(home_root, target, items, trigger=TRIGGER_PROFILE, setting=setting, store=store, skipped=skipped)
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 - a profile save never fails because of the pipeline: logged by type, nothing queued
        _logger.warning("pipeline: the profile_changed trigger failed (%s); nothing was queued", type(exc).__name__)
        return TriggerResult(TRIGGER_PROFILE, reason="trigger_failed")


# --- approvals ------------------------------------------------------------------------------


def _waiting_by_approval(store: PipelineStore) -> dict[str, list[Pair]]:
    """approval id -> the ``(profile, job)`` pairs still waiting for it, each once."""

    waiting: dict[str, list[Pair]] = {}
    for step in store.steps():
        if step.approval_id is not None and step.state == STATE_AWAITING_APPROVAL:
            pairs = waiting.setdefault(step.approval_id, [])
            if (step.profile_id, step.job) not in pairs:
                pairs.append((step.profile_id, step.job))
    return waiting


def _approval_json(approval: Approval, waiting: Sequence[Pair]) -> dict[str, object]:
    return {
        "id": approval.id,
        "state": approval.state,
        "trigger": approval.trigger,
        "profile_id": approval.profile_id,
        "jobs": approval.jobs,
        "waiting_jobs": len(waiting),
        "est_calls": approval.est_calls,
        "est_tokens": approval.est_tokens,
        "created_at": approval.created_at,
        "decided_at": approval.decided_at,
        "decided_by": approval.decided_by,
        "waiting": [{"profile_id": profile_id, "job_identity": job} for profile_id, job in waiting],
    }


def approvals_of(store: PipelineStore, *, state: str | None = None) -> list[dict[str, object]]:
    """The approvals of an open store, oldest first. A pending one with no job left waiting is left out.

    (Its jobs were processed one by one, or cancelled: there is nothing left
    to approve.)
    """

    waiting = _waiting_by_approval(store)
    listed = []
    for approval in store.approvals(state=state):
        pairs = waiting.get(approval.id, [])
        if approval.state == APPROVAL_PENDING and not pairs:
            continue
        listed.append(_approval_json(approval, pairs))
    return listed


def approvals(home_root: Path, target: Path, *, state: str | None = None) -> dict[str, object]:
    """``scout-pipeline-approvals:1``: the approvals and how many are pending. Reads only; never creates the file."""

    path = pipeline_path(Path(home_root), Path(target))
    listed: list[dict[str, object]] = []
    if path.is_file():
        store = PipelineStore(path)
        try:
            listed = approvals_of(store, state=state)
        finally:
            store.close()
    return {
        "schema_version": APPROVALS_SCHEMA,
        "pending": sum(1 for item in listed if item["state"] == APPROVAL_PENDING),
        "approvals": listed,
    }


def decide(home_root: Path, target: Path, approval_id: str, *, approve: bool, decided_by: str = "operator") -> dict[str, object]:
    """Approve (its jobs open; the runner runs them, within the daily cap) or deny (its jobs are cancelled; no model call).

    Returns the approval as it now is. Deciding one that is already decided
    changes nothing. Raises ``PipelineStoreError`` (``approval_not_found``,
    ``invalid_value``).
    """

    path = pipeline_path(Path(home_root), Path(target))
    if not path.is_file():
        raise PipelineStoreError("approval_not_found", "no such approval")
    store = PipelineStore(path)
    try:
        before = _waiting_by_approval(store).get(approval_id, [])
        store.decide_approval(approval_id, approved=approve, decided_by=decided_by)
        approval = next(item for item in store.approvals() if item.id == approval_id)
        answer = _approval_json(approval, _waiting_by_approval(store).get(approval_id, []))
        answer["decided_jobs"] = len(before)
        return answer
    finally:
        store.close()


def approve_all(home_root: Path, target: Path, *, decided_by: str = "operator") -> list[dict[str, object]]:
    """Approve every pending approval (``gigai scout new --process``: the yes to its offer). The approvals decided."""

    pending = [item for item in approvals(home_root, target, state=APPROVAL_PENDING)["approvals"]]  # type: ignore[union-attr]
    return [decide(home_root, target, str(item["id"]), approve=True, decided_by=decided_by) for item in pending]


# --- the daily counters ---------------------------------------------------------------------


def today(now: datetime | None = None) -> str:
    """The local day the daily counters count for (the runner's day)."""

    return (now or datetime.now().astimezone()).date().isoformat()


def caps(
    home_root: Path, target: Path, *, setting: PipelineSetting | None = None, store: PipelineStore | None = None, now: datetime | None = None
) -> dict[str, object]:
    """Today's counters against their caps, for the whole install. Reads only; never creates the file."""

    home_root, target = Path(home_root), Path(target)
    setting = setting if setting is not None else pipeline_setting(home_root, target)
    day = today(now)
    used = {CAP_PIPELINE_CALLS: 0, CAP_RANK_CALLS: 0}
    path = pipeline_path(home_root, target)
    if store is not None or path.is_file():
        opened = store if store is not None else PipelineStore(path)
        try:
            used = {cap: opened.used(cap, day) for cap in used}
        finally:
            if store is None:
                opened.close()
    return {
        "day": day,
        "jobs_per_trigger": setting.auto_jobs_per_trigger,
        "pipeline_calls": {"used": used[CAP_PIPELINE_CALLS], "limit": setting.max_model_calls_per_day},
        "rank_calls": _rank_count(used[CAP_RANK_CALLS], setting),
    }


def _rank_count(used: int, setting: PipelineSetting) -> dict[str, object]:
    """The day's rank count against its caps. ``warning``: the count has passed the warning level (the 61st call of a day with the default 60)."""

    return {
        "used": used,
        "limit": setting.rank_max_calls_per_day,
        "warn_at": setting.rank_warn_calls_per_day,
        "warning": used > setting.rank_warn_calls_per_day,
    }


def rank_calls_today(store: PipelineStore | None, setting: PipelineSetting, day: str) -> dict[str, object]:
    """The day's rank counter as a status block: numbers only. ``store`` None: no pipeline file yet, nothing counted."""

    used = 0 if store is None else store.used(CAP_RANK_CALLS, day)
    return {"day": day, **_rank_count(used, setting), "reached": used >= setting.rank_max_calls_per_day}


def spend_rank_calls(
    home_root: Path,
    target: Path,
    calls: int = 1,
    *,
    setting: PipelineSetting | None = None,
    store: PipelineStore | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Count ``calls`` rank model calls for today against ``rank.max_calls_per_day``, BEFORE they are made.

    THE one rank counter: the background rank lane (``rank_lane.rank_tick``)
    and every other caller take their calls here, so the install has one
    count whatever the profile and whoever ranks.

    ``{allowed, used, limit, warn_at, warning, day}``. ``allowed`` false:
    nothing was counted and the calls must not be made (the cap, or settings
    that cannot be read: a background job that spends model calls does not
    guess). ``warning`` is true once the day's count has passed
    ``rank.warn_calls_per_day``: with the defaults the 60th call is not
    flagged, the 61st is, and the 101st is not allowed.
    """

    home_root, target = Path(home_root), Path(target)
    setting = setting if setting is not None else pipeline_setting(home_root, target)
    day = today(now)
    opened = store if store is not None else PipelineStore(pipeline_path(home_root, target))
    try:
        allowed = setting.source != SOURCE_UNREADABLE and opened.spend(CAP_RANK_CALLS, day, calls, limit=setting.rank_max_calls_per_day)
        used = opened.used(CAP_RANK_CALLS, day)
    finally:
        if store is None:
            opened.close()
    return {"allowed": allowed, **_rank_count(used, setting), "day": day}


def refund_rank_calls(home_root: Path, target: Path, calls: int = 1, *, store: PipelineStore | None = None, now: datetime | None = None) -> None:
    """Give back rank calls counted by :func:`spend_rank_calls` that were never made."""

    opened = store if store is not None else PipelineStore(pipeline_path(Path(home_root), Path(target)))
    try:
        opened.refund(CAP_RANK_CALLS, today(now), calls)
    finally:
        if store is None:
            opened.close()


__all__ = [
    "APPROVALS_SCHEMA",
    "DISABLED",
    "FIRED",
    "NOTHING",
    "SKIPPED_WEAK_FIT",
    "TRIGGER_ANSWER",
    "TRIGGER_PROCESS",
    "TRIGGER_PROFILE",
    "TRIGGER_SCHEMA",
    "TRIGGER_STORY",
    "Pending",
    "TriggerResult",
    "approvals",
    "approvals_of",
    "approve_all",
    "caps",
    "decide",
    "enqueue_pairs",
    "estimate_calls",
    "pending_answer",
    "pending_story",
    "process_now",
    "profile_changed",
    "rank_calls_today",
    "refund_rank_calls",
    "spend_rank_calls",
    "today",
]
