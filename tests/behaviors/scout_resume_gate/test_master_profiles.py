"""0.1.10.9 master P3: profiles on the master resume, and the migration.

The END outcomes, through the real CLI on a temp ``--home``:

* ``gigai scout resume master init`` (no ``--from``) builds the master from the resumes the profiles hold:
  the union of their lines, the same line once, near-duplicates folded, a conflict ASKED and nothing
  written until it is answered;
* each profile's first selection is its own old resume, and its resume is not rewritten: the read model
  does not rebuild, a second ``scout new`` assesses nothing, the pipeline re-opens nothing;
* a selection is sticky: an edited or retired shown line reaches the profile's resume, new master lines
  are only offered ("3 new master lines: refresh?");
* a refresh, and a new profile's first selection, select from the whole master against the postings the
  profile's titles match in the local index, with no model and no request; every existing reader then
  reads the 2-page view through the profile's ``resume_ref``.

Everything is synthetic: ``tests/evals/fixtures/master`` (the spike's invented person: an 8-page master,
and two resumes "as a user has them today": a newer 2-page one and an older 3-page one with two reworded
lines and one line with older numbers). No model is called: the one assessment is made by the fixture
transport. No operator data is read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import subprocess

from click.testing import CliRunner
import pytest

from gigai.canonical import canonical_json_bytes
from gigai.cli import cli
from gigai.scout import master_migration as mm
from gigai.scout import master_profiles as mp
from gigai.scout import profile_records
from gigai.scout.find_jobs.contracts import PinnedResume
from gigai.scout.find_jobs.resume_input import resume_for_profile
from gigai.scout.master_resume import MasterResumeError, parse_master
from gigai.scout.master_selection import SelectionProfile, render_selection, select
from gigai.scout.master_store import load_master
from gigai.scout.resume_import import import_resume_file
from gigai.scout.resume_pdf import measure_markdown, parse_resume_markdown
from gigai.scout.target_resolution import home_scout_target
from gigai.validators import validate_serialized_contract
from gigai.workpad import resolve_workpad

from tests.support.scout_profile_fixtures import default_find_jobs_config
from tests.support.setup_home import setup_home

FIXTURES = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "master"
AI_RESUME = FIXTURES / "legacy-ai.md"  # the newer resume: 2 pages, has the Taskloom project
SWE_RESUME = FIXTURES / "legacy-swe.md"  # the older one: 3 pages, two reworded lines, one line with older numbers
NEW_NUMBERS = "executes 2.1 million tool-calling LLM tasks per day across 140 internal teams"
OLD_NUMBERS = "executes 1.4 million tool-calling LLM tasks per day across 90 internal teams"
#: The two lines the older resume words differently (same numbers): (the newer wording, the older wording).
REWORDED = (
    ("Owned the control plane for a managed database product running 12,000", "Owned the control plane of a managed database product: 12,000"),
    ("Cut p99 API latency from 840 ms to 190 ms by redesigning", "Reduced p99 API latency from 840 ms to 190 ms through"),
)


def _split(markdown: str) -> tuple[list[str], list[str]]:
    """``(the bullet lines outside Skills, the Skills lines)`` of resume markdown, without the marker."""

    lines: list[str] = []
    skills: list[str] = []
    section = ""
    for line in markdown.splitlines():
        if line.startswith("## "):
            section = line[3:].strip().lower()
        elif line.startswith("- "):
            (skills if section == "skills" else lines).append(line[2:].strip())
    return lines, skills


def _lines(markdown: str) -> list[str]:
    return _split(markdown)[0]


def _skills(markdown: str) -> set[str]:
    return {name for line in _split(markdown)[1] for name in line.split(": ", 1)[-1].rstrip(".").split(", ")}


def _setup(tmp_path: Path) -> Path:
    return setup_home(tmp_path / "home", workpad_root=tmp_path / "workpads")


def _master(home: Path, *args: str, ok: bool = True) -> dict:
    result = CliRunner().invoke(cli, ["scout", "resume", "master", *args, "--home", str(home), "--json"])
    assert result.exit_code == (0 if ok else 1), result.output
    return json.loads(result.output.strip().splitlines()[-1])


class _Home:
    """A Scout home with two profiles, each holding its own resume: the default one (the newer resume) and a second."""

    def __init__(self, tmp_path: Path) -> None:
        self.home = _setup(tmp_path)
        runner = CliRunner()
        # The older resume first, so the newer one is the newer record (its wording wins a near-duplicate).
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

    def profile(self, profile_id: str) -> profile_records.ProfileRecord:
        return next(item for item in profile_records.list_profiles(self.resolved) if item.profile_id == profile_id)

    def resume_text(self, profile_id: str) -> str:
        """What every reader of "the profile's resume" reads: the pinned record, digest re-verified."""

        return resume_for_profile(self.profile(profile_id), resolved=self.resolved, home_root=self.home, target=self.scout).text

    def master(self):
        stored = load_master(home_root=self.home, target=self.scout)
        assert stored is not None
        return stored

    def journal_head(self) -> str:
        return subprocess.run(["git", "-C", str(self.resolved.path), "rev-parse", "HEAD"], capture_output=True, check=True, text=True).stdout

    def migrate(self, answer: str = "a") -> dict:
        asked = _master(self.home, "init")
        return _master(self.home, "init", *(part for question in asked["questions"] for part in ("--answer", f"{question['question_id']}={answer}")))

    def status(self, profile_id: str) -> dict:
        return next(item for item in _master(self.home, "selection", "status")["profiles"] if item["profile_id"] == profile_id)

    def write_master(self, edit) -> dict:  # noqa: ANN001 - str -> str over the stored markdown
        """Store an edited copy of the master as its next revision, as a person editing the file would."""

        stored = self.master()
        path = self.home.parent / f"master-{stored.revision.revision + 1}.md"
        path.write_text(edit(stored.master.markdown()), encoding="utf-8")
        return _master(self.home, "init", "--from", str(path), "--revision", str(stored.revision.revision))


@pytest.fixture()
def two(tmp_path: Path) -> _Home:
    return _Home(tmp_path)


# --- the migration: the real CLI ---------------------------------------------------------------


def test_the_migration_asks_about_a_conflict_and_writes_nothing_until_it_is_answered(two: _Home) -> None:
    before = two.journal_head()
    asked = _master(two.home, "init")
    assert (asked["status"], asked["written"], asked["master"], asked["mode"]) == ("needs_answers", False, None, "migration")
    (question,) = asked["questions"]
    assert question["kind"] == "number_conflict" and question["choices"] == ["a", "b", "both"] and question["answer"] is None
    assert (question["section"], question["entry"]) == ("experience", "Lumenfold")
    a, b = question["options"]
    # a is the newer resume's wording, b the older one's; each names the profile whose resume has it.
    assert (a["key"], NEW_NUMBERS in a["text"], a["profiles"]) == ("a", True, ["default"])
    assert (b["key"], OLD_NUMBERS in b["text"], b["profiles"]) == ("b", True, ["Staff Software Engineer"])
    assert asked["migration"]["unanswered"] == [question["question_id"]]
    # Nothing was written: no master, no profile write, the journal did not move. A dry run writes nothing either.
    assert _master(two.home, "show", ok=False)["error"]["code"] == "master_not_found"
    assert _master(two.home, "init", "--dry-run")["status"] == "needs_answers"
    assert two.journal_head() == before
    assert two.profile(two.ai_id).master_selection is None and two.profile(two.swe_id).master_selection is None

    # An answer that names no question, or is not a / b / both, is refused: nothing is guessed.
    assert _master(two.home, "init", "--answer", "mq-000000000000=a", ok=False)["error"]["code"] == "migration_answer_unknown"
    assert _master(two.home, "init", "--answer", f"{question['question_id']}=newer", ok=False)["error"]["code"] == "migration_answer_invalid"
    assert _master(two.home, "init", "--answer", "a", ok=False)["error"]["code"] == "migration_answer_invalid"
    assert two.journal_head() == before

    # The plain text asks the same question and gives the command to answer it.
    text = CliRunner().invoke(cli, ["scout", "resume", "master", "init", "--home", str(two.home)]).output
    assert "nothing was written" in text and f"--answer {question['question_id']}=a" in text and NEW_NUMBERS in text and OLD_NUMBERS in text


def test_the_migration_merges_the_profiles_resumes_into_one_master(two: _Home) -> None:
    ai_lines, swe_lines = _lines(AI_RESUME.read_text(encoding="utf-8")), _lines(SWE_RESUME.read_text(encoding="utf-8"))
    done = two.migrate("a")
    assert (done["status"], done["written"], done["master"]["revision"]) == ("created", True, 1)
    merge = done["migration"]
    assert (merge["resumes"], len(merge["near_duplicates"]), len(merge["questions"]), merge["unanswered"]) == (2, 2, 1, [])
    master = two.master().master
    texts = [item.text for item in master.items.values()]
    assert len(texts) == len(set(texts)), "a line is in the master twice"

    # The union: every line of the newer resume is in the master word for word (its wording wins).
    assert [line for line in ai_lines if line not in texts] == []
    # Every line of the older resume is there too, except the two it words differently and the one with older numbers.
    folded = [line for line in swe_lines if line not in texts]
    assert sorted(folded) == sorted(line for line in swe_lines if OLD_NUMBERS in line or any(line.startswith(old) for _new, old in REWORDED))
    assert {(near["kept"].startswith(new), near["folded"].startswith(old)) for near in merge["near_duplicates"] for new, old in REWORDED if near["kept"].startswith(new)} == {(True, True)}
    assert all(near["profiles"] == ["Staff Software Engineer"] and near["kept_id"] in master.items for near in merge["near_duplicates"])
    # The conflict as answered: a keeps the newer numbers, and only those.
    assert sum(NEW_NUMBERS in text for text in texts) == 1 and not any(OLD_NUMBERS in text for text in texts)
    # Skills lines with one label are joined: the union of both resumes' skills, each once.
    wanted = _skills(AI_RESUME.read_text(encoding="utf-8")) | _skills(SWE_RESUME.read_text(encoding="utf-8"))
    assert len(wanted) > 50 and wanted == set(master.skills())
    labels = [item.text.split(": ", 1)[0] for item in master.items.values() if item.kind == "skills"]
    assert len(labels) == len(set(labels)) == 7
    # An entry both resumes have is one entry; the newer project is there although the older resume lacks it.
    headings = [entry.heading for entry in master.entries.values()]
    assert len(headings) == len(set(headings)) and "Taskloom: a personal multi-agent workbench" in headings
    # The stored master is a master: every line has its id, and it holds no contact data by construction.
    assert parse_master(master.markdown()).items.keys() == master.items.keys()

    # Running it again changes nothing: every profile has its selection.
    again = _master(two.home, "init")
    assert (again["status"], again["written"], again["profiles"]) == ("unchanged", False, [])
    assert _master(two.home, "history")["revision"] == 1


@pytest.mark.parametrize("answer", ["b", "both"])
def test_the_answer_decides_which_wording_the_master_keeps(two: _Home, answer: str) -> None:
    two.migrate(answer)
    master = two.master().master
    newer = [item.id for item in master.items.values() if NEW_NUMBERS in item.text]
    older = [item.id for item in master.items.values() if OLD_NUMBERS in item.text]
    ai, swe = two.profile(two.ai_id).master_selection, two.profile(two.swe_id).master_selection
    assert ai is not None and swe is not None
    if answer == "b":
        # One line, with the older resume's numbers; both profiles' selections show it.
        assert (len(newer), len(older)) == (0, 1) and older[0] in ai.item_ids and older[0] in swe.item_ids
    else:
        # Two lines; each profile's selection shows the one its own resume has.
        assert (len(newer), len(older)) == (1, 1)
        assert newer[0] in ai.item_ids and older[0] not in ai.item_ids and older[0] in swe.item_ids and newer[0] not in swe.item_ids


def test_each_profiles_first_selection_is_its_own_resume_and_the_resume_is_not_rewritten(two: _Home) -> None:
    before = {profile_id: two.profile(profile_id) for profile_id in (two.ai_id, two.swe_id)}
    texts = {profile_id: two.resume_text(profile_id) for profile_id in before}
    done = two.migrate("a")
    master = two.master()
    for profile_id, old in before.items():
        new = two.profile(profile_id)
        selection = new.master_selection
        assert selection is not None
        # The profile keeps its resume, its revision and its content digest: one more write, nothing a reader sees.
        assert (new.resume_ref, new.revision, new.content_digest, new.titles, new.queries) == (old.resume_ref, old.revision, old.content_digest, old.titles, old.queries)
        assert new.seq == old.seq + 1 and two.resume_text(profile_id) == texts[profile_id]
        assert (selection.source, selection.selector_version, selection.pins, selection.excludes) == ("migration", "sel-1", (), ())
        assert selection.master_revision_id == selection.synced_revision_id == master.revision.revision_id
        assert selection.resume_revision_id == old.resume_ref.revision_id
        # The selection is the old resume in the master's ids: every one of its lines, in its own order.
        shown = [master.master.items[item_id].text for item_id in selection.item_ids if item_id in master.master.items]
        own = _lines(texts[profile_id])
        assert len(shown) == len(own)
        for line, (text, original) in enumerate(zip(shown, own)):
            # Word for word, but for the two lines folded into the newer wording and the one answered with the newer numbers.
            reworded = any(original.startswith(old_) and text.startswith(new_) for new_, old_ in REWORDED)
            assert text == original or reworded or (OLD_NUMBERS in original and NEW_NUMBERS in text), (profile_id, line)
        entries = [master.master.entries[item_id].heading for item_id in selection.item_ids if item_id in master.master.entries]
        assert entries == [line[4:].strip() for line in texts[profile_id].splitlines() if line.startswith("### ")]
        assert set(selection.item_ids) <= master.master.items.keys() | master.master.entries.keys()
        assert set(selection.skills) == _skills(texts[profile_id]) and len(selection.skills) == len(set(selection.skills))
    # The command says so, with the pins it left alone.
    by_id = {item["profile_id"]: item for item in done["profiles"]}
    assert {profile_id: by_id[profile_id]["resume_ref"] for profile_id in before} == {profile_id: old.resume_ref.to_json() for profile_id, old in before.items()}
    status = _master(two.home, "selection", "status")["profiles"]
    assert [(item["has_selection"], item["attached"], item["source"], item["stale"], item["offer"], item["made_from_revision"]) for item in status] == [(True, True, "migration", False, None, 1)] * 2
    # The older profile's selection does not show the newer project; the master has it, for a refresh to pick.
    task = next(entry for entry in master.master.entries.values() if entry.heading.startswith("Taskloom"))
    ai, swe = two.profile(two.ai_id).master_selection, two.profile(two.swe_id).master_selection
    assert ai is not None and swe is not None and task.id in ai.item_ids and task.id not in swe.item_ids


def test_profiles_are_migrated_onto_a_master_already_stored(two: _Home) -> None:
    """A master stored from a file first: the profiles' resumes are merged INTO it, and its ids stay."""

    stored = _master(two.home, "init", "--from", str(FIXTURES / "master.md"))
    assert stored["status"] == "created" and stored["profiles"] == {"synced": [], "offers": []}
    base = two.master().master
    asked = _master(two.home, "init")
    assert (asked["status"], asked["written"], asked["master"]["revision"]) == ("needs_answers", False, 1)
    (question,) = asked["questions"]
    # a is the master's own line (no profile's resume has it word for word but the newer one's); b the older resume's.
    assert NEW_NUMBERS in question["options"][0]["text"] and question["options"][1]["profiles"] == ["Staff Software Engineer"]
    # Keeping both adds a line, so the master changes: the revision that was read must be named, as for every write.
    refused = _master(two.home, "init", "--answer", f"{question['question_id']}=both", ok=False)["error"]
    assert refused["code"] == "master_exists" and refused["current"]["revision"] == 1
    assert two.profile(two.ai_id).master_selection is None  # nothing was written for the profiles either
    done = _master(two.home, "init", "--answer", f"{question['question_id']}=both", "--revision", "1")
    assert (done["status"], done["master"]["revision"], done["migration"]["ids_assigned"]) == ("revised", 2, 1)
    master = two.master().master
    assert set(base.items) < set(master.items) and set(base.entries) == set(master.entries)
    assert all(master.items[item_id].text == item.text for item_id, item in base.items.items())  # no master line was reworded
    (added,) = set(master.items) - set(base.items)
    assert OLD_NUMBERS in master.items[added].text and NEW_NUMBERS in master.items["b-lum-01"].text
    ai, swe = two.profile(two.ai_id).master_selection, two.profile(two.swe_id).master_selection
    assert ai is not None and swe is not None
    # The selections are in the master's own ids: the newer resume shows b-lum-01, the older one its own version.
    assert ai.item_ids[:3] == ("sum-ai", "r-lum", "b-lum-01") and "b-lum-01" not in swe.item_ids and added in swe.item_ids
    assert {near["kept_id"] for near in done["migration"]["near_duplicates"]} == {"b-hex-01", "b-hex-08"} <= set(swe.item_ids)
    assert ai.master_revision_id == swe.master_revision_id == two.master().revision.revision_id
    # Every line the profiles show that the master already had was folded, not added again.
    assert done["migration"]["exact_duplicates"] == done["migration"]["lines_in"] - 3


