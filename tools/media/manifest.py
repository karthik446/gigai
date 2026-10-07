"""The image set's manifest, its size budget, and the copy into the docs site's fixed paths.

`manifest.json` sits beside the PNGs: one row per image (file, kind, scheme, alt text, size,
sha256). The docs and the README embed the files by these names, so the names are the contract:
`EXPECTED_FILES` lists them and a build that does not produce exactly that set fails.

Standard library only, except `optimise` (Pillow, from the `media` group).
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys

SCHEMA = "gigai-media-manifest:1"
MANIFEST_NAME = "manifest.json"
MAX_TOTAL_BYTES = 2 * 1024 * 1024  # the whole set, so the docs site and the repo stay small
REPO = Path(__file__).resolve().parents[2]
DOCS_MEDIA = REPO / "gigai-docs" / "public" / "media"

UI_NAMES: tuple[str, ...] = (
    "jobs", "job-pipeline", "job-resume", "job-picked", "job-left-out", "master", "master-lines",
    "answers", "stories", "pdf", "background",
)
TERMINAL_NAMES: tuple[str, ...] = (
    "terminal-new", "terminal-assessed", "terminal-yours", "terminal-answer", "terminal-story", "terminal-pdf",
    "terminal-master", "terminal-master-add",
)
EXPECTED_FILES: tuple[str, ...] = tuple(
    sorted([f"{name}-{scheme}.png" for name in UI_NAMES for scheme in ("light", "dark")] + [f"{name}.png" for name in TERMINAL_NAMES])
)


class ManifestError(RuntimeError):
    pass


def png_size(path: Path) -> tuple[int, int]:
    """(width, height) from the PNG header."""

    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ManifestError(f"{path.name} is not a PNG")
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def optimise(folder: Path) -> None:
    """Palette-quantise every PNG in place (a UI screenshot has few colours): about a third of the size."""

    from PIL import Image

    for path in sorted(folder.glob("*.png")):
        with Image.open(path) as opened:
            image = opened.convert("RGB")
        image.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(path, optimize=True)


def build_manifest(folder: Path, rows: Sequence[dict[str, str]], *, version: str, generated_on: str) -> dict[str, object]:
    """Describe the PNGs in `folder`; fails on a missing or extra file and on the size budget."""

    present = sorted(path.name for path in folder.glob("*.png"))
    if present != list(EXPECTED_FILES):
        missing = sorted(set(EXPECTED_FILES) - set(present))
        extra = sorted(set(present) - set(EXPECTED_FILES))
        raise ManifestError(f"the image set is not the expected one: missing {missing}, extra {extra}")
    by_file = {row["file"]: row for row in rows}
    images = []
    total = 0
    for name in EXPECTED_FILES:
        path = folder / name
        data = path.read_bytes()
        if not data:
            raise ManifestError(f"{name} is empty")
        row = by_file.get(name)
        if row is None or not row.get("alt"):
            raise ManifestError(f"{name} has no description")
        width, height = png_size(path)
        total += len(data)
        images.append({
            "file": name, "kind": row["kind"], "scheme": row["scheme"], "alt": row["alt"],
            "width": width, "height": height, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        })
    if total > MAX_TOTAL_BYTES:
        raise ManifestError(f"the image set is {total} bytes; the budget is {MAX_TOTAL_BYTES}")
    return {"schema": SCHEMA, "version": version, "generated_on": generated_on, "total_bytes": total, "images": images}


def write_manifest(folder: Path, manifest: dict[str, object]) -> Path:
    path = folder / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def check_manifest(folder: Path) -> dict[str, object]:
    """Read `folder`'s manifest and check every file against it (name set, bytes, sha256, budget)."""

    path = folder / MANIFEST_NAME
    if not path.is_file():
        raise ManifestError(f"no {MANIFEST_NAME} in {folder}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise ManifestError(f"unknown manifest schema {manifest.get('schema')!r}")
    images = manifest.get("images") or []
    listed = sorted(image["file"] for image in images)
    present = sorted(item.name for item in folder.glob("*.png"))
    if listed != list(EXPECTED_FILES) or present != listed:
        raise ManifestError(f"manifest and folder disagree with the expected set: listed {len(listed)}, present {len(present)}, expected {len(EXPECTED_FILES)}")
    total = 0
    for image in images:
        data = (folder / image["file"]).read_bytes()
        total += len(data)
        if len(data) != image["bytes"] or hashlib.sha256(data).hexdigest() != image["sha256"]:
            raise ManifestError(f"{image['file']} does not match the manifest; run `make media-publish`")
        if not image.get("alt"):
            raise ManifestError(f"{image['file']} has no description")
    if total != manifest.get("total_bytes") or total > MAX_TOTAL_BYTES:
        raise ManifestError(f"the image set is {total} bytes (manifest: {manifest.get('total_bytes')}, budget: {MAX_TOTAL_BYTES})")
    return manifest


def publish(source: Path, destination: Path = DOCS_MEDIA) -> list[str]:
    """Copy a checked image set to the docs site's fixed paths, replacing the previous release's."""

    check_manifest(source)
    destination.mkdir(parents=True, exist_ok=True)
    for stale in destination.glob("*.png"):
        stale.unlink()
    copied = []
    for name in (*EXPECTED_FILES, MANIFEST_NAME):
        shutil.copyfile(source / name, destination / name)
        copied.append(name)
    return copied


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "check":
        manifest = check_manifest(Path(args[1]))
        print(f"{args[1]}: {len(manifest['images'])} images, {manifest['total_bytes']} bytes, version {manifest['version']}")
        return 0
    if len(args) in (2, 3) and args[0] == "publish":
        copied = publish(Path(args[1]), *(Path(item) for item in args[2:]))
        print(f"published {len(copied)} files to {args[2] if len(args) == 3 else DOCS_MEDIA.relative_to(REPO)}")
        return 0
    print("usage: python -m tools.media.manifest check DIR | publish BUILD_DIR [DOCS_MEDIA_DIR]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ManifestError as error:
        print(f"media manifest: {error}", file=sys.stderr)
        raise SystemExit(1) from None
