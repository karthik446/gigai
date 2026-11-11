"""Experimental designs for ``eval ablate``: which runs an ablation makes and which runs each level comparison pools.

A design is a list of cells; a cell is ``{axis: level}`` for every axis and becomes one ``eval run``. Two are offered:

- ``ofat`` (one factor at a time): the baseline cell holds every axis at its first level; every other level of every
  axis gets one cell with the other axes at baseline. ``1 + sum(len(levels) - 1)`` runs; one three-level axis is three.
- ``l9``: the Taguchi L9 orthogonal array, nine runs for two to four axes of exactly three levels each. Every pair of
  columns holds each level pair exactly once, so a level's three runs are balanced over the other axes.

``arm(axis, level)`` gives the cell indexes whose scores stand for that level: in OFAT the one cell with the other
axes at baseline (the baseline cell for the first level), in L9 the three cells at that level.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

DESIGNS = ("ofat", "l9")

#: Taguchi L9(3^4): nine rows, four columns, level indexes 0..2. Any two columns hold every level pair once.
L9 = (
    (0, 0, 0, 0),
    (0, 1, 1, 1),
    (0, 2, 2, 2),
    (1, 0, 1, 2),
    (1, 1, 2, 0),
    (1, 2, 0, 1),
    (2, 0, 2, 1),
    (2, 1, 0, 2),
    (2, 2, 1, 0),
)


class DesignError(ValueError):
    """The axes do not fit the design (a refusal before any run)."""


@dataclass(frozen=True)
class Design:
    name: str
    #: axis id -> its levels, first = baseline; insertion order is the column order.
    axes: Mapping[str, Sequence[Any]]
    cells: Sequence[Mapping[str, Any]]

    def arm(self, axis: str, level: Any) -> list[int]:
        """Indexes of the cells whose scores stand for ``level`` of ``axis``."""

        if self.name == "ofat":
            baseline = {name: levels[0] for name, levels in self.axes.items() if name != axis}
            return [i for i, cell in enumerate(self.cells) if cell[axis] == level and all(cell[n] == b for n, b in baseline.items())]
        return [i for i, cell in enumerate(self.cells) if cell[axis] == level]

    def pairs(self, axis: str) -> list[tuple[Any, Any]]:
        """Every level pair of ``axis`` in level order: (first, second), (first, third), (second, third)."""

        levels = list(self.axes[axis])
        return [(levels[i], levels[j]) for i in range(len(levels)) for j in range(i + 1, len(levels))]


def design(name: str, axes: Mapping[str, Sequence[Any]]) -> Design:
    """The cells of design ``name`` over ``axes`` (id -> levels, first = baseline); ``DesignError`` when they do not fit."""

    if name not in DESIGNS:
        raise DesignError(f"unknown design {name!r}: one of {', '.join(DESIGNS)}")
    if not axes:
        raise DesignError("an ablation needs at least one axis")
    for axis, levels in axes.items():
        if len(levels) < 2:
            raise DesignError(f"axis {axis} has {len(levels)} level(s); an ablation moves an axis over at least two")
        if len(set(map(repr, levels))) != len(levels):
            raise DesignError(f"axis {axis} repeats a level: {list(levels)}")
    ordered = {axis: tuple(levels) for axis, levels in axes.items()}
    return Design(name, ordered, _ofat(ordered) if name == "ofat" else _l9(ordered))


def _ofat(axes: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    baseline = {axis: levels[0] for axis, levels in axes.items()}
    cells = [dict(baseline)]
    for axis, levels in axes.items():
        cells.extend({**baseline, axis: level} for level in levels[1:])
    return cells


def _l9(axes: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    if not 2 <= len(axes) <= 4:
        raise DesignError(f"the L9 design takes two to four axes, not {len(axes)}")
    for axis, levels in axes.items():
        if len(levels) != 3:
            raise DesignError(f"the L9 design needs exactly three levels per axis; axis {axis} has {len(levels)}")
    names = list(axes)
    return [{axis: axes[axis][row[column]] for column, axis in enumerate(names)} for row in L9]
