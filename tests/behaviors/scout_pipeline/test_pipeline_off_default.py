"""0.1.11: the background pipeline is OFF by default; an explicit ``enabled: true`` keeps it as 0.1.10 had it.

With it off nothing tailors, re-assesses, scores or labels: ``resume store`` makes no model call and queues
nothing, a saved answer and a profile change queue nothing, ``pipeline process`` refuses, and the hidden
``resume tailor`` answers ``tailoring_off``.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.scout import story_bank
from gigai.scout.find_jobs.refresh_tick import settings_path
from gigai.scout.master_store import import_master
from gigai.scout.pipeline import triggers
from gigai.scout.pipeline.settings import PIPELINE_ENV, SOURCE_DEFAULT, SOURCE_ENVIRONMENT, SOURCE_SETTING, pipeline_setting
from gigai.scout.scout_cli import scout_group

from tests.support.pipeline_fixtures import JOB, QUESTION_ID, PipelineFixture, build_pipeline_fixture, set_pipeline_enabled

MASTER = """## Summary

- Engineer with nine years on Python inference services.

## Experience

### Acme Corp
Senior Engineer | 2019 - 2023

- Built Python services for six years; cut p99 latency by 40%.
- Operated Kubernetes clusters backed by PostgreSQL.

## Skills

- Platform: Python, Kubernetes, PostgreSQL
"""


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, pipeline: bool) -> PipelineFixture:
    monkeypatch.delenv(PIPELINE_ENV, raising=False)
    fx = build_pipeline_fixture(tmp_path, monkeypatch, pipeline=pipeline)
    source = tmp_path / "master.md"
    source.write_text(MASTER, encoding="utf-8")
    assert import_master(home_root=fx.home_root, target=fx.target, source=source, gig_id=fx.gig.resolved.gig_id).status == "created"
    return fx


def _invoke(fx: PipelineFixture, *args: str):
    return CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])


def _store(fx: PipelineFixture, tmp_path: Path) -> dict:
    handed = tmp_path / "handed.md"
    handed.write_text(MASTER, encoding="utf-8")
    result = _invoke(fx, "resume", "store", "--in", str(handed), "--job-url", JOB, "--as", "agent")
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _steps(fx: PipelineFixture) -> int:
    from gigai.scout.pipeline.store import PipelineStore

    if not fx.db.is_file():
        return 0
    store = PipelineStore(fx.db)
    try:
        return len(store.steps())
    finally:
        store.close()


def test_no_setting_is_off_and_an_explicit_true_is_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _fixture(tmp_path, monkeypatch, pipeline=False)
    assert not settings_path(fx.home_root, fx.target).is_file() or "enabled" not in json.loads(settings_path(fx.home_root, fx.target).read_text())["pipeline"]
    off = pipeline_setting(fx.home_root, fx.target)
    assert (off.enabled, off.source) == (False, SOURCE_DEFAULT)
    # Caps without the switch are still off.
    path = settings_path(fx.home_root, fx.target)
    path.write_text(json.dumps({"schema_version": "scout-settings:1", "pipeline": {"max_model_calls_per_day": 5}}), encoding="utf-8")
    assert pipeline_setting(fx.home_root, fx.target).enabled is False
    set_pipeline_enabled(fx.home_root, fx.target, True)
    on = pipeline_setting(fx.home_root, fx.target)
    assert (on.enabled, on.source, on.max_model_calls_per_day) == (True, SOURCE_SETTING, 5)
    # The environment still wins either way.
    assert pipeline_setting(fx.home_root, fx.target, environ={PIPELINE_ENV: "off"}).source == SOURCE_ENVIRONMENT
    set_pipeline_enabled(fx.home_root, fx.target, None)
    assert pipeline_setting(fx.home_root, fx.target, environ={PIPELINE_ENV: "on"}).enabled is True


def test_resume_store_with_the_pipeline_off_makes_no_model_call_and_queues_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _fixture(tmp_path, monkeypatch, pipeline=False)
    before = fx.model.calls
    payload = _store(fx, tmp_path)
    assert payload["changed"] is True and payload["recheck"]["result"] == "pipeline_off" and payload["recheck"]["error_code"] is None
    assert payload["drain"] is None and payload["recheck_failed"] is None
    assert fx.model.calls == before, "store made a model call with the pipeline off"
    assert _steps(fx) == 0


def test_resume_store_with_the_pipeline_explicitly_on_still_checks_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _fixture(tmp_path, monkeypatch, pipeline=True)
    before = fx.model.calls
    payload = _store(fx, tmp_path)
    assert payload["recheck"]["result"] != "pipeline_off" and payload["drain"] is not None
    assert fx.model.calls > before


def test_a_saved_answer_and_a_profile_change_queue_nothing_while_the_pipeline_is_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _fixture(tmp_path, monkeypatch, pipeline=False)
    before = fx.model.calls
    entry = story_bank.save_answer(
        home_root=fx.home_root, target=fx.target, question_id=QUESTION_ID, answer="Yes, three years on GCP.", question="Have you run workloads on GCP?"
    )
    fired = triggers.pending_answer(fx.home_root, fx.target, entry, job_identity=JOB).fire()
    assert fired.enqueued == () and fired.awaiting == ()
    assert triggers.profile_changed(fx.home_root, fx.target, fx.profile_id).enqueued == ()
    queued = triggers.enqueue_pairs(fx.home_root, fx.target, [(fx.profile_id, JOB)], trigger="process_now")
    assert queued.state == triggers.DISABLED and queued.enqueued == ()
    assert _steps(fx) == 0 and fx.model.calls == before


def test_pipeline_process_refuses_and_the_hidden_tailor_command_says_it_is_switched_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _fixture(tmp_path, monkeypatch, pipeline=False)
    before = fx.model.calls
    process = CliRunner().invoke(scout_group, ["pipeline", "process", JOB, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert process.exit_code == 1 and json.loads(process.output)["error"]["code"] == "pipeline_off"
    tailor = _invoke(fx, "resume", "tailor", "--job-url", JOB)
    assert tailor.exit_code == 1 and json.loads(tailor.output)["error"]["code"] == "tailoring_off"
    assert "tailoring is switched off in 0.1.11" in tailor.output
    assert fx.model.calls == before and _steps(fx) == 0
    assert scout_group.commands["resume"].commands["tailor"].hidden is True
    assert "tailor" not in CliRunner().invoke(scout_group, ["resume", "--help"]).output.split("Commands:")[-1].split()
