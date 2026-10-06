"""0.1.11.2 after-release #2/#10: the job page's address is read the way the store keeps it (jobAddress.js, under node)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.contracts import normalize_url

UI_SRC = Path(__file__).resolve().parents[2] / "src" / "gigai" / "scout" / "ui" / "src"
ADDRESSES = [
    "https://boards.greenhouse.io/acme/careers/?gh_jid=5550101",
    "https://boards.greenhouse.io/acme/careers?gh_jid=5550101",
    "HTTPS://Boards.Greenhouse.io:443/acme/careers//?utm_source=x&gh_jid=5550101&b=2&a=1#frag",
    "https://example.com/jobs/7/",
]


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the UI model test cannot run")
    script = (
        'import * as m from %s; const a = %s;'
        ' console.log(JSON.stringify({normal: a.map(m.normalizeJobAddress),'
        ' resolved: m.resolveJobId(a[0], [a[1]]), fresh: m.resolveJobId(a[0], []), bad: m.normalizeJobAddress("nope")}));'
    ) % (json.dumps((UI_SRC / "jobAddress.js").as_uri()), json.dumps(ADDRESSES))
    done = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_page_normalises_the_address_exactly_as_the_store_does(out: dict) -> None:
    assert out["normal"] == [normalize_url(address) for address in ADDRESSES]
    assert out["normal"][0] == out["normal"][1]
    assert out["bad"] is None


def test_a_slash_before_the_query_finds_the_known_job(out: dict) -> None:
    assert out["resolved"] == ADDRESSES[1]
    assert out["fresh"] == out["normal"][0]
