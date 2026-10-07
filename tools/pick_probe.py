"""The pick probe (0110-10-15): the FINAL selection the selector makes for a master, a profile and postings, as ids.

One pure function, ``probe``: a master (markdown), a profile's prior and postings in; for each posting and each
path, what the resume shows in the end, by master id.  No home, no file, no model, no network.  It reads only
what a ``gigai`` tree has had since 0.1.10.9, so the SAME file runs against an older checkout: ``python
tools/pick_probe.py`` with ``PYTHONPATH=<old tree>/src`` reads a JSON payload on stdin and writes the JSON
answer on stdout (``run_in_tree``).  That is how ``tools/pick_report.py --baseline`` and
``tests/evals/run_pick_eval.py --baseline`` compare an old selector with the current one.

PAGES AND THE CAP (0.1.11.5 item 1c).  A tree from ``sel-7`` on makes a selection by score up to a cap of bullets
(``master_selection.MAX_PICK_BULLETS``) and counts no page: for its ``select``, ``fallback`` and ``settle`` paths the
answer's ``pages`` is ``None`` (not counted) and ``bullets`` / ``max_bullets`` say what the cap held.  An older tree
has no cap (``max_bullets`` ``None``) and its ``pages`` are measured, as before.  ``tailor_copy`` mirrors what the
PRODUCT makes of a tailor call's answer (``MasterTailoring.finish``): from ``sel-7`` on that is fitted to no page
(``tailor_master.whole_selection``), so its ``pages`` is ``None`` too, and its ``max_bullets`` is ``None`` because
no cap applies to it (a tailoring shows every line the model showed: the candidate set is the pick BEFORE its cap);
in an older tree it is the page fit (``fit_selected``), measured.

The paths (each is deterministic code):

- ``select``: ``master_selection.select``, the selection ``gigai scout resume master selection show
  --job-url`` prints and a profile's view is made of.
- ``fallback``: ``tailor_master.code_only``, the tailored resume that is stored when no model answers (the
  selection put through the validator, the no-loss pass and the Skills rules; before ``sel-7`` the page fit too).
- ``tailor_copy``: the tailor path with a model that copies every candidate line in the order listed
  (``job_candidates`` -> settled -> what the tree's ``MasterTailoring.finish`` does: ``whole_selection`` from
  ``sel-7`` on, so every candidate line stays; ``fit_selected`` before, where the fit alone decided what stays).
- ``settle`` (0.1.11, asked for by name: a tree before 0.1.11 has no such function and answers an error):
  ``pick.settle``, the ONE function the product makes a job's selection with.  The posting's cited rows are the
  assessment's rows with their ``sources``.  The pick is the case's own (``posting.pick``: line ids, best first)
  or, for a posting that carries cited rows and no pick, a stand-in: every line of the selector's pick before its
  cap (an older tree: its page fit), in the selector's own order of worth (a model that ranks as the code does).  With no cited rows and no
  pick there is no pick: ``settle`` answers the code selector's selection (``no_pick``).

A posting may carry ``cited``: the rows of its stored assessment that cite master lines (``{"id", "text",
"mandatory", "met", "lines"}``).  A tree whose selector reads citations (``sel-3`` on) selects from them; an older
tree ignores them.  Each answer names the roles and projects the selector says the posting's title names
(``title_entries``: entry id -> its best line; ``sel-4`` on, an older tree answers none).

The answer never holds a line's text: ids, skill names, counts and codes only.
"""

from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys

PATHS: tuple[str, ...] = ("select", "fallback", "tailor_copy")
#: 0.1.11: the product's own entry (``pick.settle``). Not in ``PATHS``: a baseline tree before 0.1.11 cannot answer it.
SETTLE = "settle"
PROBE_TIMEOUT_SECONDS = 1800


def _date_key(entry) -> tuple[int, int]:
    return (9999 if entry.ongoing else (entry.end or 0), entry.start or 0)


def _shape(master, entries: dict[str, list[str]]) -> tuple[list[str], bool]:
    """``(projects printed with no bullet, experience roles in date order)`` for the entries a resume shows.

    A ROLE with no bullet is not empty (0.1.11.4 item 9): it is listed by its one heading line (``earlier`` in the probe's answer)."""

    empty = [entry_id for entry_id, bullets in entries.items() if entry_id in master.entries and master.entries[entry_id].section == "projects" and not bullets]
    roles = [master.entries[entry_id] for entry_id in entries if entry_id in master.entries and master.entries[entry_id].section == "experience"]
    keys = [_date_key(entry) for entry in roles]
    return empty, keys == sorted(keys, reverse=True)


