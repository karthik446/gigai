from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import time as real_time
import uuid

from click.testing import CliRunner
import pytest

from gigai import target_binding
from gigai.cli import cli
from gigai.project_binding import (
    BINDING_SCHEMA_VERSION,
    MalformedProjectBindingError,
    UnsupportedProjectBindingVersionError,
    load_project_binding,
    new_project_binding,
    parse_project_binding,
    render_project_binding,
)
from gigai.registry import (
    ProjectRecord,
    REGISTRY_APPLICATION_ID,
    RegistryConflictError,
    RegistryCorruptError,
    RegistryPermissionError,
    RegistryVersionError,
    open_project_registry,
    registry_path,
)
from gigai.setup import build_config, run_setup
from gigai.target_binding import (
    INIT_LOCK_OWNER,
    INIT_LOCK_TIMEOUT_SECONDS,
    ConflictingBindingError,
    InitLockUnavailableError,
    TargetIdentityChangedError,
    TargetInitLock,
    TargetPermissionError,
    TrackedBindingError,
    assert_target_identity_stable,
    initialize_target,
    resolve_target,
)


PROJECT_ID = "project_12345678-1234-4234-9234-123456789abc"
OTHER_PROJECT_ID = "project_87654321-4321-4432-a321-cba987654321"


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        capture_output=True,
        check=True,
        shell=False,
    )


def _git_repository(root: Path) -> None:
    root.mkdir()
    _run_git(root, "init", "--initial-branch=main", "--quiet")
    (root / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _run_git(root, "add", "tracked.txt")
    _run_git(
        root,
        "-c",
        "user.name=GigAI Test",
        "-c",
        "user.email=test@gigai.invalid",
        "commit",
        "--quiet",
        "-m",
        "baseline",
    )


def _configured_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    workpad = tmp_path / "workpad"
    home.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=workpad,
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    return home


def test_project_binding_is_canonical_path_free_and_round_trips() -> None:
    binding = new_project_binding(PROJECT_ID)
    rendered = render_project_binding(binding)

    assert rendered == (
        b'schema_version = "1.0"\n'
        b'project_id = "project_12345678-1234-4234-9234-123456789abc"\n'
        b'workpad_locator = "registry:project_12345678-1234-4234-9234-123456789abc"\n'
    )
    assert b"/" not in rendered
    assert parse_project_binding(
        {
            "schema_version": BINDING_SCHEMA_VERSION,
            "project_id": PROJECT_ID,
            "workpad_locator": f"registry:{PROJECT_ID}",
        }
    ) == binding


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {
            "schema_version": BINDING_SCHEMA_VERSION,
            "project_id": PROJECT_ID,
            "workpad_locator": "registry:wrong",
        },
        {
            "schema_version": BINDING_SCHEMA_VERSION,
            "project_id": "project_not-a-uuid",
            "workpad_locator": "registry:project_not-a-uuid",
        },
        {
            "schema_version": BINDING_SCHEMA_VERSION,
            "project_id": PROJECT_ID,
            "workpad_locator": f"registry:{PROJECT_ID}",
            "unknown": True,
        },
        {
            "schema_version": BINDING_SCHEMA_VERSION,
            "project_id": PROJECT_ID,
            "workpad_locator": f"registry:{PROJECT_ID}",
            "active_gig_id": None,
        },
    ),
)
def test_project_binding_rejects_malformed_or_ambiguous_fields(payload: object) -> None:
    with pytest.raises(MalformedProjectBindingError):
        parse_project_binding(payload)


def test_project_binding_rejects_unsupported_version() -> None:
    with pytest.raises(UnsupportedProjectBindingVersionError, match="no migration"):
        parse_project_binding(
            {
                "schema_version": "2.0",
                "project_id": PROJECT_ID,
                "workpad_locator": f"registry:{PROJECT_ID}",
            }
        )


