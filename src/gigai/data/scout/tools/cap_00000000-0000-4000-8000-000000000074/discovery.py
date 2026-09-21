"""Pure candidate renderer for externally supplied Scout job discovery.

This candidate has no workpad, provider, filesystem, journal, or approval
authority.  A later fixed bridge must authenticate its selected input and
persist its returned bytes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import html
import re
from urllib.parse import urlsplit

from gigai.canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from gigai.validators import validate_serialized_contract


_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_PROJECT = re.compile(r"^project_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_GIG = re.compile(r"^gig_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_GRAPH = re.compile(r"^graph_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_RUN = re.compile(r"^run_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_RECORD = re.compile(r"^record_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_REVISION = re.compile(r"^revision_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_TASK = re.compile(r"^task_context_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ARTIFACT = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_REFERENCE = re.compile(r"^ref_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_RUN_INPUT = re.compile(r"^input_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_PREFERENCE_FIELDS = frozenset({"geography", "work_mode", "seniority", "employment_type", "compensation"})
_POSTING_FIELDS = _PREFERENCE_FIELDS | {"employer_sponsorship", "eligibility"}


class DiscoveryPacketError(ValueError):
    """Typed, content-free refusal for a discovery candidate packet."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DiscoveryPacket:
    markdown: bytes
    sidecar: dict[str, object]
    sidecar_bytes: bytes


def _refuse(code: str, message: str) -> None:
    raise DiscoveryPacketError(code, message)


