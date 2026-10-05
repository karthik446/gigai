"""Bind the real Scout find-jobs nodes to the functional graph.

The graph-node registry is deliberately process-local.  ``launch_run`` starts
the deterministic scheduler in a spawned child, so registration also installs
a module-level worker wrapper; the wrapper re-registers the nodes in the child
before handing control to the existing scheduler entry point.

The two ``GIGAI_SCOUT_FIND_JOBS_TEST_*`` hooks are intentionally narrow test
seams.  They make it possible for an offline behavior test to construct
``httpx.MockTransport`` in the spawned child, where a parent-process transport
object cannot be pickled.  Production callers leave both variables unset.
"""

from __future__ import annotations

from dataclasses import replace
from functools import partial
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from ...canonical import parse_json_bytes
from ...config import GigAIConfig, load_config
from ...graph_node_registry import RegisteredNode, lookup, register
from .ats_board_clients import ATSBoardClients
from .exa_client import ExaSearchClient
from .contracts import (
    ACQUIRE_CAPABILITY,
    ACQUIRE_DECLARED_EFFECTS,
    ASSESS_CAPABILITY,
    ASSESS_DECLARED_EFFECTS,
    PRESENT_CAPABILITY,
    PRESENT_DECLARED_EFFECTS,
    AcquireOutput,
    NotAssessedReason,
    NotAssessedRow,
    PresentInput,
)
from .market_acquisition import BOARDS_FROM_INDEX, acquire_node
from ..projection import present_node
from ..proposal_execution import assess_node
from .watchlist import JournalWatchlistClient, list_active


GRAPH_ID = "find-jobs-functional"
GRAPH_VERSION = 1

# These names are public so the offline M1 behavior can configure a valid
# local-model identity without duplicating the binding's test constants.
TEST_MODEL_NAME = "scout-test:latest"
TEST_MODEL_DIGEST = "sha256:" + ("0" * 64)

_HOME_ROOT_ENV = "GIGAI_SCOUT_FIND_JOBS_HOME_ROOT"
_TEST_HTTP_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_HTTP"
_TEST_MODEL_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_MODEL"
# P5: the quick-assess deadline for the fake model; read ONLY when
# _TEST_MODEL_ENV is on (see _test_assess_timeout_seconds).
_TEST_ASSESS_TIMEOUT_ENV = "GIGAI_SCOUT_ASSESS_TIMEOUT_SECONDS"
# P5: prompt-content markers the fake model handler reacts to (a journey puts
# one into the pasted posting text; the real prompt carries it through).
TEST_MODEL_GARBAGE_MARKER = "GIGAI-TEST-MODEL: return garbage"
TEST_MODEL_SLEEP_MARKER = "GIGAI-TEST-MODEL: sleep"
TEST_MODEL_SLEEP_SECONDS = 2.5
_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
_LIVE_BINDINGS: dict[tuple[Path, Path], tuple[RegisteredNode, ...]] = {}


def _registry_graph_ids(*, home_root: Path, target: Path) -> tuple[str, ...]:
    """Return the selector plus the sealed graph identity used by I-2.

    I-1's descriptor/selector is ``find-jobs-functional`` while the compiled
    goal graph has its own canonical ``graph_*`` identity.  The scheduler's
    registry lookup is keyed by the latter, so keep the required selector
    binding and add the sealed graph identity as an execution alias.
    """

    graph_ids = [GRAPH_ID]
    try:
        from ...workpad import resolve_workpad

        resolved = resolve_workpad(
            home_root=home_root,
            requested_target=target,
            gig_id=None,
            allow_semantic_state=True,
        )
        pointer = parse_json_bytes(
            (resolved.path / "manifests" / "active-gig-version.json").read_bytes()
        )
        graph_set_ref = pointer.get("graph_set") if isinstance(pointer, dict) else None
        graph_set_path = graph_set_ref.get("path") if isinstance(graph_set_ref, dict) else None
        if not isinstance(graph_set_path, str):
            return tuple(graph_ids)
        graph_set = parse_json_bytes((resolved.path / graph_set_path).read_bytes())
        descriptors = graph_set.get("graphs") if isinstance(graph_set, dict) else None
        if not isinstance(descriptors, list):
            return tuple(graph_ids)
        descriptor = next(
            (item for item in descriptors if isinstance(item, dict) and item.get("graph_id") == GRAPH_ID),
            None,
        )
        graph_ref = descriptor.get("goal_graph") if isinstance(descriptor, dict) else None
        graph_path = graph_ref.get("path") if isinstance(graph_ref, dict) else None
        if not isinstance(graph_path, str):
            return tuple(graph_ids)
        graph = parse_json_bytes((resolved.path / graph_path).read_bytes())
        graph_id = graph.get("graph_id") if isinstance(graph, dict) else None
        if isinstance(graph_id, str) and graph_id and graph_id not in graph_ids:
            graph_ids.append(graph_id)
    except (OSError, ValueError, KeyError, TypeError):
        # Bare binding/import tests may not have an active approved workpad;
        # the mandated selector registration remains valid in that case.
        pass
    return tuple(graph_ids)


class _BoundWatchlist:
    """A-5's journal client plus the read method A-6 probes when polling ATS."""

    def __init__(self, home_root: Path, target: Path) -> None:
        self._home_root = home_root
        self._target = target
        self._journal = JournalWatchlistClient(home_root, target)
        self._gig_id = self._journal._resolved.gig_id

    def add_to_watchlist(self, entry: object) -> object:
        return self._journal.add_to_watchlist(entry)  # type: ignore[arg-type]

    def active_entries(self) -> tuple[object, ...]:
        return list_active(
            self._home_root,
            self._target,
            gig_id=self._gig_id,
        )


def _test_http_enabled() -> bool:
    return os.environ.get(_TEST_HTTP_ENV) == "1"


