"""uat-bug-042: "Assess all new" and the run's "All new postings" cap.

Pinned here, with fakes only (no model, no network):

* the cap's JSON: ``"all"`` is accepted everywhere a cap is (config, run
  request, sealed run input, acquire/assess input and output), a numeric
  config serializes and DIGESTS exactly as before (the digest below was
  computed on the code before this change), and anything else is refused;
* the selection "all" makes: no per-company cap, duplicates still dropped,
  at most ``ASSESS_ALL_CEILING``;
* the queue: new/edited rows the run did not assess (nor carry forward), not
  in the store, not left out on purpose, in the order given;
* the estimate: a minute figure only from measured seconds;
* the job: K calls at a time (a slow fake proves the bound), a posting
  already in the store is skipped, a cancel mid-way keeps what finished and
  starts nothing new, a start after the cancel does not redo what finished,
  a second start joins, a fatal code stops the job, a record whose owner is
  gone reads ``interrupted``;
* the assess node with a cap of "all": K calls at a time, every posting
  assessed, the sealed order is the selection order; a numeric cap still
  assesses one at a time.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from gigai.scout.find_jobs import assess_all
from gigai.scout.find_jobs.contracts import (
    ASSESS_ALL,
    ASSESS_ALL_CEILING,
    AcquireInput,
    FindJobsConfig,
    FindJobsContractError,
    RunRequest,
    is_assess_all,
    selection_cap_limit,
)
from gigai.scout.find_jobs.selection import DEFAULT_PER_COMPANY_CAP, select_for_assessment, selection_limits
from tests.behaviors.scout_find_jobs.conftest import load_fixture

# FindJobsConfig.digest() of _CONFIG on the code BEFORE uat-bug-042 (HEAD f5a19aa).
_NUMERIC_DIGEST_BEFORE = "sha256:77c58108bec6cc112c13cfbfde68fbeb4e2fd68f213b591dad772dd29f9eb553"
_CONFIG = {
    "schema_version": "find-jobs-config:1",
    "roles": ["Software Engineer"],
    "merged_queries": ["software engineer"],
    "location": "Denver, CO",
    "remote": True,
    "published_after": None,
    "sources": {"exa": False, "ats": True, "hiringcafe": False},
    "default_assess_cap": 10,
    "default_model_target": "codex_cli",
}


# --- the cap's JSON --------------------------------------------------------------------


def test_an_old_numeric_config_loads_unchanged_and_digests_as_before() -> None:
    config = FindJobsConfig.from_json(dict(_CONFIG))
    assert config.default_assess_cap == 10
    assert config.to_json() == _CONFIG
    assert config.digest() == _NUMERIC_DIGEST_BEFORE
    for fixture in ("fixture-find-jobs-config-v1.json", "fixture-find-jobs-config-v1-sponsorship.json"):
        raw = load_fixture(fixture)
        assert FindJobsConfig.from_json(raw).to_json() == raw


def test_all_new_postings_saves_and_reloads_in_the_config() -> None:
    saved = FindJobsConfig.from_json({**_CONFIG, "default_assess_cap": ASSESS_ALL})
    assert saved.default_assess_cap == "all" and is_assess_all(saved.default_assess_cap)
    written = json.loads(json.dumps(saved.to_json()))
    assert written["default_assess_cap"] == "all"
    reloaded = FindJobsConfig.from_json(written)
    assert reloaded == saved and reloaded.digest() == saved.digest() != _NUMERIC_DIGEST_BEFORE


@pytest.mark.parametrize("bad", ["ALL", "All", "every", "", 0, 51, True, 10.0, None])
def test_a_cap_is_a_number_1_to_50_or_all_and_nothing_else(bad: object) -> None:
    with pytest.raises(FindJobsContractError):
        FindJobsConfig.from_json({**_CONFIG, "default_assess_cap": bad})


def test_every_sealed_contract_takes_all_and_keeps_its_numbers() -> None:
    request = load_fixture("fixture-api-run-request-v1.json")
    assert RunRequest.from_json(request).to_json()["selection_cap"] == request["selection_cap"]
    as_all = RunRequest.from_json({**request, "selection_cap": "all"})
    assert as_all.selection_cap == "all" and as_all.to_json()["selection_cap"] == "all"
    with pytest.raises(FindJobsContractError):
        RunRequest.from_json({**request, "selection_cap": 51})

    acquire = load_fixture("fixture-acquire-input-v1.json")
    assert AcquireInput.from_json(acquire).to_json() == acquire
    assert AcquireInput.from_json({**acquire, "selection_cap": "all"}).selection_cap == "all"


def test_the_limit_of_all_is_the_ceiling() -> None:
    from gigai.scout.find_jobs.market_acquisition import IMPORT_ROW_CAP

    assert selection_cap_limit("all") == ASSESS_ALL_CEILING == IMPORT_ROW_CAP == 500, "all = every imported posting"
    assert selection_cap_limit(7) == 7
    assert selection_limits("all") == (ASSESS_ALL_CEILING, None)
    assert selection_limits(10) == (10, DEFAULT_PER_COMPANY_CAP)


# --- the selection "all" makes -----------------------------------------------------------


@dataclass(frozen=True)
class _Row:
    normalized_url: str
    company: str
    title: str
    location: str = "Denver, CO"
    published_at: str | None = "2026-09-20T00:00:00Z"


def test_all_has_no_per_company_cap_but_still_drops_duplicates() -> None:
    rows = [_Row(f"u{index}", "Acme", f"Engineer {index}") for index in range(6)]
    rows.append(_Row("dup", "Acme", "Engineer 0", published_at="2026-09-01T00:00:00Z"))
    cap, per_company = selection_limits("all")
    chosen = select_for_assessment(rows, cap=cap, per_company=per_company)
    assert [row.normalized_url for row in chosen.selected] == [f"u{index}" for index in range(6)]
    assert chosen.dropped == {"dup": "duplicate"}
    numeric = select_for_assessment(rows, cap=10)
    assert len(numeric.selected) == DEFAULT_PER_COMPANY_CAP, "a number keeps today's per-company cap"


# --- the queue ---------------------------------------------------------------------------


def _result(url: str, outcome: str = "new", *, title: str | None = None, location: str = "Denver, CO") -> SimpleNamespace:
    title = title if title is not None else f"Engineer {url}"
    posting = SimpleNamespace(normalized_url=url, url=url, title=title, company="Acme", location=location)
    return SimpleNamespace(posting=posting, outcome=SimpleNamespace(value=outcome))


def test_the_queue_is_every_new_posting_not_assessed_in_the_order_given() -> None:
    rows = [
        _result("run-assessed"),
        _result("best"),
        _result("carried"),
        _result("in-store"),
        _result("unchanged", "unchanged"),
        _result("edited", "edited"),
        _result("abroad"),
        _result("duplicate"),
        _result("failed-in-run"),
        _result("", "new"),
        _result("worst"),
    ]
    queue = assess_all.build_queue(
        rows,
        run_assessed={"run-assessed", "carried"},
        not_assessed_reasons={"abroad": "location_mismatch", "duplicate": "duplicate", "failed-in-run": "model_unavailable", "best": "over_cap"},
        already_assessed={"in-store"},
    )
    assert [item.normalized_url for item in queue] == ["best", "edited", "failed-in-run", "worst"]
    assert queue[0] == assess_all.QueueItem("best", "best", "Engineer best", "Acme")


def test_the_queue_leaves_out_near_copies_as_the_run_does() -> None:
    rows = [
        _result("kept", title="Software Engineer"),
        _result("copy-of-kept", title="software engineer!"),
        _result("first", title="Data Engineer"),
        _result("copy-of-first", title="Data  Engineer"),
        _result("elsewhere", title="Data Engineer", location="Berlin, Germany"),
    ]
    queue = assess_all.build_queue(rows, run_assessed={"kept"}, not_assessed_reasons={}, already_assessed=())
    assert [item.normalized_url for item in queue] == ["first", "elsewhere"]


# --- the estimate --------------------------------------------------------------------------


def test_a_minute_figure_only_from_measured_seconds() -> None:
    measured = assess_all.plan(count=406, model_target="codex_cli", concurrency=4, run_seconds=[40.0, 44.0, 42.0])
    assert measured == {
        "count": 406,
        "model_target": "codex_cli",
        "concurrency": 4,
        "per_call_seconds": 42.0,
        "per_call_source": "run",
        "per_call_samples": 3,
        "estimate_minutes": 72,  # ceil(406 / 4) = 102 waves x 42 s = 71.4 min
    }
    earlier = assess_all.plan(count=10, model_target="codex_cli", concurrency=4, job_seconds=[30.0], run_seconds=[99.0])
    assert earlier["per_call_source"] == "assess_all" and earlier["per_call_seconds"] == 30.0 and earlier["estimate_minutes"] == 2
    unknown = assess_all.plan(count=406, model_target="claude_cli", concurrency=4)
    assert unknown["per_call_seconds"] is None and unknown["estimate_minutes"] is None and unknown["per_call_source"] is None
    assert assess_all.estimate_minutes(0, 4, 42.0) == 0 and assess_all.estimate_minutes(1, 4, 1.0) == 1


def test_the_run_measures_its_own_calls(tmp_path: Path) -> None:
    progress = tmp_path / "progress"
    progress.mkdir()
    lines = [
        {"event": "started", "normalized_url": "a", "at": "2026-09-29T10:00:00Z"},
        {"event": "finished", "normalized_url": "a", "ok": True, "at": "2026-09-29T10:00:40Z"},
        {"event": "started", "normalized_url": "b", "at": "2026-09-29T10:00:40Z"},
        {"event": "finished", "normalized_url": "b", "ok": False, "at": "2026-09-29T10:00:41Z"},
        {"event": "started", "normalized_url": "c", "at": "2026-09-29T10:00:41Z"},
        {"event": "finished", "normalized_url": "c", "ok": True, "at": "2026-09-29T10:01:25Z"},
    ]
    (progress / "assess.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    assert assess_all.run_call_seconds(tmp_path) == [40.0, 44.0], "a failed call is not a measured one"


def test_k_is_four_and_never_more_than_the_rankers_k() -> None:
    assert assess_all.ASSESS_CONCURRENCY == 4
    assert assess_all.assess_concurrency(cpus=4) == 4 and assess_all.assess_concurrency(cpus=64) == 4


# --- the job ---------------------------------------------------------------------------------


class _SlowAssess:
    """A fake ``assess_one``: each call holds ``hold`` seconds; records concurrency and calls."""

    def __init__(self, *, hold: float = 0.05, gate: threading.Event | None = None, fail: dict[str, str] | None = None) -> None:
        self.hold = hold
        self.gate = gate
        self.fail = fail or {}
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.calls: list[str] = []
        self.started = threading.Semaphore(0)

    def __call__(self, item: assess_all.QueueItem) -> str | None:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls.append(item.normalized_url)
        self.started.release()
        try:
            if self.gate is not None:
                assert self.gate.wait(10), "the test never opened the gate"
            else:
                time.sleep(self.hold)
            if item.normalized_url in self.fail:
                raise assess_all.AssessOneError(self.fail[item.normalized_url])
            return "matched_above_threshold"
        finally:
            with self.lock:
                self.active -= 1


@pytest.fixture
def records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "assess_all"
    monkeypatch.setattr(assess_all, "records_dir", lambda _home, _target: root)
    yield root
    assert assess_all.wait_for_jobs(timeout=10), "a job was left running"


def _queue(count: int) -> tuple[assess_all.QueueItem, ...]:
    return tuple(assess_all.QueueItem(f"https://jobs.test/{index}", f"https://jobs.test/{index}") for index in range(count))


def _job(tmp_path: Path, queue, fake, *, concurrency: int = 3, is_assessed=lambda _item: False) -> assess_all.JobInput:
    return assess_all.JobInput(
        home_root=tmp_path / "home",
        target=tmp_path / "target",
        run_id="run_1",
        profile_id="profile_1",
        model_target="codex_cli",
        queue=tuple(queue),
        assess_one=fake,
        concurrency=concurrency,
        is_assessed=is_assessed,
    )


def _finish(record: assess_all.AssessAllRecord) -> dict:
    assert assess_all.wait_for_jobs(timeout=10)
    reread = next(item for item in assess_all.list_records(Path("h"), Path("t")) if item.record_id == record.record_id)
    return assess_all.summary(reread)


def test_the_job_runs_k_at_a_time_and_skips_what_is_already_assessed(tmp_path: Path, records: Path) -> None:
    fake = _SlowAssess(hold=0.05)
    queue = _queue(10)
    stored = {queue[2].normalized_url, queue[7].normalized_url}
    outcome = assess_all.start_or_join(_job(tmp_path, queue, fake, is_assessed=lambda item: item.normalized_url in stored))
    assert outcome.action == "started" and outcome.record.record_id.startswith("aa_")
    done = _finish(outcome.record)
    assert fake.peak == 3, f"K=3 means three calls at once, saw {fake.peak}"
    assert sorted(fake.calls) == sorted(item.normalized_url for item in queue if item.normalized_url not in stored)
    assert fake.calls[:3] == [item.normalized_url for item in queue[:2]] + [queue[3].normalized_url], "rank order"
    assert done["status"] == "complete" and done["total"] == 10 and done["assessed"] == 8 and done["skipped"] == 2
    assert done["remaining"] == 0 and done["text"] == "8 of 10 assessed"
    details = json.loads((outcome.record.root / "record.json").read_text(encoding="utf-8"))
    assert details["kind"] == "assess_all" and details["status"] == "complete" and details["concurrency"] == 3
    assert [item["normalized_url"] for item in details["queue"]] == [item.normalized_url for item in queue]
    seconds = assess_all.finished_call_seconds(assess_all.list_records(Path("h"), Path("t")))
    assert len(seconds) == 8 and all(value > 0 for value in seconds), "each call is timed for the next estimate"


def test_cancel_mid_way_keeps_what_finished_and_a_new_start_does_not_redo_it(tmp_path: Path, records: Path) -> None:
    gate = threading.Event()
    fake = _SlowAssess(gate=gate)
    queue = _queue(9)
    outcome = assess_all.start_or_join(_job(tmp_path, queue, fake))
    for _ in range(3):
        assert fake.started.acquire(timeout=10)
    assert assess_all.summary(outcome.record)["in_flight"] == 3

    joined = assess_all.start_or_join(_job(tmp_path, queue, fake))
    assert joined.action == "joined" and joined.record.record_id == outcome.record.record_id

    assert assess_all.cancel(Path("h"), Path("t"), outcome.record.record_id)
    gate.set()  # the three in flight finish after the cancel
    done = _finish(outcome.record)
    assert done["status"] == "cancelled" and done["assessed"] == 3 and done["remaining"] == 6
    assert done["text"] == "3 of 9 assessed"
    assert len(fake.calls) == 3, "no call starts after a cancel"

    # Resume = a new start whose queue skips what is in the store now.
    finished = set(fake.calls)
    again = _SlowAssess(hold=0.01)
    rest = [item for item in queue if item.normalized_url not in finished]
    second = assess_all.start_or_join(_job(tmp_path, rest, again, is_assessed=lambda item: item.normalized_url in finished))
    assert second.action == "started" and second.record.record_id != outcome.record.record_id
    done_again = _finish(second.record)
    assert done_again["status"] == "complete" and done_again["assessed"] == 6
    assert set(again.calls) == {item.normalized_url for item in rest} and not (set(again.calls) & finished)


def test_nothing_to_queue_starts_nothing(tmp_path: Path, records: Path) -> None:
    outcome = assess_all.start_or_join(_job(tmp_path, (), _SlowAssess()))
    assert outcome == assess_all.StartOutcome(None, "none")
    assert not records.exists() or not list(records.iterdir())


def test_a_failed_posting_is_counted_and_a_fatal_code_stops_the_job(tmp_path: Path, records: Path) -> None:
    queue = _queue(6)
    fake = _SlowAssess(hold=0.01, fail={queue[1].normalized_url: "model_output_invalid"})
    done = _finish(assess_all.start_or_join(_job(tmp_path, queue, fake, concurrency=1)).record)
    assert done["status"] == "complete" and done["assessed"] == 5 and done["failed"] == 1
    assert done["text"] == "5 of 6 assessed, 1 failed"

    fatal = _SlowAssess(hold=0.01, fail={queue[0].normalized_url: "model_target_unavailable"})
    stopped = _finish(assess_all.start_or_join(_job(tmp_path, queue, fatal, concurrency=1)).record)
    assert stopped["status"] == "failed" and stopped["reason"] == "model_target_unavailable"
    assert fatal.calls == [queue[0].normalized_url], "no further call once the target is unavailable"


def test_a_running_record_whose_owner_is_gone_is_interrupted(records: Path) -> None:
    root = records / "aa_gone"
    root.mkdir(parents=True)
    (root / "record.json").write_text(
        json.dumps({"kind": "assess_all", "record_id": "aa_gone", "run_id": "run_1", "profile_id": "p", "status": "running", "queue": [], "started_at": "t"}),
        encoding="utf-8",
    )
    (root / "owner.json").write_text(json.dumps({"pid": 2**22 + 12345, "token": "x"}), encoding="utf-8")
    record = assess_all.list_records(Path("h"), Path("t"), run_id="run_1")[0]
    assert record.live_status() == "interrupted" and assess_all.summary(record)["status"] == "interrupted"
    assert assess_all.running_record(Path("h"), Path("t"), "run_1", "p") is None


# --- the assess node with a cap of "all" ---------------------------------------------------------


class _ConcurrentPort:
    """A thread-safe fake model port: every call holds a moment, peak concurrency recorded."""

    def __init__(self, answer: str, hold: float = 0.05) -> None:
        self.answer = answer
        self.hold = hold
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.prompts: list[str] = []

    def invoke(self, request):
        from gigai.adapters.port import InvocationResult, NormalizedUsage

        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.prompts.append(request.prompt)
        try:
            time.sleep(self.hold)
        finally:
            with self.lock:
                self.active -= 1
        return InvocationResult(
            status="success",
            output_text=self.answer,
            resolved_model="fixture",
            raw_usage={},
            normalized_usage=NormalizedUsage(1, 1, 2),
            cost_status="unavailable",
        )


_GOOD = json.dumps(
    {
        "matrix": [{"requirement": "5+ years Python", "resume_evidence": ["Built Python services for 6 years"], "status": "met"}],
        "suggestions": [],
        "questions": [],
    }
)


def _run_node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, cap, count: int, run_number: int):
    from tests.behaviors.scout_find_jobs import test_assess_model_policy as policy

    port = _ConcurrentPort(_GOOD)

    class _Binding:
        def __init__(self, _outputs) -> None:
            self.port = port

        def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
            return SimpleNamespace(prompt=prompt, role=role)

    monkeypatch.setattr(policy, "_ScriptedBinding", _Binding)
    monkeypatch.setattr(assess_all, "ASSESS_CONCURRENCY", 3)
    fixture, target = policy._assess_fixture(tmp_path)
    postings = [
        policy._posting(normalized_url=f"https://boards.greenhouse.io/acme/jobs/{500 + index}", text=f"We need Python. Posting {index}.", title=f"Engineer {index}")
        for index in range(count)
    ]
    output, _binding = policy._run_assess(
        fixture,
        target,
        run_id=f"run_00000000-0000-4000-8000-00000000{run_number:04d}",
        postings=postings,
        outputs=[],
        monkeypatch=monkeypatch,
        selection_cap=cap,
    )
    return output, port, postings


def test_the_assess_node_with_all_runs_k_at_a_time_and_seals_in_selection_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, port, postings = _run_node(tmp_path, monkeypatch, cap="all", count=8, run_number=4201)
    assert port.peak == 3, f"K=3 means three model calls at once, saw {port.peak}"
    assert len(port.prompts) == 8 and not output.not_assessed
    assert output.selection_cap == "all"
    assert [item.posting.normalized_url for item in output.assessments] == [posting.normalized_url for posting in postings]
    assert list(output.proposal_revision_refs) == [item.proposal_revision_ref for item in output.assessments]
    (lines,) = tmp_path.glob("**/runs/run_00000000-0000-4000-8000-000000004201/progress/assess.jsonl")
    events = [json.loads(line) for line in lines.read_text(encoding="utf-8").splitlines()]
    assert sum(1 for event in events if event["event"] == "started") == 8
    assert sum(1 for event in events if event["event"] == "finished" and event["ok"]) == 8


def test_a_numeric_cap_still_assesses_one_at_a_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output, port, _postings = _run_node(tmp_path, monkeypatch, cap=10, count=4, run_number=4202)
    assert port.peak == 1 and len(output.assessments) == 4 and output.selection_cap == 10
