#!/usr/bin/env python3
"""0.1.10.9 master P4: today's tailor against the master-based tailor, on synthetic data only.

The inputs are the master-resume spike's (``fixtures/master``): one invented
person's 8-page master, two profiles, three postings, and each profile's own
resume as it is "today" (``legacy-ai.md``, 2 pages; ``legacy-swe.md``, 3 pages,
older, without the newest project).  Each case is one profile x one posting.

VARIANTS, one tailor call per case each:

- ``today``: the product's path WITHOUT a master.  The profile's own resume
  through ``tailored_resume.tailor_once`` (the packaged ``tailor.md``) and the
  length rule (``tailor_length.fit_to_pages``).  This is the baseline.
- ``view`` / ``shortlist`` / ``evidence``: the product's path WITH a master
  (``tailor_master``).  Code prepares the job's candidate set from the whole
  master (the 2-page selection, the selector's pick before the page fit, or
  the evidence view's bullets), the same ``tailor_once`` call orders and words
  inside it, and code fits the result to 2 pages.  The profile's prior is the
  lines of its own resume, as after the migration.

A result that shows a rewritten line costs one more call: the tailor eval's
batched fabrication judge (``run_tailor_eval.judge_resume``).

MEASURED per result: pages of the PDF the shipped renderer makes (auto fit),
the Scout ATS score and the posting key skills found (one keyword list per
posting for every variant: the alias table plus the master's skills), lines
that state something their sources do not (``invented``: a copy that is not
verbatim, a rewrite that fails the numeric or posting-term guard re-run
outside the product, a rewrite the judge calls unsupported), model calls,
tokens as the CLI reports them, prompt characters, and how many of the
SYNTHETIC labels the resume shows.

THE LABELS ARE SYNTHETIC (``fixtures/master/synthetic-labels.json``): for
each case, the lines one model call picked from the whole master in the spike.
They stand in for the operator's labelled set, which does not exist yet.

``tailor.md`` is read again on every render: run ``--variant today`` to its end
BEFORE editing the template.

Modes: LIVE (``GIGAI_ASSESS_EVAL_LIVE=1``; the config is read from ``--home``,
never written) and FAKE (``--fake-model``: a temp home and the
``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL`` seam, offline).  ``--max-calls`` is a hard
cap on model calls, the product's own single retry and the judge included: a
case is skipped when its tailor call and that call's retry no longer fit, and
a judge call the cap refuses leaves ``invented`` unknown for that row.  One
call at a time (``--concurrency`` accepts only 1).

``--compare A.json B.json ...`` makes no call: it prints the reports' summary
rows side by side.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
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
from tests.evals.run_tailor_eval import CallBudget, CallCapReached, CappedBinding, JudgeClaim, detect_line, judge_resume  # noqa: E402

FIXTURES = harness.FIXTURES_DIR / "master"
LABELS_PATH = FIXTURES / "synthetic-labels.json"
REPORT_SCHEMA = "gigai-master-tailor-eval-report:1"
#: The day the fixtures were written for: which roles are "old" depends on the year.
TODAY = date(2026, 10, 3)
STAMP = datetime(2026, 10, 3, tzinfo=UTC)
VARIANT_TODAY = "today"
MASTER_VARIANTS = ("view", "shortlist", "evidence")
VARIANTS = (VARIANT_TODAY, *MASTER_VARIANTS)
LEGACY = {"profile-ai": "legacy-ai.md", "profile-swe": "legacy-swe.md"}


# --- the fixtures -----------------------------------------------------------------------------------


def load_master():
    from gigai.scout.master_resume import parse_master

    return parse_master((FIXTURES / "master.md").read_text(encoding="utf-8"))


def load_profiles() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "profiles.json").read_text(encoding="utf-8"))["profiles"]


def load_postings() -> list[dict[str, str]]:
    out = []
    for path in sorted((FIXTURES / "postings").glob("*.md")):
        head, _, body = path.read_text(encoding="utf-8").partition("\n\n")
        meta = dict(line.split(": ", 1) for line in head.splitlines())
        out.append({"id": path.stem, "title": meta["title"], "company": meta["company"], "location": meta.get("location", ""), "text": body.strip() + "\n"})
    return out


def load_labels(path: Path = LABELS_PATH) -> dict[tuple[str, str], frozenset[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "gigai-master-tailor-labels:1":
        raise ValueError(f"{path} is not a gigai-master-tailor-labels:1 fixture")
    return {(case["profile"], case["posting"]): frozenset(case["ids"]) for case in payload["cases"]}


def legacy_resume(profile_id: str) -> str:
    return (FIXTURES / LEGACY[profile_id]).read_text(encoding="utf-8")


def _bare(text: str) -> str:
    """A resume or master line as it compares: no bullet or heading marker, no trailing comment."""

    from gigai.scout.tailored_resume import _display

    return _display(text.split("<!--", 1)[0].strip())


def ids_by_text(master) -> dict[str, str]:
    """Master line text -> its id (bullets, summaries, Other lines); a text two lines share is left out."""

    seen: dict[str, str | None] = {}
    for item in master.items.values():
        if item.kind == "skills":
            continue
        seen[item.text] = None if item.text in seen else item.id
    return {text: item_id for text, item_id in seen.items() if item_id is not None}


def base_ids(master, resume_text: str) -> tuple[str, ...]:
    """The master ids a profile's own resume shows, as the migration records them: its lines and its entries, by text."""

    by_text = ids_by_text(master)
    headings = {entry.heading: entry.id for entry in master.entries.values()}
    out: list[str] = []
    for line in resume_text.splitlines():
        bare = _bare(line)
        found = headings.get(bare) if line.startswith("### ") else by_text.get(bare)
        if found is not None and found not in out:
            out.append(found)
    return tuple(out)


