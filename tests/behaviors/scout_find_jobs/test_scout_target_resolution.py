"""``gigai.scout.target_resolution`` — the shared Scout target resolver.

uat-bug-002: every ``gigai scout ...`` command that takes ``--target``
resolves it through this one module, in order: (1) ``--target``; (2) a
registered project at cwd (unchanged, today's behavior); (3) the one
existing Scout project in the registry; (4) create and bind ``<home>/scout``.
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
    ScoutTargetError,
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


# --- Step 2: a registered cwd binding is used, Scout installed or not ------


def test_cwd_bound_project_is_used_even_before_scout_is_installed(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    target = tmp_path / "target"
    _init(home, target, username="cwd-test")

    resolved = resolve_scout_target(home_root=home, requested_target=None, cwd=target)
    assert resolved == target.resolve()


def test_cwd_registered_scout_folder_is_used_unchanged(tmp_path: Path) -> None:
    """Acceptance: cwd = a registered Scout folder -> that one is used."""

    home = _setup(tmp_path)
    target = tmp_path / "target"
    _init(home, target, username="cwd-scout-test")
    _scout_install(home, target)

    resolved = resolve_scout_target(home_root=home, requested_target=None, cwd=target)
    assert resolved == target.resolve()

    # And from a subfolder of it.
    subfolder = target / "nested"
    subfolder.mkdir()
    resolved_nested = resolve_scout_target(home_root=home, requested_target=None, cwd=subfolder)
    assert resolved_nested == target.resolve()


# --- Step 3: exactly one existing Scout project is reused ------------------


def test_one_existing_scout_project_is_reused_from_an_unrelated_cwd(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    target = tmp_path / "target"
    _init(home, target, username="reuse-test")
    _scout_install(home, target)

    unrelated = tmp_path / "unrelated-cwd"
    unrelated.mkdir()

    resolved = resolve_scout_target(home_root=home, requested_target=None, cwd=unrelated)
    assert resolved == target.resolve()


def test_two_existing_scout_projects_raise_a_clear_error_naming_both(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    _init(home, first, username="two-scout-test")
    _scout_install(home, first)
    _init(home, second, username="two-scout-test")
    _scout_install(home, second)

    unrelated = tmp_path / "unrelated-cwd"
    unrelated.mkdir()

    with pytest.raises(ScoutTargetError) as excinfo:
        resolve_scout_target(home_root=home, requested_target=None, cwd=unrelated)
    message = str(excinfo.value)
    assert str(first.resolve()) in message
    assert str(second.resolve()) in message
    assert "--target" in message


def test_a_bound_but_scout_less_project_does_not_count_as_an_existing_scout_project(
    tmp_path: Path,
) -> None:
    """`gigai init` alone (no `scout install`) doesn't make a project a Scout
    candidate for step 3 -- it falls through to creating <home>/scout.
    """

    home = _setup(tmp_path)
    target = tmp_path / "target"
    _init(home, target, username="not-scout-test")

    unrelated = tmp_path / "unrelated-cwd"
    unrelated.mkdir()

    resolved = resolve_scout_target(home_root=home, requested_target=None, cwd=unrelated)
    assert resolved == home_scout_target(home)
    assert resolved != target.resolve()


# --- Step 4: create <home>/scout, and its identity vs <home>/workpads ------


def test_creates_and_binds_home_scout_from_a_fresh_registry(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    unbound = tmp_path / "empty-cwd"
    unbound.mkdir()

    resolved = resolve_scout_target(home_root=home, requested_target=None, cwd=unbound)
    assert resolved == home_scout_target(home)
    assert resolved.is_dir()

    # A second resolution reuses the same, now-registered target (step 2/3,
    # not a second creation) -- idempotent.
    resolved_again = resolve_scout_target(home_root=home, requested_target=None, cwd=unbound)
    assert resolved_again == resolved


def test_home_scout_does_not_collide_with_the_workpad_root(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    unbound = tmp_path / "empty-cwd"
    unbound.mkdir()

    scout_target = resolve_scout_target(home_root=home, requested_target=None, cwd=unbound)

    workpad_root = tmp_path / "workpads"
    assert scout_target != workpad_root
    assert not scout_target.is_relative_to(workpad_root)
    assert not workpad_root.is_relative_to(scout_target)
    assert scout_target == home / "scout"


def test_on_created_callback_reports_the_created_path_and_username(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    unbound = tmp_path / "empty-cwd"
    unbound.mkdir()

    calls: list[tuple[Path, str]] = []
    resolved = resolve_scout_target(
        home_root=home,
        requested_target=None,
        cwd=unbound,
        requested_username="callback-test",
        on_created=lambda path, username: calls.append((path, username)),
    )
    assert calls == [(resolved, "callback-test")]

    # A second call still resolves through step 4 (no `scout install` ran, so
    # it isn't a registered *Scout* project yet -- see the "bound but
    # Scout-less" test above); it reuses the same, already-bound target
    # rather than failing, so on_created fires again with the same values.
    calls.clear()
    again = resolve_scout_target(
        home_root=home,
        requested_target=None,
        cwd=unbound,
        on_created=lambda p, u: calls.append((p, u)),
    )
    assert again == resolved
    assert calls == [(resolved, "callback-test")]


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


def test_requested_username_wins_when_creating_home_scout(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    unbound = tmp_path / "empty-cwd"
    unbound.mkdir()

    resolved = resolve_scout_target(
        home_root=home, requested_target=None, cwd=unbound, requested_username="explicit-name"
    )
    assert _owner_username_for(home, resolved) == "explicit-name"


def test_single_shared_existing_owner_username_is_reused(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    _init(home, first, username="shared-owner")
    _init(home, second, username="shared-owner")

    unbound = tmp_path / "empty-cwd"
    unbound.mkdir()
    resolved = resolve_scout_target(home_root=home, requested_target=None, cwd=unbound)

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

    # Two Scout projects would be ambiguous, but there is exactly one
    # (`scouted`); resolution reuses it (step 3) rather than reaching step 4
    # at all, so assert the username choice directly.
    from gigai.scout.target_resolution import _choose_username  # noqa: PLC0415

    choice = _choose_username(home, None)
    assert choice.username == "scout-owner"
