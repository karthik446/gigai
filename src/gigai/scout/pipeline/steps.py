"""0.1.10.7 PL3: the four steps of one job's pipeline, and what each one's input digest is made of.

The DAG is fixed (``store.DEPS``): ``tailor -> {reassess, ats} -> label``,
per JOB (0.1.11.9: one set of steps a job; the role on its rows is the one
that asked first, whose resume is tailored, and selects nothing). A job
enters it through :func:`enqueue_job` (the one entry the triggers and
``gigai scout pipeline process <job>`` share), and only when it already has
an assessment: the pipeline reads the posting from that stored assessment
and never fetches anything.

=========  ==================================================  ============================================
step       input digest over                                   output (in the store it already uses)
=========  ==================================================  ============================================
tailor     the posting digest, the profile's id and its        the tailored-resume store:
           resume's digest, every answer's revision, the       ``resumes/job/<sha>.json`` + ``.md``
           matching stories' marks, the base assessment's
           matrix, the tailor instructions, the model target;
           with a master resume also its revision, the
           selector's version and the candidate rule
reassess   the tailored markdown's digest, the posting         the TAILORED VARIANT of the assessment:
           digest, the assess prompt version, the              ``quick_assess_tailored/job/<sha>.json``
           constraints digest, the answers-and-stories         (the base assessment is never written)
           digest, the model target
ats        the tailored markdown's digest, the posting         ``ats/job/<sha>.json``
           digest, the ATS rules version
label      the reassess and ats output digests, the label      ``label/job/<sha>.json``
           rule version, the ATS minimum, whether the base
           assessment is stale
=========  ==================================================  ============================================

``<sha>`` is ``sha256(job identity)``, as in the quick-assess store.
0.1.11.9: each output is the JOB's, in the store's ``job`` folder
(``job_store_layout``). Until then it was kept per role
(``<store>/<profile>/<sha>.json``); such a file is still read when the job
has none of its own (the role named first, then the newest of the others)
and is never moved or removed. A
digest is computed when the step is claimed, from its upstream OUTPUT
digests: a re-tailoring that returns the same markdown leaves ``ats`` with
the digest it was done with, and it finishes without running
(:func:`unchanged`). ``reassess`` also reads the answers, so an answer that
changed runs it again even over the same markdown.

**The tailoring's digest is what changes the tailored text, nothing else of
the profile.** Not the profile record's revision, label, titles or search
settings: renaming a profile or changing its titles re-opens no tailoring and
calls no model. A new resume does (its digest), and the profile's own
candidate settings re-open the re-assessment, whose digest reads them.

**The tailor step never replaces the user's work.** It writes the
tailored-resume store, which the user also writes: tailoring on demand
(``POST /api/tailored-resumes``, ``gigai scout resume tailor``) and the
per-line choices and edits (``PUT /api/tailored-resumes/lines``). The step
replaces a stored resume only when it is the step's OWN last tailoring, byte
for byte (``pipeline/tailor/job/<sha>.json`` keeps that file's digest).
Any other stored resume is the user's: one tailored on demand, one with a line
choice or an edited line. The step then calls no model, ADOPTS the stored
resume as its output (the re-assessment, the ATS check and the label run
against what the user chose) and finishes with the code
``tailor_kept_user_edits``. The same check runs again under the store's write
lock just before the step writes, so a tailoring or a line choice that lands
while the model call is out is kept too: the step's own new tailoring is
dropped and the stored resume adopted. The store's revision (``updated_at``)
therefore never changes under a user who holds it, and their next line choice
is not refused. To get a new tailoring of a resume that is theirs, the user
tailors it on demand; ``pipeline process --force`` does not replace it.

**The Scout label** (:func:`label_for`) is Scout's own recommendation, made
from the user's settings, resume and answers. It is not a prediction of what
an employer will decide. ``recommended`` when the assessment against the
tailored resume is a match with no open question, the Scout ATS score is at
least ``pipeline.label_min_ats`` and the base assessment is not stale;
otherwise ``needs_attention`` with the reasons as codes.

**Privacy.** The tailoring is headerless and made from the lines
``resume_privacy.model_resume`` keeps; the ATS check renders a headerless
PDF. Every output is checked for contact-shaped text before the step is
called done (``contact_data_found`` fails it). ``pipeline.sqlite`` gets ids,
digests, codes and numbers only (``store.py``).

**Metrics.** A model step's calls go through the ``CallMeter`` its function
already uses (one ``model_call`` row each); the runner collects the same
numbers (``call_metrics.capture_calls``) for the step's ``step_run`` row.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re

from ...canonical import digest_imported_bytes
from ..call_metrics import lane_for
from ..requirement_weights import blocking_question_count
from ..wording import LABEL_WORDING
from .settings import PipelineSetting
from .store import Claim, PipelineStore, fits, pipeline_path

#: What the ATS step's digest names: bump when ``ats_score`` or ``posting_keywords`` change what a score means.
ATS_RULES_VERSION = "scout-ats:2"
LABEL_RULE_VERSION = "scout-label:1"
ATS_RECORD_SCHEMA = "scout-ats-record:1"
LABEL_RECORD_SCHEMA = "scout-label-record:1"
TAILOR_RECORD_SCHEMA = "scout-tailor-step-record:1"

ATS_DIR = "ats"
LABEL_DIR = "label"
#: What the tailor step last did per job, and the digest of the stored resume it wrote: digests and codes only.
TAILOR_DIR = "pipeline/tailor"

#: What the tailor step did: it wrote a new tailoring, or it kept the stored resume because it is the user's.
TAILOR_TAILORED = "tailored"
TAILOR_KEPT_USER_EDITS = "tailor_kept_user_edits"
TAILOR_OUTCOMES: tuple[str, ...] = (TAILOR_TAILORED, TAILOR_KEPT_USER_EDITS)

#: The tailor call's failures the master resume's code-only selection may stand in for inside the pipeline.
TAILOR_FALLBACK_CODES: frozenset[str] = frozenset({"model_output_invalid"})

#: The label's name, everywhere it is shown.
LABEL_NAME = "Scout label"  # its one-line notice is LABEL_WORDING (gigai.scout.wording)
LABEL_RECOMMENDED = "recommended"
LABEL_NEEDS_ATTENTION = "needs_attention"
LABELS: tuple[str, ...] = (LABEL_RECOMMENDED, LABEL_NEEDS_ATTENTION)

REASON_NOT_MATCHED = "tailored_assessment_not_matched"
REASON_OPEN_QUESTIONS = "open_questions"
REASON_ATS_BELOW_MINIMUM = "ats_below_minimum"
REASON_ASSESSMENT_STALE = "base_assessment_stale"
LABEL_REASONS: tuple[str, ...] = (REASON_NOT_MATCHED, REASON_OPEN_QUESTIONS, REASON_ATS_BELOW_MINIMUM, REASON_ASSESSMENT_STALE)

#: Step failures of this module's own (the model and input codes are ``quick_assess``'s).
ERROR_ASSESSMENT_MISSING = "assessment_missing"
#: 0.1.11: the pipeline is off (the default); ``triggers.process_now`` queues nothing.
ERROR_PIPELINE_OFF = "pipeline_off"
ERROR_POSTING_TEXT_UNAVAILABLE = "posting_text_unavailable"
ERROR_UPSTREAM_OUTPUT_MISSING = "upstream_output_missing"
ERROR_CONTACT_DATA_FOUND = "contact_data_found"
ERROR_STORE_UNWRITABLE = "store_unwritable"
ERROR_STEP_FAILED = "step_failed"

_MATCHED = "matched_above_threshold"
_COMMENT = re.compile(r"[ \t]*<!--.*?-->", re.DOTALL)


class StepError(Exception):
    """A step that cannot run or whose output is refused; ``code`` is the bounded error code the queue stores."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class StepContext:
    """Where a step runs: the GigAI home, the project folder, the model configuration and the pipeline settings."""

    home_root: Path
    target: Path
    config: object | None = None
    setting: PipelineSetting = field(default_factory=PipelineSetting)


