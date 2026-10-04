"""What the Jobs flows share: reading the rows the page shows, and clicking a chip with its checks.

No Playwright import and nothing of the product: the functions take the `ui` fixture (tests/ui/conftest.py).
"""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlsplit

LIST = "/api/postings"
WAITING = ("Loading", "Still loading", "Preparing")

ROWS_JS = """() => ({
  line: (document.querySelector('[data-role="postings-count"]') || {}).textContent || '',
  rows: Array.from(document.querySelectorAll('[data-testid="job-row"]')).map((row) => ({
    title: row.querySelector('[data-action="open-job"]').textContent,
    href: row.querySelector('[data-action="open-job"]').getAttribute('href'),
    state: row.dataset.state,
    isNew: !!row.querySelector('[data-role="new-tag"]'),
    profiles: Array.from(row.querySelectorAll('[data-testid="profile-chip"]')).map((tag) => tag.textContent),
    label: !!row.querySelector('[data-testid="scout-label-chip"]'),
  })),
})"""

#: The list shows exactly `count` rows and is neither loading nor being refreshed in place.
SETTLED_ROWS_JS = """(count) => document.querySelectorAll('[data-testid="job-row"]').length === count
  && !((document.querySelector('[data-role="postings-count"]') || {}).textContent || 'Loading').startsWith('Loading')
  && (document.querySelector('[data-role="postings-count"]') || {dataset: {}}).dataset.refreshing !== 'true'"""


def shown(ui) -> dict:
    """What the page shows: the count line and, for each row, its title, identity, state and tags."""

    page = ui.page.evaluate(ROWS_JS)
    for row in page["rows"]:
        row["job_identity"] = unquote((row.pop("href") or "").removeprefix("#/jobs/"))
    return page


def identities(rows: list[dict]) -> list[str]:
    return [row["job_identity"] for row in rows]


def count_line(ui) -> str:
    """The count line without what follows it ("· Clear filters")."""

    return (ui.page.locator('[data-role="postings-count"]').first.text_content() or "").split(" · ")[0].strip()


def click_chip(ui, selector: str, step: str, query: str, *, home: str, cpu_seconds: float, wall_seconds: float) -> tuple[dict, dict]:
    """Click a chip that changes the list; return (the server's answer to the page, what the page shows).

    The wait is on the list response itself, then on the rows that answer holds. Checked here: ONE list request, with
    this chip's query; the page shows that answer's rows, in its order; the server, asked the same query directly (as
    an agent would), counts the same postings (and lists the same ones, when they fit one page); then the two
    budgets. The page is compared with the answer IT got, never with a second read of the list: the background rank
    lane moves not-assessed rows while a test runs, so two reads a moment apart may differ in order.
    """

    before = f"before-{step}"
    ui.step(before)
    with ui.page.expect_response(lambda response: urlsplit(response.url).path == LIST) as answered:
        ui.page.click(selector)
    truth = answered.value.json()
    ui.page.wait_for_function(SETTLED_ROWS_JS, arg=truth["counts"]["shown"])
    ui.step(step)
    page = shown(ui)
    assert ui.requests_between(before, step, LIST) == 1
    assert parse_qs(urlsplit(answered.value.url).query) == parse_qs(f"{query}&limit=50"), answered.value.url
    assert identities(page["rows"]) == identities(truth["postings"]["rows"]), "the page does not show the rows the server answered with"
    direct = ui.server_json(f"{LIST}?{query}&limit=50")
    assert direct["counts"]["matched"] == truth["counts"]["matched"]
    if truth["counts"]["matched"] <= 50:
        assert sorted(identities(direct["postings"]["rows"])) == sorted(identities(page["rows"]))
    ui.cpu_budget(f"chip {step} ({home})", cpu_seconds, before, step)
    ui.wall_budget(f"chip {step} ({home})", wall_seconds, before, step)
    return truth, page


def pressed(ui, selector: str) -> bool:
    return ui.page.locator(selector).get_attribute("aria-pressed") == "true"
