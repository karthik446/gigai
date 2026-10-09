"""P3: the outbound check (``gigai.scout.outbound_check``), without a server.

What must be redacted (the ``resume_pii`` shapes and their usual obfuscations), what must
pass unchanged (ordinary job data: URLs, company domains, salaries, versions, dates), which
strings are never scanned (ids, URLs, digests, timestamps, posting text), and that a
100,000-character pathological input is read in linear time. Synthetic data only.
"""

from __future__ import annotations

import io
import json
import re
import time

import pytest

from gigai.scout.find_jobs.api import openapi, server
from gigai.scout.outbound_check import KINDS, REDACTIONS_KEY, TOKENS, redact_payload, redact_text

_EMAIL = "zed.marker@synthmail.example"
_PHONE = "(415) 555-0142"

# (text, kind, a fragment that must be gone)
_CONTACT_SHAPED = [
    (f"reach me at {_EMAIL} any time", "email", "synthmail"),
    ("ZED.MARKER+jobs@Synthmail.Example", "email", "ynthmail"),
    (f"call {_PHONE} after five", "phone", "555-0142"),
    ("415-555-0142", "phone", "0142"),
    ("415.555.0142", "phone", "0142"),
    ("+1 415 555 0142", "phone", "0142"),
    ("1-800-555-0142 toll free", "phone", "0142"),
    ("+44 20 7946 0958", "phone", "7946"),
    ("+91-98765-43210", "phone", "43210"),
    ("my place is 742 Evergreen Terrace Rd, Springfield", "address", "Evergreen"),
    ("12 Elm St apt 4", "address", "Elm"),
    ("1600 Synthetic Avenue", "address", "Synthetic"),
    ("see linkedin.com/in/zed-marker for more", "links", "zed-marker"),
    ("https://www.linkedin.com/in/zed-marker/", "links", "zed-marker"),
    ("uk.linkedin.com/in/zed-marker", "links", "zed-marker"),
    ("code at https://github.com/zedmarker/dotfiles.", "links", "zedmarker"),
    ("gitlab.com/zedmarker", "links", "zedmarker"),
]
_OBFUSCATED = [
    ("zed dot marker at synthmail dot com", "email", "synthmail"),
    ("write to zed at synthmail dot co dot uk please", "email", "synthmail"),
    ("zed.marker [at] synthmail.example", "email", "synthmail"),
    ("zed.marker(at)synthmail(dot)com", "email", "synthmail"),
    ("zed.marker {at} synthmail [dot] com", "email", "synthmail"),
    ("zed.marker @ synthmail.example", "email", "synthmail"),
    ("zed.marker@synthmail dot com", "email", "synthmail"),
    ("zed.marker@synthmail . example", "email", "synthmail"),
    ("z e d @ s y n t h m a i l . c o m", "email", "s y n t h"),
    ("z e d . m a r k e r @ synthmail.example", "email", "synthmail"),
    ("zed.marker＠synthmail.example", "email", "synthmail"),  # fullwidth @
    ("zed.marker﹫synthmail．example", "email", "synthmail"),  # small @, fullwidth dot
    ("4 1 5 5 5 5 0 1 4 2", "phone", "0 1 4 2"),
    ("4-1-5-5-5-5-0-1-4-2", "phone", "0-1-4-2"),
    ("[415] 555 0142", "phone", "0142"),
    ("{415} 555.0142", "phone", "0142"),
    ("415 - 555 - 0142", "phone", "0142"),
    ("４１５-５５５-０１４２", "phone", "０１４２"),  # fullwidth digits
    ("٤١٥-٥٥٥-٠١٤٢", "phone", "٠١٤٢"),  # Arabic-Indic digits
    ("415‑555‑0142", "phone", "0142"),  # non-breaking hyphens
]


@pytest.mark.parametrize(("text", "kind", "gone"), _CONTACT_SHAPED + _OBFUSCATED)
def test_a_contact_shape_becomes_its_token(text: str, kind: str, gone: str) -> None:
    counts: dict[str, int] = {}
    out = redact_text(text, counts)
    assert TOKENS[kind] in out, out
    assert gone not in out, out
    assert counts == {kind: 1}, (out, counts)
    # A redacted text is stable: scanning it again finds nothing.
    again: dict[str, int] = {}
    assert redact_text(out, again) == out and again == {}


