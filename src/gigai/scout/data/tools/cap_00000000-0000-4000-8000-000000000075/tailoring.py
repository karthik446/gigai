"""Pure candidate renderer for a supplied Scout application packet.

This module is deliberately a renderer/validator boundary only.  It does not
read paths, execute source, call a provider, infer a claim, or publish a Run.
The caller supplies the already-normalized input envelopes, their exact bytes,
and externally reported claim judgments.  The returned Markdown is kept
separate from a strict JSON sidecar which binds every decision to those bytes.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import unescape
import re
from urllib.parse import urlsplit

from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.scout.checks import check_document


_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_PROJECT = re.compile(r"^project_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_GIG = re.compile(r"^gig_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_RUN = re.compile(r"^run_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_GRAPH = re.compile(r"^graph_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_DOCUMENTS = frozenset({"resume", "cover_letter"})
_ROLES = frozenset({"posting", "candidate_evidence"})
_INPUT_FAMILIES = frozenset({"g45_reference", "g45_run_input", "scout_record"})
_MAX_DOCUMENT_BYTES = 256_000
_MAX_SOURCE_BYTES = 1_048_576
_MAX_TOTAL_SOURCE_BYTES = 4 * 1_048_576
_MAX_INPUTS = 32
_MAX_SOURCES = 32
_MAX_ITEMS = 128
_MAX_TEXT = 4_000
_RAW_HTML_TAG = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_RAW_HTML_DECLARATION = re.compile(r"<\s*![^>]*>|<\s*\?[^>]*\??>", re.IGNORECASE | re.DOTALL)
_RAW_HTML_COMMENT = re.compile(r"<\s*!--.*?(?:--\s*>|$)", re.IGNORECASE | re.DOTALL)
_INLINE_LINK = re.compile(r"\]\(\s*(?:<(?P<bracket>[^>\r\n]*)>|(?P<plain>[^\s)]+))", re.IGNORECASE)
_REFERENCE_LINK = re.compile(r"^\s{0,3}\[[^\]\r\n]+\]:\s*(?:<(?P<bracket>[^>\r\n]*)>|(?P<plain>\S+))", re.IGNORECASE | re.MULTILINE)
_AUTOLINK = re.compile(r"<(?P<target>[^>\r\n]+)>")


class TailoringPacketError(ValueError):
    """Typed, redacted refusal for malformed tailoring data."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TailoringPacket:
    """Exact requested document bytes and their canonical sidecar."""

    markdown: dict[str, bytes]
    sidecar: dict[str, object]
    sidecar_bytes: bytes

    @property
    def documents(self) -> dict[str, bytes]:
        """Alias useful to callers that treat the packet as a document map."""

        return self.markdown


def _refuse(code: str, message: str) -> None:
    raise TailoringPacketError(code, message)


