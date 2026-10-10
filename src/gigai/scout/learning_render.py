"""0.1.11.10 Part B packet G3: render a course.json into a static course site (the slice-1 import accepts it).

A ROLE COURSE is generated offline from a role's postings and verified documentation pages (the generator is G2;
this module only renders what G2 already validated into ``course.json``). The page shell, CSS and page bodies are a
direct port of the research spike's ``render/render_course.py``: same structure, same validators, same classes and
ids (the UI and the acceptance browser test depend on those). What changed for the product:

* every path is a parameter (``out_dir``, an explicit assets source), never a path on the author's machine;
* the render is deterministic byte for byte: no timestamp, no random id, every traversal of a dict or set sorted
  before it is written;
* a GLYPH gate (section 5 of the design): a page string outside ASCII, Latin-1 punctuation, the middle dot and
  typographic quotes fails the render; a zero-width space or other invisible Unicode in an INPUT string is
  stripped before rendering and counted, never an error on its own;
* an INTERNAL LINK AUDIT over the finished output folder with :mod:`html.parser` (no browser dependency): every
  ``href`` and ``#anchor`` must resolve to a file and an ``id`` in that file;
* SIZE caps matching the design (at most 40 lessons; the whole site under 10 MB without ``assets/``; every page
  under 3 MB) and an inline-script / ``on*=`` attribute assertion, so a page that would violate the slice-1 CSP is
  caught here, before import, not by the importer's own scan.

No model call, no network, stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import html
import json
import re
import shutil
import unicodedata
from pathlib import Path

ASSET_REL = "assets"
MAX_LESSONS = 40
MAX_SITE_BYTES_NO_ASSETS = 10 * 1024 * 1024
MAX_HTML_BYTES = 3 * 1024 * 1024

#: Code points a rendered page may hold beyond plain ASCII: Latin-1 punctuation (0xA0-0xFF), the middle dot
#: (already in that range, kept named for clarity) and the typographic quotes/dashes this renderer itself emits.
_ALLOWED_EXTRA = frozenset("‘’“”–—…")


class CourseError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "x"


def concept_page_filename(concept_id: str) -> str:
    return f"concept-{slugify(concept_id)}.html"


def practice_page_filename(concept_id: str) -> str:
    return f"practice-{slugify(concept_id)}.html"


def tech_anchor(name: str) -> str:
    return f"tech-{slugify(name)}"


def domain_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url)
    return m.group(1) if m else url


#: Invisible/formatting code points that carry no visible content: stripped from every input string before it is
#: rendered, and counted (design section 5). Category Cf (zero-width space and friends) plus the BOM.
def strip_invisible(s: str) -> tuple[str, int]:
    kept = []
    removed = 0
    for ch in s:
        if ch == "﻿" or unicodedata.category(ch) == "Cf":
            removed += 1
            continue
        kept.append(ch)
    return "".join(kept), removed


def autolink_text(text: str, tool_links: dict[str, str], used: set[str]) -> str:
    """Escape text, then auto-link the first per-page occurrence of each tool name.

    Matches whole words, longest names first, case-sensitive. Skips names
    already linked once on this page (tracked via `used`, shared across all
    body blocks of one concept page).
    """
    escaped = esc(text)
    if not tool_links:
        return escaped

    names = sorted((n for n in tool_links if n not in used), key=len, reverse=True)
    if not names:
        return escaped

    pattern = re.compile(
        "|".join(r"\b" + re.escape(esc(n)) + r"\b" for n in names)
    )

    def repl(m: re.Match) -> str:
        matched = m.group(0)
        for n in names:
            if esc(n) == matched:
                if n in used:
                    return matched
                used.add(n)
                url = tool_links[n]
                return (
                    f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer" class="autolink">'
                    f"{matched}</a>"
                )
        return matched

    return pattern.sub(repl, escaped)


# ---------------------------------------------------------------------------
# Validation (course.json shape)
# ---------------------------------------------------------------------------

def validate_course(course: dict, paths: dict[str, dict] | None = None) -> list[str]:
    errors: list[str] = []
    modules = course.get("modules", [])
    paths = paths or {}

    all_concept_ids: set[str] = set()
    for module in modules:
        for concept in module.get("concepts", []):
            cid = concept.get("id")
            if cid:
                all_concept_ids.add(cid)

    lesson_count = sum(len(module.get("concepts", [])) for module in modules)
    if lesson_count > MAX_LESSONS:
        errors.append(f"course: {lesson_count} lessons exceeds the cap of {MAX_LESSONS}")

    for mi, module in enumerate(modules):
        mctx = f"modules[{mi}] ({module.get('id', '?')})"
        for ci, concept in enumerate(module.get("concepts", [])):
            cid = concept.get("id", "?")
            ctx = f"{mctx}.concepts[{ci}] ({cid})"

            path = paths.get(cid)
            if path:
                errors.extend(validate_path(path, f"{ctx}.path"))

            summary = concept.get("summary", "")
            if not summary or not summary.strip():
                errors.append(f"{ctx}: empty summary")
            elif not concept.get("sources") and not concept.get("subtopics"):
                pass  # a bare concept with no subtopics is allowed; the "no sources" check below is the real gate

            sources = concept.get("sources", [])
            if not sources:
                errors.append(f"{ctx}: no sources")
            else:
                for si, src in enumerate(sources):
                    url = src.get("url", "")
                    if not url.startswith("http://") and not url.startswith("https://"):
                        errors.append(
                            f"{ctx}.sources[{si}]: url '{url}' does not start with http(s)://"
                        )
                    elif is_bare_domain_url(url):
                        errors.append(f"{ctx}.sources[{si}]: url '{url}' is a bare domain/home page")

            for ri, rel in enumerate(concept.get("related", [])):
                if rel not in all_concept_ids:
                    errors.append(f"{ctx}.related[{ri}]: unknown concept id '{rel}'")

            if not concept.get("subtopics") and not concept.get("sources"):
                errors.append(f"{ctx}: concept without sources and without subtopics")

            marker = concept.get("marker")
            if marker:
                errors.extend(validate_marker(marker, f"{ctx}.marker"))
            for si, sub in enumerate(concept.get("subtopics", [])):
                errors.extend(validate_marker(sub, f"{ctx}.subtopics[{si}]"))

            diagram = concept.get("diagram")
            if diagram:
                source = str(diagram.get("source", "")).strip()
                if not source.startswith(("flowchart", "graph", "sequenceDiagram")):
                    errors.append(f"{ctx}.diagram: source does not start with flowchart, graph or sequenceDiagram")

    return errors


def is_bare_domain_url(url: str) -> bool:
    """True if url has no path beyond '/' (a bare domain / home page)."""
    m = re.match(r"https?://[^/]+(/.*)?$", url)
    if not m:
        return False
    rest = m.group(1) or ""
    return rest in ("", "/")


def validate_marker(entry: dict, ctx: str) -> list[str]:
    errors: list[str] = []
    label = entry.get("marker")
    evidence = entry.get("evidence")
    if label in ("KNOWN", "SOME"):
        if not evidence or not evidence.get("id"):
            errors.append(f"{ctx}: marker '{label}' has no evidence id")
    elif label == "NEW":
        if evidence:
            errors.append(f"{ctx}: marker 'NEW' must not carry evidence")
    return errors


# ---------------------------------------------------------------------------
# Practice paths
# ---------------------------------------------------------------------------

def load_paths(paths_dir: Path | None, concept_ids: set[str]) -> dict[str, dict]:
    """Read ``<paths_dir>/<concept-id>.json`` for each known concept id that has one.

    Concepts with no matching file are simply absent from the result -- not
    an error.
    """
    paths: dict[str, dict] = {}
    if paths_dir is None or not paths_dir.exists():
        return paths
    for cid in sorted(concept_ids):
        path = paths_dir / f"{cid}.json"
        if path.exists():
            paths[cid] = json.loads(path.read_text(encoding="utf-8"))
    return paths


def validate_path_items(items: list[dict], ctx: str) -> list[str]:
    errors: list[str] = []
    for ii, item in enumerate(items):
        source = item.get("source") or {}
        url = source.get("url", "")
        if not url:
            errors.append(f"{ctx}[{ii}]: item has no source url (path item without deep url)")
        elif not url.startswith("http://") and not url.startswith("https://"):
            errors.append(
                f"{ctx}[{ii}]: source url '{url}' does not start with http(s)://"
            )
        elif is_bare_domain_url(url):
            errors.append(
                f"{ctx}[{ii}]: source url '{url}' is a bare domain/home page (path item without deep url)"
            )
    return errors


def validate_path(path: dict, ctx: str) -> list[str]:
    errors: list[str] = []
    setup = path.get("setup", [])
    steps = path.get("steps", [])
    if not setup:
        errors.append(f"{ctx}: setup is empty")
    if not steps:
        errors.append(f"{ctx}: steps is empty")
    errors.extend(validate_path_items(setup, f"{ctx}.setup"))
    errors.extend(validate_path_items(steps, f"{ctx}.steps"))
    for si, src in enumerate(path.get("sources", [])):
        url = src.get("url", "")
        if url and not url.startswith("http://") and not url.startswith("https://"):
            errors.append(f"{ctx}.sources[{si}]: url '{url}' does not start with http(s)://")
    return errors


# ---------------------------------------------------------------------------
# Page shell
# ---------------------------------------------------------------------------

THEME_INIT_SCRIPT = """<script src="{asset_rel}/theme-init.js"></script>"""

PAGE_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
""" + THEME_INIT_SCRIPT + """
<link rel="stylesheet" href="{asset_rel}/hljs/styles/github.min.css" data-hljs-theme="light">
<link rel="stylesheet" href="{asset_rel}/hljs/styles/github-dark.min.css" data-hljs-theme="dark" disabled>
<style>
{css}
</style>
</head>
<body>
<div class="shell">
{sidebar}
<div class="page">
<div class="page-top">
{breadcrumb}
<button type="button" id="theme-toggle" class="theme-toggle" aria-label="Toggle color theme">

<span class="theme-toggle-label">Light</span>
</button>
</div>
<main>
"""

