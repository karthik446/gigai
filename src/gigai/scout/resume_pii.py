"""0.1.10-001: a local, no-model heads-up for contact details in a resume.

Scout removes the name and contact lines from the resume text before it goes to
the model the user picked, but cannot catch everything (README, Privacy and
security). This check only *notices* the
obvious shapes -- an email address, a phone number, a linkedin.com or
github.com link, a street address -- so the user can be told before adding
the resume. An empty result means "nothing obvious found", never "clean":
callers must say nothing in that case.
"""

from __future__ import annotations

import re

RESUME_WARNING = (
    "Scout removes your name and contact lines (email, phone, address, links) before sending your resume to "
    "the model you pick (Codex -> OpenAI, Claude -> Anthropic, OpenRouter -> your provider), and adds them back "
    "only in your PDF, on this machine. It can't catch personal details elsewhere in the text (a first line that holds both a title and your name, or contact details inside a sentence), so keep those out. "
    "Your contact line lives in Settings > Resume display."
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_PHONE = re.compile(
    r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?!\d)"
    r"|(?<![\w.])\+\d{1,3}(?:[\s.-]?\d{2,4}){2,4}(?!\d)"
)
_LINK = re.compile(r"(?:linkedin|github)\.com/", re.IGNORECASE)
_ADDRESS = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][A-Za-z.']*\s+){1,3}"
    r"(?i:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|way|place|pl)\b"
)

# Shown label -> pattern, in the order the heads-up lists them.
_CHECKS = (("email", _EMAIL), ("phone", _PHONE), ("links", _LINK), ("address", _ADDRESS))


def detect_contact_details(text: str) -> list[str]:
    """Labels of the contact-detail shapes found in ``text`` (``[]`` when none)."""

    return [label for label, pattern in _CHECKS if pattern.search(text)]


def heads_up(found: list[str]) -> str | None:
    """The heads-up line for ``found``; ``None`` when nothing was found."""

    if not found:
        return None
    return f"This resume seems to contain: {', '.join(found)}. Remove them before continuing?"
