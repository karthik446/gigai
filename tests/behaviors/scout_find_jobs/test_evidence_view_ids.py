"""0.1.11 N3 (SPEC 1.1): the evidence view with ids, the private note line, and an id through the model privacy strip.

- A model cannot pick ids it does not see: with ``ids`` the view carries each line's and each entry's master id in the
  trailing comment the master itself uses, within the same character cap.
- A note (0.1.11 N1b) reaches ONE text, this view, as a line of its own labelled ``private note``.  It is never in a
  selection's markdown, a candidate set, a job resume or a suggestion record.
- P8 of the spec, which was NOT PROVEN: ``resume_privacy.model_resume`` redacts contact-shaped text inside a line, and
  an id is a prefix plus 6 hex characters that can be all digits.  10,000 generated ids go through it here.
"""

from __future__ import annotations

from datetime import date
import random
import re

from gigai.scout import master_selection as ms
from gigai.scout import pick, suggestions
from gigai.scout.find_jobs.assess_contracts import AssessmentBody, AssessmentPick
from gigai.scout.find_jobs.contracts import MatrixStatus, RequirementClass, RequirementMatrixRow, Verdict
from gigai.scout.master_resume import parse_master
from gigai.scout.resume_privacy import model_resume

TODAY = date(2026, 10, 5)
MASTER_TEXT = """## Summary

- Platform engineer with ten years on Go services and Kubernetes. <!-- id:sum-a -->

## Experience

### Alpha Systems <!-- id:r-alpha -->
Staff Engineer | Jan 2023 - Present
- Built the Go control plane for the deploy platform. <!-- id:a1 -->
- Cut deploy time 40% by rebuilding the rollout controller in Go. <!-- id:a2 -->
- Ran the Kubernetes upgrade across 12 clusters with no downtime. <!-- id:a3 -->
- Wrote the on-call handbook the platform team uses. <!-- id:a4 -->

### Beta Labs <!-- id:r-beta -->
Senior Engineer | Mar 2020 - Dec 2022
- Operated Kafka for 30 services carrying 2 billion messages a day. <!-- id:b1 -->
- Wrote Terraform modules for the staging clusters. <!-- id:b2 -->
- Built the internal status page. <!-- id:b3 -->
- Kept the release checklist up to date. <!-- id:b4 -->

## Skills

- Go, Kubernetes, Kafka, Terraform <!-- id:s-1 -->

## Education

### Varnholt Technical University <!-- id:e-var -->
B.S. Computer Science | 2005 - 2009

## Other

- Speaker at a platform engineering meetup. <!-- id:o1 -->
"""
PROFILE = ms.SelectionProfile(titles=("Staff Platform Engineer",))
POSTING = ms.SelectionPosting("Staff Platform Engineer", "Requirements:\n- Go in production\n- Kafka at scale\n", "Acme", "Remote")
LINE_NOTE = "agentic roles: lead with this, keep it above the upgrade line"
ENTRY_NOTE = "shorter version: keep the first two bullets"
_ID = re.compile(r" <!-- id:(\S+) -->")


def _master(*, notes: bool = False):
    master = parse_master(MASTER_TEXT)
    if notes:
        # 0.1.11 N1b's fields (``MasterItem.note`` / ``MasterEntry.note``, read by ``master_resume.note_of``), set
        # here directly so this test also runs on a tree whose reader has no note comment yet.
        object.__setattr__(master.items["a2"], "note", LINE_NOTE)
        object.__setattr__(master.entries["r-beta"], "note", ENTRY_NOTE)
    return master