# --- nothing goes stale ----------------------------------------------------------------------


def _seed_board(home: Path, target: Path, token: str, jobs: list[dict[str, str]]) -> None:
    """A synthetic Greenhouse board straight into the board cache and the company index: no request."""

    from gigai.scout.find_jobs.ats_board_clients import BoardCache
    from gigai.scout.find_jobs.company_index import CompanyIndex, board_list_url, index_stamp, refresh_company
    from gigai.scout.find_jobs.watchlist import add_company_from_url

    seen = datetime.now(UTC) - timedelta(days=1)
    stamp = seen.strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {"jobs": [
        {
            "id": 7000000 + number, "title": job["title"], "absolute_url": f"https://boards.greenhouse.io/{token}/jobs/{7000000 + number}",
            "location": {"name": "United States - Remote"}, "updated_at": stamp, "first_published": stamp,
            "company_name": token.capitalize(), "content": job["content"],
        }
        for number, job in enumerate(jobs, 1)
    ]}
    add_company_from_url(f"https://boards.greenhouse.io/{token}", home, target)
    cache = BoardCache(home / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)
    cache.store("greenhouse", board_list_url("greenhouse", token), body=json.dumps(body).encode("utf-8"), etag=None, last_modified=None, marker=None)
    refresh_company(CompanyIndex.for_home(home), cache, ats="greenhouse", slug=token, observed_at=index_stamp(seen))


