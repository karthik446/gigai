from __future__ import annotations

import contextlib
import os
import re
import tempfile
from pathlib import Path
from typing import Iterator


SEQUENCE_FILE = re.compile(r"^(?P<sequence>[0-9]{12})-[a-z0-9-]+\.txt$")


@contextlib.contextmanager
def exclusive_writer_lock(lock_path: Path) -> Iterator[None]:
    if os.name != "posix":
        raise RuntimeError("the Phase 0 executable spike proves only the POSIX lock backend")
    import fcntl

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def append_handoff(workpad: Path, slug: str, body: bytes) -> int:
    handoffs = workpad / "handoffs"
    lock_path = workpad / ".git" / "gigai-writer.lock"
    handoffs.mkdir(parents=True, exist_ok=True)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with exclusive_writer_lock(lock_path):
        sequences = []
        for path in handoffs.iterdir():
            match = SEQUENCE_FILE.fullmatch(path.name)
            if match:
                sequences.append(int(match.group("sequence")))
        sequence = max(sequences, default=0) + 1
        destination = handoffs / f"{sequence:012d}-{slug}.txt"
        with tempfile.NamedTemporaryFile(dir=handoffs, delete=False) as temporary:
            temporary.write(body)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, destination)
        directory_fd = os.open(handoffs, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return sequence
