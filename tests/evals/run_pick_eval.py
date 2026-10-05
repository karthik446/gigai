"""The pick eval (0110-10-15): does the selector's FINAL selection keep what a posting needs as the master grows?

Deterministic, no model, synthetic fixtures only (``fixtures/pick``: an invented person).  For every posting
(5 job types and the title-match case) x master size (small, medium, large) x master variation, the final selection of three code paths
(``tools/pick_probe.py``: ``select``, ``fallback``, ``tailor_copy``) is scored against a labelled set
(``expected.json``: per posting a requirement list with the master lines that support each one) on SEPARATE
checks.  There is no blended score: a lost requirement is never offset by more skills or bullets.

The checks, per cell:

1. ``coverage``: every MANDATORY requirement the master of that cell can support keeps at least one supporting
   line.  ``lost`` names the ones that kept none.
2. ``strength``: for every supportable requirement, the level of the best line kept: 2 a ``strong`` line,
   1 a ``support`` line, 0 none.  ``weak`` names the mandatory ones below the best level the master offers.
3. ``must_keep``: the reviewer's must-keep groups with no line shown (``omitted``).
4. ``page_fit``: at most 2 pages, no role or project printed without a bullet, roles in date order.

Reported beside them, never part of a verdict on 1-3: the skill groups shown, the conflicts the result reports and
the roles and projects of the master shown with NO line (``zero_entries``; most are irrelevant ones, as they should be).

The variations of a master (``variations.json``): ``irrelevant``, ``duplicates``, ``near_duplicates``, ``long``
and ``useful`` ADD lines; ``permuted-*`` is the same master in another order; ``regrouped`` is the same skills
grouped differently.  The hard tests (``test_pick_eval.py``):

- H1 no mandatory requirement is lost in any cell;
- H2 adding lines never lowers checks 1-3 (small -> medium -> large, and base -> each adding variation);
- H3 a permuted master gives the same pick; the same skills grouped differently read the same keywords from the
  posting, show the same skills and lower none of checks 1-3 (the Skills line prints at another length, so the last
  line that fits may differ);
- H4 every cell fits the page constraint.

WITH AN ASSESSMENT (``--assessed``; ``assessments.json``; the real-data gate of 0.1.10.11).  A stored assessment
maps a requirement to master lines BY MEANING: the line it cites may share no word with the requirement.  In this
mode every posting carries its synthetic assessment: the rows' evidence quotes are traced to the cell's master with
the product's own rule (``assess_master.cited_requirements``) and passed to the selector as ``cited``.  One more
check, per cell:

5. ``cited``: every MET MANDATORY row of the assessment that cites a line of this master keeps at least one of the
   lines it cites.  ``cited_lost`` names the rows that kept none, ``cited_silent`` those of them no reported
   conflict names.  Rows that are unclear or nice-to-have are counted beside it (``cited_other_lost``).

- H5 no met mandatory row loses every line it cites unless a conflict is reported; H1-H4 hold in this mode too.

The cases (``shapes`` in ``assessments.json``): a cited line that shares no word with its requirement; a cited line
in an older role that is the only evidence; two lines carrying the same words of which one is cited; a cited line
that is long; one line cited for four rows.

A TITLE THAT NAMES AN ENTRY (0.1.10.11 PICK v5; ``title_entries`` in ``expected.json``).  A posting's title says
what the job is about, and a role or project whose own heading says the same is the last thing a resume for it should
lose.  One more check, per cell, with and without an assessment:

6. ``title``: every entry the labels say the posting's title names, that this master holds, shows at least one line.
   ``title_dropped`` names the ones that show none, ``title_silent`` those of them no ``title_entry`` conflict names.

- H6 no entry the title names is dropped whole unless a conflict is reported.

The posting ``titlematch`` is the case: its title names the agent-runtime projects, its assessment cites lines of the
roles only (the two most recent roles are cited whole), and the projects' lines state no number.  ``sel-3`` shows no
line of them in any size; 0.1.10.10 showed three.

This is regression protection on these cases, not a proof that every pick is right.

    uv run --extra test python -m tests.evals.run_pick_eval                 # the table for this checkout
    uv run --extra test python -m tests.evals.run_pick_eval --assessed      # the same with each posting's assessment
    uv run --extra test python -m tests.evals.run_pick_eval --baseline OLD  # the same for an older checkout (side by side)
    uv run --extra test python -m tests.evals.run_pick_eval --json out.json --posting agentic --path select
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import random
import re
import sys

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools import pick_probe  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pick"
SIZES: tuple[str, ...] = ("small", "medium", "large")
ADDING: tuple[str, ...] = ("irrelevant", "duplicates", "near_duplicates", "long", "useful")
PERMUTED: tuple[str, ...] = ("permuted-1", "permuted-2")
REGROUPED = "regrouped"
VARIATIONS: tuple[str, ...] = ("base", *ADDING, *PERMUTED, REGROUPED)
PATHS = pick_probe.PATHS
LEVELS = {"strong": 2, "support": 1}

_ID = re.compile(r"\s*<!-- id:(\S+) -->\s*\Z")


def date_of(text: str):
    from datetime import date

    return date.fromisoformat(text)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def postings() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for path in sorted((FIXTURES / "postings").glob("*.md")):
        head, _, body = path.read_text(encoding="utf-8").partition("\n\n")
        meta = dict(line.split(": ", 1) for line in head.splitlines())
        out[path.stem] = {"title": meta["title"], "company": meta["company"], "location": meta.get("location", ""), "text": body.strip() + "\n"}
    return out


# --- a master as plain data, so a variation is a list edit -----------------------------------------


@dataclass
class _Entry:
    id: str
    heading: str
    sublines: list[str]
    bullets: list[tuple[str, str]]  # (id, text)


@dataclass
class _Doc:
    """A master as lists: sections in order; an entry section holds entries, any other holds ``(id, text)`` lines."""

    marker: str
    order: list[str] = field(default_factory=list)
    entries: dict[str, list[_Entry]] = field(default_factory=dict)
    lines: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    def ids(self) -> set[str]:
        found = {item_id for items in self.lines.values() for item_id, _text in items}
        for entries in self.entries.values():
            for entry in entries:
                found |= {entry.id, *(item_id for item_id, _text in entry.bullets)}
        return found

    def text_of(self, item_id: str) -> str | None:
        for items in self.lines.values():
            for found, text in items:
                if found == item_id:
                    return text
        for entries in self.entries.values():
            for entry in entries:
                for found, text in entry.bullets:
                    if found == item_id:
                        return text
        return None

    def entry(self, entry_id: str) -> _Entry | None:
        return next((entry for entries in self.entries.values() for entry in entries if entry.id == entry_id), None)

    def markdown(self) -> str:
        out = [self.marker, ""]
        for section in self.order:
            out += [f"## {section.capitalize()}", ""]
            if section in self.entries:
                for entry in self.entries[section]:
                    out += [f"### {entry.heading} <!-- id:{entry.id} -->", *entry.sublines]
                    out += [f"- {text} <!-- id:{item_id} -->" for item_id, text in entry.bullets] + [""]
            else:
                out += [f"- {text} <!-- id:{item_id} -->" for item_id, text in self.lines[section]] + [""]
        return "\n".join(out).rstrip("\n") + "\n"


def _parse(markdown: str) -> _Doc:
    raw = markdown.splitlines()
    doc = _Doc(marker=raw[0])
    section = ""
    for line in raw[1:]:
        if not line.strip():
            continue
        found = _ID.search(line)
        item_id = found.group(1) if found else ""
        text = _ID.sub("", line).strip()
        if text.startswith("## "):
            section = text[3:].strip().lower()
            doc.order.append(section)
            if section in ("experience", "projects", "education"):
                doc.entries[section] = []
            else:
                doc.lines[section] = []
        elif text.startswith("### "):
            doc.entries[section].append(_Entry(item_id, text[4:].strip(), [], []))
        elif text.startswith("- "):
            if section in doc.entries:
                doc.entries[section][-1].bullets.append((item_id, text[2:]))
            else:
                doc.lines[section].append((item_id, text[2:]))
        else:
            doc.entries[section][-1].sublines.append(text)
    return doc


def _large() -> _Doc:
    return _parse((FIXTURES / "master.md").read_text(encoding="utf-8"))


def _sized(size: str) -> _Doc:
    doc = _large()
    drop = set(_load("sizes.json")["sizes"][size]["drop"])
    for section, entries in doc.entries.items():
        kept = [entry for entry in entries if entry.id not in drop]
        for entry in kept:
            entry.bullets = [bullet for bullet in entry.bullets if bullet[0] not in drop]
        doc.entries[section] = kept
    for section, items in doc.lines.items():
        doc.lines[section] = [item for item in items if item[0] not in drop]
    return doc


def _add(doc: _Doc, kind: str) -> dict[str, dict]:
    """Add the lines of one adding variation that this size can hold; the lines added, by id."""

    spec = _load("variations.json")
    added: dict[str, dict] = {}
    new_entries = {item["id"]: item for item in spec["new_entries"]}
    for line in spec[kind]:
        text = line["text"]
        if line.get("like"):
            original = doc.text_of(line["like"])
            if original is None:
                continue  # this size does not hold the line it copies
            text = text or original
        if line["entry"] == "other":
            doc.lines.setdefault("other", []).append((line["id"], text))
        else:
            entry = doc.entry(line["entry"])
            if entry is None and line["entry"] in new_entries:
                new = new_entries[line["entry"]]
                entry = _Entry(new["id"], new["heading"], list(new["sublines"]), [])
                doc.entries[new["section"]].append(entry)
            if entry is None:
                continue
            entry.bullets.append((line["id"], text))
        added[line["id"]] = line
    return added


def _permute(doc: _Doc, seed: int) -> None:
    rng = random.Random(seed)
    for entries in doc.entries.values():
        rng.shuffle(entries)
        for entry in entries:
            rng.shuffle(entry.bullets)
    for section, items in doc.lines.items():
        if section == "skills":
            # The groups of a Skills line change places too (a group itself, ``A/B/C``, is one name and stays whole).
            items[:] = [(item_id, " · ".join(rng.sample(text.split(" · "), len(text.split(" · "))))) for item_id, text in items]
        rng.shuffle(items)


def _regroup(doc: _Doc) -> None:
    doc.lines["skills"] = [(f"s-re-{index}", text) for index, text in enumerate(_load("variations.json")["skills_regrouped"], 1)]


@dataclass(frozen=True)
class MasterCase:
    size: str
    variation: str
    markdown: str
    ids: frozenset[str]
    #: Lines an adding variation added, by id (their ``like`` and ``labels``).
    added: dict[str, dict]
    #: The ids of its roles and projects.
    entries: tuple[str, ...] = ()


_CASES: dict[tuple[str, str], MasterCase] = {}


def master_case(size: str, variation: str) -> MasterCase:
    key = (size, variation)
    if key not in _CASES:
        doc = _sized(size)
        added: dict[str, dict] = {}
        if variation in ADDING:
            added = _add(doc, variation)
        elif variation in PERMUTED:
            _permute(doc, 7 + PERMUTED.index(variation))
        elif variation == REGROUPED:
            _regroup(doc)
        elif variation != "base":
            raise ValueError(f"unknown variation {variation}")
        entries = tuple(entry.id for section in ("experience", "projects") for entry in doc.entries.get(section, ()))
        _CASES[key] = MasterCase(size, variation, doc.markdown(), frozenset(doc.ids()), added, entries)
    return _CASES[key]


def profile(size: str) -> dict[str, object]:
    """The profile's prior for a master of ``size``: its legacy 2-page resume's lines (the ones that size holds)."""

    raw = _load("profile.json")
    held = master_case(size, "base").ids
    return {"profile_id": raw["profile_id"], "label": raw["label"], "titles": raw["titles"], "base_ids": [item for item in raw["base_ids"] if item in held]}


# --- the assessment of one cell --------------------------------------------------------------------


def assessment(posting: str) -> list[dict]:
    """The synthetic assessment's rows for ``posting`` (``assessments.json``): requirement, status, class, evidence quotes."""

    return list(_load("assessments.json")["postings"][posting])


