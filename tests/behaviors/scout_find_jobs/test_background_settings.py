"""0110-026f: the background settings writer, the running thread honouring a write, and the status blocks.

In-process (``serve`` on a loopback port, a real ``RefreshTicker`` with a
fake model tag queue), with no ``GIGAI_SCOUT_AUTO_REFRESH`` override, so the
settings file is what decides. The supervised-server journey
(``tests/api_e2e/test_background_settings_journey.py``) covers the route's
round trip and validation; here:

* a ``PUT`` that turns ``sources.auto_refresh`` off silences the refresh
  thread at its next look (no tick, no tag drain), and one that turns it on
  brings the drain back; the ``PUT`` itself wakes the thread;
* the ``tags`` / ``text_index`` / ``refresh`` blocks carry the right counts
  on a synthetic home, with the queue's failures, last error and retry time;
* the writer changes only what it is given, atomically, under concurrent writes;
* 0110-033: ``sources.check_times`` is read back, validated, written one day
  at a time, reset by ``null``, and a ``PUT`` moves the running thread's next
  check (a fake clock in a fixed zone).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import threading
import time
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs import posting_tags, text_index
from gigai.scout.find_jobs.background_settings import (
    SettingsError,
    SettingsUnreadableError,
    background_settings,
    validate_patch,
    write_background_settings,
)
from gigai.scout.find_jobs.model_tag import MODEL_TAGS_ENV, DrainResult, tagging_setting
from gigai.scout.find_jobs.posting_tags import normalize_title, tag_new_titles
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve
from gigai.scout.find_jobs.refresh_tick import (
    AUTO_REFRESH_ENV,
    STATE_DISABLED,
    RefreshTicker,
    auto_refresh_setting,
    check_schedule_setting,
    settings_path,
)
from gigai.scout.find_jobs.snapshot import MANIFEST_URL_ENV, SNAPSHOT_ENV, snapshot_setting
from gigai.scout.find_jobs.sources_status import refresh_block, tags_block, text_index_block
from gigai.scout.find_jobs.text_index import TextPosting

from .test_sources_update import _installed


@pytest.fixture(autouse=True)
def _no_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """The settings file decides: no environment override is in force."""

    for name in (AUTO_REFRESH_ENV, MODEL_TAGS_ENV, SNAPSHOT_ENV, MANIFEST_URL_ENV):
        monkeypatch.delenv(name, raising=False)


class _FakeQueue:
    """The model tag queue's surface, with no model: it counts its drains and reports a failed lane."""

    def __init__(self) -> None:
        self.drains = 0
        self.kicks = 0

    def drain(self, *, stop=None, max_batches=None) -> DrainResult:
        self.drains += 1
        return DrainResult("idle")

    def kick(self, *, reset_backoff: bool = False) -> None:
        self.kicks += 1

    def status(self) -> dict[str, object]:
        lane = {"model": None, "batches": 0, "calls": 0, "tagged": 0, "rejected": 0, "failures": 0, "consecutive_failures": 0, "last_error": None, "last_error_at": None, "retry_after": None}
        return {
            "setting": {"model_enabled": True, "backfill_enabled": False, "tag_backfill_model": "configured", "source": "default"},
            "prompt_version": "tag-v1",
            "batch_size": 50,
            "batches_per_tick": 4,
            "state": "backoff",
            "last_drain_at": "2026-10-01T12:01:00Z",
            "last_drain": {"state": "backoff", "batches": 0, "tagged": 0, "rejected": 0, "calls": 1},
            "parked": 2,
            "demand": {**lane, "model": "claude_cli:sonnet", "calls": 1, "failures": 3, "consecutive_failures": 3, "last_error": "model_target_unavailable: no key", "last_error_at": "2026-10-01T12:01:00Z", "retry_after": "2026-10-01T12:21:00Z"},
            "backfill": dict(lane),
        }


