"""0.1.10.7 E: every Scout model call is recorded once, with no text, and read back as averages.

In process, through the production fixture seams the payload-privacy suite
uses (the real Ollama adapter over an ``httpx.MockTransport``; its fixture
home is reused here), so what is asserted is the end outcome: a row in the
project's ``pipeline.sqlite`` and the averages ``metrics_report`` (the body
of ``GET /api/metrics`` and of ``gigai scout metrics --json``) answers.

(a) a fixture-transport assess records tokens, time and model, and the
    report answers their average;
(b) rank, tag, tailor (and extract, interview, a run's assess step) record
    through the same ``record_call``: nothing else writes a row;
(c) a call that fails records its error code and counts in the error rate,
    as does a call whose answer was unusable;
(d) no text in any metrics row (runtime: every flow, then the file's bytes;
    static: what the recorder reads off a request, a result and an error);
(e) the estimator with and without history;
(f) the CLI prints the object the report (and the API) answers.

The HTTP half of (a) and (f) is ``tests/api_e2e/test_metrics_journey.py``.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from gigai.adapters import claude_cli
from gigai.adapters.port import InvocationResult, ModelAuthenticationRequired, ModelInvocationError, NormalizedUsage
from gigai.cli import cli
from gigai.scout import call_metrics, proposal_execution
from gigai.scout.call_metrics import CallMeter, CallMetricsError, estimate, metrics_report, usage_metrics
from gigai.scout.find_jobs import bindings
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResponse
from gigai.scout.find_jobs.model_rank import rank_postings
from gigai.scout.find_jobs.rank_digest import CandidatePrefs
from gigai.scout.pipeline import store as pipeline_store
from gigai.scout.pipeline.store import COLUMN_KINDS, PipelineStore, StepMetrics, pipeline_path
from gigai.scout.quick_assess import QuickAssessError, quick_assess_dir, run_quick_assessment

from .test_interview_prep_fixtures import write_acquire_output
from .test_model_payload_privacy import (
    _POSTING,
    BODY_MARKER,
    FORBIDDEN,
    RESUME,
    Home,
    _home,
    _run_every_flow,
    arm,
)

_JOB = AssessJobInput(job_text=_POSTING, title="Staff Software Engineer", company="Acme")
_COLUMNS = tuple(COLUMN_KINDS["model_call"])


def _rows(fixture: Home) -> list[dict[str, object]]:
    connection = sqlite3.connect(pipeline_path(fixture.home, fixture.target))
    try:
        found = connection.execute(f"SELECT {', '.join(_COLUMNS)} FROM model_call ORDER BY id").fetchall()
    finally:
        connection.close()
    return [dict(zip(_COLUMNS, row)) for row in found]


def _assess(fixture: Home, job: AssessJobInput = _JOB) -> AssessResponse:
    return run_quick_assessment(AssessRequest(job=job), home_root=fixture.home, target=fixture.target, config=fixture.config)


# --- (a) an assessment records its tokens, time and model; the report averages them -------------


def test_a_fixture_assess_records_tokens_time_and_model_and_the_report_answers_their_average(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _home(tmp_path, monkeypatch)
    assert metrics_report(fixture.home, fixture.target)["aggregates"] == []
    assert not pipeline_path(fixture.home, fixture.target).exists(), "a read creates no file"

    response = _assess(fixture)

    (row,) = _rows(fixture)
    assert (row["kind"], row["outcome"], row["error_code"], row["items"]) == ("assess", "ok", None, 1)
    assert (row["adapter"], row["lane"], row["model"]) == ("ollama_local", "ollama", bindings.TEST_MODEL_NAME)
    # The fixture transport's own counts (prompt_eval_count / eval_count).
    assert (row["input_tokens"], row["output_tokens"], row["cached_tokens"]) == (10, 20, None)
    assert (row["cost_usd"], row["cost_status"]) == (None, "unavailable")
    assert isinstance(row["seconds"], float) and 0 < row["seconds"] < 60
    assert row["profile_id"] == fixture.profile_id and row["job"] == response.job.job_identity
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", str(row["input_digest"]))
    # Next to what the call produced: the project's own Scout folder holds both.
    stored = Path(response.stored_path)
    assert stored.is_file() and quick_assess_dir(fixture.home, fixture.target).parent == pipeline_path(fixture.home, fixture.target).parents[1]

    _assess(fixture)  # a re-assessment is a second call
    report = metrics_report(fixture.home, fixture.target)
    seconds = [item["seconds"] for item in _rows(fixture)]
    expected = {
        "kind": "assess", "model_target": "ollama_local", "calls": 2, "errors": 0, "error_rate": 0.0, "items": 2,
        "avg_input_tokens": 10, "avg_output_tokens": 20, "avg_cached_tokens": None, "avg_tokens": 30,
        "avg_seconds": round(sum(seconds) / 2, 3), "avg_cost_usd": None,
    }
    assert report["schema_version"] == "scout-metrics:1" and (report["kind"], report["model"]) == (None, None)
    (aggregate,) = report["aggregates"]
    (compared,) = report["comparison"]
    assert {key: aggregate[key] for key in expected} == expected and aggregate["model"] == bindings.TEST_MODEL_NAME
    assert {key: compared[key] for key in expected} == expected and compared["models"] == [bindings.TEST_MODEL_NAME]
    assert aggregate["last_at"] == max(item["started_at"] for item in _rows(fixture))
    # The filters: a model target, a model id, another kind.
    assert metrics_report(fixture.home, fixture.target, kind="assess", model="ollama_local")["aggregates"] == [aggregate]
    assert metrics_report(fixture.home, fixture.target, model=bindings.TEST_MODEL_NAME)["aggregates"] == [aggregate]
    assert metrics_report(fixture.home, fixture.target, kind="rank")["aggregates"] == []
    assert metrics_report(fixture.home, fixture.target, model="codex_cli")["comparison"] == []


# --- (b) one recorder for every call site; (d) no text in any row ---------------------------------


def _spy(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[int]]:
    """Every ``record_call`` (by kind) and every row the store wrote."""

    kinds: list[str] = []
    written: list[int] = []
    real_record, real_store = call_metrics.record_call, PipelineStore.record_call

    def record(home_root: object, target: object, **values: object) -> int | None:
        row = real_record(home_root, target, **values)  # type: ignore[arg-type]
        if row is not None:
            kinds.append(str(values["kind"]))
        return row

    def store_record(self: PipelineStore, **values: object) -> int:
        row = real_store(self, **values)  # type: ignore[arg-type]
        written.append(row)
        return row

    monkeypatch.setattr(call_metrics, "record_call", record)
    monkeypatch.setattr(PipelineStore, "record_call", store_record)
    return kinds, written


def _run_the_tag_queue(fixture: Home, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.find_jobs import posting_tags
    from gigai.scout.find_jobs.company_index import CompanyIndex, CompanyIndexEntry, IndexedPosting, index_stamp
    from gigai.scout.find_jobs.model_tag import TagQueue

    stamp = index_stamp()
    listed = {"1": ("Staff Alchemist", "Denver, CO"), "2": ("Staff Wizard", "Remote - US")}
    CompanyIndex.for_home(fixture.home).write(CompanyIndexEntry(
        company="Initrode", ats="greenhouse", slug="initrode", checked_at=stamp, etag=None, body_sha256=None,
        postings={
            key: IndexedPosting(posting_id=key, title=title, location=location, url=f"https://boards.greenhouse.io/initrode/jobs/{key}", updated_at=None, content_sha256=None, first_seen=stamp, last_seen=stamp)
            for key, (title, location) in listed.items()
        },
    ))
    tags = posting_tags.default_store(fixture.home)
    try:
        assert posting_tags.tag_new_titles(tags, [title for title, _location in listed.values()]).tagged == 2
    finally:
        tags.close()
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "1")
    result = TagQueue(home_root=fixture.home, target=fixture.target, config=fixture.config, live_update=lambda: False).drain()
    assert result.calls >= 1


class _Raising:
    """A port whose call fails, with text in the message that must never be stored."""

    name = "raising"

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def invoke(self, request: object) -> object:
        raise self.error


def _failing_adapter(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    real = proposal_execution.resolve_model_adapter

    def resolve(config: object, name: str, **kwargs: object) -> object:
        binding = real(config, name, **kwargs)  # type: ignore[arg-type]
        return SimpleNamespace(port=_Raising(error), request=binding.request, close=binding.close, current=binding.current)

    setattr(resolve, "_scout_test_transport", True)  # or the fixture seam puts its own resolver back
    monkeypatch.setattr(proposal_execution, "resolve_model_adapter", resolve)


_SECRET_ERROR = f"provider said: {BODY_MARKER} resume of zq7731@example.test could not be read"
#: Words of the prompt, the resume, the posting and the answers: none may be in the metrics file.
#: (The company's name is not listed: a job is named by its public posting URL, which carries the board's slug.)
_TEXT = (
    *FORBIDDEN, BODY_MARKER, "Glimmerfall", "Kubernetes", "Kafka", "Postgres", "Acme is hiring", "Staff Software Engineer", "Requirements",
    "Staff Alchemist", "Denver", "Initrode", "provider said", "could not be read", "fixture score", "cannot produce JSON",
    "pending_user_answers", "matched_above_threshold", "You are",
)
_SHAPE = {
    "id": r"[A-Za-z0-9][A-Za-z0-9_.:-]*",
    "job": r"https://[a-z0-9./-]+|text:sha256:[0-9a-f]{64}",
    "digest": r"sha256:[0-9a-f]{64}",
    "code": r"[a-z][a-z0-9_]*",
    "lane": r"claude_cli|codex_cli|ollama|local|api:[a-z0-9_.-]+",
    "model": r"[A-Za-z0-9][A-Za-z0-9_.:/+-]*",
    "timestamp": r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z",
}


def test_every_flow_records_through_the_one_recorder_and_no_row_holds_any_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _home(tmp_path, monkeypatch)
    home, target = fixture.home, fixture.target
    kinds, written = _spy(monkeypatch)
    capture = arm(monkeypatch)

    # A run's assess step, quick assess (profile and pasted), tailor, extract, interview prep.
    _run_every_flow(fixture, capture, monkeypatch)
    # Rank, as a run's rank step and a re-rank call it: with the project named.
    posting = write_acquire_output(home, target, "run_2")
    ranked = rank_postings(
        # Other titles than the flow above ranked for: a new candidate digest, so a model call and not a cache hit.
        [posting], resume_text=RESUME, prefs=CandidatePrefs(titles=("Principal Engineer",)), model_target="ollama_local",
        home_root=home, config=fixture.config, target=target, profile_id=fixture.profile_id,
    )
    assert ranked.postings and ranked.postings[0].scored
    _run_the_tag_queue(fixture, monkeypatch)
    # An answer that is not JSON (twice: the one retry), then a call that fails with text in its message.
    garbage = AssessJobInput(job_text=f"{_POSTING} {bindings.TEST_MODEL_GARBAGE_MARKER}", title="Staff Software Engineer", company="Acme")
    with pytest.raises(QuickAssessError) as invalid:
        _assess(fixture, garbage)
    assert invalid.value.code == "model_output_invalid"
    with monkeypatch.context() as scoped:
        _failing_adapter(scoped, ModelInvocationError(_SECRET_ERROR))
        with pytest.raises(QuickAssessError) as failed:
            _assess(fixture)
    assert failed.value.code == "model_unavailable" and capture.tripped == []

    rows = _rows(fixture)
    # (b) every kind went through record_call, and record_call is the only writer of a row.
    assert set(kinds) == {"assess", "rank", "tag", "tailor", "extract", "interview"}
    assert len(rows) == len(kinds) == len(written) and [row["kind"] for row in rows] == kinds
    by_kind = {kind: [row for row in rows if row["kind"] == kind] for kind in set(kinds)}
    assert all(row["adapter"] == "ollama_local" and row["lane"] == "ollama" for row in rows)
    assert by_kind["rank"][-1]["items"] == 1 and by_kind["rank"][-1]["profile_id"] == fixture.profile_id
    assert by_kind["rank"][-1]["outcome"] == "ok" and by_kind["rank"][-1]["input_tokens"] == 10
    assert {row["outcome"] for row in by_kind["tailor"]} == {"ok"} and all(row["job"] and row["input_tokens"] for row in by_kind["tailor"])
    # The fixture model answers a tag prompt with an assessment: the call is recorded, as an unusable answer.
    # (Both titles twice, then ONE split into halves: the queue's own retry rule.)
    assert [row["items"] for row in by_kind["tag"]] == [2, 2, 1, 1] and all(row["profile_id"] is None for row in by_kind["tag"])
    assert {row["error_code"] for row in by_kind["tag"]} == {"model_output_invalid"}
    # A run's assess step names the posting and the run's profile.
    assert any(row["job"] == posting.normalized_url and row["profile_id"] == fixture.profile_id for row in by_kind["assess"])
    # (c) the failures: two unusable answers, one failed call with its bounded code.
    codes = [row["error_code"] for row in by_kind["assess"] if row["outcome"] == "error"]
    assert codes == ["model_output_invalid", "model_output_invalid", "model_invocation_failed"]
    assert by_kind["assess"][-1]["input_tokens"] is None and by_kind["assess"][-1]["model"] == bindings.TEST_MODEL_NAME

    # (d) every value is a number or has its column's shape ...
    for row in rows:
        for column, value in row.items():
            kind = COLUMN_KINDS["model_call"][column]
            if value is None:
                continue
            if kind in ("integer", "real"):
                assert type(value) in (int, float), (column, value)
            else:
                assert type(value) is str and re.fullmatch(_SHAPE[kind], value), (column, value)
    # ... and the file as a whole holds no word of a prompt, a resume, a posting, an answer or an error message.
    files = [path for path in pipeline_path(home, target).parent.iterdir() if path.name.startswith("pipeline.sqlite")]
    raw = b"".join(path.read_bytes() for path in files).decode("latin-1").lower()
    assert files and [word for word in _TEXT if word.lower() in raw] == []
    assert not re.search(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", raw)
    served = json.dumps(metrics_report(home, target)).lower()
    assert [word for word in _TEXT if word.lower() in served] == []


def test_the_recorder_reads_only_numbers_ids_and_a_prompt_digest() -> None:
    """Static: what ``call_metrics`` takes off a request, a result and an error, and what a row can hold."""

    source = inspect.getsource(call_metrics)
    tree = ast.parse(source)
    read: set[str] = set()
    proxies = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "__getattr__"]
    proxied = {id(node) for function in proxies for node in ast.walk(function)}  # a metered port/binding passes attributes on
    assert len(proxies) == 2
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and id(node) not in proxied:
            assert isinstance(node.args[1], ast.Constant), "getattr by a computed name"
            read.add(str(node.args[1].value))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            if isinstance(node.func.value, ast.Name) and node.func.value.id in ("raw", "details"):
                assert isinstance(node.args[0], ast.Constant)
                read.add(f"usage:{node.args[0].value}")
    assert read == {
        "normalized_usage", "raw_usage", "input_tokens", "output_tokens", "cost_usd", "resolved_model", "model", "prompt", "code",
        "call_id", "invalid_output",
        "usage:cache_read_input_tokens", "usage:cache_creation_input_tokens", "usage:cached_input_tokens",
        "usage:prompt_tokens_details", "usage:input_tokens_details", "usage:cached_tokens", "usage:cost",
    }
    # The answer's text is never read; an error's message only to spot a timeout; the prompt only into a digest.
    assert "output_text" not in source and source.count("str(exc)") == 1 and '"timed out" in str(exc).lower()' in source
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    uses = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "prompt" and isinstance(node.ctx, ast.Load)]
    used_as = sorted(ast.unparse(parents[id(node)]) for node in uses)
    assert used_as == ["isinstance(prompt, str)", "prompt.encode"], used_as
    encoded = next(parents[id(node)] for node in uses if isinstance(parents[id(node)], ast.Attribute))
    assert ast.unparse(parents[id(parents[id(encoded)])]) == "digest_imported_bytes(prompt.encode('utf-8'))"
    # A row has no text column, and the shape a pipeline step's attempt records is the one reused.
    assert set(COLUMN_KINDS["model_call"].values()) <= {"integer", "real", "id", "job", "lane", "model", "code", "timestamp", "digest"}
    shared = {name: kind for name, kind in COLUMN_KINDS["step_run"].items() if name in COLUMN_KINDS["model_call"]}
    assert shared == {name: COLUMN_KINDS["model_call"][name] for name in shared} and set(StepMetrics.__dataclass_fields__) <= set(shared)


# --- (c) the codes of a failed call --------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A home and a target with a project id, without a bound project (the store only needs the path)."""

    monkeypatch.setattr("gigai.scout.find_jobs.discovery.storage.project_id", lambda home, target: "proj_metrics")
    return tmp_path / "home", tmp_path / "target"


