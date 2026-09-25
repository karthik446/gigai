#!/usr/bin/env python3
"""P7 (v0.1.9): the assess eval harness -- labelled (resume, posting) pairs through the SHIPPED path.

Every model call goes through exactly the code the product uses for one
assessment (``quick_assess.run_quick_assessment`` and the graph's assess node):

    proposal_execution.resolve_model_adapter(config, <configured target>)   (C1: module attribute)
    assessment_core.assess_once(binding, AssessJob, AssessContext, parse=...)  (prompt, retry, normalize)
    proposals.validate_assessment_bounds + AssessmentBody.from_json         (the quick-assess parser)

with the packaged ``scout/data/instructions/assess.md`` (never a copy of the
prompt or of codex flags) and ``countries``/``titles`` set per resume exactly
as ``find-jobs.json`` and the profile would set them.

Modes
-----
- LIVE (``make eval-live``, gated on ``GIGAI_ASSESS_EVAL_LIVE=1``): reads the
  operator's config from ``--home`` (default ``$GIGAI_HOME`` or ``~/.gigai``)
  READ-ONLY, resolves ``--model-target`` (default ``codex_cli``) through the
  operator's own configured targets, and writes one JSON report under
  ``../orchestrator/research/evals/<date>.json``.  Never writes under the home.
- FAKE (``--fake-model``): a temp home with the ``ollama_local`` fixture target
  and the ``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL=1`` seam (``bindings._test_model_handler``),
  so the whole harness runs end to end offline.  ``--with-jev --fake-jev`` does
  the same for Jev through ``bindings._test_jev_handler``.

Metrics (``metrics`` in the report; see ``summarize``): verdict agreement,
clean-fit match rate (every failure listed with the model's questions),
question recall (normalized exact -- both sides through the product's
``normalize_question_id`` -- plus category and raw exact) on rows with expected ids, false
asks (clean-fit rows: every question is one by construction; other rows: a
token heuristic lists *possible* false asks for review), Jev as a pre-filter
(``--with-jev``: does every matched/pending-labelled posting land in the
resume's top-N), cross-profile discrimination (same posting, two resumes) and
reliability (valid-output rate, invalid-after-retry rate with its < 5% release
bar, retries, latency, tokens; model cost is ``unavailable`` because the
adapters report no cost, Jev cost is real).

Excluded rows (``excluded=true`` in ``labels.csv``; the operator's label
policy of 2026-09-25, rule E: Canadian/province rows are dropped from 0.1.9
scoring, US-only): ``load_labels`` validates them but leaves them out unless
``include_excluded=True``, ``plan_rows`` never plans one, and ``summarize``
ignores any stored row flagged ``excluded`` -- so they enter no metric.  The
report lists them under ``run.excluded_rows``.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
import csv
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import random
import re
import statistics
import sys
import tempfile
import time
from typing import TYPE_CHECKING, Any

from gigai.scout.question_ids import normalize_question_id

if TYPE_CHECKING:  # pragma: no cover - the product imports stay lazy so `--dry-run`/the offline test import nothing heavy
    from gigai.config import GigAIConfig
    from gigai.scout.find_jobs.contracts import PostingRow

EVAL_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = EVAL_DIR / "fixtures"
POSTINGS_PATH = FIXTURES_DIR / "postings.json"
RESUMES_DIR = FIXTURES_DIR / "resumes"
RESUMES_INDEX_PATH = RESUMES_DIR / "index.json"
LABELS_PATH = EVAL_DIR / "labels.csv"
REPO_ROOT = EVAL_DIR.parents[1]
DEFAULT_REPORT_DIR = REPO_ROOT.parent / "orchestrator" / "research" / "evals"

DEFAULT_MAX_CALLS = 20
DEFAULT_TOP_N = 10  # FindJobsConfig.default_assess_cap: what a real run would assess
DEFAULT_MODEL_TARGET = os.environ.get("GIGAI_ASSESS_EVAL_MODEL_TARGET", "codex_cli")
INVALID_AFTER_RETRY_BAR = 0.05

VERDICTS = ("matched_above_threshold", "pending_user_answers", "not_a_match")
CONSIDER_VERDICTS = frozenset({"matched_above_threshold", "pending_user_answers"})
LABEL_COLUMNS = (
    "resume_id",
    "posting_id",
    "expected_verdict",
    "expected_question_ids",
    "clean_fit",
    "uncertain",
    "excluded",
    "source",
    "notes",
)
# The shipped shape check (proposals._QUESTION_ID_RE) and the experience_qa
# schema pattern (C10) -- an expected id must satisfy both.
QUESTION_ID_RE = re.compile(r"\A[a-z0-9._-]+:[a-z0-9._-]+\Z")
SCHEMA_QUESTION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
COUNTRY_CODE_RE = re.compile(r"\A[A-Z]{2}\Z")

_TOKEN_STOPWORDS = frozenset({"and", "or", "the", "of", "for", "with", "in", "to", "a", "an", "at", "on", "vs"})


# --- fixtures ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Posting:
    posting_id: str
    title: str
    company: str
    location: str
    url: str
    full_text: str


@dataclass(frozen=True)
class Resume:
    resume_id: str
    text: str
    countries: tuple[str, ...]
    titles: tuple[str, ...]
    visa_sponsorship_required: bool
    kind: str  # "synthetic" | "clean_fit"
    clean_fit_posting_id: str | None
    source: str


@dataclass(frozen=True)
class Label:
    resume_id: str
    posting_id: str
    expected_verdict: str
    expected_question_ids: tuple[str, ...]
    clean_fit: bool
    uncertain: bool
    excluded: bool  # rule E: never planned, never scored (see the module docstring)
    source: str
    notes: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.resume_id, self.posting_id)


def load_postings(path: Path = POSTINGS_PATH) -> dict[str, Posting]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload["postings"] if isinstance(payload, dict) else payload
    postings: dict[str, Posting] = {}
    for item in items:
        posting = Posting(
            posting_id=item["posting_id"],
            title=item["title"],
            company=item["company"],
            location=item["location"],
            url=item["url"],
            full_text=item["full_text"],
        )
        if posting.posting_id in postings:
            raise ValueError(f"duplicate posting_id {posting.posting_id!r}")
        postings[posting.posting_id] = posting
    return postings


def load_resumes(index_path: Path = RESUMES_INDEX_PATH) -> dict[str, Resume]:
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    resumes: dict[str, Resume] = {}
    for item in payload["resumes"]:
        text = (index_path.parent / item["file"]).read_text(encoding="utf-8")
        resume = Resume(
            resume_id=item["resume_id"],
            text=text,
            countries=tuple(item["countries"]),
            titles=tuple(item["titles"]),
            visa_sponsorship_required=bool(item["visa_sponsorship_required"]),
            kind=item["kind"],
            clean_fit_posting_id=item.get("clean_fit_posting_id"),
            source=item.get("source", ""),
        )
        if resume.resume_id in resumes:
            raise ValueError(f"duplicate resume_id {resume.resume_id!r}")
        resumes[resume.resume_id] = resume
    return resumes


def _flag(value: str, column: str) -> bool:
    if value not in {"true", "false"}:
        raise ValueError(f"labels.csv {column} must be true|false, got {value!r}")
    return value == "true"


def load_labels(path: Path = LABELS_PATH, *, include_excluded: bool = False) -> tuple[Label, ...]:
    """The labelled rows the harness scores; ``include_excluded=True`` also returns rule-E rows."""

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != LABEL_COLUMNS:
            raise ValueError(f"labels.csv columns must be {LABEL_COLUMNS}, got {reader.fieldnames}")
        labels: list[Label] = []
        for row in reader:
            ids = tuple(item.strip() for item in row["expected_question_ids"].split(";") if item.strip())
            labels.append(
                Label(
                    resume_id=row["resume_id"].strip(),
                    posting_id=row["posting_id"].strip(),
                    expected_verdict=row["expected_verdict"].strip(),
                    expected_question_ids=ids,
                    clean_fit=_flag(row["clean_fit"].strip(), "clean_fit"),
                    uncertain=_flag(row["uncertain"].strip(), "uncertain"),
                    excluded=_flag(row["excluded"].strip(), "excluded"),
                    source=row["source"].strip(),
                    notes=row["notes"].strip(),
                )
            )
    if include_excluded:
        return tuple(labels)
    return tuple(label for label in labels if not label.excluded)


def plan_rows(
    labels: Sequence[Label],
    *,
    sample: int | None = None,
    seed: int = 0,
    clean_fit_only: bool = False,
    resume_ids: Iterable[str] = (),
    posting_ids: Iterable[str] = (),
) -> tuple[Label, ...]:
    """The rows a run will call, in priority order: clean fits, then confident, then uncertain.

    An ``excluded`` row is never planned, whatever the other filters say.
    ``--max-calls`` cuts this list from the end, so the rows whose outcome is
    unambiguous (a clean fit that does not match is a failure) are always
    called first.  ``sample`` draws ``sample`` rows with ``random.Random(seed)``
    before ordering, so a sampled run is reproducible.
    """

    wanted_resumes = set(resume_ids)
    wanted_postings = set(posting_ids)
    rows = [
        label
        for label in labels
        if not label.excluded
        and (not clean_fit_only or label.clean_fit)
        and (not wanted_resumes or label.resume_id in wanted_resumes)
        and (not wanted_postings or label.posting_id in wanted_postings)
    ]
    if sample is not None and sample < len(rows):
        rows = random.Random(seed).sample(rows, sample)
    priority = {label.key: index for index, label in enumerate(labels)}
    rows.sort(key=lambda label: (0 if label.clean_fit else 1 if not label.uncertain else 2, priority[label.key]))
    return tuple(rows)


# --- question helpers ------------------------------------------------------------------


def question_category(question_id: str) -> str:
    return question_id.split(":", 1)[0]


def question_tokens(question_id: str) -> tuple[str, ...]:
    """The value part of ``<category>:<value>`` split into words worth matching."""

    _, _, value = question_id.partition(":")
    tokens = [token for token in re.split(r"[^a-z0-9]+", value.lower()) if len(token) >= 3 and token not in _TOKEN_STOPWORDS]
    return tuple(dict.fromkeys(tokens))


def possible_false_ask(question_id: str, resume_text: str) -> bool:
    """A question whose every value token already appears in the resume text.

    A heuristic for the operator's review list, not a verdict: ``cloud:aws``
    against a resume that says "AWS" is flagged; ``years:backend_systems``
    against a resume that never uses both words is not.
    """

    tokens = question_tokens(question_id)
    if not tokens:
        return False
    haystack = re.sub(r"[^a-z0-9]+", " ", resume_text.lower())
    words = set(haystack.split())
    return all(token in words for token in tokens)


def question_hits(expected: Sequence[str], observed: Sequence[str]) -> tuple[set[str], set[str], set[str]]:
    """``(exact_hits, normalized_hits, category_hits)`` over the expected ids.

    assess-prompt-v3 (2026-09-25): vocabulary drift is a HARNESS concern, not a
    prompt one. Both sides go through the product's ``normalize_question_id``
    (the same canonicalization P3's prior-answer join applies) before the
    normalized-exact and category comparisons, so ``tool:x`` labelled against an
    observed ``tooling:x`` counts as exact -- that is the number that says an
    answer recorded on one posting is found on the next. The raw exact figure
    (byte-equal ids) stays for reference only. Every returned set holds the
    expected ids as labelled, so callers can report per label.
    """

    observed_set = set(observed)
    observed_normalized = {normalize_question_id(item) for item in observed}
    observed_categories = {question_category(item) for item in observed_normalized}
    exact = {item for item in expected if item in observed_set}
    normalized = {item for item in expected if normalize_question_id(item) in observed_normalized}
    by_category = {item for item in expected if question_category(normalize_question_id(item)) in observed_categories}
    return exact, normalized, by_category


# --- the model side ------------------------------------------------------------------


def _parse_body(raw: Mapping[str, Any]) -> object:
    """The quick-assess parser: shared bounds, then the run-free DTO (mirrors ``quick_assess._parse_body``)."""

    from gigai.scout import proposals
    from gigai.scout.find_jobs.assess_contracts import AssessmentBody

    proposals.validate_assessment_bounds(raw)
    return AssessmentBody.from_json(dict(raw))


def resolve_binding(config: "GigAIConfig", model_target: str, *, home_root: Path) -> object:
    """The product's own adapter resolution (C1/C11), never a copy of adapter flags.

    ``bindings._patch_test_model_transport`` is inert unless
    ``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL=1``; the configured target name comes
    from the operator's config through
    ``_resolve_configured_target_name_for_adapter`` and the adapter through
    ``proposal_execution.resolve_model_adapter`` looked up at call time.
    """

    from gigai.scout import proposal_execution
    from gigai.scout.find_jobs import bindings

    bindings._patch_test_model_transport(config)
    adapter_target = proposal_execution._resolve_configured_target_name_for_adapter(config, model_target)
    return proposal_execution.resolve_model_adapter(config, adapter_target, home_root=home_root)


def assess_row(binding: object, label: Label, posting: Posting, resume: Resume) -> dict[str, Any]:
    """One labelled pair through ``assess_once``; the row dict the report stores."""

    from gigai.scout.assessment_core import AssessContext, AssessJob, assess_once

    job = AssessJob(title=posting.title, company=posting.company, location=posting.location, posting_text=posting.full_text)
    ctx = AssessContext(
        resume_text=resume.text,
        visa_sponsorship_required=resume.visa_sponsorship_required,
        countries=resume.countries,
        titles=resume.titles,
    )
    started = time.monotonic()
    attempt = assess_once(binding, job, ctx, parse=_parse_body)
    elapsed = time.monotonic() - started
    row: dict[str, Any] = {
        "resume_id": label.resume_id,
        "posting_id": label.posting_id,
        "expected_verdict": label.expected_verdict,
        "expected_question_ids": list(label.expected_question_ids),
        "clean_fit": label.clean_fit,
        "uncertain": label.uncertain,
        "excluded": label.excluded,
        "ok": attempt.ok,
        "attempts": attempt.attempts,
        "retried": attempt.attempts >= 2,
        "validation_error": attempt.validation_error,
        "not_assessed_reason": attempt.not_assessed_reason.value if attempt.not_assessed_reason else None,
        "elapsed_seconds": round(elapsed, 3),
        "usage": None,
        "verdict": None,
        "questions": [],
        "matrix": [],
        "not_a_match_reason": None,
    }
    if attempt.usage is not None:
        row["usage"] = {
            "input_tokens": attempt.usage.input_tokens,
            "output_tokens": attempt.usage.output_tokens,
            "total_tokens": attempt.usage.total_tokens,
        }
    if attempt.ok:
        from gigai.scout.find_jobs.assess_contracts import AssessmentBody

        body = attempt.parsed
        assert isinstance(body, AssessmentBody)
        row["verdict"] = body.verdict.value if body.verdict is not None else None
        row["questions"] = [
            {"question_id": item.question_id, "question": item.question, "requirement": item.requirement}
            for item in body.structured_questions
        ]
        row["matrix"] = [
            {
                "requirement": item.requirement,
                "class": item.requirement_class.value if item.requirement_class is not None else None,
                "status": item.status.value,
                "resume_evidence": list(item.resume_evidence),
            }
            for item in body.matrix
        ]
        row["not_a_match_reason"] = body.not_a_match_reason
    return annotate_row(row, resume.text)


def annotate_row(row: dict[str, Any], resume_text: str) -> dict[str, Any]:
    """Add the per-row judgements every metric reads (also used on stored rows)."""

    observed = [item["question_id"] for item in row.get("questions", [])]  # type: ignore[index]
    expected = list(row.get("expected_question_ids", []))  # type: ignore[arg-type]
    exact, normalized, by_category = question_hits(expected, observed)
    row["agreement"] = (row["verdict"] == row["expected_verdict"]) if row.get("ok") else None
    row["question_ids"] = observed
    row["question_ids_normalized"] = [normalize_question_id(item) for item in observed]
    row["expected_hits_exact"] = sorted(exact)
    row["expected_hits_normalized"] = sorted(normalized)
    row["expected_hits_category"] = sorted(by_category)
    row["possible_false_asks"] = [item for item in observed if possible_false_ask(item, resume_text)]
    return row


# --- metrics ---------------------------------------------------------------------------


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _latency(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "p90": None, "max": None}
    ordered = sorted(values)
    p90_index = min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))
    return {
        "mean": round(statistics.fmean(ordered), 3),
        "median": round(statistics.median(ordered), 3),
        "p90": round(ordered[p90_index], 3),
        "max": round(ordered[-1], 3),
    }


def summarize(rows: Sequence[Mapping[str, Any]], *, planned: int, max_calls: int, jev: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Every metric the plan lists, from the stored rows alone (re-runnable on a report)."""

    rows = [row for row in rows if not row.get("excluded")]  # rule E: an excluded row enters no metric
    valid = [row for row in rows if row["ok"]]
    invalid_after_retry = [row for row in rows if not row["ok"] and row["not_assessed_reason"] == "model_output_invalid"]
    transport_failures = [row for row in rows if not row["ok"] and row["not_assessed_reason"] in {"model_unavailable", "model_denied"}]
    retried = [row for row in rows if row["retried"]]
    recovered = [row for row in valid if row["retried"]]

    confusion: dict[str, dict[str, int]] = {verdict: {} for verdict in VERDICTS}
    for row in valid:
        expected = str(row["expected_verdict"])
        observed = str(row["verdict"])
        confusion.setdefault(expected, {})
        confusion[expected][observed] = confusion[expected].get(observed, 0) + 1
    agree = [row for row in valid if row["agreement"]]
    confident = [row for row in valid if not row["uncertain"]]
    confident_agree = [row for row in confident if row["agreement"]]

    clean_rows = [row for row in rows if row["clean_fit"]]
    clean_valid = [row for row in clean_rows if row["ok"]]
    clean_matched = [row for row in clean_valid if row["verdict"] == "matched_above_threshold"]
    clean_failures = [
        {
            "resume_id": row["resume_id"],
            "posting_id": row["posting_id"],
            "verdict": row["verdict"] if row["ok"] else f"invalid:{row['not_assessed_reason']}",
            "questions": list(row["questions"]),
            "not_a_match_reason": row["not_a_match_reason"],
            "unmet_rows": [item for item in row["matrix"] if item["status"] == "unmet"],  # type: ignore[index]
        }
        for row in clean_rows
        if not (row["ok"] and row["verdict"] == "matched_above_threshold")
    ]

    recall_rows = [row for row in valid if row["expected_question_ids"]]
    expected_total = sum(len(row["expected_question_ids"]) for row in recall_rows)  # type: ignore[arg-type]
    exact_total = sum(len(row["expected_hits_exact"]) for row in recall_rows)  # type: ignore[arg-type]
    normalized_total = sum(len(row["expected_hits_normalized"]) for row in recall_rows)  # type: ignore[arg-type]
    category_total = sum(len(row["expected_hits_category"]) for row in recall_rows)  # type: ignore[arg-type]
    asked_when_expected = [row for row in recall_rows if row["question_ids"]]

    clean_fit_questions = sum(len(row["question_ids"]) for row in clean_valid)  # type: ignore[arg-type]
    possible = [
        {"resume_id": row["resume_id"], "posting_id": row["posting_id"], "question_id": item["question_id"], "question": item["question"]}
        for row in valid
        if not row["clean_fit"]
        for item in row["questions"]  # type: ignore[union-attr]
        if item["question_id"] in row["possible_false_asks"]  # type: ignore[operator]
    ]

    by_posting: dict[str, list[Mapping[str, Any]]] = {}
    for row in valid:
        by_posting.setdefault(str(row["posting_id"]), []).append(row)
    cross: dict[str, Any] = {}
    pairs = 0
    discriminated = 0
    for posting_id, group in sorted(by_posting.items()):
        if len(group) < 2:
            continue
        entries = [
            {"resume_id": row["resume_id"], "expected": row["expected_verdict"], "observed": row["verdict"], "clean_fit": row["clean_fit"]}
            for row in group
        ]
        posting_pairs = 0
        posting_discriminated = 0
        for index, left in enumerate(group):
            for right in group[index + 1 :]:
                if left["expected_verdict"] == right["expected_verdict"]:
                    continue
                posting_pairs += 1
                if left["verdict"] != right["verdict"]:
                    posting_discriminated += 1
        pairs += posting_pairs
        discriminated += posting_discriminated
        cross[posting_id] = {"rows": entries, "pairs": posting_pairs, "discriminated": posting_discriminated}

    usage_rows = [row["usage"] for row in rows if row.get("usage")]
    tokens = {
        "input": sum(int(item["input_tokens"] or 0) for item in usage_rows),  # type: ignore[index]
        "output": sum(int(item["output_tokens"] or 0) for item in usage_rows),  # type: ignore[index]
        "total": sum(int(item["total_tokens"] or 0) for item in usage_rows),  # type: ignore[index]
    }
    invalid_rate = _rate(len(invalid_after_retry), len(rows))
    return {
        "calls": {"planned": planned, "made": len(rows), "max_calls": max_calls, "stopped_at_cap": planned > len(rows)},
        "verdict_agreement": {
            "rows": len(valid),
            "agree": len(agree),
            "rate": _rate(len(agree), len(valid)),
            "confident_rows": len(confident),
            "confident_agree": len(confident_agree),
            "confident_rate": _rate(len(confident_agree), len(confident)),
            "confusion": confusion,
        },
        "clean_fit": {
            "rows": len(clean_rows),
            "valid": len(clean_valid),
            "matched": len(clean_matched),
            "rate": _rate(len(clean_matched), len(clean_rows)),
            "failures": clean_failures,
        },
        "question_recall": {
            "rows": len(recall_rows),
            "rows_that_asked": len(asked_when_expected),
            "expected_ids": expected_total,
            "hit_exact": exact_total,
            "hit_normalized": normalized_total,
            "hit_category": category_total,
            "recall_exact": _rate(exact_total, expected_total),
            "recall_normalized": _rate(normalized_total, expected_total),  # headline: answer reuse across postings works
            "recall_category": _rate(category_total, expected_total),
            "per_row": [
                {
                    "resume_id": row["resume_id"],
                    "posting_id": row["posting_id"],
                    "uncertain": row["uncertain"],
                    "expected": list(row["expected_question_ids"]),  # type: ignore[arg-type]
                    "observed": list(row["question_ids"]),  # type: ignore[arg-type]
                    "observed_normalized": list(row["question_ids_normalized"]),  # type: ignore[arg-type]
                    "hit_exact": list(row["expected_hits_exact"]),  # type: ignore[arg-type]
                    "hit_normalized": list(row["expected_hits_normalized"]),  # type: ignore[arg-type]
                    "hit_category": list(row["expected_hits_category"]),  # type: ignore[arg-type]
                }
                for row in recall_rows
            ],
        },
        "false_asks": {
            "clean_fit_questions": clean_fit_questions,
            "clean_fit_rows_asked": sum(1 for row in clean_valid if row["question_ids"]),
            "bar_zero_met": clean_fit_questions == 0,
            "possible": possible,
        },
        "cross_profile": {"pairs": pairs, "discriminated": discriminated, "rate": _rate(discriminated, pairs), "postings": cross},
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
            "latency_seconds": _latency([float(row["elapsed_seconds"]) for row in rows]),
            "tokens": tokens,
            "model_cost_usd": "unavailable",
            "jev_cost_usd": jev["total_cost_usd"] if jev else None,
        },
        "jev": jev,
    }


