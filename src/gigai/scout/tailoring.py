"""Fixed packaged bridge for the Scout application-tailoring domain.

The external recorder owns the journal and supplies exact selected-input bytes.
This module only authenticates the sealed origin/input tuple, decodes one
deterministic Markdown bundle into the requested documents, and dispatches to
the literal packaged ``.075`` renderer.  It never reads a Gig path, imports a
caller module, or treats request/base64 bytes from an agent as source
authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
from importlib import import_module, resources
import json
import re

from jsonschema import Draft202012Validator, FormatChecker

from ..canonical import CanonicalizationError, canonical_json_bytes, parse_json_bytes


DOMAIN_SCHEMA_ID = "urn:gigai:scout:tailoring-packet:1"
VALIDATOR_ID = "scout-application-tailoring:1"
SCHEMA_RESOURCE = "scout/data/tools/cap_00000000-0000-4000-8000-000000000075/tailoring.schema.json"
VALIDATOR_SOURCE = "gigai.scout.tailoring:validate_tailoring_domain"
FIXED_DOMAIN_RESOURCES = {
    "schema_id": DOMAIN_SCHEMA_ID,
    "schema_resource": SCHEMA_RESOURCE,
    "validator_id": VALIDATOR_ID,
    "validator_source": VALIDATOR_SOURCE,
}
_RENDERER_MODULE = "gigai.scout.data.tools.cap_00000000-0000-4000-8000-000000000075.tailoring"
_DOCUMENTS = frozenset({"resume", "cover_letter"})
_BUNDLE_PREFIX = b"# Scout tailoring bundle\n\n"
_REQUEST_SCHEMA_VERSION = "scout-tailoring-request:1"
_REQUEST_MAX_BYTES = 262144
_REQUEST_MAX_ITEMS = 128
_REQUEST_MAX_REFS = 128
_REQUEST_MAX_OFFSET = 1048576
_REQUEST_REQUIRED = frozenset(
    {"schema_version", "requested_outputs", "source_roles", "requirements", "claim_evidence", "gaps", "questions"}
)
_REQUEST_OPTIONAL = frozenset(
    {"posting_terms", "length_requirements", "contact_requirements", "heading_requirements", "cover_letter_requirements"}
)
_REQUEST_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_REQUEST_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class ScoutTailoringError(RuntimeError):
    """Typed, content-free refusal for the external recording channel."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _refuse(code: str, message: str) -> None:
    raise ScoutTailoringError(code, message)