_CITED: dict[tuple[str, str, str], list[dict[str, object]]] = {}


def cited(posting: str, case: MasterCase) -> list[dict[str, object]]:
    """The rows of ``posting``'s assessment that cite lines of ``case``'s master, as the probe takes them.

    Traced by the product's own rule on THIS checkout (``assess_master.cited_requirements``): word for word, else
    as a paraphrase.  A quote of a line this master does not hold cites nothing here.
    """

    key = (posting, case.size, case.variation)
    if key not in _CITED:
        from gigai.scout import assess_master
        from gigai.scout.find_jobs.contracts import MatrixStatus, RequirementClass, RequirementMatrixRow
        from gigai.scout.master_resume import parse_master

        matrix = [
            RequirementMatrixRow(row["requirement"], tuple(row["evidence"]), MatrixStatus(row["status"]), RequirementClass(row["class"]))
            for row in assessment(posting)
        ]
        _CITED[key] = [row.to_json() for row in assess_master.cited_requirements(parse_master(case.markdown), matrix)]
    return _CITED[key]


# --- the labels of one cell ------------------------------------------------------------------------


@dataclass(frozen=True)
class Requirement:
    id: str
    text: str
    mandatory: bool
    #: line id -> 2 (strong) | 1 (support), for the lines this master holds.
    lines: dict[str, int]

    @property
    def best(self) -> int:
        return max(self.lines.values(), default=0)


