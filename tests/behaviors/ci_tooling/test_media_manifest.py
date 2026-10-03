"""0110-049: the release screenshots' manifest, the committed set under gigai-docs/public/media/,
the docs embeds and the non-blocking release job.

Pure: standard library only, so it runs without the `media` dependency group.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import struct
import tomllib
import zlib

import pytest

from tools.media import manifest, terminal, ui_shots

REPO = Path(__file__).resolve().parents[3]


def _need(relative: str) -> Path:
    """The offline container image ships no gigai-docs/ and no .github/ (containers/debian-offline/Dockerfile)."""

    path = REPO / relative
    if not path.exists():
        pytest.skip(f"{relative} is not in this checkout (the offline container's build context)")
    return path


def _png(width: int = 4, height: int = 2, pad: int = 0) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"tEXt", b"pad\x00" + b"x" * pad) + chunk(b"IEND", b"")
    )


def _set(folder: Path) -> list[dict[str, str]]:
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for number, name in enumerate(manifest.EXPECTED_FILES):
        (folder / name).write_bytes(_png(pad=number))
        rows.append({"file": name, "kind": "terminal" if name.startswith("terminal-") else "ui", "scheme": "dark", "alt": f"What {name} shows."})
    return rows


def _built(folder: Path) -> dict[str, object]:
    built = manifest.build_manifest(folder, _set(folder), version="9.9.9", generated_on="2026-10-03")
    manifest.write_manifest(folder, built)
    return built


def test_the_expected_files_are_what_the_shot_lists_produce() -> None:
    assert tuple(shot.name for shot in ui_shots.SHOTS) == manifest.UI_NAMES
    assert ui_shots.SCHEMES == ("light", "dark")
    assert ui_shots.VIEWPORT == {"width": 1280, "height": 800}, "desktop width only"
    source = Path(terminal.__file__).read_text(encoding="utf-8")
    assert tuple(re.findall(r'Frame\("(terminal-[a-z]+)"', source)) == manifest.TERMINAL_NAMES
    assert len(manifest.EXPECTED_FILES) == 2 * len(manifest.UI_NAMES) + len(manifest.TERMINAL_NAMES) == 24


def test_ui_shots_wait_on_test_ids_never_on_visible_text() -> None:
    source = Path(ui_shots.__file__).read_text(encoding="utf-8")
    assert "text=" not in source and "has_text" not in source and "get_by_text" not in source
    used = set(re.findall(r'_tid\("([a-z-]+)"\)', source))
    ui_source = "\n".join(path.read_text(encoding="utf-8") for path in sorted((REPO / "src" / "gigai" / "scout" / "ui" / "src").rglob("*.js*")))
    for test_id in used:
        # The time chips' ids are built in postingsModel.js: `time-chip-${window}`.
        literal = f'"{test_id}"' in ui_source or (test_id.startswith("time-chip-") and "`time-chip-${window}`" in ui_source)
        assert literal, f"data-testid {test_id} is gone from the UI source"
    assert {"job-row", "time-chip-new", "assess-these", "approval-dialog", "step-timeline", "scout-label-chip", "background-panel", "approvals-list"} <= used


def test_manifest_round_trip(tmp_path: Path) -> None:
    built = _built(tmp_path)
    assert built["schema"] == manifest.SCHEMA and built["version"] == "9.9.9"
    images = built["images"]
    assert [image["file"] for image in images] == list(manifest.EXPECTED_FILES)  # type: ignore[index,union-attr]
    assert images[0]["width"] == 4 and images[0]["height"] == 2  # type: ignore[index]
    assert built["total_bytes"] == sum((tmp_path / name).stat().st_size for name in manifest.EXPECTED_FILES)
    assert manifest.check_manifest(tmp_path) == json.loads((tmp_path / manifest.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest.main(["check", str(tmp_path)]) == 0


def test_a_missing_extra_empty_or_undescribed_image_fails(tmp_path: Path) -> None:
    rows = _set(tmp_path)
    (tmp_path / manifest.EXPECTED_FILES[0]).unlink()
    with pytest.raises(manifest.ManifestError, match="missing"):
        manifest.build_manifest(tmp_path, rows, version="1", generated_on="d")
    rows = _set(tmp_path)
    (tmp_path / "phone-narrow.png").write_bytes(_png())
    with pytest.raises(manifest.ManifestError, match="extra"):
        manifest.build_manifest(tmp_path, rows, version="1", generated_on="d")
    (tmp_path / "phone-narrow.png").unlink()
    with pytest.raises(manifest.ManifestError, match="no description"):
        manifest.build_manifest(tmp_path, rows[1:], version="1", generated_on="d")
    (tmp_path / manifest.EXPECTED_FILES[3]).write_bytes(b"")
    with pytest.raises(manifest.ManifestError, match="empty"):
        manifest.build_manifest(tmp_path, rows, version="1", generated_on="d")


def test_the_size_budget_is_two_megabytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert manifest.MAX_TOTAL_BYTES == 2 * 1024 * 1024
    rows = _set(tmp_path)
    monkeypatch.setattr(manifest, "MAX_TOTAL_BYTES", 100)
    with pytest.raises(manifest.ManifestError, match="budget"):
        manifest.build_manifest(tmp_path, rows, version="1", generated_on="d")


def test_a_changed_image_no_longer_matches_its_manifest(tmp_path: Path) -> None:
    _built(tmp_path)
    (tmp_path / manifest.EXPECTED_FILES[5]).write_bytes(_png(pad=999))
    with pytest.raises(manifest.ManifestError, match="does not match the manifest"):
        manifest.check_manifest(tmp_path)


def test_publish_replaces_the_previous_set_at_the_same_paths(tmp_path: Path) -> None:
    source, destination = tmp_path / "build", tmp_path / "docs-media"
    _built(source)
    destination.mkdir()
    (destination / "old-name.png").write_bytes(_png())
    (destination / manifest.EXPECTED_FILES[0]).write_bytes(_png(pad=5000))
    copied = manifest.publish(source, destination)
    assert sorted(copied) == sorted([*manifest.EXPECTED_FILES, manifest.MANIFEST_NAME])
    assert sorted(path.name for path in destination.iterdir()) == sorted(copied)
    assert manifest.check_manifest(destination)["version"] == "9.9.9"


def test_publish_refuses_a_set_that_fails_its_check(tmp_path: Path) -> None:
    source = tmp_path / "build"
    _built(source)
    (source / manifest.EXPECTED_FILES[0]).write_bytes(_png(pad=7))
    with pytest.raises(manifest.ManifestError):
        manifest.publish(source, tmp_path / "docs-media")
    assert not (tmp_path / "docs-media").exists()


def test_the_committed_set_matches_its_manifest_and_the_budget() -> None:
    committed = manifest.check_manifest(_need("gigai-docs/public/media"))
    assert committed["total_bytes"] < manifest.MAX_TOTAL_BYTES  # type: ignore[operator]
    for image in committed["images"]:  # type: ignore[union-attr]
        assert image["width"] == 1280, f"{image['file']} is not desktop width"
        assert image["height"] == 800 or image["kind"] == "terminal"
        assert image["alt"].endswith(".")


def test_the_docs_embed_only_committed_images_by_a_relative_path() -> None:
    page = _need("gigai-docs/src/content/docs/scout/agents.md").read_text(encoding="utf-8")
    assert "## Screenshots" in page
    sources = re.findall(r'<img [^>]*src="([^"]+)"', page)
    assert len(sources) >= 8
    for source in sources:
        # Relative to /<base>/scout/agents/, so it works under any versioned docs base.
        assert source.startswith("../../media/"), source
        assert source.removeprefix("../../media/") in manifest.EXPECTED_FILES
    assert "](/media/" not in page and 'src="/media/' not in page, "an absolute path would skip the docs base"
    for name in manifest.UI_NAMES:
        light, dark = f"../../media/{name}-light.png", f"../../media/{name}-dark.png"
        assert (light in sources) == (dark in sources), f"{name}: embed both schemes or neither"


def test_media_tooling_is_a_dependency_group_not_a_runtime_dependency_or_extra() -> None:
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    group = " ".join(project["dependency-groups"]["media"])
    assert "playwright" in group and "pillow" in group
    runtime = " ".join(project["project"]["dependencies"]).lower()
    extras = " ".join(" ".join(items) for items in project["project"]["optional-dependencies"].values()).lower()
    for name in ("playwright", "pillow"):
        assert name not in runtime and name not in extras


def _job(text: str, name: str) -> str:
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, f"no {name} job"
    return match.group(1)


def test_the_release_media_job_never_blocks_a_release() -> None:
    text = _need(".github/workflows/release.yml").read_text(encoding="utf-8")
    media = _job(text, "media")
    assert "continue-on-error: true" in media
    assert "needs: [preflight, github-release]" in media, "it runs after the GitHub Release exists"
    assert "run: make media" in media
    assert media.index("run: make media") < media.index("run: gh release upload"), "nothing is uploaded before the privacy gate ran"
    for gate in ("preflight", "tag", "publish-pypi", "github-release", "advance-main", "verify-pypi", "post-release-compatibility"):
        needs = re.search(r"needs: (.*)", _job(text, gate))
        assert needs is None or "media" not in needs.group(1), f"{gate} must not wait for media"
    # Nothing waits on it, and the docs job does not take its images from it.
    assert all("media" not in needs for needs in re.findall(r"^    needs: (.*)$", text, re.MULTILINE))
    assert "media_artifact" not in text


def test_the_docs_publish_the_committed_images() -> None:
    # docs.yml builds the tag's tree; gigai-docs/public/ is copied into the site as it is.
    text = _need(".github/workflows/docs.yml").read_text(encoding="utf-8")
    assert "working-directory: gigai-docs" in text and "npm run build" in text
    assert "media" not in text, "the docs workflow needs no step for the images"
