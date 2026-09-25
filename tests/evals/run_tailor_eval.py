#!/usr/bin/env python3
"""Q3 (v0.1.9): the tailored-resume fabrication eval -- labelled pairs through the SHIPPED path.

Every model call goes through exactly the code the product uses for one
tailoring (``tailored_resume.run_tailored_resume``):

    proposal_execution.resolve_model_adapter(config, <configured target>)   (C1: module attribute)
    tailored_resume.tailor_once(binding, TailorJob, TailorContext)          (prompt, retry, validation)

with the packaged ``scout/data/instructions/tailor.md`` and the P7 fixtures
(``tests/evals/fixtures``: 15 postings, 11 resumes, ``labels.csv``'s 26
pairs; ``--clean-fit-only`` -> the clean fits) plus ``fixtures/answers.json``
(per resume, a fixed answer for every expected question id the labels list,
so ``A <question_id>`` refs are exercised).

Detection, per ACCEPTED line (the product already rejected the rest):
1. the numeric guard and the posting-term guard RE-RUN here, outside the
   product's validator, on the line and its cited source texts -- a hit is a
   validator regression (the product must have rejected it) and counts;
2. ONE BATCHED JUDGE CALL PER TAILORED RESUME on the same binding with the
   eval-only prompt ``tests/evals/fabrication_judge.md``: every accepted
   rewritten line is a numbered CLAIM with its cited R/A source texts
   verbatim, and the judge answers ``{"verdicts": [{"line", "supported",
   "unsupported_span", "severity"}, ...]}``.  The harness demands exactly one
   verdict per claim (a missing, repeated, extra or malformed verdict is a
   judge failure for that resume; ``invoke_json_once`` feeds the error back
   and retries once, then the resume counts under ``judge_failures``).

Severity (tailor-r3).  An unsupported verdict names its severity, by the
definition the judge prompt carries verbatim: HARD = a span that adds a fact
absent from the cited sources, or attributes to the candidate an action or
outcome the source attributes to something else or does not state;
PRECISION = modality/qualifier drift on a fact the source does state
(``auditable`` -> ``audited``).  A missing or invalid severity on an
unsupported verdict is a judge failure (retried once through the shared
loop, then reported).  Per accepted line, ``severity`` is ``"hard"`` when a
guard hit it, a copy line is not verbatim, or the judge said hard;
``"precision"`` when the judge said precision; ``None`` when clean.
``fabricated = guard hit OR not supported`` (hard + precision) is kept for
the run-to-run tables.  Copy lines never reach the judge: code checks them
verbatim against the resume line they name.  The report lists EVERY flagged
line (hard and precision) with its cited source text and the span the judge
flagged, so the operator can read each one -- self-judging is acceptable for
0.1.9 only because this human check exists (orchestrator review, §8 answer
5); ``metrics.samples`` renders two tailored resumes (one clean fit, one
other) as markdown with every line's sources for that read.

Bars (``metrics.bars``): ``hard_fabrications == 0`` (``HARD_FABRICATIONS_BAR``;
an unjudged rewritten line also fails it when the judge is enabled, and no
valid resume never passes it vacuously); ``precision_rate`` = judge-precision
lines / all accepted lines ``< 0.02`` (``PRECISION_RATE_BAR``);
invalid-after-retry < 5% (``INVALID_AFTER_RETRY_BAR``, shared with the assess
eval).

Row selection.  Default: ``plan_rows`` exactly as ``run_assess_eval`` (rule
E: an ``excluded`` label is never planned and listed under
``run.excluded_rows``).  Explicit: ``--row "<resume_id> x <posting_id>"``
(repeatable) or ``--rows-file`` select labelled pairs REGARDLESS of the
``excluded`` flag, in the order given -- the exclusion is the assess eval's
scoring rule for Remote Canada/Poland locations (LABELLING.md rule E), and
whether a tailored resume fabricates does not depend on the posting's
location; the report says which selected rows carry the flag
(``run.row_selection.excluded_rows_included``).

Call cap.  ``--max-calls`` (default ``DEFAULT_MAX_CALLS``) is a HARD CAP on
model calls in total -- tailor attempts including retries plus judge
attempts including retries -- enforced by ``CappedBinding`` BEFORE each
call; when one more call would exceed it the run stops and the report lists
the rows not done (``run.rows_not_done``, with the stage that was cut).

Modes: LIVE (``GIGAI_ASSESS_EVAL_LIVE=1``, the operator's home read-only,
report under ``../orchestrator/research/evals/tailor-<date>.json``) and FAKE
(``--fake-model``: temp home, the ``bindings._test_model_handler`` seam --
the fixture answers a tailor prompt with lines built from the prompt's own
``R1``/``A cloud:gcp`` sources and judges every claim supported).
``--dump-dir`` writes every prompt and raw model output per attempt.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys
import tempfile
import time
from typing import Any

if __package__ in (None, ""):  # run as a script (``python tests/evals/run_tailor_eval.py``): make ``tests.evals`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.evals.run_assess_eval import (  # noqa: E402
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
DEFAULT_MAX_CALLS = 25  # tailor + judge attempts, retries included (operator-approved cap for the live run)
REPORT_SCHEMA = "gigai-tailor-eval-report:3"
HARD_FABRICATIONS_BAR = 0  # guard hits + copy lines not verbatim + judge "hard"
PRECISION_RATE_BAR = 0.02  # judge "precision" lines / all accepted lines, strictly below
SEVERITIES = ("hard", "precision")
GUARDS = ("numeric", "posting_term", "provenance", "copy_line_shape")
ROLE_TAILOR = "tailor"
ROLE_JUDGE = "judge"

_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
_REFS_COMMENT = re.compile(r" <!-- (.*?) -->$")


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


# --- row selection ---------------------------------------------------------------------------


class RowSelectionError(ValueError):
    """An explicit row that is not a labelled pair, or a malformed row spec."""


def parse_row_spec(spec: str) -> tuple[str, str]:
    """``"<resume_id> x <posting_id>"`` (also ``<resume_id>,<posting_id>``) -> the pair."""

    text = spec.strip()
    if " x " in text:
        left, right = text.split(" x ", 1)
    elif "," in text:
        left, right = text.split(",", 1)
    else:
        raise RowSelectionError(f"row spec {spec!r} is not '<resume_id> x <posting_id>'")
    resume_id, posting_id = left.strip(), right.strip()
    if not resume_id or not posting_id:
        raise RowSelectionError(f"row spec {spec!r} is not '<resume_id> x <posting_id>'")
    return resume_id, posting_id


def read_rows_file(path: Path) -> list[tuple[str, str]]:
    """One row spec per line; blank lines and ``#`` comments ignored."""

    specs: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            specs.append(parse_row_spec(line))
    return specs


