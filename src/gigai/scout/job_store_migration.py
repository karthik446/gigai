"""0.1.11.9 PJ1: the one-time copy of the per-profile stores into the per-job stores.

Until 0.1.11.9 a job's assessment, stored resume (with its ``.md`` and its ``.layout`` spacing) and suggestion
record were kept once PER PROFILE (``<store>/<profile id>/<sha256(job identity)>.json``).  ``migrate`` gives
each job ONE of each, in the store's per-job folder (``job_store_layout``: ``<store>/job/<same name>``).

**Which profile's records become the job's.**  A job one profile holds: that profile's.  A job several
profiles hold: the first rule of this order that names one (decision 4 of the 0.1.11.7 design, approved):

1. ``application``: the job has an application, and a profile has a stored resume for it.  An application
   event names no profile and no document, so this is a PROXY: the only profile with a stored resume, else the
   one whose resume was stored last before the application (the last stored when none was before it).
2. ``edited_resume``: the profile whose stored resume the user edited (a line of their own, a pin, an exclude
   or a saved spacing); the newest of them when several did.
3. ``only_stored_resume``: the only profile with a stored resume.
4. ``newest``: the profile with the newest assessment.

A job's three records come from that ONE profile.  A store the winner has nothing in is filled from the
newest record another profile holds there (so no record is left behind).

**A job posted more than once** (0.1.11.9 RB2).  The same job posted once per country is ONE job
(``find_jobs/job_copies``: the same board, company, title and description), so its copies get ONE of each record
too, under the identity ``find_jobs.job_key.job_key`` names for all of them: the first copy, in the canonical
order (its US posting, else the earliest posted, then the posting id), that holds a stored resume, else the first
that holds an assessment (``COPIES_RULE``).  The rule above then says which PROFILE's records of that posting are
kept.  What the profiles hold under another copy's identity is superseded like a superseded profile's: left where
it is, never copied, and listed with the kept job (``copies`` in the record and in the report).  No identity
inside a record is rewritten.

**An applied job where several profiles have a stored resume** cannot say which PDF was sent.  It is listed as
``ambiguous_applied_resume`` with every one of those resumes, and all of them stay readable.

**Copy, never move.**  Nothing under a profile's folder is changed or removed: the superseded records stay
where they are, readable, and an older GigAI opened on the same home still finds what it wrote.  Nothing in a
per-job folder is ever replaced: a file that is already there is left as it is (so a second run is a no-op,
and a job GigAI has written since is never set back).

**Paths inside the copies** are the copies' own: a record's ``stored_path`` (and a resume's
``markdown_path``), a resume's ``sources.assessment_stored_path``, the suggestion's ``basis`` and the resume
its selection names all point into the per-job folders.

**The jobs folder index** gets one entry per job (``resumes_folder.job_store_key``) that names the folder the
winning profile's resume has.  The entries keyed per profile stay, and no folder on disk is touched.

**The record.**  ``<home>/scout/job-stores-migration.json`` (0600: ids, digests and paths under
``<home>/scout``; no posting, no resume line) says per job what was kept, what was superseded and the rule
that decided.  A dry run plans with the same code and writes nothing: no folder, no index, no record.  (Reading
the applications resolves the workpad, as every GigAI read does; that may refresh the workpad's own git-ignored
``scratch/`` check file.  Nothing under ``<home>/scout`` or the jobs folder is touched.)

Applications, answers and rank scores are not touched: the first two are per job / per user already, and
rank stays a profile's.  Nothing here calls a model or the network.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path, PurePath
import stat

from . import jobs_folder, resumes_folder
from .find_jobs.discovery.storage import atomic_write, project_id
from .job_store_layout import (
    ASSESSMENTS,
    JOB_FILE_SUFFIXES,
    JOB_FOLDER,
    JOB_STORES,
    LAYOUT_SUFFIX,
    MARKDOWN_SUFFIX,
    PROPOSED_RESUME_SUFFIX,
    RECORD_SUFFIX,
    RESUMES,
    SUGGESTIONS,
    is_profile_folder,
    job_digest,
    job_store_dir,
)

RECORD_SCHEMA = "scout-job-stores-migration:1"
RESPONSE_SCHEMA = "scout-job-stores-migrate:1"

MIGRATE_COMMAND = "gigai scout migrate-job-stores"

#: Why a profile's records became the job's.
RULE_ONLY_HOLDER = "only_holder"
RULE_APPLICATION = "application"
RULE_EDITED_RESUME = "edited_resume"
RULE_ONLY_STORED_RESUME = "only_stored_resume"
RULE_NEWEST = "newest"
#: The order that decides a job several profiles hold.
RULE_ORDER = (RULE_APPLICATION, RULE_EDITED_RESUME, RULE_ONLY_STORED_RESUME, RULE_NEWEST)

#: Which posting's records a job posted more than once keeps (``find_jobs.job_key.kept_copy``), as the record says it.
COPIES_RULE = "the first copy in the canonical order (US posting, else earliest posted, then posting id) with a stored resume, else the first with an assessment"

_SHORT = 8
_RECORD_KEYS = {ASSESSMENTS: "assessment", RESUMES: "resume", SUGGESTIONS: "suggestion"}


class JobStoreMigrationError(ValueError):
    """The stores cannot be migrated as asked; ``code`` is the CLI error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def record_path(home_root: Path) -> Path:
    return Path(home_root) / "scout" / "job-stores-migration.json"


# --- reading what the profiles hold -------------------------------------------------------------


def _read_json(path: Path) -> dict[str, object] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return value if type(value) is dict else None


