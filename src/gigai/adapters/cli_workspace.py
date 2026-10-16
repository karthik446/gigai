"""Isolated per-home CLI working folders, with the legacy temporary fallback."""

from collections.abc import Iterator
from contextlib import contextmanager
from tempfile import TemporaryDirectory


@contextmanager
def cli_workspace(kind: str) -> Iterator[str]:
    from ..setup import default_home_root

    try:
        home = default_home_root().resolve()
        run = home / "run"
        directory = run / f"cli-{kind}"
        for path in (run, directory):
            if path.is_symlink():
                raise OSError("CLI workspace must not be a symlink")
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            if path.is_symlink() or not path.is_dir():
                raise OSError("CLI workspace must be a directory")
        directory.chmod(0o700)
    except (OSError, RuntimeError, ValueError):
        with TemporaryDirectory(prefix=f"gigai-{kind}-") as temporary:
            yield temporary
    else:
        yield str(directory)
