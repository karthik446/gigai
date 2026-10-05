#!/usr/bin/env python3
"""0.1.11 (orchestrator #68): score a results folder of the real-data assess eval against a GOLD KEY, in code.

The judge is not a fixed ruler: two judges read the same held result both
ways.  So the verdict, the gate, the questions and the requirement list of
every result are scored against one key built once per set, blind to every
result (two independent passes and a reconcile; the disagreements are a
CONTESTED list the operator settles).  The key is PRIVATE and lives outside
this repository (``<data-dir>/gold/key.json``); nothing of it, of a posting or
of a result is ever written here: ``--out`` inside the repository is refused.

THE KEY (``gigai-gold-key:1``), per posting id (the two digits its slug starts
with):
- ``lines``: every non-empty line of the stored posting text, numbered from 1
  as :func:`number_lines` numbers it, as ``{n, text_hash, class, alternatives,
  soft, reason, rows[{item, class, alternatives}], either}``.  A line that
  states a requirement has ``soft: false`` and its rows (one, or one per tool of
  a list the posting words as required: ``item`` is the tool); every other line
  is ``soft`` with a one-word reason.  Line 0 is the header LOCATION line.
- ``musts``: the hard and askable rows, ``{line, item, class, status,
  settled_by, status_either}``; ``optional``: the list_item
  rows, same shape.
- ``verdict``, ``gate`` (``resume`` / ``held`` / ``no_resume``),
  ``questions_allowed`` (``must``: the unclear must-have rows in priority order,
  at most ``max_must`` asked in one answer; ``optional``: the unclear list_item
  rows), ``location`` (``status``, ``ask``) and ``sponsorship``.

A RESULT is ``<results>/<NN-slug>/<cli>/assessment.json`` as
``run_assess_real_eval`` writes it (any JSON object holding ``verdict`` and
``matrix`` is read, wherever it sits in the file).  A row is tied to a posting
line by its own ``line`` when the product stores one, else by the words of its
requirement and class_basis against the posting's lines (``--postings``).

A line the key marks ``either`` (``{"classes": [...]}``) is one where a row and
no row are both defensible: a row there is not an extra row, a missing one is
not missing, and only a class outside ``classes`` is wrong.  A must-have row
with ``status_either`` is accepted with any of those statuses, asked or not.
Alternatives are not compared; a must-have "A or B" line is one row, so a
second row on it is an extra row.

WHAT IS REPORTED, per result: verdict and gate right or wrong; the location
answer; every question as ``right`` (on a row the key leaves unclear),
``over`` (on a row the key settles, on a soft line, on a nice-to-have, about
sponsorship, or past the cap), ``either`` or ``optional``; ``under_asks``
(unclear must-have rows the result owed a question and did not ask, the cap
respected); the requirement list (key rows missing, class wrong, rows on soft
lines, rows on no line, lines covered); the status of each must-have row; and
``fully_correct`` (all of: verdict, gate, location, no over-ask, no under-ask,
the must-have rows and their statuses).  No judgement beyond the key: a result
agrees with it or it does not.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

KEY_SCHEMA = "gigai-gold-key:1"
SCORE_SCHEMA = "gigai-gold-score:1"
MANDATORY = ("hard", "askable")
GATE_OF = {"matched_above_threshold": "resume", "pending_user_answers": "held", "not_a_match": "no_resume"}
HEADER = ("title", "company", "location", "url")
ELIG_IDS = ("elig-location", "elig-region", "elig-work-mode")
SPONSORSHIP_WORDS = ("sponsor", "authorization", "authorisation", "eligib", "work_permit", "visa")
MATCH_FLOOR = 0.6
TOTALS = ("results", "unread", "fully_correct", "verdict_right", "gate_right", "location_right", "over_asks", "under_asks", "musts_right", "list_exact",
          "rows_missing", "rows_extra", "rows_unmapped", "class_wrong", "status_wrong", "required_lines", "lines_covered")
REPO_ROOT = Path(__file__).resolve().parents[2]


# --- the posting's lines ------------------------------------------------------------------------------------


def number_lines(posting_file_text: str) -> list[dict[str, Any]]:
    """``[{n, text, text_hash}]``: every non-empty line after the header, stripped, numbered from 1. Pure."""

    raw = posting_file_text.splitlines()
    seen: set[str] = set()
    start = 0
    for start, line in enumerate(raw):  # noqa: B007 - the index after the header is the result
        name = line.split(":", 1)[0]
        if ":" not in line or name not in HEADER or name in seen:
            break
        seen.add(name)
    else:
        start = len(raw)
    body = [line.strip() for line in raw[start:] if line.strip()]
    return [{"n": n, "text": text, "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]} for n, text in enumerate(body, 1)]


def words(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9+#]+", text.casefold()))


def fold(text: object) -> str:
    return " ".join(re.findall(r"[a-z0-9+#]+", str(text or "").casefold()))


def contained(left: str, right: str) -> float:
    """The share of the first text's words the second holds."""

    a, b = words(left), words(right)
    return len(a & b) / len(a) if a and b else 0.0