def _mapping(value: object, *, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _refuse("tailoring_packet_invalid", f"{name} is invalid")
    return value


def _closed(value: Mapping[str, object], *, name: str, required: set[str], allowed: set[str]) -> None:
    if not required <= set(value) or set(value) - allowed:
        _refuse("tailoring_packet_invalid", f"{name} has invalid fields")


def _text(value: object, *, name: str, limit: int = _MAX_TEXT, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not allow_empty and not value.strip()):
        _refuse("tailoring_packet_invalid", f"{name} is invalid")
    if "\x00" in value or any(ord(char) < 32 and char not in "\n\t" for char in value):
        _refuse("tailoring_packet_invalid", f"{name} is invalid")
    return value


def _id(value: object, *, name: str, pattern: re.Pattern[str] = _ID) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _refuse("tailoring_packet_invalid", f"{name} is invalid")
    return value


def _sha(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _refuse("tailoring_packet_invalid", f"{name} is invalid")
    return value


def _inputs(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _refuse("tailoring_packet_invalid", "selected inputs are invalid")
    if not value or len(value) > _MAX_INPUTS:
        _refuse("tailoring_packet_incomplete", "posting and candidate evidence are required")
    result: list[dict[str, object]] = []
    for item in value:
        raw = _mapping(item, name="selected input")
        family = raw.get("family")
        if family not in _INPUT_FAMILIES:
            _refuse("tailoring_packet_invalid", "selected input family is not admitted")
        # These identity checks deliberately validate the public normalized
        # envelope without rewriting it.  Deeper authentication belongs to
        # scout_inputs and its committed journal snapshot.
        if family == "g45_reference":
            _closed(raw, name="G45 reference", required={"family", "reference_id", "record_ref", "snapshot_ref"}, allowed={"family", "reference_id", "record_ref", "snapshot_ref"})
            _id(raw["reference_id"], name="reference id", pattern=re.compile(r"^ref_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"))
        elif family == "g45_run_input":
            _closed(raw, name="G45 run input", required={"family", "run_input_id", "record_ref", "snapshot_ref"}, allowed={"family", "run_input_id", "record_ref", "snapshot_ref"})
            _id(raw["run_input_id"], name="run input id", pattern=re.compile(r"^input_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"))
        else:
            required = {"family", "record_id", "revision_id", "content"}
            if not required <= set(raw):
                _refuse("tailoring_packet_invalid", "Scout input identity is invalid")
            _id(raw["record_id"], name="record id", pattern=re.compile(r"^record_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"))
            _id(raw["revision_id"], name="revision id", pattern=re.compile(r"^revision_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"))
        result.append(dict(raw))
    return result


def _source_bytes(value: object) -> dict[str, bytes]:
    if not isinstance(value, Mapping) or len(value) > _MAX_SOURCES:
        _refuse("tailoring_packet_invalid", "source bytes are invalid")
    result: dict[str, bytes] = {}
    total = 0
    for key, item in value.items():
        source_id = _id(key, name="source id")
        if type(item) is not bytes or not item or len(item) > _MAX_SOURCE_BYTES:
            _refuse("tailoring_packet_invalid", "source bytes are invalid")
        total += len(item)
        if total > _MAX_TOTAL_SOURCE_BYTES:
            _refuse("tailoring_packet_too_large", "source bytes exceed the bounded packet limit")
        result[source_id] = item
    return result


def _documents(value: object, requested: Sequence[str]) -> dict[str, bytes]:
    if not isinstance(value, Mapping) or set(value) != set(requested):
        _refuse("tailoring_packet_invalid", "requested documents are not exact")
    result: dict[str, bytes] = {}
    for kind in requested:
        data = value.get(kind)
        if type(data) is not bytes or not data or len(data) > _MAX_DOCUMENT_BYTES:
            _refuse("tailoring_packet_invalid", "document bytes are invalid")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            _refuse("tailoring_packet_invalid", "document bytes are not UTF-8")
        if "\x00" in text or any(ord(char) < 32 and char not in "\n\t" for char in text):
            _refuse("tailoring_packet_invalid", "document contains unsafe control text")
        _validate_links(text)
        result[kind] = data
    return result


def _validate_link_target(target: str) -> None:
    # Decode entities before parsing so obfuscated schemes cannot become active
    # in a downstream Markdown renderer.  Controls, credentials, protocol
    # relative URLs, local paths, and every non-http(s) scheme are refused.
    decoded = unescape(target)
    if decoded != unescape(decoded) or any(ord(char) < 32 or ord(char) == 127 for char in decoded):
        _refuse("tailoring_packet_invalid", "document contains an unsafe link")
    candidate = decoded.strip()
    if not candidate or candidate.startswith("//") or "\\" in candidate:
        _refuse("tailoring_packet_invalid", "document contains an unsafe link")
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        _refuse("tailoring_packet_invalid", "document contains an unsafe link")
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        _refuse("tailoring_packet_invalid", "document contains an unsafe link")
    if parsed.username is not None or parsed.password is not None:
        _refuse("tailoring_packet_invalid", "document contains an unsafe link")


def _validate_links(text: str) -> None:
    # Raw HTML is outside this pure byte-preserving candidate.  Markdown
    # autolinks are handled below before rejecting non-URI angle tags.
    for match in _INLINE_LINK.finditer(text):
        _validate_link_target(match.group("bracket") or match.group("plain") or "")
    for match in _REFERENCE_LINK.finditer(text):
        _validate_link_target(match.group("bracket") or match.group("plain") or "")
    safe_autolinks: list[tuple[int, int]] = []
    for match in _AUTOLINK.finditer(text):
        target = match.group("target")
        if re.match(r"https?://", unescape(target).strip(), re.IGNORECASE):
            _validate_link_target(target)
            safe_autolinks.append(match.span())
        elif _RAW_HTML_TAG.fullmatch(match.group(0)):
            _refuse("tailoring_packet_invalid", "document contains unsupported HTML")
    scrubbed = text
    for start, end in reversed(safe_autolinks):
        scrubbed = scrubbed[:start] + scrubbed[end:]
    # Reject declarations, processing instructions, and comments as well as
    # element tags.  Decode only for this conservative classification so
    # entity-obfuscated HTML cannot evade the gate; safe Markdown URLs were
    # already checked above and are removed from the raw-text scan.
    normalized = unescape(scrubbed)
    if (_RAW_HTML_TAG.search(normalized) or _RAW_HTML_DECLARATION.search(normalized)
            or _RAW_HTML_COMMENT.search(normalized)):
        _refuse("tailoring_packet_invalid", "document contains unsupported HTML")


def _source_roles(value: object, *, inputs: list[dict[str, object]], sources: Mapping[str, bytes]) -> dict[str, list[dict[str, object]]]:
    roles = _mapping(value, name="source roles")
    _closed(roles, name="source roles", required=set(_ROLES), allowed=set(_ROLES))
    normalized: dict[str, list[dict[str, object]]] = {}
    used: set[str] = set()
    used_input_indices: set[int] = set()
    for role in ("posting", "candidate_evidence"):
        entries = roles[role]
        if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes, bytearray)) or not entries:
            _refuse("tailoring_packet_incomplete", f"{role} source is required")
        if len(entries) > _MAX_SOURCES:
            _refuse("tailoring_packet_too_large", "source roles are unbounded")
        role_items: list[dict[str, object]] = []
        for item in entries:
            raw = _mapping(item, name="source role")
            _closed(raw, name="source role", required={"source_id", "input_index"}, allowed={"source_id", "input_index"})
            source_id = _id(raw["source_id"], name="source id")
            index = raw["input_index"]
            if type(index) is not int or index < 0 or index >= len(inputs) or source_id not in sources:
                _refuse("tailoring_packet_invalid", "source role reference is invalid")
            if index in used_input_indices:
                _refuse("tailoring_packet_invalid", "an input cannot be both posting and candidate evidence")
            expected = _input_bytes_digest(inputs[index])
            if expected is not None and digest_imported_bytes(sources[source_id]) != expected:
                _refuse("tailoring_packet_artifact_mismatch", "source bytes do not match selected input")
            if source_id in used:
                _refuse("tailoring_packet_invalid", "source is assigned more than once")
            used.add(source_id)
            used_input_indices.add(index)
            role_items.append({"source_id": source_id, "input_index": index})
        normalized[role] = role_items
    if used != set(sources):
        _refuse("tailoring_packet_invalid", "source bytes and source roles do not match")
    if not normalized["posting"]:
        _refuse("tailoring_packet_incomplete", "a posting source is required")
    return normalized


def _input_bytes_digest(value: Mapping[str, object]) -> str | None:
    """Return the authenticated imported/blob digest carried by an envelope."""

    family = value.get("family")
    if family in {"g45_reference", "g45_run_input"}:
        snapshot = value.get("snapshot_ref")
    elif family == "scout_record":
        content = value.get("content")
        if not isinstance(content, Mapping):
            return None
        if content.get("family") in {"g45_reference", "g45_run_input"}:
            snapshot = content.get("snapshot_ref")
        else:
            blob = content.get("blob_ref")
            snapshot = blob if isinstance(blob, Mapping) else None
    else:
        return None
    if not isinstance(snapshot, Mapping):
        return None
    digest = snapshot.get("content_sha256")
    return digest if isinstance(digest, str) and _SHA256.fullmatch(digest) else None


def _span(value: object, *, source_id: str, role: str, sources: Mapping[str, bytes], roles: Mapping[str, list[dict[str, object]]]) -> dict[str, object]:
    raw = _mapping(value, name="evidence span")
    _closed(raw, name="evidence span", required={"source_id", "start_byte", "end_byte", "quote_sha256"}, allowed={"source_id", "start_byte", "end_byte", "quote_sha256"})
    actual_id = _id(raw["source_id"], name="evidence source id")
    if actual_id != source_id or not any(item["source_id"] == actual_id for item in roles.get(role, [])):
        _refuse("tailoring_packet_invalid", "evidence source role is invalid")
    start, end = raw["start_byte"], raw["end_byte"]
    if type(start) is not int or type(end) is not int or start < 0 or end <= start or end > len(sources[actual_id]):
        _refuse("tailoring_packet_invalid", "evidence span is invalid")
    digest = _sha(raw["quote_sha256"], name="evidence quote digest")
    if digest_imported_bytes(sources[actual_id][start:end]) != digest:
        _refuse("tailoring_packet_artifact_mismatch", "evidence bytes do not match")
    return {"source_id": actual_id, "start_byte": start, "end_byte": end, "quote_sha256": digest}


def _draft_spans(
    value: object,
    *,
    document_kind: str,
    document: bytes,
    source_role: str,
    source_spans: Sequence[Mapping[str, object]],
    sources: Mapping[str, bytes],
    roles: Mapping[str, list[dict[str, object]]],
    expected_statement: str | None = None,
) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or not value:
        _refuse("tailoring_packet_incomplete", "included assertion needs draft evidence")
    result: list[dict[str, object]] = []
    for item in value:
        raw = _mapping(item, name="draft evidence")
        _closed(raw, name="draft evidence", required={"document_kind", "start_byte", "end_byte", "quote_sha256", "document_sha256", "source_id", "source_start_byte", "source_end_byte", "source_quote_sha256", "source_role"}, allowed={"document_kind", "start_byte", "end_byte", "quote_sha256", "document_sha256", "source_id", "source_start_byte", "source_end_byte", "source_quote_sha256", "source_role"})
        if raw["document_kind"] != document_kind or raw["source_role"] != source_role:
            _refuse("tailoring_packet_invalid", "draft evidence role is invalid")
        source_id = raw["source_id"]
        if not isinstance(source_id, str) or not any(item.get("source_id") == source_id for item in roles.get(source_role, [])):
            _refuse("tailoring_packet_invalid", "draft evidence source role is invalid")
        source_ref = {"source_id": source_id, "start_byte": raw["source_start_byte"], "end_byte": raw["source_end_byte"], "quote_sha256": raw["source_quote_sha256"]}
        normalized_source = _span(source_ref, source_id=source_id, role=source_role, sources=sources, roles=roles)
        if not any(dict(candidate) == normalized_source for candidate in source_spans):
            _refuse("tailoring_packet_invalid", "draft evidence is not bound to an existing source span")
        start, end = raw["start_byte"], raw["end_byte"]
        if type(start) is not int or type(end) is not int or start < 0 or end <= start or end > len(document):
            _refuse("tailoring_packet_invalid", "draft evidence span is invalid")
        try:
            document[:start].decode("utf-8")
            document[start:end].decode("utf-8")
        except UnicodeDecodeError:
            _refuse("tailoring_packet_invalid", "draft evidence span is not on UTF-8 boundaries")
        draft_bytes = document[start:end]
        if expected_statement is not None:
            # This is an exact UTF-8 relation, not normalization, fuzzy
            # matching, or a factuality judgment. Source wording remains
            # independently caller-reported evidence.
            try:
                draft_text = draft_bytes.decode("utf-8")
            except UnicodeDecodeError:
                _refuse("tailoring_packet_invalid", "draft evidence is not UTF-8")
            if draft_text != expected_statement:
                _refuse("tailoring_packet_artifact_mismatch", "draft evidence does not match claim statement")
        draft_digest = digest_imported_bytes(draft_bytes)
        document_digest = digest_imported_bytes(document)
        if raw["quote_sha256"] != draft_digest or raw["document_sha256"] != document_digest:
            _refuse("tailoring_packet_artifact_mismatch", "draft evidence bytes do not match")
        result.append({"document_kind": document_kind, "start_byte": start, "end_byte": end, "quote_sha256": draft_digest, "document_sha256": document_digest, "source_id": source_id, "source_start_byte": normalized_source["start_byte"], "source_end_byte": normalized_source["end_byte"], "source_quote_sha256": normalized_source["quote_sha256"], "source_role": source_role})
    return result


def _request(value: object, *, sources: Mapping[str, bytes], roles: Mapping[str, list[dict[str, object]]]) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[str]]:
    request = _mapping(value, name="tailoring request")
    required = {"requested_outputs", "source_roles", "requirements", "claim_evidence", "gaps", "questions"}
    allowed = required | {"schema_version", "source_roles", "posting_terms", "length_requirements", "contact_requirements", "heading_requirements", "cover_letter_requirements"}
    _closed(request, name="tailoring request", required=required, allowed=allowed)
    if "schema_version" in request and request["schema_version"] != "scout-tailoring-request:1":
        _refuse("tailoring_packet_invalid", "tailoring request version is invalid")
    output_value = request["requested_outputs"]
    if not isinstance(output_value, Sequence) or isinstance(output_value, (str, bytes, bytearray)) or not output_value or len(output_value) > 2 or set(output_value) - _DOCUMENTS or len(set(output_value)) != len(output_value):
        _refuse("tailoring_packet_invalid", "requested outputs are invalid")
    for collection_name in ("requirements", "claim_evidence", "gaps", "questions"):
        collection = request[collection_name]
        if not isinstance(collection, Sequence) or isinstance(collection, (str, bytes, bytearray)) or len(collection) > _MAX_ITEMS:
            _refuse("tailoring_packet_invalid", f"{collection_name} are invalid")
    requirements: list[dict[str, object]] = []
    requirement_ids: set[str] = set()
    for item in request["requirements"]:  # type: ignore[union-attr]
        raw = _mapping(item, name="requirement")
        _closed(raw, name="requirement", required={"requirement_id", "statement", "posting_ref", "candidate_evidence_refs", "assessment"}, allowed={"requirement_id", "statement", "posting_ref", "candidate_evidence_refs", "assessment", "draft_evidence_refs"})
        rid = _id(raw["requirement_id"], name="requirement id")
        if rid in requirement_ids:
            _refuse("tailoring_packet_invalid", "requirement IDs are duplicated")
        requirement_ids.add(rid)
        if raw["assessment"] not in {"supported", "gap", "unknown"}:
            _refuse("tailoring_packet_invalid", "requirement assessment is invalid")
        posting_ref = _span(raw["posting_ref"], source_id=str(_mapping(raw["posting_ref"], name="posting reference").get("source_id")), role="posting", sources=sources, roles=roles)
        evidence = raw["candidate_evidence_refs"]
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes, bytearray)):
            _refuse("tailoring_packet_invalid", "requirement evidence is invalid")
        normalized_evidence = [_span(ref, source_id=str(_mapping(ref, name="candidate reference").get("source_id")), role="candidate_evidence", sources=sources, roles=roles) for ref in evidence]
        if raw["assessment"] == "supported" and not normalized_evidence:
            _refuse("tailoring_packet_incomplete", "supported requirement needs candidate evidence")
        requirements.append({"requirement_id": rid, "statement": _text(raw["statement"], name="requirement statement"), "posting_ref": posting_ref, "candidate_evidence_refs": normalized_evidence, "assessment": raw["assessment"], "draft_evidence_refs": raw.get("draft_evidence_refs", [])})
    claims: list[dict[str, object]] = []
    claim_ids: set[str] = set()
    for item in request["claim_evidence"]:  # type: ignore[union-attr]
        raw = _mapping(item, name="claim evidence")
        _closed(raw, name="claim evidence", required={"claim_id", "statement", "status", "evidence_refs"}, allowed={"claim_id", "statement", "status", "evidence_refs", "included", "draft_evidence_refs"})
        cid = _id(raw["claim_id"], name="claim id")
        if cid in claim_ids:
            _refuse("tailoring_packet_invalid", "claim IDs are duplicated")
        claim_ids.add(cid)
        status = raw["status"]
        if status not in {"supported", "unsupported", "conflicted"}:
            _refuse("tailoring_packet_invalid", "claim status is invalid")
        evidence = raw["evidence_refs"]
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes, bytearray)):
            _refuse("tailoring_packet_invalid", "claim evidence is invalid")
        refs = [_span(ref, source_id=str(_mapping(ref, name="claim reference").get("source_id")), role="candidate_evidence", sources=sources, roles=roles) for ref in evidence]
        if status == "supported" and not refs:
            _refuse("tailoring_packet_incomplete", "supported claim needs candidate evidence")
        included = status == "supported"
        if "included" in raw and raw["included"] is not included:
            _refuse("tailoring_packet_invalid", "claim inclusion does not match its status")
        claims.append({"claim_id": cid, "statement": _text(raw["statement"], name="claim statement"), "status": status, "evidence_refs": refs, "included": included, "draft_evidence_refs": raw.get("draft_evidence_refs", [])})
    # Caller-provided gaps/questions remain data, but every excluded fact gets
    # a deterministic focused follow-up so unsupported claims cannot become
    # positive candidate facts by omission.
    gaps = _simple_items(request["gaps"], name="gap", prefix="gap")
    questions = _simple_items(request["questions"], name="question", prefix="question")
    _validate_item_refs(gaps, sources=sources, roles=roles)
    _validate_item_refs(questions, sources=sources, roles=roles)
    gap_ids = {str(item["item_id"]) for item in gaps}
    question_ids = {str(item["item_id"]) for item in questions}
    for claim in claims:
        if not claim["included"]:
            cid = str(claim["claim_id"])
            if f"gap-{cid}" not in gap_ids:
                gaps.append({"item_id": f"gap-{cid}", "topic": "candidate evidence", "detail": f"Candidate claim {cid} is not supported by supplied evidence.", "source_refs": list(claim["evidence_refs"])})
                gap_ids.add(f"gap-{cid}")
            if f"question-{cid}" not in question_ids:
                questions.append({"item_id": f"question-{cid}", "prompt": f"Can you provide evidence for candidate claim {cid}?", "reason": "Unsupported or conflicting claims are excluded from the candidate facts.", "source_refs": list(claim["evidence_refs"])})
                question_ids.add(f"question-{cid}")
    terms = request.get("posting_terms", [])
    if not isinstance(terms, Sequence) or isinstance(terms, (str, bytes, bytearray)) or len(terms) > 128 or any(not isinstance(term, str) or not term.strip() or len(term) > 160 for term in terms):
        _refuse("tailoring_packet_invalid", "posting terms are invalid")
    _check_options(request)
    return dict(request), requirements, claims, gaps, questions, [str(term) for term in terms]


