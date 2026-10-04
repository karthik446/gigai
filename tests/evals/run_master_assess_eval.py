#!/usr/bin/env python3
"""0.1.10.9 master P7: assessing against the profile's 2-page view or the evidence view, on synthetic data only.

The inputs are the master-resume spike's (``fixtures/master``): one invented
person's 8-page master, two profiles and three postings (6 cases), plus the
five postings of the requirement-matrix eval (``fixtures/matrix_cases.json``,
0110-10-03), assessed for the infrastructure profile (5 cases).  The candidate's
constraints are that fixture's (Denver, US, no sponsorship need, no work mode).

VARIANTS, one assess call per case and pass:

- ``view``: the product's path WITHOUT the switch.  The prompt's RESUME is the
  profile's 2-page view of the master (``master_selection.select`` with no
  posting: what ``selection refresh`` stores as the profile's resume).  This
  is the baseline.
- ``evidence``: the prompt's RESUME is the evidence view
  (``assess_master.evidence_text``, the function the product calls): the lines
  of the whole master most relevant to the posting, up to the prompt's own
  12,000-character resume cap.  The profile's prior is the lines its view
  shows.

Everything else is the product's one assess call: ``build_assess_context``,
``assess_once`` with the packaged ``assess.md`` and the quick-assess parser.
``assess.md`` is the same file for both variants (the report holds its digest);
it is read again on every render, so no run may overlap an edit of it.

MEASURED per result: the verdict, the open questions ("needs your answers"),
the matrix rows by status, the minor gaps, every evidence string and whether
the text the model read supports it, tokens as the CLI reports them and the
prompt's size.

EVIDENCE SUPPORT (code, then a person).  Each evidence string is compared with
what the model was given as the candidate's facts (the RESUME block and the
candidate constraints): ``verbatim`` (it is in that text, spacing and case
aside), ``paraphrase`` (every number it states is in that text and at least
three quarters of its words are), else ``check``.  A ``check`` string is listed
in the report with its row; a person reads each one and counts the INVENTED
ones (a fact the text does not hold).

THE RULE, written before any call: the evidence view is no regression when
all four hold over the same cases and passes:

1. invented evidence is 0 in the evidence runs;
2. it has no more invalid answers (after the product's one retry) than the view;
3. no case's verdicts are worse: with not a match 0, needs answers 1 and
   matched 2, a case's mean over its passes is not lower than the view's;
4. the open questions in total are not more than the view's.

Tokens are reported, not part of the rule: the evidence view is about three
times the 2-page view's characters by design.

Modes: LIVE (``GIGAI_ASSESS_EVAL_LIVE=1``; the config is read from ``--home``,
never written) and FAKE (``--fake-model``: a temp home and the
``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL`` seam, offline).  ``--max-calls`` is a hard
cap on model calls, the product's own single retry included: a case is skipped
when its call and that call's retry no longer fit.  One call at a time
(``--concurrency`` accepts only 1).

``--compare VIEW.json EVIDENCE.json`` makes no call: it prints the two reports
case by case and says what the rule above finds (``--invented N`` is the count
a person read off the ``check`` strings of the evidence report).
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any

if __package__ in (None, ""):  # run as a script: make ``tests.evals`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.evals import run_assess_eval as harness  # noqa: E402
from tests.evals.run_master_tailor_eval import load_master, load_postings, load_profiles  # noqa: E402
from tests.evals.run_tailor_eval import CallBudget, CallCapReached, CappedBinding  # noqa: E402

MATRIX_PATH = harness.FIXTURES_DIR / "matrix_cases.json"
REPORT_SCHEMA = "gigai-master-assess-eval-report:1"
#: The day the fixtures were written for: which roles are "old" depends on the year.
TODAY = date(2026, 10, 3)
VARIANT_VIEW = "view"
VARIANT_EVIDENCE = "evidence"
VARIANTS = (VARIANT_VIEW, VARIANT_EVIDENCE)
#: The profile the requirement-matrix postings (all site reliability roles) are assessed for.
MATRIX_PROFILE = "profile-swe"
DEFAULT_REPEAT = 2
RANK = {"not_a_match": 0, "pending_user_answers": 1, "matched_above_threshold": 2}
_SHORT = {"matched_above_threshold": "matched", "pending_user_answers": "pending", "not_a_match": "not a match", None: "-"}
PARAPHRASE_SHARE = 0.75


# --- the cases ----------------------------------------------------------------------------------------


def load_candidate(path: Path = MATRIX_PATH) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8"))["candidate"])


def load_matrix_postings(path: Path = MATRIX_PATH) -> list[dict[str, str]]:
    """The requirement-matrix eval's postings (not its resume: the candidate here is the master's person)."""

    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    return [{"id": f"mq-{case['id']}", "title": case["title"], "company": case["company"], "location": case["location"], "text": case["posting"]} for case in cases]


def cases(profiles: Sequence[Mapping[str, Any]], postings: Sequence[Mapping[str, str]], matrix: Sequence[Mapping[str, str]]) -> list[tuple[Mapping[str, Any], Mapping[str, str]]]:
    """``(profile, posting)``: every profile x every spike posting, then the matrix postings for the infrastructure profile."""

    out = [(profile, posting) for profile in profiles for posting in postings]
    swe = next(profile for profile in profiles if profile["profile_id"] == MATRIX_PROFILE)
    return out + [(swe, posting) for posting in matrix]


def plan(variants: Sequence[str], all_cases: Sequence[tuple[Mapping[str, Any], Mapping[str, str]]], repeat: int, limit: int | None = None):
    """``(variant, rep, profile, posting)``: every case of one pass, then the next pass; a variant at a time."""

    unknown = [name for name in variants if name not in VARIANTS]
    if unknown:
        raise ValueError(f"unknown variant {', '.join(unknown)}; known variants: {', '.join(VARIANTS)}")
    out = [(variant, rep, profile, posting) for variant in variants for rep in range(1, repeat + 1) for profile, posting in all_cases]
    return out[: limit if limit else None]


def profile_view(master: Any, profile: Mapping[str, Any]) -> Any:
    """The profile's 2-page view of the master: the selector's standing pick (no posting)."""

    from gigai.scout.master_selection import SelectionProfile, select

    return select(
        master,
        SelectionProfile(titles=tuple(profile["titles"]), focus_tags=tuple(profile.get("focus_tags") or ()), profile_id=profile["profile_id"], label=profile["label"]),
        None, today=TODAY,
    )


def resume_text(variant: str, master: Any, profile: Mapping[str, Any], posting: Mapping[str, str], view: Any) -> tuple[str, dict[str, Any]]:
    """The prompt's RESUME for ``variant``, and what it holds."""

    if variant == VARIANT_VIEW:
        return view.markdown, {"chars": len(view.markdown), "bullets": view.markdown.count("\n- "), "pages": view.pages}
    from gigai.scout import assess_master

    found = assess_master.evidence_text(
        master,
        assess_master.profile_prior(titles=tuple(profile["titles"]), item_ids=tuple(view.item_ids()), profile_id=profile["profile_id"], label=profile["label"]),
        title=posting["title"], posting_text=posting["text"], company=posting["company"], location=posting["location"], today=TODAY,
    )
    return found.markdown, {"chars": found.chars, "bullets": found.bullets, "bullets_total": found.bullets_total, "within_cap": found.within_cap, "selector_version": found.selector_version}


