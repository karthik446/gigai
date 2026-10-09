"""0.1.11.8: ``gigai scout jobs assess <job URL>`` assesses a posting that only the company index holds.

The release blocker: a posting `gigai scout jobs search` lists, on a board in the
index, whose title is in no profile's list. ``jobs assess <its URL>`` answered
``nothing_to_assess`` / ``not_found`` with exit 0 and no model call: the named
URL was looked up in the profiles' read model only.

- a named URL the company index holds and no profile's list does is assessed AS
  THE DEFAULT PROFILE (``--profile`` names another), through the same path as a
  list posting; the question says so and nothing is assessed without the yes;
- for each of the nine systems, the stored posting URL, its tracking-parameter /
  trailing-slash / host-case spellings and the system's public job page address
  all name the stored posting, with no model call;
- the same address of a posting a profile's list holds is that list's row;
- a URL in no board is still ``not_found``, and the command exits non-zero when
  every named URL is not found or failed (a mix keeps exit 0).

Every posting, company and person is made up. No network, no real home, and a
model that raises when called (but in the two tests that approve).
"""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import posting_search, postings
from gigai.scout.find_jobs.company_index import board_list_url, index_stamp, refresh_company
from gigai.scout.find_jobs.job_state import normalize_job_identity
from gigai.scout.quick_assess import read_quick_assessment

from tests.support.posting_fixtures import NOW, TITLE_BOTH, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job, posting_text

#: A title in no profile's list.
TITLE = "Office Coordinator"
BOARD = "quiet-harbor"
_UUID = "d4ea384f-e664-47ff-a164-e797012a8f2e"
_PIN = "3ab1fb8a-6f79-4e03-87c3-9dbfba1b2c3d"
_GEM = "am9icG9zdDr2RofMQLIv8cDea5dLA-SE"
_ASHBY = "5b1f0c1e-2a55-4f0e-9d7c-6c1d2f3a4b5c"
_TEXT = posting_text(7)


def _bodies(title: str = TITLE) -> dict[str, tuple[object, str]]:
    """``system -> (the board body as its public feed gives it, the posting URL that body states)``."""

    posted = "2026-10-01T12:00:00Z"
    lever = lever_job(BOARD, 7, title=title, created=days_ago(2))
    return {
        "greenhouse": (
            {"jobs": [{"id": 4410007, "title": title, "absolute_url": f"https://boards.greenhouse.io/{BOARD}/jobs/4410007", "location": {"name": "Remote - United States"}, "updated_at": posted}]},
            f"https://boards.greenhouse.io/{BOARD}/jobs/4410007",
        ),
        "lever": ([lever], str(lever["hostedUrl"])),
        "ashby": (
            {"jobs": [{
                "id": _ASHBY, "title": title, "location": "Remote - United States", "isListed": True, "isRemote": True, "workplaceType": "Remote",
                "publishedAt": "2026-10-01T12:00:00.000+00:00", "jobUrl": f"https://jobs.ashbyhq.com/{BOARD}/{_ASHBY}",
                "applyUrl": f"https://jobs.ashbyhq.com/{BOARD}/{_ASHBY}/application", "descriptionPlain": _TEXT, "descriptionHtml": f"<p>{_TEXT}</p>",
            }]},
            f"https://jobs.ashbyhq.com/{BOARD}/{_ASHBY}",
        ),
        "workable": (
            {"name": "Quiet Harbor", "jobs": [{
                "title": title, "shortcode": "198558616F", "url": "https://apply.workable.com/j/198558616F", "published_on": "2026-10-01",
                "country": "United States", "city": "Denver", "state": "Colorado", "description": f"<p>{_TEXT}</p>",
            }]},
            "https://apply.workable.com/j/198558616F",
        ),
        "rippling": (
            {"items": [{
                "id": _UUID, "name": title, "url": f"https://ats.rippling.com/{BOARD}/jobs/{_UUID}",
                "locations": [{"name": "Remote (United States)", "countryCode": "US", "workplaceType": "REMOTE"}],
            }], "totalItems": 1},
            f"https://ats.rippling.com/{BOARD}/jobs/{_UUID}",
        ),
        "gem": (
            [{
                "id": _GEM, "title": title, "absolute_url": f"https://jobs.gem.com/{BOARD}/{_GEM}", "content_plain": _TEXT,
                "first_published_at": posted, "updated_at": posted, "location": {"name": "Denver, United States"}, "location_type": "remote",
            }],
            f"https://jobs.gem.com/{BOARD}/{_GEM}",
        ),
        "recruitee": (
            {"offers": [{
                "id": 1800123, "title": title, "slug": "office-coordinator", "careers_url": "https://careers.quiet-harbor.example/o/office-coordinator",
                "location": "Denver, United States", "country_code": "US", "remote": True, "published_at": "2026-10-01 09:00:00 UTC",
                "description": f"<p>{_TEXT}</p>",
            }]},
            "https://careers.quiet-harbor.example/o/office-coordinator",
        ),
        "pinpoint": (
            {"data": [{
                "id": 7, "title": title, "url": f"https://{BOARD}.pinpointhq.com/en/postings/{_PIN}",
                "location": {"name": "Denver, Colorado", "city": "Denver", "province": "Colorado", "country": "United States"},
                "workplace_type": "fully_remote", "description": f"<p>{_TEXT}</p>",
            }]},
            f"https://{BOARD}.pinpointhq.com/en/postings/{_PIN}",
        ),
        "breezy": (
            [{
                "id": "dae417aa9de2", "friendly_id": "dae417aa9de2-office-coordinator", "name": title,
                "url": f"https://{BOARD}.breezy.hr/p/dae417aa9de2-office-coordinator", "published_date": posted,
                "location": {"name": "Remote, United States", "country": {"name": "United States", "id": "US"}, "is_remote": True},
            }],
            f"https://{BOARD}.breezy.hr/p/dae417aa9de2-office-coordinator",
        ),
    }


