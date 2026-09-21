"""Focused regressions for the frozen R4 authority boundaries."""

from __future__ import annotations

import pytest

from gigai import model_execution
from gigai.canonical import digest_imported_bytes


_RUN = "run_00000000-0000-4000-8000-000000000001"
_GOAL = "goal_00000000-0000-4000-8000-000000000002"


def _reference(source_id: str) -> model_execution.SelectedReference:
    content = source_id.encode("ascii")
    return model_execution.SelectedReference(
        reference_id=source_id,
        path=f"records/{source_id}.json",
        content=content,
        content_sha256=digest_imported_bytes(content),
    )


def _descriptor(source_id: str, *, family: str = "scout_record", purpose: str = "experience") -> dict[str, object]:
    return {
        "source_id": source_id,
        "family": family,
        "purpose": purpose,
        "content_sha256": digest_imported_bytes(source_id.encode("ascii")),
        "identity_sha256": "sha256:" + "a" * 64,
    }


@pytest.mark.parametrize(
    "descriptors",
    [
        (_descriptor("source_1"),),
        (_descriptor("source_1"), _descriptor("source_1")),
        (_descriptor("source_1", family="unknown"), _descriptor("source_2", purpose="answer")),
        ({"source_id": "source_1"}, _descriptor("source_2")),
        ([_descriptor("source_1"), _descriptor("source_2")],),
    ],
    ids=["missing-selected", "duplicate", "unknown-family", "closed-shape", "nonmapping"],
)
def test_v3_descriptor_set_is_exact_and_transport_is_not_called(
    descriptors: tuple[object, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("transport/adapter must not be resolved for invalid descriptors")

    monkeypatch.setattr(model_execution, "resolve_model_adapter", forbidden)
    with pytest.raises(model_execution.ModelExecutionError):
        model_execution.run_model_invocation(
            resolved=object(),
            config=object(),
            run_id=_RUN,
            goal_id=_GOAL,
            model_target="local-proposal",
            role="reviewer",
            prompt="synthetic prompt",
            references=(_reference("source_1"), _reference("source_2")),
            selected_reference_ids=("source_1", "source_2"),
            policy=model_execution.InvocationPolicy(
                local_allowed=True,
                selected_source_descriptors=descriptors,  # type: ignore[arg-type]
            ),
        )
