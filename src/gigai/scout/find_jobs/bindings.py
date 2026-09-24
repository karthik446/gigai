"""Bind the real Scout find-jobs nodes to the functional graph.

The graph-node registry is deliberately process-local.  ``launch_run`` starts
the deterministic scheduler in a spawned child, so registration also installs
a module-level worker wrapper; the wrapper re-registers the nodes in the child
before handing control to the existing scheduler entry point.

The two ``GIGAI_SCOUT_FIND_JOBS_TEST_*`` hooks are intentionally narrow test
seams.  They make it possible for an offline behavior test to construct
``httpx.MockTransport`` in the spawned child, where a parent-process transport
object cannot be pickled.  Production callers leave both variables unset.
"""

from __future__ import annotations

from dataclasses import replace
from functools import partial
import json
import os
from pathlib import Path
from typing import Any

import httpx

from ...canonical import parse_json_bytes
from ...config import GigAIConfig, load_config
from ...graph_node_registry import RegisteredNode, lookup, register
from .ats_board_clients import ATSBoardClients
from .exa_client import ExaSearchClient
from .contracts import (
    ACQUIRE_CAPABILITY,
    ACQUIRE_DECLARED_EFFECTS,
    ASSESS_CAPABILITY,
    ASSESS_DECLARED_EFFECTS,
    PRESENT_CAPABILITY,
    PRESENT_DECLARED_EFFECTS,
    AcquireOutput,
    NotAssessedReason,
    NotAssessedRow,
    PresentInput,
)
from .market_acquisition import acquire_node
from ..projection import present_node
from ..proposal_execution import assess_node
from .watchlist import JournalWatchlistClient, list_active


GRAPH_ID = "find-jobs-functional"
GRAPH_VERSION = 1

# These names are public so the offline M1 behavior can configure a valid
# local-model identity without duplicating the binding's test constants.
TEST_MODEL_NAME = "scout-test:latest"
TEST_MODEL_DIGEST = "sha256:" + ("0" * 64)

_HOME_ROOT_ENV = "GIGAI_SCOUT_FIND_JOBS_HOME_ROOT"
_TEST_HTTP_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_HTTP"
_TEST_MODEL_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_MODEL"
_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
_LIVE_BINDINGS: dict[tuple[Path, Path], tuple[RegisteredNode, ...]] = {}


def _registry_graph_ids(*, home_root: Path, target: Path) -> tuple[str, ...]:
    """Return the selector plus the sealed graph identity used by I-2.

    I-1's descriptor/selector is ``find-jobs-functional`` while the compiled
    goal graph has its own canonical ``graph_*`` identity.  The scheduler's
    registry lookup is keyed by the latter, so keep the required selector
    binding and add the sealed graph identity as an execution alias.
    """

    graph_ids = [GRAPH_ID]
    try:
        from ...workpad import resolve_workpad

        resolved = resolve_workpad(
            home_root=home_root,
            requested_target=target,
            gig_id=None,
            allow_semantic_state=True,
        )
        pointer = parse_json_bytes(
            (resolved.path / "manifests" / "active-gig-version.json").read_bytes()
        )
        graph_set_ref = pointer.get("graph_set") if isinstance(pointer, dict) else None
        graph_set_path = graph_set_ref.get("path") if isinstance(graph_set_ref, dict) else None
        if not isinstance(graph_set_path, str):
            return tuple(graph_ids)
        graph_set = parse_json_bytes((resolved.path / graph_set_path).read_bytes())
        descriptors = graph_set.get("graphs") if isinstance(graph_set, dict) else None
        if not isinstance(descriptors, list):
            return tuple(graph_ids)
        descriptor = next(
            (item for item in descriptors if isinstance(item, dict) and item.get("graph_id") == GRAPH_ID),
            None,
        )
        graph_ref = descriptor.get("goal_graph") if isinstance(descriptor, dict) else None
        graph_path = graph_ref.get("path") if isinstance(graph_ref, dict) else None
        if not isinstance(graph_path, str):
            return tuple(graph_ids)
        graph = parse_json_bytes((resolved.path / graph_path).read_bytes())
        graph_id = graph.get("graph_id") if isinstance(graph, dict) else None
        if isinstance(graph_id, str) and graph_id and graph_id not in graph_ids:
            graph_ids.append(graph_id)
    except (OSError, ValueError, KeyError, TypeError):
        # Bare binding/import tests may not have an active approved workpad;
        # the mandated selector registration remains valid in that case.
        pass
    return tuple(graph_ids)


