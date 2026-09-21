from __future__ import annotations

from pathlib import Path

import pytest

from gigai.canonical import digest_imported_bytes
from gigai.run_plan import (
    RunPlanError,
    approve_requirements_baseline,
    create_run_plan,
)

from tests.test_g43_provider_review import _fixture


@pytest.mark.parametrize(
    ("suffix", "media_type"),
    [
        (".md", "text/markdown"),
        (".markdown", "text/markdown"),
        (".txt", "text/plain"),
    ],
)
def test_ordinary_inputs_use_deterministic_media_and_exact_bytes(
    tmp_path: Path, suffix: str, media_type: str
) -> None:
    home, target, gig_id, _source = _fixture(tmp_path)
    source = tmp_path / f"ordinary{suffix}"
    data = "# Exact bytes\n\nNo MIME database required.\n".encode("utf-8")
    source.write_bytes(data)

    plan = create_run_plan(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        input_paths=(source,),
    )

    reference = plan.plan["inputs"][0]["snapshot_ref"]
    assert reference == {
        "path": f"run-plans/{plan.run_plan_id}/inputs/input_a.bin",
        "content_sha256": digest_imported_bytes(data),
        "media_type": media_type,
        "size_bytes": len(data),
    }


@pytest.mark.parametrize(
    ("artifact_class", "filenames_and_data"),
    [
        ("code", (("source.py", b"print('opaque bytes')\n\xff"),)),
        ("structured_data", (("payload.json", b'{"answer": 42}\n'),)),
        (
            "mixed",
            (
                ("source.py", b"print('mixed')\n"),
                ("payload.json", b'{"mixed": true}\n'),
            ),
        ),
    ],
)
def test_public_create_run_plan_accepts_declared_non_text_inputs(
    tmp_path: Path,
    artifact_class: str,
    filenames_and_data: tuple[tuple[str, bytes], ...],
) -> None:
    home, target, gig_id, _source = _fixture(tmp_path)
    sources = []
    for filename, data in filenames_and_data:
        source = tmp_path / filename
        source.write_bytes(data)
        sources.append(source)

    plan = create_run_plan(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        task_class="code_review",
        artifact_class=artifact_class,
        override_reason="operator declared the input artifact class",
        input_paths=tuple(sources),
    )

    assert plan.plan["classification"]["task_class"] == "code_review"
    assert plan.plan["classification"]["artifact_class"] == artifact_class
    assert len(plan.plan["inputs"]) == len(sources)
    for item, (_filename, data) in zip(plan.plan["inputs"], filenames_and_data, strict=True):
        reference = item["snapshot_ref"]
        assert reference["content_sha256"] == digest_imported_bytes(data)
        assert reference["size_bytes"] == len(data)
        assert reference["media_type"] == "application/octet-stream"


@pytest.mark.parametrize("suffix", [".markdown", ".txt"])
def test_baseline_approval_uses_deterministic_media(
    tmp_path: Path, suffix: str
) -> None:
    home, target, gig_id, _source = _fixture(tmp_path)
    baseline = tmp_path / f"requirements{suffix}"
    baseline.write_bytes(b"# Baseline\n")

    approval = approve_requirements_baseline(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        baseline_path=baseline,
        direct_operator_confirmed=True,
    )

    assert approval.approval["baseline_snapshot_ref"]["media_type"] == (
        "text/plain" if suffix == ".txt" else "text/markdown"
    )
    assert approval.approval["baseline_snapshot_ref"]["size_bytes"] == len(
        b"# Baseline\n"
    )
    assert approval.approval["baseline_snapshot_ref"]["content_sha256"] == digest_imported_bytes(
        b"# Baseline\n"
    )


def test_baseline_remains_text_only_for_non_text_suffix(
    tmp_path: Path,
) -> None:
    home, target, gig_id, _source = _fixture(tmp_path)
    source = tmp_path / "requirements.py"
    source.write_bytes(b"print('not a requirements baseline')\n")

    with pytest.raises(RunPlanError) as refused:
        approve_requirements_baseline(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            baseline_path=source,
            direct_operator_confirmed=True,
        )

    assert refused.value.code == "requirements_baseline_invalid"


@pytest.mark.parametrize("suffix", [".bin", ".html"])
def test_unknown_media_is_opaque_for_ordinary_but_rejected_by_baseline(
    tmp_path: Path, suffix: str
) -> None:
    home, target, gig_id, _source = _fixture(tmp_path)
    source = tmp_path / f"unknown{suffix}"
    source.write_bytes(b"still text")

    plan = create_run_plan(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        input_paths=(source,),
    )
    assert plan.plan["inputs"][0]["snapshot_ref"]["media_type"] == (
        "application/octet-stream"
    )

    with pytest.raises(RunPlanError) as baseline:
        approve_requirements_baseline(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            baseline_path=source,
            direct_operator_confirmed=True,
        )
    assert baseline.value.code == "requirements_baseline_invalid"


def test_invalid_utf8_is_preserved_for_text_only_baseline_lane(tmp_path: Path) -> None:
    home, target, gig_id, _source = _fixture(tmp_path)
    source = tmp_path / "binary.txt"
    source.write_bytes(b"valid prefix\xff\xfe")

    with pytest.raises(RunPlanError) as refused:
        approve_requirements_baseline(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            baseline_path=source,
            direct_operator_confirmed=True,
        )
    assert refused.value.code == "requirements_baseline_invalid"


def test_invalid_utf8_is_not_decoded_by_generic_ordinary_lane(tmp_path: Path) -> None:
    home, target, gig_id, _source = _fixture(tmp_path)
    source = tmp_path / "binary.txt"
    data = b"valid prefix\xff\xfe"
    source.write_bytes(data)

    plan = create_run_plan(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        input_paths=(source,),
    )

    reference = plan.plan["inputs"][0]["snapshot_ref"]
    assert reference["content_sha256"] == digest_imported_bytes(data)
    assert reference["size_bytes"] == len(data)
    assert reference["media_type"] == "text/plain"
