"""``gigai.scout.target_resolution`` — the shared Scout target resolver.

uat-bug-002: every ``gigai scout ...`` command that takes ``--target``
resolves it through this one module. uat-bug-017 reduced the order to:
(1) ``--target``; (2) ``<home>/scout`` always, created and bound when
missing. The cwd and a Scout project registered elsewhere are never used
(``test_scout_home_default.py`` covers the commands and the notice).
These tests exercise the resolver both directly (for cases awkward to set up
through the CLI, like the username fallback order) and through
``click.testing.CliRunner`` against the installed commands (matching the
sibling Scout CLI test files), always against a temp ``GIGAI_HOME`` --
never the operator's real ``~/.gigai``.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout.target_resolution import (
    home_scout_target,
    resolve_scout_target,
)


def _setup(tmp_path: Path) -> Path:
    """Non-interactive ``gigai setup`` only -- no ``gigai init``, no bound target."""

    home = tmp_path / "home"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "setup",
            "--non-interactive",
            "--home",
            str(home),
            "--workpad-root",
            str(tmp_path / "workpads"),
            "--editor",
            "/usr/bin/true",
            "--credential-ref",
            "provider=environment:GIGAI_PROVIDER_TOKEN",
            "--endpoint",
            "remote=openai_api:provider:https://api.example.test",
            "--model-target",
            "remote=remote:smoke-test",
            "--create-model-target",
            "remote",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    return home


def _init(home: Path, target: Path, *, username: str) -> None:
    target.mkdir(parents=True, exist_ok=True)
    result = CliRunner().invoke(
        cli, ["init", "--home", str(home), "--target", str(target), "--username", username, "--json"]
    )
    assert result.exit_code == 0, result.output


def _scout_install(home: Path, target: Path) -> None:
    result = CliRunner().invoke(
        cli, ["scout", "install", "--home", str(home), "--target", str(target), "--json"]
    )
    assert result.exit_code == 0, result.output


# --- Explicit --target is returned unchanged -------------------------------


def test_explicit_target_short_circuits_everything_else(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    explicit = tmp_path / "wherever"
    resolved = resolve_scout_target(home_root=home, requested_target=explicit)
    assert resolved == explicit


# --- A registered cwd is never used (was step 2 before uat-bug-017) ---------


def test_cwd_bound_project_is_not_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # uat-bug-017: was "the cwd's registered project is used"; now <home>/scout, always.
    home = _setup(tmp_path)
    target = tmp_path / "target"
    _init(home, target, username="cwd-test")
    monkeypatch.chdir(target)

    resolved = resolve_scout_target(home_root=home, requested_target=None)
    assert resolved == home_scout_target(home)


def test_cwd_registered_scout_folder_is_not_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # uat-bug-017: was "cwd = a registered Scout folder -> that one is used"; now <home>/scout.
    home = _setup(tmp_path)
    target = tmp_path / "target"
    _init(home, target, username="cwd-scout-test")
    _scout_install(home, target)
    monkeypatch.chdir(target)

    resolved = resolve_scout_target(home_root=home, requested_target=None)
    assert resolved == home_scout_target(home)

    # And from a subfolder of it.
    subfolder = target / "nested"
    subfolder.mkdir()
    monkeypatch.chdir(subfolder)
    resolved_nested = resolve_scout_target(home_root=home, requested_target=None)
    assert resolved_nested == home_scout_target(home)


# --- A Scout project elsewhere is never reused (was step 3) ----------------


def test_one_existing_scout_project_is_not_reused_from_an_unrelated_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # uat-bug-017: was "the one existing Scout project is reused"; now it is only named in the notice.
    home = _setup(tmp_path)
    target = tmp_path / "target"
    _init(home, target, username="reuse-test")
    _scout_install(home, target)

    unrelated = tmp_path / "unrelated-cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    notices: list[str] = []
    resolved = resolve_scout_target(home_root=home, requested_target=None, notify=notices.append)
    assert resolved == home_scout_target(home)
    assert len(notices) == 1
    assert str(target.resolve()) in notices[0]


def test_two_existing_scout_projects_are_each_named_once_and_never_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # uat-bug-017: was a scout_target_ambiguous error; the default no longer depends on them.
    home = _setup(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    _init(home, first, username="two-scout-test")
    _scout_install(home, first)
    _init(home, second, username="two-scout-test")
    _scout_install(home, second)

    unrelated = tmp_path / "unrelated-cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    notices: list[str] = []
    resolved = resolve_scout_target(home_root=home, requested_target=None, notify=notices.append)
    assert resolved == home_scout_target(home)
    assert len(notices) == 2
    assert str(first.resolve()) in notices[0]
    assert str(second.resolve()) in notices[1]
    assert all("--target" in notice for notice in notices)

    again: list[str] = []
    resolve_scout_target(home_root=home, requested_target=None, notify=again.append)
    assert again == []


def test_a_bound_but_scout_less_project_does_not_count_as_an_existing_scout_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`gigai init` alone (no `scout install`) doesn't make a project an
    earlier Scout project: nothing to name in the notice.
    """

    home = _setup(tmp_path)
    target = tmp_path / "target"
    _init(home, target, username="not-scout-test")

    unrelated = tmp_path / "unrelated-cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    notices: list[str] = []
    resolved = resolve_scout_target(home_root=home, requested_target=None, notify=notices.append)
    assert resolved == home_scout_target(home)
    assert resolved != target.resolve()
    assert notices == []