def _wait_for(condition, *, seconds: float = 20.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "the condition never became true"
        time.sleep(0.01)


def _must_not_tick(*args, **kwargs):
    raise AssertionError("a refresh tick ran")


class _Served:
    """A real in-process server with a real refresh thread around a fake tag queue."""

    def __init__(self, home: Path, target: Path, *, poll_seconds: float) -> None:
        self.queue = _FakeQueue()
        self.server = serve(backend=ScoutFindJobsBackend(home_root=home, target=target), bind=("127.0.0.1", 0))
        self.ticker = RefreshTicker(home_root=home, target=target, tag_queue=self.queue, run_tick=_must_not_tick, poll_seconds=poll_seconds)
        self.server.refresh_ticker = self.ticker
        # Count the thread's looks, so a test can wait for one instead of sleeping and hoping.
        self.looks = 0
        look = self.ticker._refresh_step

        def counted() -> str:
            outcome = look()
            self.looks += 1
            return outcome

        self.ticker._refresh_step = counted  # type: ignore[method-assign]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        host, port = self.server.server_address[0], self.server.server_address[1]
        self.client = httpx.Client(base_url=f"http://{host}:{port}", timeout=30.0)

    def __enter__(self) -> "_Served":
        self.thread.start()
        self.ticker.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


# --- the running thread honours a write --------------------------------------------------


def test_a_put_that_turns_auto_refresh_off_silences_the_running_thread_and_on_brings_it_back(tmp_path: Path) -> None:
    home, target = _installed(tmp_path)
    # The thread looks once at start, then once an hour: every later look in this test is one a PUT (or a kick) woke.
    with _Served(home, target, poll_seconds=3600.0) as served:
        queue, ticker, client = served.queue, served.ticker, served.client
        # On by default: the check thread looked once and the tag thread drained once (0110-028: two threads).
        _wait_for(lambda: queue.drains == 1 and served.looks == 1)
        assert auto_refresh_setting(home, target).enabled is True
        assert client.get("/api/sources/update").json()["refresh"]["enabled"] is True

        off = client.put("/api/settings/background", json={"sources": {"auto_refresh": False}})
        assert off.status_code == 200, off.text
        effective = off.json()["effective"]["sources"]
        assert (effective["auto_refresh"], effective["source"]) == (False, "setting")
        # The PUT woke the thread; its next look read the file and stood down.
        _wait_for(lambda: served.looks == 2)
        assert ticker._last_state == STATE_DISABLED
        # Every further look is silent too: no tick (it would raise), no drain.
        for look in (3, 4, 5):
            ticker.kick_tags()
            _wait_for(lambda: served.looks == look)
        time.sleep(0.05)  # the drain, if one were coming, is woken by the same kick on the tag thread
        assert queue.drains == 1, "the tag queue drained with auto refresh off"
        assert ticker._last_state == STATE_DISABLED and ticker.alive
        status = client.get("/api/sources/update").json()
        assert (status["background"]["state"], status["background"]["auto_refresh"]) == ("disabled", {"enabled": False, "source": "setting", "active": True})
        assert (status["refresh"]["enabled"], status["refresh"]["state"], status["refresh"]["next_tick_at"]) == (False, "disabled", None)

        on = client.put("/api/settings/background", json={"sources": {"auto_refresh": True}})
        assert on.status_code == 200, on.text
        _wait_for(lambda: queue.drains == 2 and served.looks == 6)  # both threads woken again, and the tag thread drained
        assert client.get("/api/sources/update").json()["background"]["auto_refresh"] == {"enabled": True, "source": "setting", "active": True}


def test_a_written_setting_is_what_each_reader_returns_on_its_next_read(tmp_path: Path) -> None:
    home, target = _installed(tmp_path)

    write_background_settings(home, target, validate_patch({
        "sources": {"auto_refresh": False},
        "tagging": {"model_enabled": False, "backfill_enabled": True, "tag_backfill_model": "openai"},
        "snapshot": {"enabled": False, "manifest_url": "https://data.example.test/manifest.json"},
    }))

    refresh, tagging, snapshot = auto_refresh_setting(home, target), tagging_setting(home, target), snapshot_setting(home, target)
    assert (refresh.enabled, refresh.source) == (False, "setting")
    assert (tagging.model_enabled, tagging.backfill_enabled, tagging.backfill_model, tagging.source) == (False, True, "openai", "setting")
    assert (snapshot.enabled, snapshot.manifest_url, snapshot.source) == (False, "https://data.example.test/manifest.json", "setting")
    # The environment still wins over the file, and the API says which decided.
    body = background_settings(home, target, environ={AUTO_REFRESH_ENV: "1"})
    assert body["settings"]["sources"]["auto_refresh"] is False
    assert (body["effective"]["sources"]["auto_refresh"], body["effective"]["sources"]["source"]) == (True, "environment")


# --- the status blocks on a synthetic home ------------------------------------------------


def test_the_tags_block_counts_rules_model_other_and_awaiting_and_carries_the_queue(tmp_path: Path) -> None:
    home, target = _installed(tmp_path)
    blank = tags_block(home, target)
    assert (blank["available"], blank["titles"], blank["awaiting_model"], blank["queue"]) == (False, 0, 0, None)
    assert not posting_tags.default_store(home).path.exists()  # a status read never creates the store

    store = posting_tags.default_store(home)
    placed = ["Software Engineer", "Senior Software Engineer", "Data Engineer", "Product Manager"]
    unplaced = ["Wizard of Light Bulb Moments", "Chief Happiness Hero", "Keeper of the Flame", "Voice of the Mountain", "Friend of the Garden"]
    tag_new_titles(store, placed + unplaced)
    assert store.count_awaiting_model() == len(unplaced), "the fixture's odd titles must be ones the rules cannot place"
    assert store.set_model_function(normalize_title(unplaced[0]), "operations", model="claude_cli:sonnet", prompt_version="tag-v1")
    assert store.set_model_function(normalize_title(unplaced[1]), "people", model="claude_cli:sonnet", prompt_version="tag-v1")
    # A model looked at this one and found no family ("other"): function stays NULL, and it is NOT awaiting.
    assert store.set_model_function(normalize_title(unplaced[2]), None, model="claude_cli:sonnet", prompt_version="tag-v1")
    awaiting = store.count_awaiting_model()
    store.close()

    queue = _FakeQueue()
    ticker = RefreshTicker(home_root=home, target=target, tag_queue=queue, run_tick=_must_not_tick)
    block = tags_block(home, target, ticker=ticker)

    assert {name: block[name] for name in ("available", "titles", "tagged_by_rules", "tagged_by_model", "model_other", "awaiting_model")} == {
        "available": True, "titles": 9, "tagged_by_rules": 4, "tagged_by_model": 2, "model_other": 1, "awaiting_model": 2,
    }
    assert block["awaiting_model"] == awaiting == 2  # TagStore.count_awaiting_model, not "function IS NULL" (that is 3)
    assert block["tagged_by_rules"] + block["tagged_by_model"] + block["model_other"] + block["awaiting_model"] == block["titles"]
    assert block["setting"] == {"model_enabled": True, "backfill_enabled": False, "tag_backfill_model": "configured", "source": "default"}
    # The queue's status as the thread reports it: failures, the last error, when the lane retries.
    assert block["queue"] == queue.status()
    demand = block["queue"]["demand"]
    assert (demand["failures"], demand["last_error"], demand["retry_after"]) == (3, "model_target_unavailable: no key", "2026-10-01T12:21:00Z")
    # The model each lane asks: the one that answered once a lane has called, else the configured target.
    configured = json.loads((target / "find-jobs.json").read_text(encoding="utf-8"))["default_model_target"]
    assert block["models"] == {"demand": "claude_cli:sonnet", "backfill": configured}

    # The backfill model follows the setting.
    write_background_settings(home, target, validate_patch({"tagging": {"backfill_enabled": True, "tag_backfill_model": "haiku"}}))
    assert tags_block(home, target)["models"] == {"demand": configured, "backfill": "claude_cli:haiku"}
    write_background_settings(home, target, validate_patch({"tagging": {"tag_backfill_model": "openai"}}))
    assert tags_block(home, target)["models"]["backfill"] == "openai_api"

    # A newly tagged title shows at the next read (the counts are cached only while the file is unchanged).
    reopened = posting_tags.default_store(home)
    tag_new_titles(reopened, ["Staff Software Engineer"])
    reopened.close()
    assert tags_block(home, target)["titles"] == 10


def test_the_text_index_block_counts_postings_with_text_and_unchecked(tmp_path: Path) -> None:
    assert text_index_block(tmp_path) == {"available": False, "postings_with_text": 0, "unchecked": 0}
    assert not text_index.text_index_path(tmp_path).exists()

    assert text_index.upsert_company(tmp_path, "greenhouse:acme", [
        TextPosting("1", "Software Engineer", "Kubernetes and Go."),
        TextPosting("2", "Data Engineer", "Spark."),
        TextPosting("3", "Platform Engineer", None),
    ])
    assert text_index.upsert_company(tmp_path, "lever:initech", [TextPosting("a", "Designer", ""), TextPosting("b", "Writer", "Docs.")])
    text_index.close(tmp_path)

    assert text_index_block(tmp_path) == {"available": True, "postings_with_text": 3, "unchecked": 2}

    # A file the block cannot read is reported unavailable and left exactly as it is.
    other = tmp_path / "other"
    path = text_index.text_index_path(other)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not a database")
    assert text_index_block(other) == {"available": False, "postings_with_text": 0, "unchecked": 0}
    assert path.read_bytes() == b"not a database"


def test_the_refresh_block_says_when_the_sources_were_updated_and_when_the_next_check_is() -> None:
    now = datetime(2026, 10, 1, 12, 12, 30, tzinfo=timezone.utc)
    waiting = {
        "auto_refresh": {"enabled": True, "source": "default", "active": True},
        "state": "waiting",
        "in_progress": False,
        "trigger": "auto",
        "last_update": {"update_id": "u1", "status": "succeeded", "trigger": "auto", "started_at": "2026-10-01T11:50:00.000Z", "finished_at": "2026-10-01T12:00:00.000Z"},
        "next_tick_at": "2026-10-01T12:50:00.000Z",
    }
    assert refresh_block(waiting, now=now) == {
        "enabled": True,
        "state": "waiting",
        "in_progress": False,
        "trigger": "auto",
        "last_updated_at": "2026-10-01T12:00:00.000Z",
        "last_updated_minutes_ago": 12,
        "next_tick_at": "2026-10-01T12:50:00.000Z",
        "next_tick_in_minutes": 37,
        "schedule": None,  # 0110-029: the caller passes the schedule (schedule_status); this call did not
    }
    # A tick that is due now: 0 minutes, never negative.
    due = {**waiting, "state": "due", "next_tick_at": "2026-10-01T12:00:00.000Z"}
    assert refresh_block(due, now=now + timedelta(hours=3))["next_tick_in_minutes"] == 0

    running = {"auto_refresh": {"enabled": True}, "state": "running", "in_progress": True, "trigger": "manual", "last_update": None, "next_tick_at": None}
    assert refresh_block(running, now=now) == {
        "enabled": True, "state": "running", "in_progress": True, "trigger": "manual",
        "last_updated_at": None, "last_updated_minutes_ago": None, "next_tick_at": None, "next_tick_in_minutes": None, "schedule": None,
    }
    off = {"auto_refresh": {"enabled": False}, "state": "disabled", "in_progress": False, "trigger": None, "last_update": None, "next_tick_at": None}
    assert (refresh_block(off, now=now)["enabled"], refresh_block(off, now=now)["state"]) == (False, "disabled")


# --- the writer ------------------------------------------------------------------------------


def test_the_writer_changes_only_what_it_is_given_and_keeps_what_it_does_not_know(tmp_path: Path) -> None:
    home, target = _installed(tmp_path)
    path = settings_path(home, target)
    assert not path.exists()

    assert write_background_settings(home, target, validate_patch({"sources": {"auto_refresh": False}})) == path
    assert json.loads(path.read_text(encoding="utf-8")) == {"schema_version": "scout-settings:1", "sources": {"auto_refresh": False}}

    path.write_text(json.dumps({
        "schema_version": "scout-settings:1",
        "sources": {"auto_refresh": False, "kept": [1, 2]},
        "tagging": {"backfill_enabled": True},
        "unknown_block": {"x": {"y": None}},
        "unknown_value": "stays",
    }), encoding="utf-8")
    write_background_settings(home, target, validate_patch({"tagging": {"tag_backfill_model": "haiku"}, "snapshot": {"enabled": False}}))
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": "scout-settings:1",
        "sources": {"auto_refresh": False, "kept": [1, 2]},
        "tagging": {"backfill_enabled": True, "tag_backfill_model": "haiku"},
        "unknown_block": {"x": {"y": None}},
        "unknown_value": "stays",
        "snapshot": {"enabled": False},
    }
    assert [item.name for item in path.parent.iterdir() if "settings" in item.name] == ["settings.json"]


