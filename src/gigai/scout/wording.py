"""0.1.10.7 K: the one-line notices Scout shows beside its numbers, labels and outputs.

One constant per line, so the UI, the docs, the README and the agent skill quote the same words
(``tests/behaviors/ci_tooling/test_wording_one_source.py`` compares them). The Scout label's and
the Scout ATS score's lines are spelled out here too and keep their names where they are used:
``pipeline.steps.LABEL_WORDING`` and ``ats_score.ATS_WORDING`` import them from this module.

A leaf module: it imports nothing, so anything may import it.
"""

from __future__ import annotations

#: Beside the Scout label ("recommended" / "needs attention").
LABEL_WORDING = (
    "Scout's own suggestion from your settings, resume and answers. Not a prediction of what an employer will decide."
)

#: Beside the Scout ATS score. The words are 0.1.10.7 D's, unchanged.
ATS_WORDING = "GigAI’s own local check of how well this resume reads and matches the posting. Not any real ATS’s score."

#: Beside an assessment's verdict.
VERDICT_WORDING = "Your model's reading of the posting against your resume and answers. Check the posting yourself."

#: Beside a tailored resume and the PDF made from it.
TAILORED_WORDING = "Every line comes from your resume, answers or stories. Read it before you send it."

#: The privacy promise (bold wherever it is shown). The limits that go with it follow it on the
#: docs privacy page; ``PRIVACY_PDF_LINE`` is the first of them, shown with the promise in the UI.
PRIVACY_PROMISE = "GigAI never stores your name, email, phone, address or links."
PRIVACY_PDF_LINE = "You type them only when you make a PDF, and GigAI forgets them right after."

#: What using an agent means (bold wherever it is shown).
AGENT_WORDING = (
    "Anything GigAI gives your agent is sent to that agent's model provider. "
    "Agents get no contact data from GigAI, but an agent with shell access can read local files."
)

#: 0.1.10.8: where to run GigAI, said before the first Update sources (README install section, the agent
#: start page, the first-10-minutes page, the public llms.txt and a one-time notice in the UI). The lead is
#: bold wherever it is shown; ``NETWORK_NOTICE`` is the whole notice as Markdown.
NETWORK_NOTICE_LEAD = "Run GigAI on your own computer and your own network, not a work laptop or office Wi-Fi."
NETWORK_NOTICE_BODY = (
    "Scout checks about 15,000 public job boards (Greenhouse, Lever, Ashby and six more hiring systems): "
    "about 16,000 requests on the first update, and it keeps checking 8 times a day. An employer can see that traffic."
)
NETWORK_NOTICE = f"**{NETWORK_NOTICE_LEAD}** {NETWORK_NOTICE_BODY}"

__all__ = [
    "AGENT_WORDING",
    "ATS_WORDING",
    "LABEL_WORDING",
    "NETWORK_NOTICE",
    "NETWORK_NOTICE_BODY",
    "NETWORK_NOTICE_LEAD",
    "PRIVACY_PDF_LINE",
    "PRIVACY_PROMISE",
    "TAILORED_WORDING",
    "VERDICT_WORDING",
]
