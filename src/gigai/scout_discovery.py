"""Fixed packaged validation of discovery evidence, without persistence or I/O authority.

The recording caller must authenticate the Plan and provide committed profile
bytes. This bridge never selects a profile, reads a Gig path, or runs Gig code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
from importlib import import_module, resources
import json

from jsonschema import Draft202012Validator, FormatChecker

from .canonical import canonical_json_bytes


DOMAIN_SCHEMA_ID = "urn:gigai:scout:discovery-packet:2"
VALIDATOR_ID = "scout-job-discovery:2"
SCHEMA_RESOURCE = (
    "data/scout/tools/cap_00000000-0000-4000-8000-000000000074/"
    "discovery.schema.json"
)
VALIDATOR_SOURCE = "gigai.scout_discovery:validate_discovery_domain"
FIXED_DOMAIN_RESOURCES = {
    "schema_id": DOMAIN_SCHEMA_ID,
    "schema_resource": SCHEMA_RESOURCE,
    "validator_id": VALIDATOR_ID,
    "validator_source": VALIDATOR_SOURCE,
}


class ScoutDiscoveryError(RuntimeError):
    """Content-free error safe for the recording caller's diagnostic channel."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@lru_cache(maxsize=1)
def _schema_validator() -> Draft202012Validator:
    try:
        raw = resources.files("gigai").joinpath(*SCHEMA_RESOURCE.split("/")).read_bytes()
        schema = json.loads(raw)
        if not isinstance(schema, dict) or schema.get("$id") != DOMAIN_SCHEMA_ID:
            raise ValueError("unexpected schema identity")
        Draft202012Validator.check_schema(schema)
    except (OSError, ValueError) as exc:
        raise ScoutDiscoveryError(
            "discovery_domain_validator_unavailable", "fixed discovery schema is unavailable"
        ) from exc
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_discovery_domain(
    *,
    value: Mapping[str, object],
    markdown: bytes,
    supporting: Mapping[str, bytes],
    preference_bytes: Mapping[str, bytes],
    run_id: str,
    project_id: str,
    gig_id: str,
    gig_version: int,
    graph_id: str,
    graph_selector: str,
    graph_version: int,
    selected_inputs: Sequence[Mapping[str, object]],
) -> None:
    """Bind supplied discovery bytes to independently authenticated caller context.

    Source verification and match claims remain external-agent declarations.
    This verifies structural integrity and exact evidence, not hiring eligibility.
    """
    if not isinstance(value, Mapping) or type(markdown) is not bytes:
        raise ScoutDiscoveryError("discovery_domain_invalid", "discovery input is invalid")
    domain = dict(value)
    try:
        # Canonicalization also rejects non-JSON objects and invalid numeric values
        # before schema traversal or rendering can encounter them.
        canonical_json_bytes(domain)
        if next(_schema_validator().iter_errors(domain), None) is not None:
            raise ValueError("schema mismatch")
        expected_origin = {
            "project_id": project_id, "gig_id": gig_id, "gig_version": gig_version,
            "graph_selector": graph_selector, "graph_id": graph_id,
            "graph_version": graph_version, "run_id": run_id,
        }
        if (
            graph_selector != "find-jobs"
            or type(gig_version) is not int or type(graph_version) is not int
            or canonical_json_bytes(domain["origin"]) != canonical_json_bytes(expected_origin)
        ):
            raise ScoutDiscoveryError(
                "discovery_domain_origin_mismatch", "discovery origin differs from the sealed Run"
            )
        if canonical_json_bytes(domain["selected_inputs"]) != canonical_json_bytes(selected_inputs):
            raise ScoutDiscoveryError(
                "discovery_domain_input_mismatch", "discovery inputs differ from the sealed Plan"
            )
    except (ValueError, TypeError, KeyError) as exc:
        raise ScoutDiscoveryError("discovery_domain_invalid", "discovery data is invalid") from exc
    try:
        # This literal is package code, never an agent-supplied module or Gig path.
        renderer = import_module(
            "gigai.data.scout.tools.cap_00000000-0000-4000-8000-000000000074.discovery"
        )
    except ImportError as exc:
        raise ScoutDiscoveryError(
            "discovery_domain_validator_unavailable", "fixed discovery renderer is unavailable"
        ) from exc
    try:
        renderer.validate_rendered_packet(
            markdown=markdown, sidecar=domain,
            preference_bytes=preference_bytes, supporting=supporting,
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise ScoutDiscoveryError(
            "discovery_domain_invalid", "discovery evidence fails the fixed renderer contract"
        ) from exc


__all__ = [
    "DOMAIN_SCHEMA_ID", "VALIDATOR_ID", "SCHEMA_RESOURCE", "VALIDATOR_SOURCE",
    "FIXED_DOMAIN_RESOURCES", "ScoutDiscoveryError", "validate_discovery_domain",
]
