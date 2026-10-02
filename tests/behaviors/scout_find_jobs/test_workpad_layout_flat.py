"""0110-043: the workpad check does not walk the whole journal on every first read.

``workpad_layout_version`` proves the layout marker
(``manifests/workpad-layout.json``) has exactly one publisher. It did that with
``git log -- <marker>``, which visits every journal commit of any kind, on the
first read of every route after a server start and in every CLI command. Now
the walk's answer for a journal head is kept in the workpad's private
``scratch/workpad-layout-check.json`` and carried to a later head by one
listing of only the new commits. These tests pin, by subprocess counts and by
equality (never wall time):

* **flat**: the check costs the same subprocesses at 10, 204, 1,212 and 3,000
  journal commits, and none of them walks the journal;
* **the walk is still the authority**: the first check walks once; whatever the
  kept answer cannot settle is walked again; and every refusal is the walk's,
  word for word (a second publisher committed after the answer was kept, an
  edited marker, edited ignore rules, a rewritten or reset history, a changed
  ownership marker, a remote, another Gig);
* **the file is only a cache**: deleted, empty, garbage, another schema, another
  Gig, another project, an unknown head, a forged publisher or blob, something
  that is not a file: the walk answers, and the answer is the same.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import gigai.journal as journal
import gigai.workpad as workpad_module
from gigai.journal import JournalConflictError, read_committed_snapshot
from gigai.workpad import (
    LAYOUT_CHECK_DIRECTORY,
    LAYOUT_CHECK_FILENAME,
    LAYOUT_CHECK_SCHEMA,
    WORKPAD_LAYOUT_PATH,
    WorkpadConflictError,
    WorkpadError,
    committed_read_cache,
    resolve_workpad,
    workpad_layout_version,
)

from .test_m1_end_to_end import _fixture


SIZES = (10, 204, 1212, 3000)
INVALID = ("WorkpadConflictError", "workpad layout marker is invalid")


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
    home, target, _workpad = _fixture(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
    return SimpleNamespace(home=home, target=target, resolved=resolved)


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


# --- helpers ---------------------------------------------------------------------------


def _git(root: Path, *args: str, data: bytes | None = None) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args],
        input=data, capture_output=True, check=True,
    )
    return done.stdout.decode("utf-8").strip()


def journal_commit_count(root: Path) -> int:
    return int(_git(root, "rev-list", "--count", "HEAD"))


def grow_journal(root: Path, commits: int) -> None:
    """Add ``commits`` commits to the workpad's journal, each adding one file, and check them out.

    Synthetic history for the workpad check only (``git fast-import``): the
    commits are not journal transactions, and nothing here reads them as such.
    """

    if commits <= 0:
        return
    start = journal_commit_count(root)
    stream: list[bytes] = []
    for index in range(start, start + commits):
        message = f"synthetic journal commit {index}\n".encode()
        content = json.dumps({"n": index}).encode() + b"\n"
        stream.append(b"commit refs/heads/main\n")
        stream.append(b"committer GigAI Journal <local@gigai.invalid> %d +0000\n" % (1_700_000_000 + index))
        stream.append(b"data %d\n%s" % (len(message), message))
        if index == start:
            stream.append(b"from refs/heads/main^0\n")
        stream.append(b"M 100644 inline records/synthetic-history/%06d.json\n" % index)
        stream.append(b"data %d\n%s\n" % (len(content), content))
    _git(root, "fast-import", "--quiet", "--date-format=raw", data=b"".join(stream))
    _git(root, "read-tree", "HEAD")
    _git(root, "checkout-index", "--all", "--force")


def grow_journal_to(root: Path, commits: int) -> None:
    grow_journal(root, commits - journal_commit_count(root))


def _check(gig: SimpleNamespace, *, gig_id: str | None = None) -> int:
    resolved = gig.resolved
    return workpad_layout_version(resolved.path, project_id=resolved.project_id, gig_id=gig_id or resolved.gig_id)


def _outcome(gig: SimpleNamespace) -> tuple[object, ...]:
    try:
        return ("admitted", _check(gig))
    except WorkpadError as exc:
        return (type(exc).__name__, str(exc))


def _kept_file(gig: SimpleNamespace) -> Path:
    return gig.resolved.path / LAYOUT_CHECK_DIRECTORY / LAYOUT_CHECK_FILENAME


def _kept(gig: SimpleNamespace) -> dict[str, str]:
    return json.loads(_kept_file(gig).read_bytes())


def _keep(gig: SimpleNamespace) -> dict[str, str]:
    """Run the check so its answer is kept at the current head; the kept record."""

    assert _check(gig) == 2
    kept = _kept(gig)
    assert kept["head"] == _git(gig.resolved.path, "rev-parse", "HEAD")
    return kept


def _git_words(call: list[str]) -> list[str]:
    """A git call's words after ``git -C <root>``."""

    return call[3:]


