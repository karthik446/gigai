from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from gigai.scout.find_jobs.contracts import (
    AcquireInput,
    ATSProvider,
    FindJobsConfig,
    NodeContext,
    PostingRow,
    SelectionRule,
    SourceKind,
    SourceToggles,
    WatchlistEntry,
    WatchlistFirstSeen,
)
from gigai.scout.find_jobs.exa_client import ExaClientError
from gigai.scout.find_jobs.market_acquisition import AcquireAllSourcesFailedError, acquire_node


FIXTURES = Path(__file__).parent / "fixtures"


def _context(tmp_path: Path, key: str = "acquire-001") -> NodeContext:
    return NodeContext(
        run_id="run_01", project_id="project_01", gig_id="gig_01",
        graph_id="find-jobs:functional", graph_version=1, goal_slug="acquire",
        manifest_digest="sha256:" + "a" * 64, operation_key=key,
        target_observation_digest="sha256:" + "b" * 64,
        workpad_path=str(tmp_path), redeemed_consent_ref="consent",
        model_target="ollama_local",
    )


def _config(*, exa: bool = True, ats: bool = True) -> FindJobsConfig:
    payload = json.loads((FIXTURES / "fixture-find-jobs-config-v1.json").read_text())
    config = FindJobsConfig.from_json(payload)
    return replace(config, sources=SourceToggles(exa=exa, ats=ats, hiringcafe=False))


def _input(rows=(), *, config: FindJobsConfig | None = None):
    return AcquireInput(
        config or _config(), "sha256:" + "c" * 64, None,
        tuple(rows), 10, SelectionRule.NEW_OR_EDITED_ROLE_MATCH,
    )


class _Watchlist:
    def __init__(self):
        self.added = []

    def add_to_watchlist(self, entry):
        self.added.append(entry)
        return entry

    def active_entries(self):
        return tuple(self.added)


class _Exa:
    def __init__(self, rows):
        self.rows = rows

    def search(self, client, config):
        return tuple(self.rows)


class _ATS:
    def list_board(self, client, provider, board_token, config):
        return ()


class _FailingExa:
    def __init__(self, exc: Exception):
        self.exc = exc

    def search(self, client, config):
        raise self.exc


class _FailingATS:
    def __init__(self, exc: Exception):
        self.exc = exc

    def list_board(self, client, provider, board_token, config):
        raise self.exc


class _ATS_ReturningRow:
    def __init__(self, row: PostingRow):
        self.row = row

    def list_board(self, client, provider, board_token, config):
        return (self.row,)


def test_supplied_rows_select_and_persist(monkeypatch, tmp_path):
    payload = json.loads((FIXTURES / "fixture-acquire-input-v1.json").read_text())
    row = PostingRow.from_json(payload["rows"][0])
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    out = acquire_node(_context(tmp_path), _input([row]), http_client=None,
                       exa=_Exa(()), ats=_ATS(), watchlist=_Watchlist())
    assert out.rows[0].outcome.value == "new"
    assert len(out.selected_postings) == 1


def test_live_exa_auto_adds_watchlist(monkeypatch, tmp_path):
    payload = json.loads((FIXTURES / "fixture-acquire-input-v1.json").read_text())
    row = PostingRow.from_json(payload["rows"][0])
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    watchlist = _Watchlist()
    out = acquire_node(_context(tmp_path, "acquire-002"), _input(), http_client=None,
                       exa=_Exa([row]), ats=_ATS(), watchlist=watchlist)
    assert out.watchlist_refs == ("scout_watchlist:greenhouse:acme",)
    assert watchlist.added[0].first_seen.source_kind.value == "exa"


def test_all_exa_fail_with_only_exa_enabled_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gigai.scout.find_jobs.market_acquisition.import_public_rows",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not write a batch when every source failed")),
    )
    config = _config(exa=True, ats=False)
    with pytest.raises(AcquireAllSourcesFailedError) as exc_info:
        acquire_node(
            _context(tmp_path, "acquire-003"),
            _input(config=config),
            http_client=None,
            exa=_FailingExa(RuntimeError("exa transport exploded")),
            ats=_ATS(),
            watchlist=_Watchlist(),
        )
    assert exc_info.value.code == "acquire_all_sources_failed"
    assert "exa transport exploded" not in str(exc_info.value)


def test_exa_fails_ats_succeeds_returns_rows_and_failure(monkeypatch, tmp_path):
    payload = json.loads((FIXTURES / "fixture-acquire-input-v1.json").read_text())
    row = PostingRow.from_json(payload["rows"][0])
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    watchlist = _Watchlist()
    watchlist.added.append(
        WatchlistEntry(
            watchlist_id="scout_watchlist:greenhouse:acme",
            provider=ATSProvider.GREENHOUSE,
            board_token="acme",
            company="Acme",
            state="active",
            first_seen=WatchlistFirstSeen(SourceKind.ATS, row.url, row.query_key, "acquire-004", "2026-09-22T00:00:00Z"),
        )
    )
    config = _config(exa=True, ats=True)
    out = acquire_node(
        _context(tmp_path, "acquire-004"),
        _input(config=config),
        http_client=None,
        exa=_FailingExa(RuntimeError("exa transport exploded")),
        ats=_ATS_ReturningRow(row),
        watchlist=watchlist,
    )
    assert out.rows and out.rows[0].posting.normalized_url == row.normalized_url
    assert any(failure.source_kind is SourceKind.EXA for failure in out.failures)


def test_missing_exa_api_key_with_only_exa_enabled_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gigai.scout.find_jobs.market_acquisition.import_public_rows",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not write a batch when every source failed")),
    )
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    config = _config(exa=True, ats=False)
    exc = ExaClientError("exa_missing_api_key", "EXA_API_KEY is not set in the environment; Exa discovery cannot run")
    with pytest.raises(AcquireAllSourcesFailedError) as exc_info:
        acquire_node(
            _context(tmp_path, "acquire-005"),
            _input(config=config),
            http_client=None,
            exa=_FailingExa(exc),
            ats=_ATS(),
            watchlist=_Watchlist(),
        )
    assert exc_info.value.code == "acquire_all_sources_failed"
    # The underlying ExaClientError's full message (including the "is not set
    # in the environment" prose) never crosses into the raised node error;
    # only the exception's type name (via FailureRow.code) is surfaced.
    assert "is not set in the environment" not in str(exc_info.value)
    assert "exaclienterror" in str(exc_info.value)
