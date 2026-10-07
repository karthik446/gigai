#!/usr/bin/env python3
"""0.1.11 N3P (orchestrator #29): the assess-and-pick eval on REAL data, from an external data directory.

The operator's rule: evals run on real data; each job is different.  The data
lives OUTSIDE this repository (``--data-dir``, git-ignored where it is kept) and
nothing from it is ever written into the repository, a fixture, a golden file,
a log kept in git or a doc: every file this runner writes goes under ``--out``
(default ``<data-dir>/results/<label>/``), and the model sees the data only
through the product's own assess path.  The synthetic fixtures of
``run_assess_pick_eval`` stay as this runner's CI self-test
(``test_assess_real_harness.py``): they prove the harness runs, they are not
evidence of quality.

THE DATA DIRECTORY
- ``master.md``: the master resume (GigAI's format, ids on every line).
- ``answers.json``: ``{"answers": [{"question_id", "question", "answer"}]}``.
- ``setup.json``: ``{"prefs": {"countries", "visa_sponsorship_required", "city", "work_mode", "roles"}}``.
- ``profiles.json``: ``{"profiles": [{"profile_id", "label", "titles", "is_default"}]}``.
- ``postings/*.md``: a 4-line header (``title:``, ``company:``, ``location:``,
  ``url:``), a blank line, the posting text.
- ``ten.json`` (optional): per posting its ``slug``, ``current_verdict`` (what
  the stored 0.1.10.11 / v8 assessment said) and ``profile_id``; the verdict is
  reported beside the new one, nothing is scored against it.
No labels are needed: this runner reports FACTS; judging is done afterwards by
a reviewer.

WHAT IS CALLED.  The product's own ``quick_assess.run_quick_assessment`` end to
end, for a profile of a gig that holds the master, in a scratch home built by
the product's own writes (``run_assess_pick_eval.build_home`` shape: gig,
``find-jobs.json``, resume, ``import_master``, profiles and their first
selections, the stored answers).  One gig PER MODEL TARGET, so a posting's
first assessment under ``claude_cli`` and under ``codex_cli`` are independent
(a second assessment would read the first one's stored requirement list).
The same posting on both CLIs therefore also measures how far two first
assessments' requirement lists differ.

WHAT IS WRITTEN, per case, under ``<out>/<NN-slug>/<cli>/``: every prompt and
raw model answer (``attempt<N>.prompt.txt`` / ``.output.txt``), the stored
assessment (``assessment.json``), the suggestion record when the tree writes
one (``suggestions.json``), the model's selection (``selection.md`` and
``selection.pdf``: the job resume as stored, rendered as the product renders
it), the code selector's selection of the same case (``code-selection.json``
and, as markdown, ``code-selection.md``), and ``case.json`` (timings, tokens,
facts).  ``<out>/report.json`` and ``<out>/report.md`` are the facts of the
whole run.  Without labels the checks of SPEC 3.5 are read off the assessment's
own rows: a met must-have row whose cited master lines are not shown is
``lost`` (for the model's selection and for the code selector's, side by
side, never added up).

THE DISCIPLINE (SPEC 8.2).  ``assess.md`` is read again on every render: finish
a run before editing it (the report holds its digest at start and end).  A
scratch home only (``--home`` made by ``gigai setup --non-interactive``; the
operator's own home and ``$GIGAI_HOME`` are refused by their path); one call at
a time; ``--max-calls`` is a hard cap, the product's retry included; live runs
need ``GIGAI_ASSESS_EVAL_LIVE=1``; ``--fake-model`` answers offline.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

if __package__ in (None, ""):  # run as a script: make ``tests.evals`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.evals import run_assess_eval as harness  # noqa: E402
from tests.evals import run_assess_pick_eval as pe  # noqa: E402
from tests.evals.run_tailor_eval import CallBudget, CallCapReached  # noqa: E402

REPORT_SCHEMA = "gigai-assess-real-eval-report:1"
MODEL_TARGETS = ("claude_cli", "codex_cli")
DEFAULT_MAX_CALLS = 50
LIVE_ENV = "GIGAI_ASSESS_EVAL_LIVE"
MANDATORY = ("hard", "askable")


# --- the data directory --------------------------------------------------------------------------------


def load_data(data_dir: Path) -> dict[str, Any]:
    """The data directory as plain data. Raises ``ValueError`` naming the missing file."""

    data_dir = Path(data_dir)
    need = ("master.md", "answers.json", "setup.json", "profiles.json")
    missing = [name for name in need if not (data_dir / name).is_file()]
    if missing or not (data_dir / "postings").is_dir():
        raise ValueError(f"{data_dir} is not a data directory: missing {', '.join([*missing, *(() if (data_dir / 'postings').is_dir() else ('postings/',))])}")
    postings = []
    for path in sorted((data_dir / "postings").glob("*.md")):
        head, _, body = path.read_text(encoding="utf-8").partition("\n\n")
        meta = dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)
        for key in ("title", "company", "url"):
            if key not in meta:
                raise ValueError(f"{path.name}: the header has no {key}:")
        postings.append({"slug": path.stem, "title": meta["title"].strip(), "company": meta["company"].strip(), "location": meta.get("location", "").strip(), "url": meta["url"].strip(), "text": body.strip() + "\n"})
    if not postings:
        raise ValueError(f"{data_dir / 'postings'} holds no posting")
    ten = {}
    if (data_dir / "ten.json").is_file():
        ten = {item["slug"]: item for item in json.loads((data_dir / "ten.json").read_text(encoding="utf-8"))}
    profiles = json.loads((data_dir / "profiles.json").read_text(encoding="utf-8"))["profiles"]
    return {
        "master": (data_dir / "master.md").read_text(encoding="utf-8"),
        "answers": json.loads((data_dir / "answers.json").read_text(encoding="utf-8"))["answers"],
        "prefs": json.loads((data_dir / "setup.json").read_text(encoding="utf-8"))["prefs"],
        "profiles": profiles, "postings": postings, "ten": ten,
    }


def profile_key(data: Mapping[str, Any], posting: Mapping[str, Any]) -> int:
    """The index in ``profiles`` of the profile a posting was assessed for (``ten.json``), else the default one (0)."""

    wanted = (data["ten"].get(posting["slug"]) or {}).get("profile_id")
    for index, profile in enumerate(data["profiles"]):
        if wanted and profile.get("profile_id") == wanted:
            return index
    return next((index for index, profile in enumerate(data["profiles"]) if profile.get("is_default")), 0)


def plan(data: Mapping[str, Any], targets: Sequence[str], only: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """``[{slug, posting, model_target, profile}]``: every posting on each model target, one target after the other."""

    slugs = [posting["slug"] for posting in data["postings"]]
    unknown = [name for name in only or () if name not in slugs and not any(slug.startswith(name) for slug in slugs)]
    if unknown:
        raise ValueError(f"unknown posting {', '.join(unknown)}; known: {', '.join(slugs)}")
    chosen = [posting for posting in data["postings"] if not only or any(posting["slug"] == name or posting["slug"].startswith(name) for name in only)]
    return [{"slug": posting["slug"], "posting": posting, "model_target": target, "profile": profile_key(data, posting)} for target in targets for posting in chosen]


# --- the scratch home ----------------------------------------------------------------------------------


def build_home(home_root: Path, root: Path, name: str, data: Mapping[str, Any]) -> pe.TargetHome:
    """One gig for one model target: the master, the profiles with their first selection, the answers, the preferences.

    The same provisioning ``run_assess_pick_eval.build_target`` does (the test
    fixtures' gig, then the product's own write functions), from the data
    directory instead of the synthetic spec.
    """

    from gigai.canonical import canonical_json_bytes, digest_imported_bytes
    from gigai.lifecycle import create_offline
    from gigai.private_records import create_record, import_reference, migrate_workpad_layout
    from gigai.scout import master_profiles, story_bank
    from gigai.scout.find_jobs.contracts import FindJobsConfig, SourceToggles
    from gigai.scout.master_resume import parse_master
    from gigai.scout.master_selection import SelectionProfile, select
    from gigai.scout.master_store import import_master, load_master
    from gigai.scout.profile_records import create_profile, list_profiles, selected_profile
    from gigai.target_binding import initialize_target
    from gigai.workpad import resolve_workpad

    prefs, profiles = data["prefs"], data["profiles"]
    target = root / name
    target.mkdir(parents=True, exist_ok=False)
    initialize_target(home_root=home_root, requested_target=target)
    created = create_offline(
        home_root=home_root, requested_target=target, name=f"assess-real-eval-{name}", open_editor=False,
        model_output="A scratch gig for the real-data assess eval; it holds one person's master resume.",
    )
    migrate_workpad_layout(workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id)
    first = profiles[0]
    roles = tuple(first["titles"])
    config = FindJobsConfig(
        roles=roles, merged_queries=roles, location=prefs.get("city") or None, remote=prefs.get("work_mode") == "remote", published_after=None,
        sources=SourceToggles(exa=True, ats=True, hiringcafe=False), countries=tuple(prefs.get("countries") or ()),
        visa_sponsorship_required=bool(prefs.get("visa_sponsorship_required")),
    )
    body = config.to_json()
    if prefs.get("work_mode"):
        body["work_mode"] = prefs["work_mode"]
    (target / "find-jobs.json").write_bytes(canonical_json_bytes(body))

    files = root / f"{name}-files"
    files.mkdir()
    master_file = files / "master.md"
    master_file.write_text(data["master"], encoding="utf-8")
    master = parse_master(data["master"])
    resume_bytes = select(master, SelectionProfile(titles=roles), None, today=date.today()).markdown.encode("utf-8")
    resume_file = files / "resume.md"
    resume_file.write_bytes(resume_bytes)
    imported = import_reference(
        home_root=home_root, requested_target=target, gig_id=created.gig_id, kind="resume", source=resume_file,
        operation_key=f"scout-resume-add:resume.md:{digest_imported_bytes(resume_bytes)}",
    )
    create_record(
        home_root=home_root, requested_target=target, gig_id=created.gig_id, kind="imported_reference", content_family="g45_reference",
        content_id=imported.item_id, actor={"kind": "operator", "id": "local-user"}, origin="imported",
        operation_key=f"scout-resume-record:{imported.item_id}",
    )

    def resolved():
        return resolve_workpad(home_root=home_root, requested_target=target, gig_id=created.gig_id, allow_semantic_state=True)

    default = selected_profile(resolved(), home_root=home_root, target=target)
    assert default is not None
    status = import_master(home_root=home_root, target=target, source=master_file, gig_id=created.gig_id).status
    assert status == "created", status
    ids = [default.profile_id]
    for extra in profiles[1:]:
        ids.append(create_profile(
            resolved(), label=extra["label"], titles=tuple(extra["titles"]), titles_to_avoid=(), queries=tuple(extra["titles"]), resume_ref=default.resume_ref,
        ).profile_id)
    for profile_id in ids:
        master_profiles.refresh_selection(home_root=home_root, target=target, profile_id=profile_id)
    for item in data["answers"]:
        story_bank.save_answer(home_root=home_root, target=target, question_id=item["question_id"], answer=item["answer"], question=item.get("question"))
    stored = load_master(home_root=home_root, target=target, gig_id=created.gig_id)
    assert stored is not None
    records = {record.profile_id: record for record in list_profiles(resolved())}
    keys = [str(index) for index in range(len(ids))]
    base_ids = {key: tuple(records[profile_id].master_selection.item_ids) if records[profile_id].master_selection is not None else () for key, profile_id in zip(keys, ids)}
    return pe.TargetHome(name, "real", target, created.gig_id, dict(zip(keys, ids)), stored.master, data["master"], base_ids)


# --- facts of one answer -------------------------------------------------------------------------------


def lines_of(row: Mapping[str, Any]) -> list[str]:
    """The master lines a row rests on: its ``sources`` (v9) that are not answers, else the lines its evidence traced to (v8)."""

    sources = [item for item in row.get("sources") or () if not str(item).startswith("A ")]
    return sources or list(row.get("traced") or ())


def coverage(rows: Sequence[Mapping[str, Any]], shown: frozenset[str]) -> dict[str, Any]:
    """Check 1 of SPEC 3.5 off the assessment's own rows: met must-have rows with cited master lines, and those with none shown."""

    cited = [row for row in rows if row["status"] == "met" and row["class"] in MANDATORY and lines_of(row)]
    lost = [str(row["requirement"])[:100] for row in cited if not shown & set(lines_of(row))]
    strongest = [row for row in cited if row.get("sources") and row["sources"][0] in shown]
    return {"cited_rows": len(cited), "lost": lost, "strongest_shown": len(strongest) if any(row.get("sources") for row in cited) else None}