def labels(posting: str, case: MasterCase) -> tuple[list[Requirement], dict[str, list[str]]]:
    """``(requirements, must-keep groups by name)`` of ``posting`` for the lines ``case`` holds (a copied line counts as its original).

    A group is named by the lines the labels list for it, so it has one name in every size and variation."""

    spec = _load("expected.json")["postings"][posting]
    copies: dict[str, list[str]] = {}
    for line_id, line in case.added.items():
        if line.get("like"):
            copies.setdefault(line["like"], []).append(line_id)
    requirements: list[Requirement] = []
    for raw in spec["requirements"]:
        lines: dict[str, int] = {}
        for tier, level in LEVELS.items():
            for line_id in raw[tier]:
                for found in (line_id, *copies.get(line_id, ())):
                    if found in case.ids:
                        lines[found] = max(lines.get(found, 0), level)
        for line_id, line in case.added.items():
            tier = (line.get("labels") or {}).get(posting, {}).get(raw["id"])
            if tier:
                lines[line_id] = LEVELS[tier]
        requirements.append(Requirement(raw["id"], raw["text"], bool(raw["mandatory"]), lines))
    groups = {"/".join(group): [found for line_id in group for found in (line_id, *copies.get(line_id, ())) if found in case.ids] for group in spec["must_keep"]}
    for line_id, line in case.added.items():
        # A useful line that states a must-keep line's fact more strongly satisfies that group too (``same_fact_as``).
        for name in (line.get("same_fact_as") or {}).get(posting, ()):
            if groups.get(name):
                groups[name].append(line_id)
    return requirements, {name: group for name, group in groups.items() if group}