@pytest.mark.parametrize(
    "stored",
    [
        "not json",
        "[]",
        '{"schema_version": "scout-settings:2", "sources": {"auto_refresh": true}}',
        '{"sources": {"auto_refresh": true}}',
        '{"schema_version": "scout-settings:1", "sources": "on"}',
    ],
)
def test_a_stored_file_the_writer_cannot_read_is_refused_and_left_byte_for_byte(tmp_path: Path, stored: str) -> None:
    home, target = _installed(tmp_path)
    path = settings_path(home, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stored, encoding="utf-8")

    with pytest.raises(SettingsUnreadableError):
        write_background_settings(home, target, validate_patch({"sources": {"auto_refresh": True}}))

    assert path.read_text(encoding="utf-8") == stored
    assert [item.name for item in path.parent.iterdir() if "settings" in item.name] == ["settings.json"]


def test_a_failed_write_leaves_the_old_file_and_no_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _installed(tmp_path)
    path = write_background_settings(home, target, validate_patch({"sources": {"auto_refresh": False}}))
    before = path.read_bytes()

    def fail(source, destination):
        raise OSError("disk full")

    monkeypatch.setattr("gigai.scout.find_jobs.background_settings.os.replace", fail)
    with pytest.raises(OSError):
        write_background_settings(home, target, validate_patch({"sources": {"auto_refresh": True}}))

    assert path.read_bytes() == before
    assert [item.name for item in path.parent.iterdir() if "settings" in item.name] == ["settings.json"]


