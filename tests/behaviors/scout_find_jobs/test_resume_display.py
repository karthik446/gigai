"""0110-003 P2 / 0110-046: resume display settings (storage, tolerant read) and the
Generate PDF form's header (parsed per render, never stored)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from gigai.scout import resume_display as rd
from gigai.scout.resume_display import DisplaySettings

#: Synthetic marker values (0110-046): never a real person.
FORM = {
    "name": " Zora Quillfeather ", "email": "zora.q@example.invalid", "phone": "555-0142-ZQ",
    "location": "Nowhere, ZZ", "linkedin": "https://www.linkedin.com/in/zq-invalid/", "link": "zq.example.invalid",
}


def test_save_load_round_trip_is_0600_and_normalized(tmp_path: Path) -> None:
    saved = rd.save_display(tmp_path, DisplaySettings({"profile_1": "Staff", "profile_2": " "}))
    path = rd.display_path(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert saved.titles == {"profile_1": "Staff"}
    loaded = rd.load_display(tmp_path)
    assert loaded is not None and loaded.titles == {"profile_1": "Staff"} and loaded.updated_at
    assert set(json.loads(path.read_text())) == {"schema_version", "titles", "spacing_scale", "auto_fit", "updated_at"}


def test_tolerant_reads(tmp_path: Path) -> None:
    assert rd.load_display(tmp_path) is None
    path = rd.display_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert rd.load_display(tmp_path) is None and rd.legacy_contact_fields(tmp_path) == {}
    path.write_text(json.dumps({"schema_version": "other:1"}))
    assert rd.load_display(tmp_path) is None
    path.unlink()
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"schema_version": rd.SCHEMA_VERSION, "name": "X"}))
    os.symlink(target, path)
    assert rd.load_display(tmp_path) is None and rd.legacy_contact_fields(tmp_path) == {}


def test_a_legacy_name_and_contact_are_ignored_counted_and_dropped_by_a_save(tmp_path: Path) -> None:
    """0110-046: a file from before 0.1.10.7 keeps reading (its layout and titles), never its personal values."""
    path = rd.display_path(tmp_path)
    path.parent.mkdir(parents=True)
    old = {
        "schema_version": rd.SCHEMA_VERSION, "name": "Zora Quillfeather",
        "contact": [{"kind": "email", "value": "zora.q@example.invalid"}, {"kind": "phone", "value": "555-0142-ZQ"}, {"kind": "link", "value": " "}],
        "titles": {"p1": "Staff"}, "spacing_scale": 1.2, "auto_fit": False, "updated_at": "2026-09-29T00:00:00Z",
    }
    path.write_text(json.dumps(old))
    loaded = rd.load_display(tmp_path)
    assert loaded == DisplaySettings({"p1": "Staff"}, "2026-09-29T00:00:00Z", 1.2, False)
    assert not hasattr(loaded, "name") and not hasattr(loaded, "contact")
    assert rd.legacy_contact_fields(tmp_path) == {"name": 1, "contact": 2}
    rd.save_display(tmp_path, loaded)
    text = path.read_text()
    assert "Zora" not in text and "zora.q" not in text and "555-0142" not in text
    assert rd.legacy_contact_fields(tmp_path) == {}
    path.write_text(json.dumps({**old, "name": "", "contact": []}))
    assert rd.legacy_contact_fields(tmp_path) == {"empty_fields": 2}


def test_nothing_is_prefilled_from_the_stored_resume() -> None:
    """0110-046 (spike finding d): no ``suggest`` parser: the stored resume's header is not kept."""
    assert not hasattr(rd, "suggest") and not hasattr(rd, "suggest_title") and not hasattr(rd, "Suggestion")


