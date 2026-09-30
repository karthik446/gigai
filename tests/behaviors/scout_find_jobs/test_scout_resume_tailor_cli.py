"""Q3: ``gigai scout resume tailor`` behavior (CliRunner over the real installed CLI).

Fixture: the real ``gigai setup`` / ``gigai init`` / ``gigai scout install`` /
``gigai scout resume add`` surface (the ``test_scout_assess_cli.py`` pattern);
the model is a scripted binding installed at the C1 seam
(``proposal_execution.resolve_model_adapter``).
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

_POSTING = "Acme is hiring a Staff AI Engineer: 5+ years of Python in production; Terraform; GCP a plus. Remote within the US.\n"
_RESUME = "# Resume\n\nStaff AI Engineer. Built Python services for six years.\n"
_VALID = json.dumps(
    {
        "header": [{"copy": 1}],
        "sections": [
            {"heading": "summary", "lines": [{"text": "Staff AI Engineer with six years of Python services.", "refs": [{"kind": "resume", "line": 2}]}]},
            {"heading": "skills", "lines": [{"copy": 2}]},
        ],
    }
)
_FABRICATED = json.dumps(
    {"header": [{"copy": 1}], "sections": [{"heading": "summary", "lines": [{"text": "Eight years of Terraform.", "refs": [{"kind": "resume", "line": 2}]}]}]}
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
    init_result = runner.invoke(cli, ["init", "--home", str(home), "--target", str(target), "--username", "q3-cli-test", "--json"])
    assert init_result.exit_code == 0, init_result.output
    assert runner.invoke(cli, ["scout", "install", "--home", str(home), "--target", str(target), "--json"]).exit_code == 0
    _write_real_find_jobs_config(target)
    resume = tmp_path / "resume.md"
    resume.write_text(_RESUME, encoding="utf-8")
    added = runner.invoke(cli, ["scout", "resume", "add", str(resume), "--home", str(home), "--target", str(target), "--json"])
    assert added.exit_code == 0, added.output
    return home, target, runner


def _base(home: Path, target: Path) -> list[str]:
    return ["scout", "resume", "tailor", "--home", str(home), "--target", str(target)]


def test_tailor_pasted_job_prints_the_markdown_path_and_copies_to_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, runner = _setup(tmp_path)
    binding = _install_model(monkeypatch, [_VALID])
    posting = tmp_path / "posting.txt"
    posting.write_text(_POSTING, encoding="utf-8")
    out = tmp_path / "out" / "tailored.md"

    result = runner.invoke(cli, [*_base(home, target), "--job-text", str(posting), "--title", "Staff AI Engineer", "--company", "Acme", "--out", str(out)])

    assert result.exit_code == 0, result.output
    assert "Tailored resume for Staff AI Engineer at Acme:" in result.output
    assert "Sections: summary, skills" in result.output
    assert "Lines: 2 (1 rewritten, every one citing its resume lines / answers)" in result.output
    assert "Model: ollama_local" in result.output
    markdown_line = next(line for line in result.output.splitlines() if line.strip().startswith("Markdown: "))
    markdown_path = Path(markdown_line.split("Markdown: ", 1)[1].strip())
    assert markdown_path.is_file() and markdown_path.suffix == ".md" and markdown_path.parent.parent.name == "resumes"
    assert markdown_path.with_suffix(".json").is_file()
    assert f"Copied to {out}" in result.output
    assert out.read_text(encoding="utf-8") == markdown_path.read_text(encoding="utf-8")
    assert "# Resume <!-- R1 -->" in out.read_text(encoding="utf-8")
    assert "- Staff AI Engineer with six years of Python services. <!-- R2 -->" in out.read_text(encoding="utf-8")
    assert "R2: Staff AI Engineer. Built Python services for six years." in binding.port.prompts[0]
    # The resume's own lines never appear in the terminal output; only the paths do.
    assert "Built Python services" not in result.output


def test_tailor_json_output_with_an_ephemeral_resume_from_stdin_posting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, runner = _setup(tmp_path)
    _install_model(monkeypatch, [_VALID])

    result = runner.invoke(
        cli,
        [*_base(home, target), "--job-text", "-", "--resume-text", _RESUME, "--json"],
        input=_POSTING,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True and payload["schema_version"] == "scout-tailor-response:1"
    assert payload["resume"]["profile_id"] is None and Path(payload["stored_path"]).parent.name == "ephemeral"
    assert payload["markdown_path"] == str(Path(payload["stored_path"]).with_suffix(".md"))
    assert payload["result"]["header"][0] == {"kind": "copy", "text": "# Resume", "refs": [{"kind": "resume", "line": 1, "text": "# Resume"}]}
    assert payload["result"]["sections"][0]["lines"][0]["refs"][0]["line"] == 2
    assert "text" not in payload["job"] and payload["out_path"] is None
    assert _POSTING.strip() not in result.output


def test_tailor_rejects_conflicting_inputs_before_calling_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, runner = _setup(tmp_path)
    binding = _install_model(monkeypatch, [_VALID])
    posting = tmp_path / "posting.txt"
    posting.write_text(_POSTING, encoding="utf-8")

    both_jobs = runner.invoke(cli, [*_base(home, target), "--job-url", "https://x.test/1", "--job-text", str(posting), "--json"])
    assert both_jobs.exit_code == 1 and json.loads(both_jobs.output)["error"]["code"] == "job_input_invalid"
    both_resumes = runner.invoke(cli, [*_base(home, target), "--job-text", str(posting), "--profile", "p", "--resume-text", "r", "--json"])
    assert both_resumes.exit_code == 1 and json.loads(both_resumes.output)["error"]["code"] == "resume_input_invalid"
    assert binding.port.prompts == []


def test_tailor_fabricated_output_is_a_typed_error_after_one_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, runner = _setup(tmp_path)
    binding = _install_model(monkeypatch, [_FABRICATED, _FABRICATED])
    posting = tmp_path / "posting.txt"
    posting.write_text(_POSTING, encoding="utf-8")

    result = runner.invoke(cli, [*_base(home, target), "--job-text", str(posting), "--json"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "model_output_invalid"
    assert 'contains the number "Eight" that appears in none of its cited sources (R2)' in payload["error"]["message"]
    assert len(binding.port.prompts) == 2
    assert not list((home / "scout").rglob("resumes"))
