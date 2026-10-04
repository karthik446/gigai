"""0.1.10.7-fix2: ``gigai scout new`` names the company, not the board slug.

A Lever board "osprey-lane" is the company "Osprey Lane" everywhere a person
reads it (the table); the JSON keeps ``company`` (the slug, as documented) and
adds ``company_name``. Same rule as the UI's ``displayCompanyName``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout.find_jobs.ats_board_clients import BoardCache
from gigai.scout.find_jobs.company_index import CompanyIndex, board_list_url, index_stamp, refresh_company
from gigai.scout.find_jobs.watchlist import add_company_from_url
from gigai.scout.scout_new import display_company_name

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import add_resume, resolve_workpad_path, setup_and_init, start_server, stop_server, write_offline_find_jobs_config

_JOB = "https://jobs.lever.co/osprey-lane/ol-00001"


def _seed(home: Path, target: Path) -> None:
    seen = datetime.now(UTC) - timedelta(days=1)
    jobs = [{
        "id": "ol-00001", "text": "Software Engineer", "hostedUrl": _JOB, "categories": {"location": "Remote - United States"},
        "country": "US", "workplaceType": "remote", "descriptionPlain": "Osprey builds Python services. Remote within the US.",
        "createdAt": int(seen.timestamp() * 1000),
    }]
    add_company_from_url("https://jobs.lever.co/osprey-lane", home, target)
    cache = BoardCache(home / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)
    cache.store("lever", board_list_url("lever", "osprey-lane"), body=json.dumps(jobs).encode("utf-8"), etag=None, last_modified=None, marker=None)
    refresh_company(CompanyIndex.for_home(home), cache, ats="lever", slug="osprey-lane", observed_at=index_stamp(seen))


def test_the_display_rule_matches_the_ui() -> None:
    assert [display_company_name(name) for name in ("osprey-lane", "customerio", "Customer.io", "OneTrust", "", None)] == [
        "Osprey Lane", "Customerio", "Customer.io", "OneTrust", "", None,
    ]


def test_scout_new_prints_the_company_name_and_keeps_the_slug(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    _seed(home, target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        workpad = resolve_workpad_path(home, target)

        def run(*args: str) -> str:
            result = CliRunner().invoke(cli, ["scout", "new", "--peek", *args, "--home", str(home), "--target", str(target)])
            assert result.exit_code == 0, result.output
            return result.output

        table = run()
        assert "Osprey Lane: Software Engineer" in table and "osprey-lane:" not in table
        (row,) = json.loads(run("--json"))["postings"]["rows"]
        # 0110-10-03: ``company`` is the name; the slug is kept as ``company_slug``.
        assert (row["company"], row["company_slug"], row["company_name"]) == ("Osprey Lane", "osprey-lane", "Osprey Lane")
    finally:
        stop_server(server)
    assert_clean_and_healthy(workpad, home)
