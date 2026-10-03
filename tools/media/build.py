"""`make media`: build the release screenshots end to end, and print how long each step took.

    uv run --locked --group media python -m tools.media.build --out build/media

Steps: a temporary HOME; the synthetic demo home with Scout running on it; the UI screenshots;
the terminal frames; PNG optimisation; the privacy gate (and its own negative control); the
manifest. Any failing step exits non-zero, and the Scout server and the temporary HOME are
removed whatever happens.

Nothing under the real `~/.gigai` is read: HOME points at a fresh temporary directory before any
GigAI code is imported, and `demo_home.assert_synthetic_home` refuses to go on otherwise.
"""

from __future__ import annotations

import argparse
from datetime import date
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import tomllib

REPO = Path(__file__).resolve().parents[2]


def _browsers_path(real_home: Path) -> str:
    """Where Playwright keeps its browsers for the real user (HOME is about to move)."""

    if sys.platform == "darwin":
        return str(real_home / "Library" / "Caches" / "ms-playwright")
    return str(Path(os.environ.get("XDG_CACHE_HOME") or real_home / ".cache") / "ms-playwright")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=REPO / "build" / "media", help="the build folder (emptied first)")
    parser.add_argument("--keep-home", action="store_true", help="leave the temporary demo home in place and print where it is")
    args = parser.parse_args(argv)
    out = args.out.resolve()

    # Read from the real environment BEFORE HOME moves: who runs this (for the gate), the
    # operator's private denylist, and where Playwright's browsers are.
    from . import privacy_scan

    username = privacy_scan.current_username()
    denylist = privacy_scan.read_denylist()
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", _browsers_path(Path.home()))

    temp_home = Path(tempfile.mkdtemp(prefix="gigai-media-")).resolve()
    os.environ["HOME"] = str(temp_home)
    os.environ.pop("GIGAI_HOME", None)

    from . import demo_home, manifest, terminal, ui_shots

    root = temp_home / "demo"
    timings: list[tuple[str, float]] = []
    started = time.monotonic()

    def step(name: str):
        class _Step:
            def __enter__(self) -> None:
                print(f"[media] {name}", flush=True)
                self.at = time.monotonic()

            def __exit__(self, *_exc: object) -> None:
                timings.append((name, time.monotonic() - self.at))

        return _Step()

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    rows: list[dict[str, str]] = []
    server_started = False
    try:
        demo_home.assert_synthetic_home(root)
        with step("demo home"):
            server_started = True
            demo = demo_home.build(root, log=lambda line: print(f"  {line}", flush=True))
        with step("UI screenshots"):
            rows += ui_shots.take_all(demo, out)
        with step("terminal frames"):
            rows += terminal.take_all(demo, out)
    finally:
        if server_started:
            with step("stop Scout"):
                try:
                    demo_home.stop(root)
                except Exception as error:  # noqa: BLE001 - the build's own error matters more; say this one and go on
                    print(f"  could not stop the demo Scout server: {error}", file=sys.stderr)
        if args.keep_home:
            print(f"[media] demo home kept at {temp_home}")
        else:
            shutil.rmtree(temp_home, ignore_errors=True)

    text_dir = out / "text"
    text_dir.mkdir()
    for text in out.glob("*.txt"):
        text.rename(text_dir / text.name)

    with step("optimise"):
        manifest.optimise(out)
    with step("privacy gate"):
        try:
            bad = privacy_scan.scan_dir(out, text_dir=text_dir, username=username, denylist=denylist)
            caught = privacy_scan.gate_catches_a_planted_path(sorted(out.glob("*.png"))[0])
        except privacy_scan.OcrUnavailable as error:
            print(f"[media] FAILED: {error}", file=sys.stderr)
            return 1
        print(f"  negative control (a planted {privacy_scan.PLANTED_PATH.split('/')[1]} path): {'caught' if caught else 'MISSED'}")
        if bad or not caught:
            print("[media] FAILED: the privacy gate found something, or missed its own negative control. Publish nothing.", file=sys.stderr)
            return 1
    with step("manifest"):
        version = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        try:
            built = manifest.build_manifest(out, rows, version=version, generated_on=date.today().isoformat())
        except manifest.ManifestError as error:
            print(f"[media] FAILED: {error}", file=sys.stderr)
            return 1
        manifest.write_manifest(out, built)

    total = time.monotonic() - started
    print("[media] timings")
    for name, seconds in timings:
        print(f"  {name:<16} {seconds:6.1f} s")
    print(f"  {'total':<16} {total:6.1f} s")
    print(f"[media] {len(built['images'])} images, {built['total_bytes'] / 1024:.0f} KB, in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
