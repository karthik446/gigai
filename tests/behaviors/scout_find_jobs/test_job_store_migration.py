"""0.1.11.9 PJ1: the per-profile stores are COPIED into the per-job stores, once. Synthetic only.

A home with two profiles (A the default, B a second one) is built by hand. Seven jobs:

- ``only_a``: A alone holds it (assessment, suggestion);
- ``newest``: both assessed it, no resume; B's assessment is newer;
- ``applied``: both have a stored resume and the job has an application; A's resume was stored before it, B's after
  (and B's has a ``.layout``, so one spacing is superseded);
- ``edited``: both have a stored resume, A's holds a line the user wrote; B is newer;
- ``one_resume``: both assessed it, only A has a stored resume; B is newer;
- ``spacing``: both have a stored resume, B's has a saved spacing; A is newer;
- ``only_b``: B alone holds it, with a resume and a proposed-resume sidecar beside its suggestion record.

Plus an assessment in ``ephemeral`` (a pasted resume), which is never migrated.

Covers acceptance (a): the winner per rule, the kept / superseded counts, the profile folders byte-identical before
and after (a hash of the tree), a second apply is a no-op, a dry run writes nothing, the paths inside the copies
resolve; and that an unfiltered list of the stores does not show a migrated job twice.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from click.testing import CliRunner
import pytest

from gigai.application_events import record_application
from gigai.cli import cli
from gigai.scout import job_store_migration as migration
from gigai.scout import jobs_folder, tailored_resume
from gigai.scout.find_jobs.ats_board_clients import BoardCache
from gigai.scout.find_jobs.company_index import CompanyIndex, board_list_url, index_stamp, refresh_company
from gigai.scout.find_jobs.job_key import copies_of
from gigai.scout.find_jobs.job_key import job_key as job_identity_key
from gigai.scout.find_jobs.discovery.storage import project_id
from gigai.scout.job_store_layout import JOB_FOLDER, job_digest
from gigai.scout.quick_assess import _read_stored, job_quick_assess_path, list_quick_assessments, quick_assess_dir, quick_assess_path
from gigai.scout.resume_pdf import job_store_layout_path
from gigai.scout.resumes_folder import job_key, job_store_key
from gigai.scout.suggestions import job_suggestions_path, proposed_resume_path
from gigai.scout.tailored_resume import job_tailored_resume_path, list_tailored_resumes
from gigai.workpad import committed_read_cache

from tests.support.answers_stories_fixtures import RESUME, assess, install_model, pending, two_profiles
from tests.support.job_store_fixtures import role_folders, role_path, to_role_folder
from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

_DAY1 = "2026-10-06T10:00:00.000000Z"
_DAY1_NOON = "2026-10-06T12:00:00.000000Z"
_APPLIED_AT = "2026-10-06T18:00:00Z"
_DAY2 = "2026-10-07T03:00:00.000000Z"
_DAY3 = "2026-10-07T18:00:00.000000Z"

_NAMES = ("only_a", "newest", "applied", "edited", "one_resume", "spacing", "only_b")
JOBS = {name: f"https://jobs.example.invalid/acme/{place}" for place, name in enumerate(_NAMES, start=1)}
PASTED = "https://jobs.example.invalid/acme/pasted"


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode("utf-8"))


class Home:
    """The synthetic home: what each profile holds, written the way the stores name their files."""

    def __init__(self, fx: ProfileFixtureGig) -> None:
        self.fx = fx
        self.home, self.target = fx.home_root, fx.target
        self.a, self.b = two_profiles(fx)
        self.project = project_id(self.home, self.target)
        self.project_dir = self.home / "scout" / self.project
        self.folders: dict[str, dict[str, object]] = {}

    def assessed(self, profile: str | None, job: str, at: str) -> Path:
        path = role_path(self.home, self.target, "quick_assess", profile, job)
        _write(path, {
            "job": {"job_identity": job, "title": "Staff Engineer"}, "created_at": _DAY1, "updated_at": at, "stored_path": str(path),
            "resume": {"profile_id": profile}, "result": {"verdict": "matched_above_threshold", "matrix": []},
        })
        if profile is not None:
            record = role_path(self.home, self.target, "suggestions", profile, job)
            resume = role_path(self.home, self.target, "resumes", profile, job)
            _write(record, {
                "job_identity": job, "profile_id": profile, "created_at": at, "updated_at": at, "stored_path": str(record),
                "basis": {"assessment": {"stored_path": str(path), "assessed_at": at}},
                "selection": {"resume": {"stored_path": str(resume), "origin": "pick"}}, "proposed": None,
            })
        return path

    def resume(self, profile: str, job: str, at: str, *, user_line: bool = False, spacing: int | None = None) -> Path:
        path = role_path(self.home, self.target, "resumes", profile, job)
        _write(path, {
            "job": {"job_identity": job, "title": "Staff Engineer"}, "created_at": _DAY1, "updated_at": at,
            "stored_path": str(path), "markdown_path": str(path.with_suffix(".md")),
            "resume": {"profile_id": profile},
            "sources": {"assessment_stored_path": str(role_path(self.home, self.target, "quick_assess", profile, job))},
            "result": {"sections": [{"lines": [{"text": "Built the platform.", "origin": "user" if user_line else "resume"}]}]},
            "selection": {"pins": [], "excludes": [], "picked_by": "model"},
        })
        path.with_suffix(".md").write_text(f"# resume of {profile[-4:]}\n", encoding="utf-8")
        if spacing is not None:
            path.with_suffix(".layout").write_text(json.dumps({"spacing_percent": spacing}) + "\n", encoding="utf-8")
        self.folders[job_key(self.home, path)] = {"dir": f"acme/staff-engineer-{len(self.folders)}", "files": {"resume.md": "sha256:" + "a" * 64}}
        return path

    def save_index(self) -> None:
        jobs_folder._save_index(self.home, jobs_folder.jobs_folder(self.home).path, self.folders)

    def apply(self, *, apply: bool = True) -> migration.Migration:
        with committed_read_cache():
            return migration.migrate(self.home, self.target, apply=apply)

    def job_file(self, store: str, name: str, suffix: str = ".json") -> Path:
        return self.project_dir / store / JOB_FOLDER / f"{job_digest(JOBS[name])}{suffix}"


@pytest.fixture
def home(tmp_path: Path) -> Home:
    built = Home(build_gig_with_resume(tmp_path, resume_text=RESUME))
    a, b = built.a, built.b
    built.assessed(a, JOBS["only_a"], _DAY1)
    for name in ("newest", "applied", "edited", "one_resume"):
        built.assessed(a, JOBS[name], _DAY1)
        built.assessed(b, JOBS[name], _DAY2)
    built.assessed(a, JOBS["spacing"], _DAY3)
    built.assessed(b, JOBS["spacing"], _DAY2)
    built.assessed(b, JOBS["only_b"], _DAY2)
    built.assessed(None, PASTED, _DAY3)

    built.resume(a, JOBS["applied"], _DAY1_NOON)
    built.resume(b, JOBS["applied"], _DAY2, spacing=70)
    built.resume(a, JOBS["edited"], _DAY1_NOON, user_line=True)
    built.resume(b, JOBS["edited"], _DAY2)
    built.resume(a, JOBS["one_resume"], _DAY1_NOON)
    built.resume(a, JOBS["spacing"], _DAY3)
    built.resume(b, JOBS["spacing"], _DAY2, spacing=85)
    only_b = built.resume(b, JOBS["only_b"], _DAY2)
    built.save_index()

    # only_b's suggestion record has a proposed resume waiting beside it.
    record = role_path(built.home, built.target, "suggestions", b, JOBS["only_b"])
    sidecar = proposed_resume_path(record)
    # (As the product writes it: the waiting resume names the STORED resume's paths, where taking it writes it.)
    _write(sidecar, {"job": {"job_identity": JOBS["only_b"]}, "stored_path": str(only_b), "markdown_path": str(only_b.with_suffix(".md")), "updated_at": _DAY2})
    value = json.loads(record.read_bytes())
    value["proposed"] = {"resume": {"stored_path": str(sidecar), "origin": "pick"}}
    _write(record, value)

    with committed_read_cache():
        result = record_application(
            resolved=built.fx.resolved,
            data={"external_ref": JOBS["applied"], "event_kind": "applied", "occurred_at": _APPLIED_AT, "timezone": "UTC", "operation_key": f"pj1-{uuid.uuid4()}"},
            confirm=True,
        )
    assert result["status"] == "recorded"
    return built


def _tree(root: Path) -> dict[str, str]:
    """Every file under ``root`` with the digest of its bytes.

    But the workpad's own ``scratch/`` (git-ignored caches of its journal head, ``workpad.LAYOUT_CHECK_FILENAME``):
    resolving the workpad, which every GigAI read does, refreshes them. They are no part of the home's stores.
    """

    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if path.is_file() and not path.is_symlink() and not (parts[0] == "workpads" and "scratch" in parts):
            found[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def _profile_folders(home: Home) -> dict[str, str]:
    """The stores' own folders of both profiles (and ``ephemeral``): everything but the per-job folders."""

    return {name: digest for name, digest in _tree(home.project_dir).items() if f"/{JOB_FOLDER}/" not in f"/{name}"}