def _result(model: str = "m-1", usage: dict[str, object] | None = None, *, tokens: tuple[int | None, int | None] = (100, 10), cost: float | None = None) -> InvocationResult:
    return InvocationResult(
        status="success", output_text="{}", resolved_model=model, raw_usage=usage or {},
        normalized_usage=NormalizedUsage(tokens[0], tokens[1], None), cost_status="unavailable", cost_usd=cost,
    )


class _Port:
    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)

    def invoke(self, request: object) -> object:
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


_REQUEST = SimpleNamespace(model="default", prompt="Assess this resume: Jane Doe, jane.doe@example.com")


def test_a_failed_call_records_a_bounded_code_and_counts_in_the_error_rate(project: tuple[Path, Path]) -> None:
    home, target = project
    meter = CallMeter("assess", "codex_cli", home, target, job="https://jobs.example.test/acme/1")
    denied = ModelAuthenticationRequired("run `codex login` as jane.doe@example.com")
    timeout = ModelInvocationError("codex timed out after 120 s")
    timeout.__cause__ = TimeoutError()
    port = _Port(_result(), denied, timeout, RuntimeError("Traceback: jane"), _result(), _result())

    meter.invoke(port, _REQUEST)
    for expected in (ModelAuthenticationRequired, ModelInvocationError, RuntimeError):
        with pytest.raises(expected):
            meter.invoke(port, _REQUEST)
    meter.invoke(port, _REQUEST)
    meter.invalid_output()  # the caller could not use this answer
    meter.invalid_output()  # ... and saying so twice changes nothing more
    meter.invoke(port, _REQUEST)

    store = PipelineStore(pipeline_path(home, target))
    rows = store._conn().execute("SELECT outcome, error_code, input_tokens, model, job FROM model_call ORDER BY id").fetchall()
    assert [(row[0], row[1]) for row in rows] == [
        ("ok", None), ("error", "model_authentication_required"), ("error", "model_timeout"), ("error", "model_call_failed"),
        ("error", "model_output_invalid"), ("ok", None),
    ]
    assert rows[1][2] is None and rows[1][3] == "default" and all(row[4] == "https://jobs.example.test/acme/1" for row in rows)
    (compared,) = metrics_report(home, target)["comparison"]
    assert (compared["calls"], compared["errors"], compared["error_rate"]) == (6, 4, 0.6667)
    # The unusable answer still reported its tokens; the three that raised reported none.
    assert compared["avg_input_tokens"] == 100 and compared["avg_tokens"] == 110
    dump = "\n".join(sqlite3.connect(store.path).iterdump())
    assert "jane" not in dump.lower() and "login" not in dump and "Assess this" not in dump


