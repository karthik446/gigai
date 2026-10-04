"""0110-8-11: a company's NAME for display, from the company index, wherever a posting's company is shown.

A posting row's ``company`` is its board token ("garnerhealth",
"medallionakafirstlayerai"): the board clients have no other name. The
company index knows the real one ("Garner Health", "Medallion": the watchlist
and catalog name ``sources update`` wrote into ``<ats>:<slug>.json``). The
token stays what it is everywhere it is an id (rows, stored records, digests,
filters); a response that SHOWS a company says its name.

0110-10-03: in a response, ``company`` IS the name. An agent reading
``posting.company`` took the board token ("ospreylabs") for the company's
name while the real one sat beside it in ``company_name``. Every
posting-shaped object of a response now carries:

* ``company``: the display name ("Osprey Lane");
* ``company_slug``: the board token ("ospreylabs"), or ``null`` when the
  stored company is not a token (a posting read from a page or pasted text
  has only the name its page gave);
* ``company_name``: the same name as ``company`` (kept for readers written
  before the change).

Stored records keep the token in ``company``, as before: only what a response
says changed.

* :func:`index_company_name`: the index's name for a token, when it has one
  that is not the token itself;
* :func:`company_display_name`: that, else the slug rule every surface already
  used (``osprey-lane`` -> ``Osprey Lane``), else the text as it is;
* :func:`company_slug`: the board token a stored ``company`` is, when it is one;
* :func:`with_company_names`: names every posting-shaped object of a JSON
  response (one that has a ``company`` and names a job by its link), so the
  Jobs rows, the job page, the assessments list, applications and tailored
  resumes all say the same name. A story's ``company`` (the user's own words)
  is not posting-shaped and is left alone.

Reading a name opens only the head of one small index file and is cached by
the file's stamp; nothing is requested, nothing is written, and it never
raises: with no index the slug rule answers, as before.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import quote

_PROVIDERS = ("greenhouse", "lever", "ashby")
_SLUG_LIKE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TOKEN_LIKE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
#: The index file is written with sorted keys, so ``company`` is in its first bytes, long before ``postings``.
_HEAD_BYTES = 4096
_COMPANY_FIELD = re.compile(r'"company":("(?:[^"\\]|\\.)*")')
#: A posting-shaped object names a job by its link next to its company (a watchlist entry carries its own name already).
_POSTING_KEYS = frozenset({"job_identity", "normalized_url", "url", "job_url", "source_url"})
_NAME_KEY = "company_name"
_SLUG_KEY = "company_slug"
#: Where a posting-shaped object says its board token itself (a run's posting row).
_TOKEN_KEY = "board_token"

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[tuple[int, int], str | None]] = {}
_CACHE_LIMIT = 20_000


def slug_display_name(company: str | None) -> str | None:
    """A board slug ("tallgrass-health") as a name ("Tallgrass Health"); a real name passes through (the UI's own rule)."""

    if not company or not _SLUG_LIKE.match(company):
        return company
    return " ".join(part[:1].upper() + part[1:] for part in company.split("-") if part)


def _name_in_file(path: Path) -> str | None:
    try:
        found = os.stat(path)
    except OSError:
        return None
    stamp = (found.st_mtime_ns, found.st_size)
    key = os.fspath(path)
    with _LOCK:
        cached = _CACHE.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    name: str | None = None
    try:
        with open(path, "rb") as handle:
            head = handle.read(_HEAD_BYTES).decode("utf-8", errors="ignore")
        match = _COMPANY_FIELD.search(head)
        if match is not None:
            value = json.loads(match.group(1))
            name = value.strip() if type(value) is str and value.strip() else None
    except (OSError, ValueError):
        name = None
    with _LOCK:
        if len(_CACHE) >= _CACHE_LIMIT:
            _CACHE.clear()
        _CACHE[key] = (stamp, name)
    return name


def index_company_name(home_root: Path | str | None, company: str | None) -> str | None:
    """The company index's name for the board token ``company``; ``None`` when it has none of its own."""

    if home_root is None or type(company) is not str or not _TOKEN_LIKE.match(company):
        return None
    root = Path(home_root) / "cache" / "scout" / "companies"
    for provider in _PROVIDERS:
        name = _name_in_file(root / f"{provider}:{quote(company, safe='')}.json")
        if name is not None and name != company:
            return name
    return None


def company_display_name(home_root: Path | str | None, company: str | None) -> str | None:
    """What to show for ``company``: the index's name, else the slug rule, else the text itself."""

    return index_company_name(home_root, company) or slug_display_name(company)


def company_slug(home_root: Path | str | None, company: str | None, board_token: object = None) -> str | None:
    """The board token of a posting whose stored company is ``company``; ``None`` when it has none.

    ``board_token`` is the posting's own, when its object carries one. Without
    it, ``company`` is the token when the index holds a board by that name or
    it is written as a slug ("osprey-lane"); a name a page gave ("Osprey Lane",
    "Garner Health") is not a token.
    """

    if type(board_token) is str and board_token:
        return board_token
    if type(company) is not str or not company:
        return None
    if _SLUG_LIKE.match(company) or index_company_name(home_root, company) is not None:
        return company
    return None


def named_company(home_root: Path | str | None, company: str | None, board_token: object = None) -> dict[str, object]:
    """``company`` / ``company_slug`` / ``company_name`` as a response says them (0110-10-03), from the stored ``company``."""

    name = company_display_name(home_root, company)
    return {"company": name, _SLUG_KEY: company_slug(home_root, company, board_token), _NAME_KEY: name}


def with_company_names(payload: object, home_root: Path | str | None) -> object:
    """``payload`` with every posting-shaped object's company named: ``company`` the name, ``company_slug`` the token.

    Nothing given is changed: an object that gets a name (and each container
    above it) is copied, everything else is returned as it was, so a row a
    route keeps between requests is never written to.
    """

    if home_root is None:
        return payload
    return _named(payload, home_root)


def _named(node: object, home_root: Path | str) -> object:
    if isinstance(node, list):
        out_list: list[object] | None = None
        for position, value in enumerate(node):
            if isinstance(value, (dict, list)):
                named = _named(value, home_root)
                if named is not value:
                    if out_list is None:
                        out_list = list(node)
                    out_list[position] = named
        return node if out_list is None else out_list
    if not isinstance(node, dict):
        return node
    out: dict[object, object] | None = None
    for key, value in node.items():
        if isinstance(value, (dict, list)):
            named = _named(value, home_root)
            if named is not value:
                if out is None:
                    out = dict(node)
                out[key] = named
    company = node.get("company")
    # An object that already says its slug was named where it was built (the Jobs rows): left as it is.
    if type(company) is str and company and _SLUG_KEY not in node and not _POSTING_KEYS.isdisjoint(node):
        if out is None:
            out = dict(node)
        named = named_company(home_root, company, node.get(_TOKEN_KEY))
        given = node.get(_NAME_KEY)
        if type(given) is str and given:
            named["company"] = named[_NAME_KEY] = given
        out.update(named)
    return node if out is None else out


__all__ = ["company_display_name", "company_slug", "index_company_name", "named_company", "slug_display_name", "with_company_names"]