def _moment(value: object) -> str:
    """A stored time as text that sorts by time (UTC, microseconds); the text itself when it is not a time."""

    if not isinstance(value, str) or not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")


def _holds_user_line(value: object) -> bool:
    """A stored resume has a line the user wrote or changed (``origin: "user"`` on a line)."""

    if isinstance(value, dict):
        return value.get("origin") == "user" or any(_holds_user_line(item) for item in value.values())
    if isinstance(value, list):
        return any(_holds_user_line(item) for item in value)
    return False


@dataclass(frozen=True)
class _Held:
    """One record a profile holds for a job."""

    profile_id: str
    path: Path
    at: str
    #: A stored resume only: the user edited it (a line, a pin, an exclude or a saved spacing).
    edited: bool = False


def _job_of(store: str, item: Mapping[str, object]) -> str | None:
    if store == SUGGESTIONS:
        job = item.get("job_identity")
    else:
        inner = item.get("job")
        job = inner.get("job_identity") if isinstance(inner, dict) else None
    return job if isinstance(job, str) and job else None


def _saved_spacing(resume_path: Path) -> bool:
    layout = _read_json(resume_path.with_suffix(LAYOUT_SUFFIX))
    return layout is not None and layout.get("spacing_percent") is not None


def _scan(project_dir: Path, store: str) -> dict[str, dict[str, _Held]]:
    """``{job identity: {profile id: record}}`` of every profile folder of ``store`` (never the per-job folder, never ``ephemeral``)."""

    root = project_dir / store
    found: dict[str, dict[str, _Held]] = {}
    if root.is_symlink() or not root.is_dir():
        return found
    for folder in sorted(root.iterdir(), key=lambda item: item.name):
        if folder.is_symlink() or not folder.is_dir() or not is_profile_folder(folder.name):
            continue
        for path in sorted(folder.glob(f"*{RECORD_SUFFIX}")):
            if path.name.endswith(PROPOSED_RESUME_SUFFIX):
                continue  # a suggestion record's sidecar: it goes where its record goes
            read = _held(store, folder.name, path)
            if read is not None:
                found.setdefault(read[0], {})[folder.name] = read[1]
    return found


def _held(store: str, profile_id: str, path: Path) -> tuple[str, _Held] | None:
    """``(job identity, record)`` of the record a profile holds at ``path``, or ``None`` (no file, or not a record)."""

    item = _read_json(path)
    job = None if item is None else _job_of(store, item)
    if item is None or job is None:
        return None
    edited = False
    if store == RESUMES:
        selection = item.get("selection")
        selection = selection if isinstance(selection, dict) else {}
        edited = bool(_holds_user_line(item.get("result")) or selection.get("pins") or selection.get("excludes") or _saved_spacing(path))
    return job, _Held(profile_id, path, _moment(item.get("updated_at") or item.get("created_at")), edited)


def read_applied(home_root: Path, target: Path) -> dict[str, str]:
    """``{job identity: when}`` of every job an application event put in an application state (the committed events).

    One committed read of the events family, and never ``job_state.read_application_events``: that one builds an
    index of the events in the workpad's scratch folder, and a dry run is to write as little as a read can.
    """

    from ..canonical import parse_json_bytes
    from ..journal import read_committed_snapshot
    from ..workpad import resolve_workpad
    from .find_jobs.job_state import _EVENTS_PREFIX, current_application_event, group_events

    try:
        resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
        snapshot = read_committed_snapshot(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, prefixes=(_EVENTS_PREFIX,))
    except Exception as exc:  # noqa: BLE001 - without the applications the first rule cannot be applied: refuse, never guess
        raise JobStoreMigrationError("applications_unreadable", "the applications of this Scout project could not be read, so nothing was planned") from exc
    events: list[Mapping[str, object]] = []
    for path in sorted(snapshot.artifacts):
        if not (path.startswith(_EVENTS_PREFIX) and path.endswith(".json")):
            continue
        try:
            value = parse_json_bytes(snapshot.artifacts[path])
        except ValueError:
            continue
        if isinstance(value, dict):
            events.append(value)
    applied: dict[str, str] = {}
    for job, group in group_events(events).items():
        event = current_application_event(group)
        if event is not None:
            applied[job] = _moment(event.get("occurred_at"))
    return applied


# --- which profile's records become the job's ---------------------------------------------------


def _newest(pool: Mapping[str, _Held]) -> str:
    return max(pool, key=lambda profile_id: (pool[profile_id].at, profile_id))


def choose(
    assessed: Mapping[str, _Held], resumes: Mapping[str, _Held], suggested: Mapping[str, _Held], applied_at: str | None,
) -> tuple[str, str]:
    """``(profile id, rule)``: whose records become the job's, and the rule that said so (the module's order)."""

    holders = sorted({*assessed, *resumes, *suggested})
    if len(holders) == 1:
        return holders[0], RULE_ONLY_HOLDER
    if applied_at is not None and resumes:
        before = {profile_id: held for profile_id, held in resumes.items() if held.at <= applied_at}
        return _newest(before or resumes), RULE_APPLICATION
    edited = {profile_id: held for profile_id, held in resumes.items() if held.edited}
    if edited:
        return _newest(edited), RULE_EDITED_RESUME
    if len(resumes) == 1:
        return next(iter(resumes)), RULE_ONLY_STORED_RESUME
    return max(holders, key=lambda profile_id: (assessed[profile_id].at if profile_id in assessed else "", profile_id)), RULE_NEWEST


