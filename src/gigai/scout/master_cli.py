"""0.1.10.9 master P1: ``gigai scout resume master show | init --from FILE | history``.

The master resume is the one document that holds every role, bullet,
project and skill, with an id on every line and no contact data
(``master_resume`` is the format, ``master_store`` the journal record and
its revisions). These commands are local: no model, no network. Errors
name a line number and the rule, never a line's text.

P3 (``master_profiles_cli``): ``init`` without ``--from`` builds the master
from the resumes the profiles hold (the migration), ``selection status`` and
``selection refresh`` keep a profile's selection, and every write of the
master ends with ``after_master_write`` (stale views printed again, new
lines offered).

P2 adds ``gigai scout resume master selection show``: which lines of the
master a resume shows for a profile or for one job, and why each line is
picked or left out (``master_selection``: code only, fitted to the page
budget by measuring). It reads the master, a profile's titles and a posting
Scout already holds; it makes no request and the selection is not stored.

P6 (``master_edit_cli``): ``add | edit | remove`` change one line, entry or
skill by id; ``show --retired`` lists what was removed (retired, never lost)
and ``show --revision N`` reads an earlier revision.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..private_records import PrivateRecordError
from ..setup import default_home_root
from ..workpad import WorkpadError, committed_read_cache
from .target_resolution import ScoutTargetError
from .template import ScoutInstallError

_NO_MASTER = (
    "there is no master resume yet; make one from your profiles' resumes with `gigai scout resume master init`, "
    "or from a file with `gigai scout resume master init --from FILE`"
)


def _options(function):
    for option in reversed((
        click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False)),
        click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False)),
        click.option("--json", "as_json", is_flag=True),
    )):
        function = option(function)
    return function


def _fail(exc: Exception, *, as_json: bool, fallback: str = "scout_master_failed") -> None:
    """Report a refusal; a stale or blocked write also carries the revision the master is at now."""

    code = getattr(exc, "code", fallback)
    current = getattr(exc, "current", None)
    if as_json:
        error: dict[str, object] = {"code": code, "message": str(exc)}
        if current is not None:
            error["current"] = current.to_json()
        click.echo(json.dumps({"status": "error", "error": error}, sort_keys=True, separators=(",", ":")))
        raise click.exceptions.Exit(1)
    raise click.ClickException(str(exc))


def _errors() -> tuple[type[Exception], ...]:
    from .master_resume import MasterResumeError
    from .master_store import MasterStoreError

    return (MasterResumeError, MasterStoreError, ScoutTargetError, ScoutInstallError, PrivateRecordError, WorkpadError, OSError, ValueError)


def _target(target_value: Path | None, home_root: Path, *, as_json: bool) -> Path:
    # Imported here: scout_cli imports this module to register the group. Its resolver is the
    # one every `gigai scout ...` command shares (--target, else <home>/scout).
    from . import scout_cli

    return scout_cli._resolved_target(target_value, home_root, as_json=as_json).expanduser().resolve(strict=True)


def _n(count: object, noun: str, plural: str | None = None) -> str:
    return f"{count} {noun if count == 1 else plural or noun + 's'}"


def _size(counts: dict[str, object]) -> str:
    return f"{_n(counts['items'], 'line')}, {_n(counts['entries'], 'entry', 'entries')}, {_n(counts['skills'], 'skill')}"


def _emit(payload: dict[str, object]) -> None:
    click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))


@click.group("master")
def master_group() -> None:
    """Your master resume: every role, bullet, project and skill, an id on every line, no contact data.

    One per Scout home. Kept as one record with a revision per change; nothing is ever rewritten.
    """


@master_group.command("show")
@click.option("--section", "section", help="Only this section: summary, experience, skills, education, projects or other.")
@click.option("--entry", "entry_id", help="Only this role, project or school (its id) and its lines.")
@click.option("--revision", "revision", type=click.IntRange(min=1), help="An earlier revision instead of the current one (history lists them).")
@click.option("--retired", "retired", is_flag=True, help="Instead: the lines and entries the master no longer holds, each with its text and the revision that still holds it.")
@_options
def master_show_command(
    section: str | None, entry_id: str | None, revision: int | None, retired: bool, target_value: Path | None, home_value: Path | None, as_json: bool,
) -> None:
    """Show the master: every entry and line with its id, tags, evidence strength and who wrote it."""

    from .master_edit import entry_json, item_json, provenance
    from .master_edit_cli import show_retired
    from .master_file import file_status, status_line
    from .master_resume import MASTER_FORMAT, SECTION_HEADINGS
    from .master_store import MasterStoreError, load_master

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        wanted = section.strip().lower() if section is not None else None
        if wanted is not None and wanted not in SECTION_HEADINGS:
            raise MasterStoreError("master_section_unknown", "--section is one of " + ", ".join(SECTION_HEADINGS))
        if retired:
            if wanted is not None or entry_id is not None or revision is not None:
                raise MasterStoreError("master_option_invalid", "--retired lists every retired line: it takes no --section, --entry or --revision")
            show_retired(home_root, target, as_json=as_json)
            return
        with committed_read_cache():
            stored = load_master(home_root=home_root, target=target, revision=revision)
        if stored is None:
            raise MasterStoreError("master_not_found", _NO_MASTER)
        master = stored.master
        if entry_id is not None and entry_id not in master.entries:
            raise MasterStoreError("master_entry_not_found", f"the master has no entry {entry_id!r}")
        # P6: who wrote a line's text and where its evidence came from (null where that is not known).
        known = provenance(home_root=home_root, target=target, master=master)
        # P8: how master.md in the resumes folder stands against the stored master (the current one, also for --revision).
        file = file_status(home_root, stored if revision is None else load_master(home_root=home_root, target=target))
    except _errors() as exc:
        _fail(exc, as_json=as_json)
        return

    def shown(item_section: str, item_entry: str | None) -> bool:
        return (wanted is None or item_section == wanted) and (entry_id is None or item_entry == entry_id)

    entries = [entry for entry in master.entries.values() if shown(entry.section, entry.id)]
    items = [item for item in master.items.values() if shown(item.section, item.entry_id)]
    if as_json:
        _emit({
            "ok": True,
            "master": {
                **stored.revision.to_json(), "record_id": stored.record_id, "format": MASTER_FORMAT,
                "sections": list(master.sections), "counts": master.counts(),
                "revisions": stored.revisions,
                "entries": [entry_json(entry, known) for entry in entries], "items": [item_json(item, known) for item in items],
            },
            "file": file,
        })
        return
    counts = master.counts()
    earlier = f" of {stored.revisions}, not the current one" if stored.revision.revision != stored.revisions else ""
    click.echo(
        f"Master resume, revision {stored.revision.revision}{earlier} (written by {stored.revision.written_by}, {stored.revision.updated_at}): "
        f"{_size(counts)}."
    )

    def line(item) -> str:  # noqa: ANN001 - a MasterItem
        tags = "".join(f" #{tag}" for tag in item.tags)
        kept = known.get(item.id, {})
        said = [str(kept[key]) for key in ("written_by", "source") if kept.get(key)]
        who = f"  ({': '.join(said) if kept.get('written_by') else 'source: ' + said[0]})" if said else ""
        for skill in kept.get("skills", ()):  # a Skills line: the skills a write added, by who added them
            who += f"  ({skill['written_by']}: {skill['name']})"
        return f"{item.id}  [{item.strength}]  {item.text}{tags}{who}"

    for name in master.sections:
        if not any(entry.section == name for entry in entries) and not any(item.section == name for item in items):
            continue
        click.echo(f"\n## {name.capitalize()}")
        for entry in (entry for entry in entries if entry.section == name):
            click.echo(f"  {entry.id}  {entry.heading}" + "".join(f" / {subline}" for subline in entry.sublines))
            for item in (item for item in items if item.entry_id == entry.id):
                click.echo(f"    {line(item)}")
        for item in (item for item in items if item.section == name and item.entry_id is None):
            click.echo(f"  {line(item)}")
    said = status_line(file)
    if said:
        click.echo(f"\n{said}")


@master_group.command("init")
@click.option("--from", "source", type=click.Path(path_type=Path, dir_okay=False), help="The resume markdown to store as the master (.md, .markdown, .txt).")
@click.option(
    "--revision", "revision", type=click.IntRange(min=0),
    help="The revision you read (show --json): needed to replace an existing master; refused with revision_conflict when it changed since.",
)
@click.option(
    "--as", "--actor", "actor", type=click.Choice(["operator", "agent"]), default="operator", show_default=True,
    help="Who is writing: recorded on the revision. An agent passes --as agent.",
)
@click.option(
    "--answer", "answers", multiple=True,
    help="Without --from: the answer to one question of the merge, QUESTION_ID=a, =b or =both (repeatable).",
)
@click.option("--dry-run", "dry_run", is_flag=True, help="Without --from: show what the merge would store; write nothing.")
@_options
def master_init_command(
    source: Path | None, revision: int | None, actor: str, answers: tuple[str, ...], dry_run: bool,
    target_value: Path | None, home_value: Path | None, as_json: bool,
) -> None:
    """Make the master resume: from the resumes your profiles hold, or from FILE.

    Without --from: the resumes of your profiles are merged into one master
    (the union of their lines; the same line and near-duplicates are folded,
    the newer wording kept). Two versions of a line that state different
    numbers are ASKED about: nothing is written until each has an --answer.
    Every profile's first selection is its own resume, which is not
    rewritten, so nothing already assessed or tailored goes stale.

    With --from FILE: FILE is stored as the master. It is resume markdown in
    GigAI's format (## Summary, ## Experience with ### entries and - bullets,
    ## Skills, ## Education, ## Projects, ## Other); contact lines are
    removed and a line without an id gets one. With a master already
    stored, pass --revision N (the revision you read): the result becomes
    revision N+1.
    """

    from . import scout_cli
    from .master_file import write_line
    from .master_profiles_cli import after_master_write, echo_after_master_write, run_migration
    from .master_store import MasterStoreError, import_master

    if source is None:
        run_migration(
            answers=answers, dry_run=dry_run, revision=revision, actor=actor, target_value=target_value, home_value=home_value, as_json=as_json,
        )
        return
    home_root = home_value or default_home_root()
    # As `resume add`: works as the very first command on a fresh home.
    scout_cli._ensure_gigai_settings(home_root, as_json=as_json)
    try:
        target = _target(target_value, home_root, as_json=as_json)
        if answers or dry_run:
            raise MasterStoreError("master_option_invalid", "--answer and --dry-run belong to `master init` without --from (the merge of your profiles' resumes)")
        installed = scout_cli.install_scout(home_root=home_root, requested_target=target)
        scout_cli.write_starter_find_jobs_config(target)
        result = import_master(home_root=home_root, target=target, source=source, actor=actor, revision=revision)
    except _errors() as exc:
        _fail(exc, as_json=as_json)
        return
    master = result.stored.master
    counts = master.counts()
    removed = result.contact_removed.to_json()
    # P3: a profile that shows an edited or retired line gets its resume printed again; new lines are only offered.
    profiles = after_master_write(home_root, target) if result.status != "unchanged" else {"synced": [], "offers": []}
    payload = {
        "ok": True,
        "status": result.status,
        "scout_installed": installed.bound or installed.approved or installed.activated,
        "master": {**result.stored.revision.to_json(), "record_id": result.stored.record_id, "counts": counts},
        "ids_assigned": result.ids_assigned,
        "ids_restored": result.ids_restored,
        "changes": result.change.to_json(),
        # What the privacy strip took out (kinds and line numbers, never a value), or null.
        "contact_removed": removed,
        "profiles": profiles,
        # P8: where the revision went in the resumes folder (null when nothing was written).
        "file": dict(result.file) if result.file is not None else None,
    }
    if as_json:
        _emit(payload)
        return
    number = result.stored.revision.revision
    if result.status == "unchanged":
        click.echo(f"The master resume is unchanged (revision {number}): the file holds what is stored.")
    else:
        click.echo(
            f"Master resume stored as revision {number}: {_size(counts)}; "
            f"{_n(result.ids_assigned, 'new id')}, {result.change.added} added, {result.change.removed} removed, {result.change.changed} changed."
        )
    if removed is not None:
        where = ", ".join(f"line {line}: {kind.replace('_', ' ')}" for kind, line in result.contact_removed.lines)
        click.echo(f"{removed['message']} Not imported: {where}.")
    said = write_line(result.file)
    if said:
        click.echo(said)
    echo_after_master_write(profiles)
    click.echo("Next: `gigai scout resume master show`.")


@master_group.command("history")
@_options
def master_history_command(target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """List the master's revisions, newest first: who wrote each, when, and what it changed."""

    from .master_store import MasterStoreError, master_history

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        with committed_read_cache():
            history = master_history(home_root=home_root, target=target)
        if not history:
            raise MasterStoreError("master_not_found", _NO_MASTER)
    except _errors() as exc:
        _fail(exc, as_json=as_json)
        return
    newest_first = list(reversed(history))
    if as_json:
        _emit({"ok": True, "revision": history[-1].revision.revision, "revisions": [entry.to_json() for entry in newest_first]})
        return
    click.echo(f"Master resume: {len(history)} revision{'' if len(history) == 1 else 's'}.")
    for entry in newest_first:
        click.echo(
            f"  {entry.revision.revision}  {entry.revision.updated_at}  {entry.revision.written_by}  {_n(entry.items, 'line')}, {_n(entry.entries, 'entry', 'entries')} "
            f"(+{entry.change.added} -{entry.change.removed} ~{entry.change.changed})  {entry.revision.revision_id}"
        )


