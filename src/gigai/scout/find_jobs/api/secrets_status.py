"""uat-bug-020: ``GET /api/secrets/status`` -- which API keys are set, as booleans.

The setup wizard's last screen names a key only when it is missing. No
existing response carried that, so this route answers ``{"keys": {"<service>": true|false}}``
for every service ``gigai secrets add`` knows (``secrets_catalog``).

Set means what the clients themselves resolve (``exa_client``): the environment variable, else the GigAI home's secret
store, non-empty. Booleans only: a key's value, length or prefix is never
read into the response or the log. Keys are still added from the CLI only.
"""

from __future__ import annotations

import os
from http import HTTPStatus
from pathlib import Path

from .... import secrets_store
from ....secrets_catalog import KNOWN_SERVICES

SCHEMA_VERSION = "scout-secrets-status:1"


def keys_set(home_root: Path | None) -> dict[str, bool]:
    """``{service: is its key set}`` for every known service."""

    # One read of the store, names only. With no home (a backend that has
    # none) only the environment counts: never the operator's default home.
    stored = secrets_store.names_set(home_root=home_root) if home_root is not None else set()
    return {
        service: bool((os.environ.get(env_var) or "").strip()) or env_var in stored
        for service, env_var in sorted(KNOWN_SERVICES.items())
    }


class SecretsStatusRoutesMixin:
    """``Handler`` mixin: ``GET /api/secrets/status``."""

    def _handle_get_secrets_status(self) -> None:
        home_root = getattr(self._backend, "home_root", None)
        self._write_json(
            HTTPStatus.OK,
            {"schema_version": SCHEMA_VERSION, "keys": keys_set(home_root)},
        )


__all__ = ["SCHEMA_VERSION", "SecretsStatusRoutesMixin", "keys_set"]