# --- Jev pre-filter --------------------------------------------------------------------


def _jev_http_client() -> object:
    import httpx

    from gigai.scout.find_jobs import bindings

    if bindings._test_jev_enabled():
        return httpx.Client(transport=httpx.MockTransport(bindings._test_jev_handler), timeout=30.0)
    return httpx.Client(timeout=30.0)


def _posting_row(posting: Posting) -> "PostingRow":
    from gigai.canonical import digest_imported_bytes
    from gigai.scout.find_jobs.contracts import ATSProvider, PostingRow, SourceKind

    return PostingRow(
        url=posting.url,
        normalized_url=posting.url,
        provider=ATSProvider.GREENHOUSE,
        board_token=posting.company,
        company=posting.company,
        title=posting.title,
        location=posting.location,
        published_at=None,
        content_sha256=digest_imported_bytes(posting.full_text.encode("utf-8")),
        source_kind=SourceKind.ATS,
        query_key="assess-eval",
        text=posting.full_text,
    )


def rank_with_jev(
    labels: Sequence[Label],
    resumes: Mapping[str, Resume],
    postings: Mapping[str, Posting],
    *,
    api_key: str,
    cache_home: Path,
    top_n: int,
    cost_cap_usd: float,
) -> dict[str, Any]:
    """P6's ``rank_postings`` per resume over every fixture posting; the pre-filter check.

    A posting labelled matched/pending for a resume must land in that resume's
    top-N (``FindJobsConfig.default_assess_cap`` = 10 by default); a not_a_match
    posting may land anywhere.  The cache lives under ``cache_home`` with a
    fixed eval project id (the eval has no bound project), so a rerun is free.
    """

    from gigai.scout.find_jobs import jev_rank
    from gigai.scout.find_jobs.jev_client import JevClient
    from gigai.scout.find_jobs.jev_rank import RankPreferences, order_by_rank, rank_postings

    rows = tuple(_posting_row(posting) for posting in postings.values())
    by_url = {row.normalized_url: posting_id for row, posting_id in zip(rows, postings)}
    original_project_id = jev_rank.project_id
    jev_rank.project_id = lambda home_root, target: "assess-eval"  # type: ignore[assignment]
    client = _jev_http_client()
    total_cost = 0.0
    per_resume: dict[str, Any] = {}
    kept = 0
    checked = 0
    try:
        jev = JevClient(api_key, client)  # type: ignore[arg-type]
        for resume_id in sorted({label.resume_id for label in labels}):
            resume = resumes[resume_id]
            prefs = RankPreferences(target_titles=resume.titles, countries=resume.countries, visa_sponsorship_required=resume.visa_sponsorship_required)
            scores, cost, capped = rank_postings(
                rows,
                client=jev,
                resume_text=resume.text,
                prefs=prefs,
                profile_id=f"eval:{resume_id}",
                resume_revision_id=None,
                home_root=cache_home,
                target=cache_home,
                cost_cap_usd=cost_cap_usd,
            )
            total_cost += cost
            ordered = order_by_rank(rows, scores)
            ranking = [by_url[row.normalized_url] for row in ordered]
            score_by_url = {score.normalized_url: score for score in scores}
            ranked = [
                {
                    "rank": index + 1,
                    "posting_id": by_url[row.normalized_url],
                    "fit": score_by_url[row.normalized_url].fit,
                    "score": score_by_url[row.normalized_url].score,
                    "hidden_by_default": score_by_url[row.normalized_url].hidden_by_default,
                    "cached": score_by_url[row.normalized_url].cached,
                }
                for index, row in enumerate(ordered)
            ]
            checks = []
            for label in labels:
                if label.resume_id != resume_id:
                    continue
                position = ranking.index(label.posting_id) + 1
                must_keep = label.expected_verdict in CONSIDER_VERDICTS
                in_top = position <= top_n
                if must_keep:
                    checked += 1
                    kept += int(in_top)
                checks.append(
                    {
                        "posting_id": label.posting_id,
                        "expected_verdict": label.expected_verdict,
                        "must_keep": must_keep,
                        "rank": position,
                        "in_top_n": in_top,
                        "kept": in_top if must_keep else None,
                    }
                )
            per_resume[resume_id] = {"cost_usd": round(cost, 6), "capped": capped, "ranking": ranked, "labels": checks}
    finally:
        jev_rank.project_id = original_project_id  # type: ignore[assignment]
        close = getattr(client, "close", None)
        if callable(close):
            close()
    return {
        "top_n": top_n,
        "postings_ranked": len(rows),
        "must_keep": checked,
        "kept": kept,
        "prefilter_rate": _rate(kept, checked),
        "total_cost_usd": round(total_cost, 6),
        "per_resume": per_resume,
    }


