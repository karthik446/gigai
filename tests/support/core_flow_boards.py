"""0110-10-hf2: LOCAL fixture boards for the core-flow smoke (``tools/core_flow.py``, ``make test-core-flow``).

The smoke drives the installed ``gigai`` command on the operator-sized synthetic home
(``tests/support/operator_home.py``). ``gigai scout sources update`` is the one command of the flow that
asks the company boards, and the product's own fixture transport (``GIGAI_SCOUT_FIND_JOBS_TEST_HTTP=1``,
``bindings._test_provider_handler``) knows one Greenhouse board and no Lever board at all. This module is
that command with ONLY the board answers replaced: it swaps the fixture transport's handler for
:func:`handler` and then calls the installed package's own ``gigai.cli.cli``. No request leaves the process.

What a board answers:

* one of the home's own boards (``op00042co``): what it answered when the home was built (the stored body,
  byte for byte), and from round ``r`` on, for the :func:`changed_boards` of that round, one more posting:
  ``MATCHED_PER_ROUND`` of them with a title the default profile matches (so ``gigai scout new --yes`` has a
  few postings to assess), the others with a title no profile matches;
* any other board (the bundled catalog's, which an update with saved preferences adds to the watchlist): an
  empty board.

Run by the interpreter the wheel is installed in, from the repo root, so ``tests.support`` is this checkout
and ``gigai`` is the wheel::

    <venv>/bin/python -m tests.support.core_flow_boards update <round> <operator-home.json> scout sources update ...
    <venv>/bin/python -m tests.support.core_flow_boards a-day-later <operator-home.json>

``a-day-later`` moves every board's "last checked" stamp back 25 hours, with the product's own reader and
writer: an update leaves a board it checked in the last 24 hours alone, and the smoke runs two.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
import json
from pathlib import Path
import re
import sys

from tests.support import operator_home

#: Boards that post one more job in each round: over the 500 the read model's "preparing" lines start at.
CHANGED_PER_ROUND = 600
#: Of those, the boards whose new posting has a title the default profile matches.
MATCHED_PER_ROUND = 6
MATCHED_TITLE = "Staff Software Engineer, Core Flow"
OTHER_TITLE = "Office Coordinator, Core Flow"

_OWN_BOARD = re.compile(r"^op(\d{5})co$")


def changed_boards(round_number: int, companies: int) -> list[int]:
    """The boards that post one more job in round ``round_number`` (1, 2, ...): spread over the home, other boards each round."""

    count = min(CHANGED_PER_ROUND, companies)
    step = max(1, companies // count)
    return [((round_number - 1) + position * step) % companies for position in range(count)]


def extra_job(board: int, round_number: int, position: int) -> dict[str, object]:
    """Round ``round_number``'s new posting of ``board``, the ``position``-th changed board of that round."""

    slug = operator_home.slug_of(board)
    posting_id = f"{slug}-round{round_number}"
    return {
        "id": posting_id,
        "text": MATCHED_TITLE if position < MATCHED_PER_ROUND else OTHER_TITLE,
        "hostedUrl": f"https://jobs.lever.co/{slug}/{posting_id}",
        "categories": {"location": "Remote - United States"},
        "country": "US",
        "workplaceType": "remote",
        "descriptionPlain": f"Round {round_number} at {slug}. " + operator_home._ABOUT * operator_home.ABOUT_COPIES,
        "createdAt": int(datetime.now(UTC).timestamp() * 1000),
    }


def matched_urls(round_number: int, companies: int) -> list[str]:
    """The posting URLs of round ``round_number`` that the default profile matches."""

    return [str(extra_job(board, round_number, position)["hostedUrl"]) for position, board in enumerate(changed_boards(round_number, companies)[:MATCHED_PER_ROUND])]


class Boards:
    def __init__(self, built: operator_home.OperatorHome, round_number: int) -> None:
        from gigai.scout.find_jobs.ats_board_clients import BoardCache
        from gigai.scout.find_jobs.company_index import board_list_url

        self.built = built
        self.round = round_number
        self.asked = 0
        self._url = board_list_url
        self._cache = BoardCache(built.home_root / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)
        self._extras: dict[int, list[dict[str, object]]] = {}
        for earlier in range(1, round_number + 1):
            for position, board in enumerate(changed_boards(earlier, built.companies)):
                self._extras.setdefault(board, []).append(extra_job(board, earlier, position))

    @lru_cache(maxsize=None)
    def _plan(self) -> dict[int, tuple[int, int]]:
        plan, number = {}, 0
        for board, size in enumerate(operator_home.board_sizes(self.built.postings, self.built.companies)):
            plan[board] = (number, size)
            number += size
        return plan

    def lever(self, slug: str) -> bytes:
        own = _OWN_BOARD.match(slug)
        if own is None or int(own.group(1)) >= self.built.companies:
            return b"[]"
        board = int(own.group(1))
        extras = self._extras.get(board)
        if not extras:
            stored = self._cache.lookup("lever", self._url("lever", slug))
            if stored is not None:
                return stored.body  # an unchanged board answers exactly what it answered before
        first_number, size = self._plan()[board]
        seen = operator_home._seen_at(board)
        jobs = [operator_home._job(slug, position, first_number + position, seen, operator_home.MATCH_EVERY) for position in range(size)]
        return json.dumps(jobs + (extras or [])).encode("utf-8")

    def handler(self, request):  # an httpx.Request; httpx is the wheel's
        import httpx

        self.asked += 1
        host, path = request.url.host, request.url.path
        if request.method == "GET" and host == "api.lever.co" and path.startswith("/v0/postings/"):
            return httpx.Response(200, content=self.lever(path.rsplit("/", 1)[1]), headers={"content-type": "application/json"}, request=request)
        if request.method == "GET" and host in ("boards-api.greenhouse.io", "api.ashbyhq.com"):
            return httpx.Response(200, json={"jobs": []}, request=request)
        return httpx.Response(404, json={"error": "core-flow fixture: no such board"}, request=request)


def _built(record: str) -> operator_home.OperatorHome:
    built = operator_home.OperatorHome(**json.loads(Path(record).read_text(encoding="utf-8")))
    operator_home.assert_synthetic_root(Path(built.root))
    return built


def a_day_later(built: operator_home.OperatorHome) -> int:
    from gigai.scout.find_jobs.sources_update import board_cache_for_home

    cache = board_cache_for_home(built.home_root)
    index = cache.load_fetch_index()
    moved = 0
    for key, stamp in list(index.boards.items()):
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")) - timedelta(hours=25)
        index.boards[key] = when.astimezone(UTC).isoformat().replace("+00:00", "Z")
        moved += 1
    cache.store_fetch_index(index)
    return moved


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "a-day-later":
        print(json.dumps({"boards_aged": a_day_later(_built(argv[1]))}))
        return 0
    if len(argv) < 4 or argv[0] != "update":
        print(__doc__, file=sys.stderr)
        return 2
    boards = Boards(_built(argv[2]), int(argv[1]))

    from gigai.cli import cli
    from gigai.scout.find_jobs import bindings

    bindings._test_provider_handler = boards.handler
    return cli.main(args=argv[3:], prog_name="gigai", standalone_mode=True) or 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
