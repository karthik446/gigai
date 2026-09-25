"""S2-B: the setup interview + "Discover companies" panel routes.

Covers ``GET/PUT /api/setup`` and ``POST /api/discover`` / ``GET
/api/discover/latest`` in ``present_api.py``. S2-A's real
``gigai.scout.find_jobs.discovery`` package hasn't landed yet (see the
INTERFACE CONTRACT in the dispatched packet), so every test here either:

- exercises HTTP routing/validation against a ``Backend``-protocol double
  (``_SetupDiscoverBackend``, no discovery module involved at all), or
- exercises ``ScoutFindJobsBackend``'s real setup/discover logic against a
  fake ``discovery`` module installed into ``sys.modules`` (matching the
  packet's own contract shape exactly: ``DiscoveryPrefs``, ``DiscoveryResult``,
  ``run_discovery``, ``latest_discovery``, ``load_prefs``, ``save_prefs``), or
- confirms the 503 ``discovery_unavailable`` degrade when no ``discovery``
  module is importable at all (the real, current state of this repo).

No live provider call anywhere; no ``git add``/commit/stash/reset/clean.
"""

from __future__ import annotations

import sys
import threading
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx
import pytest

from gigai.canonical import canonical_json_bytes, parse_json_bytes
from gigai.scout.find_jobs.contracts import FindJobsConfig, ModelTarget, PinnedResume, SourceToggles
from gigai.scout.find_jobs.present_api import (
    Backend,
    ConfigMissingError,
    DiscoveryConflictError,
    DiscoveryUnavailableError,
    ScoutFindJobsBackend,
    SetupPrefsMissingError,
    SetupValidationError,
    _validate_setup_body,
    serve,
)
from gigai.scout.profile_records import create_profile, selected_profile, write_profile

from tests.support.scout_profile_fixtures import build_gig_with_resume


# ---------------------------------------------------------------------------
# HTTP-routing-level tests: a Backend-protocol double, no discovery module.
# ---------------------------------------------------------------------------


def _config() -> FindJobsConfig:
    return FindJobsConfig(
        roles=("staff backend",),
        merged_queries=("staff backend",),
        location="Denver, CO",
        remote=True,
        published_after=None,
        sources=SourceToggles(exa=True, ats=True, hiringcafe=False),
        countries=("US",),
        visa_sponsorship_required=True,
    )


def _prefs_json() -> dict[str, object]:
    return {
        "roles": ["staff backend", "senior backend"],
        "titles_to_avoid": [],
        "countries": ["US"],
        "work_mode": "remote",
        "city": "Denver, CO",
        "visa_sponsorship_required": True,
        "exclude_companies": ["Coupang"],
        "watch_companies": [],
        "company_stage_size": None,
        "industries_include": [],
        "industries_exclude": [],
        "must_have_stack": [],
        "dealbreaker_stack": [],
        "cadence_days": 7,
        "budget_usd_per_session": 0.50,
    }


def _result_json(discovery_id: str = "disc_001", *, status: str = "succeeded") -> dict[str, object]:
    return {
        "discovery_id": discovery_id,
        "status": status,
        "started_at": "2026-09-20T00:00:00+00:00",
        "finished_at": "2026-09-20T00:05:00+00:00",
        "cost_usd": 0.025,
        "sources": [{"name": "openai_web_search", "runs": 1, "cost_usd": 0.025, "error": None}],
        "new_boards": [
            {
                "company": "Acme Inc",
                "provider": "greenhouse",
                "board_token": "acme",
                "careers_url": "https://boards.greenhouse.io/acme",
                "sponsorship": "yes",
                "sponsorship_evidence": "H-1B filing on record",
                "evidence_source_url": "https://example.com/evidence",
                "evidence_verified": True,
                "found_by": ["openai_web_search"],
                "matching_us_postings": 3,
            }
        ],
        "skipped": {"already_watched": 2},
    }


