"""S25 F1-b: overlay the selected profile onto the shared find-jobs.json.

DESIGN DECISION (coordinator, F1-b1 dispatch, resolving the digest-guard
snag): the config the UI sees IS the effective config -- ``GET /api/config``
returns the shared ``find-jobs.json`` with the SELECTED profile's
``titles``/``queries`` overlaid onto ``roles``/``merged_queries``; every
other ``FindJobsConfig`` field stays exactly the shared file's value. This
keeps ``run.launch_find_jobs_run``'s own ``config.digest() ==
run_request.config_digest`` guard literally unchanged (it still checks the
exact bytes it seals) while making a profile switch between "load the form"
and "click Run" a genuine, detectable staleness (the effective digest
changes with the profile, exactly as it already does for a shared-field
edit).

ONE builder function, used by ``ScoutFindJobsBackend.read_config`` (the
``GET /api/config`` / run-request-digest path) and ``.start_run`` (rebuilt
fresh at launch time, never trusting the client's bytes) -- see
``server.py``'s own module docstring for why those two methods (plus
``_update_find_jobs_config``) are this packet's seam, not the whole file.

Acquire/ATS/Exa/``_role_match`` are untouched: they keep reading
``config.roles``/``config.merged_queries`` exactly as before, F1-a; this
module is what makes those values the profile's, not the shared file's.

CORE STAYS PROFILE-UNAWARE: this module lives under ``scout/find_jobs/`` and
imports ``..profile_records`` (a sibling Scout module) -- never imported
back by ``gigai.run`` or any other core module.
"""

from __future__ import annotations

from dataclasses import replace

from ..profile_records import ProfileRecord
from .contracts import FindJobsConfig


def overlay_selected_profile(config: FindJobsConfig, profile: ProfileRecord | None) -> FindJobsConfig:
    """Return ``config`` with ``roles``/``merged_queries`` replaced by ``profile``'s.

    ``profile is None`` (no profile could be resolved -- e.g. no committed
    resume yet for this gig, so ``ensure_default_profile`` no-ops) returns
    ``config`` unchanged: the pre-F1-b shared-roles behaviour, never a
    crash. Every field besides ``roles``/``merged_queries`` is always the
    shared file's own value -- ``titles_to_avoid`` is NOT a ``FindJobsConfig``
    field at all (it only feeds the shared discovery prefs' union, per the
    S25 spike's Q5 decision) and is never part of this overlay.

    Q1 (v0.1.9): ``max_age_days`` / ``published_after`` (the publication
    window) are SHARED fields, never per-profile -- ``dataclasses.replace``
    carries them through untouched, exactly like ``countries``/``sources``;
    ``test_rolling_window.py`` pins that so a future field-by-field rewrite
    of this overlay can't silently drop the window.
    """

    if profile is None:
        return config
    return replace(config, roles=profile.titles, merged_queries=profile.queries)


__all__ = ["overlay_selected_profile"]
