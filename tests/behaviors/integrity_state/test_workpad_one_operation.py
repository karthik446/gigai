"""0110-11 STORE2: one operation resolves its workpad's repository once, and never past a change.

A save resolved its workpad 5 to 10 times: the store functions each resolve
again what their caller resolved, and every resolution ran the repository
check (4 git processes after the one-process listing; 9 before).
``workpad.one_operation()`` marks one operation on one thread. Inside it a
repeated ``resolve_workpad`` does everything it did except start that check's
git processes again, and only while the fingerprint of everything the check
reads is unchanged (or only the journal head moved, along a straight line of
commits that touched neither the layout marker nor the ignore rules).

What these tests hold: a change to anything the check reads, made inside the
operation, is seen by the next resolution exactly as it is seen outside one
(the same refusal, the same text); nothing is kept after the operation, after
an exception, in another thread, or for a workpad whose configuration the one
listing did not prove; the journal's own check is asked every time.
"""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import subprocess
import threading
import uuid

import pytest

from gigai import journal, workpad
from gigai.journal import read_committed_snapshot, record_transition
from gigai.private_records import migrate_workpad_layout
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import WORKPAD_LAYOUT_PATH, committed_read_cache, one_operation, provision_workpad, resolve_workpad

PROJECT_ID = "project_12345678-1234-4234-9234-123456789abc"
GIG_ID = "gig_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

#: The repository check's git processes on a layout v2 workpad: the layout marker's publisher (two), the work tree, the configuration.
REPOSITORY_CHECK = ["show", "cat-file", "rev-parse --is-inside-work-tree", "config --list"]
#: What every resolution asks git whatever it keeps: whether the target is in a Git work tree.
TARGET = ["rev-parse --show-toplevel"]


@pytest.fixture(autouse=True)
def _no_git_configuration_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(("GIT_CONFIG_", "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_CEILING")):
            monkeypatch.delenv(name)


