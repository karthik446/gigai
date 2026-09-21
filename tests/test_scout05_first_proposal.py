from __future__ import annotations

import json
from pathlib import Path
import subprocess
import uuid

from click.testing import CliRunner
import pytest

import gigai.lifecycle as lifecycle
from gigai.canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from gigai.catalog import CatalogEntry
from gigai.cli import cli
from gigai.default_init import initialize_defaults
from gigai.journal import read_committed_artifact
from gigai.lifecycle import (
    LifecycleError,
    _build_proposal_artifacts,
    approve_offline,
    propose_first_graph_set_offline,
    propose_graph_set_offline,
    validate_graph_set_proposal_workpad,
)
from gigai.project_binding import load_project_binding
from gigai.run_plan import _review_contract
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import select_active_workpad


def _ref(path: str, data: bytes, media_type: str = "application/json") -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(data),
        "media_type": media_type,
        "size_bytes": len(data),
    }


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    home.mkdir()
    target.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main", target], check=True
    )
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    return home, target


def _entry(name: str) -> CatalogEntry:
    return CatalogEntry(
        catalog_id=name,
        definition_version="1.0",
        title=name,
        summary="synthetic release-eligible default",
        capabilities=("fixture.prepare",),
        files={"definition/gig.md": f"# {name}\n".encode("utf-8")},
    )


def _write_first_definition(root: Path, *, gig_id: str, project_id: str) -> Path:
    """Create source members only; this helper never approves a predecessor."""

    root.mkdir()
    source_artifacts = _build_proposal_artifacts(
        gig_id=gig_id,
        project_id=project_id,
        proposal_id="gp_00000000-0000-4000-8000-000000000001",
        name="fixture-source",
        commission="fixture source only",
        model_target="fixture",
        model_output="fixture",
        uuid_factory=uuid.uuid4,
    )
    for artifact in source_artifacts:
        if artifact.path == "manifests/gig-proposal.json":
            continue
        path = root / artifact.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(artifact.content)

    budget = {
        "max_model_calls": 2,
        "max_tool_calls": 0,
        "max_tokens": 8000,
        "max_cost": "0.50",
        "currency": "USD",
        "max_wall_time_ms": 300000,
        "max_parallel_goals": 1,
    }
    graph = parse_json_bytes((root / "manifests/goal-graph.json").read_bytes())
    assert isinstance(graph, dict)
    graph["aggregate_budget"] = budget
    graph_data = canonical_json_bytes(graph)
    (root / "manifests/goal-graph.json").write_bytes(graph_data)
    contracts = {
        "input": canonical_json_bytes(
            {
                "schema_version": "1.0",
                "kind": "run_input_contract",
                "gig_id": gig_id,
                "fields": ["primary"],
            }
        ),
        "output": canonical_json_bytes(
            {
                "schema_version": "1.0",
                "kind": "run_output_contract",
                "gig_id": gig_id,
                "fields": ["report"],
            }
        ),
        "references": canonical_json_bytes(
            {
                "schema_version": "1.0",
                "kind": "permitted_reference_contract",
                "gig_id": gig_id,
                "fields": ["explicit"],
            }
        ),
        "evaluation": canonical_json_bytes(
            {
                "schema_version": "1.0",
                "kind": "evaluation_contract",
                "gig_id": gig_id,
                "fields": ["deterministic"],
            }
        ),
        "completion": canonical_json_bytes(
            {
                "schema_version": "1.0",
                "kind": "completion_evidence_contract",
                "gig_id": gig_id,
                "fields": ["proposal-validation"],
            }
        ),
    }
    for name, content in contracts.items():
        (root / f"{name}.json").write_bytes(content)
    review = parse_json_bytes(
        _review_contract(
            "contract_00000000-0000-4000-8000-000000000099",
            "2026-09-09T00:00:00Z",
        )
    )
    assert isinstance(review, dict)
    review["evaluator_plan"][0]["stage"] = "deterministic"
    review_data = canonical_json_bytes(review)
    (root / "review.json").write_bytes(review_data)

    definition = {
        "schema_version": "1.0",
        "gig_id": gig_id,
        "name": "first-graph-set",
        "commission": "Stage a validated first Graph Set without approval.",
        "gig_document": _ref("gig.md", (root / "gig.md").read_bytes(), "text/markdown"),
        "creation_manifest": _ref(
            "manifests/creation-manifest.json",
            (root / "manifests/creation-manifest.json").read_bytes(),
        ),
        "graphs": [
            {
                "graph_id": "career",
                "purpose": "A structural first-Graph-Set fixture.",
                "aliases": ["role"],
                "routing_summary": "Explicit career selection.",
                "goal_graph": _ref("manifests/goal-graph.json", graph_data),
                "input_contract": _ref("input.json", contracts["input"]),
                "output_contract": _ref("output.json", contracts["output"]),
                "permitted_reference_contract": _ref(
                    "references.json", contracts["references"]
                ),
                "effect_policy": ["write_workpad"],
                "capability_requirements": ["gigai.offline"],
                "provider_eligibility": {"providers": ["deterministic"]},
                "budget": budget,
                "review_contract": _ref("review.json", review_data),
                "evaluation_contract": _ref("evaluation.json", contracts["evaluation"]),
                "completion_evidence_contract": _ref(
                    "completion.json", contracts["completion"]
                ),
            }
        ],
        "shared_policy": {
            "effects": ["write_workpad"],
            "required_capability_ids": ["gigai.offline"],
            "provider_eligibility": {"providers": ["deterministic"]},
            "budget": budget,
        },
    }
    path = root / "definition.json"
    path.write_bytes(canonical_json_bytes(definition))
    return path


