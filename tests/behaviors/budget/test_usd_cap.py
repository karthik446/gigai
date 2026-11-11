"""BK3: a per-click USD cap refuses a call whose worst case is over it, before any adapter call.

The estimate comes from a price table (``<home>/prices.json``; here the synthetic ``tests/fixtures/budget/prices.json``,
never OpenRouter's live list): ``ceil(chars / 4)`` input tokens at the prompt price plus ``max_output_tokens`` at the
completion price. ``fake/model-1`` is priced so a 4000-character prompt with ``max_output_tokens = 1000`` is exactly
$0.06: a cap of 0.05 refuses it (``estimated $0.06 > cap $0.05``), a cap of 0.06 lets it through. OpenRouter is a
loopback HTTPS fake that reports ``usage.cost``; ``claude`` is a shell script; no network beyond 127.0.0.1.
"""

from __future__ import annotations

import importlib
import json
import logging
import socket
import sqlite3
import ssl
import stat
import subprocess
import sys
import threading
import tomllib
import urllib.request
import warnings
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from gigai.config import CredentialReference, Endpoint, ModelTarget, Profile, load_config
from gigai.evals import run as eval_run
from gigai.evals.records import evals_db_path, open_evals_db
from gigai.sdk import GigContext
from gigai.sdk.budget import (
    BudgetExhausted,
    ClickCapExceeded,
    GigBudget,
    PriceTable,
    PriceUnknownError,
    StalePricesWarning,
    fetch_prices,
)
from gigai.setup import build_config, run_setup
from gigai.steps import StepTargetError, StepsFileError, caps_path, listing, resolve_target, set_override, stale_overrides, step_verbs, steps_path
from tests.behaviors.gigs.test_loader import FIXTURES

GIG = "eval-fixture"
PACKAGE = FIXTURES / GIG / "gig_eval_fixture"
MANIFEST = tomllib.loads((PACKAGE / "gig.toml").read_text(encoding="utf-8"))
PRICES = Path(__file__).resolve().parents[2] / "fixtures" / "budget" / "prices.json"
KEY_ENV = "BK3_FAKE_OPENROUTER_KEY"
FAKE_KEY = "sk-or-v1-bk3-fake-key-do-not-use-0000"
PROMPT = "x" * 4000  # 1000 input tokens at 4 characters each
REPORTED_COST = 0.01


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any socket connect to a non-loopback address fails the test."""

    connect = socket.socket.connect

    def guarded(self: socket.socket, address: object) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            assert address[0] in ("127.0.0.1", "::1"), f"network call to {address!r}"  # type: ignore[index]
        return connect(self, address)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded)


class _OpenRouter:
    """A loopback HTTPS stand-in for OpenRouter: chat completions report ``usage.cost``; ``/models`` lists prices."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.cert, key = directory / "cert.pem", directory / "key.pem"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(self.cert), "-days", "1",
             "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
            check=True,
            capture_output=True,
        )
        self.calls = 0  # adapter calls that reached the fake: the mutation-resistance counter
        self.models_requests = 0
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server's name
                self.rfile.read(int(self.headers["Content-Length"]))
                provider.calls += 1
                self._reply({
                    "model": "fake/model-1",
                    "choices": [{"message": {"role": "assistant", "content": "openrouter fake says 4"}}],
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 4, "total_tokens": 1004, "cost": REPORTED_COST},
                })

            def do_GET(self) -> None:  # noqa: N802 - http.server's name
                provider.models_requests += 1
                self._reply({"data": [
                    {"id": "fake/model-1", "pricing": {"prompt": "0.00001", "completion": "0.00005"}},
                    {"id": "fake/model-2", "pricing": {"prompt": "0.000002", "completion": "0.000008"}},
                    {"id": "free/model", "pricing": {"prompt": "0", "completion": "0"}},
                    {"id": "broken/model", "pricing": {"prompt": "n/a"}},
                    {"name": "no id"},
                ]})

            def _reply(self, payload: dict[str, object]) -> None:
                encoded = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args: object) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, key)
        self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self.base_url = f"https://127.0.0.1:{self._server.server_address[1]}/api/v1"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _make_home(home: Path, openrouter_url: str, *, max_output_tokens: int = 1000) -> None:
    """A scratch home: deterministic targets, a claude CLI target (``sonnet``) and an OpenRouter target (``fake/model-1``)."""

    run_setup(
        build_config(
            home_root=home,
            workpad_root=home / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
            credentials=(CredentialReference("openrouter", "environment", KEY_ENV),),
            endpoints=(
                Endpoint("offline", "deterministic"),
                Endpoint("claude", "claude_cli"),
                Endpoint("openrouter", "openrouter_api", credential="openrouter", base_url=openrouter_url),
            ),
            model_targets=(
                ModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),
                ModelTarget("deterministic-a", "offline", "fixture-a", ("text",), 64),
                ModelTarget("deterministic-b", "offline", "fixture-b", ("text",), 64),
                ModelTarget("claude-cli", "claude", "sonnet", ("text",), max_output_tokens),
                ModelTarget("openrouter:fake", "openrouter", "fake/model-1", ("text",), max_output_tokens),
                ModelTarget("openrouter:unpriced", "openrouter", "fake/unpriced", ("text",), max_output_tokens),
            ),
            profiles=(Profile("default", planner="deterministic-a", critic="deterministic-b", adjudicator="offline-default"),),
        )
    )