class _SetupDiscoverBackend:
    """A minimal ``Backend``-protocol double covering every method the
    handler calls, so routing/validation tests don't need a real
    ``ScoutFindJobsBackend`` or filesystem at all.
    """

    def __init__(
        self,
        *,
        config: FindJobsConfig | None = None,
        config_missing: bool = False,
        prefs: dict[str, object] | None = None,
        discovery_unavailable: bool = False,
        discovery_conflict: bool = False,
        prefs_missing_for_discover: bool = False,
        latest_result: dict[str, object] | None = None,
        running: bool = False,
    ) -> None:
        self.config = config if config is not None else _config()
        self.config_missing = config_missing
        self.prefs = prefs
        self.discovery_unavailable = discovery_unavailable
        self.discovery_conflict = discovery_conflict
        self.prefs_missing_for_discover = prefs_missing_for_discover
        self.latest_result = latest_result
        self.running = running
        self.written_prefs: dict[str, object] | None = None
        self.discover_started = False

    def read_config(self) -> tuple[FindJobsConfig, bytes]:
        if self.config_missing:
            raise ConfigMissingError(Path("/tmp/fixture-target/find-jobs.json"))
        return self.config, canonical_json_bytes(self.config.to_json())

    def resume_preview(self):
        return None

    def resume_metadata(self):
        return None

    def start_run(self, run_request, config_bytes, on_run_allocated) -> None:  # pragma: no cover - unused here
        raise NotImplementedError

    def run_status(self, run_id: str):  # pragma: no cover - unused here
        raise NotImplementedError

    def run_results(self, run_id: str):  # pragma: no cover - unused here
        raise NotImplementedError

    def run_progress(self, run_id: str):  # pragma: no cover - unused here
        raise NotImplementedError

    def read_setup(self) -> dict[str, object] | None:
        if self.discovery_unavailable:
            raise DiscoveryUnavailableError("not available")
        return self.prefs

    def write_setup(self, prefs_fields: dict[str, object]) -> dict[str, object]:
        if self.discovery_unavailable:
            raise DiscoveryUnavailableError("not available")
        self.written_prefs = prefs_fields
        return {**prefs_fields}

    def start_discovery(self, on_progress: Callable[[dict[str, object]], None]) -> str:
        if self.discovery_unavailable:
            raise DiscoveryUnavailableError("not available")
        if self.discovery_conflict:
            raise DiscoveryConflictError("already running")
        if self.prefs_missing_for_discover:
            raise SetupPrefsMissingError("no prefs")
        self.discover_started = True
        return "discovery_req_test123"

    def latest_discovery(self) -> dict[str, object] | None:
        if self.discovery_unavailable:
            raise DiscoveryUnavailableError("not available")
        return self.latest_result

    def discovery_running(self) -> bool:
        return self.running


@pytest.fixture
def running_server(request: pytest.FixtureRequest):
    backend: Backend = getattr(request, "param", None) or _SetupDiscoverBackend()
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}") as client:
            yield client, backend
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_get_setup_prefs_missing_returns_404_with_prefill(running_server) -> None:
    client, _backend = running_server
    response = client.get("/api/setup")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "prefs_missing"
    prefill = body["error"]["prefill"]
    assert prefill["roles"] == ["staff backend"]
    assert prefill["countries"] == ["US"]
    assert prefill["city"] == "Denver, CO"
    assert prefill["work_mode"] == "remote"
    assert prefill["visa_sponsorship_required"] is True


def test_get_setup_prefs_missing_prefills_defaults_when_config_also_missing() -> None:
    backend = _SetupDiscoverBackend(config_missing=True)
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        with httpx.Client(base_url=f"http://{host}:{port}") as client:
            response = client.get("/api/setup")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert response.status_code == 404
    prefill = response.json()["error"]["prefill"]
    assert prefill["roles"] == []
    assert prefill["countries"] == []
    assert prefill["visa_sponsorship_required"] is False


@pytest.mark.parametrize("running_server", [_SetupDiscoverBackend(prefs=_prefs_json())], indirect=True)
def test_get_setup_returns_saved_prefs(running_server) -> None:
    client, _backend = running_server
    response = client.get("/api/setup")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "scout-find-jobs-setup-response:1"
    assert body["prefs"] == _prefs_json()


@pytest.mark.parametrize("running_server", [_SetupDiscoverBackend(discovery_unavailable=True)], indirect=True)
def test_get_setup_503_when_discovery_module_unavailable(running_server) -> None:
    client, _backend = running_server
    response = client.get("/api/setup")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "discovery_unavailable"


def test_put_setup_writes_prefs_and_returns_them(running_server) -> None:
    client, backend = running_server
    response = client.put("/api/setup", json=_prefs_json())
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "scout-find-jobs-setup-response:1"
    assert body["prefs"]["roles"] == ["staff backend", "senior backend"]
    assert backend.written_prefs is not None
    assert backend.written_prefs["roles"] == ("staff backend", "senior backend")


