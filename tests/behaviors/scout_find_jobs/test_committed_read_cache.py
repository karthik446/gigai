"""0110-033: ``workpad.committed_read_cache``, the reuse of unchanged checks inside a read.

A workpad resolve was 13 git subprocesses and a journal read as many again,
on every call. Inside the block a check that passed, and a committed read
that was made, are reused for as long as the workpad's fingerprint (its
journal head, its git config, its ignore rules, its top-level entries) is
the same. These tests pin what makes that safe:

* outside the block nothing is reused (a writer, the CLI: as before);
* a journal write is seen by the very next read;
* a workpad that stops being valid is refused again, not answered from memory;
* a caller cannot change what the next caller gets;
* a write that touched nothing a kept read was made from leaves it in use, at
  the new head, and it is exactly what a fresh read returns there; a history
  that is not a straight line from the old head keeps nothing.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace

import pytest

import gigai.journal as journal
import gigai.workpad as workpad_module
from gigai.journal import read_committed_artifact, read_committed_snapshot
from gigai.scout import profile_records
from gigai.scout.experience_answers import record_answer
from gigai.workpad import (
    WorkpadConflictError,
    committed_read_cache,
    committed_read_cache_active,
    paths_committed_between,
    resolve_workpad,
    workpad_fingerprint,
    workpad_head_without_git,
)

from .test_read_routes_lock_free import _gig


@pytest.fixture
def gig(tmp_path: Path) -> SimpleNamespace:
    for cache in (
        workpad_module._validated_repositories,
        workpad_module._resolved_targets,
        workpad_module._committed_between,
        journal._validated_workpads,
        journal._snapshot_cache,
        journal._artifact_cache,
    ):
        cache.clear()
    return _gig(tmp_path, boards=5)


@pytest.fixture
def spawns(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every subprocess this process starts, as its argument list."""

    started: list[list[str]] = []
    real = subprocess.run

    def counting(args, *rest, **kwargs):
        started.append([str(word) for word in args])
        return real(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting)
    return started


def _resolve(gig: SimpleNamespace):
    return resolve_workpad(home_root=gig.home, requested_target=gig.target, gig_id=None, allow_semantic_state=True)


def _profiles_snapshot(gig: SimpleNamespace):
    resolved = gig.resolved
    return read_committed_snapshot(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
        prefixes=("records/scout-profiles/", "records/scout-profile-selection/"),
    )


