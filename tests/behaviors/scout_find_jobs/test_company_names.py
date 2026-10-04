"""0110-8-11: a company is shown by the company index's name, not its board token. Synthetic only.

The case (ANALYSIS-1 Q6.6): "Garnerhealth", "Medallionakafirstlayerai" in the
grid. A posting row's ``company`` is its board token; the index file for the
board holds the real name ("Garner Health", "Medallion"). Every surface that
shows or returns a posting's company now carries that name as
``company_name``; the token stays the id.

Per surface: the ``scout new`` rows and table, the posting search rows (the
Jobs API) and its CLI lines, and the shapes the API serves for the job page,
the assessments list, applications, tailored resumes and an answer's jobs
(each built by its own serializer, then passed through the server's one
response pass); the PDF's file name; the CLI's "at <company>" headings.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import posting_search, scout_new
from gigai.scout.find_jobs.company_index import board_list_url, index_stamp, refresh_company
from gigai.scout.find_jobs.contracts import ATSProvider, PostingRow, SourceKind
from gigai.scout.quick_assess import read_quick_assessment

from tests.support.pipeline_fixtures import assess_base
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, lever_job

SLUG = "garnerhealth"
NAME = "Garner Health"
LONG_SLUG = "medallionakafirstlayerai"
LONG_NAME = "Medallion"
PLAIN = "osprey-lane"  # the index has no name of its own for this one: the slug rule answers
JOB = f"https://jobs.lever.co/{SLUG}/{SLUG}-00001"


def _seed(fx: PostingsFixture, slug: str, company: str | None, *, count: int = 1) -> None:
    fx.watch(slug)
    jobs = [lever_job(slug, n) for n in range(1, count + 1)]
    fx.cache.store("lever", board_list_url("lever", slug), body=json.dumps(jobs).encode("utf-8"), etag=None, last_modified=None, marker=None)
    refresh_company(fx.index, fx.cache, ats="lever", slug=slug, company=company, observed_at=index_stamp(days_ago(1)))


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed(fixture, SLUG, NAME)
    _seed(fixture, LONG_SLUG, LONG_NAME)
    _seed(fixture, PLAIN, None)
    return fixture


def test_the_index_name_then_the_slug_rule_then_the_text(fx: PostingsFixture) -> None:
    from gigai.scout.find_jobs.company_names import company_display_name, index_company_name, with_company_names  # noqa: F401

    home = fx.home_root
    assert (index_company_name(home, SLUG), index_company_name(home, LONG_SLUG), index_company_name(home, PLAIN)) == (NAME, LONG_NAME, None)
    assert [company_display_name(home, text) for text in (SLUG, LONG_SLUG, PLAIN, "customerio", "Customer.io", "", None)] == [
        NAME, LONG_NAME, "Osprey Lane", "Customerio", "Customer.io", "", None,
    ]
    assert company_display_name(None, SLUG) == "Garnerhealth"  # no home: the slug rule, as before
    assert index_company_name(home, "../../etc/passwd") is None and index_company_name(home, "no such board") is None
    # A renamed company is read again (the cache is by the file's stamp).
    entry = fx.index.read("lever", SLUG)
    assert entry is not None
    fx.index.write(replace(entry, company="Garner Health Inc"))
    assert index_company_name(home, SLUG) == "Garner Health Inc"


def test_scout_new_rows_and_table_use_the_index_name(fx: PostingsFixture) -> None:
    response = scout_new.scout_new(fx.home_root, fx.target, now=NOW, peek=True)
    rows = response["postings"]["rows"]  # type: ignore[index]
    # 0110-10-03: ``company`` is the name; the board token is ``company_slug``.
    assert {row["company_slug"]: row["company"] for row in rows} == {SLUG: NAME, LONG_SLUG: LONG_NAME, PLAIN: "Osprey Lane"}
    assert all(row["company_name"] == row["company"] for row in rows)

    table = CliRunner().invoke(cli, fx.cli("--peek"))
    assert table.exit_code == 0, table.output
    for shown in (f"{NAME}:", f"{LONG_NAME}:", "Osprey Lane:"):
        assert shown in table.output, table.output
    for slug_shown in ("Garnerhealth", "Medallionakafirstlayerai", f"{SLUG}:"):
        assert slug_shown not in table.output, table.output


def test_the_jobs_api_rows_and_the_search_lines_use_the_index_name(fx: PostingsFixture) -> None:
    found = posting_search.search_postings(fx.home_root, fx.target, now=NOW)
    rows = found["postings"]["rows"]  # type: ignore[index]
    assert {row["company_slug"]: row["company"] for row in rows} == {SLUG: NAME, LONG_SLUG: LONG_NAME, PLAIN: "Osprey Lane"}
    assert all(row["company_name"] == row["company"] for row in rows)
    lines = posting_search.render(found)
    assert f"{NAME}: " in lines and f"{LONG_NAME}: " in lines and f"{SLUG}: " not in lines


def _row(company: str, url: str) -> PostingRow:
    return PostingRow(
        url=url, normalized_url=url, provider=ATSProvider.LEVER, board_token=company, company=company, title="Staff AI Engineer",
        location="Remote", published_at=None, content_sha256=None, source_kind=SourceKind.ATS, query_key=f"ats:lever:{company}",
    )


def test_every_api_shape_that_names_a_postings_company_gets_the_name(fx: PostingsFixture) -> None:
    from gigai.scout.find_jobs.company_names import company_display_name, index_company_name, with_company_names  # noqa: F401

    home = fx.home_root
    # The job page and the assessments list: a stored assessment, as its own serializer writes it.
    assess_base(fx.base, JOB)
    stored = read_quick_assessment(home, fx.target, fx.default_profile_id, JOB)
    assert stored is not None
    assessment = {**stored.to_json(), "job": {**stored.to_json()["job"], "company": SLUG}}  # type: ignore[dict-item]
    posting = _row(SLUG, JOB).to_json()
    payload = {
        "run_rows": {"postings": [{"posting": posting, "outcome": "new"}, {"posting": _row(PLAIN, "https://jobs.lever.co/osprey-lane/1").to_json(), "outcome": "new"}]},
        "assessments": {"assessments": [assessment]},
        "applications": {"applications": [{"event_id": "e1", "external_ref": JOB, "linked_posting": posting}, {"event_id": "e2", "linked_posting": None}]},
        "tailored": {"tailored_resumes": [{"profile_id": "p", "job_identity": JOB, "company": LONG_SLUG, "title": "Staff AI Engineer"}]},
        "answer": {"jobs": [{"job_identity": JOB, "title": "Staff AI Engineer", "company": SLUG, "url": JOB, "kind": "answered"}]},
        # Not a posting: a story's company is the user's own words; a watchlist entry carries its own name.
        "story": {"story_id": "s1", "title": "Moved inference", "company": SLUG, "role": "Staff Engineer"},
        "watch": {"company": SLUG, "board_token": SLUG, "provider": "lever"},
        "already": {"job_identity": JOB, "company": SLUG, "company_name": "As Given"},
    }
    before = json.dumps(payload, sort_keys=True)

    served = with_company_names(payload, home)

    assert json.dumps(payload, sort_keys=True) == before  # what a route keeps between requests is never written to
    assert [item["posting"]["company_name"] for item in served["run_rows"]["postings"]] == [NAME, "Osprey Lane"]  # type: ignore[index]
    assert served["assessments"]["assessments"][0]["job"]["company_name"] == NAME  # type: ignore[index]
    # 0110-10-03: ``company`` says the name; the token stays, as ``company_slug`` (a run's row says its own ``board_token``).
    job = served["assessments"]["assessments"][0]["job"]  # type: ignore[index]
    assert (job["company"], job["company_slug"]) == (NAME, SLUG)
    assert [(item["posting"]["company"], item["posting"]["company_slug"]) for item in served["run_rows"]["postings"]] == [(NAME, SLUG), ("Osprey Lane", PLAIN)]  # type: ignore[index]
    assert (served["already"]["company"], served["already"]["company_slug"]) == ("As Given", SLUG)  # type: ignore[index]
    assert assessment["job"]["company"] == SLUG  # the stored record is not rewritten
    assert served["applications"]["applications"][0]["linked_posting"]["company_name"] == NAME  # type: ignore[index]
    assert served["tailored"]["tailored_resumes"][0]["company_name"] == LONG_NAME  # type: ignore[index]
    assert served["answer"]["jobs"][0]["company_name"] == NAME  # type: ignore[index]
    assert "company_name" not in served["story"] and "company_name" not in served["watch"]  # type: ignore[operator]
    assert "company_slug" not in served["story"] and served["watch"] == payload["watch"]  # type: ignore[operator]
    assert served["already"]["company_name"] == "As Given"  # type: ignore[index]
    assert served["story"] is payload["story"]  # untouched parts are the same objects


def test_the_pdf_is_named_for_the_company_not_the_token(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """``stored_resume_pdf`` (the API's and the CLI's one path): the file name and the document title."""

    from gigai.scout import resume_pdf

    seen: dict[str, object] = {}

    def render(_sections, _header, *, company, **_kwargs):
        seen["company"] = company
        return "pdf"

    monkeypatch.setattr(resume_pdf, "_render", render)
    monkeypatch.setattr(resume_pdf, "_body", lambda _result: [])
    monkeypatch.setattr(resume_pdf, "pdf_header", lambda *_args, **_kwargs: None)
    stored = SimpleNamespace(
        resume=SimpleNamespace(profile_id=fx.default_profile_id), updated_at="2026-10-03T15:00:00Z", result=None,
        job=SimpleNamespace(company=LONG_SLUG, title="Staff AI Engineer"),
    )

    _rendered, file_name = resume_pdf.stored_resume_pdf(stored, home_root=fx.home_root, today=date(2026, 10, 3))  # type: ignore[arg-type]

    assert file_name == "medallion-staff-ai-engineer-2026-10-03.pdf" and seen["company"] == LONG_NAME


def test_the_cli_headings_say_the_company_name(fx: PostingsFixture) -> None:
    from gigai.scout.scout_cli import _company_shown

    assert _company_shown(fx.home_root, SLUG) == NAME and _company_shown(fx.home_root, PLAIN) == "Osprey Lane"
    assert _company_shown(None, "Customer.io") == "Customer.io"
