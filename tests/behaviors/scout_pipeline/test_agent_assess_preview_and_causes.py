"""0110-10-13: an agent can tell what an assessment sends, and each failure Scout controls is typed. Synthetic only.

The END outcomes, through the product's own paths (the CLI's JSON, the batch's
``assessed.failed``, the API's error body), with the scripted fixture model:

* the no-call preview (``status: "ask"``) of ``gigai scout jobs assess`` carries a
  stable ``model_input_summary``: profile id and label, resume source
  (``profile_view`` | ``master_evidence``), whether answers and stories go with
  it, the model target, whether a public fetch is needed. It holds none of the
  user's text, no model is called for it, and the terminal says the same facts
  above the question.
* each typed cause (``model_target_unavailable``, ``model_denied``,
  ``model_unavailable``, ``assess_timeout``, ``model_output_invalid``,
  ``assessment_not_stored``) says whether a model call started, whether it may
  have used tokens, that no fresh assessment was stored, and the next action:
  in the CLI's JSON error, in a batch's failed item, and in the API's error
  body. Nothing was stored for the job, as the facts say.
"""

from __future__ import annotations

from http import HTTPStatus
import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.adapters.port import ModelInvocationError
from gigai.scout import assess_causes, assess_master, assess_preview, posting_search, postings, scout_new
from gigai.scout.find_jobs.api.assess import write_assess_error
from gigai.scout.master_store import import_master
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.quick_assess import QuickAssessError, read_quick_assessment

from tests.support.greenhouse_fixtures import gh_job, gh_url, seed_greenhouse
from tests.support.pipeline_fixtures import ANSWER, MARKERS, RESUME
from tests.support.posting_fixtures import NOW, SECOND_LABEL, TITLE_BOTH, TITLE_SECOND_ONLY, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job

_FIRST, _SECOND = job_url("acme", 1), job_url("acme", 2)
_MASTER = """## Experience

### Northwind Labs
Staff Engineer | 2023 - Present

- Own the Python inference services behind 40 product teams.
- Wrote the Terraform modules every Kubernetes cluster is built from.

## Skills

- Platform: Python, Kubernetes, Terraform
"""


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fixture.seed("acme", [lever_job("acme", 1), lever_job("acme", 2, title=TITLE_SECOND_ONLY)], seen_at=days_ago(1))
    postings.refresh(fixture.home_root, fixture.target, now=NOW)  # the read model, as any `scout jobs list` builds it
    return fixture


def _invoke(fx: PostingsFixture, *args: str):
    return CliRunner().invoke(cli, ["scout", *args, "--home", str(fx.home_root), "--target", str(fx.target)])


def _json(fx: PostingsFixture, *args: str, exit_code: int = 0) -> dict:
    result = _invoke(fx, *args, "--json")
    assert result.exit_code == exit_code, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _summary(fx: PostingsFixture, profiles: list[dict[str, object]], **changed: object) -> dict[str, object]:
    """The golden summary: every key, in the fixture's terms (one saved answer, one saved story, the local model)."""

    count = sum(int(item["postings"]) for item in profiles)  # type: ignore[call-overload]
    return {
        "schema_version": "scout-assess-input:1",
        "postings": count,
        "model_calls": count,
        "model_target": "ollama_local",
        "model_target_runs": "local",
        "profiles": profiles,
        "sends": ["stored_posting", "resume", "search_preferences", "answers", "stories"],
        "search_preferences": ["sponsorship", "countries", "location", "titles", "work_mode"],
        "contact_lines": "removed_by_pattern",
        "answers_used": True,
        "answers_saved": 1,
        "stories_used": True,
        "stories_saved": 1,
        "public_fetch_needed": False,
        "public_fetch_postings": 0,
        **changed,
    }


def _default_label(fx: PostingsFixture) -> str:
    return next(item["label"] for item in posting_search.search_postings(fx.home_root, fx.target, now=NOW)["profiles"] if item["profile_id"] == fx.default_profile_id)  # type: ignore[index,union-attr]


# --- (2) the no-call preview's model_input_summary ---------------------------------------------------