@dataclass(frozen=True)
class StepResult:
    """What a step produced: where it is (a store path key, never content) and its digest.

    ``code`` says how, when a done step did something other than run as
    usual (``tailor_kept_user_edits``).
    """

    output_ref: str | None = None
    output_digest: str | None = None
    code: str | None = None


# --- digests and paths ----------------------------------------------------------------------


def _digest(*parts: object) -> str:
    rendered = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return digest_imported_bytes(rendered.encode("utf-8"))


def _bytes_digest(data: bytes) -> str:
    return digest_imported_bytes(data)


def _job_key(job: str) -> str:
    return digest_imported_bytes(job.encode("utf-8")).removeprefix("sha256:")


def _scout_root(home_root: Path, target: Path) -> Path:
    return pipeline_path(home_root, target).parent.parent


def _role_record(store_dir: Path, profile_id: str | None, name: str) -> Path | None:
    """The file ``name`` a ROLE's folder of ``store_dir`` holds (written before 0.1.11.9), or ``None``.

    ``profile_id``'s own first (the role the job's steps are under); else the newest of the other roles'.
    """

    from ..job_store_layout import is_profile_folder

    if profile_id is not None and is_profile_folder(profile_id):
        own = store_dir / profile_id / name
        if own.is_file() and not own.is_symlink():
            return own
    found: list[tuple[float, str, Path]] = []
    try:
        folders = [folder for folder in store_dir.iterdir() if is_profile_folder(folder.name)]
    except OSError:
        return None
    for folder in folders:
        path = folder / name
        try:
            if path.is_file() and not path.is_symlink():
                found.append((path.stat().st_mtime, folder.name, path))
        except OSError:
            continue
    return max(found)[2] if found else None


def job_record_path(store_dir: Path, profile_id: str | None, job: str) -> Path:
    """Where the job's record of the per-job store at ``store_dir`` IS (for a read).

    ``<store>/job/<sha256(job identity)>.json``; when the job has none there, the file a role's folder holds for
    it (:func:`_role_record`); with neither, the per-job path. A WRITER takes :func:`_record_write_path`.
    """

    from ..job_store_layout import job_store_path

    path = job_store_path(store_dir, job)
    if path.is_symlink() or path.exists():
        return path
    held = _role_record(store_dir, profile_id, path.name)
    return path if held is None else held


def _record_path(ctx: StepContext, directory: str, profile_id: str | None, job: str) -> Path:
    """Where the job's record of ``directory`` is READ (:func:`job_record_path`); ``profile_id`` selects nothing."""

    return job_record_path(_scout_root(ctx.home_root, ctx.target) / directory, profile_id, job)


def _record_write_path(ctx: StepContext, directory: str, job: str) -> Path:
    """Where the job's record of ``directory`` is WRITTEN: the per-job folder, never a role's."""

    from ..job_store_layout import job_store_path

    return job_store_path(_scout_root(ctx.home_root, ctx.target) / directory, job)


def _ref(ctx: StepContext, path: Path) -> str:
    return path.relative_to(_scout_root(ctx.home_root, ctx.target)).as_posix()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, object] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    from ..find_jobs.discovery.storage import atomic_write

    try:
        atomic_write(path, json.dumps(value, indent=2, sort_keys=True).encode("utf-8"))
    except OSError as exc:
        raise StepError(ERROR_STORE_UNWRITABLE, "the step's output could not be written") from exc


