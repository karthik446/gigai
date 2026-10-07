"""0.1.11.4 item 9d (packet H2): the job page's read says whether the master holds any education.

The job page shows "Your master has no education" on a resume that prints none. It reads that from the answer it
already loads for the job (``GET /api/jobs/suggestions``, which carries the stored view of ``gigai scout resume
pick --job-url URL``): one flat flag, ``master_education``, read from the master that view loads anyway.

The synthetic home of ``test_assessment_v9_flow`` (one profile, a small invented master with no Education section,
one posting, a scripted model). The background pipeline is off.
"""

from __future__ import annotations

import json

from click.testing import CliRunner
import pytest

from gigai.scout import job_actions, tailor_master
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.scout_cli import scout_group

from tests.behaviors.scout_find_jobs.test_assessment_v9_flow import _URL, _assess, _v9_answer, fx  # noqa: F401 - `fx` is the fixture
from tests.support.posting_fixtures import PostingsFixture


@pytest.fixture(autouse=True)
def _pipeline_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PIPELINE_ENV, "off")


def _cli(fx: PostingsFixture, *args: str) -> dict:
    result = CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def test_the_jobs_view_says_whether_the_master_holds_any_education(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    _assess(fx, _v9_answer(fx))
    view = _cli(fx, "resume", "pick", "--job-url", _URL)
    assert view["master_stored"] is True and view.get("master_education") is False
    # What the page loads when a job is opened carries the same flag (no second read of the master).
    opened = job_actions.list_suggestions(fx.home_root, fx.target, _URL, with_view=True)
    assert opened.get("master_education") is False
    # A school is added with the command the notice names: the flag turns, and the page stops saying it.
    _cli(fx, "resume", "master", "add", "--heading", "Lakeside University", "--role", "B.S. Computer Science | 2010", "--section", "education")
    assert _cli(fx, "resume", "pick", "--job-url", _URL)["master_education"] is True
    assert job_actions.list_suggestions(fx.home_root, fx.target, _URL, with_view=True)["master_education"] is True
    # No master at all: there is nothing to say about its education.
    monkeypatch.setattr(tailor_master, "stored_master", lambda *_args, **_kwargs: None)
    gone = _cli(fx, "resume", "pick", "--job-url", _URL)
    assert gone["master_stored"] is False and gone["master_education"] is None