def test_the_ask_preview_says_what_would_be_sent_and_none_of_the_users_text(fx: PostingsFixture) -> None:
    calls = fx.base.model.calls
    label = _default_label(fx)

    ask = _json(fx, "jobs", "assess", _FIRST, _SECOND)

    assert ask["status"] == "ask" and ask["assessed"] is None
    assert ask["model_input_summary"] == _summary(fx, [
        {"profile_id": fx.default_profile_id, "label": label, "postings": 1, "resume_source": "profile_view"},
        {"profile_id": fx.second_profile_id, "label": SECOND_LABEL, "postings": 1, "resume_source": "profile_view"},
    ])
    # The same object from the builder the API's POST /api/postings/assess answers with.
    built = posting_search.assess_these(fx.home_root, fx.target, jobs=[_FIRST, _SECOND], now=NOW)
    assert built["model_input_summary"] == ask["model_input_summary"]
    scout_new.check_response(built)  # still one label: nothing of the user's is in it
    # None of the user's text: no resume line, no answer, no story; ids, labels, counts and names only.
    dumped = json.dumps(ask["model_input_summary"])
    for private in (ANSWER, *MARKERS, "Moved inference to GCP", *[line.strip("- ") for line in RESUME.splitlines() if len(line) > 20]):
        assert private not in dumped, private
    # No model call was made for it, and nothing was assessed.
    assert fx.base.model.calls == calls
    assert read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, _FIRST) is None

    # One posting, for one profile: only that profile is named.
    one = _json(fx, "jobs", "assess", _SECOND)
    assert one["model_input_summary"] == _summary(fx, [
        {"profile_id": fx.second_profile_id, "label": SECOND_LABEL, "postings": 1, "resume_source": "profile_view"},
    ])

    # After approval the batch ran: there is no preview to give.
    done = _json(fx, "jobs", "assess", _FIRST, "--yes")
    assert done["status"] == "assessed" and done["model_input_summary"] is None
    # Nothing left to assess: no question, no preview.
    nothing = _json(fx, "jobs", "assess", _FIRST)
    assert nothing["status"] == "nothing_to_assess" and nothing["model_input_summary"] is None


def test_the_preview_names_the_master_evidence_view_and_a_posting_that_is_fetched_first(
    fx: PostingsFixture, tmp_path: Path
) -> None:
    source = tmp_path / "master.md"
    source.write_text(_MASTER, encoding="utf-8")
    assert import_master(home_root=fx.home_root, target=fx.target, source=source, gig_id=fx.base.gig.resolved.gig_id).status == "created"
    # A Greenhouse posting with no description stored: it is fetched from its public board before it is assessed.
    seed_greenhouse(fx, "nodesc", [gh_job("nodesc", 301, TITLE_BOTH)], seen_at=days_ago(1))
    bare = gh_url("nodesc", 301)
    calls = fx.base.model.calls

    ask = _json(fx, "jobs", "assess", _FIRST, bare, "--profile", fx.default_profile_id)

    assert ask["status"] == "ask"
    assert ask["model_input_summary"] == _summary(
        fx,
        [{"profile_id": fx.default_profile_id, "label": _default_label(fx), "postings": 2, "resume_source": "master_evidence"}],
        public_fetch_needed=True, public_fetch_postings=1,
    )
    assert fx.base.model.calls == calls
    # The name is the gate the assessment itself uses: it agrees with what a call would read.
    profile = next(view.record for view in postings.active_profiles(fx.home_root, fx.target)[1] if view.profile_id == fx.default_profile_id)
    assert assess_master.assess_input(home_root=fx.home_root, target=fx.target, profile=profile, title="Staff AI Engineer", posting_text="Python") is not None
    assert assess_master.resume_source(home_root=fx.home_root, target=fx.target, profile=None) == "profile_view"  # a pasted resume


