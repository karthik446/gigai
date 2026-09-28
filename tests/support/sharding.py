"""ci-shard-suite: a stable, file-level partition of the test suite.

CI splits the source suite into ``GIGAI_TEST_SHARDS`` jobs per OS x Python
(.github/workflows/pull_request.yaml); job ``GIGAI_TEST_SHARD`` (1-based)
runs only the test files whose repo-relative POSIX path hashes into it:

    int(sha1(path).hexdigest(), 16) % GIGAI_TEST_SHARDS == GIGAI_TEST_SHARD - 1

The key is the test FILE, never the test id, so a file's tests (and its
module-scoped fixtures) always run together in one shard. sha1 of the path,
not ``hash()``: the same file lands in the same shard on every OS, Python
and xdist worker, whatever PYTHONHASHSEED is. The partition is deliberately
not balanced by hand; moving a file between shards is never the fix for a
test that only fails under sharding.

With either variable unset nothing is filtered, so a local run is
unchanged. tests/conftest.py is the only caller.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from pathlib import Path

SHARDS_ENV = "GIGAI_TEST_SHARDS"
SHARD_ENV = "GIGAI_TEST_SHARD"
REPO_ROOT = Path(__file__).resolve().parents[2]


class ShardUsageError(ValueError):
    """The shard variables are set, but not to a usable shard."""


def requested_shard(environ: Mapping[str, str]) -> tuple[int, int] | None:
    """``(shard, shards)`` when both variables are set, else ``None``.

    A variable that is set but empty counts as set: a workflow that lost its
    matrix value must fail, not quietly run the whole suite in every shard.
    """

    raw_shards = environ.get(SHARDS_ENV)
    raw_shard = environ.get(SHARD_ENV)
    if raw_shards is None or raw_shard is None:
        return None
    try:
        shards = int(raw_shards)
    except ValueError as exc:
        raise ShardUsageError(
            f"{SHARDS_ENV} must be a positive integer: {raw_shards!r}"
        ) from exc
    if shards < 1:
        raise ShardUsageError(
            f"{SHARDS_ENV} must be a positive integer: {raw_shards!r}"
        )
    try:
        shard = int(raw_shard)
    except ValueError as exc:
        raise ShardUsageError(
            f"{SHARD_ENV} must be an integer from 1 to {shards} "
            f"({SHARDS_ENV}={shards}): {raw_shard!r}"
        ) from exc
    if not 1 <= shard <= shards:
        raise ShardUsageError(
            f"{SHARD_ENV} must be an integer from 1 to {shards} "
            f"({SHARDS_ENV}={shards}): {raw_shard!r}"
        )
    return shard, shards


def repo_relative(path: Path | str, root: Path | str = REPO_ROOT) -> str:
    """The POSIX path of ``path`` relative to ``root``: the hashed key."""

    path = Path(path)
    root = Path(root)
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        pass
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ShardUsageError(
            f"cannot shard a test file outside the repository: {path}"
        ) from exc


def shard_of(relative_path: str, shards: int) -> int:
    """The 1-based shard that owns the file at ``relative_path``."""

    digest = hashlib.sha1(
        relative_path.encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    return int(digest, 16) % shards + 1


def in_shard(relative_path: str, shard: int, shards: int) -> bool:
    return shard_of(relative_path, shards) == shard


def split_items(
    items: Iterable,
    *,
    shard: int,
    shards: int,
    root: Path | str = REPO_ROOT,
) -> tuple[list, list]:
    """``(kept, deselected)`` for ``shard``, in the order given.

    An item is anything with a ``path`` attribute (a pytest item).
    """

    kept: list = []
    deselected: list = []
    for item in items:
        if in_shard(repo_relative(item.path, root), shard, shards):
            kept.append(item)
        else:
            deselected.append(item)
    return kept, deselected


__all__ = [
    "REPO_ROOT",
    "SHARDS_ENV",
    "SHARD_ENV",
    "ShardUsageError",
    "in_shard",
    "repo_relative",
    "requested_shard",
    "shard_of",
    "split_items",
]
