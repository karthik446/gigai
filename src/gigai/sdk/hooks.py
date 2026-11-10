"""Named hooks: how core reaches an object a gig provides without importing the gig.

A hook is a name bound to a ``module:attribute`` target. ``get(name)`` returns the target object and
imports its module at that first call, never when a core module is imported; it returns exactly what
``from module import attribute`` returned (function, class or constant) and raises what that import
raised. Nothing else happens here: no caching of objects beyond ``sys.modules``, no wrapping.

Where the target comes from: a row of the built-in bridge below when there is one, else the ``[hooks]``
table of the providing gig's ``gig.toml``. Scout is not a registered gig yet, so the bridge names today's
Scout objects; when Scout declares a hook in its own manifest, that row goes. While a row exists it wins:
core's Scout paths never scan installed gigs, and no installed distribution can stand in for a Scout
object (``external_recording`` keeps its closed validator table).
"""

from __future__ import annotations

from functools import cache
from importlib import import_module
from typing import Any

SCOUT = "scout"

# The gig that provides every bridge row is SCOUT. Names are snake_case so a gig.toml can declare them as
# bare keys (a dotted TOML key would be a nested table).
_BRIDGE: dict[str, str] = {
    # external_recording: inputs a Run selects, and the tailoring request rule
    "input_error": "gigai.scout.inputs:ScoutInputError",
    "assert_single_override_context": "gigai.scout.inputs:assert_single_override_context",
    "resolve_external_input": "gigai.scout.inputs:resolve_external_input",
    "revalidate_external_input": "gigai.scout.inputs:revalidate_external_input",
    "validate_tailoring_request": "gigai.scout.tailoring:validate_tailoring_request",
    # external_recording: the fixed domain validators and their packaged resources
    "research_domain_validator": "gigai.scout.research:validate_research_domain",
    "research_v3_domain_validator": "gigai.scout.research_v3:validate_research_domain",
    "discovery_domain_validator": "gigai.scout.discovery:validate_discovery_domain",
    "tailoring_domain_validator": "gigai.scout.tailoring:validate_tailoring_domain",
    "research_domain_resources": "gigai.scout.research:FIXED_DOMAIN_RESOURCES",
    "research_v3_domain_resources": "gigai.scout.research_v3:FIXED_DOMAIN_RESOURCES",
    "discovery_domain_resources": "gigai.scout.discovery:FIXED_DOMAIN_RESOURCES",
    "tailoring_domain_resources": "gigai.scout.tailoring:FIXED_DOMAIN_RESOURCES",
    # default_init, portability: the default instance's source material
    "materialization_error": "gigai.scout.materialization:ScoutMaterializationError",
    "is_materialization_candidate": "gigai.scout.materialization:is_scout_candidate",
    "materialize_candidate": "gigai.scout.materialization:materialize_scout_candidate",
    "repair_prepared_capability_manifest": "gigai.scout.materialization:repair_prepared_scout_capability_manifest",
    "read_source_snapshot": "gigai.scout.materialization:read_scout_source_snapshot",
    # capability_review, capability_successor, native_records: tool capabilities
    "tool_error": "gigai.scout.tools:ScoutToolError",
    "tool_inventory": "gigai.scout.tools:_inventory",
    "revalidate_tool_binding_at_publication": "gigai.scout.tools:revalidate_tool_binding_at_publication",
    # private_transfer: the bundled definition files
    "definition_source_files": "gigai.scout.template:scout_source_files",
    # application_cli, application_events: postings and opportunities
    "normalize_posting_url": "gigai.scout.find_jobs.contracts:normalize_url",
    "opportunity_reader": "gigai.scout.report_readers:opportunity_reader",
    # diagnostics: which model assessments ask for
    "assessment_asked_by_default": "gigai.scout.evaluated_models:ASKED_BY_DEFAULT",
    "assessment_results_page": "gigai.scout.evaluated_models:RESULTS_PAGE",
    "assessment_asked_model": "gigai.scout.evaluated_models:asked_model",
    "assessment_evaluated_for": "gigai.scout.evaluated_models:evaluated_for",
    "assessment_model_notice": "gigai.scout.evaluated_models:model_notice",
    # run: the find-jobs Run contract (sealed DTOs, enums, the status fold)
    "find_jobs_assess_local_effects": "gigai.scout.find_jobs.contracts:ASSESS_LOCAL_EFFECTS",
    "find_jobs_acquire_input": "gigai.scout.find_jobs.contracts:AcquireInput",
    "find_jobs_acquire_output": "gigai.scout.find_jobs.contracts:AcquireOutput",
    "find_jobs_artifact_ref": "gigai.scout.find_jobs.contracts:ArtifactRef",
    "find_jobs_assess_input": "gigai.scout.find_jobs.contracts:AssessInput",
    "find_jobs_config": "gigai.scout.find_jobs.contracts:FindJobsConfig",
    "find_jobs_run_input": "gigai.scout.find_jobs.contracts:FindJobsRunInput",
    "find_jobs_goal_error": "gigai.scout.find_jobs.contracts:GoalError",
    "find_jobs_model_target": "gigai.scout.find_jobs.contracts:ModelTarget",
    "find_jobs_node_context": "gigai.scout.find_jobs.contracts:NodeContext",
    "find_jobs_node_failure": "gigai.scout.find_jobs.contracts:NodeFailure",
    "find_jobs_node_receipt": "gigai.scout.find_jobs.contracts:NodeReceipt",
    "find_jobs_node_status": "gigai.scout.find_jobs.contracts:NodeStatus",
    "find_jobs_present_input": "gigai.scout.find_jobs.contracts:PresentInput",
    "find_jobs_producer": "gigai.scout.find_jobs.contracts:Producer",
    "find_jobs_pinned_resume": "gigai.scout.find_jobs.contracts:PinnedResume",
    "find_jobs_profile_ref": "gigai.scout.find_jobs.contracts:ProfileRef",
    "find_jobs_run_request": "gigai.scout.find_jobs.contracts:RunRequest",
    "find_jobs_selection_reason_code": "gigai.scout.find_jobs.contracts:SelectionReasonCode",
    "find_jobs_usage_block": "gigai.scout.find_jobs.contracts:UsageBlock",
    "find_jobs_aggregate_status": "gigai.scout.find_jobs.contracts:aggregate_status",
    # run: the resume revision a find-jobs Run pins
    "resume_record_revision": "gigai.scout.inputs:_record_revision",
    # run: local proposal, interview and Tailor Runs
    "execute_local_proposal": "gigai.scout.proposal_execution:execute_local_proposal",
    "record_proposal_revision": "gigai.scout.proposal_records:record_proposal_revision",
    "prepare_interview": "gigai.scout.interview_records:prepare_interview",
    "committed_run_bytes": "gigai.scout.proposal_execution:_committed_run_bytes",
    "validate_active_goal_bytes": "gigai.scout.proposal_execution:_validate_active_goal_bytes",
    "resolve_proposal_sources": "gigai.scout.proposal_execution:_resolve_sources",
    "tailor_answer": "gigai.scout.tailor_selection:TailorAnswer",
    "tailor_proposal": "gigai.scout.tailor_selection:TailorProposal",
    "tailor_selection": "gigai.scout.tailor_selection:TailorSelection",
    "tailor_source": "gigai.scout.tailor_selection:TailorSource",
    "build_tailoring_request": "gigai.scout.tailor_selection:build_tailoring_request",
    "hydrate_saved_proposal": "gigai.scout.tailor_selection:hydrate_saved_proposal",
    "execute_tailor": "gigai.scout.tailor_execution:execute_tailor",
    "record_document_revision": "gigai.scout.document_records:record_document_revision",
    "prepare_document_revision": "gigai.scout.documents:prepare_document_revision",
    "decode_tailoring_bundle": "gigai.scout.tailoring:decode_tailoring_bundle",
}


