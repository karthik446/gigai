"""The bounded host caller for a local Scout proposal assessment.

This module is deliberately small: authority resolution stays in the existing
completed-discovery and committed-input resolvers, model transport stays in
``run_model_invocation``, and this host caller only joins their exact bytes,
validates the model assessment, and records a private result.  It does not
create a resume, Tailor action, application state, or an approval pointer.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from .. import model_execution
from ..canonical import (
    EntityPrefix,
    canonical_json_bytes,
    digest_imported_bytes,
    parse_json_front_matter,
    parse_json_bytes,
    validate_entity_id,
)
from .assessment_core import (  # noqa: F401 - moved in P1; re-exported by the old names
    _MAX_PROMPT_POSTING_TEXT,
    _MAX_PROMPT_RESUME_TEXT,
    _MAX_PROMPT_VALIDATION_ERROR,
    _SPONSORSHIP_SYNONYMS,
    _STATUS_SYNONYMS,
    _extract_json_object,
    _normalize_assessment_payload,
    _normalize_sponsorship,
    _normalize_status,
    _normalize_string_list,
    AssessContext,
    AssessJob,
    assess_once,
    render_assess_prompt,
)
from .find_jobs.progress import ProgressWriter
from ..journal import (
    JournalArtifact,
    JournalConflictError,
    JournalEntry,
    JournalTransition,
    _git,
    _git_bytes,
    JournalSnapshot,
    JournalWriter,
    read_committed_artifact,
    run_with_journal_writer,
)
from ..model_targets import ModelTargetResolutionError, resolve_model_target
from ..roles import RoleError, require_registered
from .inputs import ScoutInputError, resolve_external_input
from .posting_inputs import (
    ScoutPostingInputError,
    resolve_discovery_posting_input_from_journal,
)
from .proposals import (
    ProposalSource,
    ScoutProposalError,
    ScoutProposalRequest,
    build_proposal_prompt,
    validate_proposal_output,
)
from ..workpad import ResolvedWorkpad
from ..config import GigAIConfig
from ..model_execution import (
    InvocationBudget,
    InvocationPolicy,
    ModelInvocationExecution,
    SelectedReference,
)
from ..adapters.factory import AdapterFactoryError, resolve_model_adapter
from ..validators import validate_goal_graph, validate_serialized_contract
from ..validators import validate_model_invocation


_ROLE = "reviewer"
_MAX_PRIVATE_SOURCES = 12
_MAX_RESULT_BYTES = 512 * 1024
_PRIVATE_PURPOSES = frozenset({"preferences", "experience", "answer"})
_REFERENCE_KINDS = frozenset({"resume", "project_evidence", "role_history", "cover_letter"})
_TERMINAL_GOAL_STATES = frozenset({"complete", "failed", "blocked", "cancelled"})


class ScoutProposalExecutionError(ValueError):
    """Content-free refusal before or during a local proposal assessment."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _assess_progress_writer(context: "NodeContext", target: Path | None) -> ProgressWriter | None:
    """Best-effort ``ProgressWriter`` for this run, mirroring acquire's own.

    Assess's ``NodeContext`` always carries ``workpad_path`` (unlike acquire,
    which may resolve through ``home_root``/``target``), so this reads that
    directly rather than re-resolving the workpad -- one less way for a
    progress-only failure to diverge from the sealed node's own resolution.

    ``target`` (the operator's bound repo/target root, as ``present_api``
    binds it) is NOT used here even when present: it can differ from the
    workpad root whenever the active Gig's workpad isn't the target root
    itself, and every other progress writer/reader -- acquire's own
    ``_progress_writer`` in ``market_acquisition.py``, and
    ``present_api.run_progress`` (``/progress``) -- always resolves and uses
    the real workpad path. Writing under ``target`` instead leaves a stray
    ``runs/`` dir in the operator's repo and the UI stuck on "waiting"
    because ``/progress`` never looks there.
    """

    try:
        run_id = getattr(context, "run_id", None)
        workpad_path = getattr(context, "workpad_path", None)
        root = Path(workpad_path) if isinstance(workpad_path, str) else None
        if root is None or not isinstance(run_id, str) or not run_id:
            return None
        return ProgressWriter(root / "runs" / run_id)
    except Exception:  # noqa: BLE001 - progress must never break the sealed run
        return None


def assess_node(
    context: "NodeContext",
    input: "AssessInput",
    *,
    home_root: Path,
    target: Path | None,
    config: GigAIConfig,
) -> "AssessOutput":
    """Run the bounded find-jobs assess node (progress-wrapped; see ``_assess_node_body``)."""

    progress = _assess_progress_writer(context, target)
    if progress is not None:
        progress.start_step("assess")
    try:
        output = _assess_node_body(
            context, input, home_root=home_root, target=target, config=config, progress=progress
        )
    except BaseException as exc:
        if progress is not None:
            # uat-bug-005 part 2: carry the failure's own message into
            # progress/steps.json so /progress (the UI's live-status read)
            # doesn't leave the operator looking at a bare "failed" with no
            # explanation -- str(exc) matches what the sealed node-failure
            # receipt (run.py's _redacted_failure_message) is built from for
            # every other exception type; only BaseException subclasses that
            # aren't a plain str-able message (rare: e.g. a bare
            # KeyboardInterrupt) would give an empty string here, which
            # finish_step already tolerates as "no message" via `or None`.
            progress.finish_step("assess", ok=False, message=str(exc) or None)
        raise
    if progress is not None:
        progress.finish_step("assess", ok=True)
    return output


