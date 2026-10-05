"""0110-10-15: the pick eval's hard tests (``run_pick_eval.py``), on the FINAL selection of three code paths.

Deterministic, no model, synthetic fixtures only (``fixtures/pick``).  6 postings x 3 master sizes x 9 master
variations x 3 paths = 486 final selections, each checked on separate checks against a labelled requirement set:

- H1 no mandatory requirement the master can support is left without a supporting line, in any cell;
- H2 adding lines to a master never lowers mandatory coverage, evidence strength or the must-keep lines shown;
- H3 the same lines in another order give the same pick; the same skills grouped differently change no check;
- H4 every cell fits 2 pages, with no role printed without a bullet and the roles in date order.

Regression protection on these cases, not a proof.  The baseline (``sel-1``, 0.1.10.10) fails all four: the worker's
report holds the table (``python -m tests.evals.run_pick_eval --baseline <a git archive of v0.1.10.10>``).

WITH AN ASSESSMENT (the real-data gate of 0.1.10.11): the same 486 selections again, every posting carrying its
synthetic assessment (``assessments.json``), whose rows cite master lines BY MEANING.  H1-H4 hold there too, and

- H5 no met mandatory row of the assessment loses every line it cites unless a conflict is reported.

``sel-2`` (which matched by words alone) fails H5 in 90 of those cells: ``python -m tests.evals.run_pick_eval
--assessed --baseline <a git archive of ddd5a58a>``.

A TITLE THAT NAMES AN ENTRY (0.1.10.11 PICK v5), with and without an assessment:

- H6 no role or project the posting's title names is dropped whole unless a conflict is reported.

The posting ``titlematch`` is the case (its title names the agent-runtime projects; its assessment cites lines of the
roles only).  ``sel-3`` fails H6 in all 162 of its cells: ``python -m tests.evals.run_pick_eval --posting titlematch
[--assessed] --baseline <a git archive of 567bd940>``.
"""

from __future__ import annotations

import json

import pytest

from gigai.scout import master_selection as ms
from gigai.scout.master_resume import parse_master
from gigai.scout.master_selection import is_old_role
from tests.evals import run_pick_eval as ev

POSTINGS = ("agentic", "backend", "leadership", "sre", "titlematch", "weakfit")
_RESULTS: dict[tuple[str, bool], dict[tuple[str, str, str, str], ev.Checks]] = {}


def _results(posting: str, *, assessed: bool = False) -> dict[tuple[str, str, str, str], ev.Checks]:
    if (posting, assessed) not in _RESULTS:
        _RESULTS[(posting, assessed)] = ev.run([posting], assessed=assessed)
    return _RESULTS[(posting, assessed)]


def test_the_fixtures_are_synthetic_and_every_label_names_a_line_the_master_holds() -> None:
    large = parse_master((ev.FIXTURES / "master.md").read_text(encoding="utf-8"))
    ids = set(large.items) | set(large.entries)
    expected, sizes, variations = (json.loads((ev.FIXTURES / name).read_text(encoding="utf-8")) for name in ("expected.json", "sizes.json", "variations.json"))
    assert "SYNTHETIC" in (ev.FIXTURES / "master.md").read_text(encoding="utf-8").splitlines()[0]
    assert all("SYNTHETIC" in spec["note"] for spec in (expected, sizes, variations)) and "TO BE REVIEWED by a person" in expected["note"]
    assert tuple(sorted(expected["postings"])) == POSTINGS == tuple(sorted(ev.postings()))
    assert {spec["type"] for spec in expected["postings"].values()} == {
        "agentic / AI platform", "backend platform", "SRE / infra", "leadership-heavy", "weak fit", "the title names a project",
    }
    for name, spec in expected["postings"].items():
        text = ev.postings()[name]["text"]
        requirement_ids = [requirement["id"] for requirement in spec["requirements"]]
        assert len(requirement_ids) == len(set(requirement_ids)) >= 7
        for requirement in spec["requirements"]:
            assert requirement["text"] in text, (name, requirement["id"], "a requirement is a line of the posting, word for word")
            assert set(requirement["strong"]) | set(requirement["support"]) <= set(large.items), (name, requirement["id"])
            assert not set(requirement["strong"]) & set(requirement["support"])
        assert spec["must_keep"] and all(set(group) <= set(large.items) for group in spec["must_keep"])
        # The entries a reviewer says the title names are roles or projects of the master.
        assert all(large.entries[entry].section in ("experience", "projects") for entry in spec["title_entries"]), name
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
    assert there == json.loads(json.dumps(here)) and there["selector_version"] == "sel-4"
    with pytest.raises(ValueError, match="holds no src/gigai"):
        ev.pick_probe.run_in_tree(request, ev.FIXTURES)


