"""Find and read the current pinned resume for a bound Scout project.

S25 F1-b2: resolves the SELECTED profile's ``resume_ref`` (or an explicitly
named ``profile_id``'s, when the caller already resolved one -- see
``prep.py``'s own ``--profile`` threading), never "newest" -- this used to
be an independent second implementation of "pick the newest resume
reference" (its own prior docstring said so explicitly: "There is no
'current' pointer kept"), which is now the profile record's own
``resume_ref`` field. Reuses ``run.resolve_profile_resume`` (the same
pinned-content reader ``present_api.py``'s ``resume_details()`` uses) rather
than re-deriving a second reader of ``private_records.read_record``.

Privacy: this module returns resume bytes to the caller (``prep.py``), which
must send them only to the configured assess-equivalent model call, never to
the web-search request -- enforced in ``prep.py``/``websearch.py``, not here.
"""

from __future__ import annotations

from pathlib import Path

from ...workpad import resolve_workpad
from ..interview_prep.types import ResumeIdentity


class ResumeUnavailableError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _has_real_find_jobs_config(target: Path) -> bool:
    """True iff ``find-jobs.json`` exists and is NOT still the starter placeholder.

    F1-b2-r1: the exact same "is this real or still the starter?" check
    ``profile_records.ensure_default_profile`` makes before migrating (its
    own comment: "Never migrate while the file still IS the starter
    placeholder, verbatim") -- reused here (never re-derived) so this
    module's error distinction stays byte-for-byte consistent with what
    actually gates a profile's existence. Degrades to ``False`` (never
    raises) on any read/parse failure -- an unreadable or corrupt config is
    "not set up," the same conclusion an absent file reaches.
    """

    from ...canonical import parse_json_bytes
    from ..find_jobs.contracts import FindJobsConfig
    from ..scout_cli import STARTER_FIND_JOBS_CONFIG

    config_path = target / "find-jobs.json"
    if config_path.is_symlink() or not config_path.is_file():
        return False
    try:
        config = FindJobsConfig.from_json(parse_json_bytes(config_path.read_bytes()))
    except Exception:
        return False
    return not (
        config.roles == STARTER_FIND_JOBS_CONFIG.roles
        and config.merged_queries == STARTER_FIND_JOBS_CONFIG.merged_queries
    )


def current_resume(
    *, home_root: Path, requested_target: Path | None, gig_id: str | None, profile_id: str | None = None
) -> tuple[ResumeIdentity, bytes]:
    """Return the current profile's resume identity and bytes, or raise if none is set.

    ``profile_id=None`` (the default) resolves the gig's SELECTED profile
    (migrating a default profile on first read, same as every other F1-b2
    profile-aware reader); an explicit ``profile_id`` reads that committed
    profile directly instead, refusing one that isn't committed in this gig.
    """

    from ... import private_records
    from ... import run
    from ...canonical import digest_imported_bytes
    from ...journal import read_committed_artifact
    from .. import profile_records

    resolved = resolve_workpad(
        home_root=home_root, requested_target=requested_target, gig_id=gig_id, allow_semantic_state=True
    )
    # ``requested_target`` may be ``None`` (resolved via a cwd binding);
    # every profile-aware call below needs a concrete path -- use the
    # workpad's own resolved target root, the same concrete value every
    # other F1-b2 profile reader (``server.py``'s ``_target_root()``) uses.
    target = resolved.target_root

    if profile_id is None:
        try:
            profile = profile_records.selected_profile(
                resolved, home_root=home_root, target=target
            )
        except profile_records.ProfileRecordError as exc:
            raise ResumeUnavailableError("resume_unavailable", "the selected profile is unavailable") from exc
        if profile is None:
            # F1-b2-r1: ``selected_profile``/``ensure_default_profile`` return
            # ``None`` for THREE different reasons (no ``find-jobs.json`` at
            # all, ``find-jobs.json`` still the starter placeholder, or a
            # real config but no committed resume yet) -- collapsing all
            # three to "resume_missing" is a misleading error for the first
            # two: the user HAS a resume; what's actually missing is Scout
            # setup (no profile can exist until `find-jobs.json` is real).
            # Distinguish here: only a REAL (non-starter) config with no
            # resume is genuinely "resume_missing"; anything else means
            # setup itself is incomplete.
            if not _has_real_find_jobs_config(target):
                raise ResumeUnavailableError(
                    "find_jobs_config_missing",
                    "no find-jobs.json is set up for this project; run the Scout setup interview first",
                )
            raise ResumeUnavailableError(
                "resume_missing", "no resume is set for this project; run `gigai scout resume add <file>`"
            )
    else:
        profiles = {item.profile_id: item for item in profile_records.list_profiles(resolved)}
        profile = profiles.get(profile_id)
        if profile is None:
            raise ResumeUnavailableError("profile_unavailable", f"profile {profile_id!r} is not committed in this gig")

    try:
        pinned = run.resolve_profile_resume(
            resolved,
            profile.resume_ref.record_id,
            profile.resume_ref.revision_id,
            home_root=home_root,
            target=target,
        )
    except run.RunError as exc:
        raise ResumeUnavailableError("resume_unavailable", "the profile's pinned resume is unavailable") from exc

    # The profile's resume_ref pins the g45_reference RECORD (record_id,
    # revision_id) -- the underlying imported reference_id (what
    # references/<reference_id>/source.txt is keyed by) lives inside that
    # record revision's own content, exactly like
    # `profile_records._resolve_newest_resume_for_gig`'s own record<->
    # reference link. ``list_revisions`` (not ``read_record``, whose
    # ``content=False`` shape strips the ``content`` key entirely) is the
    # one that returns the revision's own embedded content reference.
    try:
        revisions = private_records.list_revisions(resolved=resolved, record_id=profile.resume_ref.record_id)
        revision = next(
            (item for item in revisions if item.get("revision_id") == profile.resume_ref.revision_id), None
        )
        content = revision.get("content") if revision is not None else None
        reference_id = content.get("reference_id") if isinstance(content, dict) else None
    except Exception as exc:
        raise ResumeUnavailableError("resume_unavailable", "resume record is unavailable") from exc
    if not isinstance(reference_id, str):
        raise ResumeUnavailableError("resume_unavailable", "resume record has no reference_id")

    source_path = f"references/{reference_id}/source.txt"
    try:
        data, _commit = read_committed_artifact(
            workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=source_path,
        )
    except Exception as exc:
        raise ResumeUnavailableError("resume_unavailable", "resume content is not committed authority") from exc
    if digest_imported_bytes(data) != pinned.content_sha256:
        raise ResumeUnavailableError("resume_digest_mismatch", "resume content changed since it was imported")
    return ResumeIdentity(reference_id=reference_id, content_sha256=pinned.content_sha256), data


__all__ = ["ResumeUnavailableError", "current_resume"]
