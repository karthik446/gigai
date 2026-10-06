"""The shared find-jobs assessment core: one prompt, one model call, one parse.

P1 (v0.1.9 API-first plan) lifts the per-posting model loop out of
``proposal_execution._assess_node_body`` so a later standalone assess (API/CLI,
no run) can call exactly the same code the graph's assess node uses:

    prompt build -> model invoke -> extract JSON -> normalize -> validate ->
    at most ONE retry with the validation error fed back (U22)

``assess_node`` keeps everything around that loop -- candidate selection,
reuse/skip, sealing, journaling and progress -- and this module never touches
any of it.  Two seams stay in the caller by design (plan constraints C1/C2):

- the model adapter is resolved by ``proposal_execution`` through its own
  module attribute ``resolve_model_adapter`` (the production test transport
  and the unit tests patch that exact name); this module only ever receives
  the already-resolved binding;
- the strict parser (``proposals.parse_assessment_proposal``, which
  ``bindings._assess_bound`` swaps at call time) is passed in as ``parse=``,
  never imported here.

The prompt text lives in the packaged resource
``scout/data/instructions/assess.md`` (shipped via ``pyproject.toml``
package-data) as a paragraph template.  ``render_assess_prompt`` splits the
template on blank lines BEFORE substitution, drops the paragraph that carries
``{{validation_error}}`` when there is no error to feed back, substitutes each
``{{name}}`` placeholder in a single pass (substituted text is never rescanned),
and re-joins with blank lines -- byte-identical to the pre-P1 hand-built prompt
for the same inputs (``tests/behaviors/scout_find_jobs/test_assessment_core.py``
pins the golden strings).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from importlib import resources
import json
import re

from ..adapters.port import ModelInvocationError, NormalizedUsage
from ..canonical import digest_imported_bytes
from .call_metrics import note_invalid_output
from .find_jobs.assess_contracts import (
    MAX_MASTER_ID_CHARS,
    MAX_PICK_LINES,
    MAX_STRUCTURED_SUGGESTIONS,
    MAX_SUGGESTION_PHRASE_CHARS,
    MAX_SUGGESTION_WHY_CHARS,
    PICK_SECTIONS,
    SUGGESTION_KINDS,
    SUGGESTION_KINDS_WITHOUT_RESUME,
    AssessmentPick,
    AssessmentSuggestion,
)
from .find_jobs.contracts import (
    ELIGIBILITY_ROW_IDS,
    MAX_ALTERNATIVE_CHARS,
    MAX_CLASS_BASIS_CHARS,
    MAX_ROW_ALTERNATIVES,
    MAX_ROW_SOURCES,
    MAX_SOURCE_CHARS,
    FindJobsContractError,
    NotAssessedReason,
    is_row_id,
)
from .find_jobs.work_mode import in_person_modes
from .question_ids import normalize_question_id
from .requirement_weights import bound_rows, cap_list_item_questions, cap_mandatory_questions, settled_verdict
from .requirements_list import ListedRequirement, check_listed, extracted, fold
from .stated_check import MasterFacts, read_master, settle_stated
from .suggestion_check import MasterLine, check_suggestions, clean_citations, parse_master_lines, truthful_notes, with_verbatim_evidence, without_unknown_places
from .resume_gate import (
    HOLD_QUESTION,
    gate,
    is_authorization_question,
    is_authorization_row,
    says_no_sponsorship,
    unasked_message,
    unasked_rows,
    uses_v9_rules,
)
from .resume_privacy import model_resume
from .untrusted_text import fence_untrusted_posting

_INSTRUCTIONS_RESOURCE = "scout/data/instructions/assess.md"
_ROLE = "reviewer"

_MAX_PROMPT_POSTING_TEXT = 12_000
_MAX_PROMPT_RESUME_TEXT = 12_000
_MAX_PROMPT_VALIDATION_ERROR = 300

_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
_VALIDATION_PLACEHOLDER = "{{validation_error}}"
_PRIOR_ANSWERS_PLACEHOLDER = "{{prior_answers}}"
_BANK_ANSWERS_PLACEHOLDER = "{{bank_answers}}"
_REMOTE_ONLY_PLACEHOLDER = "{{remote_only_area}}"
_IN_PERSON_PLACEHOLDER = "{{in_person_mode}}"
# 0.1.11 N3 (assessment v9, SPEC 1.1): the placeholders a v9 ``assess.md`` may carry. The shipped v8 file carries
# none of them, so nothing below changes a byte of a v8 prompt. A paragraph that carries one is dropped when the
# assessment has nothing to put there (``render_assess_prompt``):
# - ``{{id_example}}``: an id comment as the RESUME block shows it; dropped when the block shows no ids;
# - ``{{note_example}}``: a private note line as the block shows one; dropped when the block shows no note;
# - ``{{pick_lines}}``: how many lines the pick should hold; dropped when the block shows no ids;
# - ``{{requirements}}``: the posting's stored requirement list, fenced as untrusted; dropped without a list.
PLACEHOLDER_ID_EXAMPLE = "id_example"
PLACEHOLDER_NOTE_EXAMPLE = "note_example"
PLACEHOLDER_PICK_LINES = "pick_lines"
PLACEHOLDER_REQUIREMENTS = "requirements"
_ID_PLACEHOLDERS = (PLACEHOLDER_ID_EXAMPLE, PLACEHOLDER_PICK_LINES)
REQUIREMENTS_BLOCK_HEADER = "REQUIREMENTS (id | class | requirement | the posting wording behind the class):"

#: The name of the shipped ``assess.md`` wording. v4 (0110-034) adds the
#: STORY BANK paragraph: the profile's answered questions (id, the question
#: as asked, a one-line answer) and the rule to reuse one that covers a
#: requirement instead of asking again. The paragraph is dropped when no
#: bank answer is offered, so such a prompt renders exactly as v3-r2 did.
#: v5 (0110-038) adds the CANDIDATE WORK MODE paragraph: the candidate's work
#: mode (remote only / hybrid / on-site) and own area, with the rule a
#: remote-only candidate is never matched on a role that needs presence. A
#: candidate with no work mode (or "any") gets no such paragraph, and that
#: prompt renders byte for byte as v4 did, so it keeps the v4 name
#: (``assess_prompt_version``).
#: v6 (0110-048) changed the hybrid paragraph's words ("remote roles, and
#: hybrid or on-site roles in their own area"; decision #207), so a hybrid
#: prompt got a name of its own while the others kept theirs.
#: v7 (0.1.10.7 P5) fences the posting (ROLE, COMPANY, LOCATION and POSTING
#: TEXT) as untrusted and adds the UNTRUSTED TEXT rule (``untrusted_text``).
#: That changes the bytes of EVERY prompt, whatever the work mode, so all
#: three names below moved to v7 together: an assessment sealed as v4, v5 or
#: v6 is older wording now (``assessment_basis``: ``older_prompt``). The three
#: constants stay, one per paragraph a prompt can carry, so a later change to
#: one work mode's words can rename that one alone, as v6 did.
#: v8 (0110-10-03) weighs requirements: the LIST_ITEM class (one tool of a
#: list a sentence names), a verdict one open LIST_ITEM question does not
#: hold (rule 7), a row for every stated requirement and no 12-row cap
#: (``requirement_weights``). The rules are every prompt's, so again all
#: three names moved together: an assessment sealed as v7 has a matrix cut
#: to 12 rows and no weights, and reads as older wording.
#: ``tests/behaviors/scout_find_jobs/test_assessment_core.py`` pins it with
#: the file's digest.
ASSESS_PROMPT_VERSION = "assess-prompt-v9"
#: The name of a prompt rendered with no CANDIDATE WORK MODE paragraph.
ASSESS_PROMPT_VERSION_NO_WORK_MODE = "assess-prompt-v9"
#: The name of a prompt rendered for a HYBRID candidate (decision #207).
ASSESS_PROMPT_VERSION_HYBRID = "assess-prompt-v9"
#: The versions the shipped ``assess.md`` renders today. An assessment sealed
#: with none of them is older wording. One sealed with one of them is current
#: when the constraints digest (which includes the work mode) is the same and
#: the version is the one that work mode renders now (``assess_prompt_version``).
CURRENT_ASSESS_PROMPT_VERSIONS = frozenset({ASSESS_PROMPT_VERSION, ASSESS_PROMPT_VERSION_NO_WORK_MODE, ASSESS_PROMPT_VERSION_HYBRID})

_WORK_MODES = ("remote", "hybrid", "onsite")
_IN_PERSON_LABELS = {"hybrid": "hybrid", "onsite": "on-site"}


def _in_person_mode_text(preference: str) -> str:
    # 0110-048: the roles named come from the filter's rule (work_mode.in_person_modes).
    roles = " or ".join(_IN_PERSON_LABELS[mode] for mode in in_person_modes(preference))
    return f"{_IN_PERSON_LABELS[preference]} (remote roles, and {roles} roles in their own area)"


_IN_PERSON_MODE_TEXT = {mode: _in_person_mode_text(mode) for mode in ("hybrid", "onsite")}


def normalize_work_mode(value: object) -> str:
    """``remote`` / ``hybrid`` / ``onsite``, or ``""`` for none.

    Takes ``find-jobs.json``'s ``work_mode`` (a ``WorkModePreference`` or its
    string) or a profile's ``search_settings.work_mode``. ``any``, ``None``
    and anything unknown are "no work mode": the prompt then carries no
    CANDIDATE WORK MODE paragraph.
    """

    text = str(getattr(value, "value", value) or "").strip().lower()
    return text if text in _WORK_MODES else ""


def assess_prompt_version(work_mode: object = "") -> str:
    """The version of the prompt a candidate with ``work_mode`` is assessed with."""

    mode = normalize_work_mode(work_mode)
    if mode == "hybrid":
        return ASSESS_PROMPT_VERSION_HYBRID
    return ASSESS_PROMPT_VERSION if mode else ASSESS_PROMPT_VERSION_NO_WORK_MODE

# Exception mapping at the model boundary, exactly as the pre-P1 loop had it:
# a transport/adapter failure whose code is one of these is the operator's own
# policy refusing the call (MODEL_DENIED); every other transport failure is
# MODEL_UNAVAILABLE.  An unknown exception type is only mapped when it carries
# one of the three known codes; otherwise it is re-raised untouched.
_DENIED_INVOCATION_CODES = frozenset({"network_denied", "model_denied", "credential_denied"})
_MAPPED_FOREIGN_CODES = frozenset({"model_denied", "network_denied", "model_unavailable"})


def _instruction_bytes() -> bytes:
    return resources.files("gigai").joinpath(*_INSTRUCTIONS_RESOURCE.split("/")).read_bytes()


def load_assess_instructions() -> str:
    """The packaged assess prompt template, minus the file's single trailing newline."""

    text = _instruction_bytes().decode("utf-8")
    if text.endswith("\n"):
        text = text[:-1]
    return text