# --- the plan ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Copy:
    source: Path
    dest: Path
    #: A JSON record whose inner paths are rewritten; ``False``: copied byte for byte.
    record: bool


@dataclass
class _JobPlan:
    job: str
    digest: str
    winner: str
    rule: str
    holders: list[str]
    #: store -> the record that becomes the job's.
    kept: dict[str, _Held] = field(default_factory=dict)
    #: store -> the records left where they are.
    superseded: dict[str, list[_Held]] = field(default_factory=dict)
    copies: list[_Copy] = field(default_factory=list)
    #: The applied job's stored resumes when several profiles have one (the sent PDF cannot be told).
    ambiguous: list[_Held] = field(default_factory=list)
    applied_at: str | None = None

    def stems(self) -> frozenset[str]:
        """The names (without a suffix) this job's files have in the profiles' folders: the digest, for every file GigAI wrote."""

        every = [*self.kept.values(), *(held for found in self.superseded.values() for held in found)]
        return frozenset({self.digest, *(held.path.stem for held in every)})


def _relative(home_root: Path, path: Path) -> str:
    try:
        return path.relative_to(Path(home_root) / "scout").as_posix()
    except ValueError:
        return os.fspath(path)


def _plan_job(project_dir: Path, job: str, held: Mapping[str, Mapping[str, _Held]], applied_at: str | None, recorded: Mapping[str, object] | None) -> _JobPlan:
    assessed, resumes, suggested = held[ASSESSMENTS], held[RESUMES], held[SUGGESTIONS]
    winner, rule = choose(assessed, resumes, suggested, applied_at)
    holders = sorted({*assessed, *resumes, *suggested})
    if recorded is not None and recorded.get("kept_profile_id") in holders and isinstance(recorded.get("rule"), str):
        # Decided once: an earlier run's choice stands (a run that stopped part way copies the rest from the same profile).
        winner, rule = str(recorded["kept_profile_id"]), str(recorded["rule"])
    digest = job_digest(job)
    plan = _JobPlan(job, digest, winner, rule, holders, applied_at=applied_at)
    for store in JOB_STORES:
        pool = held[store]
        if not pool:
            continue
        source = pool[winner] if winner in pool else pool[_newest(pool)]
        plan.kept[store] = source
        plan.superseded[store] = [pool[profile_id] for profile_id in sorted(pool) if profile_id != source.profile_id]
        folder = job_store_dir(project_dir / store)
        plan.copies.append(_Copy(source.path, folder / f"{digest}{RECORD_SUFFIX}", True))
        beside = {RESUMES: ((MARKDOWN_SUFFIX, False), (LAYOUT_SUFFIX, False)), SUGGESTIONS: ((PROPOSED_RESUME_SUFFIX, True),)}.get(store, ())
        for suffix, is_record in beside:
            sibling = source.path.with_name(f"{source.path.stem}{suffix}")
            if sibling.is_file() and not sibling.is_symlink():
                plan.copies.append(_Copy(sibling, folder / f"{digest}{suffix}", is_record))
    if applied_at is not None and len(resumes) > 1:
        plan.ambiguous = [resumes[profile_id] for profile_id in sorted(resumes)]
    return plan


# --- the copies -------------------------------------------------------------------------------


def _job_path(project_dir: Path, digest: str, stems: frozenset[str], value: str) -> str | None:
    """The per-job path of ``value`` when it names one of this job's files in a profile's folder, else ``None``."""

    if not value.startswith(("/", "~")) and not PurePath(value).is_absolute():
        return None
    parts = PurePath(value).parts
    if len(parts) < 3 or parts[-3] not in JOB_STORES or not is_profile_folder(parts[-2]):
        return None
    for suffix in sorted(JOB_FILE_SUFFIXES, key=len, reverse=True):
        if parts[-1].endswith(suffix) and parts[-1][: -len(suffix)] in stems:
            return os.fspath(job_store_dir(project_dir / parts[-3]) / f"{digest}{suffix}")
    return None


def _rewritten(value: object, project_dir: Path, digest: str, stems: frozenset[str]) -> object:
    if isinstance(value, str):
        moved = _job_path(project_dir, digest, stems, value)
        return value if moved is None else moved
    if isinstance(value, dict):
        return {key: _rewritten(item, project_dir, digest, stems) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewritten(item, project_dir, digest, stems) for item in value]
    return value


def _copy_bytes(project_dir: Path, plan: _JobPlan, copy: _Copy) -> bytes:
    data = copy.source.read_bytes()
    if not copy.record:
        return data
    item = json.loads(data)
    if type(item) is not dict:
        raise ValueError("a stored record is not a JSON object")
    # Every path a record holds to one of this job's files in a profile's folder (whichever profile's: the job has ONE of each now).
    item = _rewritten(item, project_dir, plan.digest, plan.stems())
    assert isinstance(item, dict)
    # Where the copy IS, whatever it recorded (a home that was copied or moved names another folder there).
    # (Not the sidecar of a suggestion record: it is a resume that WAITS there, and the paths it names are the job's
    # stored resume's, where ``suggestions.use_proposed`` writes it. The rewrite above has made them the per-job ones.)
    if copy.dest.name == f"{plan.digest}{RECORD_SUFFIX}":
        if "stored_path" in item:
            item["stored_path"] = os.fspath(copy.dest)
        if "markdown_path" in item:
            item["markdown_path"] = os.fspath(copy.dest.with_suffix(MARKDOWN_SUFFIX))
    return json.dumps(item, indent=2, sort_keys=True).encode("utf-8")  # the stores' own form


def _taken(path: Path) -> bool:
    return path.is_symlink() or path.exists()


