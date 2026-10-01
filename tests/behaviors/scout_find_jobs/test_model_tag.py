"""0110-024 P3: the model tag queue (``scout/find_jobs/model_tag.TagQueue``) and its ride on the refresh thread.

No live model: the adapter seam the ranker uses
(``proposal_execution.resolve_model_adapter``, looked up as a module
attribute) is patched with a scripted fake port that records every request.
Titles, locations and profiles are synthetic; time is a fake clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace
from typing import Callable

import pytest

from gigai.adapters.port import InvocationRequest, InvocationResult, ModelAuthenticationRequired, ModelInvocationError, NormalizedUsage
from gigai.canonical import canonical_json_bytes
from gigai.config import Endpoint, GigAIConfig
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.scout.find_jobs import model_tag, posting_tags
from gigai.scout.find_jobs.company_index import CompanyIndex, CompanyIndexEntry, IndexedPosting, index_stamp
from gigai.scout.find_jobs.contracts import FindJobsConfig, ModelTarget, SourceToggles
from gigai.scout.find_jobs.model_tag import (
    BACKOFF_SECONDS,
    MODEL_TAGS_ENV,
    PROMPT,
    PROMPT_VERSION,
    Demand,
    IndexTitles,
    TagAnswerError,
    TaggingSetting,
    TagQueue,
    backfill_adapter,
    load_demand,
    tagging_setting,
    validate_tag_answer,
)
from gigai.scout.find_jobs.refresh_tick import STATE_NEEDS_FIRST_UPDATE, STATE_RUNNING, RefreshTicker, settings_path
from gigai.scout.find_jobs.tag_store import SOURCE_MODEL
from gigai.scout.find_jobs.title_query import TitleMatcher, open_tag_store
from gigai.setup import build_config

from .test_sources_update import _installed, _write_running

ROLES = ("Director of Engineering",)  # rules: director + software, so the demand set is the director level
_LINE = re.compile(r"^(s\d{3}) \| (.*) \| (.*)$")
_HEAD, _TAIL = PROMPT.split("{n}):\n{lines}")


def _demand_titles(count: int) -> list[str]:
    return [f"Director of Wizardry {number:03d}" for number in range(count)]


def _backfill_titles(count: int) -> list[str]:
    return [f"Senior Alchemist {number:03d}" for number in range(count)]


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class _Port:
    """Scripted model: ``respond(prompt, call_number)`` returns the answer text or raises. Records every request."""

    def __init__(self, respond: Callable[[str, int], str] | None = None) -> None:
        self.respond = respond if respond is not None else (lambda prompt, _n: _answer(prompt))
        self.requests: list[InvocationRequest] = []

    @property
    def prompts(self) -> list[str]:
        return [request.prompt for request in self.requests]

    def invoke(self, request: InvocationRequest) -> InvocationResult:
        self.requests.append(request)
        text = self.respond(request.prompt, len(self.requests))
        return InvocationResult("success", text, "fake-model", {}, NormalizedUsage(100, 10, 110), "unavailable")


class _Binding:
    def __init__(self, port: object, target: str) -> None:
        self.port = port
        self.target = target
        self.current = SimpleNamespace(target=SimpleNamespace(model="default"))
        self.closed = 0

    def request(self, *, role: str, prompt: str, required_capabilities: frozenset[str] = frozenset({"text"})) -> InvocationRequest:
        return InvocationRequest(target_name=self.target, endpoint_name="fake", model="default", role=role, prompt=prompt, target_capabilities=frozenset({"text"}))

    def close(self) -> None:
        self.closed += 1


def _config(home: Path) -> GigAIConfig:
    return build_config(
        home_root=home, workpad_root=home.parent / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False,
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="codex", adapter="codex_cli"),
            Endpoint(name="claude", adapter="claude_cli"),
        ),
        model_targets=(
            ConfigModelTarget(name="offline-default", endpoint="offline", model="fixture-v1", capabilities=("text",), max_output_tokens=64),
            ConfigModelTarget(name="codex-default", endpoint="codex", model="default", capabilities=("text",), max_output_tokens=64),
            ConfigModelTarget(name="claude-default", endpoint="claude", model="sonnet", capabilities=("text",), max_output_tokens=64),
        ),
    )


def _install(monkeypatch: pytest.MonkeyPatch, port: _Port) -> list[str]:
    """Every configured target resolves to ``port``; returns the target names asked for, in order."""

    asked: list[str] = []

    def resolve(config, target_name, **_kwargs):
        asked.append(target_name)
        return _Binding(port, target_name)

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    return asked


def _lines(prompt: str) -> list[tuple[str, str, str]]:
    """The ``(id, title, location)`` of every posting line in one prompt."""

    block = prompt.split("\nPOSTINGS (", 1)[1].split("\n\nAnswer with ONLY", 1)[0]
    return [match.groups() for match in map(_LINE.match, block.splitlines()[1:]) if match]  # type: ignore[misc]


def _titles_in(prompt: str) -> list[str]:
    return [title for _id, title, _location in _lines(prompt)]


def _answer(prompt: str, fn: str = "op") -> str:
    return json.dumps([{"id": posting_id, "level": "D", "fn": fn} for posting_id, _title, _location in _lines(prompt)])


def _seed(home: Path, titles: list[str], *, stamp: str | None = None) -> None:
    """Rules-tag ``titles`` (as Update sources does); ``stamp`` back-dates when they were tagged."""

    store = posting_tags.default_store(home)
    try:
        posting_tags.tag_new_titles(store, titles)
        if stamp is not None:
            keys = [posting_tags.normalize_title(title) for title in titles]
            store._conn().executemany("UPDATE title_tags SET tagged_at = ? WHERE title_key = ?", [(stamp, key) for key in keys])
    finally:
        store.close()


def _rows(home: Path) -> dict[str, tuple[str | None, str | None, str | None, str | None]]:
    """``title key -> (function, function_source, model, prompt_version)`` straight from the store file."""

    store = posting_tags.default_store(home)
    try:
        return {row[0]: tuple(row[1:]) for row in store._conn().execute("SELECT title_key, function, function_source, model, prompt_version FROM title_tags")}
    finally:
        store.close()


def _model_rows(home: Path) -> set[str]:
    return {key for key, row in _rows(home).items() if row[1] == SOURCE_MODEL}


class _NoListing:
    """No company index: every title goes out as its key, with no location."""

    def lookup(self, keys, *, should_stop=lambda: False):
        return {}


def _queue(home: Path, *, backfill: bool = False, backfill_model: str = "configured", enabled: bool = True, **kwargs) -> TagQueue:
    kwargs.setdefault("demand_loader", lambda _home, _target: Demand(ROLES, "codex_cli"))
    kwargs.setdefault("titles", _NoListing())
    kwargs.setdefault("live_update", lambda: False)
    kwargs.setdefault("config", _config(home))
    kwargs.setdefault("setting", lambda: TaggingSetting(enabled, backfill, backfill_model, "setting"))
    return TagQueue(home_root=home, target=home.parent / "project", **kwargs)


# --- the prompt and its answer -----------------------------------------------------------------


def test_the_prompt_is_the_spikes_tag_v1_and_carries_only_the_lines() -> None:
    # The digest of research/posting-index-spike/scripts/models.py PROMPT: an edit here needs a new version.
    assert PROMPT_VERSION == "tag-v1"
    assert hashlib.sha256(PROMPT.encode("utf-8")).hexdigest() == "a3dc7ff83bc1a373ef720d9561e1f562abdcdf571d065fa0d0aa3a2d0cf9867c"

    line = model_tag.tag_line("s000", "Director | of\nWizardry", "Denver,  CO")
    assert line == "s000 | Director / of Wizardry | Denver, CO"  # one line, no stray column
    prompt = model_tag.render_tag_prompt([line, model_tag.tag_line("s001", "Barista", None)])
    assert _lines(prompt) == [("s000", "Director / of Wizardry", "Denver, CO"), ("s001", "Barista", "")]
    assert "POSTINGS (2):" in prompt and "{" not in prompt.split("Answer with ONLY", 1)[0]


def test_an_answer_is_validated_against_the_seventeen_families_and_other() -> None:
    assert set(model_tag.FN_CODES.values()) == {*posting_tags.FUNCTIONS, None} and len(posting_tags.FUNCTIONS) == 17
    ids = ["s000", "s001", "s002", "s003"]
    answer = validate_tag_answer(
        '```json\n[{"id":"s000","level":"D","fn":"sw"},{"id":"s001","level":"zz","fn":"OT"},{"id":"s002","fn":"wizardry"}]\n```', ids
    )
    assert answer.functions == {"s000": "software", "s001": None}  # "other" is an answer: no family
    assert answer.rejected == {"s002": "unknown fn code 'wizardry'", "s003": "left out of the answer"}

    for broken in (
        "I cannot tag these.",
        '{"id":"s000","fn":"sw"}',
        '["s000"]',
        '[{"id":"s999","fn":"sw"}]',
        '[{"id":"s000","fn":"sw"},{"id":"s000","fn":"da"}]',
        '[{"id":"s000","fn":"nope"}]',  # well formed, and not one usable item
    ):
        with pytest.raises(TagAnswerError):
            validate_tag_answer(broken, ids)


# --- order: the demand set, then the backfill ---------------------------------------------------


def test_demand_set_titles_drain_before_the_backfill_each_with_its_own_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    # The backfill titles are OLDER: "oldest first" alone would ask for them first.
    _seed(home, _backfill_titles(60), stamp="2026-09-01T00:00:00Z")
    _seed(home, _backfill_titles(120)[60:], stamp="2026-09-02T00:00:00Z")
    _seed(home, _demand_titles(60), stamp="2026-09-20T00:00:00Z")
    _seed(home, ["Director of Engineering", "Senior Software Engineer"])  # the rules placed these: never asked
    port = _Port()
    asked = _install(monkeypatch, port)
    queue = _queue(home, backfill=True, backfill_model="haiku", max_batches=4)

    result = queue.drain()

    assert (result.state, result.batches, result.tagged, result.calls) == ("drained", 4, 160, 4)
    batches = [_titles_in(prompt) for prompt in port.prompts]
    assert [len(batch) for batch in batches] == [50, 10, 50, 50]
    assert all(title.startswith("director of wizardry") for title in batches[0] + batches[1])
    assert all(title.startswith("senior alchemist") for title in batches[2] + batches[3])
    assert batches[2] + batches[3] == [title.lower() for title in _backfill_titles(100)]  # oldest first
    assert not any("engineer" in title for batch in batches for title in batch)
    # The demand set ran on the project's configured model, the backfill on Haiku through the Claude target.
    assert asked == ["codex-default", "claude-default"]
    assert [request.model for request in port.requests] == ["default", "default", "haiku", "haiku"]
    assert {request.reasoning_effort for request in port.requests} == {"low"}

    rows = _rows(home)
    assert rows["director of wizardry 000"] == ("operations", "model", "codex_cli:default", "tag-v1")
    assert rows["senior alchemist 000"] == ("operations", "model", "claude_cli:haiku", "tag-v1")
    assert rows["director of engineering"][:2] == ("software", "rules")
    assert rows["senior alchemist 100"] == (None, None, None, None)
    status = queue.status()
    assert (status["demand"]["model"], status["demand"]["batches"], status["demand"]["tagged"]) == ("codex_cli:default", 2, 60)
    assert (status["backfill"]["model"], status["backfill"]["batches"], status["backfill"]["tagged"]) == ("claude_cli:haiku", 2, 100)

    # The next drain takes the rest of the backfill, then there is nothing to ask.
    assert queue.drain().tagged == 20 and len(port.requests) == 5
    assert (queue.drain().state, len(port.requests)) == ("idle", 5)


def test_the_backfill_is_off_until_the_operator_turns_it_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _seed(home, _demand_titles(3) + _backfill_titles(5))
    port = _Port()
    _install(monkeypatch, port)

    queue = _queue(home)  # the defaults: demand set on, backfill off
    assert queue.drain().tagged == 3
    assert queue.drain().state == "idle"  # five titles still lack a function; nobody asks for them

    assert [sorted(_titles_in(prompt)) for prompt in port.prompts] == [[title.lower() for title in _demand_titles(3)]]
    assert _model_rows(home) == {title.lower() for title in _demand_titles(3)}

    # A role with no function takes no part in the tag query: no demand set at all.
    assert Demand(("Wizard",), "codex_cli").levels == () and Demand(ROLES, "codex_cli").levels == ("director",)
    quiet = _queue(home, demand_loader=lambda _home, _target: Demand(("Wizard",), "codex_cli"))
    assert (quiet.drain().state, len(port.requests)) == ("idle", 1)


def test_other_is_stored_as_asked_with_no_family_and_is_not_asked_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _seed(home, _demand_titles(2))
    port = _Port(lambda prompt, _n: _answer(prompt, fn="ot"))
    _install(monkeypatch, port)
    queue = _queue(home)

    assert queue.drain().tagged == 2 and queue.drain().state == "idle"

    assert len(port.requests) == 1
    assert _rows(home)["director of wizardry 000"] == (None, "model", "codex_cli:default", "tag-v1")
    # Search still treats such a title as untagged: the plain rule judges it.
    matcher = TitleMatcher(ROLES, open_tag_store(home))
    assert matcher.matches("Director of Wizardry 000") is False and matcher.counts.untagged_fallback == 1


# --- resumable ---------------------------------------------------------------------------------------


class _Killed(BaseException):
    """The process dying mid-call (not an ``Exception``: nothing in the queue may swallow it)."""


def test_a_drain_killed_mid_call_loses_at_most_one_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    titles = [title.lower() for title in _demand_titles(170)]
    _seed(home, _demand_titles(170))

    def dies_on_the_third(prompt: str, number: int) -> str:
        if number == 3:
            raise _Killed()
        return _answer(prompt)

    first = _Port(dies_on_the_third)
    _install(monkeypatch, first)
    with pytest.raises(_Killed):
        _queue(home, max_batches=10).drain()

    # Two batches returned and each was written on return; the third was in flight.
    assert _model_rows(home) == set(titles[:100])
    lost = _titles_in(first.prompts[2])
    assert lost == titles[100:150]

    # A new process (a new queue, nothing in memory) picks up exactly where the store says.
    second = _Port()
    _install(monkeypatch, second)
    result = _queue(home, max_batches=10).drain()

    assert (result.batches, result.tagged) == (2, 70)
    assert [_titles_in(prompt) for prompt in second.prompts] == [titles[100:150], titles[150:]]
    assert _model_rows(home) == set(titles)
    paid_twice = [title for title in titles if sum(title in _titles_in(p) for p in first.prompts + second.prompts) > 1]
    assert paid_twice == lost and len(lost) <= 50  # only the killed batch was asked for again


# --- never in the way ----------------------------------------------------------------------------------


def test_a_search_and_an_update_write_return_while_a_model_call_is_in_flight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _seed(home, _demand_titles(60) + ["Director of Engineering"])
    entered, release = threading.Event(), threading.Event()

    def held(prompt: str, _n: int) -> str:
        entered.set()
        assert release.wait(30), "the test never released the model call"
        return _answer(prompt, fn="sw")

    port = _Port(held)
    _install(monkeypatch, port)
    queue = _queue(home, max_batches=2)
    done: list[object] = []
    draining = threading.Thread(target=lambda: done.append(queue.drain()), daemon=True)
    draining.start()
    try:
        assert entered.wait(10), "the drain never reached the model"
        started = time.monotonic()
        # A search: the shared matcher reads the tag store.
        matcher = TitleMatcher(ROLES, open_tag_store(home))
        assert matcher.matches("Director of Engineering") is True
        assert matcher.matches("Director of Wizardry 000") is False  # not tagged yet: the rule alone
        # An update: its hook writes new titles to the same store.
        writer = posting_tags.default_store(home)
        try:
            assert posting_tags.tag_new_titles(writer, ["Staff Alchemist"]).tagged == 1
        finally:
            writer.close()
        # The status is read without the drain's lock.
        assert queue.status()["demand"]["batches"] == 0
        assert time.monotonic() - started < 5 and draining.is_alive() and not done
    finally:
        release.set()
    draining.join(timeout=30)

    assert done and done[0].tagged == 60  # type: ignore[attr-defined]
    # The search sees the model's tags as soon as they land.
    assert TitleMatcher(ROLES, open_tag_store(home)).matches("Director of Wizardry 000") is True


def test_the_drain_yields_to_a_live_update_and_honours_the_stop_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _seed(home, _demand_titles(120))
    port = _Port()
    _install(monkeypatch, port)
    clock = _Clock()

    # The real check: the sources update snapshot says an update is running.
    _write_running(home, updated_at=index_stamp(clock.now))
    opened: list[Path] = []
    real_open = model_tag.open_tag_store
    monkeypatch.setattr(model_tag, "open_tag_store", lambda home_root: opened.append(home_root) or real_open(home_root))
    queue = _queue(home, clock=clock, live_update=None)
    assert (queue.drain().state, port.requests) == ("yielded", [])
    assert opened == []  # the store is not even opened: the update may be creating it right now
    CompanyIndex.for_home(home).update_summary_path.unlink()

    # An update that starts mid-drain: the batch in flight lands, the next one is not started.
    live = {"now": False}

    def update_starts(prompt: str, _n: int) -> str:
        live["now"] = True
        return _answer(prompt)

    port.respond = update_starts
    during = _queue(home, live_update=lambda: live["now"]).drain()
    assert (during.state, during.batches, len(port.requests)) == ("yielded", 1, 1)

    # A stop (the server is shutting down) between batches.
    stop = threading.Event()

    def stops(prompt: str, _n: int) -> str:
        stop.set()
        return _answer(prompt)

    port.respond = stops
    stopped = _queue(home).drain(stop=stop)
    assert (stopped.state, stopped.batches, len(port.requests)) == ("stopped", 1, 2)
    assert _queue(home).drain(stop=stop).state == "stopped" and len(port.requests) == 2
    assert len(_model_rows(home)) == 100

    # The per-tick budget: one drain starts at most ``max_batches`` batches.
    _seed(home, [f"Director of Sorcery {number:03d}" for number in range(200)])
    port.respond = lambda prompt, _n: _answer(prompt)
    budgeted = _queue(home, max_batches=3).drain()
    assert (budgeted.batches, len(port.requests)) == (3, 5)
    assert _queue(home, max_batches=0).drain().state == "idle" and len(port.requests) == 5


# --- invalid answers ---------------------------------------------------------------------------------


def test_an_invalid_answer_is_retried_with_the_error_then_split_and_never_stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _seed(home, _demand_titles(8))
    clock = _Clock()

    # Retried once with the error fed back: the second answer is good.
    port = _Port(lambda prompt, number: "Sure! Here are the tags." if number == 1 else _answer(prompt))
    _install(monkeypatch, port)
    queue = _queue(home, clock=clock)
    assert queue.drain().tagged == 8
    assert len(port.requests) == 2 and "Your previous answer was rejected: not a JSON array" in port.prompts[1]
    assert _lines(port.prompts[0]) == _lines(port.prompts[1])

    # Never valid: one retry, one split into halves, nothing stored, and the lane backs off.
    _seed(home, [f"Director of Sorcery {number}" for number in range(6)])
    garbage = _Port(lambda prompt, _n: '[{"id":"s000","fn":"wizardry"}]')
    _install(monkeypatch, garbage)
    queue = _queue(home, clock=clock)
    result = queue.drain()

    assert (result.state, result.batches, result.tagged, result.rejected, result.calls) == ("backoff", 0, 0, 6, 4)
    assert [len(_lines(prompt)) for prompt in garbage.prompts] == [6, 6, 3, 3]
    assert len(_model_rows(home)) == 8 and not any("sorcery" in key for key in _model_rows(home))
    status = queue.status()["demand"]
    assert (status["failures"], status["consecutive_failures"]) == (1, 1)
    assert status["last_error"].startswith("no usable answer: invalid: no usable item for 3 ids")
    assert queue.drain().state == "backoff" and len(garbage.requests) == 4  # no call while backing off


def test_one_bad_item_is_skipped_the_rest_are_stored_and_a_title_that_keeps_failing_is_parked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _seed(home, _demand_titles(5))

    def one_bad(prompt: str, _n: int) -> str:
        items = json.loads(_answer(prompt))
        for item, (_id, title, _location) in zip(items, _lines(prompt)):
            if title == "director of wizardry 002":
                item["fn"] = "management"  # not a code
        return json.dumps(items)

    port = _Port(one_bad)
    _install(monkeypatch, port)
    queue = _queue(home)

    first = queue.drain()
    assert (first.state, first.tagged, first.rejected) == ("drained", 4, 1)
    assert _rows(home)["director of wizardry 002"] == (None, None, None, None)

    second = queue.drain()  # asked once more, alone
    assert (second.state, second.rejected) == ("backoff", 1) and _titles_in(port.prompts[-1]) == ["director of wizardry 002"]
    calls = len(port.requests)

    queue.kick(reset_backoff=True)
    assert (queue.drain().state, queue.status()["parked"], len(port.requests)) == ("idle", 1, calls)  # parked: not asked a third time
    assert _rows(home)["director of wizardry 002"] == (None, None, None, None)


# --- a model that cannot be called ---------------------------------------------------------------------


def test_a_model_failure_backs_off_and_the_status_carries_the_count_and_the_last_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _seed(home, _demand_titles(60))
    clock = _Clock()

    def down(prompt: str, _n: int) -> str:
        raise ModelInvocationError("codex exited with status 1")

    port = _Port(down)
    _install(monkeypatch, port)
    queue = _queue(home, clock=clock)

    failed = queue.drain()
    assert (failed.state, failed.batches, failed.tagged, failed.calls) == ("backoff", 0, 0, 2)  # one attempt and its retry
    status = queue.status()
    assert status["state"] == "backoff" and status["parked"] == 0  # the model failed, not the titles
    assert status["demand"]["failures"] == 1 and "codex exited with status 1" in status["demand"]["last_error"]
    assert status["demand"]["last_error_at"] == "2026-10-01T12:00:00Z" and status["demand"]["retry_after"] == "2026-10-01T12:05:00Z"

    clock.advance(seconds=BACKOFF_SECONDS - 1)
    assert queue.drain().state == "backoff" and len(port.requests) == 2  # no call before the retry time

    clock.advance(seconds=1)
    assert queue.drain().state == "backoff" and len(port.requests) == 4
    again = queue.status()["demand"]
    assert (again["failures"], again["consecutive_failures"], again["retry_after"]) == (2, 2, "2026-10-01T12:15:00Z")  # doubled

    # The model is back: the lane recovers and the count of failures stays for the status.
    port.respond = lambda prompt, _n: _answer(prompt)
    clock.advance(minutes=10)
    assert queue.drain().tagged == 60
    healed = queue.status()["demand"]
    assert (healed["failures"], healed["consecutive_failures"], healed["retry_after"]) == (2, 0, None)
    assert _model_rows(home) == {title.lower() for title in _demand_titles(60)}


def test_an_authentication_error_or_a_target_that_cannot_be_resolved_makes_no_further_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _seed(home, _demand_titles(3) + _backfill_titles(3))

    def denied(prompt: str, _n: int) -> str:
        raise ModelAuthenticationRequired("run `codex login`")

    port = _Port(denied)
    asked = _install(monkeypatch, port)
    queue = _queue(home)
    assert queue.drain().state == "backoff" and len(port.requests) == 1  # no retry against a login wall
    assert "model_authentication_required" in queue.status()["demand"]["last_error"]

    # No model target in find-jobs.json: nothing to call.
    nobody = _queue(home, demand_loader=lambda _home, _target: Demand(ROLES, None))
    assert nobody.drain().state == "backoff" and nobody.status()["demand"]["last_error"] == "no model target is configured for this project"

    # The OpenAI backfill is a provider id only: with no configured openai_api target the backfill backs off, the demand set is untouched.
    assert backfill_adapter("openai", "codex_cli") == ("openai_api", None)
    assert backfill_adapter("haiku", "codex_cli") == ("claude_cli", "haiku")
    assert backfill_adapter("configured", "codex_cli") == ("codex_cli", None)
    port.respond = lambda prompt, _n: _answer(prompt)
    openai = _queue(home, backfill=True, backfill_model="openai")
    result = openai.drain()
    assert (result.state, result.tagged) == ("drained", 3) and asked[-1] == "codex-default"
    assert "model_target_unavailable" in openai.status()["backfill"]["last_error"]
    assert not any("alchemist" in key for key in _model_rows(home))


# --- opt-out ---------------------------------------------------------------------------------------------


def test_opt_out_makes_no_model_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _installed(tmp_path)
    monkeypatch.delenv(MODEL_TAGS_ENV, raising=False)
    _seed(home, _demand_titles(3) + _backfill_titles(3))
    port = _Port()
    _install(monkeypatch, port)
    path = settings_path(home, target)
    path.parent.mkdir(parents=True, exist_ok=True)

    def queue(environ: dict[str, str] | None = None) -> TagQueue:
        return TagQueue(
            home_root=home, target=target, environ=environ if environ is not None else {}, titles=_NoListing(), live_update=lambda: False,
            config=_config(home), demand_loader=lambda _home, _target: Demand(ROLES, "codex_cli"),
        )

    # The defaults: the demand set is on, the backfill is off.
    assert tagging_setting(home, target, environ={}).to_json() == {"model_enabled": True, "backfill_enabled": False, "tag_backfill_model": "configured", "source": "default"}

    path.write_text(json.dumps({"schema_version": "scout-settings:1", "sources": {"auto_refresh": True}, "tagging": {"model_enabled": False}}), encoding="utf-8")
    assert tagging_setting(home, target, environ={}).to_json()["model_enabled"] is False
    assert (queue().drain().state, port.requests) == ("disabled", [])

    # A file that cannot be understood does not start spending model calls.
    for broken in ("{not json", json.dumps({"schema_version": "scout-settings:9"}), json.dumps({"schema_version": "scout-settings:1", "tagging": {"tag_backfill_model": "gpt"}}), json.dumps({"schema_version": "scout-settings:1", "tagging": {"model_enabled": "yes"}})):
        path.write_text(broken, encoding="utf-8")
        assert tagging_setting(home, target, environ={}).source == "settings_unreadable"
        assert (queue().drain().state, port.requests) == ("disabled", [])

    # The environment wins either way.
    path.write_text(json.dumps({"schema_version": "scout-settings:1"}), encoding="utf-8")
    assert (queue({MODEL_TAGS_ENV: "off"}).drain().state, port.requests) == ("disabled", [])

    # On, with the backfill turned on in the file: both lanes run.
    path.write_text(json.dumps({"schema_version": "scout-settings:1", "tagging": {"backfill_enabled": True, "tag_backfill_model": "haiku"}}), encoding="utf-8")
    assert tagging_setting(home, target, environ={}).to_json() == {"model_enabled": True, "backfill_enabled": True, "tag_backfill_model": "haiku", "source": "setting"}
    assert queue().drain().tagged == 6 and [request.model for request in port.requests] == ["default", "haiku"]


# --- what is sent ------------------------------------------------------------------------------------------


def _write_company(home: Path, slug: str, postings: list[tuple[str, str, str]]) -> None:
    stamp = index_stamp()
    CompanyIndex.for_home(home).write(CompanyIndexEntry(
        company=slug.title(), ats="greenhouse", slug=slug, checked_at=stamp, etag=None, body_sha256=None,
        postings={
            posting_id: IndexedPosting(posting_id=posting_id, title=title, location=location, url=f"https://boards.greenhouse.io/{slug}/jobs/{posting_id}", updated_at=None, content_sha256=None, first_seen=stamp, last_seen=stamp)
            for posting_id, title, location in postings
        },
    ))


def test_a_payload_is_the_prompt_and_only_titles_and_locations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = tmp_path / "home", tmp_path / "project"
    target.mkdir()
    # What must never be in a tag payload: profile and resume text, and the companies that listed the titles.
    secret_role = "Director of Engineering at Quillfeather Labs"
    (target / "resume.md").write_text("Zephyrine Quillfeather\nzq7731@example.test\nOperated the Glimmerfall ledger.", encoding="utf-8")
    _write_company(home, "acme", [("1", "Director, Wizardry", "Denver, CO"), ("2", "Director of Potions | EMEA", "London\nUK")])
    _write_company(home, "globex", [("7", "Director of Wizardry", "Remote - US"), ("8", "Senior Alchemist", "Austin, TX")])
    _seed(home, ["Director, Wizardry", "Director of Potions | EMEA", "Director of Wizardry", "Senior Alchemist", "Director of Vanished Roles"])
    port = _Port()
    _install(monkeypatch, port)
    queue = _queue(
        home, backfill=True, titles=IndexTitles(home),
        demand_loader=lambda _home, _target: Demand(("Director of Engineering", secret_role), "codex_cli"),
    )
    queue.target = target

    assert queue.drain().tagged == 5

    demand, backfill = port.prompts
    # The posting's own title and location, as its board listed them; a title no board lists any more goes as its key.
    assert _lines(demand) == [
        ("s000", "Director of Potions / EMEA", "London UK"),
        ("s001", "director of vanished roles", ""),
        ("s002", "Director of Wizardry", "Remote - US"),
        ("s003", "Director, Wizardry", "Denver, CO"),
    ]
    assert _lines(backfill) == [("s000", "Senior Alchemist", "Austin, TX")]
    for request, lines in zip(port.requests, ([model_tag.tag_line(*line) for line in _lines(demand)], [model_tag.tag_line(*line) for line in _lines(backfill)])):
        # The whole payload is the fixed prompt around exactly those lines.
        assert request.prompt == f"{_HEAD.format(extra='')}{len(lines)}):\n" + "\n".join(lines) + _TAIL.replace("{{", "{").replace("}}", "}")
        assert request.role == "reviewer"
        for forbidden in ("Quillfeather", "Zephyrine", "zq7731", "Glimmerfall", "Acme", "acme", "Globex", "globex", "greenhouse", "Engineering", str(home), str(target)):
            assert forbidden not in request.prompt, forbidden


def test_the_roles_and_the_model_come_from_the_project_and_reading_them_changes_nothing(tmp_path: Path) -> None:
    home, target = _installed(tmp_path)
    # A fresh install: the template's placeholder is not a role, so there is no demand set.
    assert load_demand(home, target) == Demand((), "ollama_local") and load_demand(home, target).levels == ()
    assert load_demand(home, tmp_path / "nowhere") == Demand((), None)

    config = FindJobsConfig(
        roles=("Director of Engineering", "VP Engineering"), merged_queries=("director of engineering",), location="Denver, CO", remote=True,
        published_after=None, sources=SourceToggles(exa=False, ats=True, hiringcafe=False), default_model_target=ModelTarget.CLAUDE_CLI,
    )
    (target / "find-jobs.json").write_bytes(canonical_json_bytes(config.to_json()))
    before = sorted(str(path) for path in home.rglob("*") if path.is_file())

    demand = load_demand(home, target)

    assert demand == Demand(("Director of Engineering", "VP Engineering"), "claude_cli")
    assert demand.levels == ("director", "vp")
    assert sorted(str(path) for path in home.rglob("*") if path.is_file()) == before  # read only: no profile is migrated


# --- the tag store helpers -----------------------------------------------------------------------------------


def test_titles_awaiting_a_model_are_listed_by_level_oldest_first(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _seed(home, ["Senior Alchemist", "Director of Wizardry"], stamp="2026-09-01T00:00:00Z")
    _seed(home, ["Director of Potions", "VP of Wizardry", "Director of Engineering"], stamp="2026-09-02T00:00:00Z")
    store = posting_tags.default_store(home)
    try:
        assert store.titles_awaiting_model() == ["director of wizardry", "senior alchemist", "director of potions", "vp of wizardry"]
        assert store.titles_awaiting_model(levels=["director"]) == ["director of wizardry", "director of potions"]
        assert store.titles_awaiting_model(levels=["director", "vp"], limit=2) == ["director of wizardry", "director of potions"]
        assert store.titles_awaiting_model(exclude_levels=["director"]) == ["senior alchemist", "vp of wizardry"]
        assert store.titles_awaiting_model(levels=[]) == [] and store.titles_awaiting_model(exclude_levels=[]) == store.titles_awaiting_model()
        assert (store.count_awaiting_model(), store.count_awaiting_model(levels=["director"]), store.count_awaiting_model(exclude_levels=["director"])) == (4, 2, 2)

        assert store.set_model_function("director of potions", None, model="m", prompt_version="tag-v1")  # asked: no family
        assert store.set_model_function("vp of wizardry", "operations", model="m", prompt_version="tag-v1")
        assert store.titles_awaiting_model() == ["director of wizardry", "senior alchemist"]
        # The older listing is unchanged: a title with no function is still "lacking" one.
        assert store.titles_lacking_function() == ["director of wizardry", "senior alchemist", "director of potions"]
    finally:
        store.close()


# --- on the refresh thread ------------------------------------------------------------------------------------


def test_the_queue_drains_on_the_refresh_thread_after_each_look_and_a_kick_wakes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    monkeypatch.delenv("GIGAI_SCOUT_AUTO_REFRESH", raising=False)
    _seed(home, _demand_titles(3))
    port = _Port()
    _install(monkeypatch, port)
    clock = _Clock()
    queue = _queue(home, clock=clock, live_update=None)

    def ticker(**kwargs) -> RefreshTicker:
        return RefreshTicker(home_root=home, target=tmp_path / "project", client_factory=lambda: None, clock=clock, tag_queue=queue, **kwargs)

    # While a manual update is live the look says so, and the queue yields to it: no model call.
    _write_running(home, updated_at=index_stamp(clock.now))
    assert ticker().step() == STATE_RUNNING and port.requests == [] and queue.status()["state"] == "yielded"
    CompanyIndex.for_home(home).update_summary_path.unlink()

    # The step's return value is the refresh's alone; the drain happened behind it.
    one = ticker()
    assert one.step() == STATE_NEEDS_FIRST_UPDATE
    assert len(port.requests) == 1 and one.tag_queue_status()["last_drain"]["tagged"] == 3
    assert one.tag_queue is queue and ticker(model_tags=False).tag_queue is queue
    assert RefreshTicker(home_root=home, target=tmp_path / "project", model_tags=False).tag_queue_status() is None
    assert isinstance(RefreshTicker(home_root=home, target=tmp_path / "project").tag_queue, TagQueue)  # production: a queue by default

    # The thread: an hour between looks, so only a kick can explain a second drain.
    thread = ticker(poll_seconds=3600.0)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while queue.status()["last_drain"]["state"] != "idle":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        _seed(home, ["Director of Potions"])
        thread.kick_tags()
        while len(port.requests) < 2:
            assert time.monotonic() < deadline, "the kick did not wake the thread"
            time.sleep(0.01)
    finally:
        started = time.monotonic()
        assert thread.stop(timeout=10) is True
    assert time.monotonic() - started < 5 and thread.alive is False  # the stop ends the hour-long wait
    assert _titles_in(port.prompts[1]) == ["director of potions"]

    # A stopping thread starts no drain.
    _seed(home, ["Director of Charms"])
    assert thread.step() == STATE_NEEDS_FIRST_UPDATE and len(port.requests) == 2


def test_a_drain_that_raises_never_takes_the_refresh_thread_down(tmp_path: Path) -> None:
    class _Broken:
        def drain(self, *, stop):
            raise RuntimeError("tags.sqlite is locked")

        def kick(self, *, reset_backoff=False):
            pass

        def status(self):
            return {}

    ticker = RefreshTicker(home_root=tmp_path / "home", target=tmp_path / "project", client_factory=lambda: None, tag_queue=_Broken())  # type: ignore[arg-type]
    assert ticker.step() == STATE_NEEDS_FIRST_UPDATE


# --- 0110-028: the queue does not starve behind a background check -------------------------------------------


def _live(home: Path, clock: _Clock, *, trigger: str | None) -> None:
    """A live update's snapshot: ``trigger`` ``"auto"`` is a background check, ``None`` a manual update."""

    stamp = index_stamp(clock.now)
    snapshot: dict[str, object] = {"update_id": "sources_update_live", "status": "running", "started_at": stamp, "updated_at": stamp, "finished_at": None}
    if trigger is not None:
        snapshot["trigger"] = trigger
    CompanyIndex.for_home(home).write_update_summary(snapshot)


