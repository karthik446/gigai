"""0110-040: a run does not seal the posting text of a row it did not assess.

Since 0110-037 a launched run labels every row it leaves out (``over_cap``,
``duplicate``, ``location_mismatch``, ...), and each label was a whole
posting, text included, in ``outputs/assess.json`` (``not_assessed`` and
``candidate_rows``) and again in ``outputs/present.json``. At catalog size
that took ``assess.json`` from 0.27 MB to 4.06 MB a run, in a git-backed
journal. A run over unchanged postings sealed the same (``bindings``' own
``unchanged`` rows).

What is pinned here, on 0110-037's launched-run fixture (a real gig, a
hand-written acquire batch, a fake model):

* a not-assessed row is sealed with identity, digest, the list's label
  fields and its reason, and its text is nowhere in the sealed output; an
  assessed row's candidate row is untouched;
* the same for the ``unchanged`` rows ``bindings`` adds
  (``AssessOutput.without_unassessed_text``, what it seals through);
* a run sealed BEFORE this (its not-assessed rows carry their text) still
  reads: the contracts accept both shapes and the run reads answer the same
  reasons and the same posting;
* the no-query ``/results`` read answers a not-assessed row with its text,
  as before, from the run's own acquire row (``with_row_text``).

The sealed sizes at catalog size, through the real server and the real
bindings: ``tests/api_e2e/test_run_reads_fast_journey.py``.
"""

from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace

from gigai.scout.find_jobs.api import run_reads
from gigai.scout.find_jobs.contracts import (
    AssessOutput,
    NotAssessedReason,
    NotAssessedRow,
    PostingRow,
    PostingRowResult,
    RowOutcome,
    posting_identity,
    with_row_text,
)

from tests.behaviors.scout_find_jobs.test_run_not_assessed_reasons import (
    _ABROAD,
    _ASSESSED_ACME,
    _ASSESSED_BOREALIS,
    _BATCH,
    _COPY_ACME,
    _OTHER_ROLE,
    _OVER_CAP,
    _assess,
    _reasons,
)
from tests.behaviors.scout_find_jobs.test_story_bank_run_assess import (  # noqa: F401 - fx and binding are fixtures
    _Binding,
    _run_id,
    binding,
    fx,
)
from tests.support.scout_profile_fixtures import ProfileFixtureGig

_NOT_ASSESSED = [_COPY_ACME, _OVER_CAP, _ABROAD]
_ASSESSED = [_ASSESSED_ACME, _ASSESSED_BOREALIS]
_KEPT = [item for item in _BATCH if item is not _OTHER_ROLE]


def _with_text(sealed: dict[str, object]) -> dict[str, object]:
    """``sealed`` (an ``AssessOutput``'s JSON) in the shape a run sealed before 0110-040: every row with its text."""

    text = {item.normalized_url: item.text for item in _BATCH}
    old = json.loads(json.dumps(sealed))
    for key in ("candidate_rows", "not_assessed"):
        for row in old[key]:
            row["posting"]["text"] = text[row["posting"]["normalized_url"]]
    return old


def _view(run_id: str, output: AssessOutput) -> run_reads.RunView:
    acquire = SimpleNamespace(
        rows=tuple(PostingRowResult(item, RowOutcome.NEW) for item in _KEPT), carried_forward_assessments=(), rank_scores=()
    )
    return run_reads.RunView(SimpleNamespace(run_id=run_id, acquire_output=acquire, assess_output=output), scores={})


def test_a_row_the_run_did_not_assess_is_sealed_without_its_text(fx: ProfileFixtureGig, binding: _Binding) -> None:
    output = _assess(fx, _run_id(80), seal=True)
    sealed = output.to_json()
    sealed_text = json.dumps(sealed)

    assert len(_reasons(output)) == len(_NOT_ASSESSED)
    for posting in _NOT_ASSESSED:
        assert posting.text and posting.text not in sealed_text, f"{posting.company}'s posting text is in the sealed output"
    rows = {row["posting"]["normalized_url"]: row for row in sealed["not_assessed"]}  # type: ignore[union-attr]
    candidates = {row["posting"]["normalized_url"]: row["posting"] for row in sealed["candidate_rows"]}  # type: ignore[union-attr]
    for posting in _NOT_ASSESSED:
        # Identity, digest and what the list shows; the reason beside it. Nothing but the text is gone.
        expected = {key: value for key, value in posting.to_json().items() if key != "text"}
        assert rows[posting.normalized_url]["posting"] == expected == candidates[posting.normalized_url]
        assert expected["content_sha256"] == posting.content_sha256 and expected["title"] and expected["company"]
        assert rows[posting.normalized_url]["reason"] in {"duplicate", "over_cap", "location_mismatch"}
    # An assessed row is sealed as before.
    for posting in _ASSESSED:
        assert candidates[posting.normalized_url] == posting.to_json() and str(posting.text) in sealed_text
    # What is sealed reads back as what the node returned.
    assert AssessOutput.from_json(sealed) == output


