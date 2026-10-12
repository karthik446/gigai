"""0.1.11.10 Part A: ``gigai scout learning list | show | open | import`` (the store is ``learning_store``).

A learning pathway is a course for one role, as static HTML the Scout server serves. These commands read the store
(``list``, ``show``), say where the running server serves a course and open it (``open``), and import a course
folder somebody already generated (``import``). None makes a model call or a network request.

0.1.11.10 Part B (G1): ``gigai scout learning corpus "ROLE"`` reads the postings stored for a role and counts what
they ask for (``learning_corpus``). It is the one command here that calls the model, so it asks first with the
estimate; it stores nothing.

0.1.11.10 Part B (G5): ``gigai scout learning generate "ROLE"`` generates the course of a role (``learning_job``:
the same job the Scout server runs, here in the foreground with plain progress lines, under the same
one-course-at-a-time claim). It asks first with the estimate (default no; ``--yes`` approves). ``cancel ID`` asks a
running job to stop, whichever process runs it; ``resume ID`` goes on with one that failed or was interrupted.

0.1.11.10 shipped lessons (slice 3): ``list`` also shows the courses that ship inside GigAI (marked ``shipped``,
first), and ``show`` and ``open`` take a shipped course's ROLE_KEY where they take a pathway ID (``learning_shipped_store``).
A shipped course is not generated here, so ``cancel`` and ``resume`` refuse a role key.

0.1.11.10 shipped lessons (slices 4b and 5b): ``pathway ROLE_KEY`` builds, or builds again, the one page of a
shipped course that is yours (``learning_pathway_build``): what your own stored postings ask for, and what your
resume already shows of each lesson. The resume is always the reference, so the build always compares it (about
one model call): the command says what that takes and sends and asks first (default no; ``--yes`` approves).

``--json`` prints one JSON object (what the API route answers, through the same outbound check) and an error is
``{"status": "error", "error": {"code", "message"}}`` with exit code 1, like the other ``gigai scout`` commands.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..setup import default_home_root
from . import learning_shipped_store, learning_store
from .learning_shipped_store import ShippedCourse
from .learning_store import LearningError, Pathway

_START_HINT = "Start it with `gigai scout run`, then run this command again."


def _emit(payload: dict[str, object]) -> None:
    from .outbound_check import redact_payload

    click.echo(json.dumps(redact_payload(payload), sort_keys=True, separators=(",", ":")))


def _fail(code: str, message: str, *, as_json: bool) -> None:
    if as_json:
        _emit({"status": "error", "error": {"code": code, "message": message}})
        raise click.exceptions.Exit(1)
    raise click.ClickException(message)


def _errors() -> tuple[type[Exception], ...]:
    from ..workpad import WorkpadError
    from .target_resolution import ScoutTargetError

    return (LearningError, ScoutTargetError, WorkpadError, OSError, ValueError)


def _target(target_value: Path | None, home_root: Path, *, as_json: bool) -> Path:
    # Imported here: gigai.cli imports both modules; the resolver is the one every `gigai scout ...` command shares.
    from . import scout_cli

    return scout_cli._resolved_target(target_value, home_root, as_json=as_json).expanduser().resolve(strict=True)


def _size(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.1f} MB" if size_bytes >= 1024 * 1024 else f"{max(1, round(size_bytes / 1024))} KB"


def _course_words(pathway: Pathway) -> str:
    if pathway.course is None:
        return "no course yet"
    lessons = pathway.course.lessons
    return f"{lessons} lesson{'' if lessons == 1 else 's'}, {_size(pathway.course.size_bytes)}"


def _cost_words(cost: object) -> str:
    names = (("cli_model_calls", "model calls"), ("worker_minutes", "worker minutes"), ("web_fetches", "page fetches"), ("web_searches", "web searches"))
    known = [f"{cost[key]} {label}" for key, label in names if isinstance(cost, dict) and cost.get(key) is not None]
    note = cost.get("note") if isinstance(cost, dict) else None
    return ", ".join([*known, *([str(note)] if note else [])]) or "not recorded"


def _live(home_root: Path, target: Path, pathway: Pathway) -> str:
    """The status a reader sees: ``interrupted`` for a generation no live process runs (``learning_job.live_status``)."""

    from .find_jobs.api.learning import _live_status

    return _live_status(home_root, target, pathway)


def _progress_words(pathway: Pathway) -> str | None:
    """How far a generation is, in one line; ``None`` for a pathway with no generation (an imported course)."""

    progress = pathway.progress
    if progress is None:
        return None
    steps = [item for item in progress.get("steps", []) if isinstance(item, dict)]  # type: ignore[union-attr]
    done = sum(1 for item in steps if item.get("status") in ("done", "skipped"))
    current = next((item for item in steps if item.get("id") == progress.get("step")), None)  # type: ignore[union-attr]
    words = f"{done} of {len(steps)} steps, {progress.get('calls', 0)} model calls, {progress.get('fetches', 0)} page fetches"  # type: ignore[union-attr]
    if current is not None and current.get("detail"):
        words += f"; {current['id']}: {current['detail']}"
    dropped = progress.get("dropped") or []  # type: ignore[union-attr]
    if dropped:
        words += f"; {len(dropped)} not included"  # type: ignore[arg-type]
    return words


_LOCAL_PAGE_WORDS = {
    learning_shipped_store.PAGE_NOT_BUILT: "your pathway page is not built yet",
    learning_shipped_store.PAGE_BUILT: "your pathway page was built on {day}",
    learning_shipped_store.PAGE_STALE: "your pathway page was built on {day} and is out of date",
}
#: Why a built page is out of date, by ``local_page.reasons`` (``learning_pathway_local.state``).
_STALE_WORDS = {
    "gigai_version": "GigAI was updated since",
    "catalog": "the course changed since",
    "master": "your resume changed since",
    "postings": "your stored postings changed since",
}


def _local_page_words(local_page: object) -> str:
    """The state of a shipped course's locally built pathway page, in words; a stale page says why."""

    state = local_page.get("state") if isinstance(local_page, dict) else None
    built_at = local_page.get("built_at") if isinstance(local_page, dict) else None
    words = _LOCAL_PAGE_WORDS.get(str(state), _LOCAL_PAGE_WORDS[learning_shipped_store.PAGE_NOT_BUILT]).format(day=str(built_at or "")[:10])
    reasons = local_page.get("reasons") if isinstance(local_page, dict) else None
    why = [_STALE_WORDS[reason] for reason in (reasons if isinstance(reasons, list) else []) if reason in _STALE_WORDS]
    if state == learning_shipped_store.PAGE_STALE and why:
        words += ": " + ", ".join(why)
    build = local_page.get("build") if isinstance(local_page, dict) else None
    if isinstance(build, dict) and build.get("status") == "running":
        words += "; it is being built now"
    elif isinstance(build, dict) and build.get("status") == "failed":
        words += f"; the last build did not finish ({build.get('error_code')})"
    return words


