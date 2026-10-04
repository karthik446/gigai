#!/usr/bin/env python3
"""0110-10-05 C: a small before/after eval of the tailor prompt on four synthetic cases.

``fixtures/tailor_cases.json`` holds four invented resumes and postings, each
called ``repeat`` times:

- ``helm_answer``: the posting asks for Helm, the resume lacks it, an answer
  says the candidate has it (and another says they do NOT have ArgoCD).
  Is Helm shown, who added it (the model, or GigAI's own rule), and is
  ArgoCD left out?
- ``over_long``: a resume that prints on 3 pages.  How many pages does the
  tailoring have, did the model drop bullets or roles, and which roles did
  the length rule leave out?
- ``duplicate_skills``: Skills lines that wrap into one another.  Does a
  Skills bullet repeat inside another before and after the collapse?
- ``control``: a one-page resume that fits its posting.  Nothing is added,
  cut or collapsed.

Every call goes through the path the product uses for one tailoring:
``resolve_binding`` (the configured target for the adapter kind) and
``tailored_resume.tailor_once`` with the packaged ``tailor.md``, then the
length rule (``tailor_length.fit_to_pages`` with the product's page count).
Each row reports the MODEL's own answer (validated and settled, before
GigAI's Skills rules and the length rule) beside the FINAL result, so a
prompt change is measured apart from what the code guarantees.

``tailor.md`` is read again on every render: run the baseline to its end
BEFORE editing the template.

Modes: LIVE (``GIGAI_ASSESS_EVAL_LIVE=1``; the config is read from ``--home``,
never written) and FAKE (``--fake-model``: a temp home and the
``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL`` seam, offline).  ``--max-calls`` is a hard
cap on model calls, the product's own single retry included: a case run is
skipped when its worst case (two calls) no longer fits.  One call at a time
(``--concurrency`` accepts only 1).

``--compare BEFORE.json AFTER.json`` makes no call: it prints the two reports
side by side.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

if __package__ in (None, ""):  # run as a script: make ``tests.evals`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.evals import run_assess_eval as harness  # noqa: E402
from tests.evals.run_tailor_eval import CallBudget, CallCapReached, CappedBinding  # noqa: E402

CASES_PATH = harness.FIXTURES_DIR / "tailor_cases.json"
DEFAULT_MAX_CALLS = 11
REPORT_SCHEMA = "gigai-tailor-cases-eval-report:1"


def load_cases(path: Path = CASES_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "gigai-tailor-cases:1" or not isinstance(payload.get("cases"), list):
        raise ValueError(f"{path} is not a gigai-tailor-cases:1 fixture")
    seen: set[str] = set()
    for case in payload["cases"]:
        for key in ("id", "repeat", "kind", "title", "company", "posting", "resume", "answers", "matrix", "expect"):
            if key not in case:
                raise ValueError(f"case {case.get('id')!r} is missing {key}")
        if case["id"] in seen:
            raise ValueError(f"case {case['id']!r} appears twice")
        seen.add(case["id"])
    return payload


def plan(payload: Mapping[str, Any], names: Sequence[str] | None = None) -> list[tuple[Mapping[str, Any], int]]:
    """``(case, run number)`` in fixture order; ``names`` keeps only those cases."""

    known = [case["id"] for case in payload["cases"]]
    unknown = [name for name in names or () if name not in known]
    if unknown:
        raise ValueError(f"unknown case {', '.join(unknown)}; known cases: {', '.join(known)}")
    return [
        (case, run)
        for case in payload["cases"]
        if not names or case["id"] in names
        for run in range(1, int(case["repeat"]) + 1)
    ]


def job_and_context(case: Mapping[str, Any]):
    """The product's ``TailorJob`` and ``TailorContext`` for one case (answers keyed by their canonical id)."""

    from gigai.scout.question_ids import normalize_question_id
    from gigai.scout.tailored_resume import AnswerSource, MatrixRow, TailorJob, tailor_context

    answers = {}
    for item in case["answers"]:
        key = normalize_question_id(item["question_id"])
        answers[key] = AnswerSource(key, item["answer"], "eval", item.get("prompt", ""))
    matrix = tuple(MatrixRow(row["requirement"], row["status"]) for row in case["matrix"])
    job = TailorJob(title=case["title"], company=case["company"], location=case.get("location", ""), posting_text=case["posting"])
    return job, tailor_context(case["resume"], answers=answers, matrix=matrix)


# --- what one result shows ----------------------------------------------------------------------


def _shown(result: Any) -> list[tuple[str, Any]]:
    return [(section.heading, line) for section in result.sections for line in section.all_lines()]


