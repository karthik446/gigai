"""SCOPE-ADD-3 C1: a scripted model port for the ranking pass (no live model call).

``RankPort`` answers ``model_rank``'s rank-v1 prompt with a valid id-keyed
array; ``score_for(title)`` decides each posting's score and blockers from
its digest line. ``gate`` lets a test hold every batch after the first one
so it can read progress mid-pass.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import re
import threading
from types import SimpleNamespace

from gigai.adapters.port import InvocationRequest, InvocationResult, NormalizedUsage

_LINE = re.compile(r"^(p\d+) \| (.*)$")


def batch_lines(prompt: str) -> list[tuple[str, str]]:
    block = prompt.split("\nPOSTINGS (", 1)[1].split("\n\nAnswer with ONLY", 1)[0]
    return [m.groups() for m in map(_LINE.match, block.splitlines()[1:]) if m]  # type: ignore[union-attr]


ScoreFor = Callable[[str], "tuple[int, list[str]]"]


class RankPort:
    """Answers each batch; records every prompt; optionally holds batches after the first."""

    def __init__(self, score_for: ScoreFor, *, hold_after_first: bool = False, fail: bool = False) -> None:
        self.score_for = score_for
        self.prompts: list[str] = []
        self.hold_after_first = hold_after_first
        self.fail = fail
        self.gate = threading.Event()
        self.first_answered = threading.Event()
        self.second_started = threading.Event()  # the second call is provably in flight (asked, held on ``gate``)
        self._lock = threading.Lock()

    @property
    def asked(self) -> list[str]:
        """Every digest line asked about, in the order the calls were made."""

        return [rest for prompt in self.prompts for _pid, rest in batch_lines(prompt)]

    def invoke(self, request: InvocationRequest) -> InvocationResult:
        with self._lock:
            self.prompts.append(request.prompt)
            first = len(self.prompts) == 1
            if len(self.prompts) == 2:
                self.second_started.set()
        if self.hold_after_first and not first:
            assert self.gate.wait(30), "the test never released the held batches"
        items = []
        for pid, rest in batch_lines(request.prompt):
            score, blockers = self.score_for(rest)
            items.append({"posting_id": pid, "score": score, "reasons": ["fits the stack"], "blockers": blockers})
        text = "not json" if self.fail else json.dumps(items)
        if first:
            self.first_answered.set()
        return InvocationResult("success", text, "fake-rank-model", {}, NormalizedUsage(100, 10, 110), "unavailable")


class RankBinding:
    def __init__(self, port: object) -> None:
        self.port = port
        self.current = SimpleNamespace(target=SimpleNamespace(model="fake-rank-model"))

    def request(self, *, role: str, prompt: str, required_capabilities: frozenset[str] = frozenset({"text"})) -> InvocationRequest:
        return InvocationRequest(target_name="fake-target", endpoint_name="fake", model="fake-rank-model", role=role,
                                 prompt=prompt, target_capabilities=frozenset({"text"}))

    def close(self) -> None:
        pass


def install_rank_port(monkeypatch, port: object, *, configured: str | None = None) -> list[str]:
    """Route the ranker's adapter seam to ``port``; returns the configured target names asked for."""

    asked: list[str] = []

    def resolve(config, target_name, **_kwargs):
        asked.append(target_name)
        return RankBinding(port)

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    if configured is not None:
        monkeypatch.setattr(
            "gigai.scout.proposal_execution._resolve_configured_target_name_for_adapter",
            lambda *_args, **_kwargs: configured,
        )
    return asked


__all__ = ["RankBinding", "RankPort", "batch_lines", "install_rank_port"]
