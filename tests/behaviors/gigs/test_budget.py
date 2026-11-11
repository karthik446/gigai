"""P12: ``[[keys]] alias`` + ``spend_cap`` -> a per-gig key and ``ctx.budget``; ``status --json`` shows ``budget``/``keys``.

Every key here is a synthetic sentinel; the only provider is the in-memory loopback fake (no network, no paid call).
The eval fixture (``tests/fixtures/gigs/eval-fixture``) is "installed" the way test_loader.py does it.
"""

from __future__ import annotations

import json
import logging
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gigai import secrets_store
from gigai.config import CredentialReference
from gigai.credentials import CredentialReferenceError, CredentialUnavailableError, alias_env_name, resolve_reference_value
from gigai.model_execution import InvocationBudget, ModelExecutionError
from gigai.scout.find_jobs import background_settings
from gigai.scout.find_jobs.discovery.prefs import DiscoveryPrefs, DiscoveryPrefsError, session_budget_usd
from gigai.sdk.budget import BudgetExhausted, LoopbackKeyProvider, SpendFileError, status_fields
from gigai.sdk.context import GigContext
from tests.behaviors.gigs.test_loader import _add_fixture, _run

SECRET = "sk-or-sentinel-NEVER-PRINTED-7f3a"
SHARED = "sk-or-shared-sentinel-2b19"
REFERENCE = CredentialReference(name="openrouter", kind="environment", reference="OPENROUTER_API_KEY")
CAPPED = {
    "gig": {"name": "capped"},
    "keys": [
        {
            "service": "openrouter",
            "required": False,
            "alias": "capped-gig",
            "spend_cap": {"usd_per_day": 1.0, "usd_per_run": 0.25, "calls_per_day": 3},
        }
    ],
}


def _context(home: Path, name: str, manifest: dict) -> GigContext:
    return GigContext(home, name, manifest, logging.getLogger(f"gigai.gig.{name}"))


def _clear_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENROUTER_API_KEY", "OPENROUTER_API_KEY_SCOUT", "OPENROUTER_API_KEY_CAPPED_GIG", "OPENROUTER_API_KEY_EVAL_FIXTURE"):
        monkeypatch.delenv(name, raising=False)


def test_invocation_budget_keeps_its_behaviour_and_gains_an_optional_usd_dimension() -> None:
    ledger = InvocationBudget(max_model_calls=3, max_tokens=12000)
    assert (ledger.max_usd, ledger.usd) == (None, 0.0)
    assert [ledger.reserve(1000) for _ in range(4)] == [True, True, True, False]
    assert (ledger.model_calls, ledger.tokens, ledger.usd) == (3, 3000, 0.0)
    with pytest.raises(ModelExecutionError, match="positive output tokens"):
        ledger.reserve(0)

    priced = InvocationBudget(max_model_calls=10, max_tokens=10**6, max_usd=0.5)
    assert priced.reserve(100, usd=0.3) and not priced.reserve(100, usd=0.3)
    assert (priced.model_calls, priced.usd) == (1, 0.3)
    assert priced.reserve(100, usd=0.2) and priced.usd == 0.5
    with pytest.raises(ModelExecutionError, match="non-negative"):
        InvocationBudget(max_model_calls=1, max_tokens=1, max_usd=-0.1)
    with pytest.raises(ModelExecutionError, match="non-negative usd"):
        priced.reserve(1, usd=-1.0)


def test_alias_env_name_is_the_shared_variable_suffixed_with_the_alias() -> None:
    assert alias_env_name("OPENROUTER_API_KEY", "scout") == "OPENROUTER_API_KEY_SCOUT"
    assert alias_env_name("OPENROUTER_API_KEY", "eval-fixture") == "OPENROUTER_API_KEY_EVAL_FIXTURE"
    with pytest.raises(ValueError, match="key aliases"):
        alias_env_name("OPENROUTER_API_KEY", "Not Valid")


