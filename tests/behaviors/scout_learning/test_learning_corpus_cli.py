"""0.1.11.10 Part B, packet G1: ``gigai scout learning corpus "ROLE" [--json] [--yes]`` against a temporary home.

1. ESTIMATE FIRST: without ``--yes`` the command answers the estimate and the question and calls nothing; with no
   recorded ``learning`` call it says so; after a run the estimate comes from the recorded calls.
2. ``--yes`` runs steps 2 to 5: ``--json`` is the documented object, the text output prints counts and no posting
   sentence; a terminal is asked first and a "no" calls nothing.
3. METERING: every model call is one ``learning`` row of the project's metrics (numbers only); an answer that had
   to be asked for again is an error row.
4. NO RESUME: every resume reader raises here, and the command still runs; no prompt holds the resume's text.
5. NOTHING STORED: no pathway record, no file outside the metrics database.
6. Errors exit 1 with their code: contact data in the role, no search index, an unusable answer twice.

The model is a scripted fake on the one adapter seam; the boards, the resume and every sentence are made up.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from gigai.adapters.port import InvocationResult, ModelInvocationError, NormalizedUsage
from gigai.cli import cli
from gigai.scout import call_metrics, learning_corpus, learning_store
from gigai.scout.find_jobs import search_index

from tests.behaviors.scout_learning.test_learning_corpus import ACME, BOLT, TITLES_OK, concept, lever_job, seed_board, vocabulary
from tests.support.answers_stories_fixtures import config
from tests.support.scout_profile_fixtures import build_gig_with_resume

pytestmark = pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 9, 0), reason="needs FTS5")

ROLE = "MLOps engineer"
RESUME_MARKER = "Quillfeather inference lead"
#: Readers and strippers of the stored resume: none may be called by this command.
RESUME_READERS = (
    "gigai.scout.find_jobs.resume_input.resume_for_profile",
    "gigai.scout.find_jobs.resume_input.resolve_resume",
    "gigai.scout.proposal_execution.read_pinned_resume",
    "gigai.scout.master_store.load_master",
    "gigai.scout.resume_privacy.model_resume",
    "gigai.scout.interview_prep.resume.current_resume",
)


class SeamModel:
    """A scripted binding on ``proposal_execution.resolve_model_adapter``: the answers in order, every prompt kept."""

    def __init__(self) -> None:
        self.port = self
        self.answers: list[str] = []
        self.prompts: list[str] = []
        self.closed = 0
        #: Raised by the next call instead of answering, once.
        self.fail_next: BaseException | None = None

    def invoke(self, request: object) -> InvocationResult:
        self.prompts.append(request.prompt)  # type: ignore[attr-defined]
        if self.fail_next is not None:
            error, self.fail_next = self.fail_next, None
            raise error
        assert self.answers, "the model was called more often than the script allows"
        return InvocationResult(
            status="success", output_text=self.answers.pop(0), resolved_model="fixture-model", raw_usage={},
            normalized_usage=NormalizedUsage(1000, 200, 1200), cost_status="unavailable",
        )

    def request(self, *, role: str, prompt: str, required_capabilities: object = frozenset({"text"}), timeout_seconds: float | None = None) -> SimpleNamespace:
        return SimpleNamespace(prompt=prompt, role=role)

    def close(self) -> None:
        self.closed += 1


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A gig (a US setup, any work mode) with a resume, 17 postings with text on three synthetic boards, the index built, the fake model on the seam."""

    gig = build_gig_with_resume(tmp_path, resume_text=f"# Fixture Resume\n\n{RESUME_MARKER}. (fixture only.)\n".encode())
    home, target = gig.home_root, gig.target
    seed_board(home, "acme", ACME)
    seed_board(home, "bolt", BOLT)
    seed_board(home, "cove", [
        lever_job("cove", n, "ML Platform Engineer", 10 + n, f"Experience with Terraform is required. Posting {n}." if n <= 5 else f"You will own service {n}.")
        for n in range(1, 13)
    ])
    assert search_index.rebuild_from_index(home).available
    model = SeamModel()

    def resolve(_config: object, _adapter_target: str, **_kwargs: object) -> SeamModel:
        return model

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    monkeypatch.setattr("gigai.config.load_config", config)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the corpus command reads no resume")

    for name in RESUME_READERS:
        monkeypatch.setattr(name, refuse)
    yield SimpleNamespace(home=home, target=target, model=model)
    search_index.close(home)


