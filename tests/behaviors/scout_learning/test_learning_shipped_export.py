"""0.1.11.10 shipped lessons, slice 1: the shipped schema, the export and the scan (``gigai.scout.learning_shipped``).

1. THE SHIPPED FORM. A finished course's work folder becomes ``course.json`` (``scout-learning-shipped-course:1``:
   no marker, evidence, missing, count, posting sentence or company; each lesson gains ``concept_keys``),
   ``catalog.json`` (role key and title, title phrases and deny words, the vocabulary, the expectation and
   technology ids, per lesson its concept keys and subtopic ids and titles) and ``paths/<lesson>.json`` without the
   sentences code wrote from the posting counts. Minified, and byte-identical for the same input.
2. THE SCAN is a hard gate. A master line, 8 consecutive words of one, a master id, anything shaped like one, an
   employer, school or project name, 8 consecutive words of a stored posting sentence, a posting's company or a
   count of postings anywhere in the shipped text refuses the export: it raises, nothing final is written, a
   folder exported before stays as it was. A hit says the file, the field and the source, never the text.
   (S1b) A posting's company is searched in prose only, never in a URL field; a reviewed allow list lets a company
   name through and reports every prose place it stands in, and never exempts a line, a shingle, an id or a name
   of the master; a count is a hit only when it counts POSTINGS.
3. The shingle size and the master-name extraction are constants with their own tests.
4. A field the schema does not know is refused, never passed through.
5. The developer tool prints the counts, the sizes (raw and deflated) and the hits.
6. The wheel configuration lists the shipped folders as package data.

Everything here is SYNTHETIC: the course, the postings and the master were written for this file.
"""

from __future__ import annotations

import copy
import json
import tomllib
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import pytest

from gigai.scout import learning_shipped as shipped
from gigai.scout.learning_shipped import ShippedExportError, ShippedScanError
from gigai.scout.master_resume import parse_master

REPO = Path(__file__).resolve().parents[3]
ROLE_KEY = "kiln-operator"

MASTER = """<!-- gigai-master:1 -->

## Summary

- Kiln engineer who rebuilt the glaze scheduling service for nine studios across two winters. <!-- id:sum-0a1b2c -->

## Experience

### Quokkaflux Ceramics L.L.C., Inc. <!-- id:r-1a2b3c -->
Lead Kiln Engineer | Jan 2020 - Present
- Rebuilt the glaze scheduling service so nine studios share one firing calendar without double bookings. <!-- id:b-4d5e6f -->
- Cut kiln idle time 31% with a queue. <!-- id:b-7a8b9c -->

### Snowmelt Robotics | Remote <!-- id:r-2b3c4d -->
Engineer | Jan 2017 - Dec 2019
- Shipped firmware. <!-- id:b-0d1e2f -->

## Projects

### Glazewright <!-- id:p-3c4d5e -->
- Open kiln log viewer used by forty hobby potters every month of the year. <!-- id:b-5f6a7b -->

## Education

### Tarnwick Institute of Clay <!-- id:e-6a7b8c -->
BSc Ceramics | 2012 - 2016

## Skills

- Tools: Pyrometer, Kiln Controller <!-- id:s-9c0d1e -->
"""

POSTING_LONG = "You will tune the downdraft kiln controllers and write the firing schedules for every porcelain batch we ship."
POSTING_SHORT = "Own our glaze chemistry lab notebooks."
POSTING_TINY = "Load kilns daily."


def concept(concept_id: str, display: str, category: str, phrases: list[str], technical: bool = True) -> dict[str, Any]:
    return {"id": concept_id, "display": display, "category": category, "phrases": phrases, "technical": technical}


VOCABULARY = [
    concept("kiln-controller", "Kiln Controller", "tool", ["kiln controller", "kiln controllers"]),
    concept("pyrometry", "Pyrometry", "skill", ["pyrometry", "pyrometer"]),
    concept("firing-schedules", "Firing schedules", "responsibility", ["firing schedule", "firing schedules"], False),
    concept("glaze-chemistry", "Glaze chemistry", "skill", ["glaze chemistry"]),
    concept("batch-ownership", "Batch ownership", "seniority", ["own the batch"], False),
    concept("emberline", "Emberline", "tool", ["emberline"]),
]


def example(sentence: str, company: str) -> dict[str, str]:
    return {"phrase": sentence, "company": company, "title": "Kiln Operator", "url": "https://jobs.example.test/k/1"}


def counted() -> list[dict[str, Any]]:
    examples = {
        "kiln-controller": [example(POSTING_LONG, "Brindlecote Pottery"), example(POSTING_TINY, "Vexmoor")],
        "glaze-chemistry": [example(POSTING_SHORT, "Vexmoor")],
        "emberline": [example("Emberline experience is a plus for this kiln operator opening in our studio.", "Emberline")],
    }
    return [
        {**item, "count_in90d": 4, "n_in90d": 9, "percent_in90d": 44.4, "count_all": 5, "n_all": 12, "percent_all": 41.7, "kept": True, "examples": examples.get(item["id"], [])}
        for item in VOCABULARY
    ]


def front(concept_id: str, *, examples: bool) -> dict[str, Any]:
    display = next(item["display"] for item in VOCABULARY if item["id"] == concept_id)
    item: dict[str, Any] = {"id": concept_id, "display": display, "percent": 44.4, "in_90d": 4, "any_date": 5}
    if examples:
        item["examples"] = [{"text": POSTING_LONG, "company": "Brindlecote Pottery", "title": "Kiln Operator"}]
    return item


def source(title: str, url: str) -> dict[str, Any]:
    return {"title": title, "url": url, "kind": "doc"}


def lesson(lesson_id: str, title: str, *, technologies: list[str], related: list[str]) -> dict[str, Any]:
    return {
        "id": lesson_id, "title": title, "postings_in_90d": 4, "postings_any_date": 5, "source": "postings",
        "summary": f"{title} explains how a studio plans the work and checks the result before the next load.",
        "detail": [], "in_practice": "A studio writes the plan down, runs one load and compares the cones afterwards.",
        "technologies": technologies, "diagram": {"source": "flowchart LR\n  A[Plan] --> B[Fire]"}, "code": [],
        "sources": [source("Kiln handbook", "https://docs.example.test/kiln/handbook")], "related": related,
        "marker": {
            "id": lesson_id, "title": title, "marker": "SOME",
            "evidence": {"id": "b-4d5e6f", "line": "Rebuilt the glaze scheduling service"}, "missing": "Nothing on cone packs yet.",
        },
        "subtopics": [
            {
                "id": f"{lesson_id}-basics", "title": f"{title}: the basics",
                "body": [{"heading": "What it is", "body": "A ramp is a planned rise in temperature, held at the top and cooled slowly."}],
                "marker": "SOME", "evidence": {"id": "b-4d5e6f", "line": "Rebuilt the glaze scheduling service"}, "missing": "No cone packs.",
            },
            {
                "id": f"{lesson_id}-checks", "title": f"{title}: the checks",
                "body": [{"heading": "", "body": "Witness cones on every shelf show whether the heat work matched the plan."}],
                "marker": "NEW", "evidence": None, "missing": None,
            },
        ],
    }