def test_put_setup_rejects_missing_roles_with_400_field_errors(running_server) -> None:
    client, _backend = running_server
    bad = _prefs_json()
    bad["roles"] = []
    response = client.put("/api/setup", json=bad)
    assert response.status_code == 400
    body = response.json()
    assert "roles" in body["error"]["field_errors"]


def test_put_setup_rejects_bad_country_code(running_server) -> None:
    client, _backend = running_server
    bad = _prefs_json()
    bad["countries"] = ["USA"]
    response = client.put("/api/setup", json=bad)
    assert response.status_code == 400
    assert "countries" in response.json()["error"]["field_errors"]


def test_put_setup_rejects_bad_work_mode(running_server) -> None:
    client, _backend = running_server
    bad = _prefs_json()
    bad["work_mode"] = "wfh"
    response = client.put("/api/setup", json=bad)
    assert response.status_code == 400
    assert "work_mode" in response.json()["error"]["field_errors"]


def test_put_setup_rejects_unknown_field(running_server) -> None:
    client, _backend = running_server
    bad = _prefs_json()
    bad["bogus_field"] = "x"
    response = client.put("/api/setup", json=bad)
    assert response.status_code == 400
    assert "_" in response.json()["error"]["field_errors"]


def test_put_setup_applies_defaults_for_optional_fields(running_server) -> None:
    client, backend = running_server
    minimal = {"roles": ["staff backend"], "countries": ["US"], "work_mode": "remote", "city": None}
    response = client.put("/api/setup", json=minimal)
    assert response.status_code == 200
    assert backend.written_prefs["cadence_days"] == 7
    assert backend.written_prefs["budget_usd_per_session"] == 0.50
    assert backend.written_prefs["visa_sponsorship_required"] is False


@pytest.mark.parametrize("running_server", [_SetupDiscoverBackend(discovery_unavailable=True)], indirect=True)
def test_put_setup_503_when_discovery_module_unavailable(running_server) -> None:
    client, _backend = running_server
    response = client.put("/api/setup", json=_prefs_json())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "discovery_unavailable"


def test_post_discover_returns_202_with_discovery_id(running_server) -> None:
    client, backend = running_server
    response = client.post("/api/discover", json={})
    assert response.status_code == 202
    assert response.json()["discovery_id"] == "discovery_req_test123"
    assert backend.discover_started is True


