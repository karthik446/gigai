"""SCOPE-ADD-3 B1: one model-ranking pass (``scout/find_jobs/model_rank.rank_postings``).

No live model: the adapter seam the assess node and quick assess share
(``proposal_execution.resolve_model_adapter``, looked up as a module
attribute) is patched with a scripted fake transport. The resume is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace
from typing import Callable

import pytest

from gigai.adapters.claude_cli import ClaudeCLIAdapter
from gigai.adapters.port import (
    InvocationRequest,
    InvocationResult,
    ModelAuthenticationRequired,
    ModelInvocationError,
    NormalizedUsage,
)
from gigai.config import Endpoint, GigAIConfig
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.scout.find_jobs import model_rank
from gigai.scout.find_jobs.contracts import ATSProvider, ModelTarget, PostingRow, SourceKind
from gigai.scout.find_jobs.model_rank import (
    BatchResult,
    CandidatePrefs,
    RankResult,
    cache_dir,
    rank_postings,
    ranked_order,
)
from gigai.setup import build_config

_RESUME = """Sam Example
Staff Software Engineer

Backend engineer with 9 years building payment platforms in Python and Go on AWS with Kubernetes and Postgres.
"""
_PREFS = CandidatePrefs(titles=("Staff Software Engineer",), countries=("US",), visa_sponsorship_required=True)
_LINE = re.compile(r"^(p\d+) \| (.*)$")


def _rows(count: int, *, start: int = 0) -> list[PostingRow]:
    rows = []
    for index in range(start, start + count):
        text = f"Requirements\n- {index % 9 + 2}+ years of Python and AWS building distributed systems at scale here.\n"
        if index % 5 == 0:
            text += "We are unable to sponsor visas for this role.\n"
        rows.append(PostingRow(
            url=f"https://boards.example/{index}", normalized_url=f"https://boards.example/{index}",
            provider=ATSProvider.GREENHOUSE, board_token="acme", company=f"co{index}", title="Staff Software Engineer",
            location="Remote - US", published_at=None, content_sha256=f"sha256:{index:064d}", source_kind=SourceKind.ATS,
            query_key="q", text=text,
        ))
    return rows


def _batch_lines(prompt: str) -> list[tuple[str, str]]:
    block = prompt.split("\nPOSTINGS (", 1)[1].split("\n\nAnswer with ONLY", 1)[0]
    return [m.groups() for m in map(_LINE.match, block.splitlines()[1:]) if m]  # type: ignore[union-attr]


def _good_answer(prompt: str) -> str:
    items = []
    for pid, rest in _batch_lines(prompt):
        number = int(pid[1:])
        blocked = "flags=no_sponsor" in rest
        items.append({"posting_id": pid, "score": 15 if blocked else 90 - number % 50, "reasons": ["fits"],
                      "blockers": ["no_sponsor"] if blocked else []})
    return json.dumps(items)


class _Port:
    """Scripted transport: ``respond(prompt, call_number)`` returns text or raises."""

    def __init__(self, respond: Callable[[str, int], str], *, delay: float = 0.0) -> None:
        self.respond = respond
        self.delay = delay
        self.prompts: list[str] = []
        self.efforts: list[str | None] = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def invoke(self, request: InvocationRequest) -> InvocationResult:
        with self._lock:
            self.prompts.append(request.prompt)
            self.efforts.append(request.reasoning_effort)
            number = len(self.prompts)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            text = self.respond(request.prompt, number)
        finally:
            with self._lock:
                self.active -= 1
        return InvocationResult("success", text, "gpt-test", {"cached_input_tokens": 7},
                                NormalizedUsage(100, 10, 110), "unavailable")


class _Binding:
    def __init__(self, port: object, model: str = "default") -> None:
        self.port = port
        self.current = SimpleNamespace(target=SimpleNamespace(model=model))
        self.closed = False

    def request(self, *, role: str, prompt: str, required_capabilities: frozenset[str] = frozenset({"text"})) -> InvocationRequest:
        return InvocationRequest(target_name="codex-default", endpoint_name="codex", model="default", role=role,
                                 prompt=prompt, target_capabilities=frozenset({"text"}))

    def close(self) -> None:
        self.closed = True


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


def _install(monkeypatch: pytest.MonkeyPatch, binding: object) -> list[str]:
    asked: list[str] = []

    def resolve(config, target_name, **_kwargs):
        asked.append(target_name)
        return binding

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    return asked


def _rank(tmp_path: Path, rows: list[PostingRow], **kwargs) -> RankResult:
    kwargs.setdefault("model_target", ModelTarget.CODEX_CLI)
    return rank_postings(rows, resume_text=_RESUME, prefs=_PREFS, home_root=tmp_path / "home",
                         config=_config(tmp_path / "home"), **kwargs)


# --- the happy path ------------------------------------------------------------------------


def test_every_row_scored_through_the_assess_adapter_seam_at_low_effort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    port = _Port(lambda prompt, _n: _good_answer(prompt))
    binding = _Binding(port)
    asked = _install(monkeypatch, binding)

    result = _rank(tmp_path, _rows(120), batch_size=50)

    assert asked == ["codex-default"]
    assert binding.closed
    assert result.status == "complete" and result.fail_open_reason is None
    assert [item.posting_id for item in result.postings] == [f"p{i}" for i in range(120)]
    assert all(item.scored for item in result.postings)
    assert sorted(batch.batch_id for batch in result.batches) == ["b000", "b001", "b002"]
    assert [len(batch.posting_ids) for batch in sorted(result.batches, key=lambda b: b.batch_id)] == [50, 50, 20]
    assert set(port.efforts) == {"low"}
    assert result.effort == "low"
    assert (result.model_target, result.configured_target, result.model, result.resolved_model) == (
        "codex_cli", "codex-default", "default", "gpt-test")
    assert result.totals()["calls"] == 3 and result.totals()["input_tokens"] == 300
    assert result.totals()["cached_input_tokens"] == 21
    # the prompt carries digests, never the resume text
    assert "Sam Example" not in port.prompts[0] and "skills: Python, Go, AWS" in port.prompts[0]


def test_blocked_postings_are_demoted_not_dropped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _Binding(_Port(lambda prompt, _n: _good_answer(prompt))))

    result = _rank(tmp_path, _rows(10))

    blocked = [item for item in result.postings if item.demoted]
    assert [item.posting_id for item in blocked] == ["p0", "p5"]
    assert all(item.score == 15 and item.blockers == ("no_sponsor",) for item in blocked)
    order = [item.posting_id for item in ranked_order(result)]
    assert len(order) == 10 and order[-2:] == ["p0", "p5"]


# --- validation, retry, split, unscored ----------------------------------------------------


def test_invalid_answer_is_retried_once_with_the_error_fed_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    port = _Port(lambda prompt, n: "[]" if n == 1 else _good_answer(prompt))
    _install(monkeypatch, _Binding(port))

    result = _rank(tmp_path, _rows(4))

    assert result.status == "complete"
    assert len(port.prompts) == 2
    assert "Your previous answer was rejected: 4 of 4 posting_ids missing" in port.prompts[1]
    (batch,) = result.batches
    assert batch.valid and batch.attempts == 2 and batch.usage.input_tokens == 200


def test_twice_invalid_batch_is_split_once_into_halves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(prompt: str, _n: int) -> str:
        return "not json" if len(_batch_lines(prompt)) == 6 else _good_answer(prompt)

    port = _Port(respond)
    _install(monkeypatch, _Binding(port))

    result = _rank(tmp_path, _rows(6))

    assert result.status == "complete"
    assert [len(_batch_lines(p)) for p in port.prompts] == [6, 6, 3, 3]
    parent, first, second = result.batches
    assert parent.split and not parent.valid and parent.postings == () and parent.attempts == 2
    assert (first.batch_id, second.batch_id) == ("b000a", "b000b")
    assert first.split_from == second.split_from == "b000"
    assert [item.batch_id for item in result.postings] == ["b000a"] * 3 + ["b000b"] * 3
    assert result.totals()["calls"] == 4


def test_a_half_that_fails_again_leaves_its_rows_unscored_with_the_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(prompt: str, _n: int) -> str:
        ids = [pid for pid, _ in _batch_lines(prompt)]
        return _good_answer(prompt) if ids == ["p0", "p1"] else json.dumps([{"posting_id": ids[0], "score": 500, "reasons": [], "blockers": []}])

    port = _Port(respond)
    _install(monkeypatch, _Binding(port))

    result = _rank(tmp_path, _rows(4))

    assert len(port.prompts) == 4  # attempt + retry + one per half, never more
    assert [item.scored for item in result.postings] == [True, True, False, False]
    assert result.postings[2].unscored_reason == "invalid: p2: score must be an integer 0-100"
    assert result.status == "partial"
    assert result.fail_open_reason == "2 of 4 postings unscored"
    assert result.totals()["unscored"] == 2


def test_transport_failure_is_an_attempt_and_the_pass_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(prompt: str, _n: int) -> str:
        raise ModelInvocationError("CLI model invocation timed out")

    _install(monkeypatch, _Binding(_Port(respond)))

    result = _rank(tmp_path, _rows(2))

    assert result.status == "partial"
    assert all(not item.scored for item in result.postings)
    assert result.postings[0].unscored_reason.startswith("invalid: model call failed (model_invocation_failed)")


def test_authentication_error_stops_the_pass_instead_of_spending_the_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(prompt: str, _n: int) -> str:
        raise ModelAuthenticationRequired("authentication_required: not logged in")

    port = _Port(respond)
    _install(monkeypatch, _Binding(port))

    result = _rank(tmp_path, _rows(10), batch_size=2, concurrency=1)

    assert len(port.prompts) == 1
    assert result.status == "partial"
    assert result.fail_open_reason == "model_unavailable: model_authentication_required: 10 of 10 postings unscored"
    assert {item.unscored_reason for item in result.postings} == {"model_unavailable: model_authentication_required"}


def test_unresolvable_model_target_skips_the_pass(tmp_path: Path) -> None:
    result = _rank(tmp_path, _rows(3), model_target=ModelTarget.OPENROUTER_API)

    assert result.status == "skipped"
    assert result.fail_open_reason.startswith("model_target_unavailable: ")
    assert result.batches == () and all(not item.scored for item in result.postings)
    assert result.configured_target is None


# --- budgets -------------------------------------------------------------------------------


def test_call_budget_bounds_the_pass_and_leaves_the_rest_unscored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    port = _Port(lambda prompt, _n: _good_answer(prompt))
    _install(monkeypatch, _Binding(port))

    result = _rank(tmp_path, _rows(10), batch_size=2, concurrency=1, max_calls=3)

    assert len(port.prompts) == 3
    assert sum(item.scored for item in result.postings) == 6
    assert {item.unscored_reason for item in result.postings if not item.scored} == {"call_budget"}
    assert result.fail_open_reason == "call_budget: 4 of 10 postings unscored"


def test_token_budget_stops_new_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    port = _Port(lambda prompt, _n: _good_answer(prompt))
    _install(monkeypatch, _Binding(port))

    result = _rank(tmp_path, _rows(10), batch_size=2, concurrency=1, max_tokens=200)

    assert len(port.prompts) == 2  # 110 tokens per call: the third call sees 220 >= 200
    assert result.fail_open_reason == "token_budget: 6 of 10 postings unscored"


def test_default_call_cap_allows_about_thirty_batches() -> None:
    assert model_rank.DEFAULT_BATCH_SIZE == 50
    assert model_rank.DEFAULT_CONCURRENCY == 8 and model_rank.SMALL_MACHINE_CONCURRENCY == 4
    assert model_rank.DEFAULT_MAX_CALLS >= 30


# --- concurrency, streaming, cancel --------------------------------------------------------


def test_concurrency_is_bounded_by_k(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    port = _Port(lambda prompt, _n: _good_answer(prompt), delay=0.05)
    _install(monkeypatch, _Binding(port))

    result = _rank(tmp_path, _rows(24), batch_size=2, concurrency=3)

    assert result.status == "complete" and len(port.prompts) == 12
    assert port.max_active == 3


def test_concurrency_actually_overlaps_up_to_k(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    port = _Port(lambda prompt, _n: _good_answer(prompt), delay=0.05)
    _install(monkeypatch, _Binding(port))

    _rank(tmp_path, _rows(16), batch_size=2, concurrency=8)

    assert port.max_active == 8


def test_on_batch_runs_on_the_caller_thread_as_batches_land(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    release = {"b000": threading.Event(), "b001": threading.Event()}

    def respond(prompt: str, _n: int) -> str:
        first = _batch_lines(prompt)[0][0]
        if first == "p0":  # b000 waits until b001 has landed
            assert release["b001"].wait(5)
        return _good_answer(prompt)

    _install(monkeypatch, _Binding(_Port(respond)))
    seen: list[tuple[str, int, str]] = []

    def on_batch(batch: BatchResult) -> None:
        seen.append((batch.batch_id, len(batch.postings), threading.current_thread().name))
        if batch.batch_id in release:
            release[batch.batch_id].set()

    result = _rank(tmp_path, _rows(4), batch_size=2, concurrency=2, on_batch=on_batch)

    main = threading.current_thread().name
    assert seen == [("b001", 2, main), ("b000", 2, main)]
    assert [batch.batch_id for batch in result.batches] == ["b001", "b000"]


def test_on_batch_failure_stops_new_calls_and_is_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    port = _Port(lambda prompt, _n: _good_answer(prompt))
    _install(monkeypatch, _Binding(port))

    def on_batch(batch: BatchResult) -> None:
        raise RuntimeError("progress write failed")

    with pytest.raises(RuntimeError, match="progress write failed"):
        _rank(tmp_path, _rows(10), batch_size=2, concurrency=1, on_batch=on_batch)
    assert len(port.prompts) == 1


def test_cancel_stops_new_calls_and_keeps_landed_batches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cancel = threading.Event()
    port = _Port(lambda prompt, _n: _good_answer(prompt))
    _install(monkeypatch, _Binding(port))
    landed: list[str] = []

    def on_batch(batch: BatchResult) -> None:
        landed.append(batch.batch_id)
        cancel.set()

    result = _rank(tmp_path, _rows(8), batch_size=2, concurrency=1, cancel=cancel, on_batch=on_batch)

    assert len(port.prompts) == 1
    assert result.status == "cancelled"
    assert result.fail_open_reason == "cancelled: 6 of 8 postings unscored"
    assert [item.scored for item in result.postings] == [True, True] + [False] * 6
    assert {item.unscored_reason for item in result.postings[2:]} == {"cancelled"}
    assert landed == ["b000", "b001", "b002", "b003"]  # every batch lands, cancelled ones unscored


def test_cancel_before_start_makes_no_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cancel = threading.Event()
    cancel.set()
    port = _Port(lambda prompt, _n: _good_answer(prompt))
    _install(monkeypatch, _Binding(port))

    result = _rank(tmp_path, _rows(3), cancel=cancel)

    assert port.prompts == [] and result.status == "cancelled"


# --- cache ---------------------------------------------------------------------------------


def test_rerank_pays_only_for_new_postings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    port = _Port(lambda prompt, _n: _good_answer(prompt))
    _install(monkeypatch, _Binding(port))
    first = _rank(tmp_path, _rows(5))
    assert len(port.prompts) == 1 and len(list(cache_dir(tmp_path / "home").glob("*.json"))) == 5

    rows = _rows(5) + _rows(2, start=100)
    seen: list[BatchResult] = []
    second = _rank(tmp_path, rows, on_batch=seen.append)

    assert len(port.prompts) == 2
    assert [pid for pid, _ in _batch_lines(port.prompts[1])] == ["p5", "p6"]
    assert seen[0].source == "cache" and seen[0].attempts == 0 and len(seen[0].postings) == 5
    assert [item.cached for item in second.postings] == [True] * 5 + [False] * 2
    assert [item.score for item in second.postings[:5]] == [item.score for item in first.postings]
    assert second.totals()["cached"] == 5 and second.totals()["calls"] == 1


def test_cache_key_covers_resume_prefs_model_and_versions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = dict(content_sha256="sha256:1", resume_digest_sha256="r", prefs_sha256="p", model="codex_cli:default")
    key = model_rank.cache_key(**base)
    for field, value in (("content_sha256", "sha256:2"), ("resume_digest_sha256", "r2"), ("prefs_sha256", "p2"),
                         ("model", "claude_cli:sonnet")):
        assert model_rank.cache_key(**{**base, field: value}) != key
    monkeypatch.setattr(model_rank, "PROMPT_VERSION", "rank-v2")
    assert model_rank.cache_key(**base) != key
    monkeypatch.setattr(model_rank, "PROMPT_VERSION", "rank-v1")
    monkeypatch.setattr(model_rank, "DIGEST_VERSION", "digest-v3")
    assert model_rank.cache_key(**base) != key
    assert model_rank.prefs_digest(_PREFS) != model_rank.prefs_digest(CandidatePrefs(titles=("Staff Software Engineer",)))
    assert cache_dir(tmp_path) == tmp_path / "cache" / "scout" / "rank" / "scores"


def test_changed_prefs_or_resume_miss_the_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    port = _Port(lambda prompt, _n: _good_answer(prompt))
    _install(monkeypatch, _Binding(port))
    _rank(tmp_path, _rows(2))
    rank_postings(_rows(2), resume_text=_RESUME, prefs=CandidatePrefs(titles=("Senior Engineer",)), model_target="codex_cli",
                  home_root=tmp_path / "home", config=_config(tmp_path / "home"))
    rank_postings(_rows(2), resume_text=_RESUME + "\nRust, Kafka\n", prefs=_PREFS, model_target="codex_cli",
                  home_root=tmp_path / "home", config=_config(tmp_path / "home"))
    assert len(port.prompts) == 3


def test_only_valid_batches_are_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _Binding(_Port(lambda prompt, _n: "nope")))
    _rank(tmp_path, _rows(2))
    assert not cache_dir(tmp_path / "home").exists() or list(cache_dir(tmp_path / "home").glob("*.json")) == []


def test_a_corrupt_cache_file_is_a_miss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    port = _Port(lambda prompt, _n: _good_answer(prompt))
    _install(monkeypatch, _Binding(port))
    _rank(tmp_path, _rows(1))
    (path,) = cache_dir(tmp_path / "home").glob("*.json")
    path.write_text('{"score": 900, "reasons": [], "blockers": []}', encoding="utf-8")

    result = _rank(tmp_path, _rows(1))

    assert len(port.prompts) == 2 and not result.postings[0].cached


# --- claude lean + sealed shape --------------------------------------------------------------


def test_claude_target_ranks_through_the_lean_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binding = _Binding(ClaudeCLIAdapter(executable="/opt/fake/claude"), model="sonnet")
    asked = _install(monkeypatch, binding)

    resolved = model_rank._resolve(_config(tmp_path / "home"), "claude_cli", home_root=tmp_path / "home")

    assert asked == ["claude-default"]
    assert isinstance(resolved.port, ClaudeCLIAdapter) and resolved.port.lean
    assert not binding.port.lean  # the assess binding's own adapter is untouched
    assert resolved.effort_applied == "low"
    argv = resolved.port.argv(binding.request(role="reviewer", prompt="x"))
    assert "--permission-mode" not in argv


def test_pass_result_seals_to_rank_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _Binding(_Port(lambda prompt, _n: _good_answer(prompt))))

    result = _rank(tmp_path, _rows(3))
    sealed = json.loads(json.dumps(result.to_json()))

    assert sealed["schema_version"] == "scout-rank:1"
    assert sealed["status"] == "complete" and sealed["fail_open_reason"] is None
    assert sealed["postings"][0]["normalized_url"] == "https://boards.example/0"
    assert sealed["postings"][0]["demoted"] is True
    assert sealed["batches"][0]["attempts"] == 1 and sealed["batches"][0]["valid"] is True
    assert RankResult.from_json(sealed).to_json() == sealed
