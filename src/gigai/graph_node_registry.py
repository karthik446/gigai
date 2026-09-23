"""Fail-closed registry for real callable graph nodes.

The scheduler owns admission and execution policy; this module only stores the
binding made by an importer or graph materializer.  A binding is keyed by the
sealed graph identity, goal slug, and capability so a callable cannot be
reused by a different graph or goal accidentally.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from typing import Callable, Iterable


NodeCallable = Callable[..., object]
NodeKey = tuple[str, int, str, str]


@dataclass(frozen=True)
class RegisteredNode:
    """One approved callable and the effects it is allowed to declare."""

    graph_id: str
    graph_version: int
    goal_slug: str
    capability: str
    declared_effects: frozenset[str]
    callable: NodeCallable


_REGISTRY: dict[NodeKey, RegisteredNode] = {}


def _key(
    graph_id: object,
    graph_version: object,
    goal_slug: object,
    capability: object,
) -> NodeKey | None:
    if (
        type(graph_id) is not str
        or not graph_id
        or type(graph_version) is not int
        or graph_version < 1
        or type(goal_slug) is not str
        or not goal_slug
        or type(capability) is not str
        or not capability
    ):
        return None
    return graph_id, graph_version, goal_slug, capability


def register(
    graph_id: str,
    graph_version: int,
    goal_slug: str,
    capability: str,
    declared_effects: Iterable[str],
    callable: NodeCallable,
) -> RegisteredNode:
    """Register one callable at graph import/bind time.

    Re-registering the exact same binding is idempotent, which lets a module
    safely bind during import in a reloadable development process.  A different
    binding for an occupied key is refused rather than silently replacing the
    authority selected by the first binder.
    """

    key = _key(graph_id, graph_version, goal_slug, capability)
    if key is None:
        raise ValueError("graph node identity is invalid")
    if not builtins.callable(callable):
        raise TypeError("graph node callable is required")
    if isinstance(declared_effects, (str, bytes)):
        raise TypeError("declared_effects must be an iterable of effect names")
    try:
        effects = frozenset(declared_effects)
    except TypeError as exc:
        raise TypeError("declared_effects must be iterable") from exc
    if any(type(effect) is not str or not effect for effect in effects):
        raise ValueError("declared_effects must contain non-empty strings")
    binding = RegisteredNode(
        graph_id=graph_id,
        graph_version=graph_version,
        goal_slug=goal_slug,
        capability=capability,
        declared_effects=effects,
        callable=callable,
    )
    previous = _REGISTRY.get(key)
    if previous is not None and previous != binding:
        raise ValueError("graph node binding is already registered")
    _REGISTRY[key] = binding
    return binding


def lookup(
    graph_id: object,
    graph_version: object,
    goal_slug: object,
    capability: object,
) -> RegisteredNode | None:
    """Return a binding, or ``None`` for every malformed/unregistered key."""

    key = _key(graph_id, graph_version, goal_slug, capability)
    return _REGISTRY.get(key) if key is not None else None


def clear() -> None:
    """Clear bindings for isolated tests and interpreter-local rebinds."""

    _REGISTRY.clear()


__all__ = ["NodeCallable", "RegisteredNode", "clear", "lookup", "register"]