PAGE_TAIL = """
</main>
<footer>
<p class="muted">Course &middot; generated offline &middot; no tracking</p>
</footer>
</div>
</div>
<script src="{asset_rel}/mermaid/mermaid.min.js"></script>
<script src="{asset_rel}/hljs/highlight.min.js"></script>
<script src="{asset_rel}/hljs/languages/python.min.js"></script>
<script src="{asset_rel}/hljs/languages/go.min.js"></script>
<script src="{asset_rel}/hljs/languages/bash.min.js"></script>
<script src="{asset_rel}/course.js" defer></script>
</body>
</html>
"""

CSS = """
:root, :root[data-theme="light"] {
  --fg: #1a1a1a;
  --bg: #ffffff;
  --muted: #4d4d4d;
  --border: #ddd;
  --accent: #0b5fff;
  --code-bg: #f6f8fa;
  --chip-bg: #eef2ff;
  --sidebar-bg: #fafbfc;
}
:root[data-theme="dark"] {
  --fg: #e6e6e6;
  --bg: #15171a;
  --muted: #b3b3b3;
  --border: #3a3d42;
  --accent: #7fb0ff;
  --code-bg: #1d2024;
  --chip-bg: #22273a;
  --sidebar-bg: #191b1e;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.55;
}
.shell { display: flex; align-items: flex-start; }
nav.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
  width: 280px;
  flex-shrink: 0;
  background: var(--sidebar-bg);
  border-right: 1px solid var(--border);
  padding: 1.2rem 1rem 2rem;
  font-size: 0.88rem;
}
nav.sidebar .sidebar-title { font-weight: 600; margin-bottom: 0.8rem; }
nav.sidebar .sidebar-title a { color: var(--fg); }
nav.sidebar .sidebar-search { margin-bottom: 1rem; }
nav.sidebar .sidebar-search input {
  width: 100%;
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--fg);
  font-size: 0.85rem;
}
nav.sidebar .sidebar-overview { list-style: none; margin: 0 0 1rem; padding: 0; }
nav.sidebar .sidebar-overview li { margin: 0.15rem 0; }
nav.sidebar .sidebar-overview a { color: var(--muted); display: block; padding: 0.1rem 0.3rem; border-radius: 4px; }
nav.sidebar .sidebar-overview a:hover { color: var(--accent); }
nav.sidebar details.toc-module { margin-bottom: 0.2rem; }
nav.sidebar details.toc-module > summary {
  cursor: pointer;
  font-weight: 600;
  padding: 0.3rem 0.2rem;
  list-style: none;
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
}
nav.sidebar details.toc-module > summary::-webkit-details-marker { display: none; }
nav.sidebar details.toc-module > summary::before {
  content: "";
  display: inline-block;
  width: 0;
  height: 0;
  margin-top: 0.4em;
  border-top: 4px solid transparent;
  border-bottom: 4px solid transparent;
  border-left: 5px solid var(--muted);
  transition: transform 0.15s ease;
  flex-shrink: 0;
}
nav.sidebar details.toc-module[open] > summary::before { transform: rotate(90deg); }
nav.sidebar ul.module-lessons { list-style: none; margin: 0.2rem 0 0.4rem; padding-left: 0.9rem; }
nav.sidebar ul.module-lessons li { margin: 0.1rem 0; }
nav.sidebar ul.module-lessons a {
  color: var(--muted);
  display: flex;
  align-items: baseline;
  gap: 0.45rem;
  padding: 0.12rem 0.3rem;
  border-radius: 4px;
}
nav.sidebar ul.module-lessons a:hover { color: var(--accent); text-decoration: none; }
nav.sidebar ul.module-lessons a.current { color: var(--accent); background: var(--chip-bg); font-weight: 600; }
nav.sidebar ul.module-lessons .lesson-dot {
  display: inline-block;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--muted);
  flex-shrink: 0;
  margin-top: 0.5em;
}
nav.sidebar li.lesson-hidden, nav.sidebar details.module-hidden { display: none; }
.page { max-width: 70ch; margin: 0 auto; padding: 1.5rem 1.5rem 4rem; flex: 1; }
.page-top { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: 1.5rem; }
nav.breadcrumb { font-size: 0.9rem; color: var(--muted); margin-bottom: 0; }
nav.breadcrumb a { color: var(--accent); }
.theme-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.82rem;
  color: var(--fg);
  background: var(--chip-bg);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.3rem 0.7rem;
  cursor: pointer;
  flex-shrink: 0;
}
.theme-toggle:hover { border-color: var(--accent); }
.theme-toggle-glyph { font-size: 0.95rem; line-height: 1; }
h1, h2, h3 { line-height: 1.25; }
h1 { font-size: 1.8rem; margin-top: 0; }
h2 { font-size: 1.3rem; margin-top: 2.2rem; border-bottom: 1px solid var(--border); padding-bottom: .3rem; }
h3 { font-size: 1.05rem; margin-top: 1.6rem; }
p, li { max-width: 70ch; }
.muted { color: var(--muted); font-size: 0.9rem; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
th, td { text-align: left; padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.02em; }
.examples-list { list-style: none; padding: 0; margin: 0.4rem 0 0; }
.examples-list li { padding: 0.35rem 0; border-bottom: 1px dashed var(--border); font-size: 0.92rem; }
.count-chip {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
  color: var(--muted);
}
.tech-group { margin: 1rem 0 1.6rem; }
.tech-group h3 { margin-top: 0.6rem; }
.tech-bar-row { display: flex; align-items: center; gap: 0.6rem; margin: 0.3rem 0; }
.tech-bar-label { width: 11rem; flex-shrink: 0; font-size: 0.92rem; }
.tech-bar-label a { color: var(--fg); scroll-margin-top: 1rem; }
.tech-bar-track { flex: 1; background: var(--chip-bg); border-radius: 4px; height: 0.8rem; overflow: hidden; min-width: 3rem; }
.tech-bar-fill { background: var(--accent); height: 100%; border-radius: 4px; display: block; }
.tech-bar-pct { font-size: 0.82rem; color: var(--muted); white-space: nowrap; width: 3rem; text-align: right; }
.toc-modules { list-style: none; padding: 0; }
.toc-modules > li { margin-bottom: 1.2rem; }
.toc-modules h3 { margin: 0 0 0.3rem; }
.toc-modules .intro { color: var(--muted); font-size: 0.9rem; margin: 0 0 0.5rem; }
.toc-modules ul.concepts { list-style: none; padding-left: 1rem; }
.toc-modules ul.concepts li { margin: 0.25rem 0; }
pre {
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.8rem 1rem;
  overflow-x: auto;
}
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.88rem; }
.code-lang {
  display: block;
  font-size: 0.72rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: -0.4rem;
}
.mermaid-wrap { margin: 1.2rem 0; }
.mermaid { display: none; }
html.js .mermaid { display: block; }
.mermaid-fallback {
  display: block;
  background: var(--code-bg);
  border: 1px dashed var(--border);
  border-radius: 6px;
  padding: 0.8rem 1rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.82rem;
  white-space: pre-wrap;
}
html.js .mermaid-fallback { display: none; }
.sources-list { list-style: none; padding: 0; }
.sources-list li { padding: 0.4rem 0; border-bottom: 1px solid var(--border); font-size: 0.9rem; }
.sources-list .domain { color: var(--muted); font-size: 0.8rem; }
.sources-preview {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.6rem 1rem;
  margin: 0.8rem 0 1.2rem;
  background: var(--sidebar-bg);
}
.sources-preview-title { margin: 0 0 0.3rem; font-size: 0.8rem; text-transform: uppercase; color: var(--muted); letter-spacing: 0.03em; }
.sources-preview .sources-list li:last-child { border-bottom: none; }
.ext-marker { font-size: 0.75em; margin-left: 0.15em; opacity: 0.7; }
a.autolink { text-decoration: underline; text-decoration-style: dotted; text-decoration-thickness: 1px; }
.kind-badge {
  display: inline-block;
  font-size: 0.72rem;
  text-transform: uppercase;
  background: var(--chip-bg);
  color: var(--muted);
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
  margin-right: 0.4rem;
}
.related-list { list-style: none; padding: 0; }
.related-list li { display: inline-block; margin: 0.2rem 0.4rem 0.2rem 0; }
.related-list a {
  display: inline-block;
  padding: 0.2rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 0.85rem;
}
.prevnext { display: flex; justify-content: space-between; margin-top: 2rem; border-top: 1px solid var(--border); padding-top: 1rem; }
.prevnext a { font-size: 0.92rem; }
.tech-tags { margin: 0.6rem 0; }
.tech-tags a, .tech-tags span {
  display: inline-block;
  font-size: 0.8rem;
  padding: 0.1rem 0.5rem;
  margin: 0.1rem 0.3rem 0.1rem 0;
  background: var(--chip-bg);
  border-radius: 999px;
  color: var(--fg);
}
footer { margin-top: 3rem; border-top: 1px solid var(--border); padding-top: 1rem; }
@media print {
  nav.sidebar, nav.breadcrumb, footer, .theme-toggle { display: none; }
  a { color: inherit; text-decoration: underline; }
  .page { max-width: none; }
}
@media (max-width: 820px) {
  .shell { flex-direction: column; }
  nav.sidebar {
    position: static;
    height: auto;
    width: 100%;
    max-height: 40vh;
    border-right: none;
    border-bottom: 1px solid var(--border);
  }
  .page { padding: 1rem 0.9rem 3rem; max-width: none; }
}
@media (max-width: 480px) {
  h1 { font-size: 1.5rem; }
  .tech-bar-label { width: 8rem; }
}
.curriculum-badge {
  display: inline-block;
  font-size: 0.72rem;
  color: var(--muted);
  background: var(--chip-bg);
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
}
.on-this-page {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.8rem 1rem;
  margin: 1rem 0 1.6rem;
  font-size: 0.9rem;
}
.on-this-page .otp-title { font-weight: 600; margin: 0 0 0.3rem; }
.on-this-page ul { list-style: none; padding-left: 0.2rem; margin: 0; }
.on-this-page li { margin: 0.2rem 0; }
.concept-marker-line { color: var(--muted); font-size: 0.88rem; margin: 0.2rem 0 1rem; }
.source-id {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
  color: var(--muted);
  background: var(--chip-bg);
  border-radius: 3px;
  padding: 0.05rem 0.35rem;
}
.evidence-line { color: var(--muted); font-size: 0.88rem; margin: 0.3rem 0; }
.missing-line { color: var(--muted); font-size: 0.88rem; margin: 0.3rem 0; font-style: italic; }
.your-path { border: 1px solid var(--border); border-radius: 8px; padding: 1rem 1.2rem; margin: 1rem 0 2rem; }
.your-path ul { list-style: none; padding-left: 0; margin: 0.4rem 0; }
.your-path li { padding: 0.25rem 0; }
.your-path details { margin-top: 0.8rem; }
.your-path summary { cursor: pointer; color: var(--muted); font-size: 0.9rem; }
.your-path-module { margin: 1rem 0; }
.your-path-module h3 { margin: 0 0 0.3rem; font-size: 1rem; }
.your-path-module:first-of-type { margin-top: 0.4rem; }
.path-number { color: var(--muted); font-size: 0.85rem; margin-right: 0.5rem; font-variant-numeric: tabular-nums; }
.path-wrench { font-size: 0.85rem; opacity: 0.8; margin-left: 0.3rem; }
.practice-path-lead { color: var(--muted); font-size: 0.92rem; margin: 0.2rem 0 1rem; }
.practice-steps { list-style: none; padding: 0; margin: 0.6rem 0 1.2rem; }
.practice-steps li {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.8rem 1rem;
  margin: 0 0 0.8rem;
}
.practice-steps li:last-child { margin-bottom: 0; }
.practice-step-title { margin: 0 0 0.4rem; font-weight: 600; }
.practice-step-title .path-number { font-weight: 400; }
.practice-step-why { margin: 0 0 0.5rem; }
.practice-step-source { font-size: 0.85rem; color: var(--muted); margin: 0.3rem 0; }
.practice-step-done { color: var(--muted); font-size: 0.85rem; font-style: italic; margin: 0.3rem 0 0; }
.practice-link-button {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.92rem;
  font-weight: 600;
  color: var(--accent);
  background: var(--chip-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.55rem 1rem;
  margin: 0 0 1.4rem;
  text-decoration: none;
}
.practice-link-button:hover { border-color: var(--accent); text-decoration: none; }
.practice-link-button-end { margin: 2rem 0 0; }
nav.sidebar ul.module-lessons .lesson-practice-link {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  color: var(--muted);
  font-size: 0.82rem;
  padding: 0.05rem 0.3rem 0.1rem 1.1rem;
  text-decoration: none;
}
nav.sidebar ul.module-lessons .lesson-practice-link:hover { color: var(--accent); text-decoration: none; }
nav.sidebar ul.module-lessons .lesson-practice-link.current { color: var(--accent); font-weight: 600; }
.practice-lead { color: var(--muted); font-size: 0.92rem; margin: 0.2rem 0 1.4rem; }
.practice-section-title { margin-top: 2.2rem; }
.practice-back-link { margin-top: 2rem; }
"""


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def postings_label(concept: dict) -> str:
    postings = concept.get("postings_in_90d", 0)
    if postings:
        return f'<span class="count-chip">{postings} postings</span>'
    if concept.get("source") == "curriculum":
        return '<span class="curriculum-badge">field curriculum</span>'
    return ""


