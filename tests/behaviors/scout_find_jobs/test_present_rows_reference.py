"""0110-042: ``present.json`` does not seal a second copy of every acquired posting's text.

The present node's output listed the run's acquire rows whole, text
included: at catalog size 2.1 MB of a 2.37 MB ``outputs/present.json``, a
second copy of ``outputs/acquire.json`` in a git-backed journal, every run.

What is pinned here, on 0110-037's launched-run fixture (a real gig, a
hand-written acquire batch, a fake model):

* a sealed present row references its acquire row: identity, digest, the
  list's label fields and the outcome, and no posting text anywhere in the
  sealed output (``PresentOutput.without_row_text``);
* the bound present node (what a run seals through) returns that shape;
* a run sealed BEFORE this (its present rows carry their text) still reads:
  the contract accepts both shapes and re-writes the earlier one byte for
  byte;
* what the run reads serve is unchanged: they never read ``present.json``,
  they build the payload from the run's own acquire rows
  (``present_payload_from``), text and all, and that payload is the earlier
  sealed shape.

The sealed sizes at catalog size, through the real server and the real
bindings: ``tests/api_e2e/test_run_reads_fast_journey.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gigai.scout.find_jobs import bindings
from gigai.scout.find_jobs.contracts import (
    AggregateStatus,
    AssessOutput,
    PostingRowResult,
    PresentInput,
    PresentOutput,
    PresentPayload,
    RowOutcome,
)
from gigai.scout.projection import RunEvidence, present_payload_from

from tests.behaviors.scout_find_jobs.test_run_not_assessed_reasons import _BATCH, _OTHER_ROLE, _assess, _run_config
from tests.behaviors.scout_find_jobs.test_story_bank_run_assess import (  # noqa: F401 - fx and binding are fixtures
    _Binding,
    _context,
    _pinned,
    _run_id,
    binding,
    fx,
)
from tests.support.scout_profile_fixtures import ProfileFixtureGig

_KEPT = [item for item in _BATCH if item is not _OTHER_ROLE]
_ROWS = tuple(PostingRowResult(item, RowOutcome.NEW) for item in _KEPT)


def _node_output(fx: ProfileFixtureGig, run_id: str, assess: AssessOutput) -> PresentOutput:
    """What ``present_node`` returns for the run: its acquire rows as acquire sealed them, text and all."""

    payload = PresentPayload(
        run_id=run_id,
        config=_run_config(),
        pinned_resume=_pinned(fx),
        rows=_ROWS,
        failures=(),
        assessments=assess.assessments,
        not_assessed=assess.not_assessed,
        node_receipts=(),
        status=AggregateStatus.PENDING,
    )
    return PresentOutput(payload=payload, aggregate_status=AggregateStatus.PENDING)


def _assessed(fx: ProfileFixtureGig, run_id: str) -> AssessOutput:
    return _assess(fx, run_id, seal=True).without_unassessed_text()


def test_a_sealed_present_row_references_its_acquire_row_and_carries_no_text(fx: ProfileFixtureGig, binding: _Binding) -> None:
    run_id = _run_id(90)
    full = _node_output(fx, run_id, _assessed(fx, run_id))
    assert all(posting.text and posting.text in json.dumps(full.to_json()) for posting in _KEPT), "the node's rows carry the text"

    sealed = full.without_row_text()
    sealed_json = sealed.to_json()
    sealed_text = json.dumps(sealed_json)
    for posting in _BATCH:
        assert posting.text and posting.text not in sealed_text, f"{posting.company}'s posting text is in the sealed present output"
    rows = sealed_json["payload"]["rows"]  # type: ignore[index]
    assert [row["posting"]["normalized_url"] for row in rows] == [posting.normalized_url for posting in _KEPT]
    for row, posting in zip(rows, _KEPT):
        # Identity, digest and what the list shows; the outcome beside it. Nothing but the text is gone.
        assert row == {"posting": {key: value for key, value in posting.to_json().items() if key != "text"}, "outcome": "new"}
        assert row["posting"]["content_sha256"] == posting.content_sha256 and row["posting"]["url"] and row["posting"]["title"]
    # Everything else is what the node returned.
    assert {key: value for key, value in sealed_json["payload"].items() if key != "rows"} == {  # type: ignore[union-attr]
        key: value for key, value in full.to_json()["payload"].items() if key != "rows"  # type: ignore[union-attr]
    }
    assert sealed.aggregate_status is full.aggregate_status
    # What is sealed reads back as what was sealed; there is nothing left to drop.
    assert PresentOutput.from_json(sealed_json) == sealed
    assert sealed.without_row_text() is sealed


def test_the_bound_present_node_seals_rows_without_their_text(
    fx: ProfileFixtureGig, binding: _Binding, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ``_present_bound`` is the callable a run's present goal is registered with: what it returns is
    # what the run writes to ``outputs/present.json``.
    run_id = _run_id(91)
    full = _node_output(fx, run_id, _assessed(fx, run_id))
    seen: list[PresentInput] = []

    def node(context: object, input: PresentInput, **_: object) -> PresentOutput:
        seen.append(input)
        return full

    monkeypatch.setattr(bindings, "present_node", node)
    sealed = bindings._present_bound(
        _context(fx, run_id, "present"), PresentInput("records/batch/input.json", None, ()), home_root=fx.home_root, target=fx.target
    )

    assert [item.batch_ref for item in seen] == [f"runs/{run_id}/outputs/acquire.json"]
    assert sealed == full.without_row_text()
    assert all(row.posting.text is None for row in sealed.payload.rows)  # type: ignore[attr-defined]
    assert not any(posting.text in json.dumps(sealed.to_json()) for posting in _BATCH if posting.text)  # type: ignore[attr-defined]


def test_a_present_output_sealed_with_the_text_still_reads(fx: ProfileFixtureGig, binding: _Binding) -> None:
    run_id = _run_id(92)
    # The pre-042 shape, as a run wrote it: every present row with its posting text.
    old_json = json.loads(json.dumps(_node_output(fx, run_id, _assessed(fx, run_id)).to_json()))
    assert all(row["posting"]["text"] for row in old_json["payload"]["rows"]), "the fixture is the earlier shape"

    old = PresentOutput.from_json(old_json)
    assert old.to_json() == old_json, "an earlier output reads and writes byte for byte"
    assert json.dumps(old.to_json()) == json.dumps(old_json)
    assert [row.posting.text for row in old.payload.rows] == [posting.text for posting in _KEPT]
    # Both shapes name the same rows, outcomes, assessments and reasons.
    new = PresentOutput.from_json(old.without_row_text().to_json())
    assert [(row.posting.normalized_url, row.posting.content_sha256, row.outcome) for row in new.payload.rows] == [
        (row.posting.normalized_url, row.posting.content_sha256, row.outcome) for row in old.payload.rows
    ]
    assert new.payload.assessments == old.payload.assessments and new.payload.not_assessed == old.payload.not_assessed


def test_the_run_reads_take_the_text_from_the_runs_own_acquire_rows(fx: ProfileFixtureGig, binding: _Binding) -> None:
    # No read parses ``present.json``: the payload a read serves is built from the run's sealed input,
    # its acquire output and its assess output. So a run whose present rows are sealed without text is
    # read exactly as one sealed with it: the payload below IS the earlier sealed shape.
    run_id = _run_id(93)
    assess = _assessed(fx, run_id)
    evidence = RunEvidence(
        run_id=run_id,
        details=None,
        run_input=SimpleNamespace(config=_run_config(), pinned_resume=_pinned(fx)),  # type: ignore[arg-type]
        acquire_output=SimpleNamespace(rows=_ROWS, failures=()),  # type: ignore[arg-type]
        assess_output=assess,
        receipts=(),
    )

    served = present_payload_from(evidence)
    assert served.to_json() == _node_output(fx, run_id, assess).payload.to_json()
    assert [row.posting.text for row in served.rows] == [posting.text for posting in _KEPT]
    assert served.to_json() != _node_output(fx, run_id, assess).without_row_text().payload.to_json()
