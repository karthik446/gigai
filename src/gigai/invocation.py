"""Validation for explicit agent-to-GigAI invocation envelopes."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping

from .canonical import canonical_json_bytes


INVOCATION_PROTOCOL_VERSION = "1"
EXPLICIT_TRIGGERS = ("gigai:", "$gigai", "/gigai", "gigai run")
ALLOWED_COMMANDS = frozenset({"setup", "models", "doctor", "create", "run"})
KNOWN_AGENT_IDS = frozenset({"codex", "claude"})
MAX_ENVELOPE_BYTES = 256 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_TOP_LEVEL_FIELDS = frozenset(
    {
        "protocol_version",
        "invocation_id",
        "trigger",
        "actor",
        "command",
        "target",
        "input",
        "requested",
        "consent",
    }
)
_ACTOR_FIELDS = frozenset({"kind", "id", "session_id"})
_TARGET_FIELDS = frozenset({"home", "project"})
_INPUT_FIELDS = {
    "setup": frozenset({"answers"}),
    "models": frozenset({"probe"}),
    "doctor": frozenset({"probe"}),
    "create": frozenset({"intent", "answers", "proposal"}),
    "run": frozenset({"gig_id", "version", "wait"}),
}
_PROPOSAL_FIELDS = frozenset(
    {"summary", "assumptions", "unresolved_questions", "citations", "effect"}
)
_CITATION_FIELDS = frozenset(
    {"claim_id", "source_kind", "locator", "source_sha256", "verification"}
)
_CONSENT_FIELDS = frozenset({"action", "actor"})
_FORBIDDEN_KEYS = frozenset(
    {
        "conversation",
        "credential",
        "credentials",
        "hidden_prompt",
        "messages",
        "raw_prompt",
        "secret",
        "secrets",
        "token",
        "tokens",
        "transcript",
    }
)


class InvocationValidationError(ValueError):
    """An explicit agent invocation is malformed or unsafe to accept."""

    code = "invocation_invalid"


@dataclass(frozen=True)
class AgentInvocation:
    protocol_version: str
    invocation_id: str
    trigger: str
    actor: dict[str, str]
    command: str
    target: dict[str, str]
    input: dict[str, Any]
    requested: dict[str, list[str]]
    consent: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "invocation_id": self.invocation_id,
            "trigger": self.trigger,
            "actor": dict(self.actor),
            "command": self.command,
            "target": dict(self.target),
            "input": self.input,
            "requested": self.requested,
            "consent": list(self.consent),
        }


def parse_invocation(payload: Mapping[str, Any]) -> AgentInvocation:
    """Validate one bounded, typed envelope without importing conversation state."""

    try:
        encoded = canonical_json_bytes(dict(payload))
    except (TypeError, ValueError) as exc:
        raise InvocationValidationError("invocation envelope must be JSON data") from exc
    if len(encoded) > MAX_ENVELOPE_BYTES:
        raise InvocationValidationError("invocation envelope exceeds the size limit")
    _reject_forbidden_keys(payload)
    _reject_unknown_fields(payload, _TOP_LEVEL_FIELDS, "invocation")

    protocol_version = _required_string(payload, "protocol_version")
    if protocol_version != INVOCATION_PROTOCOL_VERSION:
        raise InvocationValidationError("unsupported invocation protocol version")
    invocation_id = _required_string(payload, "invocation_id")
    if not invocation_id.startswith("inv_") or not _IDENTIFIER.fullmatch(invocation_id):
        raise InvocationValidationError("invocation_id must be a bounded inv_ identifier")
    trigger = _required_string(payload, "trigger")
    if trigger not in EXPLICIT_TRIGGERS:
        raise InvocationValidationError("invocation requires an explicit GigAI trigger")
    actor = _actor(payload.get("actor"))
    command = _required_string(payload, "command")
    if command not in ALLOWED_COMMANDS:
        raise InvocationValidationError(f"unsupported invocation command: {command}")
    target = _string_map(payload.get("target"), "target", _TARGET_FIELDS)
    input_values = _input(payload.get("input"), command)
    requested = _requested(payload.get("requested"))
    consent = _consent(payload.get("consent"))
    if command == "run" and consent:
        raise InvocationValidationError(
            "agent-provided run consent is not accepted; use direct operator confirmation"
        )
    return AgentInvocation(
        protocol_version=protocol_version,
        invocation_id=invocation_id,
        trigger=trigger,
        actor=actor,
        command=command,
        target=target,
        input=dict(input_values),
        requested=requested,
        consent=consent,
    )


def load_invocation_bytes(data: bytes) -> AgentInvocation:
    if len(data) > MAX_ENVELOPE_BYTES:
        raise InvocationValidationError("invocation envelope exceeds the size limit")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvocationValidationError("invocation input must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise InvocationValidationError("invocation input must be a JSON object")
    return parse_invocation(payload)


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise InvocationValidationError(f"{key} must be a non-empty string")
    return value.strip()


def _actor(value: Any) -> dict[str, str]:
    actor = _string_map(value, "actor", _ACTOR_FIELDS)
    if actor.get("kind") != "agent":
        raise InvocationValidationError("actor.kind must be agent")
    if "id" not in actor or "session_id" not in actor:
        raise InvocationValidationError("actor requires id and session_id")
    if actor["id"] not in KNOWN_AGENT_IDS:
        raise InvocationValidationError(f"unknown agent actor: {actor['id']}")
    return actor


def _string_map(
    value: Any, name: str, allowed_fields: frozenset[str]
) -> dict[str, str]:
    if not isinstance(value, dict):
        raise InvocationValidationError(f"{name} must be an object")
    _reject_unknown_fields(value, allowed_fields, name)
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise InvocationValidationError(f"{name} values must be strings")
        if "\0" in key or "\0" in item or len(item) > 4096:
            raise InvocationValidationError(f"{name} contains an invalid value")
        result[key] = item
    return result


def _input(value: Any, command: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvocationValidationError("input must be an object")
    _reject_unknown_fields(value, _INPUT_FIELDS[command], "input")
    result = dict(value)
    if "intent" in result:
        _bounded_text(result["intent"], "input.intent", 20_000)
    if "answers" in result:
        _answers(result["answers"])
    if "proposal" in result:
        _proposal(result["proposal"])
    if "probe" in result:
        _bounded_text(result["probe"], "input.probe", 255)
    if "gig_id" in result:
        _bounded_text(result["gig_id"], "input.gig_id", 255)
    if "version" in result and (
        type(result["version"]) is not int or result["version"] < 1
    ):
        raise InvocationValidationError("input.version must be a positive integer")
    if "wait" in result and type(result["wait"]) is not bool:
        raise InvocationValidationError("input.wait must be boolean")
    return result


def _answers(value: Any) -> None:
    if not isinstance(value, dict):
        raise InvocationValidationError("input.answers must be an object")
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise InvocationValidationError("input.answers must map strings to strings")
        _bounded_text(key, "input.answers key", 255)
        _bounded_text(item, "input.answers value", 20_000)


def _proposal(value: Any) -> None:
    if not isinstance(value, dict):
        raise InvocationValidationError("input.proposal must be an object")
    _reject_unknown_fields(value, _PROPOSAL_FIELDS, "input.proposal")
    if "summary" in value:
        _bounded_text(value["summary"], "input.proposal.summary", 20_000)
    if "effect" in value:
        _bounded_text(value["effect"], "input.proposal.effect", 255)
    for field in ("assumptions", "unresolved_questions"):
        if field in value:
            values = value[field]
            if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
                raise InvocationValidationError(f"input.proposal.{field} must be a string list")
            for item in values:
                _bounded_text(item, f"input.proposal.{field} item", 20_000)
    if "citations" in value:
        citations = value["citations"]
        if not isinstance(citations, list):
            raise InvocationValidationError("input.proposal.citations must be a list")
        for citation in citations:
            if not isinstance(citation, dict):
                raise InvocationValidationError("input.proposal citations must be objects")
            _reject_unknown_fields(citation, _CITATION_FIELDS, "input.proposal.citation")
            for field in ("claim_id", "source_kind", "locator", "verification"):
                if field in citation:
                    _bounded_text(citation[field], f"citation.{field}", 4096)
            if "source_sha256" in citation and citation["source_sha256"] is not None:
                _bounded_text(citation["source_sha256"], "citation.source_sha256", 255)


def _bounded_text(value: Any, name: str, limit: int) -> None:
    if not isinstance(value, str) or not value.strip() or "\0" in value or len(value) > limit:
        raise InvocationValidationError(f"{name} must be bounded text")


def _requested(value: Any) -> dict[str, list[str]]:
    if value is None:
        return {"roles": [], "models": [], "capabilities": []}
    if not isinstance(value, dict):
        raise InvocationValidationError("requested must be an object")
    result: dict[str, list[str]] = {}
    for key in ("roles", "models", "capabilities"):
        values = value.get(key, [])
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise InvocationValidationError(f"requested.{key} must be a string list")
        result[key] = list(values)
    unknown = set(value) - {"roles", "models", "capabilities"}
    if unknown:
        raise InvocationValidationError(f"requested contains unsupported fields: {sorted(unknown)}")
    return result


def _consent(value: Any) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise InvocationValidationError("consent must be a list")
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise InvocationValidationError("each consent record must be an object")
        _reject_unknown_fields(item, _CONSENT_FIELDS, "consent record")
        if not isinstance(item.get("action"), str) or not isinstance(item.get("actor"), dict):
            raise InvocationValidationError("consent requires action and actor")
        if item["actor"].get("kind") != "operator":
            raise InvocationValidationError("consent actor must be operator")
        if item["actor"].get("id") != "local-user" or set(item["actor"]) != {"kind", "id"}:
            raise InvocationValidationError("consent actor must be the local operator")
        normalized.append(dict(item))
    return tuple(normalized)


def _reject_unknown_fields(
    value: Mapping[str, Any], allowed: frozenset[str], name: str
) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise InvocationValidationError(
            f"{name} contains unsupported fields: {sorted(unknown)}"
        )


def _reject_forbidden_keys(value: Any) -> None:
    if isinstance(value, dict):
        forbidden = _FORBIDDEN_KEYS.intersection(value)
        if forbidden:
            raise InvocationValidationError(
                f"invocation contains forbidden fields: {sorted(forbidden)}"
            )
        for item in value.values():
            _reject_forbidden_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_forbidden_keys(item)


__all__ = [
    "ALLOWED_COMMANDS",
    "AgentInvocation",
    "EXPLICIT_TRIGGERS",
    "KNOWN_AGENT_IDS",
    "InvocationValidationError",
    "INVOCATION_PROTOCOL_VERSION",
    "load_invocation_bytes",
    "parse_invocation",
]