def select_rows(labels: Sequence[Label], pairs: Sequence[tuple[str, str]]) -> tuple[Label, ...]:
    """The labelled rows for ``pairs``, in the order given, whatever their ``excluded`` flag.

    ``labels`` must be the full sheet (``load_labels(include_excluded=True)``).
    An unknown pair or a repeated one is an error: the run must never
    silently do fewer rows than the operator approved.
    """

    by_key = {label.key: label for label in labels}
    selected: list[Label] = []
    seen: set[tuple[str, str]] = set()
    for pair in pairs:
        if pair not in by_key:
            raise RowSelectionError(f"{pair[0]} x {pair[1]} is not a labelled pair in labels.csv")
        if pair in seen:
            raise RowSelectionError(f"{pair[0]} x {pair[1]} is listed twice")
        seen.add(pair)
        selected.append(by_key[pair])
    return tuple(selected)


# --- the call cap and the call log -------------------------------------------------------------


class CallCapReached(Exception):
    """Raised BEFORE a model call that would exceed ``--max-calls``.

    Carries no ``code`` attribute on purpose: ``invoke_json_once`` maps only
    the model-boundary errors it can name and re-raises everything else, so
    this reaches the harness loop untouched and stops the run.
    """

    def __init__(self, max_calls: int, role: str) -> None:
        super().__init__(f"call cap {max_calls} reached before a {role} call")
        self.max_calls = max_calls
        self.role = role


@dataclass
class CallRecord:
    index: int  # 1-based, across the run
    role: str  # ROLE_TAILOR | ROLE_JUDGE
    row_key: tuple[str, str] | None
    attempt: int  # 1-based within the row's role
    prompt: str
    output_text: str | None
    elapsed_seconds: float
    usage: dict[str, int | None] | None
    error: str | None  # the transport error type when the call raised


@dataclass
class CallBudget:
    max_calls: int
    calls: list[CallRecord] = field(default_factory=list)
    current_role: str = ROLE_TAILOR
    current_row: tuple[str, str] | None = None
    _attempts_in_step: int = 0

    @property
    def made(self) -> int:
        return len(self.calls)

    def begin(self, role: str, row_key: tuple[str, str] | None) -> None:
        self.current_role = role
        self.current_row = row_key
        self._attempts_in_step = 0

    def record(self, **fields: Any) -> CallRecord:
        self._attempts_in_step += 1
        record = CallRecord(index=self.made + 1, role=self.current_role, row_key=self.current_row, attempt=self._attempts_in_step, **fields)
        self.calls.append(record)
        return record

    def calls_for(self, role: str, row_key: tuple[str, str]) -> list[CallRecord]:
        return [call for call in self.calls if call.role == role and call.row_key == row_key]


