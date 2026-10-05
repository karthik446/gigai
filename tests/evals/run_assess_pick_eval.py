#!/usr/bin/env python3
"""0.1.11 N3 (SPEC 8.2): the live eval of one assessment that also picks the resume, on synthetic data only.

THE QUESTION.  Does the v9 assessment prompt, which asks one call for more
(sources, suggestions, the pick), keep the verdicts v8 gives, and is its pick
better than the code selector's?  BEFORE is the shipped ``assess.md`` (v8);
AFTER is v9.  This runner is the same for both: it reads no prompt version
and edits no file, it reports what the product stored.

THE CASES (``fixtures/assess_pick/cases.json``): 20 first assessments.  16
postings on the medium synthetic master of the pick eval (``fixtures/pick``),
4 of them again on the large one (the 16 of SPEC 8.2, and one added on the
orchestrator's review of the v9 list rule).  A case names its posting, its
target (the master the gig holds and the answers its bank holds), its profile
and the model target that answers (9 on ``claude_cli``, 8 on ``codex_cli``; a
case keeps its target in every run).

WHAT IS CALLED.  One function, the product's: ``quick_assess.run_quick_assessment``
end to end, for a profile of a gig that holds a master, with the posting
passed as an already resolved job (nothing is fetched).  Each gig is
provisioned as the test fixtures provision one (``initialize_target``,
``create_offline`` with a given proposal text so no model is asked,
``migrate_workpad_layout``), then filled by the product's own writes:
``find-jobs.json``, a resume, ``import_master``, a second profile, each
profile's first selection (``master_profiles.refresh_selection``) and, for
one target, two stored answers (``story_bank.save_answer``).  The
model is reached through the product's own seam
(``proposal_execution.resolve_model_adapter``, looked up as a module
attribute): the runner puts the hard call cap and a per-call log in front of
the port and changes nothing else.  With a tree that has ``scout/pick.py``
(N3 steps 1 and 2) the same call also settles the pick and writes the
suggestion record; this runner then READS that record (``--settle auto``,
the default).  Without it (``--settle off``, or a tree before N3) only the
code selector's selection is measured.

THE CODE SELECTOR, on the same case: ``tools/pick_probe.py``'s ``fallback``
path (``tailor_master.code_only``), given the rows of the assessment the
case just stored as ``cited`` (``assess_master.cited_requirements``), in a
child process.  It is the selection a job gets when no model pick is usable.

THE LABELS (``fixtures/assess_pick/labels.json``; its ``note`` says how to
read them).  Written by the worker, to be reviewed by a person.  Nothing
below is one score:

- the verdict, against the verdicts a reviewer accepts for that master size;
  the resume gate of SPEC 1.5 worked out from the rows (and the stored
  ``resume_gate`` when the tree writes one), against the accepted decisions;
- the requirement rows, matched to the labelled rows by their words: rows
  the labels have and the answer does not, rows the answer adds, rows whose
  class or status a reviewer does not accept;
- the open questions, and those on a row the master already answers (a
  ``strong`` line is held: target 0) or a stored answer already settles;
- sources (v9): ``met`` mandatory rows with none, and sources that name a
  line the labels do not list for that row.  For a v8 answer the evidence
  quotes are traced to master lines with the product's own rule first;
- the pick (v9): ids that are not selectable lines of the master; then the
  checks of SPEC 3.5 for the model's selection and for the code selector's:
  mandatory requirements left without a line, covered ones without their
  strongest line, must-keep groups not shown, the page fit;
- suggestions: how many, how many name a line or a gap the labels agree
  with, how many are left for a person to read, how many state a number
  that neither the posting nor the master holds;
- the injection posting: every string of what it asked for, by the field of
  the answer it turned up in;
- what code dropped at the model boundary, read off the model's own last
  answer beside what was stored: sources the prompt did not offer, a pick
  on a verdict that keeps none, suggestions and questions that were not kept;
- seconds and tokens for every call.

THE SAME POSTING, ANOTHER RESUME (DECISIONS.md addition 1).  Four postings
are assessed twice (``pairs`` in the cases file): the requirement lists of
the two first assessments are compared row by row and the share of rows
that have no counterpart is the disagreement rate, with the classes that
differ among the rows that do.  ``--disagreement A.json B.json ...`` does the
same for every case two or more reports share (another CLI, another pass).

THE DISCIPLINE (SPEC 8.2).  ``assess.md`` is read again on every render: no
run may overlap an edit of it, and the report holds its digest at the start
and at the end of the run.  A scratch home only (``gigai setup
--non-interactive --home <scratch>/home ...``): the runner refuses ``~/.gigai``
and ``$GIGAI_HOME``, and a home that already holds Scout data unless
``--reuse-home``.  One call at a time; ``--max-calls`` is a hard cap, the
product's own retry included, and a case is skipped when its call and that
call's retry no longer fit.  ``--fake-model`` runs every case offline with
answers scripted from the labels (``--fake-shape v8`` or ``v9``): run it
before any live call.  LIVE needs ``GIGAI_ASSESS_EVAL_LIVE=1``.

``--rescore REPORT.json --report NEW.json`` makes no call: every stored
answer is scored again with the labels as they are now (a report holds the
digest of the labels it was scored with).

``--compare BEFORE.json AFTER.json`` makes no call: verdicts, questions,
seconds and tokens case by case, and the cases whose verdict got worse.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import Any

if __package__ in (None, ""):  # run as a script: make ``tests.evals`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.evals import run_assess_eval as harness  # noqa: E402
from tests.evals import run_pick_eval as pick_eval  # noqa: E402
from tests.evals.run_tailor_eval import CallBudget, CallCapReached, CappedBinding  # noqa: E402

REPO = harness.REPO_ROOT
FIXTURES = harness.FIXTURES_DIR
CASES_PATH = FIXTURES / "assess_pick" / "cases.json"
LABELS_PATH = FIXTURES / "assess_pick" / "labels.json"
REPORT_SCHEMA = "gigai-assess-pick-eval-report:1"
DEFAULT_MAX_CALLS = 21
LIVE_ENV = "GIGAI_ASSESS_EVAL_LIVE"
FAKE_TARGET = "ollama_local"
MODEL_TARGETS = ("claude_cli", "codex_cli")
TARGET_MODES = ("as-labelled", "swapped", *MODEL_TARGETS)
SHAPES = ("v8", "v9")
MANDATORY = ("hard", "askable")
OPTIONAL = ("list_item", "nice_to_have")
MATCHED, PENDING, NOT_A_MATCH = "matched_above_threshold", "pending_user_answers", "not_a_match"
RANK = {NOT_A_MATCH: 0, PENDING: 1, MATCHED: 2}
SHORT = {MATCHED: "matched", PENDING: "pending", NOT_A_MATCH: "not a match", None: "-"}
GATE_SUGGEST, GATE_HOLD_QUESTION, GATE_HOLD_UNMET, GATE_NOT_A_MATCH = "suggest", "hold_question", "hold_unmet", "not_a_match"
#: A labelled row and an answer's row are the same requirement from this share of the shorter one's words.
ROW_MATCH = 0.6
#: Two rows of two answers are the same requirement from this share of the words they hold together.
LIST_MATCH = 0.6
ELIGIBILITY_PREFIX = "elig-"


# --- the fixtures -------------------------------------------------------------------------------------


def load_spec(path: Path = CASES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_labels(path: Path = LABELS_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))["postings"]


def load_posting(spec: Mapping[str, Any], posting_id: str) -> dict[str, str]:
    """One posting file: ``key: value`` lines, a blank line, the posting's text."""

    path = FIXTURES / spec["postings"][posting_id]["file"]
    head, _, body = path.read_text(encoding="utf-8").partition("\n\n")
    meta = dict(line.split(": ", 1) for line in head.splitlines())
    return {
        "id": posting_id, "title": meta["title"], "company": meta["company"], "location": meta.get("location", ""), "url": meta["url"],
        "text": body.strip() + "\n",
    }


def master_of(size: str) -> pick_eval.MasterCase:
    """The pick eval's synthetic master at ``size`` (``medium`` or ``large``), unvaried."""

    return pick_eval.master_case(size, "base")


def plan(spec: Mapping[str, Any], only: Sequence[str] | None = None, mode: str = "as-labelled") -> list[dict[str, Any]]:
    """The cases to run, each with the model target ``mode`` gives it."""

    cases = [dict(case) for case in spec["cases"]]
    known = [case["id"] for case in cases]
    unknown = [name for name in only or () if name not in known]
    if unknown:
        raise ValueError(f"unknown case {', '.join(unknown)}; known cases: {', '.join(known)}")
    if mode not in TARGET_MODES:
        raise ValueError(f"unknown --model-target {mode}; one of: {', '.join(TARGET_MODES)}")
    for case in cases:
        labelled = case["model_target"]
        if mode == "swapped":
            case["model_target"] = MODEL_TARGETS[1 - MODEL_TARGETS.index(labelled)]
        elif mode != "as-labelled":
            case["model_target"] = mode
    return [case for case in cases if not only or case["id"] in only]


# --- the labels of one case ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelRow:
    """One labelled requirement row, for the lines one master size holds."""

    id: str
    text: str
    classes: tuple[str, ...]
    class_basis: str
    alternatives: tuple[str, ...]
    strong: tuple[str, ...]
    support: tuple[str, ...]
    skills: tuple[str, ...]
    answers: tuple[str, ...]
    statuses: tuple[str, ...]
    in_pick: bool

    @property
    def mandatory(self) -> bool:
        return self.classes[0] in MANDATORY

    @property
    def lines(self) -> dict[str, int]:
        return {**{line: 1 for line in self.support}, **{line: 2 for line in self.strong}}

    @property
    def master_answers(self) -> bool:
        """A line of this master settles the requirement: a question about it asks what the master already says."""

        return bool(self.strong)


def _derived_statuses(classes: Sequence[str], strong: Sequence[str], support: Sequence[str], skills: Sequence[str]) -> tuple[str, ...]:
    if strong:
        return ("met",)
    if support or skills:
        return ("met", "unclear")
    return ("unclear",) if classes[0] in ("askable", "list_item") else ("unclear", "unmet")


def label_rows(labels: Mapping[str, Any], posting_id: str, size: str) -> list[LabelRow]:
    held = master_of(size).ids
    out: list[LabelRow] = []
    for raw in labels[posting_id]["rows"]:
        strong = tuple(line for line in raw.get("strong", ()) if line in held)
        support = tuple(line for line in raw.get("support", ()) if line in held)
        skills = tuple(line for line in raw.get("skills", ()) if line in held)
        classes = tuple(raw["class"])
        stated = (raw.get("status") or {}).get(size)
        out.append(LabelRow(
            raw["id"], raw["text"], classes, raw.get("class_basis", ""), tuple(raw.get("alternatives", ())), strong, support, skills,
            tuple(raw.get("answers", ())), tuple(stated) if stated else _derived_statuses(classes, strong, support, skills), raw.get("pick", True),
        ))
    return out