def _winner(home: Home, report: migration.Migration, name: str) -> tuple[str, str]:
    row = next(item for item in report.decided if item["job"] == job_digest(JOBS[name]))
    return ("A" if row["kept_profile_id"] == home.a else "B"), str(row["rule"])


def test_the_path_helpers_name_the_per_job_folder(home: Home) -> None:
    job = JOBS["applied"]
    digest = job_digest(job)
    assert job_quick_assess_path(home.home, home.target, job) == home.project_dir / "quick_assess" / "job" / f"{digest}.json"
    assert job_tailored_resume_path(home.home, home.target, job) == home.project_dir / "resumes" / "job" / f"{digest}.json"
    assert job_store_layout_path(home.home, home.target, job) == home.project_dir / "resumes" / "job" / f"{digest}.layout"
    assert job_suggestions_path(home.home, home.target, job) == home.project_dir / "suggestions" / "job" / f"{digest}.json"
    # The file name does not change: only the folder does.
    assert job_quick_assess_path(home.home, home.target, job).name == role_path(home.home, home.target, "quick_assess", home.a, job).name
    assert job_store_key(home.project, job) == job_key(home.home, job_tailored_resume_path(home.home, home.target, job)) == f"{home.project}/resumes/job/{digest}"


def test_a_dry_run_counts_and_writes_nothing(home: Home, tmp_path: Path) -> None:
    before = _tree(tmp_path)
    report = home.apply(apply=False)
    assert _tree(tmp_path) == before
    assert report.dry_run is True
    assert not migration.record_path(home.home).exists()
    counts = report.counts
    assert counts["assessments"] == {"files": 12, "jobs": 7, "kept": 7, "superseded": 5}
    assert counts["resumes"] == {"files": 8, "jobs": 5, "kept": 5, "superseded": 3}
    assert counts["layouts"] == {"files": 2, "kept": 1, "superseded": 1}
    assert counts["suggestions"] == {"files": 12, "jobs": 7, "kept": 7, "superseded": 5}
    assert counts["jobs_folder"] == {"job_entries": 5, "to_add": 5}
    # 7 assessments + 7 suggestion records + 1 sidecar + 5 resumes with their .md + 1 spacing.
    assert counts["files"] == {"to_copy": 26, "copied": 0, "already": 0}
    assert counts["deleted"] == 0
    assert (counts["jobs"], counts["jobs_held_by_several_roles"], counts["ambiguous_applied_resume"]) == (7, 5, 1)
    text = "\n".join(report.lines())
    assert text.startswith("Dry run: nothing was written.")
    assert "Assessments: 12 files for 7 jobs: 7 kept as the job's, 5 superseded" in text
    assert "profile" not in text.lower()  # a profile is a "role" in what the user reads


