from __future__ import annotations

from fnmatch import fnmatchcase
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import re
import tomllib

import pytest
from click.testing import CliRunner

from gigai.canonical import EntityPrefix, generate_entity_id, validate_entity_id
from gigai.catalog import catalog_entries, package_bytes
from gigai.cli import cli
from gigai.package import inspect_package
from gigai.scout_template import (
    SCOUT_GRAPHS,
    scout_catalog_candidate,
    scout_graph_source,
    scout_source_files,
)


def test_five_accepted_selectors_and_non_funnel_inputs() -> None:
    assert tuple(graph.selector for graph in SCOUT_GRAPHS) == (
        "research-role", "find-jobs", "tailor-application",
        "record-application", "prepare-interview",
    )
    assert scout_graph_source("research-role").required_inputs == ("role",)
    assert "posting" in scout_graph_source("tailor-application").required_inputs
    assert "research_revisions" not in scout_graph_source("tailor-application").required_inputs
    assert "user_request" in scout_graph_source("record-application").required_inputs
    assert "application" not in scout_graph_source("prepare-interview").required_inputs


def test_native_cli_mount_does_not_replace_existing_g45_record_commands() -> None:
    runner = CliRunner()
    old = runner.invoke(cli, ["record", "create", "--help"])
    native = runner.invoke(cli, ["record", "native", "create", "--help"])
    assert old.exit_code == native.exit_code == 0
    assert "--content-family" in old.output
    assert "--content-file" in native.output


@pytest.mark.parametrize("selector", ["../README", "/etc/passwd", "jsl", "scout", "RESEARCH-ROLE", ""])
def test_selector_cannot_become_resource_path(selector: str) -> None:
    with pytest.raises(ValueError, match="unknown Scout graph selector"):
        scout_graph_source(selector)


def test_source_inventory_is_immutable_deterministic_and_authority_free() -> None:
    source = scout_source_files()
    assert source == scout_source_files()
    with pytest.raises(TypeError):
        source["README.md"] = b"changed"  # type: ignore[index]
    assert all(not PurePosixPath(path).is_absolute() and ".." not in PurePosixPath(path).parts for path in source)
    assert not any(path.startswith(("records/", "runs/", "reports/", "references/", "manifests/")) for path in source)
    assert "state.sqlite" not in source
    assert "gig.py" in source
    assert source["gig.py"] == (
        Path(__file__).parents[1] / "src/gigai/data/scout/gig.py"
    ).read_bytes()
    assert all(graph.instructions_path in source for graph in SCOUT_GRAPHS)
    # Availability of inert assets is deliberately not release eligibility.
    assert "scout" not in {entry.catalog_id for entry in catalog_entries()}


def test_markdown_links_resolve_inside_source_bundle() -> None:
    source = scout_source_files()
    for path, data in source.items():
        if not path.endswith(".md"):
            continue
        for target in re.findall(r"\]\(([^)]+)\)", data.decode("utf-8")):
            assert not target.startswith(("/", "http:", "https:", "javascript:"))
            candidate = PurePosixPath(path).parent / target
            assert candidate.as_posix() in source, (path, target)


def test_package_data_declares_every_inert_non_python_scout_asset() -> None:
    configuration = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text()
    )
    patterns = configuration["tool"]["setuptools"]["package-data"]["gigai.data"]
    for path in scout_source_files():
        # Python modules are discovered by setuptools; definition is generated.
        if path.endswith(".py") or path.startswith("definition/"):
            continue
        assert any(fnmatchcase(f"scout/{path}", pattern) for pattern in patterns), path


def test_candidate_uses_existing_inert_package_boundary(tmp_path: Path) -> None:
    entry = scout_catalog_candidate()
    inventory = package_bytes(entry)
    package_root = tmp_path / entry.package_id
    assert inventory == package_bytes(scout_catalog_candidate())
    for path, data in inventory.items():
        destination = package_root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    inspected = inspect_package(package_root)
    assert inspected.package_id == entry.package_id
    assert not (package_root / "state.sqlite").exists()
    assert (package_root / "gig.py").read_bytes() == scout_source_files()["gig.py"]


class _HTMLInventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.attributes: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attributes.extend(attrs)


def test_ui_source_is_local_readonly_and_separate_from_generated_report() -> None:
    source = scout_source_files()
    parsed = _HTMLInventory()
    parsed.feed(source["ui/template.html"].decode("utf-8"))
    assert not {"script", "iframe", "form", "object", "embed"}.intersection(parsed.tags)
    assert ("lang", "en") in parsed.attributes
    assert not any(key.lower().startswith("on") for key, _ in parsed.attributes)
    for key, value in parsed.attributes:
        if key in {"src", "href"}:
            assert value and (value.startswith("#") or f"ui/{value}" in source)
    assert "reports/scout/index.html" not in source


@pytest.mark.parametrize("prefix", [EntityPrefix.CHECKPOINT, EntityPrefix.RECEIPT, EntityPrefix.TASK_CONTEXT])
def test_parallel_protocol_ids_roundtrip_central_identity(prefix: EntityPrefix) -> None:
    identifier = generate_entity_id(prefix, is_persisted=lambda _candidate: False)
    validate_entity_id(identifier, expected_prefix=prefix)