@pytest.mark.parametrize("running_server", [_SetupDiscoverBackend(discovery_conflict=True)], indirect=True)
def test_post_discover_409_when_already_running(running_server) -> None:
    client, _backend = running_server
    response = client.post("/api/discover", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "discovery_running"


@pytest.mark.parametrize("running_server", [_SetupDiscoverBackend(discovery_unavailable=True)], indirect=True)
def test_post_discover_503_when_discovery_module_unavailable(running_server) -> None:
    client, _backend = running_server
    response = client.post("/api/discover", json={})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "discovery_unavailable"


@pytest.mark.parametrize("running_server", [_SetupDiscoverBackend(prefs_missing_for_discover=True)], indirect=True)
def test_post_discover_404_when_prefs_missing(running_server) -> None:
    client, _backend = running_server
    response = client.post("/api/discover", json={})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "prefs_missing"


@pytest.mark.parametrize(
    "running_server",
    [_SetupDiscoverBackend(latest_result=_result_json())],
    indirect=True,
)
def test_get_discover_latest_returns_result_and_days_ago(running_server) -> None:
    client, _backend = running_server
    response = client.get("/api/discover/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "scout-find-jobs-discover-latest-response:1"
    assert body["result"]["discovery_id"] == "disc_001"
    assert body["running"] is False
    assert isinstance(body["days_ago"], int)
    assert body["days_ago"] >= 0


def test_get_discover_latest_returns_null_result_when_none_exists(running_server) -> None:
    client, _backend = running_server
    response = client.get("/api/discover/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["result"] is None
    assert body["days_ago"] is None
    assert body["running"] is False


@pytest.mark.parametrize(
    "running_server",
    [_SetupDiscoverBackend(latest_result=_result_json(status="running"), running=True)],
    indirect=True,
)
def test_get_discover_latest_reflects_running_state(running_server) -> None:
    client, _backend = running_server
    response = client.get("/api/discover/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["running"] is True
    assert body["result"]["status"] == "running"


@pytest.mark.parametrize("running_server", [_SetupDiscoverBackend(discovery_unavailable=True)], indirect=True)
def test_get_discover_latest_503_when_discovery_module_unavailable(running_server) -> None:
    client, _backend = running_server
    response = client.get("/api/discover/latest")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "discovery_unavailable"


# ---------------------------------------------------------------------------
# _validate_setup_body unit tests (no HTTP, no backend).
# ---------------------------------------------------------------------------


def test_validate_setup_body_happy_path_round_trips_all_11_fields() -> None:
    fields = _validate_setup_body(_prefs_json())
    assert fields["roles"] == ("staff backend", "senior backend")
    assert fields["titles_to_avoid"] == ()
    assert fields["countries"] == ("US",)
    assert fields["work_mode"] == "remote"
    assert fields["city"] == "Denver, CO"
    assert fields["visa_sponsorship_required"] is True
    assert fields["exclude_companies"] == ("Coupang",)
    assert fields["cadence_days"] == 7
    assert fields["budget_usd_per_session"] == 0.50


def test_validate_setup_body_rejects_non_object() -> None:
    with pytest.raises(SetupValidationError) as excinfo:
        _validate_setup_body(["not", "an", "object"])
    assert "_" in excinfo.value.field_errors


def test_validate_setup_body_rejects_negative_budget() -> None:
    bad = _prefs_json()
    bad["budget_usd_per_session"] = -1
    with pytest.raises(SetupValidationError) as excinfo:
        _validate_setup_body(bad)
    assert "budget_usd_per_session" in excinfo.value.field_errors


def test_validate_setup_body_rejects_non_positive_cadence() -> None:
    bad = _prefs_json()
    bad["cadence_days"] = 0
    with pytest.raises(SetupValidationError) as excinfo:
        _validate_setup_body(bad)
    assert "cadence_days" in excinfo.value.field_errors


# ---------------------------------------------------------------------------
# ScoutFindJobsBackend-level tests against a fake discovery module.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeDiscoveryPrefs:
    roles: tuple[str, ...]
    titles_to_avoid: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    work_mode: str = "any"
    city: str | None = None
    visa_sponsorship_required: bool = False
    exclude_companies: tuple[str, ...] = ()
    watch_companies: tuple[str, ...] = ()
    company_stage_size: str | None = None
    industries_include: tuple[str, ...] = ()
    industries_exclude: tuple[str, ...] = ()
    must_have_stack: tuple[str, ...] = ()
    dealbreaker_stack: tuple[str, ...] = ()
    cadence_days: int = 7
    budget_usd_per_session: float = 0.50

    def to_json(self) -> dict[str, object]:
        return {
            "roles": list(self.roles),
            "titles_to_avoid": list(self.titles_to_avoid),
            "countries": list(self.countries),
            "work_mode": self.work_mode,
            "city": self.city,
            "visa_sponsorship_required": self.visa_sponsorship_required,
            "exclude_companies": list(self.exclude_companies),
            "watch_companies": list(self.watch_companies),
            "company_stage_size": self.company_stage_size,
            "industries_include": list(self.industries_include),
            "industries_exclude": list(self.industries_exclude),
            "must_have_stack": list(self.must_have_stack),
            "dealbreaker_stack": list(self.dealbreaker_stack),
            "cadence_days": self.cadence_days,
            "budget_usd_per_session": self.budget_usd_per_session,
        }

    @classmethod
    def from_json(cls, obj: dict[str, object]) -> "_FakeDiscoveryPrefs":
        return cls(
            roles=tuple(obj["roles"]),
            titles_to_avoid=tuple(obj.get("titles_to_avoid", ())),
            countries=tuple(obj.get("countries", ())),
            work_mode=obj.get("work_mode", "any"),
            city=obj.get("city"),
            visa_sponsorship_required=obj.get("visa_sponsorship_required", False),
            exclude_companies=tuple(obj.get("exclude_companies", ())),
            watch_companies=tuple(obj.get("watch_companies", ())),
            company_stage_size=obj.get("company_stage_size"),
            industries_include=tuple(obj.get("industries_include", ())),
            industries_exclude=tuple(obj.get("industries_exclude", ())),
            must_have_stack=tuple(obj.get("must_have_stack", ())),
            dealbreaker_stack=tuple(obj.get("dealbreaker_stack", ())),
            cadence_days=obj.get("cadence_days", 7),
            budget_usd_per_session=obj.get("budget_usd_per_session", 0.50),
        )


@dataclass(frozen=True)
class _FakeDiscoveryResult:
    discovery_id: str
    status: str = "succeeded"
    started_at: str = "2026-09-20T00:00:00+00:00"
    finished_at: str | None = "2026-09-20T00:05:00+00:00"
    cost_usd: float = 0.025
    sources: tuple = ()
    new_boards: tuple = ()
    skipped: dict = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {
            "discovery_id": self.discovery_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cost_usd": self.cost_usd,
            "sources": list(self.sources),
            "new_boards": list(self.new_boards),
            "skipped": dict(self.skipped),
        }


def _install_fake_discovery_module(
    monkeypatch: pytest.MonkeyPatch,
    *,
    prefs_store: dict[str, "_FakeDiscoveryPrefs"],
    results_store: dict[str, "_FakeDiscoveryResult"],
    run_discovery_impl=None,
) -> types.ModuleType:
    """Install a fake ``gigai.scout.find_jobs.discovery`` module into
    ``sys.modules`` matching the S2-A/S2-B interface contract exactly, so
    ``ScoutFindJobsBackend``'s lazy ``from gigai.scout.find_jobs import
    discovery`` import resolves to this fake for the duration of one test.
    """

    module = types.ModuleType("gigai.scout.find_jobs.discovery")
    module.DiscoveryPrefs = _FakeDiscoveryPrefs
    module.DiscoveryResult = _FakeDiscoveryResult

    def load_prefs(*, home_root: Path, target: Path):
        return prefs_store.get(str(target))

    def save_prefs(*, home_root: Path, target: Path, prefs) -> None:
        prefs_store[str(target)] = prefs

    def latest_discovery(*, home_root: Path, target: Path):
        return results_store.get(str(target))

    def run_discovery(*, home_root: Path, target: Path, prefs, runs: int = 3, on_progress=None):
        if run_discovery_impl is not None:
            result = run_discovery_impl(home_root=home_root, target=target, prefs=prefs, on_progress=on_progress)
        else:
            if on_progress is not None:
                on_progress({"status": "running", "step": "search"})
            result = _FakeDiscoveryResult(discovery_id="disc_generated_001")
        results_store[str(target)] = result
        return result

    module.load_prefs = load_prefs
    module.save_prefs = save_prefs
    module.latest_discovery = latest_discovery
    module.run_discovery = run_discovery

    monkeypatch.setitem(sys.modules, "gigai.scout.find_jobs.discovery", module)
    # ``_discovery_module`` does ``from gigai.scout.find_jobs import
    # discovery``, which is an attribute lookup on the already-imported
    # parent package, not (only) a sys.modules lookup by dotted name. Once
    # any other test in this run has really imported
    # ``gigai.scout.find_jobs.discovery`` (e.g. test_discovery_prefs.py),
    # that import binds ``discovery`` as an attribute of the
    # ``gigai.scout.find_jobs`` package object; patching sys.modules alone
    # then has no effect on a later ``from ... import discovery`` in this
    # process, since Python finds the attribute first. Patching the parent
    # package's attribute too closes that gap regardless of what else has
    # already imported the real module in this test session.
    import gigai.scout.find_jobs as find_jobs_package

    monkeypatch.setattr(find_jobs_package, "discovery", module, raising=False)
    return module


@pytest.fixture
def backend_fixture(tmp_path: Path) -> ScoutFindJobsBackend:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir(parents=True)
    home.mkdir(parents=True)
    return ScoutFindJobsBackend(home_root=home, target=target)


def test_read_setup_returns_none_when_no_prefs_saved(
    backend_fixture: ScoutFindJobsBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_discovery_module(monkeypatch, prefs_store={}, results_store={})
    assert backend_fixture.read_setup() is None


def test_read_setup_returns_saved_prefs_json(
    backend_fixture: ScoutFindJobsBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefs_store: dict[str, _FakeDiscoveryPrefs] = {}
    _install_fake_discovery_module(monkeypatch, prefs_store=prefs_store, results_store={})
    prefs_store[str(backend_fixture.target)] = _FakeDiscoveryPrefs(roles=("staff backend",))
    result = backend_fixture.read_setup()
    assert result is not None
    assert result["roles"] == ["staff backend"]


def test_write_setup_saves_prefs_and_updates_find_jobs_json_when_missing(
    backend_fixture: ScoutFindJobsBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefs_store: dict[str, _FakeDiscoveryPrefs] = {}
    _install_fake_discovery_module(monkeypatch, prefs_store=prefs_store, results_store={})
    fields = {
        "roles": ("staff backend", "senior backend"),
        "titles_to_avoid": (),
        "countries": ("US",),
        "work_mode": "remote",
        "city": "Denver, CO",
        "visa_sponsorship_required": True,
        "exclude_companies": ("Coupang",),
        "watch_companies": (),
        "company_stage_size": None,
        "industries_include": (),
        "industries_exclude": (),
        "must_have_stack": (),
        "dealbreaker_stack": (),
        "cadence_days": 7,
        "budget_usd_per_session": 0.50,
    }

    backend_fixture.write_setup(fields)

    assert str(backend_fixture.target) in prefs_store
    saved_prefs = prefs_store[str(backend_fixture.target)]
    assert saved_prefs.roles == ("staff backend", "senior backend")

    config_path = backend_fixture.target / "find-jobs.json"
    assert config_path.is_file()
    written = FindJobsConfig.from_json(parse_json_bytes(config_path.read_bytes()))
    assert written.roles == ("staff backend", "senior backend")
    assert written.merged_queries == ("staff backend", "senior backend")
    assert written.location == "Denver, CO"
    assert written.remote is True
    assert written.countries == ("US",)
    assert written.visa_sponsorship_required is True
    # Untouched fields keep FindJobsConfig's own defaults on first write.
    assert written.default_assess_cap == 10


def test_write_setup_keeps_other_find_jobs_json_fields_unchanged(
    backend_fixture: ScoutFindJobsBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_discovery_module(monkeypatch, prefs_store={}, results_store={})
    existing = FindJobsConfig(
        roles=("old role",),
        merged_queries=("old role", "extra query"),
        location="old location",
        remote=False,
        published_after="2026-01-01T00:00:00+00:00",
        sources=SourceToggles(exa=False, ats=True, hiringcafe=True),
        default_assess_cap=25,
        default_model_target=ModelTarget.CODEX_CLI,
        countries=(),
        visa_sponsorship_required=False,
    )
    config_path = backend_fixture.target / "find-jobs.json"
    config_path.write_bytes(canonical_json_bytes(existing.to_json()))

    fields = {
        "roles": ("new role",),
        "titles_to_avoid": (),
        "countries": ("DE",),
        "work_mode": "onsite",
        "city": "Berlin",
        "visa_sponsorship_required": True,
        "exclude_companies": (),
        "watch_companies": (),
        "company_stage_size": None,
        "industries_include": (),
        "industries_exclude": (),
        "must_have_stack": (),
        "dealbreaker_stack": (),
        "cadence_days": 14,
        "budget_usd_per_session": 1.0,
    }
    backend_fixture.write_setup(fields)

    written = FindJobsConfig.from_json(parse_json_bytes(config_path.read_bytes()))
    assert written.roles == ("new role",)
    assert written.merged_queries == ("new role",)
    assert written.location == "Berlin"
    assert written.remote is False
    assert written.countries == ("DE",)
    assert written.visa_sponsorship_required is True
    # Everything else preserved verbatim from the pre-existing file.
    assert written.published_after == "2026-01-01T00:00:00+00:00"
    assert written.sources == SourceToggles(exa=False, ats=True, hiringcafe=True)
    assert written.default_assess_cap == 25
    assert written.default_model_target.value == "codex_cli"


def test_prefs_roles_is_union_of_active_profile_titles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """S25 F1-b2 (operator decision, spike Q5): discovery prefs stay ONE

    shared file, but ``roles``/``titles_to_avoid`` are the order-stable,
    de-duplicated UNION of every ACTIVE (non-archived) profile's own
    ``titles``/``titles_to_avoid`` -- never just the profile this save
    touched, and never an archived profile's.
    """

    fixture = build_gig_with_resume(tmp_path, name="setup-union-proof")

    # Trigger the (real, un-mocked) migration BEFORE the fake discovery
    # module is installed below -- `ensure_default_profile` lazily imports
    # `scout_cli`, which imports the REAL `find_jobs.discovery` package at
    # module level; importing it once here caches it in ``sys.modules`` so
    # the later `_install_fake_discovery_module` swap (needed for
    # `write_setup` itself) doesn't shadow a name `scout_cli`'s own
    # top-level import needs (`DiscoveryBudgetExceeded`), which the fake
    # module doesn't define.
    selected_profile(fixture.resolved, home_root=fixture.home_root, target=fixture.target)

    prefs_store: dict[str, _FakeDiscoveryPrefs] = {}
    _install_fake_discovery_module(monkeypatch, prefs_store=prefs_store, results_store={})
    backend = ScoutFindJobsBackend(home_root=fixture.home_root, target=fixture.target)

    # A second, ACTIVE profile with its own distinct titles.
    second_ref = PinnedResume(
        record_id=fixture.resume_record_id, revision_id=fixture.resume_revision_id, content_sha256="sha256:" + "1" * 64
    )
    create_profile(
        fixture.resolved, label="second", titles=("staff platform engineer", "shared title"),
        titles_to_avoid=("recruiter",), queries=("staff platform engineer",), resume_ref=second_ref,
    )
    # A third, ARCHIVED profile -- its titles must be EXCLUDED from the union.
    third_ref = PinnedResume(
        record_id=fixture.resume_record_id, revision_id=fixture.resume_revision_id, content_sha256="sha256:" + "2" * 64
    )
    third = create_profile(
        fixture.resolved, label="third", titles=("excluded archived title",),
        titles_to_avoid=(), queries=("excluded archived title",), resume_ref=third_ref,
    )
    write_profile(fixture.resolved, profile_id=third.profile_id, state="archived")

    # The setup-interview save touches the SELECTED (default/migrated)
    # profile's own titles, which also contain "shared title" -- proving
    # de-duplication, not just concatenation.
    default_profile = selected_profile(fixture.resolved, home_root=fixture.home_root, target=fixture.target)
    assert default_profile is not None

    fields = {
        "roles": ["shared title", "default profile new role"],
        "titles_to_avoid": ["avoid this"],
        "countries": ["US"],
        "work_mode": "remote",
        "city": "Denver, CO",
        "visa_sponsorship_required": False,
        "exclude_companies": [],
        "watch_companies": [],
        "company_stage_size": None,
        "industries_include": [],
        "industries_exclude": [],
        "must_have_stack": [],
        "dealbreaker_stack": [],
        "cadence_days": 7,
        "budget_usd_per_session": 0.50,
    }
    result = backend.write_setup(fields)

    # END outcome: the returned AND saved prefs carry the union, not the
    # raw submitted roles.
    assert set(result["roles"]) == {"shared title", "default profile new role", "staff platform engineer"}
    assert "excluded archived title" not in result["roles"]
    # De-duplicated: "shared title" appears in both the default profile
    # (just saved) and the second profile -- only once in the union.
    assert result["roles"].count("shared title") == 1
    assert set(result["titles_to_avoid"]) == {"avoid this", "recruiter"}

    saved = prefs_store[str(fixture.target)]
    assert set(saved.roles) == set(result["roles"])
    assert set(saved.titles_to_avoid) == set(result["titles_to_avoid"])


def test_start_discovery_runs_synchronously_on_a_background_thread(
    backend_fixture: ScoutFindJobsBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefs_store: dict[str, _FakeDiscoveryPrefs] = {str(backend_fixture.target): _FakeDiscoveryPrefs(roles=("x",))}
    results_store: dict[str, _FakeDiscoveryResult] = {}
    started = threading.Event()
    release = threading.Event()

    def slow_run(*, home_root, target, prefs, on_progress):
        started.set()
        release.wait(timeout=5)
        if on_progress is not None:
            on_progress({"status": "running"})
        return _FakeDiscoveryResult(discovery_id="disc_slow_001")

    _install_fake_discovery_module(
        monkeypatch, prefs_store=prefs_store, results_store=results_store, run_discovery_impl=slow_run
    )

    request_id = backend_fixture.start_discovery(lambda _event: None)
    assert request_id.startswith("discovery_req_")
    assert started.wait(timeout=5)
    assert backend_fixture.discovery_running() is True

    release.set()
    for _ in range(200):
        if not backend_fixture.discovery_running():
            break
        time.sleep(0.01)
    assert backend_fixture.discovery_running() is False
    assert str(backend_fixture.target) in results_store


def test_latest_discovery_mid_run_keeps_the_result_shape_with_progress_nested(
    backend_fixture: ScoutFindJobsBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0-5 repro: ``run_discovery``'s ``on_progress`` emits small, raw
    progress-step events (e.g. ``{"stage": "discovery_start", ...}`` --
    see ``discovery/__init__.py``'s own ``_progress`` calls), not anything
    shaped like ``DiscoveryResult``. ``GET /api/discover/latest`` mid-run
    must still carry the contract fields (``status``, ``cost_usd``,
    ``sources``, ``new_boards``) instead of just whatever the raw event
    happened to contain -- otherwise the Discover panel renders "Cost
    $undefined" mid-run.
    """

    prefs_store: dict[str, _FakeDiscoveryPrefs] = {str(backend_fixture.target): _FakeDiscoveryPrefs(roles=("x",))}
    results_store: dict[str, _FakeDiscoveryResult] = {}
    progress_seen = threading.Event()
    release = threading.Event()

    def slow_run(*, home_root, target, prefs, on_progress):
        # Mirrors discovery/__init__.py's real _progress({"stage": ...})
        # shape exactly -- no "status"/"cost_usd"/"sources" keys at all.
        if on_progress is not None:
            on_progress({"stage": "discovery_start", "discovery_id": "disc_midrun_001", "runs": 3})
        progress_seen.set()
        release.wait(timeout=5)
        return _FakeDiscoveryResult(discovery_id="disc_midrun_001")

    _install_fake_discovery_module(
        monkeypatch, prefs_store=prefs_store, results_store=results_store, run_discovery_impl=slow_run
    )

    backend_fixture.start_discovery(lambda _event: None)
    assert progress_seen.wait(timeout=5)

    snapshot = backend_fixture.latest_discovery()
    assert snapshot is not None
    assert snapshot["status"] == "running"
    assert snapshot["cost_usd"] == 0.0
    assert snapshot["sources"] == []
    assert snapshot["new_boards"] == []
    assert snapshot["skipped"] == {}
    # The raw progress event is still observable, just nested rather than
    # overwriting the snapshot.
    assert snapshot["progress"]["stage"] == "discovery_start"

    release.set()
    for _ in range(200):
        if not backend_fixture.discovery_running():
            break
        time.sleep(0.01)


def test_start_discovery_refuses_a_second_run_while_one_is_in_progress(
    backend_fixture: ScoutFindJobsBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefs_store: dict[str, _FakeDiscoveryPrefs] = {str(backend_fixture.target): _FakeDiscoveryPrefs(roles=("x",))}
    release = threading.Event()

    def slow_run(*, home_root, target, prefs, on_progress):
        release.wait(timeout=5)
        return _FakeDiscoveryResult(discovery_id="disc_002")

    _install_fake_discovery_module(
        monkeypatch, prefs_store=prefs_store, results_store={}, run_discovery_impl=slow_run
    )

    backend_fixture.start_discovery(lambda _event: None)
    for _ in range(200):
        if backend_fixture.discovery_running():
            break
        time.sleep(0.01)

    with pytest.raises(DiscoveryConflictError):
        backend_fixture.start_discovery(lambda _event: None)

    release.set()
    for _ in range(200):
        if not backend_fixture.discovery_running():
            break
        time.sleep(0.01)


def test_start_discovery_raises_when_no_prefs_saved(
    backend_fixture: ScoutFindJobsBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_discovery_module(monkeypatch, prefs_store={}, results_store={})
    with pytest.raises(SetupPrefsMissingError):
        backend_fixture.start_discovery(lambda _event: None)
    # The lock must be released on this pre-run failure so a subsequent
    # call is not wrongly refused as "already running".
    assert backend_fixture.discovery_running() is False


def test_latest_discovery_returns_none_when_nothing_has_run(
    backend_fixture: ScoutFindJobsBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_discovery_module(monkeypatch, prefs_store={}, results_store={})
    assert backend_fixture.latest_discovery() is None


def test_latest_discovery_returns_sealed_result_after_a_run_completes(
    backend_fixture: ScoutFindJobsBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefs_store: dict[str, _FakeDiscoveryPrefs] = {str(backend_fixture.target): _FakeDiscoveryPrefs(roles=("x",))}
    results_store: dict[str, _FakeDiscoveryResult] = {}
    _install_fake_discovery_module(monkeypatch, prefs_store=prefs_store, results_store=results_store)

    backend_fixture.start_discovery(lambda _event: None)
    for _ in range(200):
        if not backend_fixture.discovery_running():
            break
        time.sleep(0.01)

    result = backend_fixture.latest_discovery()
    assert result is not None
    assert result["discovery_id"] == "disc_generated_001"


def test_read_setup_503s_when_discovery_module_is_unimportable(
    backend_fixture: ScoutFindJobsBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate the module being absent (a partial install, or S2-A not
    # landed in some other checkout) by making the lazy-import seam itself
    # raise -- CHANGE #3's 503 degrade must hold regardless of whether the
    # real package happens to exist on disk in *this* checkout.
    def _raise_module_not_found() -> None:
        raise DiscoveryUnavailableError("the discovery module is not available yet")

    monkeypatch.setattr(ScoutFindJobsBackend, "_discovery_module", staticmethod(_raise_module_not_found))
    with pytest.raises(DiscoveryUnavailableError):
        backend_fixture.read_setup()