def test_concurrent_writes_to_different_keys_all_land(tmp_path: Path) -> None:
    home, target = _installed(tmp_path)
    patches = [
        {"sources": {"auto_refresh": False}},
        {"tagging": {"model_enabled": False}},
        {"tagging": {"backfill_enabled": True}},
        {"tagging": {"tag_backfill_model": "haiku"}},
        {"snapshot": {"enabled": False}},
        {"snapshot": {"manifest_url": "https://data.example.test/manifest.json"}},
    ]
    start = threading.Barrier(len(patches))

    def write(patch: dict) -> None:
        start.wait()
        write_background_settings(home, target, validate_patch(patch))

    threads = [threading.Thread(target=write, args=(patch,)) for patch in patches]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not any(thread.is_alive() for thread in threads)
    # Read, merge and replace are one step under the writer's lock: no write is lost.
    assert json.loads(settings_path(home, target).read_text(encoding="utf-8")) == {
        "schema_version": "scout-settings:1",
        "sources": {"auto_refresh": False},
        "tagging": {"model_enabled": False, "backfill_enabled": True, "tag_backfill_model": "haiku"},
        "snapshot": {"enabled": False, "manifest_url": "https://data.example.test/manifest.json"},
    }


def test_a_patch_is_validated_before_anything_is_written() -> None:
    assert validate_patch({"snapshot": {"manifest_url": " https://a.test/m.json "}}) == {"snapshot": {"manifest_url": "https://a.test/m.json"}}
    for body, code in (
        (None, "wrong_type"),
        ({"sources": {"auto_refresh": 1}}, "wrong_type"),
        ({"tagging": {"tag_backfill_model": "sonnet"}}, "bad_enum"),
        ({"snapshot": {"manifest_url": "/tmp/manifest.json"}}, "invalid_value"),
        ({"snapshot": {"manifest_url": "https://"}}, "invalid_value"),
        ({"snapshot": {"manifest_url": "https://a.test/" + "x" * 2048}}, "invalid_value"),
        ({"tagging": {"bogus": True}}, "unknown_key"),
        ({"nope": {}}, "unknown_key"),
        ({}, "invalid_value"),
    ):
        with pytest.raises(SettingsError) as caught:
            validate_patch(body)
        assert caught.value.code == code, body


