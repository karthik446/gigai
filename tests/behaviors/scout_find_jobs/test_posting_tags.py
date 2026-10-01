"""0110-024a: rules tagger + title-keyed tag store. Synthetic titles only."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from gigai.scout.find_jobs import posting_tags
from gigai.scout.find_jobs.ats_board_clients import matches_roles
from gigai.scout.find_jobs.posting_tags import TAGGER_VERSION, normalize_title, tag_new_titles, tag_title
from gigai.scout.find_jobs.tag_store import TagStore


def _store(tmp_path: Path, version: int = TAGGER_VERSION) -> TagStore:
    return TagStore(tmp_path / "cache" / "scout" / "tags.sqlite", tagger_version=version)


# (title, level, function)
_CASES = [
    # the 021 list
    ("Director, Engineering", "director", "software"),
    ("Director Engineering", "director", "software"),
    ("Senior Director, Engineering", "director", "software"),
    ("Director, Software Engineering", "director", "software"),
    ("Sr. Director, Back-End Engineering", "director", "software"),
    ("Director, Machine Learning Engineering", "director", "ai_ml"),
    ("Engineering Director", "director", "software"),
    ("Director, Engineering (Platform)", "director", "software"),
    ("Director of Engineering", "director", "software"),
    ("DIRECTOR OF ENGINEERING", "director", "software"),
    ("Director, AI Platform", "director", "ai_ml"),
    ("Director of AI/ML", "director", "ai_ml"),
    ("Senior Software Engineer", "senior", "software"),
    ("Software Engineering Manager", "manager", "software"),
    ("Staff Platform Engineer", "staff", "software"),
    ("Engineer, Software Platform", "mid", "software"),
    ("Data Engineers", "mid", "data"),
    ("Principal Machine Learning Engineer", "principal", "ai_ml"),
    ("Backend Software Engineer II", "mid", "software"),
    # look-alikes the 021 prefilter lets through
    ("Sales Engineering Director", "director", "solutions"),
    ("Director, Mechanical Engineering", "director", "hardware"),
    ("Director, Solutions Engineering", "director", "solutions"),
    ("Director of Hardware Engineering", "director", "hardware"),
    ("Director, Data Engineering", "director", "data"),
    ("Director of Security Engineering", "director", "security_it"),
    ("Director, Engineering Operations", "director", "software"),
    ("Director of Product Management", "director", "product"),
    # "cto" must not fire inside "director" (and friends)
    ("Director of Sales", "director", "sales"),
    ("Director, Marketing", "director", "marketing"),
    ("Director, People", "director", "people"),
    ("Director of Finance", "director", "finance"),
    ("Director, Customer Success", "director", "customer"),
    ("Director, Clinical Operations", "director", "healthcare"),
    ("Director of Design", "director", "design"),
    ("Director, Legal", "director", "legal"),
    # other levels
    ("CTO", "chief", "software"),
    ("Chief Technology Officer", "chief", "software"),
    ("Chief Financial Officer", "chief", "finance"),
    ("Chief of Staff", "mid", "operations"),
    ("VP, Engineering", "vp", "software"),
    ("Vice President of Sales", "vp", "sales"),
    ("SVP Product", "vp", "product"),
    ("Head of Engineering", "head", "software"),
    ("Head of Data", "head", "data"),
    ("Engineering Manager", "manager", "software"),
    ("Technical Program Manager", "manager", "operations"),
    ("Principal Engineer", "principal", "software"),
    ("Tech Lead, Backend", "lead", "software"),
    ("Junior Designer", "junior", "design"),
    ("Software Engineering Intern", "intern", "software"),
    ("Associate Director, Engineering", "director", "software"),
    ("Account Executive", "mid", "sales"),
    ("Registered Nurse", "mid", "healthcare"),
    ("Barista", "mid", None),
]


@pytest.mark.parametrize(("title", "level", "function"), _CASES, ids=[c[0] for c in _CASES])
def test_title_tags(title: str, level: str, function: str | None) -> None:
    tag = tag_title(title)
    assert (tag.level, tag.function) == (level, function), title


def test_cto_inside_director_is_not_a_chief() -> None:
    for title in ("Director of Sales", "Director, Engineering", "Directors Assistant"):
        assert tag_title(title).level != "chief", title


def test_lookalikes_pass_the_021_matcher_but_are_not_software() -> None:
    roles = ("Director of Engineering",)
    for title in ("Director, Mechanical Engineering", "Sales Engineering Director"):
        assert matches_roles(title, roles)
        assert tag_title(title).function != "software"
    assert tag_title("Director, Engineering").function == "software"


def test_normalized_title_is_punctuation_and_case_insensitive() -> None:
    assert normalize_title("Director, Engineering") == normalize_title("DIRECTOR  ENGINEERING")


def test_tag_new_titles_writes_and_reads_back(tmp_path: Path) -> None:
    store = _store(tmp_path)
    counts = tag_new_titles(store, ["Director, Engineering", "Director Engineering", "Barista", "", "Sales Engineering Director"])
    assert (counts.distinct, counts.already_tagged, counts.tagged) == (3, 0, 3)
    got = store.get(normalize_title("Director of Engineering"))
    assert got is None
    got = store.get(normalize_title("Sales Engineering Director"))
    assert (got.level, got.function, got.level_source, got.function_source) == ("director", "solutions", "rules", "rules")
    barista = store.get("barista")
    assert (barista.level, barista.function, barista.function_source) == ("mid", None, None)


def test_retagging_unchanged_titles_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tag_new_titles(store, ["Director, Engineering", "Barista"])
    conn = store._conn()
    before = conn.total_changes
    counts = tag_new_titles(store, ["Director, Engineering", "Barista"])
    assert (counts.already_tagged, counts.tagged) == (2, 0)
    assert conn.total_changes == before


def test_deleting_the_store_rebuilds_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tag_new_titles(store, ["Director, Engineering"])
    for suffix in ("", "-wal", "-shm"):
        Path(f"{store.path}{suffix}").unlink(missing_ok=True)
    assert store.get("director engineering") is None  # same store object, same thread: reopened empty
    counts = tag_new_titles(store, ["Director, Engineering"])
    assert counts.tagged == 1
    assert store.get("director engineering").level == "director"


def test_missing_directory_and_corrupt_file_rebuild(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_bytes(b"this is not a sqlite database" * 50)
    assert tag_new_titles(store, ["Barista"]).tagged == 1
    assert store.get("barista") is not None


def test_tagger_version_bump_invalidates(tmp_path: Path) -> None:
    v1 = _store(tmp_path, 1)
    tag_new_titles(v1, ["Director, Engineering"])
    v1.close()
    v2 = _store(tmp_path, 2)
    assert v2.get("director engineering") is None
    assert v2.count() == 0
    counts = tag_new_titles(v2, ["Director, Engineering"])
    assert (counts.already_tagged, counts.tagged) == (0, 1)
    assert v2.get("director engineering").tagger_version == 2


def test_schema_version_mismatch_rebuilds_the_cache(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tag_new_titles(store, ["Barista"])
    store.close()
    raw = sqlite3.connect(store.path)
    raw.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
    raw.commit()
    raw.close()
    assert store.get("barista") is None
    assert tag_new_titles(store, ["Barista"]).tagged == 1


def test_function_lacking_listing_and_model_fill(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tag_new_titles(store, ["Barista", "Wizard of Oz", "Director, Engineering"])
    assert store.titles_lacking_function() == ["barista", "wizard of oz"]
    assert "director engineering" in store.titles_lacking_function(include_rules=True)
    assert store.set_model_function("barista", "operations", model="fake-model", prompt_version="tag-v1")
    assert not store.set_model_function("unknown title", "operations", model="m", prompt_version="tag-v1")
    assert store.titles_lacking_function() == ["wizard of oz"]
    got = store.get("barista")
    assert (got.function, got.function_source, got.model, got.prompt_version) == ("operations", "model", "fake-model", "tag-v1")
    assert "barista" not in store.titles_lacking_function(include_rules=True)


def test_model_function_survives_a_rules_version_bump_but_level_is_retagged(tmp_path: Path) -> None:
    v1 = _store(tmp_path, 1)
    tag_new_titles(v1, ["Barista"])
    v1.set_model_function("barista", "operations", model="m", prompt_version="tag-v1")
    v1.close()
    v2 = _store(tmp_path, 2)
    tag_new_titles(v2, ["Barista"])
    got = v2.get("barista")
    assert (got.tagger_version, got.function, got.function_source) == (2, "operations", "model")


def test_default_store_path_and_version(tmp_path: Path) -> None:
    store = posting_tags.default_store(tmp_path)
    assert store.path == tmp_path / "cache" / "scout" / "tags.sqlite"
    assert store.tagger_version == TAGGER_VERSION
    tag_new_titles(store, ["Barista"])
    assert store.path.exists()


def test_one_writer_with_concurrent_readers(tmp_path: Path) -> None:
    store = _store(tmp_path)
    titles = [f"Director, Team {i}" for i in range(600)]
    errors: list[BaseException] = []
    done = threading.Event()

    def read() -> None:
        try:
            while not done.is_set():
                store.get_many(normalize_title(t) for t in titles[:50])
                store.count()
                store.titles_lacking_function(10)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            store.close()

    readers = [threading.Thread(target=read) for _ in range(4)]
    for t in readers:
        t.start()
    try:
        for start in range(0, len(titles), 50):
            tag_new_titles(store, titles[start : start + 50])
    finally:
        done.set()
        for t in readers:
            t.join(10)
    assert not errors, errors
    assert store.count() == 600
    mode = store._conn().execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_writes_from_two_threads_are_serialized(tmp_path: Path) -> None:
    store = _store(tmp_path)
    errors: list[BaseException] = []

    def write(prefix: str) -> None:
        try:
            tag_new_titles(store, [f"{prefix} Manager {i}" for i in range(200)])
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            store.close()

    threads = [threading.Thread(target=write, args=(p,)) for p in ("Alpha", "Beta")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not errors, errors
    assert store.count() == 400