def test_recording_never_fails_the_call_it_describes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answer = _result()
    # No project named: the call is made, nothing is recorded.
    assert CallMeter("rank", "codex_cli").invoke(_Port(answer), _REQUEST) is answer
    # A folder that is not bound to a project, and a store that cannot be written.
    assert CallMeter("assess", "codex_cli", tmp_path / "home", tmp_path / "unbound").invoke(_Port(answer), _REQUEST) is answer
    monkeypatch.setattr("gigai.scout.find_jobs.discovery.storage.project_id", lambda home, target: "proj_x")
    blocked = tmp_path / "home" / "scout" / "proj_x"
    blocked.mkdir(parents=True)
    (blocked / "pipeline").write_text("a file where the folder should be")
    meter = CallMeter("assess", "codex_cli", tmp_path / "home", tmp_path / "target")
    assert meter.invoke(_Port(answer), _REQUEST) is answer
    meter.invalid_output()
    # A profile or a job that is not shaped like one is left out, never stored as it is.
    (blocked / "pipeline").unlink()
    odd = CallMeter("assess", "my adapter", tmp_path / "home", tmp_path / "target", profile_id="Jane Doe", job="Senior engineer at Acme")
    odd.invoke(_Port(_result(model="gpt-x, and some words")), _REQUEST)
    row = sqlite3.connect(pipeline_path(tmp_path / "home", tmp_path / "target")).execute(
        "SELECT profile_id, job, adapter, model, lane FROM model_call"
    ).fetchone()
    assert row == (None, None, None, "gpt-x", "local")


