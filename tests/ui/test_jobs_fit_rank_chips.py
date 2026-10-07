"""0.1.11.5 UI-01: the fit and rank chips on the Jobs list, and the box of an assessed job page.

Real server and page (the demo home, no model call, no network). The numbers asked for are the ones the server serves
on the rows (`fit`, `assessment.met` / `.requirements`, `rank_score`): no read is added for the chips.

- every row has its chips under the title, beside the profile tags: an assessed row "Fit 92% · 19/22" and "Rank 92",
  a not assessed row the rank chip alone; the middle column keeps the state and no number;
- the assessed job page has the box top right: the fit ("92%", "19 of 22 requirements") and the rank under it.
"""

from __future__ import annotations

import json
from urllib.parse import quote

import pytest

from tests.ui.evidence import shot

pytestmark = pytest.mark.ui

ROW = '[data-testid="job-row"]'
TAGS = '[data-role="profile-tags"]'


def _served(ui) -> dict[str, dict]:
    rows = ui.server_json("/api/postings?limit=200")["postings"]["rows"]
    return {row["job_identity"]: row for row in rows}


def _ranked(ui, served: dict[str, dict]) -> dict[str, int]:
    """The demo home has no ranked posting (ranking is a model call). Rank numbers are put on the rows the server
    served, one distinct number each, so the chips and the box have a rank to show; every other field stays the server's."""

    ranks = {identity: 92 - place for place, identity in enumerate(served)}

    def postings(route) -> None:
        response = route.fetch()
        body = response.json()
        found = (body.get("postings") or {}).get("rows")
        if found:
            for row in found:
                row["rank_score"] = ranks.get(row["job_identity"], 50)
        route.fulfill(response=response, body=json.dumps(body))

    ui.page.route("**/api/postings?*", postings)
    return ranks


def _listed(ui) -> list[dict]:
    return ui.page.locator(ROW).evaluate_all(
        """(rows) => rows.map((row) => ({
          href: row.querySelector('[data-action="open-job"]').getAttribute('href'),
          state: row.dataset.state,
          fit: Array.from(row.querySelectorAll('[data-role="profile-tags"] [data-testid="fit-chip"]')).map((chip) => chip.textContent),
          fitTone: Array.from(row.querySelectorAll('[data-role="profile-tags"] [data-testid="fit-chip"]')).map((chip) => chip.dataset.tone),
          fitGreen: Array.from(row.querySelectorAll('[data-role="profile-tags"] [data-testid="fit-chip"]')).map((chip) => chip.classList.contains('tone-ok')),
          fitColours: Array.from(row.querySelectorAll('[data-role="profile-tags"] [data-testid="fit-chip"]')).map((chip) => getComputedStyle(chip).color),
          rankTone: Array.from(row.querySelectorAll('[data-role="profile-tags"] [data-testid="rank-chip"]')).map((chip) => chip.dataset.tone),
          rankColours: Array.from(row.querySelectorAll('[data-role="profile-tags"] [data-testid="rank-chip"]')).map((chip) => getComputedStyle(chip).color),
          rank: Array.from(row.querySelectorAll('[data-role="profile-tags"] [data-testid="rank-chip"]')).map((chip) => chip.textContent),
          score: row.querySelector('[data-role="score"]').textContent,
        }))"""
    )


def test_every_row_has_its_fit_and_rank_chips_with_the_servers_numbers(ui, scout_server) -> None:
    served = _served(ui)
    ranks = _ranked(ui, served)
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    ui.step("listed")
    listed = _listed(ui)
    assert listed, "the demo home lists no row"
    seen = {"assessed": 0, "not_assessed": 0}
    tones: dict[str, set[str]] = {}
    for entry in listed:
        job = next(row for identity, row in served.items() if entry["href"] == "#/jobs/" + quote(identity, safe=""))
        if job["state"] == "not_assessed":
            seen["not_assessed"] += 1
            assert entry["fit"] == [], entry
            assert "not assessed" in entry["score"]
        else:
            seen["assessed"] += 1
            if job["thin_posting"]:
                assert entry["fit"] == [], entry
            else:
                met, total = job["assessment"]["met"], job["assessment"]["requirements"]
                assert entry["fit"] == [f"Fit {job['fit']}% · {met}/{total}"], (entry, job["score_text"])
                # 0.1.11.5 (B1 review): green ONLY on a matched row; neutral, like the rank chip, on every other state
                # (needs your answers at Fit 50% must not read as "good fit").
                green = job["state"] in ("matched", "tailored")
                tones.setdefault(job["state"], set()).add(entry["fitTone"][0])
                assert entry["fitTone"] == ["ok" if green else "plain"] and entry["fitGreen"] == [green], (job["state"], entry)
                assert (entry["fitColours"] == entry["rankColours"]) is (not green), (job["state"], entry)
        assert entry["rankTone"] == ["plain"], entry
        assert entry["rank"] == [f"Rank {ranks[job['job_identity']]}"], entry
        # The middle column keeps the state, never the numbers the chips carry.
        assert "%" not in entry["score"] and "rank" not in entry["score"].lower() and " of " not in entry["score"], entry["score"]
    assert seen["assessed"] and seen["not_assessed"], seen
    assert any(state not in ("matched", "tailored") for state in tones), f"the home lists no assessed row that is not a match: {tones}"
    ui.page.evaluate("() => document.querySelector('[data-testid=\"job-row\"]').scrollIntoView()")
    shot(ui, "jobs-list-fit-and-rank-chips")
    ui.assert_clean()


def test_an_assessed_job_page_has_the_fit_and_rank_box(ui, scout_server) -> None:
    served = _served(ui)
    ranks = _ranked(ui, served)
    # An assessed row with a fit; the demo's hero job is a thin posting (no fit), so it is not the one.
    identity, job = next(((key, row) for key, row in served.items() if isinstance(row["fit"], int)), (None, None))
    assert job is not None, [(row["state"], row["score_text"]) for row in served.values()]
    ui.goto("/#/jobs/" + quote(identity, safe=""))
    ui.wait_for_job_page()
    ui.settle()
    box = ui.page.locator('.job-header [data-testid="score-box"]')
    box.wait_for()
    assert (box.locator('[data-role="score-box-fit"]').text_content() or "").strip() == f"{job['fit']}%"
    assert f"{job['assessment']['met']} of {job['assessment']['requirements']} requirements" in (box.text_content() or "")
    assert (box.locator('[data-role="score-box-rank"]').text_content() or "").strip() == f"Rank {ranks[identity]}"
    shot(ui, "job-page-assessed-fit-and-rank-box")
    ui.assert_clean()