def _walks(spawns: list[list[str]]) -> list[list[str]]:
    """The calls that walk the journal for the marker: a ``git log`` with no ``old..new`` range."""

    return [
        _git_words(call) for call in spawns
        if _git_words(call)[:1] == ["log"] and not any(".." in word for word in _git_words(call))
    ]


def _both_ways(gig: SimpleNamespace, spawns: list[list[str]]) -> tuple[object, ...]:
    """The check's outcome with the kept answer in place, which must be the walk's outcome without it."""

    before = _kept_file(gig).read_bytes()
    assert workpad_module._read_layout_check(gig.resolved.path, gig.resolved.project_id, gig.resolved.gig_id) is not None
    spawns.clear()
    with_kept = _outcome(gig)
    if with_kept[0] != "admitted":
        assert _walks(spawns), "a refusal must be the walk's"
        assert _kept_file(gig).read_bytes() == before, "a refusal is never kept"
    _kept_file(gig).unlink()
    walked = _outcome(gig)
    assert with_kept == walked
    return with_kept


def _commit_marker(gig: SimpleNamespace, content: bytes | None, message: str) -> None:
    """A later commit that touches the marker (``None``: removes it), working file included."""

    root = gig.resolved.path
    if content is None:
        _git(root, "rm", "--quiet", WORKPAD_LAYOUT_PATH)
    else:
        (root / WORKPAD_LAYOUT_PATH).parent.mkdir(exist_ok=True)
        (root / WORKPAD_LAYOUT_PATH).write_bytes(content)
        _git(root, "add", WORKPAD_LAYOUT_PATH)
    _git(root, "commit", "--quiet", "-m", message)


# --- flat ------------------------------------------------------------------------------