def title_entries(posting: str, case: MasterCase) -> tuple[str, ...]:
    """The roles and projects the labels say ``posting``'s title names, of those ``case`` holds."""

    return tuple(entry for entry in _load("expected.json")["postings"][posting].get("title_entries", ()) if entry in case.entries)


@dataclass(frozen=True)
class Checks:
    """One cell's separate checks (see the module text). ``error``: the path raised; every check then fails."""

    #: The mandatory requirements this master can support.
    mandatory: frozenset[str]
    covered: frozenset[str]
    lost: tuple[str, ...]
    levels: dict[str, int]
    weak: tuple[str, ...]
    kept_groups: frozenset[str]
    omitted: tuple[str, ...]
    pages: int | None
    empty: tuple[str, ...]
    date_order: bool
    lines: int
    skills: int
    skills_left_out: int
    conflicts: int
    shown: frozenset[str]
    shown_skills: frozenset[str]
    summary: tuple[str, ...]
    keywords: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())
    error: str | None = None
    #: With an assessment: the met mandatory rows that cite a line of this master, those with no cited line shown,
    #: and those of them that no reported conflict names (the hard test H5).
    cited_met: tuple[str, ...] = ()
    cited_lost: tuple[str, ...] = ()
    cited_silent: tuple[str, ...] = ()
    #: The other rows that cite a line (unclear, or nice-to-have) with no cited line shown: reported, in no hard test.
    cited_other_lost: tuple[str, ...] = ()
    #: The roles and projects of this master shown with no line (reported: most are irrelevant ones).
    zero_entries: tuple[str, ...] = ()
    #: The entries the labels say the title names (of those this master holds), those shown with no line, and those
    #: of them that no reported conflict names (the hard test H6).
    title_entries: tuple[str, ...] = ()
    title_dropped: tuple[str, ...] = ()
    title_silent: tuple[str, ...] = ()

    @property
    def page_fit(self) -> bool:
        return self.error is None and self.pages is not None and self.pages <= 2 and not self.empty and self.date_order


def atoms(names: list[str]) -> frozenset[str]:
    """The single skills inside skill names: ``Python/Go`` and ``Model Context Protocol (MCP)`` are two each."""

    return frozenset(part.strip().casefold() for name in names for part in re.split(r"[/()·,]", name) if part.strip())


