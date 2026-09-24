"""Strip API-key/auth material before a response is ever written to disk.

Applied to every object this spike saves under ``fixtures/`` -- the run
request (minus headers, see ``run_agent.py``) and the run response. Belt and
suspenders: ``run_agent.py`` never includes the outgoing headers dict in
what it saves, and this module additionally walks the saved object and
redacts any key whose name looks like a credential, and any string value
that looks like a bearer token, in case a future response body ever echoes
request headers back (undocumented, not expected, but cheap to guard).
"""

from __future__ import annotations

import re

_CREDENTIAL_KEY_NAMES = {
    "x-api-key",
    "x_api_key",
    "authorization",
    "api_key",
    "apikey",
    "exa_api_key",
    "bearer",
}

# A bare Exa key look-alike (long opaque token) or an "Authorization: Bearer
# ..." style string value.
_BEARER_VALUE_RE = re.compile(r"bearer\s+\S+", re.IGNORECASE)


def _redact_value(value: object) -> object:
    if isinstance(value, str) and _BEARER_VALUE_RE.search(value):
        return "[REDACTED]"
    return value


def redact(obj: object) -> object:
    """Return a deep copy of ``obj`` with credential-shaped keys/values removed."""

    if isinstance(obj, dict):
        result: dict[str, object] = {}
        for key, value in obj.items():
            if isinstance(key, str) and key.strip().lower() in _CREDENTIAL_KEY_NAMES:
                result[key] = "[REDACTED]"
                continue
            result[key] = redact(value)
        return result
    if isinstance(obj, list):
        return [redact(item) for item in obj]
    return _redact_value(obj)


__all__ = ["redact"]
