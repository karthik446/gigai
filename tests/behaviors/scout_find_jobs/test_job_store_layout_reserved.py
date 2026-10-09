"""0.1.11.9 PJ5: ``pathways/`` and ``prep/`` are RESERVED job-keyed store folders for 0.1.11.10.

Only the path helpers exist (``job_pathways_path``, ``job_prep_path``); nothing is read, written, created, or
migrated. This test proves the paths they name, that calling them creates nothing on disk (a tree hash of the
whole home, before and after), and that their digest is the same one every other job store already uses
(``job_quick_assess_path``'s).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from gigai.scout.find_jobs.discovery.storage import project_id
from gigai.scout.job_store_layout import job_digest, job_pathways_path, job_prep_path
from gigai.scout.quick_assess import job_quick_assess_path

from tests.support.scout_profile_fixtures import build_gig_with_resume

JOB = "https://jobs.example.invalid/acme/reserved-store"


def _tree(root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            found[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def test_the_reserved_paths_name_a_job_folder_under_pathways_and_prep(tmp_path: Path) -> None:
    fx = build_gig_with_resume(tmp_path)
    digest = job_digest(JOB)
    project_dir = fx.home_root / "scout" / project_id(fx.home_root, fx.target)

    assert job_pathways_path(fx.home_root, fx.target, JOB) == project_dir / "pathways" / "job" / f"{digest}.json"
    assert job_prep_path(fx.home_root, fx.target, JOB) == project_dir / "prep" / "job" / f"{digest}.json"


def test_the_digest_matches_the_other_job_stores(tmp_path: Path) -> None:
    fx = build_gig_with_resume(tmp_path)

    assert job_pathways_path(fx.home_root, fx.target, JOB).name == job_quick_assess_path(fx.home_root, fx.target, JOB).name
    assert job_prep_path(fx.home_root, fx.target, JOB).name == job_quick_assess_path(fx.home_root, fx.target, JOB).name


def test_calling_the_helpers_creates_nothing_on_disk(tmp_path: Path) -> None:
    fx = build_gig_with_resume(tmp_path)
    before = _tree(tmp_path)

    job_pathways_path(fx.home_root, fx.target, JOB)
    job_prep_path(fx.home_root, fx.target, JOB)
    job_pathways_path(fx.home_root, fx.target, JOB).parent.exists()
    job_prep_path(fx.home_root, fx.target, JOB).parent.exists()

    assert _tree(tmp_path) == before
    assert not (fx.home_root / "scout" / project_id(fx.home_root, fx.target) / "pathways").exists()
    assert not (fx.home_root / "scout" / project_id(fx.home_root, fx.target) / "prep").exists()