def _assess_node_body(
    context: "NodeContext",
    input: "AssessInput",
    *,
    home_root: Path,
    target: Path | None,
    config: GigAIConfig,
    progress: ProgressWriter | None,
) -> "AssessOutput":
    """Run the bounded find-jobs assess node.

    This is intentionally a node-level host seam rather than a second proposal
    DTO.  The frozen graph contracts are imported lazily so this module remains
    usable while the contract/records packets land in parallel.
    """
    from .find_jobs.contracts import (
        AssessOutput,
        AssessmentResult,
        MatrixStatus,
        NotAssessedReason,
        NotAssessedRow,
        Producer,
        RequirementMatrixRow,
        RowOutcome,
        SelectionRule,
        PostingRowResult,
        ModelTarget as ContractModelTarget,
        UsageBlock,
    )

    if context.model_target != input.model_target:
        raise ScoutProposalExecutionError("model_target_mismatch", "sealed model target differs from node context")
    model_target = input.model_target.value
    if model_target not in {"ollama_local", "codex_cli", "openrouter_api"}:
        raise ScoutProposalExecutionError("model_target_invalid", "unsupported model target")
    # U2 (0.1.8.1 UAT): the sealed enum value (ollama_local/codex_cli/
    # openrouter_api) is an *adapter kind*, never a configured target's own
    # name -- ``gigai setup`` names its targets "codex-default",
    # "claude-default", etc, so ``resolve_model_adapter(config, "codex_cli")``
    # (a literal lookup by name) always failed. Resolve the adapter kind
    # through the operator's own configuration -- the configured target whose
    # endpoint's adapter matches -- instead of hard-coding target name
    # strings. No silent fallback to another provider (existing rule): an
    # unmatched adapter kind fails loudly, naming the fix.
    adapter_target = _resolve_configured_target_name_for_adapter(config, model_target)
    try:
        binding = resolve_model_adapter(config, adapter_target, home_root=home_root)
    except (AdapterFactoryError, ModelTargetResolutionError, KeyError) as exc:
        # Missing credentials and unknown targets are setup errors, not a row
        # level model outage: callers must see them loudly.
        raise ScoutProposalExecutionError("model_target_unavailable", "sealed model target or credential is unavailable") from exc

    root = Path(target) if isinstance(target, Path) else Path(context.workpad_path)
    resume = _read_pinned_resume(home_root, root, context.gig_id, input.pinned_resume)
    acquire_rows = _read_acquire_rows(root, input.acquire_batch_ref)
    policy = assess_invocation_policy(model_target, input)
    sealed_config = _read_sealed_config(root, context.run_id)
    visa_sponsorship_required = bool(getattr(sealed_config, "visa_sponsorship_required", False))
    # P2 (v0.1.9), operator answer 5: {{countries}} comes from find-jobs.json;
    # {{titles}} is the effective config's roles (overlay_selected_profile
    # already replaces roles with the profile's titles before sealing, so
    # this is the profile's titles when one is selected, C11-adjacent).
    prompt_countries = tuple(getattr(sealed_config, "countries", ()) or ())
    prompt_titles = tuple(getattr(sealed_config, "roles", ()) or ())
    # assess-prompt-v2 (v0.1.9), operator decision: {{candidate_location}} is
    # the sealed config's own ``location`` (the operator's "Denver, CO" from
    # find-jobs.json), so assess.md rule 4 can decide a posting's
    # state/province restriction; None/empty renders "unknown".
    prompt_location = str(getattr(sealed_config, "location", "") or "")

    # Candidate resolution mirrors acquire's own selection loop exactly
    # (coordinator decision, P2 dispatch): a candidate is a new/edited,
    # role-matched row. Every candidate that isn't selected gets a reason
    # -- exclusion_reason() first (location_mismatch/sponsorship_excluded),
    # then B2's selection helper (duplicate/over_cap) for an otherwise-
    # eligible row acquire's diversity selection left behind. Duplicate/
    # failed and role-mismatched rows are not candidates at all and never
    # appear in candidate_rows.
    #
    # uat-bug-009: UNCHANGED is no longer an automatic exclusion here either
    # -- acquire itself only ever selects an UNCHANGED row into
    # `input.selected_postings` (assess never widens the *selected* set on
    # its own) when it found no successful prior assessment for the current
    # resume revision (see `market_acquisition._prior_assessments`). So a
    # selected UNCHANGED row is already-sealed selection authority the same
    # as a selected NEW/EDITED row; an UNCHANGED row that ISN'T selected is
    # independently re-checked here with the identical pure helpers acquire
    # used, so the two call sites can never disagree about which UNCHANGED
    # rows are genuinely skippable (has a valid prior assessment) versus
    # eligible (recorded provisionally as OVER_CAP below, same as any other
    # otherwise-eligible row acquire's diversity selection didn't pick).
    from .find_jobs.contracts import PinnedResume
    from .find_jobs.filters import exclusion_reason
    from .find_jobs.market_acquisition import _default_profile_id, _prior_assessments, _role_match
    from .find_jobs.selection import select_for_assessment
    from ..workpad import resolve_workpad

    selected_by_url = {item.normalized_url: item for item in input.selected_postings}
    roles = tuple(getattr(sealed_config, "roles", ())) if sealed_config is not None else ()
    resume_revision_id = input.pinned_resume.revision_id if isinstance(input.pinned_resume, PinnedResume) else None
    # S25 A2/F1-b: the corrected cache key adds `profile_id` ALONGSIDE the
    # resume-revision dimension -- twin predicate with market_acquisition's
    # own acquire-side carry-forward check (that module's
    # `_run_profile_identity`/`_default_profile_id` docstrings have the full
    # rationale; kept identical here by hand, since assess doesn't import
    # acquire's candidate loop).
    try:
        resolved_for_profile = resolve_workpad(
            home_root=home_root, requested_target=root, gig_id=context.gig_id, allow_semantic_state=True
        )
    except Exception:
        resolved_for_profile = None
    default_profile_id = _default_profile_id(resolved_for_profile)
    sealed_run_input = _read_sealed_run_input(root, context.run_id)
    current_profile_ref = getattr(sealed_run_input, "profile_ref", None)
    current_profile_id = (
        current_profile_ref.profile_id if current_profile_ref is not None else default_profile_id
    )
    prior_assessments = _prior_assessments(root, context.run_id, default_profile_id=default_profile_id)
    rows: list[object] = []
    not_assessed: list[object] = []
    to_assess: list[tuple[object, bytes | None]] = []
    eligible_postings: list[object] = []
    for posting, outcome in acquire_rows:
        selected = selected_by_url.get(posting.normalized_url)
        # uat-bug-009-r1: a selected row is already-sealed selection
        # authority (see the `is_candidate` branch below) regardless of its
        # acquire outcome -- a selected UNCHANGED row must fall through to
        # be assessed, the same as a selected NEW/EDITED row, so this check
        # comes first and never routes a selected row into the outcome
        # branches below. Twin predicate: market_acquisition.py's own
        # candidates loop (acquire side) applies the identical
        # selected-first / unchanged-valid-prior-skip / other-outcomes-skip
        # ordering; keep the two in sync by hand since assess doesn't import
        # acquire's candidate loop.
        if selected is not None:
            pass
        elif outcome is RowOutcome.UNCHANGED:
            prior = prior_assessments.get(posting.normalized_url)
            if (
                prior is not None
                and prior.result.posting.content_sha256 == posting.content_sha256
                and resume_revision_id is not None
                and prior.resume_revision_id == resume_revision_id
                # S25 A2: profile_id must ALSO match -- twin predicate with
                # market_acquisition's own acquire-side check (see its
                # docstring for why `None == None` is a legitimate match:
                # no profile has ever been migrated for this workpad at
                # all, today's pre-F1-b behaviour, kept unchanged).
                and prior.profile_id == current_profile_id
            ):
                # A genuinely skippable UNCHANGED row: not a candidate at
                # all, same as before this fix. Its carried-forward result
                # is surfaced by present_api.py from acquire's own
                # AcquireOutput.carried_forward_assessments, not here.
                continue
        elif outcome not in (RowOutcome.NEW, RowOutcome.EDITED):
            continue
        if selected is not None:
            # Already-sealed selection authority (AssessInput validates
            # every selected posting's role_match=True at construction) --
            # never re-derive it here, so an assess call with no sealed
            # config (older run dirs, most direct-call tests) still assesses
            # every explicitly selected posting.
            is_candidate = True
        elif sealed_config is not None:
            is_candidate = _role_match(posting, roles)
        else:
            # No sealed config and not selected: there is no config to
            # derive role-match or exclusion from, so this row is simply
            # not a candidate (matches the pre-existing behavior of only
            # ever assessing the selected set when no run input is sealed).
            is_candidate = False
        if not is_candidate:
            continue
        rows.append(PostingRowResult(posting, outcome))
        if selected is not None:
            to_assess.append((posting, _posting_text_bytes(posting)))
            continue
        reason = exclusion_reason(posting, sealed_config) if sealed_config is not None else None
        if reason is not None:
            not_assessed.append(NotAssessedRow(posting, reason))
            continue
        # Not excluded by location/visa/role -- an otherwise-eligible row
        # that acquire's diversity selection (B2) didn't pick. Recorded
        # provisionally; the actual duplicate/over_cap split is resolved
        # below by re-running the same pure helper over the same eligible
        # set, so this can never disagree with what acquire itself dropped.
        eligible_postings.append(posting)
        not_assessed.append(NotAssessedRow(posting, NotAssessedReason.OVER_CAP))

    if eligible_postings:
        # Recompute B2's selection over every eligible (not excluded,
        # role-matched, new/edited) row -- selected postings plus the
        # not-yet-labeled ones above -- from the same inputs acquire itself
        # used (selection_cap; per_company stays at select_for_assessment's
        # own default, matching acquire). Same pure helper, same inputs:
        # its `dropped` map can only ever agree with acquire's own drop, so
        # a selected posting recomputing as "dropped" here (e.g. an older
        # sealed AssessInput from before this helper existed) never
        # overrides the sealed selection authority above -- only the
        # provisional OVER_CAP labels just added are refined.
        #
        # P6: acquire orders its own `candidates` by the SEALED
        # `AcquireOutput.rank_scores` before calling `select_for_assessment`
        # (market_acquisition.py); this twin recompute must reproduce that
        # exact order from the same sealed scores, never re-rank by calling
        # Jev again, or the two selections (and their duplicate/over_cap
        # labels) could disagree. `_read_rank_scores` degrades to `()` for a
        # run with no Jev key/pre-P6 run, in which case `_order_by_rank_scores`
        # is a no-op and this list is in its original (pre-P6) order, exactly
        # as before this packet.
        selected_postings_as_rows = [
            posting for posting, _outcome in acquire_rows if posting.normalized_url in selected_by_url
        ]
        rank_scores = _read_rank_scores(root, input.acquire_batch_ref)
        ordered_eligible = _order_by_rank_scores(list(eligible_postings), rank_scores)
        recomputed = select_for_assessment(
            [*selected_postings_as_rows, *ordered_eligible],
            cap=input.selection_cap,
        )
        drop_reason_by_url = {
            url: (NotAssessedReason.DUPLICATE if reason == "duplicate" else NotAssessedReason.OVER_CAP)
            for url, reason in recomputed.dropped.items()
        }
        not_assessed = [
            NotAssessedRow(row.posting, drop_reason_by_url.get(row.posting.normalized_url, row.reason))
            if row.posting.normalized_url in drop_reason_by_url
            else row
            for row in not_assessed
        ]

    if progress is not None:
        # B4: every row/posting that is definitively not going to the model
        # (unchanged, role-mismatch never even reaches this list, excluded,
        # duplicate/over_cap) is recorded now, before the model loop even
        # starts -- the UI's "why wasn't this assessed" text doesn't have to
        # wait for the whole assess step to finish.
        for entry in not_assessed:
            progress.not_assessed(entry.posting.normalized_url, reason=entry.reason.value)

    assessments = []
    revisions = []
    usage_values = []
    attempted = 0
    model_attempts = 0
    producer = Producer("scout.find_jobs.assess", "1", "scout-assess", ContractModelTarget(model_target), getattr(binding.port, "name", model_target))
    from ..workpad import resolve_workpad
    resolved = resolve_workpad(home_root=home_root, requested_target=root, gig_id=context.gig_id, allow_semantic_state=True)
    # B-1 owns parsing and persistence. Keep both imports lazy so the
    # parallel packet can replace these exact seams in tests.
    from .proposals import parse_assessment_proposal
    from .proposal_records import save_assessment_revision

    # P1: the prompt -> invoke -> extract -> normalize -> validate -> retry
    # loop is `assessment_core.assess_once`; this node keeps selection,
    # reuse/skip, sealing, journaling and progress around it.
    assess_context = AssessContext(
        resume_text=resume.decode("utf-8", errors="replace"),
        visa_sponsorship_required=visa_sponsorship_required,
        countries=prompt_countries,
        titles=prompt_titles,
        location=prompt_location,
    )

    for posting, posting_text in to_assess[: input.selection_cap]:
        if not posting_text:
            # U25: an older acquire batch (or a row the acquirer genuinely
            # could not fetch) has no captured posting text.  Guessing a
            # requirements matrix from the title alone is worse than not
            # assessing it, so this single posting is skipped and the rest
            # of the batch continues (U22 per-posting isolation).
            not_assessed.append(NotAssessedRow(posting, NotAssessedReason.FAILED))
            if progress is not None:
                progress.not_assessed(posting.normalized_url, reason=NotAssessedReason.FAILED.value)
            continue
        attempted += 1
        if progress is not None:
            # B4: a "started" line the instant this posting is handed to the
            # model, so its card can show "assessing…" instead of sitting on
            # "waiting" for however long the model call + retry takes.
            progress.assessment_started(posting.normalized_url)
        # The frozen assessment_result.posting field is the narrower
        # SelectedPosting DTO, not the full PostingRow the model was
        # shown; use the sealed selected-posting identity so parsing
        # never fails on PostingRow's extra keys (provider,
        # board_token, text, ...).
        selected_posting = selected_by_url[posting.normalized_url]
        selected_posting_json = selected_posting.to_json()

        def parse_selected(normalized: dict[str, object], _posting_json: dict = selected_posting_json) -> object:
            return parse_assessment_proposal(
                {**normalized, "posting": _posting_json, "proposal_revision_ref": None}
            )

        outcome = assess_once(binding, _assess_job(posting, posting_text), assess_context, parse=parse_selected)
        model_attempts += outcome.attempts
        if not outcome.ok:
            # MODEL_DENIED / MODEL_UNAVAILABLE / MODEL_OUTPUT_INVALID, mapped
            # inside assess_once exactly as this loop mapped them before P1;
            # every other exception has already propagated (U22 per-posting
            # isolation applies only to failures the boundary can name).
            reason = outcome.not_assessed_reason
            not_assessed.append(NotAssessedRow(posting, reason))
            if progress is not None:
                progress.assessment_finished(posting.normalized_url, ok=False, reason=reason.value)
            continue
        parsed = outcome.parsed
        saved = save_assessment_revision(
            home_root=home_root,
            target=resolved,
            posting=selected_posting,
            result=parsed,
            producer=producer,
            pinned_resume=input.pinned_resume,
        )
        revision_ref = _revision_ref(saved)
        assessments.append(replace(parsed, proposal_revision_ref=revision_ref))
        if revision_ref:
            revisions.append(revision_ref)
        usage_values.append(outcome.usage)
        if progress is not None:
            progress.assessment_finished(posting.normalized_url, ok=True, assessment_json=parsed.to_json())

    if attempted and model_attempts and not assessments:
        # Every posting that had text and reached the model failed there.
        # A single bad model answer must not silently drop the whole batch
        # (U22); but if literally everything failed at the model, that is a
        # real node-level failure the caller (and U21's failure receipt)
        # must see, not a quiet empty result.
        raise ScoutProposalExecutionError(
            "assess_all_postings_failed",
            "every selected posting failed at the model boundary",
        )

    usage = _usage_block(usage_values, UsageBlock)
    return AssessOutput(tuple(input.selected_postings), input.pinned_resume, input.target, input.selection_cap, SelectionRule.NEW_OR_EDITED_ROLE_MATCH, tuple(rows), tuple(assessments), tuple(not_assessed), tuple(revisions), ContractModelTarget(model_target), producer, usage, ())