def check(posting: str, case: MasterCase, final: dict[str, object], rows: list[dict[str, object]] | None = None) -> Checks:
    """One final selection's checks; ``rows``: the cited rows the selection was made with (the assessed mode)."""

    requirements, groups = labels(posting, case)
    rows = rows or []
    met = tuple(str(row["id"]) for row in rows if row["mandatory"] and row["met"])
    titled = title_entries(posting, case)
    if "error" in final:
        mandatory = tuple(req.id for req in requirements if req.mandatory and req.lines)
        return Checks(
            frozenset(mandatory), frozenset(), mandatory, {}, mandatory, frozenset(), tuple(groups), None, (), False, 0, 0, 0, 0, frozenset(), frozenset(), (),
            error=str(final["error"]), cited_met=met, cited_lost=met, cited_silent=met,
            zero_entries=case.entries, title_entries=titled, title_dropped=titled, title_silent=titled,
        )
    shown = frozenset([*final["summary"], *(bullet for bullets in final["entries"].values() for bullet in bullets), *final["other"]])  # type: ignore[union-attr]
    # A cited line that a better line repeats is stood for by that line (the selector leaves the copy out).
    twin: dict[str, str] = final.get("duplicates") or {}  # type: ignore[assignment]
    without = [str(row["id"]) for row in rows if not shown & {found for line in row["lines"] for found in (line, twin.get(line, line))}]  # type: ignore[union-attr]
    named = {str(conflict.get("requirement_id") or "") for conflict in final["conflicts"]}  # type: ignore[union-attr]
    zero = tuple(entry for entry in case.entries if not final["entries"].get(entry))  # type: ignore[union-attr]
    reported = {str(found) for conflict in final["conflicts"] if conflict.get("kind") == "title_entry" for found in conflict.get("ids") or ()}  # type: ignore[union-attr]
    levels = {req.id: max((level for line_id, level in req.lines.items() if line_id in shown), default=0) for req in requirements if req.lines}
    covered = frozenset(req_id for req_id, level in levels.items() if level > 0)
    mandatory = [req for req in requirements if req.mandatory and req.lines]
    kept_groups = frozenset(name for name, group in groups.items() if shown & set(group))
    return Checks(
        mandatory=frozenset(req.id for req in mandatory),
        covered=covered,
        lost=tuple(req.id for req in mandatory if req.id not in covered),
        levels=levels,
        weak=tuple(req.id for req in mandatory if levels[req.id] < req.best),
        kept_groups=kept_groups,
        omitted=tuple(name for name in groups if name not in kept_groups),
        pages=final["pages"],  # type: ignore[arg-type]
        empty=tuple(final["empty_entries"]),  # type: ignore[arg-type]
        date_order=bool(final["date_order"]),
        lines=len(shown),
        skills=len(final["skills"]),  # type: ignore[arg-type]
        skills_left_out=len(final["skills_left_out"]),  # type: ignore[arg-type]
        conflicts=len(final["conflicts"]),  # type: ignore[arg-type]
        shown=shown,
        shown_skills=atoms(list(final["skills"])),  # type: ignore[arg-type]
        summary=tuple(final["summary"]),  # type: ignore[arg-type]
        keywords=(tuple(final.get("keywords", {}).get("must", ())), tuple(final.get("keywords", {}).get("nice", ()))),  # type: ignore[union-attr]
        cited_met=met,
        cited_lost=tuple(row for row in without if row in met),
        cited_silent=tuple(row for row in without if row in met and row not in named),
        cited_other_lost=tuple(row for row in without if row not in met),
        zero_entries=zero,
        title_entries=titled,
        title_dropped=tuple(entry for entry in titled if entry in zero),
        title_silent=tuple(entry for entry in titled if entry in zero and entry not in reported),
    )


# --- running the grid --------------------------------------------------------------------------------


def cell_key(posting: str, size: str, variation: str) -> str:
    return f"{posting}|{size}|{variation}"


def payload(posting_ids: list[str], sizes: list[str], variations: list[str], paths: list[str], *, assessed: bool = False) -> dict[str, object]:
    """The probe's request for the grid; ``assessed``: each posting carries the rows its assessment cites of that master."""

    posts = postings()
    cases = [
        {
            "key": cell_key(posting, size, variation), "master": master_case(size, variation).markdown, "profile": profile(size),
            "posting": {**posts[posting], "cited": cited(posting, master_case(size, variation))} if assessed else posts[posting],
        }
        for posting in posting_ids for size in sizes for variation in variations
    ]
    return {"today": _load("sizes.json")["today"], "paths": paths, "cases": cases}