class _BoundWatchlist:
    """A-5's journal client plus the read method A-6 probes when polling ATS."""

    def __init__(self, home_root: Path, target: Path) -> None:
        self._home_root = home_root
        self._target = target
        self._journal = JournalWatchlistClient(home_root, target)
        self._gig_id = self._journal._resolved.gig_id

    def add_to_watchlist(self, entry: object) -> object:
        return self._journal.add_to_watchlist(entry)  # type: ignore[arg-type]

    def active_entries(self) -> tuple[object, ...]:
        return list_active(
            self._home_root,
            self._target,
            gig_id=self._gig_id,
        )


def _test_http_enabled() -> bool:
    return os.environ.get(_TEST_HTTP_ENV) == "1"


def _test_provider_handler(request: httpx.Request) -> httpx.Response:
    """Serve the fixed Exa/Greenhouse fixtures used by the child-process test."""

    if request.method == "POST" and request.url.host == "api.exa.ai" and request.url.path == "/search":
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://boards.greenhouse.io/acme/jobs/101",
                        "title": "Software Engineer",
                        "publishedDate": "2026-09-22T00:00:00Z",
                    }
                ]
            },
            request=request,
        )
    if request.method == "GET" and request.url.host == "boards-api.greenhouse.io":
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "101",
                        "title": "Software Engineer",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/101",
                        "location": {"name": "Denver, CO"},
                        "updated_at": "2026-09-22T00:00:00Z",
                        "content": "Build reliable Python services.",
                    }
                ]
            },
            request=request,
        )
    return httpx.Response(404, json={"error": "test fixture route not found"}, request=request)


def _test_model_handler(request: httpx.Request) -> httpx.Response:
    """Answer the three Ollama identity/chat calls without a model process."""

    if request.method == "GET" and request.url.path == "/api/version":
        return httpx.Response(200, json={"version": "scout-test"}, request=request)
    if request.method == "GET" and request.url.path == "/api/tags":
        return httpx.Response(
            200,
            json={"models": [{"name": TEST_MODEL_NAME, "digest": TEST_MODEL_DIGEST}]},
            request=request,
        )
    if request.method == "POST" and request.url.path == "/api/chat":
        return httpx.Response(
            200,
            json={
                "model": TEST_MODEL_NAME,
                "created_at": "2026-09-23T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "matrix": [
                                {
                                    "requirement": "Python",
                                    "resume_evidence": ["Built Python services"],
                                    "status": "met",
                                }
                            ],
                            "suggestions": ["Keep the service example."],
                            "questions": ["Which platform would you prefer?"]
                        },
                        separators=(",", ":"),
                    ),
                },
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 10,
                "eval_count": 20,
            },
            request=request,
        )
    return httpx.Response(404, json={"error": "test fixture route not found"}, request=request)


def _http_client() -> httpx.Client:
    transport = httpx.MockTransport(_test_provider_handler) if _test_http_enabled() else None
    return httpx.Client(
        timeout=_TIMEOUT,
        transport=transport,
        follow_redirects=False,
        trust_env=False,
    )


def _patch_test_model_transport(config: GigAIConfig) -> None:
    """Inject a MockTransport into B-2's real adapter factory for M1 only."""

    if os.environ.get(_TEST_MODEL_ENV) != "1":
        return
    from .. import proposal_execution as scout_proposal_execution
    from ...model_targets import resolve_model_target
    from ...adapters.factory import resolve_model_adapter as original

    current = getattr(scout_proposal_execution, "resolve_model_adapter")
    if getattr(current, "_scout_test_transport", False):
        return

    def resolve_with_test_transport(active: GigAIConfig, target_name: str, **kwargs: Any) -> object:
        resolved = resolve_model_target(active, target_name)
        overrides = dict(kwargs.pop("transport_overrides", {}) or {})
        overrides.setdefault(resolved.endpoint.name, httpx.MockTransport(_test_model_handler))
        return original(active, target_name, transport_overrides=overrides, **kwargs)

    setattr(resolve_with_test_transport, "_scout_test_transport", True)
    scout_proposal_execution.resolve_model_adapter = resolve_with_test_transport  # type: ignore[assignment]


