"""A gig's own SQLite stores in its gig folder, and a version helper for each store file.

``ctx.store.db(gig, name)`` opens (and on first use creates) ``<home>/gigs/<gig>/<name>.sqlite`` and returns the
``sqlite3.Connection``. A gig opens only its own stores, and only the ones its ``gig.toml`` declares under
``[data] stores`` with ``engine = "sqlite"``; ``evals`` is implied by an ``[evals]`` table. The name ``registry`` is
reserved for core. Opening a store touches no other file.

Each store file carries its own version in SQLite's ``PRAGMA user_version``, the way ``registry.sqlite`` carries
core's. ``version(home, path)`` reads it, ``set_version(home, path, n)`` sets it, and ``migrate(home, path, steps)``
walks it 0 -> 1 -> 2 ...: ``steps[i]`` moves the file from version ``i`` to ``i + 1``, each step and its version bump
in one transaction. A file newer than ``len(steps)`` (the versions the gig knows) is refused, never touched.

The helpers open a file only when it is ``<home>/gigs/<gig>/<name>.sqlite`` of the gigai home ``home`` (normally
``ctx.home``) once both paths are resolved, and they refuse a symlink at ``<home>/gigs``, the gig folder or the file.
``db`` refuses the same symlinks. So no path, link or store name reaches ``registry.sqlite`` or a workpad's files.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..gigs.home import GIGS_DIRNAME, check_name, ensure_gig_folder, gig_folder

STORE_SUFFIX = ".sqlite"
RESERVED_STORES = frozenset({"registry"})  # core's file names; a gig store never takes them
_MAX_VERSION = 2**31 - 1  # user_version is a signed 32-bit integer

Step = Callable[[sqlite3.Connection], object]


class StoreError(Exception):
    """A store the gig may not open, or a path that is not a gig's store file."""


class StoreVersionError(StoreError):
    """A store file whose version is newer than the gig knows."""


def declared_stores(manifest: Mapping[str, Any]) -> frozenset[str]:
    """The store names ``manifest`` lets its gig open: ``[data] stores`` with ``engine = "sqlite"``, plus ``evals``
    when it has an ``[evals]`` table."""

    data = manifest.get("data")
    stores = data.get("stores") if isinstance(data, dict) else None
    names = {
        store["name"]
        for store in (stores if isinstance(stores, list) else ())
        if isinstance(store, dict) and store.get("engine") == "sqlite" and isinstance(store.get("name"), str)
    }
    if isinstance(manifest.get("evals"), dict):
        names.add("evals")
    return frozenset(names)


@dataclass(frozen=True)
class GigStore:
    """``ctx.store``: the stores of gig ``gig`` (manifest ``manifest``) under the gigai home ``home``."""

    home: Path
    gig: str
    manifest: Mapping[str, Any]

    def path(self, gig: str, name: str) -> Path:
        """Where store ``name`` of ``gig`` lives; raises ``StoreError`` when this gig may not open it."""

        check_name("gig", gig)
        _check_store_name(name)
        if gig != self.gig:
            raise StoreError(f"gig {self.gig!r} cannot open a store in the gig folder of {gig!r}")
        if name not in declared_stores(self.manifest):
            raise StoreError(
                f"store {name!r} is not declared under [data] stores with engine = \"sqlite\" in {gig!r}'s gig.toml"
            )
        return gig_folder(self.home, gig) / f"{name}{STORE_SUFFIX}"

    def db(self, gig: str, name: str) -> sqlite3.Connection:
        """Open ``<home>/gigs/<gig>/<name>.sqlite``, creating the file (mode 0600) when it is missing."""

        self.path(gig, name)
        ensure_gig_folder(self.home, gig)
        path = _real_store_path(self.home, gig, name)
        try:
            os.close(os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600))
        except OSError as exc:
            raise StoreError(f"cannot open store file {path}: {exc}") from exc
        return sqlite3.connect(path)


def version(home: Path, path: Path) -> int:
    """The ``user_version`` of the store file at ``path`` in the gigai home ``home``."""

    connection = _connect(home, path, "ro")
    try:
        return _user_version(connection)
    finally:
        connection.close()