def _place_prices(home: Path, *, age: timedelta = timedelta(0)) -> Path:
    """The fixture table in ``home``, fetched ``age`` ago (so the 7-day staleness rule is tested against now)."""

    payload = json.loads(PRICES.read_text(encoding="utf-8"))
    payload["fetched_at"] = (datetime.now(UTC) - age).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = home / "prices.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _fake_claude(directory: Path, *, cost: float | None) -> Path:
    """``directory/claude``: answers the probe, records each call's argv; reports usage and ``total_cost_usd`` only
    when ``cost`` is given (without them the adapter's ``cost_status`` is ``unavailable``)."""

    directory.mkdir(parents=True, exist_ok=True)
    record = directory / "claude.argv"
    reply: dict[str, object] = {"type": "result", "subtype": "success", "result": "claude fake says 4"}
    if cost is not None:
        reply.update(usage={"input_tokens": 1000, "output_tokens": 2}, total_cost_usd=cost)
    executable = directory / "claude"
    executable.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--help" ]; then echo "--setting-sources --strict-mcp-config --tools"; exit 0; fi\n'
        f"for arg in \"$@\"; do printf '%s\\0' \"$arg\"; done >> '{record}'\n"
        "/bin/cat > /dev/null\n"
        f"printf '%s' '{json.dumps(reply)}'\n",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return record


@pytest.fixture
def provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_OpenRouter]:
    fake = _OpenRouter(tmp_path / "tls")
    monkeypatch.setenv(KEY_ENV, FAKE_KEY)
    monkeypatch.setenv("SSL_CERT_FILE", str(fake.cert))
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    try:
        yield fake
    finally:
        fake.close()


@pytest.fixture
def home(tmp_path: Path, provider: _OpenRouter) -> Path:
    _make_home(tmp_path / "home", provider.base_url)
    _place_prices(tmp_path / "home")
    return tmp_path / "home"


@pytest.fixture
def ctx(home: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[GigContext]:
    """The fixture gig's context (``[[keys]]`` openrouter alias ``eval-fixture``, usd_per_day 1.0, usd_per_run 0.25)."""

    monkeypatch.syspath_prepend(str(FIXTURES / GIG))
    importlib.import_module("gig_eval_fixture")
    yield GigContext(home, GIG, MANIFEST, logging.getLogger("gigai.gig.eval-fixture"))
    sys.modules.pop("gig_eval_fixture", None)


def _traces(home: Path) -> list[dict[str, object]]:
    connection = sqlite3.connect(evals_db_path(GIG, home))
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute("SELECT * FROM traces ORDER BY recorded_at")]
    finally:
        connection.close()