def _lessons_words(lessons: int) -> str:
    return f"{lessons} lesson{'' if lessons == 1 else 's'}"


def _shipped(pathway_id: str) -> ShippedCourse | None:
    """The course shipped with GigAI that ``pathway_id`` names as a role key, or ``None``."""

    return learning_shipped_store.get_course(pathway_id)


def _refuse_shipped(pathway_id: str, doing: str) -> None:
    if _shipped(pathway_id) is not None:
        raise LearningError("pathway_not_found", f"{pathway_id} is a course shipped with GigAI: it is not generated here, so there is nothing to {doing}")


def _pathway(home_root: Path, target: Path, pathway_id: str) -> Pathway:
    pathway = learning_store.get_pathway(home_root, target, pathway_id)
    if pathway is None:
        raise LearningError("pathway_not_found", f"there is no learning pathway {pathway_id!r}; `gigai scout learning list` lists them")
    return pathway


_home_option = click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False))
_target_option = click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False))
_json_option = click.option("--json", "as_json", is_flag=True)


@click.group("learning")
def learning_group() -> None:
    """Learning pathways: a course for one role, as static HTML the Scout server serves."""


@learning_group.command("list")
@_home_option
@_target_option
@_json_option
def learning_list_command(home_value: Path | None, target_value: Path | None, as_json: bool) -> None:
    """List the learning pathways, the newest request first. Reads the store; no model call."""

    from . import learning_job
    from .find_jobs.api.learning import pathways_response, shipped_summary

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        if as_json:
            _emit(pathways_response(home_root, target))
            return
        pathways = learning_store.list_pathways(home_root, target)
        courses = learning_shipped_store.list_courses()
        learning = learning_store.learning_dir(home_root, target) if courses else None
        shipped = [shipped_summary(home_root, target, course, learning=learning) for course in courses]
    except _errors() as exc:
        _fail(getattr(exc, "code", "scout_learning_failed"), str(exc), as_json=as_json)
        return
    if not pathways and not shipped:
        click.echo("No learning pathways yet. Import a course folder: gigai scout learning import DIR --role \"ROLE\"")
        return
    for row in shipped:
        lessons = _lessons_words(int(row["course"]["lessons"]))  # type: ignore[index]
        click.echo(f"{row['id']}  shipped  {row['role_text']}  ({lessons})  [shipped with GigAI]  {_local_page_words(row['local_page'])}")
    if shipped:
        click.echo(learning_job.MORE_COURSES_NOTE)
    for pathway in pathways:
        line = f"{_live(home_root, target, pathway)}"
        line = f"{pathway.id}  {line:<7}  {pathway.requested_at[:10]}  {pathway.role_text}  ({_course_words(pathway)})  [{pathway.source}]"
        if pathway.error:
            line += f"  error: {pathway.error}"
        click.echo(line)
    click.echo("Open one: gigai scout learning open ID" + (" (a shipped course: its ROLE_KEY, the first word of its line)" if shipped else ""))


