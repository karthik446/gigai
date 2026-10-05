"""0.1.11 N2 (SPEC 5.3): an agent's hand-back is checked against the MASTER, and stored.

On the END outcome, through the real CLI (``gigai scout resume tailor --in FILE --job-url URL --as agent``, the
spelling N2 keeps) on the synthetic gig of ``tests/support/pipeline_fixtures`` with a small invented master.

The defect (the agent-tailoring spike, 0.1.10.9 as shipped): the check read the profile's own 2-page resume, so a
sound hand-back made of master lines was refused ("an entry heading must be a line of your resume", "neither your
resume nor your answers state the skill ...") and nothing was stored: 16 of 16.

Here the profile's resume shows ONE role and the master holds two and a Skills line the resume never had:

- a hand-back of master lines, the newer role and the master's Skills line included, is stored, with every line
  traced to its master id and the master revision it was checked against (FAILS before N2:
  ``resume_markdown_invalid``, an entry heading);
- a reworded line that cites its master line is stored in the shape of a rewrite, and one per-line choice puts
  the master line back;
- a refusal says every problem at once by line number, never a line's text, and stores nothing;
- more than two pages is refused with the page count;
- a profile with no master, or detached from it, keeps the 0.1.10 check against its own resume.

The check is a guard, not proof: nothing here shows that a reworded line which passes is true.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.scout.master_store import import_master
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import TailorResponse, apply_line_choice, list_tailored_resumes, shown_text

from tests.support.pipeline_fixtures import JOB, PipelineFixture, build_pipeline_fixture

#: The profile's own resume, in GigAI's format: one role, three skills.
RESUME = """## Experience

### Acme Corp
Senior Engineer | 2019 - 2023

- Built Python services for six years; cut p99 latency by 40%.
- Operated Kubernetes clusters backed by PostgreSQL.

## Skills