def _conflicts(value: object) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for item in getattr(value, "conflicts", ()) or ():
        out.append(item.to_json() if hasattr(item, "to_json") else dict(item))
    return out


def _duplicates(selected) -> dict[str, str]:
    """``line -> the better line that says the same and is shown in its place`` (a tree without the rule: none)."""

    return dict(getattr(selected, "duplicates", None) or {}) if selected is not None else {}


def _cap():
    """The tree's cap of bullets (``sel-7`` on), or ``None`` for a tree whose selection is fitted to pages."""

    from gigai.scout import master_selection as ms

    return getattr(ms, "MAX_PICK_BULLETS", None)


def _bullets(master, entries: dict[str, list[str]]) -> int:
    """What the cap counts: the bullets shown under roles and projects."""

    return sum(len(bullets) for entry_id, bullets in entries.items() if entry_id in master.entries and master.entries[entry_id].section in ("experience", "projects"))


def _title_entries(selected) -> dict[str, str]:
    """``role or project the posting's title names -> its best line`` (a tree without the rule, ``sel-3`` and older: none)."""

    return dict(getattr(selected, "title_entries", None) or {}) if selected is not None else {}


def _keywords(selected) -> dict[str, list[str]]:
    keywords = selected.keywords
    return {"must": list(keywords.must), "nice": list(keywords.nice)} if keywords is not None else {"must": [], "nice": []}


def _from_selected(master, selected) -> dict[str, object]:
    from gigai.scout import master_selection as ms
    from gigai.scout.resume_pdf import measure_markdown

    entries = {entry_id: list(bullets) for entry_id, bullets in selected.entries.items()}
    empty, in_order = _shape(master, entries)
    return {
        "selector_version": selected.selector_version,
        "summary": list(selected.summary), "entries": entries, "other": list(selected.other), "skills": list(selected.skills),
        # A tree that fits to pages: measured again from the final markdown, not taken from the selector's own word.
        # A tree with a cap counts no page.
        "pages": measure_markdown(selected.markdown, spacing_scale=ms.FIT_SCALE)[0] if _cap() is None else None, "max_pages": selected.max_pages,
        "bullets": _bullets(master, entries), "max_bullets": getattr(selected, "max_bullets", None),
        "empty_entries": empty, "date_order": in_order,
        # ``roles_dropped``: the roles not on the resume at all; ``earlier``: the ones listed by their heading line alone.
        "cuts": len(selected.cut_for_length), "roles_dropped": [role for role in selected.roles_dropped if role not in selected.earlier],
        "earlier": list(selected.earlier),
        "skills_left_out": [skill.name for skill in selected.skill_reasons if not skill.picked],
        "conflicts": _conflicts(selected),
        "keywords": _keywords(selected),
        "duplicates": _duplicates(selected),
        "title_entries": _title_entries(selected),
    }


def _from_result(master, result, record=None, selected=None, *, capped: bool = True) -> dict[str, object]:
    """``capped`` ``False`` (``tailor_copy``): the result is under no cap of bullets, so ``max_bullets`` is ``None``."""

    from gigai.scout import master_selection as ms
    from gigai.scout import tailor_master as tm
    from gigai.scout.master_resume import skill_names

    summary: list[str] = []
    other: list[str] = []
    skills: list[str] = []
    entries: dict[str, list[str]] = {}
    for section in result.sections:
        if section.heading == "skills":
            for line in section.lines:
                skills += [name for name in skill_names(line.text.lstrip("-*• ").strip())[1] if name not in skills]
        elif section.entries:
            for entry in section.entries:
                entry_id = tm.line_item_id(entry.heading[0]) if entry.heading else None
                entries[entry_id or f"?{len(entries)}"] = [item for line in entry.bullets if (item := tm.line_item_id(line)) is not None]
        else:
            ids = [item for line in section.lines if (item := tm.line_item_id(line)) is not None]
            (summary if section.heading == "summary" else other).extend(ids)
    empty, in_order = _shape(master, entries)
    length = result.length
    listed = {name.casefold() for name in skills}
    return {
        "selector_version": ms.SELECTOR_VERSION,
        "summary": summary, "entries": entries, "other": other, "skills": skills,
        "pages": tm.measure_pages(result) if _cap() is None else None, "max_pages": ms.MAX_PAGES,
        "bullets": _bullets(master, entries), "max_bullets": _cap() if capped else None,
        "empty_entries": empty, "date_order": in_order,
        "cuts": (len(length.cut) + sum(len(role.bullets) for role in length.trimmed)) if length is not None else 0,
        "roles_dropped": [tm.line_item_id(role.entry.heading[0]) or "" for role in length.cut] if length is not None else [],
        "earlier": [entry_id for entry_id, bullets in entries.items() if entry_id in master.entries and master.entries[entry_id].section == "experience" and not bullets],
        "skills_left_out": [name for name in master.skills() if name.casefold() not in listed],
        "conflicts": _conflicts(record) if record is not None else [],
        "keywords": _keywords(selected) if selected is not None else {"must": [], "nice": []},
        "duplicates": _duplicates(selected),
        "title_entries": _title_entries(selected),
    }