def _script(fx: SimpleNamespace, *, titles: tuple[str, ...] = (TITLES_OK,)) -> None:
    fx.model.answers = [
        *titles,
        vocabulary(concept("terraform"), concept("kubernetes", "kubernetes", "k8s"), concept("mlflow"), pad_to=60),
        '{"concepts": []}',  # the follow-up ("own service" is uncovered seven times) adds nothing
    ]


def _run(fx: SimpleNamespace, *args: str):
    return CliRunner().invoke(cli, ["scout", "learning", "corpus", *args, "--home", str(fx.home), "--target", str(fx.target)], catch_exceptions=False)


def _json(result) -> dict[str, object]:
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1, result.output
    return json.loads(lines[0])


def _learning_rows(fx: SimpleNamespace) -> list[dict[str, object]]:
    return call_metrics.metrics_report(fx.home, fx.target, kind="learning")["comparison"]  # type: ignore[return-value]


def _files(root: Path) -> set[str]:
    return {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}


def test_the_estimate_comes_first_and_nothing_is_called_without_approval(fx: SimpleNamespace) -> None:
    asked = _run(fx, ROLE, "--json")
    assert asked.exit_code == 0, asked.output
    body = _json(asked)
    assert set(body) == {"schema_version", "status", "role_text", "model_target", "estimate", "sends", "question"}
    assert body["schema_version"] == "scout-learning-corpus:1" and body["status"] == "ask" and body["role_text"] == ROLE
    assert body["model_target"] == "ollama_local"
    # No learning call was ever recorded here: the calls are known, tokens and time are not, and the question says so.
    assert body["estimate"] == {"calls": 3, "max_calls": 5, "tokens": None, "seconds": None, "cost": None, "basis_calls": 0}
    assert body["question"] == {
        "text": "Read the stored postings for 'MLOps engineer' and count what they ask for? ~3 model calls (at most 5) "
        "(no recorded learning calls yet to estimate tokens or time from)"
    }
    assert "No resume, no answer and no story is read or sent." in body["sends"]  # type: ignore[operator]

    plain = _run(fx, ROLE)  # no terminal: the question is printed, nothing is asked, nothing is called
    assert plain.exit_code == 0 and "count what they ask for? ~3 model calls (at most 5)" in plain.output
    assert "This sends: the role as typed" in plain.output and plain.output.rstrip().endswith("Nothing was called. Approve with --yes.")
    assert fx.model.prompts == [] and _learning_rows(fx) == [] and learning_store.list_pathways(fx.home, fx.target) == []