def _refuse_contact_data(*texts: str) -> None:
    """No pipeline output holds contact-shaped text (DESIGN 9); a step whose output does is failed."""

    from ..resume_pii import detect_contact_details

    for text in texts:
        if detect_contact_details(text):
            raise StepError(ERROR_CONTACT_DATA_FOUND, "the step's output holds contact-shaped text")


def error_code(exc: BaseException) -> str:
    """The bounded code the queue stores for a step that raised: never its message."""

    code = getattr(exc, "code", None)
    if code == "tailor_timeout":
        return "assess_timeout"  # the queue's one retryable timeout code
    return code if isinstance(code, str) and fits("code", code) else ERROR_STEP_FAILED


# --- the job's inputs -----------------------------------------------------------------------


@dataclass(frozen=True)
class _Inputs:
    resolved: object
    profile: object
    base: object
    job: object  # the ResolvedJob, with the posting text the base assessment stored
    posting_sha256: str


class SharedInputs:
    """0.1.10.11 S2: what every job of ONE trigger reads alike, read once and passed to each job's digest.

    A trigger looks at every finished job of a profile, or at every job that
    asked a question. Without this each job's digest resolved the workpad and
    the profile again and read the answers, the stories and the master again
    (about 98 git processes a digest on the operator-sized home; four digests
    a job when nothing changed). With it a job costs its own stored
    assessment (read once for its four digests) and a match in memory.

    It is ONE look: what it read is not read again, so it lives for one
    trigger and is never kept. Each value is what the per-job path reads,
    made by the same function with the same arguments, so a digest computed
    with it is byte for byte the one computed without
    (``tests/behaviors/scout_pipeline/test_trigger_digests_identical.py``; a
    different digest would tailor every finished job again). A read that
    failed is not kept (a profile that is not there, a bank or a master that
    could not be read, which read as "none"): the next job asks again, as it
    always did, so one failed read never decides for every job of the trigger.
    """

    def __init__(self, ctx: StepContext) -> None:
        self._ctx = ctx
        self._resolved: object | None = None
        self._profiles: dict[str, object] = {}
        self._answer_marks: dict[str, object] | None = None
        self._banks: dict[str, object] = {}
        self._master_parts: dict[str, tuple[object, ...]] = {}
        #: profile id -> the folder its stored assessments are in (``None``: ask ``quick_assess`` for every job).
        self._base_dirs: dict[str, Path | None] = {}
        self._job: tuple[tuple[str, str], _Inputs] | None = None

    def resolved(self) -> object:
        if self._resolved is None:
            from ..quick_assess import _resolve_workpad

            self._resolved = _resolve_workpad(self._ctx.home_root, self._ctx.target)
        return self._resolved

    def profile(self, profile_id: str) -> object:
        if profile_id not in self._profiles:
            from ..find_jobs.assess_contracts import AssessResumeInput
            from ..find_jobs.resume_input import resolve_profile

            profile = resolve_profile(
                AssessResumeInput(profile_id=profile_id), resolved=self.resolved(), home_root=self._ctx.home_root, target=self._ctx.target  # type: ignore[arg-type]
            )
            assert profile is not None
            self._profiles[profile_id] = profile
        return self._profiles[profile_id]

    def bank(self, profile_id: str) -> object:
        """``story_bank.assess_bank`` for ``profile_id``: what its tailoring is offered and its re-assessment seals."""

        kept = self._banks.get(profile_id)
        if kept is None:
            from .. import story_bank

            kept = story_bank.assess_bank(home_root=self._ctx.home_root, target=self._ctx.target, profile_id=profile_id)
            if kept.profile_id is not None:  # ``None``: no bank could be read
                self._banks[profile_id] = kept
        return kept

    def source_marks(self, profile_id: str, *, title: str, posting_text: str) -> list[tuple[str, object]]:
        """``(id, revision mark)`` of everything ``tailored_resume.tailor_sources`` offers this job's tailoring, sorted.

        The same answers (every one the user gave) and the same pick of
        stories (``AssessBank.for_job``: the ones that match THIS posting, an
        answer with a story's id winning); only the two reads behind them are
        this trigger's, not this job's.
        """

        marks = self._answer_marks
        if marks is None:
            from ...private_records import PrivateRecordError
            from .. import story_bank

            try:
                answers = story_bank.answers_for_reuse(home_root=self._ctx.home_root, target=self._ctx.target, strict=True)
            except (story_bank.StoryBankError, PrivateRecordError):
                marks = {}  # what ``answers_for_reuse`` answers for a bank it cannot read
            else:
                marks = self._answer_marks = {key: item.revision_id for key, item in answers.items()}
        marks = dict(marks)
        job_bank = self.bank(profile_id).for_job(title=title, text=posting_text)  # type: ignore[attr-defined]
        for line in job_bank.bank_answers:
            if line.question_id in job_bank.job_stories and line.question_id not in marks:
                marks[line.question_id] = job_bank.marks.get(line.question_id) or "unmarked"
        return sorted(marks.items(), key=lambda item: item[0])

    def master_parts(self, profile: object) -> tuple[object, ...]:
        """``tailor_master.digest_parts`` for ``profile``: the same for every job of it."""

        profile_id = profile.profile_id  # type: ignore[attr-defined]
        kept = self._master_parts.get(profile_id)
        if kept is None:
            from ..tailor_master import digest_parts

            kept = digest_parts(self._ctx.home_root, self._ctx.target, profile, resolved=self.resolved())
            if kept:  # ``()``: no master, a detached profile, or a master that could not be read
                self._master_parts[profile_id] = kept
        return kept

    def basis(self):
        """An ``assessment_basis.BasisCheck`` on the workpad this trigger resolved.

        One a digest, as before: what a check read is kept by that module for
        as long as the workpad is unchanged, and what it could not read is
        not, so nothing is gained by holding one here.
        """

        from ..assessment_basis import BasisCheck

        return BasisCheck(home_root=self._ctx.home_root, target=self._ctx.target, resolved=self._resolved)

    def base(self, profile_id: str, job: str):
        """``quick_assess.read_quick_assessment``: the job's own stored assessment, one file.

        Where a profile's assessments are is asked once a trigger (it is one
        lookup of the bound project, 2 ms, which every job repeated), then a
        job's file is ``<sha256(job identity)>.json`` in it. Should the store
        ever name its files otherwise, every job is asked for as before.
        """

        from ..quick_assess import _read_stored, quick_assess_path

        if profile_id not in self._base_dirs:
            path = quick_assess_path(self._ctx.home_root, self._ctx.target, profile_id, job)
            self._base_dirs[profile_id] = path.parent if path.name == f"{_job_key(job)}.json" else None
            return _read_stored(path)
        directory = self._base_dirs[profile_id]
        if directory is not None and (directory / f"{_job_key(job)}.json").is_file():
            return _read_stored(directory / f"{_job_key(job)}.json")
        # Not in the folder the first job was read from (0.1.11.9: a home that was not migrated keeps a job nothing
        # has written since in its role's folder), or no folder is kept: the store says where this job's is.
        return _read_stored(quick_assess_path(self._ctx.home_root, self._ctx.target, profile_id, job))

    def job(self, profile_id: str, job: str) -> _Inputs | None:
        """The inputs last read for this job, so its four digests read its assessment once."""

        if self._job is not None and self._job[0] == (profile_id, job):
            return self._job[1]
        return None

    def keep_job(self, profile_id: str, job: str, found: _Inputs) -> None:
        self._job = ((profile_id, job), found)