def test_the_view_with_ids_shows_every_line_and_entry_with_its_id_and_is_the_same_view_otherwise() -> None:
    master = _master()
    plain = ms.evidence_view(master, PROFILE, POSTING, today=TODAY)
    with_ids = ms.evidence_view(master, PROFILE, POSTING, today=TODAY, ids=True)
    assert not plain.ids and "<!--" not in plain.markdown and with_ids.ids and with_ids.notes == 0
    # The same lines (this master fits the cap either way), and the markdown without its id comments is the plain one.
    assert with_ids.item_ids() == plain.item_ids() and _ID.sub("", with_ids.markdown) == plain.markdown
    ids = _ID.findall(with_ids.markdown)
    assert ids == ["sum-a", "r-alpha", "a1", "a2", "a3", "a4", "r-beta", "b1", "b2", "b3", "b4", "e-var", "o1"]
    assert set(with_ids.item_ids()) == {"sum-a", "a1", "a2", "a3", "a4", "b1", "b2", "b3", "b4", "o1"}  # what a row's sources and a pick may name
    assert "- Cut deploy time 40% by rebuilding the rollout controller in Go. <!-- id:a2 -->" in with_ids.markdown.splitlines()
    assert "### Alpha Systems <!-- id:r-alpha -->" in with_ids.markdown.splitlines()
    assert "- Go, Kubernetes, Kafka, Terraform" in with_ids.markdown.splitlines()  # the Skills line is code's: no id, never picked


def test_the_cap_is_measured_on_the_text_with_ids() -> None:
    master = _master()
    cap = len(ms.evidence_view(master, PROFILE, POSTING, today=TODAY).markdown)
    tight = ms.evidence_view(master, PROFILE, POSTING, today=TODAY, cap=cap, ids=True)
    assert tight.within_cap and len(tight.markdown) <= cap and 0 < tight.bullets < 8  # the ids cost room: fewer bullets, never over the cap
    assert tight.bullets == len([line for line in tight.markdown.splitlines() if line.startswith("- ") and "<!-- id:" in line]) - len(tight.summary) - len(tight.other)


def test_a_note_reaches_the_prompts_view_only_as_a_labelled_private_note_line() -> None:
    master = _master(notes=True)
    view = ms.evidence_view(master, PROFILE, POSTING, today=TODAY, ids=True)
    lines = view.markdown.splitlines()
    assert view.notes == 2
    # A line of its own, labelled, right under the line it belongs to; an entry's under its heading lines.
    at = lines.index("- Cut deploy time 40% by rebuilding the rollout controller in Go. <!-- id:a2 -->")
    assert lines[at + 1] == f"<!-- private note: {LINE_NOTE} -->"
    heading = lines.index("### Beta Labs <!-- id:r-beta -->")
    assert lines[heading + 1 : heading + 3] == ["Senior Engineer | Mar 2020 - Dec 2022", f"<!-- private note: {ENTRY_NOTE} -->"]
    assert [line for line in lines if "note" in line.lower()] == [f"<!-- private note: {LINE_NOTE} -->", f"<!-- private note: {ENTRY_NOTE} -->"]
    # The strip every model-bound resume goes through keeps the label and the note together.
    assert f"<!-- private note: {LINE_NOTE} -->" in model_resume(view.markdown).text.splitlines()


