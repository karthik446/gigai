"""The privacy gate: no release image may show a real user name, home path, email or phone number.

Every image is checked twice:

* its own text, exactly (`text/<name>.txt`: the page's DOM text and field values, or the terminal
  transcript), where a miss is impossible;
* its pixels, by OCR (tesseract), as the last check on what a reader actually sees.

A hit is any of: the OS user name of whoever runs the build, a home or temp path
(`/Users/<name>`, `/home/<name>`, `C:\\Users\\<name>`, `/private/var/...`, `/var/folders/...`),
an email, phone number or profile link that is not one of the demo persona's obviously fictional
values, or an entry of the operator's private denylist (`GIGAI_MEDIA_DENYLIST`: a file with one
entry per line, or the entries themselves separated by commas; never committed, and a hit names
the entry's number, not its text).

`python -m tools.media.privacy_scan DIR` exits 1 on any hit. The text half is pure (standard
library only) and is tested without the `media` dependency group.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import getpass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from . import persona

DENYLIST_ENV = "GIGAI_MEDIA_DENYLIST"

#: Reserved for documentation and fiction (RFC 2606 / RFC 6761): never somebody's address.
SAFE_EMAIL_DOMAINS = ("example.test", "example.com", "example.org", "example.net")
#: The Scout UI's own placeholder texts in the Generate PDF form.
UI_PLACEHOLDERS: tuple[str, ...] = ("you@example.com", "+1 555 123 4567", "linkedin.com/in/you", "github.com/you")
#: The project's own public repository: Settings shows the default starter-snapshot address, which is on it.
PROJECT_LINKS: tuple[str, ...] = ("github.com/karthik446",)
ALLOWED_VALUES: tuple[str, ...] = (*persona.ALLOWED_CONTACT_VALUES, *UI_PLACEHOLDERS, *PROJECT_LINKS)
#: Account names of build machines: too common as plain words ("the runner") to flag on their
#: own. A path that holds one (`/home/runner`) is still a hit.
GENERIC_USERNAMES = frozenset({"runner", "root", "user", "ubuntu", "admin", "build", "ci", "docker", "vscode", "node"})

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
PHONE = re.compile(r"(?<![\d.])(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)")
HOME_PATH = re.compile(r"(?:/(?:Users|home)/[A-Za-z0-9._-]+|[A-Za-z]:\\Users\\[A-Za-z0-9._-]+|/private/(?:var|tmp)/\S+|/var/folders/\S+)")
PROFILE_LINK = re.compile(r"(?:linkedin\.com/in|github\.com|twitter\.com|x\.com)/[A-Za-z0-9_-]+", re.IGNORECASE)


@dataclass(frozen=True)
class Hit:
    kind: str
    shown: str  # what to print: the matched text, or "entry N" for a denylist entry

    def __str__(self) -> str:
        return f"{self.kind}: {self.shown}"


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _allowed(kind: str, value: str, allow: Sequence[str]) -> bool:
    lowered = value.lower().strip(".,;:")
    if any(lowered == item.lower() for item in allow):
        return True
    if kind == "email":
        return lowered.rsplit("@", 1)[-1] in SAFE_EMAIL_DOMAINS
    if kind == "phone":
        return any(_digits(value) and _digits(value) == _digits(item) for item in allow)
    return False


def current_username() -> str:
    """Whoever runs the build. Read before HOME is redirected; never guessed from a path."""

    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return ""


def read_denylist(value: str | None = None) -> list[str]:
    raw = os.environ.get(DENYLIST_ENV, "") if value is None else value
    if not raw.strip():
        return []
    path = Path(raw).expanduser()
    try:
        is_file = path.is_file()
    except OSError:
        is_file = False
    entries = path.read_text(encoding="utf-8").splitlines() if is_file else raw.split(",")
    return [entry.strip() for entry in entries if entry.strip()]


def scan_text(text: str, *, username: str = "", denylist: Iterable[str] = (), allow: Sequence[str] = ALLOWED_VALUES) -> list[Hit]:
    """Every private-looking thing in `text`. Pure: no file, no process."""

    hits: list[Hit] = []
    for kind, pattern in (("email", EMAIL), ("phone", PHONE), ("profile-link", PROFILE_LINK)):
        for match in pattern.finditer(text):
            if not _allowed(kind, match.group(0), allow):
                hits.append(Hit(kind, match.group(0)))
    hits.extend(Hit("home-path", match.group(0)) for match in HOME_PATH.finditer(text))
    name = username.strip()
    if name and name.lower() not in GENERIC_USERNAMES and re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", text, re.IGNORECASE):
        hits.append(Hit("os-username", name))
    lowered = text.lower()
    for number, entry in enumerate(denylist, start=1):
        if entry.lower() in lowered:
            hits.append(Hit("denylist", f"entry {number}"))
    return hits


class OcrUnavailable(RuntimeError):
    pass


def ocr(image: Path) -> str:
    """The text tesseract reads in `image` (needs the `media` group's Pillow and the `tesseract` binary)."""

    if shutil.which("tesseract") is None:
        raise OcrUnavailable("the privacy gate needs `tesseract` on PATH (brew install tesseract / apt-get install tesseract-ocr)")
    from PIL import Image, ImageOps, ImageStat

    with Image.open(image) as opened:
        grey = opened.convert("L")
    grey = grey.resize((grey.width * 2, grey.height * 2))
    if ImageStat.Stat(grey).mean[0] < 110:  # a dark theme: tesseract reads dark text on light
        grey = ImageOps.invert(grey)
    with tempfile.TemporaryDirectory() as folder:
        prepared = Path(folder) / "frame.png"
        grey.save(prepared)
        done = subprocess.run(["tesseract", str(prepared), "-", "--psm", "11"], capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise OcrUnavailable(f"tesseract failed on {image.name}: {done.stderr[-300:]}")
    return done.stdout


def scan_image(image: Path, *, text_dir: Path | None = None, username: str = "", denylist: Sequence[str] = ()) -> dict[str, list[Hit]]:
    """`{"text": [...], "ocr": [...]}` for one image; `text` is None-free and empty when it has no text file."""

    found: dict[str, list[Hit]] = {"text": [], "ocr": []}
    exact = (text_dir or image.parent) / f"{image.stem}.txt"
    if exact.is_file():
        found["text"] = scan_text(exact.read_text(encoding="utf-8"), username=username, denylist=denylist)
    found["ocr"] = scan_text(ocr(image), username=username, denylist=denylist)
    return found


def scan_dir(folder: Path, *, text_dir: Path | None = None, username: str | None = None, denylist: Sequence[str] | None = None, log=print) -> int:
    """Scan every PNG in `folder`. Returns the number of images with a hit."""

    name = current_username() if username is None else username
    entries = read_denylist() if denylist is None else list(denylist)
    images = sorted(folder.glob("*.png"))
    if not images:
        log(f"privacy gate: no images in {folder}")
        return 1
    bad = 0
    for image in images:
        found = scan_image(image, text_dir=text_dir, username=name, denylist=entries)
        hits = [f"{where} {hit}" for where, items in found.items() for hit in items]
        has_text = ((text_dir or image.parent) / f"{image.stem}.txt").is_file()
        log(f"  {'HIT ' if hits else 'ok  '}{image.name}{'' if has_text else ' (pixels only: no text file)'}{': ' + '; '.join(hits) if hits else ''}")
        bad += bool(hits)
    log(f"privacy gate: {len(images)} images, {bad} with a hit, denylist entries: {len(entries)}")
    return bad


PLANTED_PATH = "/" + "Users" + "/mreyes/Documents/resume-final.md"


def plant(image: Path, planted: Path, text: str = PLANTED_PATH) -> None:
    """The negative control: a copy of `image` with a real-looking path written across it."""

    from PIL import Image, ImageDraw, ImageFont

    with Image.open(image) as opened:
        frame = opened.convert("RGB")
    draw = ImageDraw.Draw(frame)
    draw.rectangle((0, 40, frame.width, 110), fill="white")
    draw.text((24, 56), text, fill="black", font=ImageFont.load_default(size=30))
    frame.save(planted)


def gate_catches_a_planted_path(image: Path) -> bool:
    """True when the pixel half of the gate flags a planted path in a copy of `image`."""

    with tempfile.TemporaryDirectory() as folder:
        planted = Path(folder) / "planted.png"
        plant(image, planted)
        return any(hit.kind == "home-path" for hit in scan_image(planted)["ocr"])


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) not in (1, 2):
        print("usage: python -m tools.media.privacy_scan IMAGE_DIR [TEXT_DIR]", file=sys.stderr)
        return 2
    folder = Path(args[0])
    text_dir = Path(args[1]) if len(args) == 2 else (folder / "text" if (folder / "text").is_dir() else None)
    try:
        return 1 if scan_dir(folder, text_dir=text_dir) else 0
    except OcrUnavailable as error:
        print(f"privacy gate: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