def _spend(home: Path) -> dict[str, object] | None:
    path = home / "gigs" / GIG / "spend.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# --- the estimate --------------------------------------------------------------------------------------------------


def test_the_estimate_is_input_chars_over_four_times_prompt_price_plus_max_output_times_completion_price(home: Path) -> None:
    budget = GigBudget.for_gig(home, GIG, MANIFEST)
    target = SimpleNamespace(model="fake/model-1", max_output_tokens=1000)

    assert budget.estimate(SimpleNamespace(prompt=PROMPT), target) == 0.06  # 1000 * 0.00001 + 1000 * 0.00005
    assert budget.estimate(SimpleNamespace(prompt="x" * 4001), target) == 0.06001  # 1001 input tokens: rounded up
    # The request's own max_output_tokens, its system text and prior turns count too.
    request = SimpleNamespace(prompt="x" * 4000, system="y" * 400, messages=({"role": "user", "content": "z" * 400},), max_output_tokens=10)
    assert budget.estimate(request, target) == round(1200 * 0.00001 + 10 * 0.00005, 6)
    # Nothing is guessed: no max_output_tokens, or a model the table lacks, is a typed refusal naming what is missing.
    with pytest.raises(PriceUnknownError, match="max_output_tokens"):
        budget.estimate(SimpleNamespace(prompt=PROMPT), SimpleNamespace(model="fake/model-1", max_output_tokens=None))
    with pytest.raises(PriceUnknownError, match=r"no price for model 'fake/unpriced' in .*prices\.json"):
        budget.estimate(SimpleNamespace(prompt=PROMPT), SimpleNamespace(model="fake/unpriced", max_output_tokens=1))


def test_reserve_refuses_over_the_cap_with_two_decimals_and_lets_an_equal_estimate_through(home: Path) -> None:
    budget = GigBudget.for_gig(home, GIG, MANIFEST)
    with pytest.raises(ClickCapExceeded) as refused:
        budget.reserve(0.06, cap_usd=0.05)
    assert str(refused.value) == "estimated $0.06 > cap $0.05"
    assert isinstance(refused.value, BudgetExhausted) and refused.value.code == "cap_usd_exceeded"
    assert _spend(home) is None, "a refusal charges nothing"

    assert budget.reserve(0.06, cap_usd=0.06).usd == pytest.approx(0.25 - 0.06)  # usd_per_run is the tighter cap
    assert budget.settle(0.06, 0.01).usd == 0.01, "the ledger is kept to the microdollar: no float residue"
    assert budget.run.usd == 0.01
    assert budget.settle(0.0, None).usd == 0.01, "an unreported cost keeps the estimate"
    assert budget.charge(0.1).usd == 0.11 and budget.run.usd == 0.11


def test_a_stale_price_table_warns_once_and_is_still_used(home: Path) -> None:
    _place_prices(home, age=timedelta(days=8))
    budget = GigBudget.for_gig(home, GIG, MANIFEST)
    target = SimpleNamespace(model="fake/model-1", max_output_tokens=1000)

    with pytest.warns(StalePricesWarning, match="8 days ago") as caught:
        assert budget.estimate(SimpleNamespace(prompt=PROMPT), target) == 0.06
    assert [w.filename for w in caught] == [__file__], "the warning is attributed to estimate's caller, not to budget.py"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert budget.estimate(SimpleNamespace(prompt=PROMPT), target) == 0.06

    _place_prices(home, age=timedelta(days=6))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert GigBudget.for_gig(home, GIG, MANIFEST).estimate(SimpleNamespace(prompt=PROMPT), target) == 0.06


