"""The wizard's local LLM provider, written onto GigAI's ``config.toml``.

The Scout wizard stores the operator's local model choice in
``find-jobs.json`` (``local_llm_url`` / ``local_llm_protocol`` /
``local_llm_model``, validated there against the same loopback rule). For a
``local_llm`` model target to actually dispatch, ``~/.gigai/config.toml``
must carry an endpoint and a model target for it: this helper upserts the
ONE pair -- endpoint ``local-llm`` (adapter ``openai_local`` or
``anthropic_local`` by protocol) and target ``local-llm-default`` -- so
proposal dispatch resolves exactly one local target and a protocol change
swaps the endpoint in place rather than leaving a stale second one behind.

Loopback-only: the URL goes through the same explicit
``http://127.0.0.1:<port>`` check the adapters apply, so a wizard save can
never point a "local" provider at a remote host. A key-requiring server
(``llama.cpp --api-key``, vLLM) is supported: the key VALUE is written to
the 0600 secrets file (``LOCAL_LLM_API_KEY``) and config.toml only ever
carries a credential REFERENCE to it -- never the key itself.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .. import secrets_store
from ..adapters.http import checked_loopback_endpoint
from ..config import CredentialReference, Endpoint, ModelTarget, load_config, write_config_atomic

#: The single endpoint/target names a wizard save owns.
ENDPOINT_NAME = "local-llm"
TARGET_NAME = "local-llm-default"
#: The credential reference config.toml carries when the server needs a key.
CREDENTIAL_NAME = "local-llm-key"
#: The secrets-file name the key value lives under (never in config.toml).
SECRET_NAME = "LOCAL_LLM_API_KEY"
#: config._validate_config_relationships bounds every local target at 8192.
MAX_LOCAL_OUTPUT_TOKENS = 8192


def adapter_for_protocol(protocol: str) -> str:
    return "anthropic_local" if protocol == "anthropic" else "openai_local"


def ensure_local_llm_target(
    home_root: Path,
    url: str,
    protocol: str,
    model: str | None = None,
    *,
    api_key_required: bool = False,
    api_key: str | None = None,
) -> bool:
    """Upsert the loopback endpoint + ``local-llm-default`` target; changed-bytes flag.

    Raises ``ValueError`` for a non-loopback URL (the caller has usually
    validated already; this is the last gate before config.toml is touched)
    and ``ConfigurationError`` (missing/read-only/malformed) from the config
    layer. An empty/absent model id stores the placeholder ``"default"`` --
    a server that lists models is asked for ids before use.

    Key handling: when ``api_key_required`` and a fresh ``api_key`` arrives,
    it is stored in the secrets file; when required with no fresh key, an
    already-stored key is kept (a re-save of the wizard shouldn't demand
    retyping). When not required, the stored key and the credential
    reference are removed.
    """

    base_url = checked_loopback_endpoint(url, "local_llm_url")
    adapter = adapter_for_protocol(protocol)
    model_id = (model or "").strip() or "default"
    config = load_config(home_root)
    credential_name: str | None = None
    if api_key_required:
        fresh = (api_key or "").strip()
        if fresh:
            secrets_store.set(SECRET_NAME, fresh, home_root=home_root)
        elif secrets_store.get(SECRET_NAME, home_root=home_root) is None:
            raise ValueError(
                "local_llm_api_key: this server needs an API key -- enter one, "
                'or uncheck "API key required"'
            )
        credential_name = CREDENTIAL_NAME
    else:
        secrets_store.remove(SECRET_NAME, home_root=home_root)
    credentials = tuple(item for item in config.credentials if item.name != CREDENTIAL_NAME)
    if credential_name is not None:
        credentials += (CredentialReference(CREDENTIAL_NAME, "environment", SECRET_NAME),)
    endpoints = tuple(item for item in config.endpoints if item.name != ENDPOINT_NAME) + (
        Endpoint(name=ENDPOINT_NAME, adapter=adapter, credential=credential_name, base_url=base_url),
    )
    target = ModelTarget(
        name=TARGET_NAME,
        endpoint=ENDPOINT_NAME,
        model=model_id,
        capabilities=("text",),
        max_output_tokens=MAX_LOCAL_OUTPUT_TOKENS,
        reasoning_effort=None,
        enabled=True,
    )
    targets = tuple(item for item in config.model_targets if item.name != TARGET_NAME) + (target,)
    return write_config_atomic(
        replace(config, credentials=credentials, endpoints=endpoints, model_targets=targets)
    )