# --- config -------------------------------------------------------------------------------


@contextmanager
def seam_env(**values: str):
    """Set the ``bindings.py`` test seams for the duration of a fake run, then restore them."""

    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, old in previous.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old


def build_fake_config(home: Path) -> "GigAIConfig":
    """The M1/api-e2e fixture config: one ``ollama_local`` target the model seam intercepts."""

    from gigai.config import Endpoint, ModelTarget, Profile
    from gigai.scout.find_jobs.bindings import TEST_MODEL_DIGEST, TEST_MODEL_NAME
    from gigai.setup import build_config, run_setup

    config = build_config(
        home_root=home,
        workpad_root=home.parent / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(Endpoint("local-test", "ollama_local", base_url="http://127.0.0.1:11434"),),
        model_targets=(
            ModelTarget(
                name="ollama_local",
                endpoint="local-test",
                model=TEST_MODEL_NAME,
                capabilities=("text",),
                max_output_tokens=512,
                reasoning_effort=None,
                model_digest=TEST_MODEL_DIGEST,
                context_tokens=2048,
                max_response_bytes=65536,
            ),
        ),
        profiles=(Profile("default", "ollama_local", "ollama_local", "ollama_local"),),
    )
    run_setup(config)
    return config


def _default_home() -> Path:
    raw = os.environ.get("GIGAI_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".gigai"


def _git_head() -> str | None:
    head = REPO_ROOT / ".git"
    try:
        if head.is_file():  # a worktree: .git is a pointer file
            gitdir = Path(head.read_text(encoding="utf-8").split(":", 1)[1].strip())
        else:
            gitdir = head
        ref = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if ref.startswith("ref: "):
            ref_path = ref[5:]
            for base in (gitdir, gitdir.parent.parent if gitdir.name != ".git" else gitdir):
                candidate = base / ref_path
                if candidate.is_file():
                    return candidate.read_text(encoding="utf-8").strip()
            return None
        return ref
    except OSError:
        return None


def default_report_path(*, fake: bool, now: datetime) -> Path:
    date = now.strftime("%Y-%m-%d")
    if fake:
        return Path(tempfile.gettempdir()) / f"gigai-assess-eval-fake-{date}-{now.strftime('%H%M%S')}.json"
    path = DEFAULT_REPORT_DIR / f"{date}.json"
    if path.exists():
        path = DEFAULT_REPORT_DIR / f"{date}-{now.strftime('%H%M%S')}.json"
    return path


# --- CLI ------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS, help=f"stop after this many model calls (default {DEFAULT_MAX_CALLS})")
    parser.add_argument("--sample", type=int, default=None, help="draw this many labelled rows at random (with --seed) before ordering")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--clean-fit-only", action="store_true", help="only the clean-fit rows")
    parser.add_argument("--resume", action="append", default=[], metavar="RESUME_ID", help="only rows for this resume (repeatable)")
    parser.add_argument("--posting", action="append", default=[], metavar="POSTING_ID", help="only rows for this posting (repeatable)")
    parser.add_argument("--with-jev", action="store_true", help="also rank every fixture posting per resume with Jev (P6 jev_rank) and check the top-N pre-filter")
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N, help=f"pre-filter depth for --with-jev (default {DEFAULT_TOP_N}, the default assess cap)")
    parser.add_argument("--jev-cost-cap-usd", type=float, default=1.0, help="per-resume Jev spend cap for --with-jev (default 1.0)")
    parser.add_argument("--jev-cache-home", type=Path, default=None, help="where the Jev rank cache lives (default <report dir>/jev_cache_home; a temp dir when fake)")
    parser.add_argument("--report", type=Path, default=None, help="report path (default ../orchestrator/research/evals/<date>.json)")
    parser.add_argument("--home", type=Path, default=None, help="GigAI home to read the config from (default $GIGAI_HOME or ~/.gigai); never written")
    parser.add_argument("--model-target", default=DEFAULT_MODEL_TARGET, help=f"sealed adapter kind to resolve through the operator's config (default {DEFAULT_MODEL_TARGET})")
    parser.add_argument("--fake-model", action="store_true", help="offline: a temp home with the ollama_local fixture target and the GIGAI_SCOUT_FIND_JOBS_TEST_MODEL seam")
    parser.add_argument("--fake-jev", action="store_true", help="offline: the GIGAI_SCOUT_FIND_JOBS_TEST_JEV seam for --with-jev")
    parser.add_argument("--dry-run", action="store_true", help="print the planned rows and exit without any model or Jev call")
    parser.add_argument("--rescore", type=Path, default=None, metavar="REPORT_JSON", help="no calls: re-annotate the stored rows of this report with the current scoring code and write the re-scored report to --report (the stored jev section is kept)")
    parser.add_argument("--quiet", action="store_true")
    return parser


