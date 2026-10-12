"""0.1.11.10 fix C: a stored job keeps its company, and the folders filed under ``company/`` can be moved back.

The incident: storing an edited resume for a job pasted by its board address (a Rippling posting, synthetic here) resolved it by a
page scrape (``generic``, company ``""``), so its folder became ``<jobs>/company/<role>`` instead of ``<jobs>/<company>/<role>``.

On the END outcome (the jobs folder's own file), synthetic homes only, no network, no model:

- a posting only the company index holds is read from there (company, title, the board's text, kind ``ats_board``), never scraped;
- a page scrape that names no company gets the company index's for the same address (text, kind and digest untouched), in a fresh
  resolution and in an already stored job (``resolve_tailor_job``); a job in no index keeps ``""`` (the layout's unknown-company folder);
- storing an edited resume for a job whose stored assessment lost its company files it under the company's folder, and for a job no
  index knows it says so (``folder_company_unknown``, and a line in the text output);
- ``gigai scout jobs-folder repair`` lists the folders under ``company/`` with their target and moves nothing (the tree is byte-identical);
  ``--apply`` moves each to ``<company>/<same name>`` (its files, the index and the record follow); a target that exists is a collision,
  reported and left alone; a job with no known company is unresolved and stays; a second ``--apply`` moves nothing.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.scout import jobs_folder
from gigai.scout.find_jobs import job_source
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessResumeInput, ResolvedJob
from gigai.scout.find_jobs.company_index import CompanyIndex, CompanyIndexEntry, IndexedPosting
from gigai.scout.find_jobs.contracts import normalize_url
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import TailorRequest, resolve_tailor_job

from tests.behaviors.scout_find_jobs.test_assessment_v9_flow import (  # noqa: F401 - `fx` is the fixture
    _JOB, _URL, PYTHON_LINE, _assess, _assessment_path, _v9_answer, fx,
)
from tests.support.posting_fixtures import PostingsFixture

_STAMP = "2026-10-01T09:00:00Z"
_ROLE_ID = "55febce8-e184-4683-978e-61024b78c853"
_RIPPLING = f"https://ats.rippling.com/orchard-labs/jobs/{_ROLE_ID}"
_OTHER = "https://apply.example.invalid/9001"
_PAGE = "A script-light page about the role. " * 8


@pytest.fixture(autouse=True)
def _pipeline_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PIPELINE_ENV, "off")


def _write_rippling(home: Path) -> None:
    posting = IndexedPosting(
        posting_id=_ROLE_ID, title="Forward Deployed Engineer, Lead", location="Remote (United States)", url=_RIPPLING, updated_at=None,
        content_sha256=None, first_seen=_STAMP, last_seen=_STAMP, published_at="2026-09-30T00:00:00Z", published_kind="posted",
    )
    CompanyIndex.for_home(home).write(CompanyIndexEntry(
        company="orchard-labs", ats="rippling", slug="orchard-labs", checked_at=_STAMP, etag=None, body_sha256=None, postings={_ROLE_ID: posting},
    ))


def _scrape(url: str) -> ResolvedJob:
    """What resolving the address by its page gave (the incident): a ``generic`` job with no company."""

    normalized = normalize_url(url)
    return ResolvedJob(
        job_identity=normalized, source_url=url, normalized_url=normalized, fetch_kind="generic", title="Forward Deployed Engineer, Lead",
        company="", location="", text=_PAGE, text_sha256="sha256:" + "ab" * 32,
    )


# --- the resolution ---------------------------------------------------------------------------


def test_a_scrape_with_no_company_gets_the_company_index_company(tmp_path: Path) -> None:
    _write_rippling(tmp_path)
    scraped = _scrape(_RIPPLING)
    resolved = job_source.resolve_job_for_assessment(
        AssessJobInput(job_url=_RIPPLING), home_root=tmp_path, target=tmp_path, open_client=_FakeClient, resolve=lambda *_args, **_kw: scraped,
    )
    assert resolved.company == "orchard-labs"
    assert (resolved.fetch_kind, resolved.text, resolved.text_sha256) == ("generic", _PAGE, scraped.text_sha256)
    # The folder is the company's, not the unknown-company one.
    assert jobs_folder.company_slug(resolved.company) == "orchard-labs"


class _FakeClient:
    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *_a: object) -> None:
        return None


def test_a_job_in_no_index_keeps_no_company_and_the_unknown_company_folder(tmp_path: Path) -> None:
    _write_rippling(tmp_path)
    scraped = replace(_scrape(_OTHER), title="Staff Engineer")
    resolved = job_source.resolve_job_for_assessment(
        AssessJobInput(job_url=_OTHER), home_root=tmp_path, target=tmp_path, open_client=_FakeClient, resolve=lambda *_args, **_kw: scraped,
    )
    assert resolved.company == ""
    assert jobs_folder.company_slug(resolved.company) == "company"  # the layout's documented folder for an unknown company


def test_a_stored_job_that_lost_its_company_gets_it_back_on_store(tmp_path: Path) -> None:
    _write_rippling(tmp_path)
    request = TailorRequest(job=AssessJobInput(job_url=_RIPPLING), resume=AssessResumeInput(profile_id=None))
    job = resolve_tailor_job(request, home_root=tmp_path, target=tmp_path, resolved_job=_scrape(_RIPPLING))
    assert job.company == "orchard-labs"
    unknown = resolve_tailor_job(
        TailorRequest(job=AssessJobInput(job_url=_OTHER), resume=AssessResumeInput(profile_id=None)), home_root=tmp_path, target=tmp_path, resolved_job=_scrape(_OTHER),
    )
    assert unknown.company == ""


def test_a_company_that_is_named_is_never_replaced(tmp_path: Path) -> None:
    _write_rippling(tmp_path)
    named = replace(_scrape(_RIPPLING), company="Orchard Labs, Inc.")
    assert job_source.with_indexed_company(tmp_path, named) is named


def test_a_posting_only_the_company_index_holds_is_read_from_it(fx: PostingsFixture) -> None:
    """Another board kind (the fixture's Lever board): in the company index and the board cache, in no role's posting store."""

    from gigai.scout import postings
    from gigai.scout.pipeline.store import pipeline_path

    pipeline_path(fx.home_root, fx.target).unlink()  # no posting store: only the company index holds the posting
    assert postings.posting_texts  # the reader the resolution uses
    held = job_source.company_index_posting(fx.home_root, fx.target, _JOB)
    assert held is not None
    assert (held.fetch_kind, held.job_identity) == ("ats_board", _JOB)
    assert held.company and held.title and "Harborlight" in held.text


def test_a_unknown_address_is_not_held(fx: PostingsFixture) -> None:
    assert job_source.company_index_posting(fx.home_root, fx.target, normalize_url("https://jobs.lever.co/nobody/none-1")) is None


# --- storing an edited resume -----------------------------------------------------------------


def _blank_company(fx: PostingsFixture) -> None:
    """Make the job's stored records what the incident left: a page scrape (``generic``) with no company."""

    root = _assessment_path(fx).parents[1]  # <project>/quick_assess: the assessments only (the stored resume is left as it is)
    for path in root.rglob(f"{_assessment_path(fx).stem}.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw.get("job"), dict):
            raw["job"].update({"company": "", "fetch_kind": "generic"})
            path.write_text(json.dumps(raw), encoding="utf-8")


def _invoke(fx: PostingsFixture, *args: str):
    return CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target)])


