"""Disposable SQLite projection of one authoritative private Git journal."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import subprocess

from .canonical import canonical_json_bytes, parse_json_bytes, parse_json_front_matter


class IndexError(RuntimeError):
    """The index cannot truthfully represent the authoritative journal."""


@dataclass(frozen=True)
class JournalProjection:
    project_id: str
    gig_id: str
    head: str
    entries: tuple[dict[str, object], ...]
    proposal: dict[str, object] | None
    active_version: dict[str, object] | None

    def as_dict(self) -> dict[str, object]:
        return {
            "active_version": self.active_version,
            "entries": list(self.entries),
            "gig_id": self.gig_id,
            "head": self.head,
            "project_id": self.project_id,
            "proposal": self.proposal,
        }


def rebuild_index(*, workpad: Path, project_id: str, gig_id: str) -> JournalProjection:
    """Rebuild the ignored projection from committed workpad history only."""

    root = _root(workpad)
    _require_clean_authority(root)
    commits = tuple(
        line
        for line in _git(root, "rev-list", "--reverse", "HEAD").splitlines()
        if line
    )
    if not commits:
        raise IndexError("authoritative journal has no committed handoffs")
    entries: list[dict[str, object]] = []
    expected_sequence = 1
    for commit in commits:
        names = _git(root, "show", "--format=", "--name-only", commit).splitlines()
        handoffs = [
            name
            for name in names
            if name.startswith("handoffs/") and name.endswith(".txt")
        ]
        if len(handoffs) != 1:
            raise IndexError(
                "authoritative journal commit does not contain exactly one handoff"
            )
        handoff = handoffs[0]
        metadata, _body = parse_json_front_matter(
            _git_bytes(root, "show", f"{commit}:{handoff}")
        )
        if metadata.get("sequence") != expected_sequence:
            raise IndexError("authoritative journal sequence diverges")
        if metadata.get("gig_id") != gig_id:
            raise IndexError("authoritative journal Gig identity diverges")
        transition = metadata.get("transition")
        handoff_id = metadata.get("handoff_id")
        if not isinstance(transition, str) or not isinstance(handoff_id, str):
            raise IndexError("authoritative journal handoff lacks identity")
        entries.append(
            {
                "commit": commit,
                "handoff_id": handoff_id,
                "path": handoff,
                "sequence": expected_sequence,
                "transition": transition,
            }
        )
        expected_sequence += 1
    head = commits[-1]
    proposal = _json_at(root, head, "manifests/gig-proposal.json")
    active_version = _json_at(root, head, "manifests/active-gig-version.json")
    projection = JournalProjection(
        project_id, gig_id, head, tuple(entries), proposal, active_version
    )
    _write_projection(root / "state.sqlite", projection)
    return projection


def read_index(*, workpad: Path, project_id: str, gig_id: str) -> JournalProjection:
    """Return a matching projection, rebuilding absent, corrupt, or stale state."""

    root = _root(workpad)
    head = _git(root, "rev-parse", "--verify", "HEAD").strip()
    try:
        projection = _read_projection(root / "state.sqlite")
    except (IndexError, OSError, sqlite3.Error):
        return rebuild_index(workpad=root, project_id=project_id, gig_id=gig_id)
    if (
        projection.head != head
        or projection.project_id != project_id
        or projection.gig_id != gig_id
    ):
        return rebuild_index(workpad=root, project_id=project_id, gig_id=gig_id)
    _require_clean_authority(root)
    return projection


def _write_projection(path: Path, projection: JournalProjection) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("CREATE TABLE projection (payload BLOB NOT NULL)")
        connection.execute(
            "INSERT INTO projection(payload) VALUES (?)",
            (canonical_json_bytes(projection.as_dict()),),
        )
        connection.commit()
    finally:
        connection.close()
    os.replace(temporary, path)


def _read_projection(path: Path) -> JournalProjection:
    if path.is_symlink() or not path.is_file():
        raise IndexError("index is unavailable")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT payload FROM projection").fetchall()
    finally:
        connection.close()
    if len(rows) != 1 or type(rows[0][0]) is not bytes:
        raise IndexError("index contents are invalid")
    payload = parse_json_bytes(rows[0][0])
    if not isinstance(payload, dict):
        raise IndexError("index payload is invalid")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise IndexError("index entries are invalid")
    required = {"project_id", "gig_id", "head", "proposal", "active_version"}
    if not required.issubset(payload):
        raise IndexError("index payload is incomplete")
    return JournalProjection(
        payload["project_id"],
        payload["gig_id"],
        payload["head"],
        tuple(entries),
        payload["proposal"],
        payload["active_version"],
    )


def _json_at(root: Path, commit: str, path: str) -> dict[str, object] | None:
    result = _git_process(root, "show", f"{commit}:{path}", check=False)
    if result.returncode != 0:
        return None
    payload = parse_json_bytes(result.stdout.encode("utf-8"))
    if not isinstance(payload, dict):
        raise IndexError(f"authoritative {path} is not an object")
    return payload


def _root(workpad: Path) -> Path:
    root = workpad.resolve(strict=True)
    if root != workpad or root.is_symlink() or not root.is_dir():
        raise IndexError("workpad is unavailable or redirected")
    return root


def _require_clean_authority(root: Path) -> None:
    if _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise IndexError("authoritative workpad has uncommitted divergence")


def _git(root: Path, *args: str) -> str:
    return _git_process(root, *args).stdout


def _git_bytes(root: Path, *args: str) -> bytes:
    return _git_process(root, *args, text=False).stdout


def _git_process(
    root: Path, *args: str, check: bool = True, text: bool = True
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        capture_output=True,
        text=text,
        check=False,
        shell=False,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    if check and result.returncode != 0:
        raise IndexError(f"authoritative Git read failed: {result.stderr}")
    return result


__all__ = ["IndexError", "JournalProjection", "read_index", "rebuild_index"]
