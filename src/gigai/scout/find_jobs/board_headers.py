"""The headers every request to a job board carries: who is asking, and where to write (0.1.11.8).

One place. Every client that can reach a board's host builds its headers here: the sources update and the
find-jobs run (``bindings._http_client``), the liveness check (``posting_live.liveness_client``), a description
lookup and a pasted job URL (``job_input.job_fetch_client``), discovery's board probes and the Exa search. A board
operator who sees the requests can name the reader and reach its maintainer; the robots guard
(``robots_guard.py``) reads a host's rules for the same token.
"""

from __future__ import annotations

#: The product token: the User-Agent's first word, and the group ``robots_guard`` looks for in a robots.txt.
PRODUCT_TOKEN = "GigAI"
#: Where a board operator can read what this is and write to its maintainer.
CONTACT_URL = "https://github.com/karthik446/gigai"


def user_agent() -> str:
    """``GigAI/<version> (+https://github.com/karthik446/gigai)``."""

    import importlib.metadata

    try:
        version = importlib.metadata.version("gigai")
    except importlib.metadata.PackageNotFoundError:
        version = "dev"
    return f"{PRODUCT_TOKEN}/{version} (+{CONTACT_URL})"


def board_headers() -> dict[str, str]:
    """A new dict of the headers a board-facing client is built with."""

    return {"User-Agent": user_agent()}


__all__ = ["CONTACT_URL", "PRODUCT_TOKEN", "board_headers", "user_agent"]