# --- with an assessment: coverage comes from the lines it cites (the real-data gate of 0.1.10.11) --------------


def _row(posting: str, requirement: str, case: ev.MasterCase) -> dict[str, object]:
    return next(row for row in ev.cited(posting, case) if row["text"] == requirement)


def test_the_synthetic_assessments_cite_master_lines_and_hold_the_five_cases_the_gate_found_missing() -> None:
    spec = json.loads((ev.FIXTURES / "assessments.json").read_text(encoding="utf-8"))
    assert "SYNTHETIC" in spec["note"] and tuple(sorted(spec["postings"])) == POSTINGS
    today = ev.date_of(json.loads((ev.FIXTURES / "sizes.json").read_text(encoding="utf-8"))["today"])
    large = parse_master(ev.master_case("large", "base").markdown)
    for name, rows in spec["postings"].items():
        text = ev.postings()[name]["text"]
        assert all(row["requirement"] in text for row in rows), (name, "a row's requirement is a line of the posting, word for word")
        traced = ev.cited(name, ev.master_case("large", "base"))
        # Every row that quotes evidence traces to a line of the master, by the product's own rule; a row with none is left out.
        assert len(traced) == sum(bool(row["evidence"]) for row in rows) >= 7 and all(set(row["lines"]) <= set(large.items) for row in traced)  # type: ignore[arg-type]
        assert [row["id"] for row in traced] == [f"r{place}" for place, row in enumerate(rows, 1) if row["evidence"]]
    assert set(spec["shapes"]) == {"no_shared_words", "older_role_only", "same_word_one_cited", "long_line", "one_line_four_rows"}

    def master_of(case: dict) -> tuple[ev.MasterCase, object]:
        found = ev.master_case(case["sizes"][-1], case["variation"])
        return found, parse_master(found.markdown)

    # A cited line that shares NO word with its requirement: the selector's word rule does not see it at all.
    for case in spec["shapes"]["no_shared_words"]:
        found, master = master_of(case)
        row = _row(case["posting"], case["requirement"], found)
        assert row["lines"] == [case["line"]] and row["mandatory"] and row["met"]
        assert not ms._words(case["requirement"]) & ms._words(master.items[case["line"]].text)  # noqa: SLF001 - the selector's own words
        assert case["line"] not in ms.word_supporters(master, case["requirement"])
    # A cited line in an OLDER role that is the only evidence of its row.
    for case in spec["shapes"]["older_role_only"]:
        found, master = master_of(case)
        row = _row(case["posting"], case["requirement"], found)
        assert row["lines"] == [case["line"]] and is_old_role(master.entries[master.items[case["line"]].entry_id], today)
    # Two (here three) lines carry the same words; ONE is cited, and by words the selector prefers another.
    for case in spec["shapes"]["same_word_one_cited"]:
        found, master = master_of(case)
        row = _row(case["posting"], case["requirement"], found)
        by_words = ms.word_supporters(master, case["requirement"])
        assert row["lines"] == [case["line"]] and set(case["others"]) <= set(by_words) and by_words[0] != case["line"]
    # A cited line that is long (the selector counts a line over LONG_LINE_CHARS as costly), the row's only citation.
    for case in spec["shapes"]["long_line"]:
        found, master = master_of(case)
        row = _row(case["posting"], case["requirement"], found)
        assert row["lines"] == [case["line"]] and len(master.items[case["line"]].text) > ms.LONG_LINE_CHARS
    # One line cited for four rows.
    for case in spec["shapes"]["one_line_four_rows"]:
        found, _master = master_of(case)
        citing = [row for row in ev.cited(case["posting"], found) if case["line"] in row["lines"]]  # type: ignore[operator]
        assert len(citing) == case["rows"] == 4 and all(row["mandatory"] and row["met"] for row in citing)
        assert any(row["lines"] == [case["line"]] for row in citing) and any(len(row["lines"]) > 1 for row in citing)  # type: ignore[arg-type]