def test_the_winner_of_each_rule(home: Home) -> None:
    report = home.apply()
    assert _winner(home, report, "newest") == ("B", migration.RULE_NEWEST)
    assert _winner(home, report, "applied") == ("A", migration.RULE_APPLICATION)  # B's resume is newer, but stored after the application
    assert _winner(home, report, "edited") == ("A", migration.RULE_EDITED_RESUME)
    assert _winner(home, report, "one_resume") == ("A", migration.RULE_ONLY_STORED_RESUME)
    assert _winner(home, report, "spacing") == ("B", migration.RULE_EDITED_RESUME)  # a saved spacing is an edit; A is newer
    assert report.winners == {home.a: 3, home.b: 2}
    assert report.rules == {migration.RULE_NEWEST: 1, migration.RULE_APPLICATION: 1, migration.RULE_EDITED_RESUME: 2, migration.RULE_ONLY_STORED_RESUME: 1}
    assert len(report.decided) == 5  # only_a and only_b have one holder: nothing to decide

    # The copy IS the winner's record (but for the paths it holds).
    for name, winner in (("only_a", home.a), ("newest", home.b), ("applied", home.a), ("edited", home.a), ("one_resume", home.a), ("spacing", home.b), ("only_b", home.b)):
        copy = json.loads(home.job_file("quick_assess", name).read_bytes())
        assert copy["resume"]["profile_id"] == winner
        assert json.loads(home.job_file("suggestions", name).read_bytes())["profile_id"] == winner
    assert home.job_file("resumes", "applied", ".md").read_text(encoding="utf-8") == f"# resume of {home.a[-4:]}\n"
    assert json.loads(home.job_file("resumes", "edited").read_bytes())["result"]["sections"][0]["lines"][0]["origin"] == "user"
    # The spacing goes with the job's resume; the superseded one is not copied.
    assert json.loads(home.job_file("resumes", "spacing", ".layout").read_text(encoding="utf-8")) == {"spacing_percent": 85}
    assert not home.job_file("resumes", "applied", ".layout").exists()
    assert not home.job_file("resumes", "newest").exists()  # no profile has a resume for it
    # A pasted resume's records stay where they are.
    assert not (home.project_dir / "quick_assess" / JOB_FOLDER / f"{job_digest(PASTED)}.json").exists()