def _journal_commits(workpad: Path) -> tuple[str, ...]:
    return tuple(
        subprocess.run(
            ["git", "-C", str(workpad), "log", "--format=%H"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    )


def _publish_second_proposal_publisher(
    workpad: Path, proposal_path: Path, proposal_bytes: bytes
) -> None:
    """Create an intentionally conflicting committed artifact in a disposable workpad."""
    proposal_path.write_bytes(proposal_bytes)
    relative = proposal_path.relative_to(workpad).as_posix()
    subprocess.run(["git", "-C", str(workpad), "add", "--", relative], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(workpad),
            "commit",
            "--quiet",
            "--no-verify",
            "-m",
            "synthetic conflicting proposal publisher",
        ],
        check=True,
    )


def _rewrite_graph_member(definition: Path, *, path: str) -> None:
    payload = json.loads(definition.read_text(encoding="utf-8"))
    payload["graphs"][0]["goal_graph"]["path"] = path
    definition.write_bytes(canonical_json_bytes(payload))


def _bound_defaults(tmp_path: Path) -> tuple[Path, Path, str, str, Path, Path]:
    home, target = _setup(tmp_path)
    result = initialize_defaults(
        home_root=home,
        requested_target=target,
        username="owner",
        inventory=(_entry("first"), _entry("second")),
    )
    by_template = {item.template_id: item.gig_id for item in result.instances}
    workpads = {
        path.name: path for path in (tmp_path / "workpads").glob("projects/*/gigs/*")
    }
    return (
        home,
        target,
        result.package.project_id,
        by_template["first"],
        workpads[by_template["first"]],
        workpads[by_template["second"]],
    )


def test_first_graph_set_is_source_backed_pending_then_explicitly_approved(
    tmp_path: Path,
) -> None:
    home, target, project_id, first_gig, first_workpad, second_workpad = _bound_defaults(
        tmp_path
    )
    definition = _write_first_definition(
        tmp_path / "first-source", gig_id=first_gig, project_id=project_id
    )
    assert load_project_binding(target).active_gig_id is None

    proposal = propose_first_graph_set_offline(
        home_root=home,
        requested_target=target,
        definition_path=definition,
        gig_id=first_gig,
    )
    pending = parse_json_bytes(
        (first_workpad / "manifests/gig-proposal.json").read_bytes()
    )
    assert isinstance(pending, dict)
    assert pending["kind"] == "create"
    assert pending["base_gig_version"] is None
    assert pending["parent_proposal_id"] is None
    assert validate_graph_set_proposal_workpad(first_workpad, pending).valid
    graph_set_path = pending["graph_set"]["path"]
    graph_set_bytes, _commit = read_committed_artifact(
        workpad=first_workpad,
        project_id=project_id,
        gig_id=first_gig,
        path=graph_set_path,
    )
    assert graph_set_bytes == (first_workpad / graph_set_path).read_bytes()
    identity_path = str(Path(str(graph_set_path)).parent / "first-proposal-inputs.json")
    identity_bytes, _identity_commit = read_committed_artifact(
        workpad=first_workpad,
        project_id=project_id,
        gig_id=first_gig,
        path=identity_path,
    )
    identity = parse_json_bytes(identity_bytes)
    assert isinstance(identity, dict)
    assert identity["kind"] == "first_graph_set_source_identity"
    assert identity["definition_sha256"] == digest_imported_bytes(
        definition.read_bytes()
    )
    assert not (second_workpad / "manifests/gig-proposal.json").exists()
    assert load_project_binding(target).active_gig_id is None

    # A different explicit active Gig cannot redirect approval for this proposal.
    second_gig = second_workpad.name
    select_active_workpad(
        home_root=home,
        requested_target=target,
        gig_id=second_gig,
        allow_semantic_state=True,
    )
    approved = approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=proposal.proposal_id,
        gig_id=first_gig,
    )
    assert approved.gig_id == first_gig
    assert approved.version == 1
    assert load_project_binding(target).active_gig_id == second_gig