@learning_group.command("show")
@click.argument("pathway_id")
@_home_option
@_target_option
@_json_option
def learning_show_command(pathway_id: str, home_value: Path | None, target_value: Path | None, as_json: bool) -> None:
    """Show one learning pathway: what was asked, how far it is, what it cost. Reads the store; no model call.

    PATHWAY_ID is a pathway id, or the ROLE_KEY of a course shipped with GigAI.
    """

    from .find_jobs.api.learning import pathway_response, shipped_response

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        course = _shipped(pathway_id)
        if course is not None:
            shown = shipped_response(home_root, target, course)
            if as_json:
                _emit(shown)
                return
            row = shown["pathway"]
            assert isinstance(row, dict)
            click.echo("\n".join([
                f"{course.role_key}  {course.role}",
                "Status: done",
                f"Course: {_lessons_words(course.lessons)}",
                "Source: shipped with GigAI (the lessons and practice pages are the same for everyone)",
                f"Your pathway page: {_local_page_words(row['local_page'])}",
                f"Build it or build it again: gigai scout learning pathway {course.role_key}",
                f"Open it: gigai scout learning open {course.role_key}",
            ]))
            return
        pathway = _pathway(home_root, target, pathway_id)
        status = _live(home_root, target, pathway)
    except _errors() as exc:
        _fail(getattr(exc, "code", "scout_learning_failed"), str(exc), as_json=as_json)
        return
    response = pathway_response(pathway, status=status)
    if as_json:
        _emit(response)
        return
    record = response["pathway"]
    assert isinstance(record, dict)
    lines = [
        f"{pathway.id}  {pathway.role_text}",
        f"Status: {status}",
        f"Asked: {pathway.requested_at}  (changed {pathway.updated_at})",
        f"Course: {_course_words(pathway)}",
        f"Source: {pathway.source}" + (f" (from the folder {pathway.imported_from})" if pathway.imported_from else ""),
        f"Cost: {_cost_words(record['cost'])}",
    ]
    progress = _progress_words(pathway)
    if progress:
        lines.append(f"Generation: {progress}")
    if pathway.error:
        lines.append(f"Error: {pathway.error}" + (f" ({pathway.error_code})" if pathway.error_code else ""))
    if record["url"]:
        lines.append(f"Open it: gigai scout learning open {pathway.id}")
    elif status in ("failed", "interrupted") and pathway.source == "requested":
        lines.append(f"Go on with it: gigai scout learning resume {pathway.id}")
    click.echo("\n".join(lines))


