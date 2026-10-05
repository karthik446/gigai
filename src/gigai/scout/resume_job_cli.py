"""0.1.11 N5 (SPEC 4.4, 5.1-5.3): the commands of the chat step, one job at a time.

    gigai scout resume brief --job-url URL [--profile ID] [--posting] [--out FILE] [--json]
    gigai scout resume store --in FILE --job-url URL [--profile ID] --as agent [--source TEXT] [--resolves sg-1,sg-3] [--fit] [--json]
    gigai scout resume pick  --job-url URL [--profile ID] [--refresh | --draft | --use-proposed | --dismiss-proposed] [--json]
    gigai scout suggestions list | add | resolve | dismiss --job-url URL ...
    gigai scout resume tailor --in FILE --job-url URL    (the old spelling of ``resume store``: it forwards, and says so in one line)

* ``brief`` (``job_brief``): what an agent reads before it works on the job's resume with the user, in two calls
  that never mix: the user's own part, and ``--posting``, the posting's. No model call, nothing fetched, nothing
  written (``--out FILE`` writes the part to the file the caller names).
* ``store``: the hand-back. The markdown is checked in code and stored as that ONE job's resume, or refused line
  by line (``tailored_resume_edit.attach_edited_resume``; the command adds no check of its own). ``--resolves``
  names the suggestions this edit settles: they are checked BEFORE anything is stored and set to ``done`` after.
  ``--fit`` lets code cut a hand-back that is over two pages (SPEC 3.2) and record the cut, which one Restore puts
  back; without it a resume over two pages is refused (``over_page_limit``). The job is then put through the pipeline, as the old spelling did.
* ``pick`` (``job_actions``): the job resume as it is stored, who picked it, the gate, the stale list and the
  conflicts; one flag takes one explicit step. No model call in any form.
* ``suggestions`` (``job_actions``): the job's suggestion record; ``add`` / ``resolve`` / ``dismiss`` say who wrote
  (``--as agent``).
* ``tailor --in``: ``scout_cli``'s own ``resume tailor`` hands its ``--in`` path to ``store_resume`` here, which
  does what ``resume store`` does and names the new spelling in one line (stderr; ``renamed`` in ``--json``).

NOT WIRED HERE: ``resume_tailor_removed_command``, the command ``gigai scout resume tailor`` becomes when the
tailor model call goes (SPEC 4.4: the typed exit ``tailoring_removed`` with one line that names ``resume pick``;
``--in`` still forwards). The packet that removes the call registers it in place of the old command
(``resume_group.add_command(resume_tailor_removed_command)``); until then the tree keeps the old one.

``register`` puts ``brief``, ``store``, ``pick`` and ``suggestions`` on the ``gigai scout`` tree (``scout_cli``
calls it once).
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..setup import default_home_root
from ..workpad import committed_read_cache

TAILORING_REMOVED = "tailoring_removed"
#: The one line a caller of the removed command reads (SPEC 4.4), on the CLI and from ``POST /api/tailored-resumes``.
TAILORING_REMOVED_LINE = (
    "GigAI no longer rewrites a resume with a model. The resume for a job is picked when the job is assessed: "
    "`gigai scout resume pick --job-url URL`."
)
#: The one line the old ``--in`` spelling prints before it does what ``resume store`` does.
TAILOR_IN_RENAMED_LINE = "`gigai scout resume tailor --in` is now `gigai scout resume store --in FILE --job-url URL`; the old spelling works for this release."

_JOB_URL = click.option("--job-url", "job_url", required=True, help="The posting URL: the ONE job this is about.")
_PROFILE = click.option("--profile", "profile_id", help="The Scout profile ID (default: the profile whose assessment of the job is newest).")
_ACTOR = click.option(
    "--as", "--actor", "actor", type=click.Choice(["operator", "agent"]), default="operator", show_default=True,
    help="Who is writing: recorded with the change. An agent passes --as agent.",
)


def _options(function):
    for option in reversed((
        click.option("--target", "target_value", type=click.Path(path_type=Path, file_okay=False)),
        click.option("--home", "home_value", type=click.Path(path_type=Path, file_okay=False)),
        click.option("--json", "as_json", is_flag=True),
    )):
        function = option(function)
    return function


def _emit(payload: dict[str, object]) -> None:
    click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _fail(exc: Exception, *, as_json: bool, fallback: str) -> None:
    """Report a refusal by its code. A refused hand-back also lists every problem (line, code, what, fix)."""

    code = getattr(exc, "code", fallback)
    if as_json:
        error: dict[str, object] = {"code": code, "message": str(exc)}
        problems = getattr(exc, "problems", None)
        if problems:
            error["problems"] = [item.to_json() for item in problems]
        _emit({"status": "error", "error": error})
        raise click.exceptions.Exit(1)
    raise click.ClickException(str(exc))


def _target(target_value: Path | None, home_root: Path, *, as_json: bool) -> Path:
    # Imported here: scout_cli imports this module to register the commands. Its resolver is the
    # one every `gigai scout ...` command shares (--target, else <home>/scout).
    from . import scout_cli

    return scout_cli._resolved_target(target_value, home_root, as_json=as_json).expanduser().resolve(strict=True)


def _errors() -> tuple[type[Exception], ...]:
    from ..private_records import PrivateRecordError
    from ..workpad import WorkpadError
    from .data_labels import LabelError
    from .find_jobs.contracts import FindJobsContractError
    from .job_actions import JobActionError
    from .job_brief import BriefError
    from .job_resume_port import NotBuilt
    from .quick_assess import QuickAssessError
    from .target_resolution import ScoutTargetError

    return (BriefError, JobActionError, NotBuilt, QuickAssessError, LabelError, FindJobsContractError, ScoutTargetError, PrivateRecordError, WorkpadError, OSError, ValueError)


def _write_out(out_file: Path, text: str, *, as_json: bool) -> Path | None:
    path = out_file.expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        _fail(exc, as_json=as_json, fallback="output_file_unwritable")
        return None
    return path


# --- resume brief -------------------------------------------------------------------------------------------


@click.command("brief")
@_JOB_URL
@_PROFILE
@click.option("--posting", "posting", is_flag=True, help="The OTHER part: the posting, text written by strangers (public-untrusted). Never printed with your own part.")
@click.option("--out", "out_file", type=click.Path(path_type=Path, dir_okay=False), help="Write this part to FILE instead of printing it.")
@_options
def resume_brief_command(job_url: str, profile_id: str | None, posting: bool, out_file: Path | None, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """What your agent reads before it works on ONE job's resume with you. No model call.

    Two calls, never one output: without --posting, YOUR part (the rules, the
    job's state, the stored resume with each line's master id, every master
    line with what is picked and what is left out, your answers and stories,
    the requirement rows by id, the suggestions); with --posting, the POSTING
    (inside GigAI's untrusted-text markers) with each requirement's words by
    the same ids. The job needs a stored assessment. Nothing is fetched and
    nothing is stored.
    """

    from . import job_brief

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        with committed_read_cache():
            part = job_brief.brief(home_root, target, job_url, profile_id=profile_id or None, part=job_brief.PART_POSTING if posting else job_brief.PART_YOURS)
        text = job_brief.render(part)
    except _errors() as exc:
        _fail(exc, as_json=as_json, fallback="scout_resume_brief_failed")
        return
    if out_file is not None:
        written = _write_out(out_file, json.dumps(part, sort_keys=True, indent=2) + "\n" if as_json else text, as_json=as_json)
        if written is None:
            return
        summary = {"ok": True, "out_path": str(out_file), "part": part["part"], "label": part["label"], "job_identity": part["job_identity"], "profile_id": part["profile_id"]}
        if as_json:
            _emit(summary)
        else:
            click.echo(f"Wrote the {part['part']} part of the brief ({part['label']}) to {out_file}.")
        return
    if as_json:
        _emit({"ok": True, **part})
        return
    click.echo(text, nl=False)


# --- resume store (the hand-back) ---------------------------------------------------------------------------


def _folder_file(home_root: Path, response: object) -> str | None:
    """Where the resumes folder holds this stored resume's markdown (as the user types it), or ``None``."""

    from . import resumes_folder
    from .target_resolution import _display_path

    name = resumes_folder.job_files(home_root, resumes_folder.job_key(home_root, response.stored_path))["markdown"]  # type: ignore[attr-defined]
    return None if name is None else _display_path(resumes_folder.resumes_folder(home_root).path / name)


def store_resume(
    in_file: str, job_url: str, profile_id: str | None, actor: str, source: str | None, out_file: Path | None, home_root: Path, target: Path, as_json: bool,
    *, resolves: str | None = None, fit: bool = False, renamed_from: str | None = None,
) -> None:
    """``gigai scout resume store`` (and the old ``resume tailor --in``, ``renamed_from``): check, store, then the checks again."""

    from . import job_actions, scout_cli
    from .pipeline.runner import pipeline_status, run_once
    from .tailored_resume_edit import attach_edited_resume, queue_recheck

    profile_id = profile_id or None
    try:
        markdown = scout_cli._read_text_option(in_file, flag="--in")
    except (OSError, ValueError) as exc:  # ValueError: not UTF-8
        _fail(exc, as_json=as_json, fallback="input_file_unreadable")
        return
    try:
        # ONE default for every command of the job: the profile of its newest assessment (``--profile`` names another).
        profile_id = job_actions.default_profile(home_root, target, job_url, profile_id)
        names = job_actions.suggestion_ids(resolves)
        # A hand-back that names a suggestion the job does not have stores nothing.
        job_actions.check_resolves(home_root, target, job_url, profile_id, names)
        attached = attach_edited_resume(
            markdown, job_url=job_url, profile_id=profile_id, written_by=actor, source=source, home_root=home_root, target=target, fit=fit,
        )
    except _errors() as exc:
        _fail(exc, as_json=as_json, fallback="scout_resume_store_failed")
        return
    response = attached.response
    out_path: Path | None = None
    if out_file is not None:
        out_path = _write_out(out_file, response.markdown, as_json=as_json)
        if out_path is None:
            return
    # No model call: the final-selection check on what the resume prints now, and the named suggestions set to done.
    record: dict[str, object] | None = None
    record_error: str | None = None
    try:
        record = job_actions.after_handback(home_root, target, response, resolves=names, actor=actor)
    except _errors() as exc:  # the resume is stored: a record that cannot be updated is reported, never the command's failure
        record_error = getattr(exc, "code", None) or "suggestions_not_updated"

    recheck = queue_recheck(home_root, target, attached)
    drain: dict[str, object] | None = None
    status: dict[str, object] | None = None
    pair = (response.resume.profile_id, response.job.job_identity)
    if attached.changed and recheck["error_code"] is None and pair[0] is not None:
        if not as_json:
            click.echo("Stored. Checking it again (this can take a minute)...")
        try:
            drain = run_once(home_root, target, only=pair, force_enabled=True).to_json()  # type: ignore[arg-type]
            status = pipeline_status(home_root, target, profile_id=pair[0], job=pair[1])
        except scout_cli._pipeline_errors() as exc:  # the resume is stored: a failed check is reported, never the command's failure
            recheck = {**recheck, "result": "not_run", "error_code": getattr(exc, "code", None) or "scout_pipeline_failed"}
    folder_file = _folder_file(home_root, response)
    # E2EFIX F5: the resume is stored, but a step of the check that failed (a sandbox with no network: ``model_unavailable``) is said.
    failed_steps = [
        {"name": step.get("name"), "error_code": step["error_code"]} for step in (drain or {}).get("steps", ()) if step.get("error_code")  # type: ignore[union-attr]
    ]
    recheck_failed = {"error_code": failed_steps[0]["error_code"], "steps": failed_steps} if failed_steps else None
    if as_json:
        payload: dict[str, object] = {
            "ok": True, **response.to_json(), "changed": attached.changed, "out_path": None if out_path is None else str(out_path),
            "folder_path": folder_file, "recheck": recheck, "drain": drain, "status": status, "recheck_failed": recheck_failed,
            "suggestions": record, "suggestions_error": record_error,
        }
        if renamed_from is not None:
            payload["renamed"] = {"from": renamed_from, "to": "gigai scout resume store", "message": TAILOR_IN_RENAMED_LINE}
        _emit(payload)
        return
    if renamed_from is not None:
        click.echo(TAILOR_IN_RENAMED_LINE, err=True)
    job = response.job
    heading = job.title or "(untitled posting)"
    if job.company:
        heading += f" at {scout_cli._company_shown(home_root, job.company)}"
    if not attached.changed:
        click.echo(f"No change: this is already the stored resume for {heading}.")
    else:
        from .tailored_resume import tailor_line_stats

        stats = tailor_line_stats(response.result)
        click.echo(f"Stored your edited resume for {heading} (written by {response.edited.written_by}):")  # type: ignore[union-attr]
        click.echo(f"  Lines: {response.result.line_count()} ({stats.edited} edited, {stats.copied} copied from your resume, {stats.shown_rewritten} kept rewrites)")
    length = response.result.length
    if fit and attached.changed and length is not None and length.leaves_out():
        click.echo(f"  Cut to {length.pages} of {length.max_pages} pages: {length.trimmed_count()} lines and {len(length.cut)} roles left out; the resume page can put them back.")
    click.echo(f"  Markdown: {response.markdown_path}")
    if folder_file is not None:
        click.echo(f"  In your resumes folder: {folder_file}")
    if out_path is not None:
        click.echo(f"  Copied to {out_path}")
    if record is not None:
        gate = record["gate"]
        ready = {True: "ready", False: "needs attention"}.get(gate.get("ready"), "not checked")  # type: ignore[union-attr]
        click.echo(f"  Evidence check: {ready}." + (f" Suggestions set to done: {', '.join(names)}." if names else ""))
    elif record_error is not None:
        click.echo(f"  The suggestion record was not updated ({record_error}).")
    if recheck["error_code"] == "assessment_missing":
        click.echo("  Not checked again: this job has no assessment for this profile yet. Run `gigai scout assess --job-url ...`, then `gigai scout pipeline process <url>`.")
    elif recheck["error_code"] is not None:
        click.echo(f"  Not checked again ({recheck['error_code']}); see `gigai scout pipeline status`.")
    elif drain is not None and status is not None:
        click.echo("  " + scout_cli._pipeline_drain_line(drain))
        for line in scout_cli._pipeline_status_lines(status)[1:]:
            click.echo("  " + line)
    if recheck_failed is not None:
        codes = ", ".join(dict.fromkeys(str(step["error_code"]) for step in failed_steps))
        click.echo(
            f"  WARNING: the resume is stored, but checking it again failed ({codes}). Its scores are NOT updated."
            + (" Your runtime's sandbox may block the network: do not retry; tell the user what to allow (network access for the model host)." if "model_unavailable" in codes else "")
        )


@click.command("store")
@click.option("--in", "in_file", required=True, help="The edited resume markdown FILE (or - for stdin), in GigAI's resume format.")
@_JOB_URL
@click.option("--profile", "profile_id", help="The Scout profile ID the resume is for (default: the profile whose assessment of the job is newest).")
@_ACTOR
@click.option("--source", "source", help="Where the edit came from, in your own words (one line).")
@click.option("--resolves", "resolves", help="The suggestions this edit settles, by id: sg-1,sg-3. They are set to done once the resume is stored.")
@click.option("--fit", "fit", is_flag=True, help="Let GigAI cut the resume to two pages and record what it cut.")
@click.option("--out", "out_file", type=click.Path(path_type=Path, dir_okay=False), help="Also write the stored markdown to FILE.")
@_options
def resume_store_command(
    in_file: str, job_url: str, profile_id: str | None, actor: str, source: str | None, resolves: str | None, fit: bool, out_file: Path | None,
    target_value: Path | None, home_value: Path | None, as_json: bool,
) -> None:
    """Hand an edited resume back for ONE job: checked in code, then stored. No model rewrites anything.

    FILE is resume markdown in GigAI's format (the job's file from your
    resumes folder, or the resume `gigai scout resume brief` printed, edited).
    A line copied unchanged needs nothing; a line you reworded or added ends
    with its sources (`<!-- src: b-23b6dc, A tooling:temporal -->`). The
    check is a guard on numbers, names, ownership, entries and sources; it
    does not prove that a reworded line is true. A refusal lists every
    problem by line number, with its fix, and stores nothing. The stored
    resume is marked edited with who wrote it (--as), and the Scout ATS score
    and the Scout label are made again from it.
    """

    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
    except _errors() as exc:
        _fail(exc, as_json=as_json, fallback="scout_resume_store_failed")
        return
    store_resume(in_file, job_url, profile_id or None, actor, source, out_file, home_root, target, as_json, resolves=resolves, fit=fit)


# --- resume tailor (removed) ------------------------------------------------------------------------------------


@click.command("tailor")
@click.option("--job-url", "job_url", help="The posting URL.")
@click.option("--in", "in_file", help="Old spelling of `gigai scout resume store --in FILE --job-url URL`: store this edited resume markdown for the job.")
@click.option("--profile", "profile_id", help="With --in: the Scout profile ID the resume is for (default: the profile whose assessment of the job is newest).")
@_ACTOR
@click.option("--source", "source", help="With --in: where the edit came from, in your own words (one line).")
@click.option("--out", "out_file", type=click.Path(path_type=Path, dir_okay=False), help="With --in: also write the stored markdown to FILE.")
@_options
def resume_tailor_removed_command(
    job_url: str | None, in_file: str | None, profile_id: str | None, actor: str, source: str | None, out_file: Path | None,
    target_value: Path | None, home_value: Path | None, as_json: bool,
) -> None:
    """Removed: GigAI no longer rewrites a resume with a model.

    The resume for a job is picked when the job is assessed; see it with
    `gigai scout resume pick --job-url URL`. To store a resume you edited for
    one job, use `gigai scout resume store --in FILE --job-url URL` (the old
    `resume tailor --in` spelling still does that, for this release).
    """

    if not in_file:
        _fail(_Removed(TAILORING_REMOVED_LINE), as_json=as_json, fallback=TAILORING_REMOVED)
        return
    if not job_url:
        _fail(ValueError("--in FILE goes with --job-url URL: `gigai scout resume store --in FILE --job-url URL`"), as_json=as_json, fallback="invalid_value")
        return
    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
    except _errors() as exc:
        _fail(exc, as_json=as_json, fallback="scout_resume_store_failed")
        return
    store_resume(in_file, job_url, profile_id or None, actor, source, out_file, home_root, target, as_json, renamed_from="gigai scout resume tailor --in")


class _Removed(ValueError):
    code = TAILORING_REMOVED


# --- resume pick ---------------------------------------------------------------------------------------------------


def _pick_lines(view: dict[str, object]) -> list[str]:
    gate = view["gate"]
    lines = [f"Job: {view['job_identity']}   Profile: {view['profile_id']}", f"Verdict: {view['verdict'] or '(none)'}"]
    if isinstance(gate, dict):
        ready = {True: "yes", False: "no"}.get(gate.get("ready"), "not checked")  # type: ignore[arg-type]
        reasons = ", ".join(str(item.get("code")) + (f" {item['requirement']}" if item.get("requirement") else "") for item in gate.get("reasons", ()))
        lines.append(f"Gate: {gate.get('decision')}   ready: {ready}" + (f"   ({reasons})" if reasons else ""))
    else:
        lines.append("Gate: none stored (this assessment was made before 0.1.11; re-assess the job to get one).")
    resume, picked = view["resume"], view["picked"]
    if isinstance(resume, dict):
        who = f"picked by {picked['picked_by']}" + (f" (fallback: {picked['fallback']})" if picked.get("fallback") else "") + (", a draft" if picked.get("draft") else "") if isinstance(picked, dict) else f"made by {resume['made_by']}"
        if isinstance(resume["edited"], dict):
            who += f"; edited by {resume['edited']['written_by']}"
        lines.append(f"Resume: {resume['lines']} lines, {who}; yours, kept as it is" if resume["replaceable"] is False else f"Resume: {resume['lines']} lines, {who}")
        if isinstance(resume["counts"], dict):
            counts = resume["counts"]
            lines.append(f"  Picked {counts['picked']}, left out {counts['left_out']}, cut for length {counts['cut_for_length']}.")
        if isinstance(picked, dict) and picked.get("pages") is not None:
            lines.append(f"  Pages: {picked['pages']} of {picked['max_pages']}.")
        if resume["folder_path"]:
            lines.append(f"  In your resumes folder: {resume['folder_path']}")
    else:
        lines.append("Resume: none stored for this job." + (f" ({view['selection_error']})" if view.get("selection_error") else ""))
    lines.append("Stale: " + (", ".join(str(code) for code in view["stale"]) or "nothing"))  # type: ignore[union-attr]
    conflicts = [str(item.get("code")) + (f" {item['requirement']}" if item.get("requirement") else "") for item in view["conflicts"]]  # type: ignore[union-attr]
    lines.append("Conflicts: " + (", ".join(conflicts) or "none"))
    asking = [str(item["question_id"]) for item in view.get("open_questions", ())]  # type: ignore[union-attr]
    if asking:
        lines.append("Open questions (answer one with `gigai scout answers save QUESTION_ID`): " + ", ".join(asking))
    if view["proposed"] is not None:
        lines.append(f"A new suggested resume is waiting: gigai scout resume pick --job-url {view['job_identity']} --profile {view['profile_id']} --use-proposed")
    return lines


@click.command("pick")
@_JOB_URL
@_PROFILE
@click.option("--refresh", "refresh", is_flag=True, help="Pick again from the stored assessment against your master as it is now. No model call.")
@click.option("--draft", "draft", is_flag=True, help="Make a draft for a job whose gate holds (an open must-have question, a gap, not a match). No model call.")
@click.option("--use-proposed", "use_proposed", is_flag=True, help="Replace the stored job resume with the new suggested one that is waiting.")
@click.option("--dismiss-proposed", "dismiss_proposed", is_flag=True, help="Drop the new suggested resume that is waiting; the stored one stays.")
@_options
def resume_pick_command(
    job_url: str, profile_id: str | None, refresh: bool, draft: bool, use_proposed: bool, dismiss_proposed: bool,
    target_value: Path | None, home_value: Path | None, as_json: bool,
) -> None:
    """The resume picked for ONE job, as it is stored: who picked it, the gate, what is stale. No model call.

    Without a flag nothing is recomputed and nothing is written. --refresh
    picks again in code (refused while the assessment is old: re-assess
    instead); --draft makes a draft for a held job; --use-proposed takes the
    new suggested resume that waits beside a resume you edited, and
    --dismiss-proposed drops it. A resume you edited is never replaced by
    anything but --use-proposed.
    """

    from . import job_actions

    chosen = [name for name, given in (
        (job_actions.ACTION_REFRESH, refresh), (job_actions.ACTION_DRAFT, draft), (job_actions.ACTION_USE_PROPOSED, use_proposed),
        (job_actions.ACTION_DISMISS_PROPOSED, dismiss_proposed),
    ) if given]
    if len(chosen) > 1:
        _fail(ValueError("pass at most one of --refresh, --draft, --use-proposed, --dismiss-proposed"), as_json=as_json, fallback="invalid_value")
        return
    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        if chosen:
            view = job_actions.pick_action(home_root, target, job_url, chosen[0], profile_id=profile_id or None)
        else:
            with committed_read_cache():
                view = job_actions.pick_view(home_root, target, job_url, profile_id=profile_id or None)
    except _errors() as exc:
        _fail(exc, as_json=as_json, fallback="scout_resume_pick_failed")
        return
    if as_json:
        _emit({"ok": True, **view})
        return
    click.echo("\n".join(_pick_lines(view)))


# --- suggestions ---------------------------------------------------------------------------------------------------


@click.group("suggestions")
def suggestions_group() -> None:
    """The suggestions of one job: what would make its resume fit better, and what was done about each."""


def _suggestion_lines(body: dict[str, object]) -> list[str]:
    counts = body["counts"]
    lines = [f"Job: {body['job_identity']}   Profile: {body['profile_id']}", f"Suggestions: {counts['open']} open, {counts['done']} done, {counts['dismissed']} dismissed."]  # type: ignore[index]
    for item in body["suggestions"]:  # type: ignore[union-attr]
        text = f"  {item['id']}  {item['kind']}"
        text += f"  line {item['line']}" if item.get("line") else ""
        text += f"  requirement {item['requirement']}" if item.get("requirement") else ""
        text += f"  {item['status']} ({item['source']})"
        if isinstance(item.get("resolved"), dict):
            text += f"  {item['resolved']['how']} by {item['resolved']['by']}" + (f" -> {item['resolved']['ref']}" if item["resolved"].get("ref") else "")
        lines += [text, f"      {item['why']}"]
    return lines


def _run_suggestions(call, *, target_value: Path | None, home_value: Path | None, as_json: bool, read_only: bool = False) -> None:  # noqa: ANN001 - (home_root, target) -> body
    home_root = home_value or default_home_root()
    try:
        target = _target(target_value, home_root, as_json=as_json)
        if read_only:
            with committed_read_cache():
                body = call(home_root, target)
        else:
            body = call(home_root, target)
    except _errors() as exc:
        _fail(exc, as_json=as_json, fallback="scout_suggestions_failed")
        return
    if as_json:
        _emit({"ok": True, **body})
        return
    for key, verb in (("added", "Added"), ("resolved", "Set to done:"), ("dismissed", "Dismissed")):
        if key in body:
            click.echo(f"{verb} {body[key]}.")
    click.echo("\n".join(_suggestion_lines(body)))


@suggestions_group.command("list")
@_JOB_URL
@_PROFILE
@click.option("--status", "status", type=click.Choice(["open", "done", "dismissed"]), help="Only the suggestions in this state.")
@_options
def suggestions_list_command(job_url: str, profile_id: str | None, status: str | None, target_value: Path | None, home_value: Path | None, as_json: bool) -> None:
    """List one job's suggestions, as stored. Nothing is recomputed."""

    from . import job_actions

    _run_suggestions(
        lambda home_root, target: job_actions.list_suggestions(home_root, target, job_url, profile_id=profile_id or None, status=status, with_view=True),
        target_value=target_value, home_value=home_value, as_json=as_json, read_only=True,
    )


@suggestions_group.command("add")
@_JOB_URL
@_PROFILE
@click.option("--kind", "kind", required=True, type=click.Choice(["reword", "keyword", "order", "gap", "master_line"]), help="What the suggestion is about.")
@click.option("--why", "why", required=True, help="What would help, in one or two sentences. No name and no contact detail.")
@click.option("--line", "line", help="The master line it is about, by id (b-...).")
@click.option("--requirement", "requirement", help="The requirement row it is about, by id (req-...).")
@click.option("--posting-phrase", "posting_phrase", help="At most 60 characters of the posting the suggestion points at.")
@_ACTOR
@_options
def suggestions_add_command(
    job_url: str, profile_id: str | None, kind: str, why: str, line: str | None, requirement: str | None, posting_phrase: str | None, actor: str,
    target_value: Path | None, home_value: Path | None, as_json: bool,
) -> None:
    """Add a suggestion to one job. It names a master line (--line), a requirement row (--requirement), or both."""

    from . import job_actions

    _run_suggestions(
        lambda home_root, target: job_actions.add_suggestion(
            home_root, target, job_url, kind=kind, why=why, actor=actor, profile_id=profile_id or None, line=line or None,
            requirement=requirement or None, posting_phrase=posting_phrase or None,
        ),
        target_value=target_value, home_value=home_value, as_json=as_json,
    )


@suggestions_group.command("resolve")
@click.argument("suggestion_id")
@_JOB_URL
@_PROFILE
@click.option("--how", "how", required=True, type=click.Choice(["job_resume_edit", "master_line", "answer"]), help="What settled it: an edit of this job's resume, a master line, or an answer.")
@click.option("--ref", "ref", help="What it points at: the master line id (--how master_line) or the QUESTION_ID (--how answer).")
@_ACTOR
@_options
def suggestions_resolve_command(
    suggestion_id: str, job_url: str, profile_id: str | None, how: str, ref: str | None, actor: str,
    target_value: Path | None, home_value: Path | None, as_json: bool,
) -> None:
    """Set one suggestion to done, and say what settled it. It stays in the record."""

    from . import job_actions

    _run_suggestions(
        lambda home_root, target: job_actions.resolve_suggestion(home_root, target, job_url, suggestion_id, how=how, actor=actor, profile_id=profile_id or None, ref=ref or None),
        target_value=target_value, home_value=home_value, as_json=as_json,
    )


@suggestions_group.command("dismiss")
@click.argument("suggestion_id")
@_JOB_URL
@_PROFILE
@_ACTOR
@_options
def suggestions_dismiss_command(
    suggestion_id: str, job_url: str, profile_id: str | None, actor: str, target_value: Path | None, home_value: Path | None, as_json: bool,
) -> None:
    """Dismiss one suggestion. It stays in the record, marked dismissed."""

    from . import job_actions

    _run_suggestions(
        lambda home_root, target: job_actions.dismiss_suggestion(home_root, target, job_url, suggestion_id, actor=actor, profile_id=profile_id or None),
        target_value=target_value, home_value=home_value, as_json=as_json,
    )


def register(scout_group: click.Group, resume_group: click.Group) -> None:
    """Put ``resume brief | store | pick`` and ``suggestions`` on the tree. ``resume tailor`` is not touched here (the module text)."""

    resume_group.add_command(resume_brief_command)
    resume_group.add_command(resume_store_command)
    resume_group.add_command(resume_pick_command)
    scout_group.add_command(suggestions_group)


__all__ = [
    "TAILORING_REMOVED",
    "TAILORING_REMOVED_LINE",
    "TAILOR_IN_RENAMED_LINE",
    "register",
    "resume_brief_command",
    "resume_pick_command",
    "resume_store_command",
    "resume_tailor_removed_command",
    "store_resume",
    "suggestions_group",
]
