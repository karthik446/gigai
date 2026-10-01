#!/usr/bin/env python3
"""0110-038: the rules A/B mode of the assess eval -- location, sponsorship and work mode on synthetic postings.

A small, bounded run over ``fixtures/rules_cases.json`` (at most 30 synthetic
postings, one synthetic resume): does the configured model follow
``assess.md`` rules 4 (location) and 5 (sponsorship) and the CANDIDATE WORK
MODE paragraph (assess-prompt-v5)?  Each case is called with the shipped
prompt (v5) and, when the candidate has a work mode, once more with the same
candidate and NO work mode: that prompt is byte for byte what
assess-prompt-v4 rendered (pinned in ``test_assess_work_mode.py``), so the
second column is the pre-change prompt on the same posting and the same model.
A candidate with no work mode has one prompt for both versions and is called
once.

Every call goes through the path the product uses, exactly as
``run_assess_eval.py`` does: ``resolve_binding`` (the operator's configured
target for the adapter kind) and ``assessment_core.assess_once`` with the
packaged ``assess.md`` and the context from the ONE shared builder
(``build_assess_context``).

Modes
-----
- LIVE (gated on ``GIGAI_ASSESS_EVAL_LIVE=1``): reads the config from
  ``--home`` (default ``$GIGAI_HOME`` or ``~/.gigai``) READ-ONLY and resolves
  ``--model-target`` (default: ``find-jobs.json``'s ``default_model_target``
  under ``--target``, else ``codex_cli``).
- FAKE (``--fake-model``): a temp home and the ``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL``
  seam, offline.

``--max-calls`` (default 60) is a hard budget on MODEL CALLS, the product's
own single validation retry included: a case is skipped when its worst case
(two calls) no longer fits. One pass, no other retry.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from typing import Any

if __package__ in (None, ""):  # run as a script: make ``tests.evals`` importable
    sys.path.insert(0, os.fspath(Path(__file__).resolve().parents[2]))

from tests.evals import run_assess_eval as harness  # noqa: E402

CASES_PATH = harness.FIXTURES_DIR / "rules_cases.json"
MAX_CASES = 30
DEFAULT_MAX_CALLS = 60
SHIPPED = "v5"
BEFORE = "v4"


def load_cases(path: Path = CASES_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload["cases"]
    if len(cases) > MAX_CASES:
        raise ValueError(f"the rules eval takes at most {MAX_CASES} postings, got {len(cases)}")
    seen: set[str] = set()
    for case in cases:
        if case["id"] in seen:
            raise ValueError(f"duplicate case id {case['id']!r}")
        seen.add(case["id"])
        if case["candidate"] not in payload["candidates"]:
            raise ValueError(f"case {case['id']!r} names an unknown candidate")
        for key in ("expected", "expected_v4"):
            for verdict in case.get(key, ()):
                if verdict not in harness.VERDICTS:
                    raise ValueError(f"case {case['id']!r} {key} has an unknown verdict {verdict!r}")
    return payload


def posting_text(payload: Mapping[str, Any], case: Mapping[str, Any]) -> str:
    intro = payload["posting_intro"].format(company=case["company"], title=case["title"])
    return f"{intro}\n\n{case['statement']}\n\n{payload['requirements']}"


def plan(payload: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], str]]:
    """``(case, version)`` calls in fixture order: v5 for every case, then v4 where its prompt differs."""

    calls: list[tuple[Mapping[str, Any], str]] = []
    for case in payload["cases"]:
        calls.append((case, SHIPPED))
        if payload["candidates"][case["candidate"]]["work_mode"]:
            calls.append((case, BEFORE))
    return calls


def render_prompt(payload: Mapping[str, Any], case: Mapping[str, Any], version: str) -> str:
    from gigai.scout.assessment_core import render_assess_prompt

    job, ctx = _job_and_context(payload, case, version)
    return render_assess_prompt(job, ctx)


def _job_and_context(payload: Mapping[str, Any], case: Mapping[str, Any], version: str):
    from gigai.scout.assessment_core import AssessJob, build_assess_context

    candidate = payload["candidates"][case["candidate"]]
    job = AssessJob(title=case["title"], company=case["company"], location=case["location"], posting_text=posting_text(payload, case))
    ctx = build_assess_context(
        resume_text=payload["resume"],
        visa_sponsorship_required=bool(candidate["visa_sponsorship_required"]),
        countries=tuple(candidate["countries"]),
        titles=tuple(payload["titles"]),
        location=candidate["location"],
        bank=None,
        # The pre-change prompt: the same candidate with no work mode renders the v4 bytes.
        work_mode=candidate["work_mode"] if version == SHIPPED else "",
    )
    return job, ctx


class _Recording:
    """The resolved binding, with each call's resolved model name kept for the report."""

    def __init__(self, binding: object) -> None:
        self._binding = binding
        self.port = self
        self.resolved_models: list[str] = []
        self.invocations = 0  # every model call made, an answered one or not

    def request(self, **kwargs: Any) -> object:
        return self._binding.request(**kwargs)  # type: ignore[attr-defined]

    def invoke(self, request: object) -> object:
        self.invocations += 1
        result = self._binding.port.invoke(request)  # type: ignore[attr-defined]
        resolved = getattr(result, "resolved_model", None)
        if isinstance(resolved, str):
            self.resolved_models.append(resolved)
        return result

    def close(self) -> None:
        close = getattr(self._binding, "close", None)
        if callable(close):
            close()


