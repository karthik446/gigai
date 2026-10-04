"""0110-8-11 over HTTP: every response that names a posting's company carries the company index's name beside it.

One real server. A posting assessed by its board URL is stored with its board
token as the company ("shell"); the company index knows the name. The
assessment the job page reads (``POST /api/assess``) and the assessments list
(``GET /api/assessments``) both return ``company_name``; the token stays in
``company``. The UI's ``displayCompanyName`` then shows that name everywhere
it showed the token (run under node).
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from gigai.scout.find_jobs.api import static as static_module

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import add_resume, resolve_workpad_path, setup_and_init, start_server, stop_server, write_offline_find_jobs_config

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
_URL = "https://boards.greenhouse.io/shell/jobs/303"


def _index_names_the_company(home: Path, name: str) -> None:
    """A synthetic company index file for the ``shell`` board, as ``sources update`` writes one (sorted keys, no postings)."""

    root = home / "cache" / "scout" / "companies"
    root.mkdir(parents=True, exist_ok=True)
    entry = {
        "ats": "greenhouse", "body_sha256": None, "changed_at": "2026-10-02T15:00:00.000000Z", "checked_at": "2026-10-02T15:00:00.000000Z",
        "company": name, "etag": None, "last_modified": None, "postings": {}, "schema_version": "scout-company-index:1", "slug": "shell",
    }
    (root / "greenhouse:shell.json").write_text(json.dumps(entry, separators=(",", ":"), sort_keys=True), encoding="utf-8")


def test_the_assessment_and_the_assessments_list_carry_the_index_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        workpad = resolve_workpad_path(home, target)
        client = server.client
        assessed = client.post("/api/assess", json={"job": {"job_url": _URL}})
        assert assessed.status_code == 200, assessed.text
        # 0110-10-03: ``company`` is the name; the board token is ``company_slug``.
        first = assessed.json()["job"]
        token = first["company_slug"]
        assert token == "shell"
        # No index yet: the slug rule, as the UI showed it before.
        assert first["company"] == first["company_name"] == token.capitalize()

        _index_names_the_company(home, "Shell Robotics")

        (item,) = client.get("/api/assessments").json()["items"]
        assert (item["job"]["company"], item["job"]["company_slug"], item["job"]["company_name"]) == ("Shell Robotics", token, "Shell Robotics")
        again = client.post("/api/assess", json={"job": {"job_url": _URL}}).json()
        assert (again["job"]["company"], again["job"]["company_slug"], again["job"]["company_name"]) == ("Shell Robotics", token, "Shell Robotics")
    finally:
        stop_server(server)
    assert_clean_and_healthy(workpad, home)


_SCRIPT = """
const display = await import(process.argv[1] + "/display.js");
const before = [display.displayCompanyName("garnerhealth"), display.atCompany("medallionakafirstlayerai")];
const payload = {
  items: [{ job: { job_identity: "u1", company: "garnerhealth", company_name: "Garner Health" } }],
  postings: { rows: [{ company: "medallionakafirstlayerai", company_name: "Medallion" }, { company: "osprey-lane", company_name: "Osprey Lane" }] },
  story: { company: "acme" },
};
const same = display.rememberCompanyNames(payload) === payload;
console.log(JSON.stringify({
  before, same,
  after: [display.displayCompanyName("garnerhealth"), display.atCompany("medallionakafirstlayerai"), display.displayCompanyName("osprey-lane")],
  untouched: [display.displayCompanyName("acme"), display.displayCompanyName("Customer.io"), display.displayCompanyName(""), display.displayCompanyName(null)],
}));
"""


def test_the_ui_shows_the_name_the_api_gave_wherever_it_showed_the_token() -> None:
    if shutil.which("node") is None:
        pytest.skip("LOUD: node is not on PATH; the company-name UI model was NOT checked")
    done = subprocess.run(["node", "--input-type=module", "-e", _SCRIPT, str(UI_SRC)], capture_output=True, text=True, timeout=60, check=True)
    out = json.loads(done.stdout)
    assert out["before"] == ["Garnerhealth", " at Medallionakafirstlayerai"]  # the slug rule: what the grid showed
    assert out["same"] is True
    assert out["after"] == ["Garner Health", " at Medallion", "Osprey Lane"]
    assert out["untouched"] == ["Acme", "Customer.io", "", None]
    api = (UI_SRC / "api.js").read_text(encoding="utf-8")
    assert "return rememberCompanyNames(payload);" in api  # every response passes through it