def test_a_put_for_a_target_with_no_scout_project_is_404(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = tmp_path / "not-a-project"
    home.mkdir()
    target.mkdir()
    server = serve(backend=ScoutFindJobsBackend(home_root=home, target=target), bind=("127.0.0.1", 0))
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=30.0) as client:
            refused = client.put("/api/settings/background", json={"sources": {"auto_refresh": False}})
            assert refused.status_code == 404 and refused.json()["error"]["code"] == "target_unavailable", refused.text
            # Reading still answers: with no project there is no file, so the defaults apply.
            defaults = client.get("/api/settings/background")
            assert defaults.status_code == 200 and defaults.json()["settings"]["sources"] == {"auto_refresh": True, "check_times": _DEFAULT_TIMES}
            assert defaults.json()["effective"]["sources"]["check_times"]["source"] == "default"
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)
    assert not (home / "scout").exists()


# --- 0110-033: sources.check_times ----------------------------------------------------------

_DEFAULT_TIMES = {
    "weekdays": ["03:00", "07:00", "09:00", "11:00", "13:00", "15:00", "17:00", "19:00"],
    "weekends": ["09:00", "18:00"],
}


def test_check_times_are_read_back_written_one_day_at_a_time_and_reset_by_null(tmp_path: Path) -> None:
    home, target = _installed(tmp_path)
    path = settings_path(home, target)

    fresh = background_settings(home, target)
    assert fresh["settings"]["sources"] == {"auto_refresh": True, "check_times": _DEFAULT_TIMES}
    assert fresh["effective"]["sources"]["check_times"] == {**_DEFAULT_TIMES, "source": "default", "default": _DEFAULT_TIMES}
    assert not path.exists()

    # One day named: sorted, repeats dropped; the other day keeps its default and is not written.
    write_background_settings(home, target, validate_patch({"sources": {"check_times": {"weekdays": ["13:30", "07:00", "13:30"]}}}))
    assert json.loads(path.read_text(encoding="utf-8")) == {"schema_version": "scout-settings:1", "sources": {"check_times": {"weekdays": ["07:00", "13:30"]}}}
    body = background_settings(home, target)
    assert body["settings"]["sources"]["check_times"] == {"weekdays": ["07:00", "13:30"], "weekends": _DEFAULT_TIMES["weekends"]}
    assert body["effective"]["sources"]["check_times"] == {
        "weekdays": ["07:00", "13:30"], "weekends": _DEFAULT_TIMES["weekends"], "source": "setting", "default": _DEFAULT_TIMES,
    }
    # The thread's own reader returns the same times (it reads the file at every look).
    schedule = check_schedule_setting(home, target)
    assert (schedule.schedule.weekdays, schedule.schedule.weekends, schedule.source) == (("07:00", "13:30"), ("09:00", "18:00"), "setting")

    # The other day: the first one stays; so does a key this version does not know, and auto_refresh.
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["sources"]["kept"] = [1]
    path.write_text(json.dumps(stored), encoding="utf-8")
    write_background_settings(home, target, validate_patch({"sources": {"auto_refresh": False, "check_times": {"weekends": ["10:00"]}}}))
    assert json.loads(path.read_text(encoding="utf-8"))["sources"] == {
        "check_times": {"weekdays": ["07:00", "13:30"], "weekends": ["10:00"]}, "kept": [1], "auto_refresh": False,
    }

    # null for one day: its default again; the other day is untouched.
    write_background_settings(home, target, validate_patch({"sources": {"check_times": {"weekdays": None}}}))
    assert json.loads(path.read_text(encoding="utf-8"))["sources"]["check_times"] == {"weekends": ["10:00"]}
    assert background_settings(home, target)["settings"]["sources"]["check_times"] == {"weekdays": _DEFAULT_TIMES["weekdays"], "weekends": ["10:00"]}

    # null for the whole setting: both defaults; nothing else in the block is lost.
    write_background_settings(home, target, validate_patch({"sources": {"check_times": None}}))
    assert json.loads(path.read_text(encoding="utf-8"))["sources"] == {"kept": [1], "auto_refresh": False}
    assert background_settings(home, target)["effective"]["sources"]["check_times"]["source"] == "default"

    # The last day reset empties the stored object: the key goes too.
    write_background_settings(home, target, validate_patch({"sources": {"check_times": {"weekends": ["08:00"]}}}))
    write_background_settings(home, target, validate_patch({"sources": {"check_times": {"weekends": None}}}))
    assert "check_times" not in json.loads(path.read_text(encoding="utf-8"))["sources"]