def plan(variants: Sequence[str], profiles: Sequence[Mapping[str, Any]], postings: Sequence[Mapping[str, str]], limit: int | None = None) -> list[tuple[str, Mapping[str, Any], Mapping[str, str]]]:
    """``(variant, profile, posting)`` in order: every case of the first variant, then the next variant's."""

    unknown = [name for name in variants if name not in VARIANTS]
    if unknown:
        raise ValueError(f"unknown variant {', '.join(unknown)}; known variants: {', '.join(VARIANTS)}")
    return [(variant, profile, posting) for variant in variants for profile in profiles for posting in postings][: limit if limit else None]


# --- what one result shows ----------------------------------------------------------------------------


def shown_ids(result: Any, by_text: Mapping[str, str]) -> tuple[list[str], int]:
    """The master ids a result's body lines show, and how many refs carry an ``item_id`` their text contradicts.

    A line shows the master lines it copies or cites: the ref's ``item_id`` when the tailoring recorded one
    (the master path), else the master line with the same text (``today``: the profile's resume was cut from
    the master, so most of its lines are master lines word for word)."""

    out: list[str] = []
    mismatched = 0
    for section in result.sections:
        if section.heading == "skills":
            continue
        for line in section.body_lines():
            for ref in line.refs:
                if ref.kind != "resume":
                    continue
                by_words = by_text.get(_bare(ref.text))
                recorded = getattr(ref, "item_id", None)
                if recorded is not None and by_words is not None and recorded != by_words:
                    mismatched += 1
                found = recorded or by_words
                if found is not None and found not in out:
                    out.append(found)
    return out, mismatched


def measure(result: Any, posting: Mapping[str, str], keywords: Any, by_text: Mapping[str, str], labels: frozenset[str]) -> dict[str, Any]:
    """The facts one result is compared on (see the module text)."""

    from gigai.scout import ats_score
    from gigai.scout.resume_pdf import _body, _render
    from gigai.scout.tailored_resume import tailor_line_stats

    rendered = _render(_body(result), None, company=posting["company"], timestamp=STAMP, spacing_scale=1.0, auto_fit=True, count_pages=True)
    score = ats_score.score(rendered.pdf, result, keywords)
    coverage = score.breakdown["coverage"]
    stats = tailor_line_stats(result)
    shown, mismatched = shown_ids(result, by_text)
    bullets = [item for item in shown if item.startswith("b-")]
    wanted = {item for item in labels if item.startswith("b-")}
    length = result.length
    return {
        "pages": rendered.pages,
        "spacing_scale": rendered.spacing_scale,
        "ats": score.score,
        "ats_line": score.line,
        "key_skills": coverage["key_skills"],
        "missing_must": coverage["missing_must"],
        "missing_nice": coverage["missing_nice"],
        "lines": result.line_count(),
        "bullets": sum(len(entry.bullets) for section in result.sections for entry in section.entries),
        "shown_rewritten": stats.shown_rewritten,
        "copied": stats.copied,
        "fallbacks": stats.fallbacks,
        "dropped_bullets": stats.dropped_bullets,
        "master_ids_shown": shown,
        "item_id_mismatches": mismatched,
        "labels": len(wanted),
        "labels_shown": len(wanted & set(bullets)),
        "label_jaccard": round(len(wanted & set(bullets)) / len(wanted | set(bullets)), 2) if wanted | set(bullets) else None,
        "length": None if length is None else {
            "status": length.status, "pages": length.pages, "full_pages": length.full_pages, "cut_roles": list(length.roles()),
            "cut_bullets": length.trimmed_count(),
        },
    }