def test_the_terminal_says_the_same_facts_above_the_question(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    # The command reads the real clock and the fixture's postings have fixed dates: inside 7 days of them the terminal
    # also says "Ranking is still running: ..." under the question (0.1.11.2, pinned in test_rank_order_and_top_batch).
    # Ranking off, so this says the same on any day.
    monkeypatch.setenv(PIPELINE_ENV, "off")
    label = _default_label(fx)

    shown = _invoke(fx, "jobs", "assess", _FIRST)

    assert shown.exit_code == 0, shown.output
    lines = shown.output.splitlines()
    assert lines[:9] == [
        "What this sends to your model target, ollama_local (Ollama on this computer; nothing leaves it), one call per posting:",
        "  - the stored posting",
        f"  - profile {label} ({fx.default_profile_id}; an id, not contact data), 1 posting: this profile's own resume (profile_view)",
        "    contact lines are removed from the resume by pattern, which can miss an unusual name or contact format",
        "  - your search preferences: sponsorship, countries, location, titles, work mode",
        "  - your saved answers: 1",
        "  - your saved stories that match a posting: of 1 saved",
        "  Public fetch first: none needed; every posting's text is stored.",
        f"Assess 1 posting ({label} 1)? ~1 call",
    ]
    assert lines[9] == "  Nothing was assessed. Yes: run the same command with --yes."


def test_in_a_terminal_the_facts_come_before_the_y_n_prompt(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    # A terminal: the command asks and waits (the runner's stdin says it is one).
    monkeypatch.setattr("click.testing._NamedTextIOWrapper.isatty", lambda self: True, raising=False)
    calls = fx.base.model.calls

    def run(answer: str):
        result = CliRunner().invoke(cli, ["scout", "jobs", "assess", _FIRST, "--home", str(fx.home_root), "--target", str(fx.target)], input=answer)
        assert result.exit_code == 0, result.output
        return result.output.splitlines()

    no = run("n\n")
    prompt = next(index for index, line in enumerate(no) if line.startswith("Assess 1 posting (") and "[y/N]" in line)
    assert prompt == 8  # the eight lines of what would be sent, then the question
    assert no[0].startswith("What this sends to your model target, ollama_local") and no[7].startswith("  Public fetch first:")
    assert fx.base.model.calls == calls  # a no assesses nothing

    yes = run("y\n")
    assert yes[0].startswith("What this sends to your model target, ollama_local")
    assert any(line.startswith("Assess 1 posting (") and "[y/N]" in line for line in yes) and "Assessed 1 of 1." in yes
    assert fx.base.model.calls == calls + 1


def test_summary_lines_for_a_cli_login_a_master_and_nothing_saved() -> None:
    summary = {
        "model_target": "codex_cli",
        "profiles": [{"profile_id": "profile_1", "label": "Staff Engineer", "postings": 3, "resume_source": "master_evidence"}],
        "answers_used": False, "answers_saved": 0, "stories_used": False, "stories_saved": 0,
        "public_fetch_needed": True, "public_fetch_postings": 2,
    }
    assert assess_preview.summary_lines(summary) == [
        "What this sends to your model target, codex_cli (the Codex CLI on your own login; the text goes to OpenAI), one call per posting:",
        "  - the stored posting",
        "  - profile Staff Engineer (profile_1; an id, not contact data), 3 postings: the lines of your master resume picked for each posting (master_evidence)",
        "    contact lines are removed from the resume by pattern, which can miss an unusual name or contact format",
        "  - your search preferences: sponsorship, countries, location, titles, work mode",
        "  - your saved answers: none saved",
        "  - your saved stories that match a posting: none saved",
        "  Public fetch first: 2 postings have no stored text; each is fetched from its public board (one request each).",
    ]


# --- (4) typed causes --------------------------------------------------------------------------------

_NO_CALL = {"model_call_started": False, "may_have_used_tokens": False, "fresh_assessment_stored": False}
_CALLED = {"model_call_started": True, "may_have_used_tokens": True, "fresh_assessment_stored": False}
#: The golden facts and next action per typed cause, spelled out (never read from the table under test).
GOLDEN: dict[str, dict[str, object]] = {
    "model_target_unavailable": {
        **_NO_CALL,
        "next_action": "Fix the model target, then assess again: `gigai models` shows what is configured and `gigai setup` changes it "
        "(a codex_cli or claude_cli target needs that command on PATH and logged in).",
    },
    "model_denied": {
        **_NO_CALL,
        "next_action": "GigAI's own settings refused this call before anything was sent. Ask the user to allow the model target in "
        "`gigai setup` or to pick another one; do not retry unchanged.",
    },
    "model_unavailable": {
        **_CALLED,
        "next_action": "The model target did not answer. Check that it is running and logged in (`gigai models`), then assess again; "
        "that is one more model call.",
    },
    "assess_timeout": {
        **_CALLED,
        "next_action": "The model call ran out of time. Check `assessed_at` for this job first; if it is unchanged, assess again "
        "(one more model call) or pick a faster model target.",
    },
    "model_output_invalid": {
        **_CALLED,
        "next_action": "The model answered twice with something that is not an assessment. Assess again (one more model call) or "
        "pick another model target.",
    },
    "assessment_not_stored": {
        **_CALLED,
        "next_action": "The model answered but the result could not be written. Check free disk space and that the GigAI home is "
        "writable, then assess again; that is one more model call.",
    },
}


def _break(code: str, fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the next assessment fail with ``code``, the way the product meets it."""

    model = fx.base.model
    if code == "model_target_unavailable":
        # The configured target cannot be resolved: what a CLI target that is not on PATH answers, before any call.
        from gigai.scout import proposal_execution

        def refuse(_config, name):
            raise proposal_execution.ScoutProposalExecutionError("scout_model_target_unavailable", "codex executable is not available on PATH")

        monkeypatch.setattr(proposal_execution, "_resolve_configured_target_name_for_adapter", refuse)
    elif code == "model_denied":
        denied = ModelInvocationError("network denied by policy")
        denied.code = "network_denied"  # type: ignore[attr-defined]
        model.fail_next = denied
    elif code == "model_unavailable":
        model.fail_next = ModelInvocationError("local Ollama transport request failed")
    elif code == "assess_timeout":
        model.fail_next = TimeoutError("read timed out")
    elif code == "model_output_invalid":
        model.assessed = "this is not an assessment"
    elif code == "assessment_not_stored":
        def full(_path, _data):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("gigai.scout.quick_assess.atomic_write", full)
    else:  # pragma: no cover - a cause added to GOLDEN needs its own way to happen
        raise AssertionError(code)


@pytest.mark.parametrize("code", sorted(GOLDEN))
def test_each_typed_cause_says_its_three_facts_and_the_next_action_in_the_cli_json(
    code: str, fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _break(code, fx, monkeypatch)

    # One job, `gigai scout assess`: the error object.
    failed = _json(fx, "assess", "--job-url", _FIRST, "--profile", fx.default_profile_id, exit_code=1)

    assert failed["status"] == "error"
    error = failed["error"]
    assert error["code"] == code and error["message"]
    assert {key: value for key, value in error.items() if key not in ("code", "message")} == GOLDEN[code]
    # What the facts say is what happened: nothing was stored for the job.
    assert read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, _FIRST) is None


@pytest.mark.parametrize("code", sorted(GOLDEN))
def test_each_typed_cause_is_on_the_failed_item_of_a_batch(code: str, fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    _break(code, fx, monkeypatch)

    done = _json(fx, "jobs", "assess", _FIRST, "--yes", "--actor", "agent")

    assert done["status"] == "assessed" and done["assessed"]["assessed"] == 0
    assert done["assessed"]["failed"] == [
        {"job_identity": _FIRST, "profile_id": fx.default_profile_id, "error_code": code, **GOLDEN[code]}
    ]
    assert read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, _FIRST) is None
    row = next(item for item in done["postings"]["rows"] if item["job_identity"] == _FIRST)
    assert row["state"] == "not_assessed" and row["assessment"] is None  # no false "done": there is no assessed_at to show


def test_a_failure_that_is_not_a_typed_cause_keeps_its_shape(fx: PostingsFixture) -> None:
    failed = _json(fx, "assess", "--job-url", _FIRST, "--profile", "profile_00000000-0000-4000-8000-000000000000", exit_code=1)
    assert set(failed["error"]) == {"code", "message"} and failed["error"]["code"] == "profile_not_found"
    assert assess_causes.cause_fields("profile_not_found") == {} and assess_causes.cause_lines(None) == ()


def test_the_terminal_says_the_facts_and_the_next_action_under_the_message(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    _break("assess_timeout", fx, monkeypatch)
    one = _invoke(fx, "assess", "--job-url", _FIRST, "--profile", fx.default_profile_id)
    assert one.exit_code == 1
    assert one.output.splitlines()[-3:] == [
        "Error: the model call timed out; try again or pick a faster model target",
        "A model call started and may have used tokens. No new assessment was stored.",
        "Next: " + str(GOLDEN["assess_timeout"]["next_action"]),
    ]

    _break("model_denied", fx, monkeypatch)
    batch = _invoke(fx, "jobs", "assess", _FIRST, "--yes")
    assert batch.exit_code == 0
    assert f"  not assessed (model_denied): {_FIRST}" in batch.output
    assert (
        "  model_denied: No model call was made and no tokens were used. No new assessment was stored. Next: "
        + str(GOLDEN["model_denied"]["next_action"])
    ) in batch.output.splitlines()


class _Handler:
    def __init__(self) -> None:
        self.sent: list[tuple[object, ...]] = []

    def _error(self, status, code, message) -> None:
        self.sent.append((int(status), code, message))

    def _error_with_extra(self, status, code, message, extra) -> None:
        self.sent.append((int(status), code, message, extra))


@pytest.mark.parametrize("code", sorted(GOLDEN))
def test_the_api_error_body_of_a_failed_assessment_carries_the_cause(code: str) -> None:
    handler = _Handler()
    write_assess_error(handler, HTTPStatus.SERVICE_UNAVAILABLE, QuickAssessError(code, "what went wrong"))
    assert handler.sent == [(503, code, "what went wrong", GOLDEN[code])]


def test_the_api_error_body_of_any_other_failure_is_unchanged() -> None:
    handler = _Handler()
    write_assess_error(handler, HTTPStatus.NOT_FOUND, QuickAssessError("profile_not_found", "no such profile"))
    assert handler.sent == [(404, "profile_not_found", "no such profile")]


def test_the_table_is_exactly_the_typed_causes() -> None:
    assert set(assess_causes.CAUSES) == set(GOLDEN)
    assert assess_causes.TIMEOUT == "assess_timeout"