#: ``system -> the addresses a user has for the stored posting``: the system's public job page, and other spellings.
_PUBLIC: dict[str, tuple[str, ...]] = {
    "greenhouse": (f"https://job-boards.greenhouse.io/{BOARD}/jobs/4410007", f"https://boards.greenhouse.io/{BOARD}/jobs/4410007?gh_src=abc123"),
    "lever": (f"https://jobs.lever.co/{BOARD}/{BOARD}-00007/apply", f"https://jobs.lever.co/{BOARD}/{BOARD}-00007?lever-source=LinkedIn"),
    "ashby": (f"https://jobs.ashbyhq.com/{BOARD}/{_ASHBY}/application", f"https://jobs.ashbyhq.com/{BOARD}/{_ASHBY}?utm_source=x"),
    "workable": (f"https://apply.workable.com/{BOARD}/j/198558616F/", f"https://apply.workable.com/{BOARD}/j/198558616F/apply/", "https://apply.workable.com/j/198558616F/"),
    "rippling": (f"https://ats.rippling.com/{BOARD}/jobs/{_UUID}/", f"https://ats.rippling.com/{BOARD}/jobs/{_UUID}?utm_source=share", f"https://ats.rippling.com/{BOARD}/jobs/{_UUID}/apply"),
    "gem": (f"https://jobs.gem.com/{BOARD}/{_GEM}/", f"https://jobs.gem.com/{BOARD}/{_GEM}?utm_medium=email"),
    "recruitee": (f"https://{BOARD}.recruitee.com/o/office-coordinator", f"https://{BOARD}.recruitee.com/o/office-coordinator/c/new"),
    "pinpoint": (f"https://{BOARD}.pinpointhq.com/postings/{_PIN}", f"https://{BOARD}.pinpointhq.com/en/postings/{_PIN}/"),
    "breezy": (f"https://{BOARD}.breezy.hr/p/dae417aa9de2-office-coordinator/", f"https://{BOARD}.breezy.hr/p/dae417aa9de2", f"https://{BOARD}.breezy.hr/p/dae417aa9de2-office-coordinator/apply"),
}
SYSTEMS = tuple(_bodies())


def _seed(fx: PostingsFixture, system: str, *, title: str = TITLE, watch: bool = False) -> str:
    """One board of ``system`` in the company index (its body in the board cache); the posting URL as stored."""

    body, url = _bodies(title)[system]
    if watch:
        from gigai.scout.find_jobs import providers
        from gigai.scout.find_jobs.watchlist import add_company_from_url

        add_company_from_url(providers.board_url(system, BOARD), fx.home_root, fx.target)
    fx.cache.store(system, board_list_url(system, BOARD), body=json.dumps(body).encode("utf-8"), etag=None, last_modified=None, marker=None)
    refresh_company(fx.index, fx.cache, ats=system, slug=BOARD, observed_at=index_stamp(NOW - timedelta(minutes=30)))
    return url


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fixture.seed("acme", [lever_job("acme", 1)], seen_at=days_ago(1))  # one posting both profiles' lists hold
    postings.refresh(fixture.home_root, fixture.target, now=NOW)
    return fixture


def _no_model(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_prompt: str) -> str:
        raise AssertionError("a model was called for a question")

    monkeypatch.setattr(fx.base.model, "answer", refuse)


def _invoke(fx: PostingsFixture, *args: str):
    return CliRunner().invoke(cli, ["scout", "jobs", "assess", *args, "--home", str(fx.home_root), "--target", str(fx.target)])


def _json(fx: PostingsFixture, *args: str, exit_code: int = 0) -> dict:
    result = _invoke(fx, *args, "--json")
    assert result.exit_code == exit_code, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _default_label(response: dict) -> str:
    return next(item["label"] for item in response["profiles"] if item["is_default"])


