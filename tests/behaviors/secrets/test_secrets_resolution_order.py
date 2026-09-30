from __future__ import annotations

from pathlib import Path

import pytest

from gigai import secrets_store
from gigai.config import CredentialReference
from gigai.credentials import CredentialUnavailableError, resolve_reference_value


REFERENCE = CredentialReference(name="exa", kind="environment", reference="EXA_API_KEY")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("GIGAI_HOME", str(home))
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    return home


def test_env_wins_over_dot_env(monkeypatch: pytest.MonkeyPatch, _isolated_home: Path) -> None:
    secrets_store.set("EXA_API_KEY", "from-dot-env", home_root=_isolated_home)
    monkeypatch.setenv("EXA_API_KEY", "from-environment")

    assert resolve_reference_value(REFERENCE) == "from-environment"


def test_dot_env_used_when_env_absent(_isolated_home: Path) -> None:
    secrets_store.set("EXA_API_KEY", "from-dot-env", home_root=_isolated_home)

    assert resolve_reference_value(REFERENCE) == "from-dot-env"


def test_neither_present_raises_existing_error(_isolated_home: Path) -> None:
    with pytest.raises(CredentialUnavailableError) as excinfo:
        resolve_reference_value(REFERENCE)

    assert "exa" in str(excinfo.value)
    assert excinfo.value.code == "credential_unavailable"


def test_secret_manager_kind_still_refused(_isolated_home: Path) -> None:
    reference = CredentialReference(
        name="vault-secret", kind="secret-manager", reference="vault://path/to/secret"
    )

    with pytest.raises(CredentialUnavailableError):
        resolve_reference_value(reference)
