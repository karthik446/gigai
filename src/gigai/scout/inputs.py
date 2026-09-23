"""Committed-input resolution shared by the external Scout recording lane.

The resolver deliberately receives the caller's locked journal snapshot.  It
never selects a latest native record or reads a mutable working-copy blob.
"""

from __future__ import annotations

import re
from typing import Mapping

from ..journal import JournalSnapshot, JournalWriter
from ..native_records import _chain as _native_chain
from ..native_records import _sidecar as _native_sidecar
from ..private_records import PrivateRecordError, _content_for_reference
from ..workpad import ResolvedWorkpad


_G45_FAMILIES = frozenset({"g45_reference", "g45_run_input"})
_RESEARCH_FAMILY = "scout_research"
_DISCOVERY_SELECTOR_FAMILY = "scout_discovery"
_DISCOVERY_INPUT_FAMILY = "scout_discovery_posting"
_NATIVE_KINDS = frozenset({"profile_preferences", "experience_qa"})
_TASK_CONTEXT = re.compile(
    r"^task_context_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class ScoutInputError(ValueError):
    """A redacted refusal for an input that cannot be sealed or redeemed."""


def _invalid(message: str) -> ScoutInputError:
    return ScoutInputError(message)


def _record_revision(
    resolved: ResolvedWorkpad,
    snapshot: JournalSnapshot,
    record_id: object,
    revision_id: object,
) -> dict[str, object]:
    if not isinstance(record_id, str) or not isinstance(revision_id, str):
        raise _invalid("native record identity is invalid")
    try:
        chain = _native_chain(resolved, snapshot, record_id)
        revision = next(
            (item for item in chain if item.get("revision_id") == revision_id), None
        )
        if revision is None:
            raise _invalid("selected native revision is unavailable")
    except PrivateRecordError as exc:
        raise _invalid(
            "selected native revision is unavailable or unauthenticated"
        ) from exc
    return revision


def _native_revision(
    resolved: ResolvedWorkpad,
    snapshot: JournalSnapshot,
    record_id: object,
    revision_id: object,
) -> tuple[dict[str, object], dict[str, object]]:
    revision = _record_revision(resolved, snapshot, record_id, revision_id)
    try:
        sidecar, _raw = _native_sidecar(resolved, snapshot, revision)
    except PrivateRecordError as exc:
        raise _invalid(
            "selected native revision is unavailable or unauthenticated"
        ) from exc
    return revision, sidecar


def _supplied_native_scope(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _invalid("native input scope is required")
    scope = dict(value)
    if set(scope) != {"mode", "task_context_id"}:
        raise _invalid("native input scope is invalid")
    if scope == {"mode": "saved_default", "task_context_id": None}:
        return scope
    context = scope.get("task_context_id")
    if (
        scope.get("mode") != "run_override"
        or not isinstance(context, str)
        or _TASK_CONTEXT.fullmatch(context) is None
    ):
        raise _invalid("native input scope is invalid")
    return scope


def _native_scope(
    resolved: ResolvedWorkpad,
    snapshot: JournalSnapshot,
    supplied: object,
    committed: object,
) -> dict[str, object]:
    requested = _supplied_native_scope(supplied)
    if not isinstance(committed, Mapping):
        raise _invalid("committed native scope is invalid")
    full = dict(committed)
    if (
        full.get("mode") != requested["mode"]
        or full.get("task_context_id") != requested["task_context_id"]
    ):
        raise _invalid("native input scope does not match the selected revision")
    if requested["mode"] == "saved_default":
        if full != {"mode": "saved_default", "task_context_id": None, "base": None}:
            raise _invalid("committed saved-default scope is invalid")
        return full
    base = full.get("base")
    if not isinstance(base, Mapping) or set(base) != {"record_id", "revision_id"}:
        raise _invalid("committed native override base is invalid")
    # The base remains a real historical native revision, not merely IDs copied
    # into an override sidecar.  It may itself later be archived.
    _native_revision(resolved, snapshot, base.get("record_id"), base.get("revision_id"))
    return full


def _resolve_g45(
    resolved: ResolvedWorkpad, snapshot: JournalSnapshot, family: str, item_id: object
) -> dict[str, object]:
    if family not in _G45_FAMILIES or not isinstance(item_id, str):
        raise _invalid("G45 input reference is invalid")
    try:
        content, _data = _content_for_reference(
            resolved, family, item_id, snapshot=snapshot
        )
    except PrivateRecordError as exc:
        raise _invalid("selected G45 input is unavailable or unauthenticated") from exc
    return {"family": family, **content}


def resolve_external_input(
    resolved: ResolvedWorkpad, snapshot: JournalSnapshot, raw: object,
    *, allow_role_request: bool = False, allow_research_run: bool = False,
    allow_discovery_posting: bool = False,
    writer: JournalWriter | None = None,
) -> dict[str, object]:
    """Resolve one strict caller selection to immutable journal authority."""
    if not isinstance(raw, Mapping):
        raise _invalid("input reference is invalid")
    item = dict(raw)
    family = item.get("family")
    if not isinstance(family, str):
        raise _invalid("input family is invalid")
    if family == _RESEARCH_FAMILY:
        if not allow_research_run:
            raise _invalid("research Run inputs require external Plan version 2")
        if writer is None:
            raise _invalid("research Run input requires a caller-held journal writer")
        try:
            from .research_inputs import resolve_research_input_from_journal
            return resolve_research_input_from_journal(resolved, item, writer=writer)
        except Exception as exc:
            if isinstance(exc, ScoutInputError):
                raise
            raise _invalid("selected research Run is unavailable or unauthenticated") from exc
    if family == _DISCOVERY_SELECTOR_FAMILY:
        if not allow_discovery_posting:
            raise _invalid("discovery posting inputs require external Plan version 2")
        if writer is None:
            raise _invalid("discovery posting input requires a caller-held journal writer")
        try:
            from .posting_inputs import resolve_discovery_posting_input_from_journal
            resolved_input = resolve_discovery_posting_input_from_journal(
                resolved, item, writer=writer
            )
        except Exception as exc:
            if isinstance(exc, ScoutInputError):
                raise
            raise _invalid("selected discovery posting is unavailable or unauthenticated") from exc
        resolved_input.pop("posting_bytes", None)
        resolved_input.pop("posting", None)
        return resolved_input
    if family == "role_request":
        if not allow_role_request:
            raise _invalid("role requests require external Plan version 2")
        return validate_role_request(item)
    if family in _G45_FAMILIES:
        if set(item) != {"family", "id"}:
            raise _invalid("G45 input reference is invalid")
        return _resolve_g45(resolved, snapshot, str(family), item.get("id"))
    if family != "scout_record" or not {
        "family",
        "record_id",
        "revision_id",
    } <= set(item):
        raise _invalid("Scout record input reference is invalid")

    revision = _record_revision(
        resolved, snapshot, item.get("record_id"), item.get("revision_id")
    )
    content = revision.get("content")
    if not isinstance(content, Mapping):
        raise _invalid("Scout record content is invalid")
    content_value = dict(content)
    content_family = content_value.get("family")
    if content_family in _G45_FAMILIES:
        # Existing Scout wrappers over G45 remain exactly their prior unscoped
        # wire shape; task context is native-content-only data.
        if set(item) != {"family", "record_id", "revision_id"}:
            raise _invalid("G45 Scout wrapper must not carry native scope")
        content_id = content_value.get(
            "reference_id" if content_family == "g45_reference" else "run_input_id"
        )
        if (
            _resolve_g45(resolved, snapshot, str(content_family), content_id)
            != content_value
        ):
            raise _invalid("Scout wrapper content changed or is unavailable")
        return {
            "family": "scout_record",
            "record_id": revision["record_id"],
            "revision_id": revision["revision_id"],
            "content": content_value,
        }
    if content_family != "jsl_blob" or set(item) != {
        "family",
        "record_id",
        "revision_id",
        "scope",
    }:
        raise _invalid("native Scout input reference is invalid")
    try:
        sidecar, _raw = _native_sidecar(resolved, snapshot, revision)
    except PrivateRecordError as exc:
        raise _invalid(
            "selected native revision is unavailable or unauthenticated"
        ) from exc
    native_kind = revision.get("kind")
    if native_kind not in _NATIVE_KINDS or sidecar.get("kind") != native_kind:
        raise _invalid("native record kind is not admitted to external recording")
    full_scope = _native_scope(
        resolved, snapshot, item.get("scope"), sidecar.get("scope")
    )
    return {
        "family": "scout_record",
        "record_id": revision["record_id"],
        "revision_id": revision["revision_id"],
        "native_kind": native_kind,
        "scope": full_scope,
        "content": content_value,
    }


def revalidate_external_input(
    resolved: ResolvedWorkpad, snapshot: JournalSnapshot, sealed: object,
    *, allow_role_request: bool = False, allow_research_run: bool = False,
    allow_discovery_posting: bool = False,
    writer: JournalWriter | None = None,
) -> None:
    """Re-resolve sealed input identity from the same committed snapshot."""
    if not isinstance(sealed, Mapping):
        raise _invalid("sealed input is invalid")
    value = dict(sealed)
    family = value.get("family")
    if not isinstance(family, str):
        raise _invalid("sealed input family is invalid")
    if family == _RESEARCH_FAMILY:
        if not allow_research_run or writer is None:
            raise _invalid("research Run inputs require external Plan version 2 and a caller-held writer")
        try:
            from .research_inputs import resolve_research_input_from_journal
            selector = {key: value.get(key) for key in ("family", "run_id", "receipt_id", "output_kind")}
            current = resolve_research_input_from_journal(resolved, selector, writer=writer)
        except Exception as exc:
            if isinstance(exc, ScoutInputError):
                raise
            raise _invalid("selected research Run changed or is unavailable") from exc
        if current != value:
            raise _invalid("selected research Run changed or is unavailable")
        return
    if family == _DISCOVERY_INPUT_FAMILY:
        if not allow_discovery_posting or writer is None:
            raise _invalid("discovery posting inputs require external Plan version 2 and a caller-held writer")
        try:
            from .posting_inputs import resolve_discovery_posting_input_from_journal
            selector = {
                key: value.get(key)
                for key in (
                    "family", "run_id", "receipt_id", "output_kind",
                    "opportunity_id", "snapshot_id",
                )
            }
            selector["family"] = _DISCOVERY_SELECTOR_FAMILY
            current = resolve_discovery_posting_input_from_journal(
                resolved, selector, writer=writer
            )
            current.pop("posting_bytes", None)
            current.pop("posting", None)
        except Exception as exc:
            if isinstance(exc, ScoutInputError):
                raise
            raise _invalid("selected discovery posting changed or is unavailable") from exc
        if current != value:
            raise _invalid("selected discovery posting changed or is unavailable")
        return
    if family == "role_request":
        if not allow_role_request:
            raise _invalid("role requests require external Plan version 2")
        # Authority is the enclosing committed Plan, not a fabricated record.
        current = validate_role_request(value)
    elif family in _G45_FAMILIES:
        item_id = value.get(
            "reference_id" if family == "g45_reference" else "run_input_id"
        )
        current = _resolve_g45(resolved, snapshot, str(family), item_id)
    elif family == "scout_record":
        raw = {
            "family": "scout_record",
            "record_id": value.get("record_id"),
            "revision_id": value.get("revision_id"),
        }
        if value.get("content") and isinstance(value.get("content"), Mapping):
            if value["content"].get("family") == "jsl_blob":
                scope = value.get("scope")
                if not isinstance(scope, Mapping):
                    raise _invalid("sealed native input scope is invalid")
                raw["scope"] = {
                    "mode": scope.get("mode"),
                    "task_context_id": scope.get("task_context_id"),
                }
        current = resolve_external_input(resolved, snapshot, raw)
    else:
        raise _invalid("sealed input family is invalid")
    if current != value:
        raise _invalid("sealed input changed or is unavailable")


def validate_role_request(raw: object) -> dict[str, object]:
    """Validate inert role text without normalization or manufactured provenance."""
    if not isinstance(raw, Mapping) or set(raw) != {
        "family", "role_title", "role_context"
    }:
        raise _invalid("role request is invalid")
    title, context = raw["role_title"], raw["role_context"]
    if (
        raw["family"] != "role_request"
        or not isinstance(title, str)
        or not title.strip()
        or len(title) > 300
        or (context is not None and (
            not isinstance(context, str) or not context.strip() or len(context) > 4096
        ))
    ):
        raise _invalid("role request is invalid")
    return dict(raw)


def assert_single_override_context(inputs: list[dict[str, object]]) -> None:
    contexts = {
        item["scope"]["task_context_id"]
        for item in inputs
        if item.get("family") == "scout_record"
        and isinstance(item.get("scope"), Mapping)
        and item["scope"].get("mode") == "run_override"
    }
    if len(contexts) > 1:
        raise _invalid("native override inputs span multiple task contexts")


__all__ = [
    "ScoutInputError",
    "assert_single_override_context",
    "resolve_external_input",
    "revalidate_external_input",
    "validate_role_request",
]