def test_tokens_are_comparable_across_adapters_and_the_reported_cost_is_kept() -> None:
    # Claude: input_tokens leaves cached input out; the CLI's JSON carries total_cost_usd.
    stdout = json.dumps({
        "type": "result", "subtype": "success", "is_error": False, "result": "{}", "total_cost_usd": 0.0421,
        "usage": {"input_tokens": 12, "cache_creation_input_tokens": 3000, "cache_read_input_tokens": 16000, "output_tokens": 900},
    })
    text, model, usage = claude_cli._parse_claude_json(stdout, "claude-sonnet-5-5")
    claude = InvocationResult(
        status="success", output_text=text, resolved_model=model, raw_usage=usage,
        normalized_usage=claude_cli._normalize_usage(usage), cost_status="provider_reported", cost_usd=claude_cli._reported_cost(stdout),
    )
    assert usage_metrics("claude_cli", claude) == StepMetrics(
        adapter="claude_cli", model="claude-sonnet-5-5", input_tokens=19012, output_tokens=900, cached_tokens=16000,
        cost_usd=0.0421, cost_status="provider_reported",
    )
    assert claude_cli._reported_cost('{"result": "x"}') is None and claude_cli._reported_cost("not json") is None
    assert claude_cli._reported_cost('{"total_cost_usd": -1}') is None and claude_cli._reported_cost('{"total_cost_usd": true}') is None
    with pytest.raises(ValueError):
        _result(cost=-0.5)
    # Codex: input_tokens already counts the cached part; no cost is reported.
    codex = _result("gpt-5.1-codex", {"input_tokens": 18400, "cached_input_tokens": 9200, "output_tokens": 1100}, tokens=(18400, 1100))
    assert usage_metrics("codex_cli", codex) == StepMetrics(
        adapter="codex_cli", model="gpt-5.1-codex", input_tokens=18400, output_tokens=1100, cached_tokens=9200,
        cost_usd=None, cost_status="unavailable",
    )
    # OpenRouter: usage.cost and prompt_tokens_details.cached_tokens.
    routed = _result("openai/gpt-5", {"prompt_tokens": 500, "completion_tokens": 50, "cost": 0.002, "prompt_tokens_details": {"cached_tokens": 100}}, tokens=(500, 50))
    metrics = usage_metrics("openrouter_api", routed)
    assert (metrics.cached_tokens, metrics.cost_usd, metrics.cost_status) == (100, 0.002, "provider_reported")
    # A lean Claude call names every model that ran; the first is the id. Nothing reported: nothing guessed.
    assert usage_metrics("claude_cli", _result("claude-haiku-4-5,claude-sonnet-5-5")).model == "claude-haiku-4-5"
    nothing = usage_metrics("ollama_local", _result(tokens=(None, None), usage={"runtime_version": "0.9", "cost": "free"}), requested_model="default")
    assert (nothing.input_tokens, nothing.cached_tokens, nothing.cost_usd, nothing.model) == (None, None, None, "m-1")
    assert [call_metrics.lane_for(adapter, name) for adapter, name in (
        ("claude_cli", "claude-default"), ("codex_cli", None), ("ollama_local", "x"), ("openrouter_api", "OpenRouter-Main"),
        ("openai_api", "has spaces"), ("deterministic", None),
    )] == ["claude_cli", "codex_cli", "ollama", "api:openrouter-main", "api:openai_api", "local"]


