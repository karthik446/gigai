"""0.1.11 (orchestrator #35): the title-entry floor belongs to the code selector, not to a model's pick.

0.1.10.11 PICK v5: an entry the posting's TITLE names keeps its best line. That is right for the code selector, which
matches words. A valid model pick is a judgement ("Agent Platform" in a job about endpoint agents is not the AI-agent
project), and the floor must not override it: ``select(title_floor=False)`` is what ``pick.settle`` builds a pick on.
Synthetic data only (the pick eval's invented master and its ``titlematch`` posting, the case the floor was made for).
"""

from __future__ import annotations

from datetime import date

from gigai.scout.master_resume import parse_master
from gigai.scout.master_selection import SelectionPosting, SelectionProfile, select
from tests.evals import run_pick_eval as pick_eval

TODAY = date(2026, 10, 3)


def _select(**kwargs):
    master = parse_master(pick_eval.master_case("large", "base").markdown)
    post = pick_eval.postings()["titlematch"]
    posting = SelectionPosting(post["title"], post["text"], post["company"], post["location"])
    return select(master, SelectionProfile(titles=("Staff Software Engineer",)), posting, today=TODAY, max_bullets=None, fill=False, **kwargs)


def test_the_code_selector_keeps_the_best_line_of_an_entry_the_title_names() -> None:
    selected = _select()
    assert selected.title_entries, "the title names the agent runtime: the selector floors its entries"
    for entry_id, line_id in selected.title_entries.items():
        assert line_id in selected.entries.get(entry_id, ()), (entry_id, line_id)


def test_a_model_picks_base_applies_no_title_floor() -> None:
    selected = _select(title_floor=False)
    assert selected.title_entries == {}
    assert not any(reason.code == "title_entry" for reason in selected.lines)
