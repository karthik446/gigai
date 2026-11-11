"""V1: a gig's eval runs live in `<home>/gigs/<gig>/evals.sqlite`, written through `ctx.trace` and read by `ctx.replay`.

The fixture gig `tests/fixtures/gigs/eval-fixture/` is "installed" the way test_loader.py does it. Its `answer` step
asks the deterministic adapter (no provider, no network) and traces the call with `model_call(binding, request,
result)`; every fixture row is synthetic.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import stat
import subprocess
import sys
import tarfile
import threading
from pathlib import Path

import pytest

import gigai.evals.records as records
import gigai.sdk
from gigai.adapters.claude_cli import ClaudeCLIAdapter
from gigai.adapters.deterministic import DeterministicAdapter
from gigai.adapters.factory import ModelAdapterBinding
from gigai.adapters.port import InvocationRequest, InvocationResult, NormalizedUsage, RetryPolicy
from gigai.canonical import digest_imported_bytes
from gigai.config import Endpoint, ModelTarget
from gigai.evals.records import (
    EvalsError,
    evals_db_path,
    open_evals_db,
    write_run,
    write_score,
    write_trace,
)
from gigai.gigs.home import GigNameError
from gigai.model_targets import ResolvedModelTarget
from gigai.sdk import (
    Dataset,
    GigContext,
    ItemRef,
    PromptRef,
    RunRecord,
    ScoreRecord,
    TraceRecord,
    model_call,
    new_id,
)
from gigai.sdk.store import EVALS_STORE, CoreStoreError, GigStore, StoreVersionError
from tests.behaviors.gigs.test_loader import FIXTURES, _add_fixture, _run

GIG = "eval-fixture"
MANIFEST = {"gig": {"name": GIG}, "data": {"stores": [{"name": "notes", "engine": "sqlite"}]}, "evals": {"steps": []}}
# Columns a successful deterministic-adapter trace may leave NULL: the error, the cost (V1 never prices a call).
MAY_BE_NULL = {"error", "cost_usd"}
SCHEMA = {"type": "object"}


def _gigai(tmp_path: Path, site: Path, home: Path, *argv: str) -> subprocess.CompletedProcess:
    completed = _run(tmp_path, site, *argv, env_extra={"GIGAI_HOME": str(home)})
    assert completed.returncode == 0, completed.stderr
    return completed


@pytest.fixture
def site(tmp_path: Path) -> Path:
    directory = tmp_path / "site"
    directory.mkdir()
    _add_fixture(directory, GIG)
    return directory


@pytest.fixture
def fixture_gig(monkeypatch: pytest.MonkeyPatch):
    """The fixture gig's module, imported in this process; forgotten afterwards."""

    import importlib
    import sys

    monkeypatch.syspath_prepend(str(FIXTURES / GIG))
    yield importlib.import_module("gig_eval_fixture")
    sys.modules.pop("gig_eval_fixture", None)


def _db(home: Path) -> sqlite3.Connection:
    return sqlite3.connect(evals_db_path(GIG, home))


def _run_record(run_id: str = "run_fixture") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        gig=GIG,
        manifest_digest=digest_imported_bytes(b"manifest"),
        dataset=Dataset("synthetic", "1", digest_imported_bytes(b"rows"), {"count": 2}),
    )


def _ctx(home: Path) -> GigContext:
    return GigContext(home, GIG, MANIFEST, logging.getLogger("gigai.gig.eval-fixture"))


def _trace(run_id: str = "run_fixture", item: str = "q1") -> TraceRecord:
    return TraceRecord(new_id("trace"), run_id, "answer", ItemRef(item, "1", "sums"), "in", '{"answer": "4"}')


@pytest.mark.parametrize("step", ["answer", "1.2/judge", "3.7/noise", "4.3/judge", "a.b-c_d/e"])
def test_a_trace_takes_any_step_id_a_manifest_can_declare(step):
    assert TraceRecord(new_id("trace"), "run_fixture", step, ItemRef("q1", "1", "sums"), "in", "out").step_id == step