class HookUnavailable(LookupError):
    """The gig declares no such hook and the bridge has no row for it."""


def get(name: str, *, gig: str = SCOUT) -> Any:
    """The object hook ``name`` of ``gig`` names, imported now: the bridge row, else the gig's ``gig.toml`` declaration."""

    target = _BRIDGE.get(name) if gig == SCOUT else None
    if target is None:
        target = declared(name).get(gig)
    if target is None:
        raise HookUnavailable(f"gig {gig!r} provides no hook {name!r}")
    module_name, _, attribute = target.partition(":")
    module = import_module(module_name)
    try:
        return getattr(module, attribute)
    except AttributeError:
        # What ``from module import attribute`` raises for a missing name.
        raise ImportError(
            f"cannot import name {attribute!r} from {module_name!r} ({getattr(module, '__file__', None) or 'unknown location'})",
            name=module_name,
            path=getattr(module, "__file__", None),
        ) from None


def declared(name: str) -> dict[str, str]:
    """``{gig: target}`` for every installed gig whose ``[hooks]`` table declares ``name``; the others are skipped."""

    return {gig: table[name] for gig, table in _declarations().items() if name in table}


@cache
def _declarations() -> dict[str, dict[str, str]]:
    """Every installed gig's ``[hooks]`` table, read once per process from the manifests (no gig code runs).

    A gig whose entry points or manifest cannot be read declares nothing here; ``gigai <gig>`` reports it.
    Like the loader, the first entry point for a name wins.
    """

    from importlib.metadata import entry_points

    from ..gigs.loader import ENTRY_POINT_GROUP, read_manifest

    try:
        found = tuple(entry_points(group=ENTRY_POINT_GROUP))
    except Exception:  # noqa: BLE001 - broken metadata of any dist: no gig declares a hook
        return {}
    tables: dict[str, dict[str, str]] = {}
    for entry_point in found:
        if entry_point.name in tables:
            continue
        try:
            table = read_manifest(entry_point).get("hooks")
        except Exception:  # noqa: BLE001 - an unreadable manifest declares nothing
            table = None
        tables[entry_point.name] = (
            {key: value for key, value in table.items() if isinstance(value, str)} if isinstance(table, dict) else {}
        )
    return tables


__all__ = ["HookUnavailable", "SCOUT", "declared", "get"]
