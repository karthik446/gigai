from __future__ import annotations

import ast
from pathlib import Path

from gigai.canonical import digest_imported_bytes
from gigai.config import CredentialReference
from research.s18_05.boundary import build_fixture_bundle, prepare_provider_boundary


def _credential() -> CredentialReference:
    return CredentialReference("provider", "environment", "S18_05_SYNTHETIC_TOKEN")


def _prepare(**kwargs):
    bundle, objects = build_fixture_bundle()
    credential = kwargs.pop("credential", _credential())
    network_allowed = kwargs.pop("network_allowed", True)
    offline = kwargs.pop("offline", False)
    return prepare_provider_boundary(
        bundle,
        objects,
        credential=credential,
        network_allowed=network_allowed,
        offline=offline,
        **kwargs,
    )


def test_only_explicitly_selected_allowed_references_enter_provider_input() -> None:
    decision = _prepare(selected_reference_ids=("ref-public",))
    assert decision.status == "eligible"
    assert "Public source claim." in (decision.provider_input or "")
    assert "Private unselected note." not in (decision.provider_input or "")
    assert "Repository snapshot bytes." not in (decision.provider_input or "")


def test_redaction_failure_blocks_before_provider_input_is_released() -> None:
    bundle, objects = build_fixture_bundle()
    objects["references/public.txt"] = b"Claim contains synthetic-secret.\n"
    bundle["references"][0]["content_sha256"] = digest_imported_bytes(objects["references/public.txt"])
    bundle["references"][0]["size_bytes"] = len(objects["references/public.txt"])
    decision = prepare_provider_boundary(
        bundle,
        objects,
        selected_reference_ids=("ref-public",),
        credential=_credential(),
        credential_values=("synthetic-secret",),
        redaction_values=(),
        network_allowed=True,
        offline=False,
    )
    assert decision.status == "blocked"
    assert decision.reason == "redaction_failed"
    assert decision.provider_input is None


def test_successful_redaction_keeps_credential_as_reference_only() -> None:
    bundle, objects = build_fixture_bundle()
    objects["references/public.txt"] = b"Claim contains synthetic-secret.\n"
    bundle["references"][0]["content_sha256"] = digest_imported_bytes(objects["references/public.txt"])
    bundle["references"][0]["size_bytes"] = len(objects["references/public.txt"])
    decision = prepare_provider_boundary(
        bundle,
        objects,
        selected_reference_ids=("ref-public",),
        credential=_credential(),
        credential_values=("synthetic-secret",),
        redaction_values=("synthetic-secret",),
        network_allowed=True,
        offline=False,
    )
    assert decision.status == "eligible"
    assert "synthetic-secret" not in (decision.provider_input or "")
    assert decision.credential_reference == {
        "name": "provider",
        "kind": "environment",
        "reference": "S18_05_SYNTHETIC_TOKEN",
    }
    assert "synthetic-secret" not in repr(decision.credential_reference)


def test_offline_network_denial_blocks_without_provider_input() -> None:
    decision = _prepare(selected_reference_ids=("ref-public",), offline=True)
    assert decision.status == "blocked"
    assert decision.reason == "network_denied"
    assert decision.provider_input is None


def test_unallowed_reference_and_invalid_credential_fail_closed() -> None:
    unallowed = _prepare(selected_reference_ids=("ref-not-allowed",))
    assert unallowed.reason == "reference_not_allowed"
    invalid = _prepare(
        selected_reference_ids=("ref-public",),
        credential=CredentialReference("provider", "environment", "not-valid"),
    )
    assert invalid.reason == "credential_reference_invalid"


def test_selected_reference_digest_and_path_invariants_fail_closed() -> None:
    bundle, objects = build_fixture_bundle()
    objects["references/public.txt"] = b"tampered bytes\n"
    digest_mismatch = prepare_provider_boundary(
        bundle,
        objects,
        selected_reference_ids=("ref-public",),
        credential=_credential(),
        network_allowed=True,
        offline=False,
    )
    assert digest_mismatch.reason == "reference_digest_mismatch"

    unsafe_bundle, safe_objects = build_fixture_bundle()
    unsafe_bundle["references"][0]["path"] = "../outside.txt"
    unsafe = prepare_provider_boundary(
        unsafe_bundle,
        safe_objects,
        selected_reference_ids=("ref-public",),
        credential=_credential(),
        network_allowed=True,
        offline=False,
    )
    assert unsafe.reason == "unsafe_reference_path"


def test_research_boundary_has_no_effectful_provider_imports() -> None:
    source = Path(__file__).parents[1].joinpath("research/s18_05/boundary.py").read_text()
    tree = ast.parse(source)
    imported = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    }
    imported.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert not imported.intersection({"socket", "subprocess", "httpx", "urllib", "requests"})