def subtopic_anchor(sub_id: str) -> str:
    return f"subtopic-{slugify(sub_id)}"


def render_sidebar(course: dict, modules: list[dict], current_page: str, paths: dict[str, dict] | None = None) -> str:
    paths = paths or {}
    role = course.get("role", "Course")
    parts = ['<nav class="sidebar">']
    index_class = ' class="current"' if current_page == "index.html" else ""
    parts.append(f'<div class="sidebar-title"><a href="index.html"{index_class}>{esc(role)}</a></div>')

    parts.append('<div class="sidebar-search">')
    parts.append(
        '<input type="search" id="sidebar-search-input" placeholder="Search content" '
        'autocomplete="off" aria-label="Search content">'
    )
    parts.append("</div>")

    parts.append('<ul class="sidebar-overview">')
    parts.append('<li><a href="index.html#your-path">Your path</a></li>')
    parts.append('<li><a href="index.html#expectations">Expectations</a></li>')
    parts.append('<li><a href="index.html#technologies">Technologies</a></li>')
    parts.append("</ul>")

    for mi, module in enumerate(modules, start=1):
        module_title = module.get("title", module.get("id", ""))
        parts.append('<details class="toc-module" open>')
        parts.append(f'<summary data-module-title="{esc(module_title.lower())}">{mi}. {esc(module_title)}</summary>')
        parts.append('<ul class="module-lessons">')
        for ci, concept in enumerate(module.get("concepts", []), start=1):
            cid = concept.get("id", "")
            href = concept_page_filename(cid)
            cls = " current" if href == current_page else ""
            title = concept.get("title", cid)
            numbered_title = f"{mi}.{ci} {title}"
            has_path = cid in paths
            practice_line = ""
            if has_path:
                practice_href = practice_page_filename(cid)
                practice_cls = " current" if practice_href == current_page else ""
                practice_line = (
                    f'<a href="{esc(practice_href)}" class="lesson-practice-link{practice_cls}">'
                    f'Practice</a>'
                )
            parts.append(
                f'<li data-lesson-title="{esc(numbered_title.lower())}">'
                f'<a href="{esc(href)}" class="lesson-link{cls}">'
                f'<span class="lesson-dot"></span><span>{esc(numbered_title)}</span></a>'
                f'{practice_line}</li>'
            )
        parts.append("</ul>")
        parts.append("</details>")
    parts.append("</nav>")
    return "\n".join(parts)


