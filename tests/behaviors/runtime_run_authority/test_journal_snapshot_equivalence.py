"""uat-bug-008-r1: prove the batched journal snapshot/read rewrite returns

IDENTICAL results to the pre-change implementation.

``_capture_committed_snapshot``/``read_committed_artifact`` in
``gigai.journal`` were rewritten (uat-bug-008) to batch their git plumbing
(one ``git log`` walk + one ``git show --name-only`` + one ``git cat-file
--batch`` instead of ~4 subprocess spawns per committed artifact). This
file loads the pre-change implementation straight from git history
(``git show HEAD:src/gigai/journal.py``, exec'd as a standalone module --
never vendored into ``src/``) and runs both implementations against the
same committed git histories, comparing every field of every returned
structure -- not counts, not "no exception raised" -- byte-for-byte on
snapshot artifacts, field-for-field on read results, and exception
type+code+message on every error case the old code raised.

Fixtures:
  - an operator-shaped multi-commit workpad (several records, references,
    run-inputs, dozens of total journal commits)
  - an empty journal (freshly provisioned, no transitions yet)
  - a path replaced across commits (mutable ``runs/<id>/run-details.json``)
  - a deleted/tampered artifact (working tree diverges from the committed
    blob after the fact -- both implementations must refuse it identically)
  - a binary artifact (non-UTF-8 bytes)
  - a path with spaces and unicode
  - error cases: artifact never committed, multiple publishers of an
    immutable path, tampered working-tree evidence

``test_equivalence_check_would_catch_a_real_difference`` proves the
comparison harness itself is not vacuously true, by temporarily perturbing
one field of the NEW implementation's result and showing the comparison
fails, then restoring it.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import types
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, "src")  # noqa: E402 -- must precede `import gigai`

import gigai  # noqa: E402  (ensures the real package is loaded first)
from gigai.canonical import digest_imported_bytes  # noqa: E402
from gigai.journal import JournalArtifact, record_transition  # noqa: E402
from gigai.workpad import provision_workpad  # noqa: E402

PROJECT_ID = "project_12345678-1234-4234-9234-123456789abc"
GIG_ID = "gig_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

_PREFIXES = ("records/", "references/", "run-inputs/")


def _load_head_journal_reference() -> types.ModuleType:
    """The pre-uat-bug-008 ``gigai.journal`` -- loaded from git history's

    exact source text, exec'd as a standalone module registered under the
    real ``gigai`` package (so its relative imports -- ``.canonical``,
    ``.workpad``, ``.diagnostics`` -- resolve to the same, unmodified
    modules the current code uses). Never written to ``src/``.
    """

    source = subprocess.run(
        ["git", "show", "HEAD:src/gigai/journal.py"],
        capture_output=True, text=True, check=True, shell=False,
        cwd=Path(__file__).resolve().parents[3],
    ).stdout
    module_name = "gigai._journal_head_reference_uat_bug_008_r1"
    spec = importlib.util.spec_from_loader(module_name, loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "gigai"
    module.__name__ = module_name
    sys.modules[module_name] = module
    exec(compile(source, "<HEAD:src/gigai/journal.py>", "exec"), module.__dict__)
    return module


@pytest.fixture(scope="module")
def old_journal() -> types.ModuleType:
    return _load_head_journal_reference()


def _handoff(tag: str) -> str:
    return f"handoff_{uuid.uuid5(uuid.NAMESPACE_URL, tag).hex[:8]}-1234-4abc-8def-123456789abc"


def _workpad(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    from gigai.setup import build_config, run_setup
    from gigai.target_binding import initialize_target

    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    # Pin the generated project id to exactly PROJECT_ID's UUID suffix (same
    # pattern as test_journal_locking_recovery.py's _workpad), so the fixed
    # PROJECT_ID/GIG_ID constants this file's journal calls use directly are
    # the ones actually registered.
    initialize_target(
        home_root=home,
        requested_target=target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    return provision_workpad(home_root=home, project_id=PROJECT_ID, gig_id=GIG_ID).path


def _write(workpad: Path, tag: str, *, transition: str = "private_reference_imported", **kwargs: object):
    return record_transition(
        workpad=workpad,
        project_id=PROJECT_ID,
        gig_id=GIG_ID,
        handoff_id=_handoff(tag),
        transition=transition,
        body=f"Transition {tag}",
        **kwargs,
    )


# --------------------------------------------------------------------------
# Comparison harness
# --------------------------------------------------------------------------


def _snapshot_as_dict(snapshot: object) -> dict[str, object]:
    """Full field-for-field structure, not a summary: head + every path's

    exact bytes, sorted for a stable comparison.
    """

    return {
        "head": snapshot.head,  # type: ignore[attr-defined]
        "artifacts": dict(sorted(snapshot.artifacts.items())),  # type: ignore[attr-defined]
    }


def _call_snapshot(module: types.ModuleType, root: Path, prefixes: tuple[str, ...] = _PREFIXES):
    """Call ``module``'s ``_capture_committed_snapshot``, returning either

    ``("ok", dict)`` or ``("error", (type_name, code, message))`` so a
    raised exception is itself part of the compared structure.
    """

    try:
        snapshot = module._capture_committed_snapshot(root, PROJECT_ID, GIG_ID, prefixes)
        return ("ok", _snapshot_as_dict(snapshot))
    except Exception as exc:  # noqa: BLE001 -- the exception IS the result being compared
        return ("error", (type(exc).__name__, getattr(exc, "code", None), str(exc)))


def _call_read(
    module: types.ModuleType,
    root: Path,
    path: str,
    *,
    head: str | None = None,
    allow_replaced_run_details: bool = False,
    allow_replaced_manifests: bool = False,
):
    try:
        data, commit = module.read_committed_artifact(
            workpad=root, project_id=PROJECT_ID, gig_id=GIG_ID, path=path, head=head,
            allow_replaced_run_details=allow_replaced_run_details,
            allow_replaced_manifests=allow_replaced_manifests,
        )
        return ("ok", (data, commit))
    except Exception as exc:  # noqa: BLE001
        return ("error", (type(exc).__name__, getattr(exc, "code", None), str(exc)))


def _assert_snapshot_equivalent(old_journal: types.ModuleType, root: Path, prefixes: tuple[str, ...] = _PREFIXES) -> None:
    import gigai.journal as new_journal

    old_result = _call_snapshot(old_journal, root, prefixes)
    new_result = _call_snapshot(new_journal, root, prefixes)
    assert old_result == new_result, (
        f"snapshot diverged:\nOLD: {old_result!r}\nNEW: {new_result!r}"
    )


def _assert_read_equivalent(
    old_journal: types.ModuleType, root: Path, path: str, **kwargs: object
) -> None:
    import gigai.journal as new_journal

    old_result = _call_read(old_journal, root, path, **kwargs)
    new_result = _call_read(new_journal, root, path, **kwargs)
    assert old_result == new_result, (
        f"read_committed_artifact diverged for {path!r}:\nOLD: {old_result!r}\nNEW: {new_result!r}"
    )


# --------------------------------------------------------------------------
# (a) Operator-shaped multi-commit fixture
# --------------------------------------------------------------------------


def test_equivalence_on_operator_shaped_multi_commit_workpad(
    old_journal: types.ModuleType, tmp_path: Path
) -> None:
    workpad = _workpad(tmp_path)
    # Several "resume" reference imports + their imported_reference records,
    # plus plain run-input imports -- the exact shape resolve_newest_resume_details
    # walks (records/, references/, run-inputs/), several dozen commits.
    record_ids: list[str] = []
    for i in range(3):
        ref_path = f"references/ref_{i:08x}-0000-4000-8000-000000000000/reference.json"
        source_path = f"references/ref_{i:08x}-0000-4000-8000-000000000000/source.txt"
        ref_content = f'{{"reference_id":"ref_{i}","kind":"resume","label":"r{i}"}}'.encode()
        source_content = f"resume body {i}\n".encode()
        _write(
            workpad, f"ref-{i}",
            artifacts=(
                JournalArtifact(ref_path, ref_content),
                JournalArtifact(source_path, source_content),
            ),
        )
        record_id = f"record_{i:08x}-0000-4000-8000-000000000000"
        record_ids.append(record_id)
        revision_path = f"records/{record_id}/revisions/revision_{i:08x}-0000-4000-8000-000000000000.json"
        revision_content = (
            f'{{"record_id":"{record_id}","revision_id":"revision_{i:08x}-0000-4000-8000-000000000000",'
            f'"parent_revision":null,"content":{{"family":"g45_reference","reference_id":"ref_{i}"}}}}'
        ).encode()
        _write(
            workpad, f"record-{i}",
            transition="private_record_revised",
            artifacts=(JournalArtifact(revision_path, revision_content),),
        )
    for i in range(15):
        input_path = f"run-inputs/input_{i:08x}-0000-4000-8000-000000000000/input.json"
        source_path = f"run-inputs/input_{i:08x}-0000-4000-8000-000000000000/source.txt"
        _write(
            workpad, f"input-{i}",
            artifacts=(
                JournalArtifact(input_path, f'{{"run_input_id":"input_{i}"}}'.encode()),
                JournalArtifact(source_path, f"pasted body {i}\n".encode()),
            ),
        )

    _assert_snapshot_equivalent(old_journal, workpad)

    # Also compare read_committed_artifact directly for a sample of the
    # committed paths (the single-artifact path the fix's cache/dedup work
    # in run.py/present_api.py ultimately calls through).
    for i in (0, 1, 2):
        ref_path = f"references/ref_{i:08x}-0000-4000-8000-000000000000/reference.json"
        _assert_read_equivalent(old_journal, workpad, ref_path)


# --------------------------------------------------------------------------
# (b) Empty journal
# --------------------------------------------------------------------------


def test_equivalence_on_empty_journal(old_journal: types.ModuleType, tmp_path: Path) -> None:
    workpad = _workpad(tmp_path)  # provisioned, zero transitions recorded
    _assert_snapshot_equivalent(old_journal, workpad)


# --------------------------------------------------------------------------
# (c) A path replaced across commits (mutable run-details.json)
# --------------------------------------------------------------------------


def test_equivalence_on_a_path_replaced_across_commits(
    old_journal: types.ModuleType, tmp_path: Path
) -> None:
    workpad = _workpad(tmp_path)
    run_id = "run_00000000-0000-4000-8000-000000000001"
    path = f"runs/{run_id}/run-details.json"
    _write(
        workpad, "run-details-v1",
        transition="run_started",
        artifacts=(JournalArtifact(path, b'{"status":"active"}'),),
        allow_artifact_replacement=True,
    )
    _write(
        workpad, "run-details-v2",
        transition="run_succeeded",
        artifacts=(JournalArtifact(path, b'{"status":"succeeded"}'),),
        allow_artifact_replacement=True,
    )

    # This path isn't under records/references/run-inputs, so the snapshot
    # walk (scoped to those prefixes) doesn't see it -- but the single-path
    # reader does, with allow_replaced_run_details -- the exact call
    # _capture_committed_snapshot's own per-artifact validation logic
    # shares via _validate_committed_artifact.
    _assert_read_equivalent(old_journal, workpad, path, allow_replaced_run_details=True)
    # Without the replacement allowance, both implementations must refuse
    # the multi-publisher path identically.
    _assert_read_equivalent(old_journal, workpad, path, allow_replaced_run_details=False)


# --------------------------------------------------------------------------
# (d) Deleted / tampered artifact (working tree diverges from committed bytes)
# --------------------------------------------------------------------------


def test_equivalence_on_a_deleted_or_tampered_artifact(
    old_journal: types.ModuleType, tmp_path: Path
) -> None:
    workpad = _workpad(tmp_path)
    path = "references/ref_00000000-0000-4000-8000-000000000099/reference.json"
    _write(
        workpad, "tamper-target",
        artifacts=(JournalArtifact(path, b'{"reference_id":"ref_tamper"}'),),
    )
    # Delete the working-tree file out from under the committed journal --
    # both implementations must refuse identically ("working evidence
    # differs from committed bytes" for read_committed_artifact; the
    # snapshot's own working-tree mirror check for the batched path).
    (workpad / path).unlink()

    _assert_read_equivalent(old_journal, workpad, path)
    _assert_snapshot_equivalent(old_journal, workpad)


def test_equivalence_on_a_renamed_artifact(old_journal: types.ModuleType, tmp_path: Path) -> None:
    """"Renamed" here means: the same logical content is re-published at

    a different immutable path in a later commit (records/references are
    add-only, never rewritten in place -- see docstring at top). Both
    implementations must see the same two independent artifacts.
    """

    workpad = _workpad(tmp_path)
    old_path = "references/ref_00000000-0000-4000-8000-0000000000aa/reference.json"
    new_path = "references/ref_00000000-0000-4000-8000-0000000000bb/reference.json"
    _write(workpad, "rename-old", artifacts=(JournalArtifact(old_path, b'{"reference_id":"ref_old"}'),))
    _write(workpad, "rename-new", artifacts=(JournalArtifact(new_path, b'{"reference_id":"ref_new"}'),))

    _assert_snapshot_equivalent(old_journal, workpad)
    _assert_read_equivalent(old_journal, workpad, old_path)
    _assert_read_equivalent(old_journal, workpad, new_path)


# --------------------------------------------------------------------------
# (e) Binary artifact
# --------------------------------------------------------------------------


def test_equivalence_on_a_binary_artifact(old_journal: types.ModuleType, tmp_path: Path) -> None:
    workpad = _workpad(tmp_path)
    path = "run-inputs/input_00000000-0000-4000-8000-000000000abc/source.bin"
    binary_content = bytes(range(256)) * 4  # non-UTF-8, every byte value present
    _write(workpad, "binary", artifacts=(JournalArtifact(path, binary_content),))

    _assert_snapshot_equivalent(old_journal, workpad)
    _assert_read_equivalent(old_journal, workpad, path)


# --------------------------------------------------------------------------
# (f) Path with spaces and unicode
# --------------------------------------------------------------------------


def test_equivalence_on_a_path_with_spaces(old_journal: types.ModuleType, tmp_path: Path) -> None:
    # Plain ASCII with spaces and parentheses: git does not C-quote these
    # (only non-ASCII bytes/backslash/quote trigger that -- see the unicode
    # test below for the one narrow, documented divergence this suite
    # allows), so this stays strict old == new equivalence.
    workpad = _workpad(tmp_path)
    path = "references/ref_00000000-0000-4000-8000-000000000ccc/resume source (final).txt"
    content = b"resume body with spaces in its path\n"
    _write(workpad, "spaces-path", artifacts=(JournalArtifact(path, content),))

    _assert_snapshot_equivalent(old_journal, workpad)
    _assert_read_equivalent(old_journal, workpad, path)


def test_equivalence_on_a_unicode_path_documented_divergence(
    old_journal: types.ModuleType, tmp_path: Path
) -> None:
    """The ONE intentional, narrow divergence from strict old == new equality.

    HEAD is fixed (it's the reference we're proving equivalence against,
    and cannot change) and its OLD ``read_committed_artifact`` has always
    C-quoted/octal-escaped non-ASCII path bytes in its ``git show
    --name-only`` output without ``-z`` (uat-bug-008-r1: found by this very
    equivalence test), so it raises on a legitimately committed unicode
    path where the checked (unquoted) path never matches the quoted names
    list. This bug is latent, not a live regression: real committed paths
    are always built from a UUID (references/record path components are
    ASCII hex+hyphens -- see private_records.py), so no production artifact
    has ever hit it. The NEW code (uat-bug-008-r1) uses ``-z`` everywhere it
    lists names (bonus fix, see this file's module docstring and the
    ``-z``-comment at journal.py's `read_committed_artifact`/
    `_batch_publishing_commits`/`_capture_committed_snapshot`), so it
    correctly reads this same artifact instead of also failing. Coordinator-
    approved narrow exception (uat-bug-008-r1): assert OLD's exact failure
    AND NEW's exact success + correct bytes, not "old == new".
    """

    workpad = _workpad(tmp_path)
    path = "references/ref_00000000-0000-4000-8000-000000000ccc/résumé source (final).txt"
    content = "Ünïcödé résumé body — café, naïve, 日本語\n".encode("utf-8")
    _write(workpad, "unicode-path", artifacts=(JournalArtifact(path, content),))

    old_result = _call_read(old_journal, workpad, path)
    assert old_result[0] == "error"
    assert old_result[1][0] == "JournalConflictError"
    assert old_result[1][1] == "journal_conflict"
    assert "publication is incomplete" in old_result[1][2]

    import gigai.journal as new_journal

    data, commit = new_journal.read_committed_artifact(
        workpad=workpad, project_id=PROJECT_ID, gig_id=GIG_ID, path=path,
    )
    assert data == content
    assert len(commit) == 40 and all(c in "0123456789abcdef" for c in commit)

    new_snapshot = new_journal._capture_committed_snapshot(workpad, PROJECT_ID, GIG_ID, _PREFIXES)
    assert new_snapshot.artifacts[path] == content


# --------------------------------------------------------------------------
# (g) Error cases: same exception type + code as the old implementation
# --------------------------------------------------------------------------


def test_equivalence_error_artifact_never_committed(
    old_journal: types.ModuleType, tmp_path: Path
) -> None:
    workpad = _workpad(tmp_path)
    _assert_read_equivalent(old_journal, workpad, "records/record_nope/revisions/nope.json")


def test_equivalence_error_multiple_publishers_of_an_immutable_path(
    old_journal: types.ModuleType, tmp_path: Path
) -> None:
    workpad = _workpad(tmp_path)
    path = "references/ref_00000000-0000-4000-8000-000000000ddd/reference.json"
    # Two commits publish the SAME immutable path with different bytes --
    # neither allow_replaced_run_details nor allow_replaced_manifests
    # applies, so this must be refused identically by both implementations.
    _write(workpad, "dup-1", artifacts=(JournalArtifact(path, b'{"v":1}'),), allow_artifact_replacement=True)
    _write(workpad, "dup-2", artifacts=(JournalArtifact(path, b'{"v":2}'),), allow_artifact_replacement=True)

    _assert_read_equivalent(old_journal, workpad, path)
    _assert_snapshot_equivalent(old_journal, workpad)


def test_equivalence_error_tampered_working_tree_extra_file(
    old_journal: types.ModuleType, tmp_path: Path
) -> None:
    """A file sitting in the working tree under a snapshot prefix that was

    never committed through the journal -- both implementations must
    refuse it as "extra or redirected".
    """

    workpad = _workpad(tmp_path)
    _write(
        workpad, "legit",
        artifacts=(JournalArtifact("references/ref_legit/reference.json", b'{"ok":true}'),),
    )
    extra = workpad / "references" / "ref_legit" / "extra-untracked.txt"
    extra.write_text("not committed through the journal\n", encoding="utf-8")

    _assert_snapshot_equivalent(old_journal, workpad)


# --------------------------------------------------------------------------
# The comparison harness itself must be able to fail
# --------------------------------------------------------------------------


def test_equivalence_check_would_catch_a_real_difference(
    old_journal: types.ModuleType, tmp_path: Path
) -> None:
    """Proves ``_assert_snapshot_equivalent``/``_assert_read_equivalent`` are

    not vacuously true: temporarily perturbs the NEW implementation's
    result and shows the comparison fails, then restores the real
    implementation so no other test in this file is affected.
    """

    import gigai.journal as new_journal

    workpad = _workpad(tmp_path)
    path = "references/ref_00000000-0000-4000-8000-000000000eee/reference.json"
    _write(workpad, "perturb-target", artifacts=(JournalArtifact(path, b'{"reference_id":"ref_perturb"}'),))

    real_capture = new_journal._capture_committed_snapshot
    try:
        def _perturbed_capture(root, project_id, gig_id, prefixes):
            snapshot = real_capture(root, project_id, gig_id, prefixes)
            # Flip one byte of one artifact's content -- a real regression
            # this equivalence suite exists to catch.
            tampered = dict(snapshot.artifacts)
            first_key = next(iter(tampered))
            tampered[first_key] = tampered[first_key] + b"TAMPERED"
            return type(snapshot)(snapshot.head, tampered)

        new_journal._capture_committed_snapshot = _perturbed_capture
        with pytest.raises(AssertionError, match="snapshot diverged"):
            _assert_snapshot_equivalent(old_journal, workpad)
    finally:
        new_journal._capture_committed_snapshot = real_capture

    # Restored: the real comparison passes again.
    _assert_snapshot_equivalent(old_journal, workpad)