def test_stored_times_that_cannot_be_used_show_the_defaults_and_say_so_and_a_put_replaces_them(tmp_path: Path) -> None:
    home, target = _installed(tmp_path)
    path = settings_path(home, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "scout-settings:1", "sources": {"check_times": {"weekdays": ["noon"]}}}), encoding="utf-8")

    body = background_settings(home, target)
    assert body["readable"] is True  # the file is readable; only its times are not usable
    assert body["settings"]["sources"]["check_times"] == _DEFAULT_TIMES
    assert body["effective"]["sources"]["check_times"]["source"] == "settings_unreadable"

    write_background_settings(home, target, validate_patch({"sources": {"check_times": {"weekdays": ["12:00"]}}}))
    assert background_settings(home, target)["effective"]["sources"]["check_times"]["source"] == "setting"
    assert check_schedule_setting(home, target).schedule.weekdays == ("12:00",)

    # A stored value that is not even an object is replaced whole.
    path.write_text(json.dumps({"schema_version": "scout-settings:1", "sources": {"check_times": "hourly"}}), encoding="utf-8")
    write_background_settings(home, target, validate_patch({"sources": {"check_times": {"weekends": ["11:00"]}}}))
    assert json.loads(path.read_text(encoding="utf-8"))["sources"]["check_times"] == {"weekends": ["11:00"]}