def invented(result: Any, terms: Any) -> tuple[dict[str, Any], list[JudgeClaim]]:
    """What code can check of "invented": copies that are not verbatim and guard hits; and the judge's claims."""

    not_verbatim = 0
    guard_hits = 0
    claims: list[JudgeClaim] = []
    for section in result.sections:
        for line in section.all_lines():
            if line.kind == "copy":
                not_verbatim += int(line.text != line.refs[0].text)
            elif line.kind == "rewritten":
                guard_hits += int(detect_line(line.text, [ref.text for ref in line.refs], terms)["guard_hit"])
                claims.append(JudgeClaim(len(claims) + 1, line.text, tuple((ref.label(), ref.text) for ref in line.refs)))
    return {"copies_not_verbatim": not_verbatim, "guard_hits": guard_hits, "rewritten_lines": [claim.text for claim in claims]}, claims


def _usage(budget: CallBudget, start: int) -> dict[str, Any]:
    calls = budget.calls[start:]
    return {
        "calls": len(calls),
        "seconds": round(sum(call.elapsed_seconds for call in calls), 1),
        "input_tokens": sum((call.usage or {}).get("input_tokens") or 0 for call in calls),
        "output_tokens": sum((call.usage or {}).get("output_tokens") or 0 for call in calls),
        "prompt_chars": sum(len(call.prompt) for call in calls),
        "output_chars": sum(len(call.output_text or "") for call in calls),
    }


# --- one case -----------------------------------------------------------------------------------------


