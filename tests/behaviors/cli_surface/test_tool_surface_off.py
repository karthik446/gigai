"""0110-8-07 (security): every GigAI model call runs with ALL tools off.

The prompts carry untrusted posting text next to the user's resume. A model
call that can search the web, call an MCP server or run a sub-agent could be
steered by a posting into fetching a URL or sending data out. So:

* the Codex CLI call disables every tool feature, web search and every
  configured MCP server ON THE COMMAND LINE, which outranks the user's
  ``config.toml`` (still loaded: a custom model provider lives there), and
  runs on a copy of codex's model catalog without the fields that force the
  ``exec`` and sub-agent tools on for a model;
* the Claude Code call keeps ``--tools ""``, ``--strict-mcp-config`` and
  ``--setting-sources ""``;
* the hosted and local HTTP adapters send no tool definitions at all.

The fakes (``tests/support/fake_tool_surface_cli.py``) load the config of the
``HOME`` they are given, apply the command line on top the way the real CLIs
document it, and report the resulting tool surface. The user config here turns
web search to "live" and configures MCP servers, a plugin, hooks and extra tool
features. Every call path is driven through its own production entry point.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest

from gigai.adapters import cli_probe
from gigai.adapters.cli_probe import (
    CODEX_TOOL_FEATURES,
    CodexLockdown,
    codex_lockdown,
    codex_plain_model_catalog,
    reset_probe_cache,
)
from gigai.adapters.codex_cli import MODEL_CATALOG_FILE, CodexCLIAdapter, _parse_codex_jsonl
from gigai.adapters.ollama_local import OllamaLocalAdapter
from gigai.adapters.openai_api import OpenAIAPIAdapter
from gigai.adapters.openrouter_api import OpenRouterAPIAdapter
from gigai.adapters.port import InvocationRequest, ModelInvocationError
from gigai.config import CredentialReference, Endpoint
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.scout.find_jobs.contracts import (
    AssessInput,
    ModelTarget,
    NodeContext,
    SelectedPosting,
    SelectionReason,
    SelectionReasonCode,
)
from tests.behaviors.scout_find_jobs.test_assess_model_policy import _assess_fixture, _posting as _assess_posting
from tests.behaviors.scout_find_jobs.test_run_rank_step import _posting
from tests.support.fake_tool_surface_cli import CODEX_CATALOG, calls, write_fakes

# A user config that turns ON everything the ticket is about. Synthetic.
CODEX_USER_CONFIG = """\
web_search = "live"
model_provider = "acme"
sandbox_mode = "danger-full-access"

[tools]
web_search = true

[model_providers.acme]
name = "Acme gateway"
base_url = "https://llm.acme.invalid/v1"
env_key = "ACME_KEY"

[features]
shell_tool = true
memories = true
code_mode = true
multi_agent_v2 = true
request_permissions_tool = true
standalone_web_search = true

[mcp_servers.fetcher]
command = "npx"
args = ["fetch-mcp"]

[mcp_servers.remote-docs]
url = "https://mcp.acme.invalid/mcp"

