"""0.1.11 N5 (SPEC 4.4, 5.1-5.3): the commands of the chat step, on the END outcome, through the real CLI.

The synthetic gig of ``tests/support/pipeline_fixtures`` (one profile, one answer, one story, one assessed posting;
every model call counted) with the small invented master of N2's hand-back tests.

- ``gigai scout resume store`` stores a hand-back (the new name of ``resume tailor --in``); ``gigai scout resume
  brief`` then prints that resume with each line's master id, and THAT markdown handed back unchanged changes
  nothing: an id is a trailing comment and no heading is decorated. A line reworded with the ids the brief gave is
  stored and comes back with its sources.
- The brief is two calls that never mix, on a real home: the private one holds no word of the posting, the posting
  one nothing the user wrote. It calls no model and writes nothing.
- ``resume tailor --in`` still works, does what ``resume store`` does and names the new spelling in one line.
- ``--fit`` and ``--resolves`` are checked BEFORE anything is stored: a refusal stores nothing.
- ``gigai scout resume pick`` shows what is stored and recomputes nothing; its steps go to ``scout.pick`` through one
  door (``job_resume_port``): a GigAI without that step answers the typed ``pick_not_available`` in plain words, and
  with one (a fake here; the real one, ``pick.settle_stored``, is driven in
  ``tests/behaviors/scout_find_jobs/test_assess_then_picked.py``) it is called once with the job, and never while
  the assessment is stale.
- ``gigai scout resume pdf --job-url URL`` is the stored job resume; ``--tailored`` stays an accepted spelling.
- ``resume_tailor_removed_command`` (not wired in this packet) is the typed exit ``tailoring_removed``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.scout import job_actions, job_resume_port, pick
from gigai.scout.master_store import import_master
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.resume_job_cli import TAILOR_IN_RENAMED_LINE, TAILORING_REMOVED_LINE, resume_tailor_removed_command
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import list_tailored_resumes

from tests.support.pipeline_fixtures import ANSWER, JOB, POSTING, PipelineFixture, build_pipeline_fixture

RESUME = """## Experience

### Acme Corp
Senior Engineer | 2019 - 2023

- Built Python services for six years; cut p99 latency by 40%.
- Operated Kubernetes clusters backed by PostgreSQL.

## Skills