def set_version(home: Path, path: Path, number: int) -> None:
    """Set the ``user_version`` of the store file at ``path`` in the gigai home ``home`` to ``number``."""

    _check_version(number)
    connection = _connect(home, path, "rw")
    try:
        connection.execute(f"PRAGMA user_version = {number}")
        connection.commit()
    finally:
        connection.close()


def migrate(home: Path, path: Path, steps: Sequence[Step]) -> int:
    """Walk the store file at ``path`` in the gigai home ``home`` up to version ``len(steps)``; returns it.

    ``steps[i](connection)`` moves the file from version ``i`` to ``i + 1``. Each step runs inside a
    ``BEGIN IMMEDIATE`` transaction this function opens; the version is read inside that transaction (so two runs
    at once never apply a step twice) and the bump commits together with the step. A step must therefore not end
    the transaction: no ``commit``/``rollback``, no ``executescript`` (it commits first, and its statements are not
    undone if the step then fails) and no ``PRAGMA journal_mode`` change (refused inside a transaction). A step that
    returns with the transaction ended raises ``StoreError`` ("a step committed on its own") and the version is not
    bumped. Raises ``StoreVersionError`` (and changes nothing) when the file is newer than ``len(steps)``.
    """

    known = len(steps)
    _check_version(known)
    connection = _connect(home, path, "rw")
    try:
        connection.isolation_level = None  # the transactions below are explicit
        while True:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = _user_version(connection)
                if current > known:
                    raise StoreVersionError(
                        f"store {Path(path).name} is at version {current}, newer than version {known} this gig "
                        "knows; update the gig"
                    )
                if current == known:
                    connection.execute("COMMIT")
                    return current
                steps[current](connection)
                if not connection.in_transaction:
                    raise StoreError(
                        f"store {Path(path).name}: a step committed on its own (step {current} -> {current + 1}); a "
                        "step must not commit, roll back, run executescript or change journal_mode; version left at "
                        f"{current}"
                    )
                connection.execute(f"PRAGMA user_version = {current + 1}")
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
    finally:
        connection.close()


def _check_store_name(name: str) -> None:
    check_name("store", name)
    if name in RESERVED_STORES:
        raise StoreError(f"store name {name!r} is reserved for core")


def _real_store_path(home: Path, gig: str, name: str) -> Path:
    """``<home>/gigs/<gig>/<name>.sqlite`` with ``home`` resolved; refuses a symlink at any of the last three parts."""

    gigs = Path(home).resolve() / GIGS_DIRNAME
    path = gigs / gig / f"{name}{STORE_SUFFIX}"
    for part in (gigs, path.parent, path):
        try:
            mode = part.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise StoreError(f"refusing a symlink in the gig folder: {part}")
    return path


def _store_file(home: Path, path: Path) -> Path:
    """The real path of ``path`` when it is an existing store file ``<home>/gigs/<gig>/<name>.sqlite``."""

    given = Path(path)
    refused = f"not a gig store file of {home} (<home>/gigs/<gig>/<name>.sqlite): {given}"
    try:
        if given.suffix != STORE_SUFFIX:
            raise StoreError
        check_name("gig", given.parent.name)
        _check_store_name(given.stem)
    except (StoreError, ValueError):
        raise StoreError(refused) from None
    real = _real_store_path(home, given.parent.name, given.stem)
    if given.resolve() != real:
        raise StoreError(refused)
    if not real.is_file():
        raise StoreError(f"no store file at {real}; open it with ctx.store.db first")
    return real


def _connect(home: Path, path: Path, mode: str) -> sqlite3.Connection:
    return sqlite3.connect(f"{_store_file(home, path).as_uri()}?mode={mode}", uri=True)


def _user_version(connection: sqlite3.Connection) -> int:
    (number,) = connection.execute("PRAGMA user_version").fetchone()
    return int(number)


def _check_version(number: int) -> None:
    if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= _MAX_VERSION:
        raise ValueError(f"invalid store version {number!r}: use an integer from 0 to {_MAX_VERSION}")
