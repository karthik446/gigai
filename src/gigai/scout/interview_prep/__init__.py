"""Scout interview prep (S18 minimal first slice, packet ``interview-prep-engine``).

When a posting has been acquired by find-jobs, Scout can build a prep for it:
company research (OpenAI ``web_search``, sourced and verified), role
research (from the posting text alone, no search), likely question
categories (grounded, category-level per the coordinator default), and prep
notes (reusing assess's requirement x resume matrix when one exists).

Scout-only package: core never imports this
(``[[gigai_gig_architecture_rule]]``).

Public surface: :func:`prep.build_prep`, :func:`prep.load_prep`.
"""

from __future__ import annotations

from .prep import InterviewPrepError, build_prep, load_prep
from .types import InterviewPrep

__all__ = ["InterviewPrep", "InterviewPrepError", "build_prep", "load_prep"]