def test_alias_key_beats_the_shared_key_and_an_absent_alias_falls_back_to_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    home = tmp_path / "home"
    with pytest.raises(CredentialUnavailableError):
        resolve_reference_value(REFERENCE, home_root=home, alias="scout")

    monkeypatch.setenv("OPENROUTER_API_KEY", SHARED)
    # An alias without its own key: the shared key, exactly as a call without an alias.
    assert resolve_reference_value(REFERENCE, home_root=home, alias="scout") == SHARED
    assert resolve_reference_value(REFERENCE, home_root=home) == SHARED

    monkeypatch.setenv("OPENROUTER_API_KEY_SCOUT", SECRET)
    assert resolve_reference_value(REFERENCE, home_root=home, alias="scout") == SECRET
    assert resolve_reference_value(REFERENCE, home_root=home) == SHARED  # no alias asked: the shared key, unchanged

    # The alias key in the secrets store wins over the shared key in the environment.
    monkeypatch.delenv("OPENROUTER_API_KEY_SCOUT")
    secrets_store.set("OPENROUTER_API_KEY_SCOUT", SECRET, home_root=home)
    assert resolve_reference_value(REFERENCE, home_root=home, alias="scout") == SECRET
    assert resolve_reference_value(REFERENCE, home_root=home, alias="other") == SHARED


def test_exhausted_key_is_refused_with_the_providers_message_and_no_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    provider = LoopbackKeyProvider()
    key = provider.create("capped-gig", limit_usd=0.10)
    monkeypatch.setenv("OPENROUTER_API_KEY_CAPPED_GIG", key)
    ctx = _context(tmp_path / "home", "capped", CAPPED)

    assert ctx.budget is ctx.budget  # one ledger per context
    budget = ctx.budget.with_provider(provider)
    assert budget.check().usd == 0.25 and provider.usage_calls == 1

    provider.spend(key, 0.10)
    with pytest.raises(BudgetExhausted) as refused:
        budget.check()
    assert str(refused.value) == "Key limit exceeded" and provider.usage_calls == 2
    with pytest.raises(BudgetExhausted, match="Key limit exceeded"):
        budget.reserve(0.01)
    assert provider.usage_calls == 3, "one provider call per refusal; nothing retried"
    assert budget.spent().calls == 0 and not (tmp_path / "home" / "gigs" / "capped" / "spend.json").exists()

    provider.limit(key, limit_usd=1.0)
    assert budget.check().usd == 0.25

    # F5: a provider attached but no key anywhere is a typed refusal naming the service, never a bare credential error.
    monkeypatch.delenv("OPENROUTER_API_KEY_CAPPED_GIG")
    with pytest.raises(BudgetExhausted, match="^capped: no key for openrouter: "):
        budget.check()
    assert provider.usage_calls == 4, "refused before the provider is asked"


RACERS, RACE_CAP = 12, 5
RACE = {"gig": {"name": "race"}, "keys": [{"service": "openrouter", "required": False, "spend_cap": {"calls_per_day": RACE_CAP}}]}


def _race_reserve(home: Path, barrier, outcomes) -> None:
    """One racer: a fresh process-local budget reserving once, released together with the others."""

    budget = _context(home, "race", RACE).budget
    barrier.wait(timeout=30)
    try:
        budget.reserve(0.0)
    except BudgetExhausted as refused:
        outcomes.put(("refused", str(refused)))
    except Exception as crashed:  # a lost update or a torn write shows up as a crash, not a hang
        outcomes.put(("crashed", repr(crashed)))
    else:
        outcomes.put(("ok", ""))