@pytest.mark.parametrize(
    ("check_times", "code"),
    [
        ("07:00", "wrong_type"),
        (["07:00"], "wrong_type"),
        ({}, "invalid_value"),
        ({"weekdays": "07:00"}, "wrong_type"),
        ({"weekdays": [7]}, "wrong_type"),
        ({"weekdays": []}, "invalid_value"),  # 1 to 12 times: none is refused
        ({"weekends": [f"{hour:02d}:00" for hour in range(13)]}, "invalid_value"),  # 13
        ({"weekdays": ["7:00"]}, "invalid_value"),  # HH:MM, two digits each
        ({"weekdays": ["07:0"]}, "invalid_value"),
        ({"weekdays": ["24:00"]}, "invalid_value"),
        ({"weekdays": ["07:60"]}, "invalid_value"),
        ({"weekdays": [" 07:00"]}, "invalid_value"),
        ({"weekdays": ["07:00", "noon"]}, "invalid_value"),
        ({"saturday": ["07:00"]}, "unknown_key"),
        ({"weekdays": ["07:00"], "weekends": ["25:00"]}, "invalid_value"),  # one bad day refuses both
    ],
)
def test_check_times_a_put_refuses(tmp_path: Path, check_times: object, code: str) -> None:
    with pytest.raises(SettingsError) as refused:
        validate_patch({"sources": {"check_times": check_times}})
    assert refused.value.code == code, str(refused.value)


def test_check_times_twelve_a_day_is_the_most_and_repeats_do_not_count() -> None:
    twelve = [f"{hour:02d}:30" for hour in range(12)]
    assert validate_patch({"sources": {"check_times": {"weekdays": [*reversed(twelve), "00:30"]}}})["sources"]["check_times"] == {"weekdays": twelve}


