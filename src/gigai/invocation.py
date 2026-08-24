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
MAX_ENVELOPE_BYTES = 256 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
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
    target = _string_map(payload.get("target"), "target")
    input_values = payload.get("input")
    if not isinstance(input_values, dict):
        raise InvocationValidationError("input must be an object")
    requested = _requested(payload.get("requested"))
    consent = _consent(payload.get("consent"))
    if command == "run" and not consent:
        raise InvocationValidationError(
            "run invocation requires an explicit operator consent record"
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
    actor = _string_map(value, "actor")
    if actor.get("kind") != "agent":
        raise InvocationValidationError("actor.kind must be agent")
    if "id" not in actor or "session_id" not in actor:
        raise InvocationValidationError("actor requires id and session_id")
    return actor


def _string_map(value: Any, name: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise InvocationValidationError(f"{name} must be an object")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise InvocationValidationError(f"{name} values must be strings")
        if "\0" in key or "\0" in item or len(item) > 4096:
            raise InvocationValidationError(f"{name} contains an invalid value")
        result[key] = item
    return result


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
        if not isinstance(item.get("action"), str) or not isinstance(item.get("actor"), dict):
            raise InvocationValidationError("consent requires action and actor")
        if item["actor"].get("kind") != "operator":
            raise InvocationValidationError("consent actor must be operator")
        normalized.append(dict(item))
    return tuple(normalized)


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
    "InvocationValidationError",
    "INVOCATION_PROTOCOL_VERSION",
    "load_invocation_bytes",
    "parse_invocation",
]
