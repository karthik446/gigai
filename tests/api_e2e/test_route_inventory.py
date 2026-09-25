"""test-gap-001: every route in present_api.py must have an e2e journey.

Mirrors ``test_run_dir_writer_inventory.py``'s shape: a hand-maintained
registry (``JOURNEYS`` below) is checked against an AST scan of the real
source (``route_inventory.discover_routes``) so drift is a test failure, not
a silent gap. A route landing in ``present_api.py`` without a line added
here fails this test with the route's name, telling the author to add a
journey (or an explicit, justified exclusion) for it.

Each F1-F4 packet that adds or changes a route must add its entry here in
the same packet (the plan's "Rules for the F packets" rule).
"""

from __future__ import annotations

from tests.api_e2e.route_inventory import Route, discover_routes

# Every route present_api.py serves today, mapped to the journey test(s)
# that exercise it end to end over HTTP. A route with no real journey yet
# (because building one needs a seam this packet had to ask about first) is
# listed with its blocking reason instead of a test name -- still an entry,
# so the inventory test doesn't silently drop it, but honest about the gap.
JOURNEYS: dict[Route, str] = {
    Route("GET", "/api/health"): "test_setup_config_run_poll_results.py (server-start sanity)",
    Route("GET", "/api/config"): "test_setup_config_run_poll_results.py",
    Route("GET", "/api/setup"): "test_setup_config_run_poll_results.py (404 prefs_missing path)",
    Route("PUT", "/api/setup"): "test_discover_fake_provider.py",
    Route("POST", "/api/run"): "test_setup_config_run_poll_results.py, test_second_run_unchanged_skip.py, test_failed_run_then_next_run_assesses.py",
    Route("GET", "/api/runs/{run_id}"): "test_setup_config_run_poll_results.py, test_failed_run_then_next_run_assesses.py",
    Route("GET", "/api/runs/{run_id}/progress"): "test_setup_config_run_poll_results.py, test_failed_run_then_next_run_assesses.py",
    Route("GET", "/api/runs/{run_id}/results"): "test_setup_config_run_poll_results.py, test_second_run_unchanged_skip.py",
    Route("POST", "/api/discover"): "test_discover_fake_provider.py",
    Route("GET", "/api/discover/latest"): "test_discover_fake_provider.py",
    Route("GET", "/api/profiles"): "test_profiles_journey.py",
    Route("POST", "/api/profiles"): "test_profiles_journey.py",
    Route("PUT", "/api/profiles/{profile_id}"): "test_profiles_journey.py",
    Route("POST", "/api/profiles/{profile_id}/archive"): "test_profiles_journey.py",
    Route("POST", "/api/profiles/selection"): "test_profiles_journey.py",
    Route("POST", "/api/runs/{run_id}/rank"): "test_rank_journey.py",
}


def test_every_route_in_present_api_has_a_journey() -> None:
    discovered = discover_routes()
    known = frozenset(JOURNEYS)
    missing = discovered - known
    if missing:
        rendered = "\n".join(f"  - {route.method} {route.path}" for route in sorted(missing))
        raise AssertionError(
            "present_api.py serves a route with no entry in this suite's "
            "JOURNEYS registry (tests/api_e2e/test_route_inventory.py) -- "
            "add a journey (or a justified blocking-seam entry) for it:\n"
            f"{rendered}"
        )
    # The reverse direction (a registry entry for a route that no longer
    # exists) is a maintenance smell, not a released-API risk, but worth
    # catching so the registry never silently drifts stale either.
    stale = known - discovered
    assert not stale, f"JOURNEYS has entries for routes present_api.py no longer serves: {sorted(stale)}"


def test_scanner_finds_the_known_routes() -> None:
    """Not a vacuous pass: the scanner must actually find every route this
    packet's own survey (the ticket's 'Today' section) already knows
    exists, so a scan that silently finds nothing can't hide behind a
    trivially-empty JOURNEYS registry passing the test above."""

    discovered = discover_routes()
    for expected in (
        Route("GET", "/api/health"),
        Route("GET", "/api/config"),
        Route("GET", "/api/setup"),
        Route("PUT", "/api/setup"),
        Route("POST", "/api/run"),
        Route("GET", "/api/runs/{run_id}"),
        Route("GET", "/api/runs/{run_id}/progress"),
        Route("GET", "/api/runs/{run_id}/results"),
        Route("POST", "/api/discover"),
        Route("GET", "/api/discover/latest"),
        Route("GET", "/api/profiles"),
        Route("POST", "/api/profiles"),
        Route("PUT", "/api/profiles/{profile_id}"),
        Route("POST", "/api/profiles/{profile_id}/archive"),
        Route("POST", "/api/profiles/selection"),
    ):
        assert expected in discovered, f"scanner failed to find {expected} -- found: {sorted(discovered)}"


def test_removing_a_journey_entry_fails_the_inventory_test() -> None:
    """Proves the inventory test is load-bearing: with one real route's
    entry removed from a copy of the registry (simulating a route landing
    without its journey), the missing-route assertion must fire. Done
    in-process against a tampered copy, deterministically, rather than by
    editing this file on disk -- mirrors
    ``test_run_dir_writer_inventory.py``'s own
    ``test_removing_logs_from_the_exclude_list_fails_the_inventory_test``.
    """

    tampered = dict(JOURNEYS)
    removed = tampered.pop(Route("GET", "/api/health"))
    assert removed, "test setup assumption broken: /api/health had no entry to remove"

    discovered = discover_routes()
    missing = discovered - frozenset(tampered)
    assert Route("GET", "/api/health") in missing, (
        "removing /api/health's journey entry should have made it "
        f"'missing'; missing was: {sorted(missing)}"
    )