def pick_labels(labels: Mapping[str, Any], posting_id: str, size: str) -> tuple[list[pick_eval.Requirement], dict[str, list[str]]]:
    """``(requirements, must-keep groups)`` the selection checks read for one posting and master size."""

    spec = labels[posting_id]["pick"]
    case = master_of(size)
    if "from_pick_eval" in spec:
        return pick_eval.labels(spec["from_pick_eval"], case)
    requirements = [
        pick_eval.Requirement(row.id, row.text, row.mandatory, row.lines) for row in label_rows(labels, posting_id, size) if row.in_pick and row.lines
    ]
    for duty in spec.get("duties", ()):
        lines = {**{line: 1 for line in duty.get("support", ()) if line in case.ids}, **{line: 2 for line in duty.get("strong", ()) if line in case.ids}}
        requirements.append(pick_eval.Requirement(duty["id"], duty["text"], True, lines))
    groups = {"/".join(group): [line for line in group if line in case.ids] for group in spec.get("must_keep", ())}
    return requirements, {name: group for name, group in groups.items() if group}


def selection_checks(requirements: Sequence[pick_eval.Requirement], groups: Mapping[str, Sequence[str]], shown: frozenset[str]) -> dict[str, Any]:
    """Checks 1 to 3 of SPEC 3.5 by the labels, for the master line ids a resume shows. Pure; never added up."""

    mandatory = [req for req in requirements if req.mandatory and req.lines]
    level = {req.id: max((value for line, value in req.lines.items() if line in shown), default=0) for req in requirements if req.lines}
    return {
        "mandatory": len(mandatory),
        "lost": [req.id for req in mandatory if level[req.id] == 0],
        "not_strongest": [req.id for req in mandatory if 0 < level[req.id] < req.best],
        "must_keep": len(groups),
        "omitted": [name for name, group in groups.items() if not shown & set(group)],
        "optional_shown": sum(1 for req in requirements if not req.mandatory and req.lines and level[req.id] > 0),
        "optional": sum(1 for req in requirements if not req.mandatory and req.lines),
    }


# --- matching rows by their words ---------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9][a-z0-9+#./-]*")
_STOP = frozenset(
    "a an and or the of for with in to at on as is are be by from that this it its such other another similar "
    "experience experienced strong strongly deep hands-on record background proven must have you your our we all each".split()
)


def words(text: str) -> frozenset[str]:
    found = (word.strip(".-/") for word in _WORD.findall(" ".join(text.lower().split())))
    return frozenset(word for word in found if word and word not in _STOP)


def fold(text: str) -> str:
    return " ".join(text.casefold().split()).strip(" .;:")


def contained(left: str, right: str) -> float:
    """The share of the shorter text's words the other holds (a tool's name is inside the sentence that lists it)."""

    a, b = words(left), words(right)
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def shared(left: str, right: str) -> float:
    a, b = words(left), words(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def match_rows(labelled: Sequence[LabelRow], rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, list[int]], set[str], list[int]]:
    """``(label id -> the answer's rows for it, the labels a row of another label stands for, rows no label claims)``.

    Each row of the answer goes to the labelled row whose words it shares
    most (``ROW_MATCH``).  A labelled row with no row of its own takes the
    best row that holds its words (the answer MERGED two labelled rows into
    one: the label id is then in the second value).
    """

    score = [[(1.0, 1.0) if fold(label.text) == fold(str(row["requirement"])) else (contained(label.text, str(row["requirement"])), shared(label.text, str(row["requirement"]))) for row in rows] for label in labelled]
    found: dict[str, list[int]] = {label.id: [] for label in labelled}
    extra: list[int] = []
    for place in range(len(rows)):
        best = max(range(len(labelled)), key=lambda index: score[index][place], default=None)
        if best is not None and score[best][place][0] >= ROW_MATCH:
            found[labelled[best].id].append(place)
        else:
            extra.append(place)
    merged: set[str] = set()
    for index, label in enumerate(labelled):
        if not found[label.id] and rows:
            best = max(range(len(rows)), key=lambda place: score[index][place])
            if score[index][best][0] >= ROW_MATCH:
                found[label.id] = [best]
                merged.add(label.id)
    return found, merged, extra


def list_disagreement(left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Two requirement lists of one posting, row by row. Pure.

    A row has a counterpart when the other list holds a row with the same
    words (case and spacing aside: ``same_words``) or, failing that, one
    that shares ``LIST_MATCH`` of the words the two hold together
    (``close_words``), each row used once.  ``rate`` is the share of all
    rows (counterparts counted once) that have none; ``rate_same_words``
    counts only identical wording as agreement.  Rows about the candidate
    (``elig-`` ids) are left out.
    """

    a = [row for row in left if not str(row.get("id") or "").startswith(ELIGIBILITY_PREFIX)]
    b = [row for row in right if not str(row.get("id") or "").startswith(ELIGIBILITY_PREFIX)]
    free = set(range(len(b)))
    pairs: list[tuple[int, int, bool]] = []
    for index, row in enumerate(a):
        same = next((place for place in sorted(free) if fold(str(b[place]["requirement"])) == fold(str(row["requirement"]))), None)
        if same is not None:
            pairs.append((index, same, True))
            free.discard(same)
    taken = {index for index, _place, _same in pairs}
    close = sorted(
        ((shared(str(a[index]["requirement"]), str(b[place]["requirement"])), index, place) for index in range(len(a)) if index not in taken for place in free),
        reverse=True,
    )
    for value, index, place in close:
        if value >= LIST_MATCH and index not in taken and place in free:
            pairs.append((index, place, False))
            taken.add(index)
            free.discard(place)
    same_words = sum(1 for _index, _place, same in pairs if same)
    only_left = [str(a[index]["requirement"]) for index in range(len(a)) if index not in taken]
    only_right = [str(b[place]["requirement"]) for place in sorted(free)]
    total = len(pairs) + len(only_left) + len(only_right)
    class_differs = [
        {"requirement": str(a[index]["requirement"]), "left": a[index].get("class"), "right": b[place].get("class")}
        for index, place, _same in pairs if a[index].get("class") != b[place].get("class")
    ]
    return {
        "rows_left": len(a), "rows_right": len(b), "same_words": same_words, "close_words": len(pairs) - same_words,
        "only_left": only_left, "only_right": only_right, "class_differs": class_differs,
        "rate": round((len(only_left) + len(only_right)) / total, 3) if total else 0.0,
        "rate_same_words": round((total - same_words) / total, 3) if total else 0.0,
    }


# --- what one answer is, against its labels ------------------------------------------------------------


def gate_by_spec(rows: Sequence[Mapping[str, Any]], verdict: str | None) -> str:
    """The resume gate of SPEC 1.5 for an answer's rows (OD1 and OD2 defaults): only must-haves hold. Pure.

    The eval's own reading of the table, so a v8 answer has a gate to compare
    too; a tree with ``resume_gate`` stores the product's, reported beside it.
    """

    if verdict == NOT_A_MATCH or any(row["status"] == "unmet" and row["class"] in ("hard", None) for row in rows):
        return GATE_NOT_A_MATCH
    if any(row["status"] == "unclear" and row["class"] in MANDATORY for row in rows):
        return GATE_HOLD_QUESTION
    if any(row["status"] == "unmet" and row["class"] == "askable" for row in rows):
        return GATE_HOLD_UNMET
    return GATE_SUGGEST


def _numbers(text: str) -> set[str]:
    return {found.rstrip(".,") for found in re.findall(r"\d[\d,.]*", text)}


def analyse(
    answer: Mapping[str, Any], labelled: Sequence[LabelRow], accepted: Mapping[str, Any], *, size: str, master_ids: frozenset[str],
    selectable: frozenset[str], facts: str, injection: Mapping[str, Any] | None, location: str = "",
) -> dict[str, Any]:
    """One stored answer against its labels: lists a person reads, never a score. Pure.

    ``answer``: ``{verdict, matrix, questions, suggestions, structured_suggestions, pick, not_a_match_reason}``
    as ``answer_json`` builds it.  ``facts``: the posting and the master as
    text (what a number in a suggestion may come from).  ``location``: the
    posting's LOCATION line: a row no label claims that states it (rule 4's
    row about the candidate, "Remote (US)"), or that carries an ``elig-`` id,
    is listed apart (``rows_eligibility``), not as a row the answer added.
    """

    rows: list[Mapping[str, Any]] = answer["matrix"]
    found, merged, extra = match_rows(labelled, rows)
    eligibility = [
        place for place in extra
        if str(rows[place].get("id") or "").startswith(ELIGIBILITY_PREFIX) or (location and fold(str(rows[place]["requirement"])).startswith(fold(location)))
    ]
    extra = [place for place in extra if place not in eligibility]
    by_label = {label.id: label for label in labelled}
    label_of = {place: label_id for label_id, places in found.items() if label_id not in merged for place in places}
    class_wrong, status_wrong, mandatory_wrong, rows_out = [], [], [], []
    for label in labelled:
        places = found[label.id]
        rows_out.append({
            "label": label.id, "text": label.text, "accepted_class": list(label.classes), "accepted_status": list(label.statuses),
            "merged": label.id in merged, "rows": [{"requirement": rows[place]["requirement"], "class": rows[place]["class"], "status": rows[place]["status"]} for place in places],
        })
        if label.id in merged:
            continue  # the row belongs to another label: its class and status are judged there
        for place in places:
            row = rows[place]
            if row["class"] not in label.classes:
                class_wrong.append({"label": label.id, "requirement": row["requirement"], "class": row["class"], "accepted": list(label.classes)})
                if (row["class"] in MANDATORY) != label.mandatory and not ({*label.classes} & set(MANDATORY) and {*label.classes} & set(OPTIONAL)):
                    mandatory_wrong.append({"label": label.id, "requirement": row["requirement"], "class": row["class"], "accepted": list(label.classes)})
            if row["status"] not in label.statuses:
                status_wrong.append({"label": label.id, "requirement": row["requirement"], "status": row["status"], "accepted": list(label.statuses)})

    by_requirement = {fold(str(row["requirement"])): place for place, row in enumerate(rows)}
    questions = []
    for question in answer["questions"]:
        place = by_requirement.get(fold(str(question.get("requirement") or "")))
        label = by_label.get(label_of.get(place, "")) if place is not None else None
        questions.append({
            "question_id": question.get("question_id"), "requirement": question.get("requirement"), "label": label.id if label else None,
            "row_class": rows[place]["class"] if place is not None else None,
            "master_answers": bool(label and label.master_answers), "stored_answer": bool(label and label.answers),
        })

    has_sources = any(row.get("sources") for row in rows)
    no_source, unsupported = [], []
    for place, row in enumerate(rows):
        label = by_label.get(label_of.get(place, ""))
        cited = list(row.get("sources") or ()) if has_sources else list(row.get("traced") or ())
        if has_sources and row["status"] == "met" and row["class"] in MANDATORY and not cited and not str(row.get("id") or "").startswith(ELIGIBILITY_PREFIX):
            no_source.append({"requirement": row["requirement"], "label": label.id if label else None})
        if label is None or row["status"] != "met":
            continue
        known = {*label.lines, *label.skills, *(f"A {name}" for name in label.answers)}
        wrong = [source for source in cited if source not in known]
        if wrong:
            unsupported.append({"label": label.id, "requirement": row["requirement"], "sources": wrong, "by": "sources" if has_sources else "traced evidence"})

    verdict = answer["verdict"]
    gate = gate_by_spec(rows, verdict)
    stored_gate = answer.get("resume_gate")
    structured = list(answer.get("structured_suggestions") or ())
    fact_numbers = _numbers(facts)
    suggestions = []
    for item in structured:
        line = item.get("line")
        named = next((label for label in labelled if line and line in {*label.lines, *label.skills}), None)
        gap = next((label for label in labelled if item.get("kind") in ("gap", "master_line") and not label.lines and shared(label.text, str(item.get("why") or "")) > 0.15), None)
        new_numbers = sorted(_numbers(str(item.get("why") or "")) - fact_numbers)
        suggestions.append({
            **item, "line_known": line is None or line in master_ids, "phrase_in_posting": not item.get("posting_phrase") or fold(str(item["posting_phrase"])) in fold(facts),
            "labels_agree": (named or gap).id if (named or gap) else None, "numbers_not_in_facts": new_numbers,
        })
    pick = answer.get("pick")
    pick_out = None
    if pick:
        lines = list(pick.get("lines") or ())
        pick_out = {
            "lines": len(lines), "unknown": [line for line in lines if line not in master_ids],
            "not_selectable": [line for line in lines if line in master_ids and line not in selectable],
            "repeats": len(lines) - len(set(lines)), "summary": pick.get("summary"), "section_order": pick.get("section_order"),
        }

    hits = []
    if injection:
        fields = {
            "requirement": [str(row["requirement"]) for row in rows], "evidence": [text for row in rows for text in row.get("evidence") or ()],
            "class_basis": [str(row.get("class_basis") or "") for row in rows],
            "question": [f"{item.get('question_id')} {item.get('question')}" for item in answer["questions"]],
            "suggestion": [*answer.get("suggestions", ()), *(json.dumps(item, ensure_ascii=False) for item in structured)],
            "pick": [json.dumps(pick, ensure_ascii=False)] if pick else [], "reason": [str(answer.get("not_a_match_reason") or "")],
        }
        for name, texts in fields.items():
            for text in texts:
                for needle in injection["forbidden"]:
                    # As a word of its own: "CKA" is not found inside "package".
                    if re.search(r"(?<![A-Za-z0-9])" + re.escape(needle.strip()) + r"(?![A-Za-z0-9])", text, re.IGNORECASE):
                        hits.append({"field": name, "found": needle, "text": text[:200]})

    return {
        "size": size, "verdict_accepted": list(accepted["verdicts"][size]), "verdict_ok": verdict in accepted["verdicts"][size],
        "gate_by_spec": gate, "gate_accepted": list(accepted["gate"][size]), "gate_ok": gate in accepted["gate"][size],
        "gate_stored": stored_gate, "gate_stored_differs": bool(stored_gate) and stored_gate.get("decision") != gate,
        "rows": rows_out, "rows_missing": [label.id for label in labelled if not found[label.id]], "rows_merged": sorted(merged),
        "rows_extra": [rows[place]["requirement"] for place in extra],
        "rows_eligibility": [{"requirement": rows[place]["requirement"], "class": rows[place]["class"], "status": rows[place]["status"]} for place in eligibility],
        "class_wrong": class_wrong, "mandatory_wrong": mandatory_wrong, "status_wrong": status_wrong,
        "questions": questions, "questions_master_answers": sum(item["master_answers"] for item in questions),
        "questions_stored_answer": sum(item["stored_answer"] for item in questions),
        "questions_on_optional": sum(item["row_class"] in OPTIONAL for item in questions),
        "questions_on_must_have": sum(item["row_class"] in MANDATORY for item in questions),
        "unclear_must_have_without_question": [
            str(row["requirement"]) for row in rows
            if row["status"] == "unclear" and row["class"] in MANDATORY and fold(str(row["requirement"])) not in {fold(str(item.get("requirement") or "")) for item in answer["questions"]}
        ],
        "has_sources": has_sources, "met_without_source": no_source, "sources_not_labelled": unsupported,
        "suggestions": len(answer.get("suggestions") or ()), "structured_suggestions": suggestions,
        "suggestions_labels_agree": sum(1 for item in suggestions if item["labels_agree"]),
        "suggestions_to_read": sum(1 for item in suggestions if not item["labels_agree"]),
        "suggestions_new_numbers": sum(1 for item in suggestions if item["numbers_not_in_facts"]),
        "pick": pick_out, "injection_hits": hits if injection else None,
    }


# --- the scratch home ---------------------------------------------------------------------------------


@dataclass
class TargetHome:
    """One gig of the scratch home: a master of one size, two profiles, perhaps stored answers."""

    name: str
    size: str
    target: Path
    gig_id: str
    profiles: dict[str, str]
    master: Any
    master_markdown: str
    base_ids: dict[str, tuple[str, ...]]


def refuse_home(home: Path) -> str | None:
    """Why ``home`` may not be used for a live run, or ``None``. Never the operator's own home."""

    resolved = home.expanduser().resolve()
    own = [Path.home() / ".gigai"]
    if os.environ.get("GIGAI_HOME"):
        own.append(Path(os.environ["GIGAI_HOME"]).expanduser())
    if any(resolved == path.resolve() or path.resolve() in resolved.parents for path in own):
        return f"{resolved} is the operator's GigAI home: this eval writes to a scratch home only"
    if not (resolved / "config.toml").is_file():
        return f"{resolved} holds no config.toml: make it with `gigai setup --non-interactive --home {resolved} --workpad-root <scratch>/workpads --editor /usr/bin/true`"
    return None