def _check_options(request: Mapping[str, object]) -> None:
    option_specs = {
        "length_requirements": {"min_words", "max_words", "min_chars", "max_chars"},
        "contact_requirements": {"email", "phone", "location"},
        "cover_letter_requirements": {"job_title", "company_name"},
    }
    for name, allowed in option_specs.items():
        if name not in request:
            continue
        value = request[name]
        if not isinstance(value, Mapping) or set(value) - allowed:
            _refuse("tailoring_packet_invalid", f"{name} are invalid")
        for key, item in value.items():
            if name == "length_requirements" and (type(item) is not int or item < 0):
                _refuse("tailoring_packet_invalid", f"{name} are invalid")
            if name == "contact_requirements" and (type(item) is not bool and (not isinstance(item, str) or not item.strip() or len(item) > 255)):
                _refuse("tailoring_packet_invalid", f"{name} are invalid")
            if name == "cover_letter_requirements" and (not isinstance(item, str) or not item.strip() or len(item) > 300):
                _refuse("tailoring_packet_invalid", f"{name} are invalid")
    headings = request.get("heading_requirements")
    if headings is not None and (not isinstance(headings, Sequence) or isinstance(headings, (str, bytes, bytearray)) or len(headings) > 32 or any(not isinstance(item, str) or not item.strip() or len(item) > 160 for item in headings)):
        _refuse("tailoring_packet_invalid", "heading requirements are invalid")