def render_breadcrumb(current: str, role: str) -> str:
    if current == "index":
        return f'<nav class="breadcrumb">{esc(role)}</nav>'
    return f'<nav class="breadcrumb"><a href="index.html">{esc(role)}</a></nav>'


def render_practice_breadcrumb(concept: dict) -> str:
    href = concept_page_filename(concept["id"])
    title = concept.get("title", concept["id"])
    return f'<nav class="breadcrumb"><a href="{esc(href)}">{esc(title)}</a></nav>'


def render_examples(examples: list[dict]) -> str:
    items = []
    for ex in examples:
        text = ex.get("text") or ex.get("sentence") or ""
        company = ex.get("company", "")
        title = ex.get("title", "")
        meta = " &middot; ".join(esc(x) for x in (company, title) if x)
        items.append(f'<li>&ldquo;{esc(text)}&rdquo; <span class="muted">({meta})</span></li>')
    return f'<ul class="examples-list">{"".join(items)}</ul>'


def bar_width_style(percent: float) -> str:
    width = max(percent, 2) if percent > 0 else 0
    return f"width:{width}%"


def render_expectations_table(groups: dict[str, list[dict]]) -> str:
    out = []
    for section_title, items in groups.items():
        if not items:
            continue
        out.append(f"<h3>{esc(section_title)}</h3>")
        ranked = sorted(items, key=lambda it: it.get("percent", 0), reverse=True)
        rows = []
        for item in ranked:
            display = esc(item.get("display", ""))
            percent = item.get("percent", 0)
            examples_html = render_examples(item.get("examples", []))
            rows.append(
                "<tr><td>"
                '<div class="tech-bar-row">'
                f'<span class="tech-bar-label">{display}</span>'
                f'<span class="tech-bar-track"><span class="tech-bar-fill" style="{bar_width_style(percent)}"></span></span>'
                f'<span class="tech-bar-pct">{percent}%</span>'
                "</div>"
                f"</td><td>{examples_html}</td></tr>"
            )
        out.append(
            "<table><thead><tr><th>Responsibility</th><th>Examples</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    return "\n".join(out)


def render_technologies(technologies: dict[str, list[dict]]) -> str:
    out = []
    for category, items in technologies.items():
        out.append('<div class="tech-group">')
        out.append(f"<h3>{esc(category)}</h3>")
        ranked = sorted(items, key=lambda it: it.get("percent", 0), reverse=True)
        for item in ranked:
            display = item.get("display", "")
            percent = item.get("percent", 0)
            anchor = tech_anchor(display)
            out.append(
                '<div class="tech-bar-row">'
                f'<span class="tech-bar-label"><a id="{esc(anchor)}" href="#{esc(anchor)}">{esc(display)}</a></span>'
                f'<span class="tech-bar-track"><span class="tech-bar-fill" style="{bar_width_style(percent)}"></span></span>'
                f'<span class="tech-bar-pct">{percent}%</span>'
                "</div>"
            )
        out.append("</div>")
    return "\n".join(out)


def render_toc(modules: list[dict]) -> str:
    out = ['<ul class="toc-modules">']
    for mi, module in enumerate(modules, start=1):
        out.append("<li>")
        out.append(f"<h3>{mi}. {esc(module.get('title', module.get('id', '')))}</h3>")
        intro = module.get("intro", "")
        if intro:
            out.append(f'<p class="intro">{esc(intro)}</p>')
        out.append('<ul class="concepts">')
        for concept in module.get("concepts", []):
            cid = concept.get("id", "")
            href = concept_page_filename(cid)
            label = postings_label(concept)
            out.append(
                f'<li><a href="{esc(href)}">{esc(concept.get("title", cid))}</a> {label}</li>'
            )
        out.append("</ul>")
        out.append("</li>")
    out.append("</ul>")
    return "\n".join(out)


def render_your_path(modules: list[dict]) -> str:
    """Grouped by numbered module, numbered lessons (M.N) for NEW/SOME concepts
    only; KNOWN concepts collapse under a per-module 'Skim' <details>. No
    subtopics are listed here.
    """

    def entry_line(number: str, title: str, href: str) -> str:
        return f'<li><span class="path-number">{esc(number)}</span><a href="{esc(href)}">{esc(title)}</a></li>'

    out = ['<div class="your-path" id="your-path">', "<h2>Your path</h2>"]
    any_active = False

    for mi, module in enumerate(modules, start=1):
        module_title = module.get("title", module.get("id", ""))
        active_items: list[str] = []
        known_items: list[str] = []

        for ci, concept in enumerate(module.get("concepts", []), start=1):
            cid = concept.get("id", "")
            href = concept_page_filename(cid)
            number = f"{mi}.{ci}"
            marker = concept.get("marker") or {}
            label = marker.get("marker")
            line = entry_line(number, concept.get("title", cid), href)
            if label == "KNOWN":
                known_items.append(line)
            elif label in ("SOME", "NEW"):
                active_items.append(line)

        if not active_items and not known_items:
            continue

        out.append('<div class="your-path-module">')
        out.append(f"<h3>{mi}. {esc(module_title)}</h3>")
        if active_items:
            any_active = True
            out.append(f'<ul class="path-list">{"".join(active_items)}</ul>')
        if known_items:
            out.append("<details>")
            out.append("<summary>Skim (you have production evidence)</summary>")
            out.append(f'<ul class="path-list">{"".join(known_items)}</ul>')
            out.append("</details>")
        out.append("</div>")

    if not any_active:
        out.insert(2, '<p class="muted">Nothing new or partial &mdash; see the skim lists below.</p>')

    out.append("</div>")
    return "\n".join(out)


#: Why a lesson, a module or a practice path is not in the course, in plain words (``course.json`` ``dropped[].reason``).
DROPPED_REASONS = {
    "no_verified_source": "no source page for it could be verified",
    "module_text_failed": "its text did not pass the checks, twice",
    "path_unverified": "too few of its practice steps could be verified",
    "path_invalid": "its practice path did not pass the checks",
    "cancelled": "the course was stopped before it was written",
    "cap_reached": "the course reached its cap of model calls or page fetches before it was written",
}


def render_not_included(dropped: list[dict]) -> str:
    """The index page's 'Not included' list: what was planned and is not in the course, and why. ``""`` when nothing was dropped."""

    rows = []
    for item in dropped or []:
        if not isinstance(item, dict):
            continue
        kind, _, name = str(item.get("what", "")).partition(":")
        label = {"lesson": "Lesson", "module": "Module", "path": "Practice path for"}.get(kind, "Item")
        title = str(item.get("title") or name or item.get("what") or "").strip()
        if not title:
            continue
        reason = str(item.get("reason", ""))
        why = DROPPED_REASONS.get(reason, reason.replace("_", " ") or "it did not pass the checks")
        lessons = item.get("lessons")
        count = f" ({len(lessons)} lessons)" if isinstance(lessons, list) and lessons else ""
        rows.append(f"<li>{esc(label)} {esc(title)}{esc(count)}: {esc(why)}</li>")
    if not rows:
        return ""
    return (
        '<h2 id="not-included">Not included</h2>\n'
        '<p class="muted">Planned for this course and left out, because nothing unverified is shown.</p>\n'
        "<ul>\n" + "\n".join(rows) + "\n</ul>"
    )


def render_index(course: dict, modules: list[dict]) -> str:
    role = course.get("role", "Role")
    corpus = course.get("corpus", {})
    body = [f"<h1>{esc(role)}</h1>"]

    postings_90d = corpus.get("postings_90d", 0)
    postings_any = corpus.get("postings_any_date", 0)
    scope = corpus.get("scope", "")
    corpus_line = f"Based on {postings_90d} postings in the last 90 days ({postings_any} any date)"
    if scope:
        corpus_line += f" &middot; {esc(scope)}"
    body.append(f'<p class="muted">{corpus_line}</p>')

    your_path = render_your_path(modules)
    if your_path:
        body.append(your_path)

    expectations = course.get("expectations", {})
    body.append('<h2 id="expectations">Expectations</h2>')
    body.append(
        render_expectations_table(
            {
                "Responsibilities": expectations.get("responsibilities", []),
                "Scope at staff level": expectations.get("scope_at_staff", []),
            }
        )
    )

    body.append('<h2 id="technologies">Technologies</h2>')
    body.append(render_technologies(course.get("technologies", {})))

    body.append("<h2>Concepts</h2>")
    body.append(render_toc(modules))

    not_included = render_not_included(course.get("dropped", []))
    if not_included:
        body.append(not_included)

    return "\n".join(body)


def render_detail_sections(detail: list[dict], tool_links: dict[str, str], used: set[str]) -> str:
    out = []
    for section in detail:
        heading = section.get("heading", "")
        body = section.get("body", "")
        if heading:
            out.append(f"<h3>{esc(heading)}</h3>")
        out.append(f"<p>{autolink_text(body, tool_links, used)}</p>")
    return "\n".join(out)


def render_code_block(block: dict) -> str:
    lang = esc(block.get("lang", "text"))
    code = esc(block.get("code", ""))
    return (
        f'<span class="code-lang">{lang}</span>'
        f'<pre><code class="language-{lang}" data-lang="{lang}">{code}</code></pre>'
    )


def render_mermaid(diagram: dict) -> str:
    source = diagram.get("source", "")
    return (
        '<div class="mermaid-wrap">'
        f'<pre class="mermaid">{esc(source)}</pre>'
        f'<pre class="mermaid-fallback">{esc(source)}</pre>'
        "</div>"
    )


def render_sources(sources: list[dict]) -> str:
    items = []
    for src in sources:
        title = src.get("title", src.get("url", ""))
        url = src.get("url", "")
        kind = src.get("kind", "")
        domain = domain_of(url)
        items.append(
            "<li>"
            f'<span class="kind-badge">{esc(kind)}</span>'
            f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(title)}</a> '
            f'<span class="domain">({esc(domain)})</span>'
            "</li>"
        )
    return f'<ul class="sources-list">{"".join(items)}</ul>'


