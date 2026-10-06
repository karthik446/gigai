"""0.1.11.4 E1 (scope item 10): a resume the user edited is never replaced by anything but ``--use-proposed``.

On the END outcome (the stored file's bytes and mtime), on the synthetic home of ``test_assessment_v9_flow`` (one
profile, a small invented master, one posting, a scripted model), through the real CLI, the background pipeline OFF.

- a proposal made BEFORE an edit is never applied: the edit clears it (and its sibling file), and a stale proposal
  that is still there is refused in plain words, the stored resume untouched;
- an edited resume survives (bytes and mtime) a plain ``resume pick``, a master change, and ``--refresh``,
  ``--draft`` and ``--shorten`` on the stale assessment;
- a stored file that cannot be read is the user's: no pick, no assessment and no ``--use-proposed`` writes over it,
  and the refusal says so in plain words;
- ``resume brief`` records the stored resume's revision in a comment above the resume; a file made from an OLDER
  revision is refused with a sentence that says what to do (never the "longer than 400 characters" bound), a file
  with no such comment stores as before, ``--force`` stores it anyway, and the comment never reaches the stored
  markdown, the job's ``resume.md`` or the PDF.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.scout import job_actions, suggestions
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import read_tailored_resume

from tests.behaviors.scout_find_jobs.test_assessment_v9_flow import (  # noqa: F401 - `fx` is the fixture
    _URL, KUBERNETES_LINE, OWN_LINE, PYTHON_LINE, TERRAFORM_LINE, _assess, _edit, _ids, _record, _resume_path, _v9_answer, fx,
)
from tests.support.posting_fixtures import PostingsFixture

_STALE_PROPOSAL = "the resume changed after this suggestion was made; pick again"
_UNREADABLE = "the stored resume could not be read; it was left as it is"
_STALE_FILE = (
    "This file was made from an older version of the resume. Get the current one with "
    "gigai scout resume brief --job-url URL --out FILE, then make your edits again."
)
_MARK = "gigai-resume:"


@pytest.fixture(autouse=True)
def _pipeline_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PIPELINE_ENV, "off")


def _invoke(fx: PostingsFixture, *args: str, as_json: bool = True):
    return CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), *(["--json"] if as_json else [])])


def _ok(fx: PostingsFixture, *args: str) -> dict:
    result = _invoke(fx, *args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _refused(fx: PostingsFixture, *args: str) -> dict:
    result = _invoke(fx, *args)
    assert result.exit_code == 1, result.output
    return json.loads(result.output.strip().splitlines()[-1])["error"]


def _pick(fx: PostingsFixture, *args: str) -> dict:
    return _ok(fx, "resume", "pick", "--job-url", _URL, *args)


def _store(fx: PostingsFixture, tmp_path: Path, markdown: str, *more: str) -> dict:
    path = tmp_path / "handed-back.md"
    path.write_text(markdown, encoding="utf-8")
    return _ok(fx, "resume", "store", "--in", str(path), "--job-url", _URL, "--as", "operator", *more)


def _store_refused(fx: PostingsFixture, tmp_path: Path, markdown: str, *more: str) -> dict:
    path = tmp_path / "handed-back.md"
    path.write_text(markdown, encoding="utf-8")
    return _refused(fx, "resume", "store", "--in", str(path), "--job-url", _URL, "--as", "operator", *more)


def _without(markdown: str, line: str) -> str:
    kept = [item for item in markdown.splitlines() if line not in item]
    assert len(kept) < len(markdown.splitlines()), f"the resume prints {line!r}"
    return "\n".join(kept) + "\n"


def _state(fx: PostingsFixture) -> tuple[bytes, int, bytes, int]:
    """The stored resume as it is on disk: the bytes and mtime of its JSON and of its markdown."""

    stored, markdown = _resume_path(fx), _resume_path(fx).with_suffix(".md")
    return stored.read_bytes(), stored.stat().st_mtime_ns, markdown.read_bytes(), markdown.stat().st_mtime_ns


def _reworded_too_long(fx: PostingsFixture, markdown: str) -> str:
    """``markdown`` with one line reworded past the 400-character bound, citing its master line (what an agent hands back)."""

    long = PYTHON_LINE.rstrip(".") + ", " + "and kept every one of them running for the teams that depend on them, " * 6 + "to this day."
    lines = [f"- {long} <!-- src: {_ids(fx)[PYTHON_LINE]} -->" if PYTHON_LINE in line else line for line in markdown.splitlines()]
    return "\n".join(lines) + "\n"


def _brief_resume(fx: PostingsFixture) -> str:
    return _ok(fx, "resume", "brief", "--job-url", _URL)["resume"]["markdown"]


def _edited_with_a_proposal_from_before(fx: PostingsFixture, tmp_path: Path) -> tuple[dict, bytes]:
    """The incident: a first edit, a re-pick that waits as proposed, then a SECOND edit. Returns the old proposal and its file."""

    _assess(fx, _v9_answer(fx))
    picked = _pick(fx)["resume"]["markdown"]
    _store(fx, tmp_path, _without(picked, KUBERNETES_LINE))
    waiting = _pick(fx, "--refresh")
    assert waiting["proposed"] is not None and waiting["resume"]["replaceable"] is False
    record = _record(fx)
    sibling = suggestions.proposed_resume_path(Path(record.stored_path))
    old = (dict(record.proposed), sibling.read_bytes())
    second = _store(fx, tmp_path, _without(picked, TERRAFORM_LINE))
    assert second["changed"] is True and second["edited"]["written_by"] == "operator"
    return old


def test_an_edit_clears_the_proposal_that_was_made_before_it(fx: PostingsFixture, tmp_path: Path) -> None:
    _edited_with_a_proposal_from_before(fx, tmp_path)
    mine = _state(fx)
    record = _record(fx)

    assert record.proposed is None and not suggestions.proposed_resume_path(Path(record.stored_path)).exists()
    assert _pick(fx)["proposed"] is None
    assert _refused(fx, "resume", "pick", "--job-url", _URL, "--use-proposed")["code"] == "no_proposed_resume"
    assert _state(fx) == mine
    stored = read_tailored_resume(_resume_path(fx))
    assert stored.edited is not None and stored.producer.callable != "scout.pick" and TERRAFORM_LINE not in stored.markdown


def test_use_proposed_refuses_a_proposal_made_before_the_stored_resume_changed(fx: PostingsFixture, tmp_path: Path) -> None:
    proposed, sibling_bytes = _edited_with_a_proposal_from_before(fx, tmp_path)
    # The stale proposal is still there (a home written before this fix, or an edit that could not clear it).
    record = _record(fx)
    sibling = suggestions.proposed_resume_path(Path(record.stored_path))
    with suggestions.record_write_lock(Path(record.stored_path)):
        suggestions.save_record(suggestions.replace(suggestions.read_record(Path(record.stored_path)), proposed=proposed))
        sibling.write_bytes(sibling_bytes)
    mine = _state(fx)

    error = _refused(fx, "resume", "pick", "--job-url", _URL, "--use-proposed")

    assert _state(fx) == mine, "a stale proposal is never applied"
    assert error["code"] == "proposal_stale" and _STALE_PROPOSAL in error["message"]
    stored = read_tailored_resume(_resume_path(fx))
    assert stored.edited is not None and TERRAFORM_LINE not in stored.markdown and KUBERNETES_LINE in stored.markdown
    plain = _invoke(fx, "resume", "pick", "--job-url", _URL, "--use-proposed", as_json=False)
    assert plain.exit_code == 1 and _STALE_PROPOSAL in plain.output
    # Picked again against the resume as it is now, the new proposal can be taken.
    again = _pick(fx, "--refresh")
    assert again["proposed"] is not None and _state(fx) == mine
    taken = _pick(fx, "--use-proposed")
    assert taken["proposed"] is None and taken["resume"]["edited"] is None


def test_an_edited_resume_survives_plain_picks_a_master_change_and_every_pick_step_on_a_stale_assessment(fx: PostingsFixture, tmp_path: Path) -> None:
    _assess(fx, _v9_answer(fx))
    picked = _pick(fx)["resume"]["markdown"]
    _store(fx, tmp_path, _without(picked, KUBERNETES_LINE))
    mine = _state(fx)
    answer = _v9_answer(fx)  # the model's answer names the master's lines as they are now

    assert _pick(fx)["resume"]["edited"]["written_by"] == "operator" and _pick(fx)["resume"]["replaceable"] is False
    assert _state(fx) == mine, "a plain pick reads"
    # The master changes: the assessment is stale for its resume now.
    _edit(fx, _ids(fx)[OWN_LINE], "Own the Python inference services behind 45 product teams.")
    stale = _pick(fx)
    assert stale["stale"] and _pick(fx)["stale"] == stale["stale"]
    assert _state(fx) == mine, "a plain pick on a stale assessment reads: nothing is recomputed, nothing is written"
    for step in ("--refresh", "--draft", "--shorten"):
        result = _invoke(fx, "resume", "pick", "--job-url", _URL, step)
        assert result.exit_code in (0, 1), result.output
        assert _state(fx) == mine, f"{step} on a stale assessment leaves the edited resume as it is"
    stored = read_tailored_resume(_resume_path(fx))
    assert stored.edited is not None and stored.edited.written_by == "operator" and KUBERNETES_LINE not in stored.markdown
    # Assessed again on the new master: the new selection waits; the edit is still the stored resume.
    _assess(fx, answer)
    assert _state(fx) == mine and _record(fx).proposed is not None


def test_the_incident_a_fitted_edit_then_a_master_line_then_every_pick_step_by_cli_and_by_the_routes_own_call(fx: PostingsFixture, tmp_path: Path) -> None:
    """The sequence of the report: `resume store --fit`, a line added to the master, then every pick step.

    ``POST /api/job-resumes/pick`` is ONE route for every step (the step is its body's ``action``) and calls
    ``job_actions.pick_action``: it is called here as the route calls it. Every step but ``use_proposed`` leaves the
    edit as it is; ``use_proposed`` is the one that replaces it, on a proposal made beside THIS edit.
    """

    _assess(fx, _v9_answer(fx))
    picked = _pick(fx)["resume"]["markdown"]
    stored = _store(fx, tmp_path, _without(picked, KUBERNETES_LINE), "--fit")
    assert stored["changed"] is True and stored["edited"]["written_by"] == "operator"
    mine = _state(fx)
    added = _ok(fx, "resume", "master", "add", "--section", "other", "--text", "Earlier: support engineer roles, 2010 - 2015.")
    assert added["changes"]["added"] == 1

    for step in ((), ("--refresh",), ("--draft",), ("--shorten",), ()):
        result = _invoke(fx, "resume", "pick", "--job-url", _URL, *step)
        assert result.exit_code in (0, 1), result.output
        assert _state(fx) == mine, f"`resume pick {' '.join(step)}` replaced a resume the user edited"
    for action in ("refresh", "draft", "shorten", "dismiss_proposed", "refresh"):
        try:
            job_actions.pick_action(fx.home_root, fx.target, _URL, action)
        except job_actions.JobActionError:
            pass  # a refused step (draft_not_needed, resume_short_already ...) writes nothing either
        assert _state(fx) == mine, f"POST /api/job-resumes/pick {action} replaced a resume the user edited"
    kept = read_tailored_resume(_resume_path(fx))
    assert kept.edited is not None and kept.producer.callable != "scout.pick" and KUBERNETES_LINE not in kept.markdown
    # The proposal the last refresh made waits beside THIS edit: taking it is the one explicit step that replaces it.
    view = job_actions.pick_view(fx.home_root, fx.target, _URL)
    assert view["proposed"] is not None and view["resume"]["replaceable"] is False
    assert _record(fx).proposed["against"] == suggestions.revision_of(kept)
    taken = job_actions.pick_action(fx.home_root, fx.target, _URL, "use_proposed")
    assert taken["resume"]["edited"] is None and taken["resume"]["made_by"] == "scout.pick" and _state(fx) != mine


def test_a_remove_and_a_restore_on_the_page_clear_the_proposal_too(fx: PostingsFixture, tmp_path: Path) -> None:
    from dataclasses import replace

    from gigai.scout.tailor_length_store import change_stored_length
    from gigai.scout.tailor_selection_edit import change_stored_selection
    from gigai.scout.tailored_resume import TailorEdit, save_tailor_response

    _assess(fx, _v9_answer(fx))
    # The pick's resume with the user's mark on it (a line choice on the page): it keeps its Picked / Left out.
    save_tailor_response(replace(read_tailored_resume(_resume_path(fx)), edited=TailorEdit("operator", "2026-10-05T12:00:00Z")), home_root=fx.home_root)
    assert _pick(fx, "--refresh")["proposed"] is not None
    record = _record(fx)
    sibling = suggestions.proposed_resume_path(Path(record.stored_path))
    assert sibling.is_file()

    edit = change_stored_selection(
        fx.home_root, fx.target, profile_id=fx.default_profile_id, job_identity=record.job_identity, use="remove", item_id=_ids(fx)[TERRAFORM_LINE],
    )

    assert edit.applied and edit.changed and TERRAFORM_LINE not in read_tailored_resume(_resume_path(fx)).markdown
    assert _record(fx).proposed is None and not sibling.exists(), "the proposal was made beside the resume as it was before the Remove"
    assert _refused(fx, "resume", "pick", "--job-url", _URL, "--use-proposed")["code"] == "no_proposed_resume"
    # A Restore / cut that changes nothing stores nothing and leaves a proposal alone.
    assert _pick(fx, "--refresh")["proposed"] is not None
    mine = _state(fx)
    change_stored_length(fx.home_root, fx.target, profile_id=fx.default_profile_id, job_identity=record.job_identity, use="restore")
    assert _state(fx) == mine and _record(fx).proposed is not None


def test_a_stored_resume_that_cannot_be_read_is_never_written_over(fx: PostingsFixture, tmp_path: Path) -> None:
    _assess(fx, _v9_answer(fx))
    picked = _pick(fx)["resume"]["markdown"]
    _store(fx, tmp_path, _without(picked, KUBERNETES_LINE))
    _pick(fx, "--refresh")  # a proposal waits beside the edit
    path = _resume_path(fx)
    damaged = path.read_bytes()[:-40]  # cut short: no longer JSON
    path.write_bytes(damaged)
    before = (damaged, path.stat().st_mtime_ns)

    def kept() -> bool:
        return (path.read_bytes(), path.stat().st_mtime_ns) == before

    for step in ("--refresh", "--shorten", "--use-proposed"):
        error = _refused(fx, "resume", "pick", "--job-url", _URL, step)
        assert kept(), f"{step} wrote over a stored resume it could not read"
        assert error["code"] == "stored_resume_unreadable" and _UNREADABLE in error["message"], error
    _assess(fx, _v9_answer(fx))
    assert kept(), "a new assessment wrote over a stored resume it could not read"
    view = _pick(fx)
    assert kept() and view["resume"] is None and view["resume_unreadable"] is True
    plain = _invoke(fx, "resume", "pick", "--job-url", _URL, as_json=False)
    assert _UNREADABLE in plain.output and "none stored" not in plain.output
    # The user's own hand-back is refused too, in the same words, until they say so with --force.
    error = _store_refused(fx, tmp_path, _without(picked, TERRAFORM_LINE))
    assert kept() and error["code"] == "stored_resume_unreadable" and _UNREADABLE in error["message"]
    # The rule itself: no file may be replaced, a file that cannot be read may not.
    assert suggestions.is_replaceable(None, _record(fx), path=path) is False and suggestions.is_replaceable(None, _record(fx), path=tmp_path / "none.json") is True
    forced = _store(fx, tmp_path, _without(picked, TERRAFORM_LINE), "--force")
    assert forced["changed"] is True and read_tailored_resume(path).edited.written_by == "operator"


def test_the_brief_records_the_resumes_revision_and_a_file_from_an_older_one_gets_a_plain_message(fx: PostingsFixture, tmp_path: Path) -> None:
    _assess(fx, _v9_answer(fx))
    old = _brief_resume(fx)
    stored = read_tailored_resume(_resume_path(fx))
    first = old.splitlines()[0]
    assert first.startswith(f"<!-- {_MARK} ") and first.endswith(" -->") and f"updated_at={stored.updated_at}" in first and "sha256=" in first
    out = tmp_path / "brief.md"
    assert _invoke(fx, "resume", "brief", "--job-url", _URL, "--out", str(out), as_json=False).exit_code == 0
    assert first in out.read_text(encoding="utf-8").splitlines(), "resume brief --out writes the comment above the resume"
    assert first not in _resume_path(fx).with_suffix(".md").read_text(encoding="utf-8")

    # Handed back as the brief gave it: nothing changes, the comment is read and not stored.
    assert _store(fx, tmp_path, old)["changed"] is False
    # The stored resume moves on (another edit), and the file made from the OLDER revision comes back with an edit of its own.
    _store(fx, tmp_path, _without(_brief_resume(fx), KUBERNETES_LINE))
    mine = _state(fx)
    late = _reworded_too_long(fx, old)
    error = _store_refused(fx, tmp_path, late)
    assert error["code"] == "resume_file_stale" and error["message"] == _STALE_FILE, error
    assert "400 characters" not in error["message"] and _state(fx) == mine
    assert _store_refused(fx, tmp_path, _without(old, TERRAFORM_LINE))["message"] == _STALE_FILE and _state(fx) == mine

    # A file with NO such comment is checked as before: it stores, and the over-long line is still the 400-character bound.
    bare = "\n".join(old.splitlines()[1:]).lstrip("\n") + "\n"
    assert _MARK not in bare and "400 characters" in _store_refused(fx, tmp_path, _reworded_too_long(fx, bare))["message"]
    assert _store(fx, tmp_path, _without(bare, TERRAFORM_LINE))["changed"] is True
    # --force stores a file made from an older revision.
    assert _store(fx, tmp_path, _without(old, PYTHON_LINE), "--force")["changed"] is True
    # The file made from the CURRENT brief stores without --force.
    assert _store(fx, tmp_path, _without(_brief_resume(fx), OWN_LINE))["changed"] is True


def test_the_revision_comment_never_reaches_the_stored_markdown_the_jobs_file_or_the_pdf(fx: PostingsFixture, tmp_path: Path) -> None:
    _assess(fx, _v9_answer(fx))
    stored = _store(fx, tmp_path, _without(_brief_resume(fx), KUBERNETES_LINE))
    assert stored["changed"] is True and _MARK not in stored["markdown"]
    assert _MARK not in _resume_path(fx).with_suffix(".md").read_text(encoding="utf-8") and _MARK.encode() not in _resume_path(fx).read_bytes()
    folder_file = Path(stored["folder_path"]).expanduser()
    assert folder_file.is_file() and _MARK not in folder_file.read_text(encoding="utf-8")
    assert _MARK not in _pick(fx)["resume"]["markdown"]
    out = tmp_path / "resume.pdf"
    made = _ok(fx, "resume", "pdf", "--job-url", _URL, "--out", str(out), "--no-header")
    assert out.read_bytes().startswith(b"%PDF") and made["pages"] >= 1
    pypdf = pytest.importorskip("pypdf")
    text = "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(os.fspath(out)).pages)
    assert "gigai-resume" not in text and "sha256" not in text and PYTHON_LINE.split(";")[0] in " ".join(text.split())