# --- P2: the selection ------------------------------------------------------------------------


class _SelectionInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _selection_profile(profile_id: str | None, titles: tuple[str, ...], focus: tuple[str, ...], *, home_root: Path, target: Path):
    """The profile a selection is made for: ``--profile-title`` (no stored profile needed), else a stored profile's titles.

    ``None`` when nothing names one and the home has no selected profile: the selection then has no profile prior."""

    from ..workpad import resolve_workpad
    from . import profile_records
    from .master_selection import SelectionProfile

    if titles or focus:
        if profile_id is not None:
            raise _SelectionInputError("profile_input_invalid", "pass --profile ID or --profile-title/--focus, not both")
        return SelectionProfile(titles=titles, focus_tags=focus, label=titles[0] if titles else "")
    resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
    if profile_id is None:
        record = profile_records.selected_profile(resolved, home_root=home_root, target=target)
        if record is None:
            return None
    else:
        record = next((item for item in profile_records.list_profiles(resolved) if item.profile_id == profile_id), None)
        if record is None:
            raise _SelectionInputError("profile_not_found", f"profile {profile_id!r} is not stored in this Scout home")
    return SelectionProfile(titles=tuple(record.titles), profile_id=record.profile_id, label=record.label)


def _selection_job(
    job_url: str | None, job_text_file: str | None, title: str | None, company: str | None, *, home_root: Path, target: Path,
):  # noqa: ANN202 - (SelectionPosting | None, dict | None)
    """``(the posting, what the output says about it)``, read from what Scout already holds; no request is made.

    ``(None, None)`` when no job is named."""

    from ..canonical import digest_imported_bytes
    from .master_selection import SelectionPosting

    if job_url and job_text_file:
        raise _SelectionInputError("job_input_invalid", "pass at most one of --job-url or --job-text")
    if job_text_file:
        if job_text_file == "-":
            text = click.get_text_stream("stdin").read()
        else:
            path = Path(job_text_file).expanduser()
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise _SelectionInputError("job_input_invalid", f"--job-text: cannot read {path} ({getattr(exc, 'strerror', None) or type(exc).__name__})") from None
        if not text.strip():
            raise _SelectionInputError("job_input_invalid", "--job-text: the posting text is empty")
        digest = digest_imported_bytes(text.encode("utf-8"))
        posting = SelectionPosting(title=(title or "").strip(), text=text, company=(company or "").strip())
        return posting, {"title": posting.title, "company": posting.company, "location": "", "job_identity": f"text:{digest}", "text_sha256": digest, "source": "text"}
    if not job_url:
        if title or company:
            raise _SelectionInputError("job_input_invalid", "--title and --company go with --job-url or --job-text")
        return None, None

    from .find_jobs.job_source import index_posting
    from .find_jobs.job_state import normalize_job_identity
    from .quick_assess import find_quick_assessment_by_job_identity

    identity = normalize_job_identity(job_url)  # a URL, or a pasted posting's text:sha256:<hex> identity
    source = "index"
    job = index_posting(home_root, target, identity)  # no client: the index's stored text or nothing
    if job is None:
        stored = find_quick_assessment_by_job_identity(home_root, target, identity)
        if stored is not None and (stored.posting_text or "").strip():
            from dataclasses import replace

            job, source = replace(stored.job, text=stored.posting_text), "assessment"
    if job is None or not job.text.strip():
        raise _SelectionInputError(
            "job_not_stored",
            "Scout holds no posting text for that URL (this command makes no request); pass the posting with --job-text FILE --title TITLE",
        )
    posting = SelectionPosting(title=(title or job.title).strip(), text=job.text, company=(company or job.company).strip(), location=job.location)
    return posting, {
        "title": posting.title, "company": posting.company, "location": posting.location, "job_identity": job.job_identity,
        "text_sha256": job.text_sha256, "source": source,
    }