_TEST_GENERIC_PAGE_HTML = (
    "<html><head><title>Backend Engineer - Example Careers</title></head><body>"
    "<h1>Backend Engineer</h1>"
    "<p>Example Corp builds reliable Python services for a growing customer base. "
    "We are hiring a backend engineer to own our ingestion pipeline end to end.</p>"
    "<h2>What you will do</h2><ul>"
    "<li>Design and operate HTTP services in Python.</li>"
    "<li>Own reliability: tracing, alerting, and on-call for the systems you build.</li>"
    "<li>Review code and mentor engineers across the platform group.</li></ul>"
    "<h2>What we look for</h2><ul>"
    "<li>Five or more years building production backend systems.</li>"
    "<li>Depth in Python and PostgreSQL; comfort with distributed systems.</li>"
    "<li>Clear written communication.</li></ul>"
    "<p>Example Corp is unable to sponsor visas for this position.</p>"
    "</body></html>"
)
_TEST_JS_SHELL_HTML = (
    '<!doctype html><html><head><meta charset="utf-8"><title>Job Application</title>'
    '<script src="https://boards.greenhouse.io/embed/job_app.js"></script>'
    "<script>window.__GH__={board:'acme',job:101};document.addEventListener('DOMContentLoaded',"
    "function(){window.__GH__.render();});</script></head><body><div id=\"app\"></div></body></html>"
)


_TEST_BULK_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_BULK_POSTINGS"
_TEST_BULK_BEST_OLDEST = 20
_TEST_BULK_BEST_OLDEST_SMALL = 3  # a bulk of at most 100 jobs: the 3 oldest


_TEST_BULK_AGE_DAYS_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_BULK_AGE_DAYS"


def _test_bulk_age_days() -> list[int]:
    """0110-019: ``GIGAI_SCOUT_FIND_JOBS_TEST_BULK_AGE_DAYS=2,5,20`` -- bulk job ``i`` was posted ``ages[i]`` days ago.

    The posted-window journeys need postings of known ages relative to
    today; a job with no entry keeps its fixed date. ``[]`` when unset.
    """

    try:
        return [int(item) for item in os.environ.get(_TEST_BULK_AGE_DAYS_ENV, "").split(",") if item.strip()]
    except ValueError:
        return []


def _test_days_ago(days: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _test_bulk_postings() -> list[dict[str, object]]:
    """``N`` Greenhouse listing rows when ``GIGAI_SCOUT_FIND_JOBS_TEST_BULK_POSTINGS=N`` (``[]`` otherwise).

    SCOPE-ADD-3 C1 follow-up: the api-e2e rank journeys need more postings
    than the one-row fixture, in a shape where rank order and date order
    DISAGREE. Job ``i`` is ``i`` minutes older than job 0 (the newest); the
    :data:`_TEST_BULK_BEST_OLDEST` oldest (the 3 oldest of a bulk of at most 100) carry ``fit 95`` in their title (the
    rank fixture's score, see ``_test_model_rank_reply``), every other job a
    ``fit`` of 30-69, and job 0 (the newest) scores 99 but its text says a
    security clearance is required, so the ranker names a blocker for it.
    Read only when the test-HTTP seam is on; production never sets it.
    """

    try:
        count = int(os.environ.get(_TEST_BULK_ENV, "0"))
    except ValueError:
        return []
    ages = _test_bulk_age_days()
    best_oldest = _TEST_BULK_BEST_OLDEST if count > 100 else _TEST_BULK_BEST_OLDEST_SMALL
    jobs: list[dict[str, object]] = []
    for index in range(max(count, 0)):
        if index == 0:
            fit, note = 99, "Security clearance required."
        elif index >= count - best_oldest:
            fit, note = 95, "Build reliable Python services."
        else:
            fit, note = 30 + index % 40, "Build reliable Python services."
        minutes = index % 60
        hours = index // 60
        # 0110-10-14: a Greenhouse job says when it went up (``first_published``) beside its last change; here they are one instant.
        posted = _test_days_ago(ages[index]) if index < len(ages) else f"2026-09-22T{23 - hours:02d}:{59 - minutes:02d}:00Z"
        jobs.append(
            {
                "id": str(1000 + index),
                "title": f"Software Engineer fit {fit}",
                "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{1000 + index}",
                "location": {"name": "Denver, CO"},
                "first_published": posted,
                "updated_at": posted,
                "company_name": "Acme",
                "content": f"&lt;p&gt;{note} Posting {index}.&lt;/p&gt;",
            }
        )
    return jobs


def _test_provider_handler(request: httpx.Request) -> httpx.Response:
    """Serve the fixed Exa/Greenhouse fixtures used by the child-process test."""

    if request.method == "POST" and request.url.host == "api.exa.ai" and request.url.path == "/search":
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://boards.greenhouse.io/acme/jobs/101",
                        "title": "Software Engineer",
                        "publishedDate": "2026-09-22T00:00:00Z",
                    }
                ]
            },
            request=request,
        )
    # P4 quick-assess job-input fixtures: the Greenhouse SINGLE-job endpoint
    # (checked before the host-only board match below), a generic HTML career
    # page, and a Greenhouse JavaScript shell whose text is too short to be a
    # posting (so ``resolve_job`` falls back to the board listing).
    if request.method == "GET" and request.url.host == "boards-api.greenhouse.io" and request.url.path == "/v1/boards/acme/jobs/101":
        return httpx.Response(
            200,
            json={
                "id": 101,
                "title": "Software Engineer",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/101",
                "location": {"name": "Denver, CO"},
                "first_published": "2026-09-22T00:00:00Z",
                "updated_at": "2026-09-22T00:00:00Z",
                "company_name": "Acme",
                "content": "&lt;p&gt;Build reliable Python services.&lt;/p&gt;",
            },
            request=request,
        )
    if request.method == "GET" and request.url.host == "careers.example.test" and request.url.path == "/jobs/9":
        return httpx.Response(200, text=_TEST_GENERIC_PAGE_HTML, headers={"content-type": "text/html; charset=utf-8"}, request=request)
    if request.method == "GET" and request.url.host == "boards.greenhouse.io" and request.url.path == "/acme/jobs/101":
        return httpx.Response(200, text=_TEST_JS_SHELL_HTML, headers={"content-type": "text/html; charset=utf-8"}, request=request)
    # P5 (api-e2e journey (d)): a SECOND Greenhouse board, ``shell``, whose
    # single-job endpoint is unknown (404), whose posting page is a JavaScript
    # shell, and whose board listing carries the row -- so ``resolve_job``
    # has to take the board-listing fallback.  A separate token keeps the
    # acquire journeys (which list only the ``acme`` board) untouched.
    if request.method == "GET" and request.url.host == "boards-api.greenhouse.io" and request.url.path == "/v1/boards/shell/jobs/303":
        return httpx.Response(404, json={"error": "job not found"}, request=request)
    if request.method == "GET" and request.url.host == "boards.greenhouse.io" and request.url.path == "/shell/jobs/303":
        return httpx.Response(200, text=_TEST_JS_SHELL_HTML, headers={"content-type": "text/html; charset=utf-8"}, request=request)
    if request.method == "GET" and request.url.host == "boards-api.greenhouse.io" and request.url.path == "/v1/boards/shell/jobs":
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "303",
                        "title": "Platform Engineer",
                        "absolute_url": "https://boards.greenhouse.io/shell/jobs/303",
                        "location": {"name": "Remote, US"},
                        "first_published": "2026-09-22T00:00:00Z",
                        "updated_at": "2026-09-22T00:00:00Z",
                        "content": "&lt;p&gt;Operate Python platform services on GCP.&lt;/p&gt;",
                    }
                ]
            },
            request=request,
        )
    if request.method == "GET" and request.url.host == "boards-api.greenhouse.io":
        bulk = _test_bulk_postings()
        if bulk:
            return httpx.Response(200, json={"jobs": bulk}, request=request)
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "101",
                        "title": "Software Engineer",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/101",
                        "location": {"name": "Denver, CO"},
                        "first_published": "2026-09-22T00:00:00Z",
                        "updated_at": "2026-09-22T00:00:00Z",
                        "content": "Build reliable Python services.",
                    }
                ]
            },
            request=request,
        )
    return httpx.Response(404, json={"error": "test fixture route not found"}, request=request)