# --- evidence support -----------------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9][a-z0-9+#./-]*")
_NUMBER = re.compile(r"\d[\d,.]*")
_STOP = frozenset(
    "a an and as at by for from in into is it its of on or that the their this to was were with over under across per "
    "candidate resume states says shows lists has have had experience years year".split()
)


def _flat(text: str) -> str:
    return " ".join(re.sub(r"[*_`\"'“”‘’]", "", text.lower()).split())


def _words(text: str) -> list[str]:
    return [word.strip(".-/") for word in _WORD.findall(_flat(text)) if word.strip(".-/") and word.strip(".-/") not in _STOP]


def _numbers(text: str) -> list[str]:
    return [found.rstrip(".,") for found in _NUMBER.findall(text)]


def evidence_support(evidence: str, facts: str) -> str:
    """``verbatim``, ``paraphrase`` or ``check`` (see the module text). Pure."""

    flat_facts = _flat(facts)
    flat = _flat(evidence).strip(" .…")
    if flat and flat in flat_facts:
        return "verbatim"
    # A quote the model cut in the middle ("... 2.1 million tool-calling LLM tasks ... across 140 teams").
    pieces = [piece.strip(" .") for piece in re.split(r"\.\.\.|…", flat) if piece.strip(" .")]
    if len(pieces) > 1 and all(piece in flat_facts for piece in pieces):
        return "verbatim"
    known_numbers = set(_numbers(facts))
    if any(number not in known_numbers for number in _numbers(evidence)):
        return "check"
    words = _words(evidence)
    if not words:
        return "check"
    known = set(_words(facts))
    return "paraphrase" if sum(word in known for word in words) / len(words) >= PARAPHRASE_SHARE else "check"