def _resolve_configured_target_name_for_adapter(config: GigAIConfig, adapter_kind: str) -> str:
    """The name of the configured, enabled target whose endpoint uses ``adapter_kind``.

    U2 (0.1.8.1 UAT): the sealed model-target enum (``ollama_local`` /
    ``codex_cli`` / ``openrouter_api``) names an *adapter kind*, not a
    configured target -- ``gigai setup`` always names its targets
    ``"<provider>-default"`` (``codex-default``, ``claude-default``, ...).
    This maps through the operator's own configuration rather than a
    hard-coded string list, so it keeps working whichever name setup or the
    operator gave the target.

    Fails loudly, naming the fix, when no configured+enabled target uses
    this adapter kind (never silently falls back to another provider) and
    when more than one does (ambiguous: the caller must disable/remove one
    or the sealed enum cannot pick between them).

    uat-bug-005: the 0.1.8.x README told users to create a target literally
    named after the sealed value (e.g. ``codex_cli``) alongside ``gigai
    setup``'s own ``codex-default``, both on the same endpoint. An enabled
    target whose NAME equals the sealed value wins outright -- it is
    unambiguous by construction, no adapter scan needed. Only when no target
    is named exactly the sealed value does this fall back to the adapter
    scan below (unchanged ambiguity behavior for everyone else).
    """

    exact_name_matches = [
        target.name
        for target in config.model_targets
        if target.enabled and target.name == adapter_kind
    ]
    if len(exact_name_matches) == 1:
        return exact_name_matches[0]

    endpoint_names = {
        endpoint.name for endpoint in config.endpoints if endpoint.adapter == adapter_kind
    }
    matches = [
        target.name
        for target in config.model_targets
        if target.enabled and target.endpoint in endpoint_names
    ]
    if not matches:
        raise ScoutProposalExecutionError(
            "model_target_unavailable",
            f"no configured model target uses adapter {adapter_kind!r}; "
            f"run `gigai setup` to configure one (e.g. via --model-target "
            f"NAME=ENDPOINT:MODEL) before assessing with this target",
        )
    if len(matches) > 1:
        names = ", ".join(sorted(matches))
        raise ScoutProposalExecutionError(
            "model_target_unavailable",
            f"multiple configured model targets use adapter {adapter_kind!r} "
            f"({names}); only one may stay enabled -- disable or remove all "
            f"but one, either by setting `enabled = false` on its "
            f"`[[model_targets]]` entry in config.toml or deleting that "
            f"entry, or by re-running `gigai setup` and unchecking it, so "
            f"the sealed target {adapter_kind!r} can resolve unambiguously",
        )
    return matches[0]


def _assess_job(posting: object, posting_text: bytes) -> AssessJob:
    """The prompt-facing view of one posting row (title/company/location + bounded text)."""

    posting_json = posting.to_json()
    return AssessJob(
        title=str(posting_json.get("title", "")),
        company=str(posting_json.get("company", "")),
        location=str(posting_json.get("location", "") or ""),
        posting_text=posting_text.decode("utf-8", errors="replace"),
    )


def _assess_prompt(
    posting: object,
    resume: bytes,
    posting_text: bytes,
    visa_sponsorship_required: bool,
    validation_error: str | None = None,
) -> str:
    """Build the real find-jobs assessment prompt (U25).

    P1: a thin wrapper over ``assessment_core.render_assess_prompt`` (the
    template lives in ``scout/data/instructions/assess.md``); kept under this
    name because the assess-node tests assert prompt contents through it.
    """

    context = AssessContext(
        resume_text=resume.decode("utf-8", errors="replace"),
        visa_sponsorship_required=visa_sponsorship_required,
    )
    return render_assess_prompt(_assess_job(posting, posting_text), context, validation_error)


def assess_invocation_policy(model_target: str, input: object) -> InvocationPolicy:
    """Build the explicit local/hosted policy for one assess input."""
    if model_target not in {"ollama_local", "codex_cli", "openrouter_api"}:
        raise ScoutProposalExecutionError("model_target_invalid", "unsupported model target")
    selected = getattr(input, "selected_postings", ())
    pinned = getattr(input, "pinned_resume", None)
    ids = {item.normalized_url for item in selected}
    if pinned is not None:
        ids.add(pinned.record_id)
    return InvocationPolicy(
        allowed_reference_ids=frozenset(ids),
        local_allowed=model_target == "ollama_local",
        network_allowed=model_target != "ollama_local",
        offline=model_target == "ollama_local",
    )