def test_fetch_prices_writes_the_table_from_a_models_list(tmp_path: Path, provider: _OpenRouter) -> None:
    home = tmp_path / "fetched"
    home.mkdir()
    before = datetime.now(UTC).replace(microsecond=0)

    # urllib's default context does not read SSL_CERT_FILE on every platform (Linux CI); trust the fake's cert explicitly
    context = ssl.create_default_context(cafile=str(provider.cert))
    opener = partial(urllib.request.urlopen, context=context)

    table = fetch_prices(home, url=f"{provider.base_url}/models", opener=opener)

    assert provider.models_requests == 1
    assert (table.price("fake/model-1").prompt, table.price("fake/model-1").completion) == (0.00001, 0.00005)
    assert table.price("free/model").completion == 0.0 and "broken/model" not in table.prices
    written = json.loads((home / "prices.json").read_text(encoding="utf-8"))
    assert written["schema_version"] == "gig-prices:1" and written["source"] == f"{provider.base_url}/models"
    assert set(written["prices"]) == {"fake/model-1", "fake/model-2", "free/model"}
    reloaded = PriceTable.load(home / "prices.json")
    assert reloaded.fetched_at >= before and not reloaded.stale()
    with pytest.raises(PriceUnknownError, match="could not fetch prices"):
        fetch_prices(home, url="https://127.0.0.1:9/api/v1/models")


@pytest.mark.parametrize("content", ['{"schema_version": "other"}', "not json", '{"schema_version": "gig-prices:1", "fetched_at": "2026-01-01T00:00:00Z", "prices": {"m": {"prompt": "1"}}}'])
def test_a_table_that_is_not_a_price_table_refuses_naming_the_file(tmp_path: Path, content: str) -> None:
    path = tmp_path / "prices.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(PriceUnknownError, match=r"prices\.json is not a price table"):
        PriceTable.load(path)
    with pytest.raises(PriceUnknownError, match="no price table at"):
        PriceTable.load(tmp_path / "missing.json")


# --- ctx.model ------------------------------------------------------------------------------------------------------


def test_a_call_over_the_cap_is_refused_before_the_adapter_is_called(ctx: GigContext, home: Path, provider: _OpenRouter) -> None:
    set_override(home, GIG, MANIFEST, "answer", "openrouter:fake")
    open_evals_db(GIG, home).close()

    with pytest.raises(BudgetExhausted) as refused:
        ctx.model("answer", PROMPT, cap_usd=0.05)

    assert str(refused.value) == "estimated $0.06 > cap $0.05"
    assert provider.calls == 0, "refused before any adapter call"
    assert _traces(home) == [] and _spend(home) is None


def test_an_estimate_equal_to_the_cap_passes_and_the_trace_carries_the_reported_cost(ctx: GigContext, home: Path, provider: _OpenRouter) -> None:
    set_override(home, GIG, MANIFEST, "answer", "openrouter:fake")
    open_evals_db(GIG, home).close()

    result = ctx.model("answer", PROMPT, cap_usd=0.06)

    assert result.output_text == "openrouter fake says 4" and provider.calls == 1
    (row,) = _traces(home)
    assert row["cost_usd"] == REPORTED_COST
    usage = json.loads(str(row["usage"]))
    assert (usage["cost_status"], usage["estimate_usd"], usage["input_tokens"]) == ("provider_reported", 0.06, 1000)
    spend = _spend(home)
    assert spend is not None and (spend["usd"], spend["calls"]) == (pytest.approx(REPORTED_COST), 1), "settled to the reported cost"
    assert ctx.budget.run.usd == pytest.approx(REPORTED_COST) and ctx.budget.run.model_calls == 1
    for column, value in row.items():
        assert FAKE_KEY not in str(value), f"key in traces.{column}"