# Ordinary job data: none of it is contact-shaped. Realistic and synthetic.
_ORDINARY = [
    "https://boards.greenhouse.io/acme/jobs/4012345678",
    "https://jobs.lever.co/acme-robotics/3f2a9c1e-5b7d-4e8a-9c0d-1a2b3c4d5e6f",
    "https://jobs.ashbyhq.com/acme/8b1d2c3e-415-555-0142-role?utm_source=scout",
    "Apply at https://careers.acme.example/openings/415-555-0142 before Friday.",
    "https://www.linkedin.com/jobs/view/3912345678",
    "linkedin.com/company/acme-robotics",
    "https://acme.example/blog/2024/10/03/how-we-ship",
    "www.acme.example/careers",
    "acme.io",
    "careers.acme-robotics.com",
    "Staff Engineer at acme.com",
    "Engineer @ Booking.com",
    "Senior Backend Engineer @ Acme",
    "Follow @acme_eng for updates",
    "Acme Robotics, Inc.",
    "Monday.com",
    "$120,000 - $150,000",
    "$120k-$150k + equity",
    "USD 185,000.00 base, 15% bonus",
    "100 000 - 120 000 EUR",
    "120.000 - 150.000 EUR per year",
    "401(k) match up to 4%",
    "Python 3.11.4",
    "v1.2.3",
    "Kubernetes 1.29, Postgres 15.4, Node 20.11.1",
    "pkg@1.2.3",
    "C++14 17 20 23",
    "2026-10-03",
    "2026-10-02T15:00:00.000000Z",
    "10/03/2026",
    "Oct 3, 2026 - Dec 15, 2026",
    "2019 - 2023",
    "5-10 years of experience",
    "1 2 3 4 5 6 7 8 9 10",
    "24/7 on-call, p99 < 200 ms, 99.99% uptime",
    "192.168.1.1:8765",
    "sha256:4155550142abcdef4155550142abcdef",
    "run_20260929T100000Z",
    "ISO 27001 and SOC 2 Type II",
    "Series B, 100-200 employees, 1,000,000 users",
    "2024 Best Place to work, 3 years running",
    "looked at the dot product of two embeddings",
    "We met at the conference dot the i's and cross the t's",
    "+15% bonus",
    "Yes, 4 years, GKE + BigQuery",
    "San Francisco, CA 94107 (hybrid, 3 days)",
    "Remote (US) or New York, NY",
]


def test_the_ordinary_corpus_holds_at_least_thirty_strings() -> None:
    assert len(_ORDINARY) >= 30 and len(set(_ORDINARY)) == len(_ORDINARY)


@pytest.mark.parametrize("text", _ORDINARY)
def test_ordinary_job_data_passes_unchanged(text: str) -> None:
    counts: dict[str, int] = {}
    assert redact_text(text, counts) == text
    assert counts == {}


def test_a_payload_of_ordinary_job_data_is_returned_as_is() -> None:
    payload = {"rows": [{"n": index, "value": text} for index, text in enumerate(_ORDINARY)], "total": len(_ORDINARY), "ok": True, "none": None}
    out = redact_payload(payload)
    assert out is payload  # nothing copied, nothing added
    assert REDACTIONS_KEY not in out


def test_a_payload_gets_tokens_and_counts_by_kind_and_never_a_value() -> None:
    payload = {
        "answers": [
            {"question_id": "contact:how", "answer": f"Mail {_EMAIL} or zed dot marker at synthmail dot com", "revision": 1},
            {"question_id": "contact:phone", "answer": f"Phone {_PHONE}", "tags": ["call 4 1 5 5 5 5 0 1 4 2"]},
        ],
        "story": {"narrative": {"action": "I live at 742 Evergreen Terrace Rd", "result": "see github.com/zedmarker"}},
        "total": 2,
    }
    before = json.dumps(payload)
    out = redact_payload(payload)
    assert json.dumps(payload) == before, "the payload given is not changed in place"
    assert out[REDACTIONS_KEY] == {"email": 2, "phone": 2, "links": 1, "address": 1}
    assert list(out[REDACTIONS_KEY]) == [kind for kind in KINDS if kind in out[REDACTIONS_KEY]]
    raw = json.dumps(out)
    for marker in ("synthmail", "zed.marker", "555-0142", "0 1 4 2", "Evergreen", "zedmarker"):
        assert marker not in raw, marker
    assert out["answers"][0]["answer"] == f"Mail {TOKENS['email']} or {TOKENS['email']}"
    assert out["answers"][1] == {"question_id": "contact:phone", "answer": f"Phone {TOKENS['phone']}", "tags": [f"call {TOKENS['phone']}"]}
    assert out["total"] == 2


