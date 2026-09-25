"""P3 (v0.1.9): ``gigai scout answer`` (CliRunner over the real installed CLI).

Fixture: the same ``gigai setup``/``init``/``scout install``/``resume add``
surface ``test_scout_assess_cli.py`` uses, so the C11 resolution maps onto a
real ``ollama_local`` target; the model itself is a scripted binding
installed at the C1 seam.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.canonical import canonical_json_bytes
from gigai.cli import cli
from gigai.scout.find_jobs.contracts import FindJobsConfig, SourceToggles

_POSTING = "Acme is hiring a Staff AI Engineer: 5+ years of Python in production; GCP a plus. Remote within the US.\n"
_PENDING = json.dumps(
    {
        "verdict": "pending_user_answers",
        "matrix": [
            {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]},
            {"requirement": "GCP", "class": "askable", "status": "unclear", "resume_evidence": []},
        ],
        "suggestions": [],
        "questions": [{"question_id": "cloud:gcp", "question": "Have you run workloads on GCP?", "requirement": "GCP"}],
        "not_a_match_reason": None,
    }
)
_MATCH = json.dumps(
    {
        "verdict": "matched_above_threshold",
        "matrix": [
            {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]},
            {"requirement": "GCP", "class": "askable", "status": "met", "resume_evidence": ["Yes, two years on GCP."]},
        ],
        "suggestions": [],
        "questions": [],
        "not_a_match_reason": None,
    }
)


class _ScriptedPort:
    def __init__(self, outputs: list[object]) -> None:
        self._outputs = list(outputs)
        self.prompts: list[str] = []

    def invoke(self, request):
        self.prompts.append(request.prompt)
        item = self._outputs.pop(0)
        if isinstance(item, BaseException):
            raise item
        return InvocationResult(
            status="success", output_text=item, resolved_model="fixture", raw_usage={},
            normalized_usage=NormalizedUsage(1, 1, 2), cost_status="unavailable",
        )


class _ScriptedBinding:
    def __init__(self, outputs: list[object]) -> None:
        self.port = _ScriptedPort(outputs)

    def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
        return SimpleNamespace(prompt=prompt, role=role)

    def close(self) -> None:
        pass


def _install_model(monkeypatch: pytest.MonkeyPatch, outputs: list[object]) -> _ScriptedBinding:
    binding = _ScriptedBinding(outputs)

    def resolve(config, adapter_target, **_kwargs):
        assert adapter_target == "ollama_local"
        return binding

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    return binding


def _write_real_find_jobs_config(target: Path) -> None:
    config = FindJobsConfig(
        roles=("staff ai engineer",),
        merged_queries=("staff ai engineer",),
        location="Remote",
        remote=True,
        published_after=None,
        sources=SourceToggles(exa=True, ats=True, hiringcafe=False),
        countries=("US",),
        visa_sponsorship_required=False,
    )
    (target / "find-jobs.json").write_bytes(canonical_json_bytes(config.to_json()))


def _setup(tmp_path: Path) -> tuple[Path, Path, CliRunner]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir(parents=True)
    runner = CliRunner()
    setup_result = runner.invoke(
        cli,
        [
            "setup", "--non-interactive", "--home", str(home), "--workpad-root", str(tmp_path / "workpads"),
            "--editor", "/usr/bin/true",
            "--endpoint", "ollama_local=ollama_local:http://127.0.0.1:11434",
            "--model-target", "ollama_local=ollama_local:scout-test:latest@sha256:" + "0" * 64,
            "--create-model-target", "ollama_local",
            "--json",
        ],
    )
    assert setup_result.exit_code == 0, setup_result.output
    init_result = runner.invoke(cli, ["init", "--home", str(home), "--target", str(target), "--username", "p3-cli-test", "--json"])
    assert init_result.exit_code == 0, init_result.output
    assert runner.invoke(cli, ["scout", "install", "--home", str(home), "--target", str(target), "--json"]).exit_code == 0
    _write_real_find_jobs_config(target)
    resume = tmp_path / "resume.md"
    resume.write_text("# Resume\n\nStaff AI Engineer. Built Python services for six years.\n", encoding="utf-8")
    added = runner.invoke(cli, ["scout", "resume", "add", str(resume), "--home", str(home), "--target", str(target), "--json"])
    assert added.exit_code == 0, added.output
    return home, target, runner


def _answer_base(home: Path, target: Path) -> list[str]:
    return ["scout", "answer", "--home", str(home), "--target", str(target)]


def _assess_base(home: Path, target: Path) -> list[str]:
    return ["scout", "assess", "--home", str(home), "--target", str(target)]


def test_answer_creates_an_experience_record_and_json_output(tmp_path: Path) -> None:
    home, target, runner = _setup(tmp_path)

    result = runner.invoke(cli, [*_answer_base(home, target), "cloud:gcp", "--answer-text", "Yes, two years on GCP.", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["question_id"] == "cloud:gcp"
    assert payload["record_id"].startswith("record_")
    assert payload["reassessed"] is None


def test_answer_plain_output(tmp_path: Path) -> None:
    home, target, runner = _setup(tmp_path)

    result = runner.invoke(cli, [*_answer_base(home, target), "cloud:gcp", "--answer-text", "Yes."])

    assert result.exit_code == 0, result.output
    assert "Recorded answer for cloud:gcp at revision_" in result.output


def test_answer_from_file(tmp_path: Path) -> None:
    home, target, runner = _setup(tmp_path)
    answer_file = tmp_path / "answer.txt"
    answer_file.write_text("Yes, two years on GCP.", encoding="utf-8")

    result = runner.invoke(cli, [*_answer_base(home, target), "cloud:gcp", "--answer-file", str(answer_file), "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True


def test_answer_requires_exactly_one_of_answer_text_or_answer_file(tmp_path: Path) -> None:
    home, target, runner = _setup(tmp_path)

    neither = runner.invoke(cli, [*_answer_base(home, target), "cloud:gcp", "--json"])
    assert neither.exit_code == 1
    assert json.loads(neither.output)["error"]["code"] == "answer_invalid"

    both = runner.invoke(cli, [*_answer_base(home, target), "cloud:gcp", "--answer-text", "a", "--answer-file", str(tmp_path / "x.txt"), "--json"])
    assert both.exit_code == 1
    assert json.loads(both.output)["error"]["code"] == "answer_invalid"


# --- question_id contract validation, BEFORE any write (Terra review P2) --------------


def test_answer_rejects_question_id_with_invalid_characters(tmp_path: Path) -> None:
    home, target, runner = _setup(tmp_path)

    result = runner.invoke(cli, [*_answer_base(home, target), "cloud:gcp/invalid", "--answer-text", "Yes.", "--json"])

    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["error"]["code"] == "answer_invalid"


def test_answer_rejects_question_id_over_128_chars_after_normalization(tmp_path: Path) -> None:
    home, target, runner = _setup(tmp_path)

    result = runner.invoke(cli, [*_answer_base(home, target), f"cloud:{'a' * 200}", "--answer-text", "Yes.", "--json"])

    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["error"]["code"] == "answer_invalid"


def test_answer_reused_across_a_later_assess_call_never_re_asks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, runner = _setup(tmp_path)
    answered = runner.invoke(cli, [*_answer_base(home, target), "cloud:gcp", "--answer-text", "Yes, two years on GCP.", "--json"])
    assert answered.exit_code == 0, answered.output

    binding = _install_model(monkeypatch, [_MATCH])
    posting = tmp_path / "posting.txt"
    posting.write_text(_POSTING, encoding="utf-8")

    result = runner.invoke(cli, [*_assess_base(home, target), "--job-text", str(posting), "--json"])

    assert result.exit_code == 0, result.output
    assert "cloud:gcp: Yes, two years on GCP." in binding.port.prompts[0]
    assert "is resolved by that answer, never re-asked" in binding.port.prompts[0]
    payload = json.loads(result.output)
    assert payload["result"]["verdict"] == "matched_above_threshold"


def test_reassess_flips_the_verdict_after_answering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, runner = _setup(tmp_path)
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    _install_model(monkeypatch, [_PENDING])

    first = runner.invoke(cli, [*_assess_base(home, target), "--job-url", "https://careers.example.test/jobs/9", "--json"])
    assert first.exit_code == 0, first.output
    assert json.loads(first.output)["result"]["verdict"] == "pending_user_answers"

    _install_model(monkeypatch, [_MATCH])
    answered = runner.invoke(
        cli,
        [
            *_answer_base(home, target), "cloud:gcp", "--answer-text", "Yes, two years on GCP.",
            "--reassess", "https://careers.example.test/jobs/9", "--json",
        ],
    )

    assert answered.exit_code == 0, answered.output
    payload = json.loads(answered.output)
    assert payload["reassessed"]["result"]["verdict"] == "matched_above_threshold"


def test_reassess_unknown_job_is_a_typed_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, runner = _setup(tmp_path)

    result = runner.invoke(
        cli,
        [*_answer_base(home, target), "cloud:gcp", "--answer-text", "Yes.", "--reassess", "https://nowhere.example.test/jobs/1", "--json"],
    )

    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["error"]["code"] == "reassess_not_found"