def test_the_form_header_prints_the_form_values_and_the_saved_title(tmp_path: Path) -> None:
    values = rd.parse_header_form(FORM)
    assert values["name"] == "Zora Quillfeather"
    header = rd.form_header(values, "Staff Engineer")
    assert (header.name, header.title) == ("Zora Quillfeather", "Staff Engineer")
    # 0.1.11.3 items 15/16: ONE contact line, in this order: location, (work authorization,) links, email, phone.
    # A link prints without https:// or www.; its target is the full URL.
    assert [(c.text, c.url) for c in header.contact] == [
        ("Nowhere, ZZ", None),
        ("linkedin.com/in/zq-invalid", "https://www.linkedin.com/in/zq-invalid/"),
        ("zq.example.invalid", "https://zq.example.invalid"),
        ("zora.q@example.invalid", "mailto:zora.q@example.invalid"),
        ("555-0142-ZQ", None),
    ]
    with_line = rd.form_header(rd.parse_header_form({**FORM, "work_authorization": "VISA: H1B"}))
    assert [c.text for c in with_line.contact][:2] == ["Nowhere, ZZ", "VISA: H1B"] and with_line.work_authorization == ""
    only_name = rd.form_header(rd.parse_header_form({"name": "Zora Quillfeather"}))
    assert only_name.contact == () and only_name.title == ""
    assert sorted(tmp_path.rglob("*")) == [], "parsing the form writes nothing"


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ([], "wrong_type"),
        ("Zora Quillfeather", "wrong_type"),
        ({"name": 3}, "wrong_type"),
        ({"fax": "555-0142-ZQ"}, "unknown_key"),
        ({"name": "Zora Quillfeather" * 20}, "invalid_value"),
        ({"email": "zora.q@example.invalid\nBcc: x"}, "invalid_value"),
    ],
)
def test_a_bad_form_is_refused_without_echoing_a_value(raw: object, code: str) -> None:
    with pytest.raises(rd.HeaderFormError) as caught:
        rd.parse_header_form(raw)
    assert caught.value.code == code
    message = str(caught.value)
    assert "Zora" not in message and "zora.q" not in message and "555-0142" not in message and "fax" not in message


def test_spacing_and_auto_fit_round_trip_and_old_files_read_as_defaults(tmp_path: Path) -> None:
    """0110-017: spacing_scale (0.7..1.4, default 1.0) and auto_fit (default true) live in the same file."""
    saved = rd.save_display(tmp_path, DisplaySettings(spacing_scale=1.25, auto_fit=False))
    assert (saved.spacing_scale, saved.auto_fit) == (1.25, False)
    raw = json.loads(rd.display_path(tmp_path).read_text())
    assert raw["spacing_scale"] == 1.25 and raw["auto_fit"] is False
    loaded = rd.load_display(tmp_path)
    assert loaded is not None and (loaded.spacing_scale, loaded.auto_fit) == (1.25, False)

    # A file written before 0110-017 has neither key: it reads, with the defaults.
    old = {"schema_version": rd.SCHEMA_VERSION, "titles": {"p1": "Staff"}, "updated_at": "2026-09-29T00:00:00Z"}
    rd.display_path(tmp_path).write_text(json.dumps(old))
    loaded = rd.load_display(tmp_path)
    assert loaded is not None and loaded.titles == {"p1": "Staff"}
    assert (loaded.spacing_scale, loaded.auto_fit) == (rd.SPACING_DEFAULT, True) == (1.0, True)

    # Hand-edited nonsense reads as the defaults, never as an out-of-range scale.
    for spacing, auto_fit in ((3, "yes"), (0.2, None), ("1.2", 1), (True, 0)):
        rd.display_path(tmp_path).write_text(json.dumps({**old, "spacing_scale": spacing, "auto_fit": auto_fit}))
        loaded = rd.load_display(tmp_path)
        assert loaded is not None and (loaded.spacing_scale, loaded.auto_fit) == (1.0, True), (spacing, auto_fit)
    assert rd.valid_spacing(0.7) and rd.valid_spacing(1.4) and rd.valid_spacing(1)
    assert not rd.valid_spacing(0.69) and not rd.valid_spacing(1.41) and not rd.valid_spacing(True) and not rd.valid_spacing(float("nan"))