def _selection_heading(profile, job: dict[str, object] | None) -> str:  # noqa: ANN001 - a SelectionProfile or None
    who = f"profile {profile.label or profile.profile_id}" if profile is not None and (profile.label or profile.profile_id) else "no profile"
    if job is None:
        return f"{who}, no posting (the profile's standing pick)"
    title = job["title"] or "a posting with no title"
    return f"{title}{' at ' + str(job['company']) if job['company'] else ''}, {who}"


def _echo_selection(master, selected, heading: str, revision: int) -> None:  # noqa: ANN001 - a Master and a Selected
    reasons = {line.id: line for line in selected.lines}
    picked = sum(line.picked for line in selected.lines)
    left = len(selected.lines) - picked
    fit = (
        f"{_n(selected.pages, 'page')} ({selected.pages_before_fit} before the fit), the last page {round(selected.last_page_fill * 100)}% full"
        if selected.fits
        else f"DOES NOT FIT {_n(selected.max_pages, 'page')}: {_n(selected.pages, 'page')} after every cut the rules allow"
    )
    click.echo(f"Selection for {heading}: {fit}.")
    click.echo(
        f"Picked {_n(picked, 'line')} and {_n(len(selected.skills), 'skill')}; left out {_n(left, 'line')} and "
        f"{_n(sum(not skill.picked for skill in selected.skill_reasons), 'skill')}; {_n(len(selected.cut_for_length), 'cut')} for length. "
        f"Selector {selected.selector_version}, master revision {revision}."
    )

    def show(item_ids) -> None:  # noqa: ANN001 - ids in the order to print
        for item_id in item_ids:
            line = reasons[item_id]
            click.echo(f"    {'+' if line.picked else '-'} {item_id}  {master.items[item_id].text}")
            click.echo(f"          {line.reason}")

    def lines_section(name: str, shown: tuple[str, ...]) -> None:
        items = master.in_section(name)
        if not items:
            return
        click.echo(f"\n## {name.capitalize()}: {len(shown)} picked, {len(items) - len(shown)} left out")
        show((*shown, *(item.id for item in items if item.id not in shown)))

    def entries_section(name: str) -> None:
        entries = [entry for entry in master.entries_in(name)]
        if not entries:
            return
        click.echo(f"\n## {name.capitalize()}")
        shown_first = [entry for entry_id in selected.entries for entry in entries if entry.id == entry_id]
        for entry in (*shown_first, *(entry for entry in entries if entry.id not in selected.entries)):
            shown = selected.entries.get(entry.id)
            state = f"{len(shown)} picked, {len(entry.bullets) - len(shown)} left out" if shown is not None else "not shown"
            click.echo(f"  {entry.id}  {entry.heading}" + "".join(f" / {subline}" for subline in entry.sublines) + f": {state}")
            show((*(shown or ()), *(bullet for bullet in entry.bullets if bullet not in (shown or ()))))

    lines_section("summary", selected.summary)
    entries_section("experience")
    entries_section("projects")
    if selected.skill_reasons:
        click.echo(f"\n## Skills: {len(selected.skills)} picked, {sum(not skill.picked for skill in selected.skill_reasons)} left out")
        for code in dict.fromkeys(skill.code for skill in selected.skill_reasons):
            group = [skill for skill in selected.skill_reasons if skill.code == code]
            click.echo(f"    {'+' if group[0].picked else '-'} {', '.join(skill.name for skill in group)}")
            click.echo(f"          {group[0].reason}")
    entries_section("education")
    lines_section("other", selected.other)
    if selected.keywords is not None and selected.coverage.get("must_missing"):
        click.echo("\nMust-haves of the posting this resume does not name: " + ", ".join(selected.coverage["must_missing"]) + ".")
    if selected.conflicts:
        click.echo("\nDid not fit although the rules say it stays:")
        for conflict in selected.conflicts:
            what = f" {conflict.requirement}:" if conflict.requirement else ""
            click.echo(f"  -{what} {conflict.reason}" + (f" ({', '.join(conflict.ids)})" if conflict.ids else ""))