def assess_case(binding: object, payload: Mapping[str, Any], case: Mapping[str, Any], version: str) -> dict[str, Any]:
    from gigai.scout.assessment_core import assess_once, assess_prompt_version

    job, ctx = _job_and_context(payload, case, version)
    started = time.monotonic()
    attempt = assess_once(binding, job, ctx, parse=harness._parse_body)
    row: dict[str, Any] = {
        "case": case["id"],
        "version": version,
        "prompt_version": assess_prompt_version(ctx.work_mode),
        "ok": attempt.ok,
        "attempts": attempt.attempts,
        "validation_error": attempt.validation_error,
        "not_assessed_reason": attempt.not_assessed_reason.value if attempt.not_assessed_reason else None,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "verdict": None,
        "question_ids": [],
        "not_a_match_reason": None,
        "usage": None,
    }
    if attempt.usage is not None:
        row["usage"] = {"input_tokens": attempt.usage.input_tokens, "output_tokens": attempt.usage.output_tokens, "total_tokens": attempt.usage.total_tokens}
    if attempt.ok:
        body = attempt.parsed
        row["verdict"] = body.verdict.value if body.verdict is not None else None  # type: ignore[union-attr]
        row["question_ids"] = [item.question_id for item in body.structured_questions]  # type: ignore[union-attr]
        row["not_a_match_reason"] = body.not_a_match_reason  # type: ignore[union-attr]
        row["matrix"] = [
            {"requirement": item.requirement, "class": item.requirement_class.value if item.requirement_class else None, "status": item.status.value}
            for item in body.matrix  # type: ignore[union-attr]
        ]
    return row