# --- Create <home>/scout, and its identity vs <home>/workpads ---------------


def test_creates_and_binds_home_scout_from_a_fresh_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _setup(tmp_path)
    unbound = tmp_path / "empty-cwd"
    unbound.mkdir()
    monkeypatch.chdir(unbound)

    resolved = resolve_scout_target(home_root=home, requested_target=None)
    assert resolved == home_scout_target(home)
    assert resolved.is_dir()

    # A second resolution reuses the same, now-registered target (not a
    # second creation) -- idempotent.
    resolved_again = resolve_scout_target(home_root=home, requested_target=None)
    assert resolved_again == resolved


def test_home_scout_does_not_collide_with_the_workpad_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _setup(tmp_path)
    unbound = tmp_path / "empty-cwd"
    unbound.mkdir()
    monkeypatch.chdir(unbound)

    scout_target = resolve_scout_target(home_root=home, requested_target=None)

    workpad_root = tmp_path / "workpads"
    assert scout_target != workpad_root
    assert not scout_target.is_relative_to(workpad_root)
    assert not workpad_root.is_relative_to(scout_target)
    assert scout_target == home / "scout"


def test_on_created_callback_reports_the_created_path_and_username(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _setup(tmp_path)
    unbound = tmp_path / "empty-cwd"
    unbound.mkdir()
    monkeypatch.chdir(unbound)

    calls: list[tuple[Path, str]] = []
    resolved = resolve_scout_target(
        home_root=home,
        requested_target=None,
        requested_username="callback-test",
        on_created=lambda path, username: calls.append((path, username)),
    )
    assert calls == [(resolved, "callback-test")]

    # uat-bug-017: was "on_created fires again"; an already-bound <home>/scout is not created twice.
    calls.clear()
    again = resolve_scout_target(
        home_root=home,
        requested_target=None,
        on_created=lambda p, u: calls.append((p, u)),
    )
    assert again == resolved
    assert calls == []


# --- Username fallback order (operator-approved) ----------------------------


def _owner_username_for(home: Path, target: Path) -> str:
    from gigai.registry import open_project_registry
    from gigai.workpad import resolve_bound_project

    bound = resolve_bound_project(home_root=home, requested_target=target)
    registry, _created = open_project_registry(home, create=False)
    with registry.transaction() as transaction:
        owner = transaction.find_workspace_owner(bound.project_id)
    assert owner is not None
    return owner.username


def test_requested_username_wins_when_creating_home_scout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _setup(tmp_path)
    unbound = tmp_path / "empty-cwd"
    unbound.mkdir()
    monkeypatch.chdir(unbound)

    resolved = resolve_scout_target(
        home_root=home, requested_target=None, requested_username="explicit-name"
    )
    assert _owner_username_for(home, resolved) == "explicit-name"


def test_single_shared_existing_owner_username_is_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _setup(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    _init(home, first, username="shared-owner")
    _init(home, second, username="shared-owner")

    unbound = tmp_path / "empty-cwd"
    unbound.mkdir()
    monkeypatch.chdir(unbound)
    resolved = resolve_scout_target(home_root=home, requested_target=None)

    assert _owner_username_for(home, resolved) == "shared-owner"


def test_differing_owner_usernames_prefer_the_existing_scout_projects_owner(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    plain = tmp_path / "plain"
    scouted = tmp_path / "scouted"
    _init(home, plain, username="plain-owner")
    _init(home, scouted, username="scout-owner")
    _scout_install(home, scouted)

    # A third, unrelated bound (but Scout-less) project with yet another
    # owner -- must not be picked over the Scout project's owner.
    other = tmp_path / "other"
    _init(home, other, username="other-owner")

    unbound = tmp_path / "empty-cwd"
    unbound.mkdir()

    from gigai.scout.target_resolution import _choose_username  # noqa: PLC0415

    choice = _choose_username(home, None)
    assert choice.username == "scout-owner"

    # uat-bug-017: resolution now creates <home>/scout here (was: reused `scouted`), with that owner.
    resolved = resolve_scout_target(home_root=home, requested_target=None)
    assert resolved == home_scout_target(home)
    assert _owner_username_for(home, resolved) == "scout-owner"