@master_group.group("selection")
def selection_group() -> None:
    """Which lines of the master a resume shows, and why: picked by code, fitted to 2 pages, no model."""


@selection_group.command("show")
@click.option("--profile", "profile_id", help="A stored Scout profile ID: its titles steer the pick (default: the selected profile).")
@click.option("--profile-title", "profile_titles", multiple=True, help="A title to select for instead of a stored profile (repeatable).")
@click.option("--focus", "focus_tags", multiple=True, help="With --profile-title: a master tag this profile is about (repeatable).")
@click.option("--job-url", "job_url", help="A posting Scout already holds, by its URL or job identity (the index or a stored assessment); no request is made.")
@click.option("--job-text", "job_text_file", help="File with the posting text (or - for stdin).")
@click.option("--title", "title", help="Job title (pasted text has none; overrides a stored posting's).")
@click.option("--company", "company", help="Company (pasted text has none; overrides a stored posting's).")
@click.option("--evidence", "evidence", is_flag=True, help="Show the evidence view instead: the lines most relevant to the job, up to the assess prompt's cap. Needs a job.")
@click.option("--markdown", "markdown_only", is_flag=True, help="Print only the selection's resume markdown.")
@_options
def selection_show_command(
    profile_id: str | None, profile_titles: tuple[str, ...], focus_tags: tuple[str, ...], job_url: str | None, job_text_file: str | None,
    title: str | None, company: str | None, evidence: bool, markdown_only: bool,
    target_value: Path | None, home_value: Path | None, as_json: bool,
) -> None:
    """Show what a resume made from the master would show: Picked / Left out, each line with its reason.

    Without a job: the profile's standing pick. With --job-url or --job-text:
    the pick for that posting, made from the WHOLE master. Recent roles
    always appear; for length the oldest roles are shortened, then dropped,
    first; nothing is reworded. The selection is not stored.
    """

    from .find_jobs.contracts import FindJobsContractError
    from .master_selection import SelectionProfile, evidence_view, select
    from .master_store import MasterStoreError, load_master
    from .profile_records import ProfileRecordError
    from .quick_assess import QuickAssessError

    home_root = home_value or default_home_root()
    selected = view = None
    try:
        target = _target(target_value, home_root, as_json=as_json)
        with committed_read_cache():
            stored = load_master(home_root=home_root, target=target)
        if stored is None:
            raise MasterStoreError("master_not_found", _NO_MASTER)
        # Outside the read cache: resolving the selected profile is what every Scout command does, and on a
        # home that has a resume but no profile yet it writes the default one.
        profile = _selection_profile(
            profile_id, tuple(item.strip() for item in profile_titles if item.strip()), tuple(item.strip() for item in focus_tags if item.strip()),
            home_root=home_root, target=target,
        )
        posting, job = _selection_job(job_url, job_text_file, title, company, home_root=home_root, target=target)
        master = stored.master
        if evidence:
            if posting is None:
                raise _SelectionInputError("job_input_invalid", "--evidence needs a job: pass --job-url or --job-text")
            view = evidence_view(master, profile or SelectionProfile(), posting)
        else:
            if posting is not None and job is not None and profile is not None and profile.profile_id is not None:
                # 0110-10-15: the job's stored assessment for this profile says which lines evidence each requirement.
                from dataclasses import replace

                from .assess_master import stored_citations

                posting = replace(posting, cited=stored_citations(home_root, target, master, profile.profile_id, str(job["job_identity"])))
            selected = select(master, profile or SelectionProfile(), posting)
    except (*_errors(), _SelectionInputError, ProfileRecordError, QuickAssessError, FindJobsContractError) as exc:
        _fail(exc, as_json=as_json)
        return
    context = {
        "master": {**stored.revision.to_json(), "record_id": stored.record_id},
        "profile": profile.to_json() if profile is not None else None,
        "job": job,
    }
    heading = _selection_heading(profile, job)
    if view is not None:
        if as_json:
            _emit({"ok": True, "evidence": {**view.to_json(), **context}})
            return
        if not markdown_only:
            click.echo(
                f"Evidence view for {heading}: {view.bullets} of {view.bullets_total} bullets, {view.chars} characters "
                f"(cap {view.cap}){'' if view.within_cap else ': OVER THE CAP before any bullet'}. "
                f"Selector {view.selector_version}, master revision {stored.revision.revision}.\n"
            )
        click.echo(view.markdown, nl=False)
        return
    assert selected is not None
    if as_json:
        _emit({"ok": True, "selection": {**selected.to_json(master), **context, "markdown": selected.markdown}})
    elif markdown_only:
        click.echo(selected.markdown, nl=False)
    else:
        _echo_selection(master, selected, heading, stored.revision.revision)


# P3: `selection status` and `selection refresh` register themselves on the group above.
from . import master_profiles_cli as _master_profiles_cli  # noqa: E402,F401
# P6: `add`, `edit` and `remove` do the same.
from . import master_edit_cli as _master_edit_cli  # noqa: E402,F401
# P8: `sync` too.
from . import master_file_cli as _master_file_cli  # noqa: E402,F401

__all__ = [
    "master_group", "master_history_command", "master_init_command", "master_show_command", "selection_group", "selection_show_command",
]
