"""0.1.10.9 master P4: the pipeline's tailor step when a master resume is stored.

On the END outcome, with the synthetic gig and scripted model of
``tests/support/pipeline_fixtures`` (every model call is counted) and a small
invented master:

(a) a job the pipeline tailored from the profile's own resume is tailored
    again from the master once one is stored: its basis (the tailor step's
    digest) names the master, and the stored resume then says what was picked;
(b) a write of the master re-opens the tailoring again; nothing re-opens when
    nothing changed;
(c) inside the pipeline the code's own selection stands in for an answer that
    stayed INVALID, never for a model that could not be reached: that step
    waits and is retried, as before.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.scout.master_store import import_master
from gigai.scout.pipeline import triggers
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.pipeline.store import STEPS, PipelineStore
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import TAILOR_PROMPT_HEADER, TailorResponse, list_tailored_resumes

from tests.support.master_tailor import copies_what_it_is_shown, listed
from tests.support.pipeline_fixtures import JOB, PipelineFixture, build_pipeline_fixture

#: The fixture resume in GigAI's format (the scripted model below copies what the prompt lists).
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


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    monkeypatch.setenv(PIPELINE_ENV, "on")
    fixture = build_pipeline_fixture(tmp_path, monkeypatch, resume=RESUME)
    answer = fixture.model.answer

    def copies(prompt: str):  # noqa: ANN202 - the scripted model's own answer shape
        if prompt.lstrip().startswith(TAILOR_PROMPT_HEADER):
            fixture.model.tailored = copies_what_it_is_shown(prompt)
        return answer(prompt)

    monkeypatch.setattr(fixture.model, "answer", copies)
    return fixture


def _cli(fx: PipelineFixture, *args: str) -> dict:
    result = CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _store_master(fx: PipelineFixture, tmp_path: Path, markdown: str = MASTER, revision: int | None = None) -> str:
    """Store ``markdown`` as the master (what ``gigai scout resume master init --from FILE`` writes); ``created`` or ``revised``."""

    source = tmp_path / f"master-{revision or 0}.md"
    source.write_text(markdown, encoding="utf-8")
    return import_master(home_root=fx.home_root, target=fx.target, source=source, revision=revision, gig_id=fx.gig.resolved.gig_id).status


def _stored(fx: PipelineFixture) -> tuple[TailorResponse, dict]:
    (item,) = list_tailored_resumes(fx.home_root, fx.target, profile_id=fx.profile_id, job_identity=JOB)
    return item, json.loads(Path(item.stored_path).read_text(encoding="utf-8"))


def _tailor_step(fx: PipelineFixture):
    store = PipelineStore(fx.db)
    try:
        return next(step for step in store.steps(profile_id=fx.profile_id, job=JOB) if step.name == "tailor")
    finally:
        store.close()


def _outcomes(drained: dict) -> dict[str, tuple[object, object]]:
    return {step["name"]: (step["outcome"], step.get("code") or step.get("error_code")) for step in drained["drain"]["steps"]}


def test_a_stored_master_reopens_the_tailoring_and_the_pipeline_then_tailors_from_it(fx: PipelineFixture, tmp_path: Path) -> None:
    # Before a master: the pipeline tailors from the profile's own resume, and the stored resume says nothing of one.
    first = _cli(fx, "pipeline", "process", JOB)
    assert _outcomes(first) == {name: ("done", "tailored" if name == "tailor" else None) for name in STEPS}
    _resume, on_disk = _stored(fx)
    assert "selection" not in on_disk and "master" not in on_disk["sources"] and "Terraform" not in on_disk["markdown"]
    without = _tailor_step(fx).done_digest
    assert [text for _number, text in listed(fx.model.tailor_prompts[0])] == [line for line in RESUME.splitlines() if line.strip()]
    # Nothing changed: nothing re-opens, no model is called.
    calls = fx.model.calls
    assert triggers.profile_changed(fx.home_root, fx.target).enqueued == () and fx.model.calls == calls

    # (a) A master is stored: the tailoring's basis names it, so the job's tailoring re-opens and is made from the master.
    assert _store_master(fx, tmp_path) == "created"
    reopened = triggers.profile_changed(fx.home_root, fx.target)
    assert [(item["job_identity"], item["step"]) for item in reopened.enqueued] == [(JOB, "tailor")]
    second = _cli(fx, "pipeline", "process", JOB)
    assert _outcomes(second)["tailor"] == ("done", "tailored")
    resume, on_disk = _stored(fx)
    assert _tailor_step(fx).done_digest != without
    offered = [text for _number, text in listed(fx.model.tailor_prompts[-1])]
    assert "### Northwind Labs" in offered and "- Platform: Python, Kubernetes, PostgreSQL, Terraform" not in offered, "the candidate set, with the code's Skills line"
    assert on_disk["selection"]["picked_by"] == "model" and on_disk["sources"]["master"]["revision"] == 1
    assert "Northwind Labs" in resume.markdown and "Terraform" in resume.markdown
    assert all("item_id" in ref for section in on_disk["result"]["sections"] for entry in section.get("entries", []) for line in entry["bullets"] for ref in line["refs"])
    # The re-assessment and the ATS check ran against the resume made from the master.
    assert _outcomes(second)["reassess"][0] == _outcomes(second)["ats"][0] == _outcomes(second)["label"][0] == "done"
    assert "Northwind Labs" in fx.model.assess_prompts[-1]
    calls = fx.model.calls
    assert triggers.profile_changed(fx.home_root, fx.target).enqueued == () and fx.model.calls == calls

    # (b) A write of the master re-opens the tailoring again.
    with_master = _tailor_step(fx).done_digest
    edited = MASTER.replace("behind 40 product teams", "behind 55 product teams")
    assert _store_master(fx, tmp_path, edited, revision=1) == "revised"
    assert [item["step"] for item in triggers.profile_changed(fx.home_root, fx.target).enqueued] == ["tailor"]
    _cli(fx, "pipeline", "process", JOB)
    resume, on_disk = _stored(fx)
    assert _tailor_step(fx).done_digest != with_master and on_disk["sources"]["master"]["revision"] == 2 and "55 product teams" in resume.markdown


def test_in_the_pipeline_code_stands_in_for_an_invalid_answer_but_an_unreachable_model_is_retried(
    fx: PipelineFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_master(fx, tmp_path)

    # (c) The model cannot be reached: the tailor step does not finish with a code-only resume; it waits for its retry.
    fx.model.fail_next = OSError("connection refused")
    unreachable = _cli(fx, "pipeline", "process", JOB)
    outcome, code = _outcomes(unreachable)["tailor"]
    assert outcome != "done" and code in ("model_unavailable", "model_target_unavailable"), (outcome, code)
    assert list_tailored_resumes(fx.home_root, fx.target, profile_id=fx.profile_id, job_identity=JOB) == ()

    # An answer that stays invalid (the call and its one retry): the code's own selection is the job's resume, and
    # the rest of the pipeline runs against it.
    answer = fx.model.answer

    def garbage(prompt: str):  # noqa: ANN202
        result = answer(prompt)
        return result if not prompt.lstrip().startswith(TAILOR_PROMPT_HEADER) else type(result)(
            status="success", output_text="not JSON at all", resolved_model="fixture-model", raw_usage={},
            normalized_usage=result.normalized_usage, cost_status="unavailable",
        )

    monkeypatch.setattr(fx.model, "answer", garbage)
    invalid = _cli(fx, "pipeline", "process", JOB, "--force")
    assert _outcomes(invalid)["tailor"] == ("done", "tailored"), invalid["drain"]["steps"]
    resume, on_disk = _stored(fx)
    assert (on_disk["selection"]["picked_by"], on_disk["selection"]["fallback"]) == ("code", "model_output_invalid")
    assert "Northwind Labs" in resume.markdown and "Terraform" in resume.markdown
    assert {name: outcome for name, (outcome, _code) in _outcomes(invalid).items()} == dict.fromkeys(STEPS, "done")


def test_an_edited_markdown_attached_to_a_tailoring_made_from_the_master_is_stored_and_its_kept_lines_keep_their_master_ids(
    fx: PipelineFixture, tmp_path: Path,
) -> None:
    """The edit path (0110-10-05 B) is not changed by P4: it must still take a resume that was tailored from the master."""

    _store_master(fx, tmp_path)
    _cli(fx, "pipeline", "process", JOB)
    resume, before = _stored(fx)
    dropped = "- Operated Kubernetes clusters backed by PostgreSQL."
    assert dropped in resume.markdown
    edited = tmp_path / "edited.md"
    edited.write_text("\n".join(line.split(" <!--")[0] for line in resume.markdown.splitlines() if not line.startswith(dropped)) + "\n", encoding="utf-8")

    attached = _cli(fx, "resume", "tailor", "--in", str(edited), "--job-url", JOB)

    assert attached["changed"] is True
    _resume, after = _stored(fx)
    assert after["edited"]["written_by"] == "operator" and dropped not in after["markdown"] and "Northwind Labs" in after["markdown"]
    kept = [ref for section in after["result"]["sections"] for entry in section.get("entries", []) for line in entry["bullets"] for ref in line["refs"]]
    assert kept and all("item_id" in ref for ref in kept)
    # The record of what the tailoring picked belongs to the tailoring; an attached edit carries none.
    assert "selection" in before and "selection" not in after
