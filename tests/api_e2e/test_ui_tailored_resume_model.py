"""Q4b-ui: the tailored-resume preview and the Q4b field labels, run under node.

``ui/src/tailoredResumeModel.js`` and ``ui/src/jobModel.js`` are pure
JavaScript (no React), so this test runs them under the system ``node`` the
way ``test_ui_job_model_sort.py`` does (no JS test runner), and asserts on
the JSON the script prints. LOUD skip when ``node`` is not on PATH.

What is pinned:

* the preview walks a ``TailorResponse.result`` in ``render_markdown()``'s
  order (header, then each section: ``## <Heading>``, ``### <entry heading>``,
  ``- bullet``), and EVERY content line's text is one of the response's own
  lines -- nothing the panel shows is fabricated client-side; each line
  keeps its refs (the cited source text) for the hover/expand;
* the stats line counts copied / rewritten / citing-answers lines;
* the download file name comes from the response's job fields;
* ``latestStored`` picks the newest ``updated_at``;
* ``payLabel``/``workModeLabel``/``h1bLabel`` render ONLY when the field is
  present (operator answer 3) in the spec's formats (``$180k–$220k / yr``,
  ``from $180k / yr``, ``up to $220k / yr``, no suffix when ``period`` is
  null; ``9 H-1B approvals (FY2026)``, nothing at zero approvals);
* ``buildJobs`` carries ``rows[].h1b`` onto the job (``job.h1b``), null when
  the row lacks it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
TAILORED_JS = UI_SRC / "tailoredResumeModel.js"
JOB_MODEL_JS = UI_SRC / "jobModel.js"

R1 = "Jane Doe · Denver, CO · jane@example.test"
R2 = "Software engineer with Python service experience."
R5 = "Acme Corp — Senior Engineer (2021–2024)"
R6 = "Built Python services on GCP for 3 years."
ANSWER = "cloud gcp Yes, two years on GCP."


def _ref(line: int, text: str) -> dict:
    return {"kind": "resume", "line": line, "text": text}


def _copy(line: int, text: str) -> dict:
    return {"kind": "copy", "text": text, "refs": [_ref(line, text)]}


def _rewritten(text: str, refs: list[dict]) -> dict:
    return {"kind": "rewritten", "text": text, "refs": refs}


SUMMARY = "Python services engineer with three years on GCP."
BULLET = "Ran Python services on GCP for three years."
OTHER = "Two years of hands-on GCP work."

RESPONSE = {
    "schema_version": "scout-tailor-response:1",
    "job": {"job_identity": "https://boards.greenhouse.io/acme/jobs/101", "title": "Software Engineer", "company": "Acme Corp"},
    "resume": {"profile_id": "profile_1", "content_sha256": "sha256:" + "0" * 64},
    "result": {
        "schema_version": "scout-tailored-resume:1",
        "header": [_copy(1, R1), _copy(2, R2)],
        "sections": [
            {"heading": "summary", "lines": [_rewritten(SUMMARY, [_ref(2, R2), _ref(6, R6)])]},
            {"heading": "experience", "entries": [{"heading": [_copy(5, R5)], "bullets": [_rewritten(BULLET, [_ref(6, R6)])]}]},
            {"heading": "skills", "lines": [_copy(6, R6)]},
            {"heading": "other", "lines": [_rewritten(OTHER, [{"kind": "answer", "question_id": "cloud:gcp", "text": ANSWER}])]},
        ],
    },
    "markdown": "# ignored here: the download is this string verbatim\n",
    "created_at": "2026-09-25T10:00:00+00:00",
    "updated_at": "2026-09-25T11:00:00+00:00",
    "stored_path": "/x/resumes/profile_1/abc.json",
    "markdown_path": "/x/resumes/profile_1/abc.md",
}

NODE_SCRIPT = """
import { previewLines, previewStats, statsLine, sourcesHover, sourceLabel, downloadName, latestStored } from {tailored_url};
import { buildJobs, payLabel, workModeLabel, h1bLabel, triggerLabel, questionPromptIndex } from {job_model_url};
const input = JSON.parse(process.argv[1]);
const prompts = questionPromptIndex(input.prompts);
const promptFor = (id) => prompts.get(id) || null;
const lines = previewLines(input.response.result);
const stats = previewStats(lines);
const jobs = buildJobs(input.jobsFixture);
process.stdout.write(JSON.stringify({
  lines: lines.map((line) => ({ kind: line.kind, display: line.display === undefined ? null : line.display, text: line.text === undefined ? null : line.text, where: line.where || null, refs: line.refs || [] })),
  stats,
  statsLine: statsLine(stats),
  hover: lines.filter((line) => line.kind === "copy" || line.kind === "rewritten").map((line) => sourcesHover(line)),
  sourceLabels: input.refs.map((ref) => sourceLabel(ref)),
  sourceLabelsWithPrompts: input.refs.map((ref) => sourceLabel(ref, promptFor)),
  triggers: input.triggers.map((trigger) => [triggerLabel(trigger), triggerLabel(trigger, promptFor)]),
  downloadName: downloadName(input.response),
  downloadNameBare: downloadName({ job: {} }),
  latest: latestStored(input.stored) ? latestStored(input.stored).stored_path : null,
  pay: input.pay.map(payLabel),
  modes: input.modes.map((posting) => workModeLabel(posting)),
  h1b: input.h1b.map(h1bLabel),
  jobH1b: Object.fromEntries(jobs.map((job) => [job.posting.title, job.h1b])),
}));
"""


def _run_node(payload: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; tailored-resume model check not run")
    script = NODE_SCRIPT.replace("{tailored_url}", json.dumps(TAILORED_JS.resolve().as_uri())).replace(
        "{job_model_url}", json.dumps(JOB_MODEL_JS.resolve().as_uri())
    )
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script, "--", json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - a broken script, not a model bug
        raise AssertionError(f"node printed no JSON: {completed.stdout!r}\n{completed.stderr}") from exc


def _payload() -> dict:
    row = lambda title, h1b=None: {  # noqa: E731
        "posting": {"normalized_url": f"https://example.test/{title}", "title": title, "company": "Acme", "location": "", "provider": "greenhouse", "source_kind": "ats", "published_at": None},
        "status": "not_assessed",
        "assessment": None,
        **({"h1b": h1b} if h1b is not None else {}),
    }
    return {
        "response": RESPONSE,
        "refs": [_ref(12, "x"), {"kind": "answer", "question_id": "cloud:gcp", "text": "y"}, {**_ref(12, "x y"), "continued_lines": [13, 14]}],
        "prompts": {
            "answers": [
                {"question_id": "cloud:gcp", "prompt": "Have you run services on GCP?", "answer": "Yes"},
                {"question_id": "no:prompt", "answer": "x"},
                {"question_id": "id:as_prompt", "prompt": "id:as_prompt", "answer": "x"},  # a bare POST stores the id as the prompt
            ],
            "assessment": {"structured_questions": [{"question_id": "cloud:gcp", "question": "Do you have GCP experience?"}]},
            "assessments": [None, {"structured_questions": [{"question_id": "team:lead", "question": "Have you led a team?"}]}],
        },
        "triggers": ["answer:cloud:gcp", "answer:team:lead", "answer:unknown:id", "answer:id:as_prompt", "reassess"],
        "stored": [
            {"stored_path": "older", "updated_at": "2026-09-25T09:00:00+00:00"},
            {"stored_path": "newest", "updated_at": "2026-09-25T12:00:00+00:00"},
            {"stored_path": "middle", "updated_at": "2026-09-25T10:30:00+00:00"},
        ],
        "pay": [
            {"min": 180000, "max": 220000, "currency": "USD", "period": "year"},
            {"min": 180000, "max": None, "currency": "USD", "period": "year"},
            {"min": None, "max": 220000, "currency": "USD", "period": "year"},
            {"min": 65, "max": 80, "currency": "USD", "period": "hour"},
            {"min": 90000, "max": 110000, "currency": "EUR", "period": None},
            {"min": None, "max": None, "currency": "USD", "period": "year"},
            None,
        ],
        "modes": [{"work_mode": "remote"}, {"work_mode": "hybrid"}, {"work_mode": "onsite"}, {}, {"work_mode": None}],
        "h1b": [
            {"approvals": 9, "fiscal_years": [2026]},
            {"approvals": 1, "fiscal_years": [2025, 2026]},
            {"approvals": 3, "fiscal_years": []},
            {"fiscal_years": [2026]},
            {"approvals": 0, "fiscal_years": [2026]},
            None,
        ],
        "jobsFixture": {"rows": [row("with-h1b", {"approvals": 9, "fiscal_years": [2026]}), row("without-h1b")], "rankScores": [], "quickItems": [], "runCreatedAt": None},
    }


def test_preview_lines_are_the_responses_own_text_in_markdown_order() -> None:
    out = _run_node(_payload())
    lines = out["lines"]
    shown = [(line["kind"], line["display"]) for line in lines]
    assert shown == [
        ("copy", f"# {R1}"),
        ("copy", R2),
        ("blank", None),
        ("heading", "## Summary"),
        ("blank", None),
        ("rewritten", f"- {SUMMARY}"),
        ("blank", None),
        ("heading", "## Experience"),
        ("blank", None),
        ("copy", f"### {R5}"),
        ("blank", None),
        ("rewritten", f"- {BULLET}"),
        ("blank", None),
        ("heading", "## Skills"),
        ("blank", None),
        ("copy", f"- {R6}"),
        ("blank", None),
        ("heading", "## Other"),
        ("blank", None),
        ("rewritten", f"- {OTHER}"),
    ]
    # Nothing fabricated: every content line's text IS a response line, with its refs intact.
    result = RESPONSE["result"]
    response_lines = list(result["header"]) + [
        line
        for section in result["sections"]
        for line in section.get("lines", []) + [item for entry in section.get("entries", []) for item in entry["heading"] + entry["bullets"]]
    ]
    content = [line for line in lines if line["kind"] in {"copy", "rewritten"}]
    assert [(line["text"], line["refs"]) for line in content] == [(line["text"], line["refs"]) for line in response_lines]
    assert [line["where"] for line in content] == [
        "header line 1", "header line 2", "summary line 1", "experience entry 1 heading 1", "experience entry 1 bullet 1", "skills line 1", "other line 1",
    ]
    # The hover names each cited source with its text.
    assert out["hover"][2] == f"Resume line 2: {R2}\nResume line 6: {R6}"
    assert out["hover"][6] == f"Your answer · cloud:gcp: {ANSWER}"
    assert out["sourceLabels"] == ["Resume line 12", "Your answer · cloud:gcp", "Resume lines 12–14"]
    # Orchestrator rule: a question shows as its prompt (the assessment's own
    # wording first, then the recorded answer's), the id only as a fallback.
    assert out["sourceLabelsWithPrompts"] == ["Resume line 12", "Your answer · Do you have GCP experience?", "Resume lines 12–14"]
    assert out["triggers"] == [
        ["Re-assessed after you answered cloud:gcp", "Re-assessed after you answered “Do you have GCP experience?”"],
        ["Re-assessed after you answered team:lead", "Re-assessed after you answered “Have you led a team?”"],
        ["Re-assessed after you answered unknown:id", "Re-assessed after you answered unknown:id"],
        ["Re-assessed after you answered id:as_prompt", "Re-assessed after you answered id:as_prompt"],
        ["Assessed again", "Assessed again"],
    ]


def test_stats_download_name_and_latest_stored() -> None:
    out = _run_node(_payload())
    assert out["stats"] == {"total": 7, "copied": 4, "rewritten": 3, "citingAnswers": 1, "unsourced": 0}
    assert out["statsLine"] == "7 lines · 4 copied verbatim · 3 rewritten (1 citing your answers)"
    assert out["downloadName"] == "tailored-resume-acme-corp-software-engineer.md"
    assert out["downloadNameBare"] == "tailored-resume.md"
    assert out["latest"] == "newest"


def test_q4b_field_labels_render_only_when_present() -> None:
    out = _run_node(_payload())
    assert out["pay"] == ["$180k–$220k / yr", "from $180k / yr", "up to $220k / yr", "$65–$80 / hr", "€90k–€110k", None, None]
    assert out["modes"] == ["Remote", "Hybrid", "On-site", None, None]
    assert out["h1b"] == ["9 H-1B approvals (FY2026)", "1 H-1B approval (FY2025–2026)", "3 H-1B approvals", None, None, None]
    assert out["jobH1b"] == {"with-h1b": {"approvals": 9, "fiscal_years": [2026]}, "without-h1b": None}
