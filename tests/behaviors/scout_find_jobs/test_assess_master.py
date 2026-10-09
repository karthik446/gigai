"""0.1.10.9 master P7: what an assessment reads once a master resume is stored, and the ``resume_changed`` stale reason.

The END outcomes, on a synthetic home, through the product's own assessment
path (``run_quick_assessment`` with the scripted fixture model, ``gigai scout
new --yes``) and its own reads (``GET /api/jobs``'s aggregate, the Jobs grid's
row, ``BasisCheck.served``):

* WITHOUT a master an assessment is what it was, byte for byte, whatever the
  switch says: the same prompt, the same stored record.
* WITH a master and the switch on ``evidence``, the prompt's RESUME is the
  evidence view of the whole master for that posting (a line the profile's own
  resume does not have reaches the model), the stored record names the master
  revision and the selector version, and its resume identity is still the
  profile's.  A pasted resume, a resume replaced by hand and the pipeline's
  assessment of the tailored resume read what they read before.
* ``resume_changed`` is targeted: stale only when a line the assessment quoted
  is gone, or a line that was not there names one of its open questions.  It is
  shown (the API marker, the grid's label) and nothing is assessed again until
  the user says so.  ``master init`` makes nothing stale.  Without a master the
  reason never appears.

``ASSESS_INPUT`` is set by each test: the rules hold for both values, whichever
one ships.  No model is called: every answer is scripted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.canonical import canonical_json_bytes
from gigai.cli import cli
from gigai.scout import assess_master, master_profiles, postings
from gigai.scout import profile_records
from gigai.scout.assessment_basis import BASIS_STALE_REASONS, REASON_RESUME_CHANGED, BasisCheck
from gigai.scout.find_jobs.api.agent_routes import AgentRoutesMixin
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResponse, AssessResumeInput, ResumeBasis
from gigai.scout.find_jobs.contracts import FindJobsContractError, PinnedResume, normalize_url
from gigai.scout.master_selection import SELECTOR_VERSION
from gigai.scout.master_store import import_master, load_master
from gigai.scout.quick_assess import AssessVariant, read_quick_assessment, run_quick_assessment
from gigai.scout.resume_import import import_resume_file
from gigai.scout.resume_privacy import model_resume
from gigai.scout.scout_cli import scout_group

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.pipeline_fixtures import build_pipeline_fixture
from tests.support.posting_fixtures import NOW, PostingsFixture, days_ago, freeze_scout_new_clock, job_url, lever_job

_SLUG = "harborlight"
_URL = job_url(_SLUG, 1)
_POSTING = (
    "Harborlight is hiring a Staff AI Engineer to own Python inference services. Requirements: 5+ years of Python in "
    "production; Kubernetes; Terraform modules for every cluster; Helm charts for every service. Remote within the United States."
)
#: The profile's own resume, in GigAI's format: two lines of the master.
RESUME = """## Experience

### Acme Corp
Senior Engineer | 2019 - 2023

- Built Python services for six years; cut p99 latency by 40%.
- Operated Kubernetes clusters backed by PostgreSQL.

## Skills

- Python, Kubernetes, PostgreSQL
"""
#: The master holds what the resume holds and more: a newer role, and Terraform, which the posting asks for.
MASTER = """## Summary

- Engineer with nine years on Python inference services.

## Experience

### Northwind Labs
Staff Engineer | 2023 - Present

- Own the Python inference services behind 40 product teams.
- Wrote the Terraform modules every Kubernetes cluster is built from.

### Acme Corp
Senior Engineer | 2019 - 2023

- Built Python services for six years; cut p99 latency by 40%.
- Operated Kubernetes clusters backed by PostgreSQL.

## Skills

- Platform: Python, Kubernetes, PostgreSQL, Terraform
"""
TERRAFORM_LINE = "Wrote the Terraform modules every Kubernetes cluster is built from."
PYTHON_LINE = "Built Python services for six years; cut p99 latency by 40%."
KUBERNETES_LINE = "Operated Kubernetes clusters backed by PostgreSQL."
OWN_LINE = "Own the Python inference services behind 40 product teams."
HELM_QUESTION = {"question_id": "tooling:helm", "question": "Have you written Helm charts?", "requirement": "Helm charts"}


class _Routes(AgentRoutesMixin):
    """The one-job route's own aggregate, with no server around it."""