def _no_requests(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The fixture model for an assessment; every HTTP request is written down (and answered 404)."""

    import httpx

    from gigai.scout.find_jobs import bindings
    from tests.api_e2e.harness import TEST_HTTP_ENV, TEST_MODEL_ENV

    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(f"{request.url.host}{request.url.path}")
        return httpx.Response(404, json={"error": "not found"}, request=request)

    monkeypatch.setenv(TEST_MODEL_ENV, "1")
    monkeypatch.setenv(TEST_HTTP_ENV, "1")
    monkeypatch.setattr(bindings, "_test_provider_handler", handler)
    return asked


def test_the_migration_leaves_nothing_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A home with an assessed posting and a built read model: after the migration nothing is rebuilt, assessed again or re-opened.

    The control at the end shows the same checks do move when a profile's resume really changes (a refresh).

    One thing does change with a master, by design (P4): a TAILORING of a job now reads the master, so the
    basis the pipeline keys a tailoring by names the master's revision and the selector's version. Without a
    master it is exactly the basis it was."""

    from gigai.scout import postings, tailor_master
    from gigai.scout.assessment_basis import BasisCheck
    from gigai.scout.pipeline import steps, triggers
    from gigai.scout.pipeline.settings import pipeline_setting
    from gigai.scout.quick_assess import read_quick_assessment
    from tests.api_e2e.harness import setup_and_init, write_offline_find_jobs_config

    home, target = setup_and_init(tmp_path)
    runner = CliRunner()
    base = ["--home", str(home), "--target", str(target), "--json"]
    assert runner.invoke(cli, ["scout", "install", *base]).exit_code == 0
    older = import_resume_file(home_root=home, requested_target=target, source=SWE_RESUME)
    assert runner.invoke(cli, ["scout", "resume", "add", str(AI_RESUME), *base]).exit_code == 0
    write_offline_find_jobs_config(target)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
    default = profile_records.selected_profile(resolved, home_root=home, target=target)
    assert default is not None
    second = profile_records.create_profile(
        resolved, label="Second", titles=("software engineer",), titles_to_avoid=(), queries=("software engineer",),
        resume_ref=PinnedResume(older.record_id, older.revision_id, older.content_sha256),
    )
    _seed_board(home, target, "acmelabs", [{"title": "Software Engineer", "content": "<p>Python services on Kubernetes.</p><ul><li>Python in production.</li><li>Kubernetes and Helm.</li></ul>"}])
    asked = _no_requests(monkeypatch)
    batch = runner.invoke(cli, ["scout", "new", "--yes", *base])
    assert batch.exit_code == 0, batch.output
    assert json.loads(batch.output.strip().splitlines()[-1])["assessed"]["assessed"] >= 1, batch.output

    def state() -> dict[str, object]:
        store = postings.open_store(home, target)
        rows = [(row.job, row.profile_id, row.state, row.stale_code, row.pinned_digest, row.settings_digest, row.rank_score, row.assessed_at) for row in store.postings(live=False)]
        profiles = [(item.profile_id, item.revision, item.content_digest, item.resume_ref) for item in profile_records.list_profiles(resolved)]
        job = "https://boards.greenhouse.io/acmelabs/jobs/7000001"
        assessed = {item[0]: read_quick_assessment(home, target, item[0], job) for item in profiles}
        assessed = {profile_id: item for profile_id, item in assessed.items() if item is not None}
        assert default.profile_id in assessed
        ctx = steps.StepContext(home, target, setting=pipeline_setting(home, target))
        return {
            "rows": rows, "profiles": profiles,
            "assessments": {profile_id: canonical_json_bytes(item.to_json()) for profile_id, item in assessed.items()},
            # Why a stored assessment would be offered again (None: it is current), and the basis a tailoring of the job is keyed by.
            "stale": {profile_id: BasisCheck(home_root=home, target=target).reason(item) for profile_id, item in assessed.items()},
            "tailor_basis": {profile_id: steps.tailor_digest(ctx, profile_id, job, "fixture") for profile_id in assessed},
        }

    def but_tailoring(value: dict[str, object]) -> dict[str, object]:
        return {key: item for key, item in value.items() if key != "tailor_basis"}

    assert set(postings.refresh(home, target).builds.values()) <= {"unchanged", "facts", "matched"}
    before = state()
    assert before["rows"] and set(before["stale"].values()) == {None}  # type: ignore[union-attr]
    # No master yet: a tailoring's basis holds nothing of one.
    assert [tailor_master.digest_parts(home, target, profile) for profile in profile_records.list_profiles(resolved)] == [(), ()]

    migrated = runner.invoke(cli, ["scout", "resume", "master", "init", *base])
    assert migrated.exit_code == 0, migrated.output
    done = json.loads(migrated.output.strip().splitlines()[-1])
    if done["status"] == "needs_answers":
        answers = [part for question in done["questions"] for part in ("--answer", f"{question['question_id']}=a")]
        done = json.loads(runner.invoke(cli, ["scout", "resume", "master", "init", *answers, *base]).output.strip().splitlines()[-1])
    assert done["status"] == "created" and len(done["profiles"]) == 2

    # 1. The read model: no profile is matched again, and every row is what it was. P7: an assessment now reads the
    #    master, whose revision is part of what a stale check reads, so each profile's facts are read again ONCE.
    assert set(postings.refresh(home, target).builds.values()) == {"facts"}
    assert set(postings.refresh(home, target).builds.values()) == {"unchanged"}
    migrated_state = state()
    assert but_tailoring(migrated_state) == but_tailoring(before)
    # P4: a tailoring now reads the master, so its basis names the master's revision, the selector and the candidate rule.
    stored = load_master(home_root=home, target=target)
    assert stored is not None
    for profile in profile_records.list_profiles(resolved):
        assert tailor_master.digest_parts(home, target, profile) == (
            "master", stored.revision.revision_id, tailor_master.SELECTOR_VERSION, tailor_master.CANDIDATES_VERSION, tailor_master.CANDIDATES,
        )
    assert all(migrated_state["tailor_basis"][profile_id] != basis for profile_id, basis in before["tailor_basis"].items())  # type: ignore[index, union-attr]
    # 2. The batch finds nothing to assess: the stored assessment is still current.
    again = runner.invoke(cli, ["scout", "new", "--yes", *base])
    assert again.exit_code == 0, again.output
    found = json.loads(again.output.strip().splitlines()[-1])
    assert (found["counts"]["to_assess"], found["counts"]["only_stale"], found["assessed"], found["reassessed"]) == (0, 0, None, None), again.output
    assert state() == migrated_state
    # 3. The pipeline re-opens no step of either profile.
    result = triggers.profile_changed(home, target)
    assert (result.enqueued, result.awaiting) == ((), ())
    assert asked == [], "the migration or a read after it made a request"

    # The control: a refresh really changes one profile's resume. The read model then rebuilds that profile, and a
    # tailoring of the job has another basis for it; the other profile's is what it was.
    refreshed = runner.invoke(cli, ["scout", "resume", "master", "selection", "refresh", "--profile", default.profile_id, *base])
    assert refreshed.exit_code == 0, refreshed.output
    builds = postings.refresh(home, target).builds
    assert builds[default.profile_id] != "unchanged" and builds[second.profile_id] == "unchanged"
    after = state()
    assert after["profiles"] != before["profiles"]
    assert after["tailor_basis"][default.profile_id] != migrated_state["tailor_basis"][default.profile_id]  # type: ignore[index]
    assert {key: value for key, value in after["tailor_basis"].items() if key != default.profile_id} == {  # type: ignore[union-attr]
        key: value for key, value in migrated_state["tailor_basis"].items() if key != default.profile_id  # type: ignore[union-attr]
    }

    # P4: a write of the master, and a new selector version, each give every tailoring another basis.
    edited = tmp_path / "master-2.md"
    edited.write_text(stored.master.markdown().replace("## Other", "## Other\n\n- Speaker at a regional infrastructure meetup in 2024.", 1), encoding="utf-8")
    written = runner.invoke(cli, ["scout", "resume", "master", "init", "--from", str(edited), "--revision", str(stored.revision.revision), *base])
    assert written.exit_code == 0 and json.loads(written.output.strip().splitlines()[-1])["status"] == "revised", written.output
    rewritten = state()
    assert all(rewritten["tailor_basis"][profile_id] != basis for profile_id, basis in after["tailor_basis"].items())  # type: ignore[index, union-attr]
    monkeypatch.setattr(tailor_master, "SELECTOR_VERSION", "sel-next")
    assert all(state()["tailor_basis"][profile_id] != basis for profile_id, basis in rewritten["tailor_basis"].items())  # type: ignore[index, union-attr]


# --- sticky: edits follow, new lines are offered ------------------------------------------------


def test_an_edited_or_retired_shown_line_reaches_the_profiles_resume_and_new_lines_are_only_offered(two: _Home) -> None:
    two.migrate("a")
    master = two.master().master
    ai, swe = two.profile(two.ai_id), two.profile(two.swe_id)
    # A line only the AI profile shows, and one only the other profile shows.
    only_ai = next(item for item in master.items.values() if item.id in ai.master_selection.item_ids and item.id not in swe.master_selection.item_ids and item.kind == "bullet")
    only_swe = [item for item in master.items.values() if item.id in swe.master_selection.item_ids and item.id not in ai.master_selection.item_ids and item.kind == "bullet"]

    # 1. Three new lines in the master: offered to both, and neither profile's resume moves.
    def add_three(markdown: str) -> str:
        role = f"<!-- id:{master.items[only_ai.id].entry_id} -->"
        head, tail = markdown.split(role, 1)
        role_line, rest = tail.split("\n", 2)[1], tail.split("\n", 2)[2]
        return f"{head}{role}\n{role_line}\n- New line one about agents.\n- New line two about retrieval at 3x scale.\n- New line three.\n{rest}"

    written = two.write_master(add_three)
    assert written["status"] == "revised" and written["changes"]["added"] == 3
    assert written["profiles"]["synced"] == []
    assert [(offer["label"], len(offer["new_lines"]), offer["offer"]) for offer in written["profiles"]["offers"]] == [
        ("default", 3, "3 new master lines: refresh?"), ("Staff Software Engineer", 3, "3 new master lines: refresh?"),
    ]
    assert (two.profile(two.ai_id).resume_ref, two.profile(two.swe_id).resume_ref) == (ai.resume_ref, swe.resume_ref)
    assert two.profile(two.ai_id).seq == ai.seq  # not even a write
    status = two.status(two.ai_id)
    assert (status["offer"], status["stale"], status["made_from_revision"], status["master_revision"]) == ("3 new master lines: refresh?", False, 1, 2)
    text = CliRunner().invoke(cli, ["scout", "resume", "master", "selection", "status", "--home", str(two.home)]).output
    assert text.count("3 new master lines: refresh?") == 2

    # 2. A line the AI profile shows is edited, and one the other profile shows is retired.
    edited = only_ai.text.replace(only_ai.text.split()[0], "Rebuilt", 1)
    retired = only_swe[0]
    written = two.write_master(lambda markdown: "".join(
        line for line in markdown.replace(only_ai.text, edited).splitlines(keepends=True) if f"id:{retired.id} " not in line
    ))
    assert written["changes"] == {"added": 0, "changed": 1, "removed": 1}
    synced = {item["profile_id"]: item for item in written["profiles"]["synced"]}
    assert synced[two.ai_id]["changed"] == [only_ai.id] and synced[two.ai_id]["retired"] == []
    assert synced[two.swe_id]["retired"] == [retired.id] and synced[two.swe_id]["changed"] == []
    for profile_id, old in ((two.ai_id, ai), (two.swe_id, swe)):
        new = two.profile(profile_id)
        # The profile's resume moved to the view: its own record, a real resume every reader reads.
        assert new.resume_ref != old.resume_ref and new.resume_ref.record_id == mp.view_record_id(two.resolved, profile_id)
        assert new.revision == old.revision + 1 and new.master_selection.resume_revision_id == new.resume_ref.revision_id
        assert new.master_selection.synced_revision_id == two.master().revision.revision_id
        assert new.master_selection.master_revision_id == old.master_selection.master_revision_id  # the offer is still measured from here
    ai_view, swe_view = two.resume_text(two.ai_id), two.resume_text(two.swe_id)
    assert edited in ai_view and only_ai.text not in ai_view
    assert retired.text not in swe_view and retired.id not in two.profile(two.swe_id).master_selection.item_ids
    # Sticky: nothing new came in (the three new lines are still only offered), and nothing else went.
    assert not any(line.startswith("New line") for line in _lines(ai_view))
    assert set(two.profile(two.ai_id).master_selection.item_ids) == set(ai.master_selection.item_ids)
    assert set(two.profile(two.swe_id).master_selection.item_ids) == set(swe.master_selection.item_ids) - {retired.id}
    assert two.status(two.ai_id)["offer"] == "3 new master lines: refresh?" and two.status(two.ai_id)["stale"] is False
    # The view is a resume in GigAI's format, with no id comment and no contact data.
    parse_resume_markdown(ai_view)
    assert "<!--" not in ai_view

    # 3. Nothing changed for a shown line: another write of the master touches no profile.
    seqs = (two.profile(two.ai_id).seq, two.profile(two.swe_id).seq)
    written = two.write_master(lambda markdown: markdown.replace("- New line three.", "- New line three, reworded."))
    assert written["profiles"]["synced"] == [] and (two.profile(two.ai_id).seq, two.profile(two.swe_id).seq) == seqs


def test_a_resume_replaced_by_hand_is_left_alone(two: _Home, tmp_path: Path) -> None:
    two.migrate("a")
    own = tmp_path / "own.md"
    own.write_text("## Summary\n\n- A resume the user wrote by hand after the migration.\n", encoding="utf-8")
    added = CliRunner().invoke(cli, ["scout", "resume", "add", str(own), "--profile", two.swe_id, "--home", str(two.home), "--json"])
    assert added.exit_code == 0, added.output
    pinned = two.profile(two.swe_id).resume_ref
    status = two.status(two.swe_id)
    assert (status["has_selection"], status["attached"], status["offer"], status["stale"]) == (True, False, None, False)
    # A master edit of a line that selection lists does not put the selection back over the hand-written resume.
    shown = next(item for item in two.master().master.items.values() if item.id in two.profile(two.swe_id).master_selection.item_ids and item.kind == "bullet")
    written = two.write_master(lambda markdown: markdown.replace(shown.text, shown.text + " Extended."))
    assert two.swe_id not in {item["profile_id"] for item in written["profiles"]["synced"]}
    assert two.profile(two.swe_id).resume_ref == pinned and "by hand" in two.resume_text(two.swe_id)
    # Only an explicit refresh selects from the master again.
    refreshed = _master(two.home, "selection", "refresh", "--profile", two.swe_id)["profiles"][0]
    assert refreshed["written"] is True and two.status(two.swe_id)["attached"] is True


# --- refresh, and what the readers read --------------------------------------------------------


def test_a_refresh_makes_the_two_page_view_the_profiles_resume(two: _Home) -> None:
    two.migrate("a")
    before = two.profile(two.swe_id)
    assert measure_markdown(two.resume_text(two.swe_id), spacing_scale=0.9)[0] > 2  # the older resume is over 2 pages
    preview = _master(two.home, "selection", "refresh", "--profile", two.swe_id, "--dry-run")
    assert preview["dry_run"] is True and preview["profiles"][0]["written"] is False and two.profile(two.swe_id) == before

    done = _master(two.home, "selection", "refresh", "--profile", two.swe_id)["profiles"][0]
    assert (done["action"], done["source"], done["written"], done["fits"], done["pages"]) == ("refreshed", "refresh", True, True, 2)
    assert {key: done[key] for key in ("added", "removed", "shown", "skills")} == {key: preview["profiles"][0][key] for key in ("added", "removed", "shown", "skills")}
    assert done["removed"], "a resume over 2 pages was fitted to 2 and left nothing out"
    after = two.profile(two.swe_id)
    selection = after.master_selection
    assert after.resume_ref.to_json() == done["resume_ref"] and after.resume_ref.record_id == mp.view_record_id(two.resolved, two.swe_id)
    assert after.revision == before.revision + 1 and selection.source == "refresh" and selection.resume_revision_id == after.resume_ref.revision_id
    assert set(selection.item_ids) - set(before.master_selection.item_ids) == set(done["added"])
    # What a reader of the profile's resume reads is the selection's view: master lines only, 2 pages.
    master = two.master().master
    view = two.resume_text(two.swe_id)
    assert view == render_selection(master, selection.item_ids, selection.skills)
    assert measure_markdown(view, spacing_scale=0.9)[0] == 2
    known = {item.text for item in master.items.values()}
    assert all(line in known or set(line.split(", ")) <= set(master.skills()) for line in _lines(view))
    # The other profile is not touched, and the same refresh again writes nothing new.
    assert two.profile(two.ai_id).master_selection.source == "migration"
    again = _master(two.home, "selection", "refresh", "--profile", two.swe_id)["profiles"][0]
    assert again["resume_ref"] == done["resume_ref"] and (again["added"], again["removed"]) == ([], [])
    # The Resume page's label for the profile's resume names what it is.
    from gigai import private_records

    labels = {item["reference_id"]: item["label"] for item in private_records.list_imports(home_root=two.home, requested_target=two.scout, family="reference") if item["kind"] == "resume"}
    revision = private_records.list_revisions(resolved=two.resolved, record_id=after.resume_ref.record_id)[-1]
    assert labels[revision["content"]["reference_id"]] == mp.VIEW_FILE_NAME


def test_refresh_refuses_what_it_cannot_do(two: _Home) -> None:
    assert _master(two.home, "selection", "status", ok=False)["error"]["code"] == "master_not_found"
    assert _master(two.home, "selection", "refresh", ok=False)["error"]["code"] == "master_not_found"
    two.migrate("a")
    assert _master(two.home, "selection", "refresh", "--profile", "profile_00000000-0000-4000-8000-000000000000", ok=False)["error"]["code"] == "profile_not_found"
    assert _master(two.home, "selection", "refresh", "--all", "--profile", two.ai_id, ok=False)["error"]["code"] == "profile_input_invalid"
    assert _master(two.home, "selection", "refresh", "--sync", "--dry-run", ok=False)["error"]["code"] == "selection_input_invalid"
    assert _master(two.home, "init", "--from", str(AI_RESUME), "--dry-run", ok=False)["error"]["code"] == "master_option_invalid"
    # --sync with nothing stale writes nothing.
    assert _master(two.home, "selection", "refresh", "--all", "--sync")["profiles"] == []


# --- a new profile's first selection: from the local index, no model ---------------------------

_AI_POSTING = (
    "<p>We build an agent platform.</p><p>Requirements:</p><ul><li>Python in production.</li><li>LLM applications and RAG.</li>"
    "<li>Kubernetes.</li></ul><p>Nice to have:</p><ul><li>Go.</li></ul>"
)


def test_a_new_profiles_first_selection_comes_from_the_postings_its_titles_match_in_the_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.api_e2e.harness import setup_and_init, write_offline_find_jobs_config

    home, target = setup_and_init(tmp_path)
    runner = CliRunner()
    base = ["--home", str(home), "--target", str(target), "--json"]
    assert runner.invoke(cli, ["scout", "install", *base]).exit_code == 0
    assert runner.invoke(cli, ["scout", "resume", "add", str(SWE_RESUME), *base]).exit_code == 0
    write_offline_find_jobs_config(target)
    assert runner.invoke(cli, ["scout", "resume", "master", "init", "--from", str(FIXTURES / "master.md"), *base]).exit_code == 0
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
    default = profile_records.selected_profile(resolved, home_root=home, target=target)
    assert default is not None
    _seed_board(home, target, "agentco", [{"title": "Staff AI Engineer, Agents", "content": _AI_POSTING}] * 3 + [{"title": "Account Executive", "content": "<p>Sell. Salesforce required.</p>"}])
    master = load_master(home_root=home, target=target).master

    # A new profile, created as the app creates it: with the selected profile's resume, and titles of its own.
    created = profile_records.create_profile(
        resolved, label="AI", titles=("staff ai engineer",), titles_to_avoid=(), queries=("staff ai engineer",), resume_ref=default.resume_ref,
    )
    asked = _no_requests(monkeypatch)
    posting, read = mp.index_stand_in(home, target, created, master)
    assert read == 3 and posting is not None and posting.title == "staff ai engineer"  # the three it matches, not the sales job
    assert posting.text.startswith("Requirements:\n") and {"- Python", "- Kubernetes", "- LLM"} <= set(posting.text.split("Nice to have:")[0].splitlines())
    assert "- Go" in posting.text.split("Nice to have:")[1].splitlines() and "Salesforce" not in posting.text

    made = mp.first_selection(home_root=home, target=target, profile_id=created.profile_id)
    assert made is not None and made.resume_ref != default.resume_ref and made.resume_ref.record_id == mp.view_record_id(resolved, created.profile_id)
    selection = made.master_selection
    assert (selection.source, selection.selector_version) == ("index", "sel-1")
    # The selection IS the job selection against the stand-in posting, for this profile's titles.
    expected = select(master, SelectionProfile(titles=created.titles, profile_id=created.profile_id, label="AI"), posting)
    assert selection.item_ids == mp.selection_ids(expected) and selection.skills == expected.skills
    view = resume_for_profile(made, resolved=resolved, home_root=home, target=target).text
    assert view == expected.markdown and expected.fits and expected.summary == ("sum-ai",)
    assert {"Python", "Kubernetes", "LLM"} <= set(selection.skills)
    assert asked == [], "the first selection made a request"
    # The default profile, which was not asked about, still owns its resume.
    untouched = next(item for item in profile_records.list_profiles(resolved) if item.profile_id == default.profile_id)
    assert untouched.resume_ref == default.resume_ref and untouched.master_selection is None

    # With no posting its titles match, the first selection is the standing pick for the titles.
    other = profile_records.create_profile(
        resolved, label="Data", titles=("staff data engineer",), titles_to_avoid=(), queries=("staff data engineer",), resume_ref=default.resume_ref,
    )
    done = runner.invoke(cli, ["scout", "resume", "master", "selection", "refresh", "--profile", other.profile_id, *base])
    assert done.exit_code == 0, done.output
    change = json.loads(done.output.strip().splitlines()[-1])["profiles"][0]
    assert (change["action"], change["source"], change["postings"], change["fits"], change["written"]) == ("first", "titles", 0, True, True)


def test_the_create_route_gives_a_new_profile_its_own_selection_when_a_master_is_stored(two: _Home) -> None:
    """``POST /api/profiles``' own step after the create (the route is driven over HTTP in tests/api_e2e)."""

    from types import SimpleNamespace

    from gigai.scout.find_jobs.api.profiles import ProfilesRoutesMixin

    handler = ProfilesRoutesMixin()
    handler._backend = SimpleNamespace(home_root=two.home, target=two.scout)  # type: ignore[attr-defined]
    default = two.profile(two.ai_id)

    def create(label: str) -> profile_records.ProfileRecord:
        return profile_records.create_profile(
            two.resolved, label=label, titles=("staff platform engineer",), titles_to_avoid=(), queries=("staff platform engineer",), resume_ref=default.resume_ref,
        )

    from gigai.scout.find_jobs.api import master as master_routes

    # No master yet: the profile stays as created, with the selected profile's resume, and nothing is started.
    first = create("Before")
    assert handler._first_master_selection(two.resolved, first) is None
    assert two.profile(first.profile_id).resume_ref == default.resume_ref
    two.migrate("a")
    second = create("After")
    # 0.1.10.9 master P5: the selection is made after the route's answer, on a thread; the profile is listed as pending meanwhile.
    thread = handler._first_master_selection(two.resolved, second)
    assert thread is not None
    thread.join(120)
    assert not thread.is_alive() and master_routes.pending_first_selections(two.home, two.scout) == []
    made = two.profile(second.profile_id)
    assert made.resume_ref != default.resume_ref
    assert made.master_selection is not None and made.master_selection.source == "titles"
    assert made.resume_ref.record_id == mp.view_record_id(two.resolved, second.profile_id)


# --- the profile record -------------------------------------------------------------------------


def test_the_selection_key_is_additive_and_never_bumps_a_profiles_revision(two: _Home) -> None:
    before = two.profile(two.ai_id)
    raw_before = canonical_json_bytes(before.to_json())
    assert b"master_selection" not in raw_before  # a record without the key is written as it always was
    revision_id = "revision_00000000-0000-4000-8000-000000000001"
    selection = profile_records.ProfileMasterSelection(
        item_ids=("r-hex", "b-hex-01"), skills=("Python",), master_revision_id=revision_id, synced_revision_id=revision_id,
        resume_revision_id=before.resume_ref.revision_id, selector_version="sel-1", source="migration", pins=("b-hex-01",), excludes=("b-hex-02",),
    )
    written = profile_records.write_profile(two.resolved, profile_id=two.ai_id, master_selection=selection)
    assert (written.revision, written.content_digest, written.resume_ref, written.seq) == (before.revision, before.content_digest, before.resume_ref, before.seq + 1)
    read = two.profile(two.ai_id)
    assert read.master_selection == selection
    assert validate_serialized_contract("scout-profile.schema.json", canonical_json_bytes(read.to_json())).valid
    # The sealed revision is still found by its digest, and a later edit keeps the selection.
    assert profile_records.retrieve_profile_revision(two.resolved, profile_id=two.ai_id, revision=before.revision, content_digest=before.content_digest).master_selection == selection
    renamed = profile_records.write_profile(two.resolved, profile_id=two.ai_id, label="renamed")
    assert renamed.master_selection == selection and renamed.revision == before.revision
    # The strict schema refuses a selection it does not know.
    for broken in ({"source": "guess"}, {"item_ids": ["not an id"]}, {"master_revision_id": "rev-1"}, {"extra": 1}):
        value = {**read.to_json(), "master_selection": {**selection.to_json(), **broken}}
        assert not validate_serialized_contract("scout-profile.schema.json", canonical_json_bytes(value)).valid, broken
    with pytest.raises(profile_records.ProfileRecordError):
        profile_records.ProfileMasterSelection.from_json({**selection.to_json(), "source": "guess"})
    with pytest.raises(profile_records.ProfileRecordError):
        profile_records.ProfileMasterSelection.from_json({key: value for key, value in selection.to_json().items() if key != "pins"})


# --- the merge, pure ------------------------------------------------------------------------------


def _sources() -> list[mm.SourceResume]:
    return [
        mm.SourceResume("ai", AI_RESUME.read_text(encoding="utf-8"), ("AI",)),
        mm.SourceResume("swe", SWE_RESUME.read_text(encoding="utf-8"), ("SWE",)),
    ]


def test_the_merge_folds_duplicates_and_asks_about_different_numbers() -> None:
    plan = mm.plan_migration(_sources())
    ai_lines, swe_lines = _lines(_sources()[0].text), _lines(_sources()[1].text)
    assert plan.lines_in == len(ai_lines) + len(swe_lines)
    assert (plan.exact_duplicates, len(plan.near_duplicates), len(plan.questions), len(plan.unanswered)) == (4, 2, 1, 1)
    low, high = sorted(near.similarity for near in plan.near_duplicates)
    assert mm.NEAR_DUPLICATE <= low < 0.8 < high < 1.0
    # lines out = lines in, less what was folded; the conflict is planned as a until it is answered.
    bullets_out = sum(item.kind != "skills" for item in plan.master.items.values())
    assert bullets_out == plan.lines_in - plan.exact_duplicates - len(plan.near_duplicates) - len(plan.questions)
    assert plan.ids_assigned == len(plan.master.items) + len(plan.master.entries)
    # The same input gives the same plan and the same ids.
    again = mm.plan_migration(_sources())
    assert again.master.markdown() == plan.master.markdown() and again.questions == plan.questions
    # Each source's selection renders its own lines back (the older one with the newer wording where folded).
    for source in _sources():
        selection = plan.selection(source.key)
        shown = [plan.master.items[item_id].text for item_id in selection.item_ids if item_id in plan.master.items]
        assert len(shown) == len(set(shown))
        if source.key == "ai":
            assert shown == ai_lines
    both = mm.plan_migration(_sources(), answers={plan.questions[0].question_id: "both"})
    assert len(both.master.items) == len(plan.master.items) + 1 and not both.unanswered
    with pytest.raises(MasterResumeError) as unknown:
        mm.plan_migration(_sources(), answers={"mq-nope": "a"})
    assert unknown.value.code == "migration_answer_unknown"


def test_a_line_is_a_near_duplicate_only_when_it_is_the_same_line_worded_twice() -> None:
    assert mm.near_duplicate(*("Cut p99 API latency from 840 ms to 190 ms by redesigning the PostgreSQL schema.", "Reduced p99 API latency from 840 ms to 190 ms through a PostgreSQL schema redesign.")) >= mm.NEAR_DUPLICATE
    assert mm.near_duplicate("Led a 9-engineer AI platform group and owned its roadmap.", "Ran the model-serving fleet on Kubernetes with GPU autoscaling.") < mm.NEAR_DUPLICATE
    # Two different lines of one resume are never folded into each other, however close.
    text = "## Experience\n\n### Acme\nEngineer | 2020 - 2022\n- Built the billing service in Go for 3 teams.\n- Built the billing service in Go for 4 teams.\n"
    plan = mm.plan_migration([mm.SourceResume("one", text)])
    assert len(plan.master.items) == 2 and not plan.questions and not plan.near_duplicates


def test_entries_are_one_entry_by_heading_and_role_line() -> None:
    newer = "## Experience\n\n### Acme\nStaff Engineer | Jan 2021 - Present\n- Runs the platform group.\n\n### Acme\nEngineer | 2015 - 2020\n- Built the billing service.\n"
    older = "## Experience\n\n### Acme\nSoftware Engineer | 2015 - 2020\n- Built the billing service.\n- Wrote the ledger.\n\n### Borealis\n- An entry with no role line.\n"
    plan = mm.plan_migration([mm.SourceResume("new", newer), mm.SourceResume("old", older)])
    entries = list(plan.master.entries.values())
    # The promotion is its own entry; the same years are the same entry (the newer role line is kept).
    assert [(entry.heading, entry.sublines, len(entry.bullets)) for entry in entries] == [
        ("Acme", ("Staff Engineer | Jan 2021 - Present",), 1), ("Acme", ("Engineer | 2015 - 2020",), 2), ("Borealis", (), 1),
    ]
    assert plan.exact_duplicates == 1 and plan.selection("old").item_ids[0] == entries[1].id


def test_the_merge_goes_on_top_of_a_master_already_stored_and_keeps_its_ids() -> None:
    base = parse_master((FIXTURES / "master.md").read_text(encoding="utf-8"))
    extra = "## Experience\n\n### Lumenfold\nStaff AI Engineer | Feb 2023 - Present\n- A line the master does not have yet.\n" + f"- {base.items['b-lum-02'].text}\n"
    plan = mm.plan_migration([mm.SourceResume("resume", extra)], base=base)
    assert set(base.items) < set(plan.master.items) and set(base.entries) == set(plan.master.entries)
    assert {item_id: plan.master.items[item_id].text for item_id in base.items} == {item_id: item.text for item_id, item in base.items.items()}
    (new_id,) = set(plan.master.items) - set(base.items)
    assert plan.ids_assigned == 1 and plan.master.items[new_id].entry_id == "r-lum"
    assert plan.selection("resume").item_ids == ("r-lum", new_id, "b-lum-02") and plan.exact_duplicates == 1


_FOREIGN = """Jordan Example Resume

**SUMMARY**

Platform engineer with 9 years of experience
in distributed systems.

WORK EXPERIENCE

**Staff Engineer, Acme Cloud** (2021 - Present)
* Runs the control plane for 4,000 clusters.
* Cut deploy time from 40 minutes
  to 6 minutes.

**Engineer, Borealis** (2016 - 2021)
1. Built the billing service in Go.
2. Wrote the ledger.

Technical Skills:
- Languages: Go, Python
- Cloud: Kubernetes, AWS

EDUCATION

**BS Computer Science, Example State** (2016)

Certifications
- Certified Kubernetes Administrator (2022)

AWARDS
- Hackathon winner (2019)
"""


def test_a_resume_in_another_shape_is_read_where_the_shape_is_plain() -> None:
    draft = mm.read_resume(_FOREIGN)
    assert [section.name for section in draft.sections] == ["summary", "experience", "skills", "education", "other"]
    by_name = {section.name: section for section in draft.sections}
    assert [item.text for item in by_name["summary"].items] == ["Platform engineer with 9 years of experience in distributed systems."]
    # MIGFIX: the dates a heading ends with are the entry's role line, so the entry has its dates (they were part of the heading).
    assert [(entry.heading, entry.sublines, [bullet.text for bullet in entry.bullets]) for entry in by_name["experience"].entries] == [
        ("Staff Engineer, Acme Cloud", ["2021 - Present"], ["Runs the control plane for 4,000 clusters.", "Cut deploy time from 40 minutes to 6 minutes."]),
        ("Engineer, Borealis", ["2016 - 2021"], ["Built the billing service in Go.", "Wrote the ledger."]),
    ]
    assert [item.text for item in by_name["skills"].items] == ["Languages: Go, Python", "Cloud: Kubernetes, AWS"]
    assert [(entry.heading, entry.sublines) for entry in by_name["education"].entries] == [("BS Computer Science, Example State", ["2016"])]
    # Certifications and awards are Other lines (decision 8), both sections in one.
    assert [item.text for item in by_name["other"].items] == ["Certified Kubernetes Administrator (2022)", "Hackathon winner (2019)"]
    # A master made of it is a master, and the resume's own GigAI-format twin gives the same lines.
    plan = mm.plan_migration([mm.SourceResume("foreign", _FOREIGN)])
    assert len(plan.master.items) == 9 and parse_master(plan.master.markdown()).items.keys() == plan.master.items.keys()


def test_a_resume_that_cannot_be_read_is_refused_by_line_number_never_by_text() -> None:
    secret = "ZZ-private-sentence-ZZ"
    for text, line in (
        (f"## Experience\n\n### Acme\n- Built it.\n\n{secret}\n", "line 6"),
        (f"just one paragraph of prose, {secret}\n", "no resume sections"),
    ):
        with pytest.raises(mm.MigrationResumeError) as refused:
            mm.plan_migration([mm.SourceResume("one", text, ("Only",))])
        assert refused.value.code == "migration_resume_unreadable" and refused.value.key == "one"
        assert line in str(refused.value) and secret not in str(refused.value)


def test_an_unreadable_resume_stops_the_migration_and_names_the_profile(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    runner = CliRunner()
    prose = tmp_path / "prose.md"
    prose.write_text("ZZ-private-sentence-ZZ: a resume with no section at all, only one paragraph.\n", encoding="utf-8")
    assert runner.invoke(cli, ["scout", "resume", "add", str(prose), "--home", str(home), "--json"]).exit_code == 0
    # No profile yet (no titles): the migration says what to do.
    assert _master(home, "init", ok=False)["error"]["code"] == "migration_no_profiles"
    (home_scout_target(home) / "find-jobs.json").write_bytes(canonical_json_bytes(default_find_jobs_config().to_json()))
    refused = _master(home, "init", ok=False)["error"]
    assert refused["code"] == "migration_resume_unreadable" and "profile default" in refused["message"]
    assert "ZZ-private-sentence-ZZ" not in refused["message"] and "gigai scout resume add" in refused["message"]
    assert _master(home, "show", ok=False)["error"]["code"] == "master_not_found"


def test_a_stored_selection_renders_as_the_selector_rendered_it() -> None:
    master = parse_master((FIXTURES / "master.md").read_text(encoding="utf-8"))
    selected = select(master, SelectionProfile(titles=("Staff AI Engineer",)), None, measure=lambda markdown: (1 + markdown.count("\n- ") // 14, 0.5))
    assert render_selection(master, mp.selection_ids(selected), selected.skills) == selected.markdown
    # A retired line and a skill the master no longer lists are left out; an unknown id is ignored.
    ids = mp.selection_ids(selected)
    assert render_selection(master, (*ids, "b-gone"), (*selected.skills, "COBOL")) == selected.markdown