def summarize(payload: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per case: expected vs the shipped prompt vs the pre-change prompt; pass rates per version and per rule."""

    by_key = {(row["case"], row["version"]): row for row in rows}
    table: list[dict[str, Any]] = []
    for case in payload["cases"]:
        candidate = payload["candidates"][case["candidate"]]
        after = by_key.get((case["id"], SHIPPED))
        same_prompt = not candidate["work_mode"]
        before = after if same_prompt else by_key.get((case["id"], BEFORE))
        expected_v4 = case["expected"] if same_prompt else case.get("expected_v4")
        entry = {
            "case": case["id"],
            "rule": case["rule"],
            "shape": case.get("shape"),
            "candidate": case["candidate"],
            "work_mode": candidate["work_mode"] or None,
            "expected": case["expected"],
            "expected_question_id": case.get("expected_question_id"),
            "v5": None if after is None else after["verdict"] if after["ok"] else f"INVALID ({after['not_assessed_reason']})",
            "v5_questions": [] if after is None else after["question_ids"],
            "v5_pass": None if after is None else bool(after["ok"] and after["verdict"] in case["expected"]),
            "expected_v4": expected_v4,
            "v4": None if before is None else before["verdict"] if before["ok"] else f"INVALID ({before['not_assessed_reason']})",
            "v4_questions": [] if before is None else before["question_ids"],
            "v4_pass": None if before is None or expected_v4 is None else bool(before["ok"] and before["verdict"] in expected_v4),
            "v4_meets_v5_expectation": None if before is None else bool(before["ok"] and before["verdict"] in case["expected"]),
            "same_prompt": same_prompt,
        }
        table.append(entry)

    def rate(items: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
        scored = [item for item in items if item[key] is not None]
        passed = sum(1 for item in scored if item[key])
        return {"passed": passed, "of": len(scored), "rate": round(passed / len(scored), 4) if scored else None}

    rules = sorted({item["rule"] for item in table})
    return {
        "cases": table,
        "v5": rate(table, "v5_pass"),
        "v4_against_the_v5_expectation": rate(table, "v4_meets_v5_expectation"),
        "v4_against_its_own_rules": rate(table, "v4_pass"),
        "by_rule": {rule: {"v5": rate([item for item in table if item["rule"] == rule], "v5_pass"), "v4_against_the_v5_expectation": rate([item for item in table if item["rule"] == rule], "v4_meets_v5_expectation")} for rule in rules},
        "wrong_v5": [item["case"] for item in table if item["v5_pass"] is False],
        "calls": sum(int(row.get("model_calls", row["attempts"])) for row in rows),
        "retries": sum(1 for row in rows if int(row["attempts"]) >= 2),
        "invalid": [f"{row['case']}/{row['version']}" for row in rows if not row["ok"]],
        "tokens": {
            "rows_with_usage": sum(1 for row in rows if row["usage"]),
            "input": sum(int(row["usage"]["input_tokens"] or 0) for row in rows if row["usage"]),
            "output": sum(int(row["usage"]["output_tokens"] or 0) for row in rows if row["usage"]),
        },
    }


def print_table(summary: Mapping[str, Any], *, out=sys.stdout) -> None:
    short = {"matched_above_threshold": "matched", "pending_user_answers": "pending", "not_a_match": "not a match", None: "-"}

    def name(value: object) -> str:
        if isinstance(value, list):
            return " or ".join(short.get(item, str(item)) for item in value)
        return short.get(value, str(value))  # type: ignore[arg-type]

    print("| case | rule | expected | v5 | v4 (pre-change) |", file=out)
    print("|---|---|---|---|---|", file=out)
    for item in summary["cases"]:
        after = name(item["v5"]) + ("" if item["v5_pass"] else " **WRONG**") + (f" {item['v5_questions']}" if item["v5_questions"] else "")
        before = "same prompt" if item["same_prompt"] else name(item["v4"]) + (f" {item['v4_questions']}" if item["v4_questions"] else "")
        print(f"| {item['case']} | {item['rule']} | {name(item['expected'])} | {after} | {before} |", file=out)
    print(f"v5 pass rate: {summary['v5']['passed']}/{summary['v5']['of']}; v4 against the same expectation: {summary['v4_against_the_v5_expectation']['passed']}/{summary['v4_against_the_v5_expectation']['of']}", file=out)
    print(f"model calls: {summary['calls']} (retries: {summary['retries']}; invalid: {summary['invalid']}); tokens: {summary['tokens']}", file=out)


def _default_target(target: Path | None) -> str:
    """``find-jobs.json``'s ``default_model_target`` under ``target``: how the product picks the model."""

    if target is not None:
        try:
            value = json.loads((target / "find-jobs.json").read_text(encoding="utf-8")).get("default_model_target")
            if isinstance(value, str) and value:
                return value
        except (OSError, ValueError):
            pass
    return harness.DEFAULT_MODEL_TARGET


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS, help=f"hard budget on model calls, the product's own retry included (default {DEFAULT_MAX_CALLS})")
    parser.add_argument("--report", type=Path, default=None, help="where to write the JSON report (required unless --dry-run)")
    parser.add_argument("--home", type=Path, default=None, help="GigAI home to read the config from; never written")
    parser.add_argument("--target", type=Path, default=None, help="project folder whose find-jobs.json names the default model target")
    parser.add_argument("--model-target", default=None, help="adapter kind to resolve (default: the --target find-jobs.json's default_model_target)")
    parser.add_argument("--concurrency", type=int, default=1, help="calls in flight at once (default 1)")
    parser.add_argument("--fake-model", action="store_true", help="offline: a temp home and the GIGAI_SCOUT_FIND_JOBS_TEST_MODEL seam")
    parser.add_argument("--dry-run", action="store_true", help="print the planned calls and exit without any model call")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = load_cases()
    calls = plan(payload)
    if args.dry_run:
        for index, (case, version) in enumerate(calls, 1):
            print(f"{index:3} {version} {case['id']} ({case['candidate']}) expects {case['expected']}")
        return 0
    if args.report is None:
        print("--report is required", file=sys.stderr)
        return 2
    if not args.fake_model and os.environ.get("GIGAI_ASSESS_EVAL_LIVE") != "1":
        print("refusing the live rules eval: set GIGAI_ASSESS_EVAL_LIVE=1 explicitly", file=sys.stderr)
        return 2

    now = datetime.now(UTC)
    seams = {"GIGAI_SCOUT_FIND_JOBS_TEST_MODEL": "1"} if args.fake_model else {}
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    budget = threading.Lock()
    reserved = 0
    used = 0

    with tempfile.TemporaryDirectory(prefix="gigai-assess-rules-eval-") as tmp, harness.seam_env(**seams):
        if args.fake_model:
            home_root = Path(tmp) / "home"
            home_root.mkdir()
            config = harness.build_fake_config(home_root)
            model_target = "ollama_local"
        else:
            from gigai.config import load_config

            home_root = (args.home or harness._default_home()).expanduser()
            config = load_config(home_root)
            model_target = args.model_target or _default_target(args.target)
        resolved_models: list[str] = []

        def one(item: tuple[Mapping[str, Any], str]) -> dict[str, Any] | None:
            nonlocal reserved, used
            case, version = item
            with budget:
                # Worst case for one case is two calls (the product's single validation retry).
                if used + reserved + 2 > args.max_calls:
                    skipped.append(f"{case['id']}/{version}")
                    return None
                reserved += 2
            binding = _Recording(harness.resolve_binding(config, model_target, home_root=home_root))
            try:
                row = assess_case(binding, payload, case, version)
            finally:
                binding.close()
                with budget:
                    reserved -= 2
                    used += binding.invocations
                    resolved_models.extend(binding.resolved_models)
            row["model_calls"] = binding.invocations
            if not args.quiet:
                outcome = row["verdict"] if row["ok"] else f"INVALID {row['not_assessed_reason']}: {row['validation_error']}"
                print(f"{case['id']} [{version}] -> {outcome} in {row['elapsed_seconds']}s, attempts {row['attempts']}, questions {row['question_ids']}", file=sys.stderr)
            return row

        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            rows = [row for row in pool.map(one, calls) if row is not None]

    summary = summarize(payload, rows)
    from gigai.scout.assessment_core import ASSESS_PROMPT_VERSION, INSTRUCTIONS_DIGEST

    report = {
        "schema": "gigai-assess-rules-eval-report:1",
        "run": {
            "started_at": now.isoformat().replace("+00:00", "Z"),
            "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "head": harness._git_head(),
            "fake_model": args.fake_model,
            "model_target": model_target,
            "resolved_models": sorted(set(resolved_models)),
            "prompt_version": ASSESS_PROMPT_VERSION,
            "instructions_digest": INSTRUCTIONS_DIGEST,
            "max_calls": args.max_calls,
            "planned_calls": len(calls),
            "skipped": skipped,
            "cases": os.fspath(CASES_PATH.relative_to(harness.REPO_ROOT)),
        },
        "rows": rows,
        "summary": summary,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if not args.quiet:
        print_table(summary)
        print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
