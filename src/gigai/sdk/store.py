"""A gig's own SQLite stores in its gig folder, and a version helper for each store file.

``ctx.store.db(gig, name)`` opens (and on first use creates) ``<home>/gigs/<gig>/<name>.sqlite`` and returns the
``sqlite3.Connection``. A gig opens only its own stores, and only the ones its ``gig.toml`` declares under
``[data] stores`` with ``engine = "sqlite"``; ``evals`` is implied by an ``[evals]`` table and its path comes from
``gigai.evals.records.evals_db_path``. The name ``registry`` is reserved for core. Opening a store touches no other
file.

Each store file carries its own version in SQLite's ``PRAGMA user_version``, the way ``registry.sqlite`` carries
core's. ``ctx.store.version(name)`` reads it, ``ctx.store.set_version(name, n)`` sets it, and
``ctx.store.migrate(name, steps)`` walks it 0 -> 1 -> 2 ...: ``steps[i]`` moves the file from version ``i`` to
``i + 1``, each step and its version bump in one transaction. A file newer than ``len(steps)`` (the versions the gig
knows) is refused, never touched.

The version methods take a store name, never a path or a home: they resolve it the way ``db`` does (this gig's own
folder under ``ctx.home``, a declared, non-reserved name), refuse a symlink at ``<home>/gigs``, the gig folder or the
file, and need the file to exist already. A gig therefore reaches only its own declared store files through this
module; it cannot name ``registry.sqlite``, another gig's store or a workpad's files. ``evals`` is core's: its schema
version belongs to gigai, so a gig may read it (``version``) but ``set_version`` and ``migrate`` refuse it
(``CoreStoreError``).
"""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..gigs.home import check_name, ensure_gig_folder, gig_folder

STORE_SUFFIX = ".sqlite"
RESERVED_STORES = frozenset({"registry"})  # core's file names; a gig store never takes them
EVALS_STORE = "evals"  # implied by an [evals] table; its path is gigai.evals.records.evals_db_path
_MAX_VERSION = 2**31 - 1  # user_version is a signed 32-bit integer

Step = Callable[[sqlite3.Connection], object]


class StoreError(Exception):
    """A store the gig may not open, or a path that is not a gig's store file."""


class StoreVersionError(StoreError):
    """A store file whose version is newer than the gig (or, for a core store, gigai) knows."""


class CoreStoreError(StoreError):
    """A gig asked to change the schema version of a store core owns (``evals``)."""


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
        names.add(EVALS_STORE)
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
        if name == EVALS_STORE:
            from ..evals.records import evals_db_path

            return evals_db_path(gig, self.home)
        return gig_folder(self.home, gig) / f"{name}{STORE_SUFFIX}"

    def db(self, gig: str, name: str) -> sqlite3.Connection:
        """Open the file ``path(gig, name)`` names, creating it (mode 0600) when it is missing."""

        path = self.path(gig, name)
        ensure_gig_folder(self.home, gig)
        _refuse_symlinks(path)
        try:
            os.close(os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600))
        except OSError as exc:
            raise StoreError(f"cannot open store file {path}: {exc}") from exc
        return sqlite3.connect(path)

    def version(self, name: str) -> int:
        """The ``user_version`` of this gig's store ``name``."""

        connection = self._connect(name, "ro")
        try:
            return _user_version(connection)
        finally:
            connection.close()

    def set_version(self, name: str, number: int) -> None:
        """Set the ``user_version`` of this gig's store ``name`` to ``number``; refuses core's ``evals``."""

        _check_version(number)
        _refuse_core_store(name, "set the version of")
        connection = self._connect(name, "rw")
        try:
            connection.execute(f"PRAGMA user_version = {number}")
            connection.commit()
        finally:
            connection.close()

    def migrate(self, name: str, steps: Sequence[Step]) -> int:
        """Walk this gig's store ``name`` up to version ``len(steps)``; returns it (see ``migrate_connection``).

        Refuses core's ``evals`` store: gigai walks that one.
        """

        _check_version(len(steps))
        _refuse_core_store(name, "migrate")
        connection = self._connect(name, "rw")
        try:
            return migrate_connection(connection, steps, f"{name}{STORE_SUFFIX}")
        finally:
            connection.close()

    def _connect(self, name: str, mode: str) -> sqlite3.Connection:
        """Open this gig's existing store file ``name`` (mode ``ro`` or ``rw``); never creates it."""

        path = self.path(self.gig, name)
        _refuse_symlinks(path)
        if not path.is_file():
            raise StoreError(f"no store file at {path}; open it with ctx.store.db first")
        return sqlite3.connect(f"{path.absolute().as_uri()}?mode={mode}", uri=True)


def migrate_connection(
    connection: sqlite3.Connection, steps: Sequence[Step], label: str, *, core: bool = False
) -> int:
    """Walk the open SQLite file behind ``connection`` up to version ``len(steps)``; returns it.

    ``steps[i](connection)`` moves the file from version ``i`` to ``i + 1``. Each step runs inside a
    ``BEGIN IMMEDIATE`` transaction this function opens; the version is read inside that transaction (so two runs
    at once never apply a step twice) and the bump commits together with the step. A step must therefore not end
    the transaction: no ``commit``/``rollback``, no ``executescript`` (it commits first, and its statements are not
    undone if the step then fails) and no ``PRAGMA journal_mode`` change (refused inside a transaction). A step that
    returns with the transaction ended raises ``StoreError`` ("a step committed on its own") and the version is not
    bumped. Raises ``StoreVersionError`` (and changes nothing) when the file is newer than ``len(steps)``. ``label``
    names the file in errors; ``core`` says the steps are gigai's (the remedy is then "update gigai", not the gig).
    The connection is left in autocommit mode (``isolation_level = None``).
    """

    known = len(steps)
    _check_version(known)
    connection.isolation_level = None  # the transactions below are explicit
    while True:
        connection.execute("BEGIN IMMEDIATE")
        try:
            current = _user_version(connection)
            if current > known:
                knower, remedy = ("gigai", "update gigai") if core else ("this gig", "update the gig")
                raise StoreVersionError(
                    f"store {label} is at version {current}, newer than version {known} {knower} knows; {remedy}"
                )
            if current == known:
                connection.execute("COMMIT")
                return current
            steps[current](connection)
            if not connection.in_transaction:
                raise StoreError(
                    f"store {label}: a step committed on its own (step {current} -> {current + 1}); a step must not "
                    f"commit, roll back, run executescript or change journal_mode; version left at {current}"
                )
            connection.execute(f"PRAGMA user_version = {current + 1}")
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise


def _check_store_name(name: str) -> None:
    check_name("store", name)
    if name in RESERVED_STORES:
        raise StoreError(f"store name {name!r} is reserved for core")


def _refuse_core_store(name: str, action: str) -> None:
    if name == EVALS_STORE:
        raise CoreStoreError(f"store {name!r} belongs to core: a gig cannot {action} it (gigai walks its schema)")


def _refuse_symlinks(path: Path) -> None:
    """Refuse a symlink at store file ``path``, its gig folder or ``<home>/gigs`` (the last three parts)."""

    for part in (path.parent.parent, path.parent, path):
        try:
            mode = part.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise StoreError(f"refusing a symlink in the gig folder: {part}")


def _user_version(connection: sqlite3.Connection) -> int:
    (number,) = connection.execute("PRAGMA user_version").fetchone()
    return int(number)


def _check_version(number: int) -> None:
    if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= _MAX_VERSION:
        raise ValueError(f"invalid store version {number!r}: use an integer from 0 to {_MAX_VERSION}")