INSTRUCTIONS_DIGEST = digest_imported_bytes(_instruction_bytes())
"""Digest of the shipped ``assess.md`` bytes (``digest_imported_bytes``); changes only with the file."""


def template_takes(name: str) -> bool:
    """Whether the shipped ``assess.md`` carries the placeholder ``{{name}}``.

    0.1.11 N3: what an assessment sends follows the prompt that is shipped.
    The RESUME block is rendered with master line ids, and a posting's stored
    requirement list is sent, only when the template has a paragraph that
    says what they are (:func:`prompt_reads_ids`, ``PLACEHOLDER_REQUIREMENTS``).
    With the v8 file both answer ``False`` and every prompt is what it was.
    """

    return "{{" + name + "}}" in load_assess_instructions()


def prompt_reads_ids() -> bool:
    """Whether the shipped prompt explains master line ids (and so may be given a RESUME block that shows them)."""

    return any(template_takes(name) for name in _ID_PLACEHOLDERS)


@dataclass(frozen=True)
class AssessJob:
    """What the prompt needs to know about one posting -- nothing sealed."""

    title: str
    company: str
    location: str
    posting_text: str


@dataclass(frozen=True)
class PriorAnswer:
    """One previously answered question, as the prompt needs it: id + text
    only (never the record/revision it lives in -- that is
    ``experience_answers.PriorAnswer``'s job; the caller converts)."""

    question_id: str
    prompt: str
    answer: str


@dataclass(frozen=True)
class BankAnswer:
    """One story bank entry as the prompt gets it (0110-034): the id, the
    question as it was asked ("" when only the id is known) and a one-line,
    privacy-checked answer (``story_bank.prompt_summaries`` builds them)."""

    question_id: str
    question: str
    summary: str


@dataclass(frozen=True)
class AssessContext:
    """The candidate side of one assessment."""

    resume_text: str
    visa_sponsorship_required: bool
    # P2 (v0.1.9): operator answers 5 -- {{countries}} (from find-jobs.json)
    # and {{titles}} (one line, from the profile/effective config). Both
    # default empty so every P1-era caller (and the golden-prompt tests that
    # never pass them) keeps rendering the same way.
    countries: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()
    # P3 (v0.1.9): prior answers from earlier assessments (Q&A loop), keyed
    # by NORMALIZED question_id (``question_ids.normalize_question_id``) by
    # the caller before this reaches the prompt. Empty by default so every
    # pre-P3 caller (and the golden-prompt tests) keeps rendering the same
    # way -- the {{prior_answers}} paragraph is dropped from the prompt
    # entirely when this is empty, exactly like {{validation_error}}.
    prior_answers: tuple[PriorAnswer, ...] = ()
    # assess-prompt-v2 (v0.1.9), operator decision: the candidate's OWN
    # location (find-jobs.json's ``location`` field, as the operator wrote
    # it -- "Denver, CO", "Toronto, ON, Canada"...) reaches the prompt as
    # {{candidate_location}} so assess.md rule 4 can decide a posting's
    # state/province restriction from it instead of asking every time.
    # Empty renders as "unknown" (rule 4 then asks ONCE, with the stable
    # ``location:<country>_region`` id). Defaults empty so every earlier
    # caller and golden keeps rendering the same way.
    location: str = ""
    # assess-prompt-v4 (0110-034): the profile's story bank, capped and
    # privacy-checked by the caller. Empty by default: the STORY BANK
    # paragraph is then dropped, like {{prior_answers}}, so every earlier
    # caller (the run's assess node, the goldens) renders the same way.
    bank_answers: tuple[BankAnswer, ...] = ()
    # assess-prompt-v5 (0110-038): the candidate's work mode, "remote",
    # "hybrid" or "onsite" (``normalize_work_mode``). Empty by default (and
    # for "any"): the CANDIDATE WORK MODE paragraph is then dropped and the
    # prompt renders as v4 did. The area it names is ``location`` above.
    work_mode: str = ""
    # 0.1.11 N3 (assessment v9, SPEC 1.1). All empty by default, and then nothing of v9 is in the prompt or
    # read from the answer. ``resume_ids``: the master line ids the RESUME block shows in id comments (what a
    # row's ``sources`` and the pick may name); ``resume_notes``: the block shows at least one note comment;
    # ``pick_lines``: how many lines the pick should hold; ``requirements``: the posting's stored requirement
    # list the prompt carries (the answer must then hold exactly its ids, ``requirements_list.check_listed``).
    resume_ids: tuple[str, ...] = ()
    resume_notes: bool = False
    pick_lines: int = 0
    requirements: tuple[ListedRequirement, ...] = ()


def build_assess_context(
    *,
    resume_text: str,
    visa_sponsorship_required: bool = False,
    countries: tuple[str, ...] = (),
    titles: tuple[str, ...] = (),
    location: str = "",
    bank: object | None = None,
    work_mode: object = "",
    resume_ids: tuple[str, ...] = (),
    resume_notes: bool = False,
    pick_lines: int = 0,
    requirements: tuple[ListedRequirement, ...] = (),
) -> AssessContext:
    """The candidate side of one assessment: the ONE builder every path uses.

    0110-034b / 0110-035: the job page's quick assessment, assess-all (which
    goes through it) and a find-jobs run's assess node all build their
    prompt's context here, so no path can render a prompt that lacks what
    another sends: the candidate's constraints (sponsorship need, eligible
    countries, own location, target titles), the profile's story bank
    (``bank``: a ``story_bank.AssessBank``, or ``None`` for no bank) and,
    since 0110-038, the candidate's work mode (``work_mode``: remote, hybrid
    or onsite; ``any``/none adds nothing to the prompt).
    """

    return AssessContext(
        resume_text=resume_text,
        visa_sponsorship_required=bool(visa_sponsorship_required),
        countries=tuple(countries),
        titles=tuple(titles),
        prior_answers=tuple(getattr(bank, "prior_answers", ()) or ()),
        location=location or "",
        bank_answers=tuple(getattr(bank, "bank_answers", ()) or ()),
        work_mode=normalize_work_mode(work_mode),
        resume_ids=tuple(resume_ids),
        resume_notes=bool(resume_notes),
        pick_lines=int(pick_lines),
        requirements=tuple(requirements),
    )


def constraints_digest(
    *, visa_sponsorship_required: bool, countries: tuple[str, ...] = (), location: str = "", work_mode: object = ""
) -> str:
    """A digest of the candidate constraints a verdict depends on (rules 4 and 5).

    Sponsorship need, eligible countries (order and case do not matter), the
    candidate's own location and, since 0110-038, the work mode. A candidate
    with no work mode (or "any") digests exactly as before 0110-038, so only
    the assessments of a candidate whose prompt gained the CANDIDATE WORK
    MODE paragraph are refreshed. Target titles are left out on purpose:
    rule 6 makes them context only, never a reason for a verdict, so a title
    edit does not void an assessment. A find-jobs run seals this with its
    assessments; a later run does not reuse an unchanged posting's
    assessment made under other constraints.
    """

    value: dict[str, object] = {
        "visa_sponsorship_required": bool(visa_sponsorship_required),
        "countries": sorted({item.strip().upper() for item in countries if item and item.strip()}),
        "location": " ".join((location or "").split()),
    }
    mode = normalize_work_mode(work_mode)
    if mode:
        value["work_mode"] = mode
    return digest_imported_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


@dataclass(frozen=True)
class AssessAttempt:
    """The outcome of ``assess_once``: one parsed result, or one reason it has none.

    ``attempts`` counts model invocations that RETURNED an answer (the
    pre-P1 loop's ``model_attempts``); a transport failure before any answer
    leaves it at 0.  ``validation_error`` is the last parse/validation error
    seen, if any (present on a successful retry too).
    """

    ok: bool
    parsed: object | None
    not_assessed_reason: NotAssessedReason | None
    usage: NormalizedUsage | None
    attempts: int
    validation_error: str | None
    # assess-prompt-v3-r1 (v0.1.9): bookkeeping for the eval, no contract
    # change. ``dropped_questions`` counts the questions the model attached
    # to a ``not_a_match`` verdict that ``_strip_not_a_match_questions``
    # removed before validation (the product never shows a question on a
    # failed verdict); ``dropped_question_ids`` names the structured ones.
    # Both describe the SUCCESSFUL attempt only and default to none, so every
    # earlier constructor (and ``invoke_json_once``, which knows nothing of
    # assessments) keeps building attempts the same way.
    dropped_questions: int = 0
    dropped_question_ids: tuple[str, ...] = ()
    # uat-bug-046: True when the model answered Matched on a thin requirement
    # matrix for a long posting (``posting_looks_incomplete``): the answer is
    # withheld, ``validation_error`` carries ``POSTING_INCOMPLETE_MESSAGE``.
    incomplete_posting: bool = False
    # 0.1.11 N3 (assessment v9): what the SUCCESSFUL attempt's answer carried beside the normalized payload
    # (:class:`AssessExtras`: the pick, the structured suggestions, what the boundary dropped). ``None`` for a
    # failed attempt and for ``invoke_json_once``. The run path ignores it; ``quick_assess`` merges it into the body.
    extras: "AssessExtras | None" = None
    # 0.1.11 N3b (orchestrator #14): the questions on ``list_item`` rows the boundary dropped past the cap
    # (``requirement_weights.MAX_LIST_ITEM_QUESTIONS``), counted like ``dropped_questions``: the SUCCESSFUL
    # attempt's only, 0 and none by default. Kept apart from that count, which is the not_a_match strip's.
    capped_questions: int = 0
    capped_question_ids: tuple[str, ...] = ()
    # 0.1.11 GUARDFIX (orchestrator #87): set, on a SUCCESSFUL attempt only, when ``assess_once(keep_thin=True)`` kept an
    # answer the incomplete-posting guard would have withheld: the number of requirement rows it holds (1 or 2). The
    # caller may retry it once and, if it stays thin, stores it with a note instead of refusing it.
    thin_requirements: int | None = None

    def __post_init__(self) -> None:
        if self.ok and (self.parsed is None or self.not_assessed_reason is not None):
            raise ValueError("a successful assess attempt carries a parsed result and no reason")
        if not self.ok and self.not_assessed_reason is None:
            raise ValueError("a failed assess attempt must name its not-assessed reason")