@pytest.mark.parametrize(
    ("case", "member_path", "message"),
    (
        ("traversal", "../escape.json", "path is unsafe"),
        ("absolute", "/tmp/escape.json", "path is unsafe"),
        ("backslash", "manifests\\goal-graph.json", "path is unsafe"),
        ("member_symlink", "linked.json", "path is redirected"),
        ("parent_symlink", "linked-dir/goal-graph.json", "path is redirected"),
    ),
)
def test_first_graph_set_refuses_unsafe_or_redirected_source_members(
    tmp_path: Path, case: str, member_path: str, message: str
) -> None:
    home, target, project_id, gig_id, workpad, _other_workpad = _bound_defaults(tmp_path)
    source_root = tmp_path / f"unsafe-{case}"
    definition = _write_first_definition(
        source_root, gig_id=gig_id, project_id=project_id
    )
    if case == "member_symlink":
        (source_root / "linked.json").symlink_to(
            source_root / "manifests" / "goal-graph.json"
        )
    elif case == "parent_symlink":
        (source_root / "linked-dir").symlink_to(
            source_root / "manifests", target_is_directory=True
        )
    _rewrite_graph_member(definition, path=member_path)
    before = _journal_commits(workpad)
    with pytest.raises(LifecycleError, match=message):
        propose_first_graph_set_offline(
            home_root=home,
            requested_target=target,
            definition_path=definition,
            gig_id=gig_id,
        )
    assert _journal_commits(workpad) == before
    assert not (workpad / "manifests/gig-proposal.json").exists()


def test_first_graph_set_refuses_symlinked_definition_before_resolution(
    tmp_path: Path,
) -> None:
    home, target, project_id, gig_id, workpad, _other_workpad = _bound_defaults(tmp_path)
    source_root = tmp_path / "symlinked-definition"
    definition = _write_first_definition(
        source_root, gig_id=gig_id, project_id=project_id
    )
    real_definition = source_root / "real-definition.json"
    definition.rename(real_definition)
    definition.symlink_to(real_definition)
    before = _journal_commits(workpad)
    with pytest.raises(
        LifecycleError, match="graph-set definition must be one regular local JSON file"
    ):
        propose_first_graph_set_offline(
            home_root=home,
            requested_target=target,
            definition_path=definition,
            gig_id=gig_id,
        )
    assert _journal_commits(workpad) == before


def test_first_graph_set_rechecks_original_definition_identity_inside_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target, project_id, gig_id, workpad, _other_workpad = _bound_defaults(tmp_path)
    source_root = tmp_path / "definition-race"
    definition = _write_first_definition(
        source_root, gig_id=gig_id, project_id=project_id
    )
    original_bytes = definition.read_bytes()
    replacement = source_root / "replacement-definition.json"
    real_run_with_journal_writer = lifecycle.run_with_journal_writer

    def inject_redirection(*, operation, **kwargs):
        def wrapped(writer):
            replacement.write_bytes(original_bytes)
            definition.unlink()
            definition.symlink_to(replacement)
            return operation(writer)

        return real_run_with_journal_writer(operation=wrapped, **kwargs)

    monkeypatch.setattr(lifecycle, "run_with_journal_writer", inject_redirection)
    before = _journal_commits(workpad)
    with pytest.raises(
        LifecycleError, match="graph-set definition must be one regular local JSON file"
    ):
        propose_first_graph_set_offline(
            home_root=home,
            requested_target=target,
            definition_path=definition,
            gig_id=gig_id,
        )
    assert _journal_commits(workpad) == before
    definition.unlink()
    definition.write_bytes(original_bytes)


