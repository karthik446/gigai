from __future__ import annotations

import json
from pathlib import Path
import subprocess

from click.testing import CliRunner

from gigai.catalog import catalog_entries, get_catalog_entry, package_bytes
from gigai.cli import cli
from gigai.package import inspect_package
from gigai.setup import build_config, run_setup


def _target(tmp_path: Path) -> tuple[Path, Path]:
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


def test_catalog_entries_have_deterministic_v4_package_ids_and_valid_bytes() -> None:
    expected = {
        "sync-references": "package_190960c2-7114-4c0e-8ccb-2dc2d1686f6e",
        "plan-a-research": "package_343d004a-a583-4421-ab86-5c666bfbe64d",
        "review-plan": "package_e0e90eb7-718c-49c1-9116-b5c88c91fbd9",
    }
    for entry in catalog_entries():
        assert entry.package_id == expected[entry.catalog_id]
        package = package_bytes(entry)
        assert package["package.json"] == package_bytes(entry)["package.json"]


def test_catalog_cli_lists_inspects_and_validates_offline(tmp_path: Path) -> None:
    listed = CliRunner().invoke(cli, ["catalog", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert {item["catalog_id"] for item in json.loads(listed.output)} == {
        "sync-references",
        "plan-a-research",
        "review-plan",
    }

    inspected = CliRunner().invoke(
        cli, ["catalog", "inspect", "sync-references", "--json"]
    )
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inspected.output)["package_id"] == get_catalog_entry(
        "sync-references"
    ).package_id

    validated = CliRunner().invoke(
        cli, ["catalog", "validate", "review-plan", "--json"]
    )
    assert validated.exit_code == 0, validated.output
    assert json.loads(validated.output) == {
        "authority_changed": False,
        "catalog_id": "review-plan",
        "definition_version": "1.0",
        "valid": True,
    }


def test_catalog_install_bootstraps_then_g41_adopts_without_authority_import(
    tmp_path: Path,
) -> None:
    home, target = _target(tmp_path)
    install = CliRunner().invoke(
        cli,
        [
            "catalog",
            "install",
            "sync-references",
            "--target",
            str(target),
            "--json",
        ],
    )
    assert install.exit_code == 0, install.output
    payload = json.loads(install.output)
    assert payload["authority_changed"] is False
    assert payload["bootstrap_next"] == "gigai init --adopt-package --confirm"
    package = target / ".gigai" / "packages" / payload["package_id"]
    assert inspect_package(package).content_digest == payload["package_digest"]
    assert not (target / ".gigai" / "project.toml").exists()

    adopted = CliRunner().invoke(
        cli,
        [
            "init",
            "--home",
            str(home),
            "--target",
            str(target),
            "--adopt-package",
            "--confirm",
            "--json",
        ],
    )
    assert adopted.exit_code == 0, adopted.output
    assert json.loads(adopted.output)["adopted"] is True


def test_catalog_install_refuses_second_package_without_replacement(tmp_path: Path) -> None:
    _, target = _target(tmp_path)
    first = CliRunner().invoke(
        cli,
        ["catalog", "install", "sync-references", "--target", str(target)],
    )
    assert first.exit_code == 0, first.output
    second = CliRunner().invoke(
        cli,
        ["catalog", "install", "plan-a-research", "--target", str(target)],
    )
    assert second.exit_code != 0
    assert "different package" in second.output
    assert len(list((target / ".gigai" / "packages").iterdir())) == 1


def test_catalog_install_refuses_symlinked_project_gigai_directory(tmp_path: Path) -> None:
    _, target = _target(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    (target / ".gigai").symlink_to(external, target_is_directory=True)

    result = CliRunner().invoke(
        cli,
        ["catalog", "install", "sync-references", "--target", str(target)],
    )

    assert result.exit_code != 0
    assert "symlink component" in result.output
    assert not any(external.iterdir())