def _mapping(value: object, *, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _refuse("discovery_packet_invalid", f"{name} is invalid")
    return value


def _closed(value: Mapping[str, object], *, name: str, required: set[str]) -> None:
    if set(value) != required:
        _refuse("discovery_packet_invalid", f"{name} has invalid fields")


def _text(value: object, *, name: str, limit: int = 4000, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        _refuse("discovery_packet_invalid", f"{name} is invalid")
    if "\x00" in value or any(ord(char) < 32 and char not in "\n\t" for char in value):
        _refuse("discovery_packet_invalid", f"{name} is invalid")
    return value


def _id(value: object, *, name: str, pattern: re.Pattern[str] = _ID) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _refuse("discovery_packet_invalid", f"{name} is invalid")
    return value


def _date(value: object, *, name: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or _DATE.fullmatch(value) is None:
        _refuse("discovery_packet_invalid", f"{name} is invalid")
    try:
        date.fromisoformat(value)
    except ValueError:
        _refuse("discovery_packet_invalid", f"{name} is invalid")
    return value


def _artifact_ref(value: object, *, name: str, supplied: Mapping[str, bytes], required: bool) -> dict[str, object] | None:
    if value is None:
        if required:
            _refuse("discovery_packet_incomplete", f"{name} is required")
        return None
    item = _mapping(value, name=name)
    _closed(item, name=name, required={"artifact_id", "content_sha256", "size_bytes"})
    artifact_id = _id(item["artifact_id"], name=f"{name} artifact", pattern=_ARTIFACT)
    digest, size = item["content_sha256"], item["size_bytes"]
    if not isinstance(digest, str) or _SHA.fullmatch(digest) is None or type(size) is not int or size < 0:
        _refuse("discovery_packet_invalid", f"{name} is invalid")
    content = supplied.get(artifact_id)
    if type(content) is not bytes:
        _refuse("discovery_packet_incomplete", f"{name} bytes are required")
    if len(content) != size or digest_imported_bytes(content) != digest:
        _refuse("discovery_packet_artifact_mismatch", f"{name} bytes do not match")
    return {"artifact_id": artifact_id, "content_sha256": digest, "size_bytes": size}


def _locator(value: object) -> str:
    locator = _text(value, name="posting locator", limit=2048)
    assert locator is not None
    try:
        parsed = urlsplit(locator)
        _ = parsed.port
    except ValueError:
        _refuse("discovery_packet_invalid", "posting locator is unsafe")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        _refuse("discovery_packet_invalid", "posting locator is unsafe")
    if any(part in {".", ".."} for part in parsed.path.split("/")):
        _refuse("discovery_packet_invalid", "posting locator is unsafe")
    return locator


def _normalized_locator(locator: str) -> str:
    parsed = urlsplit(locator)
    host = parsed.hostname.lower() if parsed.hostname else ""
    port = f":{parsed.port}" if parsed.port and not ((parsed.scheme == "https" and parsed.port == 443) or (parsed.scheme == "http" and parsed.port == 80)) else ""
    path = parsed.path or "/"
    return f"{parsed.scheme.lower()}://{host}{port}{path}" + (f"?{parsed.query}" if parsed.query else "")


def _input_ref(value: object) -> dict[str, object]:
    item = _mapping(value, name="preference blob ref")
    _closed(item, name="preference blob ref", required={"path", "content_sha256", "media_type", "size_bytes"})
    path, digest, media_type, size = item["path"], item["content_sha256"], item["media_type"], item["size_bytes"]
    if (
        not isinstance(path, str) or not path.startswith("records/") or len(path) > 512
        or "\\" in path or any(part in {"", ".", ".."} for part in path.split("/"))
        or not isinstance(digest, str) or _SHA.fullmatch(digest) is None
        or not isinstance(media_type, str) or not media_type or len(media_type) > 120
        or type(size) is not int or size < 0
    ):
        _refuse("discovery_packet_invalid", "preference blob ref is invalid")
    return {"path": path, "content_sha256": digest, "media_type": media_type, "size_bytes": size}


def _sealed_ref(value: object, *, name: str) -> None:
    item = _mapping(value, name=name)
    _closed(item, name=name, required={"path", "content_sha256", "media_type", "size_bytes"})
    path, digest, media_type, size = item["path"], item["content_sha256"], item["media_type"], item["size_bytes"]
    if (
        not isinstance(path, str)
        or len(path) > 512
        or not re.fullmatch(r"(?:references|run-inputs|records|docs)/[A-Za-z0-9._/-]{1,512}", path)
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or not isinstance(digest, str)
        or _SHA.fullmatch(digest) is None
        or not isinstance(media_type, str)
        or not media_type
        or len(media_type) > 120
        or type(size) is not int
        or size < 0
    ):
        _refuse("discovery_packet_invalid", f"{name} is invalid")


def _scope(value: object) -> dict[str, object]:
    item = _mapping(value, name="preference scope")
    _closed(item, name="preference scope", required={"mode", "task_context_id", "base"})
    if item == {"mode": "saved_default", "task_context_id": None, "base": None}:
        return dict(item)
    base = _mapping(item["base"], name="preference scope base")
    _closed(base, name="preference scope base", required={"record_id", "revision_id"})
    if item["mode"] != "run_override":
        _refuse("discovery_packet_invalid", "preference scope is invalid")
    return {"mode": "run_override", "task_context_id": _id(item["task_context_id"], name="preference task context", pattern=_TASK), "base": {"record_id": _id(base["record_id"], name="preference base record", pattern=_RECORD), "revision_id": _id(base["revision_id"], name="preference base revision", pattern=_REVISION)}}


def _profile_input(value: object, *, preference_bytes: Mapping[str, bytes]) -> tuple[dict[str, object], dict[str, object]]:
    item = _mapping(value, name="selected profile input")
    _closed(item, name="selected profile input", required={"family", "record_id", "revision_id", "native_kind", "scope", "content"})
    content = _mapping(item["content"], name="selected profile content")
    _closed(content, name="selected profile content", required={"family", "blob_ref", "content_sha256"})
    blob_ref = _input_ref(content["blob_ref"])
    digest = content["content_sha256"]
    if item["family"] != "scout_record" or item["native_kind"] != "profile_preferences" or content["family"] != "jsl_blob" or digest != blob_ref["content_sha256"]:
        _refuse("discovery_packet_invalid", "selected profile input is invalid")
    blob = preference_bytes.get(str(blob_ref["path"]))
    if type(blob) is not bytes:
        _refuse("discovery_packet_incomplete", "selected profile bytes are required")
    if len(blob) != blob_ref["size_bytes"] or digest_imported_bytes(blob) != digest:
        _refuse("discovery_packet_artifact_mismatch", "selected profile bytes do not match")
    report = validate_serialized_contract("native-record-content.schema.json", blob)
    if not report.valid:
        _refuse("discovery_packet_invalid", "selected profile content is invalid")
    try:
        native = parse_json_bytes(blob)
    except ValueError as exc:
        raise DiscoveryPacketError("discovery_packet_invalid", "selected profile content is invalid") from exc
    if not isinstance(native, Mapping) or native.get("kind") != "profile_preferences" or native.get("scope") != item["scope"]:
        _refuse("discovery_packet_invalid", "selected profile content does not match its sealed input")
    payload = native.get("payload")
    if not isinstance(payload, Mapping):
        _refuse("discovery_packet_invalid", "selected profile payload is invalid")
    normalized = {"family": "scout_record", "record_id": _id(item["record_id"], name="selected profile record", pattern=_RECORD), "revision_id": _id(item["revision_id"], name="selected profile revision", pattern=_REVISION), "native_kind": "profile_preferences", "scope": _scope(item["scope"]), "content": {"family": "jsl_blob", "blob_ref": blob_ref, "content_sha256": digest}}
    return normalized, dict(payload)


def _g45_input(value: Mapping[str, object], *, name: str) -> None:
    family = value.get("family")
    if family == "g45_reference":
        _closed(value, name=name, required={"family", "reference_id", "record_ref", "snapshot_ref"})
        _id(value["reference_id"], name=f"{name} reference", pattern=_REFERENCE)
    elif family == "g45_run_input":
        _closed(value, name=name, required={"family", "run_input_id", "record_ref", "snapshot_ref"})
        _id(value["run_input_id"], name=f"{name} run input", pattern=_RUN_INPUT)
    else:
        _refuse("discovery_packet_invalid", f"{name} family is invalid")
    _sealed_ref(value["record_ref"], name=f"{name} record ref")
    _sealed_ref(value["snapshot_ref"], name=f"{name} snapshot ref")


def _optional_input(value: object) -> dict[str, object]:
    """Validate but never interpret an optional sealed external input."""
    item = _mapping(value, name="optional selected input")
    family = item.get("family")
    if family in {"g45_reference", "g45_run_input"}:
        _g45_input(item, name="optional G45 input")
    elif family == "scout_record":
        if item.get("native_kind") == "experience_qa":
            _closed(item, name="optional experience input", required={"family", "record_id", "revision_id", "native_kind", "scope", "content"})
            _id(item["record_id"], name="optional experience record", pattern=_RECORD)
            _id(item["revision_id"], name="optional experience revision", pattern=_REVISION)
            _scope(item["scope"])
            content = _mapping(item["content"], name="optional experience content")
            _closed(content, name="optional experience content", required={"family", "blob_ref", "content_sha256"})
            if content["family"] != "jsl_blob" or content["content_sha256"] != _mapping(content["blob_ref"], name="optional experience blob ref").get("content_sha256"):
                _refuse("discovery_packet_invalid", "optional experience content is invalid")
            _input_ref(content["blob_ref"])
        else:
            _closed(item, name="optional Scout G45 wrapper", required={"family", "record_id", "revision_id", "content"})
            _id(item["record_id"], name="optional wrapper record", pattern=_RECORD)
            _id(item["revision_id"], name="optional wrapper revision", pattern=_REVISION)
            _g45_input(_mapping(item["content"], name="optional wrapper content"), name="optional wrapper content")
    else:
        _refuse("discovery_packet_invalid", "optional selected input family is invalid")
    return dict(item)


def _selected_inputs(
    values: Sequence[Mapping[str, object]], *, preference_bytes: Mapping[str, bytes]
) -> tuple[list[dict[str, object]], dict[str, object]]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
        _refuse("discovery_packet_invalid", "selected discovery inputs are invalid")
    profile: tuple[dict[str, object], dict[str, object]] | None = None
    sealed: list[dict[str, object]] = []
    for raw in values:
        item = _mapping(raw, name="selected discovery input")
        if item.get("family") == "scout_record" and item.get("native_kind") == "profile_preferences":
            if profile is not None:
                _refuse("discovery_packet_invalid", "duplicate profile preferences input is invalid")
            profile = _profile_input(item, preference_bytes=preference_bytes)
            sealed.append(profile[0])
        else:
            sealed.append(_optional_input(item))
    if profile is None:
        _refuse("discovery_packet_incomplete", "profile preferences input is required")
    if set(preference_bytes) != {profile[0]["content"]["blob_ref"]["path"]}:
        _refuse("discovery_packet_invalid", "unused preference bytes are invalid")
    return sealed, profile[1]


def _fact(value: object, *, name: str, source_ids: set[str]) -> dict[str, object]:
    item = _mapping(value, name=name)
    _closed(item, name=name, required={"state", "value", "source_ids"})
    state, source_links = item["state"], item["source_ids"]
    if state not in {"known", "unknown", "conflicting"} or not isinstance(source_links, list) or len(set(source_links)) != len(source_links) or not all(isinstance(item, str) and item in source_ids for item in source_links):
        _refuse("discovery_packet_invalid", f"{name} is invalid")
    if state == "known" and (item["value"] is None or not source_links):
        _refuse("discovery_packet_incomplete", f"{name} lacks dated evidence")
    if state != "known" and item["value"] is not None:
        _refuse("discovery_packet_invalid", f"{name} must preserve uncertainty")
    return {"state": state, "value": item["value"], "source_ids": list(source_links)}


def _source(value: object, *, supporting: Mapping[str, bytes]) -> dict[str, object]:
    item = _mapping(value, name="posting source")
    _closed(item, name="posting source", required={"source_id", "locator", "title", "publisher", "published_date", "retrieved_date", "status", "capture_ref", "verification"})
    status = item["status"]
    if status not in {"reported", "captured", "independently_verified"}:
        _refuse("discovery_packet_invalid", "posting source status is invalid")
    capture = _artifact_ref(item["capture_ref"], name="posting capture", supplied=supporting, required=status != "reported")
    verification = item["verification"]
    if status == "reported":
        if capture is not None or verification is not None:
            _refuse("discovery_packet_invalid", "reported posting source cannot claim evidence")
        verified = None
    elif status == "captured":
        if verification is not None:
            _refuse("discovery_packet_invalid", "captured posting source cannot claim verification")
        verified = None
    else:
        if not isinstance(verification, Mapping):
            _refuse("discovery_packet_incomplete", "verification evidence is required")
        required = {"method", "evidence_ref", "actor"}
        if not required <= set(verification) or set(verification) - (required | {"evidence_status"}) or verification.get("evidence_status", "supplied") != "supplied":
            _refuse("discovery_packet_invalid", "posting verification has invalid fields")
        evidence = _artifact_ref(verification["evidence_ref"], name="verification evidence", supplied=supporting, required=True)
        assert capture is not None and evidence is not None
        if evidence["artifact_id"] == capture["artifact_id"] or evidence["content_sha256"] == capture["content_sha256"] or verification["method"] not in {"independent_review", "manual_comparison", "signed_attestation"}:
            _refuse("discovery_packet_invalid", "verification evidence is invalid")
        actor = _mapping(verification["actor"], name="verification actor")
        _closed(actor, name="verification actor", required={"kind", "id"})
        if actor["kind"] not in {"agent", "operator", "reviewer", "tool"}:
            _refuse("discovery_packet_invalid", "verification actor is invalid")
        verified = {"evidence_status": "supplied", "method": verification["method"], "evidence_ref": evidence, "actor": {"kind": actor["kind"], "id": _text(actor["id"], name="verification actor id", limit=255)}}
    return {"source_id": _id(item["source_id"], name="posting source id"), "locator": _locator(item["locator"]), "title": _text(item["title"], name="posting source title", limit=500), "publisher": _text(item["publisher"], name="posting source publisher", limit=500, nullable=True), "published_date": _date(item["published_date"], name="posting published date", nullable=True), "retrieved_date": _date(item["retrieved_date"], name="posting retrieved date", nullable=True), "status": status, "capture_ref": capture, "verification": verified}


def _opportunity_id(employer: str, source_posting_id: str | None, locator: str) -> str:
    # Preserve field boundaries and the identity namespace. A posting ID that
    # happens to look like a URL is not the locator-fallback identity, and
    # embedded newlines cannot move bytes from employer into posting ID.
    key = {
        "employer": employer.casefold(),
        "identity_kind": "posting_id" if source_posting_id is not None else "locator",
        "identity": source_posting_id if source_posting_id is not None else _normalized_locator(locator),
    }
    return "opportunity_" + digest_imported_bytes(canonical_json_bytes(key)).removeprefix("sha256:")[:32]


def _posting(value: object, *, supporting: Mapping[str, bytes]) -> dict[str, object]:
    item = _mapping(value, name="posting snapshot")
    required = {"employer", "title", "source_posting_id", "observed_date", "availability", "source", "facts"}
    allowed = required | {"snapshot_id", "opportunity_id", "duplicate_of"}
    if not required <= set(item) or set(item) - allowed:
        _refuse("discovery_packet_invalid", "posting snapshot has invalid fields")
    source = _source(item["source"], supporting=supporting)
    employer = _text(item["employer"], name="posting employer", limit=500)
    title = _text(item["title"], name="posting title", limit=500)
    source_posting_id = _text(item["source_posting_id"], name="source posting ID", limit=255, nullable=True)
    availability = item["availability"]
    if availability not in {"reported_open", "unknown", "stale", "closed"}:
        _refuse("discovery_packet_invalid", "posting availability is invalid")
    observed = _date(item["observed_date"], name="posting observed date")
    assert employer is not None and title is not None and observed is not None
    if availability == "reported_open" and source["status"] == "reported" and source["retrieved_date"] is None and source["published_date"] is None:
        _refuse("discovery_packet_incomplete", "reported open posting needs dated evidence")
    facts = _mapping(item["facts"], name="posting facts")
    if set(facts) != _POSTING_FIELDS:
        _refuse("discovery_packet_invalid", "posting facts are invalid")
    source_ids = {str(source["source_id"])}
    normalized_facts = {field: _fact(facts[field], name=f"posting {field}", source_ids=source_ids) for field in sorted(_POSTING_FIELDS)}
    raw_identity = {"employer": employer, "title": title, "source_posting_id": source_posting_id, "observed_date": observed, "availability": availability, "source": source, "facts": normalized_facts}
    snapshot_id = "snapshot_" + digest_imported_bytes(canonical_json_bytes(raw_identity)).removeprefix("sha256:")[:32]
    opportunity_id = _opportunity_id(employer, source_posting_id, str(source["locator"]))
    if "snapshot_id" in item and item["snapshot_id"] != snapshot_id:
        _refuse("discovery_packet_invalid", "posting snapshot identity is invalid")
    if "opportunity_id" in item and item["opportunity_id"] != opportunity_id:
        _refuse("discovery_packet_invalid", "posting opportunity identity is invalid")
    return {"snapshot_id": snapshot_id, "opportunity_id": opportunity_id, **raw_identity}


def _profile_preferences(payload: Mapping[str, object]) -> dict[str, object]:
    hard = payload.get("hard_constraints")
    soft = payload.get("soft_priorities")
    if not isinstance(hard, Mapping) or set(hard) != _PREFERENCE_FIELDS or not isinstance(soft, Mapping):
        _refuse("discovery_packet_invalid", "selected profile preferences are invalid")
    def preserve(value: object, *, name: str) -> dict[str, object]:
        fact = _mapping(value, name=name)
        _closed(fact, name=name, required={"state", "value", "context", "provenance", "conflict_refs"})
        if fact["state"] not in {"known", "unknown", "declined", "conflicting"}:
            _refuse("discovery_packet_invalid", f"{name} is invalid")
        return dict(fact)
    return {"hard_constraints": {field: preserve(hard[field], name=f"hard preference {field}") for field in sorted(_PREFERENCE_FIELDS)}, "soft_priorities": {str(field): preserve(value, name="soft preference") for field, value in sorted(soft.items())}, "sponsorship_need": preserve(payload.get("sponsorship_need"), name="sponsorship need"), "employer_sponsorship": preserve(payload.get("employer_sponsorship"), name="employer sponsorship"), "eligibility": preserve(payload.get("eligibility"), name="eligibility")}


def _reason(value: object, *, postings: Mapping[str, Mapping[str, object]], preferences: Mapping[str, object]) -> dict[str, object]:
    item = _mapping(value, name="match reason")
    _closed(item, name="match reason", required={"preference_class", "preference_field", "snapshot_id", "source_id", "detail"})
    preference_class, field, snapshot_id = item["preference_class"], item["preference_field"], _id(item["snapshot_id"], name="reason snapshot", pattern=re.compile(r"^snapshot_[0-9a-f]{32}$"))
    if preference_class not in {"hard", "soft", "sponsorship"} or not isinstance(field, str) or snapshot_id not in postings:
        _refuse("discovery_packet_invalid", "match reason is invalid")
    posting = postings[snapshot_id]
    if item["source_id"] != posting["source"]["source_id"]:
        _refuse("discovery_packet_invalid", "match reason source is invalid")
    if preference_class == "hard":
        if field not in _PREFERENCE_FIELDS or preferences["hard_constraints"][field]["state"] != "known" or posting["facts"][field]["state"] != "known":
            _refuse("discovery_packet_invalid", "unknown hard constraint cannot pass")
    elif preference_class == "soft":
        if field not in preferences["soft_priorities"] or posting["facts"].get(field, {"state": "unknown"})["state"] != "known":
            _refuse("discovery_packet_invalid", "soft match reason is invalid")
    else:
        if field not in {"sponsorship_need", "employer_sponsorship", "eligibility"} or preferences[field]["state"] != "known":
            _refuse("discovery_packet_invalid", "sponsorship match reason is invalid")
        posting_field = "employer_sponsorship" if field == "sponsorship_need" else field
        if posting["facts"][posting_field]["state"] != "known":
            _refuse("discovery_packet_invalid", "unknown sponsorship cannot pass")
    return {"preference_class": preference_class, "preference_field": field, "snapshot_id": snapshot_id, "source_id": item["source_id"], "detail": _text(item["detail"], name="match reason detail")}


def _safe_markdown(value: str) -> str:
    inline = value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ↵ ").replace("\t", " ⇥ ")
    escaped = html.escape(inline, quote=False).replace("\\", "\\\\")
    for character in "[]()!*`#>+-":
        escaped = escaped.replace(character, "\\" + character)
    return escaped


def build_discovery_packet(*, project_id: str, gig_id: str, gig_version: int, graph_id: str, graph_version: int, run_id: str, selected_inputs: Sequence[Mapping[str, object]], preference_bytes: Mapping[str, bytes], discovery: Mapping[str, object], supporting: Mapping[str, bytes]) -> DiscoveryPacket:
    """Validate supplied discovery data and return deterministic bytes without I/O."""
    project_id = _id(project_id, name="project ID", pattern=_PROJECT)
    gig_id = _id(gig_id, name="Gig ID", pattern=_GIG)
    graph_id = _id(graph_id, name="graph ID", pattern=_GRAPH)
    run_id = _id(run_id, name="Run ID", pattern=_RUN)
    if type(gig_version) is not int or gig_version < 1 or type(graph_version) is not int or graph_version < 1:
        _refuse("discovery_packet_invalid", "origin version is invalid")
    if not isinstance(preference_bytes, Mapping) or not isinstance(supporting, Mapping):
        _refuse("discovery_packet_invalid", "selected discovery inputs are invalid")
    for key, value in (*preference_bytes.items(), *supporting.items()):
        if not isinstance(key, str) or type(value) is not bytes:
            _refuse("discovery_packet_invalid", "supplied evidence bytes are invalid")
    sealed_inputs, profile_payload = _selected_inputs(selected_inputs, preference_bytes=preference_bytes)
    profile_input = next(item for item in sealed_inputs if item.get("family") == "scout_record" and item.get("native_kind") == "profile_preferences")
    preferences = _profile_preferences(profile_payload)
    raw = _mapping(discovery, name="discovery")
    _closed(raw, name="discovery", required={"outcome", "postings", "shortlist", "exclusions", "questions"})
    if raw["outcome"] not in {"matches", "partial", "no_match"} or not isinstance(raw["postings"], list) or not isinstance(raw["shortlist"], list) or not isinstance(raw["exclusions"], list) or not isinstance(raw["questions"], list):
        _refuse("discovery_packet_invalid", "discovery is invalid")
    postings = [_posting(item, supporting=supporting) for item in raw["postings"]]
    if len({item["snapshot_id"] for item in postings}) != len(postings):
        _refuse("discovery_packet_invalid", "posting snapshots collide")
    postings.sort(key=lambda item: str(item["snapshot_id"]))
    posting_by_snapshot = {str(item["snapshot_id"]): item for item in postings}
    for opportunity in {str(item["opportunity_id"]) for item in postings}:
        duplicates = [item for item in postings if item["opportunity_id"] == opportunity]
        first = duplicates[0]["snapshot_id"]
        for item in duplicates:
            item["duplicate_of"] = None if item["snapshot_id"] == first else first
    expected_supporting = {ref["artifact_id"] for item in postings for ref in (item["source"]["capture_ref"], item["source"]["verification"]["evidence_ref"] if item["source"]["verification"] else None) if ref is not None}
    if set(supporting) != expected_supporting:
        _refuse("discovery_packet_invalid", "supporting evidence coverage is invalid")
    shortlist: list[dict[str, object]] = []
    seen_opportunities: set[str] = set()
    for item in raw["shortlist"]:
        candidate = _mapping(item, name="shortlist item")
        _closed(candidate, name="shortlist item", required={"opportunity_id", "snapshot_id", "assessment", "reasons", "unresolved_fields"})
        opportunity, snapshot = _id(candidate["opportunity_id"], name="shortlist opportunity", pattern=re.compile(r"^opportunity_[0-9a-f]{32}$")), _id(candidate["snapshot_id"], name="shortlist snapshot", pattern=re.compile(r"^snapshot_[0-9a-f]{32}$"))
        if opportunity in seen_opportunities or snapshot not in posting_by_snapshot or posting_by_snapshot[snapshot]["opportunity_id"] != opportunity or candidate["assessment"] not in {"agent_reported_match", "agent_reported_partial"} or not isinstance(candidate["reasons"], list) or not candidate["reasons"] or not isinstance(candidate["unresolved_fields"], list):
            _refuse("discovery_packet_invalid", "shortlist item is invalid")
        reasons = [_reason(reason, postings=posting_by_snapshot, preferences=preferences) for reason in candidate["reasons"]]
        if any(reason["snapshot_id"] != snapshot for reason in reasons):
            _refuse("discovery_packet_invalid", "shortlist reasons are not tied to the snapshot")
        unresolved = [field for field in candidate["unresolved_fields"] if isinstance(field, str)]
        if len(unresolved) != len(candidate["unresolved_fields"]) or len(set(unresolved)) != len(unresolved):
            _refuse("discovery_packet_invalid", "shortlist unresolved fields are invalid")
        if candidate["assessment"] == "agent_reported_match":
            hard_required = set(preferences["hard_constraints"])
            hard_matched = {reason["preference_field"] for reason in reasons if reason["preference_class"] == "hard"}
            sponsorship_required = {"sponsorship_need", "employer_sponsorship", "eligibility"}
            sponsorship_matched = {reason["preference_field"] for reason in reasons if reason["preference_class"] == "sponsorship"}
            if (
                any(fact["state"] != "known" for fact in preferences["hard_constraints"].values())
                or any(preferences[field]["state"] != "known" for field in sponsorship_required)
                or hard_required != hard_matched
                or sponsorship_required != sponsorship_matched
                or unresolved
            ):
                _refuse("discovery_packet_invalid", "match does not establish every hard or sponsorship constraint")
        seen_opportunities.add(opportunity)
        shortlist.append({"opportunity_id": opportunity, "snapshot_id": snapshot, "assessment": candidate["assessment"], "reasons": reasons, "unresolved_fields": unresolved})
    if raw["outcome"] == "matches" and not shortlist:
        _refuse("discovery_packet_incomplete", "match outcome requires a shortlist")
    if raw["outcome"] == "no_match" and shortlist:
        _refuse("discovery_packet_invalid", "no-match outcome cannot have a shortlist")
    exclusions: list[dict[str, object]] = []
    for item in raw["exclusions"]:
        exclusion = _mapping(item, name="exclusion")
        _closed(exclusion, name="exclusion", required={"snapshot_id", "opportunity_id", "preference_field", "source_id", "reason"})
        snapshot = _id(exclusion["snapshot_id"], name="exclusion snapshot", pattern=re.compile(r"^snapshot_[0-9a-f]{32}$"))
        if snapshot not in posting_by_snapshot or exclusion["opportunity_id"] != posting_by_snapshot[snapshot]["opportunity_id"] or exclusion["preference_field"] not in _PREFERENCE_FIELDS | {"sponsorship_need", "employer_sponsorship", "eligibility"} or exclusion["source_id"] != posting_by_snapshot[snapshot]["source"]["source_id"]:
            _refuse("discovery_packet_invalid", "exclusion is invalid")
        exclusions.append({"snapshot_id": snapshot, "opportunity_id": exclusion["opportunity_id"], "preference_field": exclusion["preference_field"], "source_id": exclusion["source_id"], "reason": _text(exclusion["reason"], name="exclusion reason")})
    questions: list[dict[str, object]] = []
    for item in raw["questions"]:
        question = _mapping(item, name="discovery question")
        _closed(question, name="discovery question", required={"question_id", "preference_field", "prompt", "reason"})
        if question["preference_field"] not in _PREFERENCE_FIELDS | {"sponsorship_need", "employer_sponsorship", "eligibility"}:
            _refuse("discovery_packet_invalid", "discovery question is invalid")
        questions.append({"question_id": _id(question["question_id"], name="question ID"), "preference_field": question["preference_field"], "prompt": _text(question["prompt"], name="question prompt"), "reason": _text(question["reason"], name="question reason")})
    origin = {"project_id": project_id, "gig_id": gig_id, "gig_version": gig_version, "graph_selector": "find-jobs", "graph_id": graph_id, "graph_version": graph_version, "run_id": run_id}
    normalized = {"outcome": raw["outcome"], "postings": postings, "shortlist": shortlist, "exclusions": exclusions, "questions": questions}
    data_binding = digest_imported_bytes(canonical_json_bytes({"origin": origin, "selected_inputs": sealed_inputs, "selected_profile": preferences, "discovery": normalized}))
    lines = [f"# Job discovery: {_safe_markdown(str(raw['outcome']))}", "", "## Packet binding", f"Graph: find-jobs {graph_id} version {graph_version}; Run: {run_id}.", f"Selected profile: {profile_input['record_id']} {profile_input['revision_id']}.", f"Sealed data: {data_binding}.", "", "## Posting snapshots"]
    for posting in postings:
        lines.append(f"- {posting['snapshot_id']}: {_safe_markdown(str(posting['employer']))} — {_safe_markdown(str(posting['title']))}; {posting['availability']}; observed: {posting['observed_date']}; source: {_safe_markdown(str(posting['source']['locator']))}.")
    lines.extend(["", "## Shortlist"])
    lines.extend(f"- {item['opportunity_id']}: {item['assessment']}; reasons: " + "; ".join(_safe_markdown(str(reason['detail'])) for reason in item["reasons"]) for item in shortlist)
    lines.extend(["", "## Exclusions"])
    lines.extend(f"- {item['opportunity_id']}: {_safe_markdown(str(item['reason']))}" for item in exclusions)
    lines.extend(["", "## Unresolved questions"])
    lines.extend(f"- {item['question_id']}: {_safe_markdown(str(item['prompt']))}" for item in questions)
    lines.append("")
    markdown = "\n".join(lines).encode("utf-8")
    sidecar: dict[str, object] = {"schema_version": "scout-discovery-sidecar:2", "output_kind": "job_discovery", "document_sha256": digest_imported_bytes(markdown), "document_size_bytes": len(markdown), "origin": origin, "selected_inputs": sealed_inputs, "selected_profile": preferences, "discovery": normalized}
    return DiscoveryPacket(markdown=markdown, sidecar=sidecar, sidecar_bytes=canonical_json_bytes(sidecar))


def validate_rendered_packet(*, markdown: bytes, sidecar: Mapping[str, object], preference_bytes: Mapping[str, bytes], supporting: Mapping[str, bytes]) -> None:
    """Rebuild once from complete sealed data and require exact returned bytes."""
    value = _mapping(sidecar, name="discovery sidecar")
    _closed(value, name="discovery sidecar", required={"schema_version", "output_kind", "document_sha256", "document_size_bytes", "origin", "selected_inputs", "selected_profile", "discovery"})
    if value["schema_version"] != "scout-discovery-sidecar:2" or value["output_kind"] != "job_discovery" or type(markdown) is not bytes or value["document_sha256"] != digest_imported_bytes(markdown) or value["document_size_bytes"] != len(markdown):
        _refuse("discovery_packet_digest_mismatch", "discovery document binding is invalid")
    origin = _mapping(value["origin"], name="discovery origin")
    _closed(origin, name="discovery origin", required={"project_id", "gig_id", "gig_version", "graph_selector", "graph_id", "graph_version", "run_id"})
    if origin["graph_selector"] != "find-jobs":
        _refuse("discovery_packet_invalid", "discovery origin is invalid")
    rebuilt = build_discovery_packet(project_id=origin["project_id"], gig_id=origin["gig_id"], gig_version=origin["gig_version"], graph_id=origin["graph_id"], graph_version=origin["graph_version"], run_id=origin["run_id"], selected_inputs=value["selected_inputs"], preference_bytes=preference_bytes, discovery=value["discovery"], supporting=supporting)
    if rebuilt.markdown != markdown:
        _refuse("discovery_packet_render_mismatch", "discovery document does not match sealed data")
    if rebuilt.sidecar != dict(value):
        _refuse("discovery_packet_invalid", "discovery sidecar is not canonical")


__all__ = ["DiscoveryPacket", "DiscoveryPacketError", "build_discovery_packet", "validate_rendered_packet"]
