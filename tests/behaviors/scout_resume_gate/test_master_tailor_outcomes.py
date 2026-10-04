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
        done = runner.invoke(cli, ["setup", "--non-interactive", "--home", str(self.home), "--workpad-root", str(tmp_path / "workpads"), "--editor", "/usr/bin/true", "--json"])
        assert done.exit_code == 0, done.output
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


def test_with_a_master_the_older_profiles_tailoring_shows_the_newer_project_on_two_pages_with_its_recent_roles(home: _Home, monkeypatch: pytest.MonkeyPatch) -> None:
    assert NEWER_PROJECT not in SWE_RESUME.read_text(encoding="utf-8") and NEWER_PROJECT in AI_RESUME.read_text(encoding="utf-8")
    home.migrate()
    _install_model(monkeypatch, copies_what_it_is_shown)

    response, on_disk = home.tailor(home.swe_id)

    # The newer project is in the master, so a tailoring for a posting it answers shows it, whatever the profile's own resume holds.
    assert NEWER_PROJECT in response.markdown, "the tailoring must be able to show the master's newer project"
    # It prints on 2 pages, and not by dropping every role but the newest: the recent roles are all there.
    assert home.pages(home.swe_id) <= 2
    roles = _roles(on_disk)
    assert all(any(role.startswith(recent) for role in roles) for recent in RECENT_ROLES), roles
    # For length the oldest roles went first.
    assert not any(role.startswith(old) for role in roles for old in OLDEST_ROLES), roles
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
    assert 5 <= len(shown) <= 28 and set(shown) <= set(master["skills"]), shown
    # The posting's must-haves the master lists lead the line.
    posting = _posting()
    keywords = extract_keywords(posting["text"], title=posting["title"], skills=master["skills"])
    asked = [skill for term in keywords.must for skill in master["skills"] if mentions(skill, term)]
    assert asked and shown[: len(dict.fromkeys(asked))] == list(dict.fromkeys(asked))
    assert line["kind"] == "copy"


# --- the fit: oldest first, shown, restorable; the model's order decides inside it -------------------


def test_what_the_fit_left_out_is_on_the_record_and_one_restore_puts_it_back(home: _Home, monkeypatch: pytest.MonkeyPatch) -> None:
    home.migrate()
    port = _install_model(monkeypatch, copies_what_it_is_shown)
    response, on_disk = home.tailor(home.swe_id)

    # The candidate set was more than fits: the fit left roles and lines out, whole, and says which.
    length = on_disk["result"]["length"]
    assert length["status"] == "cut" and length["pages"] <= 2 < length["full_pages"]
    cut_roles = [role["role"] for role in length["cut"]]
    assert cut_roles and all(any(role.startswith(old) for old in (*OLDEST_ROLES, "Cascade Data")) for role in cut_roles), cut_roles
    offered = [text for _number, text in _listed(port.prompts[0]) if text.startswith("- ")]
    shown = {line["text"] for line in _body_lines(on_disk)}
    assert len(offered) > len(shown)

    base = ["scout", "resume", "length", "--job-url", response.job.job_identity, "--home", str(home.home), "--target", str(home.scout), "--json"]
    restored = CliRunner().invoke(cli, [*base, "--restore"])
    assert restored.exit_code == 0, restored.output
    whole = home.stored(home.swe_id)
    assert whole.result.length is not None and whole.result.length.status == "restored"
    # Everything the tailoring was offered is back, and the resume is over 2 pages again.
    back = {line.text for section in whole.result.sections for line in section.all_lines()}
    assert set(offered) <= back and home.pages(home.swe_id) > 2

    again = CliRunner().invoke(cli, [*base, "--cut"])
    assert again.exit_code == 0, again.output
    assert home.stored(home.swe_id).result == response.result


def test_inside_the_candidate_set_the_tailoring_s_order_decides_which_lines_stay(home: _Home, monkeypatch: pytest.MonkeyPatch) -> None:
    home.migrate()
    _install_model(monkeypatch, copies_what_it_is_shown)
    _first, as_listed = home.tailor(home.swe_id)
    port = _install_model(monkeypatch, lambda prompt: copies_what_it_is_shown(prompt, bullets=lambda numbers: numbers[::-1]))
    _second, reversed_order = home.tailor(home.swe_id)

    def newest_role(on_disk: dict) -> list[str]:
        return [line["text"] for line in _sections(on_disk)["experience"]["entries"][0]["bullets"]]

    offered = [text for _number, text in _listed(port.prompts[0])]
    start = offered.index("### Lumenfold")
    role = [text for text in offered[start + 2 :][: next(i for i, text in enumerate(offered[start + 2 :]) if not text.startswith("- "))]]
    kept, kept_reversed = newest_role(as_listed), newest_role(reversed_order)
    assert len(role) > len(kept) >= 3, "the newest role was offered more lines than fit"
    # The lines that stay are the FIRST lines of the order the tailoring gave: the cut takes the end of its list.
    assert kept_reversed[:3] == role[::-1][:3] and kept[:3] == role[:3]
    assert set(kept_reversed) != set(kept)


# --- Picked / Left out -------------------------------------------------------------------------------


def test_the_stored_tailoring_says_what_was_picked_what_was_left_out_and_why(home: _Home, monkeypatch: pytest.MonkeyPatch) -> None:
    home.migrate()
    _install_model(monkeypatch, copies_what_it_is_shown)
    _response, on_disk = home.tailor(home.swe_id)
    master = home.master()

    selection = on_disk["selection"]
    assert (selection["picked_by"], selection["fallback"], selection["selector_version"]) == ("model", None, "sel-1")
    picked = [line["id"] for line in selection["picked"]]
    left = [line["id"] for line in selection["left_out"]]
    # Picked is exactly what the resume shows; with Left out it is every line of the master, each once.
    shown = list(dict.fromkeys(ref["item_id"] for line in _body_lines(on_disk) for ref in line["refs"] if "item_id" in ref))
    assert picked == shown
    selectable = {item_id for item_id, kind in master["kinds"].items() if kind != "skills"}
    assert set(picked) | set(left) == selectable and len(picked) + len(left) == len(selectable)
    assert all(line["code"] and line["reason"] for line in (*selection["picked"], *selection["left_out"]))
    assert selection["counts"] == {"picked": len(picked), "left_out": len(left), "cut_for_length": len(selection["cut_for_length"])}
    # What the fit cut is left out for that reason, oldest roles first.
    cut = selection["cut_for_length"]
    assert cut and {item["code"] for item in cut} <= {"cut_oldest_role_dropped", "cut_oldest_role_shortened", "cut_lowest_value"}
    reasons = {line["id"]: line["code"] for line in selection["left_out"]}
    assert all(reasons[item["id"]] == item["code"] for item in cut if item["kind"] == "bullet")
    roles = [item["id"] for item in cut if item["kind"] == "role"]
    assert roles and all(master["entries"][role].startswith((*OLDEST_ROLES, "Cascade Data")) for role in roles)
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
    # A whole resume: 2 pages, the recent roles, the newer project, every line a copy of a master line.
    assert home.pages(home.swe_id) <= 2 and on_disk["result"].get("length") is None
    assert all(any(role.startswith(recent) for role in _roles(on_disk)) for recent in RECENT_ROLES)
    assert NEWER_PROJECT in response.markdown
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