class Home:
    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "home"
        self.target = tmp_path / "target"
        self.target.mkdir()
        run_setup(build_config(home_root=self.home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
        bound = initialize_target(home_root=self.home, requested_target=self.target, uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"))
        assert bound.project_id == PROJECT_ID
        self.root = provision_workpad(home_root=self.home, project_id=PROJECT_ID, gig_id=GIG_ID).path
        # Layout v2 with a journal head: the workpad every Scout home has.
        migrate_workpad_layout(workpad=self.root, project_id=PROJECT_ID, gig_id=GIG_ID, uuid_factory=lambda: uuid.UUID("11111111-1111-4111-8111-111111111111"))
        self.resolve()  # the layout marker's publisher is kept in scratch/ by the first check
        self._writes = 0

    def resolve(self) -> workpad.ResolvedWorkpad:
        return resolve_workpad(home_root=self.home, requested_target=self.target, gig_id=GIG_ID)

    def another_writer_commits(self) -> None:
        """A journal transition that touches neither the layout marker nor the ignore rules."""

        self._writes += 1
        record_transition(
            workpad=self.root, project_id=PROJECT_ID, gig_id=GIG_ID, handoff_id=f"handoff_22222222-2222-4222-8222-{self._writes:012d}",
            transition="creation_started", body=f"Transition {self._writes}",
        )

    def git(self, *args: str) -> str:
        done = subprocess.run(
            ["git", "-C", os.fspath(self.root), *args], env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
            capture_output=True, text=True, check=True,
        )
        return done.stdout.strip()


@pytest.fixture
def home(tmp_path: Path) -> Home:
    return Home(tmp_path)


class Spawns:
    """The git processes the product starts, in order, each as its subcommand and first argument, with the thread that started it."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.started: list[tuple[str, str]] = []
        real = subprocess.run

        def run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
            words = [str(part) for part in argv]
            if os.path.basename(words[0]) == "git" and kwargs.get("env", {}).get("GIT_OPTIONAL_LOCKS") == "0":  # the product's, not this file's
                rest = words[3:]  # after ``git -C <root>``
                while rest[:1] == ["-c"]:
                    rest = rest[2:]
                command = " ".join(rest[:2]) if rest[0] in ("rev-parse", "config") else rest[0]
                self.started.append((threading.current_thread().name, command))
            return real(argv, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", run)

    def take(self, thread: str | None = None) -> list[str]:
        wanted = thread or threading.current_thread().name
        taken = [command for name, command in self.started if name == wanted]
        self.started = [item for item in self.started if item[0] != wanted]
        return taken


def _outcome(call: Callable[[], object]) -> tuple[str, str]:
    try:
        call()
    except Exception as error:  # noqa: BLE001 - the type and the text are what is compared
        return type(error).__name__, str(error)
    return ("pass", "")


def test_inside_one_operation_the_repository_check_is_asked_once_and_outside_every_time(home: Home, monkeypatch: pytest.MonkeyPatch) -> None:
    spawns = Spawns(monkeypatch)
    # Outside an operation: as before, every resolution asks everything.
    home.resolve()
    home.resolve()
    assert spawns.take() == [*TARGET, *REPOSITORY_CHECK] * 2
    with one_operation():
        first = home.resolve()
        assert spawns.take() == [*TARGET, *REPOSITORY_CHECK]
        again = home.resolve()
        assert spawns.take() == TARGET  # the target is asked again; the repository check is not
        assert again == first
    home.resolve()
    assert spawns.take() == [*TARGET, *REPOSITORY_CHECK]  # the operation ended: nothing of it is left


def _a_remote(home: Home) -> None:
    home.git("remote", "add", "origin", "https://example.invalid/x.git")


def _another_gig_id(home: Home) -> None:
    home.git("config", "--local", "gigai.gig-id", "gig_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def _other_ignore_rules(home: Home) -> None:
    (home.root / ".gitignore").write_bytes(b"/objects/\n")


def _same_size_ignore_rules_with_the_old_times(home: Home) -> None:
    """The ignore rules rewritten in place, same size, modification time set back: only the inode's change time tells."""

    ignore = home.root / ".gitignore"
    before = os.stat(ignore)
    content = ignore.read_bytes()
    with ignore.open("r+b") as stream:
        stream.write(content.replace(b"/objects/", b"/objectz/"))
    os.utime(ignore, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = os.stat(ignore)
    assert (after.st_size, after.st_mtime_ns, after.st_ino) == (before.st_size, before.st_mtime_ns, before.st_ino)


def _no_marker(home: Home) -> None:
    (home.root / WORKPAD_LAYOUT_PATH).unlink()


def _an_unexpected_entry(home: Home) -> None:
    (home.root / "notes.txt").write_text("x", encoding="utf-8")


def _tools_redirected(home: Home) -> None:
    elsewhere = home.root.parent / "elsewhere-tools"
    elsewhere.mkdir()
    (home.root / "tools").symlink_to(elsewhere, target_is_directory=True)


def _handoffs_redirected(home: Home) -> None:
    elsewhere = home.root.parent / "elsewhere-handoffs"
    (home.root / "handoffs").rename(elsewhere)
    (home.root / "handoffs").symlink_to(elsewhere, target_is_directory=True)


def _git_directory_replaced(home: Home) -> None:
    elsewhere = home.root.parent / "elsewhere.git"
    (home.root / ".git").rename(elsewhere)
    (home.root / ".git").write_text(f"gitdir: {elsewhere}\n", encoding="utf-8")


def _head_moved_off_the_line(home: Home) -> None:
    """The head put back on its parent: not a descendant of the head the pass was made at."""

    home.git("update-ref", "refs/heads/main", home.git("rev-parse", "HEAD~1"))


def _a_commit_that_changed_the_ignore_rules(home: Home) -> None:
    (home.root / ".gitignore").write_bytes(b"/objects/\n")
    home.git("add", ".gitignore")
    home.git("commit", "--quiet", "-m", "not a journal transition")


CHANGES: tuple[tuple[str, Callable[[Home], None], str], ...] = (
    ("a remote is added to .git/config", _a_remote, "workpad must not configure a Git remote"),
    ("a marker in .git/config gets another value", _another_gig_id, "workpad Git ownership marker gigai.gig-id mismatches"),
    ("the ignore rules are edited", _other_ignore_rules, "workpad layout marker is invalid"),
    ("the ignore rules are edited in place with the old size and time", _same_size_ignore_rules_with_the_old_times, "workpad layout marker is invalid"),
    ("the layout marker is removed", _no_marker, "workpad ignore rules differ from the declared layout contract"),
    ("an unexpected top-level entry appears", _an_unexpected_entry, "workpad contains semantic or unexpected top-level state: notes.txt"),
    ("tools/ becomes a link", _tools_redirected, "workpad tools root is redirected or invalid"),
    ("handoffs/ becomes a link", _handoffs_redirected, "workpad handoff directory is redirected or invalid"),
    ("the Git directory is replaced by a pointer elsewhere", _git_directory_replaced, "workpad uses an unexpected Git directory"),
    ("another writer's commit changed the ignore rules", _a_commit_that_changed_the_ignore_rules, "workpad layout marker is invalid"),
)


@pytest.mark.parametrize(("change", "text"), [case[1:] for case in CHANGES], ids=[case[0] for case in CHANGES])
def test_a_change_inside_the_operation_is_refused_by_the_next_resolution_as_it_is_outside_one(
    home: Home, monkeypatch: pytest.MonkeyPatch, change: Callable[[Home], None], text: str
) -> None:
    spawns = Spawns(monkeypatch)
    with one_operation():
        home.resolve()
        home.resolve()
        assert spawns.take()[-1:] == TARGET  # the pass is held: the second resolution asked only about the target
        change(home)
        inside = _outcome(home.resolve)
        again = _outcome(home.resolve)  # a retry inside the same operation: refused again, by the whole check again
    outside = _outcome(home.resolve)  # what it answers with no operation at all: today's answer
    # A held pass would have answered "pass": the refusal is the whole check's, with the text it has outside an operation.
    assert inside == outside == again == ("WorkpadConflictError", text)


def test_a_retry_after_a_refusal_sees_the_repaired_workpad(home: Home, monkeypatch: pytest.MonkeyPatch) -> None:
    spawns = Spawns(monkeypatch)
    with one_operation():
        home.resolve()
        _a_remote(home)
        assert _outcome(home.resolve) == ("WorkpadConflictError", "workpad must not configure a Git remote")
        home.git("remote", "remove", "origin")
        spawns.take()
        assert _outcome(home.resolve) == ("pass", "")
        assert spawns.take() == [*TARGET, *REPOSITORY_CHECK]  # proven again by git, then held again
        home.resolve()
        assert spawns.take() == TARGET


def test_a_head_moved_by_a_writer_is_followed_only_along_a_straight_line_that_left_the_layout_alone(home: Home, monkeypatch: pytest.MonkeyPatch) -> None:
    spawns = Spawns(monkeypatch)
    with one_operation():
        home.resolve()
        spawns.take()
        home.another_writer_commits()
        spawns.take()
        home.resolve()
        # One listing of the commits between the two heads (they touched a handoff only): the pass holds at the new head.
        assert spawns.take() == [*TARGET, "log"]
        home.resolve()
        assert spawns.take() == TARGET
        # The head put back on its parent is not a descendant: nothing held is trusted, the whole check runs.
        _head_moved_off_the_line(home)
        spawns.take()
        assert _outcome(home.resolve) == ("pass", "")
        asked = spawns.take()
        assert asked[:1] == TARGET and "config --list" in asked and "rev-parse --is-inside-work-tree" in asked


def test_nothing_is_left_after_the_operation_or_after_an_exception(home: Home, monkeypatch: pytest.MonkeyPatch) -> None:
    spawns = Spawns(monkeypatch)
    with pytest.raises(RuntimeError, match="the save failed"):
        with one_operation():
            home.resolve()
            assert workpad._OPERATION.passed  # held while the operation runs
            raise RuntimeError("the save failed")
    assert workpad._OPERATION.passed is None
    spawns.take()
    home.resolve()
    assert spawns.take() == [*TARGET, *REPOSITORY_CHECK]

    @one_operation()
    def save() -> None:
        home.resolve()
        home.resolve()

    for _call in range(2):  # the decorator: each call is its own operation
        save()
        assert spawns.take() == [*TARGET, *REPOSITORY_CHECK, *TARGET]
        assert workpad._OPERATION.passed is None


def test_an_operation_inside_another_is_the_same_operation_and_ends_with_the_outer_one(home: Home, monkeypatch: pytest.MonkeyPatch) -> None:
    spawns = Spawns(monkeypatch)
    with one_operation():
        home.resolve()
        spawns.take()
        with one_operation():
            home.resolve()
            assert spawns.take() == TARGET
        assert workpad._OPERATION.passed  # the inner one ended nothing
        home.resolve()
        assert spawns.take() == TARGET
    assert workpad._OPERATION.passed is None


def test_another_thread_is_not_inside_the_operation(home: Home, monkeypatch: pytest.MonkeyPatch) -> None:
    spawns = Spawns(monkeypatch)
    seen: dict[str, object] = {}

    def other() -> None:
        seen["passed"] = getattr(workpad._OPERATION, "passed", None)
        home.resolve()
        home.resolve()

    with one_operation():
        home.resolve()
        home.resolve()
        thread = threading.Thread(target=other, name="another-request")
        thread.start()
        thread.join()
        assert spawns.take() == [*TARGET, *REPOSITORY_CHECK, *TARGET]
    assert seen["passed"] is None
    assert spawns.take("another-request") == [*TARGET, *REPOSITORY_CHECK] * 2  # every check, every time


def test_a_pass_the_one_listing_did_not_prove_is_not_held(home: Home, monkeypatch: pytest.MonkeyPatch) -> None:
    home.git("config", "--local", "core.editor", "true")  # a key git init does not write: the five old questions answer
    spawns = Spawns(monkeypatch)
    with one_operation():
        for _call in range(2):
            home.resolve()
            asked = spawns.take()
            assert asked.count("config --local") == 4 and "remote" in asked and "rev-parse --is-inside-work-tree" in asked
        assert not workpad._OPERATION.passed


def test_the_journals_own_check_is_asked_every_time_inside_an_operation(home: Home, monkeypatch: pytest.MonkeyPatch) -> None:
    def read() -> None:
        read_committed_snapshot(workpad=home.root, project_id=PROJECT_ID, gig_id=GIG_ID, prefixes=("manifests/",))

    read()
    spawns = Spawns(monkeypatch)
    read()
    outside = spawns.take()
    assert outside[:3] == ["show", "cat-file", "config --list"]  # ``journal._check_workpad``: the layout marker's publisher, the configuration
    with one_operation():
        home.resolve()
        home.resolve()
        spawns.take()
        read()
        assert spawns.take() == outside
        read()
        assert spawns.take() == outside
        home.another_writer_commits()  # a journal write inside the operation: its own check, before its lock, as before
        written = spawns.take()
        assert written[:3] == ["show", "cat-file", "config --list"]
    # And a remote added inside an operation is refused by the journal itself, whatever the operation holds.
    with one_operation():
        home.resolve()
        _a_remote(home)
        assert _outcome(read) == ("JournalConflictError", "journal workpad has a remote")
        assert _outcome(lambda: journal._check_workpad(home.root, PROJECT_ID, GIG_ID)) == ("JournalConflictError", "journal workpad has a remote")


def test_inside_a_read_scope_the_operation_holds_nothing(home: Home) -> None:
    """A read inside an operation keeps what the read scope keeps, where it keeps it, as before: the two do not mix."""

    with one_operation():
        with committed_read_cache():
            home.resolve()
            home.resolve()
        assert workpad._OPERATION.passed == {}
        home.resolve()
        assert len(workpad._OPERATION.passed) == 1
