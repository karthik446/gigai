"""Run G18 contract and boundary mutation checks without provider effects."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_model_contracts import _exchange, _invocation  # noqa: E402
from gigai.validators import validate_model_exchange, validate_model_invocation  # noqa: E402


def _expect_rejected(name: str, report) -> None:
    if report.valid:
        raise AssertionError(f"mutation escaped: {name}")
    print(f"{name}: caught")


def main() -> None:
    mutations = []

    fallback = _exchange()
    fallback["automatic_fallback"] = True
    mutations.append(("M01", lambda: validate_model_exchange(fallback)))

    retry = _exchange()
    retry["retry_count"] = 1
    mutations.append(("M02", lambda: validate_model_exchange(retry)))

    hidden = _exchange()
    hidden["handoff"]["hidden_context"] = True  # type: ignore[index]
    mutations.append(("M03", lambda: validate_model_exchange(hidden)))

    winner = _exchange(kind="comparison", status="agreement")
    winner["comparison"]["selected_winner"] = {"winner": "left"}  # type: ignore[index]
    mutations.append(("M04", lambda: validate_model_exchange(winner)))

    cap = _exchange()
    cap["handoff"]["index"] = 2  # type: ignore[index]
    mutations.append(("M05", lambda: validate_model_exchange(cap)))

    reused = _exchange(kind="comparison", status="disagreement")
    reused["comparison"]["independent_artifacts"][1]["invocation_id"] = reused["comparison"]["independent_artifacts"][0]["invocation_id"]  # type: ignore[index]
    mutations.append(("M06", lambda: validate_model_exchange(reused)))

    no_adjudication = _exchange(kind="comparison", status="disagreement")
    no_adjudication["comparison"]["requires_human_adjudication"] = False  # type: ignore[index]
    mutations.append(("M07", lambda: validate_model_exchange(no_adjudication)))

    redaction = _invocation(outcome="succeeded", finish="completed", redaction_result="failed")
    mutations.append(("M08", lambda: validate_model_invocation(redaction)))

    for name, action in mutations:
        _expect_rejected(name, action())
    print(f"mutation_killed={len(mutations)}/{len(mutations)}")


if __name__ == "__main__":
    main()