def _inputs(ctx: StepContext, profile_id: str, job: str, shared: SharedInputs | None = None) -> _Inputs:
    """The profile, its stored (base) assessment of ``job`` and the posting that assessment was made on.

    ``shared``: a trigger's :class:`SharedInputs` (the workpad and the profile are then read once a trigger).
    """

    from ..assessment_basis import posting_sha256
    from ..find_jobs.assess_contracts import AssessResumeInput
    from ..find_jobs.contracts import FindJobsContractError
    from ..find_jobs.resume_input import resolve_profile
    from ..quick_assess import QuickAssessError, _resolve_workpad, read_quick_assessment

    if shared is not None:
        kept = shared.job(profile_id, job)
        if kept is not None:
            return kept
    try:
        if shared is not None:
            resolved, profile = shared.resolved(), shared.profile(profile_id)
        else:
            resolved = _resolve_workpad(ctx.home_root, ctx.target)
            profile = resolve_profile(AssessResumeInput(profile_id=profile_id), resolved=resolved, home_root=ctx.home_root, target=ctx.target)
    except (QuickAssessError, FindJobsContractError) as exc:
        raise StepError(error_code(exc), "the profile is not available") from exc
    assert profile is not None
    base = shared.base(profile_id, job) if shared is not None else read_quick_assessment(ctx.home_root, ctx.target, profile_id, job)
    if base is None:
        raise StepError(ERROR_ASSESSMENT_MISSING, "this job has no assessment for this profile yet; assess it first")
    if not base.posting_text:
        # Pasted posting text is never stored, so there is nothing to tailor to.
        raise StepError(ERROR_POSTING_TEXT_UNAVAILABLE, "the posting's text is not stored for this job")
    resolved_job = replace(base.job, text=base.posting_text)
    digest = base.posting_sha256 or posting_sha256(resolved_job.title, resolved_job.text)
    found = _Inputs(resolved, profile, base, resolved_job, digest)
    if shared is not None:
        shared.keep_job(profile_id, job, found)
    return found


def model_for(ctx: StepContext, step: str) -> str:
    """The adapter kind ``step`` runs with: ``pipeline.models.<step>``, else the project's configured model target."""

    from ..quick_assess import _default_model_target

    return ctx.setting.models.get(step) or _default_model_target(ctx.target).value


def _tailor_digest(ctx: StepContext, found: _Inputs, model_target: str | None, shared: SharedInputs | None = None) -> str:
    from ..tailored_resume import TAILOR_INSTRUCTIONS_DIGEST, tailor_sources

    profile = found.profile
    # Only what changes the tailored text. Of the profile: its id and its pinned resume's digest, never its
    # revision, label, titles or search settings (a rename must not re-tailor ten jobs).
    # Ids and revision marks only, so the resume itself is not read here: its digest is the profile's pinned one,
    # and which answers and stories a tailoring is offered does not depend on the resume's text.
    # 0.1.10.9 master P4: a tailoring that reads the master resume is also keyed by the master's revision, the
    # selector's version and the candidate rule (``tailor_master.digest_parts``). Nothing is added without a
    # master, so such a digest is exactly what it was.
    if shared is not None:
        marks = shared.source_marks(profile.profile_id, title=found.job.title, posting_text=found.job.text)  # type: ignore[attr-defined]
        master = shared.master_parts(profile)
    else:
        from ..tailor_master import digest_parts

        sources = tailor_sources(
            home_root=ctx.home_root, target=ctx.target, profile_id=profile.profile_id, resume_text="",  # type: ignore[attr-defined]
            title=found.job.title, posting_text=found.job.text,  # type: ignore[attr-defined]
        )
        marks = sorted((key, item.revision_id) for key, item in sources.items())
        master = digest_parts(ctx.home_root, ctx.target, profile, resolved=found.resolved)
    return _digest(
        "tailor",
        found.posting_sha256,
        profile.profile_id,  # type: ignore[attr-defined]
        profile.resume_ref.content_sha256,  # type: ignore[attr-defined]
        marks,
        [(row.requirement, row.status.value) for row in found.base.result.matrix],  # type: ignore[attr-defined]
        TAILOR_INSTRUCTIONS_DIGEST,
        model_target,
        *master,
    )


