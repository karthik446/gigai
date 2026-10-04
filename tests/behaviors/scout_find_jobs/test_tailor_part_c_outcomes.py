"""0110-10-05 C: the END outcome of one tailoring, through ``run_tailored_resume`` and the stored files.

The operator's three symptoms, each on a synthetic resume (the eval's own
fixtures, ``tests/evals/fixtures/tailor_cases.json``) with a model that
"barely tailors" (it copies the resume, or answers the way the symptom needs):

1. an answer says the candidate has a skill the posting asks for and the
   resume lacks (Helm): the stored resume shows it in Skills, sourced to the
   answer, and the Scout ATS score no longer lists it as missing; a skill an
   answer DENIES (ArgoCD) is never added;
2. a resume that prints on 3 pages: the stored tailoring prints on 2, and what
   is gone is its oldest roles, whole;
3. a Skills bullet whose every skill another Skills bullet already lists is
   not printed twice.

And the control: a resume that fits is stored exactly as the settled model
answer, with nothing added, cut or collapsed.

The first four tests read only what existed before the fix (the stored JSON,
the markdown, the PDF's page count, the ATS score), so they run against the
previous code and FAIL there for the symptom's own reason.  The rest pin what
the fix adds: the record of what was left out, the way back (the store, the
CLI), and the flag when the pages cannot be measured.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import ats_score
from gigai.scout.experience_answers import record_answer
from gigai.scout.find_jobs.assess_contracts import AssessJobInput
from gigai.scout.posting_keywords import extract_keywords, skills_from_markdown
from gigai.scout.resume_pdf import stored_resume_pdf
from gigai.scout.tailored_resume import (
    TailorJob,
    TailorRequest,
    TailorResponse,
    apply_no_loss,
    list_tailored_resumes,
    resume_lines,
    run_tailored_resume,
    tailor_context,
    validate_tailored_output,
)

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume
from tests.support.tailor_cases import CASES, copy_everything, install_scripted_model, ollama_config

_CASES = CASES


def _tailor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case_id: str, reply: dict[str, object] | None = None) -> tuple[ProfileFixtureGig, TailorResponse, dict]:
    """One tailoring of a fixture case through the product; ``(the gig, the response, the stored JSON)``."""

    case = _CASES[case_id]
    fx = build_gig_with_resume(tmp_path, resume_text=case["resume"].encode("utf-8"))
    for answer in case["answers"]:
        record_answer(
            home_root=fx.home_root, requested_target=fx.target, gig_id=fx.resolved.gig_id,
            question_id=answer["question_id"], prompt=answer["prompt"], answer=answer["answer"],
        )
    install_scripted_model(monkeypatch, [reply if reply is not None else copy_everything(case["resume"])])
    response = run_tailored_resume(
        TailorRequest(job=AssessJobInput(job_text=case["posting"], title=case["title"], company=case["company"])),
        home_root=fx.home_root, target=fx.target, config=ollama_config(fx.home_root),
    )
    on_disk = json.loads(Path(response.stored_path).read_text(encoding="utf-8"))
    assert Path(response.markdown_path).read_text(encoding="utf-8") == response.markdown == on_disk["markdown"]
    return fx, response, on_disk


def _section(on_disk: dict, heading: str) -> dict:
    return next(section for section in on_disk["result"]["sections"] if section["heading"] == heading)


def _stored(fx: ProfileFixtureGig) -> TailorResponse:
    (stored,) = list_tailored_resumes(fx.home_root, fx.target)
    return stored


def _pages(fx: ProfileFixtureGig) -> int:
    rendered, _name = stored_resume_pdf(_stored(fx), home_root=fx.home_root, count_pages=True)
    assert rendered.pages is not None
    return rendered.pages


def _markdown_bullets(markdown: str, heading: str) -> list[str]:
    """The bullets under ``## <heading>`` as a reader sees them (no source comments)."""

    out: list[str] = []
    inside = False
    for line in markdown.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip().lower() == heading
        elif inside and line.startswith("- "):
            out.append(re.sub(r"\s*<!--.*?-->\s*\Z", "", line[2:]))
    return out


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+#.]+", text.lower()))