def render_sources_preview(sources: list[dict]) -> str:
    preview = sources[:3]
    if not preview:
        return ""
    return (
        '<div class="sources-preview">'
        '<p class="sources-preview-title">Read more</p>'
        f"{render_sources(preview)}"
        "</div>"
    )


def render_related(related: list[str], concepts_by_id: dict[str, dict]) -> str:
    items = []
    for rid in related:
        target = concepts_by_id.get(rid)
        title = target.get("title", rid) if target else rid
        href = concept_page_filename(rid)
        items.append(f'<li><a href="{esc(href)}">{esc(title)}</a></li>')
    return f'<ul class="related-list">{"".join(items)}</ul>'


def index_tech_anchors(course: dict) -> set[str]:
    """The anchors the index page's Technologies section has (``render_technologies``): one per listed display name."""

    return {
        tech_anchor(item.get("display", item.get("id", "")))
        for items in course.get("technologies", {}).values()
        for item in items
    }


def render_tech_tags(tech_names: list[str], technology_links: dict[str, str], index_anchors: set[str] | None = None) -> str:
    """A lesson's technology names. A name the index page lists links to it there; any other name is plain text.

    A lesson's names are a model's own words, so a name need not be one the index lists: a link to an anchor that
    is not there would fail the internal link audit and with it the whole course. ``index_anchors`` ``None``
    (a caller that does not say what the index has): every name links, as before.
    """

    links = []
    for name in tech_names:
        url = technology_links.get(name)
        if url:
            links.append(
                f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(name)}'
                f'</a>'
            )
        elif index_anchors is not None and tech_anchor(name) not in index_anchors:
            links.append(f"<span>{esc(name)}</span>")
        else:
            links.append(f'<a href="index.html#{esc(tech_anchor(name))}">{esc(name)}</a>')
    return f'<div class="tech-tags">{"".join(links)}</div>'