def _canonical(value: object, *, name: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (CanonicalizationError, TypeError, ValueError) as exc:
        raise ScoutTailoringError("tailoring_domain_invalid", f"{name} is invalid") from exc


@lru_cache(maxsize=1)
def _schema_validator() -> Draft202012Validator:
    try:
        raw = resources.files("gigai").joinpath(*SCHEMA_RESOURCE.split("/")).read_bytes()
        schema = json.loads(raw)
        if not isinstance(schema, dict) or schema.get("$id") != DOMAIN_SCHEMA_ID:
            raise ValueError("unexpected tailoring schema identity")
        Draft202012Validator.check_schema(schema)
    except (AttributeError, FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ScoutTailoringError(
            "tailoring_domain_validator_unavailable", "fixed tailoring schema resource is unavailable"
        ) from exc
    return Draft202012Validator(schema, format_checker=FormatChecker())


@lru_cache(maxsize=1)
def _renderer():
    try:
        module = import_module(_RENDERER_MODULE)
    except (ImportError, AttributeError, ValueError) as exc:
        raise ScoutTailoringError(
            "tailoring_domain_validator_unavailable", "fixed tailoring renderer is unavailable"
        ) from exc
    if not all(hasattr(module, name) for name in ("TailoringPacketError", "validate_tailoring_packet")):
        _refuse("tailoring_domain_validator_unavailable", "fixed tailoring renderer is invalid")
    return module


def encode_tailoring_bundle(documents: Mapping[str, bytes]) -> bytes:
    """Encode requested document bytes as one deterministic Markdown bundle.

    A byte length makes the framing unambiguous even when a document contains
    headings that look like bundle markers.  The document payload itself is
    preserved byte-for-byte and is validated independently by the renderer.
    """
    if not isinstance(documents, Mapping) or not documents or set(documents) - _DOCUMENTS:
        _refuse("tailoring_domain_invalid", "tailoring documents are invalid")
    chunks = [_BUNDLE_PREFIX]
    for kind in ("resume", "cover_letter"):
        if kind not in documents:
            continue
        data = documents[kind]
        if type(data) is not bytes or not data:
            _refuse("tailoring_domain_invalid", "tailoring document bytes are invalid")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ScoutTailoringError("tailoring_domain_invalid", "tailoring document is not UTF-8") from exc
        header = f"## Document: {kind}\n\nByte length: {len(data)}\n\n".encode("ascii")
        chunks.extend((header, data, b"\n\n"))
    return b"".join(chunks)


def _request_options(value: Mapping[str, object]) -> None:
    terms = value.get("posting_terms")
    if terms is not None and (
        not isinstance(terms, list)
        or len(terms) > _REQUEST_MAX_ITEMS
        or any(not isinstance(term, str) or not term.strip() or len(term) > 160 for term in terms)
    ):
        _refuse("tailoring_input_invalid", "tailoring posting terms are invalid")
    specs = {
        "length_requirements": ("int", {"min_words", "max_words", "min_chars", "max_chars"}),
        "contact_requirements": ("text", {"email", "phone", "location"}),
        "cover_letter_requirements": ("text", {"job_title", "company_name"}),
    }
    for name, (kind, allowed) in specs.items():
        option = value.get(name)
        if option is None:
            continue
        if not isinstance(option, Mapping) or set(option) - allowed:
            _refuse("tailoring_input_invalid", f"tailoring {name} are invalid")
        for item in option.values():
            if kind == "int" and (type(item) is not int or item < 0):
                _refuse("tailoring_input_invalid", f"tailoring {name} are invalid")
            if kind == "text" and (not isinstance(item, str) or not item.strip() or len(item) > 300):
                _refuse("tailoring_input_invalid", f"tailoring {name} are invalid")
    headings = value.get("heading_requirements")
    if headings is not None and (
        not isinstance(headings, list)
        or len(headings) > 32
        or any(not isinstance(item, str) or not item.strip() or len(item) > 160 for item in headings)
    ):
        _refuse("tailoring_input_invalid", "tailoring heading requirements are invalid")


def _validate_request_shape(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _refuse("tailoring_input_invalid", "tailoring request is not an object")
    request = dict(value)
    if set(request) - (_REQUEST_REQUIRED | _REQUEST_OPTIONAL) or not _REQUEST_REQUIRED <= set(request):
        _refuse("tailoring_input_invalid", "tailoring request keys are invalid")
    if request["schema_version"] != _REQUEST_SCHEMA_VERSION:
        _refuse("tailoring_input_invalid", "tailoring request version is invalid")
    outputs = request["requested_outputs"]
    if (
        not isinstance(outputs, list)
        or not outputs
        or len(outputs) > 2
        or any(not isinstance(output, str) for output in outputs)
        or len(set(outputs)) != len(outputs)
        or set(outputs) - _DOCUMENTS
    ):
        _refuse("tailoring_input_invalid", "tailoring requested outputs are invalid")
    roles = request["source_roles"]
    if not isinstance(roles, Mapping) or set(roles) != {"posting", "candidate_evidence"}:
        _refuse("tailoring_input_invalid", "tailoring source roles are invalid")
    used_indices: set[int] = set()
    used_ids: set[str] = set()
    for role in ("posting", "candidate_evidence"):
        entries = roles[role]
        if not isinstance(entries, list) or not entries or len(entries) > 32:
            _refuse("tailoring_input_invalid", "tailoring source roles are invalid")
        for entry in entries:
            if not isinstance(entry, Mapping) or set(entry) != {"source_id", "input_index"}:
                _refuse("tailoring_input_invalid", "tailoring source role entry is invalid")
            source_id, input_index = entry["source_id"], entry["input_index"]
            if (
                not isinstance(source_id, str)
                or _REQUEST_ID.fullmatch(source_id) is None
                or type(input_index) is not int
                or input_index < 0
                or input_index in used_indices
                or source_id in used_ids
            ):
                _refuse("tailoring_input_invalid", "tailoring source role entry is invalid")
            used_indices.add(input_index)
            used_ids.add(source_id)
    for name in ("requirements", "claim_evidence", "gaps", "questions"):
        collection = request[name]
        if not isinstance(collection, list) or len(collection) > _REQUEST_MAX_ITEMS:
            _refuse("tailoring_input_invalid", f"tailoring {name} are invalid")
    def span_shape(item: object, name: str) -> tuple[object, ...]:
        if not isinstance(item, Mapping) or set(item) != {"source_id", "start_byte", "end_byte", "quote_sha256"}:
            _refuse("tailoring_input_invalid", f"tailoring {name} is invalid")
        source_id, start, end, digest = item["source_id"], item["start_byte"], item["end_byte"], item["quote_sha256"]
        if (
            not isinstance(source_id, str)
            or _REQUEST_ID.fullmatch(source_id) is None
            or type(start) is not int
            or type(end) is not int
            or start < 0
            or end <= start
            or end > _REQUEST_MAX_OFFSET
            or not isinstance(digest, str)
            or _REQUEST_SHA256.fullmatch(digest) is None
        ):
            _refuse("tailoring_input_invalid", f"tailoring {name} is invalid")
        return (source_id, start, end, digest)

    def span_list(value: object, name: str) -> None:
        if not isinstance(value, list) or len(value) > _REQUEST_MAX_REFS:
            _refuse("tailoring_input_invalid", f"tailoring {name} are invalid")
        seen: set[tuple[object, ...]] = set()
        for item in value:
            identity = span_shape(item, name)
            if identity in seen:
                _refuse("tailoring_input_invalid", f"tailoring {name} contain duplicate references")
            seen.add(identity)

    def draft_shape(item: object) -> None:
        required = {"document_kind", "start_byte", "end_byte", "quote_sha256", "document_sha256", "source_id", "source_start_byte", "source_end_byte", "source_quote_sha256", "source_role"}
        if not isinstance(item, Mapping) or set(item) != required:
            _refuse("tailoring_input_invalid", "tailoring draft evidence is invalid")
        if (
            not isinstance(item["document_kind"], str)
            or item["document_kind"] not in _DOCUMENTS
            or item["source_role"] != "candidate_evidence"
        ):
            _refuse("tailoring_input_invalid", "tailoring draft evidence is invalid")
        span_shape({"source_id": item["source_id"], "start_byte": item["start_byte"], "end_byte": item["end_byte"], "quote_sha256": item["quote_sha256"]}, "tailoring draft evidence")
        span_shape({"source_id": item["source_id"], "start_byte": item["source_start_byte"], "end_byte": item["source_end_byte"], "quote_sha256": item["source_quote_sha256"]}, "tailoring draft source evidence")
        if not isinstance(item["document_sha256"], str) or _REQUEST_SHA256.fullmatch(item["document_sha256"]) is None:
            _refuse("tailoring_input_invalid", "tailoring draft evidence is invalid")

    requirement_ids: set[str] = set()
    for item in request["requirements"]:
        if (
            not isinstance(item, Mapping)
            or set(item) - {"requirement_id", "statement", "posting_ref", "candidate_evidence_refs", "assessment", "draft_evidence_refs"}
            or not {"requirement_id", "statement", "posting_ref", "candidate_evidence_refs", "assessment"} <= set(item)
            or not isinstance(item["requirement_id"], str)
            or _REQUEST_ID.fullmatch(item["requirement_id"]) is None
            or not isinstance(item["statement"], str)
            or not item["statement"].strip()
            or not isinstance(item["posting_ref"], Mapping)
            or not isinstance(item["candidate_evidence_refs"], list)
            or not isinstance(item["assessment"], str)
            or item["assessment"] not in {"supported", "gap", "unknown"}
            or ("draft_evidence_refs" in item and not isinstance(item["draft_evidence_refs"], list))
        ):
            _refuse("tailoring_input_invalid", "tailoring requirements are invalid")
        if item["requirement_id"] in requirement_ids:
            _refuse("tailoring_input_invalid", "tailoring requirement IDs are duplicated")
        requirement_ids.add(item["requirement_id"])
        span_shape(item["posting_ref"], "tailoring posting reference")
        span_list(item["candidate_evidence_refs"], "tailoring candidate evidence references")
        if "draft_evidence_refs" in item:
            drafts = item["draft_evidence_refs"]
            if len(drafts) > _REQUEST_MAX_REFS:
                _refuse("tailoring_input_invalid", "tailoring draft evidence is unbounded")
            for draft in drafts:
                draft_shape(draft)
    claim_ids: set[str] = set()
    for item in request["claim_evidence"]:
        if (
            not isinstance(item, Mapping)
            or set(item) - {"claim_id", "statement", "status", "evidence_refs", "included", "draft_evidence_refs"}
            or not {"claim_id", "statement", "status", "evidence_refs"} <= set(item)
            or not isinstance(item["claim_id"], str)
            or _REQUEST_ID.fullmatch(item["claim_id"]) is None
            or not isinstance(item["statement"], str)
            or not item["statement"].strip()
            or not isinstance(item["status"], str)
            or item["status"] not in {"supported", "unsupported", "conflicted"}
            or not isinstance(item["evidence_refs"], list)
            or ("included" in item and type(item["included"]) is not bool)
            or ("draft_evidence_refs" in item and not isinstance(item["draft_evidence_refs"], list))
        ):
            _refuse("tailoring_input_invalid", "tailoring claim evidence is invalid")
        if item["claim_id"] in claim_ids:
            _refuse("tailoring_input_invalid", "tailoring claim IDs are duplicated")
        claim_ids.add(item["claim_id"])
        span_list(item["evidence_refs"], "tailoring claim evidence references")
        if "draft_evidence_refs" in item:
            drafts = item["draft_evidence_refs"]
            if len(drafts) > _REQUEST_MAX_REFS:
                _refuse("tailoring_input_invalid", "tailoring draft evidence is unbounded")
            for draft in drafts:
                draft_shape(draft)
    item_ids: dict[str, set[str]] = {"gaps": set(), "questions": set()}
    for name, required in (("gaps", {"item_id", "topic", "detail", "source_refs"}), ("questions", {"item_id", "prompt", "reason", "source_refs"})):
        for item in request[name]:
            if (
                not isinstance(item, Mapping)
                or set(item) != required
                or not isinstance(item["item_id"], str)
                or _REQUEST_ID.fullmatch(item["item_id"]) is None
                or any(not isinstance(item[key], str) or not item[key].strip() for key in required - {"source_refs", "item_id"})
                or not isinstance(item["source_refs"], list)
            ):
                _refuse("tailoring_input_invalid", f"tailoring {name} are invalid")
            if item["item_id"] in item_ids[name]:
                _refuse("tailoring_input_invalid", f"tailoring {name} IDs are duplicated")
            item_ids[name].add(item["item_id"])
            span_list(item["source_refs"], f"tailoring {name} source references")
    _request_options(request)
    return request


def validate_tailoring_request(data: bytes) -> dict[str, object]:
    """Validate one canonical, bounded request input before a Run exists."""
    if type(data) is not bytes or not data or len(data) > _REQUEST_MAX_BYTES:
        _refuse("tailoring_input_invalid", "tailoring request bytes exceed the fixed limit")
    try:
        value = parse_json_bytes(data)
    except (CanonicalizationError, TypeError, ValueError) as exc:
        raise ScoutTailoringError("tailoring_input_invalid", "tailoring request bytes are invalid") from exc
    if canonical_json_bytes(value) != data:
        _refuse("tailoring_input_invalid", "tailoring request bytes are not canonical")
    return _validate_request_shape(value)


def decode_tailoring_bundle(markdown: bytes, requested_outputs: Sequence[str]) -> dict[str, bytes]:
    """Decode and strictly validate the composite Markdown framing."""
    if type(markdown) is not bytes or not markdown.startswith(_BUNDLE_PREFIX):
        _refuse("tailoring_domain_invalid", "tailoring Markdown bundle framing is invalid")
    requested = list(requested_outputs)
    if not requested or set(requested) - _DOCUMENTS or len(set(requested)) != len(requested):
        _refuse("tailoring_domain_invalid", "tailoring requested outputs are invalid")
    offset = len(_BUNDLE_PREFIX)
    result: dict[str, bytes] = {}
    while offset < len(markdown):
        marker = markdown.find(b"## Document: ", offset)
        if marker != offset:
            _refuse("tailoring_domain_invalid", "tailoring Markdown bundle contains unexpected bytes")
        line_end = markdown.find(b"\n", marker)
        if line_end < 0:
            _refuse("tailoring_domain_invalid", "tailoring document marker is incomplete")
        kind = markdown[marker + len(b"## Document: "):line_end].decode("ascii", "strict")
        if kind not in _DOCUMENTS or kind in result:
            _refuse("tailoring_domain_invalid", "tailoring document marker is invalid")
        prefix = b"\n\nByte length: "
        if markdown[line_end:line_end + len(prefix)] != prefix:
            _refuse("tailoring_domain_invalid", "tailoring document length marker is invalid")
        length_start = line_end + len(prefix)
        length_end = markdown.find(b"\n", length_start)
        if length_end < 0:
            _refuse("tailoring_domain_invalid", "tailoring document length is incomplete")
        token = markdown[length_start:length_end]
        if (
            not token
            or len(token) > 6
            or any(byte < 48 or byte > 57 for byte in token)
            or (len(token) > 1 and token[:1] == b"0")
        ):
            _refuse("tailoring_domain_invalid", "tailoring document length is not canonical")
        length = int(token.decode("ascii"))
        if length <= 0 or length > 256000 or str(length).encode("ascii") != token:
            _refuse("tailoring_domain_invalid", "tailoring document length is invalid")
        payload_start = length_end + 2
        payload_end = payload_start + length
        if payload_end + 2 > len(markdown) or markdown[payload_end:payload_end + 2] != b"\n\n":
            _refuse("tailoring_domain_invalid", "tailoring document payload framing is invalid")
        result[kind] = markdown[payload_start:payload_end]
        offset = payload_end + 2
    if set(result) != set(requested):
        _refuse("tailoring_domain_invalid", "tailoring bundle does not contain exact requested documents")
    return result


def _request_from_sidecar(value: Mapping[str, object]) -> dict[str, object]:
    """Reconstruct the exact request fields represented by the sidecar."""
    options = value.get("check_options")
    if not isinstance(options, Mapping):
        _refuse("tailoring_domain_invalid", "tailoring check options are invalid")
    result: dict[str, object] = {
        "schema_version": "scout-tailoring-request:1",
        "requested_outputs": value.get("requested_outputs"),
        "source_roles": value.get("source_roles"),
        "requirements": value.get("requirements"),
        "claim_evidence": value.get("claim_evidence"),
        "gaps": value.get("gaps"),
        "questions": value.get("questions"),
    }
    result.update(dict(options))
    return result


def _validate_request_bytes(value: Mapping[str, object], request_bytes: bytes | None) -> None:
    if request_bytes is None:
        _refuse("tailoring_input_missing", "sealed tailoring request input is missing")
    try:
        parsed = parse_json_bytes(request_bytes)
    except ValueError as exc:
        raise ScoutTailoringError("tailoring_input_invalid", "sealed tailoring request input is not JSON") from exc
    expected = _request_from_sidecar(value)
    if _canonical(parsed, name="tailoring request") != _canonical(expected, name="tailoring sidecar request"):
        _refuse("tailoring_input_mismatch", "tailoring request bytes do not match the sealed packet")


def validate_tailoring_domain(
    *,
    value: Mapping[str, object],
    markdown: bytes,
    supporting: Mapping[str, bytes],
    run_id: str,
    project_id: str,
    gig_id: str,
    gig_version: int,
    graph_id: str,
    graph_selector: str,
    graph_version: int,
    selected_inputs: Sequence[Mapping[str, object]],
    source_bytes: Mapping[str, bytes] | None = None,
    request_bytes: bytes | None = None,
) -> None:
    del graph_id, supporting
    if not isinstance(value, Mapping) or type(markdown) is not bytes or not isinstance(source_bytes, Mapping):
        _refuse("tailoring_domain_invalid", "tailoring domain input is invalid")
    domain = dict(value)
    errors = tuple(_schema_validator().iter_errors(domain))
    if errors:
        _refuse("tailoring_domain_invalid", "tailoring domain value violates the fixed schema")
    expected_origin = {
        "project_id": project_id, "gig_id": gig_id, "gig_version": gig_version,
        "graph_selector": graph_selector, "graph_version": graph_version, "run_id": run_id,
    }
    if graph_selector != "tailor-application" or _canonical(domain["origin"], name="tailoring origin") != _canonical(expected_origin, name="trusted origin"):
        _refuse("tailoring_domain_origin_mismatch", "tailoring origin differs from the sealed Run")
    if _canonical(domain["selected_inputs"], name="tailoring selected inputs") != _canonical(selected_inputs, name="trusted selected inputs"):
        _refuse("tailoring_domain_input_mismatch", "tailoring selected inputs differ from the sealed Plan")
    _validate_request_bytes(domain, request_bytes)
    documents = decode_tailoring_bundle(markdown, domain["requested_outputs"])
    renderer = _renderer()
    try:
        renderer.validate_tailoring_packet(markdown=documents, sidecar=domain, source_bytes=dict(source_bytes))
    except renderer.TailoringPacketError as exc:
        code = getattr(exc, "code", "tailoring_domain_invalid")
        raise ScoutTailoringError(code, "fixed tailoring renderer refused evidence") from exc


__all__ = [
    "DOMAIN_SCHEMA_ID", "FIXED_DOMAIN_RESOURCES", "SCHEMA_RESOURCE", "VALIDATOR_ID",
    "VALIDATOR_SOURCE", "ScoutTailoringError", "decode_tailoring_bundle",
    "encode_tailoring_bundle", "validate_tailoring_domain", "validate_tailoring_request",
]