def test_a_put_of_check_times_moves_the_running_threads_next_check_on_a_fake_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The thread reads the file at every look: a saved time is the next check, with no restart."""

    from gigai.scout.find_jobs import sources_update
    from gigai.scout.find_jobs.company_index import CompanyIndex, index_stamp
    from gigai.scout.find_jobs.refresh_tick import STATE_WAITING, decide
    from gigai.scout.find_jobs.sources_update import STATUS_SUCCEEDED, run_refresh_tick, run_sources_update

    from .test_refresh_core import _project
    from .test_refresh_schedule import FAST, THURSDAY, ZONE, _Clock
    from .test_sources_update import _Boards

    home, target = _project(tmp_path)
    boards = _Boards()
    clock = _Clock(THURSDAY + timedelta(hours=7, minutes=30))
    monkeypatch.setattr(sources_update, "index_stamp", lambda moment=None: index_stamp(moment if moment is not None else clock.now))
    with boards.client() as client:
        assert run_sources_update(home_root=home, target=target, client=client, limits=FAST).status == STATUS_SUCCEEDED  # the first update, 07:30

    def fast_tick(*args, **more):
        return run_refresh_tick(*args, limits=FAST, **more)

    def next_check() -> datetime | None:
        """When the thread's next look would run a check: its own decision, at the fake clock's now."""

        index = CompanyIndex.for_home(home)
        return decide(
            index.read_update_summary(), indexed=True, enabled=True, now=clock.now,
            schedule=ticker.schedule_setting().schedule, tz=ZONE,
        ).next_tick_at

    def at(hours: int, minutes: int = 0) -> datetime:
        return THURSDAY + timedelta(hours=hours, minutes=minutes)

    server = serve(backend=ScoutFindJobsBackend(home_root=home, target=target), bind=("127.0.0.1", 0))
    ticker = RefreshTicker(home_root=home, target=target, client_factory=boards.client, clock=clock, run_tick=fast_tick, tz=ZONE, model_tags=False)
    server.refresh_ticker = ticker  # stepped by hand below: the looks are the test's, not a thread's
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=30.0) as client:
            clock.now = at(8, 10)
            assert ticker.step() == STATE_WAITING
            assert next_check() == at(9)  # the default 09:00

            saved = client.put("/api/settings/background", json={"sources": {"check_times": {"weekdays": ["12:00", "08:30"]}}})
            assert saved.status_code == 200, saved.text
            assert saved.json()["settings"]["sources"]["check_times"]["weekdays"] == ["08:30", "12:00"]
            assert client.get("/api/sources/update").json()["refresh"]["schedule"]["weekdays"] == ["08:30", "12:00"]
            assert next_check() == at(8, 30)

            clock.now = at(8, 29)
            assert ticker.step() == STATE_WAITING
            clock.now = at(8, 30)
            assert ticker.step() == "ticked"
            clock.now = at(9)  # the default's 09:00 is no longer a check time
            assert ticker.step() == STATE_WAITING
            assert next_check() == at(12)

            # Back to the default times: the 08:30 check covers 09:00 (it started within 45 minutes of it), so 11:00 is next.
            reset = client.put("/api/settings/background", json={"sources": {"check_times": None}})
            assert reset.status_code == 200, reset.text
            assert reset.json()["effective"]["sources"]["check_times"]["source"] == "default"
            assert next_check() == at(11)
            clock.now = at(11)
            assert ticker.step() == "ticked"

            # A refused body changes nothing the thread reads.
            before = settings_path(home, target).read_bytes() if settings_path(home, target).exists() else None
            refused = client.put("/api/settings/background", json={"sources": {"check_times": {"weekdays": ["8:30"]}}})
            assert refused.status_code == 422 and refused.json()["error"]["code"] == "invalid_value", refused.text
            assert (settings_path(home, target).read_bytes() if settings_path(home, target).exists() else None) == before
            assert next_check() == at(13)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