def read_pinned_resume(home_root: Path, root: Path, gig_id: str, pinned: object) -> bytes:
    from ..private_records import read_record
    value = read_record(home_root=home_root, requested_target=root, record_id=pinned.record_id, revision_id=pinned.revision_id, content=True, gig_id=gig_id)
    content = value.get("content")
    if not isinstance(content, bytes):
        raise ScoutProposalExecutionError("resume_unavailable", "pinned resume content is unavailable")
    if digest_imported_bytes(content) != pinned.content_sha256:
        raise ScoutProposalExecutionError("resume_digest_mismatch", "pinned resume revision changed")
    return content


# P4: ``find_jobs.resume_input`` reuses the digest-verifying reader; the old
# private name stays an alias (the assess node and its tests patch that name).
_read_pinned_resume = read_pinned_resume


def _read_sealed_config(root: Path, run_id: str) -> object | None:
    """Read the sealed ``FindJobsConfig`` for this run, or ``None`` if unavailable.

    Per orchestrator decision (coordinator ask, P2 dispatch): the sealed
    ``FindJobsRunInput`` under ``runs/<run_id>/sealed/find-jobs-run-input.json``
    is the authoritative per-run config; ``AssessInput`` itself carries no
    config field and stays untouched.  Older run dirs, and callers that never
    seal a find-jobs run input (most existing tests), get ``None`` back
    rather than failing the node -- callers treat that as "no constraint"
    (visa not required, no country filter).
    """
    run_input = _read_sealed_run_input(root, run_id)
    return None if run_input is None else run_input.config


def _read_sealed_run_input(root: Path, run_id: str) -> object | None:
    """Read the full sealed ``FindJobsRunInput`` for this run, or ``None``.

    S25 A2/F1-b: ``_read_sealed_config`` above only ever needed ``.config``;
    the assess-side carry-forward predicate also needs ``.profile_ref``
    (the run's own sealed profile identity), so this sibling reads the same
    file once more rather than growing ``_read_sealed_config``'s return type
    for its one existing caller.
    """
    from .find_jobs.contracts import FindJobsRunInput

    path = root / "runs" / run_id / "sealed" / "find-jobs-run-input.json"
    if path.is_symlink() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return FindJobsRunInput.from_json(payload)
    except (OSError, ValueError, TypeError):
        return None


def _read_acquire_rows(root: Path, batch_ref: str) -> tuple[tuple[object, object], ...]:
    """Return every acquired ``(PostingRow, RowOutcome)`` pair, in acquire's own order.

    Assess needs the *full* acquire batch, not just the selected postings, so
    it can classify every role-matched new/edited row -- selected or not --
    with the same ``exclusion_reason``/over-cap accounting acquire itself
    used (coordinator decision, P2 dispatch).
    """
    from .find_jobs.contracts import PostingRow, RowOutcome

    path = root / batch_ref
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value.get("rows", value.get("selected_postings", ())) if isinstance(value, Mapping) else ()
    result = []
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        posting_json = item.get("posting", item)
        if not isinstance(posting_json, Mapping):
            continue
        outcome_value = item.get("outcome") if isinstance(item, Mapping) and "outcome" in item else "new"
        try:
            outcome = RowOutcome(outcome_value) if isinstance(outcome_value, str) else RowOutcome.NEW
        except ValueError:
            outcome = RowOutcome.NEW
        result.append((PostingRow.from_json(posting_json), outcome))
    return tuple(result)


def _read_rank_scores(root: Path, batch_ref: str) -> tuple:
    """P6: the sealed ``AcquireOutput.rank_scores`` from this run's acquire batch.

    Assess re-runs ``select_for_assessment`` over the eligible set for its
    own not-assessed labeling (see the ordering call below); it must sort
    that set by the SAME scores acquire itself used, or the twin recompute
    could disagree with acquire's own selection. Reads the exact same
    ``batch_ref`` file ``_read_acquire_rows`` reads, one key over
    (``rank_scores``) -- degrades to ``()`` for any run sealed before P6, a
    run with no Jev key, or a malformed/missing file (never raises: an
    ordering enrichment must not fail assess).
    """

    from .find_jobs.jev_contracts import RankScore

    try:
        path = root / batch_ref
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    if not isinstance(value, Mapping):
        return ()
    items = value.get("rank_scores")
    if not isinstance(items, list):
        return ()
    result = []
    for item in items:
        try:
            result.append(RankScore.from_json(item))
        except (ValueError, TypeError):
            continue
    return tuple(result)


def _order_by_rank_scores(rows: list, rank_scores: tuple) -> list:
    """Stable sort ``rows`` (objects with ``.normalized_url``) by ``rank_scores``.

    Mirrors ``jev_rank.order_by_rank`` exactly (unscored/no-score rows last,
    stable otherwise) but takes plain ``PostingRow`` objects rather than
    requiring the whole ``jev_rank`` module's cache/HTTP machinery -- assess
    only ever needs to reproduce acquire's ordering from already-sealed
    scores, never to call Jev itself.
    """

    if not rank_scores:
        return rows
    by_url = {item.normalized_url: item for item in rank_scores}

    def sort_key(row: object) -> tuple[int, int]:
        score = by_url.get(getattr(row, "normalized_url", None))
        if score is None or score.score is None:
            return (1, 0)
        return (0, -score.score)

    return sorted(rows, key=sort_key)


def _posting_text_bytes(posting: object) -> bytes | None:
    """C0's ``PostingRow.text`` snapshot as bytes, or ``None`` when unavailable (U25)."""
    text = getattr(posting, "text", None)
    return text.encode("utf-8") if text else None