def _answer(*, terraform: str | None = None, python: str = "Built Python services for six years") -> str:
    """The scripted assessment: Python met by a quote, Terraform met by ``terraform`` (else unclear), Helm left open."""

    rows = [
        {"requirement": "5+ years of Python in production", "class": "hard", "status": "met", "resume_evidence": [python]},
        {"requirement": "Terraform modules", "class": "askable", "status": "met" if terraform else "unclear", "resume_evidence": [terraform] if terraform else []},
        {"requirement": "Helm charts", "class": "askable", "status": "unclear", "resume_evidence": []},
    ]
    questions = [HELM_QUESTION] if terraform else [HELM_QUESTION, {"question_id": "tooling:terraform", "question": "Have you written Terraform modules?", "requirement": "Terraform modules"}]
    return json.dumps({"verdict": "pending_user_answers", "matrix": rows, "suggestions": [], "questions": questions, "not_a_match_reason": None})


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    base = build_pipeline_fixture(tmp_path, monkeypatch, base=False, resume=RESUME)
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    fixture = PostingsFixture(base, second_profile_id="", deleted_profile_id=None)
    fixture.seed(_SLUG, [lever_job(_SLUG, 1, text=_POSTING)], seen_at=days_ago(1))
    postings.refresh(fixture.home_root, fixture.target, now=NOW)
    return fixture


def _switch(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(assess_master, "ASSESS_INPUT", value)


def _cli(fx: PostingsFixture, *args: str, ok: bool = True) -> dict:
    result = CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert (result.exit_code == 0) is ok, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _store_master(fx: PostingsFixture, tmp_path: Path, markdown: str = MASTER) -> None:
    """Store ``markdown`` as the master (what ``gigai scout resume master init --from FILE`` writes)."""

    source = tmp_path / "master.md"
    source.write_text(markdown, encoding="utf-8")
    assert import_master(home_root=fx.home_root, target=fx.target, source=source, gig_id=fx.base.gig.resolved.gig_id).status == "created"


def _master(fx: PostingsFixture):
    stored = load_master(home_root=fx.home_root, target=fx.target, gig_id=fx.base.gig.resolved.gig_id)
    assert stored is not None
    return stored


def _line_id(fx: PostingsFixture, text: str) -> str:
    return next(item.id for item in _master(fx).master.items.values() if item.text == text)


def _entry_id(fx: PostingsFixture, heading: str) -> str:
    return next(entry.id for entry in _master(fx).master.entries.values() if entry.heading == heading)


def _edit(fx: PostingsFixture, item_id: str, text: str) -> None:
    """``gigai scout resume master edit ID --text ...`` on top of the revision the master is at."""

    _cli(fx, "resume", "master", "edit", item_id, "--text", text, "--revision", str(_master(fx).revision.revision))


def _replace_resume(fx: PostingsFixture, tmp_path: Path, markdown: str) -> None:
    """The profile's resume replaced by hand: what ``gigai scout resume add FILE --profile ID`` writes."""

    source = tmp_path / "replaced.md"
    source.write_text(markdown, encoding="utf-8")
    added = import_resume_file(home_root=fx.home_root, requested_target=fx.target, source=source, gig_id=fx.base.gig.resolved.gig_id)
    profile_records.write_profile(
        fx.base.gig.resolved, profile_id=fx.default_profile_id, resume_ref=PinnedResume(added.record_id, added.revision_id, added.content_sha256),
    )


def _profile(fx: PostingsFixture):
    return next(item for item in profile_records.list_profiles(fx.base.gig.resolved) if item.profile_id == fx.default_profile_id)


def _assess(fx: PostingsFixture, answer: str, **kwargs) -> tuple[AssessResponse, str]:
    """The job assessed by its URL for the default profile, the model answering ``answer``: ``(the stored assessment, the prompt)``."""

    fx.base.model.assessed = answer
    fx.base.model.assess_prompts.clear()
    stored = run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=_URL), resume=AssessResumeInput(profile_id=fx.default_profile_id)),
        home_root=fx.home_root, target=fx.target, config=fixture_config(fx.home_root), **kwargs,
    )
    (prompt,) = fx.base.model.assess_prompts
    return stored, prompt