@pytest.mark.parametrize("step", ["", "/judge", "1.2/", "1.2//judge", "../x", "1.2/../x", ".hidden", "a b", "Ä", None])
def test_a_trace_refuses_a_step_id_that_could_climb_out_or_is_empty(step):
    with pytest.raises(GigNameError):
        TraceRecord(new_id("trace"), "run_fixture", step, ItemRef("q1", "1", "sums"), "in", "out")


def test_one_traced_call_writes_one_complete_trace_next_to_the_gigs_stores(tmp_path, site):
    home = tmp_path / "home"
    _gigai(tmp_path, site, home, GIG, "note", "kept in the gig's own store")
    answered = json.loads(_gigai(tmp_path, site, home, GIG, "answer").stdout)
    assert answered["parse_status"] == "ok"

    folder = home / "gigs" / GIG
    assert sorted(path.name for path in folder.iterdir()) == ["evals.sqlite", "notes.sqlite"]
    assert stat.S_IMODE((folder / "evals.sqlite").stat().st_mode) == 0o600
    with _db(home) as db:
        db.row_factory = sqlite3.Row
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("select count(*) from traces").fetchone()[0] == 1
        assert db.execute(
            "select count(*) from pragma_table_info('traces') where name in "
            "('input_sha256','output_sha256','repeat_index','attempt_index','latency_ms','cluster_id','parse_status')"
        ).fetchone()[0] == 7
        row = dict(db.execute("select * from traces").fetchone())
        run = dict(db.execute("select * from runs").fetchone())
    assert sorted(name for name, value in row.items() if value is None) == sorted(MAY_BE_NULL)
    assert row["trace_id"] == answered["trace_id"] and row["run_id"] == answered["run_id"] == run["run_id"]
    assert (row["step_id"], row["item_id"], row["item_version"], row["cluster_id"]) == ("answer", "q1", "1", "sums")
    assert row["input_sha256"] == digest_imported_bytes(row["input"].encode("utf-8"))
    assert row["output_sha256"] == digest_imported_bytes(row["output_raw_text"].encode("utf-8"))
    assert json.loads(row["output_parsed"]) == {"answer": "4"}
    assert (row["temperature"], row["top_p"], row["seed"], row["output_mode"]) == (0.0, 1.0, 7, "schema")
    assert json.loads(row["retry_policy"]) == {"attempts": 1, "backoff_seconds": 0.0}
    assert json.loads(row["settings_unsupported"]) == [] and json.loads(row["argv"]) == []
    assert (row["model_provider"], row["model_endpoint"], row["adapter_version"]) == ("deterministic", "offline", "deterministic:2")
    assert row["model_adapter"] == "gig_eval_fixture.FixtureAdapter:1"
    assert set(json.loads(row["usage"])) == {"input_tokens", "output_tokens", "total_tokens", "raw"}
    assert row["outcome"] == "succeeded" and row["latency_ms"] >= 0
    assert run["gig"] == GIG and run["schema_version"] == 1
    assert json.loads(run["prereg"])["primary_metric"] == "exact_match"


def test_replay_rescoring_is_identical_twice_and_calls_no_model(tmp_path, site, fixture_gig, monkeypatch):
    home = tmp_path / "home"
    argv = [arg for item in ("q1", "q2", "q3", "q4", "q5", "q6") for arg in ("--item", item)]
    run_id = json.loads(_gigai(tmp_path, site, home, GIG, "answer", *argv).stdout.splitlines()[0])["run_id"]

    def no_model(*_args, **_kwargs):
        raise AssertionError("replay called a model")

    monkeypatch.setattr(DeterministicAdapter, "invoke", no_model)
    first = _ctx(home).replay(run_id, fixture_gig.postprocess)
    assert first == _ctx(home).replay(run_id, fixture_gig.postprocess, step_id="answer")
    assert [row.item_id for row in first] == ["q1", "q2", "q3", "q4", "q5", "q6"]
    assert [row.parse_status for row in first] == ["ok", "ok", "ok", "ok", "ok", "invalid"]
    cli = [_gigai(tmp_path, site, home, GIG, "replay", run_id).stdout for _ in range(2)]
    assert cli[0] == cli[1] and len(cli[0].splitlines()) == 6


