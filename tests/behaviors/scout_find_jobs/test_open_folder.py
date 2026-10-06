"""0.1.11.4 J3: ``open_folder.open_in_file_manager``: the OS opener, and the plain answer when there is none."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from gigai.scout import open_folder

_CALLS: list[list[str]] = []
SHOWN = "~/Documents/GigAI/jobs/acme/staff-engineer"


@pytest.fixture(autouse=True)
def _no_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    _CALLS.clear()
    monkeypatch.delenv(open_folder.TEST_ENV, raising=False)


def _platform(monkeypatch: pytest.MonkeyPatch, name: str, program: str | None, *, desktop: bool = True) -> list[list[str]]:
    calls = _CALLS
    monkeypatch.setattr(open_folder.sys, "platform", name)
    monkeypatch.setattr(open_folder.shutil, "which", lambda wanted: f"/usr/bin/{wanted}" if wanted == program else None)
    monkeypatch.setenv("DISPLAY", ":0") if desktop else monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    def run(argv, **kwargs):
        calls.append(list(argv))
        assert kwargs["shell"] is False and kwargs["stdin"] is subprocess.DEVNULL
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(open_folder.subprocess, "run", run)
    return calls


def test_macos_uses_open_and_linux_xdg_open_with_the_folder_as_the_only_argument(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _platform(monkeypatch, "darwin", "open")
    assert open_folder.open_in_file_manager(tmp_path, SHOWN, home_root=tmp_path) == open_folder.OpenResult(True, f"Opened {SHOWN}.")
    _platform(monkeypatch, "linux", "xdg-open")
    assert open_folder.open_in_file_manager(tmp_path, SHOWN, home_root=tmp_path).opened
    assert calls == [["/usr/bin/open", str(tmp_path)], ["/usr/bin/xdg-open", str(tmp_path)]]


@pytest.mark.parametrize(
    ("name", "program", "desktop"),
    [("linux", "xdg-open", False), ("linux", None, True), ("darwin", None, True), ("win32", "open", True)],
)
def test_no_desktop_or_no_program_says_so_and_names_the_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, program: str | None, desktop: bool) -> None:
    calls = _platform(monkeypatch, name, program, desktop=desktop)
    result = open_folder.open_in_file_manager(tmp_path, SHOWN, home_root=tmp_path)
    assert result.opened is False and SHOWN in result.message and "copy the path" in result.message
    assert calls == []


def test_a_failed_start_is_the_same_plain_answer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _platform(monkeypatch, "darwin", "open")

    def broken(argv, **kwargs):
        raise OSError("no")

    monkeypatch.setattr(open_folder.subprocess, "run", broken)
    assert open_folder.open_in_file_manager(tmp_path, SHOWN, home_root=tmp_path).opened is False
    monkeypatch.setattr(open_folder.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1))
    assert open_folder.open_in_file_manager(tmp_path, SHOWN, home_root=tmp_path).opened is False


def test_the_test_seam_opens_nothing_and_writes_the_folder_to_a_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _platform(monkeypatch, "darwin", "open")
    monkeypatch.setenv(open_folder.TEST_ENV, "1")
    assert open_folder.open_in_file_manager(tmp_path / "x", SHOWN, home_root=tmp_path).opened
    assert calls == [] and (tmp_path / open_folder.TEST_LOG).read_text(encoding="utf-8") == f"{tmp_path / 'x'}\n"