# --- (e) the estimator ----------------------------------------------------------------------------


def _history(home: Path, target: Path) -> None:
    store = PipelineStore(pipeline_path(home, target))
    codex = StepMetrics(adapter="codex_cli", model="gpt-5.1-codex", input_tokens=18000, output_tokens=1500, cached_tokens=9000, cost_status="unavailable")
    for seconds in (10.0, 12.0, 11.0, 11.0):
        store.record_call(kind="assess", lane="codex_cli", seconds=seconds, metrics=codex)
    store.record_call(kind="assess", lane="codex_cli", seconds=120.0, outcome="error", error_code="model_timeout", metrics=StepMetrics(adapter="codex_cli", model="gpt-5.1-codex"))
    claude = StepMetrics(adapter="claude_cli", model="claude-sonnet-5-5", input_tokens=20000, output_tokens=1000, cost_usd=0.05, cost_status="provider_reported")
    store.record_call(kind="assess", lane="claude_cli", seconds=20.0, metrics=claude)
    store.record_call(kind="assess", lane="claude_cli", seconds=30.0, metrics=StepMetrics(adapter="claude_cli", model="claude-opus-5-5", input_tokens=40000, output_tokens=3000, cost_usd=0.25, cost_status="provider_reported"))
    for _ in range(2):
        store.record_call(kind="rank", lane="codex_cli", seconds=8.0, items=50, metrics=StepMetrics(adapter="codex_cli", model="gpt-5.1-codex", input_tokens=6000, output_tokens=2000))
    store.close()