def build_target(home_root: Path, root: Path, name: str, spec: Mapping[str, Any]) -> TargetHome:
    """One project, one gig, the master, both profiles with their first selection, the stored answers.

    Provisioned as the test fixtures provision a gig, then filled by the
    product's own write functions; no store file is written by hand.
    """

    from gigai.canonical import canonical_json_bytes, digest_imported_bytes
    from gigai.lifecycle import create_offline
    from gigai.private_records import create_record, import_reference, migrate_workpad_layout
    from gigai.scout import master_profiles, story_bank
    from gigai.scout.find_jobs.contracts import FindJobsConfig, SourceToggles
    from gigai.scout.master_selection import SelectionProfile, select
    from gigai.scout.master_store import import_master, load_master
    from gigai.scout.profile_records import create_profile, list_profiles, selected_profile
    from gigai.target_binding import initialize_target
    from gigai.workpad import resolve_workpad

    settings = spec["targets"][name]
    candidate = {**spec["candidate"], **settings.get("candidate", {})}
    size = settings["master"]
    case = master_of(size)
    target = root / name
    target.mkdir(parents=True, exist_ok=False)
    initialize_target(home_root=home_root, requested_target=target)
    created = create_offline(
        home_root=home_root,
        requested_target=target,
        name=f"assess-pick-eval-{name}",
        open_editor=False,
        # A given proposal text: no model target is asked for one (a scratch home has only the two CLIs).
        model_output="A synthetic gig for the assess-and-pick eval; it holds an invented person's master resume.",
    )
    migrate_workpad_layout(workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id)
    default, other = spec["profiles"]["ai"], spec["profiles"]["swe"]
    config = FindJobsConfig(
        roles=tuple(default["titles"]), merged_queries=tuple(default["titles"]), location=candidate["location"], remote=True, published_after=None,
        sources=SourceToggles(exa=True, ats=True, hiringcafe=False), countries=tuple(candidate["countries"]),
        visa_sponsorship_required=bool(candidate["visa_sponsorship_required"]),
    )
    (target / "find-jobs.json").write_bytes(canonical_json_bytes(config.to_json()))

    files = root / f"{name}-files"
    files.mkdir()
    master_file = files / "master.md"
    master_file.write_text(case.markdown, encoding="utf-8")
    # The resume the gig starts with: the 2 pages the selector makes of the master with no posting.
    from gigai.scout.master_resume import parse_master

    resume_bytes = select(parse_master(case.markdown), SelectionProfile(titles=tuple(default["titles"])), None, today=pick_eval.date_of(pick_eval._load("sizes.json")["today"])).markdown.encode("utf-8")
    resume_file = files / "resume.md"
    resume_file.write_bytes(resume_bytes)
    imported = import_reference(
        home_root=home_root, requested_target=target, gig_id=created.gig_id, kind="resume", source=resume_file,
        operation_key=f"scout-resume-add:resume.md:{digest_imported_bytes(resume_bytes)}",
    )
    create_record(
        home_root=home_root, requested_target=target, gig_id=created.gig_id, kind="imported_reference", content_family="g45_reference",
        content_id=imported.item_id, actor={"kind": "operator", "id": "local-user"}, origin="imported",
        operation_key=f"scout-resume-record:{imported.item_id}",
    )

    def resolved():
        return resolve_workpad(home_root=home_root, requested_target=target, gig_id=created.gig_id, allow_semantic_state=True)

    first = selected_profile(resolved(), home_root=home_root, target=target)
    assert first is not None
    status = import_master(home_root=home_root, target=target, source=master_file, gig_id=created.gig_id).status
    assert status == "created", status
    second = create_profile(
        resolved(), label=other["label"], titles=tuple(other["titles"]), titles_to_avoid=(), queries=tuple(other["titles"]), resume_ref=first.resume_ref,
    )
    profiles = {"ai": first.profile_id, "swe": second.profile_id}
    for profile_id in profiles.values():
        master_profiles.refresh_selection(home_root=home_root, target=target, profile_id=profile_id)
    for answer in settings.get("answers", ()):
        story_bank.save_answer(home_root=home_root, target=target, question_id=answer["question_id"], answer=answer["answer"], question=answer["question"])
    stored = load_master(home_root=home_root, target=target, gig_id=created.gig_id)
    assert stored is not None
    records = {record.profile_id: record for record in list_profiles(resolved())}
    base_ids = {
        key: tuple(records[profile_id].master_selection.item_ids) if records[profile_id].master_selection is not None else ()
        for key, profile_id in profiles.items()
    }
    return TargetHome(name, size, target, created.gig_id, profiles, stored.master, case.markdown, base_ids)


# --- the model, behind the product's own seam ------------------------------------------------------------


class _FakePort:
    """Answers every assess call with the answer scripted for the case in flight."""

    name = "fixture"
    timed_out = False
    resolved_model = "fixture-model"

    def __init__(self, model: "FakeModel") -> None:
        self._model = model

    def invoke(self, request: Any) -> Any:
        from gigai.adapters.port import InvocationResult, NormalizedUsage

        text = self._model.answer(request.prompt)
        tokens_in, tokens_out = len(request.prompt) // 4, len(text) // 4
        return InvocationResult(
            status="success", output_text=text, resolved_model=self.resolved_model, raw_usage={},
            normalized_usage=NormalizedUsage(tokens_in, tokens_out, tokens_in + tokens_out), cost_status="unavailable",
        )


class FakeModel:
    """The offline model: ``answers[case id]`` for the case in flight; every prompt is kept.

    ``recorded`` (``--replay-dir``): the answers are a live run's own, read
    from its ``--dump-dir`` (the first attempt of each case), and its prompts
    are kept to say whether this tree renders the same prompt for the case.
    """

    def __init__(self, recorded: Path | None = None) -> None:
        self.answers: dict[str, str] = {}
        self.case: str | None = None
        self.prompts: list[tuple[str, str]] = []
        self.recorded = recorded
        self.recorded_prompts: dict[str, str] = {}
        if recorded is not None:
            for path in sorted(recorded.glob("*-attempt1.output.txt")):
                case = path.name.split("-", 1)[1].removesuffix("-attempt1.output.txt")
                self.answers[case] = path.read_text(encoding="utf-8")
                self.recorded_prompts[case] = path.with_name(path.name.replace(".output.txt", ".prompt.txt")).read_text(encoding="utf-8")

    def answer(self, prompt: str) -> str:
        assert self.case is not None
        self.prompts.append((self.case, prompt))
        return self.answers[self.case]

    def binding(self) -> Any:
        port = _FakePort(self)
        return SimpleNamespace(port=port, request=lambda *, role, prompt, required_capabilities=frozenset({"text"}): SimpleNamespace(prompt=prompt, role=role), close=lambda: None)


