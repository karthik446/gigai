"""Process guard injected only into black-box scenario child processes.

The guard records and rejects Python network access, reads from the developer's
real home outside explicitly allowed installation paths, and writes outside a
scenario's declared roots. It is repository test infrastructure, not product
runtime behavior.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
from typing import Any


_STATE = threading.local()


def _paths(name: str) -> tuple[Path, ...]:
    raw = os.environ.get(name, "[]")
    return tuple(Path(item).resolve(strict=False) for item in json.loads(raw))


_ALLOWED_READ_ROOTS = _paths("GIGAI_HARNESS_ALLOWED_READ_ROOTS")
_ALLOWED_WRITE_ROOTS = _paths("GIGAI_HARNESS_ALLOWED_WRITE_ROOTS")
_FORBIDDEN_READ_ROOTS = _paths("GIGAI_HARNESS_FORBIDDEN_READ_ROOTS")
_ALLOWED_EXECUTABLES = _paths("GIGAI_HARNESS_ALLOWED_EXECUTABLES")
_EVENT_LOG = Path(os.environ["GIGAI_HARNESS_GUARD_LOG"])


def _resolved(value: Any) -> Path | None:
    if isinstance(value, int):
        return None
    if isinstance(value, bytes):
        value = os.fsdecode(value)
    if not isinstance(value, str):
        return None
    try:
        return Path(value).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _within(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def _record_and_deny(kind: str, event: str, path: Path | None = None) -> None:
    if getattr(_STATE, "recording", False):
        raise PermissionError(f"GigAI scenario guard denied {kind}")

    _STATE.recording = True
    try:
        _EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _EVENT_LOG.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "event": event,
                        "kind": kind,
                        "path": os.fspath(path) if path is not None else None,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
    finally:
        _STATE.recording = False
    raise PermissionError(f"GigAI scenario guard denied {kind}")


def _is_write_open(mode: Any, flags: Any) -> bool:
    if isinstance(mode, str) and any(marker in mode for marker in "wax+"):
        return True
    if isinstance(flags, int):
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        return bool(flags & write_flags)
    return False


def _guard_path_read(event: str, path: Path | None) -> None:
    if path is None or _within(path, _ALLOWED_READ_ROOTS):
        return
    if _within(path, _FORBIDDEN_READ_ROOTS):
        _record_and_deny("real_home_access", event, path)


def _guard_path_write(event: str, path: Path | None) -> None:
    if path is not None and not _within(path, _ALLOWED_WRITE_ROOTS):
        _record_and_deny("undeclared_write", event, path)


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if getattr(_STATE, "recording", False):
        return

    if event.startswith("socket."):
        _record_and_deny("network_access", event)

    if event == "subprocess.Popen" and args:
        executable = _resolved(args[0])
        if executable is None or executable not in _ALLOWED_EXECUTABLES:
            _record_and_deny("undeclared_subprocess", event, executable)

    if event in {"os.posix_spawn", "os.posix_spawnp"} and args:
        executable = _resolved(args[0])
        if executable is None or executable not in _ALLOWED_EXECUTABLES:
            _record_and_deny("undeclared_subprocess", event, executable)

    if event == "os.system":
        _record_and_deny("undeclared_subprocess", event)

    if event == "open" and args:
        path = _resolved(args[0])
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else None
        if _is_write_open(mode, flags):
            _guard_path_write(event, path)
        else:
            _guard_path_read(event, path)
        return

    if event in {"os.listdir", "os.scandir", "os.chdir"} and args:
        _guard_path_read(event, _resolved(args[0]))
        return

    if event in {
        "os.remove",
        "os.rmdir",
        "os.mkdir",
        "os.chmod",
        "os.chown",
        "os.lchown",
        "os.mknod",
        "os.mkfifo",
        "os.removexattr",
        "os.setxattr",
        "os.truncate",
        "os.utime",
    } and args:
        _guard_path_write(event, _resolved(args[0]))
        return

    if event in {"os.rename", "os.replace", "os.link", "os.symlink"}:
        for raw_path in args[:2]:
            _guard_path_write(event, _resolved(raw_path))


sys.addaudithook(_audit)