# --- 1. an answer that satisfies a posting skill --------------------------------------------------


def test_an_answer_that_satisfies_a_posting_skill_is_shown_in_skills_sourced_to_the_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = _CASES["helm_terse_answer"]
    assert "helm" not in case["resume"].lower() and "Helm" in case["posting"]
    fx, response, on_disk = _tailor(tmp_path, monkeypatch, "helm_terse_answer")

    helm = [line for line in _section(on_disk, "skills")["lines"] if "Helm" in line["text"]]
    assert [line["text"] for line in helm] == ["Helm"], "the Helm answer must add Helm to Skills"
    # Provenance: the answer is its one source.
    assert [(ref["kind"], ref.get("question_id")) for ref in helm[0]["refs"]] == [("answer", "tooling:helm")]
    assert "- Helm <!-- A tooling:helm -->" in response.markdown
    assert "Helm" in skills_from_markdown(response.markdown)
    # The Scout ATS score reads the PDF: Helm is no longer a missing key skill.
    stored = _stored(fx)
    rendered, file_name = stored_resume_pdf(stored, home_root=fx.home_root)
    keywords = extract_keywords(case["posting"], title=case["title"], skills=skills_from_markdown(stored.markdown))
    score = ats_score.score(rendered.pdf, stored.result, keywords, file_name=file_name)
    assert "Helm" in keywords.must and "Helm" not in score.line.split("missing:")[-1]
    # The answer that says "No" adds nothing: ArgoCD stays a gap.
    assert "argocd" not in response.markdown.lower() and "argo" not in response.markdown.lower()
    # Every resume line is still there (the copy-everything model lost nothing).
    assert all(line.lstrip("-# *").rstrip("*") in response.markdown for line in resume_lines(case["resume"]) if line.startswith("- "))


# --- 2. over two pages ----------------------------------------------------------------------------


def test_a_tailoring_over_two_pages_fits_two_pages_by_leaving_out_its_oldest_roles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx, response, on_disk = _tailor(tmp_path, monkeypatch, "over_long")

    assert _pages(fx) <= 2, "a tailored resume over 2 pages must be cut to 2"
    roles = [entry["heading"][0]["text"] for entry in _section(on_disk, "experience")["entries"]]
    # The oldest roles go, whole, oldest first; every newer role stays, in the resume's order.
    assert roles == [
        "**Staff Backend Engineer — Meridian Parcel** (2022–Present)",
        "**Senior Backend Engineer — Tallow Bank** (2019–2022)",
        "**Senior Software Engineer — Quillfeather Media** (2017–2019)",
        "**Software Engineer — Ostrava Health** (2015–2017)",
        "**Software Engineer — Pinecrest Travel** (2013–2015)",
        "**Software Engineer — Harrow Analytics** (2011–2013)",
    ]
    assert "Bellweather Retail" not in response.markdown and "Dunmore Telecom" not in response.markdown
    # Nothing else was cut for length: the three recent roles keep every bullet, and no other section lost a line.
    assert [len(entry["bullets"]) for entry in _section(on_disk, "experience")["entries"]][:3] == [10, 10, 10]
    assert len(_section(on_disk, "skills")["lines"]) == 3 and len(_section(on_disk, "education")["entries"]) == 1
    # The Scout ATS score's own page rule passes on the stored resume.
    stored = _stored(fx)
    rendered, file_name = stored_resume_pdf(stored, home_root=fx.home_root)
    score = ats_score.score(rendered.pdf, stored.result, extract_keywords(_CASES["over_long"]["posting"]), file_name=file_name)
    assert "1-2 pages" not in score.line


# --- 3. a Skills bullet inside another ------------------------------------------------------------


def _reply_with_the_skills_lines_in_posting_order() -> dict[str, object]:
    """What the model returned in the live eval: the three wrapped Skills lines copied in the posting's order (last first).

    The resume's Skills lines are plain ``Label: items`` lines, so each one runs on into the next: a copy of the
    last line is that line, a copy of the middle one carries the last, a copy of the first carries both.
    """

    case = _CASES["duplicate_skills"]
    reply = copy_everything(case["resume"])
    first = resume_lines(case["resume"]).index("Languages: Python, Go, TypeScript") + 1
    skills = next(section for section in reply["sections"] if section["heading"] == "skills")  # type: ignore[union-attr]
    skills["lines"] = [{"copy": first + 2}, {"copy": first + 1}, {"copy": first}]
    return reply


