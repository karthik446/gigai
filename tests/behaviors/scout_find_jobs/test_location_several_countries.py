"""0.1.11.12 F5b (gigai-18i): rule 4 for a posting that names offices in several countries.

A posting that lists offices or hubs in several countries ("offices in Toronto, London and Paris") and says nowhere
that this role may be done from the candidate's country does not meet the country requirement: the model's ``met``
``elig-location`` row is read as ``unclear``, its ``code_note`` says why, and the row holds for its one question (the
row's own, the one the unasked-row rule asks). With a clause that says the role may be done from an eligible country
("may be done remotely from the United States", "remote in the US") the row stays met; a posting that names one
country, or a candidate with no country restriction, is untouched.

Offline, synthetic: every posting below is invented and only has the SHAPE of a real case (a Cohere-shaped office list).
"""

from __future__ import annotations

import pytest

from gigai.scout.assessment_core import AssessJob
from tests.behaviors.scout_find_jobs.test_evidence_suggestion_location_checks import MATCHED, NOT_A_MATCH, PENDING, _answer, _assess, _ctx, _row

OFFICES = "We have offices in Toronto, London and Paris, and our teams work across them.\nYou will train and serve large language models."
LOCATION_MET = {"id": "elig-location", "requirement": "Based near an office: Toronto, London or Paris", "class": "hard", "class_basis": "offices in Toronto, London and Paris", "status": "met", "resume_evidence": [], "sources": []}


def _run(
    body: str,
    countries: tuple[str, ...] = ("US",),
    *,
    location: dict[str, object] | None = None,
    questions: list[dict[str, object]] | None = None,
    header: str = "Toronto; London; Paris",
    **more: object,
):
    rows = [_row("Go services", "met", ["b-000003"], ["z"], "hard"), dict(location or LOCATION_MET)]
    answer = _answer(rows, questions=questions or [], **{"verdict": MATCHED, **more})
    return _assess(answer, job=AssessJob("Staff Engineer", "Northwind AI", header, body), ctx=_ctx(countries=countries)).parsed


def _location(parsed):
    return next(row for row in parsed.matrix if row.id == "elig-location")


def test_an_office_list_in_several_countries_without_a_clause_is_unclear_with_the_reason() -> None:
    parsed = _run(OFFICES)
    row = _location(parsed)
    assert row.status.value == "unclear"
    assert row.code_note is not None and "Toronto" in row.code_note and "the United States" in row.code_note
    assert parsed.verdict.value == PENDING
    assert [item.question_id for item in parsed.structured_questions] == ["requirement:elig.location"]


@pytest.mark.parametrize(
    "clause",
    [
        "This role may be done remotely from the United States or Canada.",
        "The role can be performed from the US.",
        "We are open to candidates based in the United States.",
        "This position is remote in the US.",
        "We work from anywhere in the world.",
    ],
)
def test_a_clause_that_the_role_may_be_done_from_an_eligible_country_keeps_it_met(clause: str) -> None:
    parsed = _run(f"{OFFICES}\n{clause}")
    assert _location(parsed).status.value == "met" and _location(parsed).code_note is None
    assert parsed.verdict.value == MATCHED and not parsed.structured_questions


@pytest.mark.parametrize(
    "clause",
    [
        "This role cannot be done from the United States.",
        "This role may be done remotely from Germany.",
        "We are remote-first. The pay band for this role is $150k-$180k in the United States.",
    ],
)
def test_a_denied_clause_another_country_or_a_pay_band_alone_does_not_keep_it_met(clause: str) -> None:
    parsed = _run(f"{OFFICES}\n{clause}")
    assert _location(parsed).status.value == "unclear" and parsed.verdict.value == PENDING


def test_the_models_own_question_on_the_row_is_the_one_asked() -> None:
    own = {"question_id": "location:country", "question": "Can you work from Toronto, London or Paris?", "requirement": LOCATION_MET["requirement"]}
    parsed = _run(OFFICES, questions=[own])
    assert _location(parsed).status.value == "unclear"
    assert [item.question_id for item in parsed.structured_questions] == ["location:country"]


@pytest.mark.parametrize(
    ("body", "countries"),
    [
        ("Our office is in Toronto, Canada. You will train large language models.", ("US", "CA")),  # one country
        (OFFICES, ("ANY",)),  # no country restriction
        (OFFICES, ()),  # the setup names no country
    ],
)
def test_one_country_or_no_country_restriction_is_untouched(body: str, countries: tuple[str, ...]) -> None:
    parsed = _run(body, countries)
    assert _location(parsed).status.value == "met" and _location(parsed).code_note is None
    assert parsed.verdict.value == MATCHED and not parsed.structured_questions