def path_item(n: int) -> dict[str, Any]:
    return {
        "n": n, "do": f"Run exercise {n} on the bench controller.", "why": "It shows the ramp on a small load.", "done_when": "The log shows the hold.",
        "source": {"title": "Controller guide", "url": f"https://docs.example.test/controller/guide#step-{n}", "verified": {"http": 200, "section_found": "Step", "checked": "2026-10-01"}},
    }


def make_work(root: Path) -> Path:
    """A synthetic finished course's work folder, in the files ``learning_job`` writes."""

    work = root / "work" / "lp-00000001"
    (work / "paths").mkdir(parents=True)
    course = {
        "schema_version": "scout-learning-course:1", "role": "Kiln operator",
        "corpus": {"postings_90d": 9, "postings_any_date": 12, "scope": "9 postings of the last 90 days"},
        "expectations": {"responsibilities": [front("firing-schedules", examples=True)], "scope_at_staff": [front("batch-ownership", examples=True)]},
        "technologies": {"Controllers and probes": [front("kiln-controller", examples=False), front("emberline", examples=False)]},
        "modules": [
            {
                "id": "m1", "title": "Firing", "intro": "How a load goes from cold to cone and back.",
                "concepts": [
                    lesson("firing-ramps", "Firing ramps", technologies=["Kiln Controller", "Node.js 2.0"], related=["glaze-tests"]),
                    lesson("glaze-tests", "Glaze tests", technologies=["Emberline"], related=["firing-ramps"]),
                ],
            },
        ],
        "tool_links": {}, "dropped": [{"what": "kiln-repair", "reason": "no verified source"}],
        "planned_lessons": 3, "kept_lessons": 2, "status": "done", "error": None,
    }
    curriculum = {
        "schema_version": "scout-learning-curriculum:1", "role": "Kiln operator", "planned_lessons": 3,
        "modules": [{"id": "m1", "title": "Firing", "lessons": [
            {"id": "firing-ramps", "title": "Firing ramps", "concepts": ["kiln-controller", "firing-schedules"], "source": "postings", "candidates": []},
            {"id": "glaze-tests", "title": "Glaze tests", "concepts": ["glaze-chemistry"], "source": "postings", "candidates": []},
            {"id": "kiln-repair", "title": "Kiln repair", "concepts": [], "source": "curriculum", "candidates": []},
        ]}],
        "expectations": {"responsibilities": ["firing-schedules"], "scope_at_level": ["batch-ownership"]},
        "technologies": {"Controllers and probes": ["kiln-controller", "emberline"]},
    }
    paths = {
        "firing-ramps": {
            "concept": "firing-ramps", "tool": "Node.js 2.0",
            "alternatives": "A manual sitter needs no software but records nothing. Named in your postings in the last 90 days: Kiln Controller 4 of 9, Emberline 2 of 9.",
            "why_this_tool": "4 of 9 postings in the last 90 days name Node.js 2.0 (Brindlecote Pottery, Vexmoor). Node.js 2.0 scripts the ramp so one bench load shows every stage.",
            "setup": [path_item(1)], "steps": [path_item(2), path_item(3)],
            "sources": [{"title": "Controller guide", "url": "https://docs.example.test/controller/guide", "kind": "doc", "verified": {"http": 200, "section_found": "Guide", "checked": "2026-10-01"}}],
        },
        "glaze-tests": {
            "concept": "glaze-tests", "tool": "Emberline", "alternatives": "A paper notebook works for one potter.",
            "why_this_tool": "Emberline is named in this lesson; it is not among the tools counted in your stored postings. Emberline keeps each tile's recipe beside its photo.",
            "setup": [path_item(1)], "steps": [path_item(2)], "sources": [],
        },
    }
    files = {
        "course.json": course, "curriculum.json": curriculum,
        "vocabulary.json": {"concepts": VOCABULARY, "role_text": "Kiln operator", "fingerprint": "f" * 64, "kept": len(VOCABULARY)},
        "titles.json": {"phrases": ["kiln operator", "kiln technician"], "deny": ["intern"], "role_text": "Kiln operator"},
        "corpus-summary.json": {"role_text": "Kiln operator", "postings": 12, "read": 9, "note": "Read: 9 postings of the last 90 days"},
        "counts.json": {"concepts": counted(), "role_text": "Kiln operator", "fingerprint": "f" * 64},
        "corpus.json": {"concepts": counted(), "role_text": "Kiln operator", "corpus": {"postings": 12}},
    }
    for name, value in files.items():
        (work / name).write_text(json.dumps(value, indent=1), encoding="utf-8")
    for name, value in paths.items():
        (work / "paths" / f"{name}.json").write_text(json.dumps(value, indent=1), encoding="utf-8")
    return work


def edit(work: Path, name: str, change) -> None:
    value = json.loads((work / name).read_text(encoding="utf-8"))
    change(value)
    (work / name).write_text(json.dumps(value, indent=1), encoding="utf-8")


def set_body(text: str):
    """A change of ``course.json``: the first subtopic body of the first lesson reads ``text``."""

    def change(course: dict[str, Any]) -> None:
        course["modules"][0]["concepts"][0]["subtopics"][0]["body"][0]["body"] = text

    return change


BODY_FIELD = "modules[0].concepts[0].subtopics[0].body[0].body"


def master() -> shipped.MasterParts:
    return shipped.master_parts(parse_master(MASTER), extra_ids=["story:kiln-rebuild-winter"])


def export(work: Path, out: Path, **kwargs: Any) -> shipped.ExportReport:
    kwargs.setdefault("master", master())
    return shipped.export_shipped(work, out, role_key=ROLE_KEY, **kwargs)


def folder_bytes(folder: Path) -> dict[str, bytes]:
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


def all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for inner in value.values() for key in all_keys(inner)}
    if isinstance(value, list):
        return {key for inner in value for key in all_keys(inner)}
    return set()


# ---------------------------------------------------------------------------
# 1. The shipped form
# ---------------------------------------------------------------------------