@learning_group.command("open")
@click.argument("pathway_id")
@click.option("--no-browser", "no_browser", is_flag=True, help="Print the address only; open no browser.")
@_home_option
@_target_option
@_json_option
def learning_open_command(pathway_id: str, no_browser: bool, home_value: Path | None, target_value: Path | None, as_json: bool) -> None:
    """Print where the running Scout server serves a pathway's course, and open it in the browser.

    PATHWAY_ID is a pathway id, or the ROLE_KEY of a course shipped with
    GigAI. The course is served by the Scout server only: when it is not
    running, the command says how to start it and exits 1. --json never
    opens a browser.
    """

    from . import run_supervisor
    from .find_jobs.api.learning import course_url

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        course = _shipped(pathway_id)
        if course is not None:
            shown_id, path = course.role_key, learning_shipped_store.course_url(course.role_key)
        else:
            pathway = _pathway(home_root, target, pathway_id)
            shown_id, path = pathway.id, course_url(pathway)
            if path is None:
                raise LearningError("course_not_ready", f"learning pathway {pathway.id} has no course yet (status: {pathway.status})")
        current = run_supervisor.status(home_root=home_root, requested_target=target)
    except _errors() as exc:
        _fail(getattr(exc, "code", "scout_learning_failed"), str(exc), as_json=as_json)
        return
    if current.state != run_supervisor.STATE_RUNNING or not current.url:
        if current.state == run_supervisor.STATE_UNREACHABLE:
            message = f"The Scout server did not answer from here ({current.url}). When it runs, the course is at {path} on it."
        else:
            message = f"The Scout server is not running, and it is what serves a course. {_START_HINT}"
        _fail("server_not_running", message, as_json=as_json)
        return
    url = f"{current.url.rstrip('/')}{path}"
    opened = False
    if not no_browser and not as_json:
        import webbrowser

        opened = bool(webbrowser.open(url))
    if as_json:
        _emit({"schema_version": "scout-learning-open:1", "id": shown_id, "url": url, "opened": opened})
        return
    click.echo(url)