@pytest.mark.parametrize("system", SYSTEMS)
def test_the_stored_url_of_each_system_resolves_without_a_model_call(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch, system: str) -> None:
    url = _seed(fx, system)
    _no_model(fx, monkeypatch)
    identity = normalize_job_identity(url)

    ask = _json(fx, url)

    assert ask["status"] == "ask" and ask["not_found"] == [] and ask["assessed"] is None, ask
    assert ask["counts"] == {**ask["counts"], "selected": 1, "to_assess": 1, "not_found": 0, "batch": 1}
    (row,) = ask["postings"]["rows"]
    assert row["job_identity"] == identity and row["title"] == TITLE and row["state"] == "not_assessed"
    # No profile's list holds it: it is assessed as the default profile, and the question says so.
    assert row["profile_id"] == fx.default_profile_id and row["profiles"] == []
    label = _default_label(ask)
    assert ask["question"]["by_profile"] == [{"profile_id": fx.default_profile_id, "count": 1}]
    assert f"as {label}" in ask["question"]["text"] and "no profile's list" in ask["question"]["text"]
    assert ask["question"]["yes"]["api"]["body"] == {"approve": True, "jobs": [identity]}
    assert ask["model_input_summary"]["profiles"][0]["profile_id"] == fx.default_profile_id
    assert read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, identity) is None

    # The public job page, a tracking parameter, a trailing slash, another host case: the same posting.
    host = url.split("/")[2]
    for spelled in (*_PUBLIC[system], url.replace(host, host.upper(), 1), url + "?utm_campaign=x&ref=y"):
        again = _json(fx, spelled)
        assert again["status"] == "ask" and again["not_found"] == [], spelled
        assert [item["job_identity"] for item in again["postings"]["rows"]] == [identity], spelled
        assert again["question"]["yes"]["api"]["body"]["jobs"] == [identity], spelled


@pytest.mark.parametrize("system", SYSTEMS)
def test_a_public_address_of_a_posting_a_profile_holds_is_that_profiles_row(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch, system: str) -> None:
    url = _seed(fx, system, title=TITLE_BOTH, watch=True)
    postings.refresh(fx.home_root, fx.target, now=NOW)
    _no_model(fx, monkeypatch)
    identity = normalize_job_identity(url)
    held = _json(fx, url)
    assert [item["job_identity"] for item in held["postings"]["rows"]] == [identity]
    assert held["postings"]["rows"][0]["profiles"], "the fixture's posting is in a profile's list"

    for spelled in _PUBLIC[system]:
        ask = _json(fx, spelled)
        assert ask["status"] == "ask" and ask["not_found"] == [], spelled
        assert ask["postings"]["rows"] == held["postings"]["rows"], spelled
        assert ask["question"]["text"] == held["question"]["text"], spelled


def test_the_terminal_asks_first_and_names_the_profile(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _seed(fx, "rippling")
    _no_model(fx, monkeypatch)

    result = _invoke(fx, url)

    assert result.exit_code == 0, result.output
    label = _default_label(_json(fx, url))
    assert f"Assess 1 posting ({label} 1)?" in result.output
    assert f"1 is in no profile's list: assessed as {label} (the default profile)." in result.output
    assert "Nothing was assessed. Yes: run the same command with --yes." in result.output
    assert TITLE in result.output and "not in the stored postings" not in result.output


def test_yes_assesses_the_index_only_posting_as_the_default_profile(fx: PostingsFixture) -> None:
    url = _seed(fx, "rippling")
    # Rippling's list has no description: the stored detail is what the model reads.
    from gigai.scout.find_jobs import providers

    detail = {"uuid": _UUID, "name": TITLE, "description": {"role": f"<p>{_TEXT}</p>"}, "createdOn": "2026-10-01T12:00:00-07:00"}
    fx.cache.store("rippling", providers.spec("rippling").detail_url.format(token=BOARD, id=_UUID), body=json.dumps(detail).encode("utf-8"), etag=None, last_modified=None, marker=None)
    calls = fx.base.model.calls

    done = _json(fx, f"{url}/?utm_source=share", "--yes")

    assert done["status"] == "assessed" and done["not_found"] == [], done
    assert done["assessed"] == {**done["assessed"], "requested": 1, "assessed": 1, "failed": []}
    assert fx.base.model.calls == calls + 1
    (row,) = done["postings"]["rows"]
    assert row["job_identity"] == url and row["profile_id"] == fx.default_profile_id and row["state"] != "not_assessed"
    stored = read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, url)
    assert stored is not None and stored.origin == "job_page" and stored.job.fetch_kind == "ats_board"
    assert _TEXT in (stored.posting_text or "")
    # The second profile was not asked for: nothing is stored under it.
    assert read_quick_assessment(fx.home_root, fx.target, fx.second_profile_id, url) is None
    # Assessed and current: the same command has nothing left, and it is not an error.
    nothing = _json(fx, url)
    assert nothing["status"] == "nothing_to_assess" and nothing["counts"]["already_current"] == 1 and nothing["not_found"] == []
    assert [item["state"] for item in nothing["postings"]["rows"]] == [] and fx.base.model.calls == calls + 1