def _stored(fx: PostingsFixture) -> AssessResponse:
    found = read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, normalize_url(_URL))
    assert found is not None
    return found


def _served(fx: PostingsFixture) -> dict[str, object]:
    """The additive keys the API serves with the stored assessment (a fresh request each time)."""

    return BasisCheck(home_root=fx.home_root, target=fx.target).served(_stored(fx))


def _resume_block(prompt: str) -> str:
    """The prompt's RESUME block, up to the candidate constraints."""

    return prompt.split("RESUME:\n", 1)[1].split("CANDIDATE CONSTRAINTS:", 1)[0]


def _strip_ids(block: str) -> str:
    """The RESUME block without its bookkeeping: the trailing id comments and the private-note lines."""

    lines = [line for line in block.splitlines() if not line.lstrip().startswith("<!-- private note:")]
    return re.sub(r"[ \t]*<!--\s*id:\S+\s*-->", "", "\n".join(lines)).strip()


def _timeless(stored: AssessResponse) -> dict[str, object]:
    value = stored.to_json()
    for key in ("created_at", "updated_at", "history"):
        value.pop(key, None)
    return value


# --- without a master: exactly what it was --------------------------------------------------------------


def test_without_a_master_an_assessment_is_byte_for_byte_what_it_was_whatever_the_switch(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    _switch(monkeypatch, assess_master.INPUT_VIEW)
    before, prompt_before = _assess(fx, _answer())
    _switch(monkeypatch, assess_master.INPUT_EVIDENCE)
    after, prompt_after = _assess(fx, _answer())

    # The same prompt, byte for byte, and it holds the profile's own resume.
    assert prompt_after == prompt_before
    assert model_resume(RESUME).text in prompt_before
    # The same stored record (the assessment's own time aside), with nothing of a master in it.
    assert canonical_json_bytes(_timeless(after)) == canonical_json_bytes(_timeless(before))
    stored = json.loads(Path(after.stored_path).read_text(encoding="utf-8"))
    assert "resume_basis" not in stored
    assert sorted(stored) == [
        "constraints_digest", "created_at", "history", "instructions_digest", "job", "model", "origin", "posting_sha256", "posting_text",
        "preferences", "producer", "profile_ref", "prompt_version", "result", "resume", "schema_version", "stored_path", "story_bank",
        "updated_at", "usage",
    ]
    # And the plan itself: nothing to read in place of the profile's resume.
    assert assess_master.assess_input(home_root=fx.home_root, target=fx.target, profile=_profile(fx), title="Staff AI Engineer", posting_text=_POSTING) is None


def test_without_a_master_a_replaced_resume_makes_no_assessment_stale(fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _switch(monkeypatch, assess_master.INPUT_EVIDENCE)
    _assess(fx, _answer())
    assert _served(fx) == {"basis_stale": False}
    _replace_resume(fx, tmp_path, RESUME.replace(PYTHON_LINE, "Built Go services for two years.") + "\n- Wrote Helm charts.\n")

    # The quoted line is gone and a new line names the open question: without a master that is no reason, as before.
    assert _profile(fx).resume_ref.revision_id != _stored(fx).resume.pinned.revision_id  # type: ignore[union-attr]
    assert _served(fx) == {"basis_stale": False}


# --- with a master: the evidence view is what the model reads -------------------------------------------


def test_with_a_master_the_assessment_reads_the_evidence_view_of_the_whole_master(fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _switch(monkeypatch, assess_master.INPUT_EVIDENCE)
    _store_master(fx, tmp_path)
    profile = _profile(fx)

    stored, prompt = _assess(fx, _answer(terraform=TERRAFORM_LINE))

    # The model was shown a line the profile's own resume does not have.
    assert TERRAFORM_LINE not in RESUME and TERRAFORM_LINE in prompt
    # The RESUME block is the evidence view of the master for this posting, whole: nothing else, nothing cut.
    master = _master(fx)
    view = assess_master.evidence_text(
        master.master, assess_master.profile_prior(titles=tuple(profile.titles), item_ids=None, profile_id=profile.profile_id, label=profile.label),
        title=stored.job.title, posting_text=_POSTING, company=stored.job.company, location=stored.job.location,
    )
    # With the template asking for ids the block carries each line's id comment (and any private-note line): bookkeeping.
    # So the block WITH ids is the ids=True view, and with the bookkeeping stripped it is the plain view.
    view_ids = assess_master.evidence_text(
        master.master, assess_master.profile_prior(titles=tuple(profile.titles), item_ids=None, profile_id=profile.profile_id, label=profile.label),
        title=stored.job.title, posting_text=_POSTING, company=stored.job.company, location=stored.job.location, ids=True,
    )
    block = _resume_block(prompt).strip()
    assert block == model_resume(view_ids.markdown).text.strip()
    assert _strip_ids(block) == model_resume(view.markdown).text.strip()
    # (The stored master reads, line for line, as the markdown it was imported from.)
    assert assess_master.master_lines(master.master).keys == assess_master.resume_lines(MASTER).keys
    assert view.within_cap and view.bullets == view.bullets_total == 4
    # The record says what it read; its resume identity is still the profile's pinned resume.
    assert stored.resume_basis == ResumeBasis("evidence", master.revision.revision_id, 1, SELECTOR_VERSION)
    assert stored.resume.pinned == profile.resume_ref and stored.resume.profile_id == profile.profile_id
    on_disk = json.loads(Path(stored.stored_path).read_text(encoding="utf-8"))
    assert on_disk["resume_basis"] == {"input": "evidence", "master_revision_id": master.revision.revision_id, "master_revision": 1, "selector_version": SELECTOR_VERSION}
    assert AssessResponse.from_json(on_disk).resume_basis == stored.resume_basis
    # The one job read says it too, and the assessment is current.
    job = _Routes()._job_aggregate(normalize_url(_URL), home_root=fx.home_root, target=fx.target)
    assert job is not None and job["assessments"][0]["basis_stale"] is False


def test_with_a_master_and_the_switch_on_the_profiles_view_the_assessment_reads_the_profiles_resume(
    fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, assess_master.INPUT_VIEW)
    _store_master(fx, tmp_path)

    stored, prompt = _assess(fx, _answer())

    assert TERRAFORM_LINE not in prompt and _resume_block(prompt).strip() == model_resume(RESUME).text.strip()
    assert stored.resume_basis is None and "resume_basis" not in json.loads(Path(stored.stored_path).read_text(encoding="utf-8"))


def test_assess_all_new_reads_the_evidence_view_too(fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    freeze_scout_new_clock(monkeypatch)  # the command reads the clock: the postings are seeded relative to NOW
    _switch(monkeypatch, assess_master.INPUT_EVIDENCE)
    _store_master(fx, tmp_path)
    fx.base.model.assessed = _answer(terraform=TERRAFORM_LINE)
    fx.base.model.assess_prompts.clear()

    result = CliRunner().invoke(cli, [*fx.cli("--yes"), "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["assessed"]["assessed"] == 1
    assert [TERRAFORM_LINE in prompt for prompt in fx.base.model.assess_prompts] == [True]
    assert _stored(fx).resume_basis == ResumeBasis("evidence", _master(fx).revision.revision_id, 1, SELECTOR_VERSION)


def test_a_pasted_resume_a_detached_profile_and_the_tailored_variant_read_what_they_read_before(
    fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, assess_master.INPUT_EVIDENCE)
    _store_master(fx, tmp_path)

    # A pasted resume: its own text, no basis.
    fx.base.model.assessed = _answer()
    fx.base.model.assess_prompts.clear()
    pasted = run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=_URL), resume=AssessResumeInput(resume_text="Ten years of COBOL on mainframes.")),
        home_root=fx.home_root, target=fx.target, config=fixture_config(fx.home_root),
    )
    assert pasted.resume_basis is None and "COBOL" in fx.base.model.assess_prompts[0] and TERRAFORM_LINE not in fx.base.model.assess_prompts[0]

    # The pipeline's assessment of the TAILORED resume reads the 2 pages that will be sent.
    variant, prompt = _assess(fx, _answer(), variant=AssessVariant(resume_text="## Experience\n\n- Tailored: Python services, six years.\n"))
    assert variant.resume_basis is None and "Tailored: Python services" in prompt and TERRAFORM_LINE not in prompt

    # A profile on a selection reads the evidence view ...
    _cli(fx, "resume", "master", "selection", "refresh", "--profile", fx.default_profile_id)
    attached, prompt = _assess(fx, _answer(terraform=TERRAFORM_LINE))
    assert attached.resume_basis is not None and TERRAFORM_LINE in prompt
    # ... until its resume is replaced by hand: then that resume is read, as before.
    _replace_resume(fx, tmp_path, RESUME.replace(PYTHON_LINE, "Built Go services for two years."))
    detached, prompt = _assess(fx, _answer(python="Built Go services for two years"))
    assert detached.resume_basis is None and "Built Go services for two years." in prompt and TERRAFORM_LINE not in prompt


# --- resume_changed: targeted, shown, never assessed again on its own ------------------------------------


def test_an_evidence_assessment_is_stale_only_when_a_line_it_quoted_changed(fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _switch(monkeypatch, assess_master.INPUT_EVIDENCE)
    _store_master(fx, tmp_path)
    _assess(fx, _answer(terraform=TERRAFORM_LINE))
    assert _served(fx) == {"basis_stale": False}

    # A line it did not quote is edited: the master is at another revision, and the assessment still stands.
    _edit(fx, _line_id(fx, KUBERNETES_LINE), "Operated 30 Kubernetes clusters backed by PostgreSQL.")
    assert _master(fx).revision.revision == 2
    assert _served(fx) == {"basis_stale": False}

    # The line it quoted for Terraform is edited so the quote no longer stands.
    _edit(fx, _line_id(fx, TERRAFORM_LINE), "Reviewed the Pulumi programs two clusters are built from.")
    assert _served(fx) == {
        "basis_stale": True, "basis_stale_reason": "resume_changed",
        "basis_stale_resume": [{"change": "line_changed", "requirement": "Terraform modules"}],
    }


def test_an_evidence_assessment_is_stale_when_a_retired_line_was_its_evidence(fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _switch(monkeypatch, assess_master.INPUT_EVIDENCE)
    _store_master(fx, tmp_path)
    _assess(fx, _answer(terraform=TERRAFORM_LINE))

    _cli(fx, "resume", "master", "remove", _line_id(fx, TERRAFORM_LINE), "--revision", str(_master(fx).revision.revision))

    assert _served(fx)["basis_stale_resume"] == [{"change": "line_changed", "requirement": "Terraform modules"}]


def test_a_new_master_line_makes_stale_only_the_assessment_whose_open_question_it_names_and_nothing_is_assessed_again(
    fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, assess_master.INPUT_EVIDENCE)
    _store_master(fx, tmp_path)
    _assess(fx, _answer(terraform=TERRAFORM_LINE))  # leaves tooling:helm open
    northwind = _entry_id(fx, "Northwind Labs")

    # A new line about something it did not ask: still current.
    _cli(fx, "resume", "master", "add", "--entry", northwind, "--text", "Ran the weekly incident review for the platform group.")
    assert _served(fx) == {"basis_stale": False}

    # A new line that names Helm, which it asked about.
    _cli(fx, "resume", "master", "add", "--entry", northwind, "--text", "Packaged every inference service as a Helm chart.")
    assert _served(fx) == {
        "basis_stale": True, "basis_stale_reason": "resume_changed",
        "basis_stale_resume": [{"change": "new_line", "question_id": "tooling:helm", "question": "Have you written Helm charts?"}],
    }

    # Shown: the one job read, and the grid's row and label.
    postings.refresh(fx.home_root, fx.target, now=NOW)
    job = _Routes()._job_aggregate(normalize_url(_URL), home_root=fx.home_root, target=fx.target)
    assert job is not None
    assert job["job_state"]["assessment_stale"] == {"reason": "resume_changed"}
    assert job["assessments"][0]["basis_stale_reason"] == "resume_changed"
    calls = fx.base.model.calls
    listed = CliRunner().invoke(cli, [*fx.cli(), "--json"])
    assert listed.exit_code == 0, listed.output
    response = json.loads(listed.stdout)
    row = next(row for row in response["postings"]["rows"] if row["job_identity"] == normalize_url(_URL))
    assert (row["stale_reason"], row["stale_label"]) == ("resume_changed", "old assessment: resume changed")
    # Nothing is assessed again by a read, or by a plain yes to "assess the new ones": the old ones are their own question.
    assert response["counts"]["only_stale"] == 1 and response["stale_question"] is not None
    again = CliRunner().invoke(cli, [*fx.cli("--yes"), "--json"])
    assert again.exit_code == 0, again.output
    assert json.loads(again.stdout)["reassessed"] is None and fx.base.model.calls == calls

    # Re-assessed on the user's word: the assessment is made on the master as it is now, and is current again.
    stored, prompt = _assess(fx, _answer(terraform=TERRAFORM_LINE))
    assert "Packaged every inference service as a Helm chart." in prompt
    assert stored.resume_basis is not None and stored.resume_basis.master_revision == 3
    assert _served(fx) == {"basis_stale": False}


def test_on_the_profiles_view_an_assessment_is_stale_when_its_quoted_line_leaves_the_view_or_a_new_line_enters_it(
    fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, assess_master.INPUT_VIEW)
    _store_master(fx, tmp_path)
    # The profile's resume becomes its 2-page view of the master (the whole of this small master).
    _cli(fx, "resume", "master", "selection", "refresh", "--profile", fx.default_profile_id)
    stored, prompt = _assess(fx, _answer(terraform=TERRAFORM_LINE))
    assert stored.resume_basis is None and TERRAFORM_LINE in prompt
    assert _served(fx) == {"basis_stale": False}

    # A new master line is only offered to a sticky selection: the profile's resume is what it was, nothing is stale.
    _cli(fx, "resume", "master", "add", "--entry", _entry_id(fx, "Northwind Labs"), "--text", "Packaged every inference service as a Helm chart.")
    assert _served(fx) == {"basis_stale": False}
    # A shown line it did not quote is edited: the view is printed again, and the assessment still stands.
    _edit(fx, _line_id(fx, KUBERNETES_LINE), "Operated 30 Kubernetes clusters backed by PostgreSQL.")
    assert _profile(fx).resume_ref.revision_id != stored.resume.pinned.revision_id  # type: ignore[union-attr]
    assert _served(fx) == {"basis_stale": False}

    # The refresh brings the Helm line into the view: the line names its open question.
    _cli(fx, "resume", "master", "selection", "refresh", "--profile", fx.default_profile_id)
    assert _served(fx) == {
        "basis_stale": True, "basis_stale_reason": "resume_changed",
        "basis_stale_resume": [{"change": "new_line", "question_id": "tooling:helm", "question": "Have you written Helm charts?"}],
    }
    # And the line it quoted is edited away: both are said.
    _edit(fx, _line_id(fx, TERRAFORM_LINE), "Reviewed the Pulumi programs two clusters are built from.")
    assert [change["change"] for change in _served(fx)["basis_stale_resume"]] == ["line_changed", "new_line"]  # type: ignore[union-attr]


@pytest.mark.parametrize("switch", [assess_master.INPUT_VIEW, assess_master.INPUT_EVIDENCE])
def test_the_migration_makes_no_stored_assessment_stale(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch, switch: str) -> None:
    _switch(monkeypatch, switch)
    # Assessed before any master: one question open, one line quoted.
    _assess(fx, _answer())
    before = canonical_json_bytes(_stored(fx).to_json())
    postings.refresh(fx.home_root, fx.target, now=NOW)
    rows_before = _rows(fx)
    assert [(row[1], row[2]) for row in rows_before] == [(None, "needs_answers")]

    # What ``gigai scout resume master init`` runs: the master is built from the profiles' resumes; each profile keeps its resume.
    done = master_profiles.migrate(home_root=fx.home_root, target=fx.target)
    assert done.status == "created" and _profile(fx).master_selection is not None

    assert _served(fx) == {"basis_stale": False}
    assert canonical_json_bytes(_stored(fx).to_json()) == before
    postings.refresh(fx.home_root, fx.target, now=NOW)
    assert _rows(fx) == rows_before


def _rows(fx: PostingsFixture) -> list[tuple[object, ...]]:
    store = postings.open_store(fx.home_root, fx.target)
    try:
        return [(row.job, row.stale_code, row.state, row.assessed_at, row.pinned_digest) for row in store.postings(live=False) if row.profile_id == fx.default_profile_id]
    finally:
        store.close()


def test_the_switch_ships_on_the_evidence_view() -> None:
    # Operator decision 4, after the P7 live eval found no regression (the worker's report has the numbers).
    assert assess_master.ASSESS_INPUT == assess_master.INPUT_EVIDENCE and assess_master.ASSESS_INPUTS == ("view", "evidence")


def test_the_stale_reasons_name_resume_changed_last() -> None:
    assert BASIS_STALE_REASONS == ("older_prompt", "settings_changed", "story_bank_changed", "resume_changed")
    assert REASON_RESUME_CHANGED == "resume_changed"


# --- the rule itself, pure --------------------------------------------------------------------------------


class _Row:
    def __init__(self, requirement: str, *evidence: str) -> None:
        self.requirement, self.resume_evidence = requirement, evidence


class _Question:
    def __init__(self, question_id: str, question: str = "") -> None:
        self.question_id, self.question = question_id, question


_THEN = assess_master.resume_lines(MASTER)


def _changes(now_markdown: str, rows: list[_Row], questions: list[_Question] | None = None) -> list[dict[str, object]]:
    found = assess_master.resume_changes(matrix=rows, questions=questions or [], then=_THEN, now=assess_master.resume_lines(now_markdown))
    return [change.to_json() for change in found]


def test_resume_lines_reads_a_skills_line_as_one_entry_per_skill_and_a_resume_in_another_shape() -> None:
    assert "skill: terraform" in _THEN.keys and "skill: python" in _THEN.keys
    other = assess_master.resume_lines("# A Person\n\n**Experience**\nAcme Corp, Senior Engineer (2019-2023)\n* Built Python services.\n\nSKILLS\nPython, Go; Rust\n")
    assert other.lines == ("acme corp, senior engineer (2019-2023)", "built python services", "skill: python", "skill: go", "skill: rust")


def test_evidence_is_lost_only_when_the_line_it_drew_on_is_gone_and_nothing_backs_it_as_well() -> None:
    rows = [_Row("Latency", "cut p99 latency by 40%"), _Row("Python", "six years of Python services (paraphrased)"), _Row("Terraform", "Terraform")]
    # The same lines, one retyped with other spacing and case: nothing changed for it.
    assert _changes(MASTER.replace(PYTHON_LINE, "built  PYTHON services for six years; cut p99 latency by 40%."), rows) == []
    # The number it quoted is another number now. The paraphrase beside it took no number from that line: it still stands.
    assert _changes(MASTER.replace("by 40%", "by 35%"), rows) == [{"change": "line_changed", "requirement": "Latency"}]
    # A skill it named is no longer listed, and no line names it.
    without = MASTER.replace(", Terraform", "").replace(TERRAFORM_LINE, "Wrote the Pulumi programs every cluster is built from.")
    assert _changes(without, rows) == [{"change": "line_changed", "requirement": "Terraform"}]
    # ... while a line still names it, the skill leaving the Skills list changes nothing for that evidence.
    assert _changes(MASTER.replace(", Terraform", ""), rows) == []
    # A quote that was never on a line (the model's own words) is not held against a change.
    assert _changes(MASTER.replace("by 40%", "by 35%"), [_Row("Leadership", "led a team of 12 engineers")]) == []
    # A cited answer or story is the story bank's to judge, not the resume's.
    assert _changes(MASTER.replace("by 40%", "by 35%"), [_Row("GCP", "Story bank cloud:gcp: cut p99 latency by 40%")]) == []


def test_a_fixed_typo_or_one_changed_word_is_not_a_change_and_a_reworded_line_is() -> None:
    quoted = [_Row("Terraform modules", TERRAFORM_LINE)]
    assert _changes(MASTER.replace(TERRAFORM_LINE, "Wrote the Terraform modules that every Kubernetes cluster is built from."), quoted) == []
    assert _changes(MASTER.replace(TERRAFORM_LINE, "Wrote the Terraform modules every Kubernetes cluster is provisioned from."), quoted) == []
    reworded = MASTER.replace(TERRAFORM_LINE, "Reviewed the Pulumi programs two clusters are built from.")
    assert _changes(reworded, quoted) == [{"change": "line_changed", "requirement": "Terraform modules"}]
    # A retired line: the same.
    assert _changes(MASTER.replace(f"- {TERRAFORM_LINE}\n", ""), quoted) == [{"change": "line_changed", "requirement": "Terraform modules"}]


def test_evidence_that_joins_two_lines_in_one_sentence_is_traced_to_each() -> None:
    # What a model often writes: one sentence made of two resume lines.
    joined = [_Row("Platform", "Owns Python inference services for 40 product teams and wrote the Terraform modules for every Kubernetes cluster.")]
    assert _changes(MASTER.replace("behind 40 product teams", "behind 12 product teams"), joined) == [{"change": "line_changed", "requirement": "Platform"}]
    assert _changes(MASTER.replace(f"- {TERRAFORM_LINE}\n", ""), joined) == [{"change": "line_changed", "requirement": "Platform"}]
    # A line it did not draw on changes: it still stands.
    assert _changes(MASTER.replace("by 40%", "by 35%"), joined) == []
    assert _changes(MASTER.replace(KUBERNETES_LINE, "Operated 30 Kubernetes clusters backed by PostgreSQL."), joined) == []


def test_a_new_line_answers_a_question_by_its_subject_and_never_a_question_about_the_candidates_settings() -> None:
    added = MASTER.replace(f"- {TERRAFORM_LINE}", f"- {TERRAFORM_LINE}\n- Packaged every service as a Helm chart for the Denver office.")
    questions = [_Question("tooling:helm", "Helm?"), _Question("tooling:istio", "Istio?"), _Question("location:denver", "Can you work from Denver?")]
    assert _changes(added, [], questions) == [{"change": "new_line", "question_id": "tooling:helm", "question": "Helm?"}]
    # A new skill answers too; a line that was already there does not, whatever it names.
    assert _changes(MASTER.replace(", Terraform", ", Terraform, Istio"), [], questions) == [{"change": "new_line", "question_id": "tooling:istio", "question": "Istio?"}]
    assert _changes(MASTER, [], [_Question("tooling:terraform", "Terraform?")]) == []


def test_the_resume_basis_is_additive_and_checked() -> None:
    basis = ResumeBasis("evidence", "revision_0f0e0d0c-0b0a-4a09-8807-060504030201", 3, "sel-1")
    assert ResumeBasis.from_json(basis.to_json()) == basis
    with pytest.raises(FindJobsContractError):
        ResumeBasis("view", "revision_x", 1, "sel-1")
    with pytest.raises(FindJobsContractError):
        ResumeBasis.from_json({**basis.to_json(), "master_revision": 0})
    with pytest.raises(FindJobsContractError):
        ResumeBasis.from_json({**basis.to_json(), "lines": []})
