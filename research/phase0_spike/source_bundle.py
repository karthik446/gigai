"""Create and verify a minimal exact-byte source bundle for a Python workflow."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import platform
import subprocess
from typing import Iterable
import zipfile


class BundleError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(workpad: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=workpad,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _relative_file(workpad: Path, path: Path) -> tuple[Path, bytes]:
    resolved_root = workpad.resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise BundleError(f"{path} is outside workpad {workpad}") from exc
    if not resolved.is_file():
        raise BundleError(f"source file does not exist: {path}")
    return relative, resolved.read_bytes()


def create_bundle(
    *,
    workpad: Path,
    output: Path,
    files_by_role: dict[str, Iterable[Path]],
    workflow_name: str,
    tool_names: Iterable[str] = (),
    gigai_version: str = "phase0-spike",
) -> dict:
    workpad = workpad.resolve()
    entries: list[dict] = []
    payloads: dict[str, bytes] = {}
    seen: set[Path] = set()
    for role, paths in files_by_role.items():
        for path in paths:
            relative, data = _relative_file(workpad, path)
            if relative in seen:
                continue
            seen.add(relative)
            archive_path = f"source/{relative.as_posix()}"
            payloads[archive_path] = data
            entries.append(
                {
                    "path": relative.as_posix(),
                    "archive_path": archive_path,
                    "role": role,
                    "size": len(data),
                    "sha256": _sha256(data),
                }
            )

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "workpad": str(workpad),
        "workflow": workflow_name,
        "tools": sorted(tool_names),
        "git": {
            "head": _git(workpad, "rev-parse", "HEAD"),
            "status_short": _git(workpad, "status", "--short"),
        },
        "runtime": {
            "gigai_version": gigai_version,
            "python": platform.python_version(),
        },
        "files": sorted(entries, key=lambda item: item["path"]),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(
            "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
        for archive_path, data in sorted(payloads.items()):
            bundle.writestr(archive_path, data)
    return manifest


def verify_bundle(path: Path) -> dict:
    with zipfile.ZipFile(path) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        for entry in manifest["files"]:
            data = bundle.read(entry["archive_path"])
            if len(data) != entry["size"]:
                raise BundleError(f"size mismatch for {entry['path']}")
            if _sha256(data) != entry["sha256"]:
                raise BundleError(f"hash mismatch for {entry['path']}")
    return manifest


def extract_bundle(path: Path, destination: Path) -> dict:
    manifest = verify_bundle(path)
    destination = destination.resolve()
    with zipfile.ZipFile(path) as bundle:
        for entry in manifest["files"]:
            relative = Path(entry["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise BundleError(f"unsafe bundle path {relative}")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundle.read(entry["archive_path"]))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workpad", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workflow-name", required=True)
    parser.add_argument("--workflow", type=Path, action="append", default=[])
    parser.add_argument("--tool", type=Path, action="append", default=[])
    parser.add_argument("--resource", type=Path, action="append", default=[])
    parser.add_argument("--project", type=Path, action="append", default=[])
    args = parser.parse_args()
    manifest = create_bundle(
        workpad=args.workpad,
        output=args.output,
        workflow_name=args.workflow_name,
        files_by_role={
            "workflow": args.workflow,
            "tool": args.tool,
            "resource": args.resource,
            "project": args.project,
        },
    )
    verify_bundle(args.output)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