def render_subtopic(sub: dict, tool_links: dict[str, str], used: set[str]) -> str:
    sid = sub.get("id", "")
    title = sub.get("title", sid)
    label = sub.get("marker")
    parts = [f'<h3 id="{esc(subtopic_anchor(sid))}">{esc(title)}</h3>']

    body_sections = render_detail_sections(sub.get("body", []), tool_links, used)

    if label == "KNOWN":
        evidence = sub.get("evidence") or {}
        eid = evidence.get("id", "")
        line = evidence.get("line", "")
        parts.append(f'<p class="evidence-line">Production evidence: <span class="source-id">{esc(eid)}</span> &ldquo;{esc(line)}&rdquo;</p>')
        if body_sections:
            parts.append("<details><summary>Details</summary>")
            parts.append(body_sections)
            parts.append("</details>")
    elif label == "SOME":
        missing = sub.get("missing", "")
        if missing:
            parts.append(f'<p class="missing-line">Missing: {esc(missing)}</p>')
        evidence = sub.get("evidence") or {}
        eid = evidence.get("id", "")
        line = evidence.get("line", "")
        if eid:
            parts.append(f'<p class="evidence-line">Production evidence: <span class="source-id">{esc(eid)}</span> &ldquo;{esc(line)}&rdquo;</p>')
        if body_sections:
            parts.append(body_sections)
    else:
        if body_sections:
            parts.append(body_sections)

    return "\n".join(parts)


def concept_marker_line(concept: dict) -> str:
    """Quiet muted line for KNOWN/SOME concepts: 'You have: <evidence> · Missing: <missing>'.

    Falls back to the first SOME/KNOWN subtopic's evidence/missing when the
    concept-level marker has neither. NEW gets nothing.
    """
    marker = concept.get("marker") or {}
    label = marker.get("marker")
    evidence = marker.get("evidence") or {}
    missing = marker.get("missing", "")
    line = evidence.get("line", "")

    if label not in ("KNOWN", "SOME"):
        return ""

    if not line and not missing:
        for sub in concept.get("subtopics", []):
            sub_label = sub.get("marker")
            if sub_label in ("SOME", "KNOWN"):
                sub_evidence = sub.get("evidence") or {}
                line = sub_evidence.get("line", "")
                missing = sub.get("missing", "")
                if line or missing:
                    break

    if not line and not missing:
        return ""

    parts = []
    if line:
        parts.append(f"You have: {esc(line)}")
    if missing:
        parts.append(f"Missing: {esc(missing)}")
    return f'<p class="concept-marker-line">{" &middot; ".join(parts)}</p>'


def render_on_this_page(subtopics: list[dict]) -> str:
    if not subtopics:
        return ""
    items = []
    for sub in subtopics:
        sid = sub.get("id", "")
        title = sub.get("title", sid)
        items.append(f'<li><a href="#{esc(subtopic_anchor(sid))}">{esc(title)}</a></li>')
    return (
        '<div class="on-this-page">'
        '<p class="otp-title">On this page</p>'
        f'<ul>{"".join(items)}</ul>'
        "</div>"
    )


def render_practice_link_button(concept_id: str, extra_class: str = "") -> str:
    href = practice_page_filename(concept_id)
    cls = f"practice-link-button {extra_class}".strip()
    return f'<a href="{esc(href)}" class="{esc(cls)}">Practice this</a>'


def render_practice_steps(steps: list[dict]) -> str:
    ranked = sorted(steps, key=lambda s: s.get("n", 0))
    items = []
    for step in ranked:
        n = step.get("n", "")
        do = esc(step.get("do", ""))
        why = esc(step.get("why", ""))
        source = step.get("source") or {}
        title = source.get("title", source.get("url", ""))
        url = source.get("url", "")
        domain = domain_of(url)
        done_when = step.get("done_when", "")
        parts = [
            f'<p class="practice-step-title"><span class="path-number">{esc(n)}.</span> {do}</p>',
        ]
        if why:
            parts.append(f'<p class="practice-step-why">{why}</p>')
        if url:
            parts.append(
                '<p class="practice-step-source">Explained in: '
                f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(title)} ({esc(domain)})</a></p>'
            )
        if done_when:
            parts.append(f'<p class="practice-step-done">Done when: {esc(done_when)}</p>')
        items.append(f"<li>{''.join(parts)}</li>")
    return f'<ul class="practice-steps">{"".join(items)}</ul>'


