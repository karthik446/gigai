"""Known-service map: logical service name -> environment variable name.

Only services gigai's own code actually reads today are listed here (see
``src/gigai/scout/find_jobs/exa_client.py:51`` for ``EXA_API_KEY`` and
``src/gigai/cli.py:1769-1770`` / ``src/gigai/setup_interview.py:215-219``
for the model-provider keys). Adding a service here does not make gigai use
it; this module only names services the code already reads by env var.
"""

from __future__ import annotations


KNOWN_SERVICES: dict[str, str] = {
    "exa": "EXA_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
}


class UnknownServiceError(ValueError):
    code = "secret_service_unknown"


def env_var_for(service: str) -> str:
    """Return the environment variable name for ``service``.

    Raises :class:`UnknownServiceError` listing the known services if
    ``service`` is not in the catalog.
    """

    try:
        return KNOWN_SERVICES[service]
    except KeyError:
        known = ", ".join(sorted(KNOWN_SERVICES))
        raise UnknownServiceError(
            f"unknown service {service!r}; known services: {known}"
        ) from None


def known_services() -> tuple[str, ...]:
    return tuple(sorted(KNOWN_SERVICES))


__all__ = ["KNOWN_SERVICES", "UnknownServiceError", "env_var_for", "known_services"]
