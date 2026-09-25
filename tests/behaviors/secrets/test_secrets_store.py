from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from gigai import secrets_store


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_set_creates_file_at_mode_0600(tmp_path: Path) -> None:
    home = tmp_path / "home"
    secrets_store.set("FOO_API_KEY", "s3cr3t", home_root=home)

    path = secrets_store.secrets_path(home)
    assert path.exists()
    assert _mode(path) == 0o600
    assert secrets_store.get("FOO_API_KEY", home_root=home) == "s3cr3t"


def test_set_enforces_0600_even_if_file_pre_exists_world_readable(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    path = home / ".env"
    path.write_text("EXISTING=1\n")
    path.chmod(0o644)

    secrets_store.set("FOO_API_KEY", "value", home_root=home)

    assert _mode(path) == 0o600


def test_set_preserves_other_lines_and_comments(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    path = home / ".env"
    path.write_text("# a header comment\nOTHER_VAR=keep-me\n")
    path.chmod(0o600)

    secrets_store.set("NEW_KEY", "value", home_root=home)

    content = path.read_text()
    assert "# a header comment" in content
    assert "OTHER_VAR" in content
    assert secrets_store.get("OTHER_VAR", home_root=home) == "keep-me"
    assert secrets_store.get("NEW_KEY", home_root=home) == "value"


def test_remove_deletes_only_the_named_key(tmp_path: Path) -> None:
    home = tmp_path / "home"
    secrets_store.set("KEEP_ME", "1", home_root=home)
    secrets_store.set("DROP_ME", "2", home_root=home)

    secrets_store.remove("DROP_ME", home_root=home)

    assert secrets_store.get("DROP_ME", home_root=home) is None
    assert secrets_store.get("KEEP_ME", home_root=home) == "1"


def test_remove_stays_0600_and_is_a_noop_when_absent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    secrets_store.set("SOME_KEY", "1", home_root=home)

    secrets_store.remove("NEVER_SET", home_root=home)

    path = secrets_store.secrets_path(home)
    assert _mode(path) == 0o600
    assert secrets_store.get("SOME_KEY", home_root=home) == "1"


def test_get_returns_none_when_file_missing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert secrets_store.get("ANYTHING", home_root=home) is None


def test_names_set_reflects_current_contents(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert secrets_store.names_set(home_root=home) == set()

    secrets_store.set("A_KEY", "1", home_root=home)
    secrets_store.set("B_KEY", "2", home_root=home)
    assert secrets_store.names_set(home_root=home) == {"A_KEY", "B_KEY"}

    secrets_store.remove("A_KEY", home_root=home)
    assert secrets_store.names_set(home_root=home) == {"B_KEY"}


def test_secrets_path_respects_home_root() -> None:
    home = Path("/tmp/example-home")
    assert secrets_store.secrets_path(home) == home / ".env"


@pytest.mark.parametrize(
    "value",
    [
        "a$b${HOME}c",
        "val\"with'quotes",
        "val#with#hash",
        "val with spaces",
        "val=with=equals",
        "val-with-trailing-backslash\\",
    ],
    ids=[
        "dollar-and-braces",
        "quotes",
        "hash",
        "spaces",
        "equals",
        "trailing-backslash",
    ],
)
def test_set_and_get_round_trip_special_characters_without_interpolation(
    tmp_path: Path, value: str
) -> None:
    home = tmp_path / "home"
    secrets_store.set("SPECIAL_KEY", value, home_root=home)

    assert secrets_store.get("SPECIAL_KEY", home_root=home) == value


def test_empty_stored_value_is_treated_as_unset(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("EXA_API_KEY=\n")
    os.chmod(home / ".env", 0o600)

    assert secrets_store.get("EXA_API_KEY", home_root=home) is None
    assert secrets_store.names_set(home_root=home) == set()


def test_whitespace_only_stored_value_is_treated_as_unset(tmp_path: Path) -> None:
    home = tmp_path / "home"
    secrets_store.set("WS_KEY", "   ", home_root=home)

    assert secrets_store.get("WS_KEY", home_root=home) is None
    assert secrets_store.names_set(home_root=home) == set()


def test_set_stays_0600_under_a_permissive_process_umask(tmp_path: Path) -> None:
    home = tmp_path / "home"
    previous = os.umask(0o022)
    try:
        secrets_store.set("FOO_API_KEY", "value", home_root=home)
    finally:
        restored = os.umask(previous)
        assert restored == 0o022  # confirms set() restored our umask, not its own

    assert _mode(secrets_store.secrets_path(home)) == 0o600