def test_a_note_is_never_in_a_resume_a_candidate_set_a_job_resume_or_a_record() -> None:
    master = _master(notes=True)
    secrets = (LINE_NOTE, ENTRY_NOTE, "private note")

    def clean(text: str) -> bool:
        return not any(secret in text for secret in secrets)

    # The view an assessment read before ids existed, and every text that is a resume or can become one.
    assert clean(ms.evidence_view(master, PROFILE, POSTING, today=TODAY).markdown)
    selected = ms.select(master, PROFILE, POSTING, today=TODAY)
    assert clean(selected.markdown) and clean(selected.markdown_with_ids)
    assert clean(ms.render_selection(master, ["sum-a", "r-alpha", "a2", "r-beta", "b1"], master.skills(), ids=True))
    rows = (
        RequirementMatrixRow("Go in production", (), MatrixStatus.MET, RequirementClass.HARD, id="req-000001", class_basis="Requirements", sources=("a2",)),
        RequirementMatrixRow("Kafka at scale", (), MatrixStatus.MET, RequirementClass.ASKABLE, id="req-000002", class_basis="Requirements", sources=("b1",)),
    )
    for chosen in (AssessmentPick("sum-a", ("experience", "projects"), ("a2", "a1", "a3", "a4", "b1", "b2", "b3", "b4", "o1")), None):
        body = AssessmentBody(rows, (), (), verdict=Verdict.MATCHED_ABOVE_THRESHOLD, pick=chosen)
        settled = pick.settle(master, body, None, TODAY, profile=PROFILE, posting=POSTING)
        assert settled.picked_by == ("model" if chosen else "code") and "a2" in settled.printed
        assert clean(settled.markdown) and clean(settled.candidates.markdown) and clean(repr(settled.result.to_json()))
        assert clean(repr(settled.record.to_json()))
        assert clean(repr(settled.selection_json(made_at="t", result_digest=None, master_revision_id=None)))
        assert clean(repr([row.to_json() for row in suggestions.check_selection(suggestions.requirement_rows(rows), settled.printed).rows]))


# --- P8: an id comment through the model privacy strip ------------------------------------------------------------------

_PREFIXES = ("b", "sum", "o", "r", "p", "e", "s")
_ENDINGS = (
    "Cut deploy time 40%", "Served 2,400 clinics in 2019", "Held a p95 of 140 ms", "Ran 12 clusters on 3 clouds", "Grew the team from 4 to 11",
    "Handled 415 requests per second", "Reached 99.95", "Shipped in 2021 and 2022", "Moved 150 million messages a day", "Owned the roadmap",
    "Scored 2,200 cases", "Kept 555 1234 dashboards", "Paged 6 engineers", "Passed the audit of 2018 -", "Billed 20 million events (2017)",
)


def _generated_ids(count: int = 10_000) -> list[str]:
    rng = random.Random(20261005)
    ids: list[str] = []
    seen: set[str] = set()
    while len(ids) < count:
        kind = len(ids) % 5
        if kind == 0:
            body = f"{rng.randrange(1_000_000):06d}"  # all digits: the case the spec worried about
        elif kind == 1:
            body = "".join(rng.choice("0123456789") for _ in range(5)) + rng.choice("abcdef")
        else:
            body = f"{rng.randrange(16**6):06x}"
        item_id = f"{rng.choice(_PREFIXES)}-{body}" + (f"-{rng.randrange(2, 13)}" if kind == 4 else "")  # a collision suffix, as the master gives one
        if item_id not in seen:
            seen.add(item_id)
            ids.append(item_id)
    return ids


def test_ten_thousand_generated_ids_pass_the_model_privacy_strip_unaltered() -> None:
    ids = _generated_ids()
    assert len(ids) == len(set(ids)) == 10_000 and sum(item_id.split("-")[1].isdigit() for item_id in ids) >= 2_000
    lines = ["## Experience", ""]
    for number, item_id in enumerate(ids):
        if number % 25 == 0:
            lines += ["", f"### Employer {number // 25} <!-- id:{item_id} -->", f"Staff Engineer | Jan {2000 + number % 24} - Dec {2001 + number % 24}"]
        else:
            lines.append(f"- {_ENDINGS[number % len(_ENDINGS)]} <!-- id:{item_id} -->")
    text = "\n".join(lines) + "\n"
    stripped = model_resume(text)
    altered = [item_id for item_id in ids if f"<!-- id:{item_id} -->" not in stripped.text]
    assert altered == [], f"{len(altered)} of 10,000 id comments were altered by the privacy strip, e.g. {altered[:5]}"
    # Stronger: nothing at all was redacted or withheld, so the block reaches the prompt byte for byte.
    assert stripped.text == text and stripped.withheld == frozenset()
    assert _ID.findall(stripped.text) == ids