def _revision_ref(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("revision_ref", "proposal_revision_ref", "ref", "path"):
            if isinstance(value.get(key), str):
                return value[key]
    return None


def _usage_block(values: list[object], cls: object) -> object | None:
    if not values:
        return None
    input_tokens = sum(v.input_tokens or 0 for v in values if hasattr(v, "input_tokens"))
    output_tokens = sum(v.output_tokens or 0 for v in values if hasattr(v, "output_tokens"))
    total_tokens = sum(v.total_tokens or 0 for v in values if hasattr(v, "total_tokens"))
    return cls(True, input_tokens, output_tokens, total_tokens, None, None, "unavailable")


@dataclass(frozen=True)
class ScoutProposalExecution:
    """The invocation evidence and host-owned private assessment result."""

    invocation: ModelInvocationExecution
    result: dict[str, object]
    result_entry: JournalEntry


def execute_local_proposal(
    *,
    resolved: ResolvedWorkpad,
    config: GigAIConfig,
    run_id: str,
    goal_id: str,
    model_target: str,
    posting_selector: Mapping[str, object],
    private_selectors: tuple[Mapping[str, object], ...],
    local_allowed: bool,
    budget: InvocationBudget | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> ScoutProposalExecution:
    """Assess one authenticated posting against selected private revisions.

    All selected source bytes are resolved under one caller-held writer.  The
    existing local-only model execution path performs target identity checks,
    invocation journaling, and transport closure.  A ``ref_`` G45 reference is
    required as an invocation evidence anchor because the accepted invocation
    contract only admits canonical reference IDs; native/discovery sources
    remain host-bound in the result lineage rather than receiving fabricated
    durable IDs.
    """

    try:
        validate_entity_id(run_id, expected_prefix=EntityPrefix.RUN)
        validate_entity_id(goal_id, expected_prefix=EntityPrefix.GOAL)
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "execution_identity_invalid", "Run or goal identity is invalid"
        ) from exc
    if not isinstance(posting_selector, Mapping) or not isinstance(
        private_selectors, tuple
    ):
        raise ScoutProposalExecutionError(
            "execution_input_invalid", "proposal source selectors are invalid"
        )
    if not private_selectors or len(private_selectors) > _MAX_PRIVATE_SOURCES:
        raise ScoutProposalExecutionError(
            "execution_input_invalid",
            "proposal needs a bounded private source selection",
        )
    # A canonical Run/Goal identifier is not execution authority.  Resolve
    # the committed owning Run, its sealed graph membership, active goal, and
    # narrow write-workpad effect before touching the model boundary.
    _require_active_goal(resolved, run_id, goal_id)
    try:
        target = resolve_model_target(config, model_target)
    except ModelTargetResolutionError as exc:
        raise ScoutProposalExecutionError(
            "local_target_invalid", "configured model target is unavailable"
        ) from exc
    if target.endpoint.adapter != "ollama_local":
        raise ScoutProposalExecutionError(
            "local_target_required",
            "proposal execution requires the configured local Ollama target",
        )
    if not target.target.model_digest:
        raise ScoutProposalExecutionError(
            "local_target_invalid", "configured local model identity is incomplete"
        )
    if not local_allowed:
        raise ScoutProposalExecutionError(
            "local_runtime_denied", "explicit local runtime permission is required"
        )
    try:
        require_registered(_ROLE, namespace="model_invocation")
    except RoleError as exc:
        raise ScoutProposalExecutionError(
            "invocation_role_unavailable", "registered reviewer role is unavailable"
        ) from exc

    try:
        posting, private_sources, sealed_head = _resolve_sources(
            resolved, posting_selector, private_selectors
        )
        request = ScoutProposalRequest(posting=posting, private_sources=private_sources)
        prompt = build_proposal_prompt(request)
    except (ScoutPostingInputError, ScoutInputError, ScoutProposalError) as exc:
        raise ScoutProposalExecutionError(
            "source_resolution_refused",
            "selected proposal sources are unavailable or unauthenticated",
        ) from exc

    # Native and discovery sources have no legacy ref_ identity.  Preserve
    # their exact host-owned identity in the additive invocation descriptor
    # contract; never manufacture a G45 reference merely to satisfy v1.
    references = tuple(
        SelectedReference(
            # Source handles are host-created transport labels, not durable
            # authority IDs. Using one for every selected source makes the
            # v3 descriptor set a complete one-to-one binding, while the
            # descriptor identity digest still records the real G45/native
            # identity resolved above.
            reference_id=source.handle,
            path=str(
                source.identity.get("snapshot_ref", source.identity.get("posting_ref", {})).get("path")
                if isinstance(source.identity.get("snapshot_ref", source.identity.get("posting_ref", {})), Mapping)
                else source.handle
            ),
            content=source.content,
            content_sha256=source.content_sha256,
            media_type=str(
                source.identity.get("snapshot_ref", source.identity.get("posting_ref", {})).get("media_type", "text/plain")
                if isinstance(source.identity.get("snapshot_ref", source.identity.get("posting_ref", {})), Mapping)
                else "text/plain"
            ),
        )
        for source in (request.posting, *request.private_sources)
    )
    selected_ids = tuple(item.reference_id for item in references)
    source_descriptors = tuple(
        _source_descriptor(source, item.reference_id)
        for source, item in zip((request.posting, *request.private_sources), references)
    )
    _require_active_goal(resolved, run_id, goal_id)
    try:
        execution = model_execution.run_model_invocation(
            resolved=resolved,
            config=config,
            run_id=run_id,
            goal_id=goal_id,
            model_target=model_target,
            role=_ROLE,
            prompt=prompt,
            references=references,
                selected_reference_ids=selected_ids,
                policy=InvocationPolicy(
                    allowed_reference_ids=frozenset(selected_ids),
                    local_allowed=True,
                    offline=True,
                    selected_source_descriptors=source_descriptors,
                ),
            budget=budget,
            uuid_factory=uuid_factory,
            # Proposal validation belongs to this host caller.  Generic G18
            # invocation evidence must not terminalize the proposal Goal
            # before its domain result is known.
            commit_goal_transition=False,
        )
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "invocation_failed", "local proposal invocation failed"
        ) from exc

    # Persist invocation evidence before domain parsing/publication.  This is
    # intentionally a non-terminal receipt: a competing cancellation or
    # publication conflict must not erase the request/response/usage record.
    _publish_invocation_attempt(
        resolved=resolved,
        run_id=run_id,
        goal_id=goal_id,
        model_target=model_target,
        execution=execution,
        uuid_factory=uuid_factory,
    )
    result = _host_result(
        execution=execution,
        request=request,
        sealed_head=sealed_head,
    )
    result_bytes = canonical_json_bytes(result)
    if len(result_bytes) > _MAX_RESULT_BYTES:
        raise ScoutProposalExecutionError(
            "proposal_result_too_large", "private proposal result exceeds its bound"
        )
    invocation_id = str(execution.record["invocation_id"])
    path = f"runs/{run_id}/scout-proposals/{invocation_id}/result.json"
    result_ref = _ref(path, result_bytes)
    entry = _publish_result(
        resolved=resolved,
        run_id=run_id,
        goal_id=goal_id,
        model_target=model_target,
        result=result,
        result_ref=result_ref,
        invocation_artifacts=execution.artifacts,
        uuid_factory=uuid_factory,
    )
    return ScoutProposalExecution(execution, result, entry)


def _source_descriptor(source: ProposalSource, source_id: str) -> dict[str, object]:
    """Build a closed, digest-only descriptor from authenticated host data."""
    identity = json.loads(json.dumps(source.identity, ensure_ascii=False, default=dict))
    return {
        "source_id": source_id,
        "family": source.family,
        "purpose": source.purpose,
        "content_sha256": source.content_sha256,
        "identity_sha256": digest_imported_bytes(canonical_json_bytes(identity)),
    }


def _publish_invocation_attempt(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    goal_id: str,
    model_target: str,
    execution: ModelInvocationExecution,
    uuid_factory: Callable[[], uuid.UUID],
) -> JournalEntry:
    """Commit one non-terminal, strictly authenticated invocation receipt."""

    record = execution.record
    invocation_id = record.get("invocation_id")
    if (
        not isinstance(invocation_id, str)
        or record.get("run_id") != run_id
        or record.get("goal_id") != goal_id
        or not validate_model_invocation(record).valid
    ):
        raise ScoutProposalExecutionError(
            "invocation_evidence_invalid", "local invocation evidence is not admissible"
        )
    artifacts = tuple(execution.artifacts)
    allowed_prefix = f"runs/{run_id}/model-invocations/{invocation_id}/"
    if not artifacts or any(
        not item.path.startswith(allowed_prefix)
        or item.path.rsplit("/", 1)[-1] not in {"request.json", "record.json", "response.json"}
        for item in artifacts
    ):
        raise ScoutProposalExecutionError(
            "invocation_evidence_invalid", "local invocation artifacts are not scoped"
        )
    refs = [_ref(item.path, item.content) for item in artifacts]

    def operation(writer: JournalWriter) -> JournalEntry:
        for artifact in artifacts:
            path = writer.root / artifact.path
            if path.exists() and path.read_bytes() != artifact.content:
                raise ScoutProposalExecutionError(
                    "invocation_evidence_conflict", "local invocation artifact changed"
                )
            if path.exists():
                raise ScoutProposalExecutionError(
                    "invocation_evidence_conflict", "local invocation artifact is already published"
                )
        return writer.record(
            JournalTransition(
                _new_handoff(uuid_factory),
                "proposal_invocation_recorded",
                f"Proposal invocation {invocation_id} evidence recorded before domain publication.",
                artifacts,
                {
                    "run_id": run_id,
                    "goal_id": goal_id,
                    "invocation_id": invocation_id,
                    "model_target": model_target,
                    "source": "scout-proposal-execution",
                    "actor": {
                        "kind": "gigai",
                        "id": "scout-proposal-execution",
                        "model_target": model_target,
                    },
                    "outcome": "INVOCATION_EVIDENCE",
                    "artifact_refs": refs,
                },
            )
        )

    try:
        return run_with_journal_writer(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            operation=operation,
        )
    except ScoutProposalExecutionError:
        raise
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "invocation_evidence_refused", "local invocation evidence could not be committed"
        ) from exc


