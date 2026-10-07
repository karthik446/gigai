"""0.1.10.9 master P4: the END outcome of one tailoring when a master resume is stored.

The UAT finding, on synthetic data (``tests/evals/fixtures/master``, the
master-resume spike's invented person): a profile whose own resume is older
(3 pages, no line about the newest project) is tailored to a posting that the
newer work answers.  Before P4 the tailoring could only show what that resume
holds, and the length rule then left out every role but the newest.  With a
master, the tailoring reads the job's candidate set of the WHOLE master.

Every test goes through ``run_tailored_resume`` (what ``POST
/api/tailored-resumes`` and ``gigai scout resume tailor`` call) on a temp home
and reads what is stored: the JSON, the markdown, the PDF's page count.  The
master is made the way the operator will make it: ``gigai scout resume master
init`` from the profiles' own resumes.  The model is a script that reads the
prompt it is sent and copies the lines it lists, so the same test runs on the
code before P4; no test here imports anything P4 adds.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.canonical import canonical_json_bytes
from gigai.cli import cli
from gigai.scout import profile_records
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessResumeInput
from gigai.scout.find_jobs.contracts import PinnedResume
from gigai.scout.posting_keywords import extract_keywords, mentions
from gigai.scout.resume_import import import_resume_file
from gigai.scout.resume_pdf import stored_resume_pdf
from gigai.scout.tailored_resume import TailorError, TailorRequest, TailorResponse, list_tailored_resumes, run_tailored_resume
from gigai.scout.target_resolution import home_scout_target
from gigai.workpad import resolve_workpad

from tests.support.master_tailor import copies_what_it_is_shown, install_prompt_model as _install_model, listed as _listed
from tests.support.scout_profile_fixtures import default_find_jobs_config
from tests.support.setup_home import setup_home
from tests.support.tailor_cases import ollama_config

FIXTURES = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "master"
AI_RESUME = FIXTURES / "legacy-ai.md"  # the newer resume: 2 pages, has the Taskloom project
SWE_RESUME = FIXTURES / "legacy-swe.md"  # the older one: 3 pages, six roles, no Taskloom
NEWER_PROJECT = "Taskloom"
#: Roles of the invented person that are recent for years to come, and the two that ended long ago.
RECENT_ROLES = ("Lumenfold", "Hexa Cloud")
OLDEST_ROLES = ("Tessel Robotics", "Brightwell Media")


def _posting(posting_id: str = "p1-staff-ai-agent-platform") -> dict[str, str]:
    head, _, body = (FIXTURES / "postings" / f"{posting_id}.md").read_text(encoding="utf-8").partition("\n\n")
    return {**dict(line.split(": ", 1) for line in head.splitlines()), "text": body.strip() + "\n"}


# --- the home --------------------------------------------------------------------------------------


def _cli(home: Path, *args: str, ok: bool = True) -> dict:
    result = CliRunner().invoke(cli, [*args, "--home", str(home), "--json"])
    assert result.exit_code == (0 if ok else 1), result.output
    return json.loads(result.output.strip().splitlines()[-1])


class _Home:
    """A Scout home with two profiles: the default one holds the newer resume, the second the older 3-page one."""

    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "home"
        runner = CliRunner()
        setup_home(self.home, workpad_root=tmp_path / "workpads")
        assert runner.invoke(cli, ["scout", "install", "--home", str(self.home), "--json"]).exit_code == 0
        self.scout = home_scout_target(self.home)
        older = import_resume_file(home_root=self.home, requested_target=self.scout, source=SWE_RESUME)
        added = runner.invoke(cli, ["scout", "resume", "add", str(AI_RESUME), "--home", str(self.home), "--json"])
        assert added.exit_code == 0, added.output
        (self.scout / "find-jobs.json").write_bytes(canonical_json_bytes(default_find_jobs_config().to_json()))
        self.resolved = resolve_workpad(home_root=self.home, requested_target=self.scout, gig_id=None, allow_semantic_state=True)
        default = profile_records.selected_profile(self.resolved, home_root=self.home, target=self.scout)
        assert default is not None
        self.ai_id = default.profile_id
        self.swe_id = profile_records.create_profile(
            self.resolved, label="Staff Software Engineer", titles=("staff software engineer",), titles_to_avoid=(),
            queries=("staff software engineer",), resume_ref=PinnedResume(older.record_id, older.revision_id, older.content_sha256),
        ).profile_id

    def migrate(self) -> dict:
        """``gigai scout resume master init``: the master from the two profiles' resumes, the one conflict answered."""

        asked = _cli(self.home, "scout", "resume", "master", "init")
        answers = [part for question in asked.get("questions", []) for part in ("--answer", f"{question['question_id']}=a")]
        done = _cli(self.home, "scout", "resume", "master", "init", *answers) if answers else asked
        assert done["status"] == "created", done
        return done

    def master(self) -> dict:
        """``master show --json``: ``{"items": {id: text}, "entries": {id: heading}, "skills": [...]}``."""

        shown = _cli(self.home, "scout", "resume", "master", "show")["master"]
        return {
            "items": {item["id"]: item["text"] for item in shown["items"]},
            "kinds": {item["id"]: item["kind"] for item in shown["items"]},
            "entries": {entry["id"]: entry["heading"] for entry in shown["entries"]},
            "skills": [skill for item in shown["items"] if item["kind"] == "skills" for skill in item["skills"]],
            "revision": shown["revision"],
            "revision_id": shown["revision_id"],
        }

    def tailor(self, profile_id: str, posting: dict[str, str] | None = None, **kwargs: object) -> tuple[TailorResponse, dict]:
        posting = posting or _posting()
        response = run_tailored_resume(
            TailorRequest(job=AssessJobInput(job_text=posting["text"], title=posting["title"], company=posting["company"]), resume=AssessResumeInput(profile_id=profile_id)),
            home_root=self.home, target=self.scout, config=ollama_config(self.home), **kwargs,  # type: ignore[arg-type]
        )
        on_disk = json.loads(Path(response.stored_path).read_text(encoding="utf-8"))
        assert Path(response.markdown_path).read_text(encoding="utf-8") == response.markdown == on_disk["markdown"]
        return response, on_disk

    def stored(self, profile_id: str) -> TailorResponse:
        (stored,) = list_tailored_resumes(self.home, self.scout, profile_id=profile_id)
        return stored

    def pages(self, profile_id: str) -> int:
        rendered, _name = stored_resume_pdf(self.stored(profile_id), home_root=self.home, count_pages=True)
        assert rendered.pages is not None
        return rendered.pages