#: The cases (posting x size x variation) one child process computes.  The layout engine under the page measure
#: (``typst.query``) keeps NATIVE memory for every layout, which the process that made the call never gives back
#: (freeing the Python results does not release it): a posting's 27 cases (81 selections) leave about 3.7 GB in it.
#: A child that computes 3 cases peaks near 0.6 GB and takes it all with it when it exits; a child costs about
#: 0.3 s to start (measured on macOS, Python 3.11 and 3.13).
CASES_PER_CHILD = 3


def in_children(request: dict[str, object], tree: Path) -> dict[str, object]:
    """``pick_probe.probe(request)`` by ``tree``'s selector, ``CASES_PER_CHILD`` cases to a short-lived child process."""

    cases: list[dict[str, object]] = request["cases"]  # type: ignore[assignment]
    answer: dict[str, object] = {"selector_version": "", "results": {}}
    for start in range(0, len(cases), CASES_PER_CHILD):
        part = pick_probe.run_in_tree({**request, "cases": cases[start:start + CASES_PER_CHILD]}, tree)
        answer = {**part, "results": {**answer["results"], **part["results"]}}  # type: ignore[dict-item]
    return answer


def run(
    posting_ids: list[str] | None = None, sizes: list[str] | None = None, variations: list[str] | None = None, paths: list[str] | None = None,
    *, tree: Path | None = None, assessed: bool = False,
) -> dict[tuple[str, str, str, str], Checks]:
    """``(posting, size, variation, path) -> Checks`` for the grid, by this checkout's selector or by ``tree``'s.

    ``tree``: the selections are made in short-lived child processes (``in_children``), and what the layout engine
    keeps dies with them; ``tree=REPO`` is this checkout that way.  Without it they are made in THIS process, which
    keeps that memory until it exits: for a few cells only.

    ``assessed``: every posting with its synthetic assessment (the module text). A tree whose selector does not read
    citations selects as it does without them; the ``cited`` check is made on its final selection all the same.
    """

    posting_ids = posting_ids or sorted(postings())
    sizes, variations, paths = sizes or list(SIZES), variations or list(VARIATIONS), paths or list(PATHS)
    request = payload(posting_ids, sizes, variations, paths, assessed=assessed)
    answer = in_children(request, tree) if tree is not None else pick_probe.probe(request)
    out: dict[tuple[str, str, str, str], Checks] = {}
    for posting in posting_ids:
        for size in sizes:
            for variation in variations:
                finals = answer["results"][cell_key(posting, size, variation)]
                for path in paths:
                    case = master_case(size, variation)
                    out[(posting, size, variation, path)] = check(posting, case, finals[path], cited(posting, case) if assessed else None)
    return out


# --- the hard tests, as lists of failures ---------------------------------------------------------


def _lowered(before: Checks, after: Checks) -> list[str]:
    """What checks 1-3 lost going from ``before`` to ``after`` (a master that only gained lines, or regrouped skills).

    Coverage and strength are the MANDATORY requirements' (the objective's terms 1 and 2); a nice-to-have is reported in
    the tables, never here."""

    found: list[str] = []
    gone = sorted((before.covered & before.mandatory) - after.covered)
    if gone:
        found.append("coverage lost: " + ", ".join(gone))
    lower = sorted(req for req, level in before.levels.items() if req in before.mandatory and after.levels.get(req, 0) < level and req not in gone)
    if lower:
        found.append("weaker evidence: " + ", ".join(lower))
    omitted = sorted(set(after.omitted) - set(before.omitted))
    if omitted:
        found.append("must-keep dropped: " + ", ".join(omitted))
    return found


