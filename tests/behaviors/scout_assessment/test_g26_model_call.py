from __future__ import annotations

from types import SimpleNamespace
import threading
import time

import pytest

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.model_call import BoundedCallError, invoke_bounded


def _result() -> InvocationResult:
    return InvocationResult(
        status="success",
        output_text='{"summary":"fixture"}',
        resolved_model="fixture",
        raw_usage={},
        normalized_usage=NormalizedUsage(None, None, None),
        cost_status="unavailable",
    )


def test_bounded_call_times_out_without_accepting_a_late_result() -> None:
    returned = threading.Event()

    class SlowPort:
        def invoke(self, request):
            time.sleep(0.08)
            returned.set()
            return _result()

    with pytest.raises(BoundedCallError, match="wall-time budget") as error:
        invoke_bounded(
            SlowPort(),
            SimpleNamespace(max_output_tokens=10),
            max_wall_time_ms=5,
            max_output_tokens=10,
        )
    assert error.value.reason == "timed_out"
    assert not returned.is_set()
    assert returned.wait(1)


def test_bounded_call_cancels_before_invocation() -> None:
    cancelled = threading.Event()
    cancelled.set()
    invoked = False

    class Port:
        def invoke(self, request):
            nonlocal invoked
            invoked = True
            return _result()

    with pytest.raises(BoundedCallError, match="cancelled") as error:
        invoke_bounded(
            Port(),
            SimpleNamespace(max_output_tokens=10),
            max_wall_time_ms=100,
            max_output_tokens=10,
            cancel_event=cancelled,
        )
    assert error.value.reason == "cancelled"
    assert not invoked


def test_bounded_call_cancels_while_provider_is_running() -> None:
    cancel = threading.Event()
    started = threading.Event()

    class SlowPort:
        def invoke(self, request):
            started.set()
            time.sleep(0.2)
            return _result()

    def cancel_after_start() -> None:
        assert started.wait(1)
        cancel.set()

    threading.Thread(target=cancel_after_start, daemon=True).start()
    with pytest.raises(BoundedCallError, match="cancelled") as error:
        invoke_bounded(
            SlowPort(),
            SimpleNamespace(max_output_tokens=10),
            max_wall_time_ms=500,
            max_output_tokens=10,
            cancel_event=cancel,
        )
    assert error.value.reason == "cancelled"


def test_bounded_call_rejects_request_over_token_budget() -> None:
    with pytest.raises(BoundedCallError, match="output limit") as error:
        invoke_bounded(
            lambda request: _result(),  # type: ignore[arg-type]
            SimpleNamespace(max_output_tokens=11),
            max_wall_time_ms=100,
            max_output_tokens=10,
        )
    assert error.value.reason == "budget_exhausted"