# --- reading a result --------------------------------------------------------------------------------------


def find_answer(node: object) -> Mapping[str, Any] | None:
    """The first object, depth first, that holds a ``verdict`` and a ``matrix`` list. Pure."""

    if isinstance(node, Mapping):
        if isinstance(node.get("matrix"), list) and "verdict" in node:
            return node
        for value in node.values():
            found = find_answer(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = find_answer(value)
            if found is not None:
                return found
    return None


def result_gate(answer: Mapping[str, Any], whole: object) -> str | None:
    """``resume`` / ``held`` / ``no_resume``: from the stored gate decision when the file holds one, else from the verdict."""

    for holder in (answer, whole if isinstance(whole, Mapping) else {}):
        gate = holder.get("resume_gate")
        decision = gate.get("decision") if isinstance(gate, Mapping) else None
        if isinstance(decision, str):
            if "suggest" in decision:
                return "resume"
            return "no_resume" if "not_a_match" in decision else "held"
    return GATE_OF.get(str(answer.get("verdict")))


def row_class(row: Mapping[str, Any]) -> str | None:
    value = row.get("class", row.get("requirement_class"))
    return str(value) if value is not None else None


def map_row(row: Mapping[str, Any], lines: Sequence[Mapping[str, Any]], required: frozenset[int]) -> int | None:
    """The posting line a result row stands on: its own ``line``, else the line that holds most of its words. Pure.

    A row about the candidate (an ``elig-`` id) is line 0.  Between equal lines
    the one the class_basis also points at wins, then a requirement line of the
    key, then the first; a row under :data:`MATCH_FLOOR` is on no line.
    """

    if str(row.get("id") or "") in ELIG_IDS:
        return 0
    own = row.get("line")
    if isinstance(own, int) and not isinstance(own, bool):
        return own if own == 0 or any(line["n"] == own for line in lines) else None
    requirement, basis = str(row.get("requirement") or ""), str(row.get("class_basis") or "")
    best: tuple[float, float, int, int] | None = None
    for line in lines:
        score = contained(requirement, line["text"])
        if score < MATCH_FLOOR:
            continue
        rank = (score, contained(basis, line["text"]), int(line["n"] in required), -line["n"])
        if best is None or rank > best:
            best = rank
    return None if best is None else -best[3]


def match_rows(mapped: Sequence[tuple[int | None, Mapping[str, Any]]], key_rows: Sequence[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    """``{index of a key row: the result row that is it}``. Pure.

    On a line the key splits per tool, a result row is the tool whose name its
    requirement holds; the whole-line row takes a result row no tool claimed.
    """

    out: dict[int, Mapping[str, Any]] = {}
    used: set[int] = set()
    for with_item in (True, False):
        for index, key_row in enumerate(key_rows):
            if bool(key_row.get("item")) != with_item or index in out:
                continue
            for position, (line, row) in enumerate(mapped):
                if position in used or line != key_row["line"]:
                    continue
                if with_item and key_row["line"] != 0:  # line 0 is the one row about the candidate's place, whatever its elig- id
                    name = fold(key_row["item"])
                    text = fold(row.get("requirement")) + " " + fold(row.get("id"))
                    if not (name and (name in text or contained(str(key_row["item"]), str(row.get("requirement") or "")) == 1.0)):
                        continue
                out[index] = row
                used.add(position)
                break
    return out


# --- the key, read one of two ways ---------------------------------------------------------------------------


def key_view(key: Mapping[str, Any]) -> dict[str, Any]:
    """One posting's key with the verdicts and gates it accepts, computed from its must-have rows. Pure.

    A row whose status is open either way (``status_either``) holds nothing by
    itself, so when only such rows are open both ``matched`` and ``pending`` are
    accepted.
    """

    musts = list(key["musts"])
    firm_open = [row for row in musts if row["status"] == "unclear" and not row.get("status_either")]
    loose_open = [row for row in musts if row.get("status_either")]
    if any(row["class"] == "hard" and row["status"] == "unmet" for row in musts):
        verdicts = ["not_a_match"]
    elif firm_open:
        verdicts = ["pending_user_answers"]
    else:
        verdicts = ["matched_above_threshold", "pending_user_answers"] if loose_open else ["matched_above_threshold"]
    return {**key, "musts": musts, "verdicts": verdicts, "gates": [GATE_OF[verdict] for verdict in verdicts]}


# --- scoring one result ------------------------------------------------------------------------------------


def score_result(key: Mapping[str, Any], whole: object, lines: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One result against one posting's key. Pure."""

    answer = find_answer(whole)
    if answer is None:
        return {"ok": False, "error": "no object with verdict and matrix"}
    view = key_view(key)
    limits = key["questions_allowed"]
    max_must, max_optional = int(limits.get("max_must", 4)), int(limits.get("max_optional", 3))
    required = frozenset(line["n"] for line in key["lines"] if not line["soft"])
    soft = {line["n"]: line["reason"] for line in key["lines"] if line["soft"]}
    either = {line["n"]: line["either"] for line in key["lines"] if line.get("either")}
    key_rows = [{**row, "kind": "must"} for row in view["musts"]] + [{**row, "kind": "optional"} for row in key.get("optional", ())]
    key_rows += [{"line": line["n"], "item": row.get("item"), "class": "nice_to_have", "status": None, "kind": "nice"} for line in key["lines"] for row in line.get("rows", ()) if row["class"] == "nice_to_have"]
    rows = [row for row in answer["matrix"] if isinstance(row, Mapping)]
    mapped = [(map_row(row, lines, required | frozenset(either)), row) for row in rows]
    line_of = {id(row): line for line, row in mapped}
    matched = match_rows(mapped, key_rows)
    row_of = {id(row): key_rows[index] for index, row in matched.items()}

    # the requirement list
    missing, class_wrong, extra, unmapped = [], [], [], 0
    for index, key_row in enumerate(key_rows):
        row = matched.get(index)
        name = {"line": key_row["line"], "item": key_row.get("item"), "class": key_row["class"]}
        if row is None:
            if key_row["line"] not in either and (key_row["line"] != 0 or key_row["kind"] == "must"):
                missing.append(name)
        elif row_class(row) != key_row["class"]:
            class_wrong.append({**name, "got": row_class(row)})
    for line, row in mapped:
        if id(row) in row_of or (line == 0 and str(row.get("status")) == "met"):
            continue
        if line is None:
            unmapped += 1
        elif line in either:
            if row_class(row) not in either[line]["classes"]:
                class_wrong.append({"line": line, "item": None, "class": "/".join(either[line]["classes"]), "got": row_class(row)})
        else:
            extra.append({"line": line, "class": row_class(row), "on": "a location row the key does not have" if line == 0 else soft.get(line, "a second row of a one-row line")})
    covered = {line for line, _ in mapped if line in required}
    must_lines = {row["line"] for row in key["musts"] if row["line"] != 0}
    must = lambda items: [item for item in items if item["class"] in MANDATORY or item.get("got") in MANDATORY]  # noqa: E731

    # the status of each must-have row
    status_wrong = []
    for index, key_row in enumerate(key_rows):
        row = matched.get(index)
        if key_row["kind"] != "must" or row is None:
            continue
        got = str(row.get("status"))
        if got != key_row["status"] and got not in (key_row.get("status_either") or ()):
            status_wrong.append({"line": key_row["line"], "item": key_row.get("item"), "key": key_row["status"], "got": got})

    # the questions
    by_requirement = {fold(row.get("requirement")): row for row in rows}
    by_id = {str(row["id"]): row for row in rows if row.get("id")}
    raw_questions = answer.get("structured_questions")
    if not isinstance(raw_questions, list):
        raw_questions = [item for item in answer.get("questions") or () if isinstance(item, Mapping)]
    questions, asked_firm, asked_loose, on_must = [], set(), set(), 0
    for item in raw_questions:
        if not isinstance(item, Mapping):
            continue
        question_id, target = str(item.get("question_id") or ""), str(item.get("requirement") or "")
        row = by_id.get(target) or by_requirement.get(fold(target))
        key_row = row_of.get(id(row)) if row is not None else None
        line = line_of.get(id(row)) if row is not None else None
        on_must += int(row is not None and row_class(row) in MANDATORY)
        spot = (key_row["line"], fold(key_row.get("item"))) if key_row else None
        entry: dict[str, Any] = {"question_id": question_id, "line": line, "item": key_row.get("item") if key_row else None}
        if any(word in question_id for word in SPONSORSHIP_WORDS):
            entry.update({"call": "over", "why": "sponsorship or authorization never gates"})
        elif key_row is None and line in either:
            holds = row is not None and row_class(row) in MANDATORY
            entry.update({"call": "over" if holds else "optional", "why": "a line the key leaves open, asked as a must-have" if holds else "a line the key leaves open"})
        elif key_row is None:
            entry.update({"call": "over", "why": "no row of the key" if line is None else "the key has the location met" if line == 0 else f"the line is {soft.get(line, 'one row')} in the key"})
        elif key_row["kind"] == "nice":
            entry.update({"call": "over", "why": "nice_to_have rows are never asked"})
        elif key_row.get("status_either"):
            entry.update({"call": "either", "why": "the key accepts this row asked or settled"})
            asked_loose.add(spot)
        elif key_row["status"] != "unclear":
            entry.update({"call": "over", "why": f"the key settles this row: {key_row['status']}"})
        elif key_row["kind"] == "optional":
            entry.update({"call": "optional", "why": "an unclear list_item row"})
        else:
            entry.update({"call": "right", "why": "an unclear must-have row of the key"})
            asked_firm.add(spot)
        questions.append(entry)
    over_cap = max(0, on_must - max_must)
    optional_over_cap = max(0, sum(entry["call"] == "optional" for entry in questions) - max_optional)
    firm_open = [row for row in view["musts"] if row["status"] == "unclear" and not row.get("status_either")]
    owed = max(0, min(len(firm_open), max_must - len(asked_loose)))
    under = max(0, owed - len(asked_firm))
    over = [entry for entry in questions if entry["call"] == "over"]

    # the location answer
    location_rows = [row for line, row in mapped if line == 0 or str(row.get("id") or "") in ELIG_IDS]
    location_asked = any(entry["question_id"].startswith("location:") or entry["line"] == 0 for entry in questions)
    location_status = next((str(row.get("status")) for row in location_rows if str(row.get("status")) in ("unclear", "unmet")), "met")
    verdict, gate = str(answer.get("verdict")), result_gate(answer, whole)
    score = {
        "ok": True,
        "verdict": {"key": view["verdicts"], "got": verdict, "right": verdict in view["verdicts"]},
        "gate": {"key": view["gates"], "got": gate, "right": gate in view["gates"]},
        "location": {"key_status": key["location"]["status"], "got_status": location_status, "key_ask": bool(key["location"]["ask"]), "got_ask": location_asked,
                     "right": location_status == key["location"]["status"] and location_asked == bool(key["location"]["ask"])},
        "sponsorship": {"key": key.get("sponsorship"), "got": answer.get("sponsorship"), "row": any(str(row.get("id") or "") == "elig-sponsorship" for row in rows)},
        "questions": {
            "asked": len(questions), "right": sum(entry["call"] == "right" for entry in questions), "either": sum(entry["call"] == "either" for entry in questions),
            "optional": sum(entry["call"] == "optional" for entry in questions), "over_asks": len(over) + over_cap + optional_over_cap, "under_asks": under,
            "over_cap": over_cap, "optional_over_cap": optional_over_cap, "each": questions,
            "key_unclear_not_asked": [{"line": row["line"], "item": row.get("item")} for row in firm_open if (row["line"], fold(row.get("item"))) not in asked_firm],
        },
        "list": {
            "key_rows": len([row for row in key_rows if row["line"] != 0]), "result_rows": len(rows), "missing": missing, "class_wrong": class_wrong, "extra": extra, "unmapped": unmapped,
            "musts_right": not must(missing) and not must(class_wrong) and not must(extra), "exact": not missing and not class_wrong and not extra and not unmapped,
            "line_coverage": {"required_lines": len(required), "covered": len(covered), "must_lines": len(must_lines), "must_covered": len(covered & must_lines)},
        },
        "status": {"must_rows_found": sum(1 for index, row in enumerate(key_rows) if row["kind"] == "must" and index in matched), "wrong": status_wrong},
    }
    score["fully_correct"] = bool(score["verdict"]["right"] and score["gate"]["right"] and score["location"]["right"] and not score["questions"]["over_asks"] and not under
                                  and score["list"]["musts_right"] and not status_wrong)
    return score


# --- a results folder --------------------------------------------------------------------------------------


def load_key(path: Path) -> dict[str, Any]:
    key = json.loads(path.read_text(encoding="utf-8"))
    if key.get("schema") != KEY_SCHEMA:
        raise ValueError(f"{path} is not a {KEY_SCHEMA} key")
    return key


def posting_lines(postings: Path, posting_id: str, key: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The numbered lines of one posting, checked hash by hash against the key (a changed posting is a different key)."""

    found = sorted(postings.glob(f"{posting_id}-*.md"))
    if len(found) != 1:
        raise ValueError(f"posting {posting_id}: {len(found)} files under {postings}")
    lines = number_lines(found[0].read_text(encoding="utf-8"))
    if [line["text_hash"] for line in lines] != [line["text_hash"] for line in key["lines"]]:
        raise ValueError(f"posting {posting_id}: its lines are not the lines the key was built on")
    return lines


def score_folder(key: Mapping[str, Any], results: Path, postings: Path) -> dict[str, Any]:
    """Every ``<NN-slug>/<cli>/assessment.json`` under ``results`` against the key."""

    out: list[dict[str, Any]] = []
    for path in sorted(results.glob("*/*/assessment.json")):
        posting_id, cli = path.parent.parent.name[:2], path.parent.name
        entry: dict[str, Any] = {"posting": posting_id, "cli": cli}
        posting_key = key["postings"].get(posting_id)
        if posting_key is None:
            out.append({**entry, "ok": False, "error": "posting not in the key"})
            continue
        whole = json.loads(path.read_text(encoding="utf-8"))
        out.append({**entry, **score_result(posting_key, whole, posting_lines(postings, posting_id, posting_key))})
    by_cli: dict[str, dict[str, int]] = {}
    for row in out:
        total = by_cli.setdefault(row["cli"], dict.fromkeys(TOTALS, 0))
        total["results"] += 1
        if not row["ok"]:
            total["unread"] += 1
            continue
        for name in ("verdict", "gate", "location"):
            total[name + "_right"] += int(row[name]["right"])
        total["over_asks"] += row["questions"]["over_asks"]
        total["under_asks"] += row["questions"]["under_asks"]
        total["rows_missing"] += len(row["list"]["missing"])
        total["rows_extra"] += len(row["list"]["extra"])
        total["rows_unmapped"] += row["list"]["unmapped"]
        total["class_wrong"] += len(row["list"]["class_wrong"])
        total["status_wrong"] += len(row["status"]["wrong"])
        total["musts_right"] += int(row["list"]["musts_right"])
        total["list_exact"] += int(row["list"]["exact"])
        total["required_lines"] += row["list"]["line_coverage"]["required_lines"]
        total["lines_covered"] += row["list"]["line_coverage"]["covered"]
        total["fully_correct"] += int(row["fully_correct"])
    return {"schema_version": SCORE_SCHEMA, "key_set": key.get("set"), "results_label": results.name, "by_cli": by_cli, "results": out}


def render(report: Mapping[str, Any]) -> str:
    lines = [f"# Scored against the gold key: {report['results_label']} (set {report['key_set']})", "", "Counts are agreement with the key, nothing else.", "",
             "| cli | results | fully correct | verdict | gate | location | over-asks | under-asks | must rows right | list exact | rows missing | rows extra | unmapped | class wrong | status wrong | lines covered |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for cli, total in sorted(report["by_cli"].items()):
        lines.append(f"| {cli} | {total['results']} | {total['fully_correct']} | {total['verdict_right']} | {total['gate_right']} | {total['location_right']} | {total['over_asks']} | {total['under_asks']} | "
                     f"{total['musts_right']} | {total['list_exact']} | {total['rows_missing']} | {total['rows_extra']} | {total['rows_unmapped']} | {total['class_wrong']} | {total['status_wrong']} | {total['lines_covered']}/{total['required_lines']} |")
    lines += ["", "| posting | cli | fully | verdict (key) | gate | location | asked | right | over | under | missing | extra | class wrong | status wrong | lines |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in report["results"]:
        if not row["ok"]:
            lines.append(f"| {row['posting']} | {row['cli']} | unread: {row.get('error')} | | | | | | | | | | | | |")
            continue
        mark = lambda item: "ok" if item["right"] else "WRONG"  # noqa: E731
        questions, listing, cover = row["questions"], row["list"], row["list"]["line_coverage"]
        lines.append(f"| {row['posting']} | {row['cli']} | {'yes' if row['fully_correct'] else 'no'} | {mark(row['verdict'])}: {row['verdict']['got']} ({' or '.join(row['verdict']['key'])}) | {mark(row['gate'])} | {mark(row['location'])} | "
                     f"{questions['asked']} | {questions['right']} | {questions['over_asks']} | {questions['under_asks']} | {len(listing['missing'])} | {len(listing['extra'])} | {len(listing['class_wrong'])} | {len(row['status']['wrong'])} | {cover['covered']}/{cover['required_lines']} |")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key", type=Path, required=True, help="the gold key (<data-dir>/gold/key.json)")
    parser.add_argument("--results", type=Path, required=True, help="one results folder of run_assess_real_eval (<data-dir>/results/<label>)")
    parser.add_argument("--postings", type=Path, default=None, help="the set's postings folder (default: <key's set folder>/postings)")
    parser.add_argument("--out", type=Path, default=None, help="where score.json and score.md go (default: <key folder>/scores/<label>/); never under this repository")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    key_path = args.key.resolve()
    results = args.results.resolve()
    out = (args.out or key_path.parent / "scores" / results.name).resolve()
    if out == REPO_ROOT or REPO_ROOT in out.parents:
        print(f"--out {out} is inside the repository: scores of a real-data run are never written there", file=sys.stderr)
        return 2
    try:
        key = load_key(key_path)
        report = score_folder(key, results, (args.postings or key_path.parent.parent / "postings").resolve())
    except (OSError, ValueError) as exc:
        print(f"score_against_gold: {exc}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    (out / "score.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "score.md").write_text(render(report), encoding="utf-8")
    for cli, total in sorted(report["by_cli"].items()):
        print(f"{cli}: {total['results']} results, fully correct {total['fully_correct']}, verdict right {total['verdict_right']}, over-asks {total['over_asks']}, under-asks {total['under_asks']}")
    print(f"written under {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