def test_the_profile_folders_are_byte_identical_and_nothing_is_deleted(home: Home, tmp_path: Path) -> None:
    folders = _profile_folders(home)
    everything = _tree(tmp_path)
    report = home.apply()
    assert _profile_folders(home) == folders
    after = _tree(tmp_path)
    assert {name: after[name] for name in everything if name not in ("home/scout/jobs-folder-index.json",)} == {
        name: digest for name, digest in everything.items() if name != "home/scout/jobs-folder-index.json"
    }  # every file that was there is there, unchanged (but the index, which only gained entries)
    new = sorted(set(after) - set(everything))
    record = migration.record_path(home.home).relative_to(tmp_path).as_posix()
    assert record in new
    assert all(f"/{JOB_FOLDER}/" in name for name in new if name != record)
    assert len(new) == 26 + 1
    assert report.counts["files"] == {"to_copy": 26, "copied": 26, "already": 0}
    assert report.counts["deleted"] == 0


def test_a_second_apply_changes_nothing(home: Home, tmp_path: Path) -> None:
    first = home.apply()
    after = _tree(tmp_path)
    stamps = {path: path.stat().st_mtime_ns for path in home.project_dir.rglob("*") if path.is_file()}
    stamps[migration.record_path(home.home)] = migration.record_path(home.home).stat().st_mtime_ns
    second = home.apply()
    assert _tree(tmp_path) == after
    assert {path: path.stat().st_mtime_ns for path in stamps} == stamps
    assert second.dry_run is False
    assert second.counts["files"] == {"to_copy": 0, "copied": 0, "already": 26}
    assert second.counts["jobs_folder"] == {"job_entries": 5, "to_add": 0}
    assert (second.winners, second.rules, second.decided) == (first.winners, first.rules, first.decided)
    # A dry run after it says the same and writes nothing.
    assert home.apply(apply=False).counts["files"] == {"to_copy": 0, "copied": 0, "already": 26}
    assert _tree(tmp_path) == after


def test_a_file_already_in_the_job_folder_is_never_replaced(home: Home) -> None:
    mine = home.job_file("quick_assess", "newest")
    mine.parent.mkdir(parents=True)
    mine.write_text('{"written": "since"}', encoding="utf-8")
    report = home.apply()
    assert mine.read_text(encoding="utf-8") == '{"written": "since"}'
    assert report.counts["files"] == {"to_copy": 25, "copied": 25, "already": 1}


def test_the_paths_inside_the_copies_resolve(home: Home) -> None:
    home.apply()
    for name in _NAMES:
        assessment = home.job_file("quick_assess", name)
        record = home.job_file("suggestions", name)
        assert json.loads(assessment.read_bytes())["stored_path"] == str(assessment)
        suggestion = json.loads(record.read_bytes())
        assert suggestion["stored_path"] == str(record)
        assert suggestion["basis"]["assessment"]["stored_path"] == str(assessment)
        resume = home.job_file("resumes", name)
        assert suggestion["selection"]["resume"]["stored_path"] == str(resume)  # where the job's resume is, or will be
        if not resume.exists():
            continue
        stored = json.loads(resume.read_bytes())
        assert stored["stored_path"] == str(resume)
        assert stored["markdown_path"] == str(resume.with_suffix(".md")) and Path(stored["markdown_path"]).is_file()
        assert stored["sources"]["assessment_stored_path"] == str(assessment) and assessment.is_file()
    # The proposed resume beside only_b's record went with it, and the record names the copy.
    sidecar = proposed_resume_path(home.job_file("suggestions", "only_b"))
    assert json.loads(home.job_file("suggestions", "only_b").read_bytes())["proposed"]["resume"]["stored_path"] == str(sidecar)
    # The waiting resume names the job's STORED resume (where taking it writes it), not its own file (0.1.11.9 PJ2:
    # the first copy named the sidecar itself, and ``use_proposed`` would have written the resume there).
    waiting = json.loads(sidecar.read_bytes())
    assert waiting["stored_path"] == str(home.job_file("resumes", "only_b"))
    assert waiting["markdown_path"] == str(home.job_file("resumes", "only_b", ".md"))
    # No copy names a profile's folder any more.
    for path in home.project_dir.rglob(f"{JOB_FOLDER}/*.json"):
        text = path.read_text(encoding="utf-8")
        assert f"/{home.a}/" not in text and f"/{home.b}/" not in text


