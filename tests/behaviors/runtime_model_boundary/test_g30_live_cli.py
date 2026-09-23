"""Opt-in operator UAT for real local model CLI targets."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gigai.config import ConfigurationError, load_config
from gigai.model_discovery import probe_target_readiness


pytestmark = pytest.mark.g30_live


def test_named_local_targets_are_usable_for_g30_uat() -> None:
    if os.environ.get("GIGAI_G30_UAT") != "1":
        pytest.skip("set GIGAI_G30_UAT=1 to invoke real local model CLIs")

    home = Path(os.environ.get("GIGAI_G30_HOME", Path.home() / ".gigai"))
    target_names = tuple(
        name.strip()
        for name in os.environ.get(
            "GIGAI_G30_TARGETS", "codex-default,claude-default"
        ).split(",")
        if name.strip()
    )
    if not target_names:
        pytest.fail("GIGAI_G30_TARGETS must name at least one configured target")

    try:
        config = load_config(home)
    except (ConfigurationError, OSError) as exc:
        pytest.fail(f"cannot load UAT config at {home}: {exc}")

    configured = {target.name for target in config.model_targets}
    missing = sorted(set(target_names) - configured)
    if missing:
        pytest.fail(f"UAT targets are not configured: {', '.join(missing)}")

    failures: list[str] = []
    for target_name in target_names:
        readiness = probe_target_readiness(config, target_name)
        if readiness.readiness != "usable":
            reason = readiness.reason or "no reason reported"
            failures.append(f"{target_name}: {readiness.readiness} ({reason})")

    if failures:
        pytest.fail("; ".join(failures))
