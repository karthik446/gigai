"""0.1.11 N1a/N2: the single skills inside one Skills line.

A Skills line groups skills: ``- Languages: Python/Go/TypeScript · Java/Kotlin · Model Context Protocol (MCP)``.
``skill_parts`` answers the label and every single skill of the line, split on ``/``, ``,``, ``;``, the
middle dot and parentheses.  It adds no rule of its own: the items are ``master_resume.skill_names``'s
(the PDF's chips) and each item is split by ``master_selection.skill_atoms`` (the selector's rule, which
keeps whole a name the keyword table knows, ``CI/CD``, and one with a one-letter part, ``A/B testing``).
What matches a posting (the selector) and what a hand-back may list (``handback_check``) read one split.

Pure: no file, no model.
"""

from __future__ import annotations

import re

from .master_resume import skill_names
from .master_selection import skill_atoms

#: What still separates skills inside one atom: a list inside parentheses (``(PyTorch, LoRA)``).
_INNER = re.compile(r"\s*[,;·]\s*")


def skill_parts(text: str) -> tuple[str, tuple[str, ...]]:
    """``(label, parts)`` of one Skills line: ``Data: PostgreSQL/Redis, Model Context Protocol (MCP)`` is
    ``("Data", ("PostgreSQL", "Redis", "Model Context Protocol", "MCP"))``.  Each part once, in the line's order."""

    label, names = skill_names(text)
    parts: list[str] = []
    for name in names:
        for atom in skill_atoms(name):
            parts.extend(part.strip() for part in _INNER.split(atom) if part.strip())
    return label, tuple(dict.fromkeys(parts))


__all__ = ["skill_parts"]
