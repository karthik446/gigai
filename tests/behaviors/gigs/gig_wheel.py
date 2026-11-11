"""Build a test gig's wheel or sdist from its project folder (``tests/fixtures/gigs/<name>/``) with the standard library
only: no build backend, no index, no network. Reads ``[project]`` name, version, dependencies and entry points from the
folder's ``pyproject.toml`` and packs every package folder next to it."""

from __future__ import annotations

import base64
import hashlib
import io
import tarfile
import tomllib
import zipfile
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "gigs"


def _project(source: Path, entry_name: str | None, **overrides: Any) -> dict[str, Any]:
    project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if entry_name is not None:
        (value,) = project["entry-points"]["gigai.gigs"].values()
        project["entry-points"] = {"gigai.gigs": {entry_name: value}}
    project.update({key: value for key, value in overrides.items() if value is not None})
    return project


def _package_files(source: Path) -> list[Path]:
    return sorted(
        path
        for path in source.rglob("*")
        if path.is_file() and path.relative_to(source).parts[0] != "pyproject.toml" and "__pycache__" not in path.parts
    )


def build_wheel(
    source: Path,
    out: Path,
    *,
    entry_name: str | None = None,
    name: str | None = None,
    version: str | None = None,
    dependencies: list[str] | None = None,
) -> Path:
    """``<dist>-<version>-py3-none-any.whl`` of ``source`` in ``out``; ``entry_name`` renames its one gig entry point,
    ``name``, ``version`` and ``dependencies`` replace the ``[project]`` values (a second dist claiming the same gig, a
    newer release, a gig that needs a newer gigai)."""

    project = _project(source, entry_name, name=name, version=version, dependencies=dependencies)
    stem = f"{project['name'].replace('-', '_')}-{project['version']}"
    info = f"{stem}.dist-info"
    meta = ["Metadata-Version: 2.1", f"Name: {project['name']}", f"Version: {project['version']}"]
    meta += [f"Requires-Dist: {dependency}" for dependency in project.get("dependencies", [])]
    entry_points = "".join(
        f"[{group}]\n" + "".join(f"{name} = {value}\n" for name, value in table.items()) + "\n"
        for group, table in project.get("entry-points", {}).items()
    )
    members = {path.relative_to(source).as_posix(): path.read_bytes() for path in _package_files(source)}
    members[f"{info}/METADATA"] = ("\n".join(meta) + "\n").encode()
    members[f"{info}/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: gig_wheel\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    members[f"{info}/entry_points.txt"] = entry_points.encode()
    record = "".join(
        f"{name},sha256={base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()},{len(data)}\n"
        for name, data in members.items()
    )
    members[f"{info}/RECORD"] = (record + f"{info}/RECORD,,\n").encode()
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{stem}-py3-none-any.whl"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return target


def build_sdist(source: Path, out: Path) -> Path:
    """``<dist>-<version>.tar.gz`` of ``source`` in ``out``: its ``pyproject.toml``, a ``PKG-INFO`` and its packages."""

    project = _project(source, None)
    stem = f"{project['name'].replace('-', '_')}-{project['version']}"
    members = {path.relative_to(source).as_posix(): path.read_bytes() for path in _package_files(source)}
    members["pyproject.toml"] = (source / "pyproject.toml").read_bytes()
    members["PKG-INFO"] = f"Metadata-Version: 2.1\nName: {project['name']}\nVersion: {project['version']}\n".encode()
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{stem}.tar.gz"
    with tarfile.open(target, "w:gz") as archive:
        for name, data in members.items():
            entry = tarfile.TarInfo(f"{stem}/{name}")
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    return target