def tailor_digest(
    ctx: StepContext, profile_id: str, job: str, model_target: str | None = None, *, shared: SharedInputs | None = None
) -> str:
    """The tailor step's input digest for ``(profile_id, job)`` as its inputs are now. Raises :class:`StepError`.

    ``shared``: a trigger's :class:`SharedInputs`; the digest is the same with and without it.
    """

    return _tailor_digest(
        ctx, _inputs(ctx, profile_id, job, shared), model_target if model_target is not None else model_for(ctx, "tailor"), shared
    )


def _upstream(store: PipelineStore, claim: Claim, name: str) -> str:
    step = store.step(claim.profile_id, claim.job, name)
    if step is None or step.output_digest is None:
        raise StepError(ERROR_UPSTREAM_OUTPUT_MISSING, f"the {name} step has no output")
    return step.output_digest


def _base_stale_reason(ctx: StepContext, base: object, shared: SharedInputs | None = None) -> str | None:
    from ..assessment_basis import BasisCheck

    check = shared.basis() if shared is not None else BasisCheck(home_root=ctx.home_root, target=ctx.target)
    return check.reason(base)  # type: ignore[arg-type]


def input_digest(ctx: StepContext, store: PipelineStore, claim: Claim, *, shared: SharedInputs | None = None) -> str:
    """``claim``'s input digest, from its inputs as they are now and its upstream OUTPUT digests. Raises :class:`StepError`.

    ``shared``: a trigger's :class:`SharedInputs`; the digest is the same with and without it.
    """

    found = _inputs(ctx, claim.profile_id, claim.job, shared)
    if claim.name == "tailor":
        return _tailor_digest(ctx, found, claim.model_target, shared)
    if claim.name == "reassess":
        from .. import story_bank
        from ..assessment_basis import BasisCheck

        if shared is not None:
            current = shared.basis().current(claim.profile_id)
            bank = shared.bank(claim.profile_id)
        else:
            current = BasisCheck(home_root=ctx.home_root, target=ctx.target).current(claim.profile_id)
            bank = story_bank.assess_bank(home_root=ctx.home_root, target=ctx.target, profile_id=claim.profile_id)
        return _digest(
            "reassess",
            _upstream(store, claim, "tailor"),
            found.posting_sha256,
            None if current is None else [current.prompt_version, current.constraints_digest],
            bank.digest,
            claim.model_target,
        )
    if claim.name == "ats":
        return _digest("ats", _upstream(store, claim, "tailor"), found.posting_sha256, ATS_RULES_VERSION)
    return _digest(
        "label",
        _upstream(store, claim, "reassess"),
        _upstream(store, claim, "ats"),
        LABEL_RULE_VERSION,
        ctx.setting.label_min_ats,
        _base_stale_reason(ctx, found.base, shared),
    )


def unchanged(ctx: StepContext, store: PipelineStore, claim: Claim, digest: str) -> bool:
    """``claim`` was last done with ``digest`` and its output is still there: it finishes without running.

    Never the tailor step: it is only claimed again when its digest changed
    or the user forced it.
    """

    if claim.name == "tailor" or claim.done_digest != digest:
        return False
    step = store.step(claim.profile_id, claim.job, claim.name)
    if step is None or step.output_ref is None:
        return False
    return (_scout_root(ctx.home_root, ctx.target) / step.output_ref).is_file()


# --- the steps ------------------------------------------------------------------------------


def _model_target(claim: Claim):
    from ..find_jobs.contracts import ModelTarget

    try:
        return ModelTarget(claim.model_target)
    except ValueError:
        raise StepError("model_target_unavailable", "the step's model target is not an adapter kind") from None


def _stored_tailored(ctx: StepContext, profile_id: str, job: str):
    from ..tailored_resume import list_tailored_resumes

    items = list_tailored_resumes(ctx.home_root, ctx.target, profile_id=profile_id, job_identity=job)
    if not items:
        raise StepError(ERROR_UPSTREAM_OUTPUT_MISSING, "the tailored resume is not stored")
    return items[0]


def _plain(markdown: str) -> str:
    """The tailored markdown as a reader sees it: without the per-line source comments."""

    return "\n".join(line.rstrip() for line in _COMMENT.sub("", markdown).splitlines()).strip() + "\n"


def _job_input(job: object):
    from ..find_jobs.assess_contracts import AssessJobInput

    return AssessJobInput(job_url=job.source_url or job.job_identity)  # type: ignore[attr-defined]


def _tailor_record(ctx: StepContext, profile_id: str | None, job: str) -> dict[str, object] | None:
    return _read_json(_record_path(ctx, TAILOR_DIR, profile_id, job))


def _write_tailor_record(ctx: StepContext, claim: Claim, outcome: str, record_sha256: str | None) -> None:
    path = _record_write_path(ctx, TAILOR_DIR, claim.job)
    _write_json(
        path,
        {
            "schema_version": TAILOR_RECORD_SCHEMA,
            "profile_id": claim.profile_id,
            "job_identity": claim.job,
            "outcome": outcome,
            # The stored resume's file as this step wrote it; ``None`` when the step kept the user's.
            "record_sha256": record_sha256,
            "updated_at": _now(),
        },
    )


