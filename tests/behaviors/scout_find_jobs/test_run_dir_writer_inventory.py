"""regression-001-r2: close the class, not just the case.

regression-001 covered `raw/`/`progress/`; regression-001-r2 found a third
writer (`logs/`, U21's `_write_node_failure_log`) that the same class of bug
had already slipped through -- a write straight into `runs/<run_id>/...`
that is neither journal-committed (added to git via a `JournalArtifact`/
`_commit_handoff` "add" list, so it always lands in the same commit as the
handoff that references it) nor covered by
`workpad.RUN_LOCAL_ARTIFACT_EXCLUDES`. Either omission leaves the workpad's
`git status` non-empty and bricks the next `read_index` on that gig.

This test is the guard against a *fourth* one: it statically finds every
first-level subpath directly under `runs/<run_id>/` that `src/` ever builds
a path for (AST scan, not a hardcoded list of call sites), and asserts each
one is in exactly one of two known lists below:

- `JOURNAL_COMMITTED_RUN_SUBPATHS`: written loose, then always named by a
  `JournalArtifact`/`_commit_handoff` artifact list before the handoff that
  references it is committed -- so it's never left untracked. Each entry
  below cites where.
- `workpad.RUN_LOCAL_ARTIFACT_EXCLUDES`: additive-only, run-local,
  intentionally never committed (raw HTTP payloads, live progress, failure
  logs) -- see `workpad.py`'s comment on why these can't go in the tracked
  `.gitignore`.

A subpath in neither list fails with a message telling the author which
list to add it to (and, for the excludes list, that creation *and* the
`ensure_run_local_artifact_excludes` repair both cover it automatically
since they share one tuple).

## What the scanner catches (verified below, `test_scanner_finds_the_known_writers`)

Two path-building shapes, both seen in this codebase:
1. A literal 3-part chain: `... / "runs" / <run_id-like expr> / "subpath"`
   (``Path``-style) or an f-string ``f"runs/{run_id}/subpath/..."``.
2. The `run_root`/`run_dir`/`run_path` naming convention: a variable by one
   of those names (already resolved to `<workpad>/runs/<run_id>` earlier in
   the same function) joined with a literal, e.g. `run_dir / "evidence"` or
   `f"{run_dir}/target-before.json"`.

## What it can miss (honest limits, not "detection is complete")

- **Dynamic subpath names**: a path built from a variable or computed
  string instead of a string literal (e.g. `run_root / some_variable`)
  contributes no literal for the scanner to classify -- it would need to
  run the code to know the subpath. None of today's writers do this (all
  first-level subpaths under `runs/<run_id>/` are literals), but a new one
  that did would pass this test silently.
- **A root variable under a different name**: the convention-based pass
  only recognizes `run_root`, `run_dir`, and `run_path` by name -- exactly
  the names every current call site uses (grepped and confirmed in the
  worker report). A writer that resolves `runs/<run_id>` into a
  differently-named variable (e.g. `base` or `destination`) and then joins
  a literal onto *that* would be invisible to this pass. The literal
  3-part-chain pass (`"runs" / run_id / "subpath"`) still catches it if the
  join happens in one expression instead of through an intermediate
  variable.
- **Generated/`exec`'d/dynamically-imported code**: this is a static AST
  scan of `.py` source files under `src/gigai/`; anything constructed
  outside that (a code string, a plugin loaded from outside the package)
  is invisible to it.
- **False negatives from writers that never mention "runs" as a bare
  string**: if a path is threaded in fully pre-joined from a caller several
  frames away with no local literal, this scan (deliberately kept to
  per-file, per-expression matching, no cross-function data flow) won't
  connect it back to `runs/<run_id>/`.

None of these apply to the current writer set (confirmed exhaustively by
diffing this scanner's output against the manual `grep`/read-through in the
worker report), but a future writer using one of these shapes could still
slip past silently -- this test narrows the class, it doesn't close it to
zero.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re

from gigai.workpad import RUN_LOCAL_ARTIFACT_EXCLUDES

_SRC_ROOT = Path(__file__).resolve().parents[3] / "src" / "gigai"

# --- Known classification -----------------------------------------------

# Every first-level subpath under `runs/<run_id>/` that gets `git add`ed as
# part of a `JournalArtifact`/`_commit_handoff` artifact list -- i.e. it is
# always committed in the same handoff commit as the write that produced
# it, so `git status` never sees it as untracked. One citation per entry:
# a representative call site, not an exhaustive list of every reference.
JOURNAL_COMMITTED_RUN_SUBPATHS: frozenset[str] = frozenset({
    # run.py's _prepare_records -> `prepared` dict -> every value becomes a
    # JournalArtifact in the run_started transition (run.py ~553).
    "run-manifest.json",   # run.py: prepared[f"{run_dir}/run-manifest.json"]
    "run-brief.md",        # run.py: prepared[f"{run_dir}/run-brief.md"]
    "run-details.json",    # run.py: JournalArtifact(f"runs/{run_id}/run-details.json", ...) at every goal transition
    "goal-graph.json",     # run.py: prepared[f"{run_dir}/goal-graph.json"]
    "target-before.json",  # run.py: prepared via artifact(f"{run_dir}/target-before.json", ...)
    "target-after.json",   # run.py: JournalArtifact(f"runs/{run_id}/target-after.json", ...) at run finish
    "sealed",              # run.py: prepared[f"{run_dir}/sealed/..."] (offline-capability, *-execution-request, find-jobs-*, proposal-graph-selection)
    "operator-consent.json",  # run.py: prepared[f"{run_dir}/operator-consent.json"] when consent redeemed
    "terminal-handoff.md",    # run.py: JournalArtifact-referenced via terminal_path at run finish
    "external-run.json",   # posting_inputs.py/research_inputs.py: JournalArtifact(f"runs/{run_id}/external-run.json", ...)
    # _execute_goal's registered-node path: the node's own output/receipt
    # become the goal's `evidence` ref list, which `_evidence_artifacts`
    # (run.py) turns into JournalArtifacts at goal_completed/goal_failed --
    # on failure too, since `_write_registered_failure_receipt` writes the
    # receipt before the goal_failed transition commits it.
    "outputs",              # run.py: _registered_node_paths -> output_path.write_bytes(...), committed via evidence ref
    "receipts",             # run.py: _registered_node_paths -> receipt_path.write_bytes(...), committed via evidence ref (success AND failure)
    # _execute_goal's unregistered/offline path: evidence/<goal_id>.txt is
    # itself the returned evidence ref, committed the same way.
    "evidence",              # run.py: evidence_path = run_dir / "evidence" / f"{goal_id}.txt"
    "model-invocations",     # proposal_records.py/provider_review.py: JournalArtifact(f"runs/{run_id}/model-invocations/{id}/record.json", ...)
    "model-exchanges",       # model_exchange.py: JournalArtifact(path, record_bytes) where path is f"runs/{run_id}/model-exchanges/..."
    "provider-reviews",      # run.py: JournalArtifact-referenced via f"runs/{run_id}/provider-reviews/{plan_id}/report.md"
    "review",                # run.py: JournalArtifact(f"runs/{run_id}/review/...", ...) (bundle, traces, findings, verification, adjudications, reports, review-loop, evidence)
    "scout-interview",       # run.py: JournalArtifact(result_path, ...) where result_path = f"runs/{run_id}/scout-interview/result.json"
    "scout-tailor",          # run.py/document_records.py: JournalArtifact(result_path, ...) where result_path = f"runs/{run_id}/scout-tailor/result.json"
    "scout-proposals",       # proposal_execution.py/proposal_records.py: committed via the invocation record path under runs/{run_id}/scout-proposals/{id}/
    "cases",                 # runtime_comparison.py: JournalArtifact(f"runs/{run_id}/cases/{case_id}/...", ...)
    # posting_inputs.py/research_inputs.py read these back via
    # `snapshot.artifacts` -- the journal snapshot of already-*committed*
    # paths -- which is only reachable if the write was committed.
    "checkpoints",           # posting_inputs.py: _checkpoint_history reads runs/{run_id}/checkpoints/*.json from snapshot.artifacts
    "artifacts",             # posting_inputs.py: _output validates paths under runs/{run_id}/artifacts/{checkpoint_id}/ against snapshot.artifacts
})

# Additive-only, intentionally never committed: real writers that produce
# run-local, non-authoritative artifacts the journal never names. Sourced
# directly from workpad.py's own tuple so this test can never silently drift
# from what `ensure_run_local_artifact_excludes` (creation + the read_index
# repair) actually covers.
def _exclude_subpath(pattern: str) -> str:
    match = re.fullmatch(r"/runs/\*/([^/]+)/", pattern)
    assert match, f"RUN_LOCAL_ARTIFACT_EXCLUDES entry has an unexpected shape: {pattern!r}"
    return match.group(1)


EXCLUDED_RUN_SUBPATHS: frozenset[str] = frozenset(
    _exclude_subpath(pattern) for pattern in RUN_LOCAL_ARTIFACT_EXCLUDES
)


# --- The scanner -----------------------------------------------------------

_RUN_ROOT_NAMES = re.compile(r"^(run_root|run_dir|run_path)$")


def _flatten_div_chain(node: ast.AST) -> list[str | None]:
    """Flatten a chain of ``/`` BinOps into its parts; a non-constant part
    (a name, call, f-string, ...) is recorded as ``None``."""

    parts: list[str | None] = []

    def walk(inner: ast.AST) -> None:
        if isinstance(inner, ast.BinOp) and isinstance(inner.op, ast.Div):
            walk(inner.left)
            walk(inner.right)
        elif isinstance(inner, ast.Constant) and isinstance(inner.value, str):
            parts.append(inner.value)
        else:
            parts.append(None)

    walk(node)
    return parts


def _leftmost_name(node: ast.AST) -> str | None:
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        node = node.left
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _find_run_subpaths_in_file(path: Path) -> set[str]:
    """Every literal first-level subpath this file builds directly under
    ``runs/<run_id>/`` (see the module docstring for exactly which two
    path-building shapes this recognizes, and what it cannot see)."""

    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            parts = _flatten_div_chain(node)
            # Shape 1: literal "runs" / <run_id-like expr> / "subpath".
            for i, part in enumerate(parts):
                if part == "runs" and i + 2 < len(parts):
                    subpath = parts[i + 2]
                    if subpath:
                        found.add(subpath)
            # Shape 2: the run_root/run_dir/run_path naming convention --
            # <root-name> / "subpath" (root already resolved elsewhere).
            root_name = _leftmost_name(node)
            if root_name and _RUN_ROOT_NAMES.match(root_name) and len(parts) >= 2:
                subpath = parts[1]
                if subpath:
                    found.add(subpath)

        if isinstance(node, ast.JoinedStr):
            rough = ""
            first_placeholder_name: str | None = None
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    rough += value.value
                elif isinstance(value, ast.FormattedValue):
                    if first_placeholder_name is None and isinstance(value.value, ast.Name):
                        first_placeholder_name = value.value.id
                    rough += "{X}"
            # f"runs/{run_id}/subpath/..."
            match = re.search(r"runs/\{X\}/([A-Za-z0-9_.\-]+)", rough)
            if match:
                found.add(match.group(1))
                continue
            # f"{run_dir}/subpath/..." (run_root/run_dir/run_path convention)
            if (
                rough.startswith("{X}/")
                and first_placeholder_name
                and _RUN_ROOT_NAMES.match(first_placeholder_name)
            ):
                tail_match = re.match(r"([A-Za-z0-9_.\-]+)", rough[len("{X}/"):])
                if tail_match:
                    found.add(tail_match.group(1))

    return found


def _all_run_subpaths() -> dict[str, set[str]]:
    """``{relative file path: {subpaths found in it}}`` across ``src/gigai``."""

    by_file: dict[str, set[str]] = {}
    for py_file in sorted(_SRC_ROOT.rglob("*.py")):
        hits = _find_run_subpaths_in_file(py_file)
        if hits:
            by_file[str(py_file.relative_to(_SRC_ROOT))] = hits
    return by_file


# --- Tests -------------------------------------------------------------


def test_every_discovered_run_subpath_is_committed_or_excluded() -> None:
    """The class-closing assertion: every subpath the scanner finds under
    ``runs/<run_id>/`` in ``src/`` must be in exactly one of the two known
    lists. A new writer landing in neither fails here with the subpath name
    and the file(s) it was found in, so the author knows which list to
    extend (and, for the exclude list, that it's `workpad.py`'s single
    `RUN_LOCAL_ARTIFACT_EXCLUDES` tuple -- one edit covers both workpad
    creation and the `read_index` repair)."""

    by_file = _all_run_subpaths()
    all_found: set[str] = set()
    for hits in by_file.values():
        all_found |= hits

    known = JOURNAL_COMMITTED_RUN_SUBPATHS | EXCLUDED_RUN_SUBPATHS
    unclassified = all_found - known
    if unclassified:
        offenders = {
            subpath: sorted(f for f, hits in by_file.items() if subpath in hits)
            for subpath in sorted(unclassified)
        }
        raise AssertionError(
            "found a writer under runs/<run_id>/ that is neither "
            "journal-committed nor excluded -- this is exactly the "
            "regression-001-r2 class of bug (a failed run's node-failure "
            "log was neither, and bricked every later run on that gig). "
            "For each subpath below, either (a) confirm it's always "
            "git-added via a JournalArtifact/_commit_handoff artifact list "
            "before the handoff that references it commits, and add it to "
            "JOURNAL_COMMITTED_RUN_SUBPATHS in this file with a citation, "
            "or (b) if it's a real run-local/non-authoritative artifact the "
            "journal never names, add '/runs/*/<subpath>/' to "
            "workpad.RUN_LOCAL_ARTIFACT_EXCLUDES (covers workpad creation "
            "and the read_index repair in one edit).\n"
            f"unclassified subpath -> file(s): {offenders}"
        )


def test_scanner_finds_the_known_writers() -> None:
    """The scanner itself must actually find the writers this fix and
    regression-001 care about, so a pass on the test above isn't vacuous
    (e.g. from a scan that silently finds nothing). Exercises both
    path-building shapes: the literal 3-part chain (raw/, via
    ``market_acquisition.py``'s ``"runs" / run_id / "raw"``) and the
    run_root/run_dir/run_path convention (progress/ via
    ``progress.py``'s ``run_root / "progress"``; logs/ via ``run.py``'s
    ``run_dir / "logs"``-shaped literal chain at ``_write_node_failure_log``).
    """

    by_file = _all_run_subpaths()
    all_found: set[str] = set()
    for hits in by_file.values():
        all_found |= hits

    for expected in ("raw", "progress", "logs", "outputs", "receipts", "sealed"):
        assert expected in all_found, (
            f"scanner failed to find the known writer {expected!r} -- "
            f"found: {sorted(all_found)}"
        )


def test_removing_logs_from_the_exclude_list_fails_the_inventory_test() -> None:
    """Proves the inventory test is load-bearing, not a vacuous pass: with
    '/runs/*/logs/' removed from the exclude list (simulating the state
    before this fix), 'logs' becomes unclassified and the assertion above
    must fail. This mirrors the acceptance bar's requirement to show the
    inventory test demonstrably fails without the fix -- done in-process
    here, deterministically, rather than by mutating workpad.py on disk."""

    tampered_excludes = frozenset(
        _exclude_subpath(pattern)
        for pattern in RUN_LOCAL_ARTIFACT_EXCLUDES
        if pattern != "/runs/*/logs/"
    )
    assert "logs" not in tampered_excludes, "test setup assumption broken: logs was already absent"

    by_file = _all_run_subpaths()
    all_found: set[str] = set()
    for hits in by_file.values():
        all_found |= hits

    known = JOURNAL_COMMITTED_RUN_SUBPATHS | tampered_excludes
    unclassified = all_found - known
    assert "logs" in unclassified, (
        "removing '/runs/*/logs/' from the exclude list should have made "
        f"'logs' unclassified; unclassified was: {sorted(unclassified)}"
    )