- Python, Kubernetes, PostgreSQL
"""
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
OWN = "Own the Python inference services behind 40 product teams."
REWORDED = "Own the Python inference services that 40 product teams depend on."
#: Phrases only the posting has (its title, its sentences, its requirements' words): none may be in the private part.
POSTING_ONLY = ("Staff AI Engineer", "is hiring", "Remote within the United States", "5+ years of Python", "GCP experience", "inference services. Requirements")
#: What only the user wrote (master lines, a heading, the answer): none may be in the posting part.
USER_ONLY = ("Northwind Labs", OWN, "Wrote the Terraform modules", "cut p99 latency by 40%", ANSWER, "Platform: Python")


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    monkeypatch.setenv(PIPELINE_ENV, "on")
    fx = build_pipeline_fixture(tmp_path, monkeypatch, resume=RESUME)
    source = tmp_path / "master.md"
    source.write_text(MASTER, encoding="utf-8")
    assert import_master(home_root=fx.home_root, target=fx.target, source=source, gig_id=fx.gig.resolved.gig_id).status == "created"
    return fx


def _invoke(fx: PipelineFixture, *args: str, as_json: bool = True, command=scout_group):
    return CliRunner().invoke(command, [*args, "--home", str(fx.home_root), "--target", str(fx.target), *(["--json"] if as_json else [])])


def _ok(fx: PipelineFixture, *args: str) -> dict:
    result = _invoke(fx, *args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _refused(fx: PipelineFixture, *args: str) -> dict[str, object]:
    result = _invoke(fx, *args)
    assert result.exit_code == 1, result.output
    return json.loads(result.output.strip().splitlines()[-1])["error"]


def _file(tmp_path: Path, markdown: str, name: str = "handed-back.md") -> str:
    path = tmp_path / name
    path.write_text(markdown, encoding="utf-8")
    return str(path)


def _store(fx: PipelineFixture, tmp_path: Path, markdown: str = MASTER, *more: str) -> dict:
    return _ok(fx, "resume", "store", "--in", _file(tmp_path, markdown), "--job-url", JOB, "--as", "agent", *more)


def _stored(fx: PipelineFixture):
    return list_tailored_resumes(fx.home_root, fx.target, profile_id=fx.profile_id, job_identity=JOB)


def _snapshot(fx: PipelineFixture) -> dict[str, str]:
    """Every file of the home by content digest (the workpad's git objects and scratch caches aside)."""

    found: dict[str, str] = {}
    for path in sorted(fx.home_root.rglob("*")):
        if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts and "cache" not in path.parts:
            found[str(path.relative_to(fx.home_root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def _brief(fx: PipelineFixture, *more: str) -> dict:
    return _ok(fx, "resume", "brief", "--job-url", JOB, *more)


# --- store, and the brief of what is stored ----------------------------------------------------------------


def test_store_stores_a_handback_and_the_briefs_resume_handed_back_unchanged_changes_nothing(fx: PipelineFixture, tmp_path: Path) -> None:
    payload = _store(fx, tmp_path, MASTER, "--source", "picked with the user")

    assert payload["ok"] is True and payload["changed"] is True and "renamed" not in payload
    assert payload["edited"] == {"written_by": "agent", "edited_at": payload["edited"]["edited_at"], "source": "picked with the user"}
    (stored,) = _stored(fx)
    assert "Northwind Labs" in stored.markdown and stored.sources.master is not None

    yours = _brief(fx)
    assert yours["label"] == "user-private" and yours["profile_id"] == fx.profile_id and yours["basis"] == "master"
    lines = {line["text"]: line for line in yours["master"]["lines"]}
    assert all(line["printed"] and line["left_out"] is None for line in lines.values()) and len(lines) == 5
    own_id = lines[OWN]["id"]
    markdown: str = yours["resume"]["markdown"]
    assert f"- {OWN} <!-- id:{own_id} -->" in markdown
    headings = [line for line in markdown.splitlines() if line.startswith("### ")]
    assert [line.split(" <!--")[0] for line in headings] == ["### Northwind Labs", "### Acme Corp"]
    assert all(line.endswith("-->") and "<!-- id:r-" in line for line in headings)
    assert yours["resume"]["edited_by"] == "agent" and yours["length"]["lines"] == stored.result.line_count()

    # The brief's own markdown is a hand-back as it stands: ids are comments, headings are the master's.
    again = _store(fx, tmp_path, markdown)
    assert again["changed"] is False and again["recheck"]["result"] == "unchanged"

    # A line reworded with the id the brief gave is stored, and the next brief prints it with its sources.
    reworded = markdown.replace(f"- {OWN} <!-- id:{own_id} -->", f"- {REWORDED} <!-- src: {own_id} -->")
    assert _store(fx, tmp_path, reworded)["changed"] is True
    after = _brief(fx)
    assert f"- {REWORDED} <!-- src: {own_id} -->" in after["resume"]["markdown"]
    assert {line["text"]: line for line in after["master"]["lines"]}[OWN]["printed"] is True  # a reworded line counts for the master id it cites


def test_the_brief_is_two_calls_that_never_mix_and_it_calls_no_model_and_writes_nothing(fx: PipelineFixture, tmp_path: Path) -> None:
    _store(fx, tmp_path)
    _brief(fx)  # one read first: a scratch cache may be written by the first read of a home
    before, calls = _snapshot(fx), fx.model.calls

    yours, posting = _brief(fx), _brief(fx, "--posting")
    yours_text = _invoke(fx, "resume", "brief", "--job-url", JOB, as_json=False)
    posting_text = _invoke(fx, "resume", "brief", "--job-url", JOB, "--posting", as_json=False)
    assert yours_text.exit_code == 0 and posting_text.exit_code == 0, (yours_text.output, posting_text.output)

    assert fx.model.calls == calls, "the brief calls no model"
    assert _snapshot(fx) == before, "the brief writes nothing"

    assert (yours["part"], yours["label"]) == ("yours", "user-private") and set(yours["_labels"].values()) == {"user-private"}
    assert (posting["part"], posting["label"]) == ("posting", "public-untrusted") and set(posting["_labels"].values()) == {"public-untrusted"}
    private = json.dumps(yours) + yours_text.output
    public = json.dumps(posting) + posting_text.output
    for phrase in POSTING_ONLY:
        assert phrase not in private, phrase
        assert phrase in public, phrase
    for phrase in USER_ONLY:
        assert phrase not in public, phrase
        assert phrase in private, phrase
    # The posting is fenced; the rows have the same ids in both parts; the file's name (the posting's company and role) is the posting part's.
    assert posting["posting"].startswith("<<<UNTRUSTED_POSTING_TEXT\n") and POSTING in posting["posting"]
    assert [row["id"] for row in yours["requirements"]] == [row["id"] for row in posting["requirements"]] == ["r1", "r2"]
    assert [row["text"] for row in posting["requirements"]] == ["5+ years of Python", "GCP experience"]
    assert all("text" not in row and "requirement" not in row for row in yours["requirements"])
    assert posting["resume_file"].endswith(".md") and posting["resume_file"] not in private
    assert yours["resume"]["folder"] and yours["resume"]["folder"] not in public
    # The answer is offered under the id a line cites it by; its question (the assessment's words) is in neither part.
    assert {"id": "A cloud:gcp", "kind": "answer", "text": ANSWER, "says_no": False, "denies": []} in yours["sources"]
    assert "Have you run workloads on GCP?" not in private + public
    # The hand-back command names the job and the profile; the two calls name each other.
    assert yours["commands"]["store"] == f"gigai scout resume store --in FILE --job-url {JOB} --profile {fx.profile_id} --as agent --json"
    assert f"gigai scout resume brief --job-url {JOB} --profile {fx.profile_id} --posting" in yours_text.output
    assert f"gigai scout resume brief --job-url {JOB} --profile {fx.profile_id}\n" in posting_text.output


def test_brief_out_writes_the_part_to_the_named_file_only(fx: PipelineFixture, tmp_path: Path) -> None:
    _store(fx, tmp_path)
    out = tmp_path / "brief" / "yours.txt"
    summary = _brief(fx, "--out", str(out))
    assert summary == {"ok": True, "out_path": str(out), "part": "yours", "label": "user-private", "job_identity": JOB, "profile_id": fx.profile_id}
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["part"] == "yours" and written["rules"][0].startswith("The words are yours and the user's to change")
    text = tmp_path / "posting.txt"
    plain = _invoke(fx, "resume", "brief", "--job-url", JOB, "--posting", "--out", str(text), as_json=False)
    assert plain.exit_code == 0 and plain.output.strip() == f"Wrote the posting part of the brief (public-untrusted) to {text}."
    assert text.read_text(encoding="utf-8").startswith("GigAI job brief, part 2 of 2: the posting (public-untrusted).")


def test_brief_needs_a_stored_assessment_and_a_posting_link(fx: PipelineFixture) -> None:
    missing = _refused(fx, "resume", "brief", "--job-url", "https://jobs.example.test/acme/never-assessed")
    assert missing["code"] == "assessment_missing" and "gigai scout jobs assess" in str(missing["message"])
    # 0.1.11.9: a job has ONE assessment, and a role's id picks no record: naming a role that never assessed the job
    # (0.1.11: `assessment_missing` "for profile ...") reads the job's own brief. (PJ3 decides what `--profile` is for.)
    named = _ok(fx, "resume", "brief", "--job-url", JOB, "--profile", "profile_00000000-0000-4000-8000-00000000dead")
    assert named == _ok(fx, "resume", "brief", "--job-url", JOB)
    assert _refused(fx, "resume", "brief", "--job-url", "not a link")["code"] == "invalid_value"


# --- the old spelling ---------------------------------------------------------------------------------------------


def test_the_old_in_spelling_does_what_store_does_and_names_the_new_one_in_one_line(fx: PipelineFixture, tmp_path: Path) -> None:
    handed_back = _file(tmp_path, MASTER)
    payload = _ok(fx, "resume", "tailor", "--in", handed_back, "--job-url", JOB, "--as", "agent")
    assert payload["changed"] is True and payload["edited"]["written_by"] == "agent"
    assert payload["renamed"] == {"from": "gigai scout resume tailor --in", "to": "gigai scout resume store", "message": TAILOR_IN_RENAMED_LINE}
    (stored,) = _stored(fx)

    # The new name stores the same resume: handing the same file back through it changes nothing.
    assert _store(fx, tmp_path)["changed"] is False
    assert _stored(fx) == (stored,)

    plain = _invoke(fx, "resume", "tailor", "--in", handed_back, "--job-url", JOB, "--as", "agent", as_json=False)
    assert plain.exit_code == 0, plain.output
    assert plain.output.count(TAILOR_IN_RENAMED_LINE) == 1 and "gigai scout resume store --in FILE --job-url URL" in TAILOR_IN_RENAMED_LINE
    assert "No change: this is already the stored resume" in plain.output


def test_the_removed_tailor_command_is_a_typed_exit_that_names_resume_pick_and_still_forwards_in(fx: PipelineFixture, tmp_path: Path) -> None:
    """The helper the packet that removes the tailor call wires in place of ``resume tailor``; not registered here."""

    assert scout_group.commands["resume"].commands["tailor"] is not resume_tailor_removed_command  # type: ignore[attr-defined]
    removed = _invoke(fx, "--job-url", JOB, command=resume_tailor_removed_command)
    assert removed.exit_code == 1
    assert json.loads(removed.output)["error"] == {"code": "tailoring_removed", "message": TAILORING_REMOVED_LINE}
    assert "`gigai scout resume pick --job-url URL`" in TAILORING_REMOVED_LINE and "no longer rewrites a resume with a model" in TAILORING_REMOVED_LINE
    plain = _invoke(fx, "--job-url", JOB, as_json=False, command=resume_tailor_removed_command)
    assert plain.exit_code == 1 and TAILORING_REMOVED_LINE in plain.output
    assert fx.model.calls == 0 and _stored(fx) == ()
    # The options that only served the model call are gone.
    names = {option for param in resume_tailor_removed_command.params for option in getattr(param, "opts", ())}
    assert not names & {"--model-target", "--resume", "--resume-text", "--job-text", "--title", "--company"}
    assert {"--job-url", "--in", "--as", "--source", "--profile", "--out", "--target", "--home", "--json"} <= names
    # ``--in`` forwards for one release.
    forwarded = _invoke(fx, "--in", _file(tmp_path, MASTER), "--job-url", JOB, "--as", "agent", command=resume_tailor_removed_command)
    assert forwarded.exit_code == 0, forwarded.output
    assert json.loads(forwarded.output.strip().splitlines()[-1])["renamed"]["to"] == "gigai scout resume store"
    assert len(_stored(fx)) == 1


# --- --fit and --resolves: checked before anything is stored ------------------------------------------------------


def _long_master() -> str:
    """A master whose every line handed back prints on well over two pages (six roles of sixteen long bullets)."""

    words = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango".split()
    out = ["## Summary", "", "- Engineer with nine years on Python inference services.", "", "## Experience", ""]
    for role in range(6):
        out += [f"### {['Northwind Labs', 'Acme Corp', 'Initech', 'Globex', 'Umbrella', 'Hooli'][role]}", f"Staff Engineer | {2023 - 2 * role} - {2025 - 2 * role}" if role else "Staff Engineer | 2023 - Present", ""]
        for bullet in range(16):
            out.append(f"- Shipped {words[(bullet + role) % 20]} {words[(bullet * 3 + role) % 20]} {words[(bullet * 7 + 1) % 20]} service work for the {words[bullet % 20]} platform team " + "and kept it running " * 3 + f"item {role}{bullet}.")
        out.append("")
    out += ["## Skills", "", "- Platform: Python, Kubernetes, PostgreSQL, Terraform", ""]
    return "\n".join(out)


@pytest.fixture
def long_fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    monkeypatch.setenv(PIPELINE_ENV, "on")
    fx = build_pipeline_fixture(tmp_path, monkeypatch, resume=RESUME)
    source = tmp_path / "long-master.md"
    source.write_text(_long_master(), encoding="utf-8")
    assert import_master(home_root=fx.home_root, target=fx.target, source=source, gig_id=fx.gig.resolved.gig_id).status == "created"
    return fx


def test_fit_cuts_an_over_long_handback_by_code_and_records_a_restorable_cut_and_without_it_the_page_limit_refuses(
    long_fx: PipelineFixture, tmp_path: Path,
) -> None:
    fx = long_fx
    long = _long_master()
    refused = _refused(fx, "resume", "store", "--in", _file(tmp_path, long), "--job-url", JOB, "--as", "agent")
    assert refused["code"] == "edited_resume_unsupported" and any(item["code"] == "over_page_limit" for item in refused["problems"])
    assert _stored(fx) == ()

    payload = _store(fx, tmp_path, long, "--fit")
    (stored,) = _stored(fx)
    length = stored.result.length
    assert payload["changed"] is True and length is not None and length.status == "cut" and length.leaves_out()
    assert length.pages is not None and length.pages <= 2 and not length.over()
    lines_cut = length.trimmed_count() + len(length.cut)
    assert lines_cut > 0 and stored.result.line_count() < long.count("\n- ") + long.count("\n### ")
    # Restorable: one Restore puts every cut line back.
    from gigai.scout.tailor_length import restore_cut

    assert restore_cut(stored.result).line_count() > stored.result.line_count()
    # A hand-back that fits two pages is stored whole with --fit: nothing is cut.
    short = "\n".join(long.split("\n")[:12]) + "\n\n## Skills\n\n- Platform: Python, Kubernetes, PostgreSQL, Terraform\n"
    assert _store(fx, tmp_path, short, "--fit")["changed"] is True
    assert _stored(fx)[0].result.length is None


def test_a_handback_that_names_a_suggestion_the_job_does_not_have_stores_nothing(fx: PipelineFixture, tmp_path: Path) -> None:
    error = _refused(fx, "resume", "store", "--in", _file(tmp_path, MASTER), "--job-url", JOB, "--as", "agent", "--resolves", "sg-1,sg-3")
    assert error["code"] == "suggestions_not_found", error  # this job was assessed before 0.1.11: it has no record to name one in
    assert _stored(fx) == ()
    bad = _refused(fx, "resume", "store", "--in", _file(tmp_path, MASTER), "--job-url", JOB, "--resolves", "the first one")
    assert bad["code"] == "invalid_value" and "sg-<n>" in str(bad["message"])
    assert _stored(fx) == ()


def test_a_refused_handback_lists_every_problem_as_data(fx: PipelineFixture, tmp_path: Path) -> None:
    inflated = MASTER.replace(f"- {OWN}", "- Led the Python inference services behind 55 product teams.")
    error = _refused(fx, "resume", "store", "--in", _file(tmp_path, inflated), "--job-url", JOB, "--as", "agent")
    assert error["code"] == "edited_resume_unsupported"
    problems = error["problems"]
    assert isinstance(problems, list) and problems and all(set(item) == {"line", "code", "what", "fix"} for item in problems)
    assert "inference services behind" not in json.dumps(error), "a refusal names the line number, never the line's text"
    assert _stored(fx) == ()


def test_store_takes_the_identity_of_a_pasted_job_like_brief_and_pick(fx: PipelineFixture, tmp_path: Path) -> None:
    """E2EFIX F1: a pasted posting's ``text:sha256:...`` identity was refused by store (``url must use http or https``)."""

    from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput, ResolvedJob
    from gigai.scout.quick_assess import run_quick_assessment
    from tests.support.answers_stories_fixtures import config
    from tests.support.pipeline_fixtures import assessment

    text = POSTING + "\nPasted by the user, not fetched."
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    identity = f"text:sha256:{digest}"
    fx.model.assessed = assessment(met=1)
    pasted = ResolvedJob(
        job_identity=identity, source_url=None, normalized_url=None, fetch_kind="pasted", title="", company="", location="",
        text=text, text_sha256=f"sha256:{digest}",
    )
    run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_text=text), resume=AssessResumeInput(profile_id=fx.profile_id)),
        home_root=fx.home_root, target=fx.target, config=config(fx.home_root), resolved_job=pasted,
    )
    brief = _ok(fx, "resume", "brief", "--job-url", identity)
    assert brief["label"] == "user-private"
    stored = _ok(fx, "resume", "store", "--in", _file(tmp_path, MASTER), "--job-url", identity, "--as", "agent")
    assert stored["ok"] is True and stored["changed"] is True and stored["job"]["job_identity"] == identity
    (resume,) = list_tailored_resumes(fx.home_root, fx.target, profile_id=fx.profile_id, job_identity=identity)
    assert "Northwind Labs" in resume.markdown


def _hold_the_job(fx: PipelineFixture, question_id: str = "technical:gcp") -> None:
    """Assess the fixture's posting again: the model holds it on one question about the GCP row (a different answer than the fixture's)."""

    from gigai.scout.quick_assess import run_quick_assessment
    from tests.support.answers_stories_fixtures import config
    from tests.support.pipeline_fixtures import resolved_job
    from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput

    rows = [
        {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]},
        {"requirement": "GCP experience", "class": "askable", "status": "unmet", "resume_evidence": []},
    ]
    questions = [{"question_id": question_id, "question": "Have you run workloads on GCP?", "requirement": "GCP experience"}]
    fx.model.assessed = json.dumps({"verdict": "pending_user_answers", "matrix": rows, "suggestions": [], "questions": questions, "not_a_match_reason": None})
    run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=JOB), resume=AssessResumeInput(profile_id=fx.profile_id)),
        home_root=fx.home_root, target=fx.target, config=config(fx.home_root), resolved_job=resolved_job(JOB),
    )