class _CappedPort:
    def __init__(self, inner: object, budget: CallBudget) -> None:
        self._inner = inner
        self._budget = budget

    def __getattr__(self, name: str) -> Any:  # ``name``, ``close``, whatever the report reads
        return getattr(self._inner, name)

    def invoke(self, request: Any) -> Any:
        budget = self._budget
        if budget.made >= budget.max_calls:
            raise CallCapReached(budget.max_calls, budget.current_role)
        started = time.monotonic()
        try:
            result = self._inner.invoke(request)  # type: ignore[attr-defined]
        except BaseException as exc:
            budget.record(prompt=request.prompt, output_text=None, elapsed_seconds=round(time.monotonic() - started, 3), usage=None, error=type(exc).__name__)
            raise
        usage = result.normalized_usage
        budget.record(
            prompt=request.prompt,
            output_text=result.output_text,
            elapsed_seconds=round(time.monotonic() - started, 3),
            usage={"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "total_tokens": usage.total_tokens},
            error=None,
        )
        return result


class CappedBinding:
    """The product's binding, with the hard call cap and the per-attempt log in front of ``port.invoke``.

    ``request`` is the binding's own (the role/prompt/capabilities policy is
    untouched); only the port is wrapped, so ``invoke_json_once`` still runs
    exactly the shipped loop.
    """

    def __init__(self, inner: Any, budget: CallBudget) -> None:
        self._inner = inner
        self.budget = budget
        self.port = _CappedPort(inner.port, budget)

    def request(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.request(*args, **kwargs)

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()

    @property
    def current(self) -> Any:
        return getattr(self._inner, "current", None)


# --- the batched judge -------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgeClaim:
    number: int  # 1-based within the resume
    text: str
    sources: tuple[tuple[str, str], ...]  # (label, text) verbatim


def load_judge_template(path: Path = JUDGE_PROMPT_PATH) -> str:
    text = path.read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


def render_claims(claims: Sequence[JudgeClaim]) -> str:
    blocks: list[str] = []
    for claim in claims:
        lines = [f"CLAIM {claim.number}:", claim.text, f"SOURCES FOR CLAIM {claim.number}:"]
        lines.extend(f"{label}: {text}" for label, text in claim.sources)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_judge_prompt(claims: Sequence[JudgeClaim], template: str | None = None, validation_error: str | None = None) -> str:
    """The batched judge prompt: every accepted rewritten line as a numbered CLAIM with its sources verbatim."""

    if not claims:
        raise ValueError("the judge needs at least one claim")
    values = {
        "count": str(len(claims)),
        "claims": render_claims(claims),
        "validation_error": validation_error or "",
    }
    body = load_judge_template() if template is None else template

    def fill(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ValueError(f"fabrication_judge.md uses an unknown placeholder {{{{{key}}}}}")
        return values[key]

    blocks = body.split("\n\n")
    rendered: list[str] = []
    for block in blocks:
        if "{{validation_error}}" in block and not validation_error:
            continue  # the retry paragraph is dropped on the first attempt, like tailor.md's
        rendered.append(_PLACEHOLDER.sub(fill, block))
    return "\n\n".join(rendered)


def parse_judge_answer(decoded: Mapping[str, Any], expected: int) -> dict[int, dict[str, Any]]:
    """Exactly one verdict per claim 1..``expected``; anything else is a judge failure (ValueError -> retry once).

    Each verdict is ``{"supported", "unsupported_span", "severity"}``: an
    unsupported verdict must name a severity in ``SEVERITIES`` (missing or
    anything else is a judge failure); a supported verdict's span and
    severity are normalised to ``None`` whatever the judge wrote.
    """

    verdicts = decoded.get("verdicts")
    if type(verdicts) is not list:
        raise ValueError("judge answer must carry a verdicts list")
    seen: dict[int, dict[str, Any]] = {}
    for position, item in enumerate(verdicts, 1):
        if type(item) is not dict:
            raise ValueError(f"verdicts[{position}] is not an object")
        line = item.get("line")
        if type(line) is not int or isinstance(line, bool):
            raise ValueError(f"verdicts[{position}] has no integer line")
        if not 1 <= line <= expected:
            raise ValueError(f"verdicts[{position}] names claim {line}; the claims are 1 to {expected}")
        if line in seen:
            raise ValueError(f"claim {line} has more than one verdict")
        supported = item.get("supported")
        if type(supported) is not bool:
            raise ValueError(f"the verdict for claim {line} must carry a boolean supported")
        span = item.get("unsupported_span")
        if span is not None and not isinstance(span, str):
            raise ValueError(f"the verdict for claim {line} has an unsupported_span that is not a string or null")
        severity = item.get("severity")
        if not supported and severity not in SEVERITIES:
            raise ValueError(f"the verdict for claim {line} is unsupported but its severity is {severity!r}; it must be \"hard\" or \"precision\"")
        seen[line] = {"supported": supported, "unsupported_span": span if not supported else None, "severity": severity if not supported else None}
    missing = [number for number in range(1, expected + 1) if number not in seen]
    if missing:
        raise ValueError(f"{len(seen)} verdicts for {expected} claims; missing claim(s) {missing}")
    return seen


def judge_resume(binding: object, claims: Sequence[JudgeClaim]) -> dict[str, Any]:
    """One batched judge call per tailored resume through the shared retry loop; a failed judge is reported, never hidden."""

    from gigai.scout.assessment_core import invoke_json_once

    attempt = invoke_json_once(
        binding,
        lambda error: render_judge_prompt(claims, validation_error=error),
        lambda decoded: parse_judge_answer(decoded, len(claims)),
        role="reviewer",
    )
    if attempt.ok:
        parsed = attempt.parsed
        assert isinstance(parsed, dict)
        return {"judge_ok": True, "judge_attempts": attempt.attempts, "judge_error": None, "verdicts": parsed, "usage": _usage(attempt)}
    return {
        "judge_ok": False,
        "judge_attempts": attempt.attempts,
        "judge_error": attempt.validation_error or (attempt.not_assessed_reason.value if attempt.not_assessed_reason else "unknown"),
        "verdicts": {},
        "usage": _usage(attempt),
    }


def _usage(attempt: Any) -> dict[str, int | None] | None:
    if attempt.usage is None:
        return None
    return {"input_tokens": attempt.usage.input_tokens, "output_tokens": attempt.usage.output_tokens, "total_tokens": attempt.usage.total_tokens}


# --- detection outside the product ---------------------------------------------------------------


def detect_line(text: str, sources: Sequence[str], terms: Sequence[str] | frozenset[str]) -> dict[str, Any]:
    """The two deterministic guards re-run on one accepted rewritten line."""

    from gigai.scout.tailored_resume import unsupported_numbers, unsupported_posting_terms

    numbers = [mention.span for mention in unsupported_numbers(text, sources)]
    borrowed = list(unsupported_posting_terms(text, sources, terms))
    return {"numeric_hits": numbers, "term_hits": borrowed, "guard_hit": bool(numbers or borrowed)}


def line_severity(entry: Mapping[str, Any]) -> str | None:
    """``"hard"`` when a guard hit the line, a copy line is not verbatim or the judge said hard; ``"precision"`` when the judge said precision; else ``None``.

    A guard hit outranks the judge: the deterministic guards name a fact
    the sources never state, which is the HARD definition by construction.
    """

    if entry["kind"] == "copy":
        return None if entry.get("verbatim", True) else "hard"
    if entry.get("guard_hit"):
        return "hard"
    judge = entry.get("judge") or {}
    if judge.get("supported") is False:
        severity = judge.get("severity")
        return severity if severity in SEVERITIES else "hard"  # a stored verdict without a severity (pre-r3) reads as hard, never as clean
    return None


def classify_validation_error(message: str | None) -> str | None:
    """Which guard rejected a tailor attempt, from the validator's own message (see ``tailored_resume``)."""

    if not message:
        return None
    if 'contains the number "' in message:
        return "numeric"
    if 'contains the posting term "' in message:
        return "posting_term"
    if " cites resume line " in message or "which is not an answered question" in message:
        return "provenance"
    return "copy_line_shape"


def attempt_errors(outputs: Sequence[str | None], job: Any, ctx: Any) -> list[str | None]:
    """Re-run the product's own extraction + validation on each raw tailor output.

    ``invoke_json_once`` keeps only the LAST validation error, so the
    per-attempt guard counts come from re-validating the logged outputs
    with the same deterministic validator the product ran.
    """

    from gigai.scout.assessment_core import _extract_json_object
    from gigai.scout.find_jobs.contracts import FindJobsContractError
    from gigai.scout.tailored_resume import validate_tailored_output

    errors: list[str | None] = []
    for output in outputs:
        if output is None:
            errors.append(None)
            continue
        try:
            validate_tailored_output(_extract_json_object(output), job, ctx)
        except (FindJobsContractError, ValueError, TypeError) as exc:
            errors.append(str(exc))
        else:
            errors.append(None)
    return errors


# --- one row -------------------------------------------------------------------------------------


def _context(resume: Resume, answers: Sequence[FixedAnswer]):
    """The product's ``TailorContext``: answers keyed by their CANONICAL question id.

    The validator looks a cited id up as ``normalize_question_id(raw_id)``
    and the product's ``read_answers`` stores canonical ids, so the prompt
    shows and the model cites canonical ids.  The first live run (2026-09-25)
    keyed them raw: every citation of an id whose canonical form differs
    (``language:java_cpp_go`` -> ``language:cpp_go_java``) was rejected as
    "not an answered question" although the prompt had offered exactly that
    id -- a harness defect, not a model one.
    """

    from gigai.scout.question_ids import normalize_question_id
    from gigai.scout.tailored_resume import AnswerSource, TailorContext, resume_continuations, resume_lines

    keyed: dict[str, AnswerSource] = {}
    for item in answers:
        canonical = normalize_question_id(item.question_id)
        keyed[canonical] = AnswerSource(canonical, item.answer, "eval")
    # tailor-r2: exactly the product's context, so a cited wrapped line reaches
    # the guards, the row's sources and the judge as its whole span.
    return TailorContext(resume_lines=resume_lines(resume.text), answers=keyed, continuations=resume_continuations(resume.text))


def _job(posting: Posting):
    from gigai.scout.tailored_resume import TailorJob

    return TailorJob(title=posting.title, company=posting.company, location=posting.location, posting_text=posting.full_text)


def _source_entry(ref: Any) -> dict[str, Any]:
    """One cited source as the row lists it: the label as cited, the text the
    validator saw (a wrapped resume line's whole span, tailor-r2) and, only
    when the ref was expanded, the continuation line numbers."""

    entry: dict[str, Any] = {"label": ref.label(), "text": ref.text}
    continued = list(getattr(ref, "continued_lines", ()) or ())
    if continued:
        entry["continued_lines"] = continued
    return entry


def _source_label(source: Mapping[str, Any]) -> str:
    """``R4+R5`` for an expanded ref in the human-readable markdown; the JSON label stays ``R4``."""

    return str(source["label"]) + "".join(f"+R{number}" for number in source.get("continued_lines") or ())


def _where_lines(result: Any) -> list[tuple[str, Any]]:
    where_lines: list[tuple[str, Any]] = [(f"header[{index}]", line) for index, line in enumerate(result.header, 1)]
    for section in result.sections:
        for index, line in enumerate(section.lines, 1):
            where_lines.append((f"{section.heading} line {index}", line))
        for position, block in enumerate(section.entries, 1):
            for index, line in enumerate(block.heading, 1):
                where_lines.append((f"{section.heading} entry {position} heading[{index}]", line))
            for index, line in enumerate(block.bullets, 1):
                where_lines.append((f"{section.heading} entry {position} bullet {index}", line))
    return where_lines


def tailor_row(
    binding: object,
    label: Label,
    posting: Posting,
    resume: Resume,
    answers: Sequence[FixedAnswer],
    *,
    judge: bool = True,
    budget: CallBudget | None = None,
) -> dict[str, Any]:
    """One labelled pair through ``tailor_once``; every accepted line detected and listed; one judge call.

    Raises ``CallCapReached`` when the cap stops the TAILOR step (no row).
    When the cap stops the JUDGE step the row is returned with
    ``judge_stopped_at_cap`` set and its rewritten lines unjudged.
    """

    from gigai.scout.tailored_resume import TailoredResume, guard_terms, render_markdown, tailor_once

    job = _job(posting)
    ctx = _context(resume, answers)
    terms = guard_terms(job, ctx)
    if budget is not None:
        budget.begin(ROLE_TAILOR, label.key)
    started = time.monotonic()
    attempt = tailor_once(binding, job, ctx)
    elapsed = time.monotonic() - started
    tailor_calls = budget.calls_for(ROLE_TAILOR, label.key) if budget is not None else []
    errors = attempt_errors([call.output_text for call in tailor_calls], job, ctx) if tailor_calls else ([attempt.validation_error] if attempt.validation_error else [])
    row: dict[str, Any] = {
        "resume_id": label.resume_id,
        "posting_id": label.posting_id,
        "clean_fit": label.clean_fit,
        "expected_verdict": label.expected_verdict,
        "excluded": label.excluded,  # informational here: an explicitly selected row is scored whatever the flag says
        "answers": sorted(ctx.answers),  # canonical ids, as the prompt shows them
        "ok": attempt.ok,
        "attempts": attempt.attempts,
        "retried": attempt.attempts >= 2,
        "validation_error": attempt.validation_error,
        "attempt_errors": errors,
        "attempt_guards": [classify_validation_error(error) for error in errors],
        "not_assessed_reason": attempt.not_assessed_reason.value if attempt.not_assessed_reason else None,
        "elapsed_seconds": round(elapsed, 3),
        "usage": _usage(attempt),
        "guard_terms": sorted(terms),
        "sections": [],
        "lines": [],
        "copy_lines": 0,
        "rewritten_lines": 0,
        "fabricated_lines": [],
        "judge_calls": 0,
        "judge_attempts": 0,
        "judge_ok": None,
        "judge_error": None,
        "judge_usage": None,
        "judge_elapsed_seconds": None,
        "judge_stopped_at_cap": False,
        "unjudged_lines": 0,
        "markdown": None,
    }
    if not attempt.ok:
        return row
    result = attempt.parsed
    assert isinstance(result, TailoredResume)
    row["sections"] = [section.heading for section in result.sections]
    row["markdown"] = render_markdown(result)
    claims: list[JudgeClaim] = []
    for where, line in _where_lines(result):
        cited = tuple((ref.label(), ref.text) for ref in line.refs)
        entry: dict[str, Any] = {"where": where, "kind": line.kind, "text": line.text, "sources": [_source_entry(ref) for ref in line.refs]}
        if line.kind == "copy":
            row["copy_lines"] += 1
            entry["verbatim"] = line.refs[0].text == line.text  # checked in code, never by the judge
            entry["fabricated"] = not entry["verbatim"]
            entry["severity"] = None if entry["verbatim"] else "hard"
        else:
            row["rewritten_lines"] += 1
            entry.update(detect_line(line.text, [text for _, text in cited], terms))
            entry["claim"] = len(claims) + 1
            entry["judge"] = None
            entry["fabricated"] = entry["guard_hit"]
            entry["severity"] = "hard" if entry["guard_hit"] else None
            claims.append(JudgeClaim(entry["claim"], line.text, cited))
        row["lines"].append(entry)
    rewritten = [entry for entry in row["lines"] if entry["kind"] == "rewritten"]
    if judge and claims:
        if budget is not None:
            budget.begin(ROLE_JUDGE, label.key)
        started = time.monotonic()
        try:
            verdict = judge_resume(binding, claims)
        except CallCapReached:
            row["judge_stopped_at_cap"] = True
            row["unjudged_lines"] = len(rewritten)
        else:
            row["judge_elapsed_seconds"] = round(time.monotonic() - started, 3)
            row["judge_calls"] = 1
            row["judge_attempts"] = verdict["judge_attempts"]
            row["judge_ok"] = verdict["judge_ok"]
            row["judge_error"] = verdict["judge_error"]
            row["judge_usage"] = verdict["usage"]
            if verdict["judge_ok"]:
                for entry in rewritten:
                    entry["judge"] = verdict["verdicts"][entry["claim"]]
                    entry["fabricated"] = entry["guard_hit"] or entry["judge"]["supported"] is False
                    entry["severity"] = line_severity(entry)
            else:
                row["unjudged_lines"] = len(rewritten)
    elif judge:
        row["judge_ok"] = None  # nothing to judge: every line is a copy line
    else:
        row["unjudged_lines"] = len(rewritten)
    row["fabricated_lines"] = [entry for entry in row["lines"] if entry["fabricated"]]
    return row


# --- samples: a tailored resume as markdown with every line's sources ----------------------------


def render_sample_markdown(row: Mapping[str, Any]) -> str:
    """The row's markdown with each line's cited sources (and the judge's verdict) quoted under it."""

    entries = list(row["lines"])
    out: list[str] = [f"<!-- {row['resume_id']} x {row['posting_id']} -->"]
    position = 0
    for line in str(row["markdown"]).splitlines():
        match = _REFS_COMMENT.search(line)
        if match is None or position >= len(entries):
            out.append(line)
            continue
        entry = entries[position]
        position += 1
        indent = "  " if line.startswith("- ") else ""
        out.append(line)
        for source in entry["sources"]:
            out.append(f"{indent}  > {_source_label(source)}: {source['text']}")
        if entry["kind"] == "copy":
            out.append(f"{indent}  > check: copy line, verbatim={entry['verbatim']}")
        else:
            judge = entry.get("judge")
            if judge is None:
                verdict_text = "not judged"
            elif judge["supported"]:
                verdict_text = "supported"
            else:
                verdict_text = f"UNSUPPORTED ({judge.get('severity') or 'no severity'}) span: {judge['unsupported_span']!r}"
            hits = ""
            if entry.get("numeric_hits") or entry.get("term_hits"):
                hits = f"; guard hits numeric={entry.get('numeric_hits')} terms={entry.get('term_hits')}"
            out.append(f"{indent}  > judge: {verdict_text}{hits}")
    if position != len(entries):  # the markdown and the line list disagree: say so rather than mislabel a source
        out.append(f"<!-- WARNING: {len(entries)} lines listed but {position} markdown lines carry refs -->")
    return "\n".join(out) + "\n"


def pick_samples(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One clean fit and one non-clean-fit ('pending') tailored resume, the first valid of each."""

    samples: dict[str, Any] = {"clean_fit": None, "pending": None}
    for row in rows:
        if not row["ok"]:
            continue
        slot = "clean_fit" if row["clean_fit"] else "pending"
        if samples[slot] is None:
            samples[slot] = {"resume_id": row["resume_id"], "posting_id": row["posting_id"], "expected_verdict": row["expected_verdict"], "markdown_with_sources": render_sample_markdown(row)}
    return samples


# --- metrics --------------------------------------------------------------------------------------


def latency_stats(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "p90": None, "p95": None, "max": None}
    ordered = sorted(values)

    def percentile(share: float) -> float:
        return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))]

    return {
        "count": len(ordered),
        "mean": round(statistics.fmean(ordered), 3),
        "median": round(statistics.median(ordered), 3),
        "p90": round(percentile(0.9), 3),
        "p95": round(percentile(0.95), 3),
        "max": round(ordered[-1], 3),
    }


def _tokens(calls: Sequence[CallRecord]) -> dict[str, int]:
    usages = [call.usage for call in calls if call.usage]
    return {
        "input": sum(int(item["input_tokens"] or 0) for item in usages),
        "output": sum(int(item["output_tokens"] or 0) for item in usages),
        "total": sum(int(item["total_tokens"] or 0) for item in usages),
    }


def summarize(
    rows: Sequence[Mapping[str, Any]],
    *,
    planned: int,
    max_calls: int,
    judge: bool,
    calls: Sequence[CallRecord] = (),
    rows_not_done: Sequence[Mapping[str, str]] = (),
) -> dict[str, Any]:
    """Every row given is scored (explicit selection may include ``excluded`` labels on purpose)."""

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
    judge_hard = sum(1 for row in valid for entry in row["lines"] if (entry.get("judge") or {}).get("supported") is False and (entry["judge"].get("severity") or "hard") == "hard")
    judge_precision = sum(1 for row in valid for entry in row["lines"] if (entry.get("judge") or {}).get("severity") == "precision")
    hard = [entry for entry in fabricated if line_severity(entry) == "hard"]
    precision = [entry for entry in fabricated if line_severity(entry) == "precision"]
    accepted_lines = rewritten + copied
    precision_rate = _rate(len(precision), accepted_lines)
    judge_calls = sum(int(row["judge_calls"]) for row in valid)
    judge_attempts = sum(int(row["judge_attempts"]) for row in valid)
    judge_failures = sum(1 for row in valid if row["judge_ok"] is False)
    judge_retries = sum(1 for row in valid if int(row["judge_attempts"]) >= 2)
    judge_stopped = sum(1 for row in valid if row["judge_stopped_at_cap"])
    unjudged = sum(int(row["unjudged_lines"]) for row in valid)
    answer_refs = sum(1 for row in valid for entry in row["lines"] for source in entry["sources"] if source["label"].startswith("A "))
    expanded_refs = sum(1 for row in valid for entry in row["lines"] for source in entry["sources"] if source.get("continued_lines"))
    invalid_rate = _rate(len(invalid_after_retry), len(rows))
    guard_rejections: dict[str, dict[str, int]] = {guard: {"retried": 0, "invalid_after_retry": 0} for guard in GUARDS}
    for row in rows:
        guards = list(row.get("attempt_guards") or [])
        for position, guard in enumerate(guards, 1):
            if guard is None:
                continue
            if position < len(guards) or row["ok"]:
                guard_rejections[guard]["retried"] += 1  # a later attempt followed
            else:
                guard_rejections[guard]["invalid_after_retry"] += 1  # the last attempt, and the row is invalid
    tailor_calls = [call for call in calls if call.role == ROLE_TAILOR]
    judge_call_log = [call for call in calls if call.role == ROLE_JUDGE]
    hard_bar_met = len(valid) > 0 and len(hard) == HARD_FABRICATIONS_BAR and (not judge or unjudged == 0)
    precision_bar_met = precision_rate is not None and precision_rate < PRECISION_RATE_BAR and (not judge or unjudged == 0)
    return {
        "calls": {
            "max_calls": max_calls,
            "made": len(calls),
            "tailor_calls": len(tailor_calls),
            "judge_calls": len(judge_call_log),
            "stopped_at_cap": bool(rows_not_done),
            "rows_planned": planned,
            "rows_done": len(rows),
            "rows_not_done": list(rows_not_done),
        },
        "lines": {"total": lines, "copy": copied, "rewritten": rewritten, "answer_refs": answer_refs, "expanded_refs": expanded_refs},
        "fabrication": {
            "fabricated_claims": len(fabricated),
            "fabrication_rate": _rate(len(fabricated), accepted_lines),
            "hard_fabrications": len(hard),
            "precision_lines": len(precision),
            "precision_rate": precision_rate,
            "numeric_guard_hits": numeric_hits,
            "posting_term_guard_hits": term_hits,
            "copy_lines_not_verbatim": non_verbatim,
            "judge_enabled": judge,
            "judge_calls": judge_calls,
            "judge_attempts": judge_attempts,
            "judge_retries": judge_retries,
            "judge_unsupported": judge_unsupported,
            "judge_hard": judge_hard,
            "judge_precision": judge_precision,
            "judge_failures": judge_failures,
            "judge_stopped_at_cap": judge_stopped,
            "unjudged_rewritten_lines": unjudged,
            "lines": [{**entry, "severity": line_severity(entry)} for entry in fabricated],  # EVERY flagged line, hard and precision, with its sources
        },
        "guard_rejections": {
            **guard_rejections,
            "total_retried": sum(item["retried"] for item in guard_rejections.values()),
            "total_invalid_after_retry": sum(item["invalid_after_retry"] for item in guard_rejections.values()),
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
                {
                    "resume_id": row["resume_id"],
                    "posting_id": row["posting_id"],
                    "reason": row["not_assessed_reason"],
                    "validation_error": row["validation_error"],
                    "attempt_errors": list(row.get("attempt_errors") or []),
                    "attempt_guards": list(row.get("attempt_guards") or []),
                }
                for row in rows
                if not row["ok"] or row["retried"]
            ],
            "latency_seconds": {
                "per_call": {
                    "all": latency_stats([call.elapsed_seconds for call in calls]),
                    "tailor": latency_stats([call.elapsed_seconds for call in tailor_calls]),
                    "judge": latency_stats([call.elapsed_seconds for call in judge_call_log]),
                },
                "per_row_tailor_step": latency_stats([float(row["elapsed_seconds"]) for row in rows]),
            },
            "tokens": {
                "all": _tokens(calls),
                "tailor": _tokens(tailor_calls),
                "judge": _tokens(judge_call_log),
            },
            "model_cost_usd": "unavailable",
        },
        "bars": {
            "hard_fabrications_bar": HARD_FABRICATIONS_BAR,
            "hard_fabrications_bar_met": hard_bar_met,
            "precision_rate_bar": PRECISION_RATE_BAR,
            "precision_rate_bar_met": precision_bar_met,
            "invalid_after_retry_bar_met": (invalid_rate is not None and invalid_rate < INVALID_AFTER_RETRY_BAR),
        },
        "samples": pick_samples(rows),
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
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS, help=f"HARD CAP on model calls in total: tailor + judge attempts, retries included (default {DEFAULT_MAX_CALLS})")
    parser.add_argument("--row", action="append", default=[], metavar="'RESUME_ID x POSTING_ID'", help="an explicit labelled pair to run, in the order given, whatever its excluded flag (repeatable)")
    parser.add_argument("--rows-file", type=Path, default=None, help="a file of explicit pairs, one 'RESUME_ID x POSTING_ID' per line (# comments allowed)")
    parser.add_argument("--sample", type=int, default=None, help="draw this many labelled rows at random (with --seed) before ordering")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--clean-fit-only", action="store_true", help="only the clean-fit rows")
    parser.add_argument("--resume", action="append", default=[], metavar="RESUME_ID", help="only rows for this resume (repeatable)")
    parser.add_argument("--posting", action="append", default=[], metavar="POSTING_ID", help="only rows for this posting (repeatable)")
    parser.add_argument("--no-judge", action="store_true", help="skip the judge call; only the deterministic guards run")
    parser.add_argument("--report", type=Path, default=None, help="report path (default ../orchestrator/research/evals/tailor-<date>.json)")
    parser.add_argument("--dump-dir", type=Path, default=None, help="write every prompt and raw model output per attempt under this directory")
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
    guards = metrics["guard_rejections"]
    print("=== tailor eval summary ===", file=out)
    print(
        f"calls: {calls['made']} of cap {calls['max_calls']} ({calls['tailor_calls']} tailor, {calls['judge_calls']} judge); rows {calls['rows_done']}/{calls['rows_planned']} done"
        + (f"; STOPPED AT CAP, not done: {calls['rows_not_done']}" if calls["stopped_at_cap"] else ""),
        file=out,
    )
    print(f"lines: {lines['total']} ({lines['copy']} copied, {lines['rewritten']} rewritten, {lines['answer_refs']} answer refs, {lines.get('expanded_refs', 0)} expanded resume refs)", file=out)
    bars = metrics["bars"]
    print(
        f"hard fabrications: {fab['hard_fabrications']} (bar {bars['hard_fabrications_bar']} met: {bars['hard_fabrications_bar_met']}) -- numeric {fab['numeric_guard_hits']}, posting-term {fab['posting_term_guard_hits']}, copy-not-verbatim {fab['copy_lines_not_verbatim']}, judge-hard {fab['judge_hard']}; "
        f"precision lines: {fab['precision_lines']} (rate {fab['precision_rate']}; bar < {bars['precision_rate_bar']} met: {bars['precision_rate_bar_met']}); "
        f"flagged lines in all {fab['fabricated_claims']} (rate {fab['fabrication_rate']}); judge-unsupported {fab['judge_unsupported']}, judge failures {fab['judge_failures']} (retries {fab['judge_retries']}), unjudged rewritten lines {fab['unjudged_rewritten_lines']}",
        file=out,
    )
    for entry in fab["lines"]:
        cited = "; ".join(f"{_source_label(source)}: {source['text']}" for source in entry["sources"])
        span = (entry.get("judge") or {}).get("unsupported_span")
        print(f"  FAB [{entry['severity']}] {entry['resume_id']} x {entry['posting_id']} {entry['where']}: {entry['text']!r} | span: {span!r} | numeric {entry.get('numeric_hits')} terms {entry.get('term_hits')} | sources: {cited}", file=out)
    print(
        "guard rejections (retried / invalid after retry): "
        + ", ".join(f"{guard} {guards[guard]['retried']}/{guards[guard]['invalid_after_retry']}" for guard in GUARDS)
        + f"; total {guards['total_retried']}/{guards['total_invalid_after_retry']}",
        file=out,
    )
    print(f"reliability: valid {rel['valid']}/{calls['rows_done']} ({rel['valid_output_rate']}); invalid after retry {rel['invalid_after_retry']} ({rel['invalid_after_retry_rate']}, bar < {rel['invalid_after_retry_bar']} met: {rel['invalid_after_retry_bar_met']}); retries {rel['retries']} (recovered {rel['recovered_on_retry']}); transport failures {rel['transport_failures']}", file=out)
    for item in rel["rejections"]:
        print(f"  REJECTED {item['resume_id']} x {item['posting_id']}: {item['reason'] or 'recovered on retry'} | attempts {item['attempt_guards']} | {item['validation_error']}", file=out)
    latency = rel["latency_seconds"]["per_call"]
    print(f"latency s per call: all {latency['all']}; tailor {latency['tailor']}; judge {latency['judge']}", file=out)
    print(f"tokens: {rel['tokens']}; model cost: {rel['model_cost_usd']}", file=out)
    print(f"report: {report_path}", file=out)


def _dump_calls(dump_dir: Path, calls: Sequence[CallRecord]) -> None:
    dump_dir.mkdir(parents=True, exist_ok=True)
    for call in calls:
        row_part = f"{call.row_key[0]}-x-{call.row_key[1]}" if call.row_key else "no-row"
        stem = f"{call.index:02}-{row_part}-{call.role}-attempt{call.attempt}".replace(":", "_").replace("/", "_")
        (dump_dir / f"{stem}-prompt.md").write_text(call.prompt, encoding="utf-8")
        output = call.output_text if call.output_text is not None else f"<no output: {call.error}>"
        (dump_dir / f"{stem}-output.txt").write_text(output, encoding="utf-8")
    index = [
        {"index": call.index, "role": call.role, "row": list(call.row_key) if call.row_key else None, "attempt": call.attempt, "elapsed_seconds": call.elapsed_seconds, "usage": call.usage, "error": call.error}
        for call in calls
    ]
    (dump_dir / "calls.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = datetime.now(UTC)
    postings = load_postings()
    resumes = load_resumes()
    answers = load_answers()
    all_labels = load_labels(include_excluded=True)
    excluded = [label for label in all_labels if label.excluded]
    explicit: list[tuple[str, str]] = []
    try:
        if args.rows_file is not None:
            explicit.extend(read_rows_file(args.rows_file))
        explicit.extend(parse_row_spec(spec) for spec in args.row)
        if explicit:
            rows_to_call = select_rows(all_labels, explicit)
            selection: dict[str, Any] = {
                "mode": "explicit",
                "rows": [f"{label.resume_id} x {label.posting_id}" for label in rows_to_call],
                "excluded_rows_included": [f"{label.resume_id} x {label.posting_id}" for label in rows_to_call if label.excluded],
                "note": (
                    "Explicitly selected rows are tailored and scored whatever their excluded flag: the exclusion "
                    "(LABELLING.md rule E) is the assess eval's location-scoring rule for Remote Canada/Poland postings; "
                    "whether a tailored resume fabricates does not depend on the posting's location."
                ),
            }
        else:
            rows_to_call = plan_rows(load_labels(), sample=args.sample, seed=args.seed, clean_fit_only=args.clean_fit_only, resume_ids=args.resume, posting_ids=args.posting)
            selection = {"mode": "planned", "rows": [f"{label.resume_id} x {label.posting_id}" for label in rows_to_call], "excluded_rows_included": [], "note": "plan_rows: rule E, excluded rows never planned"}
    except RowSelectionError as exc:
        print(f"row selection: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        for index, label in enumerate(rows_to_call, 1):
            marker = "clean" if label.clean_fit else "uncertain" if label.uncertain else "confident"
            flag = "excluded-flag" if label.excluded else ""
            ids = ";".join(item.question_id for item in answers.get(label.resume_id, ()))
            print(f"{index:3} {marker:9} {label.resume_id} x {label.posting_id} answers {ids or '-'} {flag}".rstrip())
        minimum = len(rows_to_call) * (1 if args.no_judge else 2)
        print(f"    {len(rows_to_call)} rows; at least {minimum} calls without retries; cap {args.max_calls}", file=sys.stderr)
        if selection["mode"] == "planned":
            for label in excluded:
                print(f"    excluded  {label.resume_id} x {label.posting_id} (rule E: not planned, not scored)", file=sys.stderr)
        return 0

    report_path = args.report or default_report_path(fake=args.fake_model, now=now)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    seams: dict[str, str] = {"GIGAI_SCOUT_FIND_JOBS_TEST_MODEL": "1"} if args.fake_model else {}
    budget = CallBudget(max_calls=args.max_calls)
    results: list[dict[str, Any]] = []
    rows_not_done: list[dict[str, str]] = []
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

        binding = CappedBinding(resolve_binding(config, model_target, home_root=home_root), budget)
        try:
            for index, label in enumerate(rows_to_call, 1):
                if not args.quiet:
                    print(f"[{index}/{len(rows_to_call)}] {label.resume_id} x {label.posting_id} (calls so far {budget.made}/{budget.max_calls})", file=sys.stderr)
                try:
                    row = tailor_row(binding, label, postings[label.posting_id], resumes[label.resume_id], answers.get(label.resume_id, ()), judge=not args.no_judge, budget=budget)
                except CallCapReached as exc:
                    rows_not_done.append({"resume_id": label.resume_id, "posting_id": label.posting_id, "stage": "tailor", "reason": str(exc)})
                    rows_not_done.extend({"resume_id": rest.resume_id, "posting_id": rest.posting_id, "stage": "not started", "reason": f"call cap {budget.max_calls} reached"} for rest in rows_to_call[index:])
                    if not args.quiet:
                        print(f"    -> STOPPED: {exc}", file=sys.stderr)
                    break
                results.append(row)
                if not args.quiet:
                    outcome = (
                        f"{len(row['lines'])} lines ({row['rewritten_lines']} rewritten), {len(row['fabricated_lines'])} fabricated, judge {'ok' if row['judge_ok'] else row['judge_error'] or 'not run'}"
                        if row["ok"]
                        else f"INVALID {row['not_assessed_reason']}: {row['validation_error']}"
                    )
                    print(f"    -> {outcome} in {row['elapsed_seconds']}s, attempts {row['attempts']}", file=sys.stderr)
                if row["judge_stopped_at_cap"]:
                    rows_not_done.append({"resume_id": label.resume_id, "posting_id": label.posting_id, "stage": "judge", "reason": f"call cap {budget.max_calls} reached before the judge call"})
                    rows_not_done.extend({"resume_id": rest.resume_id, "posting_id": rest.posting_id, "stage": "not started", "reason": f"call cap {budget.max_calls} reached"} for rest in rows_to_call[index:])
                    if not args.quiet:
                        print(f"    -> STOPPED: call cap {budget.max_calls} reached before the judge call", file=sys.stderr)
                    break
        finally:
            binding.close()

    metrics = summarize(results, planned=len(rows_to_call), max_calls=args.max_calls, judge=not args.no_judge, calls=budget.calls, rows_not_done=rows_not_done)
    from gigai.scout.tailored_resume import TAILOR_INSTRUCTIONS_DIGEST

    report = {
        "schema": REPORT_SCHEMA,
        "run": {
            "started_at": now.isoformat().replace("+00:00", "Z"),
            "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "git_head": _git_head(),
            "fake_model": bool(args.fake_model),
            "judge": not args.no_judge,
            "model_target": model_target,
            "adapter_target": getattr(getattr(binding, "port", None), "name", None) or model_target,
            "instructions_digest": TAILOR_INSTRUCTIONS_DIGEST,
            "judge_prompt_sha256": hashlib.sha256(JUDGE_PROMPT_PATH.read_bytes()).hexdigest(),
            "max_calls": args.max_calls,
            "calls_made": budget.made,
            "stopped_at_cap": bool(rows_not_done),
            "rows_not_done": rows_not_done,
            "row_selection": selection,
            "clean_fit_only": bool(args.clean_fit_only),
            "sample": args.sample,
            "seed": args.seed,
            "excluded_rows": [{"resume_id": label.resume_id, "posting_id": label.posting_id} for label in excluded],
            "dump_dir": str(args.dump_dir) if args.dump_dir else None,
            "repo_root": str(REPO_ROOT),
        },
        "metrics": metrics,
        "calls": [
            {"index": call.index, "role": call.role, "row": list(call.row_key) if call.row_key else None, "attempt": call.attempt, "elapsed_seconds": call.elapsed_seconds, "usage": call.usage, "error": call.error}
            for call in budget.calls
        ],
        "rows": results,
    }
    report_path.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.dump_dir is not None:
        _dump_calls(args.dump_dir, budget.calls)
    if not args.quiet:
        _print_summary(metrics, report_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
