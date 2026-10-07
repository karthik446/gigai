"""0.1.11.5 ASSESS-01 part 4: what the low-rank box does to a run, said by the server from its own pool logic.

The dialog's box read as 50 MORE calls ("Assess the top 50 by rank of those too? ~50 model calls"). In code
``include_low_rank`` only puts the low-ranked postings back in the pool: one approval is still the top 50 by rank of
the whole pool. ``low_rank.included`` of the ask now says what the yes would assess: the pool, the batch, how many of
the batch are low-ranked, and ``changes_batch`` (false: the same postings as without it).

The scene (``test_rank_order_and_top_batch._scene``): seven not-assessed postings: ranked 90, 70, 50 (kept), 49 and
20 (low), and two not ranked yet (kept; they come after every ranked one). The rule is checked with small caps in
place of the 50, and the answer of the ask is checked against what an approval then really assesses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai.scout import scout_new
from gigai.scout.quick_assess import read_quick_assessment

from tests.behaviors.scout_pipeline.test_rank_order_and_top_batch import _scene, _these
from tests.support.posting_fixtures import build_postings_fixture

NOT_ASSESSED = ("r90", "r70", "r50", "r49", "r20", "unranked_a", "unranked_b")


def _assessed(fx, jobs: dict[str, str]) -> set[str]:
    return {name for name in NOT_ASSESSED if read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, jobs[name])}


@pytest.mark.parametrize(
    ("cap", "batch", "included", "taken"),
    [
        # The cap is filled by postings ranked 50 or more: the low-ranked ones change nothing.
        (3, 3, {"pool": 7, "batch": 3, "more_after": 4, "low_ranked_in_batch": 0, "changes_batch": False}, {"r90", "r70", "r50"}),
        # A posting not ranked yet comes after every ranked one: rank 49 takes its place in the run.
        (4, 4, {"pool": 7, "batch": 4, "more_after": 3, "low_ranked_in_batch": 1, "changes_batch": True}, {"r90", "r70", "r50", "r49"}),
        # Fewer postings than the cap: the run grows by the low-ranked ones.
        (50, 5, {"pool": 7, "batch": 7, "more_after": 0, "low_ranked_in_batch": 2, "changes_batch": True}, set(NOT_ASSESSED)),
    ],
)
def test_the_ask_says_what_the_low_rank_yes_would_assess_and_the_yes_assesses_exactly_that(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cap: int, batch: int, included: dict, taken: set[str]
) -> None:
    monkeypatch.setattr(scout_new, "BATCH_LIMIT", cap)
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    jobs = _scene(fx, monkeypatch)
    calls = fx.base.model.calls

    asked = _these(fx)

    assert asked["status"] == "ask" and fx.base.model.calls == calls
    assert (asked["question"]["to_assess"], asked["question"]["batch"], asked["low_rank"]["skipped"]) == (5, batch, 2)  # type: ignore[index]
    said = dict(asked["low_rank"]["included"])  # type: ignore[index]
    estimate = said.pop("estimate")
    assert said == included
    assert estimate["calls"] == included["batch"], "the estimate of the run with the low-ranked ones in the pool"

    done = _these(fx, approve=True, include_low_rank=True)

    assert done["assessed"]["requested"] == included["batch"] and fx.base.model.calls == calls + included["batch"]  # type: ignore[index]
    assert _assessed(fx, jobs) == taken, "the yes assessed other postings than the ask said"
    low_in_run = len(taken & {"r49", "r20"})
    assert low_in_run == included["low_ranked_in_batch"]