def test_the_queue_yields_to_a_manual_update_and_not_to_a_background_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _seed(home, _demand_titles(3))
    port = _Port()
    _install(monkeypatch, port)
    clock = _Clock()
    queue = _queue(home, clock=clock, live_update=None)  # the queue's own rule, not a test's stand-in

    _live(home, clock, trigger=None)
    assert queue.drain().state == "yielded" and port.requests == []

    _live(home, clock, trigger="auto")
    drained = queue.drain()
    assert (drained.state, drained.tagged) == ("drained", 3) and len(port.requests) == 1


def test_the_demand_set_is_tagged_while_a_background_check_runs_not_after_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator's "0 by model": every look that found a check running skipped the drain, and a check ran all hour."""

    home = tmp_path / "home"
    monkeypatch.delenv("GIGAI_SCOUT_AUTO_REFRESH", raising=False)
    port = _Port()
    _install(monkeypatch, port)
    clock = _Clock()
    queue = _queue(home, clock=clock, live_update=None)
    index = CompanyIndex.for_home(home)
    index.root.mkdir(parents=True)
    (index.root / "greenhouse:acme.json").write_text("{}", encoding="utf-8")  # an indexed home ...
    old = index_stamp(clock.now - timedelta(hours=2))
    index.write_update_summary({"update_id": "sources_update_first", "status": "succeeded", "started_at": old, "updated_at": old, "finished_at": old})  # ... whose check is due
    entered, release = threading.Event(), threading.Event()

    def long_check(home_root, target_root, *, client, now, stop_event, config):
        del target_root, client, stop_event, config, now
        _live(home_root, clock, trigger="auto")  # what a check does first: it claims the snapshot
        entered.set()
        assert release.wait(timeout=30), "the test never let the check end"
        done = index_stamp(clock.now)
        CompanyIndex.for_home(home_root).write_update_summary({"update_id": "sources_update_live", "status": "succeeded", "trigger": "auto", "started_at": done, "updated_at": done, "finished_at": done})
        return SimpleNamespace(to_json=lambda: {"status": "succeeded"})

    ticker = RefreshTicker(
        home_root=home, target=tmp_path / "project", client_factory=lambda: None, clock=clock, tag_queue=queue,
        run_tick=long_check, config_loader=lambda _home, _target: None, interval_seconds=3600.0, poll_seconds=0.05,
    )
    ticker.start()
    try:
        assert entered.wait(timeout=10), "the check never started"
        # The titles arrive while the check runs (it writes them as boards settle).
        _seed(home, _demand_titles(3))
        ticker.kick_tags()
        deadline = time.monotonic() + 10
        while not port.requests:
            assert time.monotonic() < deadline, "the demand set waited for the background check to end"
            time.sleep(0.01)
        while queue.status()["last_drain"] is None or queue.status()["last_drain"]["tagged"] != 3:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert not release.is_set() and index.read_update_summary()["status"] == "running"  # the check is still running
    finally:
        release.set()
        assert ticker.stop(timeout=10) is True
    assert {function for function, _source, _model, _version in _rows(home).values()} == {"operations"}


def test_every_drain_that_calls_a_model_is_logged_and_an_idle_one_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    home = tmp_path / "home"
    monkeypatch.delenv("GIGAI_SCOUT_AUTO_REFRESH", raising=False)
    _seed(home, _demand_titles(3))
    port = _Port()
    _install(monkeypatch, port)
    queue = _queue(home, clock=_Clock())
    ticker = RefreshTicker(home_root=home, target=tmp_path / "project", client_factory=lambda: None, tag_queue=queue, logger=logging.getLogger("test.model_tags"))

    with caplog.at_level(logging.INFO, logger="test.model_tags"):
        for _ in range(3):
            ticker.step()

    lines = [record.getMessage() for record in caplog.records if record.getMessage().startswith("model tags:")]
    assert lines == ["model tags: drained batches=1 tagged=3 rejected=0 calls=1 models={'demand': 'codex_cli:default'}", "model tags: idle"]

    # Off is said once too, with the reason.
    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    with caplog.at_level(logging.INFO, logger="test.model_tags"):
        ticker.step()
        ticker.step()
    assert [record.getMessage() for record in caplog.records if "paused" in record.getMessage()] == ["model tags: paused (sources.auto_refresh is off)"]