def test_concurrent_reservations_never_pass_the_cap_together(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F1: 12 processes reserve at once against calls_per_day = 5; exactly 5 pass and the file says 5."""

    import multiprocessing

    _clear_keys(monkeypatch)
    home = tmp_path / "home"
    context = multiprocessing.get_context("spawn")
    barrier, outcomes = context.Barrier(RACERS), context.Queue()
    racers = [context.Process(target=_race_reserve, args=(home, barrier, outcomes)) for _ in range(RACERS)]
    for racer in racers:
        racer.start()
    results = [outcomes.get(timeout=60) for _ in racers]
    for racer in racers:
        racer.join(timeout=30)
    assert sorted(result[0] for result in results).count("ok") == RACE_CAP
    assert {message for kind, message in results if kind == "refused"} == {f"race: calls_per_day {RACE_CAP} reached for {datetime.now(UTC).strftime('%Y-%m-%d')}"}
    assert json.loads((home / "gigs" / "race" / "spend.json").read_text(encoding="utf-8"))["calls"] == RACE_CAP
    assert _context(home, "race", RACE).budget.remaining().calls == 0


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("{not json", "cannot read"),
        ("[]", "not a JSON object"),
        ("", "cannot read"),
        ('{"day": "TODAY", "usd": "abc", "calls": 1}', "usd is not a number"),
        ('{"day": "TODAY", "usd": 0.1, "calls": true}', "calls is not a number"),
        ('{"day": "TODAY", "usd": -5, "calls": 1}', "usd is negative"),
        ('{"day": "TODAY", "usd": NaN, "calls": 1}', "usd is not finite"),
        ('{"day": "TODAY", "usd": 0.1, "calls": 1.5}', "calls is not a whole number"),
    ],
)
def test_a_spend_file_that_is_not_todays_numbers_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, reason: str) -> None:
    """F3: corrupt, negative or non-numeric spend.json refuses check/reserve/charge naming the file; status reports it."""

    _clear_keys(monkeypatch)
    home = tmp_path / "home"
    budget = _context(home, "capped", CAPPED).budget
    budget.charge(0.01)
    path = home / "gigs" / "capped" / "spend.json"
    path.write_text(text.replace("TODAY", datetime.now(UTC).strftime("%Y-%m-%d")), encoding="utf-8")

    for refuse in (budget.spent, budget.remaining, budget.check, lambda: budget.reserve(0.0), lambda: budget.charge(0.0)):
        with pytest.raises(SpendFileError) as refused:
            refuse()
        assert isinstance(refused.value, BudgetExhausted) and refused.value.code == "spend_file_unreadable"
        assert str(refused.value).startswith(f"capped: cannot read {path}: ") and reason in str(refused.value)
        assert str(refused.value).endswith("; fix or remove the file")
    assert path.read_text(encoding="utf-8") == text.replace("TODAY", datetime.now(UTC).strftime("%Y-%m-%d")), "never rewritten or reset"

    status = budget.status()
    assert status["spent"] is None and status["remaining"] is None and reason in status["error"]
    assert status["window"]["timezone"] == "UTC" and status["caps"]["calls_per_day"] == 3
    fields = status_fields(home, "capped", CAPPED)
    assert fields["budget"]["error"] == status["error"] and fields["keys"][0]["alias"] == "capped-gig"

    path.unlink()
    assert budget.status()["error"] is None and budget.check().calls == 3


def test_an_alias_the_schema_accepts_but_no_variable_can_carry_is_reported_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F4: `alias = "Bad Alias"` passes the manifest schema; status reports the row with an error, check refuses."""

    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", SHARED)
    manifest = {"gig": {"name": "odd"}, "keys": [{"service": "openrouter", "required": False, "alias": "Bad Alias"}]}
    fields = status_fields(tmp_path, "odd", manifest)
    row = fields["keys"][0]
    assert row["alias"] == "Bad Alias" and row["alias_env"] is None and row["present"] is True
    assert row["error"] == "key aliases must use lowercase letters, digits, '_' or '-'"
    assert fields["budget"]["key"] == {"service": "openrouter", "alias": "Bad Alias"} and fields["budget"]["error"] is None

    budget = _context(tmp_path, "odd", manifest).budget
    assert budget.check().usd is None  # no provider, no caps: nothing to refuse
    with pytest.raises(BudgetExhausted, match="alias 'Bad Alias' is not usable"):
        budget.with_provider(LoopbackKeyProvider()).check()
    with pytest.raises(CredentialReferenceError):
        budget.resolver(REFERENCE)  # never silently the shared key
    assert SHARED not in json.dumps(fields)


def test_caps_charges_and_the_utc_day_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    home = tmp_path / "home"
    budget = _context(home, "capped", CAPPED).budget
    assert (budget.remaining().usd, budget.remaining().calls) == (0.25, 3)
    assert budget.key is not None and budget.key.alias_env == "OPENROUTER_API_KEY_CAPPED_GIG"

    assert budget.charge(0.1).usd == pytest.approx(0.1)
    left = budget.remaining()
    assert left.usd == pytest.approx(0.15) and left.calls == 2  # the run cap (0.25) binds before the day cap (1.0)
    with pytest.raises(BudgetExhausted, match=r"\$0.2000 does not fit the remaining \$0.1500"):
        budget.reserve(0.2)
    assert budget.reserve(0.1).calls == 1 and budget.run.model_calls == 2

    path = home / "gigs" / "capped" / "spend.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    assert json.loads(path.read_text(encoding="utf-8")) == {"schema_version": "gig-spend:1", "day": today, "usd": pytest.approx(0.2), "calls": 2}

    # Another process (a fresh context): today's spend is shared, the run ledger is its own.
    later = _context(home, "capped", CAPPED).budget
    assert later.spent().calls == 2 and later.run.model_calls == 0 and later.remaining().usd == 0.25
    later.charge(0.0)
    with pytest.raises(BudgetExhausted, match=f"calls_per_day 3 reached for {today}"):
        later.check()
    status = later.status()
    assert status["remaining"] == {"usd": 0.25, "calls": 0} and status["spent"] == {"usd": pytest.approx(0.2), "calls": 3}
    assert status["window"] == {"day": today, "timezone": "UTC", "resets_at": f"{today}T00:00:00Z".replace(today, _tomorrow(today))}
    assert status["key"] == {"service": "openrouter", "alias": "capped-gig"}

    # Yesterday's file counts as nothing today.
    path.write_text(json.dumps({"schema_version": "gig-spend:1", "day": "2000-01-01", "usd": 9.0, "calls": 9}), encoding="utf-8")
    assert later.spent().calls == 0 and later.remaining().calls == 3
    assert status["window"] == {"day": today, "timezone": "UTC", "resets_at": f"{_tomorrow(today)}T00:00:00Z"}

def _tomorrow(day: str) -> str:
    from datetime import timedelta

    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def test_the_secret_never_appears_in_status_errors_or_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _clear_keys(monkeypatch)
    home = tmp_path / "home"
    monkeypatch.setenv("OPENROUTER_API_KEY_CAPPED_GIG", SECRET)
    secrets_store.set("OPENROUTER_API_KEY", SHARED, home_root=home)
    provider = LoopbackKeyProvider()  # never saw SECRET: every usage call fails with the provider's words
    ctx = _context(home, "capped", CAPPED)
    with caplog.at_level(logging.DEBUG):
        budget = ctx.budget.with_provider(provider)
        with pytest.raises(BudgetExhausted) as refused:
            budget.check()
        fields = status_fields(home, "capped", CAPPED)
    texts = [str(refused.value), repr(refused.value), json.dumps(fields), repr(budget), caplog.text]
    assert all(SECRET not in text and SHARED not in text for text in texts)
    assert fields["keys"] == [
        {
            "service": "openrouter",
            "env": "OPENROUTER_API_KEY",
            "alias": "capped-gig",
            "alias_env": "OPENROUTER_API_KEY_CAPPED_GIG",
            "required": False,
            "present": True,
            "spend_cap": {"usd_per_day": 1.0, "usd_per_run": 0.25, "calls_per_day": 3},
            "error": None,
        }
    ]
    assert fields["budget"]["caps"] == {"usd_per_day": 1.0, "usd_per_run": 0.25, "calls_per_day": 3}


def test_status_fields_are_null_and_empty_without_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    manifest = {"gig": {"name": "plain"}}
    assert status_fields(tmp_path, "plain", manifest) == {"budget": None, "keys": []}
    budget = _context(tmp_path, "plain", manifest).budget
    assert budget.key is None and budget.remaining().usd is None and budget.remaining().calls is None
    assert budget.check().usd is None  # uncapped: never refuses
    assert budget.resolver.keywords == {"home_root": tmp_path, "alias": None}

    # Keys without a spend_cap (Scout's rows): a budget object with no caps, present by name only.
    shared = {"gig": {"name": "shared"}, "keys": [{"service": "exa", "required": False}, {"service": "unknown-service"}]}
    fields = status_fields(tmp_path, "shared", shared)
    assert fields["budget"]["caps"] == {"usd_per_day": None, "usd_per_run": None, "calls_per_day": None}
    assert fields["budget"]["key"] == {"service": "exa", "alias": None}
    assert [(row["env"], row["alias_env"], row["present"], row["spend_cap"]) for row in fields["keys"]] == [
        ("EXA_API_KEY", None, False, None),
        (None, None, False, None),
    ]


def test_status_json_shows_budget_and_keys_for_the_fixture_and_for_scout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    site = tmp_path / "site"
    _add_fixture(site, "eval-fixture")
    home = tmp_path / ".gigai"

    done = _run(tmp_path, site, "eval-fixture", "status", "--json", env_extra={"GIGAI_HOME": str(home)})
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    assert payload["gig"] == "eval-fixture" and "step_overrides" in payload
    assert payload["keys"][0]["alias"] == "eval-fixture" and payload["keys"][0]["present"] is False
    assert payload["budget"]["caps"] == {"usd_per_day": 1.0, "usd_per_run": 0.25, "calls_per_day": 20}
    assert payload["budget"]["remaining"] == {"usd": 0.25, "calls": 20} and payload["budget"]["window"]["timezone"] == "UTC"

    # Scout's own `status` needs a set-up home (`gigai setup`, non-interactive, synthetic endpoint, no model call).
    setup = _run(
        tmp_path, site, "setup", "--non-interactive", "--home", str(home), "--workpad-root", str(tmp_path / "workpads"),
        "--editor", "/usr/bin/true", "--credential-ref", "provider=environment:GIGAI_PROVIDER_TOKEN",
        "--endpoint", "remote=openai_api:provider:https://api.example.test", "--model-target", "remote=remote:smoke-test",
        "--create-model-target", "remote", "--json",
    )
    assert setup.returncode == 0, setup.stderr
    done = _run(tmp_path, site, "scout", "status", "--home", str(home), "--json", env_extra={"GIGAI_HOME": str(home)})
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    assert payload["ok"] is True and payload["state"] == "stopped"
    assert [(row["service"], row["alias"], row["required"]) for row in payload["keys"]] == [
        ("openrouter", "scout", False), ("openai", None, False), ("exa", None, False)
    ]
    assert payload["budget"]["caps"] == {"usd_per_day": None, "usd_per_run": None, "calls_per_day": None}
    assert payload["budget"]["key"] == {"service": "openrouter", "alias": "scout"}


def test_scout_caps_keep_their_validation_and_read_the_gig_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    with pytest.raises(DiscoveryPrefsError, match="^budget_usd_per_session must be positive$") as rejected:
        DiscoveryPrefs(budget_usd_per_session=0)
    assert rejected.value.code == "invalid_value"
    with pytest.raises(DiscoveryPrefsError, match="budget_usd_per_session must be a positive number"):
        DiscoveryPrefs.from_json({"schema_version": "scout-discovery-prefs:1", "budget_usd_per_session": -1})

    prefs = DiscoveryPrefs()
    assert prefs.budget_usd_per_session == 0.50 and session_budget_usd(prefs, None) == 0.50
    uncapped = _context(tmp_path, "plain", {"gig": {"name": "plain"}}).budget
    assert session_budget_usd(prefs, uncapped) == 0.50
    capped = _context(tmp_path, "capped", CAPPED).budget
    assert session_budget_usd(prefs, capped) == 0.25
    capped.charge(0.2)
    assert session_budget_usd(prefs, capped) == pytest.approx(0.05)

    assert background_settings.MAX_CAP == 1000 and background_settings.max_cap() == 1000
    assert background_settings.max_cap(uncapped) == 1000 and background_settings.max_cap(capped) == 3
    patch = background_settings.validate_patch({"rank": {"max_calls_per_day": 1000}})
    assert patch == {"rank": {"max_calls_per_day": 1000}}
    with pytest.raises(background_settings.SettingsError, match="rank.max_calls_per_day must be 0 to 1000"):
        background_settings.validate_patch({"rank": {"max_calls_per_day": 1001}})
    assert background_settings.validate_patch({"pipeline": {"max_model_calls_per_day": 3}}, budget=capped) == {"pipeline": {"max_model_calls_per_day": 3}}
    with pytest.raises(background_settings.SettingsError, match="pipeline.max_model_calls_per_day must be 0 to 3"):
        background_settings.validate_patch({"pipeline": {"max_model_calls_per_day": 4}}, budget=capped)
    assert background_settings.validate_patch({"pipeline": {"label_min_ats": 100}}, budget=capped) == {"pipeline": {"label_min_ats": 100}}