def test_an_unmet_row_on_an_office_list_is_not_lifted_by_a_pay_band() -> None:
    unmet = {**LOCATION_MET, "status": "unmet"}
    body = f"{OFFICES}\nWe are remote-first. The pay band for this role is $150k-$180k in the United States."
    parsed = _run(body, location=unmet, verdict=NOT_A_MATCH, not_a_match_reason="The role is in Canada, the UK or France.")
    assert _location(parsed).status.value != "met"


# --- 0.1.11.12 F5b fix round -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("header", ["Remote - US", "Remote (United States)", "New York, NY"])
def test_a_location_header_that_names_only_or_remote_in_an_eligible_country_is_the_clause(header: str) -> None:
    parsed = _run(OFFICES, header=header)
    assert _location(parsed).status.value == "met" and _location(parsed).code_note is None
    assert parsed.verdict.value == MATCHED and not parsed.structured_questions


@pytest.mark.parametrize(
    "body",
    [
        "We sell to customers based in Germany and France. Our office is in Toronto.",
        "Our software runs in 500 store locations across Germany and France. Our office is in Toronto.",
        "Our team is located in Toronto. Our partners are located in Germany and France.",
    ],
)
def test_only_office_hub_or_hq_words_make_an_office_list(body: str) -> None:
    parsed = _run(body)
    assert _location(parsed).status.value == "met" and _location(parsed).code_note is None


@pytest.mark.parametrize("status", ["unmet", "unclear"])
def test_a_clause_never_lifts_an_unmet_or_unclear_row(status: str) -> None:
    row = {**LOCATION_MET, "status": status}
    body = f"{OFFICES}\nThis role may be done remotely from the United States."
    verdict = NOT_A_MATCH if status == "unmet" else PENDING
    own = [{"question_id": "location:country", "question": "Can you work from Toronto, London or Paris?", "requirement": LOCATION_MET["requirement"]}]
    more = {"not_a_match_reason": "The role is in Canada, the UK or France."} if status == "unmet" else {"questions": own}
    parsed = _run(body, location=row, verdict=verdict, **more)
    assert _location(parsed).status.value == status and parsed.verdict.value == verdict


@pytest.mark.parametrize(
    "body",
    [
        "Remote: must reside in Poland and report to our US engineering manager.",
        "We are remote in the US and Europe. This role is open to candidates in Poland only.",
    ],
)
def test_an_unmet_row_stays_unmet_beside_a_far_or_other_country_clause(body: str) -> None:
    unmet = {**LOCATION_MET, "status": "unmet"}
    parsed = _run(f"{OFFICES}\n{body}", location=unmet, verdict=NOT_A_MATCH, not_a_match_reason="The role is in Poland.")
    assert _location(parsed).status.value == "unmet" and parsed.verdict.value == NOT_A_MATCH


def test_a_country_far_from_the_clause_is_not_the_clause() -> None:
    parsed = _run(f"{OFFICES}\nRemote: must reside in Poland and report to our US engineering manager.")
    assert _location(parsed).status.value == "unclear" and parsed.verdict.value == PENDING


def test_a_denial_after_the_country_does_not_undo_the_clause() -> None:
    parsed = _run(f"{OFFICES}\nThis role can be done remotely from the US, but we do not offer relocation.")
    assert _location(parsed).status.value == "met" and parsed.verdict.value == MATCHED and not parsed.structured_questions


def test_us_cities_are_read_as_the_united_states_and_every_place_is_in_the_note() -> None:
    parsed = _run("We have offices in New York, Austin and London.", countries=("DE",))
    note = _location(parsed).code_note
    assert _location(parsed).status.value == "unclear" and note is not None
    assert all(place in note for place in ("New York", "Austin", "London"))
    many = "Our offices: Toronto, Montreal, Vancouver, London, Manchester, Paris, Berlin, Dublin and Madrid."
    note = _location(_run(many)).code_note
    assert note is not None and all(place in note for place in ("Toronto", "Berlin", "Dublin", "Madrid")) and "..." not in note


def test_a_city_and_its_country_are_one_place_in_the_note() -> None:
    note = _location(_run("Our offices are in Toronto, Canada and London, UK.")).code_note
    assert note is not None and "(Toronto, Canada, London, UK)" not in note and "Toronto, Canada; London, UK" in note
