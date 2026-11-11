# Vendored inspect_ai text scorers and judge template @ 9567b431 (MIT, Copyright (c) 2024 UK AI Security Institute; see ../LICENSE and DIFF.md)
"""Inspect's ``match``, ``includes`` and ``pattern`` scorers and its model-graded QA template, without inspect_ai.

The scorer factories return inspect_ai's async ``score(state, target)``; ``gigai.evals.contracts.lifted`` wraps one
as a gigai scorer. ``judge_template`` holds the grading prompt and the grade regex the judge scorer uses.
"""

from ._match import includes, match
from ._pattern import pattern
from .judge_template import DEFAULT_GRADE_PATTERN, DEFAULT_MODEL_GRADED_QA_TEMPLATE, default_instructions

__all__ = ["DEFAULT_GRADE_PATTERN", "DEFAULT_MODEL_GRADED_QA_TEMPLATE", "default_instructions", "includes", "match", "pattern"]
