"""0110-049: the privacy gate fails on a real-looking path, user name, email or phone number.

The text half is pure and always runs. The pixel half (OCR) needs the `media` dependency group's
Pillow and the `tesseract` binary; without them those tests skip.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil

import pytest

from tools.media import demo_home, persona, privacy_scan

# Built from parts so no real-looking address sits in a tracked file as one string.
REAL_LOOKING_EMAIL = "jordan.avery" + "@" + "gmail" + ".com"
REAL_LOOKING_PHONE = "(415) " + "867-5309"
PLANTED_USER = "mreyes"

HAS_OCR = importlib.util.find_spec("PIL") is not None and shutil.which("tesseract") is not None
needs_ocr = pytest.mark.skipif(not HAS_OCR, reason="the media dependency group (Pillow) or tesseract is not installed")


def _kinds(text: str, **options: object) -> set[str]:
    return {hit.kind for hit in privacy_scan.scan_text(text, **options)}  # type: ignore[arg-type]


def test_negative_control_a_planted_home_path_fails_the_gate() -> None:
    assert privacy_scan.PLANTED_PATH.startswith("/Users/" + PLANTED_USER)
    assert _kinds(f"Wrote {privacy_scan.PLANTED_PATH} (1 page)") == {"home-path"}


@pytest.mark.parametrize(
    "path",
    ["/home/" + PLANTED_USER + "/.gigai/home", "C:\\Users\\" + PLANTED_USER + "\\resume.md", "/private/var/folders/bd/84nz/T/demo/home", "/var/folders/bd/84nz/T/x"],
)
def test_home_and_temp_paths_are_hits(path: str) -> None:
    assert "home-path" in _kinds(f"home: {path}")


def test_the_demo_home_shows_its_resumes_folder_as_a_path_the_gate_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """0110-10-07: the 0.1.10.9 release published no screenshots because the demo's resumes folder read as a home path.

    The demo home is `<temporary HOME>/demo/home`, so its DEFAULT resumes folder shows as
    `~/demo/home/resumes`, and `/home/resumes` matches the gate's home-path rule. The rule is right
    and stays; `make media` gives the demo home the folder a default install has instead.
    """

    from gigai.scout import resumes_folder

    user_home = tmp_path.resolve()
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.delenv("GIGAI_HOME", raising=False)
    gigai_home = user_home / "demo" / "home"  # demo_home.build: root / "home", root = <HOME>/demo (build.py)
    gigai_home.mkdir(parents=True)
    line = "In your resumes folder: {}/tallgrass-health-senior-software-engineer-scheduling-2026-10-04.md"

    # The home-path rule is NOT loosened: the default folder of that home is still a hit.
    default = resumes_folder.resumes_folder(gigai_home)
    assert default.shown == "~/demo/home/resumes"
    assert [str(hit) for hit in privacy_scan.scan_text(line.format(default.shown))] == ["home-path: /home/resumes"]

    # `make media` sets the neutral folder (and tests/ui, which pin the default folder, do not).
    build_source = (Path(demo_home.__file__).parent / "build.py").read_text(encoding="utf-8")
    assert "resumes_folder=demo_home.media_resumes_folder()" in build_source, "make media shows the demo home's default folder: ~/demo/home/resumes, a home-path hit"
    folder = resumes_folder.set_resumes_folder(gigai_home, os.fspath(demo_home.media_resumes_folder()))
    assert folder.shown == "~/Documents/GigAI/resumes", "what a default install shows"
    assert folder.path.is_relative_to(user_home) and not folder.path.is_relative_to(gigai_home)
    for text in (folder.shown, line.format(folder.shown), resumes_folder.master_file(gigai_home).shown, f"Resumes folder: {folder.shown}"):
        assert privacy_scan.scan_text(text) == [], text


def test_the_os_user_name_is_a_hit_as_a_word_only() -> None:
    assert _kinds(f"owner: {PLANTED_USER}", username=PLANTED_USER) == {"os-username"}
    assert _kinds(f"owner: {PLANTED_USER.upper()}", username=PLANTED_USER) == {"os-username"}
    assert _kinds("kart racing", username="kar") == set()
    # A build machine's account name is too common a word to flag alone; its home path still is.
    assert _kinds("the runner thread", username="runner") == set()
    assert _kinds("/home/runner/work", username="runner") == {"home-path"}


def test_emails_phones_and_profile_links_off_the_allowlist_are_hits() -> None:
    assert _kinds(f"contact {REAL_LOOKING_EMAIL}") == {"email"}
    assert _kinds(f"call {REAL_LOOKING_PHONE}") == {"phone"}
    assert _kinds("linkedin.com/in/jordan-avery") == {"profile-link"}
    assert _kinds("see github.com/someone-else/repo") == {"profile-link"}


def test_the_persona_and_the_ui_placeholders_pass() -> None:
    text = "\n".join([*persona.ALLOWED_CONTACT_VALUES, *privacy_scan.UI_PLACEHOLDERS, "anyone@example.com", "USD 172,000-205,000 per year", "2026-10-03T10:45:21.412455Z"])
    assert privacy_scan.scan_text(text) == []


def test_denylist_entries_are_hits_and_their_text_is_withheld(tmp_path: Path) -> None:
    hits = privacy_scan.scan_text("Tailored for Avery Lindholm", denylist=["nobody", "avery lindholm"])
    assert [(hit.kind, hit.shown) for hit in hits] == [("denylist", "entry 2")]
    listed = tmp_path / "denylist.txt"
    listed.write_text("First Entry\n\n second entry \n", encoding="utf-8")
    assert privacy_scan.read_denylist(str(listed)) == ["First Entry", "second entry"]
    assert privacy_scan.read_denylist("one, two ,,three") == ["one", "two", "three"]
    assert privacy_scan.read_denylist("") == []


def test_no_denylist_or_user_name_is_committed_in_the_tool() -> None:
    source = Path(privacy_scan.__file__).read_text(encoding="utf-8")
    assert "GIGAI_MEDIA_DENYLIST" in source
    assert "getpass.getuser()" in source, "the user name is read at build time, never written down"


def _frame(path: Path, text: str) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1280, 240), "white")
    ImageDraw.Draw(image).text((24, 150), text, fill="black", font=ImageFont.load_default(size=28))
    image.save(path)


@needs_ocr
def test_negative_control_a_path_planted_in_the_pixels_fails_the_gate(tmp_path: Path) -> None:
    clean = tmp_path / "clean.png"
    _frame(clean, "Senior Software Engineer, Scheduling")
    assert privacy_scan.scan_dir(tmp_path, username=PLANTED_USER, denylist=[], log=lambda _line: None) == 0
    assert privacy_scan.gate_catches_a_planted_path(clean)

    planted = tmp_path / "planted.png"
    privacy_scan.plant(clean, planted)
    found = privacy_scan.scan_image(planted, username=PLANTED_USER)
    assert {hit.kind for hit in found["ocr"]} >= {"home-path", "os-username"}
    assert found["text"] == []
    lines: list[str] = []
    assert privacy_scan.scan_dir(tmp_path, username=PLANTED_USER, denylist=[], log=lines.append) == 1
    assert any(line.strip().startswith("HIT planted.png") for line in lines)
    assert privacy_scan.main([str(tmp_path)]) == 1


@needs_ocr
def test_a_hit_in_the_page_text_fails_the_gate_even_when_the_pixels_are_clean(tmp_path: Path) -> None:
    _frame(tmp_path / "page.png", "Generate PDF")
    text_dir = tmp_path / "text"
    text_dir.mkdir()
    (text_dir / "page.txt").write_text(f"Generate PDF\n{REAL_LOOKING_EMAIL}\n", encoding="utf-8")
    assert privacy_scan.scan_dir(tmp_path, text_dir=text_dir, username="", denylist=[], log=lambda _line: None) == 1
    assert privacy_scan.main([str(tmp_path)]) == 1, "main finds the text folder beside the images"


def test_the_gate_fails_closed_without_ocr_or_images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert privacy_scan.scan_dir(tmp_path, username="", denylist=[], log=lambda _line: None) == 1, "no image is not a pass"
    (tmp_path / "frame.png").write_bytes(b"not read: tesseract is looked up first")
    monkeypatch.setattr(privacy_scan.shutil, "which", lambda _name: None)
    assert privacy_scan.main([str(tmp_path)]) == 1


def test_the_demo_home_shows_the_pdf_header_path_as_one_the_gate_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """0.1.11.3 release: the Generate PDF form prints `Filled from <path>` and `Save these details to <path>`.

    The old demo home `<HOME>/demo/home` showed `~/demo/home/header.json`: `/home/header.json` is a gate hit.
    The rule stays; the demo home is now `<HOME>/.gigai`, so the path is the default install's.
    """

    from gigai.scout import pdf_header_file

    user_home = tmp_path.resolve()
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.delenv("GIGAI_HOME", raising=False)
    old_home = user_home / "demo" / "home"
    old_shown = pdf_header_file.default_path(old_home)
    assert [str(hit) for hit in privacy_scan.scan_text(f"Filled from ~/demo/home/{old_shown.name}")] == ["home-path: /home/header.json"]

    new_home = demo_home.demo_gigai_home(user_home / "demo")
    assert new_home == user_home / ".gigai"
    shown = pdf_header_file.default_path(new_home)
    for text in (f"Filled from {pdf_header_file._display_path(shown)}", f"Save these details to {pdf_header_file._display_path(shown)}"):
        assert "~/Documents/GigAI/header.json" in text
        assert privacy_scan.scan_text(text) == [], text
