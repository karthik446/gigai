"""Immutable materialization for GigAI's built-in deterministic pack."""

from __future__ import annotations

from importlib import resources
import os
from pathlib import Path
import tempfile

from .canonical import digest_imported_bytes


PACK_NAME = "standard"
PACK_VERSION = "1"
PACK_RESOURCE = "standard-pack.json"


def pack_bytes() -> bytes:
    return resources.files("gigai.data").joinpath(PACK_RESOURCE).read_bytes()


def pack_digest() -> str:
    return digest_imported_bytes(pack_bytes())


def pack_path(home_root: Path) -> Path:
    content_hash = pack_digest().removeprefix("sha256:")
    return home_root / "packs" / "builtin" / PACK_NAME / PACK_VERSION / content_hash


def materialize_standard_pack(home_root: Path) -> tuple[Path, bool]:
    """Write the immutable pack once and reject conflicting materialization."""

    destination = pack_path(home_root)
    payload = pack_bytes()
    pack_file = destination / PACK_RESOURCE
    if pack_file.exists():
        if pack_file.read_bytes() != payload:
            raise ValueError(f"standard pack at {pack_file} does not match its content digest")
        return destination, False
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"standard pack directory {destination} contains unexpected files")
    destination.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{PACK_RESOURCE}.", suffix=".tmp", dir=destination
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o444)
        os.replace(temporary, pack_file)
    finally:
        temporary.unlink(missing_ok=True)
    return destination, True


def verify_standard_pack(home_root: Path) -> tuple[bool, str]:
    destination = pack_path(home_root)
    pack_file = destination / PACK_RESOURCE
    if not pack_file.is_file():
        return False, f"standard pack is missing at {pack_file}"
    if digest_imported_bytes(pack_file.read_bytes()) != pack_digest():
        return False, f"standard pack bytes at {pack_file} do not match the installed pack"
    extras = sorted(path.name for path in destination.iterdir() if path != pack_file)
    if extras:
        return False, f"standard pack directory contains unexpected files: {extras}"
    return True, f"standard pack {PACK_VERSION} is available offline"


__all__ = [
    "PACK_NAME",
    "PACK_VERSION",
    "materialize_standard_pack",
    "pack_bytes",
    "pack_digest",
    "pack_path",
    "verify_standard_pack",
]
