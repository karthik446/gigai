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

``--json`` prints one JSON object (what the API route answers, through the same outbound check) and an error is
``{"status": "error", "error": {"code", "message"}}`` with exit code 1, like the other ``gigai scout`` commands.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..setup import default_home_root
from . import learning_store
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

    from .find_jobs.api.learning import pathways_response

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        if as_json:
            _emit(pathways_response(home_root, target))
            return
        pathways = learning_store.list_pathways(home_root, target)
    except _errors() as exc:
        _fail(getattr(exc, "code", "scout_learning_failed"), str(exc), as_json=as_json)
        return
    if not pathways:
        click.echo("No learning pathways yet. Import a course folder: gigai scout learning import DIR --role \"ROLE\"")
        return
    for pathway in pathways:
        line = f"{_live(home_root, target, pathway)}"
        line = f"{pathway.id}  {line:<7}  {pathway.requested_at[:10]}  {pathway.role_text}  ({_course_words(pathway)})  [{pathway.source}]"
        if pathway.error:
            line += f"  error: {pathway.error}"
        click.echo(line)
    click.echo("Open one: gigai scout learning open ID")


@learning_group.command("show")
@click.argument("pathway_id")
@_home_option
@_target_option
@_json_option
def learning_show_command(pathway_id: str, home_value: Path | None, target_value: Path | None, as_json: bool) -> None:
    """Show one learning pathway: what was asked, how far it is, what it cost. Reads the store; no model call."""

    from .find_jobs.api.learning import pathway_response

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
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

    The course is served by the Scout server only: when it is not running, the
    command says how to start it and exits 1. --json never opens a browser.
    """

    from . import run_supervisor
    from .find_jobs.api.learning import course_url

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        pathway = _pathway(home_root, target, pathway_id)
        path = course_url(pathway)
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
        _emit({"schema_version": "scout-learning-open:1", "id": pathway.id, "url": url, "opened": opened})
        return
    click.echo(url)


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
    own model login, about 1000 page fetches from public documentation sites
    and about 45 minutes. Nothing is generated without approval: without
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