def hard_failures(results: dict[tuple[str, str, str, str], Checks]) -> dict[str, list[str]]:
    """The failures of each hard test over ``results`` (empty lists: all pass)."""

    failures: dict[str, list[str]] = {
        "H1 lost mandatory coverage": [], "H2 adding lines lowered a check": [], "H3 order or grouping changed the pick": [], "H4 page constraint": [],
        "H5 a met mandatory row lost every line it cites, with no conflict reported": [],
        "H6 an entry the posting's title names shows no line, with no conflict reported": [],
    }
    cells = sorted({(posting, size, path) for posting, size, _variation, path in results})
    for (posting, size, variation, path), checks in sorted(results.items()):
        name = f"{posting}/{size}/{variation}/{path}"
        if checks.error:
            failures["H4 page constraint"].append(f"{name}: raised {checks.error}")
            continue
        if checks.lost:
            failures["H1 lost mandatory coverage"].append(f"{name}: {', '.join(checks.lost)}")
        if checks.cited_silent:
            failures["H5 a met mandatory row lost every line it cites, with no conflict reported"].append(f"{name}: {', '.join(checks.cited_silent)}")
        if checks.title_silent:
            failures["H6 an entry the posting's title names shows no line, with no conflict reported"].append(f"{name}: {', '.join(checks.title_silent)}")
        if not checks.page_fit:
            why = [f"{checks.pages} pages"] if (checks.pages or 99) > 2 else []
            why += [f"no bullet under {', '.join(checks.empty)}"] if checks.empty else []
            why += [] if checks.date_order else ["roles out of date order"]
            failures["H4 page constraint"].append(f"{name}: {'; '.join(why)}")
    for posting, size, path in cells:
        base = results.get((posting, size, "base", path))
        if base is None:
            continue
        for variation in ADDING:
            after = results.get((posting, size, variation, path))
            if after is not None:
                failures["H2 adding lines lowered a check"] += [f"{posting}/{size}/base -> {variation}/{path}: {item}" for item in _lowered(base, after)]
        smaller = SIZES[SIZES.index(size) - 1] if SIZES.index(size) else None
        before = results.get((posting, smaller, "base", path)) if smaller else None
        if before is not None:
            failures["H2 adding lines lowered a check"] += [f"{posting}/{smaller} -> {size}/base/{path}: {item}" for item in _lowered(before, base)]
        for variation in PERMUTED:
            after = results.get((posting, size, variation, path))
            if after is not None and (after.shown, after.shown_skills, after.summary) != (base.shown, base.shown_skills, base.summary):
                moved = sorted(after.shown ^ base.shown)
                skills = sorted(after.shown_skills ^ base.shown_skills)
                what = [f"{len(moved)} lines differ ({', '.join(moved[:6])})"] if moved else []
                what += [f"{len(skills)} skills differ ({', '.join(skills[:6])})"] if skills else []
                what += [f"summary {'/'.join(base.summary)} -> {'/'.join(after.summary)}"] if after.summary != base.summary else []
                failures["H3 order or grouping changed the pick"].append(f"{posting}/{size}/{variation}/{path}: {'; '.join(what)}")
        after = results.get((posting, size, REGROUPED, path))
        if after is not None:
            # The same skills in other groups: the same keywords read from the posting, the same skills shown, and
            # checks 1-3 no lower. (The Skills line prints at another length, so the last line that fits may differ.)
            what = _lowered(base, after)
            what += [f"keywords read differently: {sorted(set(after.keywords[0] + after.keywords[1]) ^ set(base.keywords[0] + base.keywords[1]))}"] if after.keywords != base.keywords else []
            what += [f"skills no longer shown: {', '.join(sorted(base.shown_skills - after.shown_skills))}"] if not base.shown_skills <= after.shown_skills else []
            failures["H3 order or grouping changed the pick"] += [f"{posting}/{size}/{REGROUPED}/{path}: {item}" for item in what]
    return failures


# --- printing ----------------------------------------------------------------------------------------


def _cell(checks: Checks, mandatory: int, groups: int, skill_groups: int) -> str:
    if checks.error:
        return f"ERROR {checks.error}"
    fit = "yes" if checks.page_fit else "NO"
    return (
        f"{mandatory - len(checks.lost)}/{mandatory} | {mandatory - len(checks.weak)}/{mandatory} | {groups - len(checks.omitted)}/{groups} | "
        f"{checks.pages}p {fit} | {checks.lines} | {checks.skills}/{skill_groups} | {checks.conflicts} | "
        + (f"{len(checks.cited_met) - len(checks.cited_lost)}/{len(checks.cited_met)}" if checks.cited_met else "-")
        + " | " + (f"{len(checks.title_entries) - len(checks.title_dropped)}/{len(checks.title_entries)}" if checks.title_entries else "-")
        + f" | {len(checks.zero_entries)}"
    )