def test_the_cap_comes_from_the_call_then_steps_toml_then_the_manifest(ctx: GigContext, home: Path, provider: _OpenRouter) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["steps"]["answer"]["cap_usd"] = 0.05
    gig = GigContext(home, GIG, manifest, ctx.log)
    set_override(home, GIG, manifest, "answer", "openrouter:fake")
    config = load_config(home)

    assert resolve_target(home, GIG, manifest, "answer", config).cap_usd == 0.05
    with pytest.raises(BudgetExhausted, match=r"^estimated \$0\.06 > cap \$0\.05$"):
        gig.model("answer", PROMPT)
    # caps.toml wins over the manifest; the call argument wins over both (None lifts the cap for one call).
    assert set_override(home, GIG, manifest, "answer", cap_usd="0.01") == f"answer: cap_usd 0.01 (written to {caps_path(home, GIG)})"
    assert resolve_target(home, GIG, manifest, "answer", config).cap_usd == 0.01
    assert tomllib.loads(caps_path(home, GIG).read_text(encoding="utf-8")) == {"caps": {"answer": 0.01}}
    assert _parses_under_0_2_0(steps_path(home, GIG)) == {"answer": "openrouter:fake"}, "steps.toml stays [steps]-only"
    with pytest.raises(BudgetExhausted, match=r"cap \$0\.01"):
        gig.model("answer", PROMPT)
    assert provider.calls == 0
    gig.model("answer", PROMPT, cap_usd=0.07)
    gig.model("answer", PROMPT, cap_usd=None)
    assert provider.calls == 2
    assert set_override(home, GIG, manifest, "answer", cap_usd=None) == "answer: cap removed"
    assert set_override(home, GIG, manifest, "answer", cap_usd=None) == "answer: no cap to remove"
    assert resolve_target(home, GIG, manifest, "answer", config).cap_usd == 0.05


def test_steps_set_cap_usd_shows_in_steps_json(ctx: GigContext, home: Path) -> None:
    runner, steps = CliRunner(), step_verbs({})["steps"]

    done = runner.invoke(steps, ["set", "judge", "--cap-usd", "0.05"], obj=ctx)
    assert done.exit_code == 0, done.output
    shown = runner.invoke(steps, ["--json"], obj=ctx)
    assert shown.exit_code == 0 and json.loads(shown.output)["judge"]["cap_usd"] == 0.05
    assert json.loads(shown.output)["answer"]["cap_usd"] is None
    assert "judge: deterministic-b (deterministic fixture-b, manifest), cap_usd 0.05" in runner.invoke(steps, [], obj=ctx).output

    both = runner.invoke(steps, ["set", "judge", "deterministic-a", "--cap-usd", "0.10"], obj=ctx)
    assert both.exit_code == 0 and "judge -> deterministic-a" in both.output and "cap_usd 0.1" in both.output
    assert listing(ctx)["judge"]["cap_usd"] == 0.1 and listing(ctx)["judge"]["source"] == "override"
    assert runner.invoke(steps, ["set", "judge"], obj=ctx).exit_code == 2  # nothing to set is a usage error
    for bad in ("-1", "abc", "nan", "inf"):
        refused = runner.invoke(steps, ["set", "judge", "--cap-usd", bad], obj=ctx)
        assert refused.exit_code != 0 and "cap_usd is a non-negative USD amount" in refused.output, (bad, refused.output)
    assert listing(ctx)["judge"]["cap_usd"] == 0.1, "a refused cap changes nothing"
    assert runner.invoke(steps, ["set", "judge", "-", "--cap-usd", "-"], obj=ctx).output.strip() == "judge: override removed; judge: cap removed"
    assert runner.invoke(steps, ["set", "nosuch", "--cap-usd", "1"], obj=ctx).exit_code != 0


def _parses_under_0_2_0(path: Path) -> dict[str, str]:
    """0.2.0's ``read_overrides`` rule (steps.py:119 there), applied verbatim: anything but one ``[steps]`` table of
    non-empty strings is refused, so a rollback after ``steps set --cap-usd`` must still read the file."""

    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    table = payload.get("steps", {})
    if set(payload) - {"steps"} or not isinstance(table, dict) or any(type(v) is not str or not v for v in table.values()):
        raise AssertionError(f'{path} must hold only a [steps] table of step = "<model target>" lines')
    return table