def _mentions(result: Any, keyword: str, *, origin: str | None = None, not_origin: str | None = None) -> list[dict[str, Any]]:
    from gigai.scout.posting_keywords import mentions
    from gigai.scout.tailored_resume import shown_text

    found = []
    for heading, line in _shown(result):
        if origin is not None and line.origin != origin:
            continue
        if not_origin is not None and line.origin == not_origin:
            continue
        if mentions(shown_text(line), keyword):
            found.append({"section": heading, "kind": line.kind, "text": shown_text(line), "refs": [ref.label() for ref in line.refs]})
    return found


def skills_duplicates(result: Any) -> int:
    """How many Skills lines another Skills line already holds (the product's own rule, counted not applied)."""

    from gigai.scout.tailor_skills import SKILLS_SECTION, collapse_duplicate_skills

    for section in result.sections:
        if section.heading == SKILLS_SECTION:
            kept, _dropped = collapse_duplicate_skills(section.lines)
            return len(section.lines) - len(kept)
    return 0


def _roles(result: Any) -> list[str]:
    from gigai.scout.tailor_length import ROLE_SECTION, role_label

    return [role_label(entry) for section in result.sections if section.heading == ROLE_SECTION for entry in section.entries]


def _resume_roles(ctx: Any) -> int:
    """How many roles the resume's Experience section lists (entry headings between ``## Experience`` and the next section)."""

    count = 0
    inside = False
    for line in ctx.resume_lines:
        if line.startswith("## "):
            inside = line[3:].strip().lower() == "experience"
        elif inside and line.startswith("**"):
            count += 1
    return count


def describe(result: Any, case: Mapping[str, Any], ctx: Any, measure) -> dict[str, Any]:
    """The facts one result is judged on: its counts, its pages, and what the case's ``expect`` names."""

    from gigai.scout.tailor_skills import ORIGIN_ANSWER, SKILLS_SECTION
    from gigai.scout.tailored_resume import shown_text, tailor_line_stats

    stats = tailor_line_stats(result)
    length = result.length
    skills = next((section for section in result.sections if section.heading == SKILLS_SECTION), None)
    out: dict[str, Any] = {
        "pages": measure(result),
        "lines": result.line_count(),
        "rewritten": stats.shown_rewritten,
        "copied": stats.copied,
        "model_rewrites": stats.model_rewrites,
        "fallbacks": stats.fallbacks,
        "dropped_bullets": stats.dropped_bullets,
        "roles_shown": len(_roles(result)),
        "roles_in_resume": _resume_roles(ctx),
        "skills_lines": [shown_text(line) for line in skills.lines] if skills is not None else [],
        "skills_duplicates": skills_duplicates(result),
        "added_from_answers": [shown_text(line) for _heading, line in _shown(result) if line.origin == ORIGIN_ANSWER],
        "length": None if length is None else {"status": length.status, "pages": length.pages, "full_pages": length.full_pages, "cut": list(length.roles())},
    }
    expect = case["expect"]
    for keyword in (*expect.get("added", ()), *expect.get("never", ())):
        out.setdefault("keywords", {})[keyword] = {
            "by_model": _mentions(result, keyword, not_origin=ORIGIN_ANSWER),
            "by_gigai": _mentions(result, keyword, origin=ORIGIN_ANSWER),
        }
    return out


def tailor_case(binding: CappedBinding, case: Mapping[str, Any], run: int) -> dict[str, Any]:
    """One case run through ``tailor_once`` and the length rule; the row the report stores."""

    from gigai.scout.assessment_core import _extract_json_object
    from gigai.scout.tailor_length import fit_to_pages
    from gigai.scout.tailor_length_store import measure_pages
    from gigai.scout.tailored_resume import apply_no_loss, render_markdown, tailor_once, validate_tailored_output

    job, ctx = job_and_context(case)
    key = (case["id"], str(run))
    binding.budget.begin("tailor", key)
    started = time.monotonic()
    attempt = tailor_once(binding, job, ctx)
    calls = binding.budget.calls_for("tailor", key)
    row: dict[str, Any] = {
        "case": case["id"],
        "run": run,
        "kind": case["kind"],
        "ok": attempt.ok,
        "attempts": attempt.attempts,
        "model_calls": len(calls),
        "validation_error": attempt.validation_error,
        "not_assessed_reason": attempt.not_assessed_reason.value if attempt.not_assessed_reason else None,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "model": None,
        "final": None,
    }
    if not attempt.ok:
        return row
    final = fit_to_pages(attempt.parsed, measure=measure_pages)
    row["final"] = describe(final, case, ctx, measure_pages)
    row["markdown"] = render_markdown(final)
    # The model's own answer: the accepted output validated and settled again, without GigAI's Skills rules or the length rule.
    output = next((call.output_text for call in reversed(calls) if call.output_text), None)
    if output is not None:
        try:
            alone = apply_no_loss(validate_tailored_output(_extract_json_object(output), job, ctx), job, ctx)
        except Exception as exc:  # noqa: BLE001 - reported on the row; the final result above is what the product kept
            row["model_error"] = f"{type(exc).__name__}: {exc}"
        else:
            row["model"] = describe(alone, case, ctx, measure_pages)
    return row


