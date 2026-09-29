"""P9b (A3): ``POST /api/resume/extract`` -- stack / seniority / suggested
titles out of one resume, for the setup wizard's first screen.

Body: exactly one of ``resume_text`` (pasted or uploaded text, used for
this call only -- never imported, never journaled, never echoed back),
``profile_id`` (a committed scout profile's pinned resume, read through the
same digest-verifying reader ``POST /api/assess`` uses, ``resume_input``) or
``resume_ref`` (``{"record_id", "revision_id"}``, optionally with
``content_sha256``: a resume stored on this machine that no profile has to
use yet -- the one ``gigai scout resume add`` or ``POST /api/resumes``
stored; its text is read here, from this gig's own records, the way ``POST
/api/profiles`` reads the same ids). Optional ``model_target`` (the sealed
``ModelTarget`` enum) overrides ``find-jobs.json``'s
``default_model_target``.

Privacy is the same for all three: the resume's text goes to the model
target of this call and nowhere else, and is never logged or returned.

Response: ``{"stack": [...], "seniority": "<short label>" | null,
"titles": [...], "extractor": "model", "model_target": "<enum>",
"resolved_target": "<configured target name>", "resume": {"profile_id",
"content_sha256"}}``.  Never the resume text: only what was extracted plus
the resume's digest (``profile_id`` is ``null`` for pasted text and for a
``resume_ref``).  The server log line names the source kind (``profile``,
``pasted`` or ``stored``) and the target, never a byte of the resume.

Model resolution mirrors ``quick_assess._resolve_binding`` (C1/C11): the
adapter kind maps to a configured target through
``proposal_execution._resolve_configured_target_name_for_adapter`` and the
adapter comes from ``proposal_execution.resolve_model_adapter`` looked up as
a MODULE ATTRIBUTE at call time, so ``bindings._patch_test_model_transport``
(the api-e2e fake model) and the unit tests that patch that name intercept
this route too.  The prompt is the same shape ``interview_prep/
categories.py`` uses: JSON only, a fixed schema, one retry on unparsable
output.

Why ``extractor`` is always ``"model"``: 0.1.9 extracts everything through the
configured model (no other extractor exists). The field stays
in the contract so the wizard can say which extractor ran.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Mapping

from ....adapters.factory import AdapterFactoryError
from ....adapters.port import ModelInvocationError
from ....canonical import digest_imported_bytes
from ....config import GigAIConfig, load_config
from ....model_targets import ModelTargetResolutionError
from ....private_records import PrivateRecordError, read_record
from ....workpad import resolve_workpad
from ...quick_assess import _default_model_target, _ObservedBinding, _ObservedPort, _seam_deadline_seconds
from ..assess_contracts import AssessResumeInput, ResolvedResume
from ..contracts import FindJobsContractError, ModelTarget
from ..resume_input import resolve_resume

#: The first line of every extraction prompt.  ``bindings._test_model_handler``
#: keys its additive fixture reply on this exact text (the fake model must
#: recognise an extraction prompt without importing this module), so the two
#: literals must stay identical -- ``test_resume_extract_api.py`` asserts it.
EXTRACT_PROMPT_HEADER = "GigAI Scout resume extraction"

SCHEMA_VERSION = "scout-resume-extract-response:1"
EXTRACTOR_MODEL = "model"

_MAX_RESUME_CHARS = 20_000
_MAX_STACK = 40
_MAX_TITLES = 10
_MAX_ITEM_CHARS = 80
_MAX_SENIORITY_CHARS = 40

_SENIORITY_LEVELS = ("intern", "junior", "mid", "senior", "staff", "principal", "lead", "manager", "director", "unknown")

_ERROR_STATUS: dict[str, HTTPStatus] = {
    "resume_input_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "wrong_type": HTTPStatus.UNPROCESSABLE_ENTITY,
    "unknown_key": HTTPStatus.UNPROCESSABLE_ENTITY,
    "bad_enum": HTTPStatus.UNPROCESSABLE_ENTITY,
    "invalid_value": HTTPStatus.UNPROCESSABLE_ENTITY,
    "profile_not_found": HTTPStatus.NOT_FOUND,
    "profile_unavailable": HTTPStatus.NOT_FOUND,
    "resume_unavailable": HTTPStatus.NOT_FOUND,
    "resume_digest_mismatch": HTTPStatus.NOT_FOUND,
    "target_unavailable": HTTPStatus.NOT_FOUND,
    "model_target_unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
    "model_unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
    "model_denied": HTTPStatus.FORBIDDEN,
    "extract_timeout": HTTPStatus.GATEWAY_TIMEOUT,
    "model_output_invalid": HTTPStatus.BAD_GATEWAY,
}


class ResumeExtractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _status_for(code: str) -> HTTPStatus:
    return _ERROR_STATUS.get(code, HTTPStatus.CONFLICT)


@dataclass(frozen=True)
class StoredResumeRef:
    """A resume stored on this machine, by its record and revision id.

    ``content_sha256`` is optional; when the caller sends it the stored
    text must have that digest (``resume_digest_mismatch`` otherwise).
    """

    record_id: str
    revision_id: str
    content_sha256: str | None = None


# --- request parsing ---------------------------------------------------------------


def _parse_resume_ref(value: object) -> StoredResumeRef:
    if not isinstance(value, Mapping):
        raise ResumeExtractError("wrong_type", "resume_ref must be a JSON object")
    unknown = set(value) - {"record_id", "revision_id", "content_sha256"}
    if unknown:
        raise ResumeExtractError("unknown_key", f"unknown resume_ref field(s): {sorted(unknown)}")
    ids: list[str] = []
    for key in ("record_id", "revision_id"):
        item = value.get(key)
        if not isinstance(item, str) or not item.strip():
            raise ResumeExtractError("resume_input_invalid", f"resume_ref.{key} must be a non-empty string")
        ids.append(item)
    digest = value.get("content_sha256")
    if digest is not None and (not isinstance(digest, str) or not digest):
        raise ResumeExtractError("resume_input_invalid", "resume_ref.content_sha256 must be a non-empty string or null")
    return StoredResumeRef(record_id=ids[0], revision_id=ids[1], content_sha256=digest)


def parse_request(body: object) -> tuple[AssessResumeInput | StoredResumeRef, ModelTarget | None]:
    """``(resume input, model target override)`` from a JSON body.

    Exactly one of ``resume_text`` / ``profile_id`` / ``resume_ref`` is
    required (none is a 422 here, unlike ``/api/assess`` where "neither"
    means the selected profile: the wizard always knows which resume it is
    holding). A ``resume_ref`` comes back as a ``StoredResumeRef``.
    """

    if not isinstance(body, Mapping):
        raise ResumeExtractError("wrong_type", "request body must be a JSON object")
    unknown = set(body) - {"resume_text", "profile_id", "resume_ref", "model_target"}
    if unknown:
        raise ResumeExtractError("unknown_key", f"unknown field(s): {sorted(unknown)}")
    try:
        resume = AssessResumeInput.from_json({key: body[key] for key in ("resume_text", "profile_id") if key in body})
    except FindJobsContractError as exc:
        raise ResumeExtractError(exc.code, str(exc)) from exc
    stored = _parse_resume_ref(body["resume_ref"]) if body.get("resume_ref") is not None else None
    given = (resume.resume_text is not None) + (resume.profile_id is not None) + (stored is not None)
    if given != 1:
        raise ResumeExtractError(
            "resume_input_invalid", "pass exactly one of resume_text, profile_id or resume_ref"
        )
    if resume.resume_text is not None and not resume.resume_text.strip():
        raise ResumeExtractError("resume_input_invalid", "resume_text is empty")
    model_target: ModelTarget | None = None
    if "model_target" in body and body["model_target"] is not None:
        raw = body["model_target"]
        if not isinstance(raw, str) or raw not in {item.value for item in ModelTarget}:
            raise ResumeExtractError("bad_enum", "model_target is not a known model target")
        model_target = ModelTarget(raw)
    return (stored if stored is not None else resume), model_target


# --- a stored resume -------------------------------------------------------------------


def read_stored_resume(ref: StoredResumeRef, *, home_root: Path, target: Path, gig_id: str) -> ResolvedResume:
    """``ref``'s text, read from this gig's own records (no network, no model).

    The same read ``POST /api/profiles`` does for ``resume_record_id`` /
    ``resume_revision_id``, so what can be pinned on a profile can be
    analysed before it is. Raises ``ResumeExtractError``:
    ``resume_unavailable`` when the record or revision is not committed in
    this gig or has no readable text, ``resume_digest_mismatch`` when the
    caller named a digest and the stored text has another.
    """

    try:
        record = read_record(
            home_root=home_root,
            requested_target=target,
            record_id=ref.record_id,
            revision_id=ref.revision_id,
            content=True,
            gig_id=gig_id,
        )
    except PrivateRecordError as exc:
        raise ResumeExtractError("resume_unavailable", "that stored resume is unavailable") from exc
    content = record.get("content")
    if not isinstance(content, bytes) or not content.strip():
        raise ResumeExtractError("resume_unavailable", "that stored resume has no readable text")
    digest = digest_imported_bytes(content)
    if ref.content_sha256 is not None and ref.content_sha256 != digest:
        raise ResumeExtractError("resume_digest_mismatch", "that stored resume is not the one named")
    return ResolvedResume(
        profile_id=None,
        pinned=None,
        content_sha256=digest,
        text=content.decode("utf-8", errors="replace"),
    )


# --- the prompt and its answer -------------------------------------------------------


def render_prompt(resume_text: str) -> str:
    schema = (
        "Return JSON only (no prose, no markdown fences) matching exactly this shape:\n"
        '{"stack": ["<technology, language, framework, platform or tool named in the resume>", ...], '
        '"seniority": "<one of: ' + ", ".join(_SENIORITY_LEVELS) + '>", '
        '"titles": ["<a job title this candidate should search for>", ...]}\n'
        f"stack: at most {_MAX_STACK} distinct items, most prominent first, only things the resume actually names. "
        f"titles: between 1 and {_MAX_TITLES} realistic job titles for the NEXT role, most likely first, "
        "matching the candidate's seniority (for example 'Staff Software Engineer', not 'Engineer')."
    )
    return "\n\n".join(
        [
            EXTRACT_PROMPT_HEADER,
            "You are reading ONE candidate resume and extracting three things for a job search: "
            "the tech stack, the seniority level, and suggested job titles.",
            schema,
            "CANDIDATE RESUME (may be truncated):\n" + resume_text[:_MAX_RESUME_CHARS],
            "Ground every item in the resume text above; never invent a technology or title it does not support.",
        ]
    )


def _extract_json_object(raw: str) -> Mapping[str, object]:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ResumeExtractError("model_output_invalid", "model output contains no JSON object") from None
        try:
            decoded = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            raise ResumeExtractError("model_output_invalid", "model output is not valid JSON") from None
    if not isinstance(decoded, Mapping):
        raise ResumeExtractError("model_output_invalid", "model output is not a JSON object")
    return decoded


def _string_items(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = " ".join(item.split())[:_MAX_ITEM_CHARS].strip()
        if not cleaned or cleaned.casefold() in seen:
            continue
        seen.add(cleaned.casefold())
        items.append(cleaned)
        if len(items) >= limit:
            break
    return items


def parse_extraction(decoded: Mapping[str, object]) -> tuple[list[str], str | None, list[str]]:
    """``(stack, seniority, titles)`` from the model's JSON; raises
    ``model_output_invalid`` when there is neither a stack nor a title."""

    stack = _string_items(decoded.get("stack"), limit=_MAX_STACK)
    titles = _string_items(decoded.get("titles"), limit=_MAX_TITLES)
    raw_seniority = decoded.get("seniority")
    seniority: str | None = None
    if isinstance(raw_seniority, str):
        cleaned = " ".join(raw_seniority.split())[:_MAX_SENIORITY_CHARS].strip().lower()
        seniority = cleaned or None
        if seniority == "unknown":
            seniority = None
    if not stack and not titles:
        raise ResumeExtractError("model_output_invalid", "model output named no stack item and no title")
    return stack, seniority, titles


# --- the model call --------------------------------------------------------------------


def _resolve_binding(config: GigAIConfig, model_target: ModelTarget, *, home_root: Path) -> _ObservedBinding:
    from ... import proposal_execution
    from .. import bindings

    bindings._patch_test_model_transport(config)
    try:
        adapter_target = proposal_execution._resolve_configured_target_name_for_adapter(config, model_target.value)
        binding = proposal_execution.resolve_model_adapter(config, adapter_target, home_root=home_root)
    except (
        AdapterFactoryError,
        ModelTargetResolutionError,
        proposal_execution.ScoutProposalExecutionError,
        KeyError,
        # uat-bug-035: a CLI target whose executable is not on PATH
        # ("claude executable is not available on PATH"; codex the same).
        ModelInvocationError,
    ) as exc:
        raise ResumeExtractError(
            "model_target_unavailable", str(exc) or "the configured model target or credential is unavailable"
        ) from exc
    return _ObservedBinding(binding, _ObservedPort(binding.port, _seam_deadline_seconds()))


def extract_with_model(
    resume_text: str, *, config: GigAIConfig, model_target: ModelTarget, home_root: Path
) -> tuple[list[str], str | None, list[str], str]:
    """Run the extraction prompt; returns ``(stack, seniority, titles, resolved target name)``.

    One retry on unparsable output (the same posture ``assessment_core``
    takes); a transport failure maps to ``model_unavailable`` unless its
    cause chain says timeout (``extract_timeout``) or the policy refused
    the call (``model_denied``).
    """

    binding = _resolve_binding(config, model_target, home_root=home_root)
    prompt = render_prompt(resume_text)
    last_error: ResumeExtractError | None = None
    try:
        for _attempt in range(2):
            try:
                result = binding.port.invoke(binding.request(role="reviewer", prompt=prompt))
            except ModelInvocationError as exc:
                if binding.port.timed_out:
                    raise ResumeExtractError(
                        "extract_timeout", "the model call timed out; try again or pick a faster model target"
                    ) from exc
                if getattr(exc, "code", "") == "model_denied":
                    raise ResumeExtractError("model_denied", "the configured policy refused this model call") from exc
                raise ResumeExtractError("model_unavailable", "the configured model is unavailable right now") from exc
            try:
                stack, seniority, titles = parse_extraction(_extract_json_object(result.output_text))
            except ResumeExtractError as exc:
                last_error = exc
                continue
            return stack, seniority, titles, binding.port.name or model_target.value
    finally:
        binding.close()
    assert last_error is not None
    raise ResumeExtractError(
        "model_output_invalid", f"the model's answer was invalid after one retry: {last_error}"
    )


# --- the route -----------------------------------------------------------------------------


class ResumeExtractRoutesMixin:
    """``Handler`` mixin: ``POST /api/resume/extract``."""

    def _handle_post_resume_extract(self) -> None:
        from .server import _logger

        body = self._read_json_body()
        if body is None:
            return
        try:
            resume_input, override = parse_request(body)
        except ResumeExtractError as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return

        backend = self._backend
        target = getattr(backend, "target", None)
        if target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
            return
        home_root = backend.home_root

        stored_ref = resume_input if isinstance(resume_input, StoredResumeRef) else None
        if stored_ref is None and resume_input.is_ephemeral:
            resolved_gig = None
        else:
            try:
                resolved_gig = resolve_workpad(
                    home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True
                )
            except Exception:  # noqa: BLE001 - no bound gig: a typed 404, never a 500
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "resume_unavailable" if stored_ref is not None else "profile_unavailable",
                    "no Scout gig is available for this folder; run `gigai scout install` or pass resume_text",
                )
                return
        try:
            if stored_ref is not None:
                resume = read_stored_resume(stored_ref, home_root=home_root, target=target, gig_id=resolved_gig.gig_id)
            else:
                resume = resolve_resume(resume_input, resolved=resolved_gig, home_root=home_root, target=target)  # type: ignore[arg-type]  # ephemeral never reads the gig
        except (FindJobsContractError, ResumeExtractError) as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return
        source = "stored" if stored_ref is not None else ("profile" if resume.profile_id else "pasted")

        model_target = override or _default_model_target(target)
        try:
            config = load_config(home_root)
        except Exception as exc:  # noqa: BLE001 - an unreadable home config is "no model", not a crash
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "model_target_unavailable", f"the GigAI config is unavailable: {exc}")
            return
        try:
            stack, seniority, titles, resolved_target = extract_with_model(
                resume.text, config=config, model_target=model_target, home_root=home_root
            )
        except ResumeExtractError as exc:
            _logger.info(
                "resume extraction failed: source=%s target=%s code=%s",
                source,
                model_target.value,
                exc.code,
            )
            self._error(_status_for(exc.code), exc.code, str(exc))
            return

        # Counts and the target only -- never a stack item, title, or resume byte.
        _logger.info(
            "resume extraction: source=%s target=%s resolved=%s stack=%d titles=%d",
            source,
            model_target.value,
            resolved_target,
            len(stack),
            len(titles),
        )
        self._write_json(
            HTTPStatus.OK,
            {
                "schema_version": SCHEMA_VERSION,
                "stack": stack,
                "seniority": seniority,
                "titles": titles,
                "extractor": EXTRACTOR_MODEL,
                "model_target": model_target.value,
                "resolved_target": resolved_target,
                "resume": {"profile_id": resume.profile_id, "content_sha256": resume.content_sha256},
            },
        )


__all__ = [
    "EXTRACTOR_MODEL",
    "EXTRACT_PROMPT_HEADER",
    "ResumeExtractError",
    "ResumeExtractRoutesMixin",
    "SCHEMA_VERSION",
    "StoredResumeRef",
    "extract_with_model",
    "parse_extraction",
    "parse_request",
    "read_stored_resume",
    "render_prompt",
]
