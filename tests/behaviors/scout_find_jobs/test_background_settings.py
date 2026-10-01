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
* the writer changes only what it is given, atomically, under concurrent writes.
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
from gigai.scout.find_jobs.refresh_tick import AUTO_REFRESH_ENV, STATE_DISABLED, RefreshTicker, auto_refresh_setting, settings_path
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
        _wait_for(lambda: queue.drains == 1)  # on by default: the first look drained the tag queue
        assert served.looks == 1
        assert auto_refresh_setting(home, target).enabled is True
        assert client.get("/api/sources/update").json()["refresh"]["enabled"] is True

        off = client.put("/api/settings/background", json={"sources": {"auto_refresh": False}})
        assert off.status_code == 200, off.text
        assert off.json()["effective"]["sources"] == {"auto_refresh": False, "source": "setting"}
        # The PUT woke the thread; its next look read the file and stood down.
        _wait_for(lambda: served.looks == 2)
        assert ticker._last_state == STATE_DISABLED
        # Every further look is silent too: no tick (it would raise), no drain.
        for look in (3, 4, 5):
            ticker.kick_tags()
            _wait_for(lambda: served.looks == look)
        time.sleep(0.05)  # the drain, if one were coming, follows the look on the same thread
        assert queue.drains == 1, "the tag queue drained with auto refresh off"
        assert ticker._last_state == STATE_DISABLED and ticker.alive
        status = client.get("/api/sources/update").json()
        assert (status["background"]["state"], status["background"]["auto_refresh"]) == ("disabled", {"enabled": False, "source": "setting", "active": True})
        assert (status["refresh"]["enabled"], status["refresh"]["state"], status["refresh"]["next_tick_at"]) == (False, "disabled", None)

        on = client.put("/api/settings/background", json={"sources": {"auto_refresh": True}})
        assert on.status_code == 200, on.text
        _wait_for(lambda: queue.drains == 2)  # woken again, and this look drained
        assert served.looks == 6
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
    assert body["settings"]["sources"] == {"auto_refresh": False}
    assert body["effective"]["sources"] == {"auto_refresh": True, "source": "environment"}


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
    }
    # A tick that is due now: 0 minutes, never negative.
    due = {**waiting, "state": "due", "next_tick_at": "2026-10-01T12:00:00.000Z"}
    assert refresh_block(due, now=now + timedelta(hours=3))["next_tick_in_minutes"] == 0

    running = {"auto_refresh": {"enabled": True}, "state": "running", "in_progress": True, "trigger": "manual", "last_update": None, "next_tick_at": None}
    assert refresh_block(running, now=now) == {
        "enabled": True, "state": "running", "in_progress": True, "trigger": "manual",
        "last_updated_at": None, "last_updated_minutes_ago": None, "next_tick_at": None, "next_tick_in_minutes": None,
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
            assert defaults.status_code == 200 and defaults.json()["settings"]["sources"] == {"auto_refresh": True}
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)
    assert not (home / "scout").exists()
