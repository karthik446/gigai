"""0.1.11.6 (APPLIED-01): a job page reads its ONE posting by its address, the model run under node.

``ui/src/postingsModel.js`` is plain JavaScript: this runs it under the system ``node``. LOUD skip without ``node``.
What the server answers to that read is pinned in tests/behaviors/scout_pipeline/test_posting_by_address.py; the real
page in tests/ui/test_job_page_applied_by_url.py.

Pinned: the query is ``job=<address>&limit=1`` (the address encoded once, nothing of a list filter in it); a pasted
posting's ``text:`` identity (or nothing) has no query; the job page sends that query and no longer searches a page
of the Jobs list (the first 200 rows, then the removed ones) for its posting.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
ADDRESS = "https://jobs.example/acme/jobs/101?gh_jid=101&team=a b"

SCRIPT = """
import * as m from MODEL_URL;

const out = {};
out.query = m.postingByAddressQuery(ADDRESS);
out.back = new URLSearchParams(out.query).get("job");
out.none = [m.postingByAddressQuery("text:sha256:" + "a".repeat(64)), m.postingByAddressQuery(""), m.postingByAddressQuery(null), m.postingByAddressQuery(undefined)];
out.upper = m.postingByAddressQuery("HTTPS://Jobs.Example/x");
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the posting-by-address model was NOT run")
    script = SCRIPT.replace("MODEL_URL", json.dumps((UI_SRC / "postingsModel.js").resolve().as_uri())).replace("ADDRESS", json.dumps(ADDRESS))
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_the_query_names_the_one_posting_and_nothing_else(out: dict) -> None:
    assert out["query"] == "job=https%3A%2F%2Fjobs.example%2Facme%2Fjobs%2F101%3Fgh_jid%3D101%26team%3Da+b&limit=1"
    assert out["back"] == ADDRESS, "the server reads the address back whole"
    assert out["upper"] == "job=HTTPS%3A%2F%2FJobs.Example%2Fx&limit=1", "as given: the server normalizes it"


def test_what_is_no_web_address_has_no_query(out: dict) -> None:
    assert out["none"] == [None, None, None, None], "a pasted posting (text:...) has no row: nothing is asked"


def test_the_job_page_reads_by_address_and_searches_no_list() -> None:
    view = (UI_SRC / "views" / "FindJobsView.jsx").read_text(encoding="utf-8")
    assert "getPostings(byAddress)" in view and "postingByAddressQuery(jobRouteId)" in view
    assert view.count("getPostings(") == 1, "one postings read for a job page"
    for gone in ("MAX_LOOKUP_ROWS", "removed: true", "postingsQuery("):
        assert gone not in view, f"the job page still searches a list ({gone})"