def _test_model_enabled() -> bool:
    return os.environ.get(_TEST_MODEL_ENV) == "1"


def _test_assess_timeout_seconds() -> float | None:
    """P5's quick-assess deadline for the FAKE model, or ``None`` when unset.

    Read by ``quick_assess`` only while ``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL=1``
    (the fixture ``MockTransport`` has no socket for an adapter timeout to
    fire on, so the api-e2e timeout journey needs a deadline of its own).
    Production never reads it: the real adapters' own timeouts apply.
    """

    raw = os.environ.get(_TEST_ASSESS_TIMEOUT_ENV)
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _test_model_prompt(request: httpx.Request) -> str:
    """The prompt text inside a fixture Ollama ``/api/chat`` body (``""`` if none)."""

    try:
        payload = json.loads(request.content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ""
    messages = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(messages, list):
        return ""
    return "\n".join(
        item["content"] for item in messages if isinstance(item, dict) and isinstance(item.get("content"), str)
    )


#: P3 (v0.1.9): a marker the fixture recognizes in the RENDERED
#: {{prior_answers}} block (``assessment_core.render_assess_prompt`` writes
#: one ``- <question_id>: <answer>`` line per prior answer) -- never in the
#: posting text itself, unlike the garbage/sleep markers above. Its presence
#: means "cloud:gcp is already answered", which the fixture's own GCP
#: question is the one thing standing in the way of a match, so this alone
#: decides pending vs matched (the plan's own "keyed on prompt content" line
#: for this file).
_TEST_MODEL_ANSWERED_GCP_MARKER = "cloud:gcp:"

#: 0110-034 (story bank): a posting-text marker for the reuse journeys,
#: ``GIGAI-TEST-MODEL: bank <bank_question_id> else <asked_question_id>``.
#: The fixture then answers like a model that follows assess.md's STORY BANK
#: paragraph: when the rendered prompt offers a bank line for
#: ``<bank_question_id>`` (``- <id> | ...``), the requirement is met, its
#: evidence cites ``Story bank <id>: <the line's answer>`` and nothing is
#: asked; otherwise it asks ``<asked_question_id>`` (the same fact, worded
#: differently). ``... else <asked_question_id> always`` asks even when the
#: bank line is there (a model that did not reuse it: the near-match path).
_TEST_MODEL_BANK_MARKER = re.compile(
    r"GIGAI-TEST-MODEL: bank (?P<bank>[a-z0-9._-]+:[a-z0-9._-]+) else (?P<asked>[a-z0-9._-]+:[a-z0-9._-]+)(?P<always> always)?"
)


def _test_model_bank_reply(prompt: str, marker: "re.Match[str]") -> dict[str, object]:
    bank_id, asked_id = marker.group("bank"), marker.group("asked")
    line = re.search(r"^- " + re.escape(bank_id) + r" \|(?:.*\|)? answer: (?P<answer>.*)$", prompt, re.MULTILINE)
    requirement = "Hands-on experience with the platform this role runs on"
    python_row = {"requirement": "Python", "class": "hard", "resume_evidence": ["Built Python services"], "status": "met"}
    if line is not None and not marker.group("always"):
        return {
            "verdict": "matched_above_threshold",
            "matrix": [
                python_row,
                {"requirement": requirement, "class": "askable", "resume_evidence": [f"Story bank {bank_id}: {line.group('answer')}"], "status": "met"},
            ],
            "suggestions": [],
            "questions": [],
            "not_a_match_reason": None,
        }
    return {
        "verdict": "pending_user_answers",
        "matrix": [python_row, {"requirement": requirement, "class": "askable", "resume_evidence": [], "status": "unclear"}],
        "suggestions": [],
        "questions": [{"question_id": asked_id, "question": "Do you have hands-on Google Cloud Platform experience?", "requirement": requirement}],
        "not_a_match_reason": None,
    }


#: P9b (v0.1.9): the first line of every ``api/extract.py`` prompt
#: (``extract.EXTRACT_PROMPT_HEADER`` -- the literal is repeated here so the
#: fixture never imports the route module; ``test_resume_extract_api.py``
#: asserts the two stay identical). Its presence means "this is a resume
#: extraction, not an assessment", and the fixture answers with a fixed
#: stack/seniority/titles JSON instead of the assess verdict below. The
#: garbage/sleep markers above still apply first (they sit in the resume
#: text, which the extraction prompt carries), so the extract journeys get
#: the 502/504 paths through the same existing branches.
TEST_MODEL_EXTRACT_MARKER = "GigAI Scout resume extraction"
#: Inside an extraction prompt only: the fixture answers HTTP 503 (the
#: local adapter raises, the route maps it to 503 ``model_unavailable``) --
#: the journey's "model unavailable" case without a live provider. Checked
#: only under the extraction branch, so no assess journey ever sees it.
TEST_MODEL_UNAVAILABLE_MARKER = "GIGAI-TEST-MODEL: unavailable"
TEST_MODEL_EXTRACT_REPLY: dict[str, object] = {
    "stack": ["Python", "PostgreSQL", "Kubernetes"],
    "seniority": "staff",
    "titles": ["Staff Software Engineer", "Staff Backend Engineer"],
}

#: Q3 (v0.1.9): the first line of every ``tailored_resume.py`` prompt
#: (``tailored_resume.TAILOR_PROMPT_HEADER`` -- the literal is repeated here so
#: the fixture never imports that module; ``test_tailored_resume.py`` asserts
#: the two stay identical). Its presence means "this is a tailoring, not an
#: assessment": the fixture answers with a structurally valid tailored resume
#: built FROM THE PROMPT ITSELF (the first ``R<n>: ...`` resume line it carries, and
#: the ``A cloud:gcp: ...`` answer when one is rendered), so the answer is
#: valid against whatever resume a journey imported. The garbage/sleep
#: markers above still apply first (they sit in the posting text, which the
#: tailor prompt carries), so the 502/504 paths reuse the existing branches.
TEST_MODEL_TAILOR_MARKER = "GigAI Scout tailored resume"
#: Inside a tailor prompt only (a journey puts it in the posting text): the
#: fixture answers with PLANTED FABRICATIONS -- a line stating a number its
#: cited source never states, a line borrowing a posting-only skill, and a
#: line citing a resume line past the end -- on BOTH attempts, so the
#: product's validator must reject the answer (502 ``model_output_invalid``)
#: and the eval's detector self-test can prove it catches each one.
TEST_MODEL_FABRICATE_MARKER = "GIGAI-TEST-MODEL: fabricate"
#: Q3 eval: the first line of ``tests/evals/fabrication_judge.md``; the
#: fixture judge finds every claim supported -- one verdict per numbered
#: ``CLAIM <n>:`` block the batched judge prompt carries (the offline harness
#: proves the judge is wired and parsed, never that a fake model can judge).
TEST_MODEL_JUDGE_MARKER = "GigAI Scout fabrication judge"
_TEST_MODEL_JUDGE_CLAIM = re.compile(r"^CLAIM (\d+):$", re.MULTILINE)
TEST_MODEL_FABRICATED_NUMBER_LINE = "Led a team of 8 engineers for 12 years."
TEST_MODEL_FABRICATED_TERM_LINE = "Deep Kubernetes and Terraform experience in production."
#: 0110-006: inside a tailor prompt only (a journey puts it in the posting
#: text): the fixture answers with WEAKER rewrites -- the synthetic
#: UAT-style pairs (``TEST_MODEL_LOSSY_REWRITES``) for every resume line that
#: states one, else the first listed line with a named word dropped -- each
#: with a reason anchored in the posting, plus one COPIED bullet when the
#: resume has another bullet, so journeys exercise the no-loss fallback.
TEST_MODEL_LOSSY_MARKER = "GIGAI-TEST-MODEL: lossy"
#: Synthetic UAT-style pairs (0110-006, 0110-014): resume sentence -> the weaker rewrite
#: the tailor produced.  Matched on the listed line without its bullet marker
#: and final period.
TEST_MODEL_LOSSY_REWRITES: dict[str, str] = {
    "Managed a group of 4 analysts supporting scheduling and billing systems end to end, owning the release calendar, vendor contact, and staff training across 2 hospitals":
        "Managed 4 analysts supporting scheduling and billing systems, with the release calendar, vendor contact, and staff training across 2 hospitals.",
    "Built the medication reconciliation (MRX) workflow handling admission and discharge lists end to end under HIPAA and OH state requirements":
        "Built a workflow for medication reconciliation on admission and discharge lists under HIPAA",
}


def _test_model_prompt_source(prompt: str, prefix: str) -> str | None:
    """The text after the first prompt line that starts with ``prefix`` (``"R1: "``, ``"A cloud:gcp: "``)."""

    for line in prompt.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


#: A numbered resume line in a tailor prompt (``R7: Built Python services``).
_TEST_MODEL_RESUME_LINE = re.compile(r"^R(\d+): (.*)$", re.MULTILINE)


def _test_model_posting(prompt: str) -> str:
    """The posting text a tailor prompt carries (after ``POSTING TEXT:`` inside the untrusted fence, P5)."""

    from ..untrusted_text import unfence_untrusted_posting

    fenced = unfence_untrusted_posting(prompt)
    start = fenced.find("POSTING TEXT:\n")
    return fenced[start + len("POSTING TEXT:\n") :] if start != -1 else ""


def _test_model_reason(kind: str, posting: str, words: str = "") -> dict[str, object]:
    """A reason anchored in the posting (0110-006): the first word of ``words``
    (5+ letters) the posting also states, else the posting's first words."""

    for word in re.findall(r"[A-Za-z]{5,}", words):
        found = re.search(rf"\b{re.escape(word)}\b", posting, re.IGNORECASE)
        if found:
            return {"kind": kind, "requirement": None, "posting_phrase": found.group(0)}
    phrase = " ".join(posting.split()[:4])[:60].strip()
    return {"kind": kind, "requirement": None, "posting_phrase": phrase or None}


def _test_model_bare(text: str) -> str:
    return re.sub(r"\A[-*•]\s+", "", text.strip()).rstrip(".").strip()


def _test_model_lossy_sections(prompt: str, posting: str, number: int, first_line: str) -> list[dict[str, object]]:
    """The lossy marker's extra sections (see ``TEST_MODEL_LOSSY_MARKER``)."""

    listed = [(int(match.group(1)), match.group(2).strip()) for match in _TEST_MODEL_RESUME_LINE.finditer(prompt)]
    bullets: list[dict[str, object]] = []
    heading = number
    for line_number, text in listed:
        weaker = TEST_MODEL_LOSSY_REWRITES.get(_test_model_bare(text))
        if weaker is None:
            continue
        if not bullets:
            heading = next((n for n, t in reversed(listed) if n < line_number and t.startswith(("**", "#"))), number)
        bullets.append({"text": weaker, "refs": [{"kind": "resume", "line": line_number}], "reason": _test_model_reason("surface", posting, weaker)})
    if bullets:
        cited = {bullet["refs"][0]["line"] for bullet in bullets}  # type: ignore[index]
        other = next((n for n, t in listed if n > heading and n not in cited and t.startswith(("- ", "* ", "• "))), None)
        if other is not None:
            bullets.append({"copy": other})
        return [{"heading": "experience", "entries": [{"heading_ref": [{"copy": heading}], "bullets": bullets}]}]
    words = first_line.split()
    named = next((index for index, word in enumerate(words) if index and word[:1].isupper()), None)
    weaker_line = " ".join(word for index, word in enumerate(words) if index != (named if named is not None else len(words) - 1))
    return [{"heading": "other", "lines": [{"text": weaker_line, "refs": [{"kind": "resume", "line": number}], "reason": _test_model_reason("surface", posting, weaker_line)}]}]


def _test_model_tailor_reply(prompt: str) -> dict[str, object]:
    """The fixture's tailored resume for ``prompt`` (see ``TEST_MODEL_TAILOR_MARKER``).

    Headerless (0110-003 P1), and built on the FIRST ``R<n>: `` line the
    prompt lists: the privacy strip withholds name/contact lines, so R1 may
    be missing and the first listed line keeps its original number.  Every
    rewritten line carries a reason anchored in the posting (0110-006), so
    the no-loss pass keeps it; the lossy marker adds weaker rewrites it must
    replace with the original lines.
    """

    first = _TEST_MODEL_RESUME_LINE.search(prompt)
    number = int(first.group(1)) if first else 1
    first_line = first.group(2).strip() if first else "Resume line one."
    posting = _test_model_posting(prompt)
    if TEST_MODEL_FABRICATE_MARKER in prompt:
        return {
            "sections": [
                {
                    "heading": "summary",
                    "lines": [
                        {"text": TEST_MODEL_FABRICATED_NUMBER_LINE, "refs": [{"kind": "resume", "line": number}]},
                        {"text": TEST_MODEL_FABRICATED_TERM_LINE, "refs": [{"kind": "resume", "line": number}]},
                        {"text": first_line, "refs": [{"kind": "resume", "line": 999}]},
                    ],
                }
            ],
        }
    sections: list[dict[str, object]] = [
        {"heading": "summary", "lines": [{"text": first_line, "refs": [{"kind": "resume", "line": number}], "reason": _test_model_reason("summary", posting)}]},
        {"heading": "skills", "lines": [{"copy": number}]},
    ]
    if TEST_MODEL_LOSSY_MARKER in prompt:
        sections[1:1] = _test_model_lossy_sections(prompt, posting, number, first_line)
    gcp_answer = _test_model_prompt_source(prompt, "A cloud:gcp: ")
    if gcp_answer:
        line = {"text": gcp_answer, "refs": [{"kind": "answer", "question_id": "cloud:gcp"}], "reason": _test_model_reason("answer", posting)}
        other = next((section for section in sections if section["heading"] == "other"), None)
        if other is None:
            sections.append({"heading": "other", "lines": [line]})
        else:
            other["lines"].append(line)  # type: ignore[attr-defined]
    return {"sections": sections}


#: SCOPE-ADD-3 C1 follow-up: the first line of every rank prompt
#: (``model_rank.PROMPT`` -- the literal is repeated here so the fixture never
#: imports the ranker; ``test_rank_journey.py`` asserts the two stay
#: identical). Its presence means "this is a ranking batch, not an
#: assessment": the fixture answers the strict id-keyed schema with a
#: DETERMINISTIC score read from the posting's own digest line -- the
#: ``fit <n>`` a journey puts in a posting title (60 when there is none) --
#: and names the digest's ``flags=`` as blockers, so an api-e2e ranking pass
#: yields real scores instead of failing open.
TEST_MODEL_RANK_MARKER = "You are pre-ranking job postings for ONE candidate"
TEST_MODEL_RANK_DEFAULT_SCORE = 60
_TEST_MODEL_RANK_FIT = re.compile(r"\bfit (\d{1,3})\b")


def _test_model_rank_reply(prompt: str) -> list[dict[str, object]]:
    """One strict rank object per digest line under ``POSTINGS (n):`` in ``prompt``."""

    items: list[dict[str, object]] = []
    in_postings = False
    for line in prompt.splitlines():
        if line.startswith("POSTINGS ("):
            in_postings = True
            continue
        if not in_postings:
            continue
        if not line.strip():
            break
        if " | " not in line:
            continue  # the untrusted fence's marker lines (P5)
        posting_id = line.split(" | ", 1)[0].strip()
        fit = _TEST_MODEL_RANK_FIT.search(line)
        score = min(int(fit.group(1)), 100) if fit else TEST_MODEL_RANK_DEFAULT_SCORE
        flags = line.rsplit("flags=", 1)[1].split(",") if "flags=" in line else []
        items.append(
            {
                "posting_id": posting_id,
                "score": score,
                "reasons": [f"fixture score {score}"],
                "blockers": [flag.strip() for flag in flags if flag.strip()],
            }
        )
    return items


def _test_model_judge_reply(prompt: str) -> dict[str, object]:
    """The fixture's batched judge answer: every ``CLAIM <n>:`` block in ``prompt`` supported."""

    numbers = [int(match.group(1)) for match in _TEST_MODEL_JUDGE_CLAIM.finditer(prompt)]
    # tailor-r3: a supported verdict carries severity null (hard / precision only when unsupported).
    return {"verdicts": [{"line": number, "supported": True, "unsupported_span": None, "severity": None} for number in numbers]}


def _test_model_handler(request: httpx.Request) -> httpx.Response:
    """Answer the three Ollama identity/chat calls without a model process.

    P5: two prompt-content markers let the api-e2e assess journeys exercise
    the failure paths through the same fixture -- a posting text carrying
    ``TEST_MODEL_GARBAGE_MARKER`` gets a non-JSON answer on every call (the
    core's one retry then fails ``model_output_invalid``); one carrying
    ``TEST_MODEL_SLEEP_MARKER`` sleeps ``TEST_MODEL_SLEEP_SECONDS`` first
    (past the journey's 1 s ``GIGAI_SCOUT_ASSESS_TIMEOUT_SECONDS``).

    P3: a THIRD marker, but this one is never placed by a caller -- it is
    ``assessment_core.render_assess_prompt``'s own rendering of a prior
    answer for ``cloud:gcp`` (``experience_answers``'s Q&A loop). Its
    presence in the prompt flips the fixture's answer from
    ``pending_user_answers`` (GCP unresolved) to ``matched_above_threshold``
    (GCP now resolved by the prior answer, per assess.md rule 6) -- this is
    what lets ``test_answers_journey.py`` prove a re-assessment's verdict
    actually changes after ``POST /api/answers``, through the SAME fixture
    every other assess journey uses, rather than a second bespoke one.
    """

    if request.method == "GET" and request.url.path == "/api/version":
        return httpx.Response(200, json={"version": "scout-test"}, request=request)
    if request.method == "GET" and request.url.path == "/api/tags":
        return httpx.Response(
            200,
            json={"models": [{"name": TEST_MODEL_NAME, "digest": TEST_MODEL_DIGEST}]},
            request=request,
        )
    if request.method == "POST" and request.url.path == "/api/chat":
        prompt = _test_model_prompt(request)
        if TEST_MODEL_SLEEP_MARKER in prompt:
            time.sleep(TEST_MODEL_SLEEP_SECONDS)
        if TEST_MODEL_GARBAGE_MARKER in prompt:
            return httpx.Response(
                200,
                json={
                    "model": TEST_MODEL_NAME,
                    "created_at": "2026-09-23T00:00:00Z",
                    "message": {"role": "assistant", "content": "I cannot produce JSON for this posting, sorry."},
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 10,
                    "eval_count": 12,
                },
                request=request,
            )
        if TEST_MODEL_EXTRACT_MARKER in prompt:
            if TEST_MODEL_UNAVAILABLE_MARKER in prompt:
                return httpx.Response(503, json={"error": "test fixture: model unavailable"}, request=request)
            return httpx.Response(
                200,
                json={
                    "model": TEST_MODEL_NAME,
                    "created_at": "2026-09-25T00:00:00Z",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(TEST_MODEL_EXTRACT_REPLY, separators=(",", ":")),
                    },
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 10,
                    "eval_count": 16,
                },
                request=request,
            )
        if TEST_MODEL_TAILOR_MARKER in prompt or TEST_MODEL_JUDGE_MARKER in prompt:
            reply: dict[str, object] = (
                _test_model_tailor_reply(prompt)
                if TEST_MODEL_TAILOR_MARKER in prompt
                else _test_model_judge_reply(prompt)
            )
            return httpx.Response(
                200,
                json={
                    "model": TEST_MODEL_NAME,
                    "created_at": "2026-09-25T00:00:00Z",
                    "message": {"role": "assistant", "content": json.dumps(reply, separators=(",", ":"))},
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 10,
                    "eval_count": 18,
                },
                request=request,
            )
        if TEST_MODEL_RANK_MARKER in prompt:
            return httpx.Response(
                200,
                json={
                    "model": TEST_MODEL_NAME,
                    "created_at": "2026-09-29T00:00:00Z",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(_test_model_rank_reply(prompt), separators=(",", ":")),
                    },
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 10,
                    "eval_count": 14,
                },
                request=request,
            )
        bank_marker = _TEST_MODEL_BANK_MARKER.search(prompt)
        if bank_marker is not None:
            return httpx.Response(
                200,
                json={
                    "model": TEST_MODEL_NAME,
                    "created_at": "2026-10-01T00:00:00Z",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(_test_model_bank_reply(prompt, bank_marker), separators=(",", ":")),
                    },
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 10,
                    "eval_count": 20,
                },
                request=request,
            )
        gcp_answered = _TEST_MODEL_ANSWERED_GCP_MARKER in prompt
        gcp_row = (
            {"requirement": "GCP", "class": "askable", "resume_evidence": ["Prior answer on file"], "status": "met"}
            if gcp_answered
            else {"requirement": "GCP", "class": "askable", "resume_evidence": [], "status": "unclear"}
        )
        return httpx.Response(
            200,
            json={
                "model": TEST_MODEL_NAME,
                "created_at": "2026-09-23T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            # P2 (v0.1.9): a verdict-carrying fixture answer, so
                            # the api-e2e/child-process journeys exercise the
                            # new S29 r1 shape end to end (structured
                            # questions -> pending_user_answers).
                            "verdict": "matched_above_threshold" if gcp_answered else "pending_user_answers",
                            "matrix": [
                                {
                                    "requirement": "Python",
                                    "class": "hard",
                                    "resume_evidence": ["Built Python services"],
                                    "status": "met",
                                },
                                gcp_row,
                            ],
                            "suggestions": ["Keep the service example."],
                            "questions": (
                                []
                                if gcp_answered
                                else [
                                    {
                                        "question_id": "cloud:gcp",
                                        "question": "Which platform would you prefer?",
                                        "requirement": "GCP",
                                    }
                                ]
                            ),
                            "not_a_match_reason": None,
                        },
                        separators=(",", ":"),
                    ),
                },
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 10,
                "eval_count": 20,
            },
            request=request,
        )
    return httpx.Response(404, json={"error": "test fixture route not found"}, request=request)


