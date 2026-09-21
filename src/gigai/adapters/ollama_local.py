"""Bounded transport for an explicitly identified local Ollama runtime.

This adapter deliberately owns no process lifecycle, credentials, cloud fallback,
or tool execution.  A caller must provide the numeric loopback endpoint and the
full digest it has selected; identity is checked before a prompt is sent and
again after the response to catch a model replacement race.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import re
from urllib.parse import urlsplit

import httpx

from .capabilities import require_capabilities
from .normalization import normalize_usage
from .port import (
    InvocationRequest,
    InvocationResult,
    ModelInvocationCancelled,
    ModelInvocationError,
)


class OllamaLocalAdapterError(ModelInvocationError):
    """The configured local Ollama transport cannot satisfy its policy."""

    code = "ollama_local_adapter_failed"


class OllamaLocalIdentityError(OllamaLocalAdapterError):
    """The endpoint did not prove the selected local model identity."""

    code = "ollama_local_identity_failed"


class OllamaLocalResponseError(OllamaLocalAdapterError):
    """The runtime returned a response that is not a complete answer."""

    code = "ollama_local_response_failed"


_DIGEST_RE = re.compile(r"\A(?:sha256:)?[0-9a-f]{64}\Z")
_MAX_TIMEOUT_SECONDS = 300.0
_MAX_CONTEXT_TOKENS = 262_144
_MAX_OUTPUT_TOKENS = 8_192
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_DEFAULT_RESPONSE_BYTES = 1 * 1024 * 1024
_USAGE_KEYS = (
    "prompt_eval_count",
    "eval_count",
    "total_duration",
    "load_duration",
    "prompt_eval_duration",
    "eval_duration",
)


def _checked_positive_int(value: object, *, label: str, maximum: int) -> int:
    if type(value) is not int or value <= 0 or value > maximum:
        raise ValueError(
            f"{label} must be a positive integer no greater than {maximum}"
        )
    return value


def _checked_endpoint(endpoint: str) -> str:
    if not isinstance(endpoint, str) or "\x00" in endpoint:
        raise ValueError("local Ollama endpoint must be a NUL-free URL")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            "local Ollama endpoint must contain a valid explicit port"
        ) from exc
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
            "local Ollama endpoint must be an explicit numeric loopback "
            "http://127.0.0.1:<port> URL "
            "without credentials, path, query, or fragment"
        )
    return f"http://127.0.0.1:{port}"


def _checked_model(model: str) -> str:
    if not isinstance(model, str) or not model or "\x00" in model or len(model) > 256:
        raise ValueError("local Ollama model tag must be a bounded non-empty string")
    lowered = model.casefold()
    base, separator, tag = lowered.partition(":")
    if base.endswith(("-cloud", "-remote")) or (
        separator
        and (tag in {"cloud", "remote"} or tag.endswith(("-cloud", "-remote")))
    ):
        raise ValueError(
            "local Ollama model tag must not identify a cloud or remote model"
        )
    return model


def _checked_digest(model_digest: str) -> str:
    if not isinstance(model_digest, str) or not _DIGEST_RE.fullmatch(model_digest):
        raise ValueError(
            "local Ollama model digest must be 64 lowercase hex digits with optional sha256 prefix"
        )
    return _canonical_digest(model_digest)


def _canonical_digest(value: object) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError("local Ollama model digest metadata is malformed")
    return value if value.startswith("sha256:") else f"sha256:{value}"


class OllamaLocalAdapter:
    """Invoke one explicitly selected model through a numeric-loopback Ollama API."""

    name = "ollama_local"

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        model_digest: str,
        timeout_seconds: float = 120.0,
        context_tokens: int = 4_096,
        max_output_tokens: int = 1_024,
        max_response_bytes: int = _DEFAULT_RESPONSE_BYTES,
        think: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if (
            type(timeout_seconds) not in {int, float}
            or not 0 < timeout_seconds <= _MAX_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"timeout_seconds must be positive and no greater than {_MAX_TIMEOUT_SECONDS}"
            )
        if isinstance(timeout_seconds, float) and not timeout_seconds.is_integer():
            timeout_seconds = float(timeout_seconds)
        self.endpoint = _checked_endpoint(endpoint)
        self.model = _checked_model(model)
        self.model_digest = _checked_digest(model_digest)
        self.timeout_seconds = float(timeout_seconds)
        self.context_tokens = _checked_positive_int(
            context_tokens, label="context_tokens", maximum=_MAX_CONTEXT_TOKENS
        )
        self.max_output_tokens = _checked_positive_int(
            max_output_tokens, label="max_output_tokens", maximum=_MAX_OUTPUT_TOKENS
        )
        self.max_response_bytes = _checked_positive_int(
            max_response_bytes, label="max_response_bytes", maximum=_MAX_RESPONSE_BYTES
        )
        if type(think) is not bool or think:
            raise ValueError("local Ollama adapter requires explicit think=False")
        self.think = False
        self._client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(self.timeout_seconds),
            trust_env=False,
            follow_redirects=False,
        )

    def close(self) -> None:
        """Close the transport owned by this adapter."""

        self._client.close()

    def __enter__(self) -> "OllamaLocalAdapter":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def invoke(self, request: InvocationRequest) -> InvocationResult:
        """Run one bounded, non-streaming chat request after identity checks."""

        require_capabilities(
            ("text",), request.required_capabilities, target_name=request.target_name
        )
        if request.model != self.model:
            raise OllamaLocalIdentityError(
                "invocation model does not match the selected local model"
            )
        if request.max_output_tokens > self.max_output_tokens:
            raise OllamaLocalAdapterError(
                "invocation exceeds the configured local output bound"
            )
        if request.reasoning_effort not in {None, "none"}:
            raise OllamaLocalAdapterError(
                "local Ollama adapter only supports reasoning_effort='none'"
            )

        try:
            runtime_version, digest = self._verify_identity()
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": request.prompt}],
                "stream": False,
                "think": False,
                "options": {
                    "num_ctx": self.context_tokens,
                    "num_predict": request.max_output_tokens,
                },
            }
            response = self._request_json("POST", "/api/chat", payload=payload)
            result = self._result_from_response(
                response,
                runtime_version=runtime_version,
                digest=digest,
                max_output_tokens=request.max_output_tokens,
            )
            try:
                after_version, after_digest = self._verify_identity()
            except OllamaLocalIdentityError as exc:
                raise OllamaLocalIdentityError(
                    "local Ollama identity changed during invocation"
                ) from exc
            if after_version != runtime_version or after_digest != digest:
                raise OllamaLocalIdentityError(
                    "local Ollama identity changed during invocation"
                )
            return result
        except KeyboardInterrupt as exc:
            raise ModelInvocationCancelled("local Ollama invocation cancelled") from exc

    def _verify_identity(self) -> tuple[str, str]:
        version_payload = self._request_json("GET", "/api/version")
        version = version_payload.get("version")
        if (
            not isinstance(version, str)
            or not version
            or len(version) > 128
            or "\x00" in version
        ):
            raise OllamaLocalIdentityError(
                "local Ollama runtime returned malformed version metadata"
            )

        tags_payload = self._request_json("GET", "/api/tags")
        models = tags_payload.get("models")
        if type(models) is not list:
            raise OllamaLocalIdentityError(
                "local Ollama runtime returned malformed model metadata"
            )
        matches = [
            entry
            for entry in models
            if isinstance(entry, dict) and entry.get("name") == self.model
        ]
        if len(matches) != 1:
            raise OllamaLocalIdentityError(
                "selected local Ollama model is missing or ambiguous"
            )
        entry = matches[0]
        try:
            digest = _canonical_digest(entry.get("digest"))
        except ValueError as exc:
            raise OllamaLocalIdentityError(
                "local Ollama runtime returned malformed model digest"
            ) from exc
        if digest != self.model_digest:
            raise OllamaLocalIdentityError(
                "selected local Ollama model digest does not match"
            )
        if self._remote_markers_present(entry):
            raise OllamaLocalIdentityError(
                "selected Ollama model is marked remote or cloud-backed"
            )
        return version, digest

    @staticmethod
    def _remote_markers_present(entry: Mapping[str, object]) -> bool:
        for key in ("remote", "is_remote", "cloud", "is_cloud"):
            if key not in entry:
                continue
            value = entry.get(key)
            if type(value) is not bool and not isinstance(value, str):
                return True
            if value is True or (isinstance(value, str) and bool(value.strip())):
                return True
        for key in ("remote_model", "cloud_model", "hosted_model"):
            if key not in entry:
                continue
            value = entry.get(key)
            if not isinstance(value, str):
                return True
            if value.strip():
                return True
        return False

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        try:
            with self._client.stream(
                method, f"{self.endpoint}{path}", json=payload
            ) as response:
                if response.status_code != 200:
                    raise OllamaLocalAdapterError(
                        f"local Ollama endpoint returned HTTP {response.status_code} for {method} {path}"
                    )
                body = bytearray()
                for chunk in response.iter_bytes():
                    if len(body) + len(chunk) > self.max_response_bytes:
                        raise OllamaLocalAdapterError(
                            "local Ollama response exceeds the byte bound"
                        )
                    body.extend(chunk)
        except OllamaLocalAdapterError:
            raise
        except httpx.TimeoutException as exc:
            raise OllamaLocalAdapterError("local Ollama request timed out") from exc
        except httpx.HTTPError as exc:
            raise OllamaLocalAdapterError(
                "local Ollama transport request failed"
            ) from exc
        try:
            decoded = json.loads(bytes(body).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise OllamaLocalAdapterError(
                "local Ollama endpoint returned malformed JSON"
            ) from exc
        if type(decoded) is not dict:
            raise OllamaLocalAdapterError(
                "local Ollama endpoint returned a non-object JSON response"
            )
        return decoded

    def _result_from_response(
        self,
        response: Mapping[str, object],
        *,
        runtime_version: str,
        digest: str,
        max_output_tokens: int,
    ) -> InvocationResult:
        if response.get("model") != self.model:
            raise OllamaLocalResponseError(
                "local Ollama response model identity does not match"
            )
        if response.get("done") is not True:
            raise OllamaLocalResponseError(
                "local Ollama response was not marked complete"
            )
        done_reason = response.get("done_reason")
        if done_reason is not None and done_reason != "stop":
            raise OllamaLocalResponseError(
                "local Ollama response was truncated or stopped unexpectedly"
            )
        message = response.get("message")
        if type(message) is not dict:
            raise OllamaLocalResponseError(
                "local Ollama response has no assistant message"
            )
        if message.get("role") != "assistant" or type(message.get("role")) is not str:
            raise OllamaLocalResponseError(
                "local Ollama response message role is not assistant"
            )
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            if (
                isinstance(message.get("thinking"), str)
                and message.get("thinking", "").strip()
            ):
                raise OllamaLocalResponseError(
                    "local Ollama response contains reasoning without final content"
                )
            raise OllamaLocalResponseError(
                "local Ollama response has empty assistant content"
            )

        usage: dict[str, object] = {}
        for key in _USAGE_KEYS:
            value = response.get(key)
            if value is not None:
                if type(value) is not int or value < 0:
                    raise OllamaLocalResponseError(
                        "local Ollama response has malformed usage metadata"
                    )
                if key == "eval_count" and value > max_output_tokens:
                    raise OllamaLocalResponseError(
                        "local Ollama response exceeds requested output bound"
                    )
                usage[key] = value
        if "prompt_eval_count" in usage:
            usage["prompt_tokens"] = usage["prompt_eval_count"]
        if "eval_count" in usage:
            usage["completion_tokens"] = usage["eval_count"]
        if "prompt_tokens" in usage and "completion_tokens" in usage:
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        usage["runtime_version"] = runtime_version
        usage["model_digest"] = digest
        return InvocationResult(
            status="success",
            output_text=content,
            resolved_model=self.model,
            raw_usage=usage,
            normalized_usage=normalize_usage(usage),
            cost_status="unavailable",
        )


__all__ = [
    "OllamaLocalAdapter",
    "OllamaLocalAdapterError",
    "OllamaLocalIdentityError",
    "OllamaLocalResponseError",
]