def test_the_estimator_with_and_without_history(project: tuple[Path, Path]) -> None:
    home, target = project
    empty = {"kind": "assess", "model": "codex_cli", "n": 10, "calls": None, "tokens": None, "seconds": None, "cost": None, "basis_calls": 0}
    assert estimate("assess", "codex_cli", 10, home_root=home, target=target) == empty
    assert estimate("assess", "codex_cli", 10, home_root=home, target=None) == empty
    assert not pipeline_path(home, target).exists()

    _history(home, target)

    # Codex: 5 calls for 5 jobs (the timeout is a call too); 19.5k tokens and 11 s per call that answered; no cost reported.
    assert estimate("assess", "codex_cli", 10, home_root=home, target=target) == {
        "kind": "assess", "model": "codex_cli", "n": 10, "calls": 10, "tokens": 195000, "seconds": 110.0, "cost": None, "basis_calls": 5,
    }
    # Claude reports a cost; one model id of the target, then the target (both ids together).
    assert estimate("assess", "claude-sonnet-5-5", 4, home_root=home, target=target) == {
        "kind": "assess", "model": "claude-sonnet-5-5", "n": 4, "calls": 4, "tokens": 84000, "seconds": 80.0, "cost": 0.2, "basis_calls": 1,
    }
    both = estimate("assess", "claude_cli", 2, home_root=home, target=target)
    assert (both["calls"], both["tokens"], both["seconds"], both["cost"], both["basis_calls"]) == (2, 64000, 50.0, 0.3, 2)
    # Every model of the kind; a rank call covers 50 postings, so 120 postings are 3 calls.
    assert estimate("assess", None, 1, home_root=home, target=target)["basis_calls"] == 7
    assert estimate("rank", "codex_cli", 120, home_root=home, target=target) == {
        "kind": "rank", "model": "codex_cli", "n": 120, "calls": 3, "tokens": 24000, "seconds": 24.0, "cost": None, "basis_calls": 2,
    }
    assert estimate("rank", "codex_cli", 0, home_root=home, target=target)["calls"] == 0
    # No history for this kind or this model: None, never a guess.
    for kind, model in (("tailor", "codex_cli"), ("assess", "ollama_local"), ("rank", "claude_cli")):
        answer = estimate(kind, model, 5, home_root=home, target=target)
        assert (answer["calls"], answer["tokens"], answer["seconds"], answer["cost"], answer["basis_calls"]) == (None, None, None, None, 0)
    for bad in ({"kind": "summarize"}, {"n": -1}, {"n": 1.5}, {"model": "codex cli"}):
        with pytest.raises(CallMetricsError) as refused:
            estimate(**{"kind": "assess", "model": "codex_cli", "n": 1, **bad}, home_root=home, target=target)  # type: ignore[arg-type]
        assert refused.value.code == "invalid_value"

    # The report the estimates come from: per model id, and per model target.
    report = metrics_report(home, target, kind="assess")
    assert [(item["model_target"], item["model"], item["calls"]) for item in report["aggregates"]] == [
        ("claude_cli", "claude-opus-5-5", 1), ("claude_cli", "claude-sonnet-5-5", 1), ("codex_cli", "gpt-5.1-codex", 5),
    ]
    claude, codex = report["comparison"]
    assert (claude["model_target"], claude["models"], claude["avg_tokens"], claude["avg_seconds"], claude["avg_cost_usd"]) == (
        "claude_cli", ["claude-opus-5-5", "claude-sonnet-5-5"], 32000, 25.0, 0.15,
    )
    assert (codex["avg_tokens"], codex["avg_cached_tokens"], codex["avg_seconds"], codex["errors"], codex["error_rate"]) == (19500, 9000, 11.0, 1, 0.2)