def test_replay_refuses_a_trace_whose_stored_output_no_longer_matches_its_hash(tmp_path, site, fixture_gig):
    home = tmp_path / "home"
    run_id = json.loads(_gigai(tmp_path, site, home, GIG, "answer").stdout)["run_id"]
    with _db(home) as db:
        db.execute("update traces set output_raw_text = 'edited'")
    with pytest.raises(EvalsError, match="output"):
        _ctx(home).replay(run_id, fixture_gig.postprocess)


def test_export_writes_the_gig_folder_with_a_consistent_evals_sqlite(tmp_path, site):
    home = tmp_path / "home"
    _gigai(tmp_path, site, home, GIG, "note", "one")
    _gigai(tmp_path, site, home, GIG, "answer")
    archive = tmp_path / "out" / "eval-fixture.tar.gz"
    archive.parent.mkdir()
    exported = _gigai(tmp_path, site, home, GIG, "export", "-o", str(archive))
    assert str(archive) in exported.stdout
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    with tarfile.open(archive) as tar:
        assert sorted(tar.getnames()) == [f"{GIG}/evals.sqlite", f"{GIG}/notes.sqlite"]
        assert {member.mode for member in tar.getmembers()} == {0o600}
        tar.extractall(tmp_path / "unpacked", filter="data")
    with sqlite3.connect(tmp_path / "unpacked" / GIG / "evals.sqlite") as db:
        assert db.execute("select count(*) from traces").fetchone()[0] == 1
    refused = _run(tmp_path, site, GIG, "export", "-o", str(archive), env_extra={"GIGAI_HOME": str(home)})
    assert refused.returncode == 1 and "cannot export" in refused.stderr  # never overwrites


def test_export_refuses_a_symlinked_gig_folder(tmp_path, site):
    home = tmp_path / "home"
    other = home / "gigs" / "othergig"
    other.mkdir(parents=True)
    sqlite3.connect(other / "secret.sqlite").close()
    (home / "gigs" / GIG).symlink_to(other, target_is_directory=True)
    archive = tmp_path / "out.tar.gz"
    refused = _run(tmp_path, site, GIG, "export", "-o", str(archive), env_extra={"GIGAI_HOME": str(home)})
    assert refused.returncode == 1 and "not a real directory" in refused.stderr
    assert not archive.exists()


def test_export_refuses_an_archive_path_inside_the_gig_folder(tmp_path, site):
    home = tmp_path / "home"
    _gigai(tmp_path, site, home, GIG, "note", "one")
    folder = home / "gigs" / GIG
    for inside in (folder / "self.tar.gz", folder / "sub" / ".." / "self.tar.gz"):
        refused = _run(tmp_path, site, GIG, "export", "-o", str(inside), env_extra={"GIGAI_HOME": str(home)})
        assert refused.returncode == 1 and "inside the gig folder" in refused.stderr
    assert sorted(path.name for path in folder.iterdir()) == ["notes.sqlite"]


def test_export_is_listed_with_help_and_scout_gets_none(tmp_path, site):
    home = tmp_path / "home"
    listed = _gigai(tmp_path, site, home, GIG, "--help").stdout
    assert "export" in listed and "gig folder" in _gigai(tmp_path, site, home, GIG, "export", "--help").stdout
    nothing = _run(tmp_path, site, GIG, "export", "-o", str(tmp_path / "x.tar.gz"), env_extra={"GIGAI_HOME": str(home)})
    assert nothing.returncode == 1 and "nothing to export" in nothing.stderr
    assert not (tmp_path / "x.tar.gz").exists()
    scout = _run(tmp_path, site, "scout", "--help", env_extra={"GIGAI_HOME": str(home)})
    assert scout.returncode == 0, scout.stderr
    assert "  export" not in scout.stdout


