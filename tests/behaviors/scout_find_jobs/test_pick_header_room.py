"""The pick and the page (0.1.11.3 item 15, rewritten for 0.1.11.5 item 1c): the pick holds its 20 bullets and counts
no page; the PDF says its real pages; Shorten leaves out one more bullet.

Until 0.1.11.5 the pick was fitted to 2 pages with room kept for the PDF's header (``sel-5``), and this file pinned
"the pick's pages are the PDF's pages", and "Shorten automatically". The pick no longer knows the page limit: the user
fits the page with the spacing of the job's preview, or removes a point there; Shorten is RETIRED. What is pinned now, on synthetic data (a master of 56 long lines, a pick of all of them):

- the pick holds exactly ``MAX_PICK_BULLETS`` bullets, every must-have line among them, every role listed (an old role
  by at most 3 lines), nothing "cut for length", no page count on the record;
- the PDF of it at the automatic fit (the CLI with the person's header file, and ``POST /api/tailored-resumes/pdf``
  with the form) reports its REAL page count, here 3: the plain sentence names the way out, and the resume is still
  READY (the page count is never a reason);
- a re-pick of a job whose stored pick is shorter (made under a smaller cap, as picks before 0.1.11.5 stopped at 14)
  holds the 20;
- must-cover lines are the last to go under a smaller cap, and when one does the pick says so (a conflict, not ready);
- a re-pick leaves a resume the user edited untouched: the new pick waits as the suggested one;
- ``gigai scout resume pick --shorten`` and ``POST /api/job-resumes/pick`` ``shorten`` are RETIRED (the coordinator's
  decision in 0.1.11.5: the slider and Remove-a-point replace them): refused with ``shorten_retired`` and one plain
  sentence, nothing read, nothing written.

A fake model (the pipeline fixture's scripted one), one synthetic profile, an invented master. The pipeline is off.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import io
import json
import logging
from pathlib import Path
import re
import threading

from click.testing import CliRunner
import httpx
import pytest
from pypdf import PdfReader

from gigai.scout import assessment_core, pick, postings, resume_pdf, suggestions
from gigai.scout import tailor_master as tm
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.find_jobs.contracts import normalize_url
from gigai.scout.master_selection import MAX_PICK_BULLETS
from gigai.scout.master_store import import_master, load_master
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.quick_assess import run_quick_assessment
from gigai.scout.resume_display import DisplaySettings, save_display
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import TailorEdit, read_tailored_resume, save_tailor_response, tailored_resume_path

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.behaviors.scout_find_jobs.test_assessment_v9_flow import V9_PARAGRAPHS
from tests.support.pipeline_fixtures import build_pipeline_fixture
from tests.support.posting_fixtures import NOW, PostingsFixture, days_ago, job_url, lever_job

STAMP = datetime(2026, 10, 6, tzinfo=timezone.utc)
_SLUG = "thistledown"
_URL = job_url(_SLUG, 1)
_JOB = normalize_url(_URL)
_POSTING = (
    "Staff Software Engineer, Fullstack\n\nRequirements:\n- 5+ years of Python in production\n- Kubernetes\n- Terraform modules for every cluster\n"
    "- React in production\n\nYou will own the storefront services.\n"
)
REQUIREMENTS = ("5+ years of Python in production", "Kubernetes", "Terraform modules for every cluster", "React in production")
ROLES = (("Thistledown Market", "2024 - Present"), ("Harborlight Health", "2022 - 2024"), ("Quillshire Freight", "2020 - 2022"), ("Lanternfish Labs", "2013 - 2015"))
#: The last role ended long ago: an OLD role (at most 3 lines; listed by its heading when none is left; a recent role always keeps its best line).
OLD_ROLE_LINE = "Shipped the React storefront used by two million shoppers."
LINES_PER_ROLE = 14
#: The lines the assessment's must-have rows rest on: each the LAST line of its role, where a cap by rank alone leaves it out first.
PYTHON_LINE = "Built Python services for six years; cut p99 latency by 40%."
KUBERNETES_LINE = "Operated Kubernetes clusters backed by PostgreSQL."
TERRAFORM_LINE = "Wrote the Terraform modules every Kubernetes cluster is built from."
REACT_LINE = "Shipped the React storefront used by two million shoppers."
MUST_LINES = (PYTHON_LINE, KUBERNETES_LINE, TERRAFORM_LINE, REACT_LINE)
SKILLS = "Python, Go, TypeScript, React, Kubernetes, PostgreSQL, Terraform, AWS, Kafka, Redis, Airflow, Docker, Helm, Prometheus, Grafana, GraphQL, Node.js, CI/CD"
TITLE = "Staff Software Engineer, Fullstack"
#: A header at its largest: a contact line that is too long for one line even set smaller.
FORM = {
    "name": "Zora Quillfeather", "email": "zora.quillfeather@example.invalid", "phone": "+1 (555) 010-0142", "location": "Nowhere Springs, Colorado",
    "work_authorization": "VISA: H1B", "linkedin": "linkedin.com/in/zora-quillfeather-staff-engineer",
    "links": [{"label": "GitHub", "url": "github.com/zora-quillfeather"}],
}
HEADER_FILE = {key: value for key, value in FORM.items() if key != "linkedin"} | {"links": [*FORM["links"], {"label": "LinkedIn", "url": FORM["linkedin"]}]}
_INTERNAL = re.compile(r"\bb-[0-9a-z]{4,}\b|req-[0-9a-f]+|max_pages|max_bullets|header_lines|cut_for_length|mandatory_evidence|settle|Traceback")


_NUMBERS = re.compile(r"\s*<!-- R\d+ -->")
_NUMBER = re.compile(r"\s*<!-- R\d+ -->\Z")  # a stored resume's markdown numbers each line


def _line(role: int, line: int) -> str:
    # Long on purpose (about six printed lines): a pick of twenty bullets does not print on 2 pages at any spacing.
    return (
        f"Role {role} line {line}: built the scheduling and billing pipeline that moved {line + 3}0 million records a day across four regions with no data "
        "loss, then ran the migration of every tenant onto it over nine months with the old and the new system reconciled row by row each night, "
        "and wrote the runbook, the dashboards and the paging rules the on-call engineers of three teams have used since the first week; "
        "trained two new teams on it, handed the service over with no open incident, and stayed on its review rota for the following year, "
        "during which the monthly close fell from six working days to two and no customer invoice had to be reissued."
    )


def master_markdown() -> str:
    out = ["## Summary", "", "- Engineer with twelve years building storefronts, data platforms and the services behind them.", "", "## Experience", ""]
    for role, (company, dates) in enumerate(ROLES):
        out += [f"### {company}", f"Staff Engineer | {dates}", ""]
        out += [f"- {_line(role, line)}" for line in range(LINES_PER_ROLE - 1)]
        out += [f"- {MUST_LINES[role]}", ""]
    out += ["## Skills", "", f"- {SKILLS}", "", "## Education", "", "### Example State University", "BS Computer Science | 2010 - 2014", ""]
    return "\n".join(out)


MASTER = master_markdown()
RESUME = "## Experience\n\n### Thistledown Market\nStaff Engineer | 2024 - Present\n\n- " + PYTHON_LINE + "\n\n## Skills\n\n- Python\n"


@pytest.fixture(autouse=True)
def _pipeline_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PIPELINE_ENV, "off")


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> PostingsFixture:
    shipped = assessment_core.load_assess_instructions()
    for name in ("id_example", "note_example", "pick_lines", "requirements"):
        shipped = shipped.replace("{{" + name + "}}", "x")
    # The prompt shows each line's id and asks for a pick (the shipped v9 paragraphs, in short): a Matched answer keeps its pick.
    monkeypatch.setattr(assessment_core, "load_assess_instructions", lambda: shipped + V9_PARAGRAPHS)
    base = build_pipeline_fixture(tmp_path, monkeypatch, base=False, resume=RESUME)
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    fixture = PostingsFixture(base, second_profile_id="", deleted_profile_id=None)
    fixture.seed(_SLUG, [lever_job(_SLUG, 1, text=_POSTING)], seen_at=days_ago(1))
    postings.refresh(fixture.home_root, fixture.target, now=NOW)
    source = tmp_path / "master.md"
    source.write_text(MASTER, encoding="utf-8")
    assert import_master(home_root=fixture.home_root, target=fixture.target, source=source, gig_id=base.gig.resolved.gig_id).status == "created"
    caplog.set_level(logging.WARNING, logger="gigai.scout.server")
    return fixture


def _master(fx: PostingsFixture):
    stored = load_master(home_root=fx.home_root, target=fx.target, gig_id=fx.base.gig.resolved.gig_id)
    assert stored is not None
    return stored.master


def _ids(fx: PostingsFixture) -> dict[str, str]:
    return {item.text: item.id for item in _master(fx).items.values()}


def _answer(fx: PostingsFixture, *, must: tuple[str, ...] = MUST_LINES) -> str:
    """A Matched answer: each requirement a met must-have row resting on ONE line, and a pick of every line of the master in its order."""

    ids = _ids(fx)
    rows = [
        {"requirement": requirement, "class": "hard", "status": "met", "resume_evidence": [line], "sources": [ids[line]], "class_basis": f"Requirements: {requirement}"}
        for requirement, line in zip(REQUIREMENTS, must)
    ]
    bullets = [item.id for item in _master(fx).items.values() if item.kind == "bullet"]
    chosen = {"summary": None, "section_order": ["experience", "projects"], "lines": bullets}
    return json.dumps({"verdict": "matched_above_threshold", "matrix": rows, "questions": [], "suggestions": [], "pick": chosen, "not_a_match_reason": None})


def _assess(fx: PostingsFixture, answer: str | None = None) -> None:
    fx.base.model.assessed = answer or _answer(fx)
    fx.base.model.assess_prompts.clear()
    stored = run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=_URL), resume=AssessResumeInput(profile_id=fx.default_profile_id)),
        home_root=fx.home_root, target=fx.target, config=fixture_config(fx.home_root),
    )
    assert stored.result.verdict.value == "matched_above_threshold" and stored.resume_gate.decision == "suggest"


def _assess_under(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch, cap: int, *, answer: str | None = None) -> None:
    """A stored pick made under another cap than the shipped one (a pick of before 0.1.11.5 stopped at about 14 bullets)."""

    with monkeypatch.context() as old:
        old.setitem(pick.settle_and_store.__kwdefaults__, "max_bullets", cap)
        _assess(fx, answer)


def _no_layout_in_the_pick(monkeypatch: pytest.MonkeyPatch) -> None:
    """From here on the page ESTIMATES raise: a pick, a re-pick and a shorten lay out nothing (the PDF's own render is untouched)."""

    def refuse(*_args: object, **_kwargs: object):
        raise AssertionError("the pick must not lay out a page")

    for name in ("pages_at", "measure_markdown", "fewest_pages", "_estimate"):
        monkeypatch.setattr(resume_pdf, name, refuse)
    monkeypatch.setattr(tm, "measure_pages", refuse)


def _bullets(markdown: str) -> list[str]:
    """The bullets a resume's markdown shows under its roles."""

    body = markdown.split("## Experience", 1)[1].split("## Skills", 1)[0]
    return [_NUMBER.sub("", line[2:]) for line in body.splitlines() if line.startswith("- ")]


def _cli(fx: PostingsFixture, *args: str, as_json: bool = True):
    return CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), *(["--json"] if as_json else [])])


