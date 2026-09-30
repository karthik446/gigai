"""0.1.10-001: the resume personal-info warning and the local heads-up, UI side.

``ui/src/resumeWarning.js`` is pure JavaScript, run under the system ``node``
(LOUD skip when it is not on PATH). What lives in JSX is checked statically,
by reading the source.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.resume_pii import RESUME_WARNING

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

NODE_SCRIPT = """
const m = await import(process.argv[1]);
console.log(JSON.stringify({
  general: m.resumeWarningText(undefined),
  codex: m.resumeWarningText("codex_cli"),
  claude: m.resumeWarningText("claude_cli"),
  openrouter: m.resumeWarningText("openrouter_api"),
  ollama: m.resumeWarningText("ollama_local"),
  flagged: m.contactHeadsUp(["email", "phone"]),
  stored: m.contactHeadsUp(["email"], { stored: true }),
  none: m.contactHeadsUp([]),
}));
"""


def _run() -> dict[str, object]:
    if shutil.which("node") is None:
        pytest.skip("LOUD: node is not on PATH; the resume warning model was NOT checked")
    done = subprocess.run(
        ["node", "--input-type=module", "-e", NODE_SCRIPT, (UI_SRC / "resumeWarning.js").as_uri()],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return json.loads(done.stdout)


def test_the_warning_is_model_aware_and_matches_the_cli_wording() -> None:
    out = _run()
    assert out["general"] == RESUME_WARNING
    assert "sends your resume text to OpenAI" in out["codex"]
    assert "sends your resume text to Anthropic" in out["claude"]
    assert "your provider" in out["openrouter"]
    assert "stays on this machine" in out["ollama"] and "OpenAI" not in out["ollama"]
    for key in ("codex", "claude", "openrouter", "ollama"):
        assert out[key].startswith("Remove your personal info before adding a resume: name, email, phone, street address and links.")


def test_the_heads_up_lists_what_was_found_and_says_nothing_otherwise() -> None:
    out = _run()
    assert out["flagged"] == "This resume seems to contain: email, phone. Remove them before continuing?"
    assert out["stored"] == "This stored resume seems to contain: email."
    assert out["none"] is None


def test_every_resume_entry_surface_shows_the_warning() -> None:
    wizard = (UI_SRC / "wizard" / "ResumeScreen.jsx").read_text(encoding="utf-8")
    # Above the three tabs, not inside any one tab's body.
    assert wizard.index("<ResumeWarning") < wizard.index('role="tablist"')
    assert "Continue anyway" in wizard and 'data-role="resume-heads-up"' in wizard
    assert "<ResumeWarning" in (UI_SRC / "views" / "AssessView.jsx").read_text(encoding="utf-8")
    profiles = (UI_SRC / "views" / "ProfilesView.jsx").read_text(encoding="utf-8")
    assert profiles.count("<ResumeWarning") == 2  # replace, and add-a-profile
    assert 'className="callout warn"' in (UI_SRC / "components" / "ResumeWarning.jsx").read_text(encoding="utf-8")