def render_practice_page(concept: dict, path: dict) -> str:
    title = concept.get("title", concept.get("id", ""))
    tool = path.get("tool", "")
    why_tool = path.get("why_this_tool", "")
    alternatives = path.get("alternatives", "")

    body = [f"<h1>Practice: {esc(title)}</h1>"]

    lead_parts = []
    if tool:
        lead_parts.append(f"Main tool: {esc(tool)}")
    if why_tool:
        lead_parts.append(esc(why_tool))
    if alternatives:
        lead_parts.append(f"Alternatives: {esc(alternatives)}")
    if lead_parts:
        body.append(f'<p class="practice-lead">{" &middot; ".join(lead_parts)}</p>')

    setup = path.get("setup", [])
    if setup:
        body.append('<h2 class="practice-section-title">Setup (one time)</h2>')
        body.append(render_practice_steps(setup))

    steps = path.get("steps", [])
    if steps:
        body.append('<h2 class="practice-section-title">Steps</h2>')
        body.append(render_practice_steps(steps))

    concept_href = concept_page_filename(concept["id"])
    body.append(
        f'<p class="practice-back-link"><a href="{esc(concept_href)}">Back to {esc(title)}</a></p>'
    )

    return "\n".join(body)


def render_concept_page(
    concept: dict,
    concepts_by_id: dict[str, dict],
    prev_concept: dict | None,
    next_concept: dict | None,
    tool_links: dict[str, str],
    path: dict | None = None,
    index_anchors: set[str] | None = None,
) -> str:
    used_tools: set[str] = set()
    title = concept.get("title", concept.get("id", ""))
    label = postings_label(concept)
    body = [f"<h1>{esc(title)}</h1>"]
    if label:
        body.append(f'<p class="muted">{label}</p>')
    marker_line = concept_marker_line(concept)
    if marker_line:
        body.append(marker_line)

    if path:
        body.append(render_practice_link_button(concept["id"]))

    subtopics = concept.get("subtopics", [])
    on_this_page = render_on_this_page(subtopics)
    if on_this_page:
        body.append(on_this_page)

    sources = (path.get("sources") if path else None) or concept.get("sources", [])
    body.append(f"<p>{autolink_text(concept.get('summary', ''), tool_links, used_tools)}</p>")
    body.append(render_sources_preview(sources))

    detail = concept.get("detail", [])
    if detail:
        body.append(render_detail_sections(detail, tool_links, used_tools))

    in_practice = concept.get("in_practice", "")
    if in_practice:
        body.append("<h2>How it is done in practice</h2>")
        body.append(f"<p>{autolink_text(in_practice, tool_links, used_tools)}</p>")

    technologies = concept.get("technologies", [])
    if technologies:
        body.append("<h2>Technologies</h2>")
        body.append(render_tech_tags(technologies, concept.get("technology_links", {}), index_anchors))

    diagram = concept.get("diagram")
    if diagram:
        body.append("<h2>Diagram</h2>")
        body.append(render_mermaid(diagram))

    code_blocks = concept.get("code", [])
    if code_blocks:
        body.append("<h2>Code</h2>")
        for block in code_blocks:
            body.append(render_code_block(block))

    if subtopics:
        body.append("<h2>Subtopics</h2>")
        for sub in subtopics:
            body.append(render_subtopic(sub, tool_links, used_tools))

    body.append("<h2>Sources</h2>")
    body.append(render_sources(sources))

    related = concept.get("related", [])
    if related:
        body.append("<h2>Related concepts</h2>")
        body.append(render_related(related, concepts_by_id))

    if path:
        body.append(render_practice_link_button(concept["id"], extra_class="practice-link-button-end"))

    prev_href = f'<a href="{esc(concept_page_filename(prev_concept["id"]))}">{esc(prev_concept.get("title", ""))}</a>' if prev_concept else "<span></span>"
    next_href = f'<a href="{esc(concept_page_filename(next_concept["id"]))}">{esc(next_concept.get("title", ""))}</a>' if next_concept else "<span></span>"
    body.append(f'<div class="prevnext">{prev_href}{next_href}</div>')

    return "\n".join(body)


# ---------------------------------------------------------------------------
# Input sanitation (glyph gate, section 5)
# ---------------------------------------------------------------------------

def _strip_invisible_in(value: object) -> tuple[object, int]:
    """Recursively strip invisible Unicode from every string in a JSON-like value; returns (value, removed count)."""

    if isinstance(value, str):
        cleaned, removed = strip_invisible(value)
        return cleaned, removed
    if isinstance(value, list):
        removed_total = 0
        out = []
        for item in value:
            cleaned, removed = _strip_invisible_in(item)
            out.append(cleaned)
            removed_total += removed
        return out, removed_total
    if isinstance(value, dict):
        removed_total = 0
        out = {}
        for key, item in value.items():
            cleaned, removed = _strip_invisible_in(item)
            out[key] = cleaned
            removed_total += removed
        return out, removed_total
    return value, 0


def sanitize_course(course: dict) -> tuple[dict, int]:
    """Strip invisible Unicode from every string in ``course`` before it is rendered; returns (clean course, count)."""

    cleaned, removed = _strip_invisible_in(course)
    assert isinstance(cleaned, dict)
    return cleaned, removed


def sanitize_paths(paths: dict[str, dict]) -> tuple[dict[str, dict], int]:
    cleaned, removed = _strip_invisible_in(paths)
    assert isinstance(cleaned, dict)
    return cleaned, removed


def _bad_glyphs(text: str) -> list[str]:
    """Code points in ``text`` outside ASCII, Latin-1 punctuation and the renderer's own typographic glyphs."""

    bad = []
    for ch in text:
        code = ord(ch)
        if code < 128:
            continue
        if ch in _ALLOWED_EXTRA:
            continue
        if 0xA0 <= code <= 0xFF:
            continue
        bad.append(ch)
    return bad


def check_glyphs(html_text: str, where: str) -> list[str]:
    """Every code point outside the allowed set fails the render (design section 5: no emoji, no stray glyph)."""

    bad = sorted(set(_bad_glyphs(html_text)))
    if not bad:
        return []
    shown = ", ".join(f"U+{ord(ch):04X} {ch!r}" for ch in bad[:10])
    return [f"{where}: disallowed glyph(s) outside ASCII/Latin-1/typographic quotes: {shown}"]


# ---------------------------------------------------------------------------
# Internal link audit (no browser: html.parser over the finished output)
# ---------------------------------------------------------------------------