def run_case(
    binding: CappedBinding, variant: str, profile: Mapping[str, Any], posting: Mapping[str, str], *, master: Any, keywords: Any,
    labels: frozenset[str], judge: bool, out_dir: Path | None,
) -> dict[str, Any]:
    """One case through the product's tailor path for ``variant``; the row the report stores."""

    from gigai.scout.tailored_resume import TailorJob, guard_terms, render_markdown, render_tailor_prompt, tailor_context, tailor_once

    budget = binding.budget
    key = (variant, f"{profile['profile_id']}/{posting['id']}")
    job = TailorJob(title=posting["title"], company=posting["company"], location=posting["location"], posting_text=posting["text"])
    by_text = ids_by_text(master)
    resume = legacy_resume(profile["profile_id"])
    row: dict[str, Any] = {"variant": variant, "profile": profile["profile_id"], "posting": posting["id"]}
    candidates = None
    if variant == VARIANT_TODAY:
        ctx = tailor_context(resume)
    else:
        from gigai.scout import tailor_master
        from gigai.scout.master_selection import SelectionPosting, SelectionProfile

        candidates = tailor_master.job_candidates(
            master,
            SelectionProfile(titles=tuple(profile["titles"]), base_ids=base_ids(master, resume), profile_id=profile["profile_id"], label=profile["label"]),
            SelectionPosting(posting["title"], posting["text"], posting["company"], posting["location"]),
            mode=variant, today=TODAY,
        )
        ctx = candidates.context()
        row["candidates"] = {"lines": len(ctx.resume_lines), "bullets": candidates.bullet_count, "skills": len(candidates.skills), "chars": len(candidates.markdown)}
    prompt = render_tailor_prompt(job, ctx)
    listed = sum(1 for number, _line in ctx.model.lines if f"\nR{number}: " in "\n" + prompt)
    row.update({"resume_lines": len(ctx.resume_lines), "resume_lines_in_prompt": listed, "prompt_truncated": listed < len(ctx.model.lines), "prompt_chars": len(prompt)})

    start = budget.made
    budget.begin("tailor", key)
    started = time.monotonic()
    attempt = tailor_once(binding, job, ctx)
    row.update(_usage(budget, start))
    row.update({"ok": attempt.ok, "attempts": attempt.attempts, "validation_error": attempt.validation_error})
    if not attempt.ok:
        row["not_assessed_reason"] = attempt.not_assessed_reason.value if attempt.not_assessed_reason else None
        if candidates is None:
            row["wall_seconds"] = round(time.monotonic() - started, 1)
            return row
        # The master path's fallback: the code's own selection, no model.
        result, candidates = tailor_master.code_only(master, candidates, job, today=TODAY)
        row["picked_by"] = "code"
    elif candidates is None:
        from gigai.scout.tailor_length import fit_to_pages
        from gigai.scout.tailor_length_store import measure_pages

        result = fit_to_pages(attempt.parsed, measure=measure_pages)
    else:
        # What ``MasterTailoring.finish`` does: the code's Skills line shown, then the fit.
        result = tailor_master.fit_selected(tailor_master.ensure_skills_line(attempt.parsed, candidates, ctx), candidates, master, today=TODAY)
        row["picked_by"] = "model"
    row.update(measure(result, posting, keywords, by_text, labels))
    if candidates is not None:
        record = tailor_master.selection_record(master, candidates, result, picked_by=row["picked_by"])
        row["selection"] = {"picked": len(record.picked), "left_out": len(record.left_out), "cut_for_length": len(record.cut_for_length)}
    checks, claims = invented(result, guard_terms(job, ctx))
    row.update(checks)
    row["invented"] = checks["copies_not_verbatim"] + checks["guard_hits"]
    if claims and judge:
        start = budget.made
        budget.begin("judge", key)
        try:
            verdict = judge_resume(binding, claims)
        except CallCapReached:
            verdict = {"judge_ok": False, "judge_error": "the call cap was reached before the judge could run", "verdicts": {}}
        unsupported = [{"line": claims[number - 1].text, **found} for number, found in verdict["verdicts"].items() if not found.get("supported", True)]
        row["judge"] = {
            **_usage(budget, start), "ok": verdict["judge_ok"], "error": verdict["judge_error"], "unsupported": unsupported,
            "weakened": [{"line": claims[number - 1].text, "lost_span": found.get("lost_span")} for number, found in verdict["verdicts"].items() if found.get("weakened")],
        }
        row["invented"] = row["invented"] + len(unsupported) if verdict["judge_ok"] else None
    elif claims:
        row["invented"] = None  # rewrites were shown and no judge read them
    row["wall_seconds"] = round(time.monotonic() - started, 1)
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{variant}-{profile['profile_id']}-{posting['id']}.md").write_text(render_markdown(result), encoding="utf-8")
    return row


# --- the tables -------------------------------------------------------------------------------------