def test_export_writes_the_three_kinds_of_file_and_nothing_personal(tmp_path: Path) -> None:
    work, out = make_work(tmp_path), tmp_path / "shipped" / ROLE_KEY
    report = export(work, out)

    assert sorted(folder_bytes(out)) == ["catalog.json", "course.json", "paths/firing-ramps.json", "paths/glaze-tests.json"]
    course = json.loads((out / "course.json").read_text(encoding="utf-8"))
    assert course["schema_version"] == "scout-learning-shipped-course:1" == shipped.SCHEMA_VERSION
    assert set(course) == {"schema_version", "role_key", "role", "expectations", "technologies", "modules", "tool_links", "dropped"}
    assert (course["role_key"], course["role"]) == (ROLE_KEY, "Kiln operator")
    assert course["expectations"] == {
        "responsibilities": [{"id": "firing-schedules", "display": "Firing schedules"}],
        "scope_at_staff": [{"id": "batch-ownership", "display": "Batch ownership"}],
    }
    assert course["technologies"] == {"Controllers and probes": [{"id": "kiln-controller", "display": "Kiln Controller"}, {"id": "emberline", "display": "Emberline"}]}
    first, second = course["modules"][0]["concepts"]
    assert first["concept_keys"] == ["kiln-controller", "firing-schedules"] and second["concept_keys"] == ["glaze-chemistry"]
    assert set(first) == {"id", "title", "summary", "detail", "in_practice", "technologies", "diagram", "code", "sources", "related", "concept_keys", "subtopics"}
    assert all(set(sub) == {"id", "title", "body"} for sub in first["subtopics"])
    assert first["summary"].startswith("Firing ramps explains") and first["sources"][0]["url"] == "https://docs.example.test/kiln/handbook"

    for name, data in folder_bytes(out).items():
        tree = json.loads(data)
        assert not all_keys(tree) & shipped.FORBIDDEN_KEYS, name
        text = data.decode("utf-8")
        for gone in ("Brindlecote", "Vexmoor", "Rebuilt the glaze", "b-4d5e6f", "44.4", "downdraft", "cone packs", " of 9", "stored postings"):
            assert gone not in text, (name, gone)

    assert report.lessons == 2 and report.paths == 2 and report.out == out
    assert report.removed == {
        "corpus block": 1, "expectations counts": 2, "expectations posting sentences": 2, "technologies counts": 2,
        "lesson markers": 2, "lesson evidence quotes": 2, "lesson missing sentences": 2, "lesson posting counts": 2,
        "subtopic markers": 4, "subtopic evidence quotes": 2, "subtopic missing sentences": 2, "practice count sentences": 3,
    }


def test_catalog_holds_what_the_local_page_needs(tmp_path: Path) -> None:
    work, out = make_work(tmp_path), tmp_path / ROLE_KEY
    export(work, out)
    catalog = json.loads((out / "catalog.json").read_text(encoding="utf-8"))

    assert catalog["schema_version"] == "scout-learning-shipped-catalog:1"
    assert (catalog["role_key"], catalog["role"]) == (ROLE_KEY, "Kiln operator")
    assert catalog["titles"] == {"phrases": ["kiln operator", "kiln technician"], "deny": ["intern"]}
    assert catalog["vocabulary"] == VOCABULARY
    assert catalog["expectations"] == {"responsibilities": ["firing-schedules"], "scope_at_staff": ["batch-ownership"]}
    assert catalog["technologies"] == {"Controllers and probes": ["kiln-controller", "emberline"]}
    assert catalog["modules"] == [{"id": "m1", "title": "Firing"}]
    assert catalog["lessons"][0] == {
        "id": "firing-ramps", "title": "Firing ramps", "module": "m1", "concept_keys": ["kiln-controller", "firing-schedules"],
        "technologies": ["Kiln Controller", "Node.js 2.0"], "has_practice": True,
        "subtopics": [{"id": "firing-ramps-basics", "title": "Firing ramps: the basics"}, {"id": "firing-ramps-checks", "title": "Firing ramps: the checks"}],
    }


def test_paths_lose_the_count_sentences_and_keep_the_models_words(tmp_path: Path) -> None:
    work, out = make_work(tmp_path), tmp_path / ROLE_KEY
    export(work, out)
    ramps = json.loads((out / "paths" / "firing-ramps.json").read_text(encoding="utf-8"))
    tests = json.loads((out / "paths" / "glaze-tests.json").read_text(encoding="utf-8"))

    # the tool's name holds dots and the sentence names companies: both go, and only that sentence
    assert ramps["why_this_tool"] == "Node.js 2.0 scripts the ramp so one bench load shows every stage."
    assert ramps["alternatives"] == "A manual sitter needs no software but records nothing."
    assert tests["why_this_tool"] == "Emberline keeps each tile's recipe beside its photo."
    assert tests["alternatives"] == "A paper notebook works for one potter."
    assert [item["n"] for item in ramps["steps"]] == [2, 3] and ramps["setup"][0]["source"]["verified"]["checked"] == "2026-10-01"


def test_strip_path_reads_the_any_date_window_too() -> None:
    path = {
        "concept": "x", "tool": "Emberline", "why_this_tool": "5 of 12 stored postings name Emberline. It is small.",
        "alternatives": "Named in your stored postings: Kiln Controller 5 of 12.", "setup": [], "steps": [], "sources": [],
    }
    kept, removed = shipped.strip_path(path)
    assert (kept["why_this_tool"], kept["alternatives"], removed) == ("It is small.", "", 2)


