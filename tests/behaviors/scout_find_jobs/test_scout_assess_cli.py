"""P5: ``gigai scout assess`` behavior (CliRunner over the real installed CLI).

Fixture: the real ``gigai setup`` / ``gigai init`` / ``gigai scout install`` /
``gigai scout resume add`` surface (the ``test_scout_cli.py`` pattern) -- setup
configures an ``ollama_local``-adapter target named exactly ``ollama_local``
so the C11 resolution maps onto it; the model itself is a scripted binding
installed at the C1 seam (``proposal_execution.resolve_model_adapter``).
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
        "suggestions": ["Lead with the inference work."],
        "questions": [{"question_id": "cloud:gcp", "question": "Have you run workloads on GCP?", "requirement": "GCP"}],
        "not_a_match_reason": None,
    }
)
_NOT_A_MATCH = json.dumps(
    {
        "verdict": "not_a_match",
        "matrix": [{"requirement": "Staff level", "class": "hard", "status": "unmet", "resume_evidence": ["Intern"]}],
        "suggestions": [],
        "questions": [],
        "not_a_match_reason": "The resume states an intern level against a staff posting.",
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
        assert adapter_target == "ollama_local"  # the exact-name match rule
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
    init_result = runner.invoke(cli, ["init", "--home", str(home), "--target", str(target), "--username", "p5-cli-test", "--json"])
    assert init_result.exit_code == 0, init_result.output
    assert runner.invoke(cli, ["scout", "install", "--home", str(home), "--target", str(target), "--json"]).exit_code == 0
    _write_real_find_jobs_config(target)
    resume = tmp_path / "resume.md"
    resume.write_text("# Resume\n\nStaff AI Engineer. Built Python services for six years.\n", encoding="utf-8")
    added = runner.invoke(cli, ["scout", "resume", "add", str(resume), "--home", str(home), "--target", str(target), "--json"])
    assert added.exit_code == 0, added.output
    return home, target, runner


def _base(home: Path, target: Path) -> list[str]:
    return ["scout", "assess", "--home", str(home), "--target", str(target)]


def test_assess_pasted_job_prints_verdict_matrix_questions_and_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, runner = _setup(tmp_path)
    binding = _install_model(monkeypatch, [_PENDING])
    posting = tmp_path / "posting.txt"
    posting.write_text(_POSTING, encoding="utf-8")

    result = runner.invoke(cli, [*_base(home, target), "--job-text", str(posting), "--title", "Staff AI Engineer", "--company", "Acme"])

    assert result.exit_code == 0, result.output
    assert "Assessment for Staff AI Engineer at Acme:" in result.output
    assert "Verdict: pending_user_answers" in result.output
    assert "met      5+ years of Python [hard] -- six years" in result.output
    assert "unclear  GCP [askable]" in result.output
    assert "cloud:gcp: Have you run workloads on GCP?" in result.output
    assert "- Lead with the inference work." in result.output
    assert "Model: ollama_local" in result.output
    stored_line = next(line for line in result.output.splitlines() if line.strip().startswith("Stored at "))
    stored = Path(stored_line.split("Stored at ", 1)[1].strip())
    assert stored.is_file() and stored.parent.parent.name == "quick_assess"
    assert "staff ai engineer" in binding.port.prompts[0]  # the selected profile's titles reached the prompt
    # The resume itself never appears in the output.
    assert "six years." not in result.output.replace("-- six years", "")


def test_assess_json_output_and_stdin_posting_with_ephemeral_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, runner = _setup(tmp_path)
    _install_model(monkeypatch, [_NOT_A_MATCH])

    result = runner.invoke(
        cli,
        [*_base(home, target), "--job-text", "-", "--resume-text", "Intern, one summer of Python.", "--visa", "--json"],
        input=_POSTING,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["schema_version"] == "scout-assess-response:1"
    assert payload["result"]["verdict"] == "not_a_match"
    assert payload["result"]["not_a_match_reason"].startswith("The resume states")
    assert payload["resume"] == {"schema_version": "scout-resolved-resume:1", "profile_id": None, "pinned": None, "content_sha256": payload["resume"]["content_sha256"]}
    assert payload["preferences"]["visa_sponsorship_required"] is True
    assert payload["preferences"]["titles"] == [] and payload["preferences"]["countries"] == ["US"]
    assert "text" not in payload["job"]
    assert "Intern, one summer" not in result.output
    assert Path(payload["stored_path"]).parent.name == "ephemeral"


def test_assess_resume_file_flag_reads_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, runner = _setup(tmp_path)
    binding = _install_model(monkeypatch, [_PENDING])
    posting = tmp_path / "posting.txt"
    posting.write_text(_POSTING, encoding="utf-8")
    resume = tmp_path / "other-resume.md"
    resume.write_text("Pasted from a file: eleven years of Python.", encoding="utf-8")

    result = runner.invoke(cli, [*_base(home, target), "--job-text", str(posting), "--resume", str(resume)])

    assert result.exit_code == 0, result.output
    assert "Resume: pasted resume (not stored)" in result.output
    assert "eleven years of Python" in binding.port.prompts[0]
    assert "eleven years" not in result.output


def test_assess_rejects_conflicting_inputs_before_calling_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, runner = _setup(tmp_path)
    binding = _install_model(monkeypatch, [_PENDING])
    posting = tmp_path / "posting.txt"
    posting.write_text(_POSTING, encoding="utf-8")

    both_jobs = runner.invoke(cli, [*_base(home, target), "--job-url", "https://x.test/1", "--job-text", str(posting), "--json"])
    assert both_jobs.exit_code == 1, both_jobs.output
    assert json.loads(both_jobs.output)["error"]["code"] == "job_input_invalid"

    neither = runner.invoke(cli, [*_base(home, target), "--json"])
    assert neither.exit_code == 1, neither.output
    assert json.loads(neither.output)["error"]["code"] == "job_input_invalid"

    both_resumes = runner.invoke(cli, [*_base(home, target), "--job-text", str(posting), "--profile", "p", "--resume-text", "r", "--json"])
    assert both_resumes.exit_code == 1, both_resumes.output
    assert json.loads(both_resumes.output)["error"]["code"] == "resume_input_invalid"

    plain = runner.invoke(cli, [*_base(home, target), "--job-url", "https://x.test/1", "--job-text", str(posting)])
    assert plain.exit_code == 1
    assert "Error: pass exactly one of --job-url or --job-text" in plain.output
    assert binding.port.prompts == []


def test_assess_unknown_profile_is_a_typed_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, runner = _setup(tmp_path)
    _install_model(monkeypatch, [_PENDING])
    posting = tmp_path / "posting.txt"
    posting.write_text(_POSTING, encoding="utf-8")

    result = runner.invoke(cli, [*_base(home, target), "--job-text", str(posting), "--profile", "profile_00000000-0000-4000-8000-00000000dead", "--json"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error" and payload["error"]["code"] == "profile_not_found"


def test_assess_invalid_model_output_is_a_typed_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, runner = _setup(tmp_path)
    _install_model(monkeypatch, ["garbage", "garbage again"])
    posting = tmp_path / "posting.txt"
    posting.write_text(_POSTING, encoding="utf-8")

    result = runner.invoke(cli, [*_base(home, target), "--job-text", str(posting)])
    assert result.exit_code == 1, result.output
    assert "Error: the model's answer was invalid after one retry" in result.output
