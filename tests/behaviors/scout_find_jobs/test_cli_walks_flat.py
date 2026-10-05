"""0110-044: ``gigai status`` and the other CLI reads do not grow with the journal's history.

Three costs grew with every journal commit of any kind:

* ``index._authoritative_projection`` (``gigai status`` / ``show`` / ``history``
  and every ``read_index``) asked git twice for every journal commit: 6,003
  ``git show`` calls and 37 s at 3,000 commits. The entries are now kept for a
  journal head in ``scratch/journal-index-entries.json`` and carried to a
  later head by one listing of only the new commits;
* a journal snapshot walked the whole journal for the publishers of its paths
  (``git log --name-only <head> -- <prefixes>``). The publishers of the
  families snapshots ask for are now kept for a journal head in
  ``scratch/journal-publishers.sqlite``;
* a read-only CLI command checked the workpad again for every resolve and every
  journal read. It now opens one ``committed_read_cache``.

These tests pin, by subprocess counts and by equality (never wall time):

* **flat**: the same subprocesses at 10, 204, 1,212 and 3,000 journal commits,
  none of them a walk of the journal;
* **the same answer**: the kept entries are the walk's entries, the kept
  publishers are the walk's publishers, a command prints the same bytes;
* **the walk is still the authority**: whatever a kept file cannot settle is
  walked, and every refusal is the walk's, word for word (an external write, a
  rewritten or reset history, a merge, a second publisher, a removed file, a
  changed working file, another Gig);
* **the files are only caches**: deleted, empty, garbage, another schema,
  another Gig, another project, an unknown head, forged rows, something that is
  not a file: the answer is the walk's.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace
import uuid

from click.testing import CliRunner
import pytest

import gigai.index as index_module
import gigai.journal as journal
import gigai.workpad as workpad_module
from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.cli import cli
from gigai.index import ENTRIES_FILENAME, ENTRIES_SCHEMA, JournalIndexError, read_authoritative_index, read_index
from gigai.journal import PUBLISHERS_FILENAME, PUBLISHERS_SCHEMA, JournalError, read_committed_snapshot
from gigai.workpad import committed_read_cache, resolve_workpad, straight_history

from .test_m1_end_to_end import _fixture


SIZES = (10, 204, 1212, 3000)
SYNTHETIC = "records/synthetic-history/"
_GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def _forget() -> None:
    """As a process that just started: nothing checked or read earlier in this process is kept (the files stay)."""

    for cache in (
        workpad_module._validated_repositories,
        workpad_module._resolved_targets,
        workpad_module._committed_between,
        journal._validated_workpads,
        journal._snapshot_cache,
        journal._artifact_cache,
    ):
        cache.clear()


@pytest.fixture
def gig(tmp_path: Path) -> SimpleNamespace:
    _forget()
    home, target, _workpad = _fixture(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
    return SimpleNamespace(home=home, target=target, resolved=resolved, root=resolved.path)


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
        input=data, capture_output=True, check=True, env=_GIT_ENV,
    )
    return done.stdout.decode("utf-8").strip()


def _head(gig: SimpleNamespace) -> str:
    return _git(gig.root, "rev-parse", "HEAD")


def _count(gig: SimpleNamespace) -> int:
    return int(_git(gig.root, "rev-list", "--count", "HEAD"))


def _handoff(gig: SimpleNamespace, sequence: int, artifacts: dict[str, bytes], *, gig_id: str | None = None, **front: object) -> tuple[str, bytes, str]:
    """A journal handoff as the journal writes one: ``(path, document, commit message)``."""

    handoff_id = f"handoff_{uuid.uuid4()}"
    refs = [
        {"path": path, "content_sha256": digest_imported_bytes(content), "media_type": "application/json", "size_bytes": len(content)}
        for path, content in artifacts.items()
    ]
    document = journal._render_handoff(
        sequence, gig_id or gig.resolved.gig_id, handoff_id, "private_record_revised", "synthetic history", None,
        front_matter={"artifact_refs": refs, **front},
    )
    message = f"journal: private record revised\n\n{journal.SEQUENCE_TRAILER}: {sequence:012d}\n{journal.HANDOFF_TRAILER}: {handoff_id}\n"
    return f"handoffs/{sequence:012d}-private-record-revised.txt", document, message


def grow_journal(gig: SimpleNamespace, commits: int) -> None:
    """Add ``commits`` journal commits (``git fast-import``), each one handoff that publishes one record, and check them out.

    Every commit is what the journal itself writes for a transition with one
    artifact: the handoff names the record with its digest, the message carries
    the trailers. So the index reads them as entries and a snapshot proves the
    records, at 3,000 commits in about a second.
    """

    if commits <= 0:
        return
    start = _count(gig)
    stream: list[bytes] = []
    for sequence in range(start + 1, start + commits + 1):
        content = json.dumps({"n": sequence}).encode() + b"\n"
        record = f"{SYNTHETIC}{sequence:06d}.json"
        path, document, message = _handoff(gig, sequence, {record: content})
        stream.append(b"commit refs/heads/main\n")
        stream.append(b"committer GigAI Journal <local@gigai.invalid> %d +0000\n" % (1_700_000_000 + sequence))
        stream.append(b"data %d\n%s" % (len(message.encode()), message.encode()))
        if sequence == start + 1:
            stream.append(b"from refs/heads/main^0\n")
        for name, data in ((path, document), (record, content)):
            stream.append(b"M 100644 inline %s\n" % name.encode())
            stream.append(b"data %d\n%s\n" % (len(data), data))
    _git(gig.root, "fast-import", "--quiet", "--date-format=raw", data=b"".join(stream))
    _git(gig.root, "read-tree", "HEAD")
    _git(gig.root, "checkout-index", "--all", "--force")


def grow_journal_to(gig: SimpleNamespace, commits: int) -> None:
    grow_journal(gig, commits - _count(gig))


def _commit(gig: SimpleNamespace, files: dict[str, bytes | None], message: str = "written around the journal") -> str:
    """A commit made around the journal: each file written (``None``: removed), working tree included."""

    for name, content in files.items():
        path = gig.root / name
        if content is None:
            _git(gig.root, "rm", "--quiet", "--", name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            _git(gig.root, "add", "--", name)
    _git(gig.root, "commit", "--quiet", "--allow-empty", "-m", message)
    return _head(gig)


def _journal_commit(gig: SimpleNamespace, artifacts: dict[str, bytes], *, sequence: int | None = None, **handoff: object) -> str:
    """One more valid journal commit made by another writer (``sequence``: default the next one)."""

    path, document, message = _handoff(gig, sequence or _count(gig) + 1, artifacts, **handoff)
    return _commit(gig, {path: document, **artifacts}, message)


def _words(call: list[str]) -> list[str]:
    """A git call's words after ``git -C <root>`` and its ``-c`` settings."""

    words = call[3:]
    while words[:1] == ["-c"]:
        words = words[2:]
    return words