def _users_resume(ctx: StepContext, claim: Claim, path: Path):
    """The tailored resume stored at ``path`` when it is the user's to keep, else ``None``.

    The user's: any stored resume that is not this step's own last tailoring,
    byte for byte. So one tailored on demand, one with a line choice or an
    edited line, and one written while this step's model call was out. A file
    that cannot be read is never replaced either (0.1.11.4 E1): ``_tailor``
    refuses before it calls a model (``_refuse_unreadable``).
    """

    from ..tailored_resume import read_tailored_resume

    stored = read_tailored_resume(path)
    if stored is None:
        return None
    record = _tailor_record(ctx, claim.profile_id, claim.job)
    if record is not None and record.get("outcome") == TAILOR_TAILORED:
        try:
            if record.get("record_sha256") == _bytes_digest(path.read_bytes()):
                return None  # the pipeline's own, untouched since: it may tailor it again
        except OSError:
            pass
    return stored


def _refuse_unreadable(path: Path) -> None:
    """A stored resume file that cannot be read is the user's: the step fails and the file is left as it is."""

    from ..suggestions import STORED_UNREADABLE, stored_unreadable

    if stored_unreadable(path):
        raise StepError(ERROR_STORE_UNWRITABLE, STORED_UNREADABLE)


def _keep(ctx: StepContext, claim: Claim, path: Path, stored: object) -> StepResult:
    """The step's result when the stored resume stays: it is the output, as the user left it."""

    markdown = stored.markdown  # type: ignore[attr-defined]
    _refuse_contact_data(markdown)
    _write_tailor_record(ctx, claim, TAILOR_KEPT_USER_EDITS, None)
    return StepResult(_ref(ctx, path), _bytes_digest(markdown.encode("utf-8")), TAILOR_KEPT_USER_EDITS)


def _tailor(ctx: StepContext, claim: Claim, found: _Inputs) -> StepResult:
    from ..find_jobs.assess_contracts import AssessResumeInput
    from ..tailored_resume import (
        TailorRequest,
        run_tailored_resume,
        save_tailor_response,
        tailored_resume_write_lock,
        tailored_resume_write_path,
    )

    path = tailored_resume_write_path(ctx.home_root, ctx.target, claim.profile_id, found.job.job_identity)  # type: ignore[attr-defined]
    _refuse_unreadable(path)  # before any model call
    users = _users_resume(ctx, claim, path)
    if users is not None:
        return _keep(ctx, claim, path, users)  # no model call: the user's resume is never replaced
    kept: list[tuple[Path, object]] = []

    def store(response):
        # Refused before anything is written: a tailoring with contact-shaped text never reaches the store.
        _refuse_contact_data(response.markdown)
        written = Path(response.stored_path)
        # Compare and swap: the stored resume is read again under the store's write lock (the one a line choice
        # holds), so what landed while the model call was out is kept and this tailoring is dropped.
        with tailored_resume_write_lock(written):
            _refuse_unreadable(written)
            landed = _users_resume(ctx, claim, written)
            if landed is not None:
                kept.append((written, landed))
                return landed
            save_tailor_response(response, home_root=ctx.home_root)
            _write_tailor_record(ctx, claim, TAILOR_TAILORED, _bytes_digest(written.read_bytes()))
        return response

    response = run_tailored_resume(
        TailorRequest(job=_job_input(found.job), resume=AssessResumeInput(profile_id=claim.profile_id), model_target=_model_target(claim)),
        home_root=ctx.home_root, target=ctx.target, config=ctx.config, resolved_job=found.job,  # type: ignore[arg-type]
        store=store,
        # With a master resume, the code's own selection stands in only for an answer that stayed invalid: a model
        # that is unavailable or timed out fails the step as before, so the queue's retries and lane backoff apply.
        fallback_codes=TAILOR_FALLBACK_CODES,
    )
    if kept:
        return _keep(ctx, claim, *kept[0])
    return StepResult(_ref(ctx, Path(response.stored_path)), _bytes_digest(response.markdown.encode("utf-8")), TAILOR_TAILORED)


def _reassess(ctx: StepContext, claim: Claim, found: _Inputs) -> StepResult:
    from ..find_jobs.assess_contracts import AssessRequest, AssessResumeInput
    from ..quick_assess import AssessVariant, run_quick_assessment

    tailored = _stored_tailored(ctx, claim.profile_id, claim.job)
    response = run_quick_assessment(
        AssessRequest(job=_job_input(found.job), resume=AssessResumeInput(profile_id=claim.profile_id), model_target=_model_target(claim)),
        home_root=ctx.home_root, target=ctx.target, config=ctx.config, resolved_job=found.job,  # type: ignore[arg-type]
        variant=AssessVariant(resume_text=_plain(tailored.markdown)),
    )
    return StepResult(_ref(ctx, Path(response.stored_path)), _digest("variant", response.result.to_json()))


def _ats(ctx: StepContext, claim: Claim, found: _Inputs) -> StepResult:
    from .. import ats_score
    from ..posting_keywords import extract_keywords, skills_from_markdown
    from ..resume_pdf import stored_resume_pdf

    tailored = _stored_tailored(ctx, claim.profile_id, claim.job)
    # Headerless: the pipeline never renders a PDF with contact data.
    rendered, file_name = stored_resume_pdf(tailored, home_root=ctx.home_root, form=None)
    keywords = extract_keywords(found.job.text, title=found.job.title, skills=skills_from_markdown(tailored.markdown))  # type: ignore[attr-defined]
    result = ats_score.score(rendered.pdf, tailored.result, keywords, file_name=file_name)
    # 0.1.11.9: the JOB's record; ``profile_id`` inside it is the role that asked.
    path = _record_write_path(ctx, ATS_DIR, claim.job)
    previous = _read_json(_record_path(ctx, ATS_DIR, claim.profile_id, claim.job))
    now = _now()
    record = {
        "schema_version": ATS_RECORD_SCHEMA,
        "profile_id": claim.profile_id,
        "job_identity": claim.job,
        "rules_version": ATS_RULES_VERSION,
        "tailored_sha256": _bytes_digest(tailored.markdown.encode("utf-8")),
        "posting_sha256": found.posting_sha256,
        "keywords": keywords.to_json(),
        "result": result.to_json(),
        "created_at": previous.get("created_at", now) if previous else now,
        "updated_at": now,
        "stored_path": os.fspath(path),
    }
    _refuse_contact_data(result.line, " ".join((*keywords.must, *keywords.nice, *keywords.title)))
    _write_json(path, record)
    return StepResult(_ref(ctx, path), _digest("ats", result.to_json(), record["keywords"]))