def test_a_skills_bullet_is_never_printed_inside_another(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fx, response, on_disk = _tailor(tmp_path, monkeypatch, "duplicate_skills", _reply_with_the_skills_lines_in_posting_order())

    bullets = _markdown_bullets(response.markdown, "skills")
    assert bullets, "the Skills section is printed"
    for index, bullet in enumerate(bullets):
        others = [other for position, other in enumerate(bullets) if position != index]
        assert not any(_words(bullet) <= _words(other) for other in others), f"Skills bullet repeated inside another: {bullet!r}"
    assert len(bullets) == 1 == len(_section(on_disk, "skills")["lines"])
    # No skill was lost with the duplicate: every skill of the resume's three lines is still printed.
    for skill in ("Python", "Go", "TypeScript", "AWS", "GCP", "Kubernetes", "Terraform", "PostgreSQL", "Kafka", "Redis", "Airflow"):
        assert skill in bullets[0]


# --- the control ----------------------------------------------------------------------------------


def test_a_resume_that_fits_is_stored_exactly_as_the_settled_model_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = _CASES["control"]
    _fx, response, on_disk = _tailor(tmp_path, monkeypatch, "control")

    job = TailorJob(title=case["title"], company=case["company"], location="", posting_text=case["posting"])
    ctx = tailor_context(case["resume"])
    settled = apply_no_loss(validate_tailored_output(copy_everything(case["resume"]), job, ctx), job, ctx)
    assert response.result == settled and on_disk["result"] == settled.to_json()
    assert set(on_disk["result"]) == {"schema_version", "header", "sections"}  # no length record: nothing was left out
    assert all(line.get("origin") in ("model", None) for section in on_disk["result"]["sections"] for line in section.get("lines", []))


# --- what the fix adds: the record, the way back, the flag ----------------------------------------


def test_what_was_left_out_for_length_is_recorded_whole_and_one_restore_puts_it_all_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.tailor_length_store import change_stored_length

    fx, response, on_disk = _tailor(tmp_path, monkeypatch, "over_long")
    length = on_disk["result"]["length"]
    assert (length["status"], length["max_pages"], length["pages"], length["full_pages"]) == ("cut", 2, 2, 3)
    # Per role: the two cut roles, with their place among the roles, and the bullets the old roles left out.
    assert [(role["position"], role["role"]) for role in length["cut"]] == [
        (6, "Junior Developer — Bellweather Retail (2009–2011)"),
        (7, "Junior Developer — Dunmore Telecom (2007–2009)"),
    ]
    assert [(role["role"], len(role["bullets"])) for role in length["trimmed"]] == [
        ("Software Engineer — Ostrava Health (2015–2017)", 3),
        ("Software Engineer — Pinecrest Travel (2013–2015)", 3),
        ("Software Engineer — Harrow Analytics (2011–2013)", 3),
        ("Junior Developer — Bellweather Retail (2009–2011)", 3),
        ("Junior Developer — Dunmore Telecom (2007–2009)", 3),
    ]
    job = response.job.job_identity

    restored = change_stored_length(fx.home_root, fx.target, profile_id=None, job_identity=job, use="restore")
    assert restored.result.length is not None and restored.result.length.status == "restored"
    assert restored.updated_at == response.updated_at
    stored = _stored(fx)
    assert stored == restored and Path(stored.markdown_path).read_text(encoding="utf-8") == stored.markdown
    experience = next(section for section in stored.result.sections if section.heading == "experience")
    # Every role is back in its place with every bullet, and every resume bullet is printed again.
    assert [len(entry.bullets) for entry in experience.entries] == [10, 10, 10, 6, 6, 6, 6, 6]
    assert [entry.heading[0].text for entry in experience.entries][-2:] == [
        "**Junior Developer — Bellweather Retail** (2009–2011)", "**Junior Developer — Dunmore Telecom** (2007–2009)",
    ]
    for line in resume_lines(_CASES["over_long"]["resume"]):
        if line.startswith("- "):
            assert line[2:] in stored.markdown
    assert all(not entry.dropped for entry in experience.entries)
    ids = [line.id for section in stored.result.sections for line in section.all_lines()]
    assert all(ids) and len(ids) == len(set(ids))
    assert _pages(fx) == 3

    # Restoring again changes nothing; cutting again leaves exactly the same things out.
    assert change_stored_length(fx.home_root, fx.target, profile_id=None, job_identity=job, use="restore") == restored
    again = change_stored_length(fx.home_root, fx.target, profile_id=None, job_identity=job, use="cut")
    assert again.result == response.result and again.markdown == response.markdown and _pages(fx) == 2

    # The revision check: a stale updated_at never writes.
    from gigai.scout.tailored_resume import TailorError

    with pytest.raises(TailorError) as stale:
        change_stored_length(fx.home_root, fx.target, profile_id=None, job_identity=job, use="restore", updated_at="2020-01-01T00:00:00Z")
    assert stale.value.code == "tailored_resume_changed" and _stored(fx) == again
    with pytest.raises(TailorError) as missing:
        change_stored_length(fx.home_root, fx.target, profile_id=None, job_identity="text:sha256:" + "0" * 64, use="restore")
    assert missing.value.code == "tailored_resume_not_found"


def test_the_cli_shows_what_was_cut_and_restores_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx, response, _on_disk = _tailor(tmp_path, monkeypatch, "over_long")
    base = ["scout", "resume", "length", "--job-url", response.job.job_identity, "--home", str(fx.home_root), "--target", str(fx.target)]

    shown = CliRunner().invoke(cli, base)
    assert shown.exit_code == 0, shown.output
    assert "Cut for length (3 pages -> 2): Junior Developer — Bellweather Retail (2009–2011); Junior Developer — Dunmore Telecom (2007–2009); 15 older bullets" in shown.output
    assert "--restore" in shown.output
    assert _stored(fx).result == response.result  # showing writes nothing

    restored = CliRunner().invoke(cli, [*base, "--restore", "--json"])
    assert restored.exit_code == 0, restored.output
    payload = json.loads(restored.output)
    assert payload["ok"] is True and payload["length"]["status"] == "restored" and payload["length"]["full_pages"] == 3
    assert "Dunmore Telecom" in _stored(fx).markdown

    again = CliRunner().invoke(cli, [*base, "--cut"])
    assert again.exit_code == 0 and "Cut for length (3 pages -> 2)" in again.output
    assert _stored(fx).result == response.result

    both = CliRunner().invoke(cli, [*base, "--restore", "--cut", "--json"])
    assert both.exit_code == 1 and json.loads(both.output)["error"]["code"] == "invalid_value"
    unknown = CliRunner().invoke(cli, ["scout", "resume", "length", "--job-url", "text:sha256:" + "0" * 64, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert unknown.exit_code == 1 and json.loads(unknown.output)["error"]["code"] == "tailored_resume_not_found"


def test_a_resume_whose_pages_cannot_be_measured_is_flagged_and_no_role_is_cut(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_renderer(_result):
        raise ImportError("typst is not installed")

    monkeypatch.setattr("gigai.scout.resume_pdf.fewest_pages", no_renderer)
    _fx, response, on_disk = _tailor(tmp_path, monkeypatch, "over_long")

    length = on_disk["result"]["length"]
    assert length["cut"] == [] and length["pages"] is None and length["full_pages"] is None
    assert len(_section(on_disk, "experience")["entries"]) == 8  # every role is there
    from gigai.scout.tailor_length import length_note

    assert "could not be measured, so no role was cut" in length_note(response.result.length)

    # A resume with nothing trimmed says only that it was not measured.
    other = tmp_path / "control"
    other.mkdir()
    _fx, response, on_disk = _tailor(other, monkeypatch, "control")
    assert on_disk["result"]["length"] == {"max_pages": 2, "pages": None, "full_pages": None, "status": "unmeasured", "cut": [], "trimmed": []}
    assert length_note(response.result.length).startswith("Length not checked")