# --- what is never scanned ---------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "id", "url", "normalized_url", "job_url", "job_identity", "manifest_url", "board_url", "run_id", "question_id",
        "content_sha256", "config_digest", "created_at", "updated_at", "at", "as_of", "source", "path", "schema_version",
        "operation_key", "resume_ref", "prompt_version",
    ],
)
def test_ids_urls_digests_and_timestamps_are_not_scanned(key: str) -> None:
    # Values shaped like contact data on purpose: under a structural key they are an id or a URL, never text.
    for value in ("https://github.com/karthik446/gigai/releases/download/scout-snapshot/manifest.json", "415-555-0142", _EMAIL):
        payload = {"item": {key: value}, "items": [{key: [value]}]}
        assert redact_payload(payload) is payload, (key, value)


def test_action_links_are_not_scanned() -> None:
    payload = {"links": {"self": {"method": "GET", "path": "/api/jobs?url=https://github.com/acme/jobs/415-555-0142"}, "note": _EMAIL}}
    assert redact_payload(payload) is payload


def test_a_structural_key_does_not_hide_the_text_next_to_it() -> None:
    payload = {"url": "https://github.com/acme/careers", "note": f"mail {_EMAIL}", "job_identity": "415-555-0142"}
    out = redact_payload(payload)
    assert out == {**payload, "note": f"mail {TOKENS['email']}", REDACTIONS_KEY: {"email": 1}}


_RECRUITER = "Questions? Mail talent@hiring-desk.example or call (212) 555-0188. Office: 500 Howard Street, San Francisco."


@pytest.mark.parametrize("key", ["posting_text", "job_text", "description", "job_description"])
def test_posting_text_keys_pass_unchanged(key: str) -> None:
    payload = {"result": {key: _RECRUITER}}
    assert redact_payload(payload) is payload


@pytest.mark.parametrize("identity", ["normalized_url", "job_url"])
def test_the_text_of_a_posting_passes_and_the_same_contact_in_private_text_is_redacted(identity: str) -> None:
    posting = {identity: "https://boards.greenhouse.io/acme/jobs/101", "title": "Platform Engineer", "company": "Acme", "text": _RECRUITER}
    payload = {
        "posting": posting,
        "answers": [{"question_id": "notes:recruiter", "answer": "The recruiter is talent@hiring-desk.example, (212) 555-0188"}],
        "application_events": [{"event_kind": "applied", "notes": "Sent to talent@hiring-desk.example"}],
    }
    out = redact_payload(payload)
    assert out["posting"] is posting and out["posting"]["text"] == _RECRUITER
    assert out["answers"][0]["answer"] == f"The recruiter is {TOKENS['email']}, {TOKENS['phone']}"
    assert out["application_events"][0]["notes"] == f"Sent to {TOKENS['email']}"
    assert out[REDACTIONS_KEY] == {"email": 2, "phone": 1}


