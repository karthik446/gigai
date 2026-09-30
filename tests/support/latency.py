"""ci-fix-pr37-r2: a shared, CI-scalable wall-clock latency bound.

Shared CI runners are slower and noisier than a laptop -- a bound tight
enough to catch a real regression on a dev machine (e.g. uat-bug-008's
"/api/config answers in under a second") can flake under CI contention with
no code regression at all (observed: 1.327s and 1.305s against a 1.0s bound
in unrelated CI runs -- see ci-fix-pr37-r2's worker report). Rather than
loosen the bound everywhere (which would let a real regression back in on a
dev machine), ``latency_bound`` scales it by ``GIGAI_TEST_LATENCY_SCALE``,
an env var left at ``1.0`` (no change) everywhere except the CI workflow
job(s) that set it wider.

This does not replace a call-count/cache-hit assertion where one is
available (the real behavioral guarantee); it is the fallback wall-clock
check for callers that only have a `timed_request`-style measurement.
"""

from __future__ import annotations

import os

LATENCY_SCALE_ENV = "GIGAI_TEST_LATENCY_SCALE"


def latency_scale() -> float:
    """The active scale factor, read fresh on each call (never cached, so a
    test that itself sets the env var via ``monkeypatch`` is honored)."""

    raw = os.environ.get(LATENCY_SCALE_ENV, "1.0")
    try:
        scale = float(raw)
    except ValueError:
        return 1.0
    return scale if scale > 0 else 1.0


def latency_bound(seconds: float) -> float:
    """``seconds`` scaled by ``GIGAI_TEST_LATENCY_SCALE`` (default 1.0)."""

    return seconds * latency_scale()


__all__ = ["LATENCY_SCALE_ENV", "latency_bound", "latency_scale"]