def test_an_unchanged_row_added_after_the_node_is_sealed_without_its_text(fx: ProfileFixtureGig, binding: _Binding) -> None:
    # ``bindings`` adds the acquire rows a run skipped as unchanged (text and all) to the node's output,
    # then seals through ``without_unassessed_text``.
    output = _assess(fx, _run_id(81), seal=True)
    unchanged = replace(_OTHER_ROLE, title="Software Engineer")
    reconciled = replace(
        output,
        candidate_rows=(*output.candidate_rows, PostingRowResult(unchanged, RowOutcome.UNCHANGED)),
        not_assessed=(*output.not_assessed, NotAssessedRow(unchanged, NotAssessedReason.UNCHANGED)),
    )
    assert unchanged.text and unchanged.text in json.dumps(reconciled.to_json())

    sealed = reconciled.without_unassessed_text()
    assert unchanged.text not in json.dumps(sealed.to_json())
    assert sealed.not_assessed[-1] == NotAssessedRow(posting_identity(unchanged), NotAssessedReason.UNCHANGED)
    assert sealed.candidate_rows[-1] == PostingRowResult(posting_identity(unchanged), RowOutcome.UNCHANGED)
    assert sealed.assessments == output.assessments and sealed.candidate_rows[:-1] == output.candidate_rows
    assert sealed.without_unassessed_text() is sealed, "nothing left to drop"
    assert AssessOutput.from_json(sealed.to_json()) == sealed


def test_a_run_sealed_with_the_text_still_reads(fx: ProfileFixtureGig, binding: _Binding) -> None:
    run_id = _run_id(82)
    output = _assess(fx, run_id, seal=True)
    old_json = _with_text(output.to_json())
    assert all(row["posting"]["text"] for row in old_json["not_assessed"]), "the fixture is the earlier shape"  # type: ignore[union-attr]

    old = AssessOutput.from_json(old_json)
    assert old.to_json() == old_json, "an earlier output reads and writes byte for byte"
    assert [row.posting.text for row in old.not_assessed] == [
        next(item.text for item in _BATCH if item.normalized_url == row.posting.normalized_url) for row in old.not_assessed
    ]
    assert _reasons(old) == _reasons(output)

    # The run reads answer the same from either shape: the reason, the posting (complete), the page's rows.
    new_view, old_view = _view(run_id, output), _view(run_id, old)
    assert [row.posting.normalized_url for row in old_view.rows] == [row.posting.normalized_url for row in new_view.rows]
    for posting in _KEPT:
        new_rows = run_reads.posting_rows(new_view, posting.normalized_url)
        old_rows = run_reads.posting_rows(old_view, posting.normalized_url)
        assert new_rows is not None and old_rows == new_rows
        detail = run_reads.posting_detail(run_id, new_rows)
        assert detail["row"]["posting"]["text"] == posting.text  # type: ignore[index]
    for url, row in new_view.not_assessed.items():
        # What a results page lists for the row (``results_page``): the posting without its text, and the reason.
        assert run_reads.grid_posting(row.posting.to_json()) == run_reads.grid_posting(old_view.not_assessed[url].posting.to_json())
        assert row.reason is old_view.not_assessed[url].reason


def test_the_full_read_answers_a_not_assessed_row_with_its_text(fx: ProfileFixtureGig, binding: _Binding) -> None:
    output = _assess(fx, _run_id(83), seal=True)
    old = AssessOutput.from_json(_with_text(output.to_json()))
    rows = tuple(PostingRowResult(item, RowOutcome.NEW) for item in _KEPT)

    served = with_row_text(output.not_assessed, rows)
    assert [item.to_json() for item in served] == [item.to_json() for item in old.not_assessed]
    # A row sealed with its text is answered as it is; a row the run has no text for stays without.
    assert with_row_text(old.not_assessed, rows) == old.not_assessed
    bare: tuple[PostingRowResult, ...] = tuple(PostingRowResult(posting_identity(item), RowOutcome.NEW) for item in _KEPT)
    assert with_row_text(output.not_assessed, bare) == output.not_assessed
    assert all(isinstance(item.posting, PostingRow) and item.posting.text is None for item in output.not_assessed)
