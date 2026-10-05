"""0110-10-13: what one assessment would send to the model, said before any call (``model_input_summary``).

An agent that runs ``gigai scout jobs assess`` for its user is asked, by its
own runtime, what the command sends and where. The no-call preview
(``status: "ask"`` of ``posting_search.assess_these``: the CLI's
``gigai scout jobs assess`` without ``--yes`` and ``POST /api/postings/assess``
without ``approve: true``) answers that with this summary, so the agent has
something exact to show the user and to cite when it asks for approval.

The summary names CATEGORIES, never content: which profile (id and label),
where the prompt's RESUME comes from (``assess_master.resume_source``:
``profile_view``, the profile's own resume, or ``master_evidence``, the lines
of the master resume picked for each posting), whether saved answers and
stories go with it, the model target and where that target runs, and whether a
posting has to be fetched from its public board first. No resume line, no
answer, no story and no posting text is in it: it is ids, labels, counts and
names, and it is built from what is already read (no model call, no request).

What one assessment sends (``SENDS``; the same list is in the agent guide and
the CLI reference): the stored posting; the resume, with the contact lines of
its header removed by pattern (``resume_privacy.model_resume``: a pattern can
miss an unusual name or contact format, so that is not a guarantee); the
search preferences (sponsorship need, eligible countries, location, target
titles, work mode); every saved answer; and the saved stories that match the
posting. A profile id is not contact data.

``summary_lines`` is the same facts as the terminal prints them, above the
y/n question.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

SCHEMA_VERSION = "scout-assess-input:1"
#: What one assessment's prompt holds, by category, in the order the prompt has them.
SENDS: tuple[str, ...] = ("stored_posting", "resume", "search_preferences", "answers", "stories")
SEARCH_PREFERENCES: tuple[str, ...] = ("sponsorship", "countries", "location", "titles", "work_mode")
#: How the resume's contact lines are kept out: by pattern, which is not a guarantee.
CONTACT_LINES = "removed_by_pattern"

#: The "What one assessment sends" box: the CLI reference (commands.yaml) and the agent guide quote these sentences word for word.
WHAT_ONE_ASSESSMENT_SENDS = (
    "One assessment sends to your model target: the stored posting; your resume (the lines of your master resume picked "
    "for the posting, or the profile's own resume) with its contact lines removed by pattern; your search preferences "
    "(sponsorship, countries, location, titles, work mode); your saved answers; and the saved stories that match the posting. "
    "Pattern removal can miss an unusual name or contact format, so it is not a guarantee. A profile id is not contact data. "
    "With codex_cli or claude_cli the model target is your own login."
)
#: The three approvals an agent keeps apart, in the same two places.
THREE_APPROVALS = (
    "Three approvals are separate: the user's choice to assess; Scout's own --yes (approve: true over the API); and the "
    "agent runtime's own sandbox or model-provider approval. --yes does not bypass the runtime's policy, and an API or UI "
    "route is not a workaround: an agent whose runtime refuses the command quotes the refusal and asks the user for the "
    "exact missing authorisation."
)

#: Where a model target runs: on this computer, through the user's own CLI login, or through an API key.
RUNS_LOCAL = "local"
RUNS_OWN_LOGIN = "own_login"
RUNS_API_KEY = "api_key"
RUNS_UNKNOWN = "unknown"
_TARGETS: Mapping[str, tuple[str, str]] = {
    "ollama_local": (RUNS_LOCAL, "Ollama on this computer; nothing leaves it"),
    "codex_cli": (RUNS_OWN_LOGIN, "the Codex CLI on your own login; the text goes to OpenAI"),
    "claude_cli": (RUNS_OWN_LOGIN, "the Claude Code CLI on your own login; the text goes to Anthropic"),
    "openrouter_api": (RUNS_API_KEY, "OpenRouter's API with your key"),
}
_SOURCE_WORDS = {"master_evidence": "the lines of your master resume picked for each posting", "profile_view": "this profile's own resume"}


def model_input_summary(
    *, home_root: Path, target: Path, pairs: Sequence[tuple[str, str]], profiles: Sequence[object], model_target: str,
    without_text: int, resolved: object | None = None,
) -> dict[str, object]:
    """The summary for the postings a question asks about. No model call, no request, no text of the user's.

    ``pairs`` are the ``(job, profile_id)`` to assess, ``profiles`` the
    active profiles (``postings.ProfileView``: id, label and the record),
    ``without_text`` how many of the postings have no stored text (each is
    fetched from its public board first, one request for it alone).
    """

    from . import story_bank
    from .assess_master import resume_source

    counts: dict[str, int] = {}
    for _job, profile_id in pairs:
        counts[profile_id] = counts.get(profile_id, 0) + 1
    by_profile = [
        {
            "profile_id": view.profile_id,  # type: ignore[attr-defined]
            "label": view.label,  # type: ignore[attr-defined]
            "postings": counts[view.profile_id],  # type: ignore[attr-defined]
            "resume_source": resume_source(home_root=home_root, target=target, profile=view.record, resolved=resolved),  # type: ignore[attr-defined]
        }
        for view in profiles
        if view.profile_id in counts  # type: ignore[attr-defined]
    ]
    bank = story_bank.assess_bank(home_root=home_root, target=target)  # never raises; no bank reads as none saved
    answers, stories = len(bank.entries), len(bank.stories)
    runs, _words = _TARGETS.get(model_target, (RUNS_UNKNOWN, ""))
    return {
        "schema_version": SCHEMA_VERSION,
        "postings": len(pairs),
        "model_calls": len(pairs),
        "model_target": model_target,
        "model_target_runs": runs,
        "profiles": by_profile,
        "sends": list(SENDS),
        "search_preferences": list(SEARCH_PREFERENCES),
        "contact_lines": CONTACT_LINES,
        "answers_used": answers > 0,
        "answers_saved": answers,
        "stories_used": stories > 0,
        "stories_saved": stories,
        "public_fetch_needed": without_text > 0,
        "public_fetch_postings": without_text,
    }


def summary_lines(summary: Mapping[str, object]) -> list[str]:
    """The summary as the terminal says it, above the y/n question. The same facts, in words."""

    model = str(summary["model_target"])
    where = _TARGETS.get(model, ("", ""))[1]
    lines = [f"What this sends to your model target, {model}{' (' + where + ')' if where else ''}, one call per posting:"]
    lines.append("  - the stored posting")
    for item in summary["profiles"]:  # type: ignore[union-attr]
        source = _SOURCE_WORDS.get(str(item["resume_source"]), str(item["resume_source"]))
        lines.append(
            f"  - profile {item['label']} ({item['profile_id']}; an id, not contact data), {item['postings']} posting"
            f"{'s' if item['postings'] != 1 else ''}: {source} ({item['resume_source']})"
        )
    lines.append("    contact lines are removed from the resume by pattern, which can miss an unusual name or contact format")
    lines.append("  - your search preferences: sponsorship, countries, location, titles, work mode")
    answers, stories = summary["answers_saved"], summary["stories_saved"]
    lines.append(f"  - your saved answers: {answers if summary['answers_used'] else 'none saved'}")
    lines.append(f"  - your saved stories that match a posting: {('of ' + str(stories) + ' saved') if summary['stories_used'] else 'none saved'}")
    fetch = summary["public_fetch_postings"]
    lines.append(
        f"  Public fetch first: {fetch} posting{'s have' if fetch != 1 else ' has'} no stored text; each is fetched from its public board (one request each)."
        if summary["public_fetch_needed"] else "  Public fetch first: none needed; every posting's text is stored."
    )
    return lines


__all__ = [
    "CONTACT_LINES",
    "RUNS_API_KEY",
    "RUNS_LOCAL",
    "RUNS_OWN_LOGIN",
    "SCHEMA_VERSION",
    "SEARCH_PREFERENCES",
    "SENDS",
    "THREE_APPROVALS",
    "WHAT_ONE_ASSESSMENT_SENDS",
    "model_input_summary",
    "summary_lines",
]