def read_proposal_invocation_attempts(
    resolved: ResolvedWorkpad, run_id: str, goal_id: str
) -> tuple[dict[str, object], ...]:
    """Read proposal receipts from one authenticated, pinned journal HEAD.

    A committed receipt is not authority merely because its JSON and record
    IDs have the right shape.  The receipt's closed artifact set is checked
    against the exact bytes published by that receipt, and the record's own
    request/response references must describe those same bytes.  ``HEAD`` is
    captured once so a later writer cannot mix generations while this reader
    is evaluating one result.
    """

    try:
        head = _git(resolved.path, "rev-parse", "HEAD").stdout.strip()
        if not head:
            raise ValueError("journal has no committed HEAD")
        names = _git(
            resolved.path, "ls-tree", "-r", "--name-only", head, "--", "handoffs/"
        ).stdout.splitlines()
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "invocation_evidence_unavailable", "proposal invocation history is unavailable"
        ) from exc
    records: list[dict[str, object]] = []
    seen_invocations: set[str] = set()
    for name in names:
        if not name.endswith(".txt"):
            continue
        try:
            metadata, _body = parse_json_front_matter(
                _git_bytes(resolved.path, "show", f"{head}:{name}")
            )
        except Exception as exc:
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal invocation receipt is unreadable"
            ) from exc
        if (
            metadata.get("transition") != "proposal_invocation_recorded"
            or metadata.get("run_id") != run_id
            or metadata.get("goal_id") != goal_id
        ):
            continue
        invocation_id = metadata.get("invocation_id")
        model_target = metadata.get("model_target")
        actor = metadata.get("actor")
        if (
            not isinstance(invocation_id, str)
            or not isinstance(model_target, str)
            or metadata.get("source") != "scout-proposal-execution"
            or actor
            != {
                "kind": "gigai",
                "id": "scout-proposal-execution",
                "model_target": model_target,
            }
            or invocation_id in seen_invocations
        ):
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal invocation receipt identity is invalid"
            )
        seen_invocations.add(invocation_id)
        receipt_refs = metadata.get("artifact_refs")
        if not isinstance(receipt_refs, list) or not receipt_refs:
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal invocation receipt lacks artifact references"
            )
        normalized_refs: dict[str, dict[str, object]] = {}
        prefix = f"runs/{run_id}/model-invocations/{invocation_id}/"
        for value in receipt_refs:
            if not isinstance(value, Mapping) or set(value) != {
                "path", "content_sha256", "media_type", "size_bytes"
            }:
                raise ScoutProposalExecutionError(
                    "invocation_evidence_invalid", "proposal receipt artifact reference is malformed"
                )
            path = value.get("path")
            if (
                not isinstance(path, str)
                or not path.startswith(prefix)
                or path.rsplit("/", 1)[-1] not in {"request.json", "record.json", "response.json"}
                or path in normalized_refs
                or not isinstance(value.get("content_sha256"), str)
                or type(value.get("size_bytes")) is not int
                or value.get("media_type") != "application/json"
            ):
                raise ScoutProposalExecutionError(
                    "invocation_evidence_invalid", "proposal receipt artifact set is invalid"
                )
            normalized_refs[path] = dict(value)
        record_path = f"{prefix}record.json"
        request_path = f"{prefix}request.json"
        if record_path not in normalized_refs or request_path not in normalized_refs:
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal receipt must include request and record artifacts"
            )
        try:
            receipt_commit = _git(
                resolved.path, "log", "--format=%H", "-1", head, "--", name
            ).stdout.strip()
            if not receipt_commit:
                raise ValueError("receipt publisher is unavailable")
            artifacts: dict[str, bytes] = {}
            for path, reference in normalized_refs.items():
                data, publisher = read_committed_artifact(
                    workpad=resolved.path,
                    project_id=resolved.project_id,
                    gig_id=resolved.gig_id,
                    path=path,
                    head=head,
                )
                if publisher != receipt_commit:
                    raise JournalConflictError("proposal receipt artifact has another publisher")
                if (
                    reference["content_sha256"] != digest_imported_bytes(data)
                    or reference["size_bytes"] != len(data)
                ):
                    raise JournalConflictError("proposal receipt artifact digest differs")
                artifacts[path] = data
            record = parse_json_bytes(artifacts[record_path])
        except Exception as exc:
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal invocation receipt artifacts are unavailable"
            ) from exc
        if (
            not isinstance(record, dict)
            or record.get("run_id") != run_id
            or record.get("goal_id") != goal_id
            or record.get("invocation_id") != invocation_id
            or record.get("configured_selector") != model_target
            or not validate_model_invocation(record).valid
        ):
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal invocation record owner or target binding is invalid"
            )
        request = record.get("request")
        if not isinstance(request, Mapping):
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal invocation request is missing"
            )
        request_ref = request.get("request_artifact")
        if not _same_artifact_ref(request_ref, normalized_refs[request_path]):
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal request artifact reference does not match receipt"
            )
        try:
            request_payload = parse_json_bytes(artifacts[request_path])
        except Exception as exc:
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal request artifact is unreadable"
            ) from exc
        if (
            not isinstance(request_payload, Mapping)
            or request_payload.get("schema_version") != "1.0"
            or request_payload.get("role") != record.get("role")
            or request_payload.get("input_sha256") is None
            or request_payload.get("blocked_reason") is not None
            and record.get("outcome") == "succeeded"
            or request.get("request_sha256") != digest_imported_bytes(artifacts[request_path])
        ):
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal request content does not match its record"
            )
        selected_ids = record.get("request", {}).get("selected_references")
        request_ids = request_payload.get("selected_reference_ids")
        if (
            not isinstance(selected_ids, list)
            or not isinstance(request_ids, list)
            or request_ids != [item.get("reference_id") for item in selected_ids]
        ):
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal request selections do not match its record"
            )
        source_descriptors = request.get("selected_source_descriptors")
        request_source_descriptors = request_payload.get("selected_source_descriptors")
        if source_descriptors is None:
            if request_source_descriptors is not None:
                raise ScoutProposalExecutionError(
                    "invocation_evidence_invalid", "proposal request has unclaimed source descriptors"
                )
        elif request_source_descriptors != source_descriptors:
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal source descriptors do not match request bytes"
            )
        response_ref = _response_artifact(record)
        response_path = f"{prefix}response.json"
        if response_ref is None:
            if response_path in normalized_refs:
                raise ScoutProposalExecutionError(
                    "invocation_evidence_invalid", "proposal receipt has an unclaimed response artifact"
                )
        elif response_path not in normalized_refs or not _same_artifact_ref(
            response_ref, normalized_refs[response_path]
        ):
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal response artifact reference does not match receipt"
            )
        else:
            try:
                response_payload = parse_json_bytes(artifacts[response_path])
            except Exception as exc:
                raise ScoutProposalExecutionError(
                    "invocation_evidence_invalid", "proposal response artifact is unreadable"
                ) from exc
            if (
                not isinstance(response_payload, Mapping)
                or response_payload.get("schema_version") != "1.0"
                or not isinstance(response_payload.get("output_text"), str)
                or response_payload.get("resolved_model") != record.get("resolved_model")
            ):
                raise ScoutProposalExecutionError(
                    "invocation_evidence_invalid", "proposal response content does not match its record"
                )
        records.append(record)
    return tuple(records)


def _require_active_goal(
    resolved: ResolvedWorkpad, run_id: str, goal_id: str
) -> None:
    """Require committed scheduler ownership before local model invocation."""

    def operation(writer: JournalWriter) -> None:
        _validate_active_goal_bytes(
            resolved,
            _committed_run_bytes(writer.root, run_id),
            run_id,
            goal_id,
        )

    try:
        run_with_journal_writer(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            operation=operation,
        )
    except ScoutProposalExecutionError:
        raise
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "execution_authority_refused",
            "proposal Run/Goal is not committed active scheduler authority",
        ) from exc


def _committed_run_bytes(root: Path, run_id: str) -> tuple[bytes, bytes]:
    details_path = f"runs/{run_id}/run-details.json"
    graph_path = f"runs/{run_id}/goal-graph.json"
    try:
        return (
            _git_bytes(root, "show", f"HEAD:{details_path}"),
            _git_bytes(root, "show", f"HEAD:{graph_path}"),
        )
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "execution_authority_refused",
            "proposal Run details or sealed Goal Graph is unavailable",
        ) from exc


def _validate_active_goal_bytes(
    resolved: ResolvedWorkpad,
    run_bytes: tuple[bytes, bytes],
    run_id: str,
    goal_id: str,
) -> None:
    details_bytes, graph_bytes = run_bytes
    if not validate_serialized_contract("run-details.schema.json", details_bytes).valid:
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "proposal Run details are invalid"
        )
    if not validate_serialized_contract("goal-graph.schema.json", graph_bytes).valid:
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "proposal Goal Graph is invalid"
        )
    try:
        details = parse_json_bytes(details_bytes)
        graph = parse_json_bytes(graph_bytes)
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "proposal Run authority is unreadable"
        ) from exc
    if not isinstance(details, Mapping) or not isinstance(graph, Mapping):
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "proposal Run authority is malformed"
        )
    if (
        details.get("run_id") != run_id
        or details.get("gig_id") != resolved.gig_id
        or details.get("status") != "running"
        or details.get("goal_graph_sha256") != digest_imported_bytes(graph_bytes)
    ):
        raise ScoutProposalExecutionError(
            "execution_authority_refused",
            "proposal Run is not an active committed owner",
        )
    if _terminal_goal_handoff_exists(resolved.path, run_id, goal_id):
        raise ScoutProposalExecutionError(
            "execution_authority_refused",
            "requested Goal already has a committed terminal transition",
        )
    semantic = validate_goal_graph(graph)
    if not semantic.valid:
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "proposal Goal Graph is not admitted"
        )
    goals = graph.get("goals")
    goal_details = details.get("goals")
    if not isinstance(goals, list) or not isinstance(goal_details, list):
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "proposal Goal membership is unavailable"
        )
    goal = next((item for item in goals if isinstance(item, Mapping) and item.get("goal_id") == goal_id), None)
    detail = next((item for item in goal_details if isinstance(item, Mapping) and item.get("goal_id") == goal_id), None)
    if not isinstance(goal, Mapping) or not isinstance(detail, Mapping):
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "requested Goal is not a member of the sealed graph"
        )
    if detail.get("status") != "running" or detail.get("outcome") is not None:
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "requested Goal is not eligible for proposal execution"
        )
    effects = goal.get("effects")
    if effects != ["write_workpad"]:
        raise ScoutProposalExecutionError(
            "execution_effect_refused", "proposal Goal does not permit the private workpad effect"
        )