def test_ctx_trace_works_without_the_store_helper(tmp_path, monkeypatch):
    # Records go through gigs/home.py and evals/records.py only; P4s's ctx.store.db is never needed.
    def no_store(*_args, **_kwargs):
        raise AssertionError("ctx.store.db was used")

    monkeypatch.setattr(GigStore, "db", no_store)
    home = tmp_path / "home"
    ctx = _ctx(home)
    record = ctx.trace(
        "answer", "in", "not json", run=_run_record(), item=ItemRef("q1", "1"), parse_status="not_json"
    )
    assert ctx.store.path(GIG, EVALS_STORE) == evals_db_path(GIG, home)
    with _db(home) as db:
        assert db.execute("select trace_id, parse_status, model_id from traces").fetchall() == [
            (record.trace_id, "not_json", None)
        ]


def test_two_connections_write_at_once(tmp_path):
    home = tmp_path / "home"
    connection = open_evals_db(GIG, home)
    write_run(connection, _run_record())
    connection.close()
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def writer(item: str) -> None:
        try:
            db = open_evals_db(GIG, home)
            barrier.wait()
            for _ in range(20):
                write_trace(db, _trace(item=item))
            db.close()
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assert below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(item,)) for item in ("q1", "q2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    with _db(home) as db:
        assert db.execute("select count(*) from traces").fetchone()[0] == 40


def test_a_newer_evals_file_is_refused_and_left_as_it_was(tmp_path, fixture_gig):
    home = tmp_path / "home"
    open_evals_db(GIG, home).close()
    path = evals_db_path(GIG, home)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 2")
    before = path.read_bytes()
    with pytest.raises(StoreVersionError, match="update gigai"):
        open_evals_db(GIG, home)
    with pytest.raises(StoreVersionError):
        _ctx(home).replay("run_fixture", fixture_gig.postprocess)
    assert path.read_bytes() == before


def test_the_evals_path_has_one_source_for_records_and_the_store(tmp_path, monkeypatch):
    # Move the file evals_db_path names: ctx.store.path/db/version and ctx.trace must all follow it.
    home = tmp_path / "home"
    moved = home / "gigs" / GIG / "moved-evals.sqlite"
    monkeypatch.setattr(records, "evals_db_path", lambda gig, home_: Path(home_) / "gigs" / gig / "moved-evals.sqlite")
    ctx = _ctx(home)
    assert ctx.store.path(GIG, EVALS_STORE) == moved
    ctx.trace("answer", "in", "out", run=_run_record(), item=ItemRef("q1", "1"))
    ctx.store.db(GIG, EVALS_STORE).close()
    assert ctx.store.version(EVALS_STORE) == records.SCHEMA_VERSION
    assert sorted(path.name for path in (home / "gigs" / GIG).iterdir()) == ["moved-evals.sqlite"]


def test_a_gig_cannot_set_the_version_of_or_migrate_cores_evals_store(tmp_path):
    home = tmp_path / "home"
    ctx = _ctx(home)
    ctx.trace("answer", "in", "out", run=_run_record(), item=ItemRef("q1", "1"))
    before = evals_db_path(GIG, home).read_bytes()
    with pytest.raises(CoreStoreError, match="belongs to core"):
        ctx.store.set_version(EVALS_STORE, 9)
    with pytest.raises(CoreStoreError, match="belongs to core"):
        ctx.store.migrate(EVALS_STORE, [lambda connection: None] * 2)
    assert ctx.store.version(EVALS_STORE) == 1
    assert evals_db_path(GIG, home).read_bytes() == before


def test_the_sdk_takes_no_home_or_gig_and_the_cli_does_not_import_the_records(tmp_path):
    assert not hasattr(gigai.sdk, "trace") and not hasattr(gigai.sdk, "replay")
    assert gigai.sdk.RunRecord is records.RunRecord
    probe = "import sys, gigai.cli; print('gigai.evals.records' in sys.modules)"
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    completed = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env=env, check=True)
    assert completed.stdout.strip() == "False"


def test_opening_twice_migrates_once(tmp_path):
    home = tmp_path / "home"
    for _ in range(2):
        open_evals_db(GIG, home).close()
    with _db(home) as db:
        tables = [row[0] for row in db.execute("select name from sqlite_master where type = 'table' order by name")]
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert tables == ["judge_validations", "labels", "runs", "scores", "traces"]


