"""0110-10-15: the pick eval's hard tests (``run_pick_eval.py``), on the FINAL selection of three code paths.

Deterministic, no model, synthetic fixtures only (``fixtures/pick``).  5 postings x 3 master sizes x 9 master
variations x 3 paths = 405 final selections, each checked on separate checks against a labelled requirement set:

- H1 no mandatory requirement the master can support is left without a supporting line, in any cell;
- H2 adding lines to a master never lowers mandatory coverage, evidence strength or the must-keep lines shown;
- H3 the same lines in another order give the same pick; the same skills grouped differently change no check;
- H4 every cell fits 2 pages, with no role printed without a bullet and the roles in date order.

Regression protection on these cases, not a proof.  The baseline (``sel-1``, 0.1.10.10) fails all four: the worker's
report holds the table (``python -m tests.evals.run_pick_eval --baseline <a git archive of v0.1.10.10>``).
"""

from __future__ import annotations

import json

import pytest

from gigai.scout.master_resume import parse_master
from gigai.scout.master_selection import is_old_role
from tests.evals import run_pick_eval as ev

POSTINGS = ("agentic", "backend", "leadership", "sre", "weakfit")
_RESULTS: dict[str, dict[tuple[str, str, str, str], ev.Checks]] = {}


def _results(posting: str) -> dict[tuple[str, str, str, str], ev.Checks]:
    if posting not in _RESULTS:
        _RESULTS[posting] = ev.run([posting])
    return _RESULTS[posting]


def test_the_fixtures_are_synthetic_and_every_label_names_a_line_the_master_holds() -> None:
    large = parse_master((ev.FIXTURES / "master.md").read_text(encoding="utf-8"))
    ids = set(large.items) | set(large.entries)
    expected, sizes, variations = (json.loads((ev.FIXTURES / name).read_text(encoding="utf-8")) for name in ("expected.json", "sizes.json", "variations.json"))
    assert "SYNTHETIC" in (ev.FIXTURES / "master.md").read_text(encoding="utf-8").splitlines()[0]
    assert all("SYNTHETIC" in spec["note"] for spec in (expected, sizes, variations)) and "TO BE REVIEWED by a person" in expected["note"]
    assert tuple(sorted(expected["postings"])) == POSTINGS == tuple(sorted(ev.postings()))
    assert {spec["type"] for spec in expected["postings"].values()} == {"agentic / AI platform", "backend platform", "SRE / infra", "leadership-heavy", "weak fit"}
    for name, spec in expected["postings"].items():
        text = ev.postings()[name]["text"]
        requirement_ids = [requirement["id"] for requirement in spec["requirements"]]
        assert len(requirement_ids) == len(set(requirement_ids)) >= 7
        for requirement in spec["requirements"]:
            assert requirement["text"] in text, (name, requirement["id"], "a requirement is a line of the posting, word for word")
            assert set(requirement["strong"]) | set(requirement["support"]) <= set(large.items), (name, requirement["id"])
            assert not set(requirement["strong"]) & set(requirement["support"])
        assert spec["must_keep"] and all(set(group) <= set(large.items) for group in spec["must_keep"])
        # The case the ticket asks for: an OLDER role holds the only labelled evidence of a mandatory requirement.
        for requirement_id in spec.get("older_role_only", ()):
            requirement = next(item for item in spec["requirements"] if item["id"] == requirement_id)
            holders = {large.items[line].entry_id for line in (*requirement["strong"], *requirement["support"][:0])}
            assert requirement["mandatory"] and holders and all(is_old_role(large.entries[entry], ev.date_of(sizes["today"])) for entry in holders), (name, requirement_id)
    assert sum(bool(spec.get("older_role_only")) for spec in expected["postings"].values()) >= 3
    # Sizes are the large master without some ids; every variation line is new, and a copy names a line that exists.
    assert all(set(spec["drop"]) <= ids for spec in sizes["sizes"].values())
    counts = {size: len(ev.master_case(size, "base").ids) for size in ev.SIZES}
    assert counts["small"] < counts["medium"] < counts["large"] == len(ids)
    added = [line for kind in ev.ADDING for line in variations[kind]]
    assert len({line["id"] for line in added}) == len(added) and not {line["id"] for line in added} & ids
    assert all(line["like"] in large.items for line in added if line.get("like"))
    known = {name: {requirement["id"] for requirement in spec["requirements"]} for name, spec in expected["postings"].items()}
    assert all(set(labels) <= known[name] for line in added for name, labels in (line.get("labels") or {}).items())
    groups = {name: {"/".join(group) for group in spec["must_keep"]} for name, spec in expected["postings"].items()}
    assert all(set(names) <= groups[name] for line in added for name, names in (line.get("same_fact_as") or {}).items())
    assert max(len(line["text"]) for line in variations["long"]) > 500 > max(len(item.text) for item in large.items.values())