- Python, Kubernetes, PostgreSQL
"""
#: The master holds what the resume holds and more: a summary, a newer role, and Terraform among its skills.
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
OWN = "- Own the Python inference services behind 40 product teams."
REWORDED = "- Own the Python inference services that 40 product teams depend on."


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    monkeypatch.delenv(PIPELINE_ENV, raising=False)
    return build_pipeline_fixture(tmp_path, monkeypatch, resume=RESUME)


def _store_master(fx: PipelineFixture, tmp_path: Path) -> None:
    source = tmp_path / "master.md"
    source.write_text(MASTER, encoding="utf-8")
    assert import_master(home_root=fx.home_root, target=fx.target, source=source, gig_id=fx.gig.resolved.gig_id).status == "created"


def _invoke(fx: PipelineFixture, markdown: str, tmp_path: Path):
    handed_back = tmp_path / "handed-back.md"
    handed_back.write_text(markdown, encoding="utf-8")
    args = ["resume", "tailor", "--in", str(handed_back), "--job-url", JOB, "--as", "agent", "--source", "agent tailoring"]
    return CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])


def _handed_back(fx: PipelineFixture, markdown: str, tmp_path: Path) -> dict:
    result = _invoke(fx, markdown, tmp_path)
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _refused(fx: PipelineFixture, markdown: str, tmp_path: Path) -> dict[str, str]:
    result = _invoke(fx, markdown, tmp_path)
    assert result.exit_code == 1, result.output
    return json.loads(result.output.strip().splitlines()[-1])["error"]


def _stored(fx: PipelineFixture) -> tuple[TailorResponse, dict]:
    (item,) = list_tailored_resumes(fx.home_root, fx.target, profile_id=fx.profile_id, job_identity=JOB)
    return item, json.loads(Path(item.stored_path).read_text(encoding="utf-8"))


def _nothing_stored(fx: PipelineFixture) -> bool:
    return list_tailored_resumes(fx.home_root, fx.target, profile_id=fx.profile_id, job_identity=JOB) == ()


def _master_ids(fx: PipelineFixture) -> dict[str, str]:
    """``line or heading text -> its master id`` for the stored master."""

    from gigai.scout.tailor_master import stored_master

    master = stored_master(fx.home_root, fx.target).master
    return {**{item.text: item.id for item in master.items.values()}, **{entry.heading: entry.id for entry in master.entries.values()}}


def _number(markdown: str, start: str) -> int:
    (number,) = [number for number, line in enumerate(markdown.splitlines(), 1) if line.startswith(start)]
    return number


def test_a_handback_of_master_lines_the_profiles_resume_never_showed_is_stored(fx: PipelineFixture, tmp_path: Path) -> None:
    _store_master(fx, tmp_path)
    ids = _master_ids(fx)

    # Every line is a master line, word for word: the newer role and the master's own Skills line among them.
    payload = _handed_back(fx, MASTER, tmp_path)

    assert payload["changed"] is True and payload["edited"]["written_by"] == "agent"
    resume, on_disk = _stored(fx)
    assert "Northwind Labs" in resume.markdown and "Terraform" in resume.markdown
    assert [section.heading for section in resume.result.sections] == ["summary", "experience", "skills"]
    # Each line is a copy of its master line, by id; the stored resume names the master revision it was checked against.
    lines = [line for section in resume.result.sections for line in section.all_lines()]
    assert all(line.kind == "copy" and line.origin == "user" and len(line.refs) == 1 for line in lines)
    assert [line.refs[0].item_id for line in resume.result.sections[1].entries[0].heading] == [ids["Northwind Labs"]] * 2
    assert {line.refs[0].item_id for section in resume.result.sections for line in section.body_lines()} == {
        item_id for text, item_id in ids.items() if text not in ("Northwind Labs", "Acme Corp")
    }
    assert on_disk["sources"]["master"]["revision"] == 1 and "selection" not in on_disk
    assert payload["recheck"]["error_code"] is None, payload["recheck"]

    # Handing the same file back again changes nothing.
    again = _handed_back(fx, MASTER, tmp_path)
    assert again["changed"] is False and again["recheck"]["result"] == "unchanged"


def test_a_reworded_line_is_stored_as_a_rewrite_and_one_choice_puts_the_master_line_back(fx: PipelineFixture, tmp_path: Path) -> None:
    _store_master(fx, tmp_path)
    own_id = _master_ids(fx)[OWN.removeprefix("- ")]

    _handed_back(fx, MASTER.replace(OWN, f"{REWORDED} <!-- src: {own_id} -->"), tmp_path)

    resume, _on_disk = _stored(fx)
    (line,) = [line for line in resume.result.sections[1].entries[0].bullets if line.kind == "rewritten"]
    assert (line.text, line.origin) == (REWORDED.removeprefix("- "), "user")
    assert [(ref.kind, ref.item_id, ref.text) for ref in line.refs] == [("resume", own_id, OWN)]
    assert line.alternative is not None and (line.alternative.kind, line.alternative.text) == ("copy", OWN)
    # Restore per line: the 0.1.10 per-line choice shows the original again, and keeps the rewrite as the other version.
    restored = apply_line_choice(resume, line.id, "original")
    (back,) = [item for item in restored.result.sections[1].entries[0].bullets if item.id == line.id]
    assert (back.kind, shown_text(back)) == ("copy", OWN.removeprefix("- ")) and OWN in restored.markdown and REWORDED not in restored.markdown
    assert back.alternative is not None and (back.alternative.kind, back.alternative.text) == ("rewritten", REWORDED.removeprefix("- "))


def test_a_refused_handback_says_every_problem_by_line_and_stores_nothing(fx: PipelineFixture, tmp_path: Path) -> None:
    _store_master(fx, tmp_path)
    own_id = _master_ids(fx)[OWN.removeprefix("- ")]
    inflated = "- Led the Python inference services behind 55 product teams."
    markdown = MASTER.replace(OWN, f"{inflated} <!-- src: {own_id} -->").replace("PostgreSQL, Terraform", "PostgreSQL, Terraform, Rust")

    error = _refused(fx, markdown, tmp_path)

    assert error["code"] == "edited_resume_unsupported", error
    message = error["message"]
    line, skills = _number(markdown, "- Led the Python"), _number(markdown, "- Platform:")
    assert message.startswith("not stored: 3 problems")
    assert f'line {line}: number_not_in_sources: the number "55"' in message
    assert f'line {line}: ownership_upgrade: "led"' in message
    assert f'line {skills}: skill_not_stated: the skill "Rust"' in message
    assert "inference services behind" not in message, "a refusal names the line number, never the line's text"
    assert _nothing_stored(fx)


def test_a_handback_over_two_pages_is_refused_with_the_page_count(fx: PipelineFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout import tailored_resume_edit

    _store_master(fx, tmp_path)
    # The real renderer counts this resume at one page (the tightest spacing the PDF may choose).
    assert tailored_resume_edit._pages(MASTER) == 1

    monkeypatch.setattr(tailored_resume_edit, "_pages", lambda markdown: 3)
    error = _refused(fx, MASTER, tmp_path)

    assert error["code"] == "edited_resume_unsupported" and "the whole resume: over_page_limit: 3 pages; at most 2" in error["message"]
    assert _nothing_stored(fx)


def test_a_profile_with_no_master_keeps_the_check_against_its_own_resume(fx: PipelineFixture, tmp_path: Path) -> None:
    error = _refused(fx, MASTER, tmp_path)

    assert error["code"] == "resume_markdown_invalid" and "an entry heading" in error["message"] and "of your resume" in error["message"]
    assert _nothing_stored(fx)


def test_a_profile_detached_from_the_master_keeps_the_check_against_its_own_resume(
    fx: PipelineFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_master(fx, tmp_path)
    # The profile's resume was put there by hand after its selection: its resumes are made from that resume, not the master.
    monkeypatch.setattr("gigai.scout.tailor_master.detached", lambda home_root, profile: True)

    error = _refused(fx, MASTER, tmp_path)

    assert error["code"] == "resume_markdown_invalid" and "an entry heading" in error["message"] and "of your resume" in error["message"]
    assert _nothing_stored(fx)
    # Its own resume, handed back, is stored as before, with no master named.
    payload = _handed_back(fx, RESUME, tmp_path)
    assert payload["changed"] is True and "master" not in _stored(fx)[1]["sources"]