def _http_client() -> httpx.Client:
    transport = httpx.MockTransport(_test_provider_handler) if _test_http_enabled() else None
    return httpx.Client(
        timeout=_TIMEOUT,
        transport=transport,
        follow_redirects=False,
        trust_env=False,
    )


def _patch_test_model_transport(config: GigAIConfig) -> None:
    """Inject a MockTransport into B-2's real adapter factory for M1 only."""

    if os.environ.get(_TEST_MODEL_ENV) != "1":
        return
    from .. import proposal_execution as scout_proposal_execution
    from ...model_targets import resolve_model_target
    from ...adapters.factory import resolve_model_adapter as original

    current = getattr(scout_proposal_execution, "resolve_model_adapter")
    if getattr(current, "_scout_test_transport", False):
        return

    def resolve_with_test_transport(active: GigAIConfig, target_name: str, **kwargs: Any) -> object:
        resolved = resolve_model_target(active, target_name)
        overrides = dict(kwargs.pop("transport_overrides", {}) or {})
        overrides.setdefault(resolved.endpoint.name, httpx.MockTransport(_test_model_handler))
        return original(active, target_name, transport_overrides=overrides, **kwargs)

    setattr(resolve_with_test_transport, "_scout_test_transport", True)
    scout_proposal_execution.resolve_model_adapter = resolve_with_test_transport  # type: ignore[assignment]


