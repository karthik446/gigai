"""0.1.11.2 UI models under node: the job address the store keeps, the company-lists body, the running dialog line.

Pure JavaScript (no React) run under the system ``node``; LOUD skip when ``node`` is not on PATH.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.find_jobs.contracts import normalize_url

SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

SCRIPT = """
const input = JSON.parse(process.argv[1]);
const addr = await import(input.addr);
const lists = await import(input.lists);
const model = await import(input.model);
const dialog = { count: 2, lowRank: null };
const one = { count: 1, lowRank: null };
const low = { count: 2, lowRank: { batch: 3 } };
console.log(JSON.stringify({
  normalized: input.urls.map((url) => addr.normalizeJobAddress(url)),
  notAUrl: addr.normalizeJobAddress("not a url"),
  resolvedKnown: addr.resolveJobId("https://a.example/x/?id=1", new Set(["https://a.example/x?id=1"])),
  resolvedAsGiven: addr.resolveJobId("https://a.example/x/?id=1", new Set(["https://a.example/x/?id=1"])),
  resolvedUnknown: addr.resolveJobId("https://A.example/x/?id=1&utm_source=z", new Set()),
  onDemand: addr.onDemandItemFor([{ job: { job_identity: "https://a.example/x?id=1", normalized_url: "https://a.example/x?id=1" } }], "https://a.example/x/?id=1"),
  onDemandNone: addr.onDemandItemFor([{ job: { job_identity: "https://a.example/y", normalized_url: "https://a.example/y" } }], "https://a.example/x"),
  body: lists.companyListsBody({ roles: ["r"], work_mode: "remote", cadence_days: 7, max_age_days: 14, model_target: "claude_cli", visa_sponsorship_required: true }, { exclude: ["A"], watch: ["B"] }),
  changed: [lists.companyListsChanged({ exclude_companies: ["A"] }, { exclude: ["A"], watch: [] }), lists.companyListsChanged({ exclude_companies: ["A"] }, { exclude: [], watch: [] })],
  lines: [model.assessingLine(dialog), model.assessingLine(one), model.assessingLine(low, true), model.assessingLine(dialog, true)],
}));
"""

URLS = [
    "https://boards.example.com/acme/careers?gh_jid=555",
    "https://boards.example.com/acme/careers/?gh_jid=555",
    "HTTPS://Boards.Example.com:443/acme/careers//?utm_source=x&gh_jid=555&ref=y#frag",
    "https://boards.example.com/",
    "https://boards.example.com/a/b/?z=2&b=1&b=0",
]


@pytest.fixture(scope="module")
def out() -> dict:
    if shutil.which("node") is None:
        pytest.fail("node is not on PATH: the UI model tests need it (LOUD skip refused)")
    payload = {
        "addr": (SRC / "jobAddress.js").as_uri(),
        "lists": (SRC / "companyListsModel.js").as_uri(),
        "model": (SRC / "postingsModel.js").as_uri(),
        "urls": URLS,
    }
    done = subprocess.run(["node", "--input-type=module", "-e", SCRIPT, json.dumps(payload)], capture_output=True, text=True, check=False, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_address_is_normalized_the_way_the_store_does(out: dict) -> None:
    assert out["normalized"] == [normalize_url(url) for url in URLS]
    assert out["normalized"][0] == out["normalized"][1] == "https://boards.example.com/acme/careers?gh_jid=555"
    assert out["notAUrl"] is None


def test_a_job_page_finds_the_job_under_the_address_the_store_keeps(out: dict) -> None:
    assert out["resolvedKnown"] == "https://a.example/x?id=1"
    assert out["resolvedAsGiven"] == "https://a.example/x/?id=1"
    assert out["resolvedUnknown"] == "https://a.example/x?id=1"


def test_an_on_demand_assessment_is_found_by_its_address(out: dict) -> None:
    assert out["onDemand"]["id"] == "https://a.example/x?id=1"
    assert out["onDemandNone"] is None


def test_the_company_lists_save_sends_the_other_preferences_back(out: dict) -> None:
    body = out["body"]
    assert body["exclude_companies"] == ["A"] and body["watch_companies"] == ["B"]
    assert body["roles"] == ["r"] and body["work_mode"] == "remote" and body["visa_sponsorship_required"] is True
    assert body["cadence_days"] == 7 and body["max_age_days"] == 14 and body["model_target"] == "claude_cli"
    assert out["changed"] == [False, True]


def test_the_running_dialog_says_assessing_n_postings(out: dict) -> None:
    assert out["lines"][0].startswith("Assessing 2 postings…") and out["lines"][1].startswith("Assessing 1 posting…")
    assert out["lines"][2].startswith("Assessing postings, the low-ranked ones included…") and out["lines"][3].startswith("Assessing 2 postings…")
    assert "Nothing has been assessed" not in " ".join(out["lines"])
