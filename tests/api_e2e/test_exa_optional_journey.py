"""uat-bug-033: Exa is optional and off for a new setup, over HTTP.

Through the real supervised server from a FRESH home, no Exa key anywhere:

1. A new setup's config has ``sources.exa`` false and the key status says
   ``exa`` is not set.
2. The wizard's own finish code (node) completes with no Exa key, and Exa is
   still off afterwards.
3. Settings' toggle (``PUT /api/config/sources``) turns Exa on and off, saving
   only that toggle; turning it on with no key saves (the key hint is the
   UI's); a bad body is refused.
4. An OLD config (Exa on) keeps its saved value through the wizard's save.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.api_e2e.harness import setup_and_init, start_server, stop_server
from tests.api_e2e.test_wizard_stores_resume_journey import (
    PASTED,
    TITLES,
    _node,
    _run_the_wizard,
    _setup_body,
)


class _NoExaKey:
    """``start_server`` sets a fake ``EXA_API_KEY`` for its child; a fresh
    machine has none, so this monkeypatch view drops that one setting."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch

    def setenv(self, name: str, value: str) -> None:
        if name != "EXA_API_KEY":
            self._monkeypatch.setenv(name, value)


def _start_without_an_exa_key(home: Path, target: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    return start_server(home, target, monkeypatch=_NoExaKey(monkeypatch))  # type: ignore[arg-type]


def _sources(client) -> dict:
    return client.get("/api/config").json()["config"]["sources"]


def test_a_new_setup_has_exa_off_and_the_wizard_needs_no_exa_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = _node()
    home, target = setup_and_init(tmp_path)
    server = _start_without_an_exa_key(home, target, monkeypatch)
    try:
        client = server.client
        assert _sources(client) == {"exa": False, "ats": True, "hiringcafe": False}
        assert client.get("/api/secrets/status").json()["keys"]["exa"] is False

        runs = _run_the_wizard(
            node,
            server,
            times=1,
            fields={
                "profileName": "Staff platform engineer",
                "resumeMode": "paste",
                "resumeText": PASTED,
                "titles": TITLES,
                "workMode": "remote",
            },
        )
        assert len(runs) == 1
        assert client.get("/api/setup").json()["prefs"]["roles"] == TITLES
        assert _sources(client)["exa"] is False
    finally:
        stop_server(server)


def test_the_settings_toggle_saves_only_the_exa_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    server = _start_without_an_exa_key(home, target, monkeypatch)
    try:
        client = server.client
        before = json.loads((target / "find-jobs.json").read_text())
        assert before["sources"]["exa"] is False

        on = client.put("/api/config/sources", json={"exa": True})
        assert on.status_code == 200, on.text
        assert on.json() == {"sources": {"exa": True}}
        assert _sources(client) == {"exa": True, "ats": True, "hiringcafe": False}
        after = json.loads((target / "find-jobs.json").read_text())
        assert {k: v for k, v in after.items() if k != "sources"} == {k: v for k, v in before.items() if k != "sources"}
        # No key set, and it still saved: the hint is the UI's.
        assert client.get("/api/secrets/status").json()["keys"]["exa"] is False

        off = client.put("/api/config/sources", json={"exa": False})
        assert off.status_code == 200, off.text
        assert _sources(client)["exa"] is False

        for bad in ({}, {"exa": "yes"}, {"exa": True, "ats": False}, {"ats": False}):
            refused = client.put("/api/config/sources", json=bad)
            assert refused.status_code == 422, (bad, refused.text)
        assert _sources(client)["exa"] is False
    finally:
        stop_server(server)


def test_an_old_config_with_exa_on_keeps_it_through_the_wizards_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = setup_and_init(tmp_path)
    server = _start_without_an_exa_key(home, target, monkeypatch)
    try:
        client = server.client
        assert client.put("/api/config/sources", json={"exa": True}).status_code == 200
        saved = client.put("/api/setup", json=_setup_body(TITLES))
        assert saved.status_code == 200, saved.text
        assert _sources(client)["exa"] is True
    finally:
        stop_server(server)
