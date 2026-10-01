"""S25 F1-b: overlay the selected profile onto the shared find-jobs.json.

DESIGN DECISION (coordinator, F1-b1 dispatch, resolving the digest-guard
snag): the config the UI sees IS the effective config -- ``GET /api/config``
returns the shared ``find-jobs.json`` with the SELECTED profile's
``titles``/``queries`` overlaid onto ``roles``/``merged_queries``; every
other ``FindJobsConfig`` field stays exactly the shared file's value (since
0110-022: unless the profile has its own search settings, see
``overlay_selected_profile``). This
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
from pathlib import Path

from ..profile_records import ProfileRecord, ProfileSearchSettings
from .contracts import FindJobsConfig, WorkModePreference


def overlay_selected_profile(config: FindJobsConfig, profile: ProfileRecord | None) -> FindJobsConfig:
    """Return ``config`` with ``roles``/``merged_queries`` replaced by ``profile``'s.

    ``profile is None`` (no profile could be resolved -- e.g. no committed
    resume yet for this gig, so ``ensure_default_profile`` no-ops) returns
    ``config`` unchanged: the pre-F1-b shared-roles behaviour, never a
    crash. ``titles_to_avoid`` is NOT a ``FindJobsConfig`` field at all (it
    only feeds the shared discovery prefs' union, per the S25 spike's Q5
    decision) and is never part of this overlay.

    0110-022: a profile with its OWN ``search_settings`` (never the default
    profile) also replaces ``location``, ``work_mode`` (and ``remote``, kept
    in step: ``true`` only for Remote-only), ``countries`` and the posted
    window. Its ``max_age_days`` becomes the rolling window and clears the
    shared fixed ``published_after`` (a fixed date would otherwise win,
    ``filters.published_cutoff``); ``None`` keeps the shared window as it
    is. A profile without them (the default one, and every record written
    before 0110-022) leaves all of these the shared file's own values, so
    its effective config and digest are what they always were. Every other
    field (``sources``, ``visa_sponsorship_required``, the assess cap, the
    model target) is always the shared file's.
    """

    if profile is None:
        return config
    config = replace(config, roles=profile.titles, merged_queries=profile.queries)
    own = profile.search_settings
    if own is None:
        return config
    work_mode = WorkModePreference(own.work_mode)
    config = replace(
        config,
        location=own.location,
        work_mode=work_mode,
        remote=work_mode is WorkModePreference.REMOTE,
        countries=tuple(own.countries),
    )
    if own.max_age_days is not None:
        config = replace(config, max_age_days=own.max_age_days, published_after=None)
    return config


def saved_work_mode(*, home_root: Path, target: Path) -> WorkModePreference | None:
    """uat-bug-028: the work mode the setup interview saved (``discovery/prefs.json``).

    That file has always kept the real four-way answer, while an older
    ``find-jobs.json`` only has ``remote`` (``true`` for Any as well as
    Remote-only). ``None`` when nothing was saved or it cannot be read.
    """

    try:
        from .discovery import load_prefs

        prefs = load_prefs(home_root=Path(home_root), target=Path(target))
        value = getattr(prefs, "work_mode", None)
        return WorkModePreference(value) if value else None
    except Exception:  # noqa: BLE001 - no saved answer is the Any default, never a failed read
        return None


def with_saved_work_mode(config: FindJobsConfig, work_mode: WorkModePreference | None) -> FindJobsConfig:
    """``config`` with the saved ``work_mode`` when the file has none of its own.

    A ``work_mode`` in ``find-jobs.json`` always wins; with neither, the
    config stays Any (``FindJobsConfig.effective_work_mode``). Applied by
    ``read_config`` and ``start_run`` alike, so the digest the UI saw is the
    one the run seals.
    """

    if config.work_mode is not None or work_mode is None:
        return config
    return replace(config, work_mode=work_mode)


def default_search_settings(*, home_root: Path, target: Path) -> ProfileSearchSettings | None:
    """0110-022: the default profile's search settings, as a run for it would read them.

    The shared ``find-jobs.json`` (its work mode filled from the setup's
    saved one, like ``read_config``). What a new profile is prefilled with
    and what "same as default" shows. ``None`` when there is no readable
    config yet.
    """

    from ...canonical import parse_json_bytes

    path = Path(target) / "find-jobs.json"
    try:
        if path.is_symlink() or not path.is_file():
            return None
        config = FindJobsConfig.from_json(parse_json_bytes(path.read_bytes()))
    except Exception:  # noqa: BLE001 - no readable config is "nothing to prefill", never a failed profile read
        return None
    config = with_saved_work_mode(config, saved_work_mode(home_root=home_root, target=target))
    return ProfileSearchSettings.from_config(config)


__all__ = ["default_search_settings", "overlay_selected_profile", "saved_work_mode", "with_saved_work_mode"]