def test_a_posting_shaped_key_cannot_carry_private_text_out() -> None:
    # ``text`` is skipped only as a string directly inside an object that holds a posting identity.
    not_a_posting = {"text": f"my own line: {_EMAIL}", "title": "Platform Engineer", "company": "Acme"}
    assert redact_payload(not_a_posting)["text"] == f"my own line: {TOKENS['email']}"
    # A posting's other fields, and anything nested under it, are scanned.
    posting = {
        "normalized_url": "https://boards.greenhouse.io/acme/jobs/101",
        "text": _RECRUITER,
        "title": f"Engineer {_EMAIL}",
        "notes": f"private {_EMAIL}",
        "lines": [{"text": f"nested {_EMAIL}"}],
        "answers": {"text": {"answer": f"deeper {_EMAIL}"}},
    }
    out = redact_payload({"row": {"posting": posting}})["row"]["posting"]
    assert out["text"] == _RECRUITER
    assert out["title"] == f"Engineer {TOKENS['email']}"
    assert out["notes"] == f"private {TOKENS['email']}"
    assert out["lines"] == [{"text": f"nested {TOKENS['email']}"}]
    assert out["answers"] == {"text": {"answer": f"deeper {TOKENS['email']}"}}
    # A tailored resume line's ``text`` is the user's: scanned.
    tailored = {"job_identity": "https://boards.greenhouse.io/acme/jobs/101", "sections": [{"lines": [{"line_id": "L1", "text": f"Built it, {_EMAIL}"}]}]}
    assert redact_payload(tailored)["sections"][0]["lines"][0]["text"] == f"Built it, {TOKENS['email']}"


# --- the one choke point: the server's own response writer ---------------------------------


class _Wire:
    """What ``_write_json`` needs of a request handler, recording what it sends."""

    def __init__(self, method: str, path: str) -> None:
        self.command, self.path = method, path
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.wfile = io.BytesIO()

    def send_response(self, status: int) -> None:
        self.status = int(status)

    def send_header(self, name: str, value: str) -> None:
        self.headers[name] = value

    def end_headers(self) -> None:
        pass


def _written(method: str, path: str, payload: dict[str, object]) -> tuple[_Wire, bytes]:
    handler = server._make_handler(None)  # type: ignore[arg-type]
    wire = _Wire(method, path)
    handler._write_json(wire, 200, payload)  # type: ignore[attr-defined]
    return wire, wire.wfile.getvalue()


def test_the_response_writer_passes_a_postings_contact_lines_and_redacts_the_same_email_in_a_note() -> None:
    payload = {
        "schema_version": "scout-job-response:1",
        "job_identity": "https://boards.greenhouse.io/acme/jobs/101",
        "posting": {"normalized_url": "https://boards.greenhouse.io/acme/jobs/101", "title": "Platform Engineer", "company": "Acme", "text": _RECRUITER},
        "assessments": [{"source": "quick", "posting_text": _RECRUITER, "matrix": []}],
        "answers": [{"question_id": "notes:recruiter", "answer": "Recruiter: talent@hiring-desk.example"}],
        "application_events": [{"event_kind": "applied", "notes": f"Sent to talent@hiring-desk.example from {_EMAIL}, cell {_PHONE}, or zed dot marker at synthmail dot com"}],
        "links": {"self": {"method": "GET", "path": "/api/jobs?url=https://boards.greenhouse.io/acme/jobs/101"}},
    }
    wire, raw = _written("GET", "/api/jobs?url=https%3A%2F%2Fboards.greenhouse.io%2Facme%2Fjobs%2F101", payload)
    assert wire.status == 200 and wire.headers["Content-Type"] == "application/json"
    assert wire.headers["X-GigAI-Labels"] == "user-private, public-untrusted"
    assert int(wire.headers["Content-Length"]) == len(raw)
    for marker in (b"synthmail", b"zed.marker", b"555-0142", b"zed dot marker"):
        assert marker not in raw, marker
    body = json.loads(raw)
    # The posting's own text: the recruiter's email, the office phone and street address, unchanged.
    assert body["posting"]["text"] == _RECRUITER and body["assessments"][0]["posting_text"] == _RECRUITER
    # The same email in the user's own fields of the same response: redacted.
    assert body["answers"][0]["answer"] == f"Recruiter: {TOKENS['email']}"
    assert body["application_events"][0]["notes"] == f"Sent to {TOKENS['email']} from {TOKENS['email']}, cell {TOKENS['phone']}, or {TOKENS['email']}"
    assert raw.count(b"talent@hiring-desk.example") == 2, "only the two posting texts hold it"
    assert body["_redactions"] == {"email": 4, "phone": 1}
    assert body["links"] == payload["links"] and body["job_identity"] == payload["job_identity"]