def test_the_record_and_the_applied_job_with_two_resumes(home: Home) -> None:
    report = home.apply()
    digest = job_digest(JOBS["applied"])
    assert [item["job"] for item in report.ambiguous] == [digest]
    assert report.ambiguous[0]["kept_profile_id"] == home.a
    path = migration.record_path(home.home)
    assert path.stat().st_mode & 0o777 == 0o600
    record = json.loads(path.read_bytes())
    assert record["schema_version"] == migration.RECORD_SCHEMA and record["rule_order"] == list(migration.RULE_ORDER)
    project = record["projects"][home.project]
    assert project["ambiguous_applied_resume"] == [digest]
    assert len(project["jobs"]) == 7
    entry = project["jobs"][digest]
    assert (entry["kept_profile_id"], entry["rule"], entry["profiles"]) == (home.a, "application", sorted([home.a, home.b]))
    kept, superseded = f"{home.project}/resumes/{home.a}/{digest}.json", f"{home.project}/resumes/{home.b}/{digest}.json"
    assert entry["resume"]["kept"] == kept and entry["resume"]["superseded"] == [superseded]
    assert entry["resume"]["to"] == [f"{home.project}/resumes/job/{digest}.json", f"{home.project}/resumes/job/{digest}.md"]
    assert entry["ambiguous_applied_resume"]["resumes"] == sorted([kept, superseded])  # both are kept readable
    assert (home.home / "scout" / f"{superseded}").is_file() and (home.home / "scout" / kept).is_file()
    assert entry["assessment"]["superseded"] == [f"{home.project}/quick_assess/{home.b}/{digest}.json"]
    assert "ambiguous_applied_resume" not in project["jobs"][job_digest(JOBS["edited"])]
    # No address of a posting in the record: a job is its digest.
    assert "jobs.example.invalid" not in path.read_text(encoding="utf-8")


def test_the_jobs_folder_index_gets_one_entry_per_job_and_keeps_the_others(home: Home) -> None:
    before = jobs_folder._load_index(home.home, jobs_folder.jobs_folder(home.home).path)
    assert len(before) == 8
    home.apply()
    after = jobs_folder._load_index(home.home, jobs_folder.jobs_folder(home.home).path)
    assert {key: after[key] for key in before} == before
    added = {key: entry for key, entry in after.items() if key not in before}
    assert sorted(added) == sorted(job_store_key(home.project, JOBS[name]) for name in ("applied", "edited", "one_resume", "spacing", "only_b"))
    winner = job_key(home.home, role_path(home.home, home.target, "resumes", home.a, JOBS["applied"]))
    assert added[job_store_key(home.project, JOBS["applied"])] == before[winner]
    # The job's entry is found the way a reader of the per-job store will look it up.
    assert jobs_folder._load_index(home.home, jobs_folder.jobs_folder(home.home).path)[job_key(home.home, home.job_file("resumes", "applied"))] == before[winner]


