"""Fixed, packaged bridge for the candidate Scout research-packet v3 domain.

The bridge is deliberately not a Gig-code loader.  It imports one literal
packaged renderer and schema, then compares the domain value with the already
sealed Plan inputs supplied by the generic recorder.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
from importlib import import_module, resources
import json
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .canonical import CanonicalizationError, canonical_json_bytes


DOMAIN_SCHEMA_ID = "urn:gigai:scout:research-packet:3"
VALIDATOR_ID = "scout-role-research:3"
SCHEMA_RESOURCE = (
    "data/scout/tools/cap_00000000-0000-4000-8000-000000000076/"
    "research.schema.json"
)
VALIDATOR_SOURCE = "gigai.scout_research_v3:validate_research_domain"
FIXED_DOMAIN_RESOURCES = {
    "schema_id": DOMAIN_SCHEMA_ID,
    "schema_resource": SCHEMA_RESOURCE,
    "validator_id": VALIDATOR_ID,
    "validator_source": VALIDATOR_SOURCE,
}
_RENDERER_MODULE = (
    "gigai.data.scout.tools.cap_00000000-0000-4000-8000-000000000076.research"
)


class ScoutResearchError(RuntimeError):
    """Typed, content-free refusal for a candidate research domain value."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _refuse(code: str, message: str) -> None:
    raise ScoutResearchError(code, message)