def _met(body: object) -> dict[str, int]:
    rows = body.matrix  # type: ignore[attr-defined]
    met = sum(1 for row in rows if row.status.value == "met")
    return {"met": met, "total": len(rows), "percent": round(100 * met / len(rows)) if rows else 0}


def label_for(*, verdict: str | None, open_questions: int, ats: int, min_ats: int, stale_reason: str | None) -> tuple[str, list[str]]:
    """The Scout label and why (codes). Pure: Scout's own rule, never a prediction."""

    reasons: list[str] = []
    if verdict != _MATCHED:
        reasons.append(REASON_NOT_MATCHED)
    if open_questions:
        reasons.append(REASON_OPEN_QUESTIONS)
    if ats < min_ats:
        reasons.append(REASON_ATS_BELOW_MINIMUM)
    if stale_reason is not None:
        reasons.append(REASON_ASSESSMENT_STALE)
    return (LABEL_NEEDS_ATTENTION if reasons else LABEL_RECOMMENDED), reasons


def _label(ctx: StepContext, claim: Claim, found: _Inputs) -> StepResult:
    variant = read_variant(ctx.home_root, ctx.target, claim.profile_id, claim.job)
    ats = read_ats(ctx.home_root, ctx.target, claim.profile_id, claim.job)
    if variant is None or ats is None:
        raise StepError(ERROR_UPSTREAM_OUTPUT_MISSING, "the tailored assessment or the ATS record is not stored")
    result = ats.get("result")
    score = result.get("score") if isinstance(result, dict) else None
    if type(score) is not int:
        raise StepError(ERROR_UPSTREAM_OUTPUT_MISSING, "the ATS record has no score")
    verdict = None if variant.result.verdict is None else variant.result.verdict.value
    open_questions = len(variant.result.structured_questions) or len(variant.result.questions)
    stale = _base_stale_reason(ctx, found.base)
    # 0110-10-03: a lone question on a one-of-a-list row is a minor gap (``requirement_weights``): asked, and no reason
    # to hold the label. The record below still counts every open question.
    holding = blocking_question_count(variant.result.matrix, variant.result.structured_questions) if variant.result.structured_questions else open_questions
    label, reasons = label_for(
        verdict=verdict, open_questions=holding, ats=score, min_ats=ctx.setting.label_min_ats, stale_reason=stale
    )
    base = found.base
    path = _record_write_path(ctx, LABEL_DIR, claim.job)
    previous = _read_json(_record_path(ctx, LABEL_DIR, claim.profile_id, claim.job))
    now = _now()
    record = {
        "schema_version": LABEL_RECORD_SCHEMA,
        "name": LABEL_NAME,
        "wording": LABEL_WORDING,
        "profile_id": claim.profile_id,
        "job_identity": claim.job,
        "rule_version": LABEL_RULE_VERSION,
        "label": label,
        "reasons": reasons,
        "ats_score": score,
        "min_ats": ctx.setting.label_min_ats,
        "base_verdict": None if base.result.verdict is None else base.result.verdict.value,  # type: ignore[attr-defined]
        "tailored_verdict": verdict,
        "open_questions": open_questions,
        "base_stale_reason": stale,
        # "72 -> 86 after tailoring": the share of the posting's requirements each assessment found met.
        "requirements_met": {"base": _met(base.result), "tailored": _met(variant.result)},  # type: ignore[attr-defined]
        "created_at": previous.get("created_at", now) if previous else now,
        "updated_at": now,
        "stored_path": os.fspath(path),
    }
    _write_json(path, record)
    return StepResult(_ref(ctx, path), _digest("label", label, reasons, score, record["requirements_met"]))


_RUNNERS = {"tailor": _tailor, "reassess": _reassess, "ats": _ats, "label": _label}


def run_step(ctx: StepContext, store: PipelineStore, claim: Claim) -> StepResult:
    """Run ``claim``'s step and return where its output is. Raises :class:`StepError` or the step function's own error."""

    del store  # every input is read from the stores; the queue is the runner's
    return _RUNNERS[claim.name](ctx, claim, _inputs(ctx, claim.profile_id, claim.job))


# --- the entry: enqueue one job --------------------------------------------------------------


def enqueue_job(
    profile_id: str,
    job: str,
    *,
    force: bool = False,
    home_root: Path,
    target: Path,
    trigger: str = "process_now",
    setting: PipelineSetting | None = None,
    store: PipelineStore | None = None,
    sole: bool = False,
) -> dict[str, object]:
    """Queue ``job``'s pipeline for ``profile_id`` and return ``{result, profile_id, job, input_digest}``.

    0.1.11.9: a job has ONE set of steps; ``profile_id`` is the role they are under (``triggers.process_now``
    finds it; the role a caller names only says who asked). ``sole``: ``store.enqueue``'s, so a job that
    another writer queued under another role meanwhile gets no second set.

    ``result`` is ``store.enqueue``'s: ``enqueued``, or a no-op when the
    tailor step is already done, queued or failed with these inputs
    (``force`` re-opens it whatever its digest). The tailor step runs with
    ``pipeline.models.tailor`` and the re-assessment with
    ``pipeline.models.reassess`` (each: else the project's configured model
    target). Raises :class:`StepError` when the job cannot enter the pipeline
    (no profile, no assessment yet, no stored posting text).
    """

    from .settings import pipeline_setting

    home_root, target = Path(home_root), Path(target)
    ctx = StepContext(home_root, target, setting=setting if setting is not None else pipeline_setting(home_root, target))
    tailor_model, reassess_model = model_for(ctx, "tailor"), model_for(ctx, "reassess")
    digest = tailor_digest(ctx, profile_id, job, tailor_model)
    opened = store if store is not None else PipelineStore(pipeline_path(home_root, target))
    try:
        result = opened.enqueue(
            profile_id, job, "tailor", input_digest=digest, trigger=trigger, lane=lane_for(tailor_model),
            model_target=tailor_model, downstream_lanes={"reassess": (lane_for(reassess_model), reassess_model)}, force=force,
            sole=sole,
        )
    finally:
        if store is None:
            opened.close()
    return {"result": result, "profile_id": profile_id, "job": job, "input_digest": digest}