# --- the tables -----------------------------------------------------------------------------------


def _cell(row: Mapping[str, Any]) -> dict[str, str]:
    """One run as the table shows it: what the model did alone, and what the product ended with."""

    if not row.get("ok"):
        return {"model": f"INVALID ({row.get('not_assessed_reason')})", "final": "-"}
    model, final = row.get("model") or {}, row["final"]
    kind = row["kind"]
    if kind == "answer_skill":
        def keywords(side: Mapping[str, Any], who: str) -> str:
            parts = []
            for keyword, found in (side.get("keywords") or {}).items():
                hits = found[who]
                parts.append(f"{keyword}: " + (", ".join(sorted({hit['section'] for hit in hits})) if hits else "no"))
            return "; ".join(parts)

        return {"model": keywords(model, "by_model") or "?", "final": f"model {keywords(final, 'by_model')} | gigai {keywords(final, 'by_gigai')}"}
    if kind == "length":
        length = final["length"]
        cut = f"cut {len(length['cut'])} role(s): {'; '.join(length['cut'])}" if length and length["cut"] else (length["status"] if length else "no cut")
        return {
            "model": f"{model.get('pages')} pages, {model.get('roles_shown')}/{model.get('roles_in_resume')} roles, {model.get('dropped_bullets')} bullets dropped",
            "final": f"{final['pages']} pages, {final['roles_shown']}/{final['roles_in_resume']} roles, {cut}",
        }
    if kind == "skills":
        return {
            "model": f"{len(model.get('skills_lines', []))} skills lines, {model.get('skills_duplicates')} inside another",
            "final": f"{len(final['skills_lines'])} skills lines, {final['skills_duplicates']} inside another",
        }
    changed = bool(final["added_from_answers"] or final["length"] or (model and model.get("skills_lines") != final["skills_lines"]))
    return {
        "model": f"{model.get('lines')} lines, {model.get('rewritten')} rewritten, {model.get('dropped_bullets')} bullets dropped, {model.get('pages')} page(s)",
        "final": "changed by GigAI's rules" if changed else "same as the model's",
    }


def print_table(rows: Sequence[Mapping[str, Any]], *, out=None) -> None:
    out = out or sys.stdout
    print(f"{'case':18} {'run':>3} {'calls':>5}  model alone  ->  final", file=out)
    for row in rows:
        cell = _cell(row)
        print(f"{row['case']:18} {row['run']:>3} {row['model_calls']:>5}  {cell['model']}  ->  {cell['final']}", file=out)


def print_comparison(before: Mapping[str, Any], after: Mapping[str, Any], *, out=None) -> None:
    out = out or sys.stdout
    for name, report in (("BEFORE", before), ("AFTER", after)):
        run = report["run"]
        print(f"{name}: tailor.md {run['instructions_digest'][:19]} head {run['head']} calls {run['calls_made']}/{run['max_calls']} skipped {run['skipped']}", file=out)
        print_table(report["rows"], out=out)
        print("", file=out)


