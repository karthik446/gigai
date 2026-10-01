"""0110-007: the API's own description -- one declarative route table and the OpenAPI 3.1 document built from it.

``server.py``'s ``do_GET``/``do_POST``/``do_PUT`` are hand-written ``if path
== ...`` chains, so the dispatcher cannot read this table. Instead the table
is the single source for everything that *describes* a route (the ``GET
/api`` index, ``/api/openapi.json``, ``/llms.txt`` and the allowed keys named
in an ``unknown_key`` 422), and ``tests/api_e2e/test_openapi_drift.py`` fails
when a route the dispatcher serves has no entry here, an entry has no
description or example, or an entry names a route the dispatcher no longer
serves.

Each route carries ``x-gigai-effect`` (``read`` changes nothing; ``write``
changes stored state) and ``x-gigai-external`` (``none``; ``model`` spends a
model call; ``network`` reads the public internet). A route that can do more
than one of these declares the costliest (``model`` over ``network`` over
``none``) and says when in its description.

Nothing here is read from the stores: the document is static for a given
gigai version, so agents can cache it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Literal

from ..contracts import NotAssessedReason

OPENAPI_VERSION = "3.1.0"
SPEC_PATH = "/api/openapi.json"
INDEX_PATH = "/api"
LLMS_PATH = "/llms.txt"

Where = Literal["path", "query", "body"]


@dataclass(frozen=True)
class Param:
    name: str
    where: Where
    type: str  # JSON Schema type: string | integer | boolean | object | array
    description: str
    required: bool = False
    enum: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteSpec:
    method: str
    path: str  # "/api/runs/{run_id}" for the parametric ones
    summary: str
    effect: Literal["read", "write"]
    external: Literal["none", "model", "network"]
    example: dict[str, object]  # a response body (or, for non-JSON routes, {"content_type": ...})
    schema_version: str | None = None
    params: tuple[Param, ...] = ()
    errors: tuple[tuple[int, str], ...] = ()
    request_example: dict[str, object] | None = None
    content_type: str = "application/json"
    host_checked: bool = False  # GET routes that return personal values also check Host
    description: str = ""
    tag: str = ""  # the docs grouping; set from _META below, one of TAGS
    # Body keys the handler accepts as a top-level object (drives unknown_key's allowed keys).
    open_body: bool = False  # True: the handler accepts keys this table does not enumerate

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)


_NOT_ASSESSED_REASONS = ", ".join(reason.value for reason in NotAssessedReason)
_STALE_NOTE = (
    "job_state may carry `assessment_stale: {reason: \"posting_changed\"}` (absent otherwise) when the assessment that gives the "
    "state was made on posting text that has since changed: the verdict still reads, and the job should be re-assessed."
)


def _p(name: str, type_: str, description: str) -> Param:
    return Param(name, "path", type_, description, required=True)


def _q(name: str, type_: str, description: str, *, required: bool = False, enum: tuple[str, ...] = ()) -> Param:
    return Param(name, "query", type_, description, required=required, enum=enum)


def _b(name: str, type_: str, description: str, *, required: bool = False, enum: tuple[str, ...] = ()) -> Param:
    return Param(name, "body", type_, description, required=required, enum=enum)


_RUN_ID = _p("run_id", "string", "A run id from GET /api/runs (run_...).")
_PROFILE_ID = _p("profile_id", "string", "A profile id from GET /api/profiles.")
_NOT_FOUND = (404, "not_found")
_NO_TARGET = (404, "target_unavailable")
_UNKNOWN_KEY = (422, "unknown_key")
_INVALID = (422, "invalid_value")
_WRONG_TYPE = (422, "wrong_type")
_MODEL_ERRORS = ((503, "model_unavailable"), (403, "model_denied"), (502, "model_output_invalid"))
_JOB_INPUT = (
    _b("job", "object", 'The posting: `{"job_url": "<https url>"}` or `{"text": "<pasted posting>"}`.', required=True),
    _b("resume", "object", "Which resume: omitted = the selected profile; else {\"profile_id\": \"...\"} or pasted resume text."),
    _b("model_target", "string", "Which model target answers (claude or codex); omitted = the configured one."),
    _b("schema_version", "string", "Optional; must equal the request schema version when present."),
)
_JOB_URL = "https://boards.greenhouse.io/acme/jobs/101"
_IDENTITY_KEY: dict[str, object] = {"profile_id": "prof_1", "job_identity": _JOB_URL}
_ROW_ERRORS = (_INVALID, _WRONG_TYPE, _UNKNOWN_KEY)

_BACKGROUND_SETTINGS_EXAMPLE: dict[str, object] = {
    "schema_version": "scout-background-settings:1",
    "readable": True,
    "settings": {
        "sources": {"auto_refresh": True},
        "tagging": {"model_enabled": True, "backfill_enabled": False, "tag_backfill_model": "configured"},
        "snapshot": {"enabled": True, "manifest_url": "https://github.com/karthik446/gigai/releases/download/scout-snapshot/manifest.json"},
    },
    "effective": {
        "sources": {"auto_refresh": True, "source": "default"},
        "tagging": {"model_enabled": True, "backfill_enabled": False, "tag_backfill_model": "configured", "source": "default"},
        "snapshot": {
            "enabled": True,
            "manifest_url": "https://github.com/karthik446/gigai/releases/download/scout-snapshot/manifest.json",
            "source": "default",
        },
    },
}
_BACKGROUND_SETTINGS_NOTE = (
    "`settings` is what the project's settings file says (a key it does not hold shows its default): the values a form edits. "
    "`effective` is what the background jobs act on now, each block with the `source` that decided it: default, setting, "
    "environment (an environment variable overrides the file) or settings_unreadable. `readable` is false when the file exists "
    "and cannot be read: every background job is then off. `sources.auto_refresh` off stops all background work, the model "
    "tagging included; `tagging.model_enabled` lets a model tag titles the rules cannot place, `tagging.backfill_enabled` also "
    "tags titles no active profile can reach, with `tagging.tag_backfill_model`; `snapshot.enabled` allows the metadata snapshot "
    "download from `snapshot.manifest_url`."
)

_ROUTE_ENTRIES: tuple[RouteSpec, ...] = (
    # --- discovery of the API itself -------------------------------------------------
    RouteSpec(
        "GET", "/api", "Index of every route (method, path, effect, one line) plus the spec and agent-guide links.",
        "read", "none",
        {"schema_version": "scout-api-index:1", "openapi": SPEC_PATH, "llms": LLMS_PATH, "routes": [{"method": "GET", "path": "/api/health", "summary": "Liveness.", "effect": "read", "external": "none"}]},
        schema_version="scout-api-index:1",
        description="Start here. Every route this server answers, with its effect and cost class; follow `openapi` for parameters, schemas and error codes.",
    ),
    RouteSpec(
        "GET", SPEC_PATH, "This API as an OpenAPI 3.1 document (generated from the server's route table).",
        "read", "none", {"openapi": OPENAPI_VERSION, "info": {"title": "GigAI Scout local API", "version": "0.1.10"}, "paths": {}},
        description="Every route with params, response schema_version, example, error codes, x-gigai-effect and x-gigai-external.",
    ),
    RouteSpec(
        "GET", LLMS_PATH, "Short plain-text guide for an agent: what this server is, how to call it, where the spec is.",
        "read", "none", {"content_type": "text/plain"}, content_type="text/plain",
        description="Localhost-only, Host-checked plain text. Writes need Content-Type: application/json.",
        host_checked=True,
    ),
    RouteSpec(
        "GET", "/api/jobs", "One job, complete: posting, rank, assessment + requirement matrix, open questions, tailored resumes, job state, action links.",
        "read", "none",
        {
            "schema_version": "scout-job-response:1",
            "job_identity": _JOB_URL,
            "posting": {"normalized_url": _JOB_URL, "title": "Software Engineer", "company": "Acme", "text": "..."},
            "runs": [{"run_id": "run_20260929T100000Z", "outcome": "new"}],
            "rank": {"normalized_url": _JOB_URL, "score": 82, "reasons": ["Python"], "blockers": [], "demoted": False, "unscored_reason": None},
            "rank_score": None,
            "work_mode_fit": None,
            "h1b": None,
            "assessments": [{"source": "run", "run_id": "run_20260929T100000Z", "profile_id": None, "verdict": "matched_above_threshold", "matrix": [], "suggestions": [], "questions": []}],
            "open_questions": [{"question_id": "auth:work_authorization", "question": "Are you authorized to work in the US?", "requirement": None}],
            "answers": [],
            "tailored_resumes": [{"profile_id": "prof_1", "job_identity": _JOB_URL, "company": "Acme", "title": "Software Engineer", "created_at": "2026-09-29T10:05:00Z", "updated_at": "2026-09-29T10:05:00Z", "links": {"pdf": {"method": "POST", "path": "/api/tailored-resumes/pdf", "body": {"profile_id": "prof_1", "job_identity": _JOB_URL}}, "line": {"method": "PUT", "path": "/api/tailored-resumes/lines", "body": {"profile_id": "prof_1", "job_identity": _JOB_URL, "updated_at": "2026-09-29T10:05:00Z", "line_id": "<L id from the resume>", "use": "original"}}}}],
            "job_state": {"state": "tailored", "since": "2026-09-29T10:05:00Z", "next_events": ["applied"]},
            "application_events": [],
            "links": {
                "self": {"method": "GET", "path": "/api/jobs?url=..."},
                "assess": {"method": "POST", "path": "/api/assess", "body": {"job": {"job_url": _JOB_URL}}},
                "tailor": {"method": "POST", "path": "/api/tailored-resumes", "body": {"job": {"job_url": _JOB_URL}}},
                "pdf": {"method": "POST", "path": "/api/tailored-resumes/pdf", "body": {"profile_id": "prof_1", "job_identity": _JOB_URL}},
                "mark_applied": {"method": "POST", "path": "/api/applications", "body": {"normalized_url": _JOB_URL, "event_kind": "applied"}},
                "run_posting": {"method": "GET", "path": "/api/runs/run_20260929T100000Z/posting?url=..."},
            },
        },
        schema_version="scout-job-response:1",
        params=(_q("url", "string", "The posting URL, raw or normalized (the UI's `#/jobs/<url>` route maps here).", required=True),),
        errors=(_INVALID, _UNKNOWN_KEY, _NOT_FOUND, _NO_TARGET, (403, "forbidden_origin")),
        host_checked=True,
        description=(
            "Read only; never calls a model or the network. Aggregates the newest run posting, its rank, every run or quick "
            "assessment of the job (with the requirement matrix), the questions still unanswered, stored tailored resumes, the job's "
            "state with the events it accepts next, and the action links. The UI route `#/jobs/<posting url>` maps to this route. "
            + _STALE_NOTE
        ),
    ),
    # --- health / config / setup -----------------------------------------------------
    RouteSpec("GET", "/api/health", "Liveness probe.", "read", "none", {"status": "ok"}),
    RouteSpec(
        "GET", "/api/config", "The find-jobs config, the pinned resume preview and the config digest a run request needs.",
        "read", "none", {"config": {}, "config_digest": "sha256:...", "resume": None}, errors=((404, "config_missing"),),
    ),
    RouteSpec(
        "GET", "/api/setup", "The saved preferences (the wizard's answers).", "read", "none",
        {"roles": ["Software Engineer"], "countries": ["US"]}, errors=((404, "prefs_missing"),),
    ),
    RouteSpec(
        "PUT", "/api/setup", "Save preferences and derive the find-jobs config from them.", "write", "none",
        {"roles": ["Software Engineer"], "countries": ["US"]},
        params=(
            _b("roles", "array", "Job titles to search for (non-empty).", required=True),
            _b("titles_to_avoid", "array", "Titles to skip."), _b("countries", "array", "Country codes."),
            _b("work_mode", "string", "remote | hybrid | onsite (as the wizard offers)."), _b("city", "string", "City for onsite/hybrid."),
            _b("visa_sponsorship_required", "boolean", "Default false."), _b("exclude_companies", "array", "Companies to skip."),
            _b("watch_companies", "array", "Companies to watch."), _b("company_stage_size", "string", "Stage/size preference."),
            _b("industries_include", "array", "Industries to include."), _b("industries_exclude", "array", "Industries to exclude."),
            _b("must_have_stack", "array", "Required stack."), _b("dealbreaker_stack", "array", "Stack to avoid."),
            _b("cadence_days", "integer", "Days between discovery runs."), _b("budget_usd_per_session", "number", "Spend cap per session."),
            _b("max_age_days", "integer", "Ignore postings older than this."), _b("profile_id", "string", "Save into this profile."),
            _b("model_target", "string", "Model target."),
        ),
        request_example={"roles": ["Software Engineer"], "countries": ["US"]},
        errors=((400, "invalid_value"), _NOT_FOUND),
    ),
    RouteSpec(
        "PUT", "/api/config/sources", "Turn the Exa source on or off.", "write", "none", {"sources": {"exa": True}},
        params=(_b("exa", "boolean", "true enables Exa (needs a key), false disables it.", required=True),),
        request_example={"exa": True}, errors=(_WRONG_TYPE, (404, "config_missing")),
    ),
    RouteSpec(
        "GET", "/api/secrets/status", "Which provider keys are set (never their values).", "read", "none",
        {"exa": {"present": False}},
    ),
    # --- runs ------------------------------------------------------------------------
    RouteSpec(
        "POST", "/api/run", "Start a find-jobs run (acquire, rank, assess up to the cap).", "write", "model",
        {"schema_version": "scout-find-jobs-run-response:1", "run_id": "run_20260929T100000Z", "status": "running", "node_receipts": []},
        schema_version="scout-find-jobs-run-response:1",
        params=(
            _b("schema_version", "string", "scout-find-jobs-run-request:1.", required=True),
            _b("consent", "object", "The consent envelope the UI sends.", required=True),
            _b("config_digest", "string", "config_digest from GET /api/config.", required=True),
            _b("selection_cap", "integer", "Postings to assess (1..50).", required=True),
            _b("selection_rule", "string", "Which postings the cap keeps.", required=True),
            _b("model_target", "string", "Model target.", required=True),
            _b("keywords", "array", "Optional full-text keywords for this one search: up to 20 phrases of at most 100 characters."),
        ),
        request_example={"schema_version": "scout-find-jobs-run-request:1", "config_digest": "sha256:...", "selection_cap": 10, "keywords": ["kubernetes"]},
        errors=(_INVALID, _WRONG_TYPE, _UNKNOWN_KEY, (404, "config_missing"), (409, "config_digest_mismatch")),
        description=(
            "Reads the boards over the network and spends model calls on assessment; returns as soon as the run is allocated. "
            "Poll GET /api/runs/{run_id}. `keywords` filter the postings the profile's titles matched, through the full-text index "
            "(title and description): a posting whose stored text matches none of them is dropped; one keyword is enough, each is "
            "matched as a phrase. A posting with no stored text cannot be checked: it is kept and counted. They never add postings "
            "the titles did not match. The run's sealed config carries them (`keywords`), and GET /api/runs/{run_id}/progress "
            "`boards.keywords` reports {terms, mode: filter, applied, reason, message, matched, dropped, text_not_checked}; with no "
            "text index on this machine `applied` is false, `reason` is no_text_index, text_index_unavailable or bad_query, and the "
            "search runs as if no keyword was given. `config_digest` is the one GET /api/config returned (keywords are not part of it)."
        ),
    ),
    RouteSpec(
        "GET", "/api/runs", "Every run, newest first, with counts.", "read", "none",
        {"schema_version": "scout-runs-list-response:1", "runs": [{"run_id": "run_20260929T100000Z", "created_at": "2026-09-29T10:00:00Z", "profile_id": "prof_1", "status": "succeeded", "counts": {"found": 40, "new": 12, "assessed": 10, "matched": 3}}]},
        schema_version="scout-runs-list-response:1",
        params=(_q("profile_id", "string", "Keep the runs of this profile."), _q("status", "string", "Keep the runs with this status."), _q("limit", "integer", "The newest N runs (1..500).")),
        errors=(_INVALID, _NOT_FOUND, _NO_TARGET),
    ),
    RouteSpec(
        "GET", "/api/runs/{run_id}", "One run's status, node receipts and progress.", "read", "none",
        {"run_id": "run_20260929T100000Z", "status": "succeeded"}, params=(_RUN_ID,), errors=(_NOT_FOUND,),
    ),
    RouteSpec(
        "GET", "/api/runs/{run_id}/progress", "A run's live progress (postings, boards, assessments); ?summary=1 drops posting text.", "read", "none",
        {"run_id": "run_20260929T100000Z", "boards": {}, "postings": []},
        params=(_RUN_ID, _q("summary", "string", "1 for the small form (no posting text).", enum=("0", "1"))), errors=(_INVALID, _UNKNOWN_KEY, _NOT_FOUND),
    ),
    RouteSpec(
        "GET", "/api/runs/{run_id}/results", "A run's rows with assessments; with ?limit=N a page of them, without text.", "read", "none",
        {"schema_version": "scout-find-jobs-run-results-response:1", "run_id": "run_20260929T100000Z", "total": 40, "limit": 50, "offset": 0, "payload": {"rows": []}},
        schema_version="scout-find-jobs-run-results-response:1",
        params=(_RUN_ID, _q("limit", "integer", "Page size 1..500; without it the whole run with posting text."), _q("offset", "integer", "Page start (needs limit).")),
        errors=(_INVALID, _UNKNOWN_KEY, _NOT_FOUND),
        description="Each row carries job_state. " + _STALE_NOTE,
    ),
    RouteSpec(
        "GET", "/api/runs/{run_id}/posting", "One posting of a run, complete with its text and assessment.", "read", "none",
        {"schema_version": "scout-find-jobs-run-posting:1", "run_id": "run_20260929T100000Z", "row": {}, "assessment": None, "not_assessed_reason": None, "carried_forward": None},
        schema_version="scout-find-jobs-run-posting:1",
        params=(_RUN_ID, _q("url", "string", "The posting's normalized_url.", required=True)), errors=(_INVALID, _UNKNOWN_KEY, _NOT_FOUND),
        description=(
            "For a job across runs and quick assessments use GET /api/jobs?url= instead. "
            f"not_assessed_reason is one of: {_NOT_ASSESSED_REASONS} (posting_incomplete: the requirement list looked cut off, so no verdict was given). "
            + _STALE_NOTE
        ),
    ),
    RouteSpec(
        "POST", "/api/runs/{run_id}/rank", "Rank a run's postings against the selected profile's resume (start / cancel / read).", "write", "model",
        {"state": "idle"}, params=(
            _RUN_ID, _b("start", "boolean", "true starts a ranking."), _b("cancel", "boolean", "true stops one."),
            _b("profile_id", "string", "Rank for this profile."), _b("cost_cap_usd", "number", "Spend cap."), _b("run_id", "string", "Must match the URL."),
            _b("schema_version", "string", "Optional request schema version."),
        ),
        request_example={"start": True}, errors=(_INVALID, _WRONG_TYPE, _UNKNOWN_KEY, _NOT_FOUND),
    ),
    RouteSpec(
        "POST", "/api/runs/{run_id}/assess-all", "Assess every unassessed posting of a finished run (start / cancel / read).", "write", "model",
        {"state": "idle", "counts": {}}, params=(_RUN_ID, _b("start", "boolean", "true starts."), _b("cancel", "boolean", "true stops.")),
        request_example={"start": True}, errors=(_UNKNOWN_KEY, _NOT_FOUND),
    ),
    RouteSpec(
        "POST", "/api/runs/{run_id}/posted-window",
        "Search the stored boards for postings of the last N days a finished run does not hold, and add them to it (read / search).",
        "write", "model",
        {"run_id": "run_1", "run_days": 10, "searched_days": 30, "choices": [7, 10, 30, 60], "added_total": 4, "skip_reason": None},
        params=(_RUN_ID, _b("days", "integer", "Search this many days back (1 to 365); omitted = read what was searched.")),
        request_example={"days": 30}, errors=(_INVALID, _WRONG_TYPE, _UNKNOWN_KEY, _NOT_FOUND),
        description=(
            "No board is downloaded: the search reads the company index and board cache on this machine. No run is created. "
            "Only the added postings are ranked and assessed (at most the run's assess cap); existing assessments and answers are untouched."
        ),
    ),
    # --- discovery -------------------------------------------------------------------
    RouteSpec(
        "POST", "/api/discover", "Start a company-discovery pass (Exa).", "write", "network", {"request_id": "disc_1"},
        errors=((409, "discovery_conflict"), (503, "discovery_unavailable")),
        description="Spends the Exa budget; returns immediately; read GET /api/discover/latest.",
    ),
    RouteSpec("GET", "/api/discover/latest", "The latest discovery pass and whether one is running.", "read", "none", {"running": False, "latest": None}),
    # --- profiles --------------------------------------------------------------------
    RouteSpec(
        "GET", "/api/profiles", "Every profile and which is selected.", "read", "none", {"profiles": [], "selected": None},
        errors=(_NOT_FOUND,),
    ),
    RouteSpec(
        "POST", "/api/profiles", "Create a profile.", "write", "none", {"profile": {"profile_id": "prof_1", "label": "Backend"}},
        params=(
            _b("label", "string", "Profile name.", required=True), _b("titles", "array", "Titles to search (non-empty).", required=True),
            _b("titles_to_avoid", "array", "Titles to skip."), _b("queries", "array", "Search queries (default: titles)."),
            _b("resume_record_id", "string", "Stored resume record."), _b("resume_revision_id", "string", "Stored resume revision."),
            _b("search_settings", "object", "This profile's own {location, work_mode, countries, max_age_days}; omitted = a copy of the default's, null = same as default."),
        ),
        request_example={"label": "Backend", "titles": ["Software Engineer"]}, errors=(_WRONG_TYPE, (400, "invalid_value"), _NOT_FOUND),
        description="Every profile but the default one has its own location, work mode, countries and posted window. The first profile is the default and uses the setup settings.",
    ),
    RouteSpec(
        "PUT", "/api/profiles/{profile_id}", "Update a profile.", "write", "none", {"profile": {"profile_id": "prof_1"}},
        params=(
            _PROFILE_ID, _b("label", "string", "Profile name.", required=True), _b("titles", "array", "Titles (non-empty).", required=True),
            _b("titles_to_avoid", "array", "Titles to skip."), _b("queries", "array", "Search queries."),
            _b("resume_record_id", "string", "Stored resume record."), _b("resume_revision_id", "string", "Stored resume revision."),
            _b("search_settings", "object", "Any of {location, work_mode, countries, max_age_days} to change; null = same as default."),
        ),
        request_example={"label": "Backend", "titles": ["Software Engineer"]}, errors=(_WRONG_TYPE, (400, "invalid_value"), (409, "scout_profile_default_search_settings"), _NOT_FOUND),
        description="search_settings on the default profile is a 409: it uses the setup settings (PUT /api/setup). An unknown key inside search_settings lists that object's allowed_keys.",
    ),
    RouteSpec(
        "POST", "/api/profiles/{profile_id}/archive", "Archive a profile, optionally moving its selection to another.", "write", "none", {"archived": "prof_1"},
        params=(_PROFILE_ID, _b("replacement_profile_id", "string", "Profile to select instead.")), request_example={}, errors=((400, "invalid_value"), _NOT_FOUND),
    ),
    RouteSpec(
        "POST", "/api/profiles/selection", "Select the profile that runs and assessments use.", "write", "none", {"selected": "prof_1"},
        params=(_b("profile_id", "string", "The profile to select.", required=True),), request_example={"profile_id": "prof_1"},
        errors=((400, "invalid_value"), _NOT_FOUND),
    ),
    # --- assess / answers / applications / tailoring ----------------------------------
    RouteSpec(
        "POST", "/api/assess", "Assess one job (URL or pasted text) against a resume: verdict, requirement matrix, questions.", "write", "model",
        {"schema_version": "scout-assess-response:1", "job": {"job_identity": _JOB_URL}, "result": {"verdict": "matched_above_threshold", "matrix": [], "suggestions": [], "questions": []}},
        schema_version="scout-assess-response:1", params=(*_JOB_INPUT, _b("preferences", "object", "Override the effective preferences."), _b("origin", "string", "quick_assess | job_page.")),
        request_example={"job": {"job_url": _JOB_URL}},
        errors=(*_ROW_ERRORS, (422, "job_input_invalid"), (502, "job_fetch_failed"), (504, "assess_timeout"), *_MODEL_ERRORS, _NO_TARGET),
        description="Synchronous: blocks for the model call (and a public fetch for job_url). Stores the assessment; read it back with GET /api/jobs?url=.",
    ),
    RouteSpec(
        "GET", "/api/assessments", "Stored quick assessments, newest first, each with its job_state.", "read", "none",
        {"schema_version": "scout-assessments-list-response:1", "items": []}, schema_version="scout-assessments-list-response:1",
        params=(_q("profile_id", "string", "Only this resume identity."), _q("verdict", "string", "Only this verdict.")), errors=((422, "bad_enum"), _NO_TARGET),
    ),
    RouteSpec(
        "POST", "/api/answers", "Answer an assessment question; with reassess the job is assessed again.", "write", "model",
        {"answers": []}, params=(
            _b("question_id", "string", "The question's id (`<category>:<value>`).", required=True), _b("answer", "string", "Your answer.", required=True),
            _b("reassess", "string", "A job identity to assess again with the answer."),
        ),
        request_example={"question_id": "auth:work_authorization", "answer": "Yes"},
        errors=(_UNKNOWN_KEY, _WRONG_TYPE, (422, "answer_invalid"), (422, "reassess_unavailable"), (404, "reassess_not_found"), _NO_TARGET),
        description="Storing the answer is local; the model runs only when `reassess` is given.",
    ),
    RouteSpec(
        "GET", "/api/answers", "Every stored answer.", "read", "none",
        {"answers": [{"question_id": "auth:work_authorization", "prompt": "Are you authorized?", "answer": "Yes", "record_id": "rec_1", "revision_id": "rev_1"}]},
        errors=(_NO_TARGET,),
    ),
    RouteSpec(
        "POST", "/api/applications", "Record an application event (applied, interview_scheduled, ...) for a job.", "write", "none",
        {"event": {"event_kind": "applied"}, "job_state": {"state": "applied", "since": "2026-09-29T10:00:00Z", "next_events": []}},
        params=(
            _b("normalized_url", "string", "The posting URL (or job_identity)."), _b("job_identity", "string", "Text identity for pasted jobs."),
            _b("event_kind", "string", "applied | interview_scheduled | offer_received | rejected | withdrawn.", required=True),
            _b("occurred_at", "string", "ISO time; default now."), _b("notes", "string", "Free text."),
        ),
        request_example={"normalized_url": _JOB_URL, "event_kind": "applied"}, errors=(_UNKNOWN_KEY, _INVALID, (409, "invalid_transition"), _NO_TARGET),
    ),
    RouteSpec(
        "GET", "/api/applications", "Every application event, by job.", "read", "none", {"applications": []}, errors=(_NO_TARGET,),
    ),
    RouteSpec(
        "POST", "/api/tailored-resumes", "Tailor the resume to one posting (markdown + structure, each line cited).", "write", "model",
        {"schema_version": "scout-tailor-response:1", "job": {"job_identity": _JOB_URL}, "markdown": "# ..."}, schema_version="scout-tailor-response:1",
        params=_JOB_INPUT[:4], request_example={"job": {"job_url": _JOB_URL}},
        errors=(*_ROW_ERRORS, (502, "job_fetch_failed"), (504, "tailor_timeout"), *_MODEL_ERRORS, _NO_TARGET),
        description="Synchronous: blocks for the model call (one retry on a rejected answer).",
    ),
    RouteSpec(
        "GET", "/api/tailored-resumes", "Stored tailored resumes, newest first.", "read", "none",
        {"schema_version": "scout-tailored-resumes-response:1", "items": []},
        schema_version="scout-tailored-resumes-response:1",
        params=(_q("profile_id", "string", "Only this resume identity."), _q("job_identity", "string", "Only this job.")), errors=(_NO_TARGET,),
        description="Carries resume-derived text (the product). For a job's ids and links use GET /api/jobs?url=.",
    ),
    RouteSpec(
        "PUT", "/api/tailored-resumes/lines", "Show the original or the rewrite of one line of a stored tailored resume.", "write", "none",
        {"schema_version": "scout-tailor-response:1", "job": {"job_identity": _JOB_URL}, "markdown": "# ..."}, schema_version="scout-tailor-response:1",
        params=(
            _b("profile_id", "string", "The resume identity.", required=True), _b("job_identity", "string", "The job identity.", required=True),
            _b("updated_at", "string", "The updated_at of the tailored resume you read; a newer tailoring answers 409.", required=True),
            _b("line_id", "string", "A line id (`L<n>`) from the tailored resume.", required=True),
            _b("use", "string", "Which version to show.", required=True, enum=("original", "rewritten")),
        ),
        request_example={"profile_id": "prof_1", "job_identity": _JOB_URL, "updated_at": "2026-09-29T10:05:00Z", "line_id": "L3", "use": "original"},
        errors=(_INVALID, (404, "tailored_resume_not_found"), (409, "tailored_resume_changed"), _NO_TARGET),
        description="Idempotent: choosing what is already shown changes nothing. The PDF and the markdown follow the choice; updated_at is unchanged.",
    ),
    RouteSpec(
        "POST", "/api/tailored-resumes/pdf", "Render the stored tailored resume as a PDF (binary).", "read", "none", {"content_type": "application/pdf"},
        params=(_b("profile_id", "string", "The resume identity.", required=True), _b("job_identity", "string", "The job identity.", required=True)),
        request_example=_IDENTITY_KEY, content_type="application/pdf", errors=(_INVALID, (404, "tailored_resume_not_found"), (500, "pdf_render_failed"), _NO_TARGET),
        description="Returns application/pdf with Content-Disposition: attachment; changes nothing.",
    ),
    RouteSpec(
        "GET", "/api/resume-display", "The saved PDF header settings and the suggestion to prefill them.", "read", "none",
        {"saved": False, "name": "", "contact": [], "titles": {}, "spacing_scale": 1.0, "auto_fit": True}, host_checked=True, errors=((403, "forbidden_origin"),),
        description="Returns personal values, so the Host header must match the bound server. spacing_scale and auto_fit read as 1.0 and true until saved.",
    ),
    RouteSpec(
        "PUT", "/api/resume-display", "Save the PDF display settings (name, contact line, per-profile titles, spacing).", "write", "none", {"saved": True},
        params=(
            _b("name", "string", "The name printed on the PDF."), _b("contact", "array", "Contact items {kind, value}."), _b("titles", "object", "Title per profile id."),
            _b("spacing_scale", "number", "The PDF spacing unit's scale, 0.7 to 1.4 (default 1.0); used when auto_fit is false."),
            _b("auto_fit", "boolean", "Pick the spacing scale that ends the content near a page boundary (default true)."),
        ),
        request_example={"name": "Kar Ohm", "contact": [], "titles": {}, "spacing_scale": 1.0, "auto_fit": True}, errors=(_UNKNOWN_KEY, _WRONG_TYPE, _INVALID),
        description="Keys left out keep their saved values. A spacing_scale outside 0.7..1.4 answers 422 invalid_value.",
    ),
    # --- resume ----------------------------------------------------------------------
    RouteSpec(
        "POST", "/api/resume/extract", "Extract search preferences from a resume with the model.", "write", "model", {"suggestions": {}},
        params=(
            _b("resume_text", "string", "Pasted resume text."), _b("profile_id", "string", "Use this profile's stored resume."),
            _b("resume_ref", "object", "A stored resume {record_id, revision_id, content_sha256}."), _b("model_target", "string", "Model target."),
        ),
        request_example={"resume_text": "..."}, errors=(_UNKNOWN_KEY, _INVALID, *_MODEL_ERRORS),
    ),
    RouteSpec(
        "POST", "/api/resume/check", "Check resume text for personal data before it is sent to a model (local, no model).", "read", "none", {"findings": []},
        params=(_b("resume_text", "string", "Pasted resume text (send exactly one of resume_text, resume_ref)."), _b("resume_ref", "object", "A stored resume {record_id, revision_id, content_sha256}.")),
        request_example={"resume_text": "..."}, errors=(_WRONG_TYPE, _INVALID),
        description="Local scan only; nothing is stored.",
    ),
    RouteSpec(
        "POST", "/api/resumes", "Store a resume (pasted text or a base64 file).", "write", "none", {"record_id": "rec_1", "revision_id": "rev_1"},
        params=(_b("text", "string", "Resume text (exclusive with file_name)."), _b("file_name", "string", "Uploaded file name."), _b("content_base64", "string", "Uploaded file, base64.")),
        request_example={"text": "..."}, errors=(_UNKNOWN_KEY, _WRONG_TYPE, (422, "resume_input_invalid"), (422, "resume_too_large")),
    ),
    # --- watchlist / sources ---------------------------------------------------------
    RouteSpec(
        "GET", "/api/watchlist", "The watched company boards.", "read", "none",
        {"schema_version": "scout-watchlist-response:1", "entries": []}, schema_version="scout-watchlist-response:1",
        params=(_q("summary", "string", "1 for counts only."), _q("limit", "integer", "Page size."), _q("offset", "integer", "Page start.")),
        errors=(_UNKNOWN_KEY, _INVALID, _NOT_FOUND),
    ),
    RouteSpec(
        "POST", "/api/watchlist", "Add a company board by its posting or board URL.", "write", "none",
        {"schema_version": "scout-watchlist-add-response:1", "created": True, "entry": {}}, schema_version="scout-watchlist-add-response:1",
        params=(_b("url", "string", "A board or posting URL on a supported ATS.", required=True),),
        request_example={"url": "https://boards.greenhouse.io/acme"}, errors=(_UNKNOWN_KEY, _INVALID, _NOT_FOUND),
    ),
    RouteSpec(
        "POST", "/api/sources/update", "Refresh the board catalog and index (\"Update sources\").", "write", "network", {"state": "running"},
        params=(
            _b("force", "boolean", "Start even if another update looks live."),
            _b("full_refresh", "boolean", "Check every board; by default boards checked recently are skipped."),
        ),
        request_example={}, errors=(_UNKNOWN_KEY, _WRONG_TYPE),
        description="Reads public boards; incremental by default; returns immediately; poll GET /api/sources/update.",
    ),
    RouteSpec(
        "GET", "/api/sources/update", "State of the last or running sources update.", "read", "none",
        {
            "schema_version": "scout-sources-update-status:1",
            "running": False,
            "update": None,
            "index": {"status": "ready", "needs_update": False, "message": None, "companies_indexed": 120, "last_checked_at": "2026-10-01T12:00:00.000Z", "stale_after_hours": 24.0},
            "background": {
                "auto_refresh": {"enabled": True, "source": "default", "active": True},
                "state": "waiting",
                "message": None,
                "in_progress": False,
                "trigger": "manual",
                "last_update": {"update_id": "sources_update_1", "status": "succeeded", "trigger": "manual", "started_at": "2026-10-01T11:50:00.000Z", "finished_at": "2026-10-01T12:00:00.000Z"},
                "next_tick_at": "2026-10-01T12:50:00.000Z",
                "interval_seconds": 3600.0,
                "tags": {"available": True, "titles": 5200, "with_function": 4400, "lacking_function": 800},
                "text": {"available": True, "postings": 9000, "with_text": 6100, "unchecked": 2900},
            },
            "snapshot": {
                "enabled": True,
                "setting_source": "default",
                "manifest_url": "https://github.com/karthik446/gigai/releases/download/scout-snapshot/manifest.json",
                "as_of": "2026-10-01T06:00:00Z",
                "source": "https://github.com/karthik446/gigai/releases/download/scout-snapshot/manifest.json",
                "kind": "full",
                "imported_at": "2026-10-01T11:49:00.000Z",
                "last_attempt_at": "2026-10-01T11:49:00.000Z",
                "last_result": "imported",
                "last_reason": None,
                "last_message": "Imported the snapshot as of 2026-10-01T06:00:00Z.",
                "counts": {"boards": 120, "postings": 9000, "tags": 5200, "boards_kept_local": 0, "boards_removed": 0, "postings_removed": 0},
            },
            "tags": {
                "available": True,
                "titles": 5200,
                "tagged_by_rules": 4400,
                "tagged_by_model": 600,
                "model_other": 50,
                "awaiting_model": 150,
                "setting": {"model_enabled": True, "backfill_enabled": False, "tag_backfill_model": "configured", "source": "default"},
                "models": {"demand": "ollama_local:llama3.1", "backfill": "ollama_local"},
                "queue": {
                    "setting": {"model_enabled": True, "backfill_enabled": False, "tag_backfill_model": "configured", "source": "default"},
                    "prompt_version": "tag-v1",
                    "batch_size": 50,
                    "batches_per_tick": 4,
                    "state": "drained",
                    "last_drain_at": "2026-10-01T12:01:00Z",
                    "last_drain": {"state": "drained", "batches": 4, "tagged": 200, "rejected": 0, "calls": 4},
                    "parked": 0,
                    "demand": {
                        "model": "ollama_local:llama3.1", "batches": 12, "calls": 12, "tagged": 600, "rejected": 0, "failures": 0,
                        "consecutive_failures": 0, "last_error": None, "last_error_at": None, "retry_after": None,
                    },
                    "backfill": {
                        "model": None, "batches": 0, "calls": 0, "tagged": 0, "rejected": 0, "failures": 0,
                        "consecutive_failures": 0, "last_error": None, "last_error_at": None, "retry_after": None,
                    },
                },
            },
            "text_index": {"available": True, "postings_with_text": 6100, "unchecked": 2900},
            "refresh": {
                "enabled": True,
                "state": "waiting",
                "in_progress": False,
                "trigger": "manual",
                "last_updated_at": "2026-10-01T12:00:00.000Z",
                "last_updated_minutes_ago": 12,
                "next_tick_at": "2026-10-01T12:50:00.000Z",
                "next_tick_in_minutes": 38,
            },
        },
        schema_version="scout-sources-update-status:1",
        description=(
            "`update` is the running or last update's snapshot (null when none ever ran): `trigger` is manual or auto (the hourly "
            "background refresh), `failures` counts boards that did not answer by code, `stores` counts what the update wrote to the "
            "tag store and the text index. `index` says whether a search can read the stored postings. `background` is the hourly "
            "refresh and the two stores: `auto_refresh` {enabled, source: default|setting|environment|settings_unreadable, active: a "
            "refresh thread runs in this server}; `state` is disabled, inactive, needs_first_update (run Update sources once: the "
            "background refresh never fills an empty index), running, waiting or due; `in_progress` and `trigger` describe the live "
            "update, `last_update` the last finished one; `next_tick_at` is null unless a tick is scheduled; `tags` counts stored "
            "titles and those still lacking a function; `text.unchecked` counts postings with no stored text, which a text search "
            "cannot match. `snapshot` is the downloaded metadata snapshot (titles, locations, links, title tags and board "
            "validators; never descriptions): `enabled` and `setting_source` (default|setting|environment|settings_unreadable) say "
            "whether it may be downloaded, `as_of` when the one in use was built (null: none imported), `kind` full or delta, "
            "`last_result` imported, up_to_date, skipped, refused or failed, `last_reason` why nothing was imported (offline, "
            "not_published, local_fresher, checked_recently, digest_mismatch, ...), `counts` what the last import wrote. "
            "`tags` counts the stored titles: `tagged_by_rules` and `tagged_by_model` have a function, `model_other` are titles a "
            "model looked at and could not place, `awaiting_model` still wait for a model (it reaches 0 when the queue is done); "
            "`setting` is the tagging setting in effect, `models` the model each lane asks (demand: titles the active profiles can "
            "reach; backfill: the rest; null when none is configured), `queue` the model queue of this server (null when it runs no "
            "refresh thread): its last drain and, per lane, calls, `failures`, `last_error` and `retry_after` (when a failed lane "
            "tries again). `text_index` counts postings a keyword search can check (`postings_with_text`) and cannot (`unchecked`). "
            "`refresh` is the strip's line: `enabled`, `state` and `trigger` as in `background`, `last_updated_at` with "
            "`last_updated_minutes_ago` (null before the first update and while one runs), `next_tick_at` with "
            "`next_tick_in_minutes` (null unless a check is scheduled). Reading this makes no request."
        ),
    ),
    # --- settings ----------------------------------------------------------------------
    RouteSpec(
        "GET", "/api/settings/background", "The background settings: hourly refresh, model tagging, snapshot download.", "read", "none",
        _BACKGROUND_SETTINGS_EXAMPLE,
        schema_version="scout-background-settings:1",
        errors=(_NO_TARGET,),
        description=_BACKGROUND_SETTINGS_NOTE,
    ),
    RouteSpec(
        "PUT", "/api/settings/background", "Change background settings.", "write", "none",
        _BACKGROUND_SETTINGS_EXAMPLE,
        schema_version="scout-background-settings:1",
        params=(
            _b("sources", "object", "{auto_refresh: boolean}: the hourly background refresh of the sources."),
            _b("tagging", "object", "{model_enabled: boolean, backfill_enabled: boolean, tag_backfill_model: configured|haiku|openai}."),
            _b("snapshot", "object", "{enabled: boolean, manifest_url: an http(s) URL, or null for the default location}."),
        ),
        request_example={"sources": {"auto_refresh": False}},
        errors=(_WRONG_TYPE, _UNKNOWN_KEY, _INVALID, (422, "bad_enum"), _NO_TARGET, (409, "settings_unreadable")),
        description=(
            "Send only the keys to change; at least one. Every other key of the project's settings.json is kept, and the file is "
            "replaced in one step. The refresh thread reads the file at every look and is woken by this call, so turning "
            "`sources.auto_refresh` off stops the hourly refresh and the model tagging at once (an update already running "
            "finishes). 409 settings_unreadable: the stored file is not one Scout can read; it is left as it is. The answer is the "
            "GET body after the change. " + _BACKGROUND_SETTINGS_NOTE
        ),
    ),
)

TAGS: tuple[str, ...] = ("Agents and meta", "Runs", "Jobs", "Assessment", "Tailored resumes", "Profiles and resume", "Sources", "Settings")

# (summary <= 80 chars, imperative; tag). The long sentence written on each entry above becomes the
# start of its description, so the docs page shows a short title and the full explanation.
_META: dict[tuple[str, str], tuple[str, str]] = {
    ("GET", "/api"): ("List every route", "Agents and meta"),
    ("GET", SPEC_PATH): ("Get the OpenAPI document", "Agents and meta"),
    ("GET", LLMS_PATH): ("Get the agent guide", "Agents and meta"),
    ("GET", "/api/health"): ("Check the server is alive", "Agents and meta"),
    ("GET", "/api/jobs"): ("Get everything known about one job", "Jobs"),
    ("GET", "/api/config"): ("Get the find-jobs config", "Settings"),
    ("GET", "/api/setup"): ("Get the saved preferences", "Settings"),
    ("PUT", "/api/setup"): ("Save preferences and derive the config", "Settings"),
    ("PUT", "/api/config/sources"): ("Turn the Exa source on or off", "Sources"),
    ("GET", "/api/secrets/status"): ("Show which provider keys are set", "Settings"),
    ("POST", "/api/run"): ("Start a find-jobs run", "Runs"),
    ("GET", "/api/runs"): ("List runs", "Runs"),
    ("GET", "/api/runs/{run_id}"): ("Get one run's status", "Runs"),
    ("GET", "/api/runs/{run_id}/progress"): ("Get a run's live progress", "Runs"),
    ("GET", "/api/runs/{run_id}/results"): ("List a run's results", "Runs"),
    ("GET", "/api/runs/{run_id}/posting"): ("Get one posting of a run", "Runs"),
    ("POST", "/api/runs/{run_id}/rank"): ("Rank a run's postings", "Runs"),
    ("POST", "/api/runs/{run_id}/assess-all"): ("Assess every posting of a run", "Runs"),
    ("POST", "/api/runs/{run_id}/posted-window"): ("Find older postings for a run", "Runs"),
    ("POST", "/api/discover"): ("Start a company-discovery pass", "Sources"),
    ("GET", "/api/discover/latest"): ("Get the latest discovery pass", "Sources"),
    ("GET", "/api/profiles"): ("List profiles", "Profiles and resume"),
    ("POST", "/api/profiles"): ("Create a profile", "Profiles and resume"),
    ("PUT", "/api/profiles/{profile_id}"): ("Update a profile", "Profiles and resume"),
    ("POST", "/api/profiles/{profile_id}/archive"): ("Archive a profile", "Profiles and resume"),
    ("POST", "/api/profiles/selection"): ("Select the active profile", "Profiles and resume"),
    ("POST", "/api/assess"): ("Assess one job against a resume", "Assessment"),
    ("GET", "/api/assessments"): ("List stored assessments", "Assessment"),
    ("POST", "/api/answers"): ("Answer an assessment question", "Assessment"),
    ("GET", "/api/answers"): ("List stored answers", "Assessment"),
    ("POST", "/api/applications"): ("Record an application event", "Jobs"),
    ("GET", "/api/applications"): ("List application events", "Jobs"),
    ("POST", "/api/tailored-resumes"): ("Tailor the resume to one posting", "Tailored resumes"),
    ("GET", "/api/tailored-resumes"): ("List tailored resumes", "Tailored resumes"),
    ("PUT", "/api/tailored-resumes/lines"): ("Keep the original or the rewrite of one line", "Tailored resumes"),
    ("POST", "/api/tailored-resumes/pdf"): ("Render a tailored resume as a PDF", "Tailored resumes"),
    ("GET", "/api/resume-display"): ("Get the PDF header settings", "Tailored resumes"),
    ("PUT", "/api/resume-display"): ("Save the PDF header settings", "Tailored resumes"),
    ("POST", "/api/resume/extract"): ("Extract search preferences from a resume", "Profiles and resume"),
    ("POST", "/api/resume/check"): ("Check resume text for personal data", "Profiles and resume"),
    ("POST", "/api/resumes"): ("Store a resume", "Profiles and resume"),
    ("GET", "/api/watchlist"): ("List watched company boards", "Sources"),
    ("POST", "/api/watchlist"): ("Watch a company board", "Sources"),
    ("POST", "/api/sources/update"): ("Refresh the board catalog", "Sources"),
    ("GET", "/api/sources/update"): ("Get the board refresh status", "Sources"),
    ("GET", "/api/settings/background"): ("Get the background settings", "Settings"),
    ("PUT", "/api/settings/background"): ("Change the background settings", "Settings"),
}


def _finish(route: RouteSpec) -> RouteSpec:
    summary, tag = _META[route.key]
    assert len(summary) <= 80 and tag in TAGS, route.key
    detail = route.summary if route.summary.endswith((".", "?", "!")) else route.summary + "."
    description = f"{detail} {route.description}".strip()
    return replace(route, summary=summary, tag=tag, description=description)


ROUTES: tuple[RouteSpec, ...] = tuple(_finish(route) for route in _ROUTE_ENTRIES)


_BY_KEY: dict[tuple[str, str], RouteSpec] = {route.key: route for route in ROUTES}


def _segments_match(template: str, path: str) -> bool:
    want, got = template.split("/"), path.split("/")
    return len(want) == len(got) and all(
        (w.startswith("{") and w.endswith("}") and g != "") or w == g for w, g in zip(want, got)
    )


def route_for(method: str, path: str) -> RouteSpec | None:
    """The table entry the concrete request ``method path`` belongs to, or ``None``."""

    exact = _BY_KEY.get((method, path))
    if exact is not None:
        return exact
    for route in ROUTES:
        if route.method == method and "{" in route.path and _segments_match(route.path, path):
            return route
    return None


def allowed_keys(route: RouteSpec) -> list[str]:
    """The keys a caller may send: query keys for GET, body keys otherwise."""

    where = "query" if route.method == "GET" else "body"
    return sorted(param.name for param in route.params if param.where == where)


_ALLOWED_SUFFIX = re.compile(r" \(allowed: ([^()]*)\)$")


def with_allowed_keys(method: str, path: str, payload: dict[str, object]) -> dict[str, object]:
    """Name the allowed keys in an ``unknown_key`` error (and a profile/setup ``field_errors._`` one).

    Additive: ``error.allowed_keys`` is a new field and ``" (allowed: ...)"`` is appended
    to the message that already listed the unknown ones. A nested-object ``unknown_key``
    already carries its own object's ``" (allowed: ...)"`` suffix from the contract parser;
    it keeps that message and gains ``allowed_keys`` parsed from it (the route's top-level
    keys would be the wrong list). A top-level one has the parser's suffix replaced by the route's.
    """

    error = payload.get("error")
    if not isinstance(error, dict):
        return payload
    message = error.get("message")
    field_errors = error.get("field_errors")
    top_level_unknown = isinstance(field_errors, dict) and isinstance(field_errors.get("_"), str) and field_errors["_"].startswith("unknown field(s)")
    own = _ALLOWED_SUFFIX.search(message) if isinstance(message, str) else None
    is_unknown = error.get("code") == "unknown_key" and isinstance(message, str)
    top_level = is_unknown and (message.startswith("unknown ") or message.startswith(("assess_request ", "tailor_request ", "rank_request ", "run_request ")))  # type: ignore[union-attr]
    if is_unknown and not top_level:
        if own is None:
            return payload
        return {**payload, "error": {**error, "allowed_keys": own.group(1).split(", ")}}
    route = route_for(method, path)
    if route is None or route.open_body:
        return payload
    keys = allowed_keys(route)
    if not keys:
        return payload
    if is_unknown:
        base = message[: own.start()] if own else message  # type: ignore[index]
        return {**payload, "error": {**error, "message": f"{base} (allowed: {', '.join(keys)})", "allowed_keys": keys}}
    if top_level_unknown:
        note = field_errors["_"]  # type: ignore[index]
        return {**payload, "error": {**error, "field_errors": {**field_errors, "_": f"{note} (allowed: {', '.join(keys)})"}, "allowed_keys": keys}}  # type: ignore[dict-item]
    return payload


# --- the documents ------------------------------------------------------------------


def index_document() -> dict[str, object]:
    return {
        "schema_version": "scout-api-index:1",
        "openapi": SPEC_PATH,
        "llms": LLMS_PATH,
        "routes": [
            {"method": route.method, "path": route.path, "summary": route.summary, "effect": route.effect, "external": route.external}
            for route in ROUTES
        ],
    }


def _schema_for(param: Param) -> dict[str, object]:
    schema: dict[str, object] = {"type": param.type}
    if param.enum:
        schema["enum"] = list(param.enum)
    return schema


def _response_schema(route: RouteSpec) -> dict[str, object]:
    if route.content_type != "application/json":
        return {"type": "string", "format": "binary" if route.content_type == "application/pdf" else "plain"}
    schema: dict[str, object] = {"type": "object", "additionalProperties": True}
    if route.schema_version:
        schema["properties"] = {"schema_version": {"const": route.schema_version}}
        schema["required"] = ["schema_version"]
    return schema


def _operation(route: RouteSpec) -> dict[str, object]:
    operation: dict[str, object] = {
        "operationId": _operation_id(route),
        "summary": route.summary,
        "tags": [route.tag],
        "description": route.description or route.summary,
        "x-gigai-effect": route.effect,
        "x-gigai-external": route.external,
        "parameters": [
            {"name": p.name, "in": p.where, "required": p.required, "description": p.description, "schema": _schema_for(p)}
            for p in route.params
            if p.where in ("path", "query")
        ],
    }
    if route.host_checked:
        operation["x-gigai-host-checked"] = True
    body_params = [p for p in route.params if p.where == "body"]
    if route.method in ("POST", "PUT") and (body_params or route.open_body or route.request_example is not None):
        body_schema: dict[str, object] = {
            "type": "object",
            "properties": {p.name: {**_schema_for(p), "description": p.description} for p in body_params},
            "required": [p.name for p in body_params if p.required],
            "additionalProperties": route.open_body,
        }
        media: dict[str, object] = {"schema": body_schema}
        if route.request_example is not None:
            media["example"] = route.request_example
        operation["requestBody"] = {"required": bool(body_schema["required"]), "content": {"application/json": media}}
    ok_media: dict[str, object] = {"schema": _response_schema(route)}
    if route.content_type == "application/json":
        ok_media["example"] = route.example
    responses: dict[str, object] = {"200": {"description": "Success.", "content": {route.content_type: ok_media}}}
    for status, code in route.errors:
        entry = responses.setdefault(str(status), {"description": "", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}, "example": {"error": {"code": code, "message": "..."}}}}})
        description = str(entry["description"])  # type: ignore[index]
        entry["description"] = f"{description}, {code}" if description else code  # type: ignore[index]
    if route.method in ("POST", "PUT"):
        responses.setdefault("415", {"description": "unsupported_media_type: writes need Content-Type: application/json.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}})
    responses.setdefault("403", {"description": "forbidden / forbidden_origin: loopback peer and matching Host/Origin only.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}})
    operation["responses"] = responses
    return operation


def _operation_id(route: RouteSpec) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", route.path.lower()).strip("_") or "root"
    return f"{route.method.lower()}_{slug}"


def openapi_document(*, version: str = "0.1.10") -> dict[str, object]:
    paths: dict[str, dict[str, object]] = {}
    for route in ROUTES:
        paths.setdefault(route.path, {})[route.method.lower()] = _operation(route)
    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": "GigAI Scout local API",
            "version": version,
            "description": (
                "Loopback-only HTTP API of `gigai scout`. Writes need Content-Type: application/json and a Host of the bound "
                "127.0.0.1/localhost port. Errors are {\"error\": {\"code\", \"message\"}}. x-gigai-effect says whether a route "
                "changes stored state; x-gigai-external says whether it spends a model call or reads the network."
            ),
        },
        "servers": [{"url": "http://127.0.0.1:8765"}],
        "tags": [{"name": tag} for tag in TAGS],
        "paths": paths,
        "components": {
            "schemas": {
                "Error": {
                    "type": "object",
                    "required": ["error"],
                    "properties": {
                        "error": {
                            "type": "object",
                            "required": ["code", "message"],
                            "properties": {
                                "code": {"type": "string"},
                                "message": {"type": "string"},
                                "allowed_keys": {"type": "array", "items": {"type": "string"}},
                            },
                            "additionalProperties": True,
                        }
                    },
                }
            }
        },
    }


def llms_text() -> str:
    return (
        "# GigAI Scout local API\n\n"
        "A loopback JSON API for a personal job search (find jobs, assess, tailor a resume, track applications).\n\n"
        "- Start at GET /api (every route, its effect, and its cost class).\n"
        "- Full spec: GET /api/openapi.json (OpenAPI 3.1: params, examples, error codes; x-gigai-effect read|write, x-gigai-external none|model|network).\n"
        "- One job, everything known about it: GET /api/jobs?url=<posting url> (read only, no model calls). The UI's #/jobs/<url> is this route.\n"
        "- Writes (POST/PUT) need Content-Type: application/json. Host must be 127.0.0.1:<port> or localhost:<port>; this server only answers loopback peers.\n"
        "- Errors are {\"error\": {\"code\", \"message\"}}; an unknown_key 422 lists allowed_keys.\n"
        "- Routes marked x-gigai-external model spend a model call (assess, tailor, rank, run); network reads the public internet. Prefer read routes first.\n"
        "- Tailored resumes: POST /api/tailored-resumes, then POST /api/tailored-resumes/pdf {profile_id, job_identity} for the PDF; PUT /api/tailored-resumes/lines picks the original or the rewrite of one line.\n"
    )


# --- a lightweight OpenAPI 3.1 structure check --------------------------------------

_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


def validate_document(doc: dict[str, object]) -> list[str]:
    """Structural problems in ``doc`` as an OpenAPI 3.1 document (empty when none).

    A hand-rolled check of the parts an agent relies on, so the suite needs no
    new dependency: the version, ``info``, path keys, operation ids (unique),
    parameters (``name``/``in``/``schema``; path parameters required and present
    in the path template), request bodies, responses (status keys, content) and
    that every ``$ref`` resolves inside the document.
    """

    problems: list[str] = []
    if not str(doc.get("openapi", "")).startswith("3.1"):
        problems.append("openapi must be 3.1.x")
    info = doc.get("info")
    if not isinstance(info, dict) or not isinstance(info.get("title"), str) or not isinstance(info.get("version"), str):
        problems.append("info needs title and version strings")
    paths = doc.get("paths")
    if not isinstance(paths, dict) or not paths:
        problems.append("paths must be a non-empty object")
        return problems
    seen_ids: set[str] = set()
    for path, item in paths.items():
        if not isinstance(path, str) or not path.startswith("/"):
            problems.append(f"path {path!r} must start with /")
            continue
        if not isinstance(item, dict) or not item:
            problems.append(f"{path}: path item must be a non-empty object")
            continue
        templated = set(re.findall(r"\{([^}]+)\}", path))
        for method, operation in item.items():
            where = f"{method.upper()} {path}"
            if method not in _HTTP_METHODS or not isinstance(operation, dict):
                problems.append(f"{where}: not an operation")
                continue
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or operation_id in seen_ids:
                problems.append(f"{where}: operationId missing or duplicated")
            else:
                seen_ids.add(operation_id)
            declared_path_params: set[str] = set()
            for param in operation.get("parameters", []):
                if not isinstance(param, dict) or param.get("in") not in ("path", "query", "header", "cookie") or not isinstance(param.get("name"), str) or not isinstance(param.get("schema"), dict):
                    problems.append(f"{where}: malformed parameter {param!r}")
                    continue
                if param["in"] == "path":
                    declared_path_params.add(param["name"])
                    if param.get("required") is not True:
                        problems.append(f"{where}: path parameter {param['name']} must be required")
            if declared_path_params != templated:
                problems.append(f"{where}: path parameters {sorted(declared_path_params)} do not match template {sorted(templated)}")
            body = operation.get("requestBody")
            if body is not None and (not isinstance(body, dict) or not isinstance(body.get("content"), dict) or not body["content"]):
                problems.append(f"{where}: requestBody needs content")
            responses = operation.get("responses")
            if not isinstance(responses, dict) or not responses:
                problems.append(f"{where}: responses missing")
                continue
            for status, response in responses.items():
                if not (status == "default" or (len(status) == 3 and status.isdigit())):
                    problems.append(f"{where}: bad response key {status!r}")
                if not isinstance(response, dict) or not isinstance(response.get("description"), str):
                    problems.append(f"{where}: response {status} needs a description")
            if "200" not in responses:
                problems.append(f"{where}: no 200 response")
    components = doc.get("components", {})
    refs = _collect_refs(doc)
    for ref in refs:
        node: object = doc
        for part in ref.removeprefix("#/").split("/"):
            node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            problems.append(f"unresolved $ref {ref}")
    if not isinstance(components, dict):
        problems.append("components must be an object")
    return problems


def _collect_refs(node: object) -> set[str]:
    refs: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                refs.add(value)
            else:
                refs |= _collect_refs(value)
    elif isinstance(node, list):
        for value in node:
            refs |= _collect_refs(value)
    return refs


__all__ = [
    "INDEX_PATH",
    "LLMS_PATH",
    "OPENAPI_VERSION",
    "ROUTES",
    "SPEC_PATH",
    "Param",
    "RouteSpec",
    "allowed_keys",
    "index_document",
    "llms_text",
    "openapi_document",
    "route_for",
    "validate_document",
    "with_allowed_keys",
]
