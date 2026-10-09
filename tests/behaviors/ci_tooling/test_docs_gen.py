"""0110-011 D1: the public docs site's generated pages stay fresh, core never links into a Gig,
and the site is not shipped in the package."""

from __future__ import annotations

import shutil
import sys
import tomllib
from pathlib import Path

import pytest

from tools import docs_gen, release_check

REPO = Path(__file__).resolve().parents[3]


def _need(path: str) -> Path:
    found = REPO / path
    if not found.exists():
        pytest.skip(f"{path} is excluded from the offline container build context")
    return found


def _run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["docs_gen.py", *argv])
    return docs_gen.main()


@pytest.fixture
def repo_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch repo root holding only what docs_gen reads, with docs_gen pointed at it."""
    _need("gigai-docs")
    for name in ("CHANGELOG.md", "pyproject.toml"):
        shutil.copy(REPO / name, tmp_path / name)
    docs = tmp_path / "gigai-docs" / "src" / "content"
    shutil.copytree(REPO / "gigai-docs" / "src" / "content", docs)
    monkeypatch.setattr(docs_gen, "REPO", tmp_path)
    monkeypatch.setattr(docs_gen, "DOCS", docs / "docs")
    monkeypatch.setattr(docs_gen, "GENERATED", docs / "generated")
    return tmp_path


def test_committed_generated_pages_are_current(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _need("gigai-docs")
    assert _run(monkeypatch, "--check") == 0, capsys.readouterr().err


def test_generated_pages_are_marked_and_cover_the_cli_and_api() -> None:
    files = {path.relative_to(docs_gen.REPO).as_posix(): text for path, text in docs_gen.outputs().items()}
    assert set(files) == {
        "gigai-docs/src/content/docs/reference/cli.md",
        "gigai-docs/src/content/docs/scout/reference/cli.md",
        "gigai-docs/src/content/docs/changelog.md",
        "gigai-docs/src/content/generated/scout-openapi.json",
    }
    for rel, text in files.items():
        if rel.endswith(".md"):
            assert docs_gen.MARK in text, rel
    core, scout = (files[f"gigai-docs/src/content/docs/{p}.md"] for p in ("reference/cli", "scout/reference/cli"))
    assert "## `gigai agent-context`" in core and "## `gigai scout " not in core
    assert "## `gigai scout " in scout and "## `gigai agent-context`" not in scout
    assert "(hidden)" not in core + scout
    version = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    assert f'"version": "{version}"' in files["gigai-docs/src/content/generated/scout-openapi.json"]


def test_check_fails_on_a_stale_or_missing_page_and_write_fixes_it(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(monkeypatch, "--check") == 0
    cli_page = repo_copy / "gigai-docs/src/content/docs/reference/cli.md"
    cli_page.write_text(cli_page.read_text() + "\nhand edit\n")
    (repo_copy / "gigai-docs/src/content/generated/scout-openapi.json").unlink()
    assert _run(monkeypatch, "--check") == 1
    err = capsys.readouterr().err
    assert "reference/cli.md is stale" in err and "scout-openapi.json is stale" in err
    assert _run(monkeypatch) == 0
    assert _run(monkeypatch, "--check") == 0


def test_changelog_change_makes_the_check_fail(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    changelog = repo_copy / "CHANGELOG.md"
    changelog.write_text(changelog.read_text() + "\n## 99.0.0\n\n- new\n")
    assert _run(monkeypatch, "--check") == 1


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)


def test_core_pages_link_into_scout_only_from_the_index(tmp_path: Path) -> None:
    _write(tmp_path, "index.md", "[Scout](scout/) and [quickstart](scout/quickstart/)\n")
    _write(tmp_path, "scout/quickstart.md", "[install](../install/) [privacy](../privacy/)\n")
    _write(tmp_path, "install.md", "[web](https://example.com/scout/) [self](../concepts/architecture/) [top](#x)\n")
    assert docs_gen.boundary_violations(tmp_path) == []

    _write(tmp_path, "install.md", "see [Scout](../scout/quickstart/)\n")
    _write(tmp_path, "concepts/architecture.md", "[deep](../../scout/privacy/)\n")
    _write(tmp_path, "reference/cli.md", "[root](/scout/)\n")
    bad = docs_gen.boundary_violations(tmp_path)
    assert [line.split(":")[0] for line in bad] == ["concepts/architecture.md", "install.md", "reference/cli.md"]


def test_the_real_docs_tree_respects_the_boundary() -> None:
    _need("gigai-docs")
    assert docs_gen.boundary_violations() == []


def test_docs_site_is_not_part_of_the_package() -> None:
    manifest = _need("MANIFEST.in").read_text()
    assert "prune gigai-docs" in manifest.splitlines()
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["setuptools"]
    assert pyproject["packages"]["find"]["where"] == ["src"]  # only src/ is packaged; gigai-docs/ is top-level
    assert not any("gigai-docs" in str(value) for value in pyproject["package-data"].values())
    assert release_check._NOT_SHIPPED == ("gigai-docs/",)  # verify-artifacts rejects it in wheel and sdist


def test_docs_workflow_deploys_nothing_until_the_operator_enables_it() -> None:
    text = _need(".github/workflows/docs.yml").read_text()
    publish = text[text.index("\n  publish:") :]
    # the only job that can push is gated on the repo variable and never runs for a PR
    assert "github.event_name != 'pull_request' && vars.DOCS_PUBLISH == 'true'" in publish
    assert text.count("contents: write") == 1 and text.index("contents: write") > text.index("\n  publish:")
    assert "permissions:\n  contents: read" in text.split("\njobs:")[0]
    assert "secrets." not in text
    assert "branches: [main, 'karthik446/gigai-v*']" in text
    assert text.count("git push origin HEAD:gh-pages") == 1 and text.index("git push origin HEAD:gh-pages") > text.index("\n  publish:")
    assert "docs-gh-pages" in publish