def test_every_open_question_carries_its_id_in_the_brief_and_the_pick_and_answers_save_takes_that_id(fx: PipelineFixture, tmp_path: Path) -> None:
    """E2EFIX F3: an agent could not read a question's id (only the requirement ids), so it guessed one."""

    _hold_the_job(fx)
    brief = _brief(fx)
    assert [item["question_id"] for item in brief["open_questions"]] == ["technical:gcp"]
    (asked,) = [row for row in brief["requirements"] if row["question_id"]]
    assert asked["question_id"] == "technical:gcp" and brief["open_questions"][0]["row"] == asked["id"]
    assert all(row["question_id"] is None for row in brief["requirements"] if row is not asked)
    assert "Have you run workloads" not in json.dumps(brief), "the private part holds ids, never the question's words"
    text = _invoke(fx, "resume", "brief", "--job-url", JOB, as_json=False).output
    assert f"asked: answers save technical:gcp" in text

    picked = _ok(fx, "resume", "pick", "--job-url", JOB)
    assert [item["question_id"] for item in picked["open_questions"]] == ["technical:gcp"]
    assert picked["open_questions"][0]["row"] == asked["id"]

    saved = _ok(fx, "answers", "save", picked["open_questions"][0]["question_id"], "--answer-text", "Two years on GCP.", "--as", "agent")
    assert saved["answer"]["question_id"] == "technical:gcp"
    assert _ok(fx, "answers", "show", "technical:gcp")["answer"]["answer"] == "Two years on GCP."
    assert _brief(fx)["open_questions"] == [], "an answered question is no longer open"


