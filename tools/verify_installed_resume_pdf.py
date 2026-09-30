"""Verify the resume PDF template and fonts ship in an installed distribution and render."""

from __future__ import annotations

from importlib import resources

REQUIRED = ("resume.typ", "Inter-Regular.ttf", "Inter-SemiBold.ttf", "INTER-OFL-LICENSE.txt")


def main() -> int:
    root = resources.files("gigai.scout").joinpath("data", "resume")
    missing = [name for name in REQUIRED if not root.joinpath(name).is_file()]
    if missing:
        raise SystemExit(f"installed gigai.scout is missing data/resume files: {missing} (check package-data in pyproject.toml)")
    from gigai.scout.resume_display import PdfHeader
    from gigai.scout.resume_pdf import render_pdf
    from gigai.scout.tailored_resume import TailoredResume
    from datetime import datetime, timezone

    result = TailoredResume.from_json({"schema_version": "scout-tailored-resume:1", "header": [], "sections": []})
    data = render_pdf(result, PdfHeader(name="Verify Install"), company="Check", timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc))
    if not data.startswith(b"%PDF"):
        raise SystemExit("installed resume renderer did not produce a PDF")
    print("installed resume PDF assets and renderer OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