def test_the_response_writer_adds_nothing_to_a_clean_response_and_labels_an_error_by_its_route() -> None:
    wire, raw = _written("GET", "/api/health", {"status": "ok"})
    assert json.loads(raw) == {"status": "ok"} and wire.headers["X-GigAI-Labels"] == "none"
    # An error that repeats what the caller sent is scanned like any other response.
    wire, raw = _written("PUT", "/api/answers/contact:how", {"error": {"code": "invalid_value", "message": f"answer {_EMAIL!r} is not accepted"}})
    assert b"synthmail" not in raw and json.loads(raw)["_redactions"] == {"email": 1}
    assert wire.headers["X-GigAI-Labels"] == "user-private, public-untrusted"


# --- the API's own documents are ordinary data -------------------------------------------


def test_the_spec_the_index_and_every_route_example_pass_unchanged() -> None:
    for payload in (openapi.openapi_document(), openapi.index_document()):
        assert redact_payload(payload) is payload
    for route in openapi.ROUTES:
        assert redact_payload(route.example) is route.example, f"{route.method} {route.path}: the example holds a contact shape"
        if route.request_example is not None:
            assert redact_payload(route.request_example) is route.request_example, f"{route.method} {route.path}"


# --- linear time -------------------------------------------------------------------------

_PATHOLOGICAL = [
    "a", "1", "@", "a@", "a.", "a@a.", "1 ", "1-", "123-", "(1)", "+1 ", "+", " at ", "(at)", " dot ", "x at y dot ", "x@y dot ",
    "a b @ ", "a ", " ", ". ", "1 A ", "1 Aa St ", "http://", "www.", "github.com/", "linkedin.com/in/", "＠１", "１ ",
    "a.b.", "-", "a-", "[at] ", "@a.b ", "9 9 9 9 9 9 9 9 9 ",
]


def _seconds(text: str) -> float:
    best = float("inf")
    for _ in range(3):
        started = time.perf_counter()
        redact_text(text)
        best = min(best, time.perf_counter() - started)
    return best


@pytest.mark.parametrize("unit", _PATHOLOGICAL)
def test_a_pathological_100k_input_is_read_in_linear_time(unit: str) -> None:
    short = unit * (100_000 // len(unit))
    long = unit * (200_000 // len(unit))
    assert len(short) >= 99_990
    t_short, t_long = _seconds(short), _seconds(long)
    # Measured: at most 0.08 s for 100,000 characters. A quadratic scan of 100,000 takes minutes.
    assert t_short < 2.0, f"{unit!r}: {t_short:.3f} s for 100,000 characters"
    # Twice the text, about twice the time (4x would be quadratic); 50 ms of slack for a busy machine.
    assert t_long < 3.0 * t_short + 0.05, f"{unit!r}: 100k {t_short:.3f} s, 200k {t_long:.3f} s"


def test_the_timing_check_would_catch_a_quadratic_pattern() -> None:
    # The shape resume_privacy had before its linear rewrite: tried from every character of a run.
    naive = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

    def seconds(text: str) -> float:
        # The best of five, in this process's own CPU time: one wall-clock reading of 14 ms is mostly the other
        # workers of a full run (0.1.11.9 FX2: 26 ms and 72 ms were read there, 2.7x, for a pattern that is 4x).
        best = float("inf")
        for _ in range(5):
            started = time.process_time()
            naive.search(text)
            best = min(best, time.process_time() - started)
        return best

    t_short, t_long = seconds("a" * 3_000), seconds("a" * 6_000)
    assert t_long > 3.0 * t_short + 0.0, (t_short, t_long)


def test_a_long_mixed_text_keeps_everything_but_the_contact_shapes() -> None:
    filler = "Led the migration of 14 services to Kubernetes 1.29; cut p99 from 900 ms to 210 ms. "
    text = filler * 600 + f"Mail {_EMAIL}. " + filler * 600 + f"Call {_PHONE}."
    assert len(text) > 100_000
    counts: dict[str, int] = {}
    out = redact_text(text, counts)
    assert counts == {"email": 1, "phone": 1}
    assert out == filler * 600 + f"Mail {TOKENS['email']}. " + filler * 600 + f"Call {TOKENS['phone']}."