def _ok(fx: PostingsFixture, *args: str) -> dict:
    result = _cli(fx, *args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _refused(fx: PostingsFixture, *args: str) -> dict[str, object]:
    result = _cli(fx, *args)
    assert result.exit_code == 1, result.output
    error = json.loads(result.output.strip().splitlines()[-1])["error"]
    assert not _INTERNAL.search(str(error["message"])), error["message"]
    return error


def _view(fx: PostingsFixture) -> dict:
    return _ok(fx, "resume", "pick", "--job-url", _URL)


def _pages(pdf: bytes) -> list[str]:
    return [page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages]


def _header_file(tmp_path: Path) -> Path:
    path = tmp_path / "header.json"
    path.write_text(json.dumps(HEADER_FILE), encoding="utf-8")
    path.chmod(0o600)
    return path


def _cli_pdf(fx: PostingsFixture, tmp_path: Path, name: str = "cli.pdf") -> tuple[dict, list[str]]:
    out = tmp_path / "out" / name
    payload = _ok(fx, "resume", "pdf", "--job-url", _URL, "--header", str(_header_file(tmp_path)), "--out", str(out))
    assert payload["header"] is True
    return payload, _pages(out.read_bytes())


class _Server:
    def __init__(self, fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve

        monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
        monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
        self.server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{self.server.server_address[1]}", timeout=120)
        self.key = {"profile_id": fx.default_profile_id, "job_identity": _JOB}

    def pdf(self):
        return self.client.post("/api/tailored-resumes/pdf", json={**self.key, "header": FORM})

    def close(self) -> None:
        self.client.close()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def server(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch):
    made = _Server(fx, monkeypatch)
    yield made
    made.close()


def _header_lines(page: str) -> list[str]:
    return [line.strip() for line in page.split("SUMMARY")[0].splitlines() if line.strip()]


# --- the pick holds its bullets and counts no page; the PDF says its real pages ------------------------------------------


def test_a_pick_holds_twenty_bullets_and_the_pdf_says_its_real_pages(
    fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server: _Server,
) -> None:
    _no_layout_in_the_pick(monkeypatch)
    _assess(fx)
    view = _view(fx)
    counts = view["resume"]["counts"]
    markdown = view["resume"]["markdown"]
    # The pick: the model's (all 56 lines of the master) capped at 20 bullets, every must-have line among them.
    assert view["picked"]["picked_by"] == "model" and view["conflicts"] == []
    assert len(_bullets(markdown)) == MAX_PICK_BULLETS == 20 and all(line in markdown for line in MUST_LINES)
    # Nothing is "cut for length" and no page was counted: the record keeps the fields, informational.
    assert counts["cut_for_length"] == 0 and (view["picked"]["pages"], view["picked"]["max_pages"]) == (None, 2)
    stored = read_tailored_resume(tailored_resume_path(fx.home_root, fx.target, fx.default_profile_id, _JOB))
    assert stored is not None and stored.result.length is None and stored.selection is not None
    left = {line.id: line.code for line in stored.selection.left_out}
    assert sorted(set(left.values())) == ["old_role_limit", "over_cap", "summary_other_variant"] or sorted(set(left.values())) == ["old_role_limit", "over_cap"]
    # Every role is on the page; the old one by at most 3 lines, its must-have line among them.
    assert all(f"### {company}" in markdown for company, _dates in ROLES)
    old_role = markdown.split("### Lanternfish Labs", 1)[1].split("## ", 1)[0]
    assert 1 <= len(_bullets("## Experience" + old_role + "## Skills")) <= 3 and OLD_ROLE_LINE in old_role

    # THE END OUTCOME, the CLI with the person's header file at the automatic fit: the PDF's REAL pages, said plainly.
    payload, pages = _cli_pdf(fx, tmp_path)
    assert len(_header_lines(pages[0])) == 3, f"the header is not at its largest (name, two contact lines): {_header_lines(pages[0])}"
    assert len(pages) == payload["pages"] == 3, f"the fixture is not a pick that prints on 3 pages: {len(pages)}"
    assert payload["note"] and payload["note"].startswith("This resume takes 3 pages") and "remove a point" in payload["note"]

    # ... and the page's Generate PDF, over HTTP, with the form: the same pages.
    response = server.pdf()
    assert response.status_code == 200, response.text
    served = _pages(response.content)
    assert len(_header_lines(served[0])) == 3 and len(served) == 3 and response.headers["x-gigai-pages"] == "3"
    assert "x-gigai-fit-note" in response.headers

    # A pick that prints on 3 pages is still READY: the page count is the user's to settle, never a reason.
    assert view["gate"]["decision"] == "suggest" and view["gate"]["ready"] is True and view["gate"]["reasons"] == []
    record = suggestions.read_suggestions(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    assert record is not None and record.selection["conflicts"] == [] and record.selection["pages"] is None and record.selection["max_bullets"] == 20

    # A saved title is one more header line: the same pick, the same pages.
    save_display(fx.home_root, DisplaySettings(titles={fx.default_profile_id: TITLE}))
    titled = server.pdf()
    assert _header_lines(_pages(titled.content)[0])[1] == TITLE and len(_pages(titled.content)) == 3


def test_a_re_pick_of_a_job_picked_under_a_smaller_cap_holds_the_twenty(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stored pick stays as it is until it is picked again; ``resume pick --refresh`` then holds up to 20 bullets (they stopped at 14)."""

    _assess_under(fx, monkeypatch, 14)
    before = _view(fx)
    assert len(_bullets(before["resume"]["markdown"])) == 14 and all(line in before["resume"]["markdown"] for line in MUST_LINES)
    _no_layout_in_the_pick(monkeypatch)
    after = _ok(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    assert len(_bullets(after["resume"]["markdown"])) == 20 and after["conflicts"] == [] and after["gate"]["ready"] is True
    assert set(_bullets(before["resume"]["markdown"])) < set(_bullets(after["resume"]["markdown"])), "the larger cap only adds lines"


# --- must-cover lines last; an edited resume is never re-picked in place ----------------------------------------------


def test_when_only_must_have_lines_are_left_under_a_smaller_cap_one_goes_and_the_pick_says_so(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cap of four holds exactly the four must-have lines; a cap of three loses one, and that is a conflict.

    A recent role always keeps its best line, so the line that goes is the old role's: the role is then listed by
    its heading alone (0.1.11.4 item 9: no employer is dropped silently)."""

    _no_layout_in_the_pick(monkeypatch)
    _assess_under(fx, monkeypatch, len(MUST_LINES))
    four = _view(fx)
    assert sorted(_bullets(four["resume"]["markdown"])) == sorted(MUST_LINES), "a must-have line went while another line could go"
    assert four["conflicts"] == [] and four["gate"]["ready"] is True
    with monkeypatch.context() as tighter:  # the re-pick's own cap (``settle_stored`` passes it on)
        tighter.setitem(pick.settle_stored.__kwdefaults__, "max_bullets", len(MUST_LINES) - 1)
        three = _ok(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    markdown = three["resume"]["markdown"]
    assert OLD_ROLE_LINE not in markdown and sorted(_bullets(markdown)) == sorted(MUST_LINES[:3])
    assert "### Lanternfish Labs" not in markdown and "### Earlier experience\n\nStaff Engineer, Lanternfish Labs | 2013 - 2015" in _NUMBERS.sub("", markdown)
    assert [conflict["code"] for conflict in three["conflicts"]] == ["mandatory_evidence_does_not_fit"]
    assert three["gate"]["ready"] is False  # a lost must-have line IS a reason (the page count never is)


def test_a_re_pick_leaves_a_resume_the_user_edited_untouched_and_the_new_pick_waits(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    _assess_under(fx, monkeypatch, 14)
    path = tailored_resume_path(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    stored = read_tailored_resume(path)
    assert stored is not None
    save_tailor_response(replace(stored, edited=TailorEdit("operator", "2026-10-06T10:00:00Z", "my own wording")))
    kept = path.read_bytes()
    answer = _ok(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    assert path.read_bytes() == kept and answer["resume"]["replaceable"] is False and answer["proposed"] is not None
    assert len(_bullets(answer["resume"]["markdown"])) == 14, "the edited resume was re-picked in place"
    record = suggestions.read_suggestions(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    assert record is not None and len(suggestions.recorded_marks(record.proposed)) == len(suggestions.recorded_marks(record.selection)) + 6  # the 20 wait


# --- "Shorten automatically" is retired (0.1.11.5, the coordinator's decision) ---------------------------------------------


def _home(fx: PostingsFixture) -> dict[str, bytes]:
    return {str(path.relative_to(fx.home_root)): path.read_bytes() for path in sorted(fx.home_root.rglob("*")) if path.is_file() and ".git" not in path.parts and "cache" not in path.parts}


RETIRED = (
    "Shortening a resume automatically is no longer part of GigAI, and nothing was changed. To fit the page, open the job's page and move "
    "the spacing slider beside the resume's preview; to make the resume shorter, remove a point there."
)


def test_the_shorten_route_is_retired_says_what_to_do_instead_and_writes_nothing(fx: PostingsFixture, server: _Server) -> None:
    _assess(fx)
    over = server.pdf()
    note = over.headers["x-gigai-fit-note"]
    # The over-limit sentence no longer names the button or the command: it names removing a point.
    assert note == (
        "This resume takes 3 pages: it does not fit on 2 pages even with the tightest spacing. "
        "To get 2 pages, remove a point or two on the job's page, then generate the PDF again. Or keep it at 3 pages."
    )
    assert "shorten" not in note.lower() and note.isascii()
    _view(fx)  # one read first: a scratch cache may be written by the first read of a home
    before, calls = _home(fx), len(fx.base.model.assess_prompts)
    response = server.client.post("/api/job-resumes/pick", json={"job_url": _URL, "profile_id": fx.default_profile_id, "action": "shorten"})
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "shorten_retired" == pick.REFUSED_SHORTEN_RETIRED and error["message"] == RETIRED == pick.MESSAGES["shorten_retired"]
    assert not _INTERNAL.search(error["message"])
    assert _home(fx) == before and len(fx.base.model.assess_prompts) == calls, "a retired step read or wrote something"
    # A job with nothing stored gets the same answer: nothing is read first.
    other = server.client.post("/api/job-resumes/pick", json={"job_url": job_url(_SLUG, 2), "profile_id": fx.default_profile_id, "action": "shorten"})
    assert other.status_code == 409 and other.json()["error"]["code"] == "shorten_retired"


def test_the_cli_shorten_flag_is_retired_says_what_to_do_instead_and_writes_nothing(fx: PostingsFixture) -> None:
    # With no assessment at all: the same refusal (nothing is read first).
    assert _refused(fx, "resume", "pick", "--job-url", _URL, "--shorten")["code"] == "shorten_retired"
    _assess(fx)
    _view(fx)
    before = _home(fx)
    error = _refused(fx, "resume", "pick", "--job-url", _URL, "--shorten")
    assert error["code"] == "shorten_retired" and error["message"] == RETIRED and _home(fx) == before
    plain = _cli(fx, "resume", "pick", "--job-url", _URL, "--shorten", as_json=False)
    assert plain.exit_code == 1 and RETIRED in plain.output and _home(fx) == before
    with pytest.raises(pick.PickError) as refused:
        pick.shorten_stored(fx.home_root, fx.target, fx.default_profile_id, _JOB, now="2026-10-06T10:00:00Z")
    assert refused.value.code == "shorten_retired" and _home(fx) == before
    # Only one step at a time, still.
    assert _refused(fx, "resume", "pick", "--job-url", _URL, "--shorten", "--refresh")["code"] == "invalid_value"
