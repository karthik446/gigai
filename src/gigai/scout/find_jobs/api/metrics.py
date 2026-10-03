"""0.1.10.7 E: ``GET /api/metrics`` -- what the model calls of this project cost, as averages.

Every Scout model call (assess, rank, tag, tailor, extract, interview) is
recorded once, locally, by ``scout/call_metrics.py``: the model, tokens in /
out / cached, the cost when the provider reports one, the wall time and the
outcome. No prompt, answer, resume or posting text is ever stored, so none is
served. This route answers the averages: ``aggregates`` per ``(kind,
model_target, model)`` and ``comparison`` per ``(kind, model_target)``.
``?kind=`` and ``?model=`` (a model target or a model id) narrow both.
``gigai scout metrics --json`` prints the same object.

Reading makes no model call and creates no file.
"""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from ...call_metrics import CallMetricsError, metrics_report
from ...pipeline.store import PipelineStoreError


class MetricsRoutesMixin:
    """``Handler`` mixin: ``GET /api/metrics``."""

    def _handle_get_metrics(self) -> None:
        backend = self._backend
        target = getattr(backend, "target", None)
        if target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
            return
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=False)
        unknown = sorted(set(query) - {"kind", "model"})
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown query key: {unknown[0]}")
            return
        kind = (query.get("kind") or [None])[0]
        model = (query.get("model") or [None])[0]
        try:
            report = metrics_report(backend.home_root, target, kind=kind, model=model)
        except (CallMetricsError, PipelineStoreError) as exc:
            status = HTTPStatus.UNPROCESSABLE_ENTITY if exc.code == "invalid_value" else HTTPStatus.CONFLICT
            self._error(status, exc.code, str(exc))
            return
        self._write_json(HTTPStatus.OK, report)


__all__ = ["MetricsRoutesMixin"]
