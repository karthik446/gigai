"""The pick fixture's stored resume as a pick BEFORE 0.1.11.5 left it: some bullets shown, the others cut for length.

A pick of 0.1.11.5 counts no page: it holds its best bullets with no length record, so nothing is cut for length and
Restore has nothing to put back. The tests of Restore (``tests/ui/test_job_page_resume_points.py``,
``tests/api_e2e/test_ui_resume_points_model.py``) seed the old shape with this, on the home of the pick fixture
(``tests/behaviors/scout_find_jobs/test_pick_header_room.py``), after its job is assessed. Synthetic lines only.
"""

from __future__ import annotations

from dataclasses import replace

from gigai.scout import tailor_master as tm
from gigai.scout.tailor_length import fit_by_cuts
from gigai.scout.tailored_resume import read_tailored_resume, render_markdown, save_tailor_response, tailored_resume_path

#: How many bullets the resume shows once it is stored the way a pick before 0.1.11.5 stored it.
KEPT = 12


def stored_as_before_0_1_11_5(fixture, job: str) -> None:  # noqa: ANN001
    """The job's stored resume as a pick BEFORE 0.1.11.5 left it: ``KEPT`` bullets shown, the others cut for length.

    A pick of 0.1.11.5 holds its 20 best bullets with no length record. The fit of before (``fit_by_cuts``: the last
    lines of each role in turn, a role never emptied) is applied to it with a measure that COUNTS bullets, and the
    record says so the way it did: the cut lines on ``result.length`` (Restore puts them back) and under Left out."""

    path = tailored_resume_path(fixture.home_root, fixture.target, fixture.default_profile_id, job)
    stored = read_tailored_resume(path)
    assert stored is not None and stored.result.length is None and stored.selection is not None, "a pick of 0.1.11.5 carries no length record"
    roles = [list(entry.bullets) for section in stored.result.sections if section.heading == "experience" for entry in section.entries]
    cuts: list[tuple[str, str]] = []
    while any(len(lines) > 1 for lines in roles):
        cuts += [("bullet", lines.pop().id) for lines in roles if len(lines) > 1]

    def count(result) -> int:  # noqa: ANN001
        return sum(len(entry.bullets) for section in result.sections for entry in section.entries)

    fitted = fit_by_cuts(stored.result, cuts, measure=lambda result: 2 if count(result) <= KEPT else 3, max_pages=2)
    assert fitted.length is not None and fitted.length.status == "cut" and count(fitted) == KEPT < count(stored.result)
    gone = {tm.line_item_id(line) for role in fitted.length.trimmed for line in role.bullets}
    selection = stored.selection
    why = ("cut_lowest_value", "cut for length: lowest value for this posting")
    selection = replace(
        selection,
        picked=tuple(line for line in selection.picked if line.id not in gone),
        left_out=(*selection.left_out, *(tm.SelectedLine(line.id, *why) for line in selection.picked if line.id in gone)),
        cut_for_length=tuple(tm.SelectionCut(line.id, "bullet", *why) for line in selection.picked if line.id in gone),
    )
    save_tailor_response(replace(stored, result=fitted, markdown=render_markdown(fitted), selection=selection), home_root=fixture.home_root)
