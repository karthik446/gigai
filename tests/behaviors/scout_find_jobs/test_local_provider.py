"""Persistence tests for the Scout wizard's local LLM model target."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from gigai import secrets_store
from gigai.config import Endpoint, config_path, load_config
from gigai.scout.local_provider import (
    CREDENTIAL_NAME,
    ENDPOINT_NAME,
    SECRET_NAME,
    TARGET_NAME,
    ensure_local_llm_target,
)
from gigai.setup import build_config, run_setup


def _configured_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    config = build_config(
        home_root=home,
        workpad_root=tmp_path / "workpad",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(Endpoint(name="offline", adapter="deterministic"),),
    )
    run_setup(config)
    return home


def test_local_provider_upserts_protocol_and_keeps_a_stored_key(tmp_path: Path) -> None:
    home = _configured_home(tmp_path)

    assert ensure_local_llm_target(
        home,
        "http://127.0.0.1:8000",
        "openai",
        "first-model",
        api_key_required=True,
        api_key="local-key-canary",
    )
    assert ensure_local_llm_target(
        home,
        "http://127.0.0.1:9000",
        "anthropic",
        "second-model",
        api_key_required=True,
    )

    config = load_config(home)
    endpoints = [endpoint for endpoint in config.endpoints if endpoint.name == ENDPOINT_NAME]
    targets = [target for target in config.model_targets if target.name == TARGET_NAME]
    assert endpoints == [
        Endpoint(
            name=ENDPOINT_NAME,
            adapter="anthropic_local",
            credential=CREDENTIAL_NAME,
            base_url="http://127.0.0.1:9000",
        )
    ]
    assert len(targets) == 1
    assert targets[0].model == "second-model"
    assert targets[0].max_output_tokens == 8192
    assert sum(endpoint.name == ENDPOINT_NAME for endpoint in config.endpoints) == 1
    assert sum(target.name == TARGET_NAME for target in config.model_targets) == 1
    assert secrets_store.get(SECRET_NAME, home_root=home) == "local-key-canary"
    assert "local-key-canary" not in config_path(home).read_text(encoding="utf-8")
    assert stat.S_IMODE(secrets_store.secrets_path(home).stat().st_mode) == 0o600


def test_local_provider_removes_key_and_reference_when_key_is_not_required(
    tmp_path: Path,
) -> None:
    home = _configured_home(tmp_path)
    ensure_local_llm_target(
        home,
        "http://127.0.0.1:8000",
        "openai",
        api_key_required=True,
        api_key="remove-me",
    )

    ensure_local_llm_target(
        home,
        "http://127.0.0.1:8000",
        "openai",
        api_key_required=False,
    )

    config = load_config(home)
    endpoint = next(item for item in config.endpoints if item.name == ENDPOINT_NAME)
    assert endpoint.credential is None
    assert all(item.name != CREDENTIAL_NAME for item in config.credentials)
    assert secrets_store.get(SECRET_NAME, home_root=home) is None


def test_local_provider_requires_a_key_before_mutating_config(tmp_path: Path) -> None:
    home = _configured_home(tmp_path)
    original = config_path(home).read_bytes()

    with pytest.raises(ValueError, match="server needs an API key"):
        ensure_local_llm_target(
            home,
            "http://127.0.0.1:8000",
            "openai",
            api_key_required=True,
        )

    assert config_path(home).read_bytes() == original
    assert secrets_store.get(SECRET_NAME, home_root=home) is None


def test_local_provider_rejects_non_loopback_url_without_mutating_config(
    tmp_path: Path,
) -> None:
    home = _configured_home(tmp_path)
    original = config_path(home).read_bytes()

    with pytest.raises(ValueError, match="explicit numeric loopback"):
        ensure_local_llm_target(
            home,
            "http://example.com:8000",
            "openai",
        )

    assert config_path(home).read_bytes() == original