def test_steps_set_cap_usd_never_touches_steps_toml_so_a_rollback_to_0_2_0_still_reads_it(ctx: GigContext, home: Path) -> None:
    assert set_override(home, GIG, MANIFEST, "judge", cap_usd=0.05) == f"judge: cap_usd 0.05 (written to {caps_path(home, GIG)})"
    assert not steps_path(home, GIG).exists(), "a cap alone writes no steps.toml"
    set_override(home, GIG, MANIFEST, "judge", "deterministic-a")
    before = steps_path(home, GIG).read_bytes()
    assert before == b'# gigai eval-fixture steps set: per-step model target overrides\n[steps]\n"judge" = "deterministic-a"\n'
    set_override(home, GIG, MANIFEST, "judge", cap_usd=0.02)
    set_override(home, GIG, MANIFEST, "answer", cap_usd=1)
    set_override(home, GIG, MANIFEST, "judge", cap_usd=None)
    assert steps_path(home, GIG).read_bytes() == before and _parses_under_0_2_0(steps_path(home, GIG)) == {"judge": "deterministic-a"}
    assert tomllib.loads(caps_path(home, GIG).read_text(encoding="utf-8")) == {"caps": {"answer": 1.0}}
    assert stat.S_IMODE(caps_path(home, GIG).stat().st_mode) == 0o600
    assert sorted(p.name for p in steps_path(home, GIG).parent.iterdir() if p.suffix == ".toml") == ["caps.toml", "steps.toml"]


def test_a_bad_cap_in_caps_toml_or_the_manifest_is_refused(ctx: GigContext, home: Path) -> None:
    path = caps_path(home, GIG)
    path.parent.mkdir(parents=True)
    for content in ('[caps]\nanswer = "0.05"\n', "[caps]\nanswer = -1\n", "[caps]\nanswer = true\n", "[caps]\nanswer = inf\n", "[steps]\nanswer = 1\n"):
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)
        with pytest.raises(StepsFileError, match=r"caps\.toml must hold only a \[caps\] table of step = <usd> lines"):
            ctx.model("answer", "2+2")
    path.write_text("[caps]\nnosuch = 1\n", encoding="utf-8")
    path.chmod(0o664)
    with pytest.raises(StepsFileError, match="writable by group or others"):
        ctx.model("answer", "2+2")
    path.chmod(0o600)
    assert stale_overrides(home, GIG, MANIFEST) == [f"caps.toml caps step 'nosuch', which the manifest's [steps] lacks; run `gigai {GIG} steps set nosuch --cap-usd -`"]
    path.unlink()

    manifest = json.loads(json.dumps(MANIFEST))
    manifest["steps"]["answer"]["cap_usd"] = -0.5
    with pytest.raises(StepTargetError, match=r"\[steps\]\.answer\.cap_usd.*non-negative"):
        GigContext(home, GIG, manifest, ctx.log).model("answer", "2+2")
    with pytest.raises(ValueError, match="cap_usd"):
        ctx.model("answer", "2+2", cap_usd="lots")


def test_a_model_the_table_lacks_is_refused_before_the_call(ctx: GigContext, home: Path, provider: _OpenRouter) -> None:
    set_override(home, GIG, MANIFEST, "answer", "openrouter:unpriced")
    with pytest.raises(PriceUnknownError, match=r"no price for model 'fake/unpriced' in .*prices\.json"):
        ctx.model("answer", PROMPT, cap_usd=1.0)
    assert provider.calls == 0 and _spend(home) is None


def test_a_deterministic_target_is_free_and_an_uncapped_keyed_call_is_metered_at_its_reported_cost(ctx: GigContext, home: Path, provider: _OpenRouter) -> None:
    open_evals_db(GIG, home).close()
    (home / "prices.json").unlink()  # nothing to estimate: deterministic targets are never priced

    ctx.model("answer", "2+2", cap_usd=0.0)
    assert _spend(home) is None and json.loads(str(_traces(home)[0]["usage"]))["cost_status"] == "unavailable"
    assert _traces(home)[0]["cost_usd"] is None and "estimate_usd" not in json.loads(str(_traces(home)[0]["usage"]))

    # The gig declares a key with usd caps, so an uncapped paid call is still estimated against them and settled.
    _place_prices(home)
    set_override(home, GIG, MANIFEST, "judge", "openrouter:fake")
    ctx.model("judge", PROMPT)
    assert provider.calls == 1
    spend = _spend(home)
    assert spend is not None and (spend["usd"], spend["calls"]) == (pytest.approx(REPORTED_COST), 1)