@pytest.mark.parametrize(
    "document_bytes",
    (b"# invalid\x00markdown\n", b"\xef\xbb\xbf# invalid bom\n"),
)
def test_first_graph_set_maps_noncanonical_gig_document_to_lifecycle_error(
    tmp_path: Path, document_bytes: bytes
) -> None:
    home, target, project_id, gig_id, workpad, _other_workpad = _bound_defaults(tmp_path)
    source_root = tmp_path / "noncanonical-document"
    definition = _write_first_definition(
        source_root, gig_id=gig_id, project_id=project_id
    )
    (source_root / "gig.md").write_bytes(document_bytes)
    payload = json.loads(definition.read_text(encoding="utf-8"))
    payload["gig_document"] = _ref("gig.md", document_bytes, "text/markdown")
    definition.write_bytes(canonical_json_bytes(payload))
    before = _journal_commits(workpad)
    with pytest.raises(
        LifecycleError, match="first Graph Set Gig document is not canonical Markdown"
    ):
        propose_first_graph_set_offline(
            home_root=home,
            requested_target=target,
            definition_path=definition,
            gig_id=gig_id,
        )
    assert _journal_commits(workpad) == before


def test_first_graph_set_replays_exact_pending_publication_and_refuses_changed_source(
    tmp_path: Path,
) -> None:
    home, target, project_id, gig_id, workpad, _other_workpad = _bound_defaults(tmp_path)
    definition = _write_first_definition(
        tmp_path / "retry-source", gig_id=gig_id, project_id=project_id
    )

    def interrupt(step: str) -> None:
        if step == "after_first_graph_set_proposed":
            raise RuntimeError("injected after publication")

    with pytest.raises(RuntimeError, match="after publication"):
        propose_first_graph_set_offline(
            home_root=home,
            requested_target=target,
            definition_path=definition,
            gig_id=gig_id,
            observer=interrupt,
        )
    first = parse_json_bytes((workpad / "manifests/gig-proposal.json").read_bytes())
    assert isinstance(first, dict)
    replay = propose_first_graph_set_offline(
        home_root=home,
        requested_target=target,
        definition_path=definition,
        gig_id=gig_id,
    )
    assert replay.proposal_id == first["proposal_id"]
    identity_path = workpad / (
        Path(str(first["graph_set"]["path"])).parent
        / "first-proposal-inputs.json"
    )
    sealed_identity_bytes = identity_path.read_bytes()
    commits = subprocess.run(
        ["git", "-C", str(workpad), "log", "--format=%H", "--", "manifests/gig-proposal.json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert len(commits) == 1

    proposal_path = workpad / "manifests/gig-proposal.json"
    proposal_bytes = proposal_path.read_bytes()
    identity_bytes = sealed_identity_bytes
    journal_before_tamper = _journal_commits(workpad)

    proposal_path.write_bytes(proposal_bytes + b"\n")
    with pytest.raises(LifecycleError, match="working copy differs from committed authority"):
        propose_first_graph_set_offline(
            home_root=home,
            requested_target=target,
            definition_path=definition,
            gig_id=gig_id,
        )
    assert _journal_commits(workpad) == journal_before_tamper
    proposal_path.write_bytes(proposal_bytes)

    identity_path.write_bytes(identity_bytes + b"\n")
    with pytest.raises(LifecycleError, match="source identity differs from committed authority"):
        propose_first_graph_set_offline(
            home_root=home,
            requested_target=target,
            definition_path=definition,
            gig_id=gig_id,
        )
    assert _journal_commits(workpad) == journal_before_tamper
    identity_path.write_bytes(identity_bytes)
    assert identity_path.read_bytes() == sealed_identity_bytes

    identity_path.unlink()
    with pytest.raises(LifecycleError, match="source identity differs from committed authority"):
        propose_first_graph_set_offline(
            home_root=home,
            requested_target=target,
            definition_path=definition,
            gig_id=gig_id,
        )
    assert _journal_commits(workpad) == journal_before_tamper
    identity_path.write_bytes(identity_bytes)
    assert identity_path.read_bytes() == sealed_identity_bytes

    changed = json.loads(definition.read_text(encoding="utf-8"))
    changed["commission"] = "Different sealed source intent."
    definition.write_bytes(canonical_json_bytes(changed))
    with pytest.raises(LifecycleError, match="changed source inputs"):
        propose_first_graph_set_offline(
            home_root=home,
            requested_target=target,
            definition_path=definition,
            gig_id=gig_id,
        )
    assert (workpad / "manifests/gig-proposal.json").read_bytes() == canonical_json_bytes(first)


def test_first_graph_set_refuses_committed_multi_publisher_without_republication(
    tmp_path: Path,
) -> None:
    home, target, project_id, gig_id, workpad, _other_workpad = _bound_defaults(tmp_path)
    definition = _write_first_definition(
        tmp_path / "publisher-conflict-source", gig_id=gig_id, project_id=project_id
    )
    propose_first_graph_set_offline(
        home_root=home,
        requested_target=target,
        definition_path=definition,
        gig_id=gig_id,
    )
    proposal_path = workpad / "manifests/gig-proposal.json"
    original = proposal_path.read_bytes()
    _publish_second_proposal_publisher(workpad, proposal_path, original + b"\n")
    before = _journal_commits(workpad)
    assert len(before) >= 2
    with pytest.raises(
        LifecycleError, match="existing first Graph Set proposal cannot be authenticated"
    ):
        propose_first_graph_set_offline(
            home_root=home,
            requested_target=target,
            definition_path=definition,
            gig_id=gig_id,
        )
    assert _journal_commits(workpad) == before


def test_graph_set_cli_exposes_first_proposal_and_explicit_gig_approval(tmp_path: Path) -> None:
    home, target, project_id, gig_id, workpad, _other_workpad = _bound_defaults(tmp_path)
    definition = _write_first_definition(
        tmp_path / "cli-source", gig_id=gig_id, project_id=project_id
    )
    runner = CliRunner()
    proposed = runner.invoke(
        cli,
        [
            "graph-set",
            "propose",
            "--first",
            "--gig",
            gig_id,
            "--definition",
            str(definition),
            "--home",
            str(home),
            "--target",
            str(target),
            "--json",
        ],
    )
    assert proposed.exit_code == 0, proposed.output
    proposal_id = json.loads(proposed.output)["proposal_id"]
    approved = runner.invoke(
        cli,
        [
            "approve",
            proposal_id,
            "--gig",
            gig_id,
            "--home",
            str(home),
            "--target",
            str(target),
            "--json",
        ],
    )
    assert approved.exit_code == 0, approved.output
    assert json.loads(approved.output)["version"] == 1
    assert not load_project_binding(target).active_gig_id
    assert (workpad / "manifests/active-gig-version.json").is_file()


def test_approved_gig_graph_set_amendment_path_remains_available(tmp_path: Path) -> None:
    home, target = _setup(tmp_path)
    from gigai.lifecycle import create_offline

    initialize_target(home_root=home, requested_target=target)
    created = create_offline(
        home_root=home, requested_target=target, name="amend-source", open_editor=False
    )
    approve_offline(
        home_root=home, requested_target=target, proposal_id=created.proposal_id
    )
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    definition = _write_first_definition(
        tmp_path / "amend-source",
        gig_id=created.gig_id,
        project_id=workpad.parent.parent.name,
    )
    # The first-only fields are deliberately not accepted by amendment staging.
    source = json.loads(definition.read_text(encoding="utf-8"))
    for key in ("schema_version", "name", "commission", "gig_document", "creation_manifest"):
        source.pop(key)
    definition.write_bytes(canonical_json_bytes(source))
    amended = propose_graph_set_offline(
        home_root=home,
        requested_target=target,
        definition_path=definition,
        gig_id=created.gig_id,
    )
    assert amended.gig_id == created.gig_id
