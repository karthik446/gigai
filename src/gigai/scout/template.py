"""Inert, versioned Scout authoring source shipped with GigAI.

This is not an approved Gig, an executable provider adapter, or an initialization
receipt. Provisioning must bind a fresh instance identity and use the normal
proposal/approval path. Keeping source separate also lets a package update be
compared without replacing a user's customized instance.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from ..canonical import canonical_json_bytes
from ..catalog import CatalogEntry
from .bundled_tools import SCOUT_CRUD_ENTRY_PATH, SCOUT_CRUD_SCHEMA_PATH


SCOUT_DEFINITION_VERSION = "1.4"
SCOUT_RESEARCH_SOURCE_PATH = "tools/cap_00000000-0000-4000-8000-000000000076/research.py"
SCOUT_RESEARCH_SCHEMA_PATH = "tools/cap_00000000-0000-4000-8000-000000000076/research.schema.json"
SCOUT_DISCOVERY_SOURCE_PATH = "tools/cap_00000000-0000-4000-8000-000000000074/discovery.py"
SCOUT_DISCOVERY_SCHEMA_PATH = "tools/cap_00000000-0000-4000-8000-000000000074/discovery.schema.json"
SCOUT_TAILORING_SOURCE_PATH = "tools/cap_00000000-0000-4000-8000-000000000075/tailoring.py"
SCOUT_TAILORING_SCHEMA_PATH = "tools/cap_00000000-0000-4000-8000-000000000075/tailoring.schema.json"


@dataclass(frozen=True)
class ScoutTemplateComparison:
    """Digest comparison; it never adopts or overwrites a user file."""

    unchanged: tuple[str, ...]
    customized: tuple[str, ...]
    missing: tuple[str, ...]
    local_only: tuple[str, ...]
    decision_required: bool
    updated: tuple[str, ...] = ()


def compare_scout_template(
    workpad: Path,
    *,
    source: Mapping[str, bytes] | None = None,
    baseline: Mapping[str, bytes] | None = None,
) -> ScoutTemplateComparison:
    """Compare editable copies with the bundled bytes before an update decision.

    A customized path is reported, never replaced.  This read-only comparison
    is intentionally separate from materialization/adoption so a package
    update cannot silently activate or overwrite user-owned source.
    """
    root = workpad.expanduser()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Scout template workpad is unavailable")
    # definition/ is generated metadata kept in the journal inventory; it is
    # not an editable working-copy member and must not create a perpetual
    # false-positive update.
    expected = {
        path: data
        for path, data in dict(source or scout_source_files()).items()
        if not path.startswith("definition/")
    }
    unchanged: list[str] = []
    customized: list[str] = []
    missing: list[str] = []
    updated: list[str] = []
    for path, data in sorted(expected.items()):
        candidate = root / path
        if not candidate.exists():
            missing.append(path)
        elif candidate.is_symlink() or not candidate.is_file():
            customized.append(path)
        elif candidate.read_bytes() == data:
            unchanged.append(path)
        elif baseline is not None and path in baseline and candidate.read_bytes() == baseline[path]:
            updated.append(path)
        else:
            customized.append(path)
    local_only: list[str] = []
    for root_name in ("README.md", "CHANGELOG.md", "gig.py", "goalgraphs", "ui", "tools"):
        candidate = root / root_name
        if not candidate.exists() or candidate.is_symlink():
            continue
        paths = (candidate,) if candidate.is_file() else sorted(item for item in candidate.rglob("*") if item.is_file() and not item.is_symlink())
        for item in paths:
            path = item.relative_to(root).as_posix()
            if path not in expected:
                local_only.append(path)
    return ScoutTemplateComparison(
        tuple(unchanged),
        tuple(customized),
        tuple(missing),
        tuple(local_only),
        bool(customized or missing or local_only or updated),
        tuple(updated),
    )


# Short aliases for callers that do not use the product-specific adjective.
compare_template_update = compare_scout_template


def decide_scout_template_update(comparison: ScoutTemplateComparison, decision: str) -> dict[str, object]:
    """Return an explicit adopt/defer decision without mutating source files."""
    if not isinstance(comparison, ScoutTemplateComparison):
        raise TypeError("comparison must come from compare_scout_template")
    if decision not in {"adopt", "defer"}:
        raise ValueError("template update decision must be adopt or defer")
    return {
        "decision": decision,
        "decision_required": comparison.decision_required,
        "customized": list(comparison.customized),
        "missing": list(comparison.missing),
        "local_only": list(comparison.local_only),
        "updated": list(comparison.updated),
        "automatic_replacement": False,
    }


decide_template_update = decide_scout_template_update


@dataclass(frozen=True)
class ScoutGraphSource:
    selector: str
    title: str
    purpose: str
    required_inputs: tuple[str, ...]
    optional_inputs: tuple[str, ...]
    outputs: tuple[str, ...]

    @property
    def instructions_path(self) -> str:
        return f"goalgraphs/{self.selector}.md"


# These are the five historical semantic selectors, not allocated graph UUIDs.
# Input names describe authoring requirements; they are not private content or
# permission to select all records. Exact input resolution belongs to the Plan.
SCOUT_GRAPHS = (
    ScoutGraphSource(
        "research-role", "Research a role",
        "Understand responsibilities, role variation and compensation with sources.",
        ("role",), ("geography", "research_revisions", "source_revisions"),
        ("research",),
    ),
    ScoutGraphSource(
        "find-jobs", "Find jobs",
        "Compare dated opportunities against explicitly selected preferences.",
        ("preferences",), ("candidate_evidence", "research_revisions", "postings"),
        ("discovery",),
    ),
    ScoutGraphSource(
        "tailor-application", "Tailor an application",
        "Create the requested documents from a posting and candidate evidence.",
        ("posting", "candidate_evidence", "requested_documents"),
        ("preferences", "experience_answers", "research_revisions", "prior_draft"),
        ("tailoring",),
    ),
    ScoutGraphSource(
        "record-application", "Record application progress",
        "Record an explicit user decision without submitting an application.",
        ("opportunity", "user_request", "event"),
        ("document_revisions", "notes", "superseded_event"),
        ("application_event", "history"),
    ),
    ScoutGraphSource(
        "prepare-interview", "Prepare for an interview",
        "Build evidence-backed preparation that can continue across sessions.",
        ("role", "stage_or_format"),
        ("posting", "candidate_evidence", "research_revisions", "prior_feedback"),
        ("preparation", "practice_questions", "gaps", "questions"),
    ),
)

# Proposal assessment is deliberately a separate operation from the historical
# Tailor graph.  Keep it outside ``SCOUT_GRAPHS`` so readers/tests that enumerate
# the original five selectors retain their byte and selector contract, while the
# candidate compiler can explicitly register this additional graph member.
SCOUT_PROPOSAL_GRAPH = ScoutGraphSource(
    "proposal-assessment", "Assess a saved opportunity",
    "Produce one private, evidence-backed fit assessment from explicitly selected inputs.",
    ("posting", "candidate_evidence"),
    ("preferences", "experience_answers", "research_revisions"),
    ("proposal_assessment",),
)
SCOUT_OPERATION_GRAPHS = (*SCOUT_GRAPHS, SCOUT_PROPOSAL_GRAPH)


def scout_graph_source(selector: str) -> ScoutGraphSource:
    """Resolve only a declared selector; never interpret it as a resource path."""
    for graph in SCOUT_OPERATION_GRAPHS:
        if selector == graph.selector:
            return graph
    raise ValueError("unknown Scout graph selector")


def scout_source_files() -> Mapping[str, bytes]:
    """Return an immutable, private-data-free inventory; never write or run it."""
    root = files("gigai.scout").joinpath("data")
    paths = (
        "README.md", "CHANGELOG.md", "gig.py", "goalgraphs/README.md",
        "ui/template.html", "ui/style.css",
        SCOUT_CRUD_ENTRY_PATH, SCOUT_CRUD_SCHEMA_PATH,
        *(graph.instructions_path for graph in SCOUT_OPERATION_GRAPHS),
    )
    source = {path: root.joinpath(path).read_bytes() for path in paths}
    # Keep the inert research source outside the closed CRUD capability root.
    # Package resources retain their original location; the Gig owns these
    # explicitly mapped copies, whose bytes are pinned by its output contract.
    research_resources = root.joinpath("tools/cap_00000000-0000-4000-8000-000000000076")
    source[SCOUT_RESEARCH_SOURCE_PATH] = research_resources.joinpath("research.py").read_bytes()
    source[SCOUT_RESEARCH_SCHEMA_PATH] = research_resources.joinpath("research.schema.json").read_bytes()
    discovery_resources = root.joinpath("tools/cap_00000000-0000-4000-8000-000000000074")
    source[SCOUT_DISCOVERY_SOURCE_PATH] = discovery_resources.joinpath("discovery.py").read_bytes()
    source[SCOUT_DISCOVERY_SCHEMA_PATH] = discovery_resources.joinpath("discovery.schema.json").read_bytes()
    tailoring_resources = root.joinpath("tools/cap_00000000-0000-4000-8000-000000000075")
    source[SCOUT_TAILORING_SOURCE_PATH] = tailoring_resources.joinpath("tailoring.py").read_bytes()
    source[SCOUT_TAILORING_SCHEMA_PATH] = tailoring_resources.joinpath("tailoring.schema.json").read_bytes()
    source["definition/scout-source.json"] = canonical_json_bytes({
        "schema_version": "1.0",
        "kind": "scout_authoring_source",
        "definition_version": SCOUT_DEFINITION_VERSION,
        "mode": "external_agent_recording",
        "graphs": [{
            "selector": graph.selector,
            "title": graph.title,
            "purpose": graph.purpose,
            "instructions": graph.instructions_path,
            "required_inputs": list(graph.required_inputs),
            "optional_inputs": list(graph.optional_inputs),
            "outputs": list(graph.outputs),
        } for graph in SCOUT_OPERATION_GRAPHS],
    })
    return MappingProxyType(source)


def scout_catalog_candidate() -> CatalogEntry:
    """Build an inspectable candidate, deliberately not a release-ready default.

    Do not add it to the live default inventory until domain behavior,
    initialization, and installed workflow evidence satisfy SCOUT-05/12.
    """
    return CatalogEntry(
        catalog_id="scout",
        definition_version=SCOUT_DEFINITION_VERSION,
        title="Scout",
        summary="Your private, customizable job-search workspace.",
        capabilities=("scout.external-recording",),
        files=scout_source_files(),
    )


def scout_candidate_inventory() -> tuple[CatalogEntry, ...]:
    """Explicit non-release inventory for candidate preparation tests/integration."""

    return (scout_catalog_candidate(),)


class ScoutInstallError(RuntimeError):
    """Raised when ``install_scout`` finds an unexpected instance state."""

    code = "scout_install_state_invalid"


@dataclass(frozen=True)
class ScoutInstallResult:
    """Outcome of binding, approving, and activating Scout for one project."""

    gig_id: str
    bound: bool
    approved: bool
    activated: bool


def install_scout(
    *,
    home_root: Path,
    requested_target: Path | None,
) -> ScoutInstallResult:
    """Bind, approve, and activate Scout for the bound project in one call.

    Idempotent: calling this again when Scout is already bound, approved, and
    active reports that and changes nothing.  This wraps the same
    ``initialize_defaults(inventory=scout_candidate_inventory())`` ->
    ``approve_offline`` -> ``select_active_workpad`` path the UAT runbook used
    by hand, as a normal function rather than a copy of that workaround.
    """

    # Deferred imports: default_init imports scout.materialization, which
    # imports this module, so importing default_init/lifecycle/workpad at
    # module load time here would be circular.
    from ..default_init import initialize_defaults
    from ..lifecycle import approve_offline
    from ..registry import open_project_registry
    from ..workpad import resolve_bound_project, select_active_workpad

    # initialize_defaults() only recovers a saved username from
    # <target>/.gigai/project.toml, which a non-git target never has; read
    # the already-bound owner from the registry directly so scout install
    # works for both target kinds without asking the operator to repeat
    # --username on a project `gigai init` already bound.
    bound_project = resolve_bound_project(home_root=home_root, requested_target=requested_target)
    resolved_home = home_root.expanduser().resolve(strict=False)
    registry, _ = open_project_registry(resolved_home, create=False)
    with registry.transaction() as transaction:
        owner = transaction.find_workspace_owner(bound_project.project_id)
    if owner is None:
        raise ScoutInstallError(
            "scout_install_owner_missing: the bound project has no saved workspace owner; "
            "run `gigai init --target <dir> --username <name>` first"
        )

    result = initialize_defaults(
        home_root=home_root,
        requested_target=requested_target,
        username=owner.username,
        inventory=scout_candidate_inventory(),
    )
    instance = next(row for row in result.instances if row.template_id == "scout")
    newly_bound = instance.status not in {"existing", "capability_review_required"}

    approved = False
    if instance.status == "approval_required":
        if instance.proposal_id is None:
            raise ScoutInstallError(
                "scout install requires a pending proposal but none was prepared"
            )
        approve_offline(
            home_root=home_root,
            requested_target=requested_target,
            gig_id=instance.gig_id,
            proposal_id=instance.proposal_id,
        )
        approved = True

    # The registry's active_workpads table is the source of truth for both
    # git targets (kept in sync with <target>/.gigai/project.toml) and
    # non-git targets (which have no project.toml at all); checking it here
    # keeps this idempotent for either target kind.
    with registry.transaction() as transaction:
        active = transaction.find_active_workpad(bound_project.project_id)
    already_active = active is not None and active.gig_id == instance.gig_id
    activated = not already_active
    if activated:
        select_active_workpad(
            home_root=home_root,
            requested_target=requested_target,
            gig_id=instance.gig_id,
            allow_semantic_state=True,
        )

    return ScoutInstallResult(
        gig_id=instance.gig_id,
        bound=newly_bound,
        approved=approved,
        activated=activated,
    )