def facts(answer: Mapping[str, Any]) -> dict[str, Any]:
    """Counts of one answer by class and status, its questions by the class of their row, and its list items. No judgement."""

    rows = answer["matrix"]
    by_class: dict[str, dict[str, int]] = {}
    for row in rows:
        by_class.setdefault(str(row["class"]), {}).setdefault(str(row["status"]), 0)
        by_class[str(row["class"])][str(row["status"])] += 1
    asked = {pe.fold(str(item.get("requirement") or "")): item for item in answer["questions"]}
    classes = {pe.fold(str(row["requirement"])): row["class"] for row in rows}
    return {
        "rows": len(rows), "by_class_status": by_class,
        "questions": len(answer["questions"]),
        "questions_on_must_have": sum(classes.get(key) in MANDATORY for key in asked), "questions_on_optional": sum(classes.get(key) in ("list_item", "nice_to_have") for key in asked),
        "sources_per_met_row_mean": round(sum(len(row.get("sources") or ()) for row in rows if row["status"] == "met") / max(1, sum(row["status"] == "met" for row in rows)), 2),
        "suggestions": len(answer.get("structured_suggestions") or ()) or len(answer.get("suggestions") or ()),
    }


# --- one case ---------------------------------------------------------------------------------------------


def run_case(
    item: Mapping[str, Any], *, data: Mapping[str, Any], home: pe.TargetHome, home_root: Path, config: Any, budget: CallBudget, fake: pe.FakeModel | None,
    raw: list[dict[str, Any]], out: Path, selector: bool,
) -> dict[str, Any]:
    """One posting through ``run_quick_assessment`` on one CLI; the case's facts, its files written under ``out``."""

    from gigai.canonical import digest_imported_bytes
    from gigai.scout import assess_master
    from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput, ResolvedJob
    from gigai.scout.find_jobs.contracts import ModelTarget
    from gigai.scout.quick_assess import QuickAssessError, run_quick_assessment

    posting = item["posting"]
    cli = item["model_target"]
    profile_id = home.profiles[str(item["profile"])]
    model_target = pe.FAKE_TARGET if fake is not None else cli
    folder = out / item["slug"] / cli
    folder.mkdir(parents=True, exist_ok=True)
    row: dict[str, Any] = {"slug": item["slug"], "model_target": cli, "profile": item["profile"], "fake_model": fake is not None,
                           "stored_verdict_before": (data["ten"].get(item["slug"]) or {}).get("current_verdict")}
    if fake is not None:
        fake.case = f"{item['slug']}/{cli}"
        fake.answers[fake.case] = fake_answer(data, posting)
    job = ResolvedJob(
        job_identity=posting["url"], source_url=posting["url"], normalized_url=posting["url"], fetch_kind="generic", title=posting["title"],
        company=posting["company"], location=posting["location"], text=posting["text"], text_sha256=digest_imported_bytes(posting["text"].encode("utf-8")),
    )
    start, raw_start = budget.made, len(raw)
    budget.begin("assess", (f"{item['slug']}/{cli}", model_target))
    started = time.monotonic()
    response = None
    try:
        response = run_quick_assessment(
            AssessRequest(job=AssessJobInput(job_url=posting["url"]), resume=AssessResumeInput(profile_id=profile_id), model_target=ModelTarget(model_target)),
            home_root=home_root, target=home.target, config=config, resolved_job=job,
        )
    except QuickAssessError as exc:
        row.update({"ok": False, "error": exc.code, "error_message": str(exc)[:300]})
    calls = budget.calls[start:]
    usage = [entry for entry in raw[raw_start:] if "attempt" not in entry]
    row.update({
        "calls": len(calls), "seconds_total": round(time.monotonic() - started, 1), "call_seconds": [call.elapsed_seconds for call in calls],
        "input_tokens_reported": [(call.usage or {}).get("input_tokens") for call in calls], "output_tokens": [(call.usage or {}).get("output_tokens") for call in calls],
        "input_tokens_with_cache": [cache_total(entry["raw_usage"], call.usage) for entry, call in zip(usage, calls)] if len(usage) == len(calls) else None,
        "prompt_chars": [len(call.prompt) for call in calls], "output_chars": [len(call.output_text or "") for call in calls],
        "extras": next((entry["attempt"] for entry in reversed(raw[raw_start:]) if "attempt" in entry), None),
    })
    for call in calls:
        (folder / f"attempt{call.attempt}.prompt.txt").write_text(call.prompt, encoding="utf-8")
        (folder / f"attempt{call.attempt}.output.txt").write_text(call.output_text or f"<{call.error}>", encoding="utf-8")
    if response is None:
        (folder / "case.json").write_text(json.dumps(row, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return row
    answer = pe.answer_json(response, home.master)
    row.update({
        "ok": True, "model": response.model, "prompt_version": response.prompt_version, "instructions_digest": response.instructions_digest,
        "verdict": answer["verdict"], "gate_stored": answer.get("resume_gate"), "requirements_ref": answer.get("requirements_ref"),
        "answer": answer, "facts": facts(answer), "boundary": pe.boundary_drops(calls[-1].output_text if calls else None, answer),
        "not_a_match_reason": answer.get("not_a_match_reason"),
    })
    (folder / "assessment.json").write_text(json.dumps(response.to_json(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    master_ids = frozenset(home.master.items)
    row["pick"] = pick_facts(answer.get("pick"), master_ids)
    row["model_selection"] = write_model_selection(home_root, home, profile_id, posting["url"], folder, answer, home_root)
    if selector:
        row["code_selection"] = write_code_selection(home, item["profile"], data, posting, response, folder, answer)
    (folder / "case.json").write_text(json.dumps({k: v for k, v in row.items() if k != "answer"}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return row


def cache_total(raw_usage: Mapping[str, Any], normalized: Mapping[str, Any] | None) -> int | None:
    """Prompt tokens including the cache tokens the Claude CLI reports apart from ``input_tokens``."""

    base = (normalized or {}).get("input_tokens")
    if base is None:
        return None
    return int(base) + sum(int(raw_usage.get(name) or 0) for name in ("cache_creation_input_tokens", "cache_read_input_tokens"))


def pick_facts(pick: Mapping[str, Any] | None, master_ids: frozenset[str]) -> dict[str, Any] | None:
    if not pick:
        return None
    lines = list(pick.get("lines") or ())
    return {"lines": len(lines), "unknown": [line for line in lines if line not in master_ids], "repeats": len(lines) - len(set(lines)), "summary": pick.get("summary"), "section_order": pick.get("section_order")}


def write_model_selection(home_root: Path, home: pe.TargetHome, profile_id: str, job_identity: str, folder: Path, answer: Mapping[str, Any], _home: Path) -> dict[str, Any] | None:
    """The job resume the product stored for this assessment (markdown and PDF), and its facts; ``None`` without a suggestion record."""

    stored = pe.stored_selection(home_root, home, profile_id, job_identity)
    if stored is None:
        return None
    from gigai.scout import suggestions

    path = suggestions.suggestions_path(home_root, home.target, profile_id, job_identity)
    (folder / "suggestions.json").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    selection = stored.get("selection")
    out: dict[str, Any] = {"gate": stored["gate"], "record_suggestions": stored["suggestions"], "requirements": stored["requirements"]}
    if not selection:
        out["selection"] = None
        return out
    shown = frozenset(stored.get("printed") or ())
    out.update({
        "picked_by": selection.get("picked_by"), "fallback": selection.get("fallback"), "problems": selection.get("problems"), "added_by_code": selection.get("added_by_code"),
        "conflicts": selection.get("conflicts"), "pages": selection.get("pages"), "max_pages": selection.get("max_pages"), "lines": len(shown), "shown": sorted(shown),
        **pe.selection_fit(home.master, selection, shown),
        **coverage(answer["matrix"], shown),
    })
    resume = selection.get("resume") or {}
    try:
        from gigai.scout.resume_pdf import stored_resume_pdf
        from gigai.scout.tailored_resume import read_tailored_resume, render_markdown

        job_resume = read_tailored_resume(Path(resume["stored_path"]))
        if job_resume is not None:
            (folder / "selection.md").write_text(render_markdown(job_resume.result), encoding="utf-8")
            rendered, _name = stored_resume_pdf(job_resume, home_root=home_root, count_pages=True)
            (folder / "selection.pdf").write_bytes(rendered.pdf)
            out["pdf_pages"] = getattr(rendered, "pages", None)
            out["pdf_bytes"] = len(rendered.pdf)
    except Exception as exc:  # noqa: BLE001 - a render that fails is a fact of the run, named and kept in the report
        out["render_error"] = type(exc).__name__
    return out


def write_code_selection(home: pe.TargetHome, profile: int, data: Mapping[str, Any], posting: Mapping[str, Any], response: Any, folder: Path, answer: Mapping[str, Any]) -> dict[str, Any]:
    """The code selector's selection of this case (``pick_probe``'s ``fallback`` path), its ids, markdown and the same coverage fact."""

    from gigai.scout import assess_master

    cited = [item.to_json() for item in assess_master.cited_requirements(home.master, response.result.matrix)]
    titles = list(data["profiles"][profile]["titles"])
    key = str(profile)
    started = time.monotonic()
    try:
        final = pe.code_selection(home, key, titles, posting, cited, date.today().isoformat())
    except RuntimeError as exc:
        return {"error": str(exc)}
    if "error" in final:
        return {"error": final["error"]}
    shown = pe._shown(final)
    (folder / "code-selection.json").write_text(json.dumps({k: final[k] for k in ("summary", "entries", "other", "skills", "pages", "conflicts")}, indent=1) + "\n", encoding="utf-8")
    try:
        from gigai.scout.master_selection import render_selection

        ids = [*final["summary"], *(line for entry, lines in final["entries"].items() for line in (entry, *lines)), *final["other"]]
        (folder / "code-selection.md").write_text(render_selection(home.master, tuple(ids), tuple(final["skills"])), encoding="utf-8")
    except Exception:  # noqa: BLE001 - the ids file above is the record; the markdown is a convenience
        pass
    return {
        # ``page_fit``: the shape and the cap of bullets (0.1.11.5 item 1c: no page is counted for a selection).
        "lines": len(shown), "pages": final["pages"], "bullets": pe.capped_bullets(home.master, shown),
        "page_fit": pe.capped_bullets(home.master, shown) <= pe.MAX_PICK_BULLETS and not final["empty_entries"] and bool(final["date_order"]),
        "empty_entries": list(final["empty_entries"]), "conflicts": len(final["conflicts"]), "cited_rows": len(cited), "seconds": round(time.monotonic() - started, 1),
        "shown": sorted(shown), **coverage(answer["matrix"], shown),
    }


def fake_answer(data: Mapping[str, Any], posting: Mapping[str, Any]) -> str:
    """The offline model: a small valid v8-shaped answer (two rows, one question), the same for every posting. A harness stand-in, not a model."""

    quote = " ".join(next(line for line in data["master"].splitlines() if line.startswith("- ")).lstrip("- ").split()[:12])
    rows = [
        {"requirement": "Years of software engineering", "class": "hard", "status": "met", "resume_evidence": [quote]},
        {"requirement": "Kubernetes", "class": "askable", "status": "unclear", "resume_evidence": []},
    ]
    questions = [{"question_id": "tool:kubernetes", "question": "Have you operated Kubernetes?", "requirement": "Kubernetes"}]
    return json.dumps({"verdict": "pending_user_answers", "matrix": rows, "questions": questions, "not_a_match_reason": None, "suggestions": []})


# --- the report -----------------------------------------------------------------------------------------


def seconds_mean(rows: Sequence[Mapping[str, Any]]) -> float | None:
    values = [value for row in rows for value in row["call_seconds"]]
    return round(sum(values) / len(values), 1) if values else None


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"cases": len(rows), "ok": sum(bool(row.get("ok")) for row in rows), "calls": sum(row["calls"] for row in rows),
                           "failed": [{"slug": row["slug"], "cli": row["model_target"], "error": row.get("error")} for row in rows if not row.get("ok")], "by_model_target": {}}
    for cli in sorted({row["model_target"] for row in rows}):
        mine = [row for row in rows if row["model_target"] == cli]
        done = [row for row in mine if row.get("ok")]
        out["by_model_target"][cli] = {
            "cases": len(mine), "ok": len(done), "models": sorted({str(row.get("model")) for row in done}),
            "verdicts": {name: sum(row["verdict"] == name for row in done) for name in pe.RANK},
            "seconds_per_call_mean": seconds_mean(mine), "seconds_per_call_max": max((value for row in mine for value in row["call_seconds"]), default=None),
            "output_tokens_mean": round(sum(sum(v or 0 for v in row["output_tokens"]) for row in mine) / max(1, len(mine))),
            "input_tokens_with_cache_mean": round(sum(sum(v or 0 for v in (row["input_tokens_with_cache"] or row["input_tokens_reported"])) for row in mine) / max(1, len(mine))),
            "prompt_chars_mean": round(sum(sum(row["prompt_chars"]) for row in mine) / max(1, len(mine))),
            "questions_on_must_have": sum(row["facts"]["questions_on_must_have"] for row in done), "questions_on_optional": sum(row["facts"]["questions_on_optional"] for row in done),
            "rows": sum(row["facts"]["rows"] for row in done),
            "picks": sum(row["pick"] is not None for row in done), "pick_unknown_ids": sum(len(row["pick"]["unknown"]) for row in done if row["pick"]),
            "model_selections": sum(bool(row.get("model_selection") and row["model_selection"].get("lines")) for row in done),
            "picked_by_model": sum(bool(row.get("model_selection") and row["model_selection"].get("picked_by") == "model") for row in done),
            "fallbacks": sorted(str(row["model_selection"]["fallback"]) for row in done if row.get("model_selection") and row["model_selection"].get("fallback")),
            "model_selection_lost": sum(len(row["model_selection"].get("lost") or ()) for row in done if row.get("model_selection") and row["model_selection"].get("lines")),
            "code_selection_lost_same_cases": sum(len(row["code_selection"].get("lost") or ()) for row in done if row.get("model_selection") and row["model_selection"].get("lines") and row.get("code_selection") and "lost" in row["code_selection"]),
            "code_selection_lost_all": sum(len(row["code_selection"].get("lost") or ()) for row in done if row.get("code_selection") and "lost" in row["code_selection"]),
            "not_page_fit": sum(1 for row in done if row.get("model_selection") and row["model_selection"].get("lines") and not row["model_selection"].get("page_fit")),
            "pdf_rendered": sum(bool(row.get("model_selection") and row["model_selection"].get("pdf_bytes")) for row in done),
            "unknown_sources": sum(len((row.get("extras") or {}).get("unknown_sources") or ()) for row in done),
            "capped_questions": sum(len((row.get("extras") or {}).get("capped_questions") or ()) for row in done),
            "dropped_pick": [row["slug"][:2] for row in done if (row.get("extras") or {}).get("dropped_pick")],
            "retried": sum(row["calls"] > 1 for row in mine),
        }
    return out


def requirement_agreement(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """For each posting assessed on both CLIs: how far the two first assessments' requirement lists differ (rows without a counterpart)."""

    by_slug: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in rows:
        if row.get("ok"):
            by_slug.setdefault(row["slug"], {})[row["model_target"]] = row
    out = []
    for slug, pair in sorted(by_slug.items()):
        if len(pair) < 2:
            continue
        left, right = (pair[name] for name in sorted(pair))
        out.append({"slug": slug, "clis": sorted(pair), "verdicts": [left["verdict"], right["verdict"]], **pe.list_disagreement(left["answer"]["matrix"], right["answer"]["matrix"])})
    return out


def table(rows: Sequence[Mapping[str, Any]]) -> str:
    """One line a case: facts only (no accepted-by-labels column: there are no labels)."""

    out = [
        "| posting | CLI | verdict (before, v8 stored) | gate | rows | questions must/optional | pick | selection by | lost (model / code) | pages | PDF | s/call | tokens in/out |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        name = row["slug"][:2] + "-" + row["slug"].split("-")[1] if "-" in row["slug"] else row["slug"]
        if not row.get("ok"):
            out.append(f"| {name} | {row['model_target']} | FAILED {row.get('error')} | | | | | | | | | {'/'.join(str(v) for v in row['call_seconds'])} | |")
            continue
        sel, code = row.get("model_selection") or {}, row.get("code_selection") or {}
        f = row["facts"]
        tokens = row["input_tokens_with_cache"] or row["input_tokens_reported"]
        out.append(
            f"| {name} | {row['model_target']} | {pe.SHORT[row['verdict']]} ({pe.SHORT.get(row['stored_verdict_before'], row['stored_verdict_before'] or '-')}) | "
            f"{(row['gate_stored'] or {}).get('decision') or pe.gate_by_spec(row['answer']['matrix'], row['verdict'])} | {f['rows']} | {f['questions_on_must_have']}/{f['questions_on_optional']} | "
            f"{row['pick']['lines'] if row['pick'] else '-'} | {sel.get('picked_by') or '-'}{'/' + str(sel['fallback']) if sel.get('fallback') else ''} | "
            f"{len(sel['lost']) if 'lost' in sel else '-'} / {len(code['lost']) if 'lost' in code else '-'} | {sel.get('pages') or '-'} | {'yes' if sel.get('pdf_bytes') else '-'} | "
            f"{'/'.join(str(v) for v in row['call_seconds'])} | {sum(v or 0 for v in tokens)}/{sum(v or 0 for v in row['output_tokens'])} |"
        )
    return "\n".join(out)


def render_report(report: Mapping[str, Any]) -> str:
    run, summary = report["run"], report["summary"]
    lines = [f"# {run['label']}: assess.md {run['instructions_digest']} ({', '.join(run['prompt_versions']) or '-'}), calls {run['calls_made']}/{run['max_calls']}, skipped {run['skipped'] or 'none'}", "", table(report["rows"]), ""]
    for name, item in summary["by_model_target"].items():
        lines.append(f"- {name} ({', '.join(item['models'])}): verdicts {item['verdicts']}; {item['seconds_per_call_mean']} s/call mean (max {item['seconds_per_call_max']}); output tokens mean {item['output_tokens_mean']}, input tokens (cache included) mean {item['input_tokens_with_cache_mean']}, prompt {item['prompt_chars_mean']} chars; questions on must-have rows {item['questions_on_must_have']}, on optional rows {item['questions_on_optional']}; picks {item['picks']} (unknown ids {item['pick_unknown_ids']}), selections by the model {item['picked_by_model']} of {item['model_selections']}, fallbacks {item['fallbacks'] or 'none'}; must-have rows left unshown: model {item['model_selection_lost']} against code selector {item['code_selection_lost_same_cases']} on the same cases; not page fit {item['not_page_fit']}; PDFs {item['pdf_rendered']}; unknown sources {item['unknown_sources']}; questions capped {item['capped_questions']}; retried {item['retried']}")
    lines.append("")
    lines.append("Requirement lists of one posting on the two CLIs (first assessments, rows without a counterpart):")
    for item in report["agreement"]:
        lines.append(f"- {item['slug'][:2]}: rows {item['rows_left']} / {item['rows_right']}, same words {item['same_words']}, close words {item['close_words']}, only on {item['clis'][0]} {len(item['only_left'])}, only on {item['clis'][1]} {len(item['only_right'])}, class differs {len(item['class_differs'])}; verdicts {pe.SHORT[item['verdicts'][0]]} / {pe.SHORT[item['verdicts'][1]]}")
    return "\n".join(lines) + "\n"


# --- main -----------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="0.1.11 N3P: the assess-and-pick eval on real data from an external --data-dir (facts only, no labels)")
    parser.add_argument("--data-dir", type=Path, required=True, help="the external data directory (master.md, answers.json, setup.json, profiles.json, postings/*.md)")
    parser.add_argument("--label", default="run", help="what this run is: before, after ...")
    parser.add_argument("--out", type=Path, default=None, help="where every file is written (default <data-dir>/results/<label>/); never under this repository")
    parser.add_argument("--model-target", default="both", choices=("both", *MODEL_TARGETS), help="both CLIs (each posting on each, one gig per CLI), or one")
    parser.add_argument("--posting", action="append", default=None, help="run only this posting (its slug or a prefix, repeatable)")
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS, help=f"hard cap on model calls, the product's retry included (default {DEFAULT_MAX_CALLS})")
    parser.add_argument("--concurrency", type=int, default=1, help="only 1 is supported")
    parser.add_argument("--home", type=Path, default=None, help="the SCRATCH GigAI home (`gigai setup --non-interactive`); never the operator's")
    parser.add_argument("--reuse-home", action="store_true")
    parser.add_argument("--fake-model", action="store_true", help="offline: a temp home and a stand-in answer (CI self-test)")
    parser.add_argument("--no-selector", action="store_true", help="skip the code selector's selection")
    parser.add_argument("--stop-after-failures", type=int, default=2, help="stop when this many cases in a row fail (a CLI signed out fails every case); 0: never")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        data = load_data(args.data_dir)
        targets = list(MODEL_TARGETS) if args.model_target == "both" else [args.model_target]
        planned = plan(data, targets, args.posting)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    out = (args.out or args.data_dir / "results" / args.label).resolve()
    repo = harness.REPO_ROOT.resolve()
    if out == repo or repo in out.parents:
        print(f"--out {out} is inside the repository: results of a real-data run are never written there", file=sys.stderr)
        return 2
    if args.dry_run:
        for index, item in enumerate(planned, 1):
            print(f"{index:3} {item['slug'][:40]:40} {item['model_target']:10} profile {item['profile']}")
        print(f"{len(planned)} planned calls: --max-calls {len(planned) + max(1, len(planned) // 10)} lets every one run when a few answers are retried; results would go to {out}")
        return 0
    if args.concurrency != 1:
        print("--concurrency: only 1 is supported", file=sys.stderr)
        return 2
    if not args.fake_model:
        if os.environ.get(LIVE_ENV) != "1":
            print(f"refusing the live eval: set {LIVE_ENV}=1 explicitly", file=sys.stderr)
            return 2
        if args.home is None:
            print("--home is required for a live run (a scratch home made by `gigai setup --non-interactive`)", file=sys.stderr)
            return 2
        refused = pe.refuse_home(args.home)
        if refused is None and (args.home / "scout").exists() and not args.reuse_home:
            refused = f"{args.home} already holds Scout data: pass --reuse-home or make a fresh scratch home"
        if refused:
            print(refused, file=sys.stderr)
            return 2

    now = datetime.now(UTC)
    budget = CallBudget(max_calls=args.max_calls)
    fake = pe.FakeModel() if args.fake_model else None
    raw: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    digest_at_start = pe.instructions_digest()
    out.mkdir(parents=True, exist_ok=True)
    run: dict[str, Any] = {}

    def save() -> None:
        pairs = requirement_agreement(rows)
        run.update({
            "label": args.label, "started_at": now.isoformat().replace("+00:00", "Z"), "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "head": harness._git_head(), "fake_model": args.fake_model, "instructions_digest": digest_at_start, "instructions_digest_at_end": pe.instructions_digest(),
            "prompt_versions": sorted({str(row["prompt_version"]) for row in rows if row.get("ok")}), "max_calls": args.max_calls, "calls_made": budget.made,
            "planned_calls": len(planned), "skipped": skipped, "targets": targets, "postings": len({item["slug"] for item in planned}),
        })
        report = {"schema": REPORT_SCHEMA, "run": run, "summary": summarize(rows), "agreement": pairs, "rows": [{k: v for k, v in row.items() if k != "answer"} | {"answer_matrix": [
            {k: row_item.get(k) for k in ("id", "requirement", "class", "class_basis", "alternatives", "status", "sources")} for row_item in row["answer"]["matrix"]] if row.get("ok") else None} for row in rows]}
        (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        (out / "report.md").write_text(render_report({**report, "rows": rows}), encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="gigai-assess-real-eval-") as tmp:
        if args.fake_model:
            scratch = Path(tmp).resolve()
            home_root = scratch / "home"
            home_root.mkdir()
            config = harness.build_fake_config(home_root)
            root = scratch / "targets"
        else:
            from gigai.config import load_config

            home_root = args.home.expanduser().resolve()
            config = load_config(home_root)
            root = home_root.parent / "targets" / f"{args.label}-{now.strftime('%Y%m%dT%H%M%SZ')}"
        root.mkdir(parents=True, exist_ok=False)
        homes = {cli: build_home(home_root, root, cli.split("_")[0], data) for cli in targets}
        if not args.quiet:
            print(f"homes built: {', '.join(targets)}; {len(planned)} cases; results under {out}", file=sys.stderr)
        failed_in_a_row = 0
        with pe.model_seam(budget, fake, raw):
            for item in planned:
                name = f"{item['slug']}/{item['model_target']}"
                if (args.stop_after_failures and failed_in_a_row >= args.stop_after_failures) or budget.made + 2 > args.max_calls:
                    skipped.append(name)
                    continue
                try:
                    row = run_case(item, data=data, home=homes[item["model_target"]], home_root=home_root, config=config, budget=budget, fake=fake, raw=raw, out=out, selector=not args.no_selector)
                except CallCapReached:
                    skipped.append(name)
                    continue
                rows.append(row)
                failed_in_a_row = 0 if row.get("ok") else failed_in_a_row + 1
                save()
                if not args.quiet:
                    verdict = pe.SHORT[row["verdict"]] if row.get("ok") else f"FAILED {row.get('error')}"
                    print(f"{item['slug'][:2]} {item['model_target']:10} {verdict:12} {row['calls']} call(s) {'/'.join(str(v) for v in row['call_seconds'])} s", file=sys.stderr)
        save()
    if not args.quiet:
        print((out / "report.md").read_text(encoding="utf-8"))
    if run["instructions_digest"] != run["instructions_digest_at_end"]:
        print("assess.md CHANGED during the run: this report measures no single prompt", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
