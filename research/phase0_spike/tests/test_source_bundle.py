from pathlib import Path
import zipfile

import pytest

from ..source_bundle import (
    BundleError,
    create_bundle,
    extract_bundle,
    verify_bundle,
)


def test_bundle_replays_exact_modified_and_untracked_bytes(tmp_path: Path) -> None:
    workpad = tmp_path / "workpad"
    workflow = workpad / "src/gigs/review/workflow.py"
    tool = workpad / "src/tools/checks.py"
    prompt = workpad / "src/gigs/review/prompt.md"
    project = workpad / "pyproject.toml"
    lock = workpad / "uv.lock"
    for path, value in {
        workflow: "DIRTY = True\n",
        tool: "def check(): return 'ok'\n",
        prompt: "Review carefully.\n",
        project: "[project]\nname='fixture'\n",
        lock: "version = 1\n",
    }.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    bundle = tmp_path / "run-source.zip"
    manifest = create_bundle(
        workpad=workpad,
        output=bundle,
        workflow_name="review",
        tool_names=["fixture/checks"],
        files_by_role={
            "workflow": [workflow],
            "tool": [tool],
            "resource": [prompt],
            "project": [project, lock],
        },
    )

    assert manifest["git"]["head"] is None
    assert {entry["role"] for entry in manifest["files"]} == {
        "workflow",
        "tool",
        "resource",
        "project",
    }
    verified = verify_bundle(bundle)
    replay = tmp_path / "replay"
    extract_bundle(bundle, replay)

    assert verified == manifest
    for source in (workflow, tool, prompt, project, lock):
        relative = source.relative_to(workpad)
        assert (replay / relative).read_bytes() == source.read_bytes()


def test_bundle_rejects_file_outside_workpad(tmp_path: Path) -> None:
    workpad = tmp_path / "workpad"
    workpad.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("unsafe = True\n")

    with pytest.raises(BundleError, match="outside workpad"):
        create_bundle(
            workpad=workpad,
            output=tmp_path / "bad.zip",
            workflow_name="review",
            files_by_role={"workflow": [outside]},
        )


def test_bundle_verification_detects_tampering(tmp_path: Path) -> None:
    workpad = tmp_path / "workpad"
    workflow = workpad / "workflow.py"
    workflow.parent.mkdir()
    workflow.write_text("VALUE = 1\n")
    bundle = tmp_path / "source.zip"
    create_bundle(
        workpad=workpad,
        output=bundle,
        workflow_name="review",
        files_by_role={"workflow": [workflow]},
    )

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(tampered, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "source/workflow.py":
                data = b"VALUE = 2\n"
            target.writestr(item, data)

    with pytest.raises(BundleError, match="mismatch"):
        verify_bundle(tampered)