def _verbs(spawns: list[list[str]]) -> list[str]:
    return [_words(call)[0] if Path(call[0]).name == "git" else Path(call[0]).name for call in spawns]


def _logs(spawns: list[list[str]], *, ranged: bool) -> list[list[str]]:
    """The ``git log`` calls that list a range of commits (``old..new``), or the whole journal."""

    return [
        _words(call) for call in spawns
        if _words(call)[:1] == ["log"] and any(".." in word for word in _words(call)) == ranged
    ]


def _commit_by_commit(spawns: list[list[str]]) -> list[list[str]]:
    """The index's walk of every commit, which starts with ``git rev-list``."""

    return [_words(call) for call in spawns if _words(call)[:1] == ["rev-list"]]


@contextmanager
def _walking():
    """Without any kept file: the index walks commit by commit, a snapshot walks the journal (what ran before 0110-044)."""

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(index_module, "_kept_entries", lambda *args, **kwargs: None)
        patch.setattr(index_module, "_keep_entries", lambda *args, **kwargs: None)
        patch.setattr(journal, "_kept_publishing_commits", lambda *args, **kwargs: None)
        yield


def _identity(gig: SimpleNamespace, gig_id: str | None = None) -> dict[str, object]:
    return {"workpad": gig.root, "project_id": gig.resolved.project_id, "gig_id": gig_id or gig.resolved.gig_id}


# --- the journal's entries (``gigai status``) ---------------------------------------------


def _entries_file(gig: SimpleNamespace) -> Path:
    return gig.root / "scratch" / ENTRIES_FILENAME


def _kept_index(gig: SimpleNamespace) -> tuple[str, list[dict[str, object]]] | None:
    return index_module._read_kept_entries(gig.root, gig.resolved.project_id, gig.resolved.gig_id)


def _keep_index(gig: SimpleNamespace) -> None:
    """Read the index so its entries are kept at the current head."""

    read_authoritative_index(**_identity(gig))
    kept = _kept_index(gig)
    assert kept is not None and kept[0] == _head(gig) and len(kept[1]) == _count(gig)


def _index_outcome(gig: SimpleNamespace, *, read=read_authoritative_index, gig_id: str | None = None) -> tuple[object, ...]:
    try:
        return ("read", canonical_json_bytes(read(**_identity(gig, gig_id)).as_dict()))
    except (JournalIndexError, ValueError) as exc:
        return (type(exc).__name__, str(exc))


def _index_both_ways(gig: SimpleNamespace, spawns: list[list[str]], *, before_the_walk: bool = False, **how) -> tuple[object, ...]:
    """The index's outcome with the kept entries in place, which must be the commit-by-commit walk's.

    ``before_the_walk``: a refusal made before the entries are looked at at all (the clean check).
    """

    before = _entries_file(gig).read_bytes()
    assert _kept_index(gig) is not None
    spawns.clear()
    with_kept = _index_outcome(gig, **how)
    if with_kept[0] != "read":
        assert bool(_commit_by_commit(spawns)) != before_the_walk, "a refusal must be the walk's"
        assert _entries_file(gig).read_bytes() == before, "a refusal is never kept"
    with _walking():
        walked = _index_outcome(gig, **how)
    assert with_kept == walked
    return with_kept


