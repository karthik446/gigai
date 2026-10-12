"""0.1.11.10 fix B2: a shortened or reworded evidence quote is traced to its master line, not dropped.

THE SYMPTOM.  ``assess.md`` asks an unclear row to quote the closest master line and say what it does not state
("device gateway over MQTT; firmware or kernel work not stated").  One model quotes the line as it is written; another
shortens or rewords it.  The boundary kept a quote only when a line held it letter for letter
(``suggestion_check.clean_citations``), so the reworded quote was removed (``no_line``) and the STORED row's
``resume_evidence`` was empty: the row no longer named the line it rests on.

THE END OUTCOME asserted here, through the product's own path (``run_quick_assessment``) on a synthetic home with a
SCRIPTED model: the STORED row's ``resume_evidence`` is the WHOLE master line.  The rule is the shared-word rule an
assessment's quotes are traced to the master by (``assess_master.paraphrased_line``, the paraphrase half of
``MasterCitations.quote``).  Never invented: a quote no line is the paraphrase of is dropped as before, and so is one
that two lines share as many words with.  No status, class or source moves.  No model is called and every line below
is made up.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gigai.scout import assess_master
from gigai.scout import suggestion_check as check
from gigai.scout.assessment_core import AssessJob, assess_once, build_assess_context
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResponse, AssessResumeInput
from gigai.scout.quick_assess import _parse_body, run_quick_assessment

from tests.behaviors.scout_find_jobs.test_assess_whole_master import (  # noqa: F401 - `fx` is the fixture
    _TITLE_REQUIREMENTS, _URL, GATEWAY_LINE, PYTHON_LINE, TELEMETRY_LINE, _id_in, _master, fx,
)
from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.posting_fixtures import PostingsFixture

MATCHED = "matched_above_threshold"
HARDWARE = _TITLE_REQUIREMENTS[2]
TAIL = "firmware or kernel work not stated"

# --- the stored row ---------------------------------------------------------------------------------------


def _scripted(prompt: str, quote: str, status: str = "unclear") -> str:
    """A model that reads the whole brief and writes ``quote`` (its own wording of the gateway line) on the hardware row."""

    python, gateway = _id_in(prompt, PYTHON_LINE), _id_in(prompt, GATEWAY_LINE)
    assert python and gateway, "the brief shows both lines (fix B)"
    rows = [
        {"requirement": _TITLE_REQUIREMENTS[0], "class": "hard", "class_basis": f"Requirements: {_TITLE_REQUIREMENTS[0]}", "status": "met", "resume_evidence": ["Built Python services for six years"], "sources": [python]},
        {"requirement": _TITLE_REQUIREMENTS[1], "class": "askable", "class_basis": "Requirements: Kubernetes", "status": "met", "resume_evidence": ["Python services on Kubernetes"], "sources": [python]},
        {"requirement": HARDWARE, "class": "nice_to_have", "class_basis": "Nice to have: A low-level or hardware-adjacent background", "status": status, "resume_evidence": [quote]},
    ]
    pick = {"summary": None, "section_order": ["experience", "projects"], "lines": [python, gateway]}
    return json.dumps({"verdict": MATCHED, "matrix": rows, "questions": [], "suggestions": [], "pick": pick, "not_a_match_reason": None})


def _stored(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch, quote: str, status: str = "unclear") -> AssessResponse:
    model = fx.base.model
    answer = model.answer

    def from_the_prompt(prompt: str):
        model.assessed = _scripted(prompt, quote, status)
        return answer(prompt)

    monkeypatch.setattr(model, "answer", from_the_prompt)
    return run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=_URL), resume=AssessResumeInput(profile_id=fx.default_profile_id)),
        home_root=fx.home_root, target=fx.target, config=fixture_config(fx.home_root),
    )


def _hardware_row(stored: AssessResponse):
    return next(item for item in stored.result.matrix if item.requirement == HARDWARE)


#: The gateway line as a model shortens or rewords it. No master line holds any of these letter for letter.
SHORTENED = [
    # the first clause, with the words in between left out
    pytest.param("Assembled a Zigbee gateway that bridged about ten sensors", id="first-clause"),
    # reworded with a synonym ("Built" for "Assembled", "connecting" for "that bridged")
    pytest.param("Built a Zigbee gateway on a Raspberry Pi connecting about ten sensors to a Mosquitto MQTT broker", id="synonym"),
    # shortened, with the tail assess.md asks for on an unclear row
    pytest.param(f"Assembled a Zigbee gateway bridging ten sensors to an MQTT broker; {TAIL}", id="not-stated-tail"),
    pytest.param(f"Built a Zigbee gateway bridging about ten sensors to a Mosquitto MQTT broker; {TAIL}.", id="synonym-and-tail"),
]


@pytest.mark.parametrize("quote", SHORTENED)
def test_a_shortened_or_reworded_quote_is_stored_as_its_whole_master_line(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch, quote: str) -> None:
    stored = _stored(fx, monkeypatch, quote)
    row = _hardware_row(stored)
    # THE SYMPTOM: the stored row names the line it rests on, as the master writes it (it was empty).
    assert list(row.resume_evidence) == [GATEWAY_LINE]
    # An unclear row keeps the closest line as evidence and stays unclear: `sources` is for met rows only.
    assert row.status.value == "unclear" and not row.sources
    # The tail is the model's and is not stored; the other line about sensors and MQTT is not cited.
    assert TAIL not in " ".join(row.resume_evidence) and TELEMETRY_LINE not in row.resume_evidence
    assert dict(stored.checks.citations_cleaned) == {"traced_to_line": 1}


def test_a_made_up_quote_is_dropped_as_before(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    stored = _stored(fx, monkeypatch, f"Wrote Linux kernel drivers for an embedded camera board; {TAIL}")
    row = _hardware_row(stored)
    assert list(row.resume_evidence) == [] and row.status.value == "unclear"
    assert dict(stored.checks.citations_cleaned) == {"no_line": 1}


def test_a_met_row_with_no_source_shows_the_line_and_gets_no_source_from_code(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    stored = _stored(fx, monkeypatch, "Built a Zigbee gateway connecting about ten sensors to a Mosquitto MQTT broker", status="met")
    row = _hardware_row(stored)
    assert list(row.resume_evidence) == [GATEWAY_LINE] and row.status.value == "met"
    # Code replaces text by the candidate's own line and nothing else: the row's sources are the model's (none here).
    assert not row.sources
    # The selector traces the stored line to its id by the same rule, as it does for every row without sources.
    ids = {item.text: item.id for item in _master(fx).items.values()}
    cited = {item.text: item.lines for item in assess_master.cited_requirements(_master(fx), stored.result.matrix)}
    assert cited[HARDWARE] == (ids[GATEWAY_LINE],)


# --- the rule, row by row (the boundary alone, a scripted port) ---------------------------------------------

MASTER = """<!-- gigai-master:1 -->