def test_a_symlinked_evals_file_is_refused(tmp_path):
    home = tmp_path / "home"
    elsewhere = tmp_path / "elsewhere.sqlite"
    sqlite3.connect(elsewhere).close()
    folder = home / "gigs" / GIG
    folder.mkdir(parents=True)
    (folder / "evals.sqlite").symlink_to(elsewhere)
    with pytest.raises(EvalsError, match="symlink"):
        open_evals_db(GIG, home)


def test_a_run_id_is_never_reused_for_different_facts_and_scores_need_their_trace(tmp_path):
    connection = open_evals_db(GIG, tmp_path / "home")
    try:
        write_run(connection, _run_record())
        write_run(connection, _run_record())  # the same facts again: a no-op
        changed = RunRecord(**{**_run_record().__dict__, "manifest_digest": digest_imported_bytes(b"other")})
        with pytest.raises(EvalsError, match="already recorded with different facts"):
            write_run(connection, changed)
        with pytest.raises(sqlite3.IntegrityError):
            write_trace(connection, _trace(run_id="run_unknown"))
        trace = _trace()
        write_trace(connection, trace)
        write_score(connection, ScoreRecord(new_id("score"), trace.trace_id, "exact_match", "1", 1.0))
        with pytest.raises(sqlite3.IntegrityError):
            write_score(connection, ScoreRecord(new_id("score"), "trace_unknown", "exact_match", "1", 0.0))
        assert connection.execute("select count(*) from scores").fetchone()[0] == 1
    finally:
        connection.close()


def test_the_hook_records_unsupported_settings_as_null_and_the_claude_argv(tmp_path):
    target = ResolvedModelTarget(
        target=ModelTarget("claude-default", "claude", "claude-model", ("text",), 64),
        endpoint=Endpoint(name="claude", adapter="claude_cli"),
    )
    binding = ModelAdapterBinding(target, ClaudeCLIAdapter(executable="/nonexistent/claude"))
    request: InvocationRequest = binding.request(role="reviewer", prompt="synthetic prompt")
    request = InvocationRequest(**{
        **request.__dict__, "temperature": 0.5, "seed": 3, "output_mode": "schema", "schema": SCHEMA,
        "retry_policy": RetryPolicy(2, 0.5),
    })
    result = InvocationResult(
        status="success",
        output_text="{}",
        resolved_model="claude-model",
        raw_usage={"unsupported": ["temperature", "seed"]},
        normalized_usage=NormalizedUsage(10, 2, 12),
        cost_status="unavailable",
        latency_ms=5,
        adapter_version="claude_cli:2",
    )
    call = model_call(binding, request, result)
    executable, *rest = binding.port.argv(request)
    assert call.argv == ("claude", *rest)  # the executable's name only: its path names the operator's home
    assert (call.settings.temperature, call.settings.seed, call.settings.unsupported) == (None, None, ("temperature", "seed"))
    assert call.settings.output_mode == "schema" and call.settings.schema_sha256 is not None
    assert call.settings.retry_policy == {"attempts": 2, "backoff_seconds": 0.5}
    assert (call.usage["input_tokens"], call.usage["output_tokens"], call.usage["total_tokens"]) == (10, 2, 12)
    assert call.model_adapter == "gigai.adapters.claude_cli.ClaudeCLIAdapter:1"

    home = tmp_path / "home"
    ctx = GigContext(home, GIG, MANIFEST, logging.getLogger("gigai.gig.eval-fixture"))
    ctx.trace("answer", request.prompt, result.output_text, run=_run_record(), item=ItemRef("q1", "1"),
              prompt=PromptRef.of("answer", "1", request.prompt), model=call)
    with _db(home) as db:
        row = db.execute("select temperature, seed, settings_unsupported, argv from traces").fetchone()
    assert row[:3] == (None, None, '["temperature","seed"]')
    assert tuple(json.loads(row[3])) == call.argv
