"""The sole production factory that selects concrete model adapters."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import CredentialReference, GigAIConfig
from ..model_targets import ResolvedModelTarget, resolve_model_target
from .deterministic import DeterministicAdapter
from .openai_api import OpenAIAPIAdapter
from .openrouter_api import OpenRouterAPIAdapter
from .port import InvocationRequest, ModelInvocationPort


class AdapterFactoryError(ValueError):
    """A configured target cannot be bound to an adapter."""

    code = "adapter_factory_failed"


@dataclass(frozen=True)
class ModelAdapterBinding:
    """A resolved target and its port, without exposing provider choice upstream."""

    current: ResolvedModelTarget
    port: ModelInvocationPort

    def request(
        self,
        *,
        role: str,
        prompt: str,
        required_capabilities: frozenset[str] = frozenset({"text"}),
    ) -> InvocationRequest:
        """Build a fully resolved request using this target's declared policy."""

        return InvocationRequest(
            target_name=self.current.target.name,
            endpoint_name=self.current.endpoint.name,
            model=self.current.target.model,
            role=role,
            prompt=prompt,
            target_capabilities=frozenset(self.current.target.capabilities),
            required_capabilities=required_capabilities,
            max_output_tokens=self.current.target.max_output_tokens,
            reasoning_effort=self.current.target.reasoning_effort,
        )


def resolve_model_adapter(config: GigAIConfig, target_name: str) -> ModelAdapterBinding:
    """Resolve ``configuration -> target -> endpoint -> concrete adapter``."""

    target = resolve_model_target(config, target_name)
    endpoint = target.endpoint
    if endpoint.adapter == "deterministic":
        return ModelAdapterBinding(current=target, port=DeterministicAdapter())
    credential = _credential(config, endpoint.credential, endpoint.name)
    if endpoint.adapter == "openai_api":
        return ModelAdapterBinding(
            current=target,
            port=OpenAIAPIAdapter(
                credential=credential,
                base_url=endpoint.base_url,
            ),
        )
    if endpoint.adapter == "openrouter_api":
        return ModelAdapterBinding(
            current=target,
            port=OpenRouterAPIAdapter(
                credential=credential,
                base_url=endpoint.base_url,
            ),
        )
    raise AdapterFactoryError(
        f"model target {target_name!r} uses unsupported adapter {endpoint.adapter!r}"
    )


def _credential(
    config: GigAIConfig, credential_name: str | None, endpoint_name: str
) -> CredentialReference:
    if credential_name is None:
        raise AdapterFactoryError(
            f"remote endpoint {endpoint_name!r} has no credential reference"
        )
    credential = next(
        (item for item in config.credentials if item.name == credential_name), None
    )
    if credential is None:
        raise AdapterFactoryError(
            f"remote endpoint {endpoint_name!r} references unknown credential "
            f"{credential_name!r}"
        )
    return credential


__all__ = ["AdapterFactoryError", "ModelAdapterBinding", "resolve_model_adapter"]
