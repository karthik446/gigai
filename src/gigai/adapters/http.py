"""Shared HTTP transport plumbing for provider adapters below the model port."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import httpx

from ..config import CredentialReference
from ..credentials import resolve_reference_value
from .port import ModelInvocationError


CredentialResolver = Callable[[CredentialReference], str]
DEFAULT_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)


class HttpModelAdapter:
    """Shared credential-at-send-time and bounded JSON HTTP behavior."""

    def __init__(
        self,
        *,
        credential: CredentialReference,
        base_url: str,
        credential_resolver: CredentialResolver | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._credential = credential
        self._base_url = base_url.rstrip("/")
        self._credential_resolver = credential_resolver or resolve_reference_value
        self._client = client

    def _post_json(
        self,
        path: str,
        payload: Mapping[str, object],
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        token = self._credential_resolver(self._credential)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=DEFAULT_HTTP_TIMEOUT)
        try:
            response = client.post(f"{self._base_url}{path}", json=dict(payload), headers=headers)
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException as exc:
            raise ModelInvocationError("model HTTP invocation timed out") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ModelInvocationError(f"model HTTP invocation failed: {exc}") from exc
        finally:
            if owns_client:
                client.close()
        if type(body) is not dict:
            raise ModelInvocationError("model HTTP invocation returned a non-object JSON body")
        return body


__all__ = ["CredentialResolver", "DEFAULT_HTTP_TIMEOUT", "HttpModelAdapter"]