def summarize(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One line per variant: the sums and means the comparison is read from."""

    out = []
    for variant in VARIANTS:
        mine = [row for row in rows if row["variant"] == variant]
        done = [row for row in mine if "pages" in row]
        if not mine:
            continue
        found = sum(int(str(row["key_skills"]).split("/")[0]) for row in done)
        asked = sum(int(str(row["key_skills"]).split("/")[1]) for row in done)
        judged = [row for row in done if row.get("invented") is not None]

        def mean(key: str) -> float | None:
            values = [row[key] for row in done if isinstance(row.get(key), (int, float))]
            return round(sum(values) / len(values), 1) if values else None

        out.append({
            "variant": variant, "cases": len(mine), "model_ok": sum(bool(row.get("ok")) for row in mine),
            "code_only": sum(row.get("picked_by") == "code" for row in mine),
            "two_pages_or_fewer": sum(row["pages"] <= 2 for row in done), "ats_mean": mean("ats"), "key_skills": f"{found}/{asked}",
            "invented": sum(row["invented"] for row in judged), "invented_unknown": len(done) - len(judged),
            "tailor_calls": sum(row["calls"] for row in mine), "judge_calls": sum((row.get("judge") or {}).get("calls", 0) for row in mine),
            "input_tokens_mean": mean("input_tokens"), "output_tokens_mean": mean("output_tokens"), "prompt_chars_mean": mean("prompt_chars"),
            "seconds_mean": mean("seconds"), "bullets_mean": mean("bullets"), "rewritten": sum(row["shown_rewritten"] for row in done),
            "labels_shown": f"{sum(row['labels_shown'] for row in done)}/{sum(row['labels'] for row in done)}",
            "label_jaccard_mean": round(sum(row["label_jaccard"] for row in done) / len(done), 2) if done else None,
        })
    return out


def print_summary(summary: Sequence[Mapping[str, Any]], *, out=None) -> None:
    out = out or sys.stdout
    print(
        f"{'variant':10} {'cases':>5} {'<=2p':>4} {'ATS':>5} {'key skills':>10} {'invented':>8} {'calls':>5} {'tok in':>7} {'tok out':>7} "
        f"{'prompt':>7} {'bullets':>7} {'labels':>8} {'jaccard':>7}",
        file=out,
    )
    for line in summary:
        unknown = f"+{line['invented_unknown']}?" if line["invented_unknown"] else ""
        print(
            f"{line['variant']:10} {line['cases']:>5} {line['two_pages_or_fewer']:>4} {line['ats_mean']!s:>5} {line['key_skills']:>10} "
            f"{str(line['invented']) + unknown:>8} {line['tailor_calls']:>5} {line['input_tokens_mean']!s:>7} {line['output_tokens_mean']!s:>7} "
            f"{line['prompt_chars_mean']!s:>7} {line['bullets_mean']!s:>7} {line['labels_shown']:>8} {line['label_jaccard_mean']!s:>7}",
            file=out,
        )


def print_rows(rows: Sequence[Mapping[str, Any]], *, out=None) -> None:
    out = out or sys.stdout
    for row in rows:
        if "pages" not in row:
            print(f"{row['variant']:10} {row['profile']:12} {row['posting']:36} INVALID ({row.get('not_assessed_reason')})", file=out)
            continue
        length = row.get("length") or {}
        cut = f" cut {len(length.get('cut_roles') or [])} role(s), {length.get('cut_bullets') or 0} bullet(s)" if length else ""
        print(
            f"{row['variant']:10} {row['profile']:12} {row['posting']:36} {row['pages']} pages, ATS {row['ats']}, key skills {row['key_skills']}, "
            f"{row['bullets']} bullets, {row['shown_rewritten']} rewritten, invented {row['invented']}, labels {row['labels_shown']}/{row['labels']}, "
            f"{row['calls']} call(s) {row['input_tokens']}/{row['output_tokens']} tok{cut}{' (code only)' if row.get('picked_by') == 'code' else ''}",
            file=out,
        )


# --- main ---------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="0.1.10.9 master P4: today's tailor against the master-based tailor (synthetic, 2 profiles x 3 postings)")
    parser.add_argument("--variant", action="append", default=None, choices=VARIANTS, help="run this variant (repeatable); default: today")
    parser.add_argument("--max-calls", type=int, default=13, help="hard cap on model calls, retries and the judge included (default 13: six cases, each judged, plus one)")
    parser.add_argument("--report", type=Path, default=None, help="where to write the JSON report (required unless --dry-run or --compare)")
    parser.add_argument("--out-dir", type=Path, default=None, help="write each result's markdown under this directory")
    parser.add_argument("--home", type=Path, default=None, help="GigAI home to read the config from; never written")
    parser.add_argument("--model-target", default=harness.DEFAULT_MODEL_TARGET, help=f"adapter kind to resolve (default {harness.DEFAULT_MODEL_TARGET})")
    parser.add_argument("--concurrency", type=int, default=1, help="calls in flight at once: only 1 is supported")
    parser.add_argument("--fake-model", action="store_true", help="offline: a temp home and the GIGAI_SCOUT_FIND_JOBS_TEST_MODEL seam")
    parser.add_argument("--no-judge", action="store_true", help="never call the judge (a result that shows a rewrite then has invented: unknown)")
    parser.add_argument("--limit", type=int, default=None, help="at most this many cases in all (a smoke run)")
    parser.add_argument("--dry-run", action="store_true", help="print the planned cases and exit without any model call")
    parser.add_argument("--dump-dir", type=Path, default=None, help="write every prompt and raw model output per call under this directory")
    parser.add_argument("--compare", nargs="+", type=Path, default=None, metavar="REPORT_JSON", help="no calls: print these reports' summaries side by side")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.compare:
        rows: list[Mapping[str, Any]] = []
        for path in args.compare:
            report = json.loads(path.read_text(encoding="utf-8"))
            run = report["run"]
            print(f"{path.name}: variants {','.join(run['variants'])} tailor.md {run['instructions_digest'][:19]} head {run['head']} calls {run['calls_made']}/{run['max_calls']} skipped {run['skipped'] or 'none'}")
            rows += report["rows"]
        print("")
        print_summary(summarize(rows))
        print("")
        print_rows(rows)
        return 0
    profiles, postings = load_profiles(), load_postings()
    cases = plan(args.variant or [VARIANT_TODAY], profiles, postings, args.limit)
    if args.dry_run:
        for index, (variant, profile, posting) in enumerate(cases, 1):
            print(f"{index:3} {variant:10} {profile['profile_id']:12} {posting['id']}")
        print(f"{len(cases)} planned cases; one tailor call each, one judge call for each result that shows a rewrite: --max-calls {2 * len(cases) + 1} lets every one run when no answer is retried")
        return 0
    if args.concurrency != 1:
        print("--concurrency: only 1 is supported (one call at a time)", file=sys.stderr)
        return 2
    if args.report is None:
        print("--report is required", file=sys.stderr)
        return 2
    if not args.fake_model and os.environ.get("GIGAI_ASSESS_EVAL_LIVE") != "1":
        print("refusing the live master tailor eval: set GIGAI_ASSESS_EVAL_LIVE=1 explicitly", file=sys.stderr)
        return 2

    from gigai.scout.posting_keywords import extract_keywords

    master = load_master()
    labels = load_labels()
    keywords = {posting["id"]: extract_keywords(posting["text"], title=posting["title"], skills=master.skills()) for posting in postings}
    now = datetime.now(UTC)
    seams = {"GIGAI_SCOUT_FIND_JOBS_TEST_MODEL": "1"} if args.fake_model else {}
    rows = []
    skipped: list[str] = []
    budget = CallBudget(max_calls=args.max_calls)
    report_run: dict[str, Any] = {}

    def save() -> None:
        from gigai.canonical import digest_imported_bytes
        from gigai.scout.tailored_resume import TAILOR_INSTRUCTIONS_DIGEST, _instruction_bytes

        report_run.update({
            "started_at": now.isoformat().replace("+00:00", "Z"),
            "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "head": harness._git_head(),
            "fake_model": args.fake_model,
            "model_target": model_target,
            "variants": list(dict.fromkeys(variant for variant, _profile, _posting in cases)),
            # The template as it is on disk now (re-read per render), not the digest taken at import.
            "instructions_digest": digest_imported_bytes(_instruction_bytes()),
            "instructions_digest_at_import": TAILOR_INSTRUCTIONS_DIGEST,
            "max_calls": args.max_calls,
            "calls_made": budget.made,
            "planned_cases": len(cases),
            "skipped": skipped,
            "today": TODAY.isoformat(),
            "labels": os.fspath(LABELS_PATH.relative_to(harness.REPO_ROOT)),
        })
        calls = [
            {"index": call.index, "role": call.role, "row": call.row_key, "attempt": call.attempt, "seconds": call.elapsed_seconds, "usage": call.usage,
             "error": call.error, "prompt_chars": len(call.prompt), "output_chars": len(call.output_text or "")}
            for call in budget.calls
        ]
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps({"schema": REPORT_SCHEMA, "run": report_run, "summary": summarize(rows), "rows": rows, "calls": calls}, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    with tempfile.TemporaryDirectory(prefix="gigai-master-tailor-eval-") as tmp, harness.seam_env(**seams):
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
            for variant, profile, posting in cases:
                name = f"{variant}/{profile['profile_id']}/{posting['id']}"
                # A case needs room for its tailor call and that call's one retry; its judge call is made only if one is left.
                if budget.made + 2 > args.max_calls:
                    skipped.append(name)
                    continue
                try:
                    row = run_case(
                        binding, variant, profile, posting, master=master, keywords=keywords[posting["id"]],
                        labels=labels.get((profile["profile_id"], posting["id"]), frozenset()), judge=not args.no_judge, out_dir=args.out_dir,
                    )
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
            stem = f"{call.index:03}-{call.role}-{'-'.join(call.row_key or ('none',)).replace('/', '-')}-attempt{call.attempt}"
            (args.dump_dir / f"{stem}.prompt.txt").write_text(call.prompt, encoding="utf-8")
            (args.dump_dir / f"{stem}.output.txt").write_text(call.output_text or f"<{call.error}>", encoding="utf-8")
    if not args.quiet:
        print_summary(summarize(rows))
        print(f"calls {budget.made}/{args.max_calls}; skipped {skipped or 'none'}; report: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
