"""Local operator-scoped secret storage in ``<home_root>/.env``.

Values are never logged, printed, or returned in any diagnostic payload by
this module's callers; this module itself never formats a value into a
message. The file is created at mode ``0600`` if missing and re-chmodded to
``0600`` after every write, since :mod:`dotenv`'s write helpers rewrite the
file through a temp-file-and-replace and may not preserve the mode across
that replace on every platform/version. Writes additionally run under a
restrictive ``umask`` so the temp file dotenv creates mid-write is never
briefly world- or group-readable under a permissive default umask.

Every read goes through ``dotenv_values(..., interpolate=False)``: with
interpolation on (dotenv's default), a stored value containing ``$VAR`` or
``${VAR}`` gets silently substituted against the process environment on
read, which is never correct for an opaque secret value.

An empty or whitespace-only stored value is treated as unset throughout
this module (``get`` returns ``None``; ``names_set`` excludes it) — an
`` EXA_API_KEY=`` line is not a set secret.
"""

from __future__ import annotations

import builtins
from contextlib import contextmanager
import os
from pathlib import Path
from typing import Iterator

from dotenv import dotenv_values, set_key, unset_key


_FILE_MODE = 0o600
_RESTRICTIVE_UMASK = 0o077


def _default_home_root() -> Path:
    # Mirrors setup.default_home_root()'s resolution exactly
    # (src/gigai/setup.py:47-48). Duplicated, not imported, because
    # setup.py imports credentials.py, which imports this module for
    # env-then-.env resolution (src/gigai/credentials.py) — importing
    # setup.py here would be circular.
    return Path(os.environ.get("GIGAI_HOME", Path.home() / ".gigai")).expanduser()


def secrets_path(home_root: Path | None = None) -> Path:
    """Return the ``.env`` path for ``home_root`` (or the default home root)."""

    root = home_root if home_root is not None else _default_home_root()
    return root / ".env"


def _ensure_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch(mode=_FILE_MODE)
    os.chmod(path, _FILE_MODE)


@contextmanager
def _restrictive_umask() -> Iterator[None]:
    previous = os.umask(_RESTRICTIVE_UMASK)
    try:
        yield
    finally:
        os.umask(previous)


def _non_empty(value: str | None) -> str | None:
    return value if value and value.strip() else None


def get(name: str, *, home_root: Path | None = None) -> str | None:
    """Return the stored value for ``name``, or ``None`` if unset or empty."""

    path = secrets_path(home_root)
    if not path.exists():
        return None
    values = dotenv_values(path, interpolate=False)
    return _non_empty(values.get(name))


def set(name: str, value: str, *, home_root: Path | None = None) -> None:
    """Store ``value`` under ``name``, preserving other lines and comments."""

    path = secrets_path(home_root)
    with _restrictive_umask():
        _ensure_file(path)
        set_key(path, name, value)
        os.chmod(path, _FILE_MODE)


def remove(name: str, *, home_root: Path | None = None) -> None:
    """Remove ``name`` if present; a no-op if it was never set."""

    path = secrets_path(home_root)
    with _restrictive_umask():
        _ensure_file(path)
        unset_key(path, name)
        os.chmod(path, _FILE_MODE)


def names_set(*, home_root: Path | None = None) -> builtins.set[str]:
    """Return the set of variable names currently stored with a non-empty value."""

    path = secrets_path(home_root)
    if not path.exists():
        return builtins.set()
    return {
        key
        for key, value in dotenv_values(path, interpolate=False).items()
        if _non_empty(value) is not None
    }


__all__ = ["get", "names_set", "remove", "secrets_path", "set"]