@pytest.mark.parametrize("posting", POSTINGS)
def test_with_an_assessment_no_met_mandatory_row_loses_every_line_it_cites_and_the_four_hard_tests_still_hold(posting: str) -> None:
    results = _results(posting, assessed=True)
    assert len(results) == 81 and not any(checks.error for checks in results.values())
    failures = ev.hard_failures(results)
    assert failures == {name: [] for name in failures}, "\n".join(item for found in failures.values() for item in found)
    # Stronger than H5 on this grid: every met mandatory row keeps a cited line in every cell, and no conflict was needed.
    assert all(checks.cited_met and not checks.cited_lost and checks.conflicts == 0 for checks in results.values())


def test_each_of_the_five_cases_keeps_its_cited_line_on_every_path() -> None:
    spec = json.loads((ev.FIXTURES / "assessments.json").read_text(encoding="utf-8"))
    checked = 0
    for name, cases in spec["shapes"].items():
        for case in cases:
            results = _results(case["posting"], assessed=True)
            for size in case["sizes"]:
                for path in ev.PATHS:
                    assert case["line"] in results[(case["posting"], size, case["variation"], path)].shown, (name, size, path)
                    checked += 1
    assert checked == 39
    # Without the assessment the same master and posting do NOT show those lines (the cases are real: matching by
    # words cuts them), in the largest master of each case.
    for name, cases in spec["shapes"].items():
        for case in cases:
            plain = _results(case["posting"])[(case["posting"], case["sizes"][-1], case["variation"], "select")]
            assert case["line"] not in plain.shown, (name, "the case would pass without the fix")


def test_a_selector_that_does_not_read_citations_fails_the_cited_check() -> None:
    """The check itself: a final selection made by words alone, scored against the assessment's rows, loses rows."""

    case = ev.master_case("large", "base")
    plain = ev.pick_probe.probe(ev.payload(["leadership"], ["large"], ["base"], ["select"]))["results"][ev.cell_key("leadership", "large", "base")]["select"]
    checks = ev.check("leadership", case, plain, ev.cited("leadership", case))
    assert len(checks.cited_lost) == 2 and checks.cited_silent == checks.cited_lost
    failures = ev.hard_failures({("leadership", "large", "base", "select"): checks})
    assert len(failures["H5 a met mandatory row lost every line it cites, with no conflict reported"]) == 1


# --- a title that names an entry: the entry keeps its best line (0.1.10.11 PICK v5) ---------------------------


