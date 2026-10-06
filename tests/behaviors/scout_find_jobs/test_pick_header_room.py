"""0.1.11.3 item 15: a pick keeps room for the PDF's header, and a resume that still does not fit is shortened for the user.

The operator's case (a job whose pick said "2 pages of 2"), on synthetic data: the pick's page estimate kept a
two-line header's block and its fill packed page 2 to the last line, so the PDF with the name and contact details
on it ran to a 3rd page, and Generate PDF said "does not fit on 2 pages even with the tightest spacing".

THE END OUTCOME, read from the PDF that comes back (pypdf counts its pages):

- a pick that needed the fit (the master is 3+ pages) and fills its 2 pages prints on 2 pages WITH a header at its
  largest (the name, a contact line that wraps once), at the pick's own spacing: no tightening, no compact chips, no
  note; with a saved title's line on top of that it is still 2 pages. Through the CLI with the person's header file and through ``POST /api/tailored-resumes/pdf``
  with the form. The PDF's pages are the pick's (``picked.pages``);
- a resume the PDF cannot put on its pages (a pick made with no room for a header, the way picks before this were
  made) gets the plain sentence, and that sentence names the way out: Shorten automatically;
- ``gigai scout resume pick --shorten`` (the button's own request, ``POST /api/job-resumes/pick`` ``shorten``) picks
  again under a tighter page budget with NO model call: the PDF then fits, the answer says which lines it left out
  in plain words (the lines' text, never an id), and no line that backs a must-have requirement is among them while
  another line could go;
- when nothing but must-have lines is left, one of them goes and the answer says so;
- a resume the user edited is kept: the shorter one waits as the new suggested resume, and the answer says that;
- a job with no stored resume, and a resume that already fits with most of a page to spare, are refused in plain words.

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

from gigai.scout import assessment_core, pick, postings, suggestions
from gigai.scout import tailor_master as tm
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.find_jobs.contracts import normalize_url
from gigai.scout.master_selection import FIT_SCALE
from gigai.scout.master_store import import_master, load_master
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.quick_assess import run_quick_assessment
from gigai.scout.resume_display import DisplaySettings, form_header, parse_header_form, save_display
from gigai.scout.resume_pdf import _body, _render, pages_at
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
#: The last role ended long ago: an OLD role, which the fit may drop whole (a recent role always keeps its best line).
OLD_ROLE_LINE = "Shipped the React storefront used by two million shoppers."
LINES_PER_ROLE = 14
#: The lines the assessment's must-have rows rest on: each the LAST line of its role, where a cut by rank alone comes first.
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
_INTERNAL = re.compile(r"\bb-[0-9a-z]{4,}\b|req-[0-9a-f]+|max_pages|header_lines|cut_for_length|mandatory_evidence|settle|Traceback")


def _line(role: int, line: int) -> str:
    return f"Role {role} line {line}: built the scheduling and billing pipeline that moved {line + 3}0 million records a day across four regions with no data loss."


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
    # The operator's layout: the spacing the pick is measured at, auto fit off.
    save_display(fixture.home_root, DisplaySettings(spacing_scale=FIT_SCALE, auto_fit=False))
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


def _assess_the_old_way(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch, *, answer: str | None = None) -> None:
    """A stored pick the PDF cannot put on its pages: measured with NO room for a header at the tightest spacing, the
    way a resume that "fits nowhere" is. (Before this packet a pick kept two lines; the render's own fit absorbs
    most of those, and the operator's did not fit.)"""

    with monkeypatch.context() as old:
        old.setattr(tm, "measure_pages", lambda result: pages_at(result, 0.7, header_lines=0))
        _assess(fx, answer)


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


# --- the pick keeps room for the header ------------------------------------------------------------------------------


def test_a_pick_that_fills_two_pages_prints_on_two_pages_with_the_header_on(fx: PostingsFixture, tmp_path: Path, server: _Server) -> None:
    _assess(fx)
    view = _view(fx)
    counts = view["resume"]["counts"]
    # The pick needed the fit (the master does not fit 2 pages) and every must-have line is printed.
    assert counts["cut_for_length"] > 0 and view["picked"]["picked_by"] == "model" and view["conflicts"] == []
    assert (view["picked"]["pages"], view["picked"]["max_pages"]) == (2, 2)
    assert all(line in view["resume"]["markdown"] for line in MUST_LINES)

    # What the pick counted is what prints: laid out at the pick's own spacing with the header on and NO fit of the
    # render's own, the resume is on the pick's 2 pages (before: 3, the pick had kept no room for the header).
    stored = read_tailored_resume(tailored_resume_path(fx.home_root, fx.target, fx.default_profile_id, _JOB))
    assert stored is not None
    header = form_header(parse_header_form(FORM), "")
    plain = _render(_body(stored.result), header, company="", timestamp=STAMP, spacing_scale=FIT_SCALE, auto_fit=False, count_pages=True)
    assert plain.pages == len(_pages(plain.pdf)) == view["picked"]["pages"], f"the pick says {view['picked']['pages']} pages; with the header it prints on {plain.pages}"

    # THE END OUTCOME, the CLI with the person's header file: the pick's pages, at the pick's spacing, nothing tightened.
    payload, pages = _cli_pdf(fx, tmp_path)
    assert len(_header_lines(pages[0])) == 3, f"the header is not at its largest (name, two contact lines): {_header_lines(pages[0])}"
    assert len(pages) == payload["pages"] == view["picked"]["pages"] == 2, f"the PDF with the header is {len(pages)} pages; the pick says {view['picked']['pages']}"
    assert payload["spacing_scale"] == FIT_SCALE and payload["note"] is None, "the render had to tighten the spacing to hold the pick's pages"

    # ... and the page's Generate PDF, over HTTP, with the form.
    response = server.pdf()
    assert response.status_code == 200, response.text
    served = _pages(response.content)
    assert len(_header_lines(served[0])) == 3
    assert len(served) == view["picked"]["pages"] == 2 and "x-gigai-fit-note" not in response.headers
    assert "Role" in served[1] and "EDUCATION" in served[1], "page 2 is the resume's own second page"

    # A saved title is one more header line than the pick keeps room for: still the pick's pages, and no note.
    save_display(fx.home_root, DisplaySettings(titles={fx.default_profile_id: TITLE}, spacing_scale=FIT_SCALE, auto_fit=False))
    titled = server.pdf()
    assert _header_lines(_pages(titled.content)[0])[1] == TITLE and len(_pages(titled.content)) == 2 and "x-gigai-fit-note" not in titled.headers


def test_a_re_pick_of_a_job_picked_with_no_room_for_a_header_makes_it_fit(fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An old stored pick stays as it is (the render's own fit still applies); ``resume pick --refresh`` makes it fit."""

    _assess_the_old_way(fx, monkeypatch)
    before = _view(fx)
    payload, pages = _cli_pdf(fx, tmp_path, "before.pdf")
    assert len(pages) == 3 and payload["note"], "the fixture is not a resume the PDF cannot fit"
    after = _ok(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    assert after["resume"]["lines"] < before["resume"]["lines"] and after["conflicts"] == []
    payload, pages = _cli_pdf(fx, tmp_path, "after.pdf")
    assert len(pages) == 2 and payload["note"] is None and payload["spacing_scale"] == FIT_SCALE


# --- Shorten automatically -------------------------------------------------------------------------------------------


def test_a_resume_the_pdf_cannot_fit_is_shortened_and_the_answer_says_what_was_left_out(
    fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server: _Server,
) -> None:
    _assess_the_old_way(fx, monkeypatch)
    before = _view(fx)
    over = server.pdf()
    note = over.headers["x-gigai-fit-note"]
    assert len(_pages(over.content)) == 3
    # The sentence names the way out, and never tells the person to edit by hand.
    assert note == (
        "This resume takes 3 pages: it does not fit on 2 pages even with the tightest spacing. To get 2 pages, shorten it automatically "
        "(the Shorten automatically button on the job's page, or `gigai scout resume pick --job-url URL --shorten`; no model call), "
        "then generate the PDF again. Or keep it at 3 pages."
    )
    assert "remove a few lines" not in note and note.isascii()
    calls = len(fx.base.model.assess_prompts)

    # The button's own request.
    response = server.client.post("/api/job-resumes/pick", json={"job_url": _URL, "profile_id": fx.default_profile_id, "action": "shorten"})
    assert response.status_code == 200, response.text
    answer = response.json()
    shortened = answer["shortened"]
    assert answer["action"] == "shorten" and len(fx.base.model.assess_prompts) == calls, "shortening calls no model"

    # What it left out, in plain words: the lines' own text, never an id.
    left_out, message = shortened["left_out"], shortened["message"]
    assert left_out and len(left_out) == before["resume"]["lines"] - answer["resume"]["lines"]
    assert message.startswith(f"Left out {len(left_out)} line{'' if len(left_out) == 1 else 's'}: \"Role ") and message.endswith(" Generate the PDF again.")
    assert not _INTERNAL.search(message), message
    for line in left_out:
        assert line.startswith("Role ") and line.removesuffix("...") in MASTER and line.removesuffix("...") not in answer["resume"]["markdown"]
    # No line a must-have requirement rests on went while another line could go.
    assert shortened["must_have_cut"] is False and shortened["waiting"] is False and answer["conflicts"] == []
    assert all(line in answer["resume"]["markdown"] for line in MUST_LINES)
    assert not any(must[:40] in line for line in left_out for must in MUST_LINES)

    # THE END OUTCOME: the PDF now fits, with the header on, and says nothing.
    again = server.pdf()
    assert len(_pages(again.content)) == 2 and "x-gigai-fit-note" not in again.headers
    payload, pages = _cli_pdf(fx, tmp_path)
    assert len(pages) == 2 and payload["note"] is None


def test_the_cli_shortens_and_prints_what_it_left_out_first(fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _assess_the_old_way(fx, monkeypatch)
    payload, pages = _cli_pdf(fx, tmp_path, "before.pdf")
    assert len(pages) == 3 and "gigai scout resume pick --job-url URL --shorten" in payload["note"]
    result = _cli(fx, "resume", "pick", "--job-url", _URL, "--shorten", as_json=False)
    assert result.exit_code == 0, result.output
    first = result.output.splitlines()[0]
    assert re.fullmatch(r'Left out \d+ lines?: "Role .*"\. Generate the PDF again\.', first), first
    assert not _INTERNAL.search(first)
    payload, pages = _cli_pdf(fx, tmp_path, "after.pdf")
    assert len(pages) == 2 and payload["note"] is None

    # Again shortens further: at least one more line each time, a must-have line never before another line.
    lines = _view(fx)["resume"]["lines"]
    further = _ok(fx, "resume", "pick", "--job-url", _URL, "--shorten")
    assert further["resume"]["lines"] < lines and further["shortened"]["left_out"] and further["shortened"]["must_have_cut"] is False
    assert all(line in further["resume"]["markdown"] for line in MUST_LINES)


def test_when_only_must_have_lines_are_left_one_goes_and_the_answer_says_so(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """A page budget so tight that the lines the must-haves rest on do not all fit: nothing else is left to cut.

    A recent role always keeps its best line, so the line that goes is the old role's: the role is then listed by
    its heading alone (0.1.11.4 item 9: no employer is dropped silently)."""

    _assess_the_old_way(fx, monkeypatch)
    real = pick._tighter_measure  # noqa: SLF001 - the budget is the thing under test

    def one_page_less(current, max_pages):  # noqa: ANN001, ANN202
        real(current, max_pages)  # the refusals of the real one still apply
        return lambda result: (pages_at(result, FIT_SCALE, header_lines=40) or 0) + 1

    monkeypatch.setattr(pick, "_tighter_measure", one_page_less)
    answer = _ok(fx, "resume", "pick", "--job-url", _URL, "--shorten")
    shortened = answer["shortened"]
    markdown = answer["resume"]["markdown"]
    assert OLD_ROLE_LINE not in markdown, "the fixture left room for every must-have line"
    assert "### Lanternfish Labs" not in markdown and "### Earlier experience\n\nStaff Engineer, Lanternfish Labs | 2013 - 2015" in markdown
    assert all(line in markdown for line in MUST_LINES[:3])
    # ... and it went LAST: nothing is printed but the lines each recent role always keeps.
    assert sum(line.startswith("- Role ") for line in markdown.splitlines()) <= len(ROLES) - 1, "a must-have line went while another line could go"
    assert shortened["must_have_cut"] is True
    assert "Nothing else was left to cut, so a line that backs a must-have requirement (or a line you pinned) was left out too." in shortened["message"]
    assert any(conflict["code"] == "mandatory_evidence_does_not_fit" for conflict in answer["conflicts"]) and not _INTERNAL.search(shortened["message"])


def test_a_resume_the_user_edited_is_kept_and_the_shorter_one_waits(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    _assess_the_old_way(fx, monkeypatch)
    path = tailored_resume_path(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    stored = read_tailored_resume(path)
    assert stored is not None
    save_tailor_response(replace(stored, edited=TailorEdit("operator", "2026-10-06T10:00:00Z", "my own wording")))
    kept = path.read_bytes()
    answer = _ok(fx, "resume", "pick", "--job-url", _URL, "--shorten")
    shortened = answer["shortened"]
    assert path.read_bytes() == kept and answer["resume"]["replaceable"] is False and answer["proposed"] is not None
    assert shortened["waiting"] is True and shortened["left_out"]
    assert shortened["message"].endswith("The resume you edited was kept as it is; the shorter one is waiting as the new suggested resume.")
    record = suggestions.read_suggestions(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    assert record is not None and len(suggestions.recorded_marks(record.proposed)) < len(suggestions.recorded_marks(record.selection))


def test_shorten_is_refused_in_plain_words_when_there_is_nothing_to_shorten(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    # No assessment, no resume.
    assert _refused(fx, "resume", "pick", "--job-url", _URL, "--shorten")["code"] in ("assessment_missing", "no_resume_to_shorten")
    # A resume that fits its 2 pages with most of a page to spare: nothing is left out, nothing is written.
    ids = _ids(fx)
    few = json.loads(_answer(fx))
    few["pick"]["lines"] = [ids[line] for line in MUST_LINES] + [ids[_line(0, n)] for n in range(8)]
    with monkeypatch.context() as no_fill:
        no_fill.setattr(pick, "FILL_LINES", 0)
        _assess(fx, json.dumps(few))
    path = tailored_resume_path(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    kept = path.read_bytes()
    error = _refused(fx, "resume", "pick", "--job-url", _URL, "--shorten")
    assert error["code"] == "resume_short_already" and "nothing was left out" in str(error["message"]) and path.read_bytes() == kept
    # Only one step at a time.
    assert _refused(fx, "resume", "pick", "--job-url", _URL, "--shorten", "--refresh")["code"] == "invalid_value"