def test_the_check_costs_the_same_at_every_history_length(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    root = gig.resolved.path
    _keep(gig)
    counts: dict[int, tuple[int, int]] = {}
    for size in SIZES:
        assert journal_commit_count(root) <= size
        grow_journal_to(root, size)
        assert journal_commit_count(root) == size
        spawns.clear()
        assert _check(gig) == 2  # the head moved: caught up over the new commits only
        caught_up = [_git_words(call) for call in spawns]
        assert _walks(spawns) == []
        ranged = [words for words in caught_up if words[0] == "log"]
        assert len(ranged) == 1 and any(".." in word for word in ranged[0])
        assert ranged[0][-2:] == ["--", WORKPAD_LAYOUT_PATH]  # every new commit is listed, looked at for the marker only
        assert _kept(gig)["head"] == _git(root, "rev-parse", "HEAD")

        spawns.clear()
        assert _check(gig) == 2  # the head is the kept one
        assert [words[0] for words in map(_git_words, spawns)] == ["show", "cat-file"]
        counts[size] = (len(caught_up), len(spawns))
    assert set(counts.values()) == {(3, 2)}, counts


def test_resolving_the_workpad_costs_the_same_at_every_history_length(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    def resolve() -> None:
        resolve_workpad(home_root=gig.home, requested_target=gig.target, gig_id=None, allow_semantic_state=True)

    resolve()
    counts = set()
    for size in SIZES:
        grow_journal_to(gig.resolved.path, size)
        resolve()
        spawns.clear()
        resolve()
        assert _walks(spawns) == []
        counts.add(len(spawns))
    assert len(counts) == 1 and counts.pop() <= 10


def test_without_the_kept_answer_the_walk_visits_the_whole_journal_once(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    """What every check paid before: the walk, pinned to the head it answers for; then never again."""

    root = gig.resolved.path
    grow_journal_to(root, 204)
    head = _git(root, "rev-parse", "HEAD")
    _kept_file(gig).unlink(missing_ok=True)
    spawns.clear()
    assert _check(gig) == 2
    assert _walks(spawns) == [["log", "--format=%H", head, "--", WORKPAD_LAYOUT_PATH]]
    assert len(spawns) == 6  # the walk's five, and the marker's blob at the head for the kept record
    kept = _kept(gig)
    assert kept == {
        "schema": LAYOUT_CHECK_SCHEMA,
        "project_id": gig.resolved.project_id,
        "gig_id": gig.resolved.gig_id,
        "head": head,
        "publisher": _git(root, "log", "--format=%H", "--", WORKPAD_LAYOUT_PATH),
        "marker_blob": _git(root, "rev-parse", f"HEAD:{WORKPAD_LAYOUT_PATH}"),
    }
    spawns.clear()
    assert _check(gig) == 2
    assert _walks(spawns) == [] and len(spawns) == 2


def test_a_head_only_git_can_read_still_uses_the_kept_answer(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    root = gig.resolved.path
    _keep(gig)
    _git(root, "pack-refs", "--all")
    assert workpad_module.workpad_head_without_git(root) is None
    spawns.clear()
    assert _check(gig) == 2
    assert [words[0] for words in map(_git_words, spawns)] == ["rev-parse", "show", "cat-file"]


def test_the_kept_answer_is_private_scratch_and_leaves_the_journal_clean(gig: SimpleNamespace) -> None:
    root = gig.resolved.path
    _keep(gig)
    assert _git(root, "status", "--porcelain") == ""
    assert _git(root, "check-ignore", f"{LAYOUT_CHECK_DIRECTORY}/{LAYOUT_CHECK_FILENAME}") != ""
    assert [name for name in os.listdir(root / LAYOUT_CHECK_DIRECTORY) if name.startswith(".")] == []
    assert set(_kept(gig)) == {"schema", "project_id", "gig_id", "head", "publisher", "marker_blob"}


def test_a_workpad_with_no_marker_asks_nothing_and_keeps_nothing(tmp_path: Path, spawns: list[list[str]]) -> None:
    assert workpad_layout_version(tmp_path, project_id="project_x", gig_id="gig_x") == 1
    assert spawns == [] and os.listdir(tmp_path) == []


def test_a_journal_read_inside_a_read_never_walks(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    resolved = gig.resolved
    _keep(gig)
    grow_journal_to(resolved.path, 204)
    spawns.clear()
    with committed_read_cache():
        read_committed_snapshot(
            workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
            prefixes=("records/scout-profile-selection/",),
        )
    assert [words for words in _walks(spawns) if WORKPAD_LAYOUT_PATH in words] == []


# --- every refusal is still the walk's -------------------------------------------------


def test_a_second_publisher_committed_after_the_answer_was_kept_is_refused(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    root = gig.resolved.path
    _keep(gig)
    grow_journal(root, 3)
    _commit_marker(gig, (root / WORKPAD_LAYOUT_PATH).read_bytes() + b"\n", "a second publisher")
    grow_journal(root, 3)
    assert _both_ways(gig, spawns) == INVALID


def test_a_marker_removed_and_published_again_with_the_same_bytes_is_refused(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    """The marker's blob at the head is the kept one: only the commits in between say it was published again."""

    root = gig.resolved.path
    kept = _keep(gig)
    original = (root / WORKPAD_LAYOUT_PATH).read_bytes()
    _commit_marker(gig, None, "the marker removed")
    _commit_marker(gig, original, "the marker published again")
    assert _git(root, "rev-parse", f"HEAD:{WORKPAD_LAYOUT_PATH}") == kept["marker_blob"]
    assert _both_ways(gig, spawns) == INVALID


def test_a_marker_edited_in_the_working_tree_is_refused(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    root = gig.resolved.path
    _keep(gig)
    marker = root / WORKPAD_LAYOUT_PATH
    marker.write_bytes(marker.read_bytes().replace(b'"layout_version":2', b'"layout_version":3') + b" ")
    assert _both_ways(gig, spawns) == INVALID


def test_a_marker_that_is_redirected_is_refused(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    root = gig.resolved.path
    _keep(gig)
    marker = root / WORKPAD_LAYOUT_PATH
    elsewhere = root / "manifests" / "elsewhere.json"
    marker.rename(elsewhere)
    marker.symlink_to(elsewhere)
    spawns.clear()
    assert _outcome(gig) == ("WorkpadConflictError", "workpad layout marker is redirected")
    assert spawns == []


def test_ignore_rules_edited_in_the_working_tree_are_refused(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    root = gig.resolved.path
    _keep(gig)
    with (root / ".gitignore").open("ab") as stream:
        stream.write(b"/records/\n")
    assert _both_ways(gig, spawns) == INVALID


def test_another_gig_is_refused_and_does_not_take_the_kept_answer(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    kept = _keep(gig)
    other = "gig_00000000-0000-4000-8000-0000000000ff"
    spawns.clear()
    with pytest.raises(WorkpadConflictError, match="^workpad layout marker is invalid$"):
        _check(gig, gig_id=other)
    assert _walks(spawns), "the kept answer is this Gig's: another Gig is walked"
    assert _kept(gig) == kept


def test_a_changed_ownership_marker_is_refused(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    resolved = gig.resolved
    _keep(gig)

    def refusals() -> tuple[str, str]:
        with pytest.raises(WorkpadConflictError) as by_workpad:
            resolve_workpad(home_root=gig.home, requested_target=gig.target, gig_id=None, allow_semantic_state=True)
        with pytest.raises(JournalConflictError) as by_journal:
            journal._validate_workpad(resolved.path, resolved.project_id, resolved.gig_id)
        return str(by_workpad.value), str(by_journal.value)

    _git(resolved.path, "config", "--local", "gigai.gig-id", "gig_00000000-0000-4000-8000-000000000000")
    with_kept = refusals()
    assert with_kept == ("workpad Git ownership marker gigai.gig-id mismatches", "journal workpad ownership marker mismatches")
    _kept_file(gig).unlink()
    assert refusals() == with_kept

    _git(resolved.path, "config", "--local", "gigai.gig-id", resolved.gig_id)
    _keep(gig)
    _git(resolved.path, "remote", "add", "origin", "https://example.invalid/x.git")
    with_kept = refusals()
    assert with_kept == ("workpad must not configure a Git remote", "journal workpad has a remote")
    _kept_file(gig).unlink()
    assert refusals() == with_kept


def test_an_unexpected_top_level_entry_is_refused(gig: SimpleNamespace) -> None:
    _keep(gig)
    (gig.resolved.path / "not-a-workpad-root").mkdir()
    with pytest.raises(WorkpadConflictError, match="unexpected top-level state: not-a-workpad-root"):
        resolve_workpad(home_root=gig.home, requested_target=gig.target, gig_id=None, allow_semantic_state=True)


# --- a history that is not a straight line from the kept head --------------------------


def _move_head(root: Path, commit: str) -> None:
    """Point the journal at ``commit`` and check its tree out, in this synthetic workpad only."""

    _git(root, "update-ref", "refs/heads/main", commit)
    _git(root, "read-tree", "HEAD")
    _git(root, "checkout-index", "--all", "--force")


def test_a_journal_set_back_is_walked_again(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    root = gig.resolved.path
    grow_journal(root, 6)
    kept = _keep(gig)
    earlier = _git(root, "rev-parse", "HEAD~4")
    _move_head(root, earlier)
    spawns.clear()
    assert _check(gig) == 2
    assert _walks(spawns) == [["log", "--format=%H", earlier, "--", WORKPAD_LAYOUT_PATH]]
    assert _kept(gig) == {**kept, "head": earlier}
    assert _both_ways(gig, spawns) == ("admitted", 2)


def test_a_rewritten_history_is_walked_again(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    root = gig.resolved.path
    grow_journal(root, 4)
    _keep(gig)
    rewritten = _git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD~2", "-m", "another line of history")
    _move_head(root, rewritten)
    spawns.clear()
    assert _check(gig) == 2
    assert _walks(spawns) == [["log", "--format=%H", rewritten, "--", WORKPAD_LAYOUT_PATH]]
    assert _kept(gig)["head"] == rewritten


def test_a_rewritten_history_with_a_second_publisher_is_refused(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    root = gig.resolved.path
    grow_journal(root, 4)
    kept = _keep(gig)
    _commit_marker(gig, (root / WORKPAD_LAYOUT_PATH).read_bytes() + b"\n", "a second publisher")
    # The same tree on another line of history, which leaves the kept head out.
    rewritten = _git(root, "commit-tree", "HEAD^{tree}", "-p", f"{kept['head']}~2", "-m", "rewritten")
    _move_head(root, rewritten)
    assert _both_ways(gig, spawns) == INVALID


def test_a_journal_set_back_to_before_the_marker_is_refused(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    """The working marker is there and the journal at its head never published it."""

    root = gig.resolved.path
    _keep(gig)
    # The marker's publisher is the journal's first commit: a history without it starts somewhere else.
    empty_tree = _git(root, "mktree", data=b"")
    _git(root, "update-ref", "refs/heads/main", _git(root, "commit-tree", empty_tree, "-m", "a journal with no marker"))
    assert _both_ways(gig, spawns) == INVALID


def test_a_merge_is_walked(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    root = gig.resolved.path
    grow_journal(root, 3)
    kept = _keep(gig)
    merged = _git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-p", "HEAD~2", "-m", "a merge")
    _move_head(root, merged)
    spawns.clear()
    _outcome(gig)
    assert _walks(spawns) == [["log", "--format=%H", merged, "--", WORKPAD_LAYOUT_PATH]]
    assert kept["head"] != merged


# --- the file is only a cache ----------------------------------------------------------


def _rewrite_kept(gig: SimpleNamespace, **changed: object) -> None:
    _kept_file(gig).write_text(json.dumps({**_kept(gig), **changed}), encoding="utf-8")


def _some_other_commit(gig: SimpleNamespace) -> str:
    return _git(gig.resolved.path, "rev-parse", "HEAD")


CACHE_DAMAGE = {
    "deleted": lambda gig: _kept_file(gig).unlink(),
    "empty": lambda gig: _kept_file(gig).write_bytes(b""),
    "garbage": lambda gig: _kept_file(gig).write_bytes(b"\x00\xffnot json at all"),
    "not an object": lambda gig: _kept_file(gig).write_text("[1, 2, 3]", encoding="utf-8"),
    "too large": lambda gig: _kept_file(gig).write_bytes(b" " * 100_000 + _kept_file(gig).read_bytes()),
    "another schema": lambda gig: _rewrite_kept(gig, schema="workpad-layout-check/0"),
    "another gig": lambda gig: _rewrite_kept(gig, gig_id="gig_00000000-0000-4000-8000-0000000000ff"),
    "another project": lambda gig: _rewrite_kept(gig, project_id="project_00000000-0000-4000-8000-0000000000ff"),
    "an extra key": lambda gig: _rewrite_kept(gig, note="x"),
    "a missing key": lambda gig: _kept_file(gig).write_text(
        json.dumps({key: value for key, value in _kept(gig).items() if key != "marker_blob"}), encoding="utf-8"
    ),
    "an unknown head": lambda gig: _rewrite_kept(gig, head="0" * 40),
    "a head that is not a commit id": lambda gig: _rewrite_kept(gig, head="--all"),
    "a publisher that is not a commit id": lambda gig: _rewrite_kept(gig, publisher="HEAD"),
    "an unknown publisher": lambda gig: _rewrite_kept(gig, publisher="1" * 40),
    "a publisher that did not publish the marker": lambda gig: _rewrite_kept(gig, publisher=_some_other_commit(gig)),
    "another marker blob": lambda gig: _rewrite_kept(gig, marker_blob="2" * 40),
    "a marker blob of the wrong type": lambda gig: _rewrite_kept(gig, marker_blob=7),
}


@pytest.mark.parametrize("damage", sorted(CACHE_DAMAGE))
def test_a_kept_answer_that_cannot_be_used_is_walked_again(gig: SimpleNamespace, spawns: list[list[str]], damage: str) -> None:
    root = gig.resolved.path
    kept = _keep(gig)
    grow_journal(root, 2)
    kept_now = _keep(gig)
    assert kept_now == {**kept, "head": _git(root, "rev-parse", "HEAD")}
    CACHE_DAMAGE[damage](gig)
    spawns.clear()
    assert _check(gig) == 2
    assert _walks(spawns) == [["log", "--format=%H", kept_now["head"], "--", WORKPAD_LAYOUT_PATH]]
    assert _kept(gig) == kept_now  # written again from the walk
    spawns.clear()
    assert _check(gig) == 2
    assert _walks(spawns) == [] and len(spawns) == 2


@pytest.mark.parametrize("damage", ["an unknown head", "another marker blob", "a publisher that did not publish the marker", "deleted"])
def test_a_second_publisher_is_refused_whatever_the_file_says(gig: SimpleNamespace, spawns: list[list[str]], damage: str) -> None:
    root = gig.resolved.path
    _keep(gig)
    _commit_marker(gig, (root / WORKPAD_LAYOUT_PATH).read_bytes() + b"\n", "a second publisher")
    CACHE_DAMAGE[damage](gig)
    spawns.clear()
    assert _outcome(gig) == INVALID
    assert _walks(spawns)
    assert not _kept_file(gig).exists() or _kept(gig)["head"] != _git(root, "rev-parse", "HEAD")


def test_something_that_is_not_a_file_in_its_place_is_never_used_or_replaced(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    root = gig.resolved.path
    _keep(gig)
    _kept_file(gig).unlink()
    _kept_file(gig).mkdir()
    for _again in range(2):
        spawns.clear()
        assert _check(gig) == 2
        assert len(_walks(spawns)) == 1
    assert _kept_file(gig).is_dir()

    _kept_file(gig).rmdir()
    outside = root.parent / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    _kept_file(gig).symlink_to(outside)
    spawns.clear()
    assert _check(gig) == 2
    assert len(_walks(spawns)) == 1
    assert outside.read_text(encoding="utf-8") == "{}" and _kept_file(gig).is_symlink()


def test_a_scratch_directory_that_cannot_be_written_changes_nothing_but_the_cost(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    scratch = gig.resolved.path / LAYOUT_CHECK_DIRECTORY
    _keep(gig)
    _kept_file(gig).unlink()
    scratch.chmod(0o500)
    try:
        for _again in range(2):
            spawns.clear()
            assert _check(gig) == 2
            assert len(_walks(spawns)) == 1
        assert not _kept_file(gig).exists()
    finally:
        scratch.chmod(0o700)


def test_a_kept_answer_from_a_copy_of_another_workpad_is_not_used(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    """Another workpad's file names commits this journal does not have."""

    _keep(gig)
    _rewrite_kept(gig, head="a" * 40, publisher="b" * 40, marker_blob="c" * 40)
    spawns.clear()
    assert _check(gig) == 2
    assert len(_walks(spawns)) == 1
    assert _kept(gig)["head"] == _git(gig.resolved.path, "rev-parse", "HEAD")