def candidate_facts(prompt_resume: str, candidate: Mapping[str, Any], titles: Sequence[str]) -> str:
    """What the model was given as the candidate's facts: the RESUME block as rendered, and the constraints."""

    sponsorship = "needs visa sponsorship" if candidate["visa_sponsorship_required"] else "does not need visa sponsorship; no sponsorship required"
    return "\n".join([prompt_resume, candidate["location"], " ".join(candidate["countries"]), "united states us usa", sponsorship, *titles])


# --- one case -------------------------------------------------------------------------------------------


def run_case(
    binding: CappedBinding, variant: str, rep: int, profile: Mapping[str, Any], posting: Mapping[str, str], *, master: Any, view: Any, candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """One case through the product's assess call; the row the report stores."""

    from gigai.scout.assessment_core import AssessJob, assess_once, assess_prompt_version, build_assess_context, render_assess_prompt
    from gigai.scout.requirement_weights import minor_gaps
    from gigai.scout.resume_privacy import model_resume

    budget = binding.budget
    case = f"{profile['profile_id']}/{posting['id']}"
    text, holds = resume_text(variant, master, profile, posting, view)
    job = AssessJob(title=posting["title"], company=posting["company"], location=posting["location"], posting_text=posting["text"])
    ctx = build_assess_context(
        resume_text=text,
        visa_sponsorship_required=bool(candidate["visa_sponsorship_required"]),
        countries=tuple(candidate["countries"]),
        titles=tuple(profile["titles"]),
        location=candidate["location"],
        bank=None,
        work_mode=candidate["work_mode"],
    )
    prompt = render_assess_prompt(job, ctx)
    shown = model_resume(text).text
    row: dict[str, Any] = {
        "variant": variant, "case": case, "rep": rep, "profile": profile["profile_id"], "posting": posting["id"],
        "prompt_version": assess_prompt_version(ctx.work_mode), "resume": holds, "prompt_chars": len(prompt),
        # The whole RESUME reached the prompt: nothing was cut off it.
        "resume_whole_in_prompt": shown in prompt,
    }
    start = budget.made
    budget.begin("assess", (variant, f"{case}#{rep}"))
    started = time.monotonic()
    attempt = assess_once(binding, job, ctx, parse=harness._parse_body)
    calls = budget.calls[start:]
    row.update({
        "ok": attempt.ok, "attempts": attempt.attempts, "validation_error": attempt.validation_error,
        "not_assessed_reason": attempt.not_assessed_reason.value if attempt.not_assessed_reason else None,
        "calls": len(calls), "seconds": round(time.monotonic() - started, 1),
        "input_tokens": sum((call.usage or {}).get("input_tokens") or 0 for call in calls),
        "output_tokens": sum((call.usage or {}).get("output_tokens") or 0 for call in calls),
    })
    if not attempt.ok:
        return row
    body = attempt.parsed
    facts = candidate_facts(shown, candidate, profile["titles"])
    rows = []
    for item in body.matrix:  # type: ignore[union-attr]
        evidence = [{"text": found, "support": evidence_support(found, facts)} for found in item.resume_evidence if found.strip()]
        rows.append({
            "requirement": item.requirement, "class": item.requirement_class.value if item.requirement_class else None,
            "status": item.status.value, "evidence": evidence,
        })
    every = [found for item in rows for found in item["evidence"]]
    row.update({
        "verdict": body.verdict.value if body.verdict is not None else None,  # type: ignore[union-attr]
        "not_a_match_reason": body.not_a_match_reason,  # type: ignore[union-attr]
        "questions": [{"question_id": item.question_id, "question": item.question} for item in body.structured_questions],  # type: ignore[union-attr]
        "minor_gaps": list(minor_gaps(body.matrix)),  # type: ignore[union-attr]
        "matrix": rows,
        "rows": len(rows),
        "met": sum(item["status"] == "met" for item in rows),
        "unmet": sum(item["status"] == "unmet" for item in rows),
        "unclear": sum(item["status"] == "unclear" for item in rows),
        "evidence_strings": len(every),
        "evidence_verbatim": sum(found["support"] == "verbatim" for found in every),
        "evidence_paraphrase": sum(found["support"] == "paraphrase" for found in every),
        "evidence_check": [
            {"requirement": item["requirement"], "status": item["status"], "text": found["text"]}
            for item in rows for found in item["evidence"] if found["support"] == "check"
        ],
    })
    return row


# --- the tables -----------------------------------------------------------------------------------------


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def summarize(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One line per variant: the sums and means the comparison is read from."""

    out = []
    for variant in VARIANTS:
        mine = [row for row in rows if row["variant"] == variant]
        done = [row for row in mine if row.get("ok")]
        if not mine:
            continue
        out.append({
            "variant": variant, "results": len(mine), "ok": len(done), "invalid": len(mine) - len(done), "retried": sum(row["attempts"] > 1 for row in mine),
            "verdicts": {name: sum(row["verdict"] == key for row in done) for key, name in _SHORT.items() if key},
            "questions": sum(len(row["questions"]) for row in done),
            "results_with_questions": sum(bool(row["questions"]) for row in done),
            "minor_gaps": sum(len(row["minor_gaps"]) for row in done),
            "rows": sum(row["rows"] for row in done), "met": sum(row["met"] for row in done), "unmet": sum(row["unmet"] for row in done),
            "unclear": sum(row["unclear"] for row in done),
            "evidence_strings": sum(row["evidence_strings"] for row in done),
            "evidence_verbatim": sum(row["evidence_verbatim"] for row in done),
            "evidence_paraphrase": sum(row["evidence_paraphrase"] for row in done),
            "evidence_check": sum(len(row["evidence_check"]) for row in done),
            "calls": sum(row["calls"] for row in mine),
            "input_tokens_mean": _mean([row["input_tokens"] for row in done]), "output_tokens_mean": _mean([row["output_tokens"] for row in done]),
            "input_tokens": sum(row["input_tokens"] for row in mine), "output_tokens": sum(row["output_tokens"] for row in mine),
            "prompt_chars_mean": _mean([row["prompt_chars"] for row in mine]), "resume_chars_mean": _mean([row["resume"]["chars"] for row in mine]),
            "seconds_mean": _mean([row["seconds"] for row in done]),
            "resume_cut_by_prompt": sum(not row["resume_whole_in_prompt"] for row in mine),
        })
    return out


def print_summary(summary: Sequence[Mapping[str, Any]], *, out=None) -> None:
    out = out or sys.stdout
    print(
        f"{'variant':9} {'ok':>5} {'matched':>7} {'pending':>7} {'not':>4} {'questions':>9} {'gaps':>4} {'rows':>5} {'met':>4} {'unmet':>5} {'unclear':>7} "
        f"{'evidence':>8} {'verbatim':>8} {'paraphr':>7} {'check':>5} {'tok in':>8} {'tok out':>7} {'prompt':>7} {'resume':>7}",
        file=out,
    )
    for line in summary:
        verdicts = line["verdicts"]
        print(
            f"{line['variant']:9} {str(line['ok']) + '/' + str(line['results']):>5} {verdicts['matched']:>7} {verdicts['pending']:>7} {verdicts['not a match']:>4} "
            f"{line['questions']:>9} {line['minor_gaps']:>4} {line['rows']:>5} {line['met']:>4} {line['unmet']:>5} {line['unclear']:>7} {line['evidence_strings']:>8} "
            f"{line['evidence_verbatim']:>8} {line['evidence_paraphrase']:>7} {line['evidence_check']:>5} {line['input_tokens_mean']!s:>8} "
            f"{line['output_tokens_mean']!s:>7} {line['prompt_chars_mean']!s:>7} {line['resume_chars_mean']!s:>7}",
            file=out,
        )


def print_rows(rows: Sequence[Mapping[str, Any]], *, out=None) -> None:
    out = out or sys.stdout
    for row in rows:
        name = f"{row['variant']:9} {row['case']:52} #{row['rep']}"
        if not row.get("ok"):
            print(f"{name} INVALID ({row.get('not_assessed_reason')}: {row.get('validation_error')})", file=out)
            continue
        asked = ", ".join(item["question_id"] for item in row["questions"]) or "-"
        print(
            f"{name} {_SHORT[row['verdict']]:11} {row['rows']:>2} rows ({row['met']} met, {row['unmet']} unmet, {row['unclear']} unclear), "
            f"questions: {asked}; gaps: {', '.join(row['minor_gaps']) or '-'}; evidence {row['evidence_strings']} ({len(row['evidence_check'])} to check); "
            f"{row['input_tokens']}/{row['output_tokens']} tok, {row['calls']} call(s)",
            file=out,
        )


def compare(view: Mapping[str, Any], evidence: Mapping[str, Any], *, invented: int | None = None) -> dict[str, Any]:
    """The two reports case by case, and what the rule of the module text finds. Pure."""

    def by_case(report: Mapping[str, Any], variant: str) -> dict[str, list[Mapping[str, Any]]]:
        out: dict[str, list[Mapping[str, Any]]] = {}
        for row in report["rows"]:
            if row["variant"] == variant:
                out.setdefault(row["case"], []).append(row)
        return out

    before, after = by_case(view, VARIANT_VIEW), by_case(evidence, VARIANT_EVIDENCE)
    lines = []
    worse = []
    for case in before:
        if case not in after:
            continue
        mine, theirs = [row for row in before[case] if row.get("ok")], [row for row in after[case] if row.get("ok")]
        mean_before = _mean([RANK[row["verdict"]] for row in mine if row["verdict"] in RANK])
        mean_after = _mean([RANK[row["verdict"]] for row in theirs if row["verdict"] in RANK])
        line = {
            "case": case,
            "view": [_SHORT.get(row.get("verdict")) if row.get("ok") else "invalid" for row in before[case]],
            "evidence": [_SHORT.get(row.get("verdict")) if row.get("ok") else "invalid" for row in after[case]],
            "view_questions": [[item["question_id"] for item in row["questions"]] for row in mine],
            "evidence_questions": [[item["question_id"] for item in row["questions"]] for row in theirs],
            "view_unclear": [row["unclear"] for row in mine], "evidence_unclear": [row["unclear"] for row in theirs],
            "view_met": [row["met"] for row in mine], "evidence_met": [row["met"] for row in theirs],
            "view_rank": mean_before, "evidence_rank": mean_after,
        }
        if mean_before is not None and mean_after is not None and mean_after < mean_before:
            worse.append(case)
        lines.append(line)
    compared = {line["case"] for line in lines}

    def total(report_rows: Mapping[str, list[Mapping[str, Any]]], key) -> int:
        return sum(key(row) for case, rows in report_rows.items() if case in compared for row in rows)

    invalid_before = total(before, lambda row: not row.get("ok"))
    invalid_after = total(after, lambda row: not row.get("ok"))
    questions_before = total(before, lambda row: len(row.get("questions") or ()))
    questions_after = total(after, lambda row: len(row.get("questions") or ()))
    to_check = total(after, lambda row: len(row.get("evidence_check") or ()))
    rule = {
        "invented_evidence": {"value": invented, "to_check": to_check, "holds": None if invented is None else invented == 0},
        "invalid_answers": {"view": invalid_before, "evidence": invalid_after, "holds": invalid_after <= invalid_before},
        "verdicts_not_worse": {"worse_cases": worse, "holds": not worse},
        "open_questions": {"view": questions_before, "evidence": questions_after, "holds": questions_after <= questions_before},
    }
    holds = [part["holds"] for part in rule.values()]
    return {"cases": lines, "rule": rule, "no_regression": None if None in holds else all(holds)}


def print_comparison(result: Mapping[str, Any], *, out=None) -> None:
    out = out or sys.stdout
    print(f"{'case':52} {'view':28} {'evidence':28} questions view -> evidence", file=out)
    for line in result["cases"]:
        asked_before = sum(len(found) for found in line["view_questions"])
        asked_after = sum(len(found) for found in line["evidence_questions"])
        print(f"{line['case']:52} {', '.join(map(str, line['view'])):28} {', '.join(map(str, line['evidence'])):28} {asked_before} -> {asked_after}", file=out)
    rule = result["rule"]
    print("", file=out)
    invented = rule["invented_evidence"]
    print(f"1. invented evidence: {invented['value'] if invented['value'] is not None else 'not read yet'} ({invented['to_check']} strings to read): {_holds(invented['holds'])}", file=out)
    print(f"2. invalid answers: view {rule['invalid_answers']['view']}, evidence {rule['invalid_answers']['evidence']}: {_holds(rule['invalid_answers']['holds'])}", file=out)
    print(f"3. cases with worse verdicts: {', '.join(rule['verdicts_not_worse']['worse_cases']) or 'none'}: {_holds(rule['verdicts_not_worse']['holds'])}", file=out)
    print(f"4. open questions: view {rule['open_questions']['view']}, evidence {rule['open_questions']['evidence']}: {_holds(rule['open_questions']['holds'])}", file=out)
    verdict = {True: "NO REGRESSION", False: "REGRESSION", None: "not decided (pass --invented N after reading the strings to check)"}[result["no_regression"]]
    print(f"the rule: {verdict}", file=out)


def _holds(value: bool | None) -> str:
    return {True: "holds", False: "FAILS", None: "not decided"}[value]


# --- main -----------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="0.1.10.9 master P7: assess against the profile's 2-page view or the evidence view (synthetic)")
    parser.add_argument("--variant", action="append", default=None, choices=VARIANTS, help="run this variant (repeatable); default: view")
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT, help=f"passes over the cases (default {DEFAULT_REPEAT})")
    parser.add_argument("--max-calls", type=int, default=23, help="hard cap on model calls, the product's retry included (default 23: 11 cases x 2 passes + 1)")
    parser.add_argument("--report", type=Path, default=None, help="where to write the JSON report (required unless --dry-run or --compare)")
    parser.add_argument("--home", type=Path, default=None, help="GigAI home to read the config from; never written")
    parser.add_argument("--model-target", default=harness.DEFAULT_MODEL_TARGET, help=f"adapter kind to resolve (default {harness.DEFAULT_MODEL_TARGET})")
    parser.add_argument("--concurrency", type=int, default=1, help="calls in flight at once: only 1 is supported")
    parser.add_argument("--fake-model", action="store_true", help="offline: a temp home and the GIGAI_SCOUT_FIND_JOBS_TEST_MODEL seam")
    parser.add_argument("--limit", type=int, default=None, help="at most this many calls planned in all (a smoke run)")
    parser.add_argument("--dry-run", action="store_true", help="print the planned cases and exit without any model call")
    parser.add_argument("--dump-dir", type=Path, default=None, help="write every prompt and raw model output per call under this directory")
    parser.add_argument("--compare", nargs=2, type=Path, default=None, metavar=("VIEW_JSON", "EVIDENCE_JSON"), help="no calls: print the two reports case by case and the rule")
    parser.add_argument("--invented", type=int, default=None, help="with --compare: the invented evidence strings a person counted in the evidence report")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.compare:
        reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.compare]
        for path, report in zip(args.compare, reports):
            run = report["run"]
            print(f"{path.name}: variants {','.join(run['variants'])} assess.md {run['instructions_digest'][:19]} head {run['head']} calls {run['calls_made']}/{run['max_calls']} skipped {run['skipped'] or 'none'}")
        print("")
        print_summary(summarize([row for report in reports for row in report["rows"]]))
        print("")
        print_comparison(compare(reports[0], reports[1], invented=args.invented))
        return 0
    profiles, postings, matrix = load_profiles(), load_postings(), load_matrix_postings()
    planned = plan(args.variant or [VARIANT_VIEW], cases(profiles, postings, matrix), args.repeat, args.limit)
    if args.dry_run:
        for index, (variant, rep, profile, posting) in enumerate(planned, 1):
            print(f"{index:3} {variant:9} #{rep} {profile['profile_id']:12} {posting['id']}")
        print(f"{len(planned)} planned calls: --max-calls {len(planned) + 1} lets every one run when at most one answer is retried")
        return 0
    if args.concurrency != 1:
        print("--concurrency: only 1 is supported (one call at a time)", file=sys.stderr)
        return 2
    if args.report is None:
        print("--report is required", file=sys.stderr)
        return 2
    if not args.fake_model and os.environ.get("GIGAI_ASSESS_EVAL_LIVE") != "1":
        print("refusing the live master assess eval: set GIGAI_ASSESS_EVAL_LIVE=1 explicitly", file=sys.stderr)
        return 2

    master = load_master()
    candidate = load_candidate()
    views = {profile["profile_id"]: profile_view(master, profile) for profile in profiles}
    now = datetime.now(UTC)
    seams = {"GIGAI_SCOUT_FIND_JOBS_TEST_MODEL": "1"} if args.fake_model else {}
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    budget = CallBudget(max_calls=args.max_calls)
    report_run: dict[str, Any] = {}
    model_target = "ollama_local" if args.fake_model else args.model_target

    def save() -> None:
        from gigai.canonical import digest_imported_bytes
        from gigai.scout.assessment_core import INSTRUCTIONS_DIGEST, _instruction_bytes

        report_run.update({
            "started_at": now.isoformat().replace("+00:00", "Z"),
            "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "head": harness._git_head(),
            "fake_model": args.fake_model,
            "model_target": model_target,
            "variants": list(dict.fromkeys(variant for variant, _rep, _profile, _posting in planned)),
            "repeat": args.repeat,
            # The template as it is on disk now (re-read per render), not the digest taken at import.
            "instructions_digest": digest_imported_bytes(_instruction_bytes()),
            "instructions_digest_at_import": INSTRUCTIONS_DIGEST,
            "max_calls": args.max_calls,
            "calls_made": budget.made,
            "planned_calls": len(planned),
            "skipped": skipped,
            "today": TODAY.isoformat(),
            "candidate": candidate,
            "views": {profile_id: {"chars": len(view.markdown), "pages": view.pages, "ids": len(view.item_ids())} for profile_id, view in views.items()},
        })
        calls = [
            {"index": call.index, "row": call.row_key, "attempt": call.attempt, "seconds": call.elapsed_seconds, "usage": call.usage, "error": call.error,
             "prompt_chars": len(call.prompt), "output_chars": len(call.output_text or "")}
            for call in budget.calls
        ]
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps({"schema": REPORT_SCHEMA, "run": report_run, "summary": summarize(rows), "rows": rows, "calls": calls}, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    with tempfile.TemporaryDirectory(prefix="gigai-master-assess-eval-") as tmp, harness.seam_env(**seams):
        if args.fake_model:
            home_root = Path(tmp) / "home"
            home_root.mkdir()
            config = harness.build_fake_config(home_root)
        else:
            from gigai.config import load_config

            home_root = (args.home or harness._default_home()).expanduser()
            config = load_config(home_root)
        binding = CappedBinding(harness.resolve_binding(config, model_target, home_root=home_root), budget)
        try:
            for variant, rep, profile, posting in planned:
                name = f"{variant}/{profile['profile_id']}/{posting['id']}#{rep}"
                # A case needs room for its call and that call's one retry.
                if budget.made + 2 > args.max_calls:
                    skipped.append(name)
                    continue
                try:
                    row = run_case(binding, variant, rep, profile, posting, master=master, view=views[profile["profile_id"]], candidate=candidate)
                except CallCapReached:
                    skipped.append(name)
                    continue
                rows.append(row)
                save()  # after every case: a stopped run keeps what it measured
                if not args.quiet:
                    print_rows([row], out=sys.stderr)
        finally:
            binding.close()
    save()
    if args.dump_dir is not None:
        args.dump_dir.mkdir(parents=True, exist_ok=True)
        for call in budget.calls:
            stem = f"{call.index:03}-{'-'.join(call.row_key or ('none',)).replace('/', '-').replace('#', '-rep')}-attempt{call.attempt}"
            (args.dump_dir / f"{stem}.prompt.txt").write_text(call.prompt, encoding="utf-8")
            (args.dump_dir / f"{stem}.output.txt").write_text(call.output_text or f"<{call.error}>", encoding="utf-8")
    if not args.quiet:
        print_summary(summarize(rows))
        print(f"calls {budget.made}/{args.max_calls}; skipped {skipped or 'none'}; report: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