def _store(fx: PostingsFixture, tmp_path: Path, *more: str, drop: str = PYTHON_LINE):
    brief = _invoke(fx, "resume", "brief", "--job-url", _URL, "--json")
    assert brief.exit_code == 0, brief.output
    path = tmp_path / "handed-back.md"
    markdown = json.loads(brief.output.strip().splitlines()[-1])["resume"]["markdown"]
    kept = [line for line in markdown.splitlines() if drop not in line]  # an edit: the resume is changed, so it is stored again
    assert len(kept) < len(markdown.splitlines())
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return _invoke(fx, "resume", "store", "--in", str(path), "--job-url", _URL, "--as", "operator", *more)


def test_store_for_a_stored_job_files_it_under_its_company(fx: PostingsFixture, tmp_path: Path) -> None:
    _assess(fx, _v9_answer(fx))
    _blank_company(fx)
    result = _store(fx, tmp_path, "--json")
    assert result.exit_code == 0, result.output
    body = json.loads(result.output.strip().splitlines()[-1])
    assert body["job"]["company"]
    assert body["folder_company_unknown"] is False
    shown = body["job_folder"].replace("\\", "/")
    assert f"/{jobs_folder.company_slug(body['job']['company'])}/" in shown + "/" and "/company/" not in shown


def test_store_for_a_job_no_index_knows_says_so(fx: PostingsFixture, tmp_path: Path) -> None:
    _assess(fx, _v9_answer(fx))
    _blank_company(fx)
    for entry in list(CompanyIndex.for_home(fx.home_root).keys()):
        CompanyIndex.for_home(fx.home_root).delete(*entry)
    result = _store(fx, tmp_path)
    assert result.exit_code == 0, result.output
    assert "filed under company/" in result.output and "jobs-folder repair" in result.output
    as_json = _store(fx, tmp_path, "--json", drop="Python, Kubernetes, Terraform, PostgreSQL")
    assert json.loads(as_json.output.strip().splitlines()[-1])["folder_company_unknown"] is True