def table(results: dict[tuple[str, str, str, str], Checks], *, path: str, variation: str = "base", title: str = "") -> str:
    """One row per posting x size for one path and one variation."""

    out = [f"### {title or path}: variation `{variation}`", "", "| posting | size | mandatory covered | strongest kept | must-keep kept | pages, fits | lines | skills shown | conflicts | cited rows kept | title entries shown | entries with 0 lines | lost / weak / omitted / cited rows lost / title entries dropped |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for posting in sorted({key[0] for key in results}):
        for size in SIZES:
            checks = results.get((posting, size, variation, path))
            if checks is None:
                continue
            case = master_case(size, variation)
            requirements, groups = labels(posting, case)
            mandatory = sum(1 for req in requirements if req.mandatory and req.lines)
            skill_groups = checks.skills + checks.skills_left_out
            detail = "; ".join(part for part in (
                "lost " + ",".join(checks.lost) if checks.lost else "", "weak " + ",".join(checks.weak) if checks.weak else "",
                "omitted " + ",".join(checks.omitted) if checks.omitted else "",
                "cited lost " + ",".join(checks.cited_lost) if checks.cited_lost else "",
                "(not met or nice: " + ",".join(checks.cited_other_lost) + ")" if checks.cited_other_lost else "",
                "title dropped " + ",".join(checks.title_dropped) if checks.title_dropped else "",
            ) if part)
            out.append(f"| {posting} | {size} | {_cell(checks, mandatory, len(groups), skill_groups)} | {detail or '-'} |")
    return "\n".join(out)


def summary(results: dict[tuple[str, str, str, str], Checks]) -> str:
    failures = hard_failures(results)
    cells = len(results)
    out = [f"Hard tests over {cells} cells ({len({key[:3] for key in results})} masters x postings, {len({key[3] for key in results})} paths):", ""]
    for name, found in failures.items():
        out.append(f"- {name}: {'PASS' if not found else f'{len(found)} FAIL'}")
        out += [f"    - {item}" for item in found[:40]]
        if len(found) > 40:
            out.append(f"    - ... and {len(found) - 40} more")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--baseline", type=Path, help="an older checkout (or git archive) whose selector is run beside this one")
    parser.add_argument("--posting", action="append", choices=sorted(postings()), help="only these postings")
    parser.add_argument("--size", action="append", choices=SIZES)
    parser.add_argument("--variation", action="append", choices=VARIATIONS)
    parser.add_argument("--path", action="append", choices=PATHS)
    parser.add_argument("--only-baseline", action="store_true", help="run only the baseline tree")
    parser.add_argument("--assessed", action="store_true", help="every posting with its synthetic assessment: coverage from the lines it cites (check 5, H5)")
    parser.add_argument("--json", type=Path, help="write every cell's checks here")
    args = parser.parse_args(argv)
    runs: list[tuple[str, dict[tuple[str, str, str, str], Checks]]] = []
    if args.baseline is not None:
        runs.append((f"BASELINE ({args.baseline.name})", run(args.posting, args.size, args.variation, args.path, tree=args.baseline, assessed=args.assessed)))
    if not args.only_baseline:
        runs.append(("THIS CHECKOUT", run(args.posting, args.size, args.variation, args.path, tree=REPO, assessed=args.assessed)))
    for name, results in runs:
        print(f"\n## {name}{' (every posting with its assessment)' if args.assessed else ''}\n")
        for path in args.path or PATHS:
            for variation in (args.variation or ["base"]):
                print(table(results, path=path, variation=variation, title=f"{name}, path `{path}`"))
                print()
        print(summary(results))
    if args.json is not None:
        dump = {
            name: {
                "/".join(key): {
                    "lost": checks.lost, "weak": checks.weak, "omitted": checks.omitted, "pages": checks.pages, "page_fit": checks.page_fit, "lines": checks.lines,
                    "skills": checks.skills, "skills_left_out": checks.skills_left_out, "conflicts": checks.conflicts, "shown": sorted(checks.shown), "error": checks.error,
                    "cited_met": checks.cited_met, "cited_lost": checks.cited_lost, "cited_silent": checks.cited_silent, "cited_other_lost": checks.cited_other_lost,
                    "zero_entries": checks.zero_entries, "title_entries": checks.title_entries, "title_dropped": checks.title_dropped, "title_silent": checks.title_silent,
                }
                for key, checks in results.items()
            }
            for name, results in runs
        }
        args.json.write_text(json.dumps(dump, indent=1) + "\n", encoding="utf-8")
    return 1 if any(found for _name, results in runs[-1:] for found in hard_failures(results).values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