def test_a_gig_without_keys_or_caps_writes_no_ledger(ctx: GigContext, home: Path, provider: _OpenRouter) -> None:
    manifest = {key: value for key, value in MANIFEST.items() if key != "keys"}
    gig = GigContext(home, GIG, manifest, ctx.log)
    set_override(home, GIG, manifest, "answer", "openrouter:fake")

    gig.model("answer", PROMPT)
    assert provider.calls == 1 and _spend(home) is None
    with pytest.raises(BudgetExhausted, match=r"^estimated \$0\.06 > cap \$0\.05$"):
        gig.model("answer", PROMPT, cap_usd=0.05)
    assert provider.calls == 1


def test_a_cli_target_that_reports_no_cost_keeps_the_estimate_and_a_null_cost(ctx: GigContext, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = _fake_claude(tmp_path / "bin", cost=None)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    set_override(home, GIG, MANIFEST, "answer", "claude-cli")
    open_evals_db(GIG, home).close()
    expected = round(1000 * 0.000003 + 1000 * 0.000015, 6)  # sonnet: $0.018

    with pytest.raises(BudgetExhausted, match=r"^estimated \$0\.02 > cap \$0\.01$"):
        ctx.model("answer", PROMPT, cap_usd=0.01)
    assert not record.exists()
    ctx.model("answer", PROMPT, cap_usd=0.02)

    (row,) = _traces(home)
    usage = json.loads(str(row["usage"]))
    assert row["cost_usd"] is None and (usage["cost_status"], usage["estimate_usd"]) == ("unavailable", expected)
    spend = _spend(home)
    assert spend is not None and spend["usd"] == pytest.approx(expected), "no reported cost: the estimate stays on the ledger"


def test_the_per_run_cap_still_applies_under_a_per_click_cap(ctx: GigContext, home: Path, provider: _OpenRouter) -> None:
    set_override(home, GIG, MANIFEST, "answer", "openrouter:fake")
    (home / "gigs" / GIG).mkdir(parents=True, exist_ok=True)
    (home / "gigs" / GIG / "spend.json").write_text(json.dumps({"schema_version": "gig-spend:1", "day": datetime.now(UTC).strftime("%Y-%m-%d"), "usd": 0.97, "calls": 3}), encoding="utf-8")

    with pytest.raises(BudgetExhausted, match=r"does not fit the remaining \$0\.0300"):
        ctx.model("answer", PROMPT, cap_usd=0.06)
    assert provider.calls == 0


# --- eval run -------------------------------------------------------------------------------------------------------


def test_eval_run_refuses_a_capped_step_before_any_call_and_before_loading_the_suite(ctx: GigContext, home: Path, provider: _OpenRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eval_run, "_package_dir", lambda name: PACKAGE if name == GIG else None)
    set_override(home, GIG, MANIFEST, "answer", "openrouter:fake", cap_usd="0.00")
    open_evals_db(GIG, home).close()

    with pytest.raises(eval_run.RunRefused) as refused:
        eval_run.execute(ctx, "answer", "release", max_calls=1)

    assert str(refused.value) == "estimated $0.05 > cap $0.00"  # the output floor alone: 1000 * 0.00005
    assert provider.calls == 0
    connection = sqlite3.connect(evals_db_path(GIG, home))
    try:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0, "no run row for a refusal"
    finally:
        connection.close()
    # A prompt over the cap at call time is the same refusal (exit 2 through ``guard``), not a failed call: the floor
    # ($0.05) fits a cap of $0.05002, the shortest rendered prompt's input tokens ($0.00004) do not.
    set_override(home, GIG, MANIFEST, "solve", "openrouter:fake", cap_usd="0.05002")
    with pytest.raises(eval_run.RunRefused, match=r"^estimated \$0\.0500[4-9]\d > cap \$0\.050020$"):
        eval_run.execute(ctx, "solve", "release", max_calls=1)
    assert provider.calls == 0
