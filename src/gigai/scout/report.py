"""Local, script-free HTML reports for the Scout projection."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import html
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit
import uuid

from ..canonical import canonical_json_bytes, digest_imported_bytes
from ..index import database_lock
from .projection import ScoutProjection, rebuild_projection


class ScoutReportError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


_UNSAFE_MARKUP = re.compile(r"<\s*script\b|\bon[a-z]+\s*=|javascript\s*:|data\s*:", re.I)
_REMOTE_CSS = re.compile(r"@import|url\s*\(\s*(?:https?:|//)", re.I)


def _text(value: object, fallback: str = "Unknown") -> str:
    if value is None or value == "":
        return fallback
    return html.escape(str(value), quote=True)


def _safe_external(value: object) -> str | None:
    if not isinstance(value, str) or any(ord(ch) < 32 for ch in value):
        return None
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None
    if parts.username or parts.password or "\\" in value or value.startswith("//"):
        return None
    return html.escape(value, quote=True)


def _safe_local_href(workpad: Path, from_dir: Path, value: object) -> str | None:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        return None
    parts = Path(value).parts
    if ".." in parts or any(part in {"", "."} for part in parts):
        return None
    target = workpad.joinpath(*parts)
    try:
        # Every component is checked so a committed-looking path cannot use a
        # symlink to escape the selected Gig.
        current = workpad
        for part in parts:
            current = current / part
            if current.is_symlink():
                return None
        if not target.is_file():
            return None
        target.resolve(strict=True).relative_to(workpad.resolve(strict=True))
        href = os.path.relpath(target, from_dir)
    except (OSError, RuntimeError, ValueError):
        return None
    return html.escape(href.replace(os.sep, "/"), quote=True)


def _link(workpad: Path, from_dir: Path, value: object, label: object) -> str:
    label_html = _text(label, "source")
    local = _safe_local_href(workpad, from_dir, value)
    if local is not None:
        return f'<a href="{local}">{label_html}</a>'
    external = _safe_external(value)
    if external is not None:
        return f'<a href="{external}">{label_html}</a>'
    return label_html


def _li(items: list[str], empty: str = "Nothing recorded yet.") -> str:
    return "<ul>" + ("".join(f"<li>{item}</li>" for item in items) or f'<li class="empty">{_text(empty)}</li>') + "</ul>"


def _proposal_content(item: Mapping[str, object]) -> str:
    proposal = item.get("assessment") or item.get("proposal")
    if not isinstance(proposal, Mapping):
        return ""
    fields = (
        ("proposed_resume_focus", "Resume focus"),
        ("fit_reasons", "Fit reasons"),
        ("hard_blockers", "Hard blockers"),
        ("unknowns", "Unknowns"),
        ("preference_rejection_reason", "Preference rejection"),
        ("focused_experience_questions", "Focused questions"),
    )
    parts: list[str] = []
    for key, label in fields:
        value = proposal.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            rendered = _li([_text(entry) for entry in value], "None recorded.")
        elif isinstance(value, Mapping):
            rendered = _text(json.dumps(dict(value), sort_keys=True, ensure_ascii=False))
        else:
            rendered = _text(value)
        parts.append(f"<dt>{_text(label)}</dt><dd>{rendered}</dd>")
    return f'<details class="proposal-content"><summary>Proposal content</summary><dl>{"".join(parts) or "<dd>Proposal content unavailable.</dd>"}</dl></details>'


def _section(projection: ScoutProjection, *, workpad: Path, from_dir: Path) -> str:
    opportunities = {str(item.get("opportunity_id")): item for item in projection.opportunities}
    apps_by_opportunity: dict[str, list[Mapping[str, object]]] = {}
    for app in projection.applications:
        apps_by_opportunity.setdefault(str(app.get("opportunity_ref")), []).append(app)
    proposal_by_opportunity: dict[str, list[Mapping[str, object]]] = {}
    for item in projection.proposals:
        proposal_by_opportunity.setdefault(str(item.get("opportunity_id")), []).append(item)

    jobs: list[str] = []
    for opportunity_id, item in opportunities.items():
        source = item.get("source") if isinstance(item.get("source"), Mapping) else {}
        source_url = source.get("locator") if isinstance(source, Mapping) else None
        title = item.get("title") or item.get("role_title") or opportunity_id
        employer = item.get("employer") or item.get("company") or "Unknown employer"
        source_markup = _link(workpad, from_dir, source_url, "posting source") if source_url else "source unavailable"
        apps = apps_by_opportunity.get(opportunity_id, [])
        active = [app for app in apps if app.get("current")]
        status = active[-1].get("event_kind") if active else "not recorded"
        jobs.append(
            "<article class=\"job\"><h3>"
            + _text(title)
            + "</h3><p><strong>"
            + _text(employer)
            + f"</strong> · {_text(status)}</p><p>Opportunity {_text(opportunity_id)} · {_text(item.get('snapshot_id'), 'snapshot unknown')} · {source_markup}</p>"
            + (f"<p>Proposals: {_text(len(proposal_by_opportunity.get(opportunity_id, [])))}</p>" if opportunity_id in proposal_by_opportunity else "")
            + "</article>"
        )
    if not jobs and projection.applications:
        jobs.append('<article class="job warning"><h3>Unlinked application events</h3><p>Application history exists, but no committed opportunity reader has linked it yet; no job identity was invented.</p></article>')

    application_items = [
        f"{_text(item.get('event_kind'))} · {_text(item.get('occurred_at'))} · {_text(item.get('opportunity_ref'))}"
        + (" · opportunity verified" if item.get("opportunity_verified") else " · opportunity unresolved")
        for item in projection.applications
    ]
    proposal_items = []
    for item in projection.proposals:
        identity = (
            f"{_text(item.get('status'), 'proposal state unknown')} · "
            f"{_text(item.get('opportunity_id'), 'opportunity unknown')} · "
            + (_link(workpad, from_dir, item.get("path"), item.get("revision_id", "proposal")) if item.get("path") else _text(item.get("revision_id"), "revision unknown"))
        )
        if item.get("opportunity_verified") is False:
            identity += " · opportunity unresolved"
        source = _link(workpad, from_dir, item.get("source_path"), "source") if item.get("source_path") else "source unavailable"
        proposal_items.append(identity + f" · {source}" + _proposal_content(item))
    question_items = [
        f"{_text(item.get('prompt'), item.get('question_id', 'question'))} · {_text(item.get('state'))}"
        for item in projection.questions
        if item.get("state") != "answered"
    ]
    document_items = [
        f"{_text(item.get('document_kind'), item.get('kind', 'document'))} · "
        + (_link(workpad, from_dir, item.get("path") or item.get("location"), item.get("revision_id", "document")) if item.get("path") or item.get("location") else _text(item.get("revision_id"), "revision unknown"))
        for item in projection.documents
    ]
    evidence_items = [
        f"{_text(item.get('label') or item.get('claim_id') or item.get('evidence_id'), 'evidence')} · {_text(item.get('status'), 'status unknown')}"
        + (_link(workpad, from_dir, item.get("path"), "evidence source") if item.get("path") else "")
        for item in projection.evidence
    ]
    run_items = [
        f"{_text(item.get('status'), 'Run state unknown')} · "
        + (_link(workpad, from_dir, item.get("path"), item.get("run_id", "Run")) if item.get("path") else _text(item.get("run_id"), "Run identity unknown"))
        for item in projection.runs
    ]
    return (
        '<section id="jobs" aria-labelledby="jobs-heading"><h2 id="jobs-heading">Jobs</h2>'
        + ("".join(jobs) or '<p class="empty">No committed opportunity records are available yet.</p>')
        + '</section><section id="applications" aria-labelledby="applications-heading"><h2 id="applications-heading">Application history</h2>'
        + _li(application_items, "No explicit application events recorded.")
        + '</section><section id="proposals" aria-labelledby="proposals-heading"><h2 id="proposals-heading">Saved proposals</h2>'
        + _li(proposal_items, "No validated proposal revisions available.")
        + '</section><section id="questions" aria-labelledby="questions-heading"><h2 id="questions-heading">Questions</h2>'
        + _li(question_items, "No pending questions.")
        + '</section><section id="documents" aria-labelledby="documents-heading"><h2 id="documents-heading">Documents and checks</h2>'
        + _li(document_items, "No selected document revisions available.")
        + '</section><section id="evidence" aria-labelledby="evidence-heading"><h2 id="evidence-heading">Evidence</h2>'
        + _li(evidence_items, "No linked evidence available.")
        + '</section><section id="runs" aria-labelledby="runs-heading"><h2 id="runs-heading">Runs and sources</h2>'
        + _li(run_items, "No linked Run records available.")
        + '</section>'
    )


def _validate_source(template: bytes, css: bytes) -> None:
    try:
        template_text = template.decode("utf-8")
        css_text = css.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ScoutReportError("report_source_invalid", "report source is not UTF-8") from exc
    if _UNSAFE_MARKUP.search(template_text) or _REMOTE_CSS.search(css_text):
        raise ScoutReportError("report_source_unsafe", "report source contains executable or remote markup")
    # The shipped renderer owns the local stylesheet reference; a customized
    # source with another local link remains allowed, but remote links do not.
    for href in re.findall(r"(?:href|src)=[\"']([^\"']+)", template_text, flags=re.I):
        if _safe_external(href) is not None or href.startswith("//"):
            raise ScoutReportError("report_source_unsafe", "report source references a remote resource")


def render_html(*, projection: ScoutProjection, template: bytes, css: bytes, workpad: Path | None = None, report_dir: Path | None = None) -> tuple[bytes, bytes]:
    """Render complete HTML and CSS while preserving the editable source."""
    _validate_source(template, css)
    try:
        source = template.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ScoutReportError("report_source_invalid", "template is not UTF-8") from exc
    root = workpad or Path.cwd()
    origin = report_dir or root
    content = _section(projection, workpad=root, from_dir=origin)
    if "<!-- SCOUT:CONTENT -->" in source:
        rendered = source.replace("<!-- SCOUT:CONTENT -->", content)
    elif re.search(r"<main\b[^>]*>.*?</main>", source, flags=re.I | re.S):
        rendered = re.sub(r"(<main\b[^>]*>).*?(</main>)", lambda match: match.group(1) + content + match.group(2), source, count=1, flags=re.I | re.S)
    else:
        raise ScoutReportError("report_source_invalid", "template has no main element or Scout content marker")
    return rendered.encode("utf-8"), css


def _source_paths(root: Path, template_path: Path | None) -> tuple[Path, Path]:
    template = template_path or (root / "ui" / "template.html")
    css = template.parent / "style.css"
    for path in (template, css):
        try:
            if path.is_symlink() or not path.is_file():
                raise ScoutReportError("report_source_missing", "selected report source is not a regular file")
            path.resolve(strict=True).relative_to(root.resolve(strict=True))
        except (OSError, RuntimeError, ValueError) as exc:
            if isinstance(exc, ScoutReportError):
                raise
            raise ScoutReportError("report_source_invalid", "report source escapes the selected Gig") from exc
    return template, css


def publish_report(*, resolved, projection: ScoutProjection | None = None, template_path: Path | None = None) -> dict[str, object]:
    """Atomically publish a complete generated report bundle.

    The old current selector is untouched if source validation or staging
    fails.  Generated files never overwrite ``ui/template.html`` or CSS.
    """
    root = resolved.path
    if projection is None:
        projection = rebuild_projection(resolved=resolved)
    template, css = _source_paths(root, template_path)
    template_bytes, css_bytes = template.read_bytes(), css.read_bytes()
    generation = f"generation_{uuid.uuid4()}"
    reports = root / "reports" / "scout"
    generations = reports / "generations"
    for directory in (reports, generations):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ScoutReportError("report_path_invalid", "report output path is redirected")
        directory.mkdir(parents=True, exist_ok=True)
    staged = generations / f".{generation}.staging"
    current = reports / "current.json"
    try:
        if staged.exists():
            raise ScoutReportError("report_path_invalid", "report staging path already exists")
        staged.mkdir(mode=0o700)
        html_bytes, rendered_css = render_html(projection=projection, template=template_bytes, css=css_bytes, workpad=root, report_dir=staged)
        (staged / "index.html").write_bytes(html_bytes)
        (staged / "style.css").write_bytes(rendered_css)
        metadata = {
            "schema_version": "scout-report:1",
            "generation_id": generation,
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "project_id": projection.project_id,
            "gig_id": projection.gig_id,
            "journal_head": projection.journal_head,
            "projection_cursor": projection.cursor,
            "template_sha256": digest_imported_bytes(template_bytes),
            "style_sha256": digest_imported_bytes(rendered_css),
            "index": "index.html",
        }
        (staged / "report.json").write_bytes(canonical_json_bytes(metadata))
        destination = generations / generation
        if destination.exists() or destination.is_symlink():
            raise ScoutReportError("report_path_invalid", "report generation path already exists")
        os.replace(staged, destination)
        selector = {
            "schema_version": "scout-report-selector:1",
            "generation_id": generation,
            "journal_head": projection.journal_head,
            "path": f"reports/scout/generations/{generation}/index.html",
            "report": f"reports/scout/generations/{generation}/report.json",
        }
        # Selector replacement is the final publication step.  It happens
        # under the same lock as index projection writers.
        with database_lock(root):
            temporary = reports / f".current-{uuid.uuid4()}.tmp"
            try:
                temporary.write_bytes(canonical_json_bytes(selector))
                os.replace(temporary, current)
            finally:
                temporary.unlink(missing_ok=True)
    except ScoutReportError:
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
        raise
    except (OSError, ValueError) as exc:
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
        raise ScoutReportError("report_publication_failed", "report generation could not be published") from exc
    return {"status": "published", "generation_id": generation, "path": str(generations / generation / "index.html"), "journal_head": projection.journal_head, "selector": selector}


def read_current_report(*, resolved) -> dict[str, object]:
    """Read one selector and report staleness against the current journal HEAD."""
    current = resolved.path / "reports" / "scout" / "current.json"
    try:
        selector = json.loads(current.read_text(encoding="utf-8"))
        if not isinstance(selector, dict) or selector.get("schema_version") != "scout-report-selector:1":
            raise ValueError("invalid selector")
        path = selector.get("path")
        if not isinstance(path, str) or not path.startswith("reports/scout/generations/") or ".." in Path(path).parts or "\\" in path:
            raise ValueError("unsafe report path")
        target = resolved.path / path
        if target.is_symlink() or not target.is_file():
            raise ValueError("report target unavailable")
        target.resolve(strict=True).relative_to(resolved.path.resolve(strict=True))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ScoutReportError("report_unavailable", "current Scout report selector is invalid") from exc
    try:
        head = __import__("subprocess").check_output(["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, __import__("subprocess").CalledProcessError) as exc:
        raise ScoutReportError("report_head_unavailable", "journal HEAD is unavailable") from exc
    return {**selector, "stale": selector.get("journal_head") != head, "resolved_path": str(target)}


__all__ = ["ScoutReportError", "publish_report", "read_current_report", "render_html"]