# --- the read side --------------------------------------------------------------------------


def _role(profile_id: str | None) -> str:
    """What the job stores take where a role is asked and the caller names none: the job's own record."""

    from ..quick_assess import JOB_RECORD

    return JOB_RECORD if profile_id is None else profile_id


def read_variant(home_root: Path, target: Path, profile_id: str | None, job: str):
    """The assessment of ``job`` against the tailored resume (``quick_assess.read_tailored_variant``).

    0.1.11.9: the job's one, whichever role ``profile_id`` names (``None``: none named).
    """

    from ..quick_assess import read_tailored_variant

    return read_tailored_variant(Path(home_root), Path(target), _role(profile_id), job)


def read_ats(home_root: Path, target: Path, profile_id: str | None, job: str) -> dict[str, object] | None:
    """The stored Scout ATS record of ``job``'s tailored resume, or ``None``. The job's one (0.1.11.9)."""

    return _read_json(_record_path(StepContext(Path(home_root), Path(target)), ATS_DIR, profile_id, job))


def read_label(
    home_root: Path, target: Path, profile_id: str | None, job: str, *, scout_root: Path | None = None
) -> dict[str, object] | None:
    """The stored Scout label record of ``job``, or ``None``. The job's one (0.1.11.9), whichever role is named.

    ``scout_root``: the project's Scout folder (``pipeline_path(...).parent.parent``) when the caller holds it. A
    caller that lists many jobs passes it, so the bound project is looked up once and not once a job.
    """

    if scout_root is not None:
        return _read_json(job_record_path(Path(scout_root) / LABEL_DIR, profile_id, job))
    return _read_json(_record_path(StepContext(Path(home_root), Path(target)), LABEL_DIR, profile_id, job))


def job_outputs(home_root: Path, target: Path, profile_id: str | None, job: str) -> dict[str, object]:
    """What the pipeline has stored for ``job``: codes, numbers and paths, never a text.

    0.1.11.9: the job's own records; ``profile_id`` (``None``: no role named) selects nothing.
    """

    from ..quick_assess import read_quick_assessment
    from ..tailored_resume import list_tailored_resumes

    home_root, target = Path(home_root), Path(target)
    base = read_quick_assessment(home_root, target, _role(profile_id), job)
    variant = read_variant(home_root, target, profile_id, job)
    tailored = list_tailored_resumes(home_root, target, profile_id=_role(profile_id), job_identity=job)
    ats = read_ats(home_root, target, profile_id, job)
    label = read_label(home_root, target, profile_id, job)
    tailor = _tailor_record(StepContext(home_root, target), profile_id, job)
    # What the tailor step last did with this job's resume: ``tailored``, or ``tailor_kept_user_edits`` (it is the user's).
    tailor_outcome = tailor.get("outcome") if tailor is not None and tailor.get("outcome") in TAILOR_OUTCOMES else None

    def assessment(item: object | None) -> dict[str, object] | None:
        if item is None:
            return None
        verdict = item.result.verdict  # type: ignore[attr-defined]
        return {
            "verdict": None if verdict is None else verdict.value,
            "requirements_met": _met(item.result),  # type: ignore[attr-defined]
            "updated_at": item.updated_at,  # type: ignore[attr-defined]
            "stored_path": item.stored_path,  # type: ignore[attr-defined]
        }

    result = ats.get("result") if ats else None
    return {
        "base_assessment": assessment(base),
        "tailored_assessment": assessment(variant),
        "tailored_resume": (
            None if not tailored else {
                "markdown_path": tailored[0].markdown_path, "stored_path": tailored[0].stored_path, "updated_at": tailored[0].updated_at,
                "outcome": tailor_outcome,
            }
        ),
        "ats": None if not isinstance(result, dict) else {"score": result.get("score"), "line": result.get("line"), "stored_path": ats.get("stored_path")},  # type: ignore[union-attr]
        "label": None if label is None else {key: label.get(key) for key in ("name", "label", "reasons", "ats_score", "requirements_met", "updated_at", "stored_path")},
    }


__all__ = [
    "ATS_DIR",
    "ATS_RULES_VERSION",
    "LABELS",
    "LABEL_DIR",
    "LABEL_NAME",
    "LABEL_NEEDS_ATTENTION",
    "LABEL_REASONS",
    "LABEL_RECOMMENDED",
    "LABEL_RULE_VERSION",
    "LABEL_WORDING",
    "SharedInputs",
    "TAILOR_DIR",
    "TAILOR_KEPT_USER_EDITS",
    "TAILOR_OUTCOMES",
    "TAILOR_TAILORED",
    "StepContext",
    "StepError",
    "StepResult",
    "enqueue_job",
    "error_code",
    "input_digest",
    "job_outputs",
    "job_record_path",
    "label_for",
    "model_for",
    "read_ats",
    "read_label",
    "read_variant",
    "run_step",
    "tailor_digest",
    "unchanged",
]