def test_yes_runs_the_steps_meters_every_call_and_stores_nothing_else(fx: SimpleNamespace) -> None:
    _run(fx, ROLE, "--json")  # a read first: whatever a read creates on a fresh home is not this command's write
    before = _files(fx.home) | _files(fx.target)
    _script(fx)

    done = _run(fx, ROLE, "--yes", "--json")
    assert done.exit_code == 0, done.output
    body = _json(done)
    assert set(body) == {"schema_version", "status", "role_text", "titles", "corpus", "vocabulary", "threshold", "rule", "concepts", "below_threshold", "calls"}
    assert body["status"] == "done" and body["role_text"] == ROLE and body["calls"] == 3 and fx.model.closed == 1
    assert body["titles"] == {"phrases": ["mlops engineer", "ml platform engineer", "machine learning platform engineer"], "deny": ["robotics"]}
    corpus = body["corpus"]
    assert isinstance(corpus, dict)
    assert set(corpus) == {
        "candidates", "title_matches", "denied", "copies", "beyond_limit", "postings", "postings_in_window", "with_text",
        "with_text_in_window", "window_days", "limit", "basis", "read", "widen_below", "filters", "note",
    }
    # The only filter is the country of this setup (US only), never the work mode or the area ("Remote") of its Jobs
    # search, and no posted window: the Berlin posting is out, the Denver office one is in, the 120-day-old one is in.
    assert corpus["filters"] == {"countries": ["US"], "us_only": True, "work_mode": "any", "area": None}
    assert (corpus["postings"], corpus["with_text"], corpus["with_text_in_window"], corpus["denied"], corpus["copies"]) == (18, 17, 16, 1, 1)
    # 16 postings of the last 90 days have text: fewer than 25, so every date is read, and the note says so.
    assert (corpus["basis"], corpus["read"], corpus["widen_below"]) == ("any_date", 17, 25)
    assert corpus["note"] == (
        "Fewer than 25 postings of the last 90 days have stored text (16), so every date is read: 17 postings with stored text "
        "(of 18 stored for this role's titles; US or unclear location, any work mode)"
    )
    assert body["vocabulary"]["minimum"] == 25 and "HOW MANY: 25 to 150 concepts." in fx.model.prompts[1]  # type: ignore[index]
    assert body["vocabulary"]["returned"] == 60 and body["vocabulary"]["follow_up"] == "added"  # type: ignore[index]
    concepts = body["concepts"]
    assert isinstance(concepts, list) and [item["id"] for item in concepts] == ["terraform", "kubernetes"] and body["below_threshold"] == 1
    assert set(concepts[0]) == {
        "id", "display", "category", "phrases", "technical", "count_in90d", "n_in90d", "percent_in90d", "count_all", "n_all",
        "percent_all", "kept", "examples",
    }
    assert (concepts[0]["count_all"], concepts[0]["n_all"], concepts[0]["percent_all"]) == (6, 17, 35.3)
    assert all(set(example) == {"phrase", "company", "title", "url"} and len(example["phrase"]) <= 160 for item in concepts for example in item["examples"])
    assert "Own the model platform" not in done.output, "no posting text beyond the example sentences"

    # One metrics row per model call, kind learning, numbers only.
    (row,) = _learning_rows(fx)
    assert (row["kind"], row["model_target"], row["calls"], row["errors"], row["items"]) == ("learning", "ollama_local", 3, 0, 3)
    assert row["avg_input_tokens"] == 1000 and row["avg_output_tokens"] == 200
    # Nothing else was stored: no pathway record, and every new file is the metrics database's.
    assert learning_store.list_pathways(fx.home, fx.target) == []
    new = (_files(fx.home) | _files(fx.target)) - before
    assert new and all("pipeline" in name for name in new), sorted(new)

    # No prompt holds a word of the resume; the three prompts are the titles, the vocabulary and the follow-up.
    assert len(fx.model.prompts) == 3 and all(RESUME_MARKER not in prompt and "Fixture Resume" not in prompt for prompt in fx.model.prompts)
    assert fx.model.prompts[0].startswith("You are turning one job role") and "FOLLOW-UP:" in fx.model.prompts[2]

    # The next estimate is sized from the recorded calls: 3 calls of 1200 tokens.
    again = _json(_run(fx, ROLE, "--json"))
    assert again["status"] == "ask" and again["estimate"]["calls"] == 3 and again["estimate"]["tokens"] == 3600 and again["estimate"]["basis_calls"] == 3  # type: ignore[index]
    assert "~4k tokens" in again["question"]["text"] and "no recorded" not in again["question"]["text"]  # type: ignore[index]


def test_the_text_output_prints_counts_and_no_posting_sentence(fx: SimpleNamespace) -> None:
    _script(fx)
    done = _run(fx, ROLE, "--yes")
    assert done.exit_code == 0, done.output
    for part in (
        "Asking for the title phrases of this role...", "Reading the stored postings...", "Asking what 17 postings ask for...",
        "Role: MLOps engineer", "Filters: US only; work mode any",
        "Postings: 18 (17 posted in the last 90 days); 17 with stored text (16 in the last 90 days).",
        "Concepts: 2 named by at least 3 postings of the last 90 days or 4 of any date (1 below that; the model named 60).",
        "   6  37.5%     6  35.3%  tool           Terraform", "   3  18.8%     4  23.5%  tool           Kubernetes",
        "Model calls: 3. Nothing was stored.",
    ):
        assert part in done.output, (part, done.output)
    assert "Experience with Terraform is required" not in done.output and "jobs.lever.co" not in done.output