def test_every_variation_is_the_master_it_says_it_is() -> None:
    base = ev.master_case("large", "base")
    for variation in ev.ADDING:
        case = ev.master_case("large", variation)
        assert base.ids < case.ids and set(case.added) == set(case.ids - base.ids) - {"p-plot"}
    for variation in ev.PERMUTED:
        case = ev.master_case("large", variation)
        assert case.ids == base.ids and case.markdown != base.markdown
        one, two = parse_master(case.markdown), parse_master(base.markdown)
        assert {item.id: item.text for item in one.items.values() if item.kind != "skills"} == {item.id: item.text for item in two.items.values() if item.kind != "skills"}
        assert sorted(one.skills()) == sorted(two.skills()) and [item.id for item in one.items.values()] != [item.id for item in two.items.values()]
    regrouped = parse_master(ev.master_case("large", ev.REGROUPED).markdown)
    assert ev.atoms(regrouped.skills()) == ev.atoms(parse_master(base.markdown).skills()) and len(regrouped.skills()) > len(parse_master(base.markdown).skills())
    # A copied line carries its original's labels; a useful line its own.
    requirements, groups = ev.labels("agentic", ev.master_case("large", "duplicates"))
    assert next(requirement for requirement in requirements if requirement.id == "A1").lines["dup-07"] == 2 and "dup-07" in groups["hal-01"]
    requirements, groups = ev.labels("agentic", ev.master_case("large", "useful"))
    assert next(requirement for requirement in requirements if requirement.id == "A2").lines["use-01"] == 2 and "use-02" in groups["relay-02"]
    # A size that does not hold a line does not count the requirement only that line supports.
    requirements, _groups = ev.labels("sre", ev.master_case("small", "base"))
    assert next(requirement for requirement in requirements if requirement.id == "S5").lines == {}


@pytest.mark.parametrize("posting", POSTINGS)
def test_the_hard_tests_hold_in_every_cell_of_a_posting(posting: str) -> None:
    results = _results(posting)
    assert len(results) == len(ev.SIZES) * len(ev.VARIATIONS) * len(ev.PATHS) == 81
    assert not any(checks.error for checks in results.values())
    failures = ev.hard_failures(results)
    assert failures == {name: [] for name in failures}, "\n".join(item for found in failures.values() for item in found)
    # No conflict was needed anywhere: everything mandatory fitted.
    assert all(checks.conflicts == 0 for checks in results.values())


@pytest.mark.parametrize(("posting", "requirement", "line", "sizes"), [
    ("backend", "B6", "pel-01", ("small", "medium", "large")),
    ("sre", "S6", "ost-05", ("medium", "large")),
    ("weakfit", "W4", "ost-07", ("large",)),
])
def test_an_older_role_that_holds_the_only_evidence_keeps_that_line(posting: str, requirement: str, line: str, sizes: tuple[str, ...]) -> None:
    results = _results(posting)
    for size in sizes:
        for path in ev.PATHS:
            checks = results[(posting, size, "base", path)]
            assert line in checks.shown and checks.levels[requirement] == 2, (size, path)
            # ... while, in a master that does not fit two pages, lines of recent roles and projects were left out.
            if size != "small":
                newer = {item for item in ev.master_case(size, "base").ids if item.startswith(("hal-", "qui-", "bra-", "loom-", "eval-", "pgq-", "trail-", "relay-"))}
                assert len(newer - checks.shown) >= 5, (size, path)


def test_the_agentic_posting_does_not_get_worse_as_the_master_grows() -> None:
    """The operator's case (0110-10-15): richer project content pushed strong, relevant lines out. Medium -> large:
    every mandatory requirement keeps its strongest line, and the new project's multi-agent and durable-runtime
    lines are shown."""

    results = _results("agentic")
    for path in ev.PATHS:
        medium, large = results[("agentic", "medium", "base", path)], results[("agentic", "large", "base", path)]
        assert (medium.lost, medium.weak, medium.omitted) == ((), (), ()) == (large.lost, large.weak, large.omitted), path
        assert {"relay-01", "relay-02", "hal-01", "hal-03", "loom-01"} <= large.shown, path
        assert large.skills == 14 and large.skills_left_out == 0, "all 14 skill groups are shown"


def test_the_same_probe_runs_in_another_checkout_s_tree() -> None:
    """``--baseline``: the probe file run with another tree on ``PYTHONPATH`` (here this checkout's own) gives that tree's answer."""

    request = ev.payload(["weakfit"], ["small"], ["base"], ["select"])
    here = ev.pick_probe.probe(request)
    there = ev.pick_probe.run_in_tree(request, ev.REPO)
    assert there == json.loads(json.dumps(here)) and there["selector_version"] == "sel-2"
    with pytest.raises(ValueError, match="holds no src/gigai"):
        ev.pick_probe.run_in_tree(request, ev.FIXTURES)
