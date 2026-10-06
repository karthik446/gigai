"""0.1.11.4 J3: open a folder in the computer's file manager (the job page's "Open folder").

One job: given a folder GigAI has already checked, ask the OS to show it.  macOS uses ``open``, Linux
``xdg-open`` (only when there is a desktop: ``DISPLAY`` or ``WAYLAND_DISPLAY``); anything else, no
desktop, a missing program or a failed start gives ``opened=False`` and one plain sentence that names the
folder, so the page can show the path to copy instead.  The folder is always an absolute path, so it can
never be read as an option.  Nothing here reads the folder's files.

Test seam: with ``GIGAI_SCOUT_OPEN_FOLDER_TEST=1`` nothing is launched; the folder is appended to
``<home>/open-folder-test.log`` and the answer is "opened".  The browser tests use it (a real Finder
window must never open during a test run).
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys

TEST_ENV = "GIGAI_SCOUT_OPEN_FOLDER_TEST"
TEST_LOG = "open-folder-test.log"
_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class OpenResult:
    opened: bool
    message: str


def _program() -> str | None:
    if sys.platform == "darwin":
        return shutil.which("open")
    if sys.platform.startswith("linux") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return shutil.which("xdg-open")
    return None


def open_in_file_manager(folder: Path, shown: str, *, home_root: Path) -> OpenResult:
    """Show ``folder`` (absolute, checked by the caller) in the file manager; ``shown`` is its name for the user."""

    if os.environ.get(TEST_ENV) == "1":
        with (Path(home_root) / TEST_LOG).open("a", encoding="utf-8") as log:
            log.write(f"{folder}\n")
        return OpenResult(True, f"Opened {shown}.")
    cannot = OpenResult(False, f"This computer could not open the folder for you. It is {shown}: copy the path and open it yourself.")
    program = _program()
    if program is None:
        return cannot
    try:
        done = subprocess.run([program, os.fspath(folder)], stdin=subprocess.DEVNULL, capture_output=True, timeout=_TIMEOUT_SECONDS, check=False, shell=False)
    except (OSError, subprocess.SubprocessError):
        return cannot
    return OpenResult(True, f"Opened {shown}.") if done.returncode == 0 else cannot


__all__ = ["OpenResult", "TEST_ENV", "TEST_LOG", "open_in_file_manager"]
