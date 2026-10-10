"""0.1.11.10 Part A: the learning-pathway store (``gigai.scout.learning_store``).

1. A request is one record of schema ``scout-learning-pathway:1`` under ``<home>/scout/<project>/learning/records``:
   exactly the documented keys, ``queued``, no course, every cost value null, and NOTHING about reading (no
   progress, no cursor).
2. Records are written whole and atomically, listed newest first and read back by id; a file that is not a record,
   a link, or a record under another name is not listed.
3. A role that holds contact data is refused (``personal_info_refused``), like every other free text; an empty one,
   one over 200 characters or one of two lines is ``invalid_value``. Nothing is stored for a refused role.
4. ``resolve_course_file`` is the one gate a served file passes: only a ``done`` pathway, only a plain file of an
   allowed kind inside the course's own folder; ``..``, an empty part, a dotfile, a backslash, a percent sign, a
   link (file or folder) and a folder are all ``None``.

The course every test of this folder uses is SYNTHETIC and built in ``tmp_path`` by ``make_course`` below.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gigai.scout import learning_store
from gigai.scout.learning_store import LearningError

from tests.behaviors.scout_find_jobs.test_m1_end_to_end import _fixture

APP_JS = 'document.documentElement.setAttribute("data-app", "ran");\n'
SITE_CSS = "body { font-family: sans-serif; }\n.lesson { margin: 1rem; }\n"
MARK_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><rect width="8" height="8" fill="#345"/></svg>\n'
RECORD_KEYS = {"schema", "id", "role_text", "requested_at", "updated_at", "status", "cost", "course", "source", "imported_from", "error", "revision"}


def page(title: str, body: str = "", *, head: str = "") -> str:
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>' + title + "</title>\n"
        '<link rel="stylesheet" href="assets/site.css">\n<style>h1 { color: #123; }</style>\n' + head + "</head>\n"
        "<body><h1>" + title + '</h1>\n<img id="mark" src="assets/mark.svg" alt="">\n' + body + '\n<script src="assets/app.js"></script>\n</body></html>\n'
    )


def make_course(root: Path, *, lessons: int = 2, index_body: str = "", extra: dict[str, str | bytes] | None = None) -> Path:
    """A synthetic mini course in ``root``: an index, ``lessons`` lesson and practice pages, a script, a stylesheet, an svg."""

    files: dict[str, str | bytes] = {
        "index.html": page("Synthetic course", '<a href="concept-1-1-first.html">First lesson</a>\n' + index_body),
        "assets/app.js": APP_JS,
        "assets/site.css": SITE_CSS,
        "assets/mark.svg": MARK_SVG,
        "assets/lib/LICENSE": "Synthetic licence notice.\n",
    }
    for number in range(1, lessons + 1):
        files[f"concept-1-{number}-first.html"] = page(f"Lesson {number}", '<p class="lesson">Synthetic lesson text.</p>')
        files[f"practice-1-{number}-first.html"] = page(f"Practice {number}", "<ol><li>Synthetic step.</li></ol>")
    files.update(extra or {})
    for relative, content in files.items():
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return root


@pytest.fixture
def home_target(tmp_path: Path) -> tuple[Path, Path]:
    home, target, _workpad = _fixture(tmp_path)
    return home, target


def test_a_request_is_one_record_with_exactly_the_documented_keys_and_nothing_about_reading(home_target: tuple[Path, Path]) -> None:
    home, target = home_target
    made = learning_store.create_request(home, target, "  MLOps engineer  ", now=datetime(2026, 10, 9, 18, 0, tzinfo=UTC))

    folder = learning_store.records_dir(home, target)
    assert folder == home / "scout" / folder.parents[1].name / "learning" / "records" and folder.parents[2] == home / "scout"
    stored = json.loads((folder / f"{made.id}.json").read_text(encoding="utf-8"))
    assert set(stored) == RECORD_KEYS
    assert stored == {
        "schema": "scout-learning-pathway:1", "id": made.id, "role_text": "MLOps engineer", "requested_at": "2026-10-09T18:00:00Z",
        "updated_at": "2026-10-09T18:00:00Z", "status": "queued",
        "cost": {"cli_model_calls": None, "worker_minutes": None, "web_fetches": None, "web_searches": None, "tokens": None, "note": None},
        "course": None, "source": "requested", "imported_from": None, "error": None, "revision": 1,
    }
    assert learning_store.is_pathway_id(made.id) and made.id.startswith("lp-") and len(made.id) == 11
    # Nothing about reading a course: no key of the record, at any depth, names progress.
    flat = json.dumps(stored).lower()
    for word in ("progress", "cursor", "completed", "position", "last_read", "percent", "visited"):
        assert word not in flat, word
    assert learning_store.pathway_from_json(stored) == made
    with pytest.raises(ValueError):
        learning_store.pathway_from_json({**stored, "progress": 0.5})  # a record is its keys, no more


def test_records_are_written_atomically_listed_newest_first_and_read_by_id(home_target: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = home_target
    assert learning_store.list_pathways(home, target) == [] and learning_store.get_pathway(home, target, "lp-00000000") is None
    first = learning_store.create_request(home, target, "MLOps engineer", now=datetime(2026, 10, 8, tzinfo=UTC))
    second = learning_store.create_request(home, target, "Forward deployed engineer", now=datetime(2026, 10, 9, tzinfo=UTC))
    assert first.id != second.id
    assert [item.id for item in learning_store.list_pathways(home, target)] == [second.id, first.id]
    assert learning_store.get_pathway(home, target, first.id) == first
    for not_an_id in ("", "lp-1", "lp-0000000g", "../lp-00000000", f"{first.id}.json", "LP-00000000"):
        assert learning_store.get_pathway(home, target, not_an_id) is None, not_an_id

    folder = learning_store.records_dir(home, target)
    assert sorted(path.name for path in folder.iterdir()) == sorted([f"{first.id}.json", f"{second.id}.json"]), "no temporary file is left"

    # A write that fails midway leaves the stored record as it was (a temporary file, then one rename).
    before = (folder / f"{first.id}.json").read_bytes()

    def broken(_source, _destination) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError):
        learning_store.write_pathway(home, target, first)
    monkeypatch.undo()
    assert (folder / f"{first.id}.json").read_bytes() == before
    assert sorted(path.name for path in folder.iterdir()) == sorted([f"{first.id}.json", f"{second.id}.json"])

    # What is not a record is not listed: junk, a record under another id's name, a link to a real record.
    (folder / "lp-aaaaaaaa.json").write_text("{not json", encoding="utf-8")
    (folder / "lp-bbbbbbbb.json").write_bytes(before)
    (folder / "lp-cccccccc.json").symlink_to(folder / f"{second.id}.json")
    assert [item.id for item in learning_store.list_pathways(home, target)] == [second.id, first.id]
    assert learning_store.get_pathway(home, target, "lp-bbbbbbbb") is None and learning_store.get_pathway(home, target, "lp-cccccccc") is None


@pytest.mark.parametrize(
    ("role", "code"),
    [
        ("MLOps engineer, write to jane.roe@example.com", "personal_info_refused"),
        ("Product manager +1 415 555 0100", "personal_info_refused"),
        ("Engineer see https://www.linkedin.com/in/jane-roe", "personal_info_refused"),
        ("", "invalid_value"),
        ("   ", "invalid_value"),
        ("x" * 201, "invalid_value"),
        ("MLOps\nengineer", "invalid_value"),
        (None, "invalid_value"),
    ],
)
def test_a_role_with_contact_data_or_no_role_is_refused_and_nothing_is_stored(home_target: tuple[Path, Path], role: object, code: str) -> None:
    home, target = home_target
    with pytest.raises(LearningError) as refused:
        learning_store.create_request(home, target, role)  # type: ignore[arg-type]
    assert refused.value.code == code
    assert learning_store.list_pathways(home, target) == []
    assert learning_store.create_request(home, target, "x" * 200).role_text == "x" * 200  # 200 characters is allowed


def test_cost_keeps_every_key_and_refuses_what_it_does_not_know() -> None:
    assert learning_store.clean_cost(None) == {
        "cli_model_calls": None, "worker_minutes": None, "web_fetches": None, "web_searches": None, "tokens": None, "note": None,
    }
    full = {"cli_model_calls": 11, "worker_minutes": 135.5, "web_fetches": 430, "web_searches": 16, "tokens": 412_000, "note": "six worker sessions"}
    assert learning_store.clean_cost(full) == full
    assert learning_store.clean_cost({"web_fetches": 0}) == {
        "cli_model_calls": None, "worker_minutes": None, "web_fetches": 0, "web_searches": None, "tokens": None, "note": None,
    }
    for bad in ({"wat": 1}, {"cli_model_calls": -1}, {"cli_model_calls": True}, {"cli_model_calls": 1.5}, {"tokens": -1}, {"tokens": True}, {"worker_minutes": "9"}, {"note": 3}, {"note": "a\nb"}, [1]):
        with pytest.raises(LearningError) as refused:
            learning_store.clean_cost(bad)
        assert refused.value.code == "invalid_value", bad
    with pytest.raises(LearningError) as refused:
        learning_store.clean_cost({"note": "ask jane.roe@example.com"})
    assert refused.value.code == "personal_info_refused"


def test_only_a_plain_allowed_file_inside_a_done_course_resolves(home_target: tuple[Path, Path], tmp_path: Path) -> None:
    home, target = home_target
    done = learning_store.import_course(home, target, make_course(tmp_path / "course"), "MLOps engineer").pathway
    site = learning_store.course_site_dir(home, target, done.id)
    queued = learning_store.create_request(home, target, "Product manager")

    assert learning_store.resolve_course_file(home, target, done.id, "") == site / "index.html"
    assert learning_store.resolve_course_file(home, target, done.id, "index.html") == site / "index.html"
    assert learning_store.resolve_course_file(home, target, done.id, "assets/app.js") == site / "assets" / "app.js"
    assert learning_store.resolve_course_file(home, target, done.id, "assets/lib/LICENSE") == site / "assets" / "lib" / "LICENSE"

    # A file beside the course, and one outside the store, for the escapes below to aim at.
    (site.parent / "secret.txt").write_text("not part of the course", encoding="utf-8")
    (site / ".hidden.html").write_text("<p>dotfile</p>", encoding="utf-8")
    (site / "notes.exe").write_text("not an allowed kind", encoding="utf-8")
    (site / "link.html").symlink_to(site / "index.html")
    (site / "linked").symlink_to(site / "assets", target_is_directory=True)
    (site / "out.txt").symlink_to(site.parent / "secret.txt")
    refused = (
        "../secret.txt", "assets/../../secret.txt", "..", ".", "./index.html", "assets//app.js", "/index.html", "/etc/passwd", "assets/",
        "assets", ".hidden.html", "notes.exe", "link.html", "linked/app.js", "out.txt", "assets\\app.js", "%2e%2e/secret.txt", "index.html%00",
        "index.html\x00", "missing.html", "ASSETS/app.js" if not (site / "ASSETS").exists() else "missing2.html", "café.html", "a b.html",
    )
    for relpath in refused:
        assert learning_store.resolve_course_file(home, target, done.id, relpath) is None, relpath

    # Only a done pathway; an unknown id and a text that is not an id are the same answer.
    assert learning_store.resolve_course_file(home, target, queued.id, "index.html") is None
    (learning_store.course_site_dir(home, target, queued.id)).mkdir(parents=True)
    (learning_store.course_site_dir(home, target, queued.id) / "index.html").write_text("<p>half made</p>", encoding="utf-8")
    assert learning_store.resolve_course_file(home, target, queued.id, "index.html") is None, "a course is served only once its pathway is done"
    for unknown in ("lp-00000000", "nope", "../" + done.id, ""):
        assert learning_store.resolve_course_file(home, target, unknown, "index.html") is None, unknown

    # A course folder that is itself a link is never read through.
    real = site.parent
    moved = real.with_name("moved-aside")
    real.rename(moved)
    real.symlink_to(moved, target_is_directory=True)
    assert learning_store.resolve_course_file(home, target, done.id, "index.html") is None