def test_profile_names_the_profile_an_index_only_posting_is_assessed_as(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _seed(fx, "gem")
    _no_model(fx, monkeypatch)

    ask = _json(fx, url, "--profile", fx.second_profile_id)

    assert ask["status"] == "ask" and ask["question"]["by_profile"] == [{"profile_id": fx.second_profile_id, "count": 1}]
    assert ask["postings"]["rows"][0]["profile_id"] == fx.second_profile_id
    second = next(item["label"] for item in ask["profiles"] if item["profile_id"] == fx.second_profile_id)
    assert f"assessed as {second}." in ask["question"]["text"] and "default profile" not in ask["question"]["text"]


def test_a_url_in_no_board_is_not_found_and_the_command_exits_non_zero(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _seed(fx, "rippling")
    _no_model(fx, monkeypatch)
    missing = f"https://ats.rippling.com/{BOARD}/jobs/00000000-0000-4000-8000-000000000000"
    elsewhere = "https://jobs.lever.co/nobody-here/abc-123"

    # Every named URL is not found: exit 1, the same object, with and without the yes.
    for args in ((missing,), (missing, elsewhere, "--yes")):
        none = _json(fx, *args, exit_code=1)
        assert none["status"] == "nothing_to_assess" and none["not_found"] == [item for item in args if item != "--yes"]
        assert none["postings"]["rows"] == [] and none["counts"]["not_found"] == len(none["not_found"])
    plain = _invoke(fx, missing)
    assert plain.exit_code == 1 and f"not in the stored postings: {missing}" in plain.output

    # A mix keeps exit 0: the found one is asked about, the other is named.
    mixed = _json(fx, url, missing)
    assert mixed["status"] == "ask" and mixed["not_found"] == [missing] and len(mixed["postings"]["rows"]) == 1
    # A posting with a current assessment is not a failure, and neither is a filter that selects nothing.
    assert _invoke(fx, "--query", "no-such-word-anywhere", "--json").exit_code == 0


def test_a_posting_its_board_no_longer_lists_is_not_found(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _seed(fx, "rippling")
    _no_model(fx, monkeypatch)
    assert _json(fx, url)["status"] == "ask"
    # The board's next list no longer has it: the index keeps the posting, marked removed.
    fx.cache.store("rippling", board_list_url("rippling", BOARD), body=json.dumps({"items": [], "totalItems": 0}).encode("utf-8"), etag=None, last_modified=None, marker=None)
    refresh_company(fx.index, fx.cache, ats="rippling", slug=BOARD, observed_at=index_stamp(NOW))

    gone = _json(fx, url, exit_code=1)

    assert gone["status"] == "nothing_to_assess" and gone["not_found"] == [url] and gone["postings"]["rows"] == []


def test_every_named_url_failing_exits_non_zero_and_a_mix_does_not(fx: PostingsFixture) -> None:
    url = _seed(fx, "rippling")  # no description stored, and no request is made in a test: its text cannot be had
    held = job_url("acme", 1)

    failed = _json(fx, url, "--yes", exit_code=1)
    assert failed["status"] == "assessed" and failed["assessed"]["assessed"] == 0
    assert [item["job_identity"] for item in failed["assessed"]["failed"]] == [url]
    plain = _invoke(fx, url, "--yes")
    assert plain.exit_code == 1 and "not assessed (" in plain.output

    mixed = _json(fx, url, held, "--yes")
    assert mixed["assessed"]["assessed"] == 1 and len(mixed["assessed"]["failed"]) == 1


def test_the_api_builder_answers_the_same_for_named_jobs(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """``POST /api/postings/assess {jobs}`` is this builder: the index-only posting is selected, a stranger is not found."""

    url = _seed(fx, "workable")
    _no_model(fx, monkeypatch)
    public = f"https://apply.workable.com/{BOARD}/j/198558616F/"

    built = posting_search.assess_these(fx.home_root, fx.target, jobs=[public, "https://jobs.lever.co/nobody-here/abc-123"], now=NOW)

    posting_search.check_response(built)
    assert built["status"] == "ask" and built["not_found"] == ["https://jobs.lever.co/nobody-here/abc-123"]
    assert [item["job_identity"] for item in built["postings"]["rows"]] == [url]
    assert built["question"]["yes"]["api"]["body"]["jobs"] == [url, "https://jobs.lever.co/nobody-here/abc-123"]