@pytest.fixture()
def home(tmp_path: Path) -> _Home:
    return _Home(tmp_path)


def _sections(on_disk: dict) -> dict[str, dict]:
    return {section["heading"]: section for section in on_disk["result"]["sections"]}


def _roles(on_disk: dict) -> list[str]:
    return [entry["heading"][0]["text"].removeprefix("### ") for entry in _sections(on_disk).get("experience", {}).get("entries", [])]


def _body_lines(on_disk: dict) -> list[dict]:
    """Every shown line that is not an entry heading."""

    out: list[dict] = []
    for section in on_disk["result"]["sections"]:
        out += section.get("lines", [])
        for entry in section.get("entries", []):
            out += entry["bullets"]
    return out


def _all_lines(on_disk: dict) -> list[dict]:
    return [*_body_lines(on_disk), *(line for section in on_disk["result"]["sections"] for entry in section.get("entries", []) for line in entry["heading"])]


# --- the UAT finding ---------------------------------------------------------------------------------


def _only_in_the_newer_resume(on_disk: dict) -> list[str]:
    """The shown lines that the newer resume holds and the older profile's own resume does not."""

    older, newer = SWE_RESUME.read_text(encoding="utf-8"), AI_RESUME.read_text(encoding="utf-8")
    return [text for line in _body_lines(on_disk) if (text := line["text"].removeprefix("- ")) in newer and text not in older]


