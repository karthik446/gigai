"""Resolve a quick-assessment resume input and preference defaults (P4).

Profile path (``profile_id`` given, or neither field): the committed scout
profile's ``resume_ref`` is read through ``proposal_execution.read_pinned_resume``,
the same digest-verifying reader the find-jobs assess node uses.  Ephemeral
path (``resume_text`` given): the text is used for this call only.  It is
never imported into the journal, never written as a private record, and never
serialized by ``ResolvedResume`` (S30 Q2 recommendation (a)).

Preferences: ``visa_sponsorship_required`` and ``countries`` default from the
target's ``find-jobs.json`` read tolerantly (missing, unreadable or starter
file -> ``False`` / ``()``, the same posture as
``proposal_execution._read_sealed_config``); ``titles`` default from the
resolved profile (``()`` for an ephemeral resume).  Every field can be
overridden per call through ``AssessPreferences``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ...canonical import digest_imported_bytes, parse_json_bytes
from .assess_contracts import AssessPreferences, AssessResumeInput, ResolvedResume
from .contracts import FindJobsConfig, FindJobsContractError

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    from ...workpad import ResolvedWorkpad
    from ..profile_records import ProfileRecord


def resolve_profile(
    input: AssessResumeInput, *, resolved: "ResolvedWorkpad", home_root: Path, target: Path
) -> "ProfileRecord | None":
    """The profile ``input`` names (or the gig's selected one); ``None`` iff ephemeral.

    Raises ``FindJobsContractError`` with ``profile_not_found`` for an explicit
    id that is not committed in this gig, and ``profile_unavailable`` when no
    profile is selected (or the selection cannot be read).
    """

    from .. import profile_records

    if input.is_ephemeral:
        return None
    if input.profile_id is not None:
        for record in profile_records.list_profiles(resolved):
            if record.profile_id == input.profile_id:
                return record
        raise FindJobsContractError("profile_not_found", f"profile {input.profile_id!r} is not committed in this gig")
    try:
        selected = profile_records.selected_profile(resolved, home_root=home_root, target=target)
    except profile_records.ProfileRecordError as exc:
        raise FindJobsContractError("profile_unavailable", "the selected profile is unavailable") from exc
    if selected is None:
        raise FindJobsContractError(
            "profile_unavailable",
            "no scout profile is available for this project; finish the Scout setup and add a resume, or pass --resume-text",
        )
    return selected


def resolve_resume(
    input: AssessResumeInput, *, resolved: "ResolvedWorkpad", home_root: Path, target: Path
) -> ResolvedResume:
    """Resolve ``input`` to the resume text plus its identity.

    Raises ``FindJobsContractError``: ``profile_not_found`` / ``profile_unavailable``
    (see :func:`resolve_profile`), ``resume_unavailable`` / ``resume_digest_mismatch``
    when the profile's pinned revision cannot be read back intact, and
    ``resume_input_invalid`` for whitespace-only ephemeral text.
    """

    if input.is_ephemeral:
        assert input.resume_text is not None
        text = input.resume_text.strip()
        if not text:
            raise FindJobsContractError("resume_input_invalid", "resume_text is empty")
        return ResolvedResume(
            profile_id=None,
            pinned=None,
            content_sha256=digest_imported_bytes(text.encode("utf-8")),
            text=text,
        )

    profile = resolve_profile(input, resolved=resolved, home_root=home_root, target=target)
    assert profile is not None
    return resume_for_profile(profile, resolved=resolved, home_root=home_root, target=target)


def resume_for_profile(
    profile: "ProfileRecord", *, resolved: "ResolvedWorkpad", home_root: Path, target: Path
) -> ResolvedResume:
    """Read ``profile``'s pinned resume, re-verifying its digest."""

    from ..proposal_execution import ScoutProposalExecutionError, read_pinned_resume

    try:
        content = read_pinned_resume(home_root, target, resolved.gig_id, profile.resume_ref)
    except ScoutProposalExecutionError as exc:
        raise FindJobsContractError(exc.code, str(exc)) from exc
    except Exception as exc:  # private_records' own errors: the record is gone or unreadable
        raise FindJobsContractError("resume_unavailable", "the profile's pinned resume is unavailable") from exc
    return ResolvedResume(
        profile_id=profile.profile_id,
        pinned=profile.resume_ref,
        content_sha256=profile.resume_ref.content_sha256,
        text=content.decode("utf-8", errors="replace"),
    )


def read_config_preferences(target: Path) -> tuple[bool, tuple[str, ...]]:
    """``(visa_sponsorship_required, countries)`` from ``<target>/find-jobs.json``, tolerantly.

    A missing, unreadable, malformed or starter config yields ``(False, ())``
    -- "no constraint", the same conclusion the assess node reaches for a run
    with no sealed config.
    """

    path = target / "find-jobs.json"
    if path.is_symlink() or not path.is_file():
        return False, ()
    try:
        config = FindJobsConfig.from_json(parse_json_bytes(path.read_bytes()))
    except Exception:
        return False, ()
    return bool(config.visa_sponsorship_required), tuple(config.countries)


def resolve_preferences(
    overrides: AssessPreferences | None, *, target: Path, profile: "ProfileRecord | None"
) -> AssessPreferences:
    """Fill every ``None`` in ``overrides`` from the defaults; never returns a ``None`` field."""

    visa_default, countries_default = read_config_preferences(target)
    titles_default: tuple[str, ...] = () if profile is None else tuple(profile.titles)
    given = overrides if overrides is not None else AssessPreferences()
    return AssessPreferences(
        visa_sponsorship_required=visa_default if given.visa_sponsorship_required is None else given.visa_sponsorship_required,
        titles=titles_default if given.titles is None else tuple(given.titles),
        countries=countries_default if given.countries is None else tuple(given.countries),
        # assess-prompt-v2: the candidate's own location is NOT defaulted here
        # (it stays ``None`` unless the request carried one, so the echoed
        # preferences object is unchanged for existing callers); the config
        # fallback lives in ``quick_assess._config_location``.
        location=given.location,
    )


__all__ = [
    "read_config_preferences",
    "resolve_preferences",
    "resolve_profile",
    "resolve_resume",
    "resume_for_profile",
]