## Summary

- Platform engineer with ten years on distributed services. <!-- id:sum-000001 -->

## Experience

### NORTHWIND <!-- id:r-000001 -->
**Staff Engineer | 2022 - Present**
- Ran the on-call rota for six payment services and cut incident resolution time by half. <!-- id:b-000001 -->
- Built a tracing SDK in Go that instruments forty serverless functions. <!-- id:b-000002 -->

### CONTOSO <!-- id:r-000002 -->
**Engineer | 2019 - 2022**
- Migrated the nightly batch ingest job to a Kafka queue worker, raising uptime to 99.9%. <!-- id:b-000003 -->
- Migrated the nightly batch export job to a Kafka queue worker, raising uptime to 99.5%. <!-- id:b-000004 -->
- Grew four engineers from mid-level to senior through weekly design reviews of event-driven systems. <!-- id:b-000005 -->

## Skills

- Go, Kafka, Kubernetes, Terraform, Postgres <!-- id:s-000001 -->
"""
IDS = ("sum-000001", "b-000001", "b-000002", "b-000003", "b-000004", "b-000005", "s-000001")
LINES = check.parse_master_lines(MASTER)
TEXT = {line_id: line.text for line_id, line in LINES.items()}
CITABLE = check._Citable(LINES, {}, {})  # noqa: SLF001
JOB = AssessJob("Staff Engineer", "Acme", "Remote", "Requirements: Go services; Kafka streaming. Nice to have: mentoring.")


class _Port:
    name = "scripted"

    def __init__(self, output: object) -> None:
        self.output = json.dumps(output)

    def invoke(self, request):
        return SimpleNamespace(output_text=self.output, normalized_usage=None)


class _Binding:
    def __init__(self, output: object) -> None:
        self.port = _Port(output)

    def request(self, *, role: str, prompt: str):
        return SimpleNamespace(role=role, prompt=prompt)


def _row(requirement: str, status: str, evidence: list[str], sources: list[str] | None = None, klass: str = "nice_to_have") -> dict[str, object]:
    return {"requirement": requirement, "class": klass, "class_basis": f"Requirements: {requirement}", "status": status, "resume_evidence": evidence, "sources": sources or []}


def _assess(rows: list[dict[str, object]]):
    answer = {"verdict": MATCHED, "matrix": rows, "questions": [], "suggestions": [], "not_a_match_reason": None}
    attempt = assess_once(_Binding(answer), JOB, build_assess_context(resume_text=MASTER, resume_ids=IDS, pick_lines=20), parse=_parse_body)
    assert attempt.ok, attempt
    return attempt


def test_each_reworded_quote_is_one_master_line_and_what_no_line_backs_is_gone() -> None:
    rows = [
        # reworded ("Mentored" for "Grew", the words in another order) -> the line
        _row("Mentoring", "unclear", ["Mentored four mid-level engineers to senior in weekly design reviews; formal management not stated"]),
        # shortened and reworded on a met row with no source -> the line; the row gets no source from code
        _row("Tracing", "met", ["Wrote a Go tracing SDK for forty serverless functions"]),
        # a quote two lines share as many words with -> nothing: code does not choose between them
        _row("Batch jobs", "unclear", ["Migrated a nightly batch job to a Kafka worker"]),
        # the same quote with the one word that tells the two lines apart -> that line
        _row("Exports", "unclear", ["Migrated a nightly batch export job to a Kafka worker"]),
        # a made-up quote -> nothing
        _row("Flink", "unclear", ["Operated a Flink cluster for the fraud team across three regions"]),
        # a sentence that only says what is missing, in words a line has -> nothing (it is not a quote of that line)
        _row("Uptime", "unclear", ["Uptime of the nightly batch ingest Kafka queue worker not stated"]),
        # names from the Skills line in a sentence -> nothing: a Skills line is never what a sentence is traced to
        _row("Stack", "unclear", ["Deployed Kafka, Kubernetes, Terraform and Postgres"]),
        # a met row with a source: its evidence is the cited line already (C2), whatever the quote says
        _row("On-call", "met", ["Ran on-call"], ["b-000001"], "hard"),
    ]
    before = sorted((str(row["requirement"]), str(row["status"]), str(row["class"]), list(row["sources"])) for row in rows)  # type: ignore[call-overload]
    attempt = _assess(rows)
    matrix = attempt.parsed.matrix
    assert {row.requirement: list(row.resume_evidence) for row in matrix} == {
        "Mentoring": [TEXT["b-000005"]],
        "Tracing": [TEXT["b-000002"]],
        "Batch jobs": [],
        "Exports": [TEXT["b-000004"]],
        "Flink": [],
        "Uptime": [],
        "Stack": [],
        "On-call": [TEXT["b-000001"]],
    }
    # Every stored citation is a line of the master as it is written.
    assert all(item in set(TEXT.values()) for row in matrix for item in row.resume_evidence)
    # Only the evidence moved: no status, class or source did.
    assert sorted((row.requirement, row.status.value, row.requirement_class.value, list(row.sources)) for row in matrix) == before
    assert sorted(attempt.extras.checked_citations) == ["no_line"] * 4 + ["traced_to_line"] * 3


def test_what_the_check_did_is_named_and_a_piece_a_line_holds_is_read_as_before() -> None:
    cites = check.single_line_cites
    # A piece one line holds letter for letter is that line, as before (no word rule is needed).
    assert cites("Built a tracing SDK in Go", CITABLE) == ([TEXT["b-000002"]], "trimmed_to_line")
    assert cites("Built a tracing SDK in Go; serverless cold starts not stated", CITABLE) == ([TEXT["b-000002"]], "trimmed_to_line")
    assert cites(TEXT["b-000001"], CITABLE) == ([TEXT["b-000001"]], "verbatim")
    # A reworded piece is traced; two reworded pieces of two lines are cited each on its own, in the order joined.
    assert cites("Wrote a Go tracing SDK for forty functions", CITABLE) == ([TEXT["b-000002"]], "traced_to_line")
    assert cites("Wrote a Go tracing SDK for forty functions; handled on-call for six payment services", CITABLE) == ([TEXT["b-000002"], TEXT["b-000001"]], "split_join")
    # Too few shared words to be a paraphrase (the rule asks for three, and four tenths of the quote's own).
    assert cites("Led the Kafka migration", CITABLE) == ([], "no_line")
    assert cites("Wrote a tracing SDK and an event bus, a rules engine, a billing ledger and a search index for a retail bank", CITABLE) == ([], "no_line")


def test_the_rule_is_the_one_the_selector_traces_a_quote_by() -> None:
    lines = [(line.id, line.text) for line in LINES.values()]
    assert assess_master.paraphrased_line("Wrote a Go tracing SDK for forty functions", lines) == "b-000002"
    assert assess_master.paraphrased_line("Migrated a nightly batch job to a Kafka worker", lines) is None  # two lines, as many words
    assert assess_master.paraphrased_line("Migrated a nightly batch export job to a Kafka worker", lines) == "b-000004"
    assert assess_master.paraphrased_line("Operated a Flink cluster", lines) is None
    assert assess_master.paraphrased_line("", lines) is None
