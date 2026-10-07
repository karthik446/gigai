"""0.1.11 N3: the pick eval calls ``pick.settle``, the product's one entry (``run_pick_eval.py --path settle``).

The same synthetic grid as ``test_pick_eval.py`` (6 postings x 3 master sizes x 9 master variations), every posting
with its synthetic assessment: the rows' cited lines are the rows' ``sources``, and the pick is a stand-in that ranks
every line as the code selector does (``tools/pick_probe.py``).  The selections are made in short-lived child
processes, as the other grid's are.

What holds in all 162 cells, and is a hard test here:

- H1 no mandatory requirement of the LABELS is left without a supporting line;
- H2 adding lines to a master never lowers coverage, strength or the must-keep lines shown;
- H4 every cell holds at most ``master_selection.MAX_PICK_BULLETS`` bullets (here: exactly that many), with no
  project printed without a bullet and the roles in date order; NO PAGE IS COUNTED (0.1.11.5 item 1c);
- H5 no met mandatory row of the assessment loses every line it names unless a conflict is reported;
- H6 no role or project the posting's title names is dropped whole unless a conflict is reported;
- the same lines in another ORDER give the same selection.

NOTHING IS A RECORDED EXCEPTION ANY MORE (0.1.11.5 item 1c).  While the pick was fitted to 2 pages, three cells lost a
line the reviewer's labels call must-keep or strong, each time because something else took the page's room: the Skills
line printed longer once regrouped (``agentic`` large, ``relay-02``), the header's reserve (``sel-5``: ``titlematch``
small -> medium, ``qui-01`` and T2's strength) and the heading lines of the roles with no bullet (``sel-6``:
``agentic`` medium -> large, ``relay-02``).  With a cap of bullets and no page, none of them takes a bullet's place:
all 162 cells are clean, and the test below says so by EQUALITY, so a cell that moves again is seen.

Regression protection on these cases, not a proof that every pick is right.
"""

from __future__ import annotations

import pytest

from tests.evals import run_pick_eval as ev

POSTINGS = ("agentic", "backend", "leadership", "sre", "titlematch", "weakfit")
H3 = "H3 order or grouping changed the pick"
H2 = "H2 adding lines lowered a check"


@pytest.mark.parametrize("posting", POSTINGS)
def test_settle_holds_the_hard_tests_in_every_cell_of_a_posting_with_its_assessment(posting: str) -> None:
    results = ev.run([posting], paths=[ev.SETTLE], tree=ev.REPO, assessed=True)
    assert len(results) == len(ev.SIZES) * len(ev.VARIATIONS) == 27
    assert not any(checks.error for checks in results.values()), {key: checks.error for key, checks in results.items() if checks.error}
    failures = ev.hard_failures(results)
    # Every hard test, with NO recorded exception (the module text): the cells that p15's header reserve, H1's heading
    # lines and the regrouped Skills line once moved (H2, H3) are clean, because nothing takes a bullet's place now.
    assert failures == {name: [] for name in failures}, failures
    # No conflict was needed anywhere, and every cell holds exactly the cap: these masters have more lines than it.
    assert all(checks.conflicts == 0 and checks.bullets == ev.MAX_PICK_BULLETS == 20 for checks in results.values())
    assert not any(checks.lost or checks.weak or checks.omitted or checks.cited_lost for checks in results.values())


def _bullets(final: dict) -> int:
    """The bullets a final selection shows under roles and projects (what the cap counts), for the medium master."""

    return sum(len(final["entries"].get(entry, ())) for entry in ev.master_case("medium", "base").entries)


def test_the_settle_path_is_the_products_function_and_says_who_picked() -> None:
    assessed = ev.payload(["agentic"], ["medium"], ["base"], [ev.SETTLE], assessed=True)
    plain = ev.payload(["agentic"], ["medium"], ["base"], [ev.SETTLE, "fallback"])
    key = ev.cell_key("agentic", "medium", "base")
    with_pick = ev.in_children(assessed, ev.REPO)["results"][key][ev.SETTLE]
    without = ev.in_children(plain, ev.REPO)["results"][key]
    # With an assessment: the stand-in pick, validated and capped by ``pick.settle``, which lays out nothing.
    assert (with_pick["picked_by"], with_pick["fallback"], with_pick["pick_conflicts"], with_pick["ready"]) == ("model", None, [], True)
    assert with_pick["layouts"] == 0 and not with_pick["empty_entries"] and with_pick["date_order"]
    assert _bullets(with_pick) == ev.MAX_PICK_BULLETS
    # Without one there is no pick: the code selector's selection, through the same function, with the reason.
    assert (without[ev.SETTLE]["picked_by"], without[ev.SETTLE]["fallback"]) == ("code", "no_pick")
    shown = lambda final: (final["summary"], final["entries"], final["other"], final["skills"])  # noqa: E731
    assert shown(without[ev.SETTLE]) == shown(without["fallback"])  # the fallback of 3.4 IS ``tailor_master.code_only``
    # A case may carry its own pick: unknown ids and a Skills line are dropped and recorded, the rest is settled.
    own = ev.payload(["agentic"], ["medium"], ["base"], [ev.SETTLE], assessed=True)
    lines = [line for bullets in with_pick["entries"].values() for line in bullets]
    own["cases"][0]["posting"] = {**own["cases"][0]["posting"], "pick": ["b-zzzzzz", "s-lang", *lines]}
    mine = ev.in_children(own, ev.REPO)["results"][key][ev.SETTLE]
    assert mine["picked_by"] == "model" and [problem["code"] for problem in mine["problems"]][:2] == ["unknown_id", "not_selectable"]
    assert _bullets(mine) == ev.MAX_PICK_BULLETS and set(lines) <= {line for bullets in mine["entries"].values() for line in bullets} | set(mine["other"])


def test_a_tree_without_settle_answers_an_error_for_that_path_and_the_cell_reads_as_failed() -> None:
    # What ``--baseline`` gives for a tree before 0.1.11 (it has no ``gigai.scout.pick``): an error by type, never a guess.
    case = ev.master_case("small", "base")
    checks = ev.check("weakfit", case, {"error": "ModuleNotFoundError"})
    assert checks.error == "ModuleNotFoundError" and ev.SETTLE not in ev.PATHS and ev.SETTLE == "settle"
