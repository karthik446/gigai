"""uat-bug-023: a resume whose file name has a space or a non-ASCII letter.

``gigai scout resume add "My Resume.md"`` failed with
``private_operation_invalid: operation key is invalid``: the operation key
was built from the file name as it is, and an operation key is
``[A-Za-z0-9._:-]``. ``resume_import.operation_key_name`` now names the file
in the key; the stored label is still the file's own name.

Against a real, journal-authoritative workpad
(``tests.support.scout_profile_fixtures.build_gig_with_resume``) for the
shared function, and the installed CLI surface (``gigai setup`` / ``init`` /
``scout install`` / ``scout resume add``) for the command.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.private_records import list_imports, read_record
from gigai.scout.resume_import import (
    import_resume_bytes,
    import_resume_file,
    operation_key_name,
    reduce_file_name,
    safe_resume_file_name,
)

from tests.behaviors.scout_find_jobs.test_scout_cli import _setup_and_init
from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

READABLE_NAME = "Jordan Rivera résumé (2026).md"
# 0110-046: no name line (an import strips it, and a file name holding the removed name is not kept).
RESUME = "Staff engineer: nine years of Go and Kafka.\nBuilt the billing pipeline.\n".encode("utf-8")
KEY_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-")


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path)


def _file(directory: Path, name: str, data: bytes = RESUME) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(data)
    return path


def _resume_labels(home: Path, target: Path, gig_id: str | None) -> list[str]:
    imports = list_imports(home_root=home, requested_target=target, family="reference", gig_id=gig_id)
    return sorted(str(item["label"]) for item in imports if item.get("kind") == "resume")


# --- the name in the operation key ----------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["resume.md", "Resume.MD", "first-resume.md", "resume-1.md", "cv_2026.final.markdown", "x" * 67 + ".txt"],
)
def test_a_name_an_operation_key_can_carry_is_the_key_name_as_it_is(name: str) -> None:
    """The keys written before uat-bug-023 are still the keys."""

    assert operation_key_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "My Resume.md",
        READABLE_NAME,
        "履歴書.md",
        "a:b.md",
        "x" * 72 + ".md",
        "resume.weird suffix " + "y" * 200,
        " ",
    ],
)
def test_any_other_name_becomes_one_an_operation_key_can_carry(name: str) -> None:
    key_name = operation_key_name(name)
    key = f"scout-resume-add:{key_name}:sha256:{'0' * 64}"

    assert len(key) <= 160
    assert set(key) <= KEY_CHARS
    assert key_name == operation_key_name(name)  # the same name, the same key


def test_the_key_name_is_the_routes_reduction_and_the_names_own_tag() -> None:
    """One reduction: what ``POST /api/resumes`` imports the file under."""

    key_name = operation_key_name(READABLE_NAME)

    assert reduce_file_name(READABLE_NAME) == safe_resume_file_name(READABLE_NAME) == "Jordan-Rivera-r-sum-2026.md"
    assert key_name.startswith("Jordan-Rivera-r-sum-2026.md-")
    tag = key_name.removeprefix("Jordan-Rivera-r-sum-2026.md-")
    assert len(tag) == 12 and set(tag) <= set("0123456789abcdef")


def test_two_names_with_one_reduction_do_not_share_a_key() -> None:
    """The label is sealed with the key: a shared key would be a conflict."""

    names = ["My Resume.md", "My  Resume.md", "My (Resume).md", "My-Resume.md"]

    assert len({reduce_file_name(name) for name in names}) == 1
    assert len({operation_key_name(name) for name in names}) == len(names)


# --- the shared function ---------------------------------------------------------------


def test_a_file_with_spaces_and_a_non_ascii_letter_is_imported(fx: ProfileFixtureGig, tmp_path: Path) -> None:
    source = _file(tmp_path / "in", READABLE_NAME)

    stored = import_resume_file(home_root=fx.home_root, requested_target=fx.target, source=source)

    assert stored.created and stored.reference_created and stored.record_created
    # What is stored: the file's own name as the label, its bytes as they are.
    assert stored.label == READABLE_NAME
    assert READABLE_NAME in _resume_labels(fx.home_root, fx.target, fx.resolved.gig_id)
    record = read_record(
        home_root=fx.home_root,
        requested_target=fx.target,
        record_id=stored.record_id,
        revision_id=stored.revision_id,
        content=True,
        gig_id=fx.resolved.gig_id,
    )
    assert record["content"] == RESUME


def test_the_same_bytes_under_another_name_are_the_same_resume(fx: ProfileFixtureGig, tmp_path: Path) -> None:
    first = import_resume_file(
        home_root=fx.home_root, requested_target=fx.target, source=_file(tmp_path / "a", READABLE_NAME)
    )
    before = _resume_labels(fx.home_root, fx.target, fx.resolved.gig_id)

    repeats = [
        # the same file again
        import_resume_file(
            home_root=fx.home_root, requested_target=fx.target, source=_file(tmp_path / "b", READABLE_NAME)
        ),
        # another readable name
        import_resume_file(
            home_root=fx.home_root, requested_target=fx.target, source=_file(tmp_path / "c", "My Resume.md")
        ),
        # a name that is this one's reduction (it would share a key without the tag)
        import_resume_file(
            home_root=fx.home_root,
            requested_target=fx.target,
            source=_file(tmp_path / "d", "Jordan-Rivera-r-sum-2026.md"),
        ),
        # the wizard's upload of the same file (POST /api/resumes imports it
        # under the reduced name, so under another key)
        import_resume_bytes(home_root=fx.home_root, requested_target=fx.target, data=RESUME, file_name=READABLE_NAME),
    ]

    for repeat in repeats:
        assert not repeat.created
        assert (repeat.reference_id, repeat.record_id, repeat.revision_id) == (
            first.reference_id,
            first.record_id,
            first.revision_id,
        )
        assert repeat.label == READABLE_NAME
    assert _resume_labels(fx.home_root, fx.target, fx.resolved.gig_id) == before


def test_edited_bytes_under_the_same_name_are_a_new_resume(fx: ProfileFixtureGig, tmp_path: Path) -> None:
    first = import_resume_file(
        home_root=fx.home_root, requested_target=fx.target, source=_file(tmp_path / "a", READABLE_NAME)
    )
    edited = import_resume_file(
        home_root=fx.home_root,
        requested_target=fx.target,
        source=_file(tmp_path / "b", READABLE_NAME, RESUME + b"More.\n"),
    )

    assert edited.created
    assert edited.reference_id != first.reference_id and edited.record_id != first.record_id


# --- the command -------------------------------------------------------------------------


def _resume_add(runner: CliRunner, source: Path, home: Path, target: Path) -> dict[str, object]:
    result = runner.invoke(
        cli, ["scout", "resume", "add", str(source), "--home", str(home), "--target", str(target), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert "private_operation_invalid" not in result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    return payload


def test_resume_add_takes_a_file_name_with_spaces_and_a_non_ascii_letter(tmp_path: Path) -> None:
    home, target = _setup_and_init(tmp_path)
    runner = CliRunner()

    first = _resume_add(runner, _file(tmp_path / "in", READABLE_NAME), home, target)
    assert first["reference_created"] is True and first["record_created"] is True
    assert _resume_labels(home, target, str(first["gig_id"])) == [READABLE_NAME]

    again = _resume_add(runner, _file(tmp_path / "in", READABLE_NAME), home, target)
    renamed = _resume_add(runner, _file(tmp_path / "other", "My Resume.md"), home, target)
    for repeat in (again, renamed):
        assert repeat["reference_created"] is False and repeat["record_created"] is False
        assert (repeat["reference_id"], repeat["record_id"], repeat["revision_id"]) == (
            first["reference_id"],
            first["record_id"],
            first["revision_id"],
        )
    assert _resume_labels(home, target, str(first["gig_id"])) == [READABLE_NAME]


def test_resume_add_prints_no_error_for_the_name_in_the_bug_report(tmp_path: Path) -> None:
    home, target = _setup_and_init(tmp_path)

    result = CliRunner().invoke(
        cli,
        ["scout", "resume", "add", str(_file(tmp_path / "in", "My Resume.md")), "--home", str(home), "--target", str(target)],
    )

    assert result.exit_code == 0, result.output
    assert "operation key is invalid" not in result.output
    assert "are ready" in result.output
