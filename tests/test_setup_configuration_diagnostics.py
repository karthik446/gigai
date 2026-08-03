from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from gigai.adapters import DeterministicAdapter, OfflineAdapterError
from gigai.config import (
    CredentialReference,
    MalformedConfigurationError,
    ReadOnlyConfigurationError,
    UnsupportedConfigurationVersionError,
    load_config,
    parse_config,
    render_config,
)
from gigai.credentials import CredentialReferenceError, validate_reference
from gigai.diagnostics import render_report_json, run_doctor
from gigai.setup import build_config, resolve_editor_argv, run_setup
from gigai.standard_pack import pack_digest, pack_path


def configured(tmp_path: Path, **overrides: object):
    values: dict[str, object] = {
        "home_root": tmp_path / "home",
        "workpad_root": tmp_path / "external-workpads",
        "editor_argv": ("/usr/bin/true", "--literal", "file with spaces"),
        "open_with_target": True,
        "credentials": (
            CredentialReference("provider", "environment", "GIGAI_PROVIDER_TOKEN"),
        ),
    }
    values.update(overrides)
    return build_config(**values)  # type: ignore[arg-type]


def test_config_round_trip_is_typed_canonical_and_reference_only(tmp_path: Path) -> None:
    config = configured(tmp_path)
    rendered = render_config(config)
    payload = __import__("tomllib").loads(rendered.decode("utf-8"))
    parsed = parse_config(payload)

    assert parsed == config
    assert rendered.endswith(b"\n")
    assert b"GIGAI_PROVIDER_TOKEN" in rendered
    assert b"provider-secret-canary" not in rendered
    assert payload["editor"]["argv"] == [
        "/usr/bin/true",
        "--literal",
        "file with spaces",
    ]


def test_config_rejects_unknown_duplicate_and_unsupported_contracts(tmp_path: Path) -> None:
    config = configured(tmp_path)
    payload = __import__("tomllib").loads(render_config(config).decode("utf-8"))
    payload["surprise"] = True
    with pytest.raises(MalformedConfigurationError, match=r"unknown=\['surprise'\]"):
        parse_config(payload)

    payload.pop("surprise")
    payload["schema_version"] = "99.0"
    with pytest.raises(UnsupportedConfigurationVersionError, match="no migration"):
        parse_config(payload)

    payload["schema_version"] = "1.0"
    payload["endpoints"].append(dict(payload["endpoints"][0]))
    with pytest.raises(MalformedConfigurationError, match="duplicate names"):
        parse_config(payload)


def test_setup_is_byte_idempotent_and_materializes_one_immutable_pack(tmp_path: Path) -> None:
    config = configured(tmp_path)
    first = run_setup(config)
    before = (config.home_root / "config.toml").read_bytes()
    second = run_setup(config)

    assert first.config_changed is True
    assert first.pack_changed is True
    assert second.config_changed is False
    assert second.pack_changed is False
    assert (config.home_root / "config.toml").read_bytes() == before
    assert load_config(config.home_root) == config
    assert pack_path(config.home_root).is_dir()
    assert len(tuple((config.home_root / "packs").rglob("standard-pack.json"))) == 1
    assert all(check.status == "PASS" for check in first.mount_checks)


def test_setup_rejects_read_only_config_before_materializing_missing_pack(
    tmp_path: Path,
) -> None:
    config = configured(tmp_path)
    config.home_root.mkdir(parents=True)
    path = config.home_root / "config.toml"
    path.write_bytes(render_config(config))
    path.chmod(0o400)

    with pytest.raises(ReadOnlyConfigurationError, match="no changes"):
        run_setup(config)

    assert not (config.home_root / "packs").exists()


def test_setup_preserves_alternate_mount_and_refuses_pack_corruption(tmp_path: Path) -> None:
    config = configured(tmp_path)
    run_setup(config)
    installed_pack = pack_path(config.home_root) / "standard-pack.json"
    installed_pack.chmod(0o600)
    installed_pack.write_text("corrupt\n", encoding="utf-8")
    config_before = (config.home_root / "config.toml").read_bytes()

    with pytest.raises(ValueError, match="do not match"):
        run_setup(config)

    assert load_config(config.home_root).workpad_root == config.workpad_root
    assert (config.home_root / "config.toml").read_bytes() == config_before


def test_doctor_is_offline_structured_and_warns_without_reading_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = configured(tmp_path)
    run_setup(config)
    monkeypatch.delenv("GIGAI_PROVIDER_TOKEN", raising=False)
    report = run_doctor(config.home_root)
    payload = json.loads(render_report_json(report))

    assert report.overall_status == "WARN"
    assert payload["schema_version"] == "1.0"
    assert payload["command"] == "doctor"
    assert {check["status"] for check in payload["checks"]} >= {"PASS", "WARN"}
    ids = {check["id"] for check in payload["checks"]}
    assert {
        "config.valid",
        "path.home",
        "path.workpad",
        "editor.resolved",
        "adapter.offline",
        "mount.atomic_replace",
        "mount.interprocess_lock",
    } <= ids
    assert "provider-secret-canary" not in render_report_json(report)


def test_doctor_fails_closed_on_missing_config_and_unavailable_mount(tmp_path: Path) -> None:
    missing = run_doctor(tmp_path / "not-configured")
    assert missing.overall_status == "FAIL"
    assert missing.checks[0].id == "config.valid"

    config = configured(tmp_path)
    run_setup(config)
    config.workpad_root.rmdir()
    report = run_doctor(config.home_root)
    statuses = {check.id: check.status for check in report.checks}
    assert statuses["path.workpad"] == "FAIL"
    assert statuses["mount.atomic_replace"] == "FAIL"
    assert statuses["mount.interprocess_lock"] == "FAIL"


def test_editor_and_credential_boundaries_reject_shell_and_raw_secret_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    with pytest.raises(ValueError, match="no editor"):
        resolve_editor_argv(None)
    monkeypatch.setenv("EDITOR", '/usr/bin/true --wait "file with spaces"')
    assert resolve_editor_argv(None) == (
        "/usr/bin/true",
        "--wait",
        "file with spaces",
    )
    assert resolve_editor_argv("/usr/bin/true", ("$(touch nope)",)) == (
        "/usr/bin/true",
        "$(touch nope)",
    )
    with pytest.raises(CredentialReferenceError):
        validate_reference(CredentialReference("provider", "secret-manager", "sk-raw-secret"))
    validate_reference(
        CredentialReference("provider", "secret-manager", "op://vault/provider/token")
    )


def test_deterministic_adapter_has_one_fixture_backed_success_path() -> None:
    adapter = DeterministicAdapter()
    assert adapter.invoke("doctor-probe") == "gigai-offline-ok"
    with pytest.raises(OfflineAdapterError, match="no deterministic response"):
        adapter.invoke("unregistered")
    assert pack_digest().startswith("sha256:")


def test_product_subprocesses_are_literal_argv_with_shell_disabled() -> None:
    product_root = Path(__file__).parents[1] / "src" / "gigai"
    calls: list[tuple[Path, ast.Call]] = []
    for path in product_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
                and node.func.attr == "run"
            ):
                calls.append((path, node))

    assert calls, "at least one real product subprocess boundary must be exercised"
    for path, call in calls:
        assert call.args and isinstance(call.args[0], ast.List), path
        shell = next((item.value for item in call.keywords if item.arg == "shell"), None)
        assert isinstance(shell, ast.Constant) and shell.value is False, path