def test_the_title_match_case_is_a_posting_whose_title_names_projects_that_no_row_of_its_assessment_cites() -> None:
    spec = json.loads((ev.FIXTURES / "expected.json").read_text(encoding="utf-8"))["postings"]["titlematch"]
    posting = ev.postings()["titlematch"]
    case = ev.master_case("large", "base")
    large = parse_master(case.markdown)
    subject = ms._title_subject(posting["title"])  # noqa: SLF001 - the selector's own reading of a title
    assert subject == {"backend", "agent", "runtim"} and spec["title_entries"] == ["p-loom", "p-relay"] == list(ev.title_entries("titlematch", case))
    for entry in spec["title_entries"]:
        # The entry's own heading says what the title says ...
        assert len(subject & ms._title_subject(large.entries[entry].heading)) == 2, entry  # noqa: SLF001
        # ... no row of the assessment cites a line of it (every cited line is a line of a role) ...
        assert not any(set(row["lines"]) & set(large.entries[entry].bullets) for row in ev.cited("titlematch", case)), entry  # type: ignore[arg-type]
        # ... and no must-keep group names a line of it: the eval's check 3 does not ask for it either.
        assert not any(set(group) & set(large.entries[entry].bullets) for group in spec["must_keep"]), entry
    cited = {line for row in ev.cited("titlematch", case) for line in row["lines"]}  # type: ignore[union-attr]
    assert all(large.items[line].entry_id in ("r-hal", "r-qui", "r-bra") for line in cited)
    # "Two roles that fill the page with cited lines": the two most recent roles are cited whole.
    assert set(large.entries["r-hal"].bullets) | set(large.entries["r-qui"].bullets) <= cited
    # The medium master holds the first project only, the small one too.
    assert ev.title_entries("titlematch", ev.master_case("medium", "base")) == ("p-loom",) == ev.title_entries("titlematch", ev.master_case("small", "base"))


@pytest.mark.parametrize("assessed", [False, True])
def test_an_entry_the_posting_s_title_names_keeps_a_line_in_every_cell_and_no_conflict_is_needed(assessed: bool) -> None:
    results = _results("titlematch", assessed=assessed)
    assert len(results) == 81
    for (_posting, size, variation, path), checks in results.items():
        assert checks.title_entries and not checks.title_dropped and checks.conflicts == 0, (size, variation, path)
        # The first project's best line is the one that says what the title says (a copy of it, where the master holds a better twin).
        assert {"loom-01", "near-06"} & checks.shown, (size, variation, path)
        # It costs no requirement its line: the checks the grid already had hold beside it.
        assert (checks.lost, checks.weak, checks.omitted, checks.cited_lost) == ((), (), (), ()), (size, variation, path)
    # The agentic posting's title names the same projects; they were shown before and still are.
    assert not any(checks.title_dropped for checks in _results("agentic", assessed=assessed).values())


def test_a_selection_that_drops_the_entry_the_title_names_fails_the_title_check_unless_a_conflict_names_it() -> None:
    """The check itself, on a final selection edited by hand: the project gone, with and without the conflict."""

    case = ev.master_case("medium", "base")
    final = ev.pick_probe.probe(ev.payload(["titlematch"], ["medium"], ["base"], ["select"]))["results"][ev.cell_key("titlematch", "medium", "base")]["select"]
    assert final["entries"]["p-loom"] and ev.check("titlematch", case, final).title_dropped == ()
    dropped = {**final, "entries": {entry: bullets for entry, bullets in final["entries"].items() if entry != "p-loom"}}  # type: ignore[union-attr]
    checks = ev.check("titlematch", case, dropped)
    assert checks.title_dropped == checks.title_silent == ("p-loom",) and "p-loom" in checks.zero_entries
    failures = ev.hard_failures({("titlematch", "medium", "base", "select"): checks})
    assert failures["H6 an entry the posting's title names shows no line, with no conflict reported"] == ["titlematch/medium/base/select: p-loom"]
    reported = {**dropped, "conflicts": [{"kind": "title_entry", "ids": ["p-loom", "loom-01"], "reason": "does not fit"}]}
    checks = ev.check("titlematch", case, reported)
    assert checks.title_dropped == ("p-loom",) and checks.title_silent == ()
    assert not ev.hard_failures({("titlematch", "medium", "base", "select"): checks})["H6 an entry the posting's title names shows no line, with no conflict reported"]
