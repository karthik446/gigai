"""0110-003 P2: resume display settings (storage, tolerant read, prefill, pdf header)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from gigai.scout import resume_display as rd
from gigai.scout.resume_display import ContactEntry, DisplaySettings

RESUME = (
    "# Zed Quixote\n"
    "Staff Engineer\n"
    "Denver, CO | github.com/zq | zq@example.test | (555) 013-7731 | H-1B visa | mystery segment here\n"
    "[LinkedIn](https://linkedin.com/in/zq)\n\n"
    "## Experience\n"
    "Built things at https://body.example.com and other@example.test\n"
)


def test_save_load_round_trip_is_0600_and_normalized(tmp_path: Path) -> None:
    saved = rd.save_display(
        tmp_path,
        DisplaySettings(
            "  Zed Quixote ",
            (
                ContactEntry("email", "a@b.co"),
                ContactEntry("email", "dup@b.co"),
                ContactEntry("link", "x.dev"),
                ContactEntry("link", "y.dev"),
                ContactEntry("phone", "   "),
                ContactEntry("bogus", "nope"),
            ),
            {"profile_1": "Staff", "profile_2": " "},
        ),
    )
    path = rd.display_path(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert [e.value for e in saved.contact] == ["a@b.co", "x.dev", "y.dev"]
    loaded = rd.load_display(tmp_path)
    assert loaded is not None and loaded.name == "Zed Quixote" and loaded.titles == {"profile_1": "Staff"}
    assert loaded.contact == saved.contact and loaded.updated_at


def test_tolerant_reads(tmp_path: Path) -> None:
    assert rd.load_display(tmp_path) is None
    path = rd.display_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert rd.load_display(tmp_path) is None
    path.write_text(json.dumps({"schema_version": "other:1"}))
    assert rd.load_display(tmp_path) is None
    path.unlink()
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"schema_version": rd.SCHEMA_VERSION, "name": "X"}))
    os.symlink(target, path)
    assert rd.load_display(tmp_path) is None


def test_suggest_is_local_classifies_and_drops_the_unclassifiable() -> None:
    s = rd.suggest(RESUME)
    assert s.name == "Zed Quixote" and s.title == "Staff Engineer"
    assert [(e.kind, e.value) for e in s.contact] == [
        ("location", "Denver, CO"),
        ("work_authorization", "H-1B visa"),
        ("linkedin", "https://linkedin.com/in/zq"),
        ("github", "github.com/zq"),
        ("email", "zq@example.test"),
        ("phone", "(555) 013-7731"),
    ]
    values = " ".join(e.value for e in s.contact)
    assert "mystery" not in values and "body.example.com" not in values and "other@example.test" not in values


def test_suggest_never_writes(tmp_path: Path) -> None:
    before = sorted(tmp_path.rglob("*"))
    rd.suggest(RESUME)
    assert sorted(tmp_path.rglob("*")) == before
    assert rd.suggest("") == rd.Suggestion()
    assert rd.suggest("Built a thing.\nMore text.").name == ""


def test_pdf_header_uses_saved_values_and_fallbacks() -> None:
    settings = DisplaySettings(
        "Zed", (ContactEntry("email", "z@e.co"), ContactEntry("github", "github.com/zq"), ContactEntry("linkedin", "https://www.linkedin.com/in/zq/"), ContactEntry("phone", "555")),
        {"p1": "Staff Engineer"},
    )
    h = rd.pdf_header(settings, "p1")
    assert (h.name, h.title) == ("Zed", "Staff Engineer")
    assert [(c.text, c.url) for c in h.contact] == [
        ("z@e.co", "mailto:z@e.co"),
        ("github.com/zq", "https://github.com/zq"),
        ("www.linkedin.com/in/zq", "https://www.linkedin.com/in/zq/"),
        ("555", None),
    ]
    assert rd.pdf_header(settings, "other").title == ""
    assert rd.pdf_header(None, "p1", "Legacy Name") == rd.PdfHeader(name="Legacy Name")
    assert rd.pdf_header(DisplaySettings(), None, "Legacy").name == "Legacy"
    assert rd.pdf_header(None, None) == rd.PdfHeader()


def test_spacing_and_auto_fit_round_trip_and_old_files_read_as_defaults(tmp_path: Path) -> None:
    """0110-017: spacing_scale (0.7..1.4, default 1.0) and auto_fit (default true) live in the same file."""
    saved = rd.save_display(tmp_path, DisplaySettings("Zed", spacing_scale=1.25, auto_fit=False))
    assert (saved.spacing_scale, saved.auto_fit) == (1.25, False)
    raw = json.loads(rd.display_path(tmp_path).read_text())
    assert raw["spacing_scale"] == 1.25 and raw["auto_fit"] is False
    loaded = rd.load_display(tmp_path)
    assert loaded is not None and (loaded.spacing_scale, loaded.auto_fit) == (1.25, False)

    # A file written before 0110-017 has neither key: it reads, with the defaults.
    old = {"schema_version": rd.SCHEMA_VERSION, "name": "Zed", "contact": [], "titles": {"p1": "Staff"}, "updated_at": "2026-09-29T00:00:00Z"}
    rd.display_path(tmp_path).write_text(json.dumps(old))
    loaded = rd.load_display(tmp_path)
    assert loaded is not None and loaded.name == "Zed" and loaded.titles == {"p1": "Staff"}
    assert (loaded.spacing_scale, loaded.auto_fit) == (rd.SPACING_DEFAULT, True) == (1.0, True)

    # Hand-edited nonsense reads as the defaults, never as an out-of-range scale.
    for spacing, auto_fit in ((3, "yes"), (0.2, None), ("1.2", 1), (True, 0)):
        rd.display_path(tmp_path).write_text(json.dumps({**old, "spacing_scale": spacing, "auto_fit": auto_fit}))
        loaded = rd.load_display(tmp_path)
        assert loaded is not None and (loaded.spacing_scale, loaded.auto_fit) == (1.0, True), (spacing, auto_fit)
    assert rd.valid_spacing(0.7) and rd.valid_spacing(1.4) and rd.valid_spacing(1)
    assert not rd.valid_spacing(0.69) and not rd.valid_spacing(1.41) and not rd.valid_spacing(True) and not rd.valid_spacing(float("nan"))