def _terminal_goal_handoff_exists(root: Path, run_id: str, goal_id: str) -> bool:
    """Treat a committed terminal handoff as authoritative over stale details."""

    try:
        names = _git(
            root, "ls-tree", "-r", "--name-only", "HEAD", "--", "handoffs/", check=False
        ).stdout.splitlines()
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "committed Goal terminal history is unavailable"
        ) from exc
    latest_started = -1
    latest_terminal = -1
    for name in sorted(names):
        if not name.startswith("handoffs/") or not name.endswith(".txt"):
            continue
        try:
            metadata, _body = parse_json_front_matter(
                _git_bytes(root, "show", f"HEAD:{name}")
            )
        except Exception as exc:
            raise ScoutProposalExecutionError(
                "execution_authority_refused", "committed Goal terminal history is unreadable"
            ) from exc
        if metadata.get("run_id") != run_id or metadata.get("goal_id") != goal_id:
            continue
        try:
            sequence = int(Path(name).name.split("-", 1)[0])
        except (ValueError, IndexError):
            continue
        transition = metadata.get("transition")
        if transition == "goal_started":
            latest_started = max(latest_started, sequence)
        elif transition in {"goal_completed", "goal_failed", "goal_blocked"}:
            latest_terminal = max(latest_terminal, sequence)
    return latest_terminal > latest_started


def _terminalize_goal_details(
    details: dict[str, object],
    goal_id: str,
    result: Mapping[str, object],
    evidence: list[dict[str, object]],
) -> None:
    """Materialize Goal outcome alongside the terminal handoff artifact."""

    goals = details.get("goals")
    if not isinstance(goals, list):
        raise ScoutProposalExecutionError(
            "execution_publication_refused", "proposal Run details have no Goal state"
        )
    detail = next(
        (item for item in goals if isinstance(item, dict) and item.get("goal_id") == goal_id),
        None,
    )
    if not isinstance(detail, dict):
        raise ScoutProposalExecutionError(
            "execution_publication_refused", "proposal Goal details are unavailable"
        )
    complete = result.get("status") == "complete"
    detail.update(
        {
            "status": "complete" if complete else "failed",
            "outcome": "COMPLETE" if complete else "FAILED",
            "finished_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "evidence": evidence,
            "errors": []
            if complete
            else [
                {
                    "code": "proposal_result_invalid",
                    "message": "local proposal assessment failed bounded validation",
                    "retryable": False,
                    "invocation_id": result.get("invocation_id"),
                }
            ],
        }
    )
    # Only this Goal is terminalized.  A proposal caller does not own whole-Run
    # completion while other sealed Goals remain pending.
    goal_sets = {
        key: []
        for key in (
            "pending",
            "ready",
            "active",
            "complete",
            "failed",
            "blocked",
            "gated",
            "cancelled",
        )
    }
    for item in goals:
        if not isinstance(item, Mapping):
            continue
        state = item.get("status")
        aggregate = "active" if state in {"running", "verifying"} else state
        if aggregate in goal_sets and isinstance(item.get("goal_id"), str):
            goal_sets[aggregate].append(item["goal_id"])
    details["goal_sets"] = goal_sets

def _publish_result(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    goal_id: str,
    model_target: str,
    result: Mapping[str, object],
    result_ref: Mapping[str, object],
    invocation_artifacts: tuple[JournalArtifact, ...],
    uuid_factory: Callable[[], uuid.UUID],
) -> JournalEntry:
    """Publish invocation + domain result in one authorized Goal transition."""

    path = str(result_ref["path"])
    result_bytes = canonical_json_bytes(dict(result))
    transition = "goal_completed" if result.get("status") == "complete" else "goal_failed"

    def operation(writer: JournalWriter) -> JournalEntry:
        run_bytes = _committed_run_bytes(writer.root, run_id)
        _validate_active_goal_bytes(
            resolved,
            run_bytes,
            run_id,
            goal_id,
        )
        try:
            details = parse_json_bytes(run_bytes[0])
            graph = parse_json_bytes(run_bytes[1])
        except Exception as exc:
            raise ScoutProposalExecutionError(
                "execution_publication_refused", "proposal Run state is unreadable"
            ) from exc
        if not isinstance(details, dict) or not isinstance(graph, Mapping):
            raise ScoutProposalExecutionError(
                "execution_publication_refused", "proposal Run state is malformed"
            )
        refs = [
            _ref(item.path, item.content)
            for item in (*invocation_artifacts, JournalArtifact(path, result_bytes))
        ]
        details_ref = _ref(f"runs/{run_id}/run-details.json", b"")
        # Replace the placeholder digest after the materialized details bytes
        # are built; it is included in the same immutable transition.
        _terminalize_goal_details(details, goal_id, result, refs)
        details_bytes = canonical_json_bytes(details)
        details_ref = _ref(f"runs/{run_id}/run-details.json", details_bytes)
        refs.append(details_ref)
        graph_goals = graph.get("goals")
        graph_goal = next(
            (item for item in graph_goals or [] if isinstance(item, Mapping) and item.get("goal_id") == goal_id),
            {},
        )
        # Invocation artifacts were committed by the non-terminal attempt
        # receipt.  Reuse only byte-identical files; the result/details files
        # remain the sole new artifacts in this Goal terminal transition.
        for artifact in (*invocation_artifacts, JournalArtifact(path, result_bytes)):
            if (writer.root / artifact.path).exists():
                if (writer.root / artifact.path).read_bytes() != artifact.content:
                    raise ScoutProposalExecutionError(
                        "execution_publication_refused",
                        "proposal invocation or result artifact changed",
                    )
        return writer.record(
            JournalTransition(
                _new_handoff(uuid_factory),
                transition,
                f"Scout local proposal assessment terminalized as {result.get('status') }.",
                (
                    JournalArtifact(path, result_bytes),
                    JournalArtifact(details_ref["path"], details_bytes),
                ),
                {
                    "run_id": run_id,
                    "goal_id": goal_id,
                    "gig_id": resolved.gig_id,
                    "gig_version": details.get("gig_version"),
                    "goal_version": graph_goal.get("goal_version"),
                    "goal_graph_sha256": digest_imported_bytes(run_bytes[1]),
                    "outcome": "COMPLETE" if transition == "goal_completed" else "FAILED",
                    "actor": {
                        "kind": "gigai",
                        "id": "scout-proposal-execution",
                        "model_target": model_target,
                    },
                    "artifact_refs": refs,
                },
            ),
            allow_artifact_replacement=True,
        )

    try:
        return run_with_journal_writer(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            operation=operation,
        )
    except ScoutProposalExecutionError:
        raise
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "execution_publication_refused", "proposal result could not be published safely"
        ) from exc


def _resolve_sources(
    resolved: ResolvedWorkpad,
    posting_selector: Mapping[str, object],
    private_selectors: tuple[Mapping[str, object], ...],
) -> tuple[ProposalSource, tuple[ProposalSource, ...], str]:
    def operation(
        writer: JournalWriter,
    ) -> tuple[ProposalSource, tuple[ProposalSource, ...], str]:
        posting_value = resolve_discovery_posting_input_from_journal(
            resolved, posting_selector, writer=writer
        )
        snapshot = writer.snapshot(("records/", "references/", "run-inputs/"))
        posting = _posting_source(posting_value)
        private_items: list[ProposalSource] = []
        for index, descriptor in enumerate(private_selectors):
            if set(descriptor) != {"purpose", "selector"}:
                raise ScoutProposalExecutionError(
                    "private_source_purpose_required",
                    "each private source requires an explicit host-owned purpose",
                )
            purpose = descriptor.get("purpose")
            selector = descriptor.get("selector")
            if type(purpose) is not str or purpose not in _PRIVATE_PURPOSES:
                raise ScoutProposalExecutionError(
                    "private_source_purpose_invalid",
                    "private source purpose is not admitted",
                )
            if not isinstance(selector, Mapping):
                raise ScoutProposalExecutionError(
                    "private_source_selector_invalid",
                    "private source selector is invalid",
                )
            resolved_input = resolve_external_input(resolved, snapshot, selector)
            private_items.append(
                _private_source(
                    snapshot,
                    resolved_input,
                    purpose=purpose,
                    handle=f"source_{index + 2}",
                )
            )
        private = tuple(private_items)
        return posting, private, snapshot.head

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=operation,
    )


