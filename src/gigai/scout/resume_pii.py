"""0.1.10-001: a local, no-model heads-up for contact details in a resume.

Scout sends the resume text to the model the user picked and does not remove
personal info (README, Privacy and security). This check only *notices* the
obvious shapes -- an email address, a phone number, a linkedin.com or
github.com link, a street address -- so the user can be told before adding
the resume. An empty result means "nothing obvious found", never "clean":
callers must say nothing in that case.
"""

from __future__ import annotations

import re

RESUME_WARNING = (
    "Remove your personal info before adding a resume: name, email, phone, street address and links. "
    "Scout sends your resume text to the model you pick (Codex -> OpenAI, Claude -> Anthropic, "
    "OpenRouter -> your provider) to assess postings and tailor your resume, and it does not remove "
    "personal info for you yet."
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
