"""0.1.11.2 SIZE: a Jobs row's description is a 400-character preview the server cut; the job page must say so.

`postingsModel.postingJob` marks a preview the server ended with "…" (`text_cut`), and `jobModel.jdExcerpt` then reports
`truncated` (the page's "This is the start of the posting. Open posting for the rest" line) although the preview is one short
paragraph. A whole text is not told it was cut, and `target/limit: Infinity` returns it whole with its paragraphs and lines.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.scout_new import DESCRIPTION_CHARS, _excerpt

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

SCRIPT = """
const jobModel = await import(process.argv[1]);
const postings = await import(process.argv[2]);
const input = JSON.parse(process.argv[3]);
const job = postings.postingJob({ job_identity: "https://jobs.example.test/acme/1", description: input.cutDescription });
const plain = postings.postingJob({ job_identity: "https://jobs.example.test/acme/2", description: input.shortDescription });
console.log(JSON.stringify({
  textCut: [job.posting.text_cut, plain.posting.text_cut],
  cutExcerpt: jobModel.jdExcerpt(job.posting.text, { cut: job.posting.text_cut }),
  plainExcerpt: jobModel.jdExcerpt(plain.posting.text, { cut: plain.posting.text_cut }),
  whole: jobModel.jdExcerpt(input.whole, { target: Infinity, limit: Infinity }),
}));
"""

WHOLE = "About the role.\n\nREQUIRED SKILLS\n- Five years of Python\n- Postgres\n\nNICE TO HAVE\n- Rust\n" + ("Filler line about the team.\n" * 60)


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the description cut model was NOT run")
    cut = _excerpt(WHOLE)
    assert cut is not None and len(cut) == DESCRIPTION_CHARS and cut.endswith("…")
    payload = {"cutDescription": cut, "shortDescription": "A short posting.", "whole": WHOLE}
    done = subprocess.run(
        [node, "--input-type=module", "-e", SCRIPT, (UI_SRC / "jobModel.js").as_uri(), (UI_SRC / "postingsModel.js").as_uri(), json.dumps(payload)],
        capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_preview_the_server_cut_says_it_is_the_start_of_the_posting(out: dict) -> None:
    assert out["textCut"] == [True, False]
    assert out["cutExcerpt"]["truncated"] is True
    assert out["plainExcerpt"]["truncated"] is False


def test_the_whole_text_keeps_its_sections_and_line_breaks(out: dict) -> None:
    whole = out["whole"]
    assert whole["truncated"] is False
    assert "REQUIRED SKILLS\n- Five years of Python\n- Postgres" in whole["text"]
    assert whole["text"].count("Filler line about the team.") == 60