def test_output_is_minified_and_byte_identical_for_the_same_input(tmp_path: Path) -> None:
    work = make_work(tmp_path)
    export(work, tmp_path / "a" / ROLE_KEY)
    export(work, tmp_path / "b" / ROLE_KEY)
    export(work, tmp_path / "a" / ROLE_KEY)  # over a folder exported before
    first, second = folder_bytes(tmp_path / "a" / ROLE_KEY), folder_bytes(tmp_path / "b" / ROLE_KEY)
    assert first == second

    # the same content with every object's keys written in another order gives the same bytes
    def reorder(value: object) -> object:
        if isinstance(value, dict):
            return {key: reorder(value[key]) for key in reversed(list(value))}
        return [reorder(inner) for inner in value] if isinstance(value, list) else value

    for path in [*work.glob("*.json"), *(work / "paths").glob("*.json")]:
        path.write_text(json.dumps(reorder(json.loads(path.read_text(encoding="utf-8")))), encoding="utf-8")
    export(work, tmp_path / "c" / ROLE_KEY)
    assert folder_bytes(tmp_path / "c" / ROLE_KEY) == first

    for name, data in first.items():
        text = data.decode("utf-8")
        assert "\n" not in text and '": ' not in text and '", "' not in text, name
        assert data == json.dumps(json.loads(data), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert not list((tmp_path / "a").glob(".*"))  # no temporary folder is left


# ---------------------------------------------------------------------------
# 2. The scan
# ---------------------------------------------------------------------------

# A line of the master that the strip does not remove when a lesson body repeats it.
MASTER_LINE = "Cut kiln idle time 31% with a queue."
MASTER_EIGHT = "so nine studios share one firing calendar without"  # words 5-12 of master line 1
POSTING_EIGHT = "tune the downdraft kiln controllers and write the"  # words 2-9 of the long posting sentence

SCAN_CASES = [
    ("master_line", f"Some studios say: {MASTER_LINE.upper()}  It helps.", "master line 2"),
    ("master_shingle", f"The calendar was rebuilt {MASTER_EIGHT} any clash.", "master line 1 words 5-12"),
    ("master_id", "See the note at b-7a8b9c for the queue.", "master id 2"),
    ("master_id", "It follows story:kiln-rebuild-winter closely.", "master id"),
    ("id_shape", "A stray reference o-00ff11 was left in the text.", "a token shaped like a master id"),
    ("master_name", "A shop like Quokkaflux Ceramics runs two kilns.", "master name"),
    ("master_name", "The team at SNOWMELT   robotics fires weekly.", "master name"),
    ("master_name", "Tarnwick Institute of Clay teaches this ramp.", "master name"),
    ("master_name", "The viewer Glazewright plots the cones.", "master name"),
    ("posting_shingle", f"Operators {POSTING_EIGHT.replace(' kiln ', ' KILN, ')} plan.", "examples[0].phrase words 2-9"),
    ("posting_shingle", "Good operators own our glaze chemistry lab notebooks too.", "examples[0].phrase (whole)"),
    ("posting_company", "A studio such as Brindlecote Pottery fires twice a week.", "examples[0].company"),
    ("posting_company", "Vexmoor is one example.", ".company"),
    ("posting_count", "Kilns matter: 4 of 9 postings name them.", "a count of postings in words"),
    ("posting_count", "This is not among the tools counted in your stored postings.", "a count of postings in words"),
    ("posting_count", "Named in 12 of 171 postings.", "a count of postings in words"),
    ("posting_count", "Roughly twelve postings ask for a pyrometer.", "a count of postings in words"),
    ("posting_count", "It shows up in about a third of the postings.", "a count of postings in words"),
]


@pytest.mark.parametrize(("kind", "text", "source"), SCAN_CASES, ids=[f"{case[0]}-{index}" for index, case in enumerate(SCAN_CASES)])
def test_a_hit_refuses_the_export_and_writes_nothing(tmp_path: Path, kind: str, text: str, source: str) -> None:
    work, out = make_work(tmp_path), tmp_path / "shipped" / ROLE_KEY
    edit(work, "course.json", set_body(text))

    with pytest.raises(ShippedScanError) as refused:
        export(work, out)

    assert not out.exists() and list(out.parent.iterdir()) == []  # nothing final, no temporary folder left
    hits = [hit for hit in refused.value.hits if hit.kind == kind]
    assert hits, [hit.line() for hit in refused.value.hits]
    assert (hits[0].file, hits[0].field) == ("course.json", BODY_FIELD) and source in hits[0].source
    assert refused.value.code == "shipped_scan_failed"
    # the report says where and from which source, never what the text is
    for banned in (text, MASTER_LINE, MASTER_EIGHT, POSTING_EIGHT, "Quokkaflux", "Snowmelt", "Tarnwick", "Glazewright", "Brindlecote", "Vexmoor", "b-7a8b9c", "o-00ff11"):
        assert banned.casefold() not in str(refused.value).casefold()


def test_a_refused_export_leaves_the_folder_exported_before_as_it_was(tmp_path: Path) -> None:
    work, out = make_work(tmp_path), tmp_path / "shipped" / ROLE_KEY
    export(work, out)
    before = folder_bytes(out)
    edit(work, "course.json", set_body(f"New text. {MASTER_LINE}"))

    with pytest.raises(ShippedScanError):
        export(work, out)

    assert folder_bytes(out) == before and [path.name for path in out.parent.iterdir()] == [ROLE_KEY]


def test_seven_shared_words_and_ordinary_words_are_not_a_hit(tmp_path: Path) -> None:
    work, out = make_work(tmp_path), tmp_path / ROLE_KEY
    seven_of_master = " ".join(MASTER_EIGHT.split()[:7])
    seven_of_posting = " ".join(POSTING_EIGHT.split()[:7])
    edit(work, "course.json", set_body(f"The crew wrote that {seven_of_master} fail. Later they {seven_of_posting} old plan. Load kilns daily. Shipped firmware."))
    report = export(work, out)
    # the three-word posting sentence and the two-word master line are too short to search for, and are counted
    assert report.forbidden["posting_sentences_too_short"] == 1 and report.forbidden["master_lines_too_short"] == 1


def test_a_hit_in_a_key_a_path_or_the_catalog_is_found(tmp_path: Path) -> None:
    work, out = make_work(tmp_path), tmp_path / ROLE_KEY

    def rename_group(course: dict[str, Any]) -> None:
        course["technologies"] = {"Tools at Brindlecote Pottery": course["technologies"]["Controllers and probes"]}

    edit(work, "course.json", rename_group)
    edit(work, "paths/firing-ramps.json", lambda path: path["steps"][0].update(do="Ask Vexmoor for a sample log."))
    edit(work, "titles.json", lambda titles: titles["phrases"].append("glazewright"))
    with pytest.raises(ShippedScanError) as refused:
        export(work, out)
    found = {(hit.kind, hit.file, hit.field) for hit in refused.value.hits}
    assert ("posting_company", "course.json", "technologies{key}") in found
    assert ("posting_company", "catalog.json", "technologies{key}") in found
    assert ("posting_company", "paths/firing-ramps.json", "steps[0].do") in found
    assert ("master_name", "catalog.json", "titles.phrases[2]") in found


def test_without_a_master_the_postings_side_and_the_id_shape_still_guard(tmp_path: Path) -> None:
    work, out = make_work(tmp_path), tmp_path / ROLE_KEY
    edit(work, "course.json", set_body(f"{MASTER_LINE} Glazewright."))
    report = export(work, out, master=None)  # nothing of the master is known, so its line is not a hit
    assert report.forbidden["master_id"] == 0 and "master_line" not in report.forbidden and report.forbidden["posting_shingle"] > 0

    edit(work, "course.json", set_body("As b-4d5e6f shows."))
    with pytest.raises(ShippedScanError) as refused:
        export(work, out, master=None)
    assert [hit.kind for hit in refused.value.hits] == ["id_shape"]


def test_the_postings_side_cannot_be_left_out(tmp_path: Path) -> None:
    work = make_work(tmp_path)
    (work / "counts.json").unlink()
    export(work, tmp_path / "one" / ROLE_KEY)  # corpus.json still holds the sentences
    (work / "corpus.json").unlink()
    with pytest.raises(ShippedExportError) as refused:
        export(work, tmp_path / "two" / ROLE_KEY)
    assert refused.value.code == "no_posting_text" and not (tmp_path / "two" / ROLE_KEY).exists()


def test_a_name_the_course_teaches_as_a_term_is_not_a_name_hit_and_is_reported(tmp_path: Path) -> None:
    """A posting's company named like a tool of the vocabulary ("Emberline") stands in the lessons by right."""

    work, out = make_work(tmp_path), tmp_path / ROLE_KEY
    report = export(work, out)
    assert report.forbidden["names_that_are_course_terms"] == 1
    assert report.exempt == ("corpus.json concepts[5].examples[0].company",)
    assert "Emberline" in (out / "course.json").read_text(encoding="utf-8")

    # the exemption is for the NAME only: a sentence of that company's posting is still a hit
    edit(work, "course.json", set_body("Emberline experience is a plus for this kiln operator opening, they say."))
    with pytest.raises(ShippedScanError) as refused:
        export(work, out)
    assert {hit.kind for hit in refused.value.hits} == {"posting_shingle"}


# --- S1b: a count is a hit only when it counts postings


@pytest.mark.parametrize(
    "text",
    [
        "Named in 12 of 171 postings",
        "twelve postings",
        "about a third of the postings",
        "Half of all job ads ask for it.",
        "It is in 40% of the listings.",
        "One in three job postings names a pyrometer.",
        "Seen in 1,204 stored postings.",
        "Job postings: 12 of 171 name it.",
        "Counted over the postings in the last 90 days.",
        "Named in your postings in the last ninety days: Kiln Controller.",
        "It is not among the tools counted in your stored postings.",
        "Dozens of job listings repeat this.",
    ],
)
def test_a_count_of_postings_in_words_is_a_hit(text: str) -> None:
    assert shipped.scan_text(text, shipped.Forbidden()) == [("posting_count", "a count of postings in words")]


@pytest.mark.parametrize(
    "text",
    [
        # the wording style of a real practice step: people, lanes, shelves and steps are counted, never postings
        "Every person or kiln named in your firing notes has exactly one lane and no lane is empty of a named owner.",
        "The log shows three linked loads and you can fire only the last one without rerunning the first two.",
        "When a second or third studio needs the same ramp, the pattern is real.",
        "Run all 12 steps of the checklist, then hold for 90 minutes at cone 6.",
        "The tools named in your notes cover half of the shelf plan.",
        "Read the job posting again before the interview and list what it asks for.",
        "Postings differ by studio. 3 checks follow.",
        "The API accepts a POST of one event; posting the same event twice changes nothing.",
        "Release v1.2 postings to the queue are idempotent.",
    ],
)
def test_a_number_that_counts_something_else_is_not_a_hit(text: str) -> None:
    assert shipped.scan_text(text, shipped.Forbidden()) == []


def test_the_real_wording_style_of_a_step_passes_the_export(tmp_path: Path) -> None:
    work, out = make_work(tmp_path), tmp_path / ROLE_KEY
    edit(work, "paths/firing-ramps.json", lambda path: path["steps"][0].update(done_when="Every person or kiln named in your firing notes has exactly one lane and no lane is empty of a named owner."))
    export(work, out)
    assert "named in your firing notes" in (out / "paths" / "firing-ramps.json").read_text(encoding="utf-8")


# --- S1b: a company is searched in prose, not in a URL field


def _company_in_urls(work: Path) -> None:
    """Put the stored company "Vexmoor" into every kind of URL field of the synthetic course."""

    def course_urls(course: dict[str, Any]) -> None:
        course["tool_links"] = {"Emberline": "https://vexmoor.example.test/docs"}
        first = course["modules"][0]["concepts"][0]
        first["sources"][0]["url"] = "https://docs.example.test/kiln/vexmoor/handbook#vexmoor-panel"
        first["technology_links"] = {"Kiln Controller": "https://docs.example.test/controllers/Vexmoor"}

    def path_urls(path: dict[str, Any]) -> None:
        path["steps"][0]["source"]["url"] = "https://docs.example.test/controller/vexmoor#headless-vexmoor-panel"
        path["setup"][0]["source"]["url"] = "https://docs.example.test/vexmoor"
        path["sources"][0]["url"] = "https://docs.example.test/controller/guide?tab=vexmoor"

    edit(work, "course.json", course_urls)
    edit(work, "paths/firing-ramps.json", path_urls)


def test_a_company_name_in_a_url_field_is_not_a_hit(tmp_path: Path) -> None:
    work, out = make_work(tmp_path), tmp_path / ROLE_KEY
    _company_in_urls(work)
    report = export(work, out)
    assert report.forbidden["posting_company"] == 2  # still searched for, in prose
    shipped_text = "".join(data.decode("utf-8") for data in folder_bytes(out).values())
    assert shipped_text.casefold().count("vexmoor") == 8


def test_a_company_name_in_prose_is_a_hit_beside_the_same_name_in_a_url(tmp_path: Path) -> None:
    work, out = make_work(tmp_path), tmp_path / ROLE_KEY
    _company_in_urls(work)
    edit(work, "paths/firing-ramps.json", lambda path: path["steps"][0].update(why="The Vexmoor studio fires this way."))
    # a link text, a link name and an address written into a sentence are prose
    edit(work, "paths/firing-ramps.json", lambda path: path["steps"][1]["source"].update(title="Vexmoor guide"))
    edit(work, "course.json", lambda course: course["tool_links"].update({"Vexmoor": "https://docs.example.test/tool"}))
    edit(work, "course.json", set_body("See https://docs.example.test/vexmoor for the ramp."))
    with pytest.raises(ShippedScanError) as refused:
        export(work, out)
    assert {(hit.kind, hit.file, hit.field) for hit in refused.value.hits} == {
        ("posting_company", "paths/firing-ramps.json", "steps[0].why"),
        ("posting_company", "paths/firing-ramps.json", "steps[1].source.title"),
        ("posting_company", "course.json", "tool_links{key}"),
        ("posting_company", "course.json", BODY_FIELD),
    }


def test_a_url_field_is_one_address_under_a_url_key_or_in_a_links_object() -> None:
    address = "https://docs.example.test/vexmoor#tabs"
    assert shipped.URL_KEYS == {"url", "href"} and shipped.URL_MAPS == {"tool_links", "technology_links"}
    assert shipped.is_url_field("url", "source", address) and shipped.is_url_field("href", "", address)
    assert shipped.is_url_field("Emberline", "tool_links", address) and shipped.is_url_field("Emberline", "technology_links", address)
    assert not shipped.is_url_field("body", "", address) and not shipped.is_url_field("title", "source", address)
    # prose under a url key is prose, and so is anything that is not one http(s) address
    for prose in ("Vexmoor says so", f"{address} and Vexmoor", f"see {address}", "ftp://vexmoor.example.test/x", "vexmoor.example.test"):
        assert not shipped.is_url_field("url", "source", prose), prose
    tree = {"steps": [{"do": "Vexmoor", "source": {"url": address, "title": "Vexmoor"}}], "tool_links": {"Vexmoor": address}, "url": "Vexmoor says so"}
    assert [(at, url) for at, _text, url in shipped._strings(tree) if "{key}" not in at] == [
        ("steps[0].do", False), ("steps[0].source.url", True), ("steps[0].source.title", False), ("tool_links.Vexmoor", True), ("url", False),
    ]


def test_only_the_company_is_left_out_of_a_url_field_everything_else_is_searched(tmp_path: Path) -> None:
    banned = shipped.forbidden(
        master=master(), posting_sentences=[("posting 0", POSTING_LONG)], posting_companies=[("company 0", "Vexmoor")],
    )
    assert shipped.scan_text("https://docs.example.test/vexmoor", banned, url=True) == []
    assert shipped.scan_text("https://docs.example.test/vexmoor", banned) == [("posting_company", "company 0")]
    in_url = {
        "master_name": "https://glazewright.example.test/log",
        "master_id": "https://docs.example.test/items/b-7a8b9c",
        "master_shingle": "https://docs.example.test/" + MASTER_EIGHT.replace(" ", "-"),
        "posting_shingle": "https://docs.example.test/" + POSTING_EIGHT.replace(" ", "-"),
        "posting_count": "https://docs.example.test/12-of-171 postings",
    }
    for kind, text in in_url.items():
        assert kind in {found for found, _source in shipped.scan_text(text, banned, url=True)}, kind

    # through the export: a name of the master in a step's address refuses it
    work, out = make_work(tmp_path), tmp_path / ROLE_KEY
    edit(work, "paths/firing-ramps.json", lambda path: path["steps"][0]["source"].update(url="https://glazewright.example.test/log"))
    with pytest.raises(ShippedScanError) as refused:
        export(work, out)
    assert [(hit.kind, hit.field) for hit in refused.value.hits] == [("master_name", "steps[0].source.url")]


# --- S1b: the reviewed allow list


def test_an_allowed_name_passes_and_every_prose_place_is_reported(tmp_path: Path) -> None:
    work, out = make_work(tmp_path), tmp_path / ROLE_KEY
    _company_in_urls(work)
    edit(work, "course.json", set_body("The role was made known by VEXMOOR, where potters sat with customers."))
    edit(work, "paths/firing-ramps.json", lambda path: path["steps"][0].update(do="Fire a tile the way Vexmoor does."))
    with pytest.raises(ShippedScanError) as refused:
        export(work, out)
    assert {hit.kind for hit in refused.value.hits} == {"posting_company"} and len(refused.value.hits) == 2
    assert refused.value.allowed == shipped.Allowed()

    report = export(work, out, allow_names=["vexmoor"])
    assert report.forbidden["names_allowed"] == 1 and report.forbidden["posting_company"] == 1  # Brindlecote Pottery stays searched
    assert report.scan.allowed.names == (("vexmoor", "corpus.json concepts[0].examples[1].company"),)
    # the prose places only: the six addresses that hold the name are not listed
    assert report.scan.allowed.places == (("vexmoor", "course.json", BODY_FIELD), ("vexmoor", "paths/firing-ramps.json", "steps[0].do"))
    assert "VEXMOOR" in (out / "course.json").read_text(encoding="utf-8")


def test_an_allowed_name_is_a_whole_name_and_allows_nothing_else(tmp_path: Path) -> None:
    work, out = make_work(tmp_path), tmp_path / "shipped" / ROLE_KEY
    edit(work, "course.json", set_body("A studio such as Brindlecote Pottery fires twice a week."))
    for names in (["Brindlecote"], ["Pottery"], ["Vexmoor"], ["Brindlecote Pottery Works"], [""], []):
        with pytest.raises(ShippedScanError) as refused:
            export(work, out, allow_names=names)
        assert [hit.kind for hit in refused.value.hits] == ["posting_company"], names
    report = export(work, out, allow_names=["  brindlecote   POTTERY "])
    assert report.scan.allowed.places == (("  brindlecote   POTTERY ", "course.json", BODY_FIELD),)


ALLOW_EVERYTHING = ["Vexmoor", "Brindlecote Pottery", "Glazewright", "Quokkaflux Ceramics", "Snowmelt Robotics", "b-7a8b9c", MASTER_LINE, MASTER_EIGHT, POSTING_EIGHT, POSTING_SHORT]


@pytest.mark.parametrize(("kind", "text", "source"), [case for case in SCAN_CASES if case[0] != "posting_company"], ids=[f"{case[0]}-{index}" for index, case in enumerate(SCAN_CASES) if case[0] != "posting_company"])
def test_an_allowed_name_never_exempts_a_line_a_shingle_an_id_a_master_name_or_a_count(tmp_path: Path, kind: str, text: str, source: str) -> None:
    work, out = make_work(tmp_path), tmp_path / "shipped" / ROLE_KEY
    edit(work, "course.json", set_body(text))
    with pytest.raises(ShippedScanError) as refused:
        export(work, out, allow_names=ALLOW_EVERYTHING)
    hits = [hit for hit in refused.value.hits if hit.kind == kind]
    assert hits and source in hits[0].source and not out.exists()
    # only the two stored companies were allowed: no name of the master, no id, no line, no sentence
    assert sorted(name for name, _source in refused.value.allowed.names) == ["Brindlecote Pottery", "Vexmoor"]


def test_forbidden_is_pure_and_an_allowed_name_with_the_words_of_a_master_line_stays_forbidden() -> None:
    parts = shipped.MasterParts(lines=("Vexmoor kiln works",), ids=("b-7a8b9c",), names=("brindlecote pottery",))
    companies = [("company 0", "Vexmoor Kiln Works"), ("company 1", "Brindlecote Pottery"), ("company 2", "Harrowgate"), ("company 3", "HARROWGATE"), ("company 4", "Emberline")]
    sentences = [("posting 0", "Harrowgate fires five kilns weekly now")]
    arguments = dict(master=parts, posting_sentences=sentences, posting_companies=companies, terms=shipped.course_terms(VOCABULARY))
    allow = ["harrowgate", "vexmoor kiln works", "Brindlecote Pottery", "Emberline", "b-7a8b9c", "Harrowgate fires five kilns weekly now", "nobody"]

    plain, allowed = shipped.forbidden(**arguments), shipped.forbidden(**arguments, allow_names=allow)
    assert plain.allowed == {} and "names_allowed" not in plain.counts and plain == shipped.forbidden(**arguments)  # the same inputs, the same set
    assert allowed == shipped.forbidden(**arguments, allow_names=tuple(allow))
    # only the company that is nothing else is allowed, once, by its first source
    assert allowed.allowed == {("harrowgate",): ("harrowgate", "company 2")} and allowed.counts["names_allowed"] == 1
    assert allowed.exempt == ("company 4",) == plain.exempt
    assert shipped.scan_text("Harrowgate is one example.", plain) == [("posting_company", "company 2")]
    assert shipped.scan_text("Harrowgate is one example.", allowed) == []
    assert shipped.allowed_in("Ask HARROWGATE, then Harrowgate again; not Harrowgates.", allowed) == ["harrowgate"]
    assert shipped.allowed_in("Ask Harrowgate.", plain) == []
    for text, found in (
        ("Vexmoor Kiln Works opened.", ("master_line", "master line 0")),
        ("Brindlecote Pottery opened.", ("master_name", "master name 0")),
        ("See b-7a8b9c.", ("master_id", "master id 0")),
        ("They say Harrowgate fires five kilns weekly now.", ("posting_shingle", "posting 0 (whole)")),
    ):
        assert found in shipped.scan_text(text, allowed), text


def test_scan_shipped_reads_every_file_and_refuses_one_that_is_not_json(tmp_path: Path) -> None:
    work, out = make_work(tmp_path), tmp_path / ROLE_KEY
    export(work, out)
    banned = shipped.build_forbidden(work, master())
    clean = shipped.scan_shipped(out, banned)
    assert clean.files == 4 and clean.strings > 50 and clean.words > 100

    (out / "paths" / "extra.json").write_text(json.dumps({"note": ["fine", {"deep": MASTER_LINE}]}), encoding="utf-8")
    with pytest.raises(ShippedScanError) as refused:
        shipped.scan_shipped(out, banned)
    assert [(hit.kind, hit.file, hit.field, hit.source) for hit in refused.value.hits] == [("master_line", "paths/extra.json", "note[1].deep", "master line 2")]

    (out / "paths" / "extra.json").unlink()
    (out / "notes.txt").write_text(MASTER_LINE, encoding="utf-8")
    with pytest.raises(ShippedExportError) as unexpected:
        shipped.scan_shipped(out, banned)
    assert unexpected.value.code == "unexpected_file"


# ---------------------------------------------------------------------------
# 3. The constants: the shingle size and the master-name extraction
# ---------------------------------------------------------------------------


def test_the_shingle_size_is_eight_words_of_normalised_text() -> None:
    assert shipped.SHINGLE_WORDS == 8 and shipped.SHORT_SENTENCE_MIN_WORDS == 5 and shipped.MASTER_LINE_MIN_WORDS == 3
    assert shipped.words("  Fine-Tuning, the  K8s_cluster (v2.1) ﬁrst! ") == ("fine", "tuning", "the", "k8s", "cluster", "v2", "1", "first")
    nine = "one two three four five six seven eight nine"
    assert shipped.shingles(nine) == [(0, tuple(nine.split()[:8])), (1, tuple(nine.split()[1:]))]
    assert shipped.shingles("one two three four five six seven") == []
    assert shipped.shingles(nine, 9) == [(0, tuple(nine.split()))]


def test_the_scan_follows_the_shingle_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    sentence = [("posting 0", POSTING_LONG)]
    text = "They tune the downdraft kiln controllers today."  # five consecutive words of the sentence
    assert shipped.scan_text(text, shipped.forbidden(posting_sentences=sentence)) == []
    monkeypatch.setattr(shipped, "SHINGLE_WORDS", 5)
    monkeypatch.setattr(shipped.shingles, "__defaults__", (5,))
    assert shipped.scan_text(text, shipped.forbidden(posting_sentences=sentence)) == [("posting_shingle", "posting 0 words 2-6")]


@pytest.mark.parametrize(
    ("heading", "names"),
    [
        ("Quokkaflux Ceramics L.L.C., Inc.", ["quokkaflux ceramics l l c inc", "quokkaflux ceramics"]),
        ("Snowmelt Robotics | Remote", ["snowmelt robotics remote", "snowmelt robotics"]),
        ("Snowmelt Robotics - Berlin", ["snowmelt robotics berlin", "snowmelt robotics"]),
        ("Snowmelt Robotics (acquired by Vexmoor)", ["snowmelt robotics acquired by vexmoor", "snowmelt robotics"]),
        ("Brindle-Cote GmbH", ["brindle cote gmbh", "brindle cote"]),
        ("Tarnwick Institute of Clay", ["tarnwick institute of clay"]),
        ("Glazewright", ["glazewright"]),
        ("Q", []),
        ("Inc.", ["inc"]),
    ],
)
def test_master_names_are_the_heading_and_its_name_without_place_or_legal_form(heading: str, names: list[str]) -> None:
    assert shipped.master_names([heading]) == names
    assert shipped.MASTER_NAME_MIN_CHARS == 2 and {"inc", "llc", "l l c", "gmbh", "ltd"} <= shipped.MASTER_NAME_SUFFIXES


def test_master_parts_are_the_lines_the_ids_and_the_names_of_a_master() -> None:
    parts = master()
    assert len(parts.lines) == 6 and parts.lines[2] == MASTER_LINE
    assert set(parts.ids) == {
        "sum-0a1b2c", "b-4d5e6f", "b-7a8b9c", "b-0d1e2f", "b-5f6a7b", "s-9c0d1e", "r-1a2b3c", "r-2b3c4d", "p-3c4d5e", "e-6a7b8c", "story:kiln-rebuild-winter",
    }
    assert list(parts.names) == [
        "quokkaflux ceramics l l c inc", "quokkaflux ceramics", "snowmelt robotics remote", "snowmelt robotics", "glazewright", "tarnwick institute of clay",
    ]
    assert all(shipped.ID_SHAPE.search(found) for found in parts.ids if not found.startswith("story:"))


# ---------------------------------------------------------------------------
# 4. What the export refuses before the scan
# ---------------------------------------------------------------------------


def _unknown_lesson_field(course: dict[str, Any]) -> None:
    course["modules"][0]["concepts"][0]["resume_hint"] = "x"


def _unknown_subtopic_field(course: dict[str, Any]) -> None:
    course["modules"][0]["concepts"][0]["subtopics"][0]["quote"] = "x"


def _unknown_front_field(course: dict[str, Any]) -> None:
    course["technologies"]["Controllers and probes"][0]["companies"] = ["x"]


def _not_done(course: dict[str, Any]) -> None:
    course.update(status="failed", error="too_little_verified")


def _unknown_front_id(course: dict[str, Any]) -> None:
    course["technologies"]["Controllers and probes"][0]["id"] = "not-in-vocabulary"


@pytest.mark.parametrize(
    ("name", "change", "code"),
    [
        ("course.json", _unknown_lesson_field, "unknown_field"),
        ("course.json", _unknown_subtopic_field, "unknown_field"),
        ("course.json", _unknown_front_field, "unknown_field"),
        ("course.json", lambda course: course.update(owner_note="x"), "unknown_field"),
        ("paths/glaze-tests.json", lambda path: path.update(evidence={"id": "x"}), "unknown_field"),
        ("vocabulary.json", lambda vocabulary: vocabulary["concepts"][0].update(count_all=4), "unknown_field"),
        ("course.json", _not_done, "course_not_done"),
        ("course.json", lambda course: course.update(schema_version="scout-learning-course:2"), "invalid_input"),
        ("course.json", _unknown_front_id, "unknown_concept"),
        ("curriculum.json", lambda curriculum: curriculum["modules"][0]["lessons"][0]["concepts"].append("ghost"), "unknown_concept"),
        ("curriculum.json", lambda curriculum: curriculum["modules"][0]["lessons"].pop(1), "unknown_lesson"),
        ("titles.json", lambda titles: titles.update(phrases=[]), "invalid_input"),
        ("titles.json", lambda titles: titles.update(role_text="Another role"), "invalid_input"),
    ],
)
def test_an_input_the_schema_does_not_know_is_refused(tmp_path: Path, name: str, change, code: str) -> None:
    work, out = make_work(tmp_path), tmp_path / "shipped" / ROLE_KEY
    edit(work, name, change)
    with pytest.raises(ShippedExportError) as refused:
        export(work, out)
    assert refused.value.code == code and not out.exists()


def test_a_missing_file_a_stray_path_a_bad_role_key_and_a_foreign_out_folder_are_refused(tmp_path: Path) -> None:
    work = make_work(tmp_path)
    for role_key in ("Kiln Operator", "kiln_operator", "-kiln", "", "k" * 65):
        with pytest.raises(ShippedExportError) as refused:
            shipped.export_shipped(work, tmp_path / "x", role_key=role_key, master=None)
        assert refused.value.code == "invalid_value"

    foreign = tmp_path / "docs"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("not a course", encoding="utf-8")
    with pytest.raises(ShippedExportError) as refused:
        export(work, foreign)
    assert refused.value.code == "out_not_shipped" and (foreign / "keep.txt").read_text(encoding="utf-8") == "not a course"

    (work / "paths" / "kiln-repair.json").write_text(json.dumps({"concept": "kiln-repair", "tool": "x"}), encoding="utf-8")
    with pytest.raises(ShippedExportError) as refused:
        export(work, tmp_path / "y")
    assert refused.value.code == "unknown_lesson"
    (work / "paths" / "kiln-repair.json").unlink()

    (work / "curriculum.json").unlink()
    with pytest.raises(ShippedExportError) as refused:
        export(work, tmp_path / "z")
    assert refused.value.code == "missing_input" and "curriculum.json" in str(refused.value)


def test_check_shipped_names_a_forbidden_key_at_any_depth(tmp_path: Path) -> None:
    inputs = shipped.load_work(make_work(tmp_path))
    built = shipped.shipped_course(inputs["course"], inputs["paths"], inputs["curriculum"], inputs["vocabulary"], inputs["titles"], role_key=ROLE_KEY)
    assert shipped.check_shipped(built) == []
    spoiled = copy.deepcopy(built)
    spoiled.course["modules"][0]["concepts"][1]["sources"][0]["evidence"] = {"id": "x"}
    spoiled.paths["glaze-tests"]["steps"][0]["company"] = "x"
    del spoiled.course["modules"][0]["concepts"][0]["concept_keys"]
    assert shipped.check_shipped(spoiled) == [
        "course.json modules[0].concepts[1].sources[0] holds evidence",
        "paths/glaze-tests.json steps[0] holds company",
        "lesson firing-ramps has no concept_keys",
    ]


# ---------------------------------------------------------------------------
# 5. The developer tool
# ---------------------------------------------------------------------------


def _tool():
    from tools import learning_export_shipped

    return learning_export_shipped


def test_the_tool_prints_counts_and_raw_and_deflated_sizes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    work, out = make_work(tmp_path), tmp_path / "shipped" / ROLE_KEY
    code = _tool().main(["--work", str(work), "--out", str(out), "--role-key", ROLE_KEY, "--no-master"])
    printed = capsys.readouterr().out
    assert code == 0 and (out / "catalog.json").is_file()
    files = folder_bytes(out)
    raw, packed = sum(len(data) for data in files.values()), sum(shipped.deflated(data) for data in files.values())
    assert 0 < packed < raw
    assert f"total            4 file(s) {raw:>10,} {packed:>10,}" in printed
    assert f"course.json      1 file(s) {len(files['course.json']):>10,}" in printed and "paths/*.json     2 file(s)" in printed
    for line in ("master: none", "2 lessons, 2 practice paths", "        3  practice count sentences", "        4  subtopic markers", "        2  posting_company", "scan: 0 hits in 4 files", "not searched (a term the course teaches): corpus.json concepts[5].examples[0].company"):
        assert line in printed, line


def test_the_tool_prints_the_hits_without_the_text_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    work, out = make_work(tmp_path), tmp_path / "shipped" / ROLE_KEY
    edit(work, "course.json", set_body(f"{MASTER_LINE} A studio such as Brindlecote Pottery agrees."))
    tool = _tool()
    monkeypatch.setattr(tool, "read_master", lambda home, target: master())
    code = tool.main(["--work", str(work), "--out", str(out), "--role-key", ROLE_KEY, "--master-home", str(tmp_path / "home")])
    printed = capsys.readouterr().out
    assert code == 1 and not out.exists()
    assert "master: 6 lines, 11 ids, 6 names" in printed and "REFUSED by the scan: 2 hit(s); nothing was written" in printed
    assert f"  master_line: course.json {BODY_FIELD} holds master line 2" in printed
    assert f"  posting_company: course.json {BODY_FIELD} holds corpus.json concepts[0].examples[0].company" in printed
    assert "Cut kiln" not in printed and "Brindlecote" not in printed


def test_the_tool_asks_for_the_master_choice_and_refuses_a_home_without_a_master(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    work, out = make_work(tmp_path), tmp_path / "shipped" / ROLE_KEY
    tool = _tool()
    with pytest.raises(SystemExit) as usage:
        tool.main(["--work", str(work), "--out", str(out), "--role-key", ROLE_KEY])  # neither --master-home nor --no-master
    assert usage.value.code == 2
    capsys.readouterr()

    home = tmp_path / "empty-home"
    home.mkdir()
    arguments = ["--work", str(work), "--out", str(out), "--role-key", ROLE_KEY, "--master-home", str(home)]
    assert tool.main(arguments) == 2  # not a GigAI home at all
    assert "REFUSED (master_unreadable)" in capsys.readouterr().out and not out.exists()
    monkeypatch.setattr("gigai.scout.master_store.load_master", lambda **_kwargs: None)  # a home with no master yet
    assert tool.main(arguments) == 2
    assert "REFUSED (no_master)" in capsys.readouterr().out and not out.exists()

    edit(work, "course.json", lambda course: course.update(status="failed"))
    code = tool.main(["--work", str(work), "--out", str(out), "--role-key", ROLE_KEY, "--no-master"])
    assert code == 2 and "REFUSED (course_not_done)" in capsys.readouterr().out


def test_the_tool_prints_each_allowed_name_and_each_prose_place(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    work, out = make_work(tmp_path), tmp_path / "shipped" / ROLE_KEY
    _company_in_urls(work)
    edit(work, "course.json", set_body("The role was made known by Vexmoor, where potters sat with customers."))
    edit(work, "paths/firing-ramps.json", lambda path: path["steps"][0].update(do="Fire a tile the way Vexmoor does."))
    arguments = ["--work", str(work), "--out", str(out), "--role-key", ROLE_KEY, "--no-master"]
    tool = _tool()

    assert tool.main(arguments) == 1 and not out.exists()
    printed = capsys.readouterr().out
    assert "REFUSED by the scan: 2 hit(s)" in printed and "allowed name" not in printed and "Vexmoor" not in printed

    assert tool.main([*arguments, "--allow-name", "Vexmoor", "--allow-name", "Glazewright", "--allow-name", "Emberline"]) == 0
    printed = capsys.readouterr().out
    for line in (
        "        1  names_allowed",
        "  allowed name (reviewed, not a hit): Vexmoor, the company of corpus.json concepts[0].examples[1].company; in 2 prose place(s)",
        f"    Vexmoor: course.json {BODY_FIELD}",
        "    Vexmoor: paths/firing-ramps.json steps[0].do",
        "  allowed name NOT USED: Glazewright is no stored posting's company",
        "  allowed name NOT USED: Emberline is no stored posting's company",  # a course term: exempt by that rule, not by the list
        "scan: 0 hits in 4 files",
    ):
        assert line in printed, line
    assert printed.count("    Vexmoor: ") == 2 and "source.url" not in printed and "tool_links" not in printed

    # a refusal prints the hits AND what was allowed, so the reader still sees every allowed place
    edit(work, "course.json", lambda course: course["modules"][0].update(intro="A studio such as Brindlecote Pottery fires weekly."))
    assert tool.main([*arguments, "--allow-name", "Vexmoor"]) == 1
    printed = capsys.readouterr().out
    assert "REFUSED by the scan: 1 hit(s)" in printed and "  posting_company: course.json modules[0].intro holds" in printed
    assert f"    Vexmoor: course.json {BODY_FIELD}" in printed and "Brindlecote" not in printed


def test_the_tool_is_not_a_user_command() -> None:
    commands = (REPO / "src" / "gigai" / "data" / "cli" / "commands.yaml").read_text(encoding="utf-8")
    assert "export_shipped" not in commands and "export-shipped" not in commands and "learning_shipped" not in commands
    assert "allow-name" not in commands and "allow_name" not in commands  # the allow list is on the developer tool only


# ---------------------------------------------------------------------------
# 6. The wheel
# ---------------------------------------------------------------------------


def test_the_wheel_configuration_lists_the_shipped_course_folders() -> None:
    configuration = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = configuration["tool"]["setuptools"]["package-data"]["gigai.scout"]
    assert "data/learning/shipped/*/*" in patterns and "data/learning/shipped/*/*/*" in patterns
    for name in ("course.json", "catalog.json", "paths/firing-ramps.json"):
        assert any(fnmatchcase(f"data/learning/shipped/{ROLE_KEY}/{name}", pattern) for pattern in patterns), name