def _settle(master, profile, posting, today: date, lines: list[str] | None) -> dict[str, object]:
    """``pick.settle`` for one case (the module text): the final selection, and what the validation and the fit recorded."""

    from gigai.scout import master_selection as ms
    from gigai.scout import pick as pick_rules
    from gigai.scout.find_jobs.assess_contracts import MAX_PICK_LINES, AssessmentBody, AssessmentPick
    from gigai.scout.find_jobs.contracts import MatrixStatus, RequirementClass, RequirementMatrixRow, Verdict

    rows = tuple(
        RequirementMatrixRow(
            row.text, (), MatrixStatus.MET if row.met else MatrixStatus.UNCLEAR, RequirementClass.HARD if row.mandatory else RequirementClass.NICE_TO_HAVE,
            id=row.id, sources=tuple(row.lines),
        )
        for row in posting.cited
    )
    if lines is None and rows:
        if _cap() is None:  # a tree that fits to pages: its pick before the fit
            before_fit = ms.select(master, profile, posting, today=today, measure=lambda _markdown: (1, 0.0), max_pages=10**6, fill=False)
        else:
            before_fit = ms.select(master, profile, posting, today=today, max_bullets=None, fill=False)
        offered = [*(bullet for entry_id, bullets in before_fit.entries.items() if master.entries[entry_id].section != "education" for bullet in bullets), *before_fit.other]
        lines = sorted(offered, key=lambda item_id: -before_fit.values.get(item_id, 0.0))[:MAX_PICK_LINES]
    chosen = None if lines is None else AssessmentPick(None, ("experience", "projects"), tuple(lines))
    body = AssessmentBody(rows, (), (), verdict=Verdict.MATCHED_ABOVE_THRESHOLD, pick=chosen)
    settled = pick_rules.settle(master, body, None, today, profile=profile, posting=posting)
    return {
        **_from_result(master, settled.result, settled.record, settled.candidates.selected),
        "picked_by": settled.picked_by, "fallback": settled.fallback, "problems": [problem.to_json() for problem in settled.problems],
        "added_by_code": [item.to_json() for item in settled.added_by_code], "pick_conflicts": [conflict.to_json() for conflict in settled.conflicts],
        "ready": settled.check.ready, "layouts": settled.layouts,
    }


def _one(master, profile, posting, path: str, today: date, lines: list[str] | None = None) -> dict[str, object]:
    from gigai.scout import master_selection as ms
    from gigai.scout import tailor_master as tm
    from gigai.scout.tailor_skills import finish_tailoring
    from gigai.scout.tailored_resume import TailorJob, apply_no_loss, validate_tailored_output

    if path == SETTLE:
        return _settle(master, profile, posting, today, lines)
    if path == "select":
        return _from_selected(master, ms.select(master, profile, posting, today=today))
    job = TailorJob(posting.title, posting.company, posting.location, posting.text)
    candidates = tm.job_candidates(master, profile, posting, today=today)
    if path == "fallback":
        result, view = tm.code_only(master, candidates, job, today=today)
        record = tm.selection_record(master, view, result, picked_by=tm.PICKED_BY_CODE, fallback="probe", today=today)
        return _from_result(master, result, record, view.selected)
    if path == "tailor_copy":
        ctx = candidates.context()
        settled = finish_tailoring(apply_no_loss(validate_tailored_output(candidates.copy_all(), job, ctx), job, ctx, today=today), job, ctx)
        shown = tm.ensure_skills_line(settled, candidates, ctx)
        # What the tree's own ``MasterTailoring.finish`` does: no page fit from ``sel-7`` on, the page fit before.
        final = tm.whole_selection(shown) if hasattr(tm, "whole_selection") else tm.fit_selected(shown, candidates, master, today=today)
        return _from_result(master, final, tm.selection_record(master, candidates, final, today=today), candidates.selected, capped=False)
    raise ValueError(f"unknown path {path}")


