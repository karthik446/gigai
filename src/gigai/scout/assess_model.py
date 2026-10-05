"""0.1.11 MODELPIN: which model an assessment asks for, and the one fallback when the CLI refuses it.

``AssessModelPort`` wraps the (metered) port of one assessment. On ``claude_cli`` a target whose configured model is
``default`` is asked for the evaluated model (``evaluated_models.ASKED_BY_DEFAULT``); a model the operator set in the
target's configuration is asked as set. When the CLI refuses the evaluated model (not on the plan, its limit is out,
any failed call) ONE fallback call goes out on the CLI's default; the port then stays on the default, so a call is
never retried more than once. A model the operator named is never swapped: its failure is the assessment's failure.

The port sits OUTSIDE the call meter, so the refused call and the fallback are both counted. It records what was asked,
what answered and whether the answer was the fallback, for the assessment to store.
"""

from __future__ import annotations

from dataclasses import replace
import subprocess

from ..adapters.port import (
    CapabilityMismatchError,
    InvocationRequest,
    ModelAuthenticationRequired,
    ModelInvocationCancelled,
    ModelInvocationError,
)
from .evaluated_models import ASKED_BY_DEFAULT, DEFAULT_MODEL, EVALUATED_MODELS, asked_model


def _refused(exc: ModelInvocationError) -> bool:
    """A failed call the default model could answer: not a timeout, a cancel, a login or a capability problem."""

    if isinstance(exc, (ModelInvocationCancelled, ModelAuthenticationRequired, CapabilityMismatchError)):
        return False
    return not isinstance(exc.__cause__, subprocess.TimeoutExpired) and "timed out" not in str(exc).lower()


class PolicyState:
    """What one assessment asked for and whether the CLI refused it; shared by every port the assessment builds.

    A guard retry (``quick_assess``) binds a new port; the state outlives it, so a refused model is asked once.
    """

    def __init__(self) -> None:
        #: What the first call asked for (``None`` before a call, or for a target with no evaluated model).
        self.asked: str | None = None
        #: ``True`` once the CLI refused the asked model and the default answered.
        self.fallback = False


class AssessModelPort:
    """``inner`` with the assessment's model policy applied to every call."""

    def __init__(self, inner: object, adapter_kind: str, state: PolicyState | None = None) -> None:
        self._inner = inner
        self._kind = adapter_kind
        self.state = state if state is not None else PolicyState()

    @property
    def asked(self) -> str | None:
        return self.state.asked

    @property
    def fallback(self) -> bool:
        return self.state.fallback

    def invoke(self, request: InvocationRequest) -> object:
        state = self.state
        policy = self._kind in EVALUATED_MODELS and isinstance(request, InvocationRequest)  # a scripted test port's request is not one
        first = request
        pinned = False
        if policy and not state.fallback:
            wanted = asked_model(self._kind, request.model)
            pinned = request.model == DEFAULT_MODEL and wanted != DEFAULT_MODEL
            first = replace(request, model=wanted)
        if policy and state.asked is None:
            state.asked = first.model
        try:
            result = self._inner.invoke(first)  # type: ignore[attr-defined]
        except ModelInvocationError as exc:
            if not (pinned and self._kind in ASKED_BY_DEFAULT and _refused(exc)):
                raise
            state.fallback = True
            result = self._inner.invoke(replace(request, model=DEFAULT_MODEL))  # type: ignore[attr-defined]
        return result

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def apply(binding: object, adapter_kind: str, state: PolicyState | None = None) -> AssessModelPort:
    """Put the policy on ``binding``'s port (in place); ``state`` is the assessment's, shared across its bindings."""

    port = AssessModelPort(binding.port, adapter_kind, state)  # type: ignore[attr-defined]
    binding.port = port  # type: ignore[attr-defined]
    return port


__all__ = ["AssessModelPort", "PolicyState", "apply"]