def test_applications_that_cannot_be_read_stop_the_plan(home: Home, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(**_kwargs: object) -> object:
        raise RuntimeError("no journal")

    monkeypatch.setattr("gigai.journal.read_committed_snapshot", broken)
    with pytest.raises(migration.JobStoreMigrationError) as caught:
        home.apply()
    assert caught.value.code == "applications_unreadable"
    assert not (home.project_dir / "quick_assess" / JOB_FOLDER).exists()


def test_the_command_is_a_dry_run_unless_apply(home: Home, tmp_path: Path) -> None:
    where = ["--home", str(home.home), "--target", str(home.target)]
    before = _tree(tmp_path)
    dry = CliRunner().invoke(cli, ["scout", "migrate-job-stores", *where])
    assert dry.exit_code == 0, dry.output
    assert _tree(tmp_path) == before
    assert "Dry run: nothing was written." in dry.output and "Files to copy: 26; already there: 0" in dry.output
    assert "Copy them: gigai scout migrate-job-stores --apply" in dry.output
    assert f"kept: role ...{home.a[-6:]} 3, role ...{home.b[-6:]} 2" in dry.output or f"kept: role ...{home.b[-6:]} 2, role ...{home.a[-6:]} 3" in dry.output
    assert "decided by: application 1, edited resume 2, only stored resume 1, newest 1" in dry.output
    assert f"every one of them is kept readable: {job_digest(JOBS['applied'])[:8]}" in dry.output
    assert "jobs.example.invalid" not in dry.output

    done = CliRunner().invoke(cli, ["scout", "migrate-job-stores", "--apply", "--json", *where])
    assert done.exit_code == 0, done.output
    payload = json.loads(done.output)
    assert payload["ok"] is True and payload["dry_run"] is False and payload["schema_version"] == migration.RESPONSE_SCHEMA
    assert payload["counts"]["files"] == {"to_copy": 26, "copied": 26, "already": 0}
    assert payload["kept_by_profile"] == {home.a: 3, home.b: 2}
    again = json.loads(CliRunner().invoke(cli, ["scout", "migrate-job-stores", "--apply", "--json", *where]).output)
    assert again["counts"]["files"] == {"to_copy": 0, "copied": 0, "already": 26}


def test_the_command_refuses_a_folder_that_is_no_scout_project(tmp_path: Path) -> None:
    empty = tmp_path / "empty-home"
    empty.mkdir()
    result = CliRunner().invoke(cli, ["scout", "migrate-job-stores", "--json", "--home", str(empty)])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "target_unavailable"
    assert list(empty.iterdir()) == []  # never creates a Scout project


def test_a_list_shows_a_job_once_before_and_after_the_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real stored assessments (a scripted model), left as a GigAI before 0.1.11.9 left them: one in each role's folder.
    A list shows the job ONCE: the record the migration would keep, read in place; after it, the per-job copy."""

    fx = build_gig_with_resume(tmp_path, resume_text=RESUME)
    a, b = two_profiles(fx)
    install_model(monkeypatch, [pending("cloud:gcp", "GCP?"), pending("cloud:gcp", "GCP?")])
    text = "Staff Engineer at Acme. 5+ years of Python. You know the platform."
    job = assess(fx, a, text, title="Staff Engineer").job.job_identity
    to_role_folder(fx.home_root, fx.target, a, job)
    assess(fx, b, text, title="Staff Engineer")
    (newest,) = [path for path in to_role_folder(fx.home_root, fx.target, b, job) if path.parent.parent.name == "quick_assess"]
    before = role_folders(fx.home_root, fx.target)

    listed = list_quick_assessments(fx.home_root, fx.target)
    assert [item.stored_path for item in listed] == [str(newest)]  # two roles hold it, no resume: the newest assessment's
    assert listed[0].resume.profile_id == b

    with committed_read_cache():
        report = migration.migrate(fx.home_root, fx.target, apply=True)
    assert report.counts["assessments"] == {"files": 2, "jobs": 1, "kept": 1, "superseded": 1}
    copy = job_quick_assess_path(fx.home_root, fx.target, job)
    stored = _read_stored(copy)
    assert stored is not None and stored.stored_path == str(copy) and stored.job.job_identity == job
    assert stored.result == listed[0].result

    after = list_quick_assessments(fx.home_root, fx.target)
    assert [item.stored_path for item in after] == [str(copy)]
    # A role's id no longer narrows: the job's one assessment, whichever role is named.
    assert [item.stored_path for item in list_quick_assessments(fx.home_root, fx.target, profile_id=a)] == [str(copy)]
    assert role_folders(fx.home_root, fx.target) == before


def test_a_resume_list_reads_the_kept_resume_of_each_job(home: Home, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Path] = []

    def read(path: Path) -> object:
        seen.append(path)
        return SimpleNamespace(job=SimpleNamespace(job_identity=JOBS["applied"]), updated_at=_DAY1, stored_path=str(path))

    monkeypatch.setattr(tailored_resume, "_read_stored", read)
    kept = {"applied": home.a, "edited": home.a, "one_resume": home.a, "spacing": home.b, "only_b": home.b}
    # Not migrated: one resume a job, the one the migration would keep, read where its role's folder holds it.
    with committed_read_cache():
        assert len(list_tailored_resumes(home.home, home.target)) == 5
    assert sorted(seen) == sorted(role_path(home.home, home.target, "resumes", profile, JOBS[name]) for name, profile in kept.items())
    seen.clear()
    home.apply()
    assert len(list_tailored_resumes(home.home, home.target)) == 5
    assert sorted(seen) == sorted(home.job_file("resumes", name) for name in kept)  # migrated: the per-job folder alone


# --- 0.1.11.9 RB2: a job posted more than once keeps ONE posting's records ---------------------------------------


def _lever(slug: str, n: int, title: str, location: str, minutes: int) -> dict[str, object]:
    """One made-up Lever posting; every one has the same description, so two with one title are copies of one job."""

    return {
        "id": f"{slug}-{n:05d}", "text": title, "hostedUrl": f"https://jobs.lever.co/{slug}/{slug}-{n:05d}",
        "categories": {"location": location}, "country": "US" if location.endswith(("CO", "TX")) else None, "workplaceType": "remote",
        "descriptionPlain": "Own the platform services. Requirements: Python in production; Kubernetes.",
        "createdAt": int((datetime(2026, 10, 1, 12, 0, tzinfo=UTC) + timedelta(minutes=minutes)).timestamp() * 1000),
    }


def _url(n: int) -> str:
    return f"https://jobs.lever.co/copies-example/copies-example-{n:05d}"


def test_a_job_posted_more_than_once_keeps_one_postings_records_and_the_others_are_superseded(tmp_path: Path) -> None:
    built = Home(build_gig_with_resume(tmp_path, resume_text=RESUME))
    a, b = built.a, built.b
    # One company file: "Staff Engineer" in Denver (1: its US posting, the canonical one although the newest), Estonia
    # (2) and Latvia (3); "Staff Engineer, Payments" in Estonia (4, the earliest) and Poland (5).
    cache = BoardCache(built.home / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)
    jobs = [
        _lever("copies-example", 1, "Staff Engineer", "Denver, CO", 50), _lever("copies-example", 2, "Staff Engineer", "Remote Estonia", 10),
        _lever("copies-example", 3, "Staff Engineer", "Remote Latvia", 20), _lever("copies-example", 4, "Staff Engineer, Payments", "Remote Estonia", 1),
        _lever("copies-example", 5, "Staff Engineer, Payments", "Remote Poland", 2),
    ]
    cache.store("lever", board_list_url("lever", "copies-example"), body=json.dumps(jobs).encode("utf-8"), etag=None, last_modified=None, marker=None)
    refresh_company(CompanyIndex.for_home(built.home), cache, ats="lever", slug="copies-example", observed_at=index_stamp(datetime(2026, 10, 2, 12, 0, tzinfo=UTC)))
    us, estonia, latvia, payments, payments_pl = (_url(n) for n in range(1, 6))
    assert copies_of(built.home, latvia) == (us, estonia, latvia) and copies_of(built.home, payments_pl) == (payments, payments_pl)

    # "Staff Engineer": role B assessed the US posting (the newest record); role A assessed the Estonia copy and has a
    # stored resume for it; role B assessed the Latvia copy too. The copy with a RESUME is the job's: Estonia.
    built.assessed(b, us, _DAY3)
    built.assessed(a, estonia, _DAY1)
    built.resume(a, estonia, _DAY1_NOON)
    built.assessed(b, latvia, _DAY2)
    # "Staff Engineer, Payments": both copies only assessed (the Poland one by both roles): the first in the canonical
    # order is the job's, the earliest posted. Role A's is the only record of it.
    built.assessed(a, payments, _DAY1)
    built.assessed(a, payments_pl, _DAY2)
    built.assessed(b, payments_pl, _DAY3)
    built.save_index()
    assert job_identity_key(built.home, built.target, us) == estonia and job_identity_key(built.home, built.target, payments_pl) == payments

    store = quick_assess_dir(built.home, built.target)

    def read(job: str) -> tuple[object, object]:
        """What the store's own path rule reads for ``job`` under role B: the record's posting, role and time."""

        path = quick_assess_path(built.home, built.target, b, job)
        item = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        resume = tailored_resume.tailored_resume_path(built.home, built.target, b, job)
        return (None if item is None else (item["job"]["job_identity"], item["resume"]["profile_id"], item["updated_at"])), resume.exists()

    def only_in_role_folders() -> list[str]:
        """The jobs a LIST of the store adds from the roles' folders (``role_records``): one a job, never a superseded copy."""

        found = migration.role_records(store, home_root=built.home, target=built.target)
        return sorted(json.loads(path.read_text(encoding="utf-8"))["job"]["job_identity"] for path in found)

    # Not migrated: every copy already reads the kept posting's record (the role's own file), and lists hold one a job.
    before = {job: read(job) for job in (us, estonia, latvia, payments, payments_pl)}
    assert before[us] == before[estonia] == before[latvia] == ((estonia, a, _DAY1), True)
    assert before[payments] == before[payments_pl] == ((payments, a, _DAY1), False)
    assert only_in_role_folders() == sorted([estonia, payments])
    roles_before = _profile_folders(built)
    scout_before = _tree(built.home / "scout")

    dry = built.apply(apply=False)

    assert dry.dry_run and _tree(built.home / "scout") == scout_before and not migration.record_path(built.home).exists()
    counts = dry.counts
    assert counts["jobs"] == 2 and counts["jobs_posted_more_than_once"] == {"jobs": 2, "superseded_postings": 3, "superseded_files": 8}
    # 6 assessment files, 2 jobs: 2 kept, 4 superseded (3 under another posting, none under another role here).
    assert counts["assessments"] == {"files": 6, "jobs": 2, "kept": 2, "superseded": 4}
    assert counts["suggestions"] == {"files": 6, "jobs": 2, "kept": 2, "superseded": 4} and counts["resumes"] == {"files": 1, "jobs": 1, "kept": 1, "superseded": 0}
    assert counts["jobs_held_by_several_roles"] == 0, "within the kept posting one role holds each job: no role rule ran"
    report = {item["job"]: {copy["job"]: copy["files"] for copy in item["superseded"]} for item in dry.to_json()["copies"]}
    assert set(report) == {job_digest(estonia), job_digest(payments)}
    assert set(report[job_digest(estonia)]) == {job_digest(us), job_digest(latvia)} and set(report[job_digest(payments)]) == {job_digest(payments_pl)}
    assert sorted(report[job_digest(payments)][job_digest(payments_pl)]) == sorted(
        f"{built.project}/{store}/{role}/{job_digest(payments_pl)}.json" for store in ("quick_assess", "suggestions") for role in (a, b)
    )
    assert dry.to_json()["copies_rule"] == migration.COPIES_RULE
    assert any(line.startswith("Jobs posted more than once with records under more than one posting: 2;") and "3 other posting(s), 8 file(s) superseded" in line for line in dry.lines())

    done = built.apply()

    # ONE of each a job, under the kept posting's key; nothing under another copy's; no identity inside was rewritten.
    job_files = sorted(path.relative_to(built.project_dir).as_posix() for store in ("quick_assess", "resumes", "suggestions") for path in (built.project_dir / store / JOB_FOLDER).glob("*"))
    assert job_files == sorted(
        [f"{store}/job/{job_digest(job)}.json" for store in ("quick_assess", "suggestions") for job in (estonia, payments)]
        + [f"resumes/job/{job_digest(estonia)}.json", f"resumes/job/{job_digest(estonia)}.md"]
    )
    kept = json.loads(job_quick_assess_path(built.home, built.target, estonia).read_text(encoding="utf-8"))
    assert kept["job"]["job_identity"] == estonia and kept["resume"]["profile_id"] == a
    assert done.counts["files"] == {"to_copy": 6, "copied": 6, "already": 0} and done.counts["deleted"] == 0
    assert _profile_folders(built) == roles_before, "every role's folder is as it was: the superseded records stay"
    # The same record is read after as before, by every copy; the lists hold one a job.
    assert {job: read(job) for job in before} == before
    assert only_in_role_folders() == [], "a superseded copy's record is not listed as a job of its own"
    # The record says which postings were superseded, and by which rule.
    record = json.loads(migration.record_path(built.home).read_text(encoding="utf-8"))["projects"][built.project]["jobs"]
    assert set(record) == {job_digest(estonia), job_digest(payments)}
    assert record[job_digest(estonia)]["copies"]["rule"] == migration.COPIES_RULE and record[job_digest(estonia)]["kept_profile_id"] == a
    assert [copy["job"] for copy in record[job_digest(estonia)]["copies"]["superseded"]] == sorted([job_digest(us), job_digest(latvia)])
    # A second run copies nothing and changes nothing.
    tree = _tree(built.home / "scout")
    again = built.apply()
    assert again.counts["files"] == {"to_copy": 0, "copied": 0, "already": 6} and _tree(built.home / "scout") == tree
