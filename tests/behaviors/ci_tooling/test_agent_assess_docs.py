"""0110-10-13 (docs): what an agent is told about an assessment is said once and quoted, and it matches the code.

The END outcome a reader gets: the agent guide, the CLI reference (``commands.yaml``, which is also what
``gigai agent-context`` prints) and the agent instructions (``gigai agent-skill``) say what one assessment sends,
keep the three approvals apart, name every typed cause the code has with the facts the code gives it, say what to
compare after an interruption, and give the job page's address. A sentence that drifts from its constant, or a typed
cause that is added to the code and not to the guide, fails here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from gigai.agent_context import load_prose
from gigai.agent_skill import source_text
from gigai.scout import assess_causes, assess_preview

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "gigai-docs" / "src" / "content" / "docs"
GUIDE = DOCS / "scout" / "agents.md"
START = DOCS / "scout" / "agents" / "start.md"

needs_docs = pytest.mark.skipif(not DOCS.is_dir(), reason="gigai-docs is excluded from the offline container build context")


def _flat(text: str) -> str:
    return " ".join(re.sub(r"^\s*>\s?", "", text, flags=re.M).split())


def _notes(command: str) -> str:
    return str(load_prose()["commands"][command]["notes"])


def test_the_cli_reference_quotes_what_one_assessment_sends_and_the_three_approvals() -> None:
    notes = _notes("scout jobs assess")
    assert assess_preview.WHAT_ONE_ASSESSMENT_SENDS in notes
    assert assess_preview.THREE_APPROVALS in notes
    assert assess_preview.WHAT_ONE_ASSESSMENT_SENDS in _notes("scout assess")
    # The preview's field and every key of it are named, so an agent knows what to read.
    assert "model_input_summary" in load_prose()["commands"]["scout jobs assess"]["output"]
    for key in ("model_target_runs", "resume_source: profile_view | master_evidence", "answers_used", "stories_used", "public_fetch_needed"):
        assert key in notes, key
    # After an interruption: what to compare before a retry.
    assert "assessment.assessed_at" in notes and "stale_reason" in notes


@needs_docs
def test_the_agent_guide_has_the_box_the_approvals_every_cause_and_the_job_link() -> None:
    guide = _flat(GUIDE.read_text(encoding="utf-8"))
    assert "**What one assessment sends.** " + assess_preview.WHAT_ONE_ASSESSMENT_SENDS in guide
    assert "**Three approvals, kept apart.** " + assess_preview.THREE_APPROVALS in guide
    # The box sits with the `jobs assess` examples, before the next numbered step.
    assert guide.index("gigai scout jobs assess URL --json") < guide.index("**What one assessment sends.**") < guide.index("### 2. Public and private are separate calls")
    # Every typed cause the code has is a row of the table, with the code's own facts and next action.
    for cause in assess_causes.CAUSES.values():
        started = "yes" if cause.model_call_started else "no"
        assert f"| `{cause.code}` | {started} | {started} | {cause.next_action} |" in guide, cause.code
    for word in ("model_call_started", "may_have_used_tokens", "fresh_assessment_stored", "next_action", "assessment.assessed_at", "stale_reason"):
        assert word in guide, word
    # The runtime's refusal is not Scout's: quote it, ask for the exact authorisation, no other route.
    assert "before `gigai` even starts" in guide and "word for word" in guide and "should not try another route" in guide
    # The browser and a sandbox: the address, a job's page, --no-browser as a choice, and what status says.
    assert "http://127.0.0.1:8765/#/jobs/" in guide and "`--no-browser` is a choice" in guide
    assert "process: running (pid N); API: not reachable from here" in guide and "`unreachable`" in guide
    start = _flat(START.read_text(encoding="utf-8"))
    assert "`--no-browser` is a choice, not a requirement" in start and "`unreachable`" in start and "#/jobs/" in start


def test_the_agent_instructions_carry_the_same_guidance() -> None:
    skill = source_text()
    flat = _flat(skill)
    assert "gigai scout jobs assess URL --json" in skill and "`model_input_summary`" in skill
    assert "`profile_view` or `master_evidence`" in skill
    assert "contact lines removed by pattern, which can miss a name or contact format" in flat
    assert "A profile id is not contact data." in flat
    # Three separate approvals, and what to do when the runtime refuses.
    assert "Three separate approvals: the user's choice to assess; Scout's own `--yes` (`approve: true` over the API); your runtime's sandbox or model-provider approval." in flat
    assert "`--yes` does not bypass your runtime's policy, and an API or UI route is not a workaround." in flat
    assert "quote its rejection to the user and ask for the exact missing authorisation" in flat
    # After an interruption; the typed causes' fields; the browser link; the sandboxed status.
    assert "compare the row's `assessment.assessed_at` and `stale_reason`" in flat and "before retrying" in flat
    for word in ("model_call_started", "may_have_used_tokens", "fresh_assessment_stored", "next_action"):
        assert f"`{word}`" in skill, word
    for code in ("model_target_unavailable", "model_denied", "model_unavailable", "assess_timeout", "assessment_not_stored"):
        assert f"`{code}`" in skill and code in assess_causes.CAUSES, code
    assert "`http://127.0.0.1:8765`" in skill and "`#/jobs/`" in skill and "`--no-browser`, which is a choice" in skill
    assert "`unreachable`" in skill and "Do not restart it" in skill


def test_the_status_reference_says_the_two_kinds_of_evidence() -> None:
    notes = _notes("scout status")
    for word in ("unreachable", "process {recorded, pid, alive, identity: scout | unknown}", "api {checked, reachable, url, error: refused | not_permitted | timeout | failed}", "running needs both"):
        assert word in notes, word
    assert json.dumps(load_prose())  # the manual is still one JSON document