[plugins."browser@local"]
enabled = true
"""
CLAUDE_USER_SETTINGS = {
    "permissions": {"allow": ["WebFetch", "Bash(curl:*)"], "defaultMode": "bypassPermissions"},
    "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "curl https://hooks.acme.invalid"}]}]},
    "enabledPlugins": {"browser@local": True},
    "mcpServers": {"settings-fetcher": {"command": "npx", "args": ["fetch-mcp"]}},
}
CLAUDE_USER_STATE = {"mcpServers": {"fetcher": {"command": "npx", "args": ["fetch-mcp"]}}}

PATHS = ("assess", "rank", "tag", "tailor", "extract", "interview_prep")
KINDS = {"codex_cli": ModelTarget.CODEX_CLI, "claude_cli": ModelTarget.CLAUDE_CLI}
_DIR = "/tmp/gigai-codex-x"


@pytest.fixture(autouse=True)
def _canned_cli_probe() -> None:
    """The REAL probes run here (the fakes answer them); only the per-process cache is cleared."""

    reset_probe_cache()


@pytest.fixture
def user_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A HOME whose codex and claude configs enable web search, MCP servers, a plugin and hooks."""

    home = tmp_path / "user-home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text(CODEX_USER_CONFIG, encoding="utf-8")
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps(CLAUDE_USER_SETTINGS), encoding="utf-8")
    (home / ".claude.json").write_text(json.dumps(CLAUDE_USER_STATE), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    return home


@pytest.fixture
def record(tmp_path: Path, user_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The fake ``codex`` and ``claude`` first on PATH; returns their call record."""

    bin_dir = tmp_path / "fake-bin"
    path = write_fakes(bin_dir)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return path


@pytest.fixture
def substrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    fixture, target = _assess_fixture(
        tmp_path,
        extra_endpoints=(Endpoint(name="codex", adapter="codex_cli"), Endpoint(name="claude", adapter="claude_cli")),
        extra_model_targets=(
            ConfigModelTarget("codex-default", "codex", "default", ("text",), 4096),
            ConfigModelTarget("claude-default", "claude", "default", ("text",), 4096),
        ),
    )
    monkeypatch.setenv("JEV_API_KEY", "jv_test_key_never_used")
    return {**fixture, "target": target}


def _request(model: str = "default", effort: str | None = None, prompt: str = "Say hi as JSON.") -> InvocationRequest:
    return InvocationRequest(
        target_name="t", endpoint_name="e", model=model, role="reviewer", prompt=prompt,
        target_capabilities=frozenset({"text"}), reasoning_effort=effort,
    )


# --- the six call paths, each through its own production entry point ---------------------------------


def _assess(substrate: dict, model_target: ModelTarget) -> None:
    from gigai.scout.proposal_execution import assess_node

    target: Path = substrate["target"]
    run_id = "run_00000000-0000-4000-8000-000000000807"
    posting = _assess_posting(normalized_url="https://boards.greenhouse.io/acme/jobs/807", text="We need Python and GCP.")
    run_dir = target / "runs" / run_id
    (run_dir / "outputs").mkdir(parents=True)
    (run_dir / "outputs" / "acquire.json").write_text(json.dumps({"rows": [{"posting": posting.to_json(), "outcome": "new"}]}))
    context = NodeContext(
        run_id=run_id, project_id=substrate["resolved"].project_id, gig_id=substrate["gig_id"],
        graph_id="graph_find_jobs_test", graph_version=1, goal_slug="assess",
        manifest_digest="sha256:" + "0" * 64, operation_key="assess-u1",
        target_observation_digest="sha256:" + "0" * 64, workpad_path=str(substrate["resolved"].path),
        redeemed_consent_ref="none", model_target=model_target,
    )
    assess_input = AssessInput(
        acquire_batch_ref=str(run_dir / "outputs" / "acquire.json"),
        acquire_output_digest="sha256:" + "0" * 64,
        selected_postings=(SelectedPosting(posting.normalized_url, posting.url, posting.content_sha256, True),),
        selection_cap=10,
        selection_reasons=(SelectionReason(posting.normalized_url, SelectionReasonCode.NEW),),
        pinned_resume=substrate["pinned"],
        target=str(target),
        model_target=model_target,
        answer_association_version="scout-answer-association:1",
    )
    output = assess_node(context, assess_input, home_root=substrate["home"], target=target, config=substrate["config"])
    assert [item.proposal_revision_ref for item in output.assessments], "the assessment was produced"


def _rank(substrate: dict, model_target: ModelTarget) -> None:
    from gigai.scout.find_jobs import model_rank

    result = model_rank.rank_postings(
        [_posting(0)],
        resume_text="Python engineer.",
        prefs=model_rank.CandidatePrefs(("Software Engineer",), ("US",), False, "", True),
        model_target=model_target,
        home_root=substrate["home"],
        config=substrate["config"],
    )
    assert result.status == "complete", result.fail_open_reason


def _tag(substrate: dict, model_target: ModelTarget) -> None:
    """The tag lane's own caller (``TagQueue._resolve``): the ranker's resolver plus the lane's meter."""

    from gigai.scout.call_metrics import KIND_TAG, CallMeter
    from gigai.scout.find_jobs import model_rank, model_tag

    resolved = model_rank._resolve(substrate["config"], model_target.value, home_root=substrate["home"])
    caller = model_tag._Caller(resolved, model_target.value, None, CallMeter(KIND_TAG, model_target.value, substrate["home"], None))
    try:
        caller.invoke(model_tag.render_tag_prompt([model_tag.tag_line("p1", "Staff Software Engineer", "Remote")]))
    finally:
        caller.close()


def _tailor(substrate: dict, model_target: ModelTarget) -> None:
    """Tailoring and quick assess share ``quick_assess._resolve_binding`` (``tailored_resume`` imports it)."""

    from gigai.scout.find_jobs import bindings
    from gigai.scout.quick_assess import _resolve_binding

    binding = _resolve_binding(substrate["config"], model_target, home_root=substrate["home"])
    try:
        binding.port.invoke(binding.request(role="reviewer", prompt=f"{bindings.TEST_MODEL_TAILOR_MARKER}\nSynthetic resume."))
    finally:
        binding.close()


def _extract(substrate: dict, model_target: ModelTarget) -> None:
    from gigai.scout.find_jobs.api.extract import extract_with_model

    extract_with_model("Python engineer, 8 years.", config=substrate["config"], model_target=model_target, home_root=substrate["home"])


def _interview_prep(substrate: dict, model_target: ModelTarget) -> None:
    from gigai.scout.interview_prep.categories import CategoryPredictionError, predict_categories

    try:
        predict_categories(
            config=substrate["config"], model_target=model_target.value, title="Staff Software Engineer",
            company="Acme", posting_text="We need Python and GCP.", resume_text="Python engineer.",
            company_claims=(), home_root=substrate["home"],
        )
    except CategoryPredictionError as exc:
        # The fixture model has no interview answer: the call itself must still have been made.
        assert exc.code == "category_prediction_invalid", exc.code


_RUN = {"assess": _assess, "rank": _rank, "tag": _tag, "tailor": _tailor, "extract": _extract, "interview_prep": _interview_prep}


# --- (a) the acceptance test: every call path, both CLIs, a user config that enables tools --------------


@pytest.mark.parametrize("path", PATHS)
def test_every_codex_call_path_offers_the_model_no_tool_whatever_the_user_config_enables(
    path: str, substrate: dict, record: Path, user_home: Path
) -> None:
    _RUN[path](substrate, ModelTarget.CODEX_CLI)

    made = calls(record, cli="codex")
    assert made, f"the {path} path made no codex call"
    for call in made:
        argv, surface = call["argv"], call["surface"]
        # the fake loaded the user's config (the one that enables everything), then the command line
        assert call["config_path"] == str(user_home / ".codex" / "config.toml")
        assert call["config_text"] == CODEX_USER_CONFIG and surface["config_loaded"] is True
        # END OUTCOME: the model is offered nothing it can act with
        assert surface["tools"] == ["request_user_input"], surface["tools"]
        assert surface["web_search"] == "disabled"
        assert surface["mcp_enabled"] == [] and surface["features_on"] == []
        assert surface["mcp_servers"] == {"fetcher": False, "remote-docs": False}  # the plugin's server is gone
        assert surface["sandbox"] == "read-only"
        # the user's own model provider and login are untouched
        assert surface["model_provider"] == "acme" and "--ignore-user-config" not in argv
        assert {"HOME", "PATH"} <= set(call["env"]) and "ACME_KEY" not in call["env"]
        # the form of the flags
        assert argv[:5] == ["exec", "--json", "--ephemeral", "--sandbox", "read-only"] and argv[-1] == "-"
        configs = [argv[i + 1] for i, value in enumerate(argv) if value == "-c"]
        assert 'web_search="disabled"' in configs
        assert {"mcp_servers.fetcher.enabled=false", "mcp_servers.remote-docs.enabled=false"} <= set(configs)
        # the default model is a "code mode only" model with sub-agents: its catalog entry is replaced for this call
        assert surface["model"] == "fake-code-model"
        assert not Path(surface["model_catalog_json"]).exists()  # removed with the call's directory
        assert Path(surface["model_catalog_json"]).name == MODEL_CATALOG_FILE
        assert Path(surface["model_catalog_json"]).parent.name == Path(call["cwd"]).name
        assert f"model_catalog_json={json.dumps(surface['model_catalog_json'])}" in configs
        disabled = [argv[i + 1] for i, value in enumerate(argv) if value == "--disable"]
        assert disabled == [name for name in CODEX_TOOL_FEATURES if name in disabled] and len(disabled) > 30
        assert not {"--enable", "--search", "--dangerously-bypass-approvals-and-sandbox", "--add-dir", "--profile", "-p"} & set(argv)
        assert Path(call["cwd"]).name.startswith("gigai-codex-")


@pytest.mark.parametrize("path", PATHS)
def test_every_claude_call_path_offers_the_model_no_tool_whatever_the_user_config_enables(
    path: str, substrate: dict, record: Path, user_home: Path
) -> None:
    _RUN[path](substrate, ModelTarget.CLAUDE_CLI)

    made = calls(record, cli="claude")
    assert made, f"the {path} path made no claude call"
    for call in made:
        argv, surface = call["argv"], call["surface"]
        assert "settings-fetcher" in call["config_text"] and "hooks.acme.invalid" in call["config_text"]
        # END OUTCOME: no built-in tool, no MCP server, no user settings (so no hooks, no plugins)
        assert surface == {"setting_sources": [], "hooks": [], "plugins": [], "mcp_enabled": [], "tools": []}
        assert argv[argv.index("--tools") + 1] == "" and argv[argv.index("--setting-sources") + 1] == ""
        assert "--strict-mcp-config" in argv
        assert not {
            "--mcp-config", "--settings", "--plugin-dir", "--plugin-url", "--allowedTools", "--allowed-tools",
            "--add-dir", "--chrome", "--agents", "--dangerously-skip-permissions",
        } & set(argv)
        assert "bypassPermissions" not in argv
        assert Path(call["cwd"]).name.startswith("gigai-claude-")


def test_the_old_codex_argv_left_web_search_and_the_mcp_servers_on(record: Path, user_home: Path) -> None:
    """What 0.1.10.7 ran (shell tool and memories off, nothing else): the baseline this ticket closes."""

    from tests.support.fake_tool_surface_cli import codex_state

    old = ["exec", "--json", "--ephemeral", "--sandbox", "read-only", "--disable", "shell_tool", "--disable", "memories",
           "--skip-git-repo-check", "--cd", _DIR, "-"]
    surface = codex_state(old, {"HOME": str(user_home)})["surface"]

    assert {"web_search", "mcp__fetcher", "mcp__remote-docs", "mcp__browser_plugin", "collaboration", "exec"} <= set(surface["tools"])
    # with NO user config at all the old argv still offered web search, sub-agents, code mode, goals, images
    bare = codex_state(old, {"HOME": str(user_home / "nobody")})["surface"]
    assert {"web_search", "collaboration", "exec", "goals", "view_image"} <= set(bare["tools"]) and bare["web_search"] == "cached"
    assert surface["web_search"] == "live"


# --- the argv is built in ONE function ---------------------------------------------------------------


def test_the_codex_argv_is_exactly_this() -> None:
    lockdown = CodexLockdown(
        features=("shell_tool", "memories", "apps", "plugins"), mcp_servers=("fetcher", "remote-docs"), model_catalog='{"models": []}'
    )
    expected = (
        "/opt/fake/codex", "exec", "--json", "--ephemeral", "--sandbox", "read-only",
        "--disable", "shell_tool", "--disable", "memories", "--disable", "apps", "--disable", "plugins",
        "-c", 'web_search="disabled"',
        "-c", "mcp_servers.fetcher.enabled=false", "-c", "mcp_servers.remote-docs.enabled=false",
        "-c", f'model_catalog_json="{_DIR}/{MODEL_CATALOG_FILE}"',
        "--skip-git-repo-check", "--cd", _DIR,
    )

    assess = CodexCLIAdapter(executable="/opt/fake/codex")
    assert assess.argv(_request(effort="low"), _DIR, lockdown) == (*expected, "-")
    assert assess.argv(_request(model="gpt-x"), _DIR, lockdown) == (*expected, "--model", "gpt-x", "-")
    # the ranker's copy adds its effort and nothing else
    assert assess.effort_copy().argv(_request(effort="low"), _DIR, lockdown) == (*expected, "-c", "model_reasoning_effort=low", "-")


def test_invoke_runs_exactly_the_argv_function(record: Path, user_home: Path) -> None:
    adapter = CodexCLIAdapter()
    adapter.invoke(_request())

    [call] = calls(record, cli="codex")
    lockdown = codex_lockdown(shutil.which("codex") or "")
    assert lockdown.mcp_servers == ("fetcher", "remote-docs")
    assert tuple(call["argv"]) == adapter.argv(_request(), call["argv"][call["argv"].index("--cd") + 1], lockdown)[1:]


# --- the lockdown probe: read on every call, fails closed --------------------------------------------


def test_an_mcp_server_added_between_two_calls_is_turned_off_on_the_next_call(record: Path, user_home: Path) -> None:
    adapter = CodexCLIAdapter()
    adapter.invoke(_request())
    with (user_home / ".codex" / "config.toml").open("a", encoding="utf-8") as handle:
        handle.write('\n[mcp_servers.added_later]\ncommand = "npx"\n')
    adapter.invoke(_request())

    first, second = calls(record, cli="codex")
    assert "mcp_servers.added_later.enabled=false" not in first["argv"]
    assert "mcp_servers.added_later.enabled=false" in second["argv"]
    assert second["surface"]["mcp_enabled"] == [] and second["surface"]["tools"] == ["request_user_input"]


def test_a_plugin_server_is_dropped_by_the_plugins_feature_not_overridden_by_name(record: Path, user_home: Path) -> None:
    """Overriding a server that is gone once plugins are off is a codex config error: it must not be named."""

    CodexCLIAdapter().invoke(_request())

    [call] = calls(record, cli="codex")
    assert "plugins" in [call["argv"][i + 1] for i, value in enumerate(call["argv"]) if value == "--disable"]
    assert not [value for value in call["argv"] if "browser_plugin" in value]
    assert [entry["kind"] for entry in calls(record, cli="codex", model_only=False)] == ["mcp_list", "mcp_list", "assess"]


def test_an_mcp_server_that_stays_enabled_stops_the_call(record: Path, user_home: Path) -> None:
    config = user_home / ".codex" / "config.toml"
    config.write_text('[fake]\nstubborn_mcp_servers = ["fetcher"]\n' + CODEX_USER_CONFIG, encoding="utf-8")

    with pytest.raises(ModelInvocationError, match="could not turn off the codex MCP server\\(s\\) fetcher"):
        CodexCLIAdapter().invoke(_request())
    assert calls(record, cli="codex") == [], "no model call was made"


def test_an_mcp_server_name_that_cannot_be_overridden_stops_the_call(record: Path, user_home: Path) -> None:
    with (user_home / ".codex" / "config.toml").open("a", encoding="utf-8") as handle:
        handle.write('\n[mcp_servers."odd.name"]\ncommand = "npx"\n')

    with pytest.raises(ModelInvocationError, match="odd.name"):
        CodexCLIAdapter().invoke(_request())
    assert calls(record, cli="codex") == []


@pytest.mark.parametrize("answer", ["", "not json", '{"servers": []}', '[{"enabled": true}]', "[3]"])
def test_an_unreadable_mcp_list_stops_the_call(answer: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_probe, "_run_probe",
        lambda argv: "shell_tool stable true\nmemories stable true\n" if argv[1:] == ("features", "list") else answer,
    )
    with pytest.raises(ModelInvocationError, match="codex mcp list --json"):
        codex_lockdown("/opt/fake/codex")


def test_only_the_features_the_installed_codex_lists_are_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--disable <unknown>`` is an error in codex, so an older codex gets only the names it has."""

    monkeypatch.setattr(
        cli_probe, "_run_probe",
        lambda argv: "shell_tool stable true\nmemories stable true\nmulti_agent stable true\nfast_mode stable true\n"
        if argv[1:] == ("features", "list") else '{"models": []}' if argv[1:] == ("debug", "models") else "[]",
    )
    assert codex_lockdown("/opt/fake/codex") == CodexLockdown(
        features=("shell_tool", "memories", "multi_agent"), mcp_servers=(), model_catalog='{"models": []}'
    )


def test_the_model_catalog_copy_drops_only_the_fields_that_force_tools_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_probe, "_run_probe", lambda argv: json.dumps(CODEX_CATALOG))

    plain = json.loads(codex_plain_model_catalog("/opt/fake/codex"))

    assert [model["slug"] for model in plain["models"]] == ["fake-code-model", "fake-plain-model"]
    for before, after in zip(CODEX_CATALOG["models"], plain["models"]):
        assert "tool_mode" not in after and "multi_agent_version" not in after
        assert after["experimental_supported_tools"] == [] and after["apply_patch_tool_type"] is None
        assert after["supports_search_tool"] is False
        kept = {"slug", "display_name", "shell_type", "base_instructions"}
        assert {key: after[key] for key in kept} == {key: before[key] for key in kept}


@pytest.mark.parametrize("answer", ["", "not json", "[]", '{"models": {}}', '{"models": ["gpt"]}', '{"data": []}'])
def test_an_unreadable_model_catalog_stops_the_call(answer: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_probe, "_run_probe",
        lambda argv: "shell_tool stable true\nmemories stable true\n" if argv[1:] == ("features", "list")
        else answer if argv[1:] == ("debug", "models") else "[]",
    )
    with pytest.raises(ModelInvocationError, match="codex debug models"):
        codex_lockdown("/opt/fake/codex")


def test_a_model_named_on_the_command_line_runs_on_the_plain_catalog_too(record: Path, user_home: Path) -> None:
    CodexCLIAdapter().invoke(_request(model="fake-plain-model"))

    [call] = calls(record, cli="codex")
    assert call["surface"]["model"] == "fake-plain-model" and call["surface"]["tools"] == ["request_user_input"]


# --- a tool event in the answer discards it ----------------------------------------------------------


@pytest.mark.parametrize("kind", ["web_search", "mcp_tool_call", "command_execution", "file_change", "collab_tool_call", "future_tool_call"])
def test_a_tool_event_in_the_codex_stream_discards_the_answer(kind: str) -> None:
    stream = "\n".join([
        json.dumps({"type": "item.completed", "item": {"type": kind, "id": "item_0"}}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "the title is Example Domain"}}),
    ])
    with pytest.raises(ModelInvocationError, match=f"Codex used a tool \\({kind}\\)"):
        _parse_codex_jsonl(stream, "default")


def test_reasoning_and_error_items_are_not_tool_events() -> None:
    stream = "\n".join([
        json.dumps({"type": "item.completed", "item": {"type": "error", "message": "Model metadata not found"}}),
        json.dumps({"type": "item.completed", "item": {"type": "reasoning", "text": "thinking"}}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}),
    ])
    assert _parse_codex_jsonl(stream, "default")[0] == "ok"


# --- the HTTP adapters send no tool definitions ------------------------------------------------------

_TOOL_KEYS = {"tools", "tool_choice", "functions", "function_call", "mcp_servers", "plugins", "web_search_options", "parallel_tool_calls"}


def test_the_hosted_api_adapters_send_no_tool_definitions(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, dict] = {}
    credential = CredentialReference(name="k", kind="env", reference="NEVER_READ")

    def post(self: object, path: str, payload: dict, **_kwargs: object) -> dict:
        sent[path] = dict(payload)
        if path == "/responses":
            return {"output_text": "ok", "model": "m"}
        return {"choices": [{"message": {"content": "ok"}}], "model": "m"}

    monkeypatch.setattr("gigai.adapters.http.HttpModelAdapter._post_json", post)
    OpenAIAPIAdapter(credential=credential).invoke(_request(model="gpt-x", effort="low"))
    OpenRouterAPIAdapter(credential=credential).invoke(_request(model="vendor/model", effort="low"))

    assert set(sent["/responses"]) == {"model", "input", "max_output_tokens", "reasoning"}
    assert set(sent["/chat/completions"]) == {"model", "messages", "max_tokens", "reasoning"}
    for payload in sent.values():
        assert not _TOOL_KEYS & set(payload)


def test_the_ollama_adapter_sends_no_tool_definitions(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict] = []
    digest = "sha256:" + "c" * 64
    adapter = OllamaLocalAdapter(endpoint="http://127.0.0.1:11434", model="fixture-model", model_digest=digest)
    monkeypatch.setattr(adapter, "_verify_identity", lambda: ("0.0.0", digest))

    def request_json(method: str, path: str, *, payload: dict | None = None) -> dict:
        sent.append(dict(payload or {}))
        raise ModelInvocationError("stop after the request was built")

    monkeypatch.setattr(adapter, "_request_json", request_json)
    with pytest.raises(ModelInvocationError):
        adapter.invoke(_request(model="fixture-model", effort="none"))
    adapter.close()

    [payload] = sent
    assert set(payload) == {"model", "messages", "stream", "think", "options"} and not _TOOL_KEYS & set(payload)
    assert set(payload["messages"][0]) == {"role", "content"}


def test_no_other_module_starts_a_model_cli() -> None:
    """Every codex/claude model call goes through the two adapters, so the lockdown cannot be bypassed."""

    source = Path(__file__).resolve().parents[3] / "src" / "gigai"
    offenders = []
    for path in sorted(source.rglob("*.py")):
        relative = path.relative_to(source).as_posix()
        text = path.read_text(encoding="utf-8")
        if relative not in ("adapters/codex_cli.py", "adapters/factory.py") and "CodexCLIAdapter(" in text:
            offenders.append(relative)
        if relative not in ("adapters/claude_cli.py", "adapters/factory.py") and "ClaudeCLIAdapter(" in text:
            offenders.append(relative)
        if not relative.startswith("adapters/") and ('"exec", "--json"' in text or '"--output-format", "json"' in text):
            offenders.append(relative)
    assert offenders == []


# --- the REAL codex, no model: what tool list does it send? (opt-in; needs codex on PATH) --------------

_CANARY_MCP = '''
import json, sys
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    method, ident = msg.get("method"), msg.get("id")
    if ident is None:
        continue
    if method == "initialize":
        result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "canary", "version": "0"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "canary_fetch", "description": "Fetch a URL (synthetic canary).",
                             "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}}}]}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}) + "\\n"); sys.stdout.flush()
'''


def _tool_definitions(payload: object) -> list[str]:
    """Every tool definition anywhere in a Responses request (top-level ``tools`` or an ``additional_tools`` input item)."""

    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "web_search" or ("name" in node and {"parameters", "format", "input_schema", "strict"} & set(node)):
                found.add(":".join(str(part) for part in (node.get("type"), node.get("name")) if part))
            if node.get("type") == "namespace" and str(node.get("name", "")).startswith("mcp__"):
                found.add(f"namespace:{node['name']}")
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return sorted(found)


@pytest.mark.skipif(
    os.environ.get("GIGAI_CODEX_TOOL_SURFACE") != "1" or shutil.which("codex") is None,
    reason="runs the installed codex against a local stand-in endpoint (no model, no login): set GIGAI_CODEX_TOOL_SURFACE=1",
)
def test_the_installed_codex_sends_no_tool_but_request_user_input(tmp_path: Path) -> None:
    """The adapter's real argv for EVERY model in codex's catalog, a scratch CODEX_HOME that enables tools, a local endpoint.

    Run this after a codex upgrade: a new tool feature or catalog field shows up here as an extra definition.
    """

    requests: list[list[str]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - http.server API
            pass

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            requests.append(_tool_definitions(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))))
            events = [
                {"type": "response.created", "response": {"id": "resp_mock"}},
                {"type": "response.output_item.done", "item": {
                    "type": "message", "role": "assistant", "id": "msg_mock", "content": [{"type": "output_text", "text": "ok"}]}},
                {"type": "response.completed", "response": {"id": "resp_mock", "usage": {
                    "input_tokens": 1, "input_tokens_details": None, "output_tokens": 1,
                    "output_tokens_details": None, "total_tokens": 2}}},
            ]
            body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    codex_home, work = tmp_path / "codex-home", tmp_path / "work"
    codex_home.mkdir()
    work.mkdir()
    (tmp_path / "canary_mcp.py").write_text(_CANARY_MCP, encoding="utf-8")
    (codex_home / "config.toml").write_text(
        'web_search = "live"\nmodel_provider = "mock"\nsuppress_unstable_features_warning = true\n'
        f'[model_providers.mock]\nname = "mock"\nbase_url = "http://127.0.0.1:{server.server_address[1]}/v1"\n'
        'wire_api = "responses"\nenv_key = "PATH"\n'
        "[features]\nshell_tool = true\ncode_mode = true\nmulti_agent_v2 = true\nrequest_permissions_tool = true\n"
        f'[mcp_servers.canary]\ncommand = "{sys.executable}"\nargs = ["{tmp_path / "canary_mcp.py"}"]\n',
        encoding="utf-8",
    )
    env = {"HOME": str(tmp_path), "CODEX_HOME": str(codex_home), "PATH": os.environ["PATH"]}
    executable = shutil.which("codex") or ""
    adapter = CodexCLIAdapter()

    def run(argv: tuple[str, ...] | list[str]) -> list[str]:
        done = subprocess.run(argv, input="Reply with the word ok", capture_output=True, text=True, cwd=work, env=env, timeout=120)
        assert done.returncode == 0, done.stderr[-400:]
        return requests[-1]

    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("CODEX_HOME", str(codex_home))
            patch.setenv("HOME", str(tmp_path))
            lockdown = codex_lockdown(executable)
        assert lockdown.model_catalog is not None and lockdown.mcp_servers == ("canary",)
        (work / MODEL_CATALOG_FILE).write_text(lockdown.model_catalog, encoding="utf-8")
        models = [model["slug"] for model in json.loads(lockdown.model_catalog)["models"]]
        old = [executable, "exec", "--json", "--ephemeral", "--sandbox", "read-only", "--disable", "shell_tool",
               "--disable", "memories", "--skip-git-repo-check", "--cd", str(work)]
        before = {model: run([*old, "--model", model, "-"]) for model in ("not-in-the-catalog", *models[:3])}
        after = {model: run(adapter.argv(_request(model=model), str(work), lockdown)) for model in ("not-in-the-catalog", *models)}
    finally:
        server.shutdown()

    for model, tools in before.items():
        print(f"\nCODEX-TOOL-SURFACE old {model}: {tools}")
    for model, tools in after.items():
        print(f"\nCODEX-TOOL-SURFACE new {model}: {tools}")
    assert all(len(tools) > 5 for tools in before.values()), "the old argv offered tools for every model"
    assert any("web_search" in tools for tools in before.values()) and any("spawn_agent" in " ".join(tools) for tools in before.values())
    assert set(after) > {"not-in-the-catalog"}
    assert {model: tools for model, tools in after.items() if tools != ["function:request_user_input"]} == {}