def _present_bound(context: object, input: PresentInput, *, home_root: Path, target: Path) -> object:
    """Normalize I-2's pre-C-1 batch ref before calling the real C-1 node.

    I-2's current scheduler passes ``AcquireOutput.batch_ref`` (the durable
    acquisition input) here, while C-1 consumes the serialized ``AcquireOutput``
    at the runner's output path.  The run ID is authoritative, so this small
    integration adapter keeps the C-1 callable unchanged and points it at the
    authenticated runner artifact.
    """

    run_id = getattr(context, "run_id", None)
    if not isinstance(run_id, str) or not run_id:
        return present_node(context, input, home_root=home_root, target=target)  # type: ignore[arg-type]
    normalized = PresentInput(
        f"runs/{run_id}/outputs/acquire.json",
        input.assessment_ref,
        input.node_receipts,
    )
    return present_node(context, normalized, home_root=home_root, target=target)  # type: ignore[arg-type]


def _assess_bound(
    context: object,
    input: object,
    *,
    home_root: Path,
    target: Path,
    config: GigAIConfig,
) -> object:
    """Point B-2 at the workpad-owned acquisition artifact.

    A-6's ``AcquireOutput.batch_ref`` names its public-import input, while
    B-2 consumes normalized ``PostingRow`` objects.  The runner's committed
    acquire output is the shared DTO handoff, so point B-2 at that artifact and
    make the path absolute without changing the frozen DTO or sealed input.
    """

    workpad_path = getattr(context, "workpad_path", None)
    run_id = getattr(context, "run_id", None)
    if isinstance(workpad_path, str) and isinstance(run_id, str) and run_id:
        input = replace(
            input,
            acquire_batch_ref=os.fspath(
                Path(workpad_path) / "runs" / run_id / "outputs" / "acquire.json"
            ),
        )
    # B-2 currently binds the model response to the full ``PostingRow`` DTO
    # before handing it to the frozen assessment parser, while that parser's
    # ``posting`` field is the narrower ``SelectedPosting`` DTO.  Keep the
    # host-bound identity authoritative and normalize only that parser edge.
    from .. import proposals as scout_proposals

    parser = scout_proposals.parse_assessment_proposal
    selected_postings = {
        item.normalized_url: item
        for item in getattr(input, "selected_postings", ())
    }
    selected_by_url = {
        normalized_url: item.to_json()
        for normalized_url, item in selected_postings.items()
    }

    def parse_bound(value: object) -> object:
        if isinstance(value, dict) and isinstance(value.get("posting"), dict):
            posting = value["posting"]
            normalized_url = posting.get("normalized_url")
            selected = selected_by_url.get(normalized_url)
            if selected is not None:
                value = {
                    **value,
                    "posting": selected,
                }
        return parser(value)  # type: ignore[arg-type]

    scout_proposals.parse_assessment_proposal = parse_bound  # type: ignore[assignment]
    try:
        output = assess_node(  # type: ignore[arg-type]
            context, input, home_root=home_root, target=target, config=config
        )
        # B-2 builds candidate rows from its normalized acquisition input and
        # therefore leaves their content digest unset.  The selected-posting
        # DTO already carries the authoritative acquisition digest; align the
        # candidate rows before the scheduler persists AssessOutput so the
        # frozen cross-field contract can validate the handoff.
        normalized_rows = tuple(
            replace(
                row,
                posting=replace(
                    row.posting,
                    content_sha256=selected_postings[row.posting.normalized_url].content_sha256,
                ),
            )
            if row.posting.normalized_url in selected_postings
            and row.posting.content_sha256
            != selected_postings[row.posting.normalized_url].content_sha256
            else row
            for row in output.candidate_rows
        )
        output = replace(output, candidate_rows=normalized_rows)
        # A-6 intentionally sends only new/edited (and, since uat-bug-009,
        # UNCHANGED-but-never-successfully-assessed) role matches to B-2. A
        # genuinely skippable UNCHANGED row (a successful prior assessment
        # already exists for it -- see market_acquisition._prior_assessments)
        # never reaches B-2's own candidate_rows/assessments/not_assessed at
        # all, yet C-1's PresentPayload partition (present_node/projection.py)
        # requires EVERY row in AcquireOutput.rows to appear in either
        # payload.assessments or payload.not_assessed. Reconcile here rather
        # than asking B-2 to reread acquisition state or changing its
        # standalone contract: any UNCHANGED acquire row missing from B-2's
        # own candidate_rows/assessments/not_assessed (a mixed run can have
        # some newly-assessed candidates alongside some genuinely-skipped
        # UNCHANGED rows, not just the all-skipped case) gets the same
        # deterministic NotAssessedReason.UNCHANGED label C-1 already used
        # before this reconciliation existed. uat-bug-009's carried-forward
        # result itself is surfaced separately, from acquire's own
        # AcquireOutput.carried_forward_assessments (present_api.py), not
        # through this reconciliation.
        if workpad_path and run_id:
            acquire_path = Path(workpad_path) / "runs" / run_id / "outputs" / "acquire.json"
            try:
                acquire = AcquireOutput.from_json(json.loads(acquire_path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                acquire = None
            if acquire is not None:
                accounted = {row.posting.normalized_url for row in output.candidate_rows} | {
                    row.posting.normalized_url for row in output.not_assessed
                } | {item.posting.normalized_url for item in output.assessments}
                missing_unchanged = tuple(
                    row for row in acquire.rows
                    if row.outcome.value == "unchanged" and row.posting.normalized_url not in accounted
                )
                if missing_unchanged:
                    output = replace(
                        output,
                        candidate_rows=(*output.candidate_rows, *missing_unchanged),
                        not_assessed=(
                            *output.not_assessed,
                            *(NotAssessedRow(row.posting, NotAssessedReason.UNCHANGED) for row in missing_unchanged),
                        ),
                    )
        return output
    finally:
        scout_proposals.parse_assessment_proposal = parser  # type: ignore[assignment]


def _register_nodes(
    *, home_root: Path, target: Path, install_worker_hook: bool
) -> tuple[RegisteredNode, ...]:
    home = Path(home_root).expanduser().resolve(strict=False)
    root = Path(target).expanduser().resolve(strict=False)
    os.environ[_HOME_ROOT_ENV] = os.fspath(home)

    graph_ids = _registry_graph_ids(home_root=home, target=root)
    keys = (
        ("acquire", ACQUIRE_CAPABILITY),
        ("assess", ASSESS_CAPABILITY),
        ("present", PRESENT_CAPABILITY),
    )
    existing = tuple(lookup(GRAPH_ID, GRAPH_VERSION, slug, capability) for slug, capability in keys)
    if all(item is not None for item in existing):
        # A caller may bind before the active graph-set pointer is available
        # (for example, an import/bootstrap probe) and call us again once a
        # sealed graph is selected.  Reuse the already-bound callables while
        # filling the execution aliases instead of leaving the child lookup
        # incomplete.
        for graph_id in graph_ids:
            if graph_id == GRAPH_ID:
                continue
            for (slug, capability), binding in zip(keys, existing):
                assert binding is not None
                alias = lookup(graph_id, GRAPH_VERSION, slug, capability)
                if alias is None:
                    register(
                        graph_id,
                        GRAPH_VERSION,
                        slug,
                        capability,
                        binding.declared_effects,
                        binding.callable,
                    )
        if install_worker_hook:
            _install_worker_hook()
        return tuple(item for item in existing if item is not None)
    if any(item is not None for item in existing):
        raise RuntimeError("find-jobs functional node registry is partially bound")

    config = load_config(home)
    _patch_test_model_transport(config)
    http_client = _http_client()
    watchlist = _BoundWatchlist(home, root)
    acquire = partial(
        acquire_node,
        http_client=http_client,
        exa=ExaSearchClient(),
        ats=ATSBoardClients(),
        watchlist=watchlist,
        home_root=home,
        target=root,
    )
    assess = partial(_assess_bound, home_root=home, target=root, config=config)
    present = partial(_present_bound, home_root=home, target=root)
    nodes = (
        register(GRAPH_ID, GRAPH_VERSION, "acquire", ACQUIRE_CAPABILITY, ACQUIRE_DECLARED_EFFECTS, acquire),
        register(GRAPH_ID, GRAPH_VERSION, "assess", ASSESS_CAPABILITY, ASSESS_DECLARED_EFFECTS, assess),
        register(GRAPH_ID, GRAPH_VERSION, "present", PRESENT_CAPABILITY, PRESENT_DECLARED_EFFECTS, present),
    )
    for graph_id in graph_ids:
        if graph_id == GRAPH_ID:
            continue
        register(graph_id, GRAPH_VERSION, "acquire", ACQUIRE_CAPABILITY, ACQUIRE_DECLARED_EFFECTS, acquire)
        register(graph_id, GRAPH_VERSION, "assess", ASSESS_CAPABILITY, ASSESS_DECLARED_EFFECTS, assess)
        register(graph_id, GRAPH_VERSION, "present", PRESENT_CAPABILITY, PRESENT_DECLARED_EFFECTS, present)
    _LIVE_BINDINGS[(home, root)] = nodes
    if install_worker_hook:
        _install_worker_hook()
    return nodes


def register_find_jobs_nodes(*, home_root: Path, target: Path) -> tuple[RegisteredNode, ...]:
    """Register the real acquire, assess, and present callables for one target."""

    return _register_nodes(home_root=home_root, target=target, install_worker_hook=True)


_FIND_JOBS_CAPABILITIES = frozenset(
    {ACQUIRE_CAPABILITY, ASSESS_CAPABILITY, PRESENT_CAPABILITY}
)


def _graph_is_find_jobs(graph: object) -> bool:
    """Detect a find-jobs Goal purely from the compiled graph payload.

    Every other graph must see this binding as a no-op (D11): the spawned
    child inspects only the ``graph`` dict already handed to it by the
    scheduler and never touches the workpad/registry for a graph that has no
    find-jobs Goal, so a target that is not bound to a GigAI project (or has
    no find-jobs config) can still run any other graph unaffected.
    """

    if not isinstance(graph, dict):
        return False
    goals = graph.get("goals")
    if not isinstance(goals, list):
        return False
    for goal in goals:
        if not isinstance(goal, dict):
            continue
        executor = goal.get("executor")
        capability = executor.get("capability") if isinstance(executor, dict) else None
        if capability in _FIND_JOBS_CAPABILITIES:
            return True
    return False


def _install_worker_hook() -> None:
    """Make ``launch_run(wait=False)`` import and bind us in its child."""

    from ... import run

    current = getattr(run, "_worker_entry")
    if getattr(current, "_scout_find_jobs_binding_hook", False):
        return
    setattr(_child_worker_entry, "_scout_find_jobs_binding_hook", True)
    run._worker_entry = _child_worker_entry


def _child_worker_entry(
    resolved: object,
    run_id: str,
    gig_version: int,
    graph: dict[str, object],
    target_before: dict[str, object],
    run_started_handoff_id: str,
    manifest_digest: str,
) -> None:
    """Spawn target: bind this process only for a find-jobs Goal, then call
    the original worker entry unconditionally.

    A run of any other graph must be a true no-op here: no workpad
    resolution, no registry mutation, and no requirement that find-jobs
    env/config exist for this target (D11: "every other graph keeps today's
    rule").
    """

    if _graph_is_find_jobs(graph):
        target_root = getattr(resolved, "target_root", None)
        if not isinstance(target_root, Path):
            raise RuntimeError("find-jobs child cannot resolve its target root")
        home_value = os.environ.get(_HOME_ROOT_ENV)
        if not home_value:
            raise RuntimeError("find-jobs child has no inherited home root")
        _register_nodes(
            home_root=Path(home_value),
            target=target_root,
            install_worker_hook=False,
        )

    from ...run import _worker_entry

    _worker_entry(
        resolved,
        run_id,
        gig_version,
        graph,
        target_before,
        run_started_handoff_id,
        manifest_digest,
    )


__all__ = [
    "GRAPH_ID",
    "GRAPH_VERSION",
    "TEST_MODEL_DIGEST",
    "TEST_MODEL_NAME",
    "register_find_jobs_nodes",
]