# --- (f) the CLI prints the report ----------------------------------------------------------------


def test_the_cli_prints_the_report_as_json_and_as_one_line_per_model(project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = project
    target.mkdir(parents=True)
    home.mkdir(parents=True)
    runner = CliRunner()
    base = ["scout", "metrics", "--home", str(home), "--target", str(target)]

    nothing = runner.invoke(cli, base)
    assert nothing.exit_code == 0 and nothing.output.strip() == "No model call has been recorded yet."
    assert json.loads(runner.invoke(cli, [*base, "--json"]).output) == metrics_report(home, target)

    _history(home, target)

    for extra in ([], ["--kind", "assess"], ["--kind", "assess", "--model", "codex_cli"], ["--model", "claude-opus-5-5"]):
        options = dict(zip([name.removeprefix("--") for name in extra[::2]], extra[1::2]))
        printed = runner.invoke(cli, [*base, *extra, "--json"])
        assert printed.exit_code == 0, printed.output
        assert json.loads(printed.output) == metrics_report(home, target, **options)  # type: ignore[arg-type]
    plain = runner.invoke(cli, [*base, "--kind", "assess"])
    assert plain.exit_code == 0 and plain.output.splitlines() == [
        "assess on claude_cli: avg 32.0k tokens, 25.0 s, $0.1500 per call; 2 calls, 0 failed (0%).",
        "assess on codex_cli: avg 19.5k tokens, 11.0 s per call; 5 calls, 1 failed (20%).",
    ]
    refused = runner.invoke(cli, [*base, "--kind", "summarize", "--json"])
    assert refused.exit_code == 1 and json.loads(refused.output)["error"]["code"] == "invalid_value"
    assert pipeline_store.SCHEMA_VERSION == 5  # 0110-9-01: posting_board, posting_source