class _RawPort:
    """The adapter's port, keeping what each answer reported beside the normalized usage.

    The Claude CLI counts a prompt it has not seen before as cache tokens:
    its ``input_tokens`` is then 2 or 3 and the prompt's size is in
    ``cache_creation_input_tokens`` / ``cache_read_input_tokens``, which the
    normalized usage leaves out.  The raw usage and the cost the CLI reports
    are kept here, per call that answered.
    """

    def __init__(self, inner: Any, log: list[dict[str, Any]]) -> None:
        self._inner = inner
        self._log = log

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def invoke(self, request: Any) -> Any:
        result = self._inner.invoke(request)
        self._log.append({
            "raw_usage": json.loads(json.dumps(dict(getattr(result, "raw_usage", None) or {}), default=str)),
            "cost_usd": getattr(result, "cost_usd", None), "resolved_model": getattr(result, "resolved_model", None),
        })
        return result


@contextmanager
def model_seam(budget: CallBudget, fake: FakeModel | None, raw: list[dict[str, Any]] | None = None) -> Iterator[None]:
    """``proposal_execution.resolve_model_adapter`` with the call cap and the per-call log in front of the port.

    LIVE: the product's own resolver is called and its binding is wrapped.
    FAKE: the binding answers from ``fake``.  ``raw`` collects each answer's
    raw usage (``_RawPort``).  The attribute is put back on exit.
    """

    from gigai.scout import proposal_execution, quick_assess

    original = proposal_execution.resolve_model_adapter
    assess_once = quick_assess.assess_once

    def observed(*args: Any, **kwargs: Any) -> Any:
        # The product's own call, passed through untouched: what the boundary dropped is on the attempt
        # (``AssessExtras``) and run_quick_assessment does not return it.
        attempt = assess_once(*args, **kwargs)
        if raw is not None:
            raw.append({"attempt": attempt_extras(attempt)})
        return attempt

    def resolve(config: Any, adapter_target: Any, **kwargs: Any) -> Any:
        inner = fake.binding() if fake is not None else original(config, adapter_target, **kwargs)
        if raw is not None:
            inner = SimpleNamespace(port=_RawPort(inner.port, raw), request=inner.request, close=getattr(inner, "close", lambda: None), current=getattr(inner, "current", None))
        return CappedBinding(inner, budget)

    # The fixture transport of ``bindings`` must not wrap this resolver a second time.
    setattr(resolve, "_scout_test_transport", True)
    proposal_execution.resolve_model_adapter = resolve  # type: ignore[assignment]
    quick_assess.assess_once = observed  # type: ignore[assignment]
    try:
        yield
    finally:
        proposal_execution.resolve_model_adapter = original  # type: ignore[assignment]
        quick_assess.assess_once = assess_once  # type: ignore[assignment]


def attempt_extras(attempt: Any) -> dict[str, Any]:
    """What the model boundary recorded on one assess attempt beside the stored answer (``AssessExtras``), as plain data.

    Every name is read if the tree has it (N3 steps 1 and 2: the dropped
    pick, the dropped suggestions, the unknown sources; N3b: the questions on
    optional rows dropped past the cap, the questions code asked for an
    unclear must-have row); a tree without one answers nothing for it.
    """

    extras = getattr(attempt, "extras", None)
    out: dict[str, Any] = {
        "ok": bool(getattr(attempt, "ok", False)), "attempts": getattr(attempt, "attempts", None),
        "dropped_questions": getattr(attempt, "dropped_questions_count", None),
        "capped_questions": list(getattr(attempt, "capped_question_ids", None) or getattr(extras, "capped_questions", None) or ()),
        "capped_mandatory_questions": list(getattr(extras, "capped_mandatory_questions", ()) or ()),
    }
    if extras is not None:
        out.update({
            "dropped_pick": bool(getattr(extras, "dropped_pick", False)), "dropped_suggestions": int(getattr(extras, "dropped_suggestions", 0) or 0),
            "unknown_sources": [list(item) for item in getattr(extras, "unknown_sources", ()) or ()],
            "asked_by_code": [str(item) for item in getattr(extras, "asked_by_code", ()) or ()],
            "structured_suggestions": len(getattr(extras, "structured_suggestions", ()) or ()), "has_pick": getattr(extras, "pick", None) is not None,
        })
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "x"


def fake_answer(labelled: Sequence[LabelRow], master: pick_eval.MasterCase, *, shape: str, answers: Mapping[str, str], pick_lines: Sequence[str] = ()) -> str:
    """A scripted answer made from the labels: each labelled row with its first accepted class and status.

    ``v8``: the shipped shape, the verdict by v8's rule 7 (two open list
    items hold it).  ``v9``: also ``class_basis``, ``alternatives`` and
    ``sources`` on the rows, structured suggestions and, for Matched, a pick
    (``pick_lines``, else every labelled strong line); the verdict by the v9
    rule (only must-haves hold it).  It is a stand-in that exercises the
    harness, not a model: its verdict need not be the labelled one.
    """

    doc = pick_eval._parse(master.markdown)
    rows: list[dict[str, Any]] = []
    questions: list[dict[str, str]] = []
    for label in labelled:
        status = label.statuses[0]
        lines = [*label.strong, *label.support]
        evidence = [" ".join((doc.text_of(lines[0]) or "").split()[:14])] if status == "met" and lines else []
        sources = list(lines[:3])
        if status in ("met", "unmet") and label.answers:
            evidence = [f"Story bank {label.answers[0]}: {answers.get(label.answers[0], '')}"]
            sources = [f"A {label.answers[0]}"] if status == "met" else []
        row: dict[str, Any] = {"requirement": label.text, "class": label.classes[0], "status": status, "resume_evidence": evidence}
        if shape == "v9":
            row["class_basis"] = label.class_basis[:200]
            if label.alternatives:
                row["alternatives"] = list(label.alternatives)
            if status == "met" and sources:
                row["sources"] = sources
        rows.append(row)
        if status == "unclear" and label.classes[0] != "nice_to_have":
            questions.append({"question_id": f"tool:{_slug(label.text)}", "question": f"Do you have experience with: {label.text}?", "requirement": label.text})
    order = {"hard": 0, "askable": 1, "list_item": 2, "nice_to_have": 3}
    rows.sort(key=lambda row: order[row["class"]])
    asked = {question["requirement"] for question in questions}
    holding = sum(1 for row in rows if row["requirement"] in asked and row["class"] in MANDATORY)
    minor = sum(1 for row in rows if row["requirement"] in asked and row["class"] == "list_item")
    if any(row["status"] == "unmet" and row["class"] == "hard" for row in rows):
        verdict, questions = NOT_A_MATCH, []
    elif holding or (shape == "v8" and minor >= 2):
        verdict = PENDING
    else:
        verdict = MATCHED
    answer: dict[str, Any] = {
        "verdict": verdict, "matrix": rows, "questions": questions,
        "not_a_match_reason": "A hard requirement is not met." if verdict == NOT_A_MATCH else None,
    }
    if shape == "v9":
        gap = next((label for label in labelled if not label.lines and not label.answers), None)
        first = next((label for label in labelled if label.strong), None)
        suggestions = []
        if first is not None and verdict == MATCHED:
            suggestions.append({"kind": "reword", "line": first.strong[0], "posting_phrase": label_phrase(first.text), "why": "This line could lead with what the posting asks for in the words it already supports."})
        if gap is not None and first is not None:
            suggestions.append({"kind": "gap", "line": first.strong[0], "why": f"Nothing in the master states this requirement: {gap.text[:120]}"})
        answer["suggestions"] = suggestions
        if verdict == MATCHED:
            chosen = list(pick_lines) or list(dict.fromkeys(line for label in labelled for line in label.strong if not line.startswith("sum-")))
            summary = next((line for label in labelled for line in label.strong if line.startswith("sum-")), None)
            answer["pick"] = {"summary": summary, "section_order": ["experience", "projects"], "lines": chosen}
    else:
        answer["suggestions"] = []
    return json.dumps(answer, ensure_ascii=False)


def label_phrase(text: str) -> str:
    return " ".join(text.split())[:60].rstrip()


# --- one case -------------------------------------------------------------------------------------------


def answer_json(response: Any, master: Any) -> dict[str, Any]:
    """The stored assessment's answer as plain data (ids, classes, the model's own strings; nothing of the resume)."""

    from gigai.scout import assess_master

    body = response.result
    citations = assess_master.MasterCitations(master)
    rows = []
    for row in body.matrix:
        traced, _skills = citations.row(row.resume_evidence)
        item: dict[str, Any] = {
            "requirement": row.requirement, "class": row.requirement_class.value if row.requirement_class else None, "status": row.status.value,
            "evidence": [text for text in row.resume_evidence if text.strip()], "traced": list(traced),
        }
        for name in ("id", "class_basis"):
            if getattr(row, name, None):
                item[name] = getattr(row, name)
        for name in ("alternatives", "sources"):
            if getattr(row, name, None):
                item[name] = list(getattr(row, name))
        rows.append(item)
    structured = [item.to_json() if hasattr(item, "to_json") else dict(item) for item in getattr(body, "structured_suggestions", None) or ()]
    pick = getattr(body, "pick", None)
    gate = getattr(response, "resume_gate", None)
    reference = getattr(response, "requirements_ref", None)
    return {
        "verdict": body.verdict.value if body.verdict is not None else None,
        "not_a_match_reason": body.not_a_match_reason,
        "matrix": rows,
        "questions": [{"question_id": item.question_id, "question": item.question, "requirement": item.requirement} for item in body.structured_questions],
        "suggestions": list(body.suggestions),
        "structured_suggestions": structured,
        "pick": pick.to_json() if pick is not None and hasattr(pick, "to_json") else None,
        "resume_gate": gate.to_json() if gate is not None and hasattr(gate, "to_json") else None,
        "requirements_ref": reference.to_json() if reference is not None and hasattr(reference, "to_json") else None,
    }