def _simple_items(value: object, *, name: str, prefix: str) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _refuse("tailoring_packet_invalid", f"{name}s are invalid")
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in value:
        raw = _mapping(item, name=name)
        if name == "gap":
            _closed(raw, name=name, required={"item_id", "topic", "detail", "source_refs"}, allowed={"item_id", "topic", "detail", "source_refs"})
            normalized = {"item_id": _id(raw["item_id"], name=f"{name} id"), "topic": _text(raw["topic"], name=f"{name} topic"), "detail": _text(raw["detail"], name=f"{name} detail"), "source_refs": raw["source_refs"]}
        else:
            _closed(raw, name=name, required={"item_id", "prompt", "reason", "source_refs"}, allowed={"item_id", "prompt", "reason", "source_refs"})
            normalized = {"item_id": _id(raw["item_id"], name=f"{name} id"), "prompt": _text(raw["prompt"], name=f"{name} prompt"), "reason": _text(raw["reason"], name=f"{name} reason"), "source_refs": raw["source_refs"]}
        if normalized["item_id"] in seen:
            _refuse("tailoring_packet_invalid", f"{name} IDs are duplicated")
        seen.add(str(normalized["item_id"]))
        result.append(normalized)
    return result


def _validate_item_refs(items: Sequence[Mapping[str, object]], *, sources: Mapping[str, bytes], roles: Mapping[str, list[dict[str, object]]]) -> None:
    for item in items:
        refs = item["source_refs"]
        if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes, bytearray)):
            _refuse("tailoring_packet_invalid", "gap/question source references are invalid")
        for ref in refs:
            raw = _mapping(ref, name="gap/question source reference")
            source_id = raw.get("source_id")
            if not isinstance(source_id, str) or source_id not in sources:
                _refuse("tailoring_packet_invalid", "gap/question source reference is invalid")
            role = "posting" if any(entry["source_id"] == source_id for entry in roles["posting"]) else "candidate_evidence"
            _span(raw, source_id=source_id, role=role, sources=sources, roles=roles)


