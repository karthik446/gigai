"""0110-003 P1: nothing bypasses the resume privacy strip (static check, AST over ``src/gigai``).

The payload-capture test (``test_model_payload_privacy.py``) proves the
flows that exist today send no name or contact value. This file keeps the
next change honest, without running any GigAI code:

1. READERS: every call that reads the stored or pasted resume text (the
   named readers below, and every ``read_record(..., content=True)`` under
   ``gigai.scout``) sits in a reviewed ``(module, function)``. A new reader
   fails here until someone checks where its text goes and allowlists it.
2. BUILDERS: every model-bound prompt builder that carries resume text
   runs it through ``resume_privacy.model_resume`` (the rank digest through
   the shared header/guard helpers).
3. ONE ADAPTER SEAM: under ``gigai.scout`` a model adapter is only reached
   through ``proposal_execution.resolve_model_adapter`` (looked up as a
   module attribute), so the test transport and the payload test see every
   call (``interview_prep/categories.py`` used to bypass it).
4. DISPLAY FIELDS: the local display settings (title, layout; since
   0110-046 no name or contact line, and the Generate PDF form's parser) are
   imported only by the PDF renderer and the settings/PDF API, never by a
   model-bound module.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src"

_READERS = frozenset({
    "read_pinned_resume", "_read_pinned_resume", "resume_for_profile", "resolve_resume", "read_stored_resume",
    "current_resume", "_read_resume_text_for_rank", "resolve_tailor_resume",
})

#: (module, enclosing function, reader) -> where the text goes. Reviewed 2026-09-29 (0110-003 P1).
_READER_ALLOWLIST: dict[tuple[str, str, str], str] = {
    ("gigai.scout.find_jobs.api.extract", "read_stored_resume", "read_record"): "returned to the extract route below",
    ("gigai.scout.find_jobs.api.extract", "ResumeExtractRoutesMixin._handle_post_resume_extract", "read_stored_resume"): "extract.render_prompt (model_resume)",
    ("gigai.scout.find_jobs.api.extract", "ResumeExtractRoutesMixin._handle_post_resume_extract", "resolve_resume"): "extract.render_prompt (model_resume)",
    ("gigai.scout.find_jobs.api.extract", "ResumeExtractRoutesMixin._handle_post_resume_check", "read_stored_resume"): "local contact-details check, no model",
    ("gigai.scout.find_jobs.api.profiles", "ProfilesRoutesMixin._resolve_resume_ref", "read_record"): "hashed only",
    ("gigai.scout.find_jobs.api.rank", "_resolve", "read_record"): "model_rank.rank_postings -> rank_digest.resume_digest",
    ("gigai.scout.find_jobs.api.run_reads", "_resume_text", "read_record"): "local run reads (RowJoins), never sent",
    ("gigai.scout.contact_cleanup", "_clean_resumes", "read_record"): "0110-046: stripped locally (resume_pii.strip_contact_lines) and stored back, never sent",
    # 0.1.10.9 master P1: the master resume (contact lines never imported); parsed for `resume master show|history|init`, no model.
    ("gigai.scout.master_store", "_read_master", "read_record"): "parsed locally (master_resume.parse_master) for the master CLI, never sent",
    # 0.1.10.9 master P3: the migration merges the profiles' stored resumes into the master, locally (master_migration), no model.
    ("gigai.scout.master_profiles", "_resume_sources", "read_record"): "merged locally into the master (master_migration.plan_migration), never sent",
    # 0.1.10.9 master P7: the `resume_changed` stale check compares an earlier revision of a profile's resume with the
    # resume now, as flattened lines, against a stored assessment's own evidence. In memory, on a read; no model.
    ("gigai.scout.assess_master", "ResumeCheck._resume.read", "read_pinned_resume"): "compared locally, line by line (assess_master.resume_changes), never sent, never stored",
    ("gigai.scout.find_jobs.market_acquisition", "_read_resume_text_for_rank", "read_record"): "returned to the rank step below",
    ("gigai.scout.find_jobs.market_acquisition", "_rank_rows_with_status", "_read_resume_text_for_rank"): "model_rank.rank_postings -> rank_digest.resume_digest",
    ("gigai.scout.find_jobs.resume_input", "resolve_resume", "resume_for_profile"): "returned to its callers (allowlisted here)",
    ("gigai.scout.find_jobs.resume_input", "resume_for_profile", "read_pinned_resume"): "returned to its callers (allowlisted here)",
    ("gigai.scout.interview_prep.prep", "build_prep", "current_resume"): "categories._prompt (model_resume)",
    ("gigai.scout.profile_records", "_resolve_newest_resume_for_gig", "read_record"): "hashed only",
    # 0.1.10.7 M3a: the posting read model looks up cached rank scores; the text only makes the cache key, no model is called.
    ("gigai.scout.postings", "_Facts._rank_key_inputs", "resume_for_profile"): "hashed only (rank_digest.resume_digest -> the rank cache key); never sent, never stored",
    # 0.1.10.7 M4a: the background rank lane ranks a profile's demand set with that profile's resume, as a run's rank step did.
    ("gigai.scout.pipeline.rank_lane", "_candidate", "resume_for_profile"): "model_rank.rank_postings -> rank_digest.resume_digest",
    ("gigai.scout.proposal_execution", "_assess_node_body", "_read_pinned_resume"): "assessment_core.render_assess_prompt (model_resume)",
    ("gigai.scout.proposal_execution", "read_pinned_resume", "read_record"): "returned to its callers (allowlisted here)",
    ("gigai.scout.quick_assess", "run_quick_assessment", "resolve_resume"): "assessment_core.render_assess_prompt (model_resume)",
    ("gigai.scout.quick_assess", "run_quick_assessment", "resume_for_profile"): "assessment_core.render_assess_prompt (model_resume)",
    # 0110-10-05 B: the one resume resolution of a tailoring and of an attached edit (resolve_tailor_resume).
    ("gigai.scout.tailored_resume", "resolve_tailor_resume", "resolve_resume"): "returned to its callers (allowlisted here)",
    ("gigai.scout.tailored_resume", "resolve_tailor_resume", "resume_for_profile"): "returned to its callers (allowlisted here)",
    ("gigai.scout.tailored_resume", "run_tailored_resume", "resolve_tailor_resume"): "tailored_resume.tailor_context (model_resume)",
    ("gigai.scout.tailored_resume_edit", "attach_edited_resume", "resolve_tailor_resume"): "tailored_resume.tailor_context (model_resume); local validation of the edited markdown, no model call",
    # 0.1.11 N5: the agent's brief offers the stories that match the posting (tailored_resume.tailor_sources). The resume is read
    # for ONE thing, as the hand-back reads it: its name line, whose words are kept out of every story line offered
    # (story_bank.assess_bank). No line of it is printed in the brief; no model is called; nothing is stored.
    ("gigai.scout.job_brief", "_resume_text", "resume_for_profile"): "name-line words removed from the story lines the brief offers (story_bank.assess_bank); never printed, never sent, never stored",
    # 0.1.11.3 (the pick of a STORED job, no model call): the same local use as the assessment's own pick and the brief.
    ("gigai.scout.pick", "_stored_inputs", "resume_for_profile"): "name-line words removed from the story lines a selection may cite (tailor_sources -> story_bank.assess_bank); never printed, never sent, never stored",
}

#: (module, function) -> the strip calls its body must make.
_BUILDERS: dict[tuple[str, str], frozenset[str]] = {
    ("gigai.scout.assessment_core", "render_assess_prompt"): frozenset({"model_resume"}),
    ("gigai.scout.tailored_resume", "tailor_context"): frozenset({"model_resume"}),
    ("gigai.scout.tailored_resume", "TailorContext.__post_init__"): frozenset({"model_resume"}),
    ("gigai.scout.find_jobs.api.extract", "render_prompt"): frozenset({"model_resume"}),
    ("gigai.scout.interview_prep.categories", "_prompt"): frozenset({"model_resume"}),
    ("gigai.scout.find_jobs.rank_digest", "resume_digest"): frozenset({"split_resume_header", "guard_private", "guard_name"}),
}

#: Who may import the local display settings and the renderer (module -> allowed importers).
_DISPLAY_IMPORTERS: dict[str, frozenset[str]] = {
    "gigai.scout.resume_display": frozenset({
        "gigai.scout.resume_pdf", "gigai.scout.find_jobs.api.resume_display", "gigai.scout.find_jobs.api.tailored_resumes",
        # 0110-046: the story bank no longer reads a saved name (GigAI stores none); the one-time
        # cleanup reads and rewrites the display file (counts only, never sent anywhere).
        "gigai.scout.contact_cleanup",
        # 0.1.11.3 item 13: the user's own header file becomes the Generate PDF form's values (the form's fields and
        # parser only, never the saved settings); it is read for the form and for one PDF, and no model-bound module
        # imports it (test_pdf_header_file_privacy.py).
        "gigai.scout.pdf_header_file",
    }),
    "gigai.scout.find_jobs.api.resume_display": frozenset({
        "gigai.scout.find_jobs.api.server", "gigai.scout.find_jobs.api.tailored_resumes",
    }),
    # posting_keywords (0.1.10.7 D): reads the resume's Skills tags through resume_pdf's markdown parser (the chips the PDF prints);
    # it never touches resume_display, so the display settings stay out of the keyword path.
    # pipeline.steps (0.1.10.7 M2): the ATS step renders the stored tailored resume HEADERLESS (form=None) to score it;
    # it passes no header form, so the per-profile title and every header value stay out of the pipeline.
    "gigai.scout.resume_pdf": frozenset({
        # tailor_length_store (0110-10-05 C): asks the renderer for a page count only (fewest_pages, headerless); it reads
        # no display setting and no header form.
        "gigai.scout.tailor_length_store",
        "gigai.scout.find_jobs.api.tailored_resumes", "gigai.scout.scout_cli", "gigai.scout.posting_keywords",  # scout_cli: `scout resume pdf` renders locally (0110-032)
        "gigai.scout.pipeline.steps",
        # tailored_resume_edit (0110-10-05 B): checks an edited markdown with resume_pdf's markdown parser (the format's one
        # description) and its line patterns; it renders nothing and never touches resume_display or a header form.
        "gigai.scout.tailored_resume_edit",
        # master_selection (0.1.10.9 master P2): measures a pick of the master (contact-free at import) HEADERLESS with
        # resume_pdf.measure_markdown to fit it to the page budget; no header form, no display settings, no model.
        "gigai.scout.master_selection",
        # tailor_master (0.1.10.9 master P4): asks the renderer for a page count only (pages_at, headerless) to fit a
        # tailoring of the master's lines to the page budget; no header form, no display settings.
        "gigai.scout.tailor_master",
        # cover_letter (0.1.11.4 C2): sets a cover letter on a page through the resume renderer's own Typst calls and its
        # compact header (`gigai scout cover-letter pdf`); the header's values come as an argument for that ONE PDF
        # (pdf_header_cli), it reads no display setting, and nothing model-bound imports it.
        "gigai.scout.cover_letter",
        # pick (0.1.11.3 item 15, "Shorten automatically"): asks the renderer for a page count only (pages_at, headerless,
        # with more header lines kept blank: the tighter budget); no header form, no display settings, no header file.
        "gigai.scout.pick",
    }),
}


def _modules() -> Iterator[tuple[str, Path, ast.Module]]:
    for path in sorted((SRC / "gigai").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = path.relative_to(SRC).with_suffix("").parts
        name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        yield name, path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _calls(tree: ast.Module) -> Iterator[tuple[str, ast.Call]]:
    """Every call with its enclosing ``Class.function`` path (``<module>`` at top level)."""

    def walk(node: ast.AST, scope: tuple[str, ...]) -> Iterator[tuple[str, ast.Call]]:
        for child in ast.iter_child_nodes(node):
            inner = scope + (child.name,) if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else scope
            if isinstance(child, ast.Call):
                yield ".".join(scope) or "<module>", child
            yield from walk(child, inner)

    yield from walk(tree, ())


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _reads_content(call: ast.Call) -> bool:
    return any(item.arg == "content" and isinstance(item.value, ast.Constant) and item.value.value is True for item in call.keywords)


def _absolute(module: str, node: ast.ImportFrom, is_package: bool) -> str:
    if not node.level:
        return node.module or ""
    base = module.split(".")
    base = base if is_package else base[:-1]
    base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _imports(name: str, path: Path, tree: ast.Module) -> Iterator[str]:
    """Absolute module names ``tree`` imports (``from pkg import mod`` yields ``pkg.mod`` too)."""

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _absolute(name, node, path.name == "__init__.py")
            yield base
            yield from (f"{base}.{alias.name}" for alias in node.names)


def test_every_resume_reader_is_reviewed() -> None:
    found: set[tuple[str, str, str]] = set()
    for name, _path, tree in _modules():
        if not name.startswith("gigai.scout"):
            continue
        for scope, call in _calls(tree):
            called = _called_name(call)
            if called in _READERS or (called == "read_record" and _reads_content(call)):
                found.add((name, scope, called))  # type: ignore[arg-type]
    unreviewed = sorted(found - set(_READER_ALLOWLIST))
    assert unreviewed == [], (
        "a new resume reader: check where its text goes (a model-bound builder must strip it with "
        "resume_privacy.model_resume) and add it to _READER_ALLOWLIST"
    )
    assert sorted(set(_READER_ALLOWLIST) - found) == [], "an allowlisted reader is gone: drop it from the list"


def test_every_model_bound_builder_runs_the_strip() -> None:
    bodies: dict[tuple[str, str], set[str]] = {}
    for name, _path, tree in _modules():
        for scope, call in _calls(tree):
            if (name, scope) in _BUILDERS:
                bodies.setdefault((name, scope), set()).add(_called_name(call) or "")
    for builder, required in _BUILDERS.items():
        assert builder in bodies, f"{builder} no longer exists: update _BUILDERS with its replacement"
        assert required <= bodies[builder], f"{builder} must call {sorted(required)}"


def test_the_tailor_prompt_numbers_only_the_stripped_lines() -> None:
    tree = next(tree for name, _path, tree in _modules() if name == "gigai.scout.tailored_resume")
    render = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "render_tailor_prompt")
    attributes = {node.attr for node in ast.walk(render) if isinstance(node, ast.Attribute)}
    assert "model" in attributes and "resume_lines" not in attributes, "the prompt lists ctx.model.lines, never ctx.resume_lines"
    run = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "run_tailored_resume")
    called = {_called_name(node) for node in ast.walk(run) if isinstance(node, ast.Call)}
    assert "tailor_context" in called and "TailorContext" not in called


def test_scout_reaches_a_model_adapter_only_through_the_proposal_execution_seam() -> None:
    for name, path, tree in _modules():
        if not name.startswith("gigai.scout"):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(alias.name == "resolve_model_adapter" for alias in node.names):
                source = _absolute(name, node, path.name == "__init__.py")
                assert source == "gigai.adapters.factory" and name in {
                    "gigai.scout.proposal_execution",  # the seam itself
                    "gigai.scout.find_jobs.bindings",  # the test transport wraps the original
                }, f"{name} imports resolve_model_adapter; call proposal_execution.resolve_model_adapter instead"
        for _scope, call in _calls(tree):
            if _called_name(call) != "resolve_model_adapter" or name in {"gigai.scout.proposal_execution", "gigai.scout.find_jobs.bindings"}:
                continue
            func = call.func
            assert isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "proposal_execution", (
                f"{name}:{call.lineno} must call proposal_execution.resolve_model_adapter (module attribute)"
            )


def test_the_display_settings_are_read_only_by_the_renderer_and_the_settings_api() -> None:
    importers: dict[str, set[str]] = {module: set() for module in _DISPLAY_IMPORTERS}
    for name, path, tree in _modules():
        for imported in set(_imports(name, path, tree)):
            if imported in importers and imported != name:
                importers[imported].add(name)
    for module, allowed in _DISPLAY_IMPORTERS.items():
        assert importers[module] <= allowed, f"{module} is imported by {sorted(importers[module] - allowed)}"
    for name, path, _tree in _modules():
        if name in {"gigai.scout.resume_display", "gigai.scout.resume_pdf"} or any(name in allowed for allowed in _DISPLAY_IMPORTERS.values()):
            continue
        text = path.read_text(encoding="utf-8")
        assert "resume_display" not in text and "resume_pdf" not in text, f"{name} mentions the display settings"
