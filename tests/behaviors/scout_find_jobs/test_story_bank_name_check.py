"""0110-046 (spike finding b): with no saved name, the story bank's personal-info check is shape-only.

Before 0.1.10.7 the API and the CLI passed the saved Resume display name (``known_names``) and an
answer holding it was refused.  GigAI stores no name now, and no hash of one either (operator
question Q1 is open), so the callers pass no name: contact shapes are still refused, a name inside an
answer is NOT caught.  This test pins that behaviour so a later change to it is deliberate.
"""

from __future__ import annotations

import ast
from pathlib import Path

from gigai.scout.story_bank import personal_info_in_answer

SRC = Path(__file__).resolve().parents[3] / "src" / "gigai" / "scout"


def test_contact_shapes_are_still_refused() -> None:
    assert personal_info_in_answer("Reach me at zora.q@example.invalid") == ["email"]
    assert personal_info_in_answer("Call (555) 014-2999 after 5pm") == ["phone"]
    assert personal_info_in_answer("Code at github.com/zq-invalid") == ["links"]


def test_a_name_inside_an_answer_is_not_caught_with_no_name_known() -> None:
    assert personal_info_in_answer("Zora Quillfeather led the migration to Postgres.") == []
    assert personal_info_in_answer("Zora Quillfeather") == [], "no name-shape fallback for answers"
    # A caller that does know a name still gets it refused (the parameter stays).
    assert personal_info_in_answer("Zora Quillfeather led it.", names=("Zora Quillfeather",)) == ["name"]


def test_no_caller_reads_a_saved_or_hashed_name() -> None:
    for relative in ("find_jobs/api/story_bank.py", "find_jobs/api/answers.py", "scout_cli.py"):
        tree = ast.parse((SRC / relative).read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert "known_names" not in names and "load_display" not in names, relative
    bank_api = (SRC / "find_jobs" / "api" / "story_bank.py").read_text(encoding="utf-8")
    assert "resume_display" not in bank_api and "sha256" not in bank_api