def _print_summary(metrics: Mapping[str, Any], report_path: Path, *, out=sys.stdout) -> None:
    calls = metrics["calls"]
    agreement = metrics["verdict_agreement"]
    clean = metrics["clean_fit"]
    recall = metrics["question_recall"]
    false_asks = metrics["false_asks"]
    cross = metrics["cross_profile"]
    reliability = metrics["reliability"]
    print("=== assess eval summary ===", file=out)
    print(f"calls: {calls['made']}/{calls['planned']} planned (cap {calls['max_calls']})", file=out)
    print(f"verdict agreement: {agreement['agree']}/{agreement['rows']} valid rows ({agreement['rate']}); confident-only {agreement['confident_agree']}/{agreement['confident_rows']} ({agreement['confident_rate']})", file=out)
    print(f"clean-fit match rate: {clean['matched']}/{clean['rows']} ({clean['rate']})", file=out)
    for failure in clean["failures"]:  # type: ignore[union-attr]
        asked = "; ".join(f"{item['question_id']}: {item['question']}" for item in failure["questions"]) or "-"
        print(f"  FAIL {failure['resume_id']} x {failure['posting_id']}: {failure['verdict']} | questions: {asked} | reason: {failure['not_a_match_reason']}", file=out)
    print(f"question recall: normalized exact {recall['hit_normalized']}/{recall['expected_ids']} ({recall['recall_normalized']}), category {recall['hit_category']}/{recall['expected_ids']} ({recall['recall_category']}), raw exact {recall['hit_exact']}/{recall['expected_ids']} ({recall['recall_exact']}) over {recall['rows']} rows", file=out)
    print(f"false asks on clean fits: {false_asks['clean_fit_questions']} (bar 0 met: {false_asks['bar_zero_met']}); possible false asks to review: {len(false_asks['possible'])}", file=out)
    print(f"cross-profile: {cross['discriminated']}/{cross['pairs']} differently-labelled pairs discriminated ({cross['rate']})", file=out)
    print(
        f"reliability: valid {reliability['valid']}/{calls['made']} ({reliability['valid_output_rate']}); invalid after retry {reliability['invalid_after_retry']} ({reliability['invalid_after_retry_rate']}, bar < {reliability['invalid_after_retry_bar']} met: {reliability['invalid_after_retry_bar_met']}); retries {reliability['retries']} (recovered {reliability['recovered_on_retry']}); transport failures {reliability['transport_failures']}",
        file=out,
    )
    print(f"latency s: {reliability['latency_seconds']}; tokens: {reliability['tokens']}; model cost: {reliability['model_cost_usd']}; jev cost usd: {reliability['jev_cost_usd']}", file=out)
    jev = metrics.get("jev")
    if jev:
        print(f"jev pre-filter: kept {jev['kept']}/{jev['must_keep']} matched/pending postings in the top-{jev['top_n']} ({jev['prefilter_rate']})", file=out)
    print(f"report: {report_path}", file=out)


