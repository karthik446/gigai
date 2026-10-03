"""0110 P1 (security, DNS rebinding): every GET answers only a Host the server is bound to.

A hostile page that rebinds its DNS name to 127.0.0.1 sends ``Host:
evil.example:<port>``; before this guard only a few GET routes checked it, so
``/api/answers``, ``/api/assessments`` and the like answered 200. Every GET
route (the OpenAPI route table plus ``/``, ``/llms.txt``, ``/api``,
``/api/health``, ``/api/openapi.json`` and a static asset) must now answer
403 ``forbidden_origin`` for a foreign Host, and not 403 for the two bound ones.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs.api.openapi import ROUTES
from tests.api_e2e.harness import setup_and_init, start_server, stop_server

_RUN_ID = "run_00000000-0000-4000-8000-000000000000"
_EXTRA_PATHS = ("/", "/llms.txt", "/api", "/api/", "/api/health", "/api/openapi.json", "/index.html", "/assets/missing.js", "/favicon.ico")


def _get_paths() -> list[str]:
    paths = [re.sub(r"\{run_id\}", _RUN_ID, re.sub(r"\{[a-z_]+\}", "x", r.path)) for r in ROUTES if r.method == "GET"]
    return sorted(set(paths) | set(_EXTRA_PATHS))


def test_every_get_route_checks_the_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        paths = _get_paths()
        assert len(paths) > 20
        # the five routes the spike read with a foreign Host are in the set
        for known in ("/api/answers", "/api/tailored-resumes", "/api/assessments", "/api/config", "/api/runs"):
            assert known in paths
        port = server.port
        bad: list[str] = []
        for path in paths:
            for host in ("evil.example", "evil.example:80", f"evil.example:{port}", f"127.0.0.1:{port + 1}", f"localhost.evil.example:{port}"):
                response = httpx.get(f"{server.base_url}{path}", headers={"Host": host})
                if response.status_code != 403 or response.json().get("error", {}).get("code") != "forbidden_origin":
                    bad.append(f"{path} Host={host} -> {response.status_code}")
            for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
                response = httpx.get(f"{server.base_url}{path}", headers={"Host": host})
                if response.status_code == 403:
                    bad.append(f"{path} Host={host} -> 403")
        assert not bad, "\n".join(bad)
    finally:
        stop_server(server)
