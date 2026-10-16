"""Offline log-only timing: every retry, no assessment payload changes."""
from pathlib import Path
from types import SimpleNamespace
import logging

import pytest

from gigai.adapters.port import ModelInvocationError
from gigai.scout import assessment_core, quick_assess, assess_model
from tests.behaviors.scout_find_jobs.test_quick_assess import (
    _GOOD_PENDING, _RESUME, _install, _pasted, _run,
)
from tests.support.scout_profile_fixtures import build_gig_with_resume


class Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        self.value += 0.01
        return self.value


@pytest.mark.parametrize("mode,expected_attempts,guards", [("success", 1, 0), ("validation", 2, 0), ("guard", 2, 1)])
def test_one_summary_and_unchanged_storage(tmp_path, monkeypatch, caplog, mode, expected_attempts, guards):
    sentinel = "PRIVATE_SENTINEL_NEVER_LOG"
    fx = build_gig_with_resume(tmp_path, resume_text=_RESUME + sentinel.encode())
    monkeypatch.setattr(quick_assess, "_now", lambda: "2026-01-01T00:00:00Z")
    monkeypatch.setattr(quick_assess, "_read_stored", lambda _: None)
    _install(monkeypatch, [_GOOD_PENDING])
    baseline = _run(fx, _pasted())
    baseline_bytes = Path(baseline.stored_path).read_bytes()
    caplog.clear()
    clock = Clock()
    monkeypatch.setattr(quick_assess, "time", clock)
    monkeypatch.setattr(assessment_core, "time", clock)
    outputs = (["not JSON", _GOOD_PENDING] if mode == "validation" else [_GOOD_PENDING] * expected_attempts)
    binding, _ = _install(monkeypatch, outputs)
    original_invoke = binding.port.invoke

    def invoke(request):
        assert sentinel in request.prompt
        clock.value += 2.0
        return original_invoke(request)

    binding.port.invoke = invoke
    original_parse = quick_assess._parse_body

    def parse(payload):
        clock.value += 0.25
        return original_parse(payload)

    monkeypatch.setattr(quick_assess, "_parse_body", parse)
    if mode == "guard":
        calls = iter([True, False, False])
        monkeypatch.setattr(quick_assess, "posting_requirements_unreadable", lambda *args: next(calls))
    original_write = quick_assess.atomic_write

    def store(*args):
        clock.value += 0.5
        return original_write(*args)

    monkeypatch.setattr(quick_assess, "atomic_write", store)
    with caplog.at_level(logging.INFO, logger="gigai.scout.server"):
        response = _run(fx, _pasted())
    summaries = [r.getMessage() for r in caplog.records if r.getMessage().startswith("assess stages ")]
    assert len(summaries) == 1
    line = summaries[0]
    assert sentinel not in line and "Acme" not in line and "Staff AI" not in line
    fields = dict(part.split("=", 1) for part in line.split()[2:])
    seconds = {key: float(fields[key][:-1]) for key in ("before", "call", "parse", "store", "settle", "total")}
    assert all(value >= 0 for value in seconds.values())
    assert sum(value for key, value in seconds.items() if key != "total") <= seconds["total"] + 0.000001
    assert seconds["call"] == pytest.approx(2.01 * expected_attempts)
    assert seconds["parse"] >= 0.25
    assert seconds["store"] == pytest.approx(0.51)
    assert fields["attempts"] == str(expected_attempts)
    assert fields["guard_retries"] == str(guards)
    assert fields["answer_chars"] == str(sum(map(len, outputs)))
    assert fields["model"] == "fixture"
    assert Path(response.stored_path).read_bytes() == baseline_bytes
    assert response.to_json() == baseline.to_json()


def test_model_fallback_counts_both_transport_attempts(monkeypatch):
    from gigai.adapters.port import InvocationRequest, InvocationResult, NormalizedUsage

    clock = Clock()
    monkeypatch.setattr(quick_assess, "time", clock)
    timing = quick_assess._StageTiming()
    models = []

    class Port:
        def invoke(self, request):
            models.append(request.model)
            clock.value += 3
            if len(models) == 1:
                raise ModelInvocationError("model not found")
            return InvocationResult("success", "{}", "fixture", {}, NormalizedUsage(1, 1, 2), "unavailable")

    binding = SimpleNamespace(port=quick_assess._TimedPort(Port(), timing))
    assess_model.apply(binding, "claude_cli")
    request = InvocationRequest("fixture", "fixture", "default", "assess", "private", frozenset({"text"}))
    binding.port.invoke(request)
    assert len(models) == 2 and models[-1] == "default"
    assert timing.attempts == 2 and timing.answer_chars == 2
    assert timing.call == pytest.approx(6.02)