def _git(gig: SimpleNamespace, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(gig.resolved.path), *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def test_the_head_read_from_the_files_is_gits_head(gig: SimpleNamespace) -> None:
    assert workpad_head_without_git(gig.resolved.path) == _git(gig, "rev-parse", "HEAD")
    assert workpad_fingerprint(gig.resolved.path)[0] == _git(gig, "rev-parse", "HEAD")  # type: ignore[index]


def test_outside_a_read_nothing_is_reused(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    assert not committed_read_cache_active()
    # 0110-043: the first check at a new journal head lists the commits since the
    # head the layout marker's publisher was kept for (one more subprocess, once).
    _resolve(gig)
    spawns.clear()
    _resolve(gig)
    first = len(spawns)
    _resolve(gig)
    assert first > 0 and len(spawns) == 2 * first

    # 0110-044: the first snapshot at a new journal head lists the commits since
    # the head every path's publishers were kept for (one subprocess, once).
    _profiles_snapshot(gig)
    spawns.clear()
    _profiles_snapshot(gig)
    one = len(spawns)
    _profiles_snapshot(gig)
    assert one > 0 and len(spawns) == 2 * one
    assert workpad_module._validated_repositories == {} and journal._snapshot_cache == {}


def test_inside_a_read_an_unchanged_workpad_is_asked_once(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    with committed_read_cache():
        assert committed_read_cache_active()
        first = _resolve(gig)
        snapshot = _profiles_snapshot(gig)
        cold = len(spawns)
        spawns.clear()
        assert _resolve(gig) == first
        assert _profiles_snapshot(gig) == snapshot
        assert spawns == []
    assert cold > 0 and not committed_read_cache_active()

    # Another thread's read reuses them too: the cache is the process's, the switch is the thread's.
    seen: list[int] = []

    def other() -> None:
        with committed_read_cache():
            _resolve(gig)
            _profiles_snapshot(gig)
        seen.append(len(spawns))

    thread = threading.Thread(target=other)
    thread.start()
    thread.join(timeout=60)
    assert seen == [0]


def test_a_cached_snapshot_is_the_uncached_one(gig: SimpleNamespace) -> None:
    plain = _profiles_snapshot(gig)
    with committed_read_cache():
        first = _profiles_snapshot(gig)
        again = _profiles_snapshot(gig)
    assert first == plain and again == plain and plain.artifacts


def test_a_caller_cannot_change_what_the_next_caller_reads(gig: SimpleNamespace) -> None:
    with committed_read_cache():
        first = _profiles_snapshot(gig)
        expected = dict(first.artifacts)
        first.artifacts.clear()
        assert _profiles_snapshot(gig).artifacts == expected


def test_a_journal_write_is_seen_by_the_next_read(gig: SimpleNamespace) -> None:
    resolved = gig.resolved
    with committed_read_cache():
        before = profile_records.list_profiles(resolved)
        assert [item.profile_id for item in before] == [gig.profile.profile_id]
        head = workpad_head_without_git(resolved.path)

        created = profile_records.create_profile(
            resolved, label="Second", titles=("data engineer",), titles_to_avoid=(), queries=("data engineer",),
            resume_ref=gig.profile.resume_ref,
        )

        assert workpad_head_without_git(resolved.path) != head
        after = profile_records.list_profiles(resolved)
        assert sorted(item.profile_id for item in after) == sorted([gig.profile.profile_id, created.profile_id])
        # and the selection written next is the one read next
        profile_records.switch_selected_profile(resolved, profile_id=created.profile_id)
        selected = profile_records.selected_profile(resolved, home_root=gig.home, target=gig.target)
        assert selected is not None and selected.profile_id == created.profile_id


def test_a_write_by_another_process_is_seen_by_the_next_read(gig: SimpleNamespace) -> None:
    """The run's child process and the CLI write the journal too: only the head on disk says so."""

    import sys
    import textwrap

    resolved = gig.resolved
    with committed_read_cache():
        assert len(profile_records.list_profiles(resolved)) == 1
        script = textwrap.dedent(
            f"""
            from pathlib import Path
            from gigai.scout import profile_records
            from gigai.workpad import resolve_workpad
            home, target = Path({str(gig.home)!r}), Path({str(gig.target)!r})
            resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
            selected = profile_records.selected_profile(resolved, home_root=home, target=target)
            profile_records.create_profile(
                resolved, label="From outside", titles=("sre",), titles_to_avoid=(), queries=("sre",), resume_ref=selected.resume_ref,
            )
            """
        )
        done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
        assert done.returncode == 0, done.stderr
        assert sorted(item.label for item in profile_records.list_profiles(resolved)) == ["From outside", "default"]


def test_a_committed_artifact_is_kept_and_a_missing_one_is_not(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    resolved = gig.resolved
    path = next(iter(_profiles_snapshot(gig).artifacts))
    read = dict(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id)
    plain = read_committed_artifact(path=path, **read)
    with committed_read_cache():
        assert read_committed_artifact(path=path, **read) == plain
        spawns.clear()
        assert read_committed_artifact(path=path, **read) == plain
        assert spawns == []
        for _ in range(2):
            with pytest.raises(journal.JournalArtifactMissingError):
                read_committed_artifact(path="records/scout-profiles/none/writes/000000000001.json", **read)
        assert spawns  # a refusal is asked again every time


def test_a_workpad_that_stops_being_valid_is_refused_again(gig: SimpleNamespace) -> None:
    with committed_read_cache():
        _resolve(gig)
        _profiles_snapshot(gig)

        _git(gig, "config", "--local", "gigai.gig-id", "gig_00000000-0000-4000-8000-000000000000")
        with pytest.raises(WorkpadConflictError):
            _resolve(gig)
        with pytest.raises(journal.JournalConflictError):
            _profiles_snapshot(gig)

        _git(gig, "config", "--local", "gigai.gig-id", gig.resolved.gig_id)
        _resolve(gig)
        _git(gig, "remote", "add", "origin", "https://example.invalid/x.git")
        with pytest.raises(WorkpadConflictError):
            _resolve(gig)


def test_an_unexpected_top_level_entry_is_refused_again(gig: SimpleNamespace) -> None:
    with committed_read_cache():
        _resolve(gig)
        (gig.resolved.path / "not-a-workpad-root").mkdir()
        with pytest.raises(WorkpadConflictError):
            _resolve(gig)


def test_a_head_only_git_can_read_is_never_cached(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    """A packed ref leaves no loose file to read: every call asks git, as outside a read."""

    _git(gig, "pack-refs", "--all")
    assert workpad_head_without_git(gig.resolved.path) is None and workpad_fingerprint(gig.resolved.path) is None
    spawns.clear()
    with committed_read_cache():
        _resolve(gig)
        first = len(spawns)
        _resolve(gig)
        _profiles_snapshot(gig)
    # Only the target lookup (one git call on the target folder, not the workpad) is reused.
    assert first > 0 and len(spawns) >= 2 * first - 1
    assert workpad_module._validated_repositories == {} and journal._snapshot_cache == {}


def test_a_target_that_becomes_a_git_repository_is_looked_up_again(tmp_path: Path, spawns: list[list[str]]) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    with committed_read_cache():
        assert workpad_module._resolve_target(folder, cwd=None).kind == "non-git"
        spawns.clear()
        assert workpad_module._resolve_target(folder, cwd=None).kind == "non-git"
        assert spawns == []
        subprocess.run(["git", "init", "--quiet", str(folder)], check=True, capture_output=True)
        spawns.clear()
        assert workpad_module._resolve_target(folder, cwd=None).kind == "git"
        assert len(spawns) == 1
    spawns.clear()
    workpad_module._resolve_target(folder, cwd=None)
    workpad_module._resolve_target(folder, cwd=None)
    assert len(spawns) == 2  # outside a read: asked every time


# --------------------------------------------------------------------------
# A write that touched nothing a kept read was made from
# --------------------------------------------------------------------------


def _answer(gig: SimpleNamespace, number: int) -> None:
    """One journal commit under ``records/record_<id>/``: nothing the profile reads select."""

    record_answer(
        home_root=gig.home, requested_target=gig.target, question_id=f"years:tool-{number}",
        prompt=f"Years of tool {number}?", answer=str(number),
    )


def test_an_unrelated_write_leaves_a_kept_snapshot_in_use_and_it_is_the_fresh_one(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    with committed_read_cache():
        _resolve(gig)
        first = _profiles_snapshot(gig)
        _answer(gig, 1)
        _answer(gig, 2)
        head = _git(gig, "rev-parse", "HEAD")
        assert head != first.head

        spawns.clear()
        assert _resolve(gig) == gig.resolved
        kept = _profiles_snapshot(gig)
        # One question for everything kept: what did the commits since touch?
        assert len(spawns) == 1 and "log" in spawns[0] and f"{first.head}..{head}" in spawns[0], spawns
        assert (kept.head, kept.artifacts) == (head, first.artifacts)

        spawns.clear()
        assert _profiles_snapshot(gig) == kept and spawns == []  # and then nothing at all
    assert _profiles_snapshot(gig) == kept  # what a read with nothing kept returns at this head


def test_a_write_under_what_a_snapshot_selected_is_read_again(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    from gigai.private_records import RECORD_DIRECTORY_PATTERN

    resolved = gig.resolved
    records = dict(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
        child_prefixes=(("records/", RECORD_DIRECTORY_PATTERN),),
    )
    with committed_read_cache():
        profiles = _profiles_snapshot(gig)
        answers = read_committed_snapshot(**records)
        assert len(answers.artifacts) > 0

        _answer(gig, 3)  # a NEW record directory: the family grew, the profiles did not
        spawns.clear()
        assert _profiles_snapshot(gig).artifacts == profiles.artifacts
        assert len(spawns) <= 1  # at most the one question about the commits since (the write may have asked it)
        spawns.clear()
        grown = read_committed_snapshot(**records)
        assert len(spawns) > 1 and set(answers.artifacts) < set(grown.artifacts)
    assert read_committed_snapshot(**records) == grown


def test_a_kept_artifact_survives_an_unrelated_write(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    resolved = gig.resolved
    path = next(iter(_profiles_snapshot(gig).artifacts))
    read = dict(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=path)
    with committed_read_cache():
        first = read_committed_artifact(**read)
        _answer(gig, 4)
        spawns.clear()
        assert read_committed_artifact(**read) == first
        assert len(spawns) <= 1  # at most the one question about the commits since (the write may have asked it)
    assert read_committed_artifact(**read) == first


def test_the_commits_between_two_heads_are_listed_only_along_a_straight_line(gig: SimpleNamespace) -> None:
    root = gig.resolved.path
    old = _git(gig, "rev-parse", "HEAD")
    _answer(gig, 5)
    middle = _git(gig, "rev-parse", "HEAD")
    _answer(gig, 6)
    new = _git(gig, "rev-parse", "HEAD")

    touched = paths_committed_between(root, old, new)
    assert touched is not None
    listed = set(_git(gig, "diff", "--name-only", old, new).splitlines())
    assert touched == listed and any(name.startswith("records/record_") for name in touched) and any(name.startswith("handoffs/") for name in touched)
    assert paths_committed_between(root, middle, new) < touched  # type: ignore[operator]

    # Not a descendant (the heads the other way round), the same head, an unknown head: nothing is trusted.
    assert paths_committed_between(root, new, old) is None
    assert paths_committed_between(root, new, new) is None
    assert paths_committed_between(root, "0" * 40, new) is None


def test_a_history_that_is_not_a_straight_line_keeps_nothing(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    """A head that is not a descendant of the one a read was kept at: the read is made again."""

    with committed_read_cache():
        _answer(gig, 7)
        ahead = _profiles_snapshot(gig)  # kept at the newer head
        # The journal never does this; a hand-made reset is what "not a straight line" looks like.
        _git(gig, "reset", "--hard", "--quiet", "HEAD~1")
        spawns.clear()
        behind = _profiles_snapshot(gig)
        assert behind.head != ahead.head and len(spawns) > 1
    assert _profiles_snapshot(gig) == behind


# --------------------------------------------------------------------------
# What is kept is bounded, and a snapshot says when it is the same one
# --------------------------------------------------------------------------


def test_kept_reads_are_bounded_by_bytes_least_recently_used_first() -> None:
    kept = journal._KeptReads(100)
    kept.put(("a",), ("f",), "A", 40)
    kept.put(("b",), ("f",), "B", 40)
    assert kept.get(("a",)) == (("f",), "A")  # "a" is now the most recently used
    kept.put(("c",), ("f",), "C", 40)  # over the bound: "b" goes
    assert kept.get(("b",)) is None and kept.get(("a",)) is not None and kept.get(("c",)) is not None
    assert (kept.bytes, len(kept)) == (80, 2)

    kept.put(("big",), ("f",), "X", 101)  # larger than the bound: never kept, and nothing is dropped for it
    assert kept.get(("big",)) is None and kept.bytes == 80
    kept.put(("a",), ("g",), "A2", 10)  # a newer value replaces the older one's bytes
    assert kept.bytes == 50 and kept.get(("a",)) == (("g",), "A2")
    kept.clear()
    assert kept == {} and kept.bytes == 0


def test_a_snapshot_larger_than_the_bound_is_read_every_time(gig: SimpleNamespace, spawns: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(journal._snapshot_cache, "max_bytes", 16)
    plain = _profiles_snapshot(gig)
    with committed_read_cache():
        first = _profiles_snapshot(gig)
        spawns.clear()
        again = _profiles_snapshot(gig)
        assert spawns and journal._snapshot_cache == {}
    assert first == plain and again == plain
    assert first.read_token is not again.read_token  # two reads: nothing says they are the same


def test_the_read_token_names_the_same_artifacts_across_heads(gig: SimpleNamespace) -> None:
    assert _profiles_snapshot(gig).read_token is None  # outside a read: nothing is said
    with committed_read_cache():
        first = _profiles_snapshot(gig)
        assert first.read_token is not None and _profiles_snapshot(gig).read_token is first.read_token

        _answer(gig, 8)  # an unrelated write: another head, the same artifacts, the same token
        moved = _profiles_snapshot(gig)
        assert moved.head != first.head and moved.read_token is first.read_token and moved.artifacts == first.artifacts

        profile_records.create_profile(
            gig.resolved, label="Third", titles=("sre",), titles_to_avoid=(), queries=("sre",), resume_ref=gig.profile.resume_ref,
        )
        changed = _profiles_snapshot(gig)
        assert changed.read_token is not None and changed.read_token is not first.read_token
        assert len(changed.artifacts) == len(first.artifacts) + 1
    # The token never takes part in equality: a snapshot is its head and its artifacts.
    assert _profiles_snapshot(gig) == changed