def test_the_index_costs_the_same_at_every_size(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    _entries_file(gig).unlink(missing_ok=True)
    for size in SIZES:
        grow_journal_to(gig, size)
        spawns.clear()
        first = read_index(**_identity(gig))
        # The first read at a new head lists the new commits once (limited to
        # the handoffs) and reads their handoffs in one batch.
        assert _verbs(spawns) == ["status", "log", "cat-file", "show", "show"], (size, _verbs(spawns))
        listing = [_words(call) for call in spawns if _words(call)[:1] == ["log"]][0]
        assert listing[-2:] == ["--", "handoffs/"] and ("--full-history" in listing)
        assert any(".." in word for word in listing) == (size != SIZES[0]), "after the first, only the new commits are listed"
        spawns.clear()
        again = read_index(**_identity(gig))
        # Then: the clean check and the two manifests at the head. Nothing per commit.
        assert _verbs(spawns) == ["status", "show", "show"], (size, _verbs(spawns))
        assert not _commit_by_commit(spawns)
        assert len(again.entries) == size and again.head == _head(gig)
        assert [entry["sequence"] for entry in again.entries] == list(range(1, size + 1))
        assert canonical_json_bytes(first.as_dict()) == canonical_json_bytes(again.as_dict())


def test_the_kept_entries_are_the_walks_entries(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    _keep_index(gig)  # at the fixture's own commits
    grow_journal_to(gig, 204)
    _journal_commit(gig, {f"{SYNTHETIC}changed.json": b"{}\n"})
    head, walked = index_module._walked_entries(gig.root, gig.resolved.gig_id)
    assert head == _head(gig) and len(walked) == 205
    caught_up = read_authoritative_index(**_identity(gig))
    assert list(caught_up.entries) == walked
    assert [list(entry) for entry in caught_up.entries] == [list(entry) for entry in walked], "the same keys in the same order"
    read_back = read_authoritative_index(**_identity(gig))
    _entries_file(gig).unlink()
    spawns.clear()
    listed_once = read_authoritative_index(**_identity(gig))
    assert not _commit_by_commit(spawns) and len(_logs(spawns, ranged=False)) == 1
    with _walking():
        reference = read_authoritative_index(**_identity(gig))
    for projection in (caught_up, read_back, listed_once):
        assert canonical_json_bytes(projection.as_dict()) == canonical_json_bytes(reference.as_dict())


def test_the_entries_file_holds_no_more_than_the_entries(gig: SimpleNamespace) -> None:
    _keep_index(gig)
    header, _newline, body = _entries_file(gig).read_bytes().partition(b"\n")
    assert set(json.loads(header)) == {"schema", "project_id", "gig_id", "head", "count", "body_sha256"}
    assert json.loads(header)["schema"] == ENTRIES_SCHEMA
    assert all(list(entry) == ["commit", "handoff_id", "path", "sequence", "transition"] for entry in json.loads(body))
    assert _git(gig.root, "status", "--porcelain", "--untracked-files=all") == ""
    assert sorted(path.name for path in (gig.root / "scratch").iterdir() if path.name.startswith(".")) == []


def test_a_valid_write_by_another_process_is_caught_up(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    _keep_index(gig)
    base = _count(gig)
    for number in range(3):
        _journal_commit(gig, {f"{SYNTHETIC}other-{number}.json": b"{}\n"})
    assert _index_both_ways(gig, spawns)[0] == "read"
    spawns.clear()
    projection = read_authoritative_index(**_identity(gig))
    assert _verbs(spawns) == ["show", "show"] and len(projection.entries) == base + 3
    assert _kept_index(gig)[0] == _head(gig)  # type: ignore[index]


REFUSED_HISTORIES = {
    "a commit with no handoff": ("JournalIndexError", "authoritative journal commit does not contain exactly one handoff"),
    "a commit with two handoffs": ("JournalIndexError", "authoritative journal commit does not contain exactly one handoff"),
    "a sequence that skips": ("JournalIndexError", "authoritative journal sequence diverges"),
    "a handoff of another Gig": ("JournalIndexError", "authoritative journal Gig identity diverges"),
    "a handoff with no transition": ("JournalIndexError", "authoritative journal handoff lacks identity"),
    "an earlier handoff changed": ("JournalIndexError", "authoritative journal commit does not contain exactly one handoff"),
    "an earlier handoff removed": ("JournalIndexError", "authoritative journal commit does not contain exactly one handoff"),
}


@pytest.mark.parametrize("history", [*REFUSED_HISTORIES, "a handoff that is not a handoff", "only a handoff removed"])
def test_a_journal_that_is_refused_is_refused_by_the_walk(gig: SimpleNamespace, spawns: list[list[str]], history: str) -> None:
    _keep_index(gig)
    _journal_commit(gig, {f"{SYNTHETIC}before.json": b"{}\n"})
    following = _count(gig) + 1
    earlier = f"handoffs/{following - 1:012d}-private-record-revised.txt"
    if history == "a commit with no handoff":
        _commit(gig, {f"{SYNTHETIC}stray.json": b"{}\n"})
    elif history == "a commit with two handoffs":
        path, document, message = _handoff(gig, following, {})
        _commit(gig, {path: document, path.replace("-private-", "-second-private-"): document}, message)
    elif history == "a sequence that skips":
        _journal_commit(gig, {}, sequence=following + 1)
    elif history == "a handoff of another Gig":
        _journal_commit(gig, {}, gig_id=f"gig_{uuid.uuid4()}")
    elif history == "a handoff with no transition":
        _journal_commit(gig, {}, transition=None)
    elif history == "an earlier handoff changed":
        path, document, message = _handoff(gig, following, {})
        _commit(gig, {path: document, earlier: (gig.root / earlier).read_bytes() + b"more\n"}, message)
    elif history == "an earlier handoff removed":
        path, document, message = _handoff(gig, following, {})
        _commit(gig, {path: document, earlier: None}, message)
    elif history == "a handoff that is not a handoff":
        _commit(gig, {f"handoffs/{following:012d}-private-record-revised.txt": b"not front matter\n"})
    else:
        _commit(gig, {earlier: None})
    _journal_commit(gig, {f"{SYNTHETIC}after.json": b"{}\n"}, sequence=following + 1)
    outcome = _index_both_ways(gig, spawns)
    assert outcome[0] != "read"
    if history in REFUSED_HISTORIES:
        assert outcome == REFUSED_HISTORIES[history]


def test_a_rewritten_a_reset_and_a_merged_history_are_read_as_the_walk_reads_them(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    grow_journal_to(gig, 20)
    _keep_index(gig)
    # Set back: the kept head is no longer in the history.
    _git(gig.root, "reset", "--quiet", "--hard", "HEAD~4")
    assert _index_both_ways(gig, spawns)[0] == "read"
    assert _kept_index(gig)[0] == _head(gig) and len(_kept_index(gig)[1]) == 16  # type: ignore[index]
    # Rewritten onto another line from an earlier commit.
    _git(gig.root, "reset", "--quiet", "--hard", "HEAD~3")
    grow_journal(gig, 5)
    assert _index_both_ways(gig, spawns)[0] == "read"
    assert len(read_authoritative_index(**_identity(gig)).entries) == 18
    # A merge is never a straight line: whatever the walk says, stands.
    _keep_index(gig)
    _git(gig.root, "branch", "side", "HEAD~1")
    _git(gig.root, "merge", "--quiet", "--no-ff", "--no-edit", "side", "-m", "a merge")
    side = _head(gig)
    _git(gig.root, "checkout", "--quiet", "side")
    _commit(gig, {f"{SYNTHETIC}side.json": b"{}\n"})
    _git(gig.root, "checkout", "--quiet", "main")
    _git(gig.root, "merge", "--quiet", "--no-ff", "--no-edit", "side", "-m", "a merge")
    assert _head(gig) != side
    assert _index_both_ways(gig, spawns)[0] != "read"


def test_another_gig_and_an_uncommitted_file_are_refused_as_before(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    _keep_index(gig)
    other = f"gig_{uuid.uuid4()}"
    assert _index_both_ways(gig, spawns, gig_id=other) == ("JournalIndexError", "authoritative journal Gig identity diverges")
    assert _kept_index(gig) is not None, "the real Gig's file is left as it was"
    (gig.root / "records" / "stray.json").write_bytes(b"{}\n")
    assert _index_both_ways(gig, spawns, read=read_index, before_the_walk=True) == ("JournalIndexError", "authoritative workpad has uncommitted divergence")


def _damage_entries(gig: SimpleNamespace, damage: str) -> None:
    path = _entries_file(gig)
    header_bytes, _newline, body = path.read_bytes().partition(b"\n")
    header, entries = json.loads(header_bytes), json.loads(body)

    def write(new_header: dict[str, object], new_entries: object, *, digest: bool = True) -> None:
        new_body = json.dumps(new_entries, separators=(",", ":")).encode()
        if digest:
            new_header = {**new_header, "body_sha256": digest_imported_bytes(new_body)}
        path.write_bytes(json.dumps(new_header, sort_keys=True).encode() + b"\n" + new_body)

    if damage == "deleted":
        path.unlink()
    elif damage == "empty":
        path.write_bytes(b"")
    elif damage == "garbage":
        path.write_bytes(b"\x00\xff not json")
    elif damage == "one line":
        path.write_bytes(header_bytes)
    elif damage == "not an object":
        path.write_bytes(b"[]\n[]")
    elif damage == "another schema":
        write({**header, "schema": "journal-index-entries/0"}, entries)
    elif damage == "another gig":
        write({**header, "gig_id": f"gig_{uuid.uuid4()}"}, entries)
    elif damage == "another project":
        write({**header, "project_id": f"project_{uuid.uuid4()}"}, entries)
    elif damage == "an extra key":
        write({**header, "more": 1}, entries)
    elif damage == "a missing key":
        write({key: value for key, value in header.items() if key != "count"}, entries)
    elif damage == "an unknown head":
        write({**header, "head": "0" * 40}, [*entries[:-1], {**entries[-1], "commit": "0" * 40}])
    elif damage == "a head that is not a commit id":
        write({**header, "head": "--all"}, [*entries[:-1], {**entries[-1], "commit": "--all"}])
    elif damage == "a body edited after it was written":
        write(header, [{**entries[0], "transition": "forged"}, *entries[1:]], digest=False)
    elif damage == "a count that is not the entries'":
        write({**header, "count": len(entries) + 1}, entries)
    elif damage == "an entry too few":
        write({**header, "count": len(entries) - 1}, [*entries[:3], *entries[4:]])
    elif damage == "an entry with another shape":
        write(header, [{**entries[0], "more": 1}, *entries[1:]])
    elif damage == "a last entry that is not the head":
        write(header, [*entries[:-1], {**entries[-1], "commit": entries[0]["commit"]}])
    elif damage == "an earlier head of this journal":
        write({**header, "head": entries[5]["commit"], "count": 6}, entries[:6])
    else:
        raise AssertionError(damage)


@pytest.mark.parametrize("damage", [
    "deleted", "empty", "garbage", "one line", "not an object", "another schema", "another gig", "another project",
    "an extra key", "a missing key", "an unknown head", "a head that is not a commit id",
    "a body edited after it was written", "a count that is not the entries'", "an entry too few",
    "an entry with another shape", "a last entry that is not the head", "an earlier head of this journal",
])
def test_an_entries_file_that_cannot_be_used_is_only_a_cache(gig: SimpleNamespace, spawns: list[list[str]], damage: str) -> None:
    _keep_index(gig)
    with _walking():
        reference = _index_outcome(gig)
    _damage_entries(gig, damage)
    spawns.clear()
    assert _index_outcome(gig) == reference
    assert not any("--all" in call for call in spawns), "what the file says is never passed to git unless it is a commit id"
    # Listed once; a head git does not know is asked for once more, then listed from the first commit.
    assert len([call for call in spawns if _words(call)[:1] == ["log"]]) == (2 if damage == "an unknown head" else 1)
    assert not _commit_by_commit(spawns)
    kept = _kept_index(gig)
    assert kept is not None and kept[0] == _head(gig), "written again"
    spawns.clear()
    assert _index_outcome(gig) == reference
    assert _verbs(spawns) == ["show", "show"]


@pytest.mark.parametrize("kind", ["a directory", "a symlink", "no scratch directory", "a redirected scratch directory"])
def test_something_that_is_not_a_file_at_the_entries_name_is_never_used(gig: SimpleNamespace, tmp_path: Path, kind: str) -> None:
    _keep_index(gig)
    with _walking():
        reference = _index_outcome(gig)
    path = _entries_file(gig)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ENTRIES_FILENAME).write_bytes(path.read_bytes())
    if kind == "a directory":
        path.unlink()
        path.mkdir()
    elif kind == "a symlink":
        path.unlink()
        path.symlink_to(elsewhere / ENTRIES_FILENAME)
    else:
        for item in path.parent.iterdir():
            item.unlink()
        path.parent.rmdir()
        if kind == "a redirected scratch directory":
            path.parent.symlink_to(elsewhere, target_is_directory=True)
    before = (elsewhere / ENTRIES_FILENAME).read_bytes()
    _journal_commit(gig, {f"{SYNTHETIC}one-more.json": b"{}\n"})
    outcome = _index_outcome(gig)
    with _walking():
        assert outcome == _index_outcome(gig) and outcome[0] == "read" and outcome != reference
    assert (elsewhere / ENTRIES_FILENAME).read_bytes() == before, "never written through"
    if kind == "a directory":
        assert path.is_dir() and not any(path.iterdir())
    elif kind == "no scratch directory":
        assert _kept_index(gig) is not None, "the directory is made again"


# --- the publishers of every path (the snapshot walk) -------------------------------------


SELECTIONS = (
    ("records/",),
    (SYNTHETIC,),
    ("records/", SYNTHETIC),
    ("handoffs/",),
    ("manifests/",),
    ("references/", "run-inputs/"),
    ("nothing-here/",),
    ("records/", "runs/", "run-plans/", "references/", "run-inputs/", "manifests/"),
)


def _publishers_file(gig: SimpleNamespace) -> Path:
    return gig.root / "scratch" / PUBLISHERS_FILENAME


def _publishers_meta(gig: SimpleNamespace) -> dict[str, str]:
    connection = sqlite3.connect(_publishers_file(gig))
    try:
        return dict(connection.execute("SELECT key, value FROM meta").fetchall())
    finally:
        connection.close()


def _kept_publishers(gig: SimpleNamespace, prefixes: tuple[str, ...], head: str | None = None) -> dict[str, list[str]] | None:
    return journal._kept_publishing_commits(gig.root, gig.resolved.project_id, gig.resolved.gig_id, head or _head(gig), prefixes)


def _keep_publishers(gig: SimpleNamespace) -> None:
    """Ask for publishers so those of ``records/`` are kept at the current head."""

    assert _kept_publishers(gig, ("records/",)) is not None
    meta = _publishers_meta(gig)
    assert meta["head"] == _head(gig) and meta["usable"] == "1" and meta["commits"] == str(_count(gig))


def _publishers_are_the_walks(gig: SimpleNamespace, head: str | None = None, selections: tuple[tuple[str, ...], ...] = SELECTIONS) -> None:
    for prefixes in selections:
        kept = _kept_publishers(gig, prefixes, head)
        assert kept is not None, prefixes
        assert kept == journal._batch_publishing_commits(gig.root, head or _head(gig), prefixes), prefixes


def _snapshot_outcome(gig: SimpleNamespace, prefixes: tuple[str, ...]) -> tuple[object, ...]:
    try:
        snapshot = read_committed_snapshot(**_identity(gig), prefixes=prefixes)
    except JournalError as exc:
        return (type(exc).__name__, str(exc))
    return ("read", snapshot.head, snapshot.artifacts)


def _snapshot_both_ways(gig: SimpleNamespace, spawns: list[list[str]], prefixes: tuple[str, ...] = (SYNTHETIC,)) -> tuple[object, ...]:
    """A snapshot's outcome with the kept publishers in place, which must be the outcome of the walk of the journal."""

    spawns.clear()
    with_kept = _snapshot_outcome(gig, prefixes)
    if with_kept[0] != "read":
        assert [words for words in _logs(spawns, ranged=False) if "--name-only" in words], "a refusal must be the walk's"
    with _walking():
        walked = _snapshot_outcome(gig, prefixes)
    assert with_kept == walked
    return with_kept


def test_a_snapshot_costs_the_same_at_every_size(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    costs: dict[int, list[str]] = {}
    _publishers_file(gig).unlink(missing_ok=True)  # the fixture's own snapshots wrote it
    for size in SIZES:
        grow_journal_to(gig, size)
        spawns.clear()
        first = read_committed_snapshot(**_identity(gig), prefixes=("references/",))
        listings = [words for words in (_words(call) for call in spawns) if words[:1] == ["log"] and "--name-status" in words]
        assert len(listings) == 1, "the first snapshot at a new head lists the new commits once"
        assert any(".." in word for word in listings[0]) == (size != SIZES[0]), "after the first, only the new commits"
        spawns.clear()
        again = read_committed_snapshot(**_identity(gig), prefixes=("references/",))
        assert not [words for words in (_words(call) for call in spawns) if words[:1] == ["log"] and not any(".." in word for word in words)], "no walk of the journal"
        costs[size] = _verbs(spawns)
        assert first.artifacts == again.artifacts and again.head == _head(gig)
        assert _publishers_meta(gig)["commits"] == str(size)
    assert all(cost == costs[SIZES[0]] for cost in costs.values()), costs
    assert "log" not in costs[SIZES[0]] and len(costs[SIZES[0]]) <= 16, costs[SIZES[0]]


def test_the_kept_publishers_are_the_walks_publishers(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    _keep_publishers(gig)  # at the fixture's own commits
    grow_journal_to(gig, 60)
    # A replaced file (the journal's run details are): modified, never removed.
    replaced = f"{SYNTHETIC}000030.json"
    _journal_commit(gig, {replaced: b'{"replaced": true}\n', "manifests/synthetic.json": b"{}\n"})
    grow_journal(gig, 3)
    _publishers_are_the_walks(gig)  # caught up
    assert len(_kept_publishers(gig, (SYNTHETIC,))[replaced]) == 2  # type: ignore[index]
    _publishers_are_the_walks(gig)  # read back
    earlier = _git(gig.root, "rev-parse", "HEAD~7")
    _publishers_are_the_walks(gig, earlier)  # a snapshot pinned to a head behind the file's
    assert _publishers_meta(gig)["head"] == _head(gig), "left where it was"
    _publishers_file(gig).unlink()
    spawns.clear()
    _publishers_are_the_walks(gig)  # each selection listed once from the first commit, limited to its prefixes
    listings = [_words(call) for call in spawns if "--name-status" in call]
    assert 1 <= len(listings) <= len(SELECTIONS) and all("--" in words and words[-1].endswith("/") for words in listings)
    spawns.clear()
    _publishers_are_the_walks(gig)
    assert not [call for call in spawns if "--name-status" in call], "and never again"
    # The snapshots themselves: the same artifacts, kept or walked.
    for prefixes in ((SYNTHETIC,), ("references/",), ("records/", "references/", "manifests/")):
        if prefixes == (SYNTHETIC,):
            assert _snapshot_both_ways(gig, spawns, prefixes)[0] == "JournalConflictError"  # the replaced record has two publishers
        else:
            assert _snapshot_both_ways(gig, spawns, prefixes)[0] in ("read", "JournalConflictError")


def test_a_selection_git_would_read_as_a_pattern_is_walked(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    _keep_publishers(gig)
    for prefixes in (("records/*/",), (":(glob)records/",), ("./records/",), ("records//x/",), ("récords/",)):
        assert _kept_publishers(gig, prefixes) is None, prefixes
    assert _kept_publishers(gig, ()) is None
    assert _kept_publishers(gig, ("records/",), "HEAD") is None, "only a commit id is ever given to git"


def test_a_valid_write_by_another_process_is_in_the_next_snapshot(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    base = _count(gig)
    grow_journal_to(gig, 14)
    _keep_publishers(gig)
    for number in range(3):
        _journal_commit(gig, {f"{SYNTHETIC}other-{number}.json": b"{}\n"})
    outcome = _snapshot_both_ways(gig, spawns)
    assert outcome[0] == "read" and len(outcome[2]) == 14 - base + 3  # type: ignore[arg-type]
    assert _publishers_meta(gig)["head"] == _head(gig)
    _publishers_are_the_walks(gig)


def test_a_second_publisher_is_refused_by_the_walk(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    grow_journal_to(gig, 14)
    record = f"{SYNTHETIC}000012.json"
    assert _snapshot_both_ways(gig, spawns)[0] == "read"
    _keep_publishers(gig)
    # Published again by a later commit, with commits before and after it.
    _journal_commit(gig, {f"{SYNTHETIC}before.json": b"{}\n"})
    _journal_commit(gig, {record: b'{"n": "forged"}\n'})
    _journal_commit(gig, {f"{SYNTHETIC}after.json": b"{}\n"})
    assert _snapshot_both_ways(gig, spawns) == ("JournalConflictError", "journal immutable artifact has multiple publishers")
    # And with the first bytes put back: only the commits in between give it away.
    _journal_commit(gig, {record: json.dumps({"n": 12}).encode() + b"\n"})
    assert _snapshot_both_ways(gig, spawns) == ("JournalConflictError", "journal immutable artifact has multiple publishers")
    assert _publishers_meta(gig)["usable"] == "1" and len(_kept_publishers(gig, (SYNTHETIC,))[record]) == 3  # type: ignore[index]


def test_a_removed_file_makes_the_walk_the_answer_for_its_family(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    grow_journal_to(gig, 14)
    record = f"{SYNTHETIC}000012.json"
    content = (gig.root / record).read_bytes()
    _keep_publishers(gig)
    assert _kept_publishers(gig, ("references/",)) is not None
    # Removed and published again with the same bytes: the tree at the head is as it was.
    _commit(gig, {record: None})
    _journal_commit(gig, {record: content})
    assert _snapshot_both_ways(gig, spawns) == ("JournalConflictError", "journal immutable artifact has multiple publishers")
    assert _kept_publishers(gig, (SYNTHETIC,)) is None and _kept_publishers(gig, ("records/",)) is None
    # Another family is answered as before.
    assert _kept_publishers(gig, ("references/",)) == journal._batch_publishing_commits(gig.root, _head(gig), ("references/",))
    # The journal grows on: still the walk for that family, and the journal is never listed again from its first commit.
    _journal_commit(gig, {f"{SYNTHETIC}later.json": b"{}\n"})
    spawns.clear()
    assert _snapshot_both_ways(gig, spawns)[0] == "JournalConflictError"
    assert _snapshot_both_ways(gig, spawns, ("references/",))[0] == "read"
    assert not [call for call in spawns if "--name-status" in call and not any(".." in word for word in call)]
    assert _publishers_meta(gig)["head"] == _head(gig)
    # A file moved from one family to another: the walk pairs the two as a rename when both are in its
    # selection, and lists them differently when one is not. The family it left is never answered from the file.
    _publishers_file(gig).unlink()
    _git(gig.root, "reset", "--quiet", "--hard", "HEAD~3")
    _keep_publishers(gig)
    (gig.root / "records" / "moved").mkdir()
    _git(gig.root, "mv", record, "records/moved/000012.json")
    _git(gig.root, "commit", "--quiet", "-m", "moved")
    for prefixes in ((SYNTHETIC,), ("records/",), (SYNTHETIC, "records/moved/")):
        assert _kept_publishers(gig, prefixes) is None, prefixes
        _snapshot_both_ways(gig, spawns, prefixes)
    assert _kept_publishers(gig, ("records/moved/",)) == journal._batch_publishing_commits(gig.root, _head(gig), ("records/moved/",))


def test_a_rewritten_a_reset_and_a_merged_history_are_walked(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    base = _count(gig)
    grow_journal_to(gig, 20)
    _keep_publishers(gig)
    kept_at = _publishers_meta(gig)["head"]
    # Set back: answered for the earlier head from the same file.
    _git(gig.root, "reset", "--quiet", "--hard", "HEAD~4")
    outcome = _snapshot_both_ways(gig, spawns)
    assert outcome[0] == "read" and len(outcome[2]) == 16 - base  # type: ignore[arg-type]
    _publishers_are_the_walks(gig, selections=(("records/",), (SYNTHETIC,), ("records/", SYNTHETIC)))
    assert _publishers_meta(gig)["head"] == kept_at
    # A family the file has not listed is walked at a head before the one the file was made at.
    made_at = _git(gig.root, "rev-parse", "HEAD~1")
    _publishers_file(gig).unlink()
    assert _kept_publishers(gig, (SYNTHETIC,)) is not None and _publishers_meta(gig)["base_head"] == _head(gig)
    assert _kept_publishers(gig, ("references/",), made_at) is None
    assert _kept_publishers(gig, (SYNTHETIC,), made_at) == journal._batch_publishing_commits(gig.root, made_at, (SYNTHETIC,))
    # Rewritten onto another line: listed again from the first commit.
    _git(gig.root, "reset", "--quiet", "--hard", "HEAD~3")
    grow_journal(gig, 5)
    outcome = _snapshot_both_ways(gig, spawns)
    assert outcome[0] == "read" and len(outcome[2]) == 18 - base  # type: ignore[arg-type]
    _publishers_are_the_walks(gig)
    assert _publishers_meta(gig) | {"commits": "18", "usable": "1", "head": _head(gig)} == _publishers_meta(gig)
    # A merge: never a straight line, so the walk, and the listing is not repeated at that head.
    _git(gig.root, "branch", "side", "HEAD~1")
    _git(gig.root, "checkout", "--quiet", "side")
    _commit(gig, {f"{SYNTHETIC}side.json": b"{}\n"})
    _git(gig.root, "checkout", "--quiet", "main")
    _git(gig.root, "merge", "--quiet", "--no-ff", "--no-edit", "side", "-m", "a merge")
    _snapshot_both_ways(gig, spawns, ("references/",))
    merged = _head(gig)
    assert _publishers_meta(gig)["usable"] == "0" and _publishers_meta(gig)["head"] == merged
    spawns.clear()
    assert _kept_publishers(gig, ("references/",), merged) is None and not spawns


def test_a_changed_and_an_uncommitted_working_file_are_refused_as_before(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    grow_journal_to(gig, 14)
    _keep_publishers(gig)
    record = gig.root / f"{SYNTHETIC}000012.json"
    committed = record.read_bytes()
    record.write_bytes(b'{"n": "edited"}\n')
    assert _snapshot_both_ways(gig, spawns) == ("JournalConflictError", "journal working evidence differs from committed bytes")
    record.write_bytes(committed)
    (gig.root / f"{SYNTHETIC}stray.json").write_bytes(b"{}\n")
    assert _snapshot_both_ways(gig, spawns) == ("JournalConflictError", "journal working evidence is extra or redirected")
    (gig.root / f"{SYNTHETIC}stray.json").unlink()
    assert _snapshot_both_ways(gig, spawns)[0] == "read"
    # Another Gig asking is refused by the workpad check, before any publisher is looked up.
    with pytest.raises(JournalError):
        read_committed_snapshot(**_identity(gig, f"gig_{uuid.uuid4()}"), prefixes=(SYNTHETIC,))
    assert _publishers_meta(gig)["gig_id"] == gig.resolved.gig_id


def _damage_publishers(gig: SimpleNamespace, damage: str) -> None:
    path = _publishers_file(gig)

    def change(*statements: tuple[str, tuple[object, ...]]) -> None:
        connection = sqlite3.connect(path)
        try:
            with connection:
                for statement, values in statements:
                    connection.execute(statement, values)
        finally:
            connection.close()

    record = f"{SYNTHETIC}000012.json"
    if damage == "deleted":
        path.unlink()
    elif damage == "empty":
        path.write_bytes(b"")
    elif damage == "garbage":
        path.write_bytes(b"\x00\xff not a database" * 64)
    elif damage == "another schema":
        change(("UPDATE meta SET value = ? WHERE key = 'schema'", ("journal-publishers/0",)))
    elif damage == "another gig":
        change(("UPDATE meta SET value = ? WHERE key = 'gig_id'", (f"gig_{uuid.uuid4()}",)))
    elif damage == "another project":
        change(("UPDATE meta SET value = ? WHERE key = 'project_id'", (f"project_{uuid.uuid4()}",)))
    elif damage == "an unknown head":
        change(("UPDATE meta SET value = ? WHERE key = 'head'", ("0" * 40,)))
    elif damage == "a head that is not a commit id":
        change(("UPDATE meta SET value = ? WHERE key = 'head'", ("--all",)))
    elif damage == "a missing key":
        change(("DELETE FROM meta WHERE key = 'commits'", ()))
    elif damage == "an extra key":
        change(("INSERT INTO meta(key, value) VALUES ('more', '1')", ()))
    elif damage == "a base past the head":
        change(("UPDATE meta SET value = '100000' WHERE key = 'base'", ()))
    elif damage == "a base that is not the journal's":
        change(("UPDATE meta SET value = ? WHERE key = 'base_head'", (_git(gig.root, "rev-parse", "HEAD~1"),)), ("DELETE FROM covered", ()))
    elif damage == "no tables":
        change(("DROP TABLE changed", ()))
    elif damage == "a publisher too many":
        change(("INSERT INTO changed(path, place, commit_id, status) VALUES (?, 2, ?, 'A')", (record, _git(gig.root, "rev-parse", "HEAD~12"))))
    elif damage == "a publisher that is not one":
        change(("UPDATE changed SET commit_id = ? WHERE path = ?", (_git(gig.root, "rev-parse", "HEAD~12"), record)))
    elif damage == "a removal that never happened":
        change(("UPDATE changed SET status = 'D' WHERE path = ?", (record,)))
    elif damage == "a publisher that is not a commit id":
        change(("UPDATE changed SET commit_id = '--all' WHERE path = ?", (record,)))
    elif damage == "a path with no publisher":
        change(("DELETE FROM changed WHERE path = ?", (record,)))
    elif damage == "a place past the head":
        change(("UPDATE changed SET place = 100000 WHERE path = ?", (record,)))
    else:
        raise AssertionError(damage)


@pytest.mark.parametrize("damage", [
    "deleted", "empty", "garbage", "another schema", "another gig", "another project", "an unknown head",
    "a head that is not a commit id", "a missing key", "an extra key", "a base past the head", "a base that is not the journal's",
    "no tables", "a publisher too many", "a publisher that is not one", "a removal that never happened",
    "a publisher that is not a commit id", "a path with no publisher", "a place past the head",
])
def test_a_publishers_file_that_cannot_be_used_is_only_a_cache(gig: SimpleNamespace, spawns: list[list[str]], damage: str) -> None:
    grow_journal_to(gig, 14)
    _keep_publishers(gig)
    with _walking():
        reference = _snapshot_outcome(gig, (SYNTHETIC,))
    assert reference[0] == "read"
    _damage_publishers(gig, damage)
    spawns.clear()
    assert _snapshot_outcome(gig, (SYNTHETIC,)) == reference
    assert not any("--all" in call for call in spawns), "what the file says is never passed to git unless it is a commit id"
    forged_rows = damage in ("a publisher too many", "a publisher that is not one", "a removal that never happened", "a path with no publisher")
    if forged_rows:
        # Well formed, so it is believed until a snapshot's own checks refuse what it says: then the walk answers.
        assert [words for words in _logs(spawns, ranged=False) if "--name-only" in words]
    else:
        meta = _publishers_meta(gig)
        assert meta["head"] == _head(gig) and meta["usable"] == "1" and meta["gig_id"] == gig.resolved.gig_id, "written again"
        _publishers_are_the_walks(gig)


@pytest.mark.parametrize("kind", ["a directory", "a symlink", "an unwritable scratch directory"])
def test_a_publishers_file_that_cannot_be_written_costs_no_listing(gig: SimpleNamespace, spawns: list[list[str]], tmp_path: Path, kind: str) -> None:
    grow_journal_to(gig, 14)
    _keep_publishers(gig)
    path = _publishers_file(gig)
    elsewhere = tmp_path / "elsewhere.sqlite"
    elsewhere.write_bytes(path.read_bytes())
    if kind == "a directory":
        path.unlink()
        path.mkdir()
    elif kind == "a symlink":
        path.unlink()
        path.symlink_to(elsewhere)
    else:
        path.unlink()
        path.parent.chmod(0o500)
    try:
        if kind != "an unwritable scratch directory":
            _journal_commit(gig, {f"{SYNTHETIC}one-more.json": b"{}\n"})
        spawns.clear()
        outcome = _snapshot_both_ways(gig, spawns)
        assert outcome[0] == "read"
        assert not [call for call in spawns if "--name-status" in call], "the journal is not listed for a file that cannot be kept"
    finally:
        path.parent.chmod(0o700)
    connection = sqlite3.connect(elsewhere)
    try:
        assert connection.execute("SELECT value FROM meta WHERE key = 'commits'").fetchone() == ("14",), "never written through"
    finally:
        connection.close()


# --- the commands ---------------------------------------------------------------------


def _run(gig: SimpleNamespace, *words: str) -> str:
    """One CLI command as a new process would run it: nothing checked earlier in this process is kept."""

    _forget()
    result = CliRunner().invoke(cli, [*words, "--home", str(gig.home), "--target", str(gig.target), "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return result.output


def _ownership_checks(spawns: list[list[str]]) -> int:
    """How many times the workpad's ownership markers were asked of git (once per check of the workpad)."""

    # 0110-11 STORE2: the four markers and the remote are one listing (it was five processes, one of them this marker's).
    return len([call for call in spawns if _words(call)[:4] == ["config", "--list", "-z", "--show-scope"]])


COMMANDS = {
    "status": (("status",), 13),
    "show": (("show",), 13),
    "history": (("history",), 13),
    "proposals": (("proposals",), 13),
    "scout profile list": (("scout", "profile", "list"), 21),
    # 0.1.10.7 C: the user-level answers list (the 0.1.10.5 ``story-bank list`` it replaces cost 29).
    "scout answers list": (("scout", "answers", "list"), 29),
}


def test_the_commands_cost_the_same_at_every_size_and_print_the_same(gig: SimpleNamespace, spawns: list[list[str]]) -> None:
    _run(gig, "scout", "profile", "list")  # the default profile is made on the first read: a write, once
    costs: dict[str, dict[int, int]] = {name: {} for name in COMMANDS}
    for size in (20, 204, 1212):
        grow_journal_to(gig, size)
        for name, (words, most) in COMMANDS.items():
            _run(gig, *words)  # the first run at a new head catches the kept files up
            spawns.clear()
            printed = _run(gig, *words)
            costs[name][size] = len(spawns)
            assert len(spawns) <= most, (name, size, _verbs(spawns))
            assert _ownership_checks(spawns) == 1, (name, "the workpad is checked once")
            assert not _commit_by_commit(spawns), name
            assert not [words_ for words_ in (_words(call) for call in spawns) if words_[:1] == ["log"]], (name, "no listing of the journal at all")
            if size == 204:
                with _walking():
                    assert _run(gig, *words) == printed, (name, "the same bytes as with the walks")
    for name, by_size in costs.items():
        assert len(set(by_size.values())) == 1, (name, by_size)


def test_a_read_only_command_opens_one_read(gig: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[int] = []
    real = workpad_module.resolve_workpad

    def resolving(*args, **kwargs):
        opened.append(getattr(workpad_module._READ_SCOPE, "depth", 0))
        return real(*args, **kwargs)

    monkeypatch.setattr(workpad_module, "resolve_workpad", resolving)
    monkeypatch.setattr("gigai.cli.resolve_workpad", resolving)
    for words in (("status",), ("show",), ("history",), ("proposals",), ("scout", "profile", "list"), ("scout", "answers", "list")):
        opened.clear()
        _run(gig, *words)
        assert opened and all(depth >= 1 for depth in opened), words
    assert getattr(workpad_module._READ_SCOPE, "depth", 0) == 0, "closed again"


def test_straight_history_lists_a_straight_line_and_nothing_else(gig: SimpleNamespace) -> None:
    grow_journal_to(gig, 14)
    head, older = _head(gig), _git(gig.root, "rev-parse", "HEAD~3")
    whole = straight_history(gig.root, None, head)
    assert whole is not None and len(whole) == 14 and whole[0][0] == head
    assert whole[0][1] == (("A", "handoffs/000000000014-private-record-revised.txt"), ("A", f"{SYNTHETIC}000014.json"))
    between = straight_history(gig.root, older, head)
    assert between == whole[:3]
    limited = straight_history(gig.root, older, head, "handoffs/")
    assert limited is not None and [changes for _commit_id, changes in limited] == [(change[0],) for _commit_id, change in ((c, ch) for c, ch in between)]  # type: ignore[union-attr]
    assert straight_history(gig.root, head, older) is None and straight_history(gig.root, head, head) is None
    assert straight_history(gig.root, "0" * 40, head) is None and straight_history(gig.root, None, "HEAD") is None
    _commit(gig, {f"{SYNTHETIC}000014.json": None, f"{SYNTHETIC}000013.json": b"changed\n"})
    changed = straight_history(gig.root, head, _head(gig))
    assert changed is not None and sorted(changed[0][1]) == [("D", f"{SYNTHETIC}000014.json"), ("M", f"{SYNTHETIC}000013.json")]
    with committed_read_cache():
        assert straight_history(gig.root, head, _head(gig)) == changed