def render_assess_prompt(job: AssessJob, ctx: AssessContext, validation_error: str | None = None) -> str:
    """Render the real find-jobs assessment prompt (U25) from the packaged template.

    Includes the role/title/company/location and the bounded posting text
    (inside the untrusted fence, ``untrusted_text``), the resume text, the
    candidate's constraints (sponsorship, eligible countries, own location,
    target titles), and a precise JSON schema so the
    model returns a shape that parses on the first try.  On a retry (U22),
    the prior validation error is fed back: the template's last paragraph
    (the one carrying ``{{validation_error}}``) names the violated bound or
    rule -- ``proposals.validate_assessment_bounds`` produces messages with
    the count and the limit ("questions has 44 items; at most 40 allowed",
    "verdict matched_above_threshold but 1 hard requirement is unmet (rule
    7 ...)") -- and tells the model it cannot see the rejected attempt
    (every attempt is a fresh, ephemeral session), so it produces a fresh
    answer instead of "fixing" one it never saw.
    """

    # 0.1.10.7 P5: everything a stranger wrote goes inside one fence the text cannot close.
    posting = (
        f"ROLE: {job.title}\nCOMPANY: {job.company}\nLOCATION: {job.location or 'unspecified'}\n"
        f"POSTING TEXT:\n{job.posting_text[:_MAX_PROMPT_POSTING_TEXT]}"
    )
    values = {
        "posting": fence_untrusted_posting(posting),
        "visa_required": "yes" if ctx.visa_sponsorship_required else "no",
        "resume_text": model_resume(ctx.resume_text).text[:_MAX_PROMPT_RESUME_TEXT],
        "validation_error": (validation_error or "")[:_MAX_PROMPT_VALIDATION_ERROR],
        "countries": ", ".join(ctx.countries) if ctx.countries else "any",
        "titles": ", ".join(ctx.titles) if ctx.titles else "unspecified",
        "candidate_location": ctx.location.strip() or "unknown",
        "remote_only_area": ctx.location.strip() or "unknown",
        "in_person_mode": _IN_PERSON_MODE_TEXT.get(ctx.work_mode, ""),
        "in_person_area": ctx.location.strip() or "unknown",
        "prior_answers": "\n".join(
            f"- {item.question_id}: {item.answer}" for item in ctx.prior_answers
        ),
        "bank_answers": "\n".join(
            f"- {item.question_id} | asked: {item.question} | answer: {item.summary}"
            if item.question
            else f"- {item.question_id} | answer: {item.summary}"
            for item in ctx.bank_answers
        ),
        # 0.1.11 N3 (v9): each is in the prompt only when the template carries its placeholder (the v8 file: none).
        PLACEHOLDER_ID_EXAMPLE: f"<!-- id:{ctx.resume_ids[0]} -->" if ctx.resume_ids else "",
        PLACEHOLDER_NOTE_EXAMPLE: "<!-- private note: ... -->",
        PLACEHOLDER_PICK_LINES: str(ctx.pick_lines),
        # The list's words are the posting's: inside a fence of their own, like the posting.
        PLACEHOLDER_REQUIREMENTS: fence_untrusted_posting(
            REQUIREMENTS_BLOCK_HEADER + "\n" + "\n".join(row.prompt_line() for row in ctx.requirements)
        ) if ctx.requirements else "",
    }
    blocks = load_assess_instructions().split("\n\n")
    if not validation_error:
        blocks = [block for block in blocks if _VALIDATION_PLACEHOLDER not in block]
    if not ctx.prior_answers:
        blocks = [block for block in blocks if _PRIOR_ANSWERS_PLACEHOLDER not in block]
    if not ctx.bank_answers:
        blocks = [block for block in blocks if _BANK_ANSWERS_PLACEHOLDER not in block]
    # 0110-038: at most one CANDIDATE WORK MODE paragraph, the candidate's own.
    if ctx.work_mode != "remote":
        blocks = [block for block in blocks if _REMOTE_ONLY_PLACEHOLDER not in block]
    if ctx.work_mode not in _IN_PERSON_MODE_TEXT:
        blocks = [block for block in blocks if _IN_PERSON_PLACEHOLDER not in block]
    # 0.1.11 N3 (v9): the paragraphs about ids, notes, the pick and the requirement list, each only with something to say.
    absent = [
        *(_ID_PLACEHOLDERS if not ctx.resume_ids else ()),
        *((PLACEHOLDER_NOTE_EXAMPLE,) if not (ctx.resume_ids and ctx.resume_notes) else ()),
        *((PLACEHOLDER_REQUIREMENTS,) if not ctx.requirements else ()),
    ]
    for name in absent:
        blocks = [block for block in blocks if "{{" + name + "}}" not in block]

    def fill(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ValueError(f"assess instructions use an unknown placeholder {{{{{key}}}}}")
        return values[key]

    return "\n\n".join(_PLACEHOLDER.sub(fill, block) for block in blocks)


def invoke_json_once(
    binding: object,
    render: Callable[[str | None], str],
    parse: Callable[[Mapping[str, object]], object],
    *,
    role: str = _ROLE,
) -> AssessAttempt:
    """One model call that must answer with a JSON object, retried at most once.

    Q3 (v0.1.9) lifted this loop out of ``assess_once`` unchanged so the
    tailored-resume flow (``scout/tailored_resume.py``) can share it: the
    two flows differ only in how they render the prompt (``render`` takes
    the previous attempt's validation error, ``None`` on the first try) and
    how they turn the extracted JSON object into a result (``parse`` may
    raise ``FindJobsContractError``/``ValueError``/``TypeError`` to reject
    it).  Everything else -- the exception mapping at the model boundary,
    ``_extract_json_object``'s tolerant fence/prose handling, the single
    retry with the error fed back, the ``AssessAttempt`` bookkeeping -- is
    exactly the pre-Q3 ``assess_once`` body.  Never raises for a
    model-boundary failure it can name; re-raises anything it cannot.
    """

    validation_error: str | None = None
    attempts = 0
    for attempt in range(2):
        prompt = render(validation_error)
        try:
            request = binding.request(role=role, prompt=prompt)
            result = binding.port.invoke(request)
            attempts += 1
        except (ModelInvocationError, OSError, TimeoutError) as exc:
            reason = (
                NotAssessedReason.MODEL_DENIED
                if getattr(exc, "code", "") in _DENIED_INVOCATION_CODES
                else NotAssessedReason.MODEL_UNAVAILABLE
            )
            return AssessAttempt(False, None, reason, None, attempts, validation_error)
        except Exception as exc:
            code = getattr(exc, "code", "")
            if code in _MAPPED_FOREIGN_CODES:
                reason = (
                    NotAssessedReason.MODEL_DENIED
                    if code == "model_denied"
                    else NotAssessedReason.MODEL_UNAVAILABLE
                )
                return AssessAttempt(False, None, reason, None, attempts, validation_error)
            raise
        try:
            decoded = _extract_json_object(result.output_text)
            parsed = parse(decoded)
        except (FindJobsContractError, ValueError, TypeError) as exc:
            validation_error = str(exc)
            note_invalid_output(binding.port)  # 0.1.10.7 E: metrics only
            if attempt == 0:
                # U22: one retry, with the validation error fed back so the
                # model can correct its own shape, before giving up on this
                # single posting.
                continue
            return AssessAttempt(
                False, None, NotAssessedReason.MODEL_OUTPUT_INVALID, None, attempts, validation_error
            )
        return AssessAttempt(True, parsed, None, result.normalized_usage, attempts, validation_error)
    raise AssertionError("invoke_json_once: the retry loop always returns")  # pragma: no cover


def assess_once(
    binding: object,
    job: AssessJob,
    ctx: AssessContext,
    *,
    parse: Callable[[dict[str, object]], object],
    keep_thin: bool = False,
) -> AssessAttempt:
    """Assess one posting against one resume: invoke, extract, normalize, validate, retry once.

    ``keep_thin`` (0.1.11 GUARDFIX): an answer the incomplete-posting guard fires on, but that holds at least one
    requirement row, is returned as a successful attempt carrying ``thin_requirements`` instead of being withheld; an
    answer with no requirement row is withheld either way.

    ``binding`` is an already-resolved ``ModelAdapterBinding`` (``request`` +
    ``port.invoke``); ``parse`` is the strict contract parser applied to the
    normalized payload (the caller adds whatever sealed identity its DTO
    needs before validating).  Never raises for a model-boundary failure it
    can name; re-raises anything it cannot.

    A thin wrapper over ``invoke_json_once`` (Q3): the prompt is
    ``render_assess_prompt`` and the parse step is the assessment-specific
    ``_normalize_assessment_payload`` followed by the caller's ``parse``.

    assess-prompt-v3-r1: the questions a ``not_a_match`` answer carried are
    stripped inside that normalization (``_strip_not_a_match_questions``);
    the returned attempt records how many, for the successful attempt only
    (a rejected first attempt's count is overwritten by the retry's).

    0.1.11 N3b (decision #11): a v9 answer with an unclear must-have row no
    question is on spends the retry (the error names the row); when the
    retry's answer still asks nothing the attempt is NOT failed: code asks
    the row's own question (``_normalize_and_strip``,
    ``AssessExtras.asked_by_code``) and the verdict is pending.
    """

    dropped: tuple[str, ...] = ()
    dropped_count = 0
    # 0.1.11 N3 (v9): what THIS prompt offered: the ids a row's sources may name, the requirement list it carried.
    from .assessment_basis import posting_sha256  # that module imports this one

    boundary = Boundary(
        source_ids=offered_sources(ctx), listed=ctx.requirements, posting_sha256=posting_sha256(job.title, job.posting_text), posting_text=job.posting_text,
        countries=tuple(item.strip().upper() for item in ctx.countries if item and item.strip()),
        location=ctx.location.strip(), work_mode=ctx.work_mode, visa_required=ctx.visa_sponsorship_required,
        lines=parse_master_lines(ctx.resume_text) if ctx.resume_ids else {},
        master=read_master(ctx.resume_text) if ctx.resume_ids else None,
        answers={item.question_id.lower(): item.answer for item in ctx.prior_answers},
        stories={item.question_id.lower(): item.summary for item in ctx.bank_answers},
        known_text="\n".join([
            job.title, job.company, job.location or "", job.posting_text, ctx.resume_text, ctx.location, *ctx.countries, *ctx.titles,
            *(item.answer for item in ctx.prior_answers), *(item.summary for item in ctx.bank_answers),
        ]),
    )
    prompts = 0

    def render(validation_error: str | None) -> str:
        # N3b: the second prompt is the one retry, and no attempt follows its answer (``Boundary.retry``).
        nonlocal prompts
        prompts += 1
        boundary.retry = prompts > 1
        return render_assess_prompt(job, ctx, validation_error)

    def parse_normalized(decoded: Mapping[str, object]) -> object:
        nonlocal dropped, dropped_count
        dropped, dropped_count = (), 0
        boundary.extras = None
        normalized, dropped, dropped_count = _normalize_and_strip(decoded, boundary=boundary)
        return parse(normalized)

    attempt = invoke_json_once(binding, render, parse_normalized)
    if attempt.ok and dropped_count:
        attempt = replace(attempt, dropped_questions=dropped_count, dropped_question_ids=dropped)
    if attempt.ok and boundary.extras is not None:
        capped = boundary.extras.capped_questions
        attempt = replace(attempt, extras=boundary.extras, capped_questions=len(capped), capped_question_ids=capped)
    if attempt.ok and keep_thin and posting_looks_incomplete(job.posting_text, attempt.parsed):
        rows = requirement_row_count(attempt.parsed)
        if rows:
            return replace(attempt, thin_requirements=rows)
    if attempt.ok and posting_looks_incomplete(job.posting_text, attempt.parsed):
        # Never Matched on a posting whose requirements look cut off: the
        # answer is withheld and the posting stays not assessed.
        return AssessAttempt(
            False,
            None,
            NotAssessedReason.POSTING_INCOMPLETE,
            attempt.usage,
            attempt.attempts,
            POSTING_INCOMPLETE_MESSAGE,
            incomplete_posting=True,
        )
    return attempt


# --- incomplete-posting guard (uat-bug-046) -------------------------------------------

#: What the operator is told when a "Matched" rests on a thin requirement matrix.
POSTING_INCOMPLETE_MESSAGE = "Posting text looks incomplete: open the posting"

#: A matrix needs this many requirement rows (beyond location/eligibility ones) to back a Matched.
_MIN_REQUIREMENT_ROWS = 3
#: A posting shorter than this is allowed a short matrix: a real short posting can have two requirements.
_SHORT_POSTING_CHARS = 1_200

# Rows about where the candidate may work, not what the job needs.
_ELIGIBILITY_ROW = re.compile(
    r"\b(remote(?:ly)?|work(?:ing)? (?:from|anywhere)|located|location|relocat\w*|on-?site|hybrid|"
    r"authori[sz]ation|eligib\w*|sponsor\w*|visa|citizen\w*|work permit|right to work|"
    r"time ?zone|(?:in|within) the (?:us|u\.s\.|united states|uk|eu|canada))\b",
    re.IGNORECASE,
)
_NO_STATED_REQUIREMENTS = "no stated requirements"


def _row_is_requirement(requirement: str) -> bool:
    text = requirement.strip().lower().rstrip(".")
    return bool(text) and text != _NO_STATED_REQUIREMENTS and _ELIGIBILITY_ROW.search(text) is None


def requirement_row_count(parsed: object) -> int:
    """How many matrix rows are about the job (the rows ``posting_looks_incomplete`` counts)."""

    return sum(1 for row in getattr(parsed, "matrix", ()) if _row_is_requirement(str(getattr(row, "requirement", ""))))


def posting_looks_incomplete(posting_text: str, parsed: object) -> bool:
    """True when a Matched verdict rests on too thin a matrix to trust.

    The rule needs ALL of: the verdict is Matched, fewer than
    ``_MIN_REQUIREMENT_ROWS`` matrix rows are about the job (rows about
    remote/location/eligibility/"No stated requirements" do not count), and
    the posting text is at least ``_SHORT_POSTING_CHARS`` long -- a long text
    with almost no requirements is a posting whose requirements were cut off.
    A "No stated requirements" row leaves the answer to the uat-bug-029
    guard (``quick_assess.posting_requirements_unreadable``).
    A verdict other than Matched is never blocked (nothing false to fix), and
    a genuinely short posting with two requirements stays assessed.
    Residual false positive: a real posting of 1,200+ characters that states
    fewer than three requirements is not assessed and the operator opens it.
    """

    verdict = getattr(parsed, "verdict", None)
    if getattr(verdict, "value", verdict) != "matched_above_threshold":
        return False
    matrix = getattr(parsed, "matrix", ())
    if any(str(getattr(row, "requirement", "")).strip().lower().rstrip(".") == _NO_STATED_REQUIREMENTS for row in matrix):
        return False  # the model says the posting states none: quick_assess's uat-bug-029 guard owns that path
    real = sum(1 for row in matrix if _row_is_requirement(str(getattr(row, "requirement", ""))))
    return real < _MIN_REQUIREMENT_ROWS and len(posting_text) >= _SHORT_POSTING_CHARS


def _extract_json_object(raw: object) -> Mapping[str, object]:
    """Extract one JSON object from model output that may be fenced/prose-wrapped.

    Tolerant boundary parsing (U22), applied before normalization and before
    strict contract validation: models sometimes wrap JSON in ``` fences or
    prepend/append prose. This never relaxes the frozen contract itself —
    ``parse_assessment_proposal`` still validates strictly after normalization.
    """
    if isinstance(raw, Mapping):
        return raw
    if not isinstance(raw, str):
        raise ValueError("assessment output is not text or an object")
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("assessment output contains no JSON object") from None
        decoded = json.loads(text[start : end + 1])
    if not isinstance(decoded, Mapping):
        raise ValueError("assessment output is not a JSON object")
    return decoded


_STATUS_SYNONYMS = {
    "met": "met",
    "meets": "met",
    "meet": "met",
    "yes": "met",
    "full": "met",
    # The S29 r1 prompt (P2, v0.1.9) speaks met|unmet|unclear directly.
    "unmet": "unmet",
    "unclear": "unclear",
    # The pre-P2 prompt's partial/gap vocabulary is gone from the live
    # prompt, but old synonyms still map onto the new words (plan section
    # "P2", operator answer 10): partial -> unclear, gap -> unmet.
    "partial": "unclear",
    "partially": "unclear",
    "partly": "unclear",
    "some": "unclear",
    "gap": "unmet",
    "missing": "unmet",
    "no": "unmet",
    "none": "unmet",
    "not_met": "unmet",
    "not met": "unmet",
}

_CLASS_SYNONYMS = {
    "hard": "hard",
    "askable": "askable",
    "nice_to_have": "nice_to_have",
    "nice-to-have": "nice_to_have",
    "nice to have": "nice_to_have",
    # 0110-10-03: one tool of a list a single sentence names.
    "list_item": "list_item",
    "list-item": "list_item",
    "list item": "list_item",
}

_VERDICT_SYNONYMS = {
    "matched_above_threshold": "matched_above_threshold",
    "pending_user_answers": "pending_user_answers",
    "not_a_match": "not_a_match",
}

_SPONSORSHIP_SYNONYMS = {
    "offered": "offered",
    "offer": "offered",
    "yes": "offered",
    "available": "offered",
    "not_offered": "not_offered",
    "not offered": "not_offered",
    "no": "not_offered",
    "unavailable": "not_offered",
    "unknown": "unknown",
    "unclear": "unknown",
    "n/a": "unknown",
    "na": "unknown",
}


def _normalize_string_list(value: object) -> list[object]:
    """A single string coerces to a one-item list; ``null``/missing to ``[]``."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return list(value)
    return [value]


def _normalize_status(value: object) -> object:
    if not isinstance(value, str):
        return value
    key = value.strip().lower()
    return _STATUS_SYNONYMS.get(key, value)


def _normalize_class(value: object) -> object:
    if value is None or not isinstance(value, str):
        return value
    key = value.strip().lower()
    return _CLASS_SYNONYMS.get(key, value)


def _normalize_verdict(value: object) -> object:
    if not isinstance(value, str):
        return value
    key = value.strip().lower()
    return _VERDICT_SYNONYMS.get(key, value)


def _normalize_question_item(item: object) -> object | None:
    """One entry of the new structured ``questions`` list, or ``None`` to drop it.

    Tolerates a bare string (treated as the question text with no id/
    requirement -- dropped, since a question_id is required downstream) and
    a mapping missing ``requirement`` (defaults to ``None``).
    """
    if isinstance(item, str):
        return None
    if not isinstance(item, Mapping):
        return None
    question_id = item.get("question_id")
    question = item.get("question")
    if not isinstance(question_id, str) or not isinstance(question, str):
        return None
    requirement = item.get("requirement")
    return {
        # P3 (v0.1.9): the S29 r1 rerun logs show the model's own slug for
        # the SAME real-world fact drifting across calls (word choice,
        # separator, token order); the normalizer (question_ids.py) is
        # applied here, at the model boundary, so every structured question
        # this normalizer ever emits is already in canonical form -- the
        # Q&A loop's prior-answer join (assessment_core's own
        # {{prior_answers}} rendering, and experience_answers.read_answers)
        # never has to re-normalize a value that passed through here.
        "question_id": normalize_question_id(question_id.strip().lower()),
        "question": question,
        "requirement": requirement if isinstance(requirement, str) else None,
    }


def _normalize_sponsorship(value: object) -> object:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    key = value.strip().lower()
    return _SPONSORSHIP_SYNONYMS.get(key, value)


def _normalize_assessment_payload(decoded: Mapping[str, object]) -> dict[str, object]:
    """Tolerant normalization at the model boundary, BEFORE strict validation (U22).

    - extracts already happened in ``_extract_json_object``
    - ``resume_evidence`` as a bare string becomes ``[string]``; ``null``/missing becomes ``[]``
    - matrix ``status`` synonyms (yes/partially/no, meets/partial/missing, any case) map to
      met/unmet/unclear (P2, v0.1.9): the old prompt's partial/gap words map onto the new
      prompt's unclear/unmet respectively, since the old prompt is gone from the live path
    - matrix ``class`` (P2): hard/askable/nice_to_have, case/hyphen tolerant; absent stays absent
    - ``suggestions`` as a bare string becomes ``[string]``; ``null``/missing become ``[]``
    - ``questions`` (P2): the S29 r1 prompt returns a list of
      ``{question_id, question, requirement}`` objects. Each valid object becomes one
      ``structured_questions`` entry (question_id lowercased) AND contributes its
      ``question`` text to the plain-string ``questions`` list the shipped UI still reads
      (C9); a bare string entry (the pre-P2 prompt's shape) is kept only in the plain
      ``questions`` list, since it carries no question_id to structure.
    - ``verdict``/``not_a_match_reason`` (P2): passed through when present; consistency
      between verdict and the matrix/questions is enforced downstream, in ``parse``
      (``proposals.parse_assessment_proposal``), not here.
    - unknown top-level keys are dropped so the frozen contract's closed-object check still applies cleanly
    - ``sponsorship`` synonyms map to offered/not_offered/unknown; absent stays absent
    - 0110-10-03 (``requirement_weights``): the matrix is put must-haves first and
      bounded (``bound_rows``): rows past the bound are counted in
      ``rows_not_shown``, never an error and never dropped in silence; and a
      matched or pending verdict is settled by what its questions are on
      (``settled_verdict``): one open question on a one-of-a-list row is kept
      and the job is matched.
    - assess-prompt-v3-r1: a ``not_a_match`` verdict keeps NO questions
      (``_strip_not_a_match_questions``): both the plain ``questions`` list and
      ``structured_questions`` are emptied before validation, so the answer is
      accepted as it is (no retry is spent on it) and a question is never
      stored or shown against a failed verdict. ``_normalize_and_strip``
      reports what was dropped; this wrapper discards that count.

    This never loosens the frozen contract itself: ``parse_assessment_proposal``
    still runs strict validation immediately after this step.
    """
    return _normalize_and_strip(decoded)[0]


def _strip_not_a_match_questions(result: dict[str, object]) -> tuple[tuple[str, ...], int]:
    """Empty the question lists of a ``not_a_match`` payload; report what was dropped.

    assess-prompt-v3-r1 (operator decision 2026-09-25, eval evidence in
    ``orchestrator/research/evals/2026-09-25-prompt-v3.md``): every remaining
    hard false ask in the v3 live run sat on a row whose verdict was already
    ``not_a_match``. A question on a failed verdict has no next action for the
    product (the Questions view lists ``pending_user_answers`` results only,
    and an answer could never flip a HARD-unmet row), so the product keeps
    none: the plain ``questions`` list (the shipped UI's C9 shape) and
    ``structured_questions`` are both emptied here, BEFORE strict validation,
    so ``proposals._validate_verdict_consistency`` sees a clean
    ``not_a_match`` with 0 questions and the answer is never refused for
    them. Returns ``(structured question ids dropped, questions dropped)``
    where the count covers the plain list (a bare-string question carries no
    id). Any other verdict, or none, is left exactly as it came.
    """

    if result.get("verdict") != "not_a_match":
        return (), 0
    plain = result.get("questions")
    structured = result.get("structured_questions")
    count = len(plain) if isinstance(plain, list) else 0
    ids = tuple(
        str(item["question_id"])
        for item in (structured if isinstance(structured, list) else ())
        if isinstance(item, Mapping) and isinstance(item.get("question_id"), str)
    )
    if not count and not ids:
        return (), 0
    result["questions"] = []
    result.pop("structured_questions", None)
    return ids, count


# --- 0.1.11 N3 (assessment v9, SPEC 1.3): the v9 keys at the model boundary ------------------------------------------
#
# Every v9 key is read leniently: one that is missing, null or malformed is normalized to "none" and never spends
# the one retry (which stays for an invalid matrix, question or verdict, and for a matrix that does not hold the
# listed requirement ids). An answer in the v8 shape carries none of these keys and is normalized exactly as before.

_ID_COMMENT = re.compile(r"\s*<!--\s*(?:id|note|private note):.*?-->")
_ANSWER_SOURCE = re.compile(r"\AA\s+(\S.*)\Z")


@dataclass(frozen=True)
class AssessExtras:
    """What a v9 answer carried BESIDE the normalized payload, and what the boundary dropped from it.

    ``pick`` and ``structured_suggestions`` are returned here, not inside the
    payload: the run path's parser is a closed object. ``dropped_pick``: the
    answer carried a pick the rules do not keep (a verdict that is not
    Matched, or a prompt that showed no ids). ``dropped_suggestions``: the
    structured suggestions a ``not_a_match`` or pending verdict does not keep.
    ``unknown_sources``: ``(the row's requirement words, the source)`` for
    every source the prompt did not offer (dropped from the row).
    ``asked_by_code`` (N3b): the id of every question code asked for an
    unclear must-have row the retry's answer still asked nothing about
    (:func:`row_question`). ``capped_questions`` (orchestrator #14): the id
    of every question on a ``list_item`` row dropped past the cap
    (``requirement_weights.cap_list_item_questions``).
    """

    pick: AssessmentPick | None = None
    structured_suggestions: tuple[AssessmentSuggestion, ...] = ()
    dropped_pick: bool = False
    dropped_suggestions: int = 0
    unknown_sources: tuple[tuple[str, str], ...] = ()
    asked_by_code: tuple[str, ...] = ()
    capped_questions: tuple[str, ...] = ()
    #: 0.1.11 (orchestrator #39): the questions on must-have rows dropped past ``MAX_MANDATORY_QUESTIONS`` (their rows hold, unasked).
    capped_mandatory_questions: tuple[str, ...] = ()
    #: 0.1.11 C3: ``(kind, reason)`` of every structured suggestion the code check dropped (``suggestion_check``); a suggestion turned into a gap is counted as ``master_line_to_gap``.
    checked_suggestions: tuple[tuple[str, str], ...] = ()
    #: 0.1.11.3 Q2: ``(row id or "", rule)`` of every ``unclear`` row the master's own lines settled as ``met``
    #: (``stated_check``), and the id of every question dropped with them.
    met_by_master: tuple[tuple[str, str], ...] = ()
    stated_questions: tuple[str, ...] = ()
    #: 0.1.11.3 Q3: what the citation check did to each evidence item that was not one verbatim line (``suggestion_check.clean_citations``): ``trimmed_to_line`` | ``split_join`` | ``no_line``.
    checked_citations: tuple[str, ...] = ()
    #: 0.1.11.4 Q4: how many questions were dropped for naming a city no text the prompt showed names (``suggestion_check.names_unknown_place``).
    unplaced_questions: int = 0


@dataclass
class Boundary:
    """What one prompt offered, for reading its answer; ``extras`` is what the last normalized answer carried.

    ``source_ids``: every id a row's ``sources`` may name (:func:`offered_sources`),
    or ``None`` when the RESUME block showed no ids (sources and a pick are
    then dropped). ``listed``: the requirement list the prompt carried
    (empty: none; the matrix is then not checked against a list).

    N3b (decision #11), for a v9 answer with an unclear must-have row no
    question is on (``resume_gate.unasked_rows``). ``retry``: ``False`` for
    the answer to the first prompt (refused, naming the row: the one retry is
    spent on it), ``True`` for the retry's answer (no attempt follows it:
    code asks the row's own question, :func:`row_question`), ``None`` outside
    ``assess_once`` (the answer is left as it came; the validator refuses
    it). ``posting_sha256``: the posting's digest, from which a first
    assessment's rows get their ids (``requirements_list``), so the row is
    named, and its question is identified, by the id the stored row carries.
    """

    source_ids: frozenset[str] | None = None
    listed: tuple[ListedRequirement, ...] = ()
    extras: AssessExtras | None = None
    retry: bool | None = None
    posting_sha256: str | None = None
    #: 0.1.11 C4: the posting's text (what a location the header names may be contradicted by), ``None`` outside ``assess_once``.
    posting_text: str | None = None
    #: 0.1.11 C4b: the candidate's eligible countries (upper-case codes; empty: none stated), from the setup.
    countries: tuple[str, ...] = ()
    #: 0.1.11 GUARDFIX (orchestrator #88): the rest of the setup an ``elig-`` row's evidence is written from
    #: (:func:`setup_evidence`): the candidate's location, work mode and whether they need visa sponsorship.
    location: str = ""
    work_mode: str = ""
    visa_required: bool | None = None
    #: 0.1.11 C2/C3: the master lines the prompt showed by id (``suggestion_check.parse_master_lines``), the answers
    #: (``question_id`` -> text) and the stories (``question_id`` -> summary) it listed. Empty: no check, no verbatim evidence.
    lines: Mapping[str, MasterLine] = field(default_factory=dict)
    answers: Mapping[str, str] = field(default_factory=dict)
    stories: Mapping[str, str] = field(default_factory=dict)
    #: 0.1.11.3 Q2: what the RESUME block states, as ``stated_check`` reads it (skills line, dated roles). ``None``: no check.
    master: MasterFacts | None = None
    #: 0.1.11.4 Q4: every text the prompt showed (role, company, location, posting, master, setup, answers, stories): where a
    #: city a question or a suggestion names must come from. Empty: no check.
    known_text: str = ""


def offered_sources(ctx: AssessContext) -> frozenset[str] | None:
    """The sources a v9 answer may cite for ``ctx``'s prompt, or ``None`` when its RESUME block shows no ids.

    The master line ids the block shows, ``A <question_id>`` for each answer
    and ``A story:<slug>`` for each story the prompt lists.
    """

    if not ctx.resume_ids:
        return None
    answers = {f"A {item.question_id}" for item in (*ctx.prior_answers, *ctx.bank_answers)}
    return frozenset(ctx.resume_ids) | answers


def _short_strings(value: object, *, most: int, chars: int) -> list[str]:
    """The clean strings of a list a model returned: stripped, non-empty, at most ``chars`` long, each once, ``most`` kept."""

    out: list[str] = []
    for item in value if isinstance(value, list) else ():
        text = " ".join(item.split()) if isinstance(item, str) else ""
        if text and len(text) <= chars and text not in out:
            out.append(text)
    return out[:most]


def _source(value: str) -> str:
    """A source as it is compared: ``A  Tooling:Temporal`` is ``A tooling:temporal``; a line id is itself."""

    found = _ANSWER_SOURCE.fullmatch(value)
    return f"A {found.group(1).strip().lower()}" if found else value


def _v9_row_keys(row: Mapping[str, object], boundary: Boundary, unknown: list[tuple[str, str]]) -> dict[str, object]:
    """The v9 keys of one matrix row, each only when it is present and well formed."""

    out: dict[str, object] = {}
    row_id = row.get("id")
    row_id = row_id.strip() if isinstance(row_id, str) else None
    # An id means something only where one was offered: a listed row's, or one of the four fixed ids.
    if row_id in ELIGIBILITY_ROW_IDS or (boundary.listed and is_row_id(row_id)):
        out["id"] = row_id
    basis = row.get("class_basis")
    if isinstance(basis, str) and basis.strip():
        out["class_basis"] = " ".join(basis.split())[:MAX_CLASS_BASIS_CHARS].rstrip()
    alternatives = _short_strings(row.get("alternatives"), most=MAX_ROW_ALTERNATIVES, chars=MAX_ALTERNATIVE_CHARS)
    if alternatives:
        out["alternatives"] = alternatives
    if boundary.source_ids is not None:
        kept: list[str] = []
        for source in _short_strings(row.get("sources"), most=MAX_ROW_SOURCES, chars=MAX_SOURCE_CHARS):
            source = _source(source)
            if source in boundary.source_ids:
                if source not in kept:
                    kept.append(source)
            else:
                unknown.append((str(row.get("requirement") or ""), source))
        if kept:
            out["sources"] = kept
    return out


def _master_id(value: object) -> str | None:
    text = value.strip() if isinstance(value, str) else ""
    return text if text and len(text) <= MAX_MASTER_ID_CHARS and not any(char.isspace() for char in text) else None


def _structured_suggestions(value: object, row_ids: Mapping[str, str]) -> list[AssessmentSuggestion]:
    """The well-formed suggestion objects of a v9 answer, at most ``MAX_STRUCTURED_SUGGESTIONS``; a bare string is not one.

    A suggestion names its requirement by the row's id or (orchestrator #14)
    by the row's words: on a first assessment the rows have no id yet, so
    ``row_ids`` (the folded words of each row -> the id it carries or is
    given) turns the words into the id, as a question's are turned into its
    row's. Words that are no row's name nothing.
    """

    out: list[AssessmentSuggestion] = []
    for item in value if isinstance(value, list) else ():
        if not isinstance(item, Mapping):
            continue
        kind = item.get("kind")
        kind = kind.strip().lower() if isinstance(kind, str) else None
        why = " ".join(item["why"].split()) if isinstance(item.get("why"), str) else ""
        line = _master_id(item.get("line"))
        named = item["requirement"].strip() if isinstance(item.get("requirement"), str) else ""
        requirement = named if is_row_id(named) else row_ids.get(fold(named)) if named else None
        if kind not in SUGGESTION_KINDS or not why or (line is None and requirement is None):
            continue
        phrase = " ".join(item["posting_phrase"].split()) if isinstance(item.get("posting_phrase"), str) else ""
        out.append(AssessmentSuggestion(
            kind=kind, why=why[:MAX_SUGGESTION_WHY_CHARS].rstrip(), line=line, requirement=requirement,
            posting_phrase=phrase[:MAX_SUGGESTION_PHRASE_CHARS].rstrip() or None,
        ))
    return out[:MAX_STRUCTURED_SUGGESTIONS]


def _lenient_pick(value: object) -> AssessmentPick | None:
    """The pick of a v9 answer, read leniently: non-strings and repeats dropped, more than ``MAX_PICK_LINES`` cut.

    ``None`` when no line id is left. Nothing is checked against a master
    here: that is ``pick.settle``'s, and a bad pick never fails an assessment.
    """

    if not isinstance(value, Mapping):
        return None
    lines: list[str] = []
    for item in value.get("lines") if isinstance(value.get("lines"), list) else ():  # type: ignore[union-attr]
        line = _master_id(item)
        if line is not None and line not in lines:
            lines.append(line)
    if not lines:
        return None
    order: list[str] = []
    for item in value.get("section_order") if isinstance(value.get("section_order"), list) else ():  # type: ignore[union-attr]
        section = item.strip().lower() if isinstance(item, str) else ""
        if section in PICK_SECTIONS and section not in order:
            order.append(section)
    return AssessmentPick(summary=_master_id(value.get("summary")), section_order=tuple(order), lines=tuple(lines[:MAX_PICK_LINES]))


# --- 0.1.11 N3b (orchestrator decision #11): an unclear must-have row always carries its question ---------------------
#
# The accepted gate table says an unresolved must-have HOLDS and asks. On v9 rows the gate holds for a ``hard`` or
# ``askable`` row that is ``unclear`` whether or not the model asked about it (``resume_gate.unasked_rows``), and a
# hold with nothing to answer is a dead end, so: the first answer with such a row is a validation error that names
# the row (the one retry); when the retry's answer still asks nothing the assessment is NOT failed: code asks the
# row's own question, from the row's requirement words. An optional row is never asked for, nor any v8 row.

#: The category of a question code asks for a row. The answers store takes any ``<category>:<value>`` id
#: (``question_ids.is_valid_question_id``); this one says the question is about one requirement row of one posting.
ROW_QUESTION_CATEGORY = "requirement"
_ROW_QUESTION = "The posting asks for: {requirement}. Do you have this? Say where."
_MAX_ROW_QUESTION_REQUIREMENT = 600  # a question is at most 700 characters (``proposals._MAX_QUESTION``)


def row_question_id(row_id: str) -> str:
    """The id of the question code asks for the row ``row_id``: ``requirement:req.3fa91c`` for ``req-3fa91c``.

    Derived from the row id alone, so it is the same id every time the row
    is asked about (the answer given once is found again). The ``-`` of a
    row id is written ``.``: ``normalize_question_id`` splits a value on
    ``-`` and sorts the parts, and this id must be what it normalizes to.
    """

    return f"{ROW_QUESTION_CATEGORY}:{row_id.replace('-', '.')}"


def row_question(row_id: str, requirement: str) -> dict[str, object]:
    """The structured question code asks for an unclear must-have row: the row's own, from its requirement words."""

    words = " ".join(requirement.split()).rstrip(". ")
    if len(words) > _MAX_ROW_QUESTION_REQUIREMENT:
        words = words[:_MAX_ROW_QUESTION_REQUIREMENT].rstrip() + " [...]"
    return {"question_id": row_question_id(row_id), "question": _ROW_QUESTION.format(requirement=words), "requirement": requirement}


def _unasked(matrix: list[Mapping[str, object]], questions: list[object], verdict: object, boundary: Boundary) -> list[tuple[str, Mapping[str, object]]]:
    """``(id, row)`` for each unclear must-have row of a v9 ``matrix`` that holds the verdict with no question on it.

    Empty beside a hard gap or a ``not_a_match`` verdict (the gate's first
    rule: such an answer keeps no question). A row is named by its own id
    (a listed row's, an ``elig-`` one) or, on a first assessment, by the id
    ``requirements_list.extracted`` gives it: the id its stored row carries.
    """

    unasked = [row for row in unasked_rows(matrix, questions) if isinstance(row.get("requirement"), str) and row["requirement"].strip()]  # type: ignore[union-attr]
    if not unasked or gate(matrix, questions, verdict).decision != HOLD_QUESTION:
        return []
    ids = _row_ids(matrix, boundary.posting_sha256 or "")
    return [(ids[index], row) for index, row in enumerate(matrix) if any(row is item for item in unasked)]


def _row_ids(matrix: list[Mapping[str, object]], posting_sha256: str) -> list[str]:
    """The id of each row of ``matrix``: its own (a listed row's, an ``elig-`` one), else the one a first assessment's row is given.

    ``requirements_list.extracted`` is what gives a first assessment's
    stored rows their ids, so this is the id the stored row carries.
    """

    derived = extracted(posting_sha256, matrix, extracted_at="")[1]
    return [str(row["id"]) if is_row_id(row.get("id")) else derived[index] for index, row in enumerate(matrix)]


# --- 0.1.11 C4 / C4b (orchestrator decision B): the location requirement is MET when the posting says "anywhere" --------
#
# A location row that is ``unmet`` or ``unclear`` is read as ``met`` (and its question dropped: no row unclear, no
# question, no hold) when the posting text (1) carries a worldwide or remote-anywhere statement ("work from anywhere in
# the world", "a globally distributed team"), or (2) shows a pay band, office or hiring entity in one of the candidate's
# eligible countries next to a remote statement. Location questions remain only for what rule 4 names (state or
# province limits: ``elig-region``; a named city with no work mode). The prompt says the same (rule 4); two live runs
# showed the prompt alone does not hold it.

LOCATION_ROW_ID = "elig-location"
_WORLDWIDE = re.compile(
    r"anywhere\s+in\s+the\s+world|globally\s+(?:distributed|remote)|global\s+remote|"
    r"(?:work|working)\s+(?:remotely\s+)?from\s+anywhere|remote\s+anywhere|anywhere\s+remote|"
    r"hire\s+in\s+any\s+countr(?:y|ies)|distributed\s+(?:team\s+)?(?:across|around)\s+the\s+(?:world|globe)",
    re.IGNORECASE,
)
_DENIES = re.compile(r"\b(?:not|cannot|can't|unable|except|excluding|only)\b|n't\b", re.IGNORECASE)
_REMOTE = re.compile(r"\bremote(?:ly|-first)?\b|\bwork\s+from\s+home\b", re.IGNORECASE)
_PAY_OFFICE_ENTITY = re.compile(
    r"pay\s+(?:band|range)|salary|compensation|office|offices|headquarter\w*|hiring\s+(?:entity|through|via)|hired\s+(?:through|via|by)|employer\s+of\s+record|\bEOR\b|"
    r"\bentity\b|employed\s+(?:by|through)",
    re.IGNORECASE,
)
#: Names a posting writes for the countries a candidate may list as a code (the code itself is matched as a word, upper case).
_COUNTRY_NAMES: Mapping[str, tuple[str, ...]] = {
    "US": ("united states", "usa", "u.s."), "GB": ("united kingdom", "uk", "u.k.", "england", "great britain"), "CA": ("canada",),
    "DE": ("germany",), "FR": ("france",), "NL": ("netherlands",), "ES": ("spain",), "IE": ("ireland",), "AU": ("australia",),
    "IN": ("india",), "PL": ("poland",), "PT": ("portugal",), "IT": ("italy",), "SE": ("sweden",), "CH": ("switzerland",),
}
_LOCATION_QUESTION = "The posting names {where} and also says the role is open more widely. Where are you able to work from?"


def says_worldwide(posting_text: str) -> bool:
    """Whether the posting text says the role can be done from anywhere (and does not take it back in the same sentence). Pure."""

    sentences = re.split(r"(?<=[.!?])\s+|\n+", posting_text)
    for sentence in sentences:
        if _DENIES.search(sentence):
            continue
        if _WORLDWIDE.search(sentence):
            return True
    return False


def _names_country(sentence: str, code: str) -> bool:
    names = _COUNTRY_NAMES.get(code, ())
    lowered = sentence.lower()
    return any(re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", lowered) for name in names) or re.search(rf"(?<![\w]){re.escape(code)}(?![\w])", sentence) is not None


def says_eligible_pay_office_or_entity(posting_text: str, countries: tuple[str, ...]) -> bool:
    """Whether the posting shows a pay band, office or hiring entity in an eligible country and says the work is remote. Pure.

    Needs a remote statement anywhere in the text and, in one sentence (or one line) that is not a denial, a pay, office or
    entity word next to the name or code of an eligible country. With no eligible country stated it is never true.
    """

    codes = [code for code in countries if code and code != "ANY"]
    if not codes or not _REMOTE.search(posting_text):
        return False
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", posting_text):
        if _DENIES.search(sentence) or not _PAY_OFFICE_ENTITY.search(sentence):
            continue
        if any(_names_country(sentence, code) for code in codes):
            return True
    return False


def _location_unclear(decoded: Mapping[str, object], boundary: Boundary) -> Mapping[str, object]:
    """``decoded`` of a v9 answer with an ``unmet`` or ``unclear`` ``elig-location`` row read as ``met`` when the posting says it can be done from anywhere. Pure.

    The row's questions (the model's) are dropped; a ``not_a_match`` that rested on the row alone is read again from
    the remaining rows (``pending_user_answers`` when a must-have question is still open, else matched), and the
    model's reason for it is dropped. Anything else is returned untouched.
    """

    matrix = decoded.get("matrix")
    if not boundary.posting_text or not isinstance(matrix, list) or not all(isinstance(row, Mapping) for row in matrix) or not uses_v9_rules(matrix):
        return decoded
    rows = [row for row in matrix if row.get("id") == LOCATION_ROW_ID and row.get("status") in ("unmet", "unclear")]
    if not rows or not (says_worldwide(boundary.posting_text) or says_eligible_pay_office_or_entity(boundary.posting_text, boundary.countries)):
        return decoded
    out = dict(decoded)
    out["matrix"] = [{**row, "status": "met"} if any(row is hit for hit in rows) else row for row in matrix]
    named = {fold(str(row.get("requirement") or "")) for row in rows} | {LOCATION_ROW_ID, row_question_id(LOCATION_ROW_ID)}
    raw = decoded.get("questions")
    if isinstance(raw, list):
        out["questions"] = [
            item for item in raw
            if not (isinstance(item, Mapping) and (fold(str(item.get("requirement") or "")) in named or str(item.get("requirement") or "") in named or str(item.get("question_id") or "") in named))
        ]
    kept_unmet = any(row.get("status") == "unmet" and row.get("class") in (None, "hard") for row in out["matrix"])  # type: ignore[union-attr]
    said = _normalize_verdict(decoded.get("verdict"))
    if not kept_unmet and said in ("not_a_match", "pending_user_answers"):
        out["verdict"] = "matched_above_threshold"
        if said == "not_a_match":
            out["not_a_match_reason"] = None
    return out


def setup_evidence(row_id: str, boundary: Boundary) -> str | None:
    """The evidence of an ``elig-`` row, in fixed wording from the candidate's setup; ``None`` for any other row id. Pure.

    0.1.11 (orchestrator #88): these rows are facts of the candidate's search settings, never a claim from the model.
    A model's own sentence on one ("Candidate is eligible to work from the US; ...") was read by a judge as a claim, so
    code always writes the evidence and the model's is discarded (:func:`_normalize_and_strip`).
    """

    if row_id == "elig-location":
        countries = [code for code in boundary.countries if code != "ANY"]
        if not countries:
            return "Your search settings do not limit the countries you can work from."
        return f"Your search settings say you can work from {', '.join(countries)}."
    if row_id == "elig-region":
        if not boundary.location:
            return "Your search settings do not name where you are based."
        return f"Your search settings say you are based in {boundary.location}."
    if row_id == "elig-work-mode":
        if not boundary.work_mode:
            return "Your search settings do not limit the work mode."
        return f"Your search settings say you want {boundary.work_mode} roles."
    if row_id == "elig-sponsorship":
        if boundary.visa_required is None:
            return "Your search settings do not say whether you need visa sponsorship."
        return f"Your search settings say you {'need' if boundary.visa_required else 'do not need'} visa sponsorship."
    return None


def _without_authorization(decoded: Mapping[str, object]) -> Mapping[str, object]:
    """``decoded`` of a v9 answer without the rows and questions about sponsorship or work authorization (0.1.11, orchestrator #45).

    The operator's rule: sponsorship and work authorization NEVER gate. They are a label (the answer's ``sponsorship``,
    which the job list already shows), not a requirement: no hold, no question, no not_a_match, no row in the score.
    Whatever the model returns about them is removed here, so a prompt-only rule cannot leak; a ``not_a_match`` that
    rested on such a row alone is read from the remaining rows (matched, or pending when a must-have question is
    open). The label becomes ``not_offered`` when a removed row says the employer does not sponsor. An answer in
    the v8 shape is returned untouched. Pure.
    """

    matrix = decoded.get("matrix")
    if not isinstance(matrix, list) or not all(isinstance(row, Mapping) for row in matrix) or not uses_v9_rules(matrix):
        return decoded
    dropped = [row for row in matrix if is_authorization_row(row)]
    if not dropped or len(dropped) == len(matrix):
        return decoded
    kept = [row for row in matrix if not is_authorization_row(row)]
    out = dict(decoded)
    out["matrix"] = kept
    questions = decoded.get("questions")
    if isinstance(questions, list):
        out["questions"] = [item for item in questions if not (isinstance(item, Mapping) and is_authorization_question(item))]
    if "sponsorship" not in out and says_no_sponsorship(dropped):
        out["sponsorship"] = "not_offered"
    said = _normalize_verdict(decoded.get("verdict"))
    if said in ("matched_above_threshold", "pending_user_answers") or (
        said == "not_a_match" and not any(row.get("status") == "unmet" and row.get("class") in (None, "hard") for row in kept)
    ):
        # The verdict the model gave may rest on the removed row: read it again from what is left. ``settled_verdict``
        # below makes it pending when a must-have question is open, and matched when none is.
        out["verdict"] = "matched_above_threshold"
        if said == "not_a_match":
            out["not_a_match_reason"] = None
    return out


def _normalize_and_strip(decoded: Mapping[str, object], *, boundary: Boundary | None = None) -> tuple[dict[str, object], tuple[str, ...], int]:
    """``_normalize_assessment_payload`` plus what the not_a_match strip removed.

    Returns ``(normalized payload, dropped structured question ids, dropped
    question count)``; ``assess_once`` records the last two on the
    ``AssessAttempt`` for the eval harness. The strip runs LAST, on the
    fully normalized shape, so it sees the canonical verdict word and every
    structured question that ``_normalize_question_item`` accepted.

    0.1.11 N3 (assessment v9). ``boundary`` says what the prompt offered
    (none: nothing). A row's v9 keys are kept when well formed (``_v9_row_keys``;
    an id comment copied into an evidence quote is stripped); with a
    requirement list in the prompt the matrix must hold exactly its ids
    (``requirements_list.check_listed``: the one v9 rule that raises, and so
    spends the retry) and each listed row is made the list's. The pick and
    the structured suggestions are NOT in the payload: they are left on
    ``boundary.extras`` (:class:`AssessExtras`), with what was dropped. Each
    kept suggestion's ``why`` joins the plain ``suggestions`` list, so a
    reader that knows only strings still shows something.

    0.1.11 N3b (decision #11), v9 rows only. An unclear must-have row with
    no question on it (``_unasked``): the first answer is refused, naming the
    row (raises: the one retry); the retry's answer gets the row's own
    question from code (:func:`row_question`), in both question lists, BEFORE
    the verdict is settled, so the verdict is ``pending_user_answers``.
    ``boundary.retry`` says which answer this is; with no boundary the
    answer is left as it came.

    0.1.11 N3b (orchestrator #14), v9 rows only. (1) A structured suggestion
    may name its requirement by the row's words (a first assessment's rows
    have no id yet): they become the row's id (``_structured_suggestions``),
    so a ``gap`` suggestion that names no line is kept. (2) At most
    ``requirement_weights.MAX_LIST_ITEM_QUESTIONS`` questions on ``list_item``
    rows are kept, those of the rows first in the matrix; the rest are
    dropped here, from both question lists, with no retry
    (``AssessExtras.capped_questions``). A ``not_a_match`` answer keeps no
    question at all, as before, and its count is the strip's.

    0.1.11.3 Q2, v9 rows of a prompt that showed ids only. An ``unclear`` row
    whose key terms the master states (``stated_check.settle_stated``: a
    name in the skills line, a name a line holds as a whole word, years a
    line states or the role dates cover) is ``met`` with the master's own
    line(s) as its evidence and sources, and the question on it is dropped
    from both lists (``AssessExtras.met_by_master`` / ``stated_questions``).
    This runs before the caps, the unasked-row rule and the gate, so all
    three read the settled rows; a ``pending`` verdict that rested on a
    settled row is read again by the gate.
    """
    boundary = boundary if boundary is not None else Boundary()
    decoded = _without_authorization(decoded)
    decoded = _location_unclear(decoded, boundary)
    unknown_sources: list[tuple[str, str]] = []
    matrix = decoded.get("matrix")
    normalized_matrix: list[object] = []
    if isinstance(matrix, list):
        for row in matrix:
            if not isinstance(row, Mapping):
                normalized_matrix.append(row)
                continue
            normalized_row: dict[str, object] = {
                "requirement": row.get("requirement"),
                "resume_evidence": [
                    _ID_COMMENT.sub("", item).strip() if "<!--" in item else item
                    for item in _normalize_string_list(row.get("resume_evidence")) if isinstance(item, str)
                ],
                "status": _normalize_status(row.get("status")),
            }
            if "class" in row:
                requirement_class = _normalize_class(row.get("class"))
                if requirement_class is not None:
                    normalized_row["class"] = requirement_class
            normalized_row.update(_v9_row_keys(row, boundary, unknown_sources))
            written = setup_evidence(str(normalized_row.get("id") or ""), boundary)
            if written is not None:
                normalized_row["resume_evidence"] = [written]  # the model's own sentence is discarded
            normalized_matrix.append(normalized_row)
    else:
        normalized_matrix = matrix
    all_rows = isinstance(normalized_matrix, list) and all(isinstance(row, Mapping) for row in normalized_matrix)
    # v9: a question may name its row by id, or in the model's words for a row whose words are the list's now.
    renamed: dict[str, object] = {}
    named_rows: dict[str, object] = {}  # the model's words for a row -> the row
    if all_rows:
        asked_as = {fold(str(row.get("requirement") or "")): index for index, row in enumerate(normalized_matrix)}  # type: ignore[union-attr]
        if boundary.listed:
            normalized_matrix = check_listed(normalized_matrix, boundary.listed)  # type: ignore[arg-type]
        for words, index in asked_as.items():
            renamed[words] = normalized_matrix[index].get("requirement")  # type: ignore[union-attr]
            named_rows[words] = normalized_matrix[index]
        renamed.update({str(row["id"]): row.get("requirement") for row in normalized_matrix if row.get("id")})  # type: ignore[union-attr]
    # 0110-10-03: must-haves first; past the bound the rest are counted ("+N not shown"), never an error.
    rows_not_shown = 0
    if all_rows:
        normalized_matrix, rows_not_shown = bound_rows(normalized_matrix)  # type: ignore[arg-type]
    is_v9 = all_rows and uses_v9_rules(normalized_matrix)  # type: ignore[arg-type]
    cited: list[str] = []
    if is_v9 and boundary.lines:
        # 0.1.11 C2: a met row's evidence is the cited master line(s) by id, never the model's paraphrase of them.
        with_verbatim_evidence(normalized_matrix, boundary.lines, boundary.answers, boundary.stories)  # type: ignore[arg-type]
        # 0.1.11.3 Q3: every other evidence item is ONE line as it is written (or one listed answer), or the row cites nothing.
        cited = clean_citations(normalized_matrix, boundary.lines, boundary.answers, boundary.stories)  # type: ignore[arg-type]
    # Orchestrator #14: the words of each kept row of a v9 matrix -> its id (its own, or the one a first assessment's
    # row is given: that needs the posting's digest, so outside ``assess_once`` only a row's own id is known).
    row_ids: dict[str, str] = {}
    id_of_row: dict[int, str] = {}  # a kept row (by identity) -> its id, its own or the one a first assessment gives it
    if is_v9:
        ids: list[object] = (
            _row_ids(normalized_matrix, boundary.posting_sha256) if boundary.posting_sha256 is not None  # type: ignore[arg-type]
            else [row.get("id") for row in normalized_matrix]  # type: ignore[union-attr]
        )
        by_row = {id(row): row_id for row, row_id in zip(normalized_matrix, ids) if is_row_id(row_id)}
        id_of_row = by_row  # type: ignore[assignment]
        for row in normalized_matrix:
            named_rows.setdefault(fold(str(row.get("requirement") or "")), row)  # type: ignore[union-attr]
        row_ids = {words: by_row[id(row)] for words, row in named_rows.items() if words and id(row) in by_row}

    raw_questions = _normalize_string_list(decoded.get("questions"))
    unplaced = 0
    if is_v9 and boundary.known_text:
        # 0.1.11.4 Q4: a question that names a city nobody gave is not asked (its row, if it holds, gets the row's own question below).
        raw_questions, unplaced = without_unknown_places(raw_questions, "question", boundary.known_text)
    plain_questions: list[str] = []
    structured_questions: list[object] = []
    plain_at: dict[int, int] = {}  # a structured question -> where its words are in the plain list
    for item in raw_questions:
        if isinstance(item, str):
            plain_questions.append(item)
            continue
        normalized_item = _normalize_question_item(item)
        if normalized_item is None:
            continue
        named = normalized_item["requirement"]  # type: ignore[index]
        if isinstance(named, str):
            known = renamed.get(named.strip()) if named.strip() in renamed else renamed.get(fold(named))
            if isinstance(known, str) and known != named:
                normalized_item["requirement"] = known  # type: ignore[index]
        structured_questions.append(normalized_item)
        plain_at[id(normalized_item)] = len(plain_questions)
        plain_questions.append(normalized_item["question"])

    # 0.1.11.3 Q2: an unclear row whose key terms the master states is met with the master's line(s), and its question
    # is not asked. BEFORE the caps and the gate, so a question that is kept takes the freed place and the verdict is the gate's.
    met_by_master: list[tuple[str, str]] = []
    stated_questions: list[str] = []
    if is_v9 and boundary.master is not None:
        structured_questions, answered, settled_rows = settle_stated(normalized_matrix, structured_questions, boundary.master)  # type: ignore[arg-type]
        met_by_master = [(id_of_row.get(id(row), ""), rule) for row, rule in settled_rows]
        stated_questions = [str(item["question_id"]) for item in answered]  # type: ignore[index]
        gone = sorted(plain_at.pop(id(item)) for item in answered)
        plain_questions = [text for index, text in enumerate(plain_questions) if index not in gone]
        plain_at = {key: at - sum(1 for index in gone if index < at) for key, at in plain_at.items()}

    said = _normalize_verdict(decoded.get("verdict")) if "verdict" in decoded else None
    # Orchestrator #14: at most three questions on one-of-a-list rows, those of the rows first in the matrix; the rest
    # are dropped here (no retry) and their rows read as minor gaps. A not_a_match answer keeps none at all (below).
    capped: list[str] = []
    dropped_questions_all: list[object] = []
    if is_v9 and said != "not_a_match":
        structured_questions, over = cap_list_item_questions(normalized_matrix, structured_questions)  # type: ignore[arg-type]
        capped = [str(item["question_id"]) for item in over]  # type: ignore[index]
        dropped_questions_all = list(over)
    # Orchestrator #39: at most four questions ASKED on must-have rows, the most decisive first (hard, then the posting's
    # own order); the rows of the others stay unclear and hold ("also unverified"); no retry, and code does not ask them.
    capped_mandatory: list[str] = []
    if is_v9 and said != "not_a_match":
        structured_questions, over_mandatory = cap_mandatory_questions(normalized_matrix, structured_questions)  # type: ignore[arg-type]
        capped_mandatory = [str(item["question_id"]) for item in over_mandatory]  # type: ignore[index]
        dropped_questions_all += over_mandatory
        gone = {plain_at[id(item)] for item in dropped_questions_all}
        plain_questions = [text for index, text in enumerate(plain_questions) if index not in gone]

    # 0.1.11 N3b (decision #11): an unclear must-have row of a v9 answer holds for its answer, so it carries a question.
    asked_by_code: list[str] = []
    if all_rows and boundary.retry is not None and said in ("matched_above_threshold", "pending_user_answers"):
        unasked = _unasked(normalized_matrix, structured_questions, said, boundary)  # type: ignore[arg-type]
        if unasked and not boundary.retry:
            raise FindJobsContractError("invalid_value", unasked_message([row_id for row_id, _row in unasked]))
        for row_id, row in unasked:
            asked = row_question(row_id, str(row["requirement"]))
            # A question that already carries the row's own id (an answer was given under it once) is the row's question.
            mine = next((item for item in structured_questions if isinstance(item, dict) and item.get("question_id") == asked["question_id"]), None)
            if mine is not None:
                mine["requirement"] = row["requirement"]
                continue
            structured_questions.append(asked)
            plain_questions.append(str(asked["question"]))
            asked_by_code.append(str(asked["question_id"]))

    raw_suggestions = _normalize_string_list(decoded.get("suggestions"))
    suggested = _structured_suggestions(raw_suggestions, row_ids)
    checked: list[tuple[str, str]] = []
    if is_v9 and boundary.lines:
        # 0.1.11 C3: a suggestion whose premises the matrix and the master do not hold is dropped (or becomes a gap).
        with_ids = [{**row, "id": id_of_row[id(row)]} if id(row) in id_of_row else row for row in normalized_matrix]  # type: ignore[union-attr]
        suggested, refused = check_suggestions(suggested, with_ids, boundary.lines, boundary.answers, boundary.known_text)  # type: ignore[arg-type]
        checked = [(item.kind, why) for item, why in refused]
        # 0.1.11.3 Q1: a plain-string suggestion that says the resume is silent on a term a master line states goes too.
        raw_suggestions, _untrue = truthful_notes(raw_suggestions, boundary.lines, boundary.known_text)
    result: dict[str, object] = {
        "matrix": normalized_matrix,
        "suggestions": [item for item in raw_suggestions if isinstance(item, str)],
        "questions": plain_questions,
    }
    if structured_questions:
        result["structured_questions"] = structured_questions
    if "sponsorship" in decoded:
        sponsorship = _normalize_sponsorship(decoded.get("sponsorship"))
        if sponsorship is not None:
            result["sponsorship"] = sponsorship
    if "verdict" in decoded:
        verdict = _normalize_verdict(decoded.get("verdict"))
        if met_by_master and verdict == "pending_user_answers":
            # The verdict the model gave may rest on a settled row: read it again from what is left (as for a removed
            # authorization row). ``settled_verdict`` below makes it pending when a must-have is still open.
            verdict = "matched_above_threshold"
        if verdict is not None:
            # 0110-10-03: a lone open question on a one-of-a-list row does not hold a match (and a must-have one does).
            result["verdict"] = settled_verdict(verdict, normalized_matrix if isinstance(normalized_matrix, list) else (), structured_questions)
    if rows_not_shown:
        result["rows_not_shown"] = rows_not_shown
    if "not_a_match_reason" in decoded:
        reason = decoded.get("not_a_match_reason")
        if isinstance(reason, str):
            result["not_a_match_reason"] = reason
        elif reason is None:
            result["not_a_match_reason"] = None
    # v9, on the settled verdict: a failed verdict keeps no suggestion (it has no next action, like its questions);
    # a pending one keeps the kinds that need no resume; only a Matched answer whose prompt showed ids keeps a pick.
    settled = result.get("verdict")
    kept = suggested
    if settled == "not_a_match":
        kept = []
    elif settled == "pending_user_answers":
        kept = [item for item in suggested if item.kind in SUGGESTION_KINDS_WITHOUT_RESUME]
    result["suggestions"] = [*result["suggestions"], *(item.why for item in kept)]  # type: ignore[misc]
    pick = _lenient_pick(decoded.get("pick"))
    keeps_pick = settled == "matched_above_threshold" and boundary.source_ids is not None
    boundary.extras = AssessExtras(
        pick=pick if keeps_pick else None,
        structured_suggestions=tuple(kept),
        dropped_pick=pick is not None and not keeps_pick,
        dropped_suggestions=len(checked) + len(suggested) - len(kept),
        unknown_sources=tuple(unknown_sources),
        asked_by_code=tuple(asked_by_code),
        capped_questions=tuple(capped),
        capped_mandatory_questions=tuple(capped_mandatory),
        checked_suggestions=tuple(checked),
        met_by_master=tuple(met_by_master),
        stated_questions=tuple(stated_questions),
        checked_citations=tuple(cited),
        unplaced_questions=unplaced,
    )
    # Drop any other unknown keys (e.g. a model echoing "posting" back, or
    # inventing extra fields): the frozen contract is a closed object, and
    # normalization's job is to fix shape, not to smuggle new keys through.
    dropped_ids, dropped_count = _strip_not_a_match_questions(result)
    return result, dropped_ids, dropped_count


__all__ = [
    "ASSESS_PROMPT_VERSION",
    "ASSESS_PROMPT_VERSION_HYBRID",
    "ASSESS_PROMPT_VERSION_NO_WORK_MODE",
    "CURRENT_ASSESS_PROMPT_VERSIONS",
    "AssessAttempt",
    "AssessContext",
    "AssessExtras",
    "AssessJob",
    "Boundary",
    "PLACEHOLDER_ID_EXAMPLE",
    "PLACEHOLDER_NOTE_EXAMPLE",
    "PLACEHOLDER_PICK_LINES",
    "PLACEHOLDER_REQUIREMENTS",
    "REQUIREMENTS_BLOCK_HEADER",
    "ROW_QUESTION_CATEGORY",
    "BankAnswer",
    "INSTRUCTIONS_DIGEST",
    "POSTING_INCOMPLETE_MESSAGE",
    "PriorAnswer",
    "assess_once",
    "assess_prompt_version",
    "build_assess_context",
    "constraints_digest",
    "posting_looks_incomplete",
    "requirement_row_count",
    "setup_evidence",
    "invoke_json_once",
    "load_assess_instructions",
    "normalize_work_mode",
    "offered_sources",
    "prompt_reads_ids",
    "render_assess_prompt",
    "row_question",
    "row_question_id",
    "template_takes",
]
