"""0.1.11 N3: the pick eval calls ``pick.settle``, the product's one entry (``run_pick_eval.py --path settle``).

The same synthetic grid as ``test_pick_eval.py`` (6 postings x 3 master sizes x 9 master variations), every posting
with its synthetic assessment: the rows' cited lines are the rows' ``sources``, and the pick is a stand-in that ranks
every line as the code selector does (``tools/pick_probe.py``).  The selections are made in short-lived child
processes, as the other grid's are.

What holds in all 162 cells, and is a hard test here:

- H1 no mandatory requirement of the LABELS is left without a supporting line;
- H2 adding lines to a master never lowers coverage, strength or the must-keep lines shown;
- H4 every cell fits 2 pages, with no role printed without a bullet and the roles in date order;
- H5 no met mandatory row of the assessment loses every line it names unless a conflict is reported;
- H6 no role or project the posting's title names is dropped whole unless a conflict is reported;
- the same lines in another ORDER give the same selection.

What does not, and is said here instead of hidden: with the same skills grouped differently the Skills line prints at
another length, and in ONE cell (``agentic`` on the large master) a line the reviewer's labels call must-keep, which
no row of the assessment names, is the last line that no longer fits.  ``settle`` protects what the assessment names
and what the profile pins; a label it is never given is not a pin.

Regression protection on these cases, not a proof that every pick is right.
"""

from __future__ import annotations

import pytest

from tests.evals import run_pick_eval as ev

POSTINGS = ("agentic", "backend", "leadership", "sre", "titlematch", "weakfit")
#: The one cell where regrouping the skills lowers a check under ``settle`` (the module text).
KNOWN_REGROUPED = {"agentic/large/regrouped/settle: must-keep dropped: relay-02"}
H3 = "H3 order or grouping changed the pick"
H2 = "H2 adding lines lowered a check"
#: 0.1.11.3 item 15 (``sel-5``): the page estimate keeps room for the PDF's header (one more line than ``sel-4``), so
#: the pick prints one line less. In ONE place the line that goes is a line the labels call strong: the medium
#: master's ``qui-01`` (T2 stays covered, by ``hal-06``, which the labels call support). A recorded exception, by the
#: coordinator's decision (2026-10-06): the cut order is unchanged here; protecting a row's label-strong source is a
#: follow-up ticket.
KNOWN_HEADER_ROOM = {"titlematch/small -> medium/base/settle: weaker evidence: T2", "titlematch/small -> medium/base/settle: must-keep dropped: qui-01"}


@pytest.mark.parametrize("posting", POSTINGS)
def test_settle_holds_the_hard_tests_in_every_cell_of_a_posting_with_its_assessment(posting: str) -> None:
    results = ev.run([posting], paths=[ev.SETTLE], tree=ev.REPO, assessed=True)
    assert len(results) == len(ev.SIZES) * len(ev.VARIATIONS) == 27
    assert not any(checks.error for checks in results.values()), {key: checks.error for key, checks in results.items() if checks.error}
    failures = ev.hard_failures(results)
    regrouped = {item for item in failures[H3] if f"/{ev.REGROUPED}/" in item}
    assert regrouped <= KNOWN_REGROUPED, regrouped
    assert set(failures[H2]) <= KNOWN_HEADER_ROOM, failures[H2]
    assert {name: found for name, found in failures.items() if name not in (H2, H3)} == {name: [] for name in failures if name not in (H2, H3)}, failures
    assert [item for item in failures[H3] if item not in regrouped] == [], failures[H3]  # a permuted master: the same selection
    # No conflict was needed anywhere, and every cell is the two pages the fit is for.
    assert all(checks.conflicts == 0 and checks.pages == 2 for checks in results.values())


def test_the_settle_path_is_the_products_function_and_says_who_picked() -> None:
    assessed = ev.payload(["agentic"], ["medium"], ["base"], [ev.SETTLE], assessed=True)
    plain = ev.payload(["agentic"], ["medium"], ["base"], [ev.SETTLE, "fallback"])
    key = ev.cell_key("agentic", "medium", "base")
    with_pick = ev.in_children(assessed, ev.REPO)["results"][key][ev.SETTLE]
    without = ev.in_children(plain, ev.REPO)["results"][key]
    # With an assessment: the stand-in pick, validated and fitted by ``pick.settle``.
    assert (with_pick["picked_by"], with_pick["fallback"], with_pick["pick_conflicts"], with_pick["ready"]) == ("model", None, [], True)
    assert with_pick["pages"] == 2 and with_pick["layouts"] > 0 and not with_pick["empty_entries"] and with_pick["date_order"]
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
    assert mine["pages"] == 2 and set(lines) <= {line for bullets in mine["entries"].values() for line in bullets} | set(mine["other"])


def test_a_tree_without_settle_answers_an_error_for_that_path_and_the_cell_reads_as_failed() -> None:
    # What ``--baseline`` gives for a tree before 0.1.11 (it has no ``gigai.scout.pick``): an error by type, never a guess.
    case = ev.master_case("small", "base")
    checks = ev.check("weakfit", case, {"error": "ModuleNotFoundError"})
    assert checks.error == "ModuleNotFoundError" and ev.SETTLE not in ev.PATHS and ev.SETTLE == "settle"