def test_a_terminal_is_asked_first_and_a_no_calls_nothing(fx: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    import click

    asked: list[tuple[str, bool]] = []
    answers = [False, True]

    def confirm(text: str, default: bool = False, **_kwargs: object) -> bool:
        asked.append((text, default))
        return answers.pop(0)

    monkeypatch.setattr(click, "confirm", confirm)
    monkeypatch.setattr("click.testing._NamedTextIOWrapper.isatty", lambda _self: True, raising=False)

    declined = _run(fx, ROLE)
    if not asked:
        pytest.skip("this click version gives the test runner no terminal to pretend with")
    assert declined.exit_code == 0 and fx.model.prompts == []
    assert asked[0][1] is False and asked[0][0].startswith("Read the stored postings for 'MLOps engineer' and count what they ask for")
    assert declined.output.index("This sends:") < declined.output.index("Nothing was called.") and declined.output.count("This sends:") == 1

    _script(fx)
    accepted = _run(fx, ROLE)
    assert accepted.exit_code == 0 and len(fx.model.prompts) == 3 and "Model calls: 3. Nothing was stored." in accepted.output


def test_an_answer_that_had_to_be_asked_for_again_is_an_error_row(fx: SimpleNamespace) -> None:
    _script(fx, titles=('{"titles": ["mlops engineer"]}', TITLES_OK))
    done = _run(fx, ROLE, "--yes", "--json")
    assert done.exit_code == 0 and _json(done)["calls"] == 4
    (row,) = _learning_rows(fx)
    assert (row["calls"], row["errors"]) == (4, 1)
    assert "A previous attempt at this same prompt was rejected by the validator" in fx.model.prompts[1]


def test_errors_exit_1_with_their_code(fx: SimpleNamespace) -> None:
    refused = _run(fx, "write to jane.roe@example.com", "--yes", "--json")
    assert refused.exit_code == 1 and _json(refused)["error"]["code"] == "personal_info_refused" and fx.model.prompts == []  # type: ignore[index]
    assert _run(fx, " ", "--json").exit_code == 1

    # The model answers twice with something unusable: two calls, both error rows, exit 1.
    fx.model.answers = ["no", "still no"]
    unusable = _run(fx, ROLE, "--yes", "--json")
    assert unusable.exit_code == 1 and _json(unusable)["error"]["code"] == "titles_invalid" and fx.model.closed == 1  # type: ignore[index]
    (row,) = _learning_rows(fx)
    assert (row["calls"], row["errors"]) == (2, 2)

    # The model target fails: exit 1 with its code, the failed call recorded, the adapter closed.
    fx.model.fail_next = ModelInvocationError("the fixture model is not reachable")
    failed = _run(fx, ROLE, "--yes")
    assert failed.exit_code == 1 and "the fixture model is not reachable" in failed.output and fx.model.closed == 2
    (row,) = _learning_rows(fx)
    assert (row["calls"], row["errors"]) == (3, 3)

    # No search index: the command says what builds one.
    search_index.close(fx.home)
    for path in (fx.home / "cache" / "scout").glob("search.sqlite*"):
        path.unlink()
    fx.model.answers = [TITLES_OK]
    missing = _run(fx, ROLE, "--yes", "--json")
    assert missing.exit_code == 1
    error = _json(missing)["error"]
    assert error["code"] == "search_index_unavailable" and "gigai scout sources update" in error["message"]  # type: ignore[index]


def test_the_estimate_helpers_say_what_the_history_can_say(fx: SimpleNamespace) -> None:
    assert learning_corpus.estimate_words({"calls": 3, "max_calls": 5, "tokens": None, "seconds": None, "basis_calls": 0}) == (
        "~3 model calls (at most 5) (no recorded learning calls yet to estimate tokens or time from)"
    )
    assert learning_corpus.estimate_words({"calls": 3, "max_calls": 5, "tokens": 42_400, "seconds": 95.0, "basis_calls": 6}) == (
        "~3 model calls (at most 5), ~42k tokens, ~2 min"
    )
    assert "learning" in call_metrics.KINDS and call_metrics.KIND_LEARNING == "learning"
    empty = call_metrics.estimate("learning", "ollama_local", 3, home_root=fx.home, target=fx.target)
    assert empty == {"kind": "learning", "model": "ollama_local", "n": 3, "calls": None, "tokens": None, "seconds": None, "cost": None, "basis_calls": 0}