def _canonical(value: object, *, name: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (CanonicalizationError, TypeError, ValueError) as exc:
        raise ScoutResearchError("research_domain_invalid", f"{name} is invalid") from exc


@lru_cache(maxsize=1)
def _schema_validator() -> Draft202012Validator:
    try:
        schema_bytes = resources.files("gigai").joinpath(*SCHEMA_RESOURCE.split("/")).read_bytes()
        schema = json.loads(schema_bytes)
    except (AttributeError, FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScoutResearchError(
            "research_domain_validator_unavailable",
            "fixed research schema resource is unavailable",
        ) from exc
    if not isinstance(schema, dict) or schema.get("$id") != DOMAIN_SCHEMA_ID:
        _refuse("research_domain_validator_unavailable", "fixed research schema resource is invalid")
    return Draft202012Validator(schema, format_checker=FormatChecker())


@lru_cache(maxsize=1)
def _renderer() -> Any:
    try:
        module = import_module(_RENDERER_MODULE)
    except (ImportError, AttributeError, ValueError) as exc:
        raise ScoutResearchError(
            "research_domain_validator_unavailable",
            "fixed research renderer is unavailable",
        ) from exc
    if not all(hasattr(module, name) for name in ("ResearchPacketError", "build_research_packet")):
        _refuse("research_domain_validator_unavailable", "fixed research renderer is invalid")
    return module


def _trusted_origin(
    *,
    run_id: object,
    project_id: object,
    gig_id: object,
    gig_version: object,
    graph_id: object,
    graph_selector: object,
    graph_version: object,
) -> dict[str, object]:
    if graph_selector != "research-role":
        _refuse("research_domain_origin_mismatch", "trusted graph selector is unsupported")
    if type(gig_version) is not int or type(graph_version) is not int:
        _refuse("research_domain_invalid", "trusted origin is invalid")
    return {
        "project_id": project_id,
        "gig_id": gig_id,
        "gig_version": gig_version,
        "graph_selector": graph_selector,
        "graph_id": graph_id,
        "graph_version": graph_version,
        "run_id": run_id,
    }


def _supporting_ids(value: Mapping[str, object]) -> set[str]:
    research = value.get("research")
    if not isinstance(research, Mapping) or not isinstance(research.get("sources"), list):
        _refuse("research_domain_invalid", "research domain data is invalid")
    result: set[str] = set()
    for source in research["sources"]:
        if not isinstance(source, Mapping):
            _refuse("research_domain_invalid", "research source is invalid")
        capture = source.get("capture_ref")
        if isinstance(capture, Mapping) and isinstance(capture.get("artifact_id"), str):
            result.add(capture["artifact_id"])
        verification = source.get("verification")
        if isinstance(verification, Mapping):
            evidence = verification.get("evidence_ref")
            if isinstance(evidence, Mapping) and isinstance(evidence.get("artifact_id"), str):
                result.add(evidence["artifact_id"])
    return result


def _validate_supporting(supporting: object, *, expected_ids: set[str]) -> Mapping[str, bytes]:
    if not isinstance(supporting, Mapping):
        _refuse("research_domain_invalid", "supporting evidence is invalid")
    if set(supporting) != expected_ids:
        _refuse("research_domain_supporting_mismatch", "supporting evidence coverage is invalid")
    for artifact_id, data in supporting.items():
        if not isinstance(artifact_id, str) or type(data) is not bytes:
            _refuse("research_domain_invalid", "supporting evidence is invalid")
    return supporting


def _historical_context(historical: Mapping[str, object]) -> dict[str, object]:
    """Return trusted provenance for one directly selected historical Run.

    The selector was resolved from the committed Run/Plan/receipt tuple by the
    caller.  Historical packet validation may therefore use that resolved
    provenance to authenticate the packet, but must not use the packet's own
    ``origin`` as its authority or reopen the selector's ancestry.
    """
    selector = historical.get("selector")
    graph = selector.get("selected_graph") if isinstance(selector, Mapping) else None
    if (
        not isinstance(selector, Mapping)
        or selector.get("family") != "scout_research"
        or not isinstance(selector.get("run_id"), str)
        or not isinstance(selector.get("project_id"), str)
        or not isinstance(selector.get("gig_id"), str)
        or type(selector.get("gig_version")) is not int
        or not isinstance(graph, Mapping)
        or not isinstance(graph.get("goal_graph_id"), str)
        or graph.get("graph_selector") != "research-role"
        or type(graph.get("graph_version")) is not int
    ):
        _refuse("research_domain_input_mismatch", "historical research provenance is invalid")
    return _trusted_origin(
        run_id=selector["run_id"],
        project_id=selector["project_id"],
        gig_id=selector["gig_id"],
        gig_version=selector["gig_version"],
        graph_id=graph["goal_graph_id"],
        graph_selector=graph["graph_selector"],
        graph_version=graph["graph_version"],
    )


def _validate_historical_packet_integrity(
    *,
    historical: Mapping[str, object],
    prior_packet: Mapping[str, object],
    prior_markdown: bytes,
    prior_supporting: Mapping[str, bytes],
) -> None:
    """Authenticate one selected packet without recursively validating inputs.

    This is deliberately a direct tuple check.  The current Run's selected
    inputs are validated separately by the recorder; a completed historical
    packet is rebuilt from its own bytes and trusted resolved provenance here.
    Its selected-input references remain sealed data, not traversal requests.
    """
    origin = _historical_context(historical)
    schema_id = historical.get("domain_schema_id")
    if schema_id == DOMAIN_SCHEMA_ID:
        validate_prior = validate_research_domain
    elif schema_id == "urn:gigai:scout:research-packet:2":
        from .scout_research import validate_research_domain as validate_prior
    else:
        _refuse("research_domain_input_mismatch", "historical research domain version is unsupported")
    validate_prior(
        value=prior_packet,
        markdown=prior_markdown,
        supporting=prior_supporting,
        run_id=origin["run_id"],
        project_id=origin["project_id"],
        gig_id=origin["gig_id"],
        gig_version=origin["gig_version"],
        graph_id=origin["graph_id"],
        graph_selector=origin["graph_selector"],
        graph_version=origin["graph_version"],
        selected_inputs=prior_packet["selected_inputs"],
    )


def validate_research_domain(
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
    historical_inputs: Sequence[Mapping[str, object]] = (),
) -> None:
    """Validate v3 bytes and directly selected historical packet bytes.

    The caller supplies the already authenticated Plan identity; this function
    does no workpad access, schema network resolution, executable Gig import,
    or provider work.
    """

    if not isinstance(value, Mapping) or type(markdown) is not bytes:
        _refuse("research_domain_invalid", "research domain input is invalid")
    domain = dict(value)
    schema_errors = tuple(_schema_validator().iter_errors(domain))
    if schema_errors:
        _refuse("research_domain_invalid", "research domain value violates the fixed schema")
    expected_origin = _trusted_origin(
        run_id=run_id,
        project_id=project_id,
        gig_id=gig_id,
        gig_version=gig_version,
        graph_id=graph_id,
        graph_selector=graph_selector,
        graph_version=graph_version,
    )
    if _canonical(domain["origin"], name="research origin") != _canonical(
        expected_origin, name="trusted origin"
    ):
        _refuse("research_domain_origin_mismatch", "research origin does not match the sealed Run")
    if _canonical(domain["selected_inputs"], name="research selected inputs") != _canonical(
        selected_inputs, name="trusted selected inputs"
    ):
        _refuse("research_domain_input_mismatch", "research selected inputs do not match the sealed Plan")
    for historical in historical_inputs:
        if not isinstance(historical, Mapping):
            _refuse("research_domain_input_mismatch", "historical research input is invalid")
        sidecar = historical.get("sidecar")
        prior_domain = historical.get("domain")
        prior_markdown = historical.get("markdown")
        prior_supporting = historical.get("supporting")
        prior_packet = prior_domain.get("value") if isinstance(prior_domain, Mapping) else None
        if (
            not isinstance(sidecar, Mapping)
            or sidecar.get("output_kind") != "research"
            or not isinstance(prior_domain, Mapping)
            or not isinstance(prior_packet, Mapping)
            or not isinstance(prior_markdown, bytes)
            or not isinstance(prior_supporting, Mapping)
            or prior_packet.get("schema_version") not in {"scout-research-sidecar:2", "scout-research-sidecar:3"}
            or not isinstance(prior_packet.get("origin"), Mapping)
            or not isinstance(prior_packet.get("selected_inputs"), list)
        ):
            _refuse("research_domain_input_mismatch", "historical research bytes are invalid")
        if prior_domain.get("schema_id") not in {"urn:gigai:scout:research-packet:2", DOMAIN_SCHEMA_ID} or not isinstance(prior_domain.get("value"), Mapping):
            _refuse("research_domain_input_mismatch", "historical research domain version is unsupported")
        selector = historical.get("selector")
        binding = selector.get("domain_binding") if isinstance(selector, Mapping) else None
        trusted_schema_id = historical.get("domain_schema_id")
        if (
            not isinstance(selector, Mapping)
            or not isinstance(binding, Mapping)
            or trusted_schema_id != binding.get("schema_id")
            or trusted_schema_id != prior_domain["schema_id"]
        ):
            _refuse("research_domain_input_mismatch", "historical research provenance is invalid")
        domain_schema_id = trusted_schema_id
        try:
            _validate_historical_packet_integrity(
                historical={
                    **dict(historical),
                    "selector": selector,
                    "domain_schema_id": domain_schema_id,
                },
                prior_packet=prior_packet,
                prior_markdown=prior_markdown,
                prior_supporting=prior_supporting,
            )
        except ScoutResearchError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", "research_domain_input_mismatch")
            _refuse(code, "historical research bytes do not validate")
    evidence = _validate_supporting(supporting, expected_ids=_supporting_ids(domain))
    renderer = _renderer()
    try:
        rebuilt = renderer.build_research_packet(
            project_id=project_id,
            gig_id=gig_id,
            gig_version=gig_version,
            graph_id=graph_id,
            graph_version=graph_version,
            run_id=run_id,
            selected_inputs=selected_inputs,
            research=domain["research"],
            artifact_bytes=evidence,
        )
    except renderer.ResearchPacketError as exc:
        raise ScoutResearchError(
            "research_domain_invalid", "research domain does not satisfy the fixed renderer contract"
        ) from exc
    if rebuilt.markdown != markdown:
        _refuse("research_domain_render_mismatch", "research document does not match sealed domain data")
    if _canonical(rebuilt.sidecar, name="rebuilt research domain") != _canonical(
        domain, name="research domain"
    ):
        _refuse("research_domain_invalid", "research domain value is not canonical")


__all__ = [
    "DOMAIN_SCHEMA_ID",
    "FIXED_DOMAIN_RESOURCES",
    "SCHEMA_RESOURCE",
    "ScoutResearchError",
    "VALIDATOR_ID",
    "VALIDATOR_SOURCE",
    "validate_research_domain",
]