def _write_copy(project_dir: Path, plan: _JobPlan, copy: _Copy) -> None:
    data = _copy_bytes(project_dir, plan, copy)
    atomic_write(copy.dest, data)
    os.chmod(copy.dest, stat.S_IMODE(copy.source.stat().st_mode))


# --- the record ---------------------------------------------------------------------------------


def _load_record(home_root: Path) -> dict[str, dict[str, object]]:
    """``{project id: {job digest: entry}}`` of the runs before this one."""

    raw = _read_json(record_path(home_root))
    if raw is None or raw.get("schema_version") != RECORD_SCHEMA or type(raw.get("projects")) is not dict:
        return {}
    kept: dict[str, dict[str, object]] = {}
    for project, value in raw["projects"].items():  # type: ignore[union-attr]
        jobs = value.get("jobs") if type(value) is dict else None
        if isinstance(project, str) and type(jobs) is dict:
            kept[project] = {digest: entry for digest, entry in jobs.items() if isinstance(digest, str) and type(entry) is dict}
    return kept


def _save_record(home_root: Path, projects: Mapping[str, Mapping[str, object]]) -> None:
    path = record_path(home_root)
    if path.is_symlink():
        raise OSError("the job stores migration record path is a symlink")
    payload = {
        "schema_version": RECORD_SCHEMA,
        "rule_order": list(RULE_ORDER),
        "projects": {
            project: {
                "jobs": {digest: jobs[digest] for digest in sorted(jobs)},
                "ambiguous_applied_resume": sorted(digest for digest, entry in jobs.items() if isinstance(entry, dict) and "ambiguous_applied_resume" in entry),
            }
            for project, jobs in sorted(projects.items())
        },
    }
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_write(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    os.chmod(path, 0o600)


def _entry(home_root: Path, plan: _JobPlan, folder: Mapping[str, str] | None) -> dict[str, object]:
    entry: dict[str, object] = {"kept_profile_id": plan.winner, "rule": plan.rule, "profiles": list(plan.holders)}
    for store in JOB_STORES:
        if store not in plan.kept:
            continue
        kept = plan.kept[store]
        part: dict[str, object] = {
            "kept": _relative(home_root, kept.path),
            "kept_profile_id": kept.profile_id,
            "to": sorted(_relative(home_root, copy.dest) for copy in plan.copies if copy.dest.parent.parent.name == store),
            "superseded": [_relative(home_root, held.path) for held in plan.superseded[store]],
        }
        if store == RESUMES and folder is not None:
            part["jobs_folder"] = dict(folder)
        entry[_RECORD_KEYS[store]] = part
    if plan.ambiguous:
        entry["ambiguous_applied_resume"] = {
            "resumes": [_relative(home_root, held.path) for held in plan.ambiguous],
            "note": "the application names no resume: each of these stays readable, and the kept one is the last stored before the application",
        }
    return entry


# --- the report -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Migration:
    """What one run planned or did.  Ids, digests and counts only, never a posting's or a resume's text."""

    dry_run: bool
    project_id: str
    record: Path
    counts: dict[str, object]
    #: Of the jobs several profiles hold: how many each profile's records were kept for, and by which rule.
    winners: dict[str, int]
    rules: dict[str, int]
    #: ``{job, kept_profile_id, resumes}``: an applied job with a stored resume under several profiles.
    ambiguous: tuple[dict[str, object], ...]
    #: ``{job, kept_profile_id, rule, profiles}`` per job several profiles hold.
    decided: tuple[dict[str, object], ...]
    #: 0.1.11.9 RB2: ``{job, superseded: [{job, files}]}`` per job posted more than once whose copies hold records:
    #: the kept posting's digest and each other copy's, with the files left where they are.
    copies: tuple[dict[str, object], ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": RESPONSE_SCHEMA,
            "dry_run": self.dry_run,
            "project_id": self.project_id,
            "record": os.fspath(self.record),
            "counts": self.counts,
            "kept_by_profile": self.winners,
            "decided_by": self.rules,
            "ambiguous_applied_resume": list(self.ambiguous),
            "decided": list(self.decided),
            "copies_rule": COPIES_RULE,
            "copies": list(self.copies),
        }

    def lines(self) -> list[str]:
        """The plain-text report (a profile is a "role" in what the user reads)."""

        counts = self.counts
        files = counts["files"]
        assert isinstance(files, dict)

        def store(label: str, key: str) -> str:
            part = counts[key]
            assert isinstance(part, dict)
            return f"{label}: {part['files']} files for {part['jobs']} jobs: {part['kept']} kept as the job's, {part['superseded']} superseded (left where they are)"

        layouts, folders = counts["layouts"], counts["jobs_folder"]
        assert isinstance(layouts, dict) and isinstance(folders, dict)
        lines = ["Dry run: nothing was written." if self.dry_run else "One assessment, one resume and one suggestion record per job."]
        lines.append(store("Assessments", "assessments"))
        lines.append(store("Resumes", "resumes"))
        lines.append(f"Spacing files (.layout): {layouts['files']}: {layouts['kept']} kept with the job's resume, {layouts['superseded']} superseded")
        lines.append(store("Suggestion records", "suggestions"))
        lines.append(f"Jobs folder index: {folders['job_entries']} job entries" + (f" ({folders['to_add']} to add)" if self.dry_run and folders["to_add"] else ""))
        several = counts["jobs_held_by_several_roles"]
        if several:
            kept = ", ".join(f"role ...{profile_id[-6:]} {count}" for profile_id, count in sorted(self.winners.items()))
            rules = ", ".join(f"{rule.replace('_', ' ')} {self.rules[rule]}" for rule in RULE_ORDER if self.rules.get(rule))
            lines.append(f"Jobs held by more than one role: {several}; kept: {kept}; decided by: {rules}")
        if self.copies:
            postings = sum(len(item["superseded"]) for item in self.copies)  # type: ignore[arg-type]
            left = sum(len(copy["files"]) for item in self.copies for copy in item["superseded"])  # type: ignore[attr-defined,union-attr]
            lines.append(
                f"Jobs posted more than once with records under more than one posting: {len(self.copies)}; each keeps one posting's records "
                f"(its US posting, else the earliest, that has a resume, else an assessment); {postings} other posting(s), {left} file(s) superseded (left where they are)"
            )
        if self.ambiguous:
            shown = ", ".join(str(item["job"])[:_SHORT] for item in self.ambiguous)
            lines.append(
                f"Applied jobs with a stored resume under more than one role ({len(self.ambiguous)}): the application does not say which one was sent, "
                f"so every one of them is kept readable: {shown}"
            )
        if self.dry_run:
            lines.append(f"Files to copy: {files['to_copy']}; already there: {files['already']}")
        else:
            lines.append(f"Files copied: {files['copied']}; already there: {files['already']}")
        lines.append(f"Deleted: {counts['deleted']}")
        if self.dry_run and files["to_copy"]:
            lines.append(f"Copy them: {MIGRATE_COMMAND} --apply")
        elif not self.dry_run:
            lines.append(f"Each role's own folder was left as it was. The record of what was kept: {self.record}")
        return lines


def _index_entry(home_root: Path, project: str, plan: _JobPlan, index: dict) -> tuple[dict[str, str] | None, int]:  # type: ignore[type-arg]
    """Give ``index`` (the jobs folder's) the job-keyed entry of ``plan``'s kept resume: a copy of the entry its profile's
    resume has.  ``(the entry as the record names it or None, 1 when it was added)``.  Nothing is written here."""

    if RESUMES not in plan.kept:
        return None, 0
    source_key = resumes_folder.job_key(home_root, plan.kept[RESUMES].path)
    key = resumes_folder.job_store_key(project, plan.job)
    added = 0
    if key not in index and source_key in index:
        added = 1
        index[key] = {"dir": index[source_key]["dir"], "files": dict(index[source_key]["files"])}
    return ({"key": key, "from_key": source_key, "dir": str(index[key]["dir"])} if key in index else None), added


def _save_folder_index(home_root: Path, root: Path, index: dict) -> None:  # type: ignore[type-arg]
    if root.is_dir() and not root.is_symlink():
        with resumes_folder._folder_lock(root):
            jobs_folder._save_index(home_root, root, index)
    else:
        jobs_folder._save_index(home_root, root, index)


def _kept_copies(home_root: Path, project_dir: Path, jobs: set[str]) -> dict[str, str]:
    """``{job: the posting its job's records are kept under}`` for each of ``jobs`` that is a copy of a job posted more
    than once (``find_jobs.job_key``); a posting with no copy is left out.  Reads the company index and file names."""

    from .find_jobs.job_key import copies_of, kept_copy

    found: dict[str, str] = {}
    for job in sorted(jobs):
        if job in found:
            continue
        copies = copies_of(home_root, job)
        if len(copies) > 1:
            kept = kept_copy(project_dir, copies)
            found.update({copy: kept for copy in copies})
    return found


def _superseded_copy(home_root: Path, target: Path, store: str, path: Path) -> bool:
    """A profile's record at ``path`` is of a posting whose JOB keeps another copy's records (so it is not the job's)."""

    from .find_jobs.job_key import job_key

    item = _read_json(path)
    job = None if item is None else _job_of(store, item)
    return job is not None and job_key(home_root, target, job) != job


def _run(home_root: Path, project: str, applied: Mapping[str, str], *, write: bool) -> tuple[Migration, bool]:
    """Plan the copies and, with ``write``, make them.  One code path for both, so a dry run counts what a run does.

    Returns the report and whether anything is (or would be) written.
    """

    project_dir = home_root / "scout" / project
    held = {store: _scan(project_dir, store) for store in JOB_STORES}
    record = _load_record(home_root)
    recorded = dict(record.get(project, {}))
    before = json.dumps(recorded, sort_keys=True)

    root = jobs_folder.jobs_folder(home_root).path
    index = jobs_folder._load_index(home_root, root)
    index_before = json.dumps(index, sort_keys=True)

    # 0.1.11.9 RB2: a job posted more than once keeps ONE posting's records (``job_key.kept_copy``). What the profiles
    # hold under another copy is superseded: not planned, not copied, listed with the kept job.
    kept_as = _kept_copies(home_root, project_dir, {job for found in held.values() for job in found})
    superseded_copies: dict[str, dict[str, list[_Held]]] = {}
    for store in JOB_STORES:
        for job in [job for job in held[store] if kept_as.get(job, job) != job]:
            superseded_copies.setdefault(kept_as[job], {}).setdefault(job, []).extend(held[store].pop(job).values())
    copies_report = tuple(
        {
            "job": job_digest(kept),
            "superseded": [
                {"job": job_digest(copy), "files": sorted(_relative(home_root, item.path) for item in found)}
                for copy, found in sorted(others.items(), key=lambda pair: job_digest(pair[0]))
            ],
        }
        for kept, others in sorted(superseded_copies.items(), key=lambda pair: job_digest(pair[0]))
    )
    by_digest = {str(item["job"]): item["superseded"] for item in copies_report}

    jobs = sorted({job for found in held.values() for job in found}, key=job_digest)
    plans = [
        _plan_job(project_dir, job, {store: held[store].get(job, {}) for store in JOB_STORES}, applied.get(job), recorded.get(job_digest(job)))  # type: ignore[arg-type]
        for job in jobs
    ]

    copied = to_copy = already = folders_to_add = 0
    try:
        for plan in plans:
            wrote = False
            for copy in plan.copies:
                if _taken(copy.dest):
                    already += 1  # never replaced: an earlier run's copy, or what GigAI wrote for the job since
                    continue
                to_copy += 1
                if write:
                    _write_copy(project_dir, plan, copy)
                    copied += 1
                    wrote = True
            folder, added = _index_entry(home_root, project, plan, index)
            folders_to_add += added
            if plan.digest not in recorded or wrote:
                recorded[plan.digest] = _entry(home_root, plan, folder)
        for digest, others in by_digest.items():
            # The kept posting's entry says which other postings of the job were superseded (also when the kept
            # posting's own records are only in the per-job folder: written since, no profile holds them).
            entry = dict(recorded.get(digest) or {})  # type: ignore[call-overload]
            if entry.get("copies") != {"rule": COPIES_RULE, "superseded": others}:
                entry["copies"] = {"rule": COPIES_RULE, "superseded": others}
                recorded[digest] = entry
    finally:
        changed = bool(to_copy) or json.dumps(recorded, sort_keys=True) != before or json.dumps(index, sort_keys=True) != index_before
        if write:
            # Also after a failure part way: what was copied is recorded, so the next run copies the rest from the same role.
            if json.dumps(index, sort_keys=True) != index_before:
                _save_folder_index(home_root, root, index)
            if json.dumps(recorded, sort_keys=True) != before:
                _save_record(home_root, {**record, project: recorded})

    def store_counts(store: str) -> dict[str, int]:
        # Files and jobs as the profiles' folders hold them; a superseded copy's files are files of the kept job.
        of_copies = sum(1 for others in superseded_copies.values() for found in others.values() for item in found if item.path.parent.parent.name == store)
        files = sum(len(found) for found in held[store].values()) + of_copies
        return {"files": files, "jobs": len(held[store]), "kept": len(held[store]), "superseded": files - len(held[store])}

    def has_layout(item: _Held) -> bool:
        return item.path.with_suffix(LAYOUT_SUFFIX).is_file()

    several = [plan for plan in plans if len(plan.holders) > 1]
    job_prefix = f"{project}/{RESUMES}/{JOB_FOLDER}/"
    counts: dict[str, object] = {
        "assessments": store_counts(ASSESSMENTS),
        "resumes": store_counts(RESUMES),
        "layouts": {
            "files": sum(1 for found in held[RESUMES].values() for item in found.values() if has_layout(item)),
            "kept": sum(1 for plan in plans if RESUMES in plan.kept and has_layout(plan.kept[RESUMES])),
            "superseded": sum(1 for plan in plans for item in plan.superseded.get(RESUMES, ()) if has_layout(item)),
        },
        "suggestions": store_counts(SUGGESTIONS),
        "jobs_folder": {"job_entries": sum(1 for key in index if key.startswith(job_prefix)), "to_add": folders_to_add},
        "jobs": len(plans),
        "jobs_held_by_several_roles": len(several),
        "jobs_posted_more_than_once": {
            "jobs": len(copies_report),
            "superseded_postings": sum(len(item["superseded"]) for item in copies_report),  # type: ignore[arg-type]
            "superseded_files": sum(len(copy["files"]) for item in copies_report for copy in item["superseded"]),  # type: ignore[attr-defined,union-attr]
        },
        "ambiguous_applied_resume": sum(1 for plan in plans if plan.ambiguous),
        "files": {"to_copy": to_copy, "copied": copied, "already": already},
        "deleted": 0,
    }
    report = Migration(
        dry_run=not write,
        project_id=project,
        record=record_path(home_root),
        counts=counts,
        winners=dict(sorted(Counter(plan.winner for plan in several).items())),
        rules=dict(Counter(plan.rule for plan in several)),
        ambiguous=tuple(
            {"job": plan.digest, "kept_profile_id": plan.winner, "resumes": [_relative(home_root, item.path) for item in plan.ambiguous]}
            for plan in plans if plan.ambiguous
        ),
        decided=tuple({"job": plan.digest, "kept_profile_id": plan.winner, "rule": plan.rule, "profiles": list(plan.holders)} for plan in several),
        copies=copies_report,
    )
    return report, changed


def migrate(home_root: Path, target: Path, *, apply: bool = False, applied: Mapping[str, str] | None = None) -> Migration:
    """Give every job of this Scout project its one assessment, resume and suggestion record; without ``apply`` only count.

    A dry run writes nothing of the home's (no folder, no index, no record).  A run with nothing to copy and nothing new to
    record writes nothing either.  ``applied`` (``{job identity: when}``) is read from the committed application
    events when not given.  Raises ``JobStoreMigrationError``.
    """

    home_root = Path(home_root)
    try:
        project = project_id(home_root, Path(target))
    except Exception as exc:  # noqa: BLE001 - any refusal of the binding: there is no Scout project to migrate
        raise JobStoreMigrationError("target_unavailable", "this folder is not bound to a GigAI project") from exc
    if applied is None:
        applied = read_applied(home_root, Path(target))
    applied = {job: _moment(at) for job, at in applied.items()}
    try:
        plan, changed = _run(home_root, project, applied, write=False)
        if not apply or not changed:
            return plan if not apply else Migration(False, plan.project_id, plan.record, plan.counts, plan.winners, plan.rules, plan.ambiguous, plan.decided, plan.copies)
        return _run(home_root, project, applied, write=True)[0]
    except (OSError, ValueError) as exc:
        if isinstance(exc, JobStoreMigrationError):
            raise
        raise JobStoreMigrationError("stores_unwritable", "the job stores could not be copied") from exc


# --- one job, on a home that was not migrated (0.1.11.9 PJ2) ------------------------------------------------------
#
# Every store reads and writes a job's records in its per-job folder.  A home whose stores were never copied there
# still works: a READ of a job with nothing in the per-job folder answers the record the migration would keep (the
# same plan, ``_plan_job``), read where it is; the first WRITE of a job copies all of its records first (``adopt_job``:
# the migration's own copies, for that one job), and then writes in the per-job folder.  A profile's folder is
# never written, and a later ``migrate`` finds those files "already there".

#: How long the applications read for a fallback READ is reused (seconds).  A write reads them again.
_APPLIED_TTL = 5.0
_applied_memo: dict[tuple[str, str], tuple[float, dict[str, str]]] = {}


def _role_folders(store_dir: Path) -> list[Path]:
    try:
        return sorted(
            (folder for folder in store_dir.iterdir() if is_profile_folder(folder.name) and not folder.is_symlink() and folder.is_dir()),
            key=lambda item: item.name,
        )
    except OSError:
        return []


def _role_files(project_dir: Path, digest: str) -> dict[str, dict[str, Path]]:
    """``{store: {profile id: path}}`` of the records the profiles' folders hold under this job's file name.  No file is read."""

    found: dict[str, dict[str, Path]] = {}
    for store in JOB_STORES:
        held: dict[str, Path] = {}
        for folder in _role_folders(project_dir / store):
            path = folder / f"{digest}{RECORD_SUFFIX}"
            if path.is_file() and not path.is_symlink():
                held[folder.name] = path
        found[store] = held
    return found


def _applied_for(home_root: Path, target: Path, *, fresh: bool) -> Mapping[str, str]:
    import time

    key = (os.fspath(home_root), os.fspath(target))
    memo = _applied_memo.get(key)
    if not fresh and memo is not None and time.monotonic() - memo[0] < _APPLIED_TTL:
        return memo[1]
    applied = read_applied(home_root, target)
    _applied_memo[key] = (time.monotonic(), applied)
    return applied


def _plan_one(home_root: Path, target: Path, project_dir: Path, digest: str, *, write: bool) -> _JobPlan | None:
    """The migration's plan for the ONE job whose files are named ``digest``; ``None`` when no profile holds it.

    ``write``: the plan is about to be carried out, so the applications are read now and an unreadable read is an
    error.  A read-only caller takes the applications as they were a moment ago, and none when they cannot be read.
    """

    files = _role_files(project_dir, digest)
    held: dict[str, dict[str, _Held]] = {store: {} for store in JOB_STORES}
    job: str | None = None
    for store, found in files.items():
        for profile_id, path in found.items():
            read = _held(store, profile_id, path)
            if read is not None and job_digest(read[0]) == digest:
                job = read[0]
                held[store][profile_id] = read[1]
    if job is None:
        return None
    holders = {profile_id for found in held.values() for profile_id in found}
    recorded = None
    applied_at = None
    if len(holders) > 1:
        entry = _load_record(home_root).get(project_dir.name, {}).get(digest)
        recorded = entry if isinstance(entry, dict) else None
        if held[RESUMES] and not (recorded is not None and recorded.get("kept_profile_id") in holders):
            # The one rule that reads anything else: a job with an application and a stored resume.
            try:
                applied_at = _applied_for(home_root, target, fresh=write).get(job)
            except JobStoreMigrationError:
                if write:
                    raise
    return _plan_job(project_dir, job, held, applied_at, recorded)


def role_record(store_dir: Path, job_identity: str, *, home_root: Path, target: Path) -> Path | None:
    """Where a job's record of one store IS when the per-job folder does not hold it: the record the migration would
    keep for the job, in its profile's folder.  ``None``: no profile holds one.  Reads only; nothing is copied.

    ``store_dir`` is the store (``.../quick_assess``).
    """

    store_dir = Path(store_dir)
    digest = job_digest(job_identity)
    files = _role_files(store_dir.parent, digest)
    here = files.get(store_dir.name) or {}
    if not here:
        return None
    holders = {profile_id for found in files.values() for profile_id in found}
    if len(holders) == 1:
        kept = next(iter(here.values()))  # the one profile that holds the job: no record is opened to say so
    else:
        plan = _plan_one(Path(home_root), Path(target), store_dir.parent, digest, write=False)
        if plan is None or store_dir.name not in plan.kept:
            return None
        kept = plan.kept[store_dir.name].path
    return kept


def stored_record(store_dir: Path, job_identity: str, *, home_root: Path, target: Path) -> Path:
    """Where a job's record of one store IS: the per-job folder's file, or (a home that was not migrated, a job not
    written since) the record a profile's folder holds for it (``role_record``).  With neither, the per-job path.

    For READS.  A writer calls ``write_record`` and writes there.

    0.1.11.9 RB2: ``job_identity`` may be ANY copy of a job posted more than once (once per country); the record is
    the job's, under the one identity ``find_jobs.job_key.job_key`` names for all of them.
    """

    from .find_jobs.job_key import job_key

    job_identity = job_key(Path(home_root), Path(target), job_identity)
    path = job_store_dir(Path(store_dir)) / f"{job_digest(job_identity)}{RECORD_SUFFIX}"
    if path.is_symlink() or path.exists():
        return path
    held = role_record(store_dir, job_identity, home_root=home_root, target=target)
    return path if held is None else held


def write_record(store_dir: Path, job_identity: str, *, home_root: Path, target: Path) -> Path:
    """Where a job's record of one store is WRITTEN: always the per-job folder.  What the profiles' folders hold for
    the job is copied there first (``adopt_job``), so the writer reads the job's record as it was and writes over a copy.

    0.1.11.9 RB2: under the job's ONE identity (``find_jobs.job_key.job_key``), whichever copy of it is named."""

    from .find_jobs.job_key import job_key

    job_identity = job_key(Path(home_root), Path(target), job_identity)
    adopt_job(home_root, target, job_identity)
    return job_store_dir(Path(store_dir)) / f"{job_digest(job_identity)}{RECORD_SUFFIX}"


def role_records(store_dir: Path, *, home_root: Path, target: Path) -> list[Path]:
    """Every record of one store that only a profile's folder holds (``role_record`` of each such job): what a LIST of
    the store adds to the per-job folder's own files on a home that was not migrated.  ``[]`` on a migrated one, for
    the price of the folders' listings."""

    store_dir = Path(store_dir)
    try:
        ours = {path.name for path in job_store_dir(store_dir).glob(f"*{RECORD_SUFFIX}")}
    except OSError:
        ours = set()
    names: set[str] = set()
    for folder in _role_folders(store_dir):
        names.update(path.name for path in folder.glob(f"*{RECORD_SUFFIX}") if not path.name.endswith(PROPOSED_RESUME_SUFFIX))
    found: list[Path] = []
    for name in sorted(names - ours):
        digest = name[: -len(RECORD_SUFFIX)]
        files = _role_files(store_dir.parent, digest)
        here = files.get(store_dir.name) or {}
        holders = {profile_id for held in files.values() for profile_id in held}
        if len(holders) == 1 and here:
            found.append(next(iter(here.values())))
            continue
        plan = _plan_one(Path(home_root), Path(target), store_dir.parent, digest, write=False)
        if plan is not None and store_dir.name in plan.kept:
            found.append(plan.kept[store_dir.name].path)
    # 0.1.11.9 RB2: a record under another copy of a job posted more than once is superseded, not a job of its own.
    return [path for path in found if not _superseded_copy(Path(home_root), Path(target), store_dir.name, path)]


def adopt_job(home_root: Path, target: Path, job_identity: str) -> bool:
    """Before a job's first write: copy every record the profiles' folders hold for it into the per-job folders, as
    ``migrate`` would (the same plan, the same copies, the jobs folder entry, the record's entry).  ``True`` when a
    file was copied.  A job no profile holds, or one that is all there already, costs the folders' listings only.

    Nothing in a profile's folder is changed and nothing in a per-job folder is replaced.  Raises
    ``JobStoreMigrationError`` when a job several profiles hold cannot be decided (its applications cannot be read),
    and ``OSError`` when a copy cannot be written: the caller's write then does not happen either.
    """

    home_root, target = Path(home_root), Path(target)
    try:
        project = project_id(home_root, target)
    except Exception as exc:  # noqa: BLE001 - any refusal of the binding: there is no Scout project to write in
        raise JobStoreMigrationError("target_unavailable", "this folder is not bound to a GigAI project") from exc
    project_dir = home_root / "scout" / project
    digest = job_digest(job_identity)
    held = _role_files(project_dir, digest)
    # Nothing to copy: no profile holds the job, or every store a profile holds it in has the job's own record already
    # (a migrated home, a job written before). No record is opened and no application is read to find that out.
    if all(_taken(job_store_dir(project_dir / store) / f"{digest}{RECORD_SUFFIX}") for store, found in held.items() if found):
        return False
    plan = _plan_one(home_root, target, project_dir, digest, write=True)
    if plan is None:
        return False
    wrote = False
    for copy in plan.copies:
        if not _taken(copy.dest):
            _write_copy(project_dir, plan, copy)
            wrote = True
    if not wrote:
        return False
    root = jobs_folder.jobs_folder(home_root).path

    def entry() -> dict[str, str] | None:
        index = jobs_folder._load_index(home_root, root)
        folder, added = _index_entry(home_root, project, plan, index)
        if added:
            jobs_folder._save_index(home_root, root, index)
        return folder

    if root.is_dir() and not root.is_symlink():
        with resumes_folder._folder_lock(root):  # read and written under the folder's own lock: a save beside it is not lost
            folder = entry()
    else:
        folder = entry()
    record = _load_record(home_root)
    recorded = dict(record.get(project, {}))
    recorded[plan.digest] = _entry(home_root, plan, folder)
    _save_record(home_root, {**record, project: recorded})
    return True


__all__ = [
    "COPIES_RULE",
    "MIGRATE_COMMAND",
    "RECORD_SCHEMA",
    "RESPONSE_SCHEMA",
    "RULE_APPLICATION",
    "RULE_EDITED_RESUME",
    "RULE_NEWEST",
    "RULE_ONLY_HOLDER",
    "RULE_ONLY_STORED_RESUME",
    "RULE_ORDER",
    "JobStoreMigrationError",
    "Migration",
    "adopt_job",
    "choose",
    "migrate",
    "read_applied",
    "record_path",
    "role_record",
    "role_records",
    "stored_record",
    "write_record",
]