def _count_words(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def _pathway_counts_line(counts: dict[str, object]) -> str:
    """The numbers a local pathway page was built from, in one line (numbers only: no posting text)."""

    corpus = counts.get("corpus") if isinstance(counts.get("corpus"), dict) else {}
    lessons = counts.get("lessons") if isinstance(counts.get("lessons"), dict) else {}
    assert isinstance(corpus, dict) and isinstance(lessons, dict)
    named = sum(1 for found in lessons.values() if isinstance(found, dict) and (found.get("postings_in_90d") or found.get("postings_any_date")))
    window = int(corpus.get("window_days", 90) or 90)
    return (
        f"Counted: {int(corpus.get('with_text_in_window', 0) or 0)} of your stored postings of the last {window} days "
        f"({int(corpus.get('with_text', 0) or 0)} of any date); a posting names {int(counts.get('concepts_named', 0) or 0)} of the "  # type: ignore[call-overload]
        f"{_count_words(int(counts.get('concepts', 0) or 0), 'thing')} the course lists and {named} of its {_count_words(len(lessons), 'lesson')}."  # type: ignore[call-overload]
    )


@learning_group.command("pathway")
@click.argument("role_key", metavar="ROLE_KEY")
@click.option("--yes", "yes", is_flag=True, help="Approve: compare your resume with the course (about 1 model call) without asking.")
@_home_option
@_target_option
@_json_option
def learning_pathway_command(role_key: str, yes: bool, home_value: Path | None, target_value: Path | None, as_json: bool) -> None:
    """Build, or build again, your own pathway page of a course shipped with GigAI.

    ROLE_KEY is the first word of the course's line in `gigai scout learning
    list`. The lessons of a shipped course are the same for everyone; this
    one page is yours: what the postings stored on this computer ask for,
    and what your resume already shows of each lesson (known, in part, new).

    Your master resume is always the reference, so the build compares it
    with the course: the lesson titles and your resume lines (contact lines
    removed) go to your own model, in about one model call. Nothing is sent
    without approval: without --yes the command says what it takes and asks
    (in a terminal; default no) or stops there (--json, or no terminal).
    With no master resume there is nothing to send, and the page is built
    with nothing marked as known.
    """

    import sys

    from . import learning_pathway_build, run_supervisor
    from .find_jobs.api.learning import shipped_response

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        course = _shipped(role_key)
        if course is None:
            raise LearningError("pathway_not_found", f"there is no course {role_key!r} shipped with GigAI; `gigai scout learning list` lists them")
        asked_for = learning_pathway_build.ask_response(home_root, target, course)
        sends = bool(asked_for["resume"]["lines"])  # type: ignore[index]
        question = f"Build your pathway page of '{course.role}'?"
        facts = [f"Estimate: {learning_pathway_build.estimate_words(asked_for['estimate'])}.", str(asked_for["sends"])]  # type: ignore[arg-type]
        approved = yes or not sends  # with no resume line nothing is sent: there is nothing to approve
        asked = not approved and not as_json and sys.stdin.isatty()
        if asked:
            # What it takes and what it sends, before the y/n (default no).
            click.echo("\n".join(facts))
            approved = click.confirm(question.rstrip("?"), default=False)
        if not approved:
            if as_json:
                _emit(asked_for)
                return
            if not asked:
                click.echo(question)
                click.echo("\n".join(facts))
            click.echo("Nothing was sent. Approve with --yes.")
            return
        built = learning_pathway_build.run(home_root, target, course, on_progress=None if as_json else click.echo)
        response = shipped_response(home_root, target, course)
        current = run_supervisor.status(home_root=home_root, requested_target=target)
    except _errors() as exc:
        _fail(getattr(exc, "code", "scout_learning_failed"), str(exc), as_json=as_json)
        return
    if as_json:
        _emit(response)
        return
    address = learning_shipped_store.course_url(course.role_key, learning_shipped_store.PATHWAY_PAGE)
    lines = [f"Built your pathway page of {course.role} ({course.role_key}).", _pathway_counts_line(dict(built.counts))]
    note = built.counts.get("corpus", {}).get("note") if isinstance(built.counts.get("corpus"), dict) else None
    if note:
        lines.append(str(note))
    if built.page.compared:
        tally = built.page.tally
        lines.append(f"Your resume: {tally.get('KNOWN', 0)} known, {tally.get('SOME', 0)} in part, {tally.get('NEW', 0)} new (subtopics of the course).")
    else:
        lines.append(learning_pathway_build.NO_RESUME)
    calls = int(built.usage.get("calls") or 0)
    if calls:
        tokens_in, tokens_out = built.usage.get("tokens_in"), built.usage.get("tokens_out")
        tokens = f": {tokens_in:,} tokens in, {tokens_out:,} out" if isinstance(tokens_in, int) and isinstance(tokens_out, int) else ""
        lines.append(f"Sent to your model: {_count_words(calls, 'call')}{tokens}.")
    else:
        lines.append("Nothing was sent to a model.")
    if current.state == run_supervisor.STATE_RUNNING and current.url:
        lines.append(f"Open it: {current.url.rstrip('/')}{address}")
    else:
        lines.append(f"Open it: the Scout server serves it at {address} (start the server with `gigai scout run`).")
    click.echo("\n".join(lines))


@learning_group.command("import")
@click.argument("course_dir", metavar="DIR", type=click.Path(path_type=Path, file_okay=False))
@click.option("--role", "role_text", required=True, help="The role the course is for, as you would type it (at most 200 characters).")
@click.option("--replace", "replace_id", metavar="ID", help="Replace the course of this pathway instead of adding a new one.")
@click.option("--cost-json", "cost_file", type=click.Path(path_type=Path, dir_okay=False), help="A JSON FILE with what making the course took: cli_model_calls, worker_minutes, web_fetches, web_searches, note (each optional).")
@_home_option
@_target_option
@_json_option
def learning_import_command(
    course_dir: Path, role_text: str, replace_id: str | None, cost_file: Path | None, home_value: Path | None, target_value: Path | None, as_json: bool,
) -> None:
    """Import a course folder (index.html, concept-*.html, its assets) as a learning pathway.

    The folder is checked whole first and copied only when every check
    passes: allowed file kinds only, no link, plain ASCII names, at most
    2,000 files and 64 MB. The same role imported again is a NEW pathway;
    --replace ID replaces that one's course. No model call.
    """

    home_root = home_value or default_home_root()
    try:
        cost: object = None
        if cost_file is not None:
            if cost_file.is_symlink() or not cost_file.is_file():
                raise LearningError("invalid_value", "--cost-json must be one regular local file")
            try:
                cost = json.loads(cost_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise LearningError("invalid_value", "--cost-json must be a UTF-8 JSON file") from exc
        target = _target(target_value, home_root, as_json=as_json)
        result = learning_store.import_course(home_root, target, course_dir, role_text, cost, replace_id=replace_id)
    except _errors() as exc:
        _fail(getattr(exc, "code", "scout_learning_failed"), str(exc), as_json=as_json)
        return
    if as_json:
        _emit(result.to_json())
        return
    pathway = result.pathway
    click.echo(f"{'Replaced' if result.replaced else 'Imported'} {pathway.id}: {pathway.role_text} ({_course_words(pathway)}, {result.files} files).")
    if result.inline_script_pages:
        pages = result.inline_script_pages
        click.echo(
            f"{pages} page{'' if pages == 1 else 's'} hold{'s' if pages == 1 else ''} an inline <script> block. The Scout server does not run "
            "those (it runs a course's script FILES only): put the code in a .js file of the course and import it again."
        )
    click.echo(f"Open it: gigai scout learning open {pathway.id}")



@learning_group.command("corpus")
@click.argument("role_text", metavar="ROLE")
@click.option("--yes", "yes", is_flag=True, help="Approve: make the model calls without asking (about 3, at most 5).")
@_home_option
@_target_option
@_json_option
def learning_corpus_command(role_text: str, yes: bool, home_value: Path | None, target_value: Path | None, as_json: bool) -> None:
    """Read the postings stored for ROLE and count what they ask for.

    The role becomes title phrases (one model call), the stored postings
    with such a title are read from this computer (the country of your
    setup; never the work mode or area of `gigai scout jobs search`: this
    is about the role, not one job search; the newest 300), and one or two
    more model calls name what the requirement sentences of the last 90
    days ask for (of every date when fewer than 25 of those postings have
    stored text; the note says which). Code counts: per concept, the
    postings of the last 90 days and of any date that name it.

    Nothing is called without approval: without --yes the command says what
    it will take and asks (in a terminal) or stops there (--json, or no
    terminal). No resume is read or sent. Nothing is stored.
    """

    import sys

    from . import learning_corpus

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        response = learning_corpus.ask_response(home_root, target, role_text)
        approved = yes
        asked = not approved and not as_json and sys.stdin.isatty()
        if asked:
            # What would be sent and where, before the y/n (default no).
            click.echo(f"This sends: {response['sends']}")
            approved = click.confirm(str(response["question"]["text"]).rstrip("?"), default=False)  # type: ignore[index]
        if approved:
            filters = learning_corpus.corpus_filters(home_root, target)
            model = learning_corpus.MeteredRoleModel(home_root, target)
            try:
                response = learning_corpus.run_corpus(
                    home_root, role_text, model=model, filters=filters, progress=None if as_json else click.echo,
                )
            finally:
                model.close()
    except _errors() as exc:
        _fail(getattr(exc, "code", "scout_learning_failed"), str(exc), as_json=as_json)
        return
    if as_json:
        _emit(response)
        return
    if response["status"] == "ask":
        if not asked:
            click.echo(str(response["question"]["text"]))  # type: ignore[index]
            click.echo(f"This sends: {response['sends']}")
        click.echo("Nothing was called. Approve with --yes.")
        return
    click.echo(learning_corpus.render(response))


_GENERATE_SENDS = (
    "It sends: the typed role, sentences from job postings stored on this computer, and the course text as it is written. "
    "The course uses your resume to mark what you already know (contact lines removed; sent to the lesson-writing calls only)."
)


def _ended(home_root: Path, target: Path, pathway: Pathway, *, as_json: bool) -> None:
    """Say how a foreground generation ended; exit 1 when it has no course."""

    from .find_jobs.api.learning import live_pathway_response

    if as_json:
        _emit(live_pathway_response(home_root, target, pathway))
    elif pathway.status == "done":
        click.echo(f"Done: {pathway.id}  {pathway.role_text} ({_course_words(pathway)}; {_cost_words(dict(pathway.cost))}).")
        dropped = (pathway.progress or {}).get("dropped") or []
        if dropped:
            click.echo(f"{len(dropped)} planned item(s) are not included; the course's first page lists them and why.")  # type: ignore[arg-type]
        click.echo(f"Open it: gigai scout learning open {pathway.id}")
    else:
        click.echo(f"Not finished: {pathway.error or pathway.status}" + (f" ({pathway.error_code})" if pathway.error_code else ""))
        click.echo(f"What was written is kept. Go on with it: gigai scout learning resume {pathway.id}")
    if pathway.status != "done":
        raise click.exceptions.Exit(1)


@learning_group.command("generate")
@click.argument("role_text", metavar="ROLE")
@click.option("--yes", "yes", is_flag=True, help="Approve: generate without asking (about 60 model calls, at most 120).")
@_home_option
@_target_option
@_json_option
def learning_generate_command(role_text: str, yes: bool, home_value: Path | None, target_value: Path | None, as_json: bool) -> None:
    """Generate a course for ROLE from the postings stored on this computer.

    A higher-usage action than an assessment: about 60 model calls on your
    own model login, about 1300 page fetches from public documentation sites
    and about 140 minutes. Nothing is generated without approval: without
    --yes the command says what it will take and asks (in a terminal;
    default no) or stops there (--json, or no terminal).

    The course marks what you already know from your master resume (contact
    lines removed; sent to the lesson-writing calls only). It runs here, in
    the foreground, with one plain progress line per step; one course at a
    time per project. It stops by itself after 120 model calls or 1500 page
    fetches; `gigai scout learning cancel ID` stops it from another terminal.
    """

    import sys

    from . import learning_job

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        response = learning_job.ask_response(home_root, target, role_text)
        caps = response["caps"]
        facts = [
            f"Estimate: {learning_job.estimate_words(response['estimate'])}.",  # type: ignore[arg-type]
            learning_job.PLAN_WARNING,
            _GENERATE_SENDS,
            f"Stops by itself after {caps['calls']} model calls or {caps['fetches']} page fetches. Stop it any time from another terminal: gigai scout learning cancel ID.",  # type: ignore[index]
        ]
        question = f"Generate a course for '{response['role_text']}'? This is a higher-usage action than an assessment."
        approved = yes
        asked = not approved and not as_json and sys.stdin.isatty()
        if asked:
            # What it takes and what it sends, before the y/n (default no).
            click.echo("\n".join(facts))
            approved = click.confirm(question.rstrip("?."), default=False)
        if not approved:
            if as_json:
                _emit(response)
                return
            if not asked:
                click.echo(question)
                click.echo("\n".join(facts))
            click.echo("Nothing was generated. Approve with --yes.")
            return
        pathway = learning_job.run_foreground(home_root, target, role_text=role_text, on_progress=None if as_json else click.echo)
    except _errors() as exc:
        _fail(getattr(exc, "code", "scout_learning_failed"), str(exc), as_json=as_json)
        return
    _ended(home_root, target, pathway, as_json=as_json)


@learning_group.command("cancel")
@click.argument("pathway_id")
@_home_option
@_target_option
@_json_option
def learning_cancel_command(pathway_id: str, home_value: Path | None, target_value: Path | None, as_json: bool) -> None:
    """Ask the job that generates a pathway's course to stop.

    No further model call and no further page fetch starts; a call in flight
    finishes. Works whichever process runs the job (the Scout server, or
    `gigai scout learning generate` in another terminal). What was written
    is kept: `gigai scout learning resume ID` goes on with it. No model call.
    """

    from . import learning_job
    from .find_jobs.api.learning import live_pathway_response

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        _refuse_shipped(pathway_id, "cancel")
        _pathway(home_root, target, pathway_id)
        asked = learning_job.cancel(home_root, target, pathway_id)
        pathway = _pathway(home_root, target, pathway_id)
        response = {**live_pathway_response(home_root, target, pathway), "cancel_requested": asked}
    except _errors() as exc:
        _fail(getattr(exc, "code", "scout_learning_failed"), str(exc), as_json=as_json)
        return
    if as_json:
        _emit(response)
        return
    if asked:
        click.echo(f"Stopping the generation of {pathway.id}: no further model call or page fetch starts; a call in flight finishes. What was written is kept.")
    else:
        click.echo(f"No job is generating {pathway.id} now: nothing to stop.")


@learning_group.command("resume")
@click.argument("pathway_id")
@_home_option
@_target_option
@_json_option
def learning_resume_command(pathway_id: str, home_value: Path | None, target_value: Path | None, as_json: bool) -> None:
    """Go on with a course whose generation failed or was interrupted.

    The same pathway, here in the foreground: every step whose stored result
    is still valid is skipped, so only what is missing costs model calls and
    page fetches. You approved this course when you started it; it stops by
    itself after 120 model calls or 1500 page fetches, as a new one does.
    """

    from . import learning_job

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        _refuse_shipped(pathway_id, "resume")
        pathway = learning_job.run_foreground(home_root, target, pathway_id=pathway_id, on_progress=None if as_json else click.echo)
    except _errors() as exc:
        _fail(getattr(exc, "code", "scout_learning_failed"), str(exc), as_json=as_json)
        return
    _ended(home_root, target, pathway, as_json=as_json)


@learning_group.command("render")
@click.argument("course_json", metavar="COURSE_JSON", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.argument("out_dir", metavar="OUT_DIR", type=click.Path(path_type=Path, file_okay=False))
@click.option("--paths", "paths_dir", metavar="DIR", type=click.Path(path_type=Path, exists=True, file_okay=False), help="A folder of per-concept practice path files (<concept-id>.json). Concepts with no matching file render with no 'Practice this' link.")
@_json_option
def learning_render_command(course_json: Path, out_dir: Path, paths_dir: Path | None, as_json: bool) -> None:
    """Render a course.json (and optional practice paths) into a static course site the `import` command accepts.

    Deterministic (byte-identical on the same input), stdlib only: no model call, no network. Validates the
    course shape, strips invisible Unicode from every input string, rejects a disallowed glyph, audits every
    internal link and anchor, and enforces the lesson-count and page-size caps before writing anything.
    """

    from . import learning_render

    try:
        result = learning_render.render_to_folder(course_json, out_dir, paths_dir)
    except learning_render.CourseError as exc:
        if as_json:
            _emit({"status": "error", "error": {"code": "course_render_failed", "message": str(exc), "problems": exc.errors}})
            raise click.exceptions.Exit(1)
        raise click.ClickException(f"{len(exc.errors)} problem(s):\n  " + "\n  ".join(exc.errors))
    if as_json:
        _emit(
            {
                "schema_version": "scout-learning-render:1",
                "out_dir": str(result.out_dir),
                "pages": result.pages,
                "size_bytes": result.size_bytes,
                "stripped_invisible": result.stripped_invisible,
            }
        )
        return
    click.echo(f"OK: wrote {result.out_dir} ({learning_render.human_size(result.size_bytes)}, {result.pages} pages).")
    if result.stripped_invisible:
        click.echo(f"Stripped {result.stripped_invisible} invisible Unicode character(s) from the input before rendering.")


__all__ = ["learning_group"]