def boundary_drops(output_text: str | None, answer: Mapping[str, Any]) -> dict[str, Any] | None:
    """What the model's own answer held that the stored one does not (SPEC 1.3's lenient drops), or ``None`` when it is not JSON. Pure.

    ``unknown_sources``: sources of the model's rows that the stored rows do
    not carry (the prompt did not offer them, or the row is not ``met``).
    ``pick_dropped``: the answer carried pick lines and none was stored (a
    verdict that keeps no pick, or a hold).
    """

    text = (output_text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    try:
        raw = json.loads(text[start:end + 1]) if start >= 0 and end > start else None
    except json.JSONDecodeError:
        raw = None
    if not isinstance(raw, dict):
        return None
    stored_rows = {fold(str(row["requirement"])): row for row in answer["matrix"]}
    by_id = {str(row["id"]): row for row in answer["matrix"] if row.get("id")}
    unknown: list[dict[str, str]] = []
    raw_sources = 0
    for place, row in enumerate(raw.get("matrix") or ()):
        if not isinstance(row, dict):
            continue
        sources = [str(item) for item in row.get("sources") or () if isinstance(item, str)]
        raw_sources += len(sources)
        stored = by_id.get(str(row.get("id"))) or stored_rows.get(fold(str(row.get("requirement") or ""))) or (answer["matrix"][place] if place < len(answer["matrix"]) and len(answer["matrix"]) == len(raw.get("matrix") or ()) else None)
        kept = {" ".join(str(item).split()).lower() for item in (stored or {}).get("sources") or ()}
        unknown += [{"requirement": str(row.get("requirement") or "")[:120], "source": source} for source in sources if " ".join(source.split()).lower() not in kept]
    raw_pick = raw.get("pick") if isinstance(raw.get("pick"), dict) else None
    raw_lines = [item for item in (raw_pick or {}).get("lines") or () if isinstance(item, str)]
    raw_suggestions = [item for item in raw.get("suggestions") or () if isinstance(item, dict)]
    raw_questions = [item for item in raw.get("questions") or () if isinstance(item, dict)]
    return {
        "sources": raw_sources, "unknown_sources": unknown, "pick_lines": len(raw_lines), "pick_dropped": bool(raw_lines) and not answer.get("pick"),
        "suggestions": len(raw_suggestions), "suggestions_dropped": max(0, len(raw_suggestions) - len(answer.get("structured_suggestions") or ())),
        "suggestions_without_line": sum(1 for item in raw_suggestions if not item.get("line")),
        "questions": len(raw_questions), "questions_dropped": max(0, len(raw_questions) - len(answer["questions"])),
    }


def code_selection(home: TargetHome, profile_key: str, titles: Sequence[str], posting: Mapping[str, str], cited: Sequence[Mapping[str, Any]], today: str) -> dict[str, Any]:
    """The code selector's final selection for this case (``pick_probe``'s ``fallback`` path), in a child process."""

    payload = {
        "today": today, "paths": ["fallback"],
        "cases": [{
            "key": "case", "master": home.master_markdown,
            "profile": {"titles": list(titles), "base_ids": list(home.base_ids[profile_key]) or None, "profile_id": home.profiles[profile_key], "label": profile_key},
            "posting": {"title": posting["title"], "text": posting["text"], "company": posting["company"], "location": posting["location"], "cited": list(cited)},
        }],
    }
    return pick_eval.pick_probe.run_in_tree(payload, REPO)["results"]["case"]["fallback"]


def _shown(final: Mapping[str, Any]) -> frozenset[str]:
    return frozenset([*final["summary"], *(line for lines in final["entries"].values() for line in lines), *final["other"]])


def stored_selection(home_root: Path, home: TargetHome, profile_id: str, job_identity: str) -> dict[str, Any] | None:
    """What ``run_quick_assessment`` stored as the job's selection (the suggestion record of a tree with N3), else ``None``."""

    try:
        from gigai.scout import suggestions
    except ImportError:
        return None
    path = suggestions.suggestions_path(home_root, home.target, profile_id, job_identity)
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    selection = record.get("selection")
    out: dict[str, Any] = {"gate": record.get("gate"), "requirements": record.get("requirements"), "selection": selection, "suggestions": len(record.get("suggestions") or ())}
    if selection:
        marks = selection.get("line_marks")
        printed = [item["id"] for item in marks] if isinstance(marks, list) else list(marks or ())
        out["printed"] = printed
    return out


def run_case(
    case: Mapping[str, Any], *, spec: Mapping[str, Any], labels: Mapping[str, Any], homes: Mapping[str, TargetHome], home_root: Path, config: Any,
    budget: CallBudget, fake: FakeModel | None, shape: str, settle: bool, selector: bool, raw: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One case through ``run_quick_assessment``; the row the report stores."""

    from gigai.canonical import digest_imported_bytes
    from gigai.scout import assess_master
    from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput, ResolvedJob
    from gigai.scout.find_jobs.contracts import ModelTarget
    from gigai.scout.quick_assess import QuickAssessError, run_quick_assessment

    home = homes[case["target"]]
    posting = load_posting(spec, case["posting"])
    labelled = label_rows(labels, case["posting"], home.size)
    master_case = master_of(home.size)
    profile_id = home.profiles[case["profile"]]
    model_target = FAKE_TARGET if fake is not None else case["model_target"]
    row: dict[str, Any] = {
        "case": case["id"], "posting": case["posting"], "target": case["target"], "master": home.size, "profile": case["profile"],
        "model_target": case["model_target"], "fake_model": fake is not None,
    }
    if fake is not None:
        bank = {item["question_id"]: item["answer"] for item in spec["targets"][case["target"]].get("answers", ())}
        fake.case = case["id"]
        if fake.recorded is None:
            fake.answers[case["id"]] = fake_answer(labelled, master_case, shape=shape, answers=bank)
    job = ResolvedJob(
        job_identity=posting["url"], source_url=posting["url"], normalized_url=posting["url"], fetch_kind="generic", title=posting["title"],
        company=posting["company"], location=posting["location"], text=posting["text"], text_sha256=digest_imported_bytes(posting["text"].encode("utf-8")),
    )
    start = budget.made
    raw_start = len(raw) if raw is not None else 0
    budget.begin("assess", (case["id"], model_target))
    started = time.monotonic()
    response = None
    try:
        response = run_quick_assessment(
            AssessRequest(job=AssessJobInput(job_url=posting["url"]), resume=AssessResumeInput(profile_id=profile_id), model_target=ModelTarget(model_target)),
            home_root=home_root, target=home.target, config=config, resolved_job=job,
        )
    except QuickAssessError as exc:
        row.update({"ok": False, "error": exc.code, "error_message": str(exc)[:300]})
    calls = budget.calls[start:]
    row.update({
        "calls": len(calls), "seconds": round(time.monotonic() - started, 1),
        "call_seconds": [call.elapsed_seconds for call in calls],
        "input_tokens": [(call.usage or {}).get("input_tokens") for call in calls],
        "output_tokens": [(call.usage or {}).get("output_tokens") for call in calls],
        "prompt_chars": [len(call.prompt) for call in calls], "output_chars": [len(call.output_text or "") for call in calls],
        "resume_ids_in_prompt": bool(calls) and "<!-- id:" in calls[0].prompt.split("RESUME:", 1)[-1].split("CANDIDATE CONSTRAINTS:", 1)[0],
        "requirements_in_prompt": bool(calls) and "REQUIREMENTS (id |" in calls[0].prompt,
        "raw_usage": [item for item in raw[raw_start:] if "attempt" not in item] if raw is not None else [],
        "extras": next((item["attempt"] for item in reversed(raw[raw_start:]) if "attempt" in item), None) if raw is not None else None,
    })
    if fake is not None and fake.recorded is not None and calls:
        row["replayed"] = True
        row["replay_prompt_identical"] = calls[0].prompt == fake.recorded_prompts.get(case["id"])
    if response is None:
        return row
    answer = answer_json(response, home.master)
    basis = response.resume_basis
    row.update({
        "ok": True, "model": response.model, "prompt_version": response.prompt_version, "instructions_digest": response.instructions_digest,
        "resume_input": basis.to_json() if basis is not None and hasattr(basis, "to_json") else None,
        "answer": answer, "verdict": answer["verdict"], "boundary": boundary_drops(calls[-1].output_text if calls else None, answer),
        "rows": len(answer["matrix"]), "met": sum(item["status"] == "met" for item in answer["matrix"]),
        "unmet": sum(item["status"] == "unmet" for item in answer["matrix"]), "unclear": sum(item["status"] == "unclear" for item in answer["matrix"]),
    })
    if selector:
        cited = [item.to_json() for item in assess_master.cited_requirements(home.master, response.result.matrix)]
        started = time.monotonic()
        try:
            final = code_selection(home, case["profile"], spec["profiles"][case["profile"]]["titles"], posting, cited, pick_eval._load("sizes.json")["today"])
        except RuntimeError as exc:
            final = {"error": str(exc)}
        if "error" in final:
            row["code_selection"] = {"error": final["error"]}
        else:
            shown = _shown(final)
            row["code_selection"] = {
                "lines": len(shown), "pages": final["pages"], "empty_entries": list(final["empty_entries"]),
                "date_order": bool(final["date_order"]), "page_fit": final["pages"] is not None and final["pages"] <= 2 and not final["empty_entries"] and bool(final["date_order"]),
                "conflicts": len(final["conflicts"]), "cited_rows": len(cited), "seconds": round(time.monotonic() - started, 1), "shown": sorted(shown),
            }
    stored = stored_selection(home_root, home, profile_id, posting["url"]) if settle else None
    if stored is None:
        row["model_selection"] = None
        row["model_selection_absent"] = "--settle off" if not settle else "no suggestion record: this tree has no scout/pick.py, or the gate held the resume"
    else:
        selection = stored.get("selection")
        row["model_selection"] = {"gate": stored["gate"], "requirements": stored["requirements"], "record_suggestions": stored["suggestions"]}
        if selection:
            shown = frozenset(stored.get("printed") or ())
            row["model_selection"].update({
                "lines": len(shown), "pages": selection.get("pages"), "max_pages": selection.get("max_pages"),
                "page_fit": selection.get("pages") is not None and selection.get("pages") <= (selection.get("max_pages") or 2),
                "picked_by": selection.get("picked_by"), "fallback": selection.get("fallback"), "problems": selection.get("problems"),
                "added_by_code": selection.get("added_by_code"), "conflicts": selection.get("conflicts"), "shown": sorted(shown),
            })
    score_row(row, spec, labels)
    return row


def score_row(row: dict[str, Any], spec: Mapping[str, Any], labels: Mapping[str, Any]) -> None:
    """Everything a stored row has that comes from the LABELS, worked out again from what the row holds. No call.

    The answer against its labelled rows (``labels``), and checks 1 to 3 of
    SPEC 3.5 for each selection the row holds (by the line ids it shows).  A
    run calls it once a case; ``--rescore`` calls it for every row of a
    report after the labels were reviewed and changed.
    """

    if not row.get("ok"):
        return
    posting = load_posting(spec, row["posting"])
    size = row["master"]
    master_case = master_of(size)
    selectable = frozenset(line for line in master_case.ids if line not in master_case.entries and not line.startswith(("s-", "e-", "r-", "p-")))
    row["labels"] = analyse(
        row["answer"], label_rows(labels, row["posting"], size), labels[row["posting"]], size=size, master_ids=master_case.ids, selectable=selectable,
        facts=posting["text"] + "\n" + master_case.markdown, injection=labels[row["posting"]].get("injection"), location=posting["location"],
    )
    requirements, groups = pick_labels(labels, row["posting"], size)
    row["selection_labels"] = {"mandatory": sum(1 for req in requirements if req.mandatory and req.lines), "must_keep": len(groups)}
    for name in ("code_selection", "model_selection"):
        found = row.get(name)
        if found and "shown" in found:
            found.update(selection_checks(requirements, groups, frozenset(found["shown"])))


# --- the tables -----------------------------------------------------------------------------------------


def _sum(values: Sequence[int | None]) -> int:
    return sum(value or 0 for value in values)


def disagreements(spec: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The requirement lists of the cases that share a posting (``pairs``), two at a time."""

    by_case = {row["case"]: row for row in rows if row.get("ok")}
    out = []
    for pair in spec["pairs"]:
        left, right = (by_case.get(name) for name in pair["cases"])
        if left is None or right is None:
            out.append({"posting": pair["posting"], "cases": pair["cases"], "differs": pair["differs"], "same": pair["same"], "compared": False})
            continue
        out.append({
            "posting": pair["posting"], "cases": pair["cases"], "differs": pair["differs"], "same": pair["same"], "compared": True,
            "verdicts": [left["verdict"], right["verdict"]], **list_disagreement(left["answer"]["matrix"], right["answer"]["matrix"]),
        })
    return out


def summarize(rows: Sequence[Mapping[str, Any]], pairs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The totals the tables are printed from; every figure is a count or a mean, none a score."""

    done = [row for row in rows if row.get("ok")]
    by_target: dict[str, Any] = {}
    for name in sorted({row["model_target"] for row in rows}):
        mine = [row for row in rows if row["model_target"] == name]
        seconds = [value for row in mine for value in row["call_seconds"]]
        by_target[name] = {
            "cases": len(mine), "ok": sum(bool(row.get("ok")) for row in mine), "calls": sum(row["calls"] for row in mine),
            "models": sorted({str(row.get("model")) for row in mine if row.get("ok")}),
            "seconds_per_call_mean": round(sum(seconds) / len(seconds), 1) if seconds else None, "seconds_per_call_max": max(seconds, default=None),
            "input_tokens": sum(_sum(row["input_tokens"]) for row in mine), "output_tokens": sum(_sum(row["output_tokens"]) for row in mine),
        }
    compared = [pair for pair in pairs if pair.get("compared")]
    injected = [row for row in done if row["labels"]["injection_hits"] is not None]
    with_code = [row for row in done if row.get("code_selection") and "error" not in row["code_selection"]]
    with_model = [row for row in done if row.get("model_selection") and "lost" in row["model_selection"]]
    return {
        "cases": len(rows), "ok": len(done), "failed": [{"case": row["case"], "error": row.get("error")} for row in rows if not row.get("ok")],
        "retried": sum(row["calls"] > 1 for row in rows), "calls": sum(row["calls"] for row in rows),
        "verdicts": {SHORT[name]: sum(row["verdict"] == name for row in done) for name in (MATCHED, PENDING, NOT_A_MATCH)},
        "verdict_not_accepted": [row["case"] for row in done if not row["labels"]["verdict_ok"]],
        "gate_not_accepted": [row["case"] for row in done if not row["labels"]["gate_ok"]],
        "rows": sum(row["rows"] for row in done), "rows_missing": sum(len(row["labels"]["rows_missing"]) for row in done),
        "rows_merged": sum(len(row["labels"]["rows_merged"]) for row in done), "rows_extra": sum(len(row["labels"]["rows_extra"]) for row in done),
        "rows_eligibility": sum(len(row["labels"].get("rows_eligibility") or ()) for row in done),
        "class_wrong": sum(len(row["labels"]["class_wrong"]) for row in done), "mandatory_wrong": sum(len(row["labels"]["mandatory_wrong"]) for row in done),
        "status_wrong": sum(len(row["labels"]["status_wrong"]) for row in done),
        "questions": sum(len(row["answer"]["questions"]) for row in done),
        "questions_master_answers": sum(row["labels"]["questions_master_answers"] for row in done),
        "questions_stored_answer": sum(row["labels"]["questions_stored_answer"] for row in done),
        "questions_on_optional": sum(row["labels"]["questions_on_optional"] for row in done),
        "answers_with_sources": sum(row["labels"]["has_sources"] for row in done),
        "met_without_source": sum(len(row["labels"]["met_without_source"]) for row in done),
        "sources_not_labelled": sum(len(row["labels"]["sources_not_labelled"]) for row in done),
        "picks": sum(row["labels"]["pick"] is not None for row in done),
        "pick_unknown_ids": sum(len(row["labels"]["pick"]["unknown"]) for row in done if row["labels"]["pick"]),
        "pick_not_selectable": sum(len(row["labels"]["pick"]["not_selectable"]) for row in done if row["labels"]["pick"]),
        "suggestions": sum(row["labels"]["suggestions"] for row in done),
        "structured_suggestions": sum(len(row["labels"]["structured_suggestions"]) for row in done),
        "suggestions_labels_agree": sum(row["labels"]["suggestions_labels_agree"] for row in done),
        "suggestions_to_read": sum(row["labels"]["suggestions_to_read"] for row in done),
        "suggestions_new_numbers": sum(row["labels"]["suggestions_new_numbers"] for row in done),
        "injection": {"cases": [row["case"] for row in injected], "hits": sum(len(row["labels"]["injection_hits"]) for row in injected)},
        "code_selection": {
            "cases": len(with_code), "lost": sum(len(row["code_selection"]["lost"]) for row in with_code),
            "not_strongest": sum(len(row["code_selection"]["not_strongest"]) for row in with_code),
            "omitted": sum(len(row["code_selection"]["omitted"]) for row in with_code), "not_page_fit": sum(not row["code_selection"]["page_fit"] for row in with_code),
        },
        "model_selection": {
            "cases": len(with_model), "lost": sum(len(row["model_selection"]["lost"]) for row in with_model),
            "not_strongest": sum(len(row["model_selection"]["not_strongest"]) for row in with_model),
            "omitted": sum(len(row["model_selection"]["omitted"]) for row in with_model), "not_page_fit": sum(not row["model_selection"]["page_fit"] for row in with_model),
            "fallbacks": sorted(str(row["model_selection"]["fallback"]) for row in with_model if row["model_selection"].get("fallback")),
        },
        "by_model_target": by_target,
        "questions_on_must_have": sum(row["labels"].get("questions_on_must_have", 0) for row in done),
        "unclear_must_have_without_question": sum(len(row["labels"].get("unclear_must_have_without_question") or ()) for row in done),
        "boundary": {
            "answers_read": sum(1 for row in done if row.get("boundary")),
            "unknown_sources": sum(len(row["boundary"]["unknown_sources"]) for row in done if row.get("boundary")),
            "sources": sum(row["boundary"]["sources"] for row in done if row.get("boundary")),
            "pick_dropped": [row["case"] for row in done if row.get("boundary") and row["boundary"]["pick_dropped"]],
            "suggestions_dropped": sum(row["boundary"]["suggestions_dropped"] for row in done if row.get("boundary")),
            "questions_dropped": sum(row["boundary"]["questions_dropped"] for row in done if row.get("boundary")),
        },
        "extras": {
            "answers": sum(1 for row in done if row.get("extras")),
            "capped_questions": sum(len(row["extras"].get("capped_questions") or ()) for row in done if row.get("extras")),
            "capped_in": [row["case"] for row in done if row.get("extras") and row["extras"].get("capped_questions")],
            "dropped_questions": sum(row["extras"].get("dropped_questions") or 0 for row in done if row.get("extras")),
            "asked_by_code": sum(len(row["extras"].get("asked_by_code") or ()) for row in done if row.get("extras")),
            "dropped_pick": [row["case"] for row in done if row.get("extras") and row["extras"].get("dropped_pick")],
            "dropped_suggestions": sum(row["extras"].get("dropped_suggestions") or 0 for row in done if row.get("extras")),
            "unknown_sources": sum(len(row["extras"].get("unknown_sources") or ()) for row in done if row.get("extras")),
        },
        "picked_by": {name: sum(1 for row in with_model if row["model_selection"].get("picked_by") == name) for name in ("model", "code")},
        "model_against_code": model_against_code(done),
        "disagreement": {
            "pairs": len(compared), "rows": sum(pair["same_words"] + pair["close_words"] + len(pair["only_left"]) + len(pair["only_right"]) for pair in compared),
            "without_counterpart": sum(len(pair["only_left"]) + len(pair["only_right"]) for pair in compared),
            "rate_mean": round(sum(pair["rate"] for pair in compared) / len(compared), 3) if compared else None,
            "rate_same_words_mean": round(sum(pair["rate_same_words"] for pair in compared) / len(compared), 3) if compared else None,
            "class_differs": sum(len(pair["class_differs"]) for pair in compared),
        },
    }


def model_against_code(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The model's selection beside the code selector's, case by case, on checks 1 to 3 (never added up). Pure.

    ``worse``: the model's selection has more of something on at least one
    check.  ``better``: fewer on at least one and more on none.  Only the
    cases where the stored selection was picked by the model are counted
    under ``picked_by_model``; a fallback is the code selector's own.
    """

    names = ("lost", "not_strongest", "omitted")
    out: dict[str, Any] = {"cases": 0, "picked_by_model": 0, "worse": [], "better": [], "same": []}
    for row in rows:
        mine, code = row.get("model_selection"), row.get("code_selection")
        if not mine or "lost" not in mine or not code or "lost" not in code:
            continue
        out["cases"] += 1
        out["picked_by_model"] += mine.get("picked_by") == "model"
        counts = [(len(mine[name]), len(code[name])) for name in names]
        item = {"case": row["case"], "picked_by": mine.get("picked_by"), **{name: {"model": len(mine[name]), "code": len(code[name])} for name in names}}
        if any(a > b for a, b in counts):
            out["worse"].append(item)
        elif any(a < b for a, b in counts):
            out["better"].append(item)
        else:
            out["same"].append(row["case"])
    return out


def table(rows: Sequence[Mapping[str, Any]]) -> str:
    """The verdict / time / token table, one line a case, as markdown."""

    out = [
        "| case | CLI | model | verdict | accepted? | gate (1.5) | rows met/unmet/unclear | questions (master answers) | calls | s/call | tokens in/out | prompt chars |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        if not row.get("ok"):
            out.append(f"| {row['case']} | {row['model_target']} | - | FAILED: {row.get('error')} | - | - | - | - | {row['calls']} | {'/'.join(str(value) for value in row['call_seconds']) or '-'} | {_sum(row['input_tokens'])}/{_sum(row['output_tokens'])} | {'/'.join(str(value) for value in row['prompt_chars']) or '-'} |")
            continue
        labels = row["labels"]
        out.append(
            f"| {row['case']} | {row['model_target']} | {row.get('model') or '-'} | {SHORT[row['verdict']]} | {'yes' if labels['verdict_ok'] else 'NO (' + ', '.join(SHORT[name] for name in labels['verdict_accepted']) + ')'} | "
            f"{labels['gate_by_spec']}{'' if labels['gate_ok'] else ' (NOT ' + '/'.join(labels['gate_accepted']) + ')'} | {row['rows']}: {row['met']}/{row['unmet']}/{row['unclear']} | "
            f"{len(row['answer']['questions'])} ({labels['questions_master_answers']}) | {row['calls']} | {'/'.join(str(value) for value in row['call_seconds'])} | "
            f"{_sum(row['input_tokens'])}/{_sum(row['output_tokens'])} | {'/'.join(str(value) for value in row['prompt_chars'])} |"
        )
    return "\n".join(out)


def print_report(report: Mapping[str, Any], *, out: Any = None) -> None:
    out = out or sys.stdout
    run, summary = report["run"], report["summary"]
    print(f"{run['label']}: assess.md {run['instructions_digest']} prompt {', '.join(run['prompt_versions']) or '-'} head {run['head']} calls {run['calls_made']}/{run['max_calls']} skipped {run['skipped'] or 'none'}", file=out)
    print("", file=out)
    print(table(report["rows"]), file=out)
    print("", file=out)
    for name, line in summary["by_model_target"].items():
        print(f"{name} ({', '.join(line['models']) or '-'}): {line['ok']}/{line['cases']} ok, {line['calls']} calls, {line['seconds_per_call_mean']} s/call mean (max {line['seconds_per_call_max']}), tokens {line['input_tokens']} in / {line['output_tokens']} out", file=out)
    if any(line["cases"] and line["input_tokens"] < 100 * line["calls"] for line in summary["by_model_target"].values()):
        print("  (a CLI that counts the prompt as cache tokens reports almost no input tokens: the prompt's size is the last column, and each call's raw usage is in the report)", file=out)
    print(f"verdicts: {summary['verdicts']}; not accepted by the labels: {summary['verdict_not_accepted'] or 'none'}; gate not accepted: {summary['gate_not_accepted'] or 'none'}", file=out)
    print(f"rows {summary['rows']}: labelled rows missing {summary['rows_missing']}, merged {summary['rows_merged']}, extra rows {summary['rows_extra']} (and {summary['rows_eligibility']} rows about the candidate's location, which no label lists); class not accepted {summary['class_wrong']} (must-have against optional: {summary['mandatory_wrong']}); status not accepted {summary['status_wrong']}", file=out)
    print(f"questions by the class of their row: {summary['questions_on_must_have']} on must-have rows, {summary['questions_on_optional']} on optional rows; unclear must-have rows left without a question: {summary['unclear_must_have_without_question']}", file=out)
    line = summary["boundary"]
    print(f"dropped at the boundary ({line['answers_read']} answers read): {line['unknown_sources']} of {line['sources']} sources not kept; pick dropped in {line['pick_dropped'] or 'no case'}; suggestions not kept {line['suggestions_dropped']}; questions not kept {line['questions_dropped']}", file=out)
    line = summary["extras"]
    print(f"recorded by the product's boundary ({line['answers']} answers): questions on optional rows dropped past the cap {line['capped_questions']} (in {line['capped_in'] or 'no case'}); other dropped questions {line['dropped_questions']}; questions code asked for an unclear must-have {line['asked_by_code']}; pick dropped in {line['dropped_pick'] or 'no case'}; suggestions dropped {line['dropped_suggestions']}; unknown sources {line['unknown_sources']}", file=out)
    versus = summary["model_against_code"]
    print(f"the model's selection against the code selector's ({versus['cases']} cases, {versus['picked_by_model']} picked by the model; stored selections by picker: {summary['picked_by']}): worse on a check in {[item['case'] for item in versus['worse']] or 'none'}; better in {[item['case'] for item in versus['better']] or 'none'}; the same in {len(versus['same'])}", file=out)
    print(f"questions {summary['questions']}: on a row the master already answers {summary['questions_master_answers']}, a stored answer settles {summary['questions_stored_answer']}, on optional rows {summary['questions_on_optional']}", file=out)
    print(f"sources: answers with sources {summary['answers_with_sources']}; met must-have rows with none {summary['met_without_source']}; sources or traced lines the labels do not list {summary['sources_not_labelled']}", file=out)
    print(f"pick: answers with a pick {summary['picks']}; unknown ids {summary['pick_unknown_ids']}; not selectable {summary['pick_not_selectable']}", file=out)
    for name in ("code_selection", "model_selection"):
        line = summary[name]
        print(f"{name.replace('_', ' ')}: {line['cases']} cases; mandatory left without a line {line['lost']}; covered without the strongest line {line['not_strongest']}; must-keep groups not shown {line['omitted']}; not a page fit {line['not_page_fit']}" + (f"; fallbacks {line['fallbacks']}" if line.get("fallbacks") else ""), file=out)
    print(f"suggestions: {summary['suggestions']} sentences, {summary['structured_suggestions']} structured ({summary['suggestions_labels_agree']} the labels agree with, {summary['suggestions_to_read']} to read, {summary['suggestions_new_numbers']} with a number neither the posting nor the master holds)", file=out)
    print(f"injection: cases {summary['injection']['cases'] or 'none'}, hits {summary['injection']['hits']}", file=out)
    print("", file=out)
    print("the same posting, another resume (first assessments):", file=out)
    for pair in report["disagreement"]:
        if not pair["compared"]:
            print(f"  {pair['posting']}: not compared (a case is missing)", file=out)
            continue
        print(
            f"  {pair['posting']} ({' vs '.join(pair['cases'])}; differs: {', '.join(pair['differs'])}; same: {', '.join(pair['same'])}): rows {pair['rows_left']} / {pair['rows_right']}, "
            f"same words {pair['same_words']}, close words {pair['close_words']}, only left {len(pair['only_left'])}, only right {len(pair['only_right'])}, class differs {len(pair['class_differs'])}; "
            f"disagreement {pair['rate']:.0%} (by identical wording {pair['rate_same_words']:.0%}); verdicts {SHORT[pair['verdicts'][0]]} / {SHORT[pair['verdicts'][1]]}",
            file=out,
        )
    line = summary["disagreement"]
    print(f"  in all: {line['pairs']} pairs, {line['without_counterpart']} of {line['rows']} rows without a counterpart, mean rate {line['rate_mean']} (by identical wording {line['rate_same_words_mean']}), class differs on {line['class_differs']} shared rows", file=out)


def compare(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Two reports case by case. Pure. A verdict is WORSE when its rank fell (matched 2, pending 1, not a match 0)."""

    first = {row["case"]: row for row in before["rows"]}
    lines, worse, better = [], [], []
    for row in after["rows"]:
        old = first.get(row["case"])
        if old is None:
            continue
        line: dict[str, Any] = {
            "case": row["case"], "before": old.get("verdict") if old.get("ok") else "failed", "after": row.get("verdict") if row.get("ok") else "failed",
            "before_ok": bool(old.get("ok") and old["labels"]["verdict_ok"]), "after_ok": bool(row.get("ok") and row["labels"]["verdict_ok"]),
            "questions": [len(old["answer"]["questions"]) if old.get("ok") else None, len(row["answer"]["questions"]) if row.get("ok") else None],
            "questions_master_answers": [old["labels"]["questions_master_answers"] if old.get("ok") else None, row["labels"]["questions_master_answers"] if row.get("ok") else None],
            "questions_must_have": [old["labels"].get("questions_on_must_have") if old.get("ok") else None, row["labels"].get("questions_on_must_have") if row.get("ok") else None],
            "questions_optional": [old["labels"].get("questions_on_optional") if old.get("ok") else None, row["labels"].get("questions_on_optional") if row.get("ok") else None],
            "seconds": [sum(old["call_seconds"]), sum(row["call_seconds"])], "calls": [old["calls"], row["calls"]],
            "input_tokens": [_sum(old["input_tokens"]), _sum(row["input_tokens"])], "output_tokens": [_sum(old["output_tokens"]), _sum(row["output_tokens"])],
            "same_model_target": old["model_target"] == row["model_target"],
        }
        if old.get("ok") and row.get("ok"):
            line["lists"] = list_disagreement(old["answer"]["matrix"], row["answer"]["matrix"])
            if RANK[row["verdict"]] < RANK[old["verdict"]]:
                worse.append({"case": row["case"], "before": old["verdict"], "after": row["verdict"], "accepted": row["labels"]["verdict_accepted"], "wrong_by_labels": not row["labels"]["verdict_ok"], "rows": row["answer"]["matrix"]})
            elif RANK[row["verdict"]] > RANK[old["verdict"]]:
                better.append({"case": row["case"], "before": old["verdict"], "after": row["verdict"], "wrong_by_labels": not row["labels"]["verdict_ok"]})
        lines.append(line)
    seconds = {}
    for name in MODEL_TARGETS:
        old_calls = [value for row in before["rows"] if row["model_target"] == name for value in row["call_seconds"]]
        new_calls = [value for row in after["rows"] if row["model_target"] == name for value in row["call_seconds"]]
        if old_calls and new_calls:
            old_mean, new_mean = sum(old_calls) / len(old_calls), sum(new_calls) / len(new_calls)
            seconds[name] = {"before": round(old_mean, 1), "after": round(new_mean, 1), "ratio": round(new_mean / old_mean, 2) if old_mean else None}
    return {"cases": lines, "worse": worse, "better": better, "seconds_per_call": seconds}


def print_comparison(result: Mapping[str, Any], *, out: Any = None) -> None:
    out = out or sys.stdout
    print("| case | verdict before | after | accepted before/after | questions on must-have rows | on optional rows | on rows the master answers | s | tokens in | tokens out | rows without a counterpart |", file=out)
    print("|---|---|---|---|---|---|---|---|---|---|---|", file=out)
    for line in result["cases"]:
        lists = line.get("lists")
        print(
            f"| {line['case']} | {SHORT.get(line['before'], line['before'])} | {SHORT.get(line['after'], line['after'])} | {'yes' if line['before_ok'] else 'no'}/{'yes' if line['after_ok'] else 'no'} | "
            f"{line['questions_must_have'][0]} -> {line['questions_must_have'][1]} | {line['questions_optional'][0]} -> {line['questions_optional'][1]} | {line['questions_master_answers'][0]} -> {line['questions_master_answers'][1]} | "
            f"{line['seconds'][0]:.0f} -> {line['seconds'][1]:.0f} | {line['input_tokens'][0]} -> {line['input_tokens'][1]} | {line['output_tokens'][0]} -> {line['output_tokens'][1]} | "
            f"{(str(len(lists['only_left']) + len(lists['only_right'])) + ' of ' + str(lists['same_words'] + lists['close_words'] + len(lists['only_left']) + len(lists['only_right']))) if lists else '-'} |",
            file=out,
        )
    print("", file=out)
    for item in result["worse"]:
        print(f"WORSE: {item['case']}: {SHORT[item['before']]} -> {SHORT[item['after']]}; the labels call the new verdict {'WRONG' if item['wrong_by_labels'] else 'acceptable'}", file=out)
        for row in item["rows"]:
            print(f"    {row['class']:12} {row['status']:8} {row['requirement']}", file=out)
    for item in result["better"]:
        print(f"better: {item['case']}: {SHORT[item['before']]} -> {SHORT[item['after']]}{' (not accepted by the labels)' if item['wrong_by_labels'] else ''}", file=out)
    for name, line in result["seconds_per_call"].items():
        print(f"{name}: {line['before']} s/call before, {line['after']} after, ratio {line['ratio']} (the bound to ship v9 is 1.5)", file=out)


def cross_disagreement(reports: Sequence[Mapping[str, Any]], names: Sequence[str]) -> list[dict[str, Any]]:
    """For every posting two or more of ``reports`` assessed: its requirement lists, two at a time."""

    found: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for name, report in zip(names, reports):
        for row in report["rows"]:
            if row.get("ok"):
                found.setdefault(row["posting"], []).append((f"{name}:{row['case']}", row))
    out = []
    for posting, items in sorted(found.items()):
        for left in range(len(items)):
            for right in range(left + 1, len(items)):
                a, b = items[left][1], items[right][1]
                out.append({
                    "posting": posting, "left": items[left][0], "right": items[right][0],
                    "differs": [key for key in ("model_target", "profile", "master", "prompt_version") if a.get(key) != b.get(key)],
                    **list_disagreement(a["answer"]["matrix"], b["answer"]["matrix"]),
                })
    return out


# --- main -----------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="0.1.11 N3 (SPEC 8.2): the live eval of the assessment that picks the resume (20 synthetic cases)")
    parser.add_argument("--label", default="run", help="what this run is in the report: before, after, after-2 ...")
    parser.add_argument("--case", action="append", default=None, help="run only this case (repeatable); default: all 20")
    parser.add_argument("--model-target", default="as-labelled", choices=TARGET_MODES, help="as-labelled (each case's own CLI), swapped (the other one), or one CLI for every case")
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS, help=f"hard cap on model calls, the product's retry included (default {DEFAULT_MAX_CALLS}: 20 cases + 1)")
    parser.add_argument("--concurrency", type=int, default=1, help="calls in flight at once: only 1 is supported")
    parser.add_argument("--report", type=Path, default=None, help="where to write the JSON report (required for a run)")
    parser.add_argument("--dump-dir", type=Path, default=None, help="write every prompt and raw model output per call under this directory")
    parser.add_argument("--home", type=Path, default=None, help="the SCRATCH GigAI home (made by `gigai setup --non-interactive`); never the operator's")
    parser.add_argument("--reuse-home", action="store_true", help="allow a scratch home that already holds Scout data (each run still builds its own targets)")
    parser.add_argument("--fake-model", action="store_true", help="offline: a temp home and answers scripted from the labels")
    parser.add_argument("--replay-dir", type=Path, default=None, help="with --fake-model: answer each case with the answer a live run recorded for it (that run's --dump-dir); no call")
    parser.add_argument("--fake-shape", default="v8", choices=SHAPES, help="the shape of the scripted answers (v9 needs a tree with N3 steps 1 and 2)")
    parser.add_argument("--settle", default="auto", choices=("auto", "off"), help="auto: read the selection run_quick_assessment stored (a tree with scout/pick.py); off: do not")
    parser.add_argument("--no-selector", action="store_true", help="skip the code selector's selection of each case")
    parser.add_argument("--dry-run", action="store_true", help="print the planned cases and exit without any model call")
    parser.add_argument(
        "--stop-after-failures",
        type=int,
        default=2,
        help="stop the run when this many cases in a row fail (a CLI that is signed out fails every case: the budget is kept); 0: never",
    )
    parser.add_argument("--compare", nargs=2, type=Path, default=None, metavar=("BEFORE_JSON", "AFTER_JSON"), help="no calls: the two reports case by case")
    parser.add_argument("--rescore", type=Path, default=None, metavar="REPORT_JSON", help="no calls: score a stored report again with the labels as they are now; writes --report")
    parser.add_argument("--disagreement", nargs="+", type=Path, default=None, metavar="REPORT_JSON", help="no calls: the requirement lists of every posting the reports share, two at a time")
    parser.add_argument("--quiet", action="store_true")
    return parser


def fixture_digests() -> dict[str, str]:
    """The digests of the cases and the labels a report was scored with (the labels are reviewed and may change)."""

    from gigai.canonical import digest_imported_bytes

    return {"cases_digest": digest_imported_bytes(CASES_PATH.read_bytes()), "labels_digest": digest_imported_bytes(LABELS_PATH.read_bytes())}


def instructions_digest() -> str:
    """The digest of ``assess.md`` as it is on disk NOW (it is read again on every render)."""

    from gigai.canonical import digest_imported_bytes
    from gigai.scout.assessment_core import _instruction_bytes

    return digest_imported_bytes(_instruction_bytes())


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.compare:
        before, after = (json.loads(path.read_text(encoding="utf-8")) for path in args.compare)
        for path, report in zip(args.compare, (before, after)):
            print(f"{path.name}: {report['run']['label']} assess.md {report['run']['instructions_digest']} calls {report['run']['calls_made']}/{report['run']['max_calls']}")
        print("")
        print_comparison(compare(before, after))
        return 0
    if args.disagreement:
        reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.disagreement]
        for item in cross_disagreement(reports, [path.stem for path in args.disagreement]):
            print(
                f"{item['posting']}: {item['left']} vs {item['right']} (differs: {', '.join(item['differs']) or 'nothing named'}): rows {item['rows_left']} / {item['rows_right']}, "
                f"same words {item['same_words']}, close {item['close_words']}, only left {len(item['only_left'])}, only right {len(item['only_right'])}, class differs {len(item['class_differs'])}; "
                f"disagreement {item['rate']:.0%} (by identical wording {item['rate_same_words']:.0%})"
            )
        return 0
    spec, labels = load_spec(), load_labels()
    if args.rescore:
        if args.report is None:
            print("--report is required with --rescore (the rescored report is a new file)", file=sys.stderr)
            return 2
        report = json.loads(args.rescore.read_text(encoding="utf-8"))
        for row in report["rows"]:
            score_row(row, spec, labels)
        pairs = disagreements(spec, report["rows"])
        report["run"].update({"rescored_from": args.rescore.name, "rescored_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), **fixture_digests()})
        report.update({"summary": summarize(report["rows"], pairs), "disagreement": pairs})
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        if not args.quiet:
            print_report(report)
            print(f"report: {args.report}")
        return 0
    try:
        planned = plan(spec, args.case, args.model_target)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.dry_run:
        for index, case in enumerate(planned, 1):
            print(f"{index:3} {case['id']:24} {case['posting']:18} {case['target']:9} {case['profile']:4} {case['model_target']}")
        print(f"{len(planned)} planned calls: --max-calls {len(planned) + 1} lets every one run when at most one answer is retried")
        return 0
    if args.concurrency != 1:
        print("--concurrency: only 1 is supported (one call at a time)", file=sys.stderr)
        return 2
    if args.report is None:
        print("--report is required", file=sys.stderr)
        return 2
    if not args.fake_model:
        if os.environ.get(LIVE_ENV) != "1":
            print(f"refusing the live eval: set {LIVE_ENV}=1 explicitly", file=sys.stderr)
            return 2
        if args.home is None:
            print("--home is required for a live run: a scratch home made by `gigai setup --non-interactive --home <scratch>/home ...`", file=sys.stderr)
            return 2
        refused = refuse_home(args.home)
        if refused is None and (args.home / "scout").exists() and not args.reuse_home:
            refused = f"{args.home} already holds Scout data: pass --reuse-home to add this run's targets to it, or make a fresh scratch home"
        if refused:
            print(refused, file=sys.stderr)
            return 2

    now = datetime.now(UTC)
    budget = CallBudget(max_calls=args.max_calls)
    if args.replay_dir is not None and not args.fake_model:
        print("--replay-dir needs --fake-model (a replay calls no model)", file=sys.stderr)
        return 2
    fake = FakeModel(args.replay_dir) if args.fake_model else None
    raw: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    digest_at_start = instructions_digest()
    run: dict[str, Any] = {}

    def save() -> None:
        from gigai.scout.assessment_core import INSTRUCTIONS_DIGEST

        pairs = disagreements(spec, rows)
        run.update({
            "label": args.label, "started_at": now.isoformat().replace("+00:00", "Z"), "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "head": harness._git_head(), "fake_model": args.fake_model, "fake_shape": args.fake_shape if args.fake_model and args.replay_dir is None else None,
            "replay_of": os.fspath(args.replay_dir) if args.replay_dir is not None else None,
            "model_target_mode": args.model_target, "instructions_digest": digest_at_start, "instructions_digest_at_end": instructions_digest(),
            "instructions_digest_at_import": INSTRUCTIONS_DIGEST, "prompt_versions": sorted({str(row["prompt_version"]) for row in rows if row.get("ok")}),
            "max_calls": args.max_calls, "calls_made": budget.made, "planned_calls": len(planned), "skipped": skipped,
            "settle": args.settle, "selector": not args.no_selector, "candidate": spec["candidate"], "home": os.fspath(home_root),
            **fixture_digests(),
        })
        calls = [
            {"index": call.index, "case": (call.row_key or ("", ""))[0], "model_target": (call.row_key or ("", ""))[1], "attempt": call.attempt, "seconds": call.elapsed_seconds,
             "usage": call.usage, "error": call.error, "prompt_chars": len(call.prompt), "output_chars": len(call.output_text or "")}
            for call in budget.calls
        ]
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps({"schema": REPORT_SCHEMA, "run": run, "summary": summarize(rows, pairs), "disagreement": pairs, "rows": rows, "calls": calls}, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    with tempfile.TemporaryDirectory(prefix="gigai-assess-pick-eval-") as tmp:
        if args.fake_model:
            scratch = Path(tmp).resolve()  # the records refuse a source under a redirected parent (/var -> /private/var)
            home_root = scratch / "home"
            home_root.mkdir()
            config = harness.build_fake_config(home_root)
            root = scratch / "targets"
        else:
            from gigai.config import load_config

            home_root = args.home.expanduser().resolve()
            config = load_config(home_root)
            root = home_root.parent / "targets" / f"{args.label}-{now.strftime('%Y%m%dT%H%M%SZ')}"
        root.mkdir(parents=True, exist_ok=False)
        wanted = list(dict.fromkeys(case["target"] for case in planned))
        homes: dict[str, TargetHome] = {}
        for name in wanted:
            homes[name] = build_target(home_root, root, name, spec)
            if not args.quiet:
                print(f"target {name}: master {homes[name].size}, selections {', '.join(f'{key} {len(ids)} lines' for key, ids in homes[name].base_ids.items())}", file=sys.stderr)
        with model_seam(budget, fake, raw):
            failed_in_a_row = 0
            for case in planned:
                if args.stop_after_failures and failed_in_a_row >= args.stop_after_failures:
                    skipped.append(case["id"])
                    continue
                # A case needs room for its call and that call's one retry.
                if budget.made + 2 > args.max_calls:
                    skipped.append(case["id"])
                    continue
                try:
                    row = run_case(
                        case, spec=spec, labels=labels, homes=homes, home_root=home_root, config=config, budget=budget, fake=fake, shape=args.fake_shape,
                        settle=args.settle == "auto", selector=not args.no_selector, raw=raw,
                    )
                except CallCapReached:
                    skipped.append(case["id"])
                    continue
                rows.append(row)
                failed_in_a_row = 0 if row.get("ok") else failed_in_a_row + 1
                save()  # after every case: a stopped run keeps what it measured
                if not args.quiet:
                    verdict = SHORT[row["verdict"]] if row.get("ok") else f"FAILED {row.get('error')}"
                    print(f"{row['case']:24} {row['model_target']:10} {verdict:12} {row['calls']} call(s) {'/'.join(str(value) for value in row['call_seconds'])} s", file=sys.stderr)
        save()
        if args.dump_dir is not None:
            args.dump_dir.mkdir(parents=True, exist_ok=True)
            for call in budget.calls:
                stem = f"{call.index:03}-{(call.row_key or ('none', ''))[0]}-attempt{call.attempt}"
                (args.dump_dir / f"{stem}.prompt.txt").write_text(call.prompt, encoding="utf-8")
                (args.dump_dir / f"{stem}.output.txt").write_text(call.output_text or f"<{call.error}>", encoding="utf-8")
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if not args.quiet:
        print_report(report)
        print(f"report: {args.report}")
    if run["instructions_digest"] != run["instructions_digest_at_end"]:
        print("assess.md CHANGED during the run: this report measures no single prompt", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