def _present_bound(context: object, input: PresentInput, *, home_root: Path, target: Path) -> object:
    """Normalize I-2's pre-C-1 batch ref before calling the real C-1 node.

    I-2's current scheduler passes ``AcquireOutput.batch_ref`` (the durable
    acquisition input) here, while C-1 consumes the serialized ``AcquireOutput``
    at the runner's output path.  The run ID is authoritative, so this small
    integration adapter keeps the C-1 callable unchanged and points it at the
    authenticated runner artifact.

    0110-042: C-1 returns the run's acquire rows whole, text and all; what a
    run seals references them (identity, digest and the list's labels) and
    leaves the text in the run's acquire output, where the reads take it from.
    """

    run_id = getattr(context, "run_id", None)
    if not isinstance(run_id, str) or not run_id:
        return present_node(context, input, home_root=home_root, target=target).without_row_text()  # type: ignore[arg-type]
    normalized = PresentInput(
        f"runs/{run_id}/outputs/acquire.json",
        input.assessment_ref,
        input.node_receipts,
    )
    return present_node(context, normalized, home_root=home_root, target=target).without_row_text()  # type: ignore[arg-type]


def _assess_bound(
    context: object,
    input: object,
    *,
    home_root: Path,
    target: Path,
    config: GigAIConfig,
) -> object:
    """Point B-2 at the workpad-owned acquisition artifact.

    A-6's ``AcquireOutput.batch_ref`` names its public-import input, while
    B-2 consumes normalized ``PostingRow`` objects.  The runner's committed
    acquire output is the shared DTO handoff, so point B-2 at that artifact and
    make the path absolute without changing the frozen DTO or sealed input.
    """

    workpad_path = getattr(context, "workpad_path", None)
    run_id = getattr(context, "run_id", None)
    if isinstance(workpad_path, str) and isinstance(run_id, str) and run_id:
        input = replace(
            input,
            acquire_batch_ref=os.fspath(
                Path(workpad_path) / "runs" / run_id / "outputs" / "acquire.json"
            ),
        )
    # B-2 currently binds the model response to the full ``PostingRow`` DTO
    # before handing it to the frozen assessment parser, while that parser's
    # ``posting`` field is the narrower ``SelectedPosting`` DTO.  Keep the
    # host-bound identity authoritative and normalize only that parser edge.
    from .. import proposals as scout_proposals

    parser = scout_proposals.parse_assessment_proposal
    selected_postings = {
        item.normalized_url: item
        for item in getattr(input, "selected_postings", ())
    }
    selected_by_url = {
        normalized_url: item.to_json()
        for normalized_url, item in selected_postings.items()
    }

    def parse_bound(value: object) -> object:
        if isinstance(value, dict) and isinstance(value.get("posting"), dict):
            posting = value["posting"]
            normalized_url = posting.get("normalized_url")
            selected = selected_by_url.get(normalized_url)
            if selected is not None:
                value = {
                    **value,
                    "posting": selected,
                }
        return parser(value)  # type: ignore[arg-type]

    scout_proposals.parse_assessment_proposal = parse_bound  # type: ignore[assignment]
    try:
        output = assess_node(  # type: ignore[arg-type]
            context, input, home_root=home_root, target=target, config=config
        )
        # B-2 builds candidate rows from its normalized acquisition input and
        # therefore leaves their content digest unset.  The selected-posting
        # DTO already carries the authoritative acquisition digest; align the
        # candidate rows before the scheduler persists AssessOutput so the
        # frozen cross-field contract can validate the handoff.
        normalized_rows = tuple(
            replace(
                row,
                posting=replace(
                    row.posting,
                    content_sha256=selected_postings[row.posting.normalized_url].content_sha256,
                ),
            )
            if row.posting.normalized_url in selected_postings
            and row.posting.content_sha256
            != selected_postings[row.posting.normalized_url].content_sha256
            else row
            for row in output.candidate_rows
        )
        output = replace(output, candidate_rows=normalized_rows)
        # A-6 intentionally sends only new/edited (and, since uat-bug-009,
        # UNCHANGED-but-never-successfully-assessed) role matches to B-2. A
        # genuinely skippable UNCHANGED row (a successful prior assessment
        # already exists for it -- see market_acquisition._prior_assessments)
        # never reaches B-2's own candidate_rows/assessments/not_assessed at
        # all, yet C-1's PresentPayload partition (present_node/projection.py)
        # requires EVERY row in AcquireOutput.rows to appear in either
        # payload.assessments or payload.not_assessed. Reconcile here rather
        # than asking B-2 to reread acquisition state or changing its
        # standalone contract: any UNCHANGED acquire row missing from B-2's
        # own candidate_rows/assessments/not_assessed (a mixed run can have
        # some newly-assessed candidates alongside some genuinely-skipped
        # UNCHANGED rows, not just the all-skipped case) gets the same
        # deterministic NotAssessedReason.UNCHANGED label C-1 already used
        # before this reconciliation existed. uat-bug-009's carried-forward
        # result itself is surfaced separately, from acquire's own
        # AcquireOutput.carried_forward_assessments (present_api.py), not
        # through this reconciliation.
        if workpad_path and run_id:
            acquire_path = Path(workpad_path) / "runs" / run_id / "outputs" / "acquire.json"
            try:
                acquire = AcquireOutput.from_json(json.loads(acquire_path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                acquire = None
            if acquire is not None:
                accounted = {row.posting.normalized_url for row in output.candidate_rows} | {
                    row.posting.normalized_url for row in output.not_assessed
                } | {item.posting.normalized_url for item in output.assessments}
                missing_unchanged = tuple(
                    row for row in acquire.rows
                    if row.outcome.value == "unchanged" and row.posting.normalized_url not in accounted
                )
                if missing_unchanged:
                    output = replace(
                        output,
                        candidate_rows=(*output.candidate_rows, *missing_unchanged),
                        not_assessed=(
                            *output.not_assessed,
                            *(NotAssessedRow(row.posting, NotAssessedReason.UNCHANGED) for row in missing_unchanged),
                        ),
                    )
        # 0110-040: the rows added above are the acquire rows, text and all;
        # a row the run did not assess is sealed without its posting text
        # (the text stays in the run's acquire output and the board cache).
        return output.without_unassessed_text()
    finally:
        scout_proposals.parse_assessment_proposal = parser  # type: ignore[assignment]


def _register_nodes(
    *, home_root: Path, target: Path, install_worker_hook: bool
) -> tuple[RegisteredNode, ...]:
    home = Path(home_root).expanduser().resolve(strict=False)
    root = Path(target).expanduser().resolve(strict=False)
    os.environ[_HOME_ROOT_ENV] = os.fspath(home)

    graph_ids = _registry_graph_ids(home_root=home, target=root)
    keys = (
        ("acquire", ACQUIRE_CAPABILITY),
        ("assess", ASSESS_CAPABILITY),
        ("present", PRESENT_CAPABILITY),
    )
    existing = tuple(lookup(GRAPH_ID, GRAPH_VERSION, slug, capability) for slug, capability in keys)
    if all(item is not None for item in existing):
        # A caller may bind before the active graph-set pointer is available
        # (for example, an import/bootstrap probe) and call us again once a
        # sealed graph is selected.  Reuse the already-bound callables while
        # filling the execution aliases instead of leaving the child lookup
        # incomplete.
        for graph_id in graph_ids:
            if graph_id == GRAPH_ID:
                continue
            for (slug, capability), binding in zip(keys, existing):
                assert binding is not None
                alias = lookup(graph_id, GRAPH_VERSION, slug, capability)
                if alias is None:
                    register(
                        graph_id,
                        GRAPH_VERSION,
                        slug,
                        capability,
                        binding.declared_effects,
                        binding.callable,
                    )
        if install_worker_hook:
            _install_worker_hook()
        return tuple(item for item in existing if item is not None)
    if any(item is not None for item in existing):
        raise RuntimeError("find-jobs functional node registry is partially bound")

    config = load_config(home)
    _patch_test_model_transport(config)
    http_client = _http_client()
    watchlist = _BoundWatchlist(home, root)
    acquire = partial(
        acquire_node,
        http_client=http_client,
        exa=ExaSearchClient(),
        ats=ATSBoardClients(),
        watchlist=watchlist,
        home_root=home,
        target=root,
        # N11-C: a search reads the company index `gigai scout sources
        # update` wrote; it never fetches a board itself.
        boards_from=BOARDS_FROM_INDEX,
    )
    assess = partial(_assess_bound, home_root=home, target=root, config=config)
    present = partial(_present_bound, home_root=home, target=root)
    nodes = (
        register(GRAPH_ID, GRAPH_VERSION, "acquire", ACQUIRE_CAPABILITY, ACQUIRE_DECLARED_EFFECTS, acquire),
        register(GRAPH_ID, GRAPH_VERSION, "assess", ASSESS_CAPABILITY, ASSESS_DECLARED_EFFECTS, assess),
        register(GRAPH_ID, GRAPH_VERSION, "present", PRESENT_CAPABILITY, PRESENT_DECLARED_EFFECTS, present),
    )
    for graph_id in graph_ids:
        if graph_id == GRAPH_ID:
            continue
        register(graph_id, GRAPH_VERSION, "acquire", ACQUIRE_CAPABILITY, ACQUIRE_DECLARED_EFFECTS, acquire)
        register(graph_id, GRAPH_VERSION, "assess", ASSESS_CAPABILITY, ASSESS_DECLARED_EFFECTS, assess)
        register(graph_id, GRAPH_VERSION, "present", PRESENT_CAPABILITY, PRESENT_DECLARED_EFFECTS, present)
    _LIVE_BINDINGS[(home, root)] = nodes
    if install_worker_hook:
        _install_worker_hook()
    return nodes


def register_find_jobs_nodes(*, home_root: Path, target: Path) -> tuple[RegisteredNode, ...]:
    """Register the real acquire, assess, and present callables for one target."""

    return _register_nodes(home_root=home_root, target=target, install_worker_hook=True)


_FIND_JOBS_CAPABILITIES = frozenset(
    {ACQUIRE_CAPABILITY, ASSESS_CAPABILITY, PRESENT_CAPABILITY}
)


def _graph_is_find_jobs(graph: object) -> bool:
    """Detect a find-jobs Goal purely from the compiled graph payload.

    Every other graph must see this binding as a no-op (D11): the spawned
    child inspects only the ``graph`` dict already handed to it by the
    scheduler and never touches the workpad/registry for a graph that has no
    find-jobs Goal, so a target that is not bound to a GigAI project (or has
    no find-jobs config) can still run any other graph unaffected.
    """

    if not isinstance(graph, dict):
        return False
    goals = graph.get("goals")
    if not isinstance(goals, list):
        return False
    for goal in goals:
        if not isinstance(goal, dict):
            continue
        executor = goal.get("executor")
        capability = executor.get("capability") if isinstance(executor, dict) else None
        if capability in _FIND_JOBS_CAPABILITIES:
            return True
    return False


def _install_worker_hook() -> None:
    """Make ``launch_run(wait=False)`` import and bind us in its child."""

    from ... import run

    current = getattr(run, "_worker_entry")
    if getattr(current, "_scout_find_jobs_binding_hook", False):
        return
    setattr(_child_worker_entry, "_scout_find_jobs_binding_hook", True)
    run._worker_entry = _child_worker_entry


def _child_worker_entry(
    resolved: object,
    run_id: str,
    gig_version: int,
    graph: dict[str, object],
    target_before: dict[str, object],
    run_started_handoff_id: str,
    manifest_digest: str,
) -> None:
    """Spawn target: bind this process only for a find-jobs Goal, then call
    the original worker entry unconditionally.

    A run of any other graph must be a true no-op here: no workpad
    resolution, no registry mutation, and no requirement that find-jobs
    env/config exist for this target (D11: "every other graph keeps today's
    rule").
    """

    if _graph_is_find_jobs(graph):
        target_root = getattr(resolved, "target_root", None)
        if not isinstance(target_root, Path):
            raise RuntimeError("find-jobs child cannot resolve its target root")
        home_value = os.environ.get(_HOME_ROOT_ENV)
        if not home_value:
            raise RuntimeError("find-jobs child has no inherited home root")
        _register_nodes(
            home_root=Path(home_value),
            target=target_root,
            install_worker_hook=False,
        )

    from ...run import _worker_entry

    _worker_entry(
        resolved,
        run_id,
        gig_version,
        graph,
        target_before,
        run_started_handoff_id,
        manifest_digest,
    )


__all__ = [
    "GRAPH_ID",
    "GRAPH_VERSION",
    "TEST_MODEL_DIGEST",
    "TEST_MODEL_GARBAGE_MARKER",
    "TEST_MODEL_NAME",
    "TEST_MODEL_SLEEP_MARKER",
    "TEST_MODEL_SLEEP_SECONDS",
    "register_find_jobs_nodes",
]
