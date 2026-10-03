"""0110 docs publish: the root index always points at an existing target, even on an empty
gh-pages (first publish is VERSION=next with no alias), and the link checker passes."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "gigai-docs" / "scripts"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None or not SCRIPTS.exists(), reason="node or gigai-docs/scripts unavailable"
)

BASE = "/gigai/"


def _dist(tmp_path: Path, name: str) -> Path:
    dist = tmp_path / f"dist-{name}"
    (dist / "install").mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text('<a href="install/">install</a>')
    (dist / "install" / "index.html").write_text('<a href="../">home</a>')
    return dist


def _publish(site: Path, tmp_path: Path, version: str, *extra: str) -> None:
    subprocess.run(
        [NODE, str(SCRIPTS / "publish-version.mjs"), "--site", str(site),
         "--dist", str(_dist(tmp_path, version)), "--version", version, *extra],
        check=True, capture_output=True, text=True,
    )  # fmt: skip


def _check(site: Path) -> None:
    done = subprocess.run(
        [NODE, str(SCRIPTS / "check-links.mjs"), str(site), "--base", BASE],
        capture_output=True, text=True,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr


def _root_target(site: Path) -> str:
    html = (site / "index.html").read_text()
    return html.split('rel="canonical" href="')[1].split('"')[0]


def _versions(site: Path) -> dict[str, list[str]]:
    data = json.loads((site / "versions.json").read_text())
    return {v["version"]: v["aliases"] for v in data}


def test_first_publish_next_without_alias_then_release_then_next_again(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / ".nojekyll").write_text("")

    # (1) empty gh-pages + next, no alias: the root goes to next/, nothing dangles
    _publish(site, tmp_path, "next")
    assert _root_target(site) == "next/"
    assert not (site / "latest").exists()
    _check(site)

    # (2) the first release takes latest: the root moves to latest/
    _publish(site, tmp_path, "0.1.10", "--alias", "latest")
    assert _root_target(site) == "latest/"
    assert (site / "next" / "index.html").exists()
    assert (site / "0.1.10" / "index.html").exists()
    assert (site / "latest" / "install" / "index.html").exists()
    assert _versions(site) == {"0.1.10": ["latest"], "next": []}
    _check(site)

    # (3) publishing next again leaves latest alone
    _publish(site, tmp_path, "next")
    assert _root_target(site) == "latest/"
    assert _versions(site) == {"0.1.10": ["latest"], "next": []}
    _check(site)


def test_release_before_alias_prefers_release_over_next(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    _publish(site, tmp_path, "next")
    _publish(site, tmp_path, "0.1.9")  # a release filed without the latest alias
    assert _root_target(site) == "0.1.9/"
    _check(site)


def test_llms_txt_is_a_real_file_under_the_alias_and_at_the_root(tmp_path: Path) -> None:
    """0.1.10.8: plain text cannot be a redirect stub, so latest/llms.txt and the root llms.txt are copies of the
    newest release's own file; a rolling build (next) has its own and never replaces the root's."""

    site = tmp_path / "site"
    site.mkdir()

    def publish(version: str, *extra: str) -> None:
        dist = _dist(tmp_path, version)
        (dist / "llms.txt").write_text(f"# GigAI\n\n- [Start here](https://example.invalid/gigai/{version}/scout/agents/start/)\n")
        subprocess.run(
            [NODE, str(SCRIPTS / "publish-version.mjs"), "--site", str(site), "--dist", str(dist), "--version", version, *extra],
            check=True, capture_output=True, text=True,
        )  # fmt: skip

    publish("next")
    assert (site / "next" / "llms.txt").is_file() and not (site / "llms.txt").exists()

    publish("0.1.10.8", "--alias", "latest")
    own = (site / "0.1.10.8" / "llms.txt").read_text()
    assert "/gigai/0.1.10.8/scout/agents/start/" in own
    assert (site / "latest" / "llms.txt").read_text() == own
    assert (site / "llms.txt").read_text() == own

    # An older line's hotfix keeps no alias: the root and latest stay on the newest release.
    publish("0.1.9.2", "--alias", "latest")
    assert (site / "llms.txt").read_text() == own and (site / "latest" / "llms.txt").read_text() == own

    publish("next")
    assert (site / "llms.txt").read_text() == own
    _check(site)