# --- main -----------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="0110-10-05 C: the tailor prompt on four synthetic cases (before/after)")
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS, help=f"hard cap on model calls, the product's own retry included (default {DEFAULT_MAX_CALLS})")
    parser.add_argument("--report", type=Path, default=None, help="where to write the JSON report (required unless --dry-run or --compare)")
    parser.add_argument("--home", type=Path, default=None, help="GigAI home to read the config from; never written")
    parser.add_argument("--model-target", default=harness.DEFAULT_MODEL_TARGET, help=f"adapter kind to resolve (default {harness.DEFAULT_MODEL_TARGET})")
    parser.add_argument("--concurrency", type=int, default=1, help="calls in flight at once: only 1 is supported")
    parser.add_argument("--fake-model", action="store_true", help="offline: a temp home and the GIGAI_SCOUT_FIND_JOBS_TEST_MODEL seam")
    parser.add_argument("--dry-run", action="store_true", help="print the planned case runs and exit without any model call")
    parser.add_argument("--case", action="append", default=None, metavar="ID", help="run only this case (repeatable); default: every case")
    parser.add_argument("--dump-dir", type=Path, default=None, help="write every prompt and raw model output per call under this directory")
    parser.add_argument("--compare", nargs=2, type=Path, default=None, metavar=("BEFORE_JSON", "AFTER_JSON"), help="no calls: print two reports of this runner side by side")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.compare:
        before, after = (json.loads(path.read_text(encoding="utf-8")) for path in args.compare)
        print_comparison(before, after)
        return 0
    payload = load_cases()
    try:
        runs = plan(payload, args.case)
    except ValueError as exc:
        print(f"--case: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        for index, (case, run) in enumerate(runs, 1):
            print(f"{index:3} {case['id']} run {run} ({case['kind']}), {len(case['answers'])} answers, {len(case['matrix'])} matrix rows")
        print(f"{len(runs)} planned case runs; --max-calls {len(runs) + 1} lets every one run when no answer is retried")
        return 0
    if args.concurrency != 1:
        print("--concurrency: only 1 is supported (one call at a time)", file=sys.stderr)
        return 2
    if args.report is None:
        print("--report is required", file=sys.stderr)
        return 2
    if not args.fake_model and os.environ.get("GIGAI_ASSESS_EVAL_LIVE") != "1":
        print("refusing the live tailor cases eval: set GIGAI_ASSESS_EVAL_LIVE=1 explicitly", file=sys.stderr)
        return 2

    now = datetime.now(UTC)
    seams = {"GIGAI_SCOUT_FIND_JOBS_TEST_MODEL": "1"} if args.fake_model else {}
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    budget = CallBudget(max_calls=args.max_calls)
    with tempfile.TemporaryDirectory(prefix="gigai-tailor-cases-eval-") as tmp, harness.seam_env(**seams):
        if args.fake_model:
            home_root = Path(tmp) / "home"
            home_root.mkdir()
            config = harness.build_fake_config(home_root)
            model_target = "ollama_local"
        else:
            from gigai.config import load_config

            home_root = (args.home or harness._default_home()).expanduser()
            config = load_config(home_root)
            model_target = args.model_target
        binding = CappedBinding(harness.resolve_binding(config, model_target, home_root=home_root), budget)
        try:
            for case, run in runs:
                # Worst case for one case run is two calls (the product's single validation retry).
                if budget.made + 2 > args.max_calls:
                    skipped.append(f"{case['id']}/{run}")
                    continue
                try:
                    row = tailor_case(binding, case, run)
                except CallCapReached:
                    skipped.append(f"{case['id']}/{run}")
                    continue
                rows.append(row)
                if not args.quiet:
                    cell = _cell(row)
                    print(f"{case['id']} run {run}: {cell['model']} -> {cell['final']} ({row['elapsed_seconds']}s, {row['model_calls']} call(s))", file=sys.stderr)
        finally:
            binding.close()

    from gigai.canonical import digest_imported_bytes
    from gigai.scout.tailored_resume import TAILOR_INSTRUCTIONS_DIGEST, _instruction_bytes

    report = {
        "schema": REPORT_SCHEMA,
        "run": {
            "started_at": now.isoformat().replace("+00:00", "Z"),
            "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "head": harness._git_head(),
            "fake_model": args.fake_model,
            "model_target": model_target,
            # The template as it is on disk now (re-read per render), not the digest taken at import.
            "instructions_digest": digest_imported_bytes(_instruction_bytes()),
            "instructions_digest_at_import": TAILOR_INSTRUCTIONS_DIGEST,
            "max_calls": args.max_calls,
            "calls_made": budget.made,
            "planned_runs": len(runs),
            "skipped": skipped,
            "cases": os.fspath(CASES_PATH.relative_to(harness.REPO_ROOT)),
        },
        "rows": rows,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.dump_dir is not None:
        args.dump_dir.mkdir(parents=True, exist_ok=True)
        for call in budget.calls:
            stem = f"{call.index:03}-{'-'.join(call.row_key or ('none',))}-attempt{call.attempt}"
            (args.dump_dir / f"{stem}.prompt.txt").write_text(call.prompt, encoding="utf-8")
            (args.dump_dir / f"{stem}.output.txt").write_text(call.output_text or f"<{call.error}>", encoding="utf-8")
    if not args.quiet:
        print_table(rows)
        print(f"calls {budget.made}/{args.max_calls}; skipped {skipped or 'none'}; report: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
