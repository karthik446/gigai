"""Shared HTTP transport plumbing for provider adapters below the model port."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import functools
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..config import CredentialReference
from ..credentials import resolve_reference_value
from .port import ModelInvocationError


CredentialResolver = Callable[[CredentialReference], str]
DEFAULT_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
#: Version header the Anthropic wire dialect expects alongside x-api-key.
ANTHROPIC_VERSION = "2023-06-01"


class HttpModelAdapter:
    """Shared credential-at-send-time and bounded JSON HTTP behavior."""

    def __init__(
        self,
        *,
        credential: CredentialReference | None = None,
        base_url: str,
        credential_resolver: CredentialResolver | None = None,
        home_root: Path | None = None,
        client: httpx.Client | None = None,
        auth_style: str = "bearer",
    ) -> None:
        """``home_root`` (P1-8 follow-up) selects which operator home the

        default resolver's secrets-store fallback uses when the environment
        variable isn't set. It's applied only when ``credential_resolver``
        is omitted -- an explicit ``credential_resolver`` (tests, or a
        caller with its own resolution policy) always wins and is used
        as-is, unaware of ``home_root``. Omitting both keeps today's default
        resolver (env, then the default home). ``resolve_reference_value``
        is imported only here (the sole raw-credential-resolution call site
        below the model port), never by the adapter factory.

        ``auth_style`` picks the header a credential is sent under:
        ``bearer`` (``Authorization: Bearer``) for OpenAI-dialect servers,
        ``x_api_key`` (``x-api-key`` + ``anthropic-version``) for the
        Anthropic dialect. The credential is resolved only at send time and
        the value never leaves this class.
        """

        if auth_style not in {"bearer", "x_api_key"}:
            raise ValueError(f"unsupported auth_style {auth_style!r}")
        self._credential = credential
        self._base_url = base_url.rstrip("/")
        self._credential_resolver = credential_resolver or functools.partial(
            resolve_reference_value, home_root=home_root
        )
        self._client = client
        self._auth_style = auth_style

    def _post_json(
        self,
        path: str,
        payload: Mapping[str, object],
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._credential is not None:
            # Remote adapters always carry a reference; loopback local
            # adapters carry one only when the operator checked "API key
            # required". Either way the value resolves only at send time.
            token = self._credential_resolver(self._credential)
            if self._auth_style == "x_api_key":
                headers["x-api-key"] = token
                headers["anthropic-version"] = ANTHROPIC_VERSION
            else:
                headers["Authorization"] = f"Bearer {token}"
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


def checked_loopback_endpoint(endpoint: str, label: str) -> str:
    """The validated ``http://127.0.0.1:<port>`` form, or ``ValueError``.

    The same explicit-numeric-loopback rule the Ollama adapter applies,
    shared by the OpenAI-/Anthropic-compatible local adapters so a local
    provider can never silently point at a remote host.
    """

    if not isinstance(endpoint, str) or "\x00" in endpoint:
        raise ValueError(f"{label} must be a NUL-free URL")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} must contain a valid explicit port") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or not 1 <= port <= 65_535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(
            f"{label} must be an explicit numeric loopback http://127.0.0.1:<port> "
            "URL without credentials, path, query, or fragment"
        )
    return f"http://127.0.0.1:{port}"


def list_local_models(
    base_url: str,
    *,
    api_key: str | None = None,
    protocol: str = "openai",
    client: httpx.Client | None = None,
    timeout_seconds: float = 5.0,
) -> tuple[str, ...]:
    """Model ids a loopback provider serves (``GET /v1/models``).

    Both the OpenAI- and Anthropic-compatible local protocols answer
    ``{"data": [{"id": ...}, ...]}`` at that path; non-loopback URLs are
    refused before any request is made. ``api_key`` (when the operator
    said the server needs one) goes out under ``Authorization: Bearer``
    for the OpenAI dialect and ``x-api-key`` + ``anthropic-version`` for
    the Anthropic one; it is never logged or echoed.
    """

    base = checked_loopback_endpoint(base_url, "local model endpoint")
    headers: dict[str, str] = {}
    key = (api_key or "").strip()
    if key:
        if protocol == "anthropic":
            headers["x-api-key"] = key
            headers["anthropic-version"] = ANTHROPIC_VERSION
        else:
            headers["Authorization"] = f"Bearer {key}"
    owns_client = client is None
    http_client = client or httpx.Client(timeout=httpx.Timeout(timeout_seconds))
    try:
        response = http_client.get(f"{base}/v1/models", headers=headers)
        response.raise_for_status()
        body = response.json()
    except httpx.TimeoutException as exc:
        raise ModelInvocationError("local model listing timed out") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise ModelInvocationError(f"local model listing failed: {exc}") from exc
    finally:
        if owns_client:
            http_client.close()
    if type(body) is not dict:
        raise ModelInvocationError("local model listing returned a non-object JSON body")
    data = body.get("data")
    if not isinstance(data, list):
        raise ModelInvocationError("local model listing returned no model array")
    return tuple(
        sorted(
            {
                item["id"]
                for item in data
                if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
            }
        )
    )


__all__ = [
    "CredentialResolver",
    "DEFAULT_HTTP_TIMEOUT",
    "HttpModelAdapter",
    "checked_loopback_endpoint",
    "list_local_models",
]