def test_a_store_whose_check_could_not_reach_the_model_says_so_in_a_typed_field_and_a_visible_line(
    fx: PipelineFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2EFIX F5: a sandbox without network failed the check inside store's drain, and store ended ``ok`` with no word of it."""

    def unreachable(prompt: str):
        raise OSError("connection refused")

    monkeypatch.setattr(fx.model, "answer", unreachable)
    payload = _store(fx, tmp_path)
    assert payload["ok"] is True and payload["changed"] is True and len(_stored(fx)) == 1, "the store itself succeeded"
    assert payload["recheck_failed"] is not None and payload["recheck_failed"]["error_code"] in ("model_unavailable", "model_target_unavailable")
    assert payload["recheck_failed"]["steps"], payload


def test_a_store_whose_check_could_not_reach_the_model_prints_a_warning_with_the_code(
    fx: PipelineFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreachable(prompt: str):
        raise OSError("connection refused")

    monkeypatch.setattr(fx.model, "answer", unreachable)
    result = _invoke(fx, "resume", "store", "--in", _file(tmp_path, MASTER), "--job-url", JOB, "--as", "agent", as_json=False)
    assert result.exit_code == 0, result.output
    (warning,) = [line for line in result.output.splitlines() if "WARNING" in line]
    assert "stored" in warning and ("model_unavailable" in warning or "model_target_unavailable" in warning), result.output


# --- resume pick ---------------------------------------------------------------------------------------------------


def test_pick_shows_what_is_stored_and_recomputes_nothing(fx: PipelineFixture, tmp_path: Path) -> None:
    none_yet = _ok(fx, "resume", "pick", "--job-url", JOB)
    assert none_yet["resume"] is None and none_yet["verdict"] == "matched_above_threshold" and none_yet["profile_id"] == fx.profile_id

    _store(fx, tmp_path)
    _ok(fx, "resume", "pick", "--job-url", JOB)
    before, calls = _snapshot(fx), fx.model.calls
    view = _ok(fx, "resume", "pick", "--job-url", JOB)
    plain = _invoke(fx, "resume", "pick", "--job-url", JOB, as_json=False)
    assert _snapshot(fx) == before and fx.model.calls == calls

    (stored,) = _stored(fx)
    resume = view["resume"]
    assert resume["made_by"] == "scout.tailor.attach" and resume["edited"]["written_by"] == "agent"
    assert resume["markdown"] == stored.markdown and resume["lines"] == stored.result.line_count()
    assert resume["folder_path"].endswith(".md")
    assert view["schema_version"] == "scout-job-resume-pick:1" and view["proposed"] is None and view["conflicts"] == []
    assert plain.exit_code == 0 and f"Job: {JOB}" in plain.output and "edited by agent" in plain.output and "Conflicts: none" in plain.output
    # An assessment made before 0.1.11 stores no gate and no record; the resume is the user's (an attached edit): never replaced.
    assert view["gate"] is None and view["picked"] is None and resume["replaceable"] is False
    assert "Gate: none stored" in plain.output and "yours, kept as it is" in plain.output

    two = _refused(fx, "resume", "pick", "--job-url", JOB, "--refresh", "--draft")
    assert two["code"] == "invalid_value"
    assert _refused(fx, "resume", "pick", "--job-url", "https://jobs.example.test/acme/never-assessed")["code"] == "assessment_missing"


def test_a_gigai_without_the_pick_action_answers_a_typed_not_available(fx: PipelineFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _store(fx, tmp_path)
    monkeypatch.delattr(pick, job_resume_port.SETTLE_STORED, raising=False)  # scout.pick without its step for a stored job
    with pytest.raises(NotImplementedError) as raised:
        job_resume_port.settle_stored()
    assert isinstance(raised.value, job_resume_port.NotBuilt) and raised.value.code == "pick_not_available"

    before, calls = _stored(fx), fx.model.calls
    for flag in ("--refresh", "--draft"):
        error = _refused(fx, "resume", "pick", "--job-url", JOB, flag)
        assert error["code"] == "pick_not_available", error
        # 0.1.11.3: for the user, never the name of the part ("scout.pick.settle_stored is not part of it" was on a job page).
        assert "scout.pick" not in str(error["message"]) and "settle_stored" not in str(error["message"])
        assert "gigai scout jobs assess URL --again" in str(error["message"])
    for flag in ("--use-proposed", "--dismiss-proposed"):
        assert _refused(fx, "resume", "pick", "--job-url", JOB, flag)["code"] == "no_proposed_resume"
    assert _stored(fx) == before and fx.model.calls == calls


def test_a_pick_step_goes_to_the_pick_action_once_and_never_while_the_assessment_is_stale(
    fx: PipelineFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a pick action (a fake: the real one is ``scout.pick``'s): what it is called with, and the two rules held in front of it."""

    _store(fx, tmp_path)
    model_calls = fx.model.calls
    calls: list[tuple[tuple, dict]] = []

    def fake_settle(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(job_resume_port, "settle_stored", lambda: fake_settle)
    real_view = job_actions.pick_view
    state: dict[str, object] = {"gate": {"decision": "suggest", "ready": True, "reasons": []}, "stale": []}
    monkeypatch.setattr(job_actions, "pick_view", lambda *args, **kwargs: {**real_view(*args, **kwargs), **state})

    refreshed = _ok(fx, "resume", "pick", "--job-url", JOB, "--refresh")
    assert refreshed["action"] == "refresh"
    ((args, kwargs),) = calls
    assert args == (fx.home_root, fx.target, fx.profile_id, JOB) and kwargs["action"] == "refresh" and str(kwargs["now"]).endswith("Z")

    # A resume is suggested already: a draft is refused, and the action is not called.
    assert _refused(fx, "resume", "pick", "--job-url", JOB, "--draft")["code"] == "draft_not_needed"
    # The stored assessment is stale: a re-pick would sit beside scores made on other evidence. Re-assess instead.
    state["stale"] = ["assessment_stale:older_prompt", "master_newer"]
    stale = _refused(fx, "resume", "pick", "--job-url", JOB, "--refresh")
    assert stale["code"] == "assessment_stale" and "assessment_stale:older_prompt" in str(stale["message"]) and "Re-assess" in str(stale["message"])
    assert len(calls) == 1
    # A job whose gate holds gets its draft on request; a note that the master is newer does not hold a re-pick.
    state.update(gate={"decision": "hold_unmet", "ready": False, "reasons": [{"code": "askable_unmet", "requirement": None}]}, stale=["master_newer"])
    assert _ok(fx, "resume", "pick", "--job-url", JOB, "--draft")["action"] == "draft"
    assert _ok(fx, "resume", "pick", "--job-url", JOB, "--refresh")["action"] == "refresh"
    assert [kwargs["action"] for _args, kwargs in calls] == ["refresh", "draft", "refresh"]
    assert fx.model.calls == model_calls, "no pick step calls a model"


# --- Apply: the PDF of the stored job resume --------------------------------------------------------------------------


def test_resume_pdf_takes_the_job_url_alone_and_tailored_stays_an_accepted_spelling(fx: PipelineFixture, tmp_path: Path) -> None:
    _store(fx, tmp_path)
    alone = _ok(fx, "resume", "pdf", "--job-url", JOB, "--out", str(tmp_path / "alone.pdf"))
    spelled = _ok(fx, "resume", "pdf", "--tailored", "--job-url", JOB, "--out", str(tmp_path / "spelled.pdf"))
    assert alone["source"] == spelled["source"] == "tailored" and alone["pages"] == spelled["pages"] >= 1
    assert (tmp_path / "alone.pdf").read_bytes().startswith(b"%PDF") and (tmp_path / "spelled.pdf").read_bytes().startswith(b"%PDF")
    # Still exactly one of a file or a job.
    both = _refused(fx, "resume", "pdf", "--in", _file(tmp_path, MASTER), "--job-url", JOB)
    assert both["code"] == "invalid_value"
    assert _refused(fx, "resume", "pdf")["code"] == "invalid_value"
