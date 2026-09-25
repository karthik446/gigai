"""P1-8 (PR #37 review): ``--home`` was ignored by every secret-key lookup.

``gigai secrets add exa --home X`` stores the key under X's ``.env``, but
``credentials.resolve_reference_value``, the Exa client's ``_require_api_key``,
discovery's ``openai_source._api_key``, and interview prep's
``websearch.api_key_present``/``web_search_structured`` all called
``secrets_store.get(name)`` with no ``home_root``, so they only ever looked at
``GIGAI_HOME``/``~/.gigai`` -- never the home the operator actually chose.
These tests reproduce that gap directly against ``credentials.py`` and
``exa_client.py`` (the two owned modules with the simplest repro surface);
the discovery/prep threading is covered by their own test files with a
``home_root``-scoped case each. GIGAI_HOME is intentionally left unset (or
pointed elsewhere) in every case here so a fix that only *happens* to work
via GIGAI_HOME still fails these.

r1 addendum (coordinator review gap): the same problem existed one layer up,
in the MODEL adapters ``assess``/interview-prep category prediction use --
``adapters/factory.py``'s ``resolve_model_adapter`` built ``OpenAIAPIAdapter``/
``OpenRouterAPIAdapter`` with no ``credential_resolver``, so those adapters
fell back to their own default (``resolve_reference_value`` with no
``home_root``) regardless of ``--home``. Covered by
``test_factory_builds_home_aware_credential_resolver_for_remote_adapters``
below.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from gigai import secrets_store
from gigai.adapters.factory import resolve_model_adapter
from gigai.cli import cli
from gigai.config import CredentialReference, Endpoint, ModelTarget, Profile
from gigai.credentials import CredentialUnavailableError, resolve_reference_value
from gigai.scout.find_jobs.exa_client import ExaSearchClient
from gigai.setup import build_config


REFERENCE = CredentialReference(name="exa", kind="environment", reference="EXA_API_KEY")


@pytest.fixture(autouse=True)
def _no_default_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # GIGAI_HOME points somewhere that never has the key -- proves the
    # value came from the explicit home_root, not the default fallback.
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path / "decoy-home"))
    monkeypatch.delenv("EXA_API_KEY", raising=False)


def test_resolve_reference_value_finds_key_stored_under_explicit_home(tmp_path: Path) -> None:
    chosen_home = tmp_path / "chosen-home"
    secrets_store.set("EXA_API_KEY", "from-chosen-home", home_root=chosen_home)

    assert resolve_reference_value(REFERENCE, home_root=chosen_home) == "from-chosen-home"


def test_resolve_reference_value_without_home_root_still_uses_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Backward compatibility: omitting home_root keeps today's behavior
    # (env-then-default-home), unaffected by this change.
    decoy_home = Path(str(tmp_path / "decoy-home"))
    secrets_store.set("EXA_API_KEY", "from-default-home", home_root=decoy_home)

    assert resolve_reference_value(REFERENCE) == "from-default-home"


def test_resolve_reference_value_raises_when_key_only_in_other_home(tmp_path: Path) -> None:
    other_home = tmp_path / "other-home"
    secrets_store.set("EXA_API_KEY", "from-other-home", home_root=other_home)
    chosen_home = tmp_path / "chosen-home"

    with pytest.raises(CredentialUnavailableError):
        resolve_reference_value(REFERENCE, home_root=chosen_home)


def test_environment_variable_still_wins_over_explicit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chosen_home = tmp_path / "chosen-home"
    secrets_store.set("EXA_API_KEY", "from-chosen-home", home_root=chosen_home)
    monkeypatch.setenv("EXA_API_KEY", "from-environment")

    assert resolve_reference_value(REFERENCE, home_root=chosen_home) == "from-environment"


def test_cli_secrets_add_home_then_exa_client_home_root_finds_it(tmp_path: Path) -> None:
    """Acceptance (P1-8): ``gigai secrets add exa --home X`` then a caller

    resolving with that same ``home_root=X`` finds the key -- the real CLI
    entry point (``secrets add``), not just ``secrets_store.set`` directly.
    A full ``gigai scout run --home X --no-browser`` spawns a background
    server process (present_api.py/run_supervisor.py, owned by other
    workers, not exercised here); this proves the same link those own --
    the Exa client's key lookup honoring an explicit home_root -- via the
    real `secrets add` CLI command and the real ExaSearchClient.search()
    call, never printing the stored value.
    """

    chosen_home = tmp_path / "chosen-home"
    result = CliRunner().invoke(
        cli,
        ["secrets", "add", "exa", "--stdin", "--home", str(chosen_home)],
        input="cli-stored-exa-key\n",
    )
    assert result.exit_code == 0, result.output
    assert "cli-stored-exa-key" not in result.output

    from gigai.scout.find_jobs.contracts import FindJobsConfig, SourceToggles

    config = FindJobsConfig(
        roles=("software engineer",), merged_queries=("software engineer",),
        location="Denver, CO", remote=True, published_after=None,
        sources=SourceToggles(exa=True, ats=True, hiringcafe=False), countries=("US",),
    )

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"results": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ExaSearchClient().search(client, config, home_root=chosen_home)

    assert len(captured) == 1
    assert captured[0].headers["x-api-key"] == "cli-stored-exa-key"


def test_factory_builds_home_aware_credential_resolver_for_remote_adapters(
    tmp_path: Path,
) -> None:
    # r1: a key stored under a temp home (GIGAI_HOME pointing elsewhere,
    # never set to this home) must be found by the factory-built
    # openrouter_api adapter when resolve_model_adapter is given that same
    # home_root -- the assess/interview-prep model-call path, not the
    # find-jobs.json key lookups covered above.
    chosen_home = tmp_path / "chosen-home"
    secrets_store.set("OPENROUTER_API_KEY", "chosen-home-model-key", home_root=chosen_home)

    config = build_config(
        home_root=tmp_path / "decoy-home",
        workpad_root=tmp_path / "workpad",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        credentials=(CredentialReference("openrouter", "environment", "OPENROUTER_API_KEY"),),
        endpoints=(Endpoint(name="openrouter", adapter="openrouter_api", credential="openrouter"),),
        model_targets=(
            ModelTarget(
                name="assess-default", endpoint="openrouter", model="model-x",
                capabilities=("text",), max_output_tokens=64, reasoning_effort=None,
            ),
        ),
        profiles=(
            Profile(name="default", planner="assess-default", critic="assess-default", adjudicator="assess-default"),
        ),
    )

    binding = resolve_model_adapter(config, "assess-default", home_root=chosen_home)

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"model": "model-x", "choices": [{"message": {"content": "ok"}}]},
        )

    # resolve_model_adapter doesn't take a client override for remote
    # adapters (only ollama_local's transport_overrides does) -- inject the
    # MockTransport the same way the adapter's own constructor would.
    binding.port._client = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[attr-defined]

    request = binding.request(role="reviewer", prompt="hello")
    binding.port.invoke(request)

    assert len(captured) == 1
    assert captured[0].headers["authorization"] == "Bearer chosen-home-model-key"


def test_factory_without_home_root_keeps_default_resolver(tmp_path: Path) -> None:
    # Backward compatibility: omitting home_root from resolve_model_adapter
    # keeps today's default resolver (env, then the default home) -- a key
    # stashed under some other explicit home is not found.
    other_home = tmp_path / "other-home"
    secrets_store.set("OPENROUTER_API_KEY", "other-home-model-key", home_root=other_home)

    config = build_config(
        home_root=tmp_path / "decoy-home",
        workpad_root=tmp_path / "workpad",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        credentials=(CredentialReference("openrouter", "environment", "OPENROUTER_API_KEY"),),
        endpoints=(Endpoint(name="openrouter", adapter="openrouter_api", credential="openrouter"),),
        model_targets=(
            ModelTarget(
                name="assess-default", endpoint="openrouter", model="model-x",
                capabilities=("text",), max_output_tokens=64, reasoning_effort=None,
            ),
        ),
        profiles=(
            Profile(name="default", planner="assess-default", critic="assess-default", adjudicator="assess-default"),
        ),
    )

    binding = resolve_model_adapter(config, "assess-default")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request should be made without a resolvable key")

    binding.port._client = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[attr-defined]

    with pytest.raises(CredentialUnavailableError):
        binding.port._credential_resolver(binding.port._credential)  # type: ignore[attr-defined]