def test_registry_schema_uniqueness_alias_identity_and_rollback(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = tmp_path / "target"
    alias = tmp_path / "alias"
    home.mkdir()
    target.mkdir()
    alias.symlink_to(target, target_is_directory=True)

    registry, created = open_project_registry(home, create=True)
    assert created is True
    with pytest.raises(RuntimeError, match="abort"):
        with registry.transaction() as transaction:
            transaction.insert(
                ProjectRecord(PROJECT_ID, os.fspath(target.resolve()), "non-git")
            )
            raise RuntimeError("abort")
    assert registry.records() == ()

    with registry.transaction() as transaction:
        transaction.insert(ProjectRecord(PROJECT_ID, os.fspath(target.resolve()), "non-git"))
        found = transaction.find_target(alias)
        assert found is not None
        assert found.project_id == PROJECT_ID
        with pytest.raises(RegistryConflictError):
            transaction.insert(
                ProjectRecord(OTHER_PROJECT_ID, os.fspath(target.resolve()), "non-git")
            )


def test_registry_rejects_corruption_and_unknown_version(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    path = registry_path(home)
    path.write_bytes(b"not sqlite")
    path.chmod(0o600)
    with pytest.raises(RegistryCorruptError):
        open_project_registry(home, create=False)

    path.unlink()
    open_project_registry(home, create=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA user_version = 99")
    finally:
        connection.close()
    path.chmod(0o600)
    with pytest.raises(RegistryVersionError, match="no migration"):
        open_project_registry(home, create=False)


def test_registry_rejects_schema_drift_at_the_current_version(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    path = registry_path(home)
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"PRAGMA application_id = {REGISTRY_APPLICATION_ID}")
        connection.execute("PRAGMA user_version = 1")
        connection.execute(
            "CREATE TABLE projects ("
            "project_id TEXT PRIMARY KEY NOT NULL, "
            "target_locator TEXT NOT NULL UNIQUE, "
            "target_kind TEXT NOT NULL"
            ") WITHOUT ROWID"
        )
        connection.commit()
    finally:
        connection.close()
    path.chmod(0o600)

    with pytest.raises(RegistryCorruptError, match="versioned schema"):
        open_project_registry(home, create=False)


def test_registry_refuses_symlinks_and_non_private_permissions(tmp_path: Path) -> None:
    home = tmp_path / "home"
    other_home = tmp_path / "other-home"
    home.mkdir()
    other_home.mkdir()
    open_project_registry(other_home, create=True)
    registry_path(home).symlink_to(registry_path(other_home))
    with pytest.raises(RegistryCorruptError, match="symlink"):
        open_project_registry(home, create=False)

    registry_path(home).unlink()
    registry_path(home).write_bytes(registry_path(other_home).read_bytes())
    registry_path(home).chmod(0o644)
    with pytest.raises(RegistryPermissionError, match="mode 0600"):
        open_project_registry(home, create=False)


def test_git_init_has_exact_ignored_delta_and_is_idempotent(tmp_path: Path) -> None:
    home = _configured_home(tmp_path)
    target = tmp_path / "target"
    _git_repository(target)
    status_before = _run_git(
        target, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    ).stdout

    first = initialize_target(
        home_root=home,
        requested_target=target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    binding_bytes = (target / ".gigai" / "project.toml").read_bytes()
    exclude_bytes = (target / ".git" / "info" / "exclude").read_bytes()
    second = initialize_target(home_root=home, requested_target=target)

    assert first.project_id == second.project_id == PROJECT_ID
    assert first.binding_created is True
    assert second.binding_created is False
    assert second.registry_changed is False
    assert second.exclude_changed is False
    assert binding_bytes == (target / ".gigai" / "project.toml").read_bytes()
    assert exclude_bytes == (target / ".git" / "info" / "exclude").read_bytes()
    assert exclude_bytes.splitlines().count(b"/.gigai/") == 1
    assert _run_git(
        target, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    ).stdout == status_before
    assert len(open_project_registry(home, create=False)[0].records()) == 1


def test_git_init_preserves_dirty_bytes_and_status(tmp_path: Path) -> None:
    home = _configured_home(tmp_path)
    target = tmp_path / "target"
    _git_repository(target)
    (target / "tracked.txt").write_text("dirty tracked\n", encoding="utf-8")
    (target / "untracked.py").write_bytes(b"print('dirty')\n")
    dirty_before = {
        "tracked": (target / "tracked.txt").read_bytes(),
        "untracked": (target / "untracked.py").read_bytes(),
        "status": _run_git(
            target, "status", "--porcelain=v1", "-z", "--untracked-files=all"
        ).stdout,
    }

    initialize_target(home_root=home, requested_target=target)

    assert (target / "tracked.txt").read_bytes() == dirty_before["tracked"]
    assert (target / "untracked.py").read_bytes() == dirty_before["untracked"]
    assert _run_git(
        target, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    ).stdout == dirty_before["status"]


def test_non_git_target_is_registry_only_and_aliases_converge(tmp_path: Path) -> None:
    home = _configured_home(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "target-alias"
    alias.symlink_to(target, target_is_directory=True)

    first = initialize_target(home_root=home, requested_target=alias)
    second = initialize_target(home_root=home, requested_target=target)

    assert first.project_id == second.project_id
    assert not (target / ".gigai").exists()
    records = open_project_registry(home, create=False)[0].records()
    assert len(records) == 1
    assert records[0].target_locator == os.fspath(target.resolve(strict=True))


def test_tracked_binding_is_refused_without_registry_mutation(tmp_path: Path) -> None:
    home = _configured_home(tmp_path)
    target = tmp_path / "target"
    _git_repository(target)
    (target / ".gigai").mkdir()
    (target / ".gigai" / "project.toml").write_bytes(
        render_project_binding(new_project_binding(PROJECT_ID))
    )
    _run_git(target, "add", ".gigai/project.toml")

    with pytest.raises(TrackedBindingError):
        initialize_target(home_root=home, requested_target=target)

    assert not registry_path(home).exists()


def test_symlinked_git_exclude_is_refused_without_touching_its_target(
    tmp_path: Path,
) -> None:
    home = _configured_home(tmp_path)
    target = tmp_path / "target"
    outside = tmp_path / "outside-exclude"
    _git_repository(target)
    exclude = target / ".git" / "info" / "exclude"
    exclude.unlink()
    outside.write_bytes(b"external canary\n")
    exclude.symlink_to(outside)

    with pytest.raises(TargetPermissionError, match="must not be a symlink"):
        initialize_target(home_root=home, requested_target=target)

    assert outside.read_bytes() == b"external canary\n"
    assert not (target / ".gigai").exists()
    assert not registry_path(home).exists()


def test_authoritative_binding_reconciles_registry_without_new_id(tmp_path: Path) -> None:
    home = _configured_home(tmp_path)
    target = tmp_path / "target"
    _git_repository(target)
    (target / ".gigai").mkdir()
    (target / ".gigai" / "project.toml").write_bytes(
        render_project_binding(new_project_binding(PROJECT_ID))
    )
    (target / "user-change.txt").write_text("preserve me\n", encoding="utf-8")
    status_before = _run_git(
        target, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    ).stdout

    result = initialize_target(home_root=home, requested_target=target)

    assert result.project_id == PROJECT_ID
    assert result.reconciled is True
    assert load_project_binding(target).project_id == PROJECT_ID
    assert open_project_registry(home, create=False)[0].records()[0].project_id == PROJECT_ID
    status_after = _run_git(
        target, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    ).stdout
    assert status_before.replace(b"?? .gigai/project.toml\0", b"") == status_after


def test_registry_conflict_does_not_replace_authoritative_binding(tmp_path: Path) -> None:
    home = _configured_home(tmp_path)
    target = tmp_path / "target"
    other = tmp_path / "other"
    _git_repository(target)
    other.mkdir()
    (target / ".gigai").mkdir()
    binding = new_project_binding(PROJECT_ID)
    (target / ".gigai" / "project.toml").write_bytes(render_project_binding(binding))
    registry, _ = open_project_registry(home, create=True)
    with registry.transaction() as transaction:
        transaction.insert(ProjectRecord(PROJECT_ID, os.fspath(other.resolve()), "git"))

    with pytest.raises(ConflictingBindingError):
        initialize_target(home_root=home, requested_target=target)

    assert load_project_binding(target) == binding
    assert registry.records()[0].target_locator == os.fspath(other.resolve())
    assert not (target / ".git" / "gigai-init.lock").exists()


def test_repointed_and_broken_aliases_fail_identity_revalidation(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    alias = tmp_path / "alias"
    first.mkdir()
    second.mkdir()
    alias.symlink_to(first, target_is_directory=True)
    resolved = resolve_target(alias)

    alias.unlink()
    alias.symlink_to(second, target_is_directory=True)
    with pytest.raises(TargetIdentityChangedError, match="repointed"):
        assert_target_identity_stable(resolved)

    alias.unlink()
    alias.symlink_to(tmp_path / "missing", target_is_directory=True)
    with pytest.raises(TargetIdentityChangedError, match="unavailable"):
        assert_target_identity_stable(resolved)


def test_abandoned_git_init_lock_is_recovered_and_cleaned(tmp_path: Path) -> None:
    home = _configured_home(tmp_path)
    target = tmp_path / "target"
    _git_repository(target)
    lock = target / ".git" / "gigai-init.lock"
    lock.mkdir()
    (lock / "owner").write_text("99999999 dead-process-token\n", encoding="ascii")

    result = initialize_target(home_root=home, requested_target=target)

    assert result.binding_created is True
    assert not lock.exists()
    assert not tuple((target / ".git").glob(".gigai-init.lock.abandoned-*"))


class _FakeClock:
    """Stands in for ``target_binding.time``: sleeping advances the clock."""

    def __init__(self, *, release: Path | None = None, release_at: float = 0.0) -> None:
        self.elapsed = 0.0
        self._release = release
        self._release_at = release_at
        self._epoch = real_time.time()

    def monotonic(self) -> float:
        return self.elapsed

    def time(self) -> float:
        return self._epoch + self.elapsed

    def sleep(self, seconds: float) -> None:
        self.elapsed += seconds
        if self._release is not None and self.elapsed >= self._release_at:
            (self._release / INIT_LOCK_OWNER).unlink()
            self._release.rmdir()
            self._release = None


def _live_owner() -> str:
    return f"{os.getpid()} live-owner-token\n"


def _held_lock(lock: Path, owner: str) -> Path:
    lock.mkdir(mode=0o700)
    (lock / INIT_LOCK_OWNER).write_text(owner, encoding="ascii")
    return lock


def test_init_lock_waiter_outlasts_the_base_timeout_while_the_owner_is_alive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lock = _held_lock(tmp_path / "default-init.lock", _live_owner())
    clock = _FakeClock(release=lock, release_at=INIT_LOCK_TIMEOUT_SECONDS + 5.0)
    monkeypatch.setattr(target_binding, "time", clock)

    with TargetInitLock(lock):
        acquired_at = clock.elapsed
        assert (lock / INIT_LOCK_OWNER).read_text(encoding="ascii") != _live_owner()

    assert acquired_at >= INIT_LOCK_TIMEOUT_SECONDS + 5.0
    assert not lock.exists()
    assert capsys.readouterr().err == (
        f"Another gigai init is running (pid {os.getpid()}); waiting…\n"
    )


def test_init_lock_waiter_gives_up_on_a_live_owner_at_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _held_lock(tmp_path / "default-init.lock", _live_owner())
    clock = _FakeClock()
    monkeypatch.setattr(target_binding, "time", clock)

    with pytest.raises(InitLockUnavailableError, match="is unavailable"):
        with TargetInitLock(lock, live_owner_timeout=30.0):
            raise AssertionError("a lock its live owner never released was acquired")

    assert 30.0 <= clock.elapsed < 30.1
    assert (lock / INIT_LOCK_OWNER).read_text(encoding="ascii") == _live_owner()


def test_init_lock_with_a_dead_owner_is_recovered_without_waiting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lock = _held_lock(tmp_path / "default-init.lock", "99999999 dead-process-token\n")
    clock = _FakeClock()
    monkeypatch.setattr(target_binding, "time", clock)

    with TargetInitLock(lock):
        acquired_at = clock.elapsed

    assert acquired_at < 0.1
    assert not lock.exists()
    assert not tuple(tmp_path.glob(".default-init.lock.abandoned-*"))
    assert capsys.readouterr().err == ""


def test_init_lock_with_an_unreadable_owner_times_out_at_the_base_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _held_lock(tmp_path / "default-init.lock", _live_owner())
    (lock / INIT_LOCK_OWNER).write_bytes(b"\xff not ascii\n")
    clock = _FakeClock()
    monkeypatch.setattr(target_binding, "time", clock)

    with pytest.raises(InitLockUnavailableError, match="is unavailable"):
        with TargetInitLock(lock):
            raise AssertionError("a lock with an unreadable owner was acquired")

    assert INIT_LOCK_TIMEOUT_SECONDS <= clock.elapsed < INIT_LOCK_TIMEOUT_SECONDS + 0.1


def test_second_init_waits_out_a_running_init_that_holds_the_lock_past_ten_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _configured_home(tmp_path)
    target = tmp_path / "target"
    _git_repository(target)
    argv = [
        "init",
        "--home",
        os.fspath(home),
        "--target",
        os.fspath(target),
        "--username",
        "synthetic-user",
        "--json",
    ]
    first = CliRunner().invoke(cli, argv)
    assert first.exit_code == 0, first.output
    # A second init arrives while another live init still holds the lock, and
    # that init needs 15 s: longer than the base timeout.
    lock = _held_lock(target / ".gigai" / "locks" / "default-init.lock", _live_owner())
    clock = _FakeClock(release=lock, release_at=INIT_LOCK_TIMEOUT_SECONDS + 5.0)
    monkeypatch.setattr(target_binding, "time", clock)

    second = CliRunner().invoke(cli, argv)

    assert second.exit_code == 0, second.output
    assert "init_lock_unavailable" not in second.output
    assert clock.elapsed >= INIT_LOCK_TIMEOUT_SECONDS + 5.0
    assert second.stderr.count("Another gigai init is running") == 1
    assert f"(pid {os.getpid()}); waiting…" in second.stderr
    assert not lock.exists()