def rescore_report(source: Mapping[str, Any], resumes: Mapping[str, Resume], *, now: datetime) -> dict[str, Any]:
    """The same report with every row re-annotated and the metrics recomputed by the current code.

    Rows keep their stored verdicts, questions, matrix, usage and their stored
    expected labels (what the model was scored against at the time); only the
    derived judgements (hits, false-ask flags) and ``metrics`` change, so two
    runs of different prompts compare like for like under one scoring rule.
    """

    rows = [annotate_row(dict(row), resumes[row["resume_id"]].text) for row in source["rows"]]
    run = dict(source["run"])
    metrics = summarize(rows, planned=int(source["metrics"]["calls"]["planned"]), max_calls=int(run["max_calls"]), jev=source["metrics"].get("jev"))
    run["rescored_from"] = {"head": run.get("head"), "finished_at": run.get("finished_at"), "instructions_digest": run.get("instructions_digest")}
    run["rescored_at"] = now.isoformat().replace("+00:00", "Z")
    run["rescored_head"] = _git_head()
    return {"schema": source["schema"], "run": run, "rows": rows, "metrics": metrics}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = datetime.now(UTC)
    postings = load_postings()
    resumes = load_resumes()
    if args.rescore is not None:
        source = json.loads(args.rescore.read_text(encoding="utf-8"))
        report = rescore_report(source, resumes, now=now)
        report_path = args.report or args.rescore.with_name(args.rescore.stem + "-rescored.json")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        if not args.quiet:
            _print_summary(report["metrics"], report_path)
        return 0
    labels = load_labels()
    excluded = [label for label in load_labels(include_excluded=True) if label.excluded]
    rows_to_call = plan_rows(labels, sample=args.sample, seed=args.seed, clean_fit_only=args.clean_fit_only, resume_ids=args.resume, posting_ids=args.posting)
    if args.dry_run:
        for index, label in enumerate(rows_to_call, 1):
            marker = "clean" if label.clean_fit else "uncertain" if label.uncertain else "confident"
            called = "call" if index <= args.max_calls else "skip (cap)"
            print(f"{index:3} {called:10} {marker:9} {label.resume_id} x {label.posting_id} expects {label.expected_verdict} {';'.join(label.expected_question_ids)}")
        for label in excluded:
            print(f"    excluded  {label.resume_id} x {label.posting_id} (rule E: not planned, not scored)", file=sys.stderr)
        return 0

    report_path = args.report or default_report_path(fake=args.fake_model, now=now)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    seams: dict[str, str] = {}
    if args.fake_model:
        seams["GIGAI_SCOUT_FIND_JOBS_TEST_MODEL"] = "1"
    if args.fake_jev:
        seams["GIGAI_SCOUT_FIND_JOBS_TEST_JEV"] = "1"

    with tempfile.TemporaryDirectory(prefix="gigai-assess-eval-") as tmp, seam_env(**seams):
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
                    print(f"[{index}/{min(len(rows_to_call), args.max_calls)}] {label.resume_id} x {label.posting_id} (expects {label.expected_verdict})", file=sys.stderr)
                row = assess_row(binding, label, postings[label.posting_id], resumes[label.resume_id])
                results.append(row)
                if not args.quiet:
                    outcome = row["verdict"] if row["ok"] else f"INVALID {row['not_assessed_reason']}: {row['validation_error']}"
                    print(f"    -> {outcome} in {row['elapsed_seconds']}s, attempts {row['attempts']}, questions {row['question_ids']}", file=sys.stderr)
        finally:
            close = getattr(binding, "close", None)
            if callable(close):
                close()

        jev_section = None
        if args.with_jev:
            from gigai.scout.find_jobs import jev_client

            if args.fake_jev:
                api_key = "fake-key"
                cache_home = tmp_root / "jev_home"
            else:
                api_key = jev_client.require_api_key(home_root=home_root)
                cache_home = args.jev_cache_home or (report_path.parent / "jev_cache_home")
            called = [label for label in rows_to_call[: args.max_calls]]
            jev_section = rank_with_jev(called, resumes, postings, api_key=api_key, cache_home=cache_home, top_n=args.top_n, cost_cap_usd=args.jev_cost_cap_usd)

    metrics = summarize(results, planned=len(rows_to_call), max_calls=args.max_calls, jev=jev_section)
    from gigai.scout.assessment_core import INSTRUCTIONS_DIGEST

    report = {
        "schema": "gigai-assess-eval-report:1",
        "run": {
            "date": now.strftime("%Y-%m-%d"),
            "started_at": now.isoformat().replace("+00:00", "Z"),
            "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "head": _git_head(),
            "fake_model": args.fake_model,
            "fake_jev": args.fake_jev,
            "model_target": model_target,
            "adapter_target": getattr(getattr(getattr(binding, "current", None), "target", None), "name", None),
            "adapter_port": getattr(getattr(binding, "port", None), "name", None),
            "instructions_digest": INSTRUCTIONS_DIGEST,
            "max_calls": args.max_calls,
            "sample": args.sample,
            "seed": args.seed,
            "filters": {"clean_fit_only": args.clean_fit_only, "resume": args.resume, "posting": args.posting},
            "labels": os.fspath(LABELS_PATH.relative_to(REPO_ROOT)),
            "postings": os.fspath(POSTINGS_PATH.relative_to(REPO_ROOT)),
            "skipped_rows": skipped,
            "excluded_rows": [{"resume_id": label.resume_id, "posting_id": label.posting_id} for label in excluded],
        },
        "rows": results,
        "metrics": metrics,
    }
    report_path.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if not args.quiet:
        _print_summary(metrics, report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