def _posting_source(value: Mapping[str, object]) -> ProposalSource:
    posting_ref = value.get("posting_ref")
    if not isinstance(posting_ref, Mapping) or not isinstance(
        posting_ref.get("ref"), Mapping
    ):
        raise ScoutProposalExecutionError(
            "posting_source_invalid", "discovery posting capture reference is invalid"
        )
    identity = {
        "family": "scout_discovery_posting",
        "run_id": value.get("run_id"),
        "receipt_id": value.get("receipt_id"),
        "checkpoint_id": value.get("checkpoint_id"),
        "opportunity_id": value.get("opportunity_id"),
        "snapshot_id": value.get("snapshot_id"),
        "run_ref": value.get("run_ref"),
        "receipt_ref": value.get("receipt_ref"),
        "checkpoint_ref": value.get("checkpoint_ref"),
        "posting_ref": posting_ref["ref"],
    }
    content = value.get("posting_bytes")
    if not isinstance(content, bytes):
        raise ScoutProposalExecutionError(
            "posting_source_invalid", "discovery posting bytes are unavailable"
        )
    digest = digest_imported_bytes(content)
    if identity["posting_ref"].get("content_sha256") != digest:
        raise ScoutProposalExecutionError(
            "posting_source_invalid", "discovery posting bytes changed"
        )
    return ProposalSource(
        "source_1", "posting", "scout_discovery_posting", identity, content, digest
    )


def _private_source(
    snapshot: JournalSnapshot,
    value: Mapping[str, object],
    *,
    purpose: str,
    handle: str,
) -> ProposalSource:
    family = value.get("family")
    content_ref: Mapping[str, object] | None = None
    identity: dict[str, object]
    if purpose not in _PRIVATE_PURPOSES:
        raise ScoutProposalExecutionError(
            "private_source_purpose_invalid", "private source purpose is not admitted"
        )
    if family in {"g45_reference", "g45_run_input"}:
        key = "reference_id" if family == "g45_reference" else "run_input_id"
        if not isinstance(value.get(key), str) or not isinstance(
            value.get("snapshot_ref"), Mapping
        ):
            raise ScoutProposalExecutionError(
                "private_source_invalid", "G45 source descriptor is invalid"
            )
        content_ref = value["snapshot_ref"]
        identity = {
            "family": family,
            key: value[key],
            "snapshot_ref": dict(content_ref),
            "content_sha256": value.get("snapshot_ref", {}).get("content_sha256"),
        }
        record_ref = value.get("record_ref")
        if not isinstance(record_ref, Mapping) or not isinstance(record_ref.get("path"), str):
            raise ScoutProposalExecutionError(
                "private_source_invalid", "G45 source record authority is unavailable"
            )
        record_bytes = snapshot.artifacts.get(str(record_ref["path"]))
        if record_bytes is None:
            raise ScoutProposalExecutionError(
                "private_source_invalid", "G45 source record authority is unavailable"
            )
        try:
            record = parse_json_bytes(record_bytes)
        except Exception as exc:
            raise ScoutProposalExecutionError(
                "private_source_invalid", "G45 source record authority is invalid"
            ) from exc
        source_kind = record.get("kind") if isinstance(record, Mapping) else None
        if family == "g45_reference":
            if source_kind not in _REFERENCE_KINDS or purpose != "experience":
                raise ScoutProposalExecutionError(
                    "private_source_purpose_mismatch",
                    "imported reference evidence is admitted only as explicit experience",
                )
        elif source_kind == "job_description":
            raise ScoutProposalExecutionError(
                "private_source_purpose_mismatch",
                "job-description Run input cannot establish an answer purpose",
            )
    elif family == "scout_record":
        content = value.get("content")
        if not isinstance(content, Mapping) or content.get("family") != "jsl_blob":
            raise ScoutProposalExecutionError(
                "private_source_invalid", "native source descriptor is invalid"
            )
        blob = content.get("blob_ref")
        if not isinstance(blob, Mapping):
            raise ScoutProposalExecutionError(
                "private_source_invalid", "native source blob reference is invalid"
            )
        content_ref = blob
        identity = {
            "family": family,
            "record_id": value.get("record_id"),
            "revision_id": value.get("revision_id"),
            "native_kind": value.get("native_kind"),
            # The pure DTO intentionally carries the closed scope selector;
            # the resolver has already authenticated any native override base.
            "scope": {
                "mode": value.get("scope", {}).get("mode"),
                "task_context_id": value.get("scope", {}).get("task_context_id"),
            }
            if isinstance(value.get("scope"), Mapping)
            else value.get("scope"),
            "blob_ref": dict(blob),
        }
        native_kind = value.get("native_kind")
        if (native_kind == "profile_preferences" and purpose != "preferences") or (
            native_kind == "experience_qa" and purpose not in {"experience", "answer"}
        ):
            raise ScoutProposalExecutionError(
                "private_source_purpose_mismatch",
                "native source kind does not match its explicit private purpose",
            )
    else:
        raise ScoutProposalExecutionError(
            "private_source_invalid", "private source family is unsupported"
        )
    content = _read_ref(snapshot, content_ref)
    digest = digest_imported_bytes(content)
    if content_ref.get("content_sha256") != digest:
        raise ScoutProposalExecutionError(
            "private_source_invalid", "private source bytes changed"
        )
    # Handles are prompt-local, not durable identity.
    return ProposalSource(handle, purpose, family, identity, content, digest)  # type: ignore[arg-type]


def _read_ref(snapshot: JournalSnapshot, ref: Mapping[str, object] | None) -> bytes:
    if not isinstance(ref, Mapping):
        raise ScoutProposalExecutionError(
            "source_ref_invalid", "source artifact reference is invalid"
        )
    path = ref.get("path")
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or "\\" in path
        or ".." in Path(path).parts
    ):
        raise ScoutProposalExecutionError(
            "source_ref_invalid", "source artifact path is invalid"
        )
    data = snapshot.artifacts.get(path)
    if (
        data is None
        or ref.get("content_sha256") != digest_imported_bytes(data)
        or ref.get("size_bytes") != len(data)
    ):
        raise ScoutProposalExecutionError(
            "source_ref_invalid", "source artifact is unavailable or changed"
        )
    return data


def _host_result(
    *,
    execution: ModelInvocationExecution,
    request: ScoutProposalRequest,
    sealed_head: str,
) -> dict[str, object]:
    invocation_id = str(execution.record["invocation_id"])
    base: dict[str, object] = {
        "schema_version": "1.0",
        "kind": "scout-proposal-execution",
        "invocation_id": invocation_id,
        "invocation_record_sha256": digest_imported_bytes(
            canonical_json_bytes(execution.record)
        ),
        "request_sha256": execution.record["request"]["request_sha256"],
        "input_lineage": request.lineage(),
        "sealed_journal_head": sealed_head,
        "status": "failed",
        "proposal": None,
        "error": None,
    }
    response_artifact = _response_artifact(execution.record)
    if response_artifact is not None:
        base["response_artifact"] = response_artifact
    if execution.result is None or execution.record.get("outcome") != "succeeded":
        base["error"] = {
            "code": "invocation_not_succeeded",
            "message": "local invocation did not complete successfully",
        }
        return base
    report = validate_proposal_output(execution.result.output_text, request=request)
    if not report.valid:
        base["error"] = {
            "code": "proposal_output_invalid",
            "message": "local assessment failed bounded validation",
        }
        return base
    try:
        proposal = json_load_object(execution.result.output_text)
    except Exception:
        base["error"] = {
            "code": "proposal_output_invalid",
            "message": "local assessment is not a JSON object",
        }
        return base
    base["status"] = "complete"
    base["proposal"] = proposal
    return base


def _response_artifact(record: Mapping[str, object]) -> Mapping[str, object] | None:
    extensions = record.get("extensions")
    if not isinstance(extensions, list):
        return None
    for extension in extensions:
        if (
            not isinstance(extension, Mapping)
            or extension.get("name") != "response_artifact"
        ):
            continue
        value = extension.get("value")
        if isinstance(value, Mapping):
            return dict(value)
    return None


def json_load_object(value: str) -> dict[str, object]:
    import json

    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("proposal output is not an object")
    return decoded


def _ref(path: str, content: bytes) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(content),
        "media_type": "application/json",
        "size_bytes": len(content),
    }


def _same_artifact_ref(left: object, right: Mapping[str, object]) -> bool:
    """Compare the authenticated reference identity shared by v1/v2 records."""

    if not isinstance(left, Mapping):
        return False
    return all(left.get(key) == right.get(key) for key in (
        "path", "content_sha256", "media_type", "size_bytes"
    ))


def _new_handoff(factory: Callable[[], uuid.UUID]) -> str:
    return f"handoff_{factory()}"


__all__ = [
    "ScoutProposalExecution",
    "ScoutProposalExecutionError",
    "execute_local_proposal",
]