def _no_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    """From here on a page count or a layout made while a tailoring is settled fails the test (0.1.11.5 item 1c)."""

    from gigai.scout import tailor_length_store, tailor_master

    def refuse(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("a tailoring from the master was laid out or its pages counted")

    monkeypatch.setattr(tailor_master, "measure_pages", refuse)
    monkeypatch.setattr(tailor_length_store, "measure_pages", refuse)
    for name in ("pages_at", "measure_markdown", "fewest_pages"):
        monkeypatch.setattr(f"gigai.scout.resume_pdf.{name}", refuse)


def test_with_a_master_the_older_profiles_tailoring_shows_what_only_the_newer_resume_held_with_every_role_and_no_page_fit(home: _Home, monkeypatch: pytest.MonkeyPatch) -> None:
    """0.1.11.5 (c): a tailoring from the master is fitted to NO page (the user fits the page with the spacing of the
    job's preview).  Until then this test was ``..._on_two_pages_with_its_recent_roles`` and pinned ``pages <= 2`` and
    the two oldest roles cut down to their heading line."""

    assert NEWER_PROJECT not in SWE_RESUME.read_text(encoding="utf-8") and NEWER_PROJECT in AI_RESUME.read_text(encoding="utf-8")
    home.migrate()
    _install_model(monkeypatch, copies_what_it_is_shown)
    _no_layout(monkeypatch)

    response, on_disk = home.tailor(home.swe_id)

    # The newer resume's lines are in the master, so a tailoring for a posting they answer shows them, whatever
    # the profile's own resume holds. (Which ones is the selector's call for the posting: with sel-2 the newer
    # PROJECT's first line ranks just under what fits here, a heading and a line being dearer than a line.)
    assert len(_only_in_the_newer_resume(on_disk)) >= 3, "the tailoring must be able to show lines only the newer resume held"
    # Nothing was cut for a page: no length record, nothing "cut for length", no page-driven conflict.
    assert on_disk["result"].get("length") is None and on_disk["selection"]["cut_for_length"] == []
    assert not {conflict["kind"] for conflict in on_disk["selection"].get("conflicts", [])} & {"earlier_roles", "over_budget"}
    # No employer is dropped (0.1.11.4 item 9): every role of the master is on the resume, the recent ones with lines,
    # any other by its lines or by its one line under "Earlier experience".
    roles = _roles(on_disk)
    assert all(any(role.startswith(recent) for role in roles) for recent in RECENT_ROLES), roles
    experience = _sections(on_disk)["experience"]["entries"]
    assert all(any(role.startswith(old) for role in roles) for old in OLDEST_ROLES), roles
    for entry in experience:
        name = entry["heading"][0]["text"].removeprefix("### ")
        if entry["bullets"]:
            assert f"### {name}" in response.markdown
        else:
            assert f"### {name}" not in response.markdown and name in response.markdown.split("### Earlier experience", 1)[1].split("\n## ", 1)[0]
    # The posting's must-haves the master can show are on the resume.
    posting = _posting()
    keywords = extract_keywords(posting["text"], title=posting["title"], skills=home.master()["skills"])
    missing = [term for term in keywords.must if not mentions(response.markdown, term)]
    in_master = " ".join(home.master()["items"].values())
    assert [term for term in missing if mentions(in_master, term)] == [], missing


# --- every line traces to the master ---------------------------------------------------------------


def test_every_line_of_the_tailoring_names_the_master_line_it_is(home: _Home, monkeypatch: pytest.MonkeyPatch) -> None:
    home.migrate()
    _install_model(monkeypatch, copies_what_it_is_shown)
    _response, on_disk = home.tailor(home.swe_id)
    master = home.master()

    skills = _sections(on_disk)["skills"]["lines"]
    checked = 0
    for line in _all_lines(on_disk):
        for ref in line["refs"]:
            assert ref["kind"] == "resume"
            if line in skills:
                assert "item_id" not in ref, "the Skills line is a list code assembled, not one master line"
                continue
            item_id = ref["item_id"]
            bare = ref["text"].removeprefix("### ").removeprefix("- ")
            # A line's own id; an entry's id for its heading and the line under the heading.
            assert master["items"].get(item_id) == bare or item_id in master["entries"], (item_id, bare)
            checked += 1
    assert checked > 30
    # What the tailoring was made from is on the record: the master's revision, never its text.
    assert on_disk["sources"]["master"]["revision"] == master["revision"] == 1
    assert on_disk["sources"]["master"]["revision_id"] == master["revision_id"]
    assert set(on_disk["sources"]["master"]) == {"revision", "revision_id", "content_sha256"}


# --- the skills line is the code's -------------------------------------------------------------------


def test_the_skills_line_is_assembled_by_code_and_is_shown_even_when_the_model_leaves_skills_out(home: _Home, monkeypatch: pytest.MonkeyPatch) -> None:
    home.migrate()
    master = home.master()
    assert len(master["skills"]) > 50  # the master lists every skill of both resumes
    _install_model(monkeypatch, lambda prompt: copies_what_it_is_shown(prompt, skip=("skills",)))

    _response, on_disk = home.tailor(home.swe_id)

    (line,) = _sections(on_disk)["skills"]["lines"]
    shown = [name.strip() for name in line["text"].removeprefix("- ").split(",")]
    # The Skills section is kept WHOLE (0.1.11.5 (c), sel-7: no name is cut for a page; until then the names past 40
    # that nothing asked for were the first thing cut for length).
    assert sorted(shown) == sorted(master["skills"]), shown
    # The posting's must-haves the master lists lead the line, in the posting's order.
    posting = _posting()
    keywords = extract_keywords(posting["text"], title=posting["title"], skills=master["skills"])
    asked = [skill for term in keywords.must for skill in master["skills"] if mentions(skill, term)]
    assert asked and set(shown[: len(dict.fromkeys(asked))]) == set(asked)
    # No skill is left out, so none is on the record as cut for length.
    assert on_disk["selection"]["skills"]["left_out"] == []
    assert line["kind"] == "copy"


# --- the fit: lowest value for the posting first, shown, restorable; the tailoring's order is what prints ---


def test_a_tailoring_is_cut_for_no_page_so_it_carries_no_length_record_and_restore_has_nothing_to_put_back(home: _Home, monkeypatch: pytest.MonkeyPatch) -> None:
    """0.1.11.5 (c).  Until then (``test_what_the_fit_left_out_is_on_the_record_and_one_restore_puts_it_back``) the
    tailor call's answer was cut to 2 pages, the cut was on ``result.length`` and one Restore put it back.  Now every
    line the tailoring showed is on the resume, there is no length record, and Restore / cut change nothing.  (The
    record and its Restore on a resume that HAS one are pinned in test_tailor_length.py and test_tailor_master.py.)"""

    home.migrate()
    port = _install_model(monkeypatch, copies_what_it_is_shown)
    _no_layout(monkeypatch)
    response, on_disk = home.tailor(home.swe_id)

    assert on_disk["result"].get("length") is None and response.result.length is None
    assert on_disk["selection"]["cut_for_length"] == [] and on_disk["selection"]["counts"]["cut_for_length"] == 0
    assert "Cut for length" not in response.markdown
    # Everything the tailoring was offered and copied is shown: nothing went for a page.
    offered = [text for _number, text in _listed(port.prompts[0]) if text.startswith("- ")]
    shown = {line["text"] for line in _body_lines(on_disk)}
    assert offered and set(offered) <= shown

    base = ["scout", "resume", "length", "--job-url", response.job.job_identity, "--home", str(home.home), "--target", str(home.scout), "--json"]
    for use in ("--restore", "--cut"):
        done = CliRunner().invoke(cli, [*base, use])
        assert done.exit_code == 0, done.output
        assert home.stored(home.swe_id).result == response.result, "there is nothing left out to put back or cut again"


def test_the_tailoring_s_order_is_what_prints_and_the_posting_decides_which_lines_stay(home: _Home, monkeypatch: pytest.MonkeyPatch) -> None:
    """0110-10-15 changed 0.1.10.9's rule here. Then the fit cut from the END of the order the tailor call gave, so the
    order alone decided which lines stayed. Now the fit cuts the lines worth least for the posting, wherever the
    tailoring put them: the same lines stay whichever way it orders them, and they print in its order."""

    home.migrate()
    _install_model(monkeypatch, copies_what_it_is_shown)
    _first, as_listed = home.tailor(home.swe_id)
    port = _install_model(monkeypatch, lambda prompt: copies_what_it_is_shown(prompt, bullets=lambda numbers: numbers[::-1]))
    _second, reversed_order = home.tailor(home.swe_id)

    def roles(on_disk: dict) -> list[list[str]]:
        return [[line["text"] for line in entry["bullets"]] for entry in _sections(on_disk)["experience"]["entries"]]

    offered = [text for _number, text in _listed(port.prompts[0]) if text.startswith("- ")]
    listed, turned = roles(as_listed), roles(reversed_order)
    assert sum(len(role) for role in listed) < len(offered), "more lines were offered than fit"
    # The same lines stay under every role, and each resume prints them in the order its tailoring gave.
    assert [set(role) for role in turned] == [set(role) for role in listed]
    assert all(role == sorted(role, key=offered.index) for role in listed) and any(len(role) > 1 for role in listed)
    assert [role[::-1] for role in turned] == listed


# --- a job with a stored assessment: the lines it cites (0110-10-15, the real-data gate) ----------------


def test_a_job_with_a_stored_assessment_keeps_the_line_the_assessment_cites_in_the_final_resume(home: _Home, monkeypatch: pytest.MonkeyPatch) -> None:
    """The END outcome on the tailor path (what the pipeline's tailor step runs): the stored tailored resume.

    By words alone a line is not among the lines offered to the tailoring. When the job's stored assessment for the
    profile cites that line for a requirement (by meaning: the requirement shares no word with it), the same
    tailoring shows it.  (Until 0.1.11.5 (c) the line was one the page fit had cut; nothing is cut for a page now.)"""

    from types import SimpleNamespace

    from gigai.scout import quick_assess
    from gigai.scout.find_jobs.contracts import MatrixStatus, RequirementClass, RequirementMatrixRow

    home.migrate()
    _install_model(monkeypatch, copies_what_it_is_shown)
    response, before = home.tailor(home.swe_id)
    master = home.master()
    line = next(
        item["id"] for item in before["selection"]["left_out"]
        if master["kinds"][item["id"]] == "bullet" and item["code"] == "role_limit" and len(master["items"][item["id"]]) > 40
    )
    assert line not in [item["id"] for item in before["selection"]["picked"]] and before["selection"]["cut_for_length"] == []

    asked: list[tuple[str | None, str]] = []

    def stored(_home_root: Path, _target: Path, profile_id: str | None, job_identity: str):
        asked.append((profile_id, job_identity))
        row = RequirementMatrixRow("Zymurgy qualifications", (master["items"][line][:40],), MatrixStatus.MET, RequirementClass.HARD)
        return SimpleNamespace(result=SimpleNamespace(matrix=(row,)))

    monkeypatch.setattr(quick_assess, "read_quick_assessment", stored)
    _response, after = home.tailor(home.swe_id)

    # The assessment read is the one of THIS profile and THIS job.
    assert asked and set(asked) == {(home.swe_id, response.job.job_identity)}
    picked = {item["id"]: item for item in after["selection"]["picked"]}
    assert line in picked and picked[line]["code"] == "requirement_evidence" and picked[line]["reason"].startswith("the line your assessment cites for: Zymurgy qualifications")
    shown = [ref["item_id"] for item in _body_lines(after) for ref in item["refs"] if "item_id" in ref]
    assert line in shown and master["items"][line] in after["markdown"]
    assert "conflicts" not in after["selection"] and after["result"].get("length") is None
    # The code-only fallback (no model answers) keeps it too.
    _install_model(monkeypatch, lambda _prompt: "this is not the JSON a tailoring answers with")
    _response, fallback = home.tailor(home.swe_id)
    assert fallback["selection"]["picked_by"] == "code" and line in [item["id"] for item in fallback["selection"]["picked"]]


# --- Picked / Left out -------------------------------------------------------------------------------


def test_the_stored_tailoring_says_what_was_picked_what_was_left_out_and_why(home: _Home, monkeypatch: pytest.MonkeyPatch) -> None:
    home.migrate()
    _install_model(monkeypatch, copies_what_it_is_shown)
    _response, on_disk = home.tailor(home.swe_id)
    master = home.master()

    selection = on_disk["selection"]
    assert (selection["picked_by"], selection["fallback"], selection["selector_version"]) == ("model", None, "sel-7")
    picked = [line["id"] for line in selection["picked"]]
    left = [line["id"] for line in selection["left_out"]]
    # Picked is exactly what the resume shows; with Left out it is every line of the master, each once.
    shown = list(dict.fromkeys(ref["item_id"] for line in _body_lines(on_disk) for ref in line["refs"] if "item_id" in ref))
    assert picked == shown
    selectable = {item_id for item_id, kind in master["kinds"].items() if kind != "skills"}
    assert set(picked) | set(left) == selectable and len(picked) + len(left) == len(selectable)
    assert all(line["code"] and line["reason"] for line in (*selection["picked"], *selection["left_out"]))
    assert selection["counts"] == {"picked": len(picked), "left_out": len(left), "cut_for_length": len(selection["cut_for_length"])}
    assert "conflicts" not in selection, "nothing mandatory was left without a line: the record carries no conflict"
    # 0.1.11.5 (c): nothing is cut for a page, so no line is left out "for length" (until then the fit's cuts were
    # listed here as ``cut_lowest_value`` / ``cut_role_dropped``) and the resume carries no length record.
    assert selection["cut_for_length"] == [] and on_disk["result"].get("length") is None
    assert not [line for line in selection["left_out"] if line["code"].startswith("cut_") or "cut for length" in line["reason"]]
    # Skills: picked and left out by name, each with its reason.
    skills = selection["skills"]
    assert {skill["name"] for skill in skills["picked"]} | {skill["name"] for skill in skills["left_out"]} == set(master["skills"])
    (line,) = _sections(on_disk)["skills"]["lines"]
    assert [skill["name"] for skill in skills["picked"]] == [name.strip() for name in line["text"].removeprefix("- ").split(",")]
    # Ids, codes and reasons only: no line of the master is copied into the record.
    record = json.dumps(selection)
    assert not any(text in record for text in master["items"].values() if len(text) > 40)


# --- the fallback ------------------------------------------------------------------------------------


def test_when_the_tailor_call_fails_the_code_s_own_selection_is_the_resume(home: _Home, monkeypatch: pytest.MonkeyPatch) -> None:
    home.migrate()
    port = _install_model(monkeypatch, lambda _prompt: "this is not JSON")

    response, on_disk = home.tailor(home.swe_id)

    assert len(port.prompts) == 2, "the call and its one retry were made first"
    assert (on_disk["selection"]["picked_by"], on_disk["selection"]["fallback"]) == ("code", "model_output_invalid")
    # A whole resume: 2 pages, the recent roles, lines only the newer resume held, every line a copy of a master line.
    assert home.pages(home.swe_id) <= 2 and on_disk["result"].get("length") is None
    assert all(any(role.startswith(recent) for role in _roles(on_disk)) for recent in RECENT_ROLES)
    assert len(_only_in_the_newer_resume(on_disk)) >= 3 and response.markdown
    master = home.master()
    bullets = [line for section in on_disk["result"]["sections"] for entry in section.get("entries", []) for line in entry["bullets"]]
    assert bullets and all(line["kind"] == "copy" and master["items"][line["refs"][0]["item_id"]] == line["text"].removeprefix("- ") for line in bullets)
    assert [line["id"] for line in on_disk["selection"]["picked"]] == list(
        dict.fromkeys(ref["item_id"] for line in _body_lines(on_disk) for ref in line["refs"] if "item_id" in ref)
    )


def test_a_model_that_cannot_be_reached_also_falls_back_unless_the_caller_keeps_its_own_retries(home: _Home, monkeypatch: pytest.MonkeyPatch) -> None:
    home.migrate()
    _install_model(monkeypatch, lambda _prompt: OSError("connection refused"))

    _response, on_disk = home.tailor(home.swe_id)
    assert (on_disk["selection"]["picked_by"], on_disk["selection"]["fallback"]) == ("code", "model_unavailable")
    assert on_disk["usage"] is None or on_disk["usage"]["model_calls"] in (0, None)

    # The pipeline's tailor step lets the code stand in only for an invalid answer: an unavailable model fails the
    # step as before, so the queue's retries and lane backoff apply. Nothing is stored over the resume above.
    before = Path(on_disk["stored_path"]).read_bytes()
    with pytest.raises(TailorError) as raised:
        home.tailor(home.swe_id, fallback_codes=frozenset({"model_output_invalid"}))
    assert raised.value.code == "model_unavailable"
    assert Path(on_disk["stored_path"]).read_bytes() == before


# --- where the master is NOT read ----------------------------------------------------------------------


def test_a_profile_whose_resume_was_replaced_by_hand_and_a_pasted_resume_are_tailored_as_before(home: _Home, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home.migrate()
    # The user puts a resume on the profile by hand after the migration: that resume is what a tailoring reads.
    own = tmp_path / "own.md"
    own.write_text("## Summary\n\n- Backend engineer with 9 years on payment systems.\n\n## Skills\n\n- Python, PostgreSQL\n", encoding="utf-8")
    added = CliRunner().invoke(cli, ["scout", "resume", "add", str(own), "--profile", home.swe_id, "--home", str(home.home), "--json"])
    assert added.exit_code == 0, added.output
    port = _install_model(monkeypatch, copies_what_it_is_shown)

    response, on_disk = home.tailor(home.swe_id)
    assert "selection" not in on_disk and "master" not in on_disk["sources"] and '"item_id"' not in json.dumps(on_disk)
    assert [text for _number, text in _listed(port.prompts[0])] == [line for line in own.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert "Backend engineer with 9 years on payment systems." in response.markdown

    # A pasted resume is never a profile's: the master is not read for it either.
    posting = _posting()
    pasted = run_tailored_resume(
        TailorRequest(job=AssessJobInput(job_text=posting["text"], title=posting["title"], company=posting["company"]), resume=AssessResumeInput(resume_text=own.read_text(encoding="utf-8"))),
        home_root=home.home, target=home.scout, config=ollama_config(home.home),
    )
    stored = json.loads(Path(pasted.stored_path).read_text(encoding="utf-8"))
    assert "selection" not in stored and "master" not in stored["sources"] and '"item_id"' not in json.dumps(stored)
