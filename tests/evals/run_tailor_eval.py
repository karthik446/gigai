#!/usr/bin/env python3
"""Q3 (v0.1.9): the tailored-resume fabrication eval -- labelled pairs through the SHIPPED path.

Every model call goes through exactly the code the product uses for one
tailoring (``tailored_resume.run_tailored_resume``):

    proposal_execution.resolve_model_adapter(config, <configured target>)   (C1: module attribute)
    tailored_resume.tailor_once(binding, TailorJob, TailorContext)          (prompt, retry, validation)

with the packaged ``scout/data/instructions/tailor.md`` and the P7 fixtures
(``tests/evals/fixtures``: 15 postings, 11 resumes, ``labels.csv``'s 26
pairs; ``--clean-fit-only`` -> the 6 clean fits) plus ``fixtures/answers.json``
(per resume, a fixed answer for every expected question id the labels list,
so ``A <question_id>`` refs are exercised).

Detection, per ACCEPTED line (the product already rejected the rest):
1. the numeric guard and the posting-term guard RE-RUN here, outside the
   product's validator, on the line and its cited source texts -- a hit is a
   validator regression (the product must have rejected it) and counts;
2. a JUDGE call on the same binding with the eval-only prompt
   ``tests/evals/fabrication_judge.md`` (SOURCES = the cited R/A texts
   verbatim, CLAIM = the line) -> ``{"supported": bool, "unsupported_span"}``.
``fabricated = guard hit OR not supported``.  The report lists EVERY accepted
line with its cited source text (and the span the judge flagged), so the
operator can spot-check a sample -- self-judging is acceptable for 0.1.9
only because this human check exists (orchestrator review, §8 answer 5).
Copy lines are checked verbatim against the resume line they name.

Bars (``metrics.bars``): ``fabricated_claims == 0`` live; invalid-after-retry
< 5% (``INVALID_AFTER_RETRY_BAR``, shared with the assess eval).

Excluded rows (``excluded=true`` in ``labels.csv``, the operator's label
policy rule E) follow ``run_assess_eval`` exactly: ``load_labels`` leaves
them out, ``plan_rows`` never plans one, ``summarize`` ignores any stored row
flagged ``excluded``, and the report lists them under ``run.excluded_rows``.

Modes: LIVE (``GIGAI_ASSESS_EVAL_LIVE=1``, the operator's home read-only,
report under ``../orchestrator/research/evals/tailor-<date>.json``) and FAKE
(``--fake-model``: temp home, the ``bindings._test_model_handler`` seam --
the fixture answers a tailor prompt with lines built from the prompt's own
``R1``/``A cloud:gcp`` sources and judges every claim supported).  No live
run happens in the packet that ships this file.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any

from tests.evals.run_assess_eval import (
    DEFAULT_MODEL_TARGET,
    DEFAULT_REPORT_DIR,
    FIXTURES_DIR,
    INVALID_AFTER_RETRY_BAR,
    REPO_ROOT,
    Label,
    Posting,
    Resume,
    _default_home,
    _git_head,
    _latency,
    _rate,
    build_fake_config,
    load_labels,
    load_postings,
    load_resumes,
    plan_rows,
    resolve_binding,
    seam_env,
)

EVAL_DIR = Path(__file__).resolve().parent
ANSWERS_PATH = FIXTURES_DIR / "answers.json"
JUDGE_PROMPT_PATH = EVAL_DIR / "fabrication_judge.md"
DEFAULT_MAX_CALLS = 26
REPORT_SCHEMA = "gigai-tailor-eval-report:1"
FABRICATED_CLAIMS_BAR = 0

_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")


# --- fixtures -----------------------------------------------------------------------------


@dataclass(frozen=True)
class FixedAnswer:
    question_id: str
    answer: str


def load_answers(path: Path = ANSWERS_PATH) -> dict[str, tuple[FixedAnswer, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    answers: dict[str, tuple[FixedAnswer, ...]] = {}
    for resume_id, items in payload["answers"].items():
        answers[resume_id] = tuple(FixedAnswer(item["question_id"], item["answer"]) for item in items)
    return answers


def load_judge_template(path: Path = JUDGE_PROMPT_PATH) -> str:
    text = path.read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


def render_judge_prompt(sources: Sequence[tuple[str, str]], claim: str, template: str | None = None) -> str:
    """The judge prompt: SOURCES = ``<label>: <text>`` lines verbatim, CLAIM = the line."""

    values = {
        "sources": "\n".join(f"{label}: {text}" for label, text in sources),
        "claim": claim,
    }
    body = load_judge_template() if template is None else template

    def fill(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ValueError(f"fabrication_judge.md uses an unknown placeholder {{{{{key}}}}}")
        return values[key]

    return "\n\n".join(_PLACEHOLDER.sub(fill, block) for block in body.split("\n\n"))


def parse_judge_answer(decoded: Mapping[str, Any]) -> dict[str, Any]:
    supported = decoded.get("supported")
    span = decoded.get("unsupported_span")
    if type(supported) is not bool:
        raise ValueError("judge answer must carry a boolean supported")
    if span is not None and not isinstance(span, str):
        raise ValueError("judge answer unsupported_span must be a string or null")
    return {"supported": supported, "unsupported_span": span if not supported else None}


def judge_line(binding: object, sources: Sequence[tuple[str, str]], claim: str) -> dict[str, Any]:
    """One judge call through the shared retry loop; a failed judge is reported, never hidden."""

    from gigai.scout.assessment_core import invoke_json_once

    attempt = invoke_json_once(
        binding,
        lambda _error: render_judge_prompt(sources, claim),
        parse_judge_answer,
        role="reviewer",
    )
    if attempt.ok:
        parsed = attempt.parsed
        assert isinstance(parsed, dict)
        verdict: dict[str, Any] = dict(parsed)
        verdict["judge_ok"] = True
        verdict["judge_attempts"] = attempt.attempts
        return verdict
    return {
        "supported": None,
        "unsupported_span": None,
        "judge_ok": False,
        "judge_attempts": attempt.attempts,
        "judge_error": attempt.validation_error or (attempt.not_assessed_reason.value if attempt.not_assessed_reason else "unknown"),
    }


# --- detection outside the product ---------------------------------------------------------------


def detect_line(text: str, sources: Sequence[str], terms: Sequence[str] | frozenset[str]) -> dict[str, Any]:
    """The two deterministic guards re-run on one accepted rewritten line."""

    from gigai.scout.tailored_resume import unsupported_numbers, unsupported_posting_terms

    numbers = [mention.span for mention in unsupported_numbers(text, sources)]
    borrowed = list(unsupported_posting_terms(text, sources, terms))
    return {"numeric_hits": numbers, "term_hits": borrowed, "guard_hit": bool(numbers or borrowed)}


# --- one row -------------------------------------------------------------------------------------


def _context(resume: Resume, answers: Sequence[FixedAnswer]):
    from gigai.scout.tailored_resume import AnswerSource, TailorContext, resume_lines

    return TailorContext(
        resume_lines=resume_lines(resume.text),
        answers={item.question_id: AnswerSource(item.question_id, item.answer, "eval") for item in answers},
    )


def _job(posting: Posting):
    from gigai.scout.tailored_resume import TailorJob

    return TailorJob(title=posting.title, company=posting.company, location=posting.location, posting_text=posting.full_text)


def tailor_row(binding: object, label: Label, posting: Posting, resume: Resume, answers: Sequence[FixedAnswer], *, judge: bool = True) -> dict[str, Any]:
    """One labelled pair through ``tailor_once``; every accepted line detected and listed."""

    from gigai.scout.tailored_resume import TailoredResume, guard_terms, render_markdown, tailor_once

    job = _job(posting)
    ctx = _context(resume, answers)
    terms = guard_terms(job, ctx)
    started = time.monotonic()
    attempt = tailor_once(binding, job, ctx)
    elapsed = time.monotonic() - started
    row: dict[str, Any] = {
        "resume_id": label.resume_id,
        "posting_id": label.posting_id,
        "clean_fit": label.clean_fit,
        "excluded": label.excluded,  # rule E: never planned, never scored (run_assess_eval's rule)
        "answers": [item.question_id for item in answers],
        "ok": attempt.ok,
        "attempts": attempt.attempts,
        "retried": attempt.attempts >= 2,
        "validation_error": attempt.validation_error,
        "not_assessed_reason": attempt.not_assessed_reason.value if attempt.not_assessed_reason else None,
        "elapsed_seconds": round(elapsed, 3),
        "usage": None,
        "guard_terms": sorted(terms),
        "sections": [],
        "lines": [],
        "copy_lines": 0,
        "rewritten_lines": 0,
        "fabricated_lines": [],
        "judge_calls": 0,
        "judge_failures": 0,
        "markdown": None,
    }
    if attempt.usage is not None:
        row["usage"] = {"input_tokens": attempt.usage.input_tokens, "output_tokens": attempt.usage.output_tokens, "total_tokens": attempt.usage.total_tokens}
    if not attempt.ok:
        return row
    result = attempt.parsed
    assert isinstance(result, TailoredResume)
    row["sections"] = [section.heading for section in result.sections]
    row["markdown"] = render_markdown(result)
    where_lines: list[tuple[str, Any]] = [(f"header[{index}]", line) for index, line in enumerate(result.header, 1)]
    for section in result.sections:
        for index, line in enumerate(section.lines, 1):
            where_lines.append((f"{section.heading} line {index}", line))
        for position, block in enumerate(section.entries, 1):
            for index, line in enumerate(block.heading, 1):
                where_lines.append((f"{section.heading} entry {position} heading[{index}]", line))
            for index, line in enumerate(block.bullets, 1):
                where_lines.append((f"{section.heading} entry {position} bullet {index}", line))
    for where, line in where_lines:
        cited = [(ref.label(), ref.text) for ref in line.refs]
        entry: dict[str, Any] = {"where": where, "kind": line.kind, "text": line.text, "sources": [{"label": label_, "text": text} for label_, text in cited]}
        if line.kind == "copy":
            row["copy_lines"] += 1
            entry["verbatim"] = line.refs[0].text == line.text
            entry["fabricated"] = not entry["verbatim"]
        else:
            row["rewritten_lines"] += 1
            entry.update(detect_line(line.text, [text for _label, text in cited], terms))
            if judge:
                row["judge_calls"] += 1
                verdict = judge_line(binding, cited, line.text)
                entry["judge"] = verdict
                if not verdict["judge_ok"]:
                    row["judge_failures"] += 1
                entry["fabricated"] = entry["guard_hit"] or verdict["supported"] is False
            else:
                entry["judge"] = None
                entry["fabricated"] = entry["guard_hit"]
        if entry["fabricated"]:
            row["fabricated_lines"].append(entry)
        row["lines"].append(entry)
    return row


# --- metrics --------------------------------------------------------------------------------------


def summarize(rows: Sequence[Mapping[str, Any]], *, planned: int, max_calls: int, judge: bool) -> dict[str, Any]:
    rows = [row for row in rows if not row.get("excluded")]  # rule E: an excluded row enters no metric
    valid = [row for row in rows if row["ok"]]
    invalid_after_retry = [row for row in rows if not row["ok"] and row["not_assessed_reason"] == "model_output_invalid"]
    transport_failures = [row for row in rows if not row["ok"] and row["not_assessed_reason"] in {"model_unavailable", "model_denied"}]
    retried = [row for row in rows if row["retried"]]
    recovered = [row for row in valid if row["retried"]]
    lines = sum(len(row["lines"]) for row in valid)
    rewritten = sum(int(row["rewritten_lines"]) for row in valid)
    copied = sum(int(row["copy_lines"]) for row in valid)
    fabricated = [
        {"resume_id": row["resume_id"], "posting_id": row["posting_id"], **entry}
        for row in valid
        for entry in row["fabricated_lines"]
    ]
    numeric_hits = sum(1 for row in valid for entry in row["lines"] if entry.get("numeric_hits"))
    term_hits = sum(1 for row in valid for entry in row["lines"] if entry.get("term_hits"))
    non_verbatim = sum(1 for row in valid for entry in row["lines"] if entry["kind"] == "copy" and not entry["verbatim"])
    judge_unsupported = sum(1 for row in valid for entry in row["lines"] if (entry.get("judge") or {}).get("supported") is False)
    judge_calls = sum(int(row["judge_calls"]) for row in valid)
    judge_failures = sum(int(row["judge_failures"]) for row in valid)
    answer_refs = sum(1 for row in valid for entry in row["lines"] for source in entry["sources"] if source["label"].startswith("A "))
    invalid_rate = _rate(len(invalid_after_retry), len(rows))
    usage_rows = [row["usage"] for row in rows if row.get("usage")]
    return {
        "calls": {"planned": planned, "made": len(rows), "max_calls": max_calls, "stopped_at_cap": planned > len(rows)},
        "lines": {"total": lines, "copy": copied, "rewritten": rewritten, "answer_refs": answer_refs},
        "fabrication": {
            "fabricated_claims": len(fabricated),
            "fabrication_rate": _rate(len(fabricated), rewritten + copied),
            "numeric_guard_hits": numeric_hits,
            "posting_term_guard_hits": term_hits,
            "copy_lines_not_verbatim": non_verbatim,
            "judge_enabled": judge,
            "judge_calls": judge_calls,
            "judge_unsupported": judge_unsupported,
            "judge_failures": judge_failures,
            "lines": fabricated,
        },
        "reliability": {
            "valid": len(valid),
            "invalid": len(rows) - len(valid),
            "valid_output_rate": _rate(len(valid), len(rows)),
            "invalid_after_retry": len(invalid_after_retry),
            "invalid_after_retry_rate": invalid_rate,
            "invalid_after_retry_bar": INVALID_AFTER_RETRY_BAR,
            "invalid_after_retry_bar_met": (invalid_rate is not None and invalid_rate < INVALID_AFTER_RETRY_BAR),
            "transport_failures": len(transport_failures),
            "retries": len(retried),
            "recovered_on_retry": len(recovered),
            "rejections": [
                {"resume_id": row["resume_id"], "posting_id": row["posting_id"], "reason": row["not_assessed_reason"], "validation_error": row["validation_error"]}
                for row in rows
                if not row["ok"] or row["retried"]
            ],
            "latency_seconds": _latency([float(row["elapsed_seconds"]) for row in rows]),
            "tokens": {
                "input": sum(int(item["input_tokens"] or 0) for item in usage_rows),
                "output": sum(int(item["output_tokens"] or 0) for item in usage_rows),
                "total": sum(int(item["total_tokens"] or 0) for item in usage_rows),
            },
            "model_cost_usd": "unavailable",
        },
        "bars": {
            "fabricated_claims_bar": FABRICATED_CLAIMS_BAR,
            "fabricated_claims_bar_met": len(fabricated) == FABRICATED_CLAIMS_BAR and len(valid) > 0,
            "invalid_after_retry_bar_met": (invalid_rate is not None and invalid_rate < INVALID_AFTER_RETRY_BAR),
        },
    }


# --- CLI -----------------------------------------------------------------------------------------


def default_report_path(*, fake: bool, now: datetime) -> Path:
    date = now.strftime("%Y-%m-%d")
    if fake:
        return Path(tempfile.gettempdir()) / f"gigai-tailor-eval-fake-{date}-{now.strftime('%H%M%S')}.json"
    path = DEFAULT_REPORT_DIR / f"tailor-{date}.json"
    if path.exists():
        path = DEFAULT_REPORT_DIR / f"tailor-{date}-{now.strftime('%H%M%S')}.json"
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS, help=f"stop after this many tailor calls (default {DEFAULT_MAX_CALLS}; judge calls are extra)")
    parser.add_argument("--sample", type=int, default=None, help="draw this many labelled rows at random (with --seed) before ordering")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--clean-fit-only", action="store_true", help="only the clean-fit rows")
    parser.add_argument("--resume", action="append", default=[], metavar="RESUME_ID", help="only rows for this resume (repeatable)")
    parser.add_argument("--posting", action="append", default=[], metavar="POSTING_ID", help="only rows for this posting (repeatable)")
    parser.add_argument("--no-judge", action="store_true", help="skip the judge call; only the deterministic guards run")
    parser.add_argument("--report", type=Path, default=None, help="report path (default ../orchestrator/research/evals/tailor-<date>.json)")
    parser.add_argument("--home", type=Path, default=None, help="GigAI home to read the config from (default $GIGAI_HOME or ~/.gigai); never written")
    parser.add_argument("--model-target", default=DEFAULT_MODEL_TARGET, help=f"sealed adapter kind to resolve through the operator's config (default {DEFAULT_MODEL_TARGET})")
    parser.add_argument("--fake-model", action="store_true", help="offline: a temp home with the ollama_local fixture target and the GIGAI_SCOUT_FIND_JOBS_TEST_MODEL seam")
    parser.add_argument("--dry-run", action="store_true", help="print the planned rows and exit without any model call")
    parser.add_argument("--quiet", action="store_true")
    return parser


def _print_summary(metrics: Mapping[str, Any], report_path: Path, *, out=sys.stdout) -> None:
    calls = metrics["calls"]
    lines = metrics["lines"]
    fab = metrics["fabrication"]
    rel = metrics["reliability"]
    print("=== tailor eval summary ===", file=out)
    print(f"calls: {calls['made']}/{calls['planned']} planned (cap {calls['max_calls']}); judge calls {fab['judge_calls']} (failures {fab['judge_failures']})", file=out)
    print(f"lines: {lines['total']} ({lines['copy']} copied, {lines['rewritten']} rewritten, {lines['answer_refs']} answer refs)", file=out)
    print(f"fabricated claims: {fab['fabricated_claims']} (rate {fab['fabrication_rate']}; bar 0 met: {metrics['bars']['fabricated_claims_bar_met']}) -- numeric {fab['numeric_guard_hits']}, posting-term {fab['posting_term_guard_hits']}, copy-not-verbatim {fab['copy_lines_not_verbatim']}, judge-unsupported {fab['judge_unsupported']}", file=out)
    for entry in fab["lines"]:
        cited = "; ".join(f"{source['label']}: {source['text']}" for source in entry["sources"])
        span = (entry.get("judge") or {}).get("unsupported_span")
        print(f"  FAB {entry['resume_id']} x {entry['posting_id']} {entry['where']}: {entry['text']!r} | span: {span!r} | numeric {entry.get('numeric_hits')} terms {entry.get('term_hits')} | sources: {cited}", file=out)
    print(f"reliability: valid {rel['valid']}/{calls['made']} ({rel['valid_output_rate']}); invalid after retry {rel['invalid_after_retry']} ({rel['invalid_after_retry_rate']}, bar < {rel['invalid_after_retry_bar']} met: {rel['invalid_after_retry_bar_met']}); retries {rel['retries']} (recovered {rel['recovered_on_retry']}); transport failures {rel['transport_failures']}", file=out)
    for item in rel["rejections"]:
        print(f"  REJECTED {item['resume_id']} x {item['posting_id']}: {item['reason'] or 'recovered on retry'} | {item['validation_error']}", file=out)
    print(f"latency s: {rel['latency_seconds']}; tokens: {rel['tokens']}; model cost: {rel['model_cost_usd']}", file=out)
    print(f"report: {report_path}", file=out)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = datetime.now(UTC)
    postings = load_postings()
    resumes = load_resumes()
    labels = load_labels()
    answers = load_answers()
    excluded = [label for label in load_labels(include_excluded=True) if label.excluded]
    rows_to_call = plan_rows(labels, sample=args.sample, seed=args.seed, clean_fit_only=args.clean_fit_only, resume_ids=args.resume, posting_ids=args.posting)
    if args.dry_run:
        for index, label in enumerate(rows_to_call, 1):
            marker = "clean" if label.clean_fit else "uncertain" if label.uncertain else "confident"
            called = "call" if index <= args.max_calls else "skip (cap)"
            ids = ";".join(item.question_id for item in answers.get(label.resume_id, ()))
            print(f"{index:3} {called:10} {marker:9} {label.resume_id} x {label.posting_id} answers {ids or '-'}")
        for label in excluded:
            print(f"    excluded  {label.resume_id} x {label.posting_id} (rule E: not planned, not scored)", file=sys.stderr)
        return 0

    report_path = args.report or default_report_path(fake=args.fake_model, now=now)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    seams: dict[str, str] = {"GIGAI_SCOUT_FIND_JOBS_TEST_MODEL": "1"} if args.fake_model else {}
    with tempfile.TemporaryDirectory(prefix="gigai-tailor-eval-") as tmp, seam_env(**seams):
        tmp_root = Path(tmp)
        if args.fake_model:
            home_root = tmp_root / "home"
            home_root.mkdir()
            config = build_fake_config(home_root)
            model_target = "ollama_local"
        else:
            from gigai.config import load_config

            home_root = (args.home or _default_home()).expanduser()
            config = load_config(home_root)
            model_target = args.model_target

        binding = resolve_binding(config, model_target, home_root=home_root)
        results: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        try:
            for index, label in enumerate(rows_to_call, 1):
                if index > args.max_calls:
                    skipped.append({"resume_id": label.resume_id, "posting_id": label.posting_id})
                    continue
                if not args.quiet:
                    print(f"[{index}/{min(len(rows_to_call), args.max_calls)}] {label.resume_id} x {label.posting_id}", file=sys.stderr)
                row = tailor_row(binding, label, postings[label.posting_id], resumes[label.resume_id], answers.get(label.resume_id, ()), judge=not args.no_judge)
                results.append(row)
                if not args.quiet:
                    outcome = (
                        f"{len(row['lines'])} lines ({row['rewritten_lines']} rewritten), {len(row['fabricated_lines'])} fabricated"
                        if row["ok"]
                        else f"INVALID {row['not_assessed_reason']}: {row['validation_error']}"
                    )
                    print(f"    -> {outcome} in {row['elapsed_seconds']}s, attempts {row['attempts']}", file=sys.stderr)
        finally:
            close = getattr(binding, "close", None)
            if callable(close):
                close()

    metrics = summarize(results, planned=len(rows_to_call), max_calls=args.max_calls, judge=not args.no_judge)
    from gigai.scout.tailored_resume import TAILOR_INSTRUCTIONS_DIGEST

    report = {
        "schema": REPORT_SCHEMA,
        "run": {
            "started_at": now.isoformat().replace("+00:00", "Z"),
            "git_head": _git_head(),
            "fake_model": bool(args.fake_model),
            "judge": not args.no_judge,
            "model_target": model_target,
            "adapter_target": getattr(getattr(binding, "port", None), "name", None) or model_target,
            "instructions_digest": TAILOR_INSTRUCTIONS_DIGEST,
            "judge_prompt_sha256": __import__("hashlib").sha256(JUDGE_PROMPT_PATH.read_bytes()).hexdigest(),
            "max_calls": args.max_calls,
            "clean_fit_only": bool(args.clean_fit_only),
            "sample": args.sample,
            "seed": args.seed,
            "skipped_rows": skipped,
            "excluded_rows": [{"resume_id": label.resume_id, "posting_id": label.posting_id} for label in excluded],
            "repo_root": str(REPO_ROOT),
        },
        "metrics": metrics,
        "rows": results,
    }
    report_path.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if not args.quiet:
        _print_summary(metrics, report_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
