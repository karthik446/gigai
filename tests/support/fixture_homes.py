"""Packed baseline gigai homes (``tests/fixtures/homes/<version>.tar.zst``) and the pytest fixture that unpacks one.

``tools/build_fixture_home.sh`` builds a home with a RELEASED gigai, then runs
``seal`` here: every absolute path that starts with the build home is rewritten
to ``HOME_TOKEN`` (text files and the TEXT values of every SQLite table), the
machine-specific runtime-discovery snapshots are dropped, and the build fails if
anything still names the build machine. ``counts`` writes the ``.counts`` file
(``<home-relative sqlite path>\\t<table>\\t<rows>`` per line). ``unpack`` is the
inverse of ``seal``: it extracts the tarball and writes the real home path back
where the token stands, so the 0.1.11.13 CLI reads a home with valid paths.

Run as ``python -m tests.support.fixture_homes seal|counts|unpack ...`` from the
repository root (the build and sweep scripts do); stdlib only, so it runs
without the test extra. The ``home_0_1_11_13`` fixture is defined only when
pytest is importable.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import zlib

try:
    import pytest
except ImportError:  # the build and sweep scripts import this module without the test extra
    pytest = None  # type: ignore[assignment]

HOME_TOKEN = "@GIGAI_FIXTURE_HOME@"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "homes"
BASELINE_TARBALL = FIXTURES / "0.1.11.13.tar.zst"
BASELINE_COUNTS = FIXTURES / "0.1.11.13.counts"
# Machine-specific: setup's runtime discovery records the builder's PATH and found tools. Every
# `<home>/snapshots/runtime-discovery/` is dropped (operator_home's nested home has one too).
DROPPED_DIR = ("snapshots", "runtime-discovery")
_SQLITE_MAGIC = b"SQLite format 3\x00"


def is_sqlite(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    with path.open("rb") as handle:
        return handle.read(16) == _SQLITE_MAGIC


def _files(home: Path) -> Iterator[Path]:
    for path in sorted(home.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield path


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [name for name, sql in rows if not (sql or "").upper().startswith("CREATE VIRTUAL")]


def _replace_in_sqlite(path: Path, old: str, new: str, *, vacuum: bool) -> None:
    connection = sqlite3.connect(path)
    try:
        for table in _tables(connection):
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({_quote(table)})")]
            for column in columns:
                quoted = _quote(column)
                connection.execute(
                    f"UPDATE {_quote(table)} SET {quoted} = replace({quoted}, ?, ?) "
                    f"WHERE typeof({quoted}) = 'text' AND instr({quoted}, ?) > 0",
                    (old, new, old),
                )
        connection.commit()
        if vacuum:  # the old values otherwise survive in free pages
            connection.execute("VACUUM")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


def _replace_in_text(path: Path, old: str, new: str) -> None:
    data = path.read_bytes()
    needle = old.encode()
    if needle in data:
        path.write_bytes(data.replace(needle, new.encode()))


def relocate(home: Path, old: str, new: str, *, vacuum: bool = False) -> None:
    """Rewrite ``old`` to ``new`` in every SQLite TEXT value and every other file under ``home``."""

    for path in _files(home):
        if path.name.endswith(("-wal", "-shm", "-journal")):
            continue
        if is_sqlite(path):
            _replace_in_sqlite(path, old, new, vacuum=vacuum)
        elif "/.git/objects/" not in path.as_posix():  # zlib-compressed; seal scans them decompressed, never rewrites
            _replace_in_text(path, old, new)


def seal(home: Path, forbidden: list[str]) -> None:
    """Make a freshly built ``home`` packable: token for its own path, no machine-specific data left."""

    home = home.resolve()
    for path in sorted(home.rglob(DROPPED_DIR[1])):
        if path.is_dir() and path.parent.name == DROPPED_DIR[0]:
            shutil.rmtree(path)
    relocate(home, str(home), HOME_TOKEN, vacuum=True)
    leftovers = [path for path in home.rglob("*") if path.name.endswith(("-wal", "-shm", "-journal")) and path.stat().st_size]
    if leftovers:
        raise SystemExit(f"seal: SQLite journals still hold data: {[str(p.relative_to(home)) for p in leftovers]}")
    hits: list[str] = []
    needles = [needle.encode() for needle in forbidden if needle]
    for path in _files(home):
        data = path.read_bytes()
        if "/.git/objects/" in path.as_posix():
            if "/.git/objects/pack/" in path.as_posix():  # deltified: a byte scan proves nothing
                raise SystemExit(f"seal: git packfiles are not scanned, refusing: {path.relative_to(home)}")
            if "/.git/objects/info/" not in path.as_posix():
                data = zlib.decompress(data)
        hits.extend(f"{path.relative_to(home)}: {needle.decode()}" for needle in needles if needle in data)
    if hits:
        raise SystemExit("seal: machine-specific strings left in the home:\n" + "\n".join(hits[:50]))


def counts(home: Path) -> str:
    """Every SQLite file under ``home`` -> every table -> its row count, one tab-separated line each."""

    home = home.resolve()
    lines: list[str] = []
    for path in _files(home):
        if not is_sqlite(path):
            continue
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        try:
            for table in _tables(connection):
                (rows,) = connection.execute(f"SELECT count(*) FROM {_quote(table)}").fetchone()
                lines.append(f"{path.relative_to(home).as_posix()}\t{table}\t{rows}")
        finally:
            connection.close()
    return "".join(line + "\n" for line in lines)


def unpack_home(tarball: Path, dest: Path) -> Path:
    """Extract ``tarball`` into ``dest`` (created, must not exist) and point its paths at ``dest``."""

    dest.mkdir(parents=True)
    dest = dest.resolve()
    subprocess.run(["tar", "--zstd", "-xf", str(tarball), "-C", str(dest)], check=True)
    relocate(dest, HOME_TOKEN, str(dest))
    return dest


if pytest is not None:

    @pytest.fixture
    def home_0_1_11_13(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
        """The 0.1.11.13 baseline home unpacked at ``tmp_path/home``; ``HOME`` is ``tmp_path``."""

        home = unpack_home(BASELINE_TARBALL, tmp_path / "home")
        monkeypatch.setenv("HOME", str(home.parent))
        monkeypatch.setenv("GIGAI_HOME", str(home))
        monkeypatch.delenv("GIGAI_WORKPAD_ROOT", raising=False)
        yield home


def main(argv: list[str]) -> int:
    usage = (
        "usage: python -m tests.support.fixture_homes seal HOME [FORBIDDEN...]\n"
        "       python -m tests.support.fixture_homes counts HOME\n"
        "       python -m tests.support.fixture_homes unpack TARBALL DEST"
    )
    if len(argv) >= 2 and argv[0] == "seal":
        seal(Path(argv[1]), argv[2:])
    elif len(argv) == 2 and argv[0] == "counts":
        sys.stdout.write(counts(Path(argv[1])))
    elif len(argv) == 3 and argv[0] == "unpack":
        print(unpack_home(Path(argv[1]), Path(argv[2])))
    else:
        print(usage, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