def _source_status(roles: Mapping[str, list[dict[str, object]]], sources: Mapping[str, bytes]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for role in ("posting", "candidate_evidence"):
        for item in roles[role]:
            source_id = str(item["source_id"])
            data = sources[source_id]
            result.append({"source_id": source_id, "role": role, "input_index": item["input_index"], "status": "captured", "content_sha256": digest_imported_bytes(data), "size_bytes": len(data)})
    return result


def _document_checks(documents: Mapping[str, bytes], terms: list[str], request: Mapping[str, object]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for kind, data in documents.items():
        report = check_document(data, kind, posting_terms=terms, length_requirements=request.get("length_requirements"), contact_requirements=request.get("contact_requirements"), heading_requirements=request.get("heading_requirements"), cover_letter_requirements=request.get("cover_letter_requirements"))
        result[kind] = report
    return result


def _bind_draft_evidence(
    requirements: list[dict[str, object]], claims: list[dict[str, object]],
    *, documents: Mapping[str, bytes], sources: Mapping[str, bytes], roles: Mapping[str, list[dict[str, object]]]
) -> None:
    for item, included, spans, expected_statement in [
        *[(item, item["assessment"] == "supported", item["candidate_evidence_refs"], None) for item in requirements],
        *[(item, item["included"] is True, item["evidence_refs"], str(item["statement"])) for item in claims],
    ]:
        raw_drafts = item.get("draft_evidence_refs", [])
        if included:
            if not isinstance(raw_drafts, Sequence) or isinstance(raw_drafts, (str, bytes, bytearray)):
                _refuse("tailoring_packet_incomplete", "included assertion needs draft evidence")
            if not raw_drafts:
                _refuse("tailoring_packet_incomplete", "included assertion needs draft evidence")
            # Each draft ref names its document kind; permit multiple requested
            # document outputs but never silently select one.
            by_kind = {}
            for draft in raw_drafts:
                if isinstance(draft, Mapping) and draft.get("document_kind") in documents:
                    by_kind.setdefault(draft["document_kind"], []).append(draft)
            if not by_kind:
                _refuse("tailoring_packet_invalid", "draft evidence document is invalid")
            normalized: list[dict[str, object]] = []
            for draft_kind, draft_items in by_kind.items():
                normalized.extend(_draft_spans(draft_items, document_kind=draft_kind, document=documents[draft_kind], source_role="candidate_evidence", source_spans=spans, sources=sources, roles=roles, expected_statement=expected_statement))
            item["draft_evidence_refs"] = normalized
        else:
            if raw_drafts:
                _refuse("tailoring_packet_invalid", "excluded assertion cannot carry draft evidence")
            item["draft_evidence_refs"] = []


def _packet_digest(sidecar: Mapping[str, object]) -> str:
    unsigned = dict(sidecar)
    unsigned.pop("packet_sha256", None)
    return digest_imported_bytes(canonical_json_bytes(unsigned))


def build_tailoring_packet(*, project_id: str, gig_id: str, gig_version: int, graph_version: int, run_id: str, selected_inputs: Sequence[Mapping[str, object]], request: Mapping[str, object], documents: Mapping[str, bytes], source_bytes: Mapping[str, bytes] | None = None, artifact_bytes: Mapping[str, bytes] | None = None) -> TailoringPacket:
    """Validate and bind supplied application documents without I/O."""

    _id(project_id, name="project id", pattern=_PROJECT)
    _id(gig_id, name="Gig id", pattern=_GIG)
    _id(run_id, name="Run id", pattern=_RUN)
    if type(gig_version) is not int or gig_version < 1 or type(graph_version) is not int or graph_version < 1:
        _refuse("tailoring_packet_invalid", "version is invalid")
    inputs = _inputs(selected_inputs)
    bytes_map = _source_bytes(source_bytes if source_bytes is not None else artifact_bytes)
    supplied_roles = _source_roles(request.get("source_roles"), inputs=inputs, sources=bytes_map)
    raw_request, requirements, claims, gaps, questions, terms = _request(request, sources=bytes_map, roles=supplied_roles)
    roles = _source_roles(raw_request["source_roles"], inputs=inputs, sources=bytes_map)
    requested = [str(item) for item in raw_request["requested_outputs"]]
    output = _documents(documents, requested)
    _bind_draft_evidence(requirements, claims, documents=output, sources=bytes_map, roles=roles)
    checks = _document_checks(output, terms, raw_request)
    source_artifacts = [{"source_id": source_id, "content_sha256": digest_imported_bytes(data), "size_bytes": len(data), "media_type": "text/plain", "content_base64": base64.b64encode(data).decode("ascii")} for source_id, data in ((key, bytes_map[key]) for key in sorted(bytes_map))]
    sidecar: dict[str, object] = {
        "schema_version": "scout-tailoring-sidecar:1", "output_kind": "tailor_application", "requested_outputs": requested,
        "origin": {"project_id": project_id, "gig_id": gig_id, "gig_version": gig_version, "graph_selector": "tailor-application", "graph_version": graph_version, "run_id": run_id},
        "selected_inputs": inputs, "source_roles": roles, "source_status": _source_status(roles, bytes_map), "source_artifacts": source_artifacts,
        "requirements": requirements, "claim_evidence": claims, "gaps": gaps, "questions": questions,
        "check_options": {key: raw_request[key] for key in ("posting_terms", "length_requirements", "contact_requirements", "heading_requirements", "cover_letter_requirements") if key in raw_request},
        "assessment_limits": ["semantic_factuality_external_agent_reported", "lexical_matches_are_not_semantic_proof", "no_hiring_or_ats_score"],
        "documents": {kind: {"document_sha256": digest_imported_bytes(data), "document_size_bytes": len(data), "checks": checks[kind]} for kind, data in output.items()},
    }
    sidecar["packet_sha256"] = _packet_digest(sidecar)
    return TailoringPacket(markdown=output, sidecar=sidecar, sidecar_bytes=canonical_json_bytes(sidecar))


def validate_tailoring_packet(*, markdown: Mapping[str, bytes], sidecar: Mapping[str, object], source_bytes: Mapping[str, bytes] | None = None, artifact_bytes: Mapping[str, bytes] | None = None) -> None:
    """Rebuild a packet from its sidecar and exact bytes, rejecting stale drafts."""

    value = _mapping(sidecar, name="tailoring sidecar")
    _closed(value, name="tailoring sidecar", required={"schema_version", "output_kind", "requested_outputs", "origin", "selected_inputs", "source_roles", "source_status", "source_artifacts", "requirements", "claim_evidence", "gaps", "questions", "assessment_limits", "documents", "check_options", "packet_sha256"}, allowed={"schema_version", "output_kind", "requested_outputs", "origin", "selected_inputs", "source_roles", "source_status", "source_artifacts", "requirements", "claim_evidence", "gaps", "questions", "assessment_limits", "documents", "check_options", "packet_sha256"})
    if value["schema_version"] != "scout-tailoring-sidecar:1" or value["output_kind"] != "tailor_application" or value["packet_sha256"] != _packet_digest(value):
        _refuse("tailoring_packet_digest_mismatch", "tailoring sidecar digest is invalid")
    origin = _mapping(value["origin"], name="tailoring origin")
    _closed(origin, name="tailoring origin", required={"project_id", "gig_id", "gig_version", "graph_selector", "graph_version", "run_id"}, allowed={"project_id", "gig_id", "gig_version", "graph_selector", "graph_version", "run_id"})
    if origin["graph_selector"] != "tailor-application":
        _refuse("tailoring_packet_invalid", "tailoring origin is invalid")
    documents = _documents(markdown, value["requested_outputs"])  # type: ignore[arg-type]
    source_map = _source_bytes(source_bytes if source_bytes is not None else artifact_bytes)
    artifacts = value["source_artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != len(source_map):
        _refuse("tailoring_packet_invalid", "source artifact inventory is invalid")
    for item in artifacts:
        artifact = _mapping(item, name="source artifact")
        _closed(artifact, name="source artifact", required={"source_id", "content_sha256", "size_bytes", "media_type", "content_base64"}, allowed={"source_id", "content_sha256", "size_bytes", "media_type", "content_base64"})
        source_id = _id(artifact["source_id"], name="source artifact id")
        try:
            decoded = base64.b64decode(str(artifact["content_base64"]), validate=True)
        except (ValueError, TypeError):
            _refuse("tailoring_packet_invalid", "source artifact encoding is invalid")
        if source_map.get(source_id) != decoded or artifact["content_sha256"] != digest_imported_bytes(decoded) or artifact["size_bytes"] != len(decoded):
            _refuse("tailoring_packet_artifact_mismatch", "source artifact changed")
    request: dict[str, object] = {"requested_outputs": value["requested_outputs"], "source_roles": value["source_roles"], "requirements": value["requirements"], "claim_evidence": value["claim_evidence"], "gaps": value["gaps"], "questions": value["questions"], **dict(_mapping(value["check_options"], name="check options"))}
    roles = _source_roles(value["source_roles"], inputs=_inputs(value["selected_inputs"]), sources=source_map)
    _request(request, sources=source_map, roles=roles)
    checks = _document_checks(documents, list(request.get("posting_terms", [])), request)
    stored_documents = _mapping(value["documents"], name="documents")
    if set(stored_documents) != set(documents):
        _refuse("tailoring_packet_invalid", "document inventory is not exact")
    for kind, data in documents.items():
        entry = _mapping(stored_documents[kind], name="document entry")
        _closed(entry, name="document entry", required={"document_sha256", "document_size_bytes", "checks"}, allowed={"document_sha256", "document_size_bytes", "checks"})
        if entry["document_sha256"] != digest_imported_bytes(data) or entry["document_size_bytes"] != len(data) or entry["checks"] != checks[kind]:
            _refuse("tailoring_packet_digest_mismatch", "document bytes do not match checks")
    rebuilt = build_tailoring_packet(
        project_id=str(origin["project_id"]), gig_id=str(origin["gig_id"]),
        gig_version=origin["gig_version"], graph_version=origin["graph_version"],
        run_id=str(origin["run_id"]), selected_inputs=value["selected_inputs"],
        request=request, documents=documents, source_bytes=source_map,
    )
    if rebuilt.sidecar != dict(value):
        _refuse("tailoring_packet_invalid", "tailoring sidecar is not canonical")


validate_rendered_packet = validate_tailoring_packet
render_tailoring_packet = build_tailoring_packet

__all__ = ["TailoringPacket", "TailoringPacketError", "build_tailoring_packet", "render_tailoring_packet", "validate_tailoring_packet", "validate_rendered_packet"]