def probe(payload: dict[str, object]) -> dict[str, object]:
    """The final selections for ``payload``: ``{"cases": [{"key", "master", "profile", "posting"}], "paths", "today"}``.

    ``master`` is master markdown (ids on every line); ``profile`` is ``{"titles", "base_ids" | null, "pins"?}``;
    ``posting`` is ``{"title", "text", "company"?, "location"?, "cited"?, "pick"?}``.  The answer is ``{"results": {key: {path: ...}}}``;
    a path that raises answers ``{"error": <the exception's type>}`` (never its message: it could quote a line).
    """

    from gigai.scout import master_selection as ms
    from gigai.scout.master_resume import parse_master

    today = date.fromisoformat(str(payload["today"]))
    paths = [str(path) for path in payload.get("paths") or PATHS]  # type: ignore[union-attr]
    parsed: dict[str, object] = {}
    results: dict[str, dict[str, object]] = {}
    for case in payload["cases"]:  # type: ignore[union-attr]
        text = str(case["master"])
        if text not in parsed:
            parsed[text] = parse_master(text)
        master = parsed[text]
        raw = case["profile"]
        fields: dict[str, object] = {
            "titles": tuple(raw.get("titles") or ()),
            "base_ids": None if raw.get("base_ids") is None else tuple(raw["base_ids"]),
            "profile_id": raw.get("profile_id"), "label": raw.get("label") or "",
        }
        if raw.get("pins") and "pins" in ms.SelectionProfile.__dataclass_fields__:
            fields["pins"] = tuple(raw["pins"])
        profile = ms.SelectionProfile(**fields)  # type: ignore[arg-type]
        post = case["posting"]
        posting = ms.SelectionPosting(str(post["title"]), str(post["text"]), str(post.get("company") or ""), str(post.get("location") or ""))
        if post.get("cited") and hasattr(ms, "CitedRequirement"):
            posting = ms.SelectionPosting(posting.title, posting.text, posting.company, posting.location, tuple(
                ms.CitedRequirement(str(row["id"]), str(row["text"]), bool(row["mandatory"]), tuple(row["lines"]), met=bool(row.get("met", True)))
                for row in post["cited"]
            ))
        out: dict[str, object] = {}
        lines = [str(item) for item in post["pick"]] if post.get("pick") is not None else None
        for path in paths:
            try:
                out[path] = _one(master, profile, posting, path, today, lines)
            except Exception as exc:  # noqa: BLE001 - a report on any home must finish; the type alone is reported
                out[path] = {"error": type(exc).__name__}
        results[str(case["key"])] = out
    return {"selector_version": ms.SELECTOR_VERSION, "results": results}


def run_in_tree(payload: dict[str, object], tree: Path) -> dict[str, object]:
    """``probe(payload)`` computed by the ``gigai`` of another checkout (``tree`` holds ``src/gigai``), in a child process.

    The payload travels on the child's stdin and the answer on its stdout: nothing is written to a file.
    """

    source = Path(tree) / "src"
    if not (source / "gigai" / "scout" / "master_selection.py").is_file():
        raise ValueError("the baseline tree holds no src/gigai/scout/master_selection.py (pass the root of a checkout or of a git archive)")
    environ = {**os.environ, "PYTHONPATH": str(source), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())], input=json.dumps(payload), capture_output=True, text=True, env=environ,
        timeout=PROBE_TIMEOUT_SECONDS, check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(f"the baseline probe failed (exit {done.returncode})")
    return json.loads(done.stdout)


def main() -> int:
    json.dump(probe(json.load(sys.stdin)), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