class _LinkCollector(HTMLParser):
    """Collects every ``href``'s target and every element ``id``/``name`` of one page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str | None]] = []  # (path or "", fragment or None)
        self.ids: set[str] = set()
        self.external: list[tuple[str, bool]] = []  # (url, has target=_blank)
        self.inline_scripts = 0
        self.on_attrs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        for name in attr_map:
            if name.lower().startswith("on"):
                self.on_attrs.append(name.lower())
        for name in ("id", "name"):
            value = attr_map.get(name)
            if value:
                self.ids.add(value)
        if tag == "a":
            href = attr_map.get("href")
            if href:
                if href.startswith(("http://", "https://")):
                    target = attr_map.get("target")
                    self.external.append((href, target == "_blank"))
                elif href.startswith("#"):
                    self.links.append(("", href[1:] or None))
                elif not href.startswith(("mailto:", "javascript:", "data:")):
                    path, _, frag = href.partition("#")
                    self.links.append((path, frag or None))
        if tag == "script":
            src = attr_map.get("src")
            if src is None:
                self.inline_scripts += 1


def audit_links(out_dir: Path) -> list[str]:
    """Internal link audit (design section 5): every ``href``/``#anchor`` resolves to a file and an id.

    A pure function over the finished output folder, parsed with
    :mod:`html.parser` (no browser). Returns a sorted list of problems; ``[]``
    on a clean site. Also flags an external link with no ``target="_blank"``,
    an inline ``<script>`` block and any ``on*=`` attribute (the slice-1 CSP
    runs neither).
    """

    problems: list[str] = []
    pages = sorted(p.relative_to(out_dir).as_posix() for p in out_dir.glob("*.html"))
    ids_by_page: dict[str, set[str]] = {}
    collected: dict[str, _LinkCollector] = {}
    for page in pages:
        collector = _LinkCollector()
        collector.feed((out_dir / page).read_text(encoding="utf-8"))
        collected[page] = collector
        ids_by_page[page] = collector.ids

    for page in pages:
        collector = collected[page]
        if collector.inline_scripts:
            problems.append(f"{page}: {collector.inline_scripts} inline <script> block(s) are not allowed")
        for attr in sorted(set(collector.on_attrs)):
            problems.append(f"{page}: an {attr}= attribute is not allowed")
        for url, is_blank in collector.external:
            if not is_blank:
                problems.append(f"{page}: external link '{url}' has no target=\"_blank\"")
        for path, frag in collector.links:
            target_page = path or page
            if target_page not in ids_by_page:
                problems.append(f"{page}: href target '{path or '#' + (frag or '')}' does not resolve to a file in the site")
                continue
            if frag and frag not in ids_by_page[target_page]:
                problems.append(f"{page}: anchor '#{frag}' does not resolve to an id in '{target_page}'")
    return sorted(problems)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RenderResult:
    out_dir: Path
    pages: int
    size_bytes: int
    stripped_invisible: int


def build_course(course: dict, out_dir: Path, paths: dict[str, dict] | None = None, *, assets_src: Path | None = None) -> RenderResult:
    """Render ``course`` (already validated) into ``out_dir``; deterministic byte for byte on the same input.

    ``assets_src`` is the folder of vendored assets (mermaid, hljs, course.js,
    theme-init.js) to copy per course; defaults to the package's own
    ``data/learning/assets`` (``package_assets_dir``).
    """

    course, stripped = sanitize_course(course)
    paths = paths or {}
    paths, stripped_paths = sanitize_paths(paths)
    stripped += stripped_paths

    role = course.get("role", "Role")
    modules = course.get("modules", [])

    flat_concepts: list[dict] = []
    for module in modules:
        for concept in module.get("concepts", []):
            flat_concepts.append(concept)
    concepts_by_id = {c["id"]: c for c in flat_concepts if c.get("id")}

    out_dir.mkdir(parents=True, exist_ok=True)

    glyph_errors: list[str] = []
    written_pages: list[str] = []

    def write_page(filename: str, body: str, title: str, current: str, breadcrumb: str | None = None) -> None:
        sidebar = render_sidebar(course, modules, filename, paths)
        if breadcrumb is None:
            breadcrumb = render_breadcrumb(current, role)
        html_out = (
            PAGE_HEAD.format(title=esc(title), asset_rel=ASSET_REL, css=CSS, sidebar=sidebar, breadcrumb=breadcrumb)
            + body
            + PAGE_TAIL.format(asset_rel=ASSET_REL)
        )
        glyph_errors.extend(check_glyphs(html_out, filename))
        encoded = html_out.encode("utf-8")
        if len(encoded) >= MAX_HTML_BYTES:
            glyph_errors.append(f"{filename}: page is {len(encoded)} bytes, at or over the {MAX_HTML_BYTES}-byte cap")
        (out_dir / filename).write_bytes(encoded)
        written_pages.append(filename)

    tool_links = course.get("tool_links", {})

    write_page("index.html", render_index(course, modules), role, "index")

    for i, concept in enumerate(flat_concepts):
        prev_c = flat_concepts[i - 1] if i > 0 else None
        next_c = flat_concepts[i + 1] if i < len(flat_concepts) - 1 else None
        path = paths.get(concept.get("id", ""))
        page_body = render_concept_page(concept, concepts_by_id, prev_c, next_c, tool_links, path, index_tech_anchors(course))
        write_page(
            concept_page_filename(concept["id"]),
            page_body,
            concept.get("title", concept["id"]),
            concept["id"],
        )
        if path:
            practice_title = f"Practice: {concept.get('title', concept['id'])}"
            write_page(
                practice_page_filename(concept["id"]),
                render_practice_page(concept, path),
                practice_title,
                practice_page_filename(concept["id"]),
                breadcrumb=render_practice_breadcrumb(concept),
            )

    if glyph_errors:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise CourseError(sorted(glyph_errors))

    link_problems = audit_links(out_dir)
    if link_problems:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise CourseError(link_problems)

    site_bytes = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file())
    if site_bytes >= MAX_SITE_BYTES_NO_ASSETS:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise CourseError([f"course: {site_bytes} bytes without assets, at or over the {MAX_SITE_BYTES_NO_ASSETS}-byte cap"])

    assets_src = assets_src if assets_src is not None else package_assets_dir()
    assets_dst = out_dir / "assets"
    if assets_src.exists():
        if assets_dst.exists():
            shutil.rmtree(assets_dst)
        shutil.copytree(assets_src, assets_dst)

    total_bytes = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file())
    return RenderResult(out_dir=out_dir, pages=len(written_pages), size_bytes=total_bytes, stripped_invisible=stripped)


def package_assets_dir() -> Path:
    """The vendored renderer assets shipped with the installed package (``importlib.resources``)."""

    from importlib import resources

    return Path(str(resources.files("gigai.scout") / "data" / "learning" / "assets"))


def total_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def render_to_folder(course_path: Path, out_dir: Path, paths_dir: Path | None = None) -> RenderResult:
    """Read ``course_path``, validate, render into ``out_dir``. Raises ``CourseError`` with every problem found."""

    try:
        course = json.loads(Path(course_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CourseError([f"cannot read course JSON: {exc}"]) from exc

    concept_ids: set[str] = set()
    for module in course.get("modules", []):
        for concept in module.get("concepts", []):
            cid = concept.get("id")
            if cid:
                concept_ids.add(cid)
    paths = load_paths(Path(paths_dir) if paths_dir is not None else None, concept_ids)

    errors = validate_course(course, paths)
    if errors:
        raise CourseError(sorted(errors))

    return build_course(course, Path(out_dir), paths)


__all__ = [
    "ASSET_REL",
    "MAX_HTML_BYTES",
    "MAX_LESSONS",
    "MAX_SITE_BYTES_NO_ASSETS",
    "CourseError",
    "RenderResult",
    "audit_links",
    "build_course",
    "check_glyphs",
    "human_size",
    "load_paths",
    "package_assets_dir",
    "render_to_folder",
    "sanitize_course",
    "sanitize_paths",
    "strip_invisible",
    "total_size",
    "validate_course",
    "validate_marker",
    "validate_path",
]