# --- the repair -------------------------------------------------------------------------------


def _job_dir(home: Path, relative: str, *, company: str, key: str, url: str, role: str = "Platform Engineer") -> Path:
    folder = jobs_folder.jobs_folder(home).path
    directory = folder / relative
    directory.mkdir(parents=True)
    (directory / "resume.md").write_text("# resume\n", encoding="utf-8")
    (directory / "cover-letter.md").write_text("mine\n", encoding="utf-8")
    (directory / jobs_folder.JOB_FILE).write_text(json.dumps({
        "schema_version": jobs_folder.JOB_SCHEMA, "job_key": key, "job_url": url, "identity_sha256": "sha256:00", "company": company, "role": role, "created": "2026-10-07",
    }), encoding="utf-8")
    return directory


def _index(home: Path, entries: dict[str, str]) -> None:
    folder = jobs_folder.jobs_folder(home).path
    jobs = {key: {"dir": relative, "files": {}} for key, relative in entries.items()}
    jobs_folder._save_index(home, folder, jobs)  # noqa: SLF001


def _tree(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _repair(home: Path, *args: str) -> dict:
    result = CliRunner().invoke(scout_group, ["jobs-folder", "repair", *args, "--home", str(home), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


@pytest.fixture
def misfiled(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "scout").mkdir(parents=True)
    _write_rippling(home)
    # 1: company in its own record; 2: company only in the company index (by the address); 3: target exists (collision);
    # 4: no company known anywhere; 5: already in the right place.
    _job_dir(home, "company/platform-engineer", company="Acme Works", key="p/resumes/job/k1", url="https://x.example.invalid/1")
    _job_dir(home, "company/forward-deployed-engineer-lead", company="", key="p/resumes/job/k2", url=_RIPPLING, role="Forward Deployed Engineer, Lead")
    _job_dir(home, "company/site-reliability-engineer", company="Acme Works", key="p/resumes/job/k3", url="https://x.example.invalid/3")
    _job_dir(home, "acme-works/site-reliability-engineer", company="Acme Works", key="p/resumes/job/k9", url="https://x.example.invalid/9")
    _job_dir(home, "company/staff-engineer-nowhere", company="", key="p/resumes/job/k4", url=_OTHER)
    _job_dir(home, "orchard-labs/data-engineer", company="Orchard Labs", key="p/resumes/job/k5", url="https://x.example.invalid/5")
    _index(home, {
        "p/resumes/job/k1": "company/platform-engineer", "p/resumes/job/k2": "company/forward-deployed-engineer-lead",
        "p/resumes/job/k3": "company/site-reliability-engineer", "p/resumes/job/k9": "acme-works/site-reliability-engineer",
        "p/resumes/job/k4": "company/staff-engineer-nowhere", "p/resumes/job/k5": "orchard-labs/data-engineer",
    })
    return home


def test_repair_is_a_dry_run_by_default_and_writes_nothing(misfiled: Path) -> None:
    before = _tree(misfiled)
    body = _repair(misfiled)
    assert _tree(misfiled) == before
    assert body["dry_run"] is True
    by_folder = {item["folder"]: item for item in body["folders"]}
    assert set(by_folder) == {
        "company/platform-engineer", "company/forward-deployed-engineer-lead", "company/site-reliability-engineer", "company/staff-engineer-nowhere",
    }
    assert (by_folder["company/platform-engineer"]["to"], by_folder["company/platform-engineer"]["status"]) == ("acme-works/platform-engineer", "move")
    assert by_folder["company/platform-engineer"]["source"] == "folder_record"
    assert (by_folder["company/forward-deployed-engineer-lead"]["to"], by_folder["company/forward-deployed-engineer-lead"]["source"]) == (
        "orchard-labs/forward-deployed-engineer-lead", "company_index",
    )
    assert by_folder["company/site-reliability-engineer"]["status"] == "collision"
    assert by_folder["company/staff-engineer-nowhere"]["status"] == "unresolved"
    assert body["counts"] == {"found": 4, "to_move": 2, "moved": 0, "collisions": 1, "unresolved": 1}


def test_repair_apply_moves_each_folder_and_never_overwrites(misfiled: Path) -> None:
    root = jobs_folder.jobs_folder(misfiled).path
    kept = (root / "acme-works/site-reliability-engineer/resume.md").read_bytes()
    body = _repair(misfiled, "--apply")
    assert body["counts"] == {"found": 4, "to_move": 0, "moved": 2, "collisions": 1, "unresolved": 1}
    assert (root / "acme-works/platform-engineer/resume.md").is_file() and (root / "acme-works/platform-engineer/cover-letter.md").read_text(encoding="utf-8") == "mine\n"
    assert (root / "orchard-labs/forward-deployed-engineer-lead/resume.md").is_file()
    assert not (root / "company/platform-engineer").exists() and not (root / "company/forward-deployed-engineer-lead").exists()
    # Collision and unresolved folders are untouched; the existing target is byte for byte what it was.
    assert (root / "company/site-reliability-engineer/resume.md").is_file() and (root / "company/staff-engineer-nowhere/resume.md").is_file()
    assert (root / "acme-works/site-reliability-engineer/resume.md").read_bytes() == kept
    # The index and the record follow the folder; an empty company in a record is filled.
    index = jobs_folder._load_index(misfiled, root)  # noqa: SLF001
    assert index["p/resumes/job/k1"]["dir"] == "acme-works/platform-engineer"
    assert index["p/resumes/job/k2"]["dir"] == "orchard-labs/forward-deployed-engineer-lead"
    assert index["p/resumes/job/k3"]["dir"] == "company/site-reliability-engineer"
    record = json.loads((root / "orchard-labs/forward-deployed-engineer-lead" / jobs_folder.JOB_FILE).read_text(encoding="utf-8"))
    assert record["company"] == "Orchard Labs" or record["company"] == "orchard-labs"


def test_repair_is_idempotent(misfiled: Path) -> None:
    _repair(misfiled, "--apply")
    root = jobs_folder.jobs_folder(misfiled).path
    after_first = _tree(root)
    again = _repair(misfiled, "--apply")
    assert again["counts"]["moved"] == 0 and again["counts"]["to_move"] == 0
    assert _tree(root) == after_first
    assert _repair(misfiled)["counts"]["to_move"] == 0


def test_repair_on_a_home_with_no_jobs_folder_says_nothing_to_repair(tmp_path: Path) -> None:
    home = tmp_path / "empty"
    (home / "scout").mkdir(parents=True)
    result = CliRunner().invoke(scout_group, ["jobs-folder", "repair", "--home", str(home)])
    assert result.exit_code == 0 and "Nothing to repair" in result.output


def test_the_repair_flags_go_with_repair_only(tmp_path: Path) -> None:
    for args in (["--apply"], ["migrate", "--apply"], ["repair", "--dry-run"]):
        result = CliRunner().invoke(scout_group, ["jobs-folder", *args, "--home", str(tmp_path), "--json"])
        assert result.exit_code == 1, (args, result.output)
        assert json.loads(result.output.strip().splitlines()[-1])["error"]["code"] == "invalid_value"


# --- fix C2: a first-party careers host names the company ---------------------------------------


@pytest.mark.parametrize(("url", "company"), [
    ("https://jobs.twilio.com/careers/job/1099554769401", "Twilio"),
    ("https://careers.example.co.uk/en/job/7", "Example"),
    ("https://www.jobs.example.com/x", "Example"),
    ("https://careers.example.com.au/x", "Example"),
])
def test_a_first_party_careers_host_names_the_company(tmp_path: Path, url: str, company: str) -> None:
    assert job_source.careers_host_company(tmp_path, url) == company
    filled = job_source.with_indexed_company(tmp_path, _scrape(url))
    assert filled.company == company and filled.fetch_kind == "generic"


@pytest.mark.parametrize("url", [
    "https://boards.greenhouse.io/x", "https://job-boards.greenhouse.io/x/jobs/1", "https://jobs.lever.co/x", "https://ats.rippling.com/x",
    "https://jobs.ashbyhq.com/x/1", "https://careers.smartrecruiters.com/x/1", "https://jobs.jobvite.com/x", "https://jobs.icims.com/x",
    "https://careers.bamboohr.com/x", "https://jobs.workable.com/x", "https://x.wd1.myworkdayjobs.com/en-US/site/job/1",
    "https://www.linkedin.com/jobs/view/1", "https://jobs.linkedin.com/x", "https://www.indeed.com/viewjob?jk=1", "https://careers.indeed.com/x",
    "https://www.glassdoor.com/job/1", "https://jobs.ziprecruiter.com/x", "https://wellfound.com/jobs/1", "https://jobs.dice.com/x",
    "https://careers.monster.com/x", "https://jobs.simplyhired.com/x", "https://x.recruitee.com/o/y", "https://jobs.teamtailor.com/x",
    "https://example.com/jobs/1", "https://a.jobs.example.com/1",
])
def test_an_ats_or_job_board_host_never_names_a_company(tmp_path: Path, url: str) -> None:
    assert job_source.careers_host_company(tmp_path, url) is None
    assert job_source.with_indexed_company(tmp_path, _scrape(url)).company == ""


def test_a_malformed_address_names_no_company(tmp_path: Path) -> None:
    assert job_source.careers_host_company(tmp_path, "not a url") is None and job_source.careers_host_company(tmp_path, None) is None


def test_the_careers_host_never_replaces_a_named_company_or_an_index_company(tmp_path: Path) -> None:
    named = replace(_scrape("https://jobs.twilio.com/careers/job/1"), company="Twilio Inc.")
    assert job_source.with_indexed_company(tmp_path, named) is named
    _write_rippling(tmp_path)
    assert job_source.with_indexed_company(tmp_path, _scrape(_RIPPLING)).company == "orchard-labs"


def test_resolve_tailor_job_fills_the_company_from_the_careers_host(tmp_path: Path) -> None:
    url = "https://jobs.twilio.com/careers/job/1099554769401"
    request = TailorRequest(job=AssessJobInput(job_url=url), resume=AssessResumeInput(profile_id=None))
    assert resolve_tailor_job(request, home_root=tmp_path, target=tmp_path, resolved_job=_scrape(url)).company == "Twilio"


def test_repair_lists_and_moves_a_folder_whose_company_is_its_careers_host(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "scout").mkdir(parents=True)
    url = "https://jobs.twilio.com/careers/job/1099554769401"
    _job_dir(home, "company/staff-software-engineer-twilio", company="", key="p/resumes/job/t1", url=url, role="Staff Software Engineer | Twilio")
    _job_dir(home, "company/board-job", company="", key="p/resumes/job/t2", url="https://boards.greenhouse.io/x/jobs/1")
    _index(home, {"p/resumes/job/t1": "company/staff-software-engineer-twilio", "p/resumes/job/t2": "company/board-job"})
    root = jobs_folder.jobs_folder(home).path
    before = _tree(root)
    dry = _repair(home)
    assert _tree(root) == before
    by_folder = {item["folder"]: item for item in dry["folders"]}
    twilio = by_folder["company/staff-software-engineer-twilio"]
    assert (twilio["to"], twilio["status"], twilio["source"], twilio["company"]) == ("twilio/staff-software-engineer-twilio", "move", "careers_host", "Twilio")
    assert by_folder["company/board-job"]["status"] == "unresolved"
    applied = _repair(home, "--apply")
    assert applied["counts"]["moved"] == 1
    assert (root / "twilio/staff-software-engineer-twilio/resume.md").is_file() and (root / "company/board-job/resume.md").is_file()
    assert json.loads((root / "twilio/staff-software-engineer-twilio" / jobs_folder.JOB_FILE).read_text(encoding="utf-8"))["company"] == "Twilio"
    after = _tree(root)
    assert _repair(home, "--apply")["counts"]["moved"] == 0 and _tree(root) == after
