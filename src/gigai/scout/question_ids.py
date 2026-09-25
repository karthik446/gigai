"""P3 (v0.1.9): a deterministic ``question_id`` normalizer, in code.

S29 r1's rerun logs (``orchestrator/research/S29-assess-verdict-evals/r1/
trial_log.jsonl``) show the SAME real-world fact, asked about the SAME
resume/posting pair, land on a different slug across reruns of the exact
prompt: ``location:us_eligible_state`` vs ``location:us_state``,
``technology:ml_lifecycle_tooling`` vs ``tooling:ml-lifecycle`` vs
``ml_tooling:lifecycle``. The model follows the prompt's
``"<category>:<value>"`` shape, but neither the category word nor the
token order/separator inside the value is stable across calls. Prompt
wording alone (``assess.md`` rule 2) cannot fix this -- it already asks for
a stable slug and the model still drifts -- so the plan calls for a
normalizer IN CODE that both the assess normalizer (P2's
``assessment_core._normalize_question_item``) and the Q&A loop
(``experience_answers.read_answers``/``record_answer``) apply, so an answer
recorded against one posting's rendering of a question_id is still found
for a different posting's differently-worded rendering of the SAME fact.

Design (deterministic, no model call, no I/O):

1. Split on the first ``:``; anything without one is not a structured id
   and is returned unchanged (``normalize_question_id`` never raises --
   callers that need a hard structured shape validate that separately,
   e.g. ``contracts.AssessmentQuestion.from_json``'s regex).
2. Lowercase both sides; split each on ``-``, ``_`` and whitespace into
   tokens; drop empty tokens.
3. Map every token through ``_TOKEN_SYNONYMS`` (the controlled term list):
   collapses spelling/word-choice variants the r1 log actually produced
   (``ml`` stays ``ml``; ``tooling``/``tools`` -> ``tooling``; ``lifecycle``
   stays; ``eligible``/``authorized`` -> ``eligible``) onto one canonical
   token each. A token not in the list passes through as-is (lowercased) --
   the list only needs to cover observed drift, not every English word.
4. Pool ALL tokens from BOTH the category and the value (the r1 drift moves
   a token like "ml"/"tooling" across that split, e.g.
   "technology:ml_lifecycle_tooling" vs "ml_tooling:lifecycle" -- both
   dissolve to the same three-token bag), then pick the canonical CATEGORY
   as the highest-priority token present (``_CATEGORY_PRIORITY``, a fixed
   order over the requirement classes/topics the assess prompt's rule 2
   names: clearance, years, seniority, education, location, cloud,
   technology, tool, sponsorship, industry -- falling back to the first
   token seen if none of them match, so an uncatalogued category still
   normalizes consistently rather than raising). The remaining tokens
   (category tokens excluded) are sorted so token ORDER in the source text
   never matters, and rejoined with ``_`` as the canonical value.
5. Reassemble ``"<category>:<value>"``. A degenerate result (empty
   category or empty value) falls back to the lowercased, whitespace-
   collapsed original rather than losing the id.

This is intentionally syntactic, not semantic: it does not know that
"us_eligible_state" and "us_state" mean the same thing (r1 shows that pair
too, and no controlled term list can safely equate them without risking a
false merge of two actually-different facts). It only removes the
NON-semantic drift a controlled term list can safely catch: word choice
between known synonyms, separator style, and token order.
"""

from __future__ import annotations

import re

_SPLIT = re.compile(r"[-_\s]+")

#: Token synonyms actually observed drifting in S29 r1's rerun pairs, plus
#: the few obvious siblings of each (see module docstring point 3). Keys and
#: values are already lowercase; a value MUST also be a key mapping to
#: itself (asserted by the self-check below) so composing this table is
#: idempotent.
_TOKEN_SYNONYMS: dict[str, str] = {
    "ml": "ml",
    "machine": "ml",
    "learning": "ml",
    "tool": "tooling",
    "tools": "tooling",
    "tooling": "tooling",
    "lifecycle": "lifecycle",
    "life": "lifecycle",
    "cycle": "lifecycle",
    "eligible": "eligible",
    "eligibility": "eligible",
    "authorized": "eligible",
    "authorization": "eligible",
    "state": "state",
    "province": "province",
    "canadian": "canadian",
    "canada": "canadian",
    "graph": "graph",
    "tabular": "tabular",
    "classification": "classification",
    "developer": "developer",
    "ai": "ai",
    "cloud": "cloud",
    "platform": "platform",
    # r1's own rerun pair shows "technology:ml_lifecycle_tooling" and
    # "ml_tooling:lifecycle" naming the SAME fact (a question about ML
    # lifecycle tooling experience) -- in this domain "technology" is used
    # interchangeably with "tool"/"tooling" for exactly this kind of
    # named-tool-or-platform question (assess.md rule 2's own examples mix
    # "cloud:gcp" and a generic "tool"/"technology" phrasing for the same
    # ASKABLE class), so both collapse onto one canonical token.
    "technology": "tooling",
    "tech": "tooling",
    "years": "years",
    "year": "years",
    "yrs": "years",
    "seniority": "seniority",
    "level": "seniority",
    "clearance": "clearance",
    "security": "clearance",
    "education": "education",
    "degree": "education",
    "phd": "phd",
    "related": "related",
    "location": "location",
    "remote": "remote",
    "visa": "visa",
    "sponsorship": "sponsorship",
    "sponsor": "sponsorship",
    "industry": "industry",
    "domain": "industry",
}

for _key, _value in _TOKEN_SYNONYMS.items():
    assert _TOKEN_SYNONYMS.get(_value) == _value, f"{_value!r} (mapped from {_key!r}) must map to itself"

#: Fixed priority order for picking the canonical CATEGORY out of the pooled
#: token bag -- the requirement topics ``assess.md``'s rule 2 example ids
#: name (``cloud:gcp``, ``years:python``, ``clearance:secret``,
#: ``seniority:staff``) plus the others r1's real postings produced. Earlier
#: entries win when multiple candidate category tokens are present. Every
#: entry here MUST be a token ``_TOKEN_SYNONYMS`` maps to itself (asserted
#: below) -- a raw/synonym spelling would never match the pooled tokens,
#: which are already passed through ``_TOKEN_SYNONYMS``.
_CATEGORY_PRIORITY: tuple[str, ...] = (
    "clearance",
    "years",
    "seniority",
    "education",
    "phd",
    "visa",
    "sponsorship",
    "location",
    "remote",
    "province",
    "state",
    "cloud",
    "platform",
    "tooling",
    "ml",
    "industry",
)

assert all(_TOKEN_SYNONYMS.get(item) == item for item in _CATEGORY_PRIORITY)


def _tokens(part: str) -> list[str]:
    return [_TOKEN_SYNONYMS.get(token, token) for token in _SPLIT.split(part.strip().lower()) if token]


def normalize_question_id(question_id: str) -> str:
    """Canonical form of ``question_id``: stable across the drift S29 r1 found.

    Never raises. A value with no ``:`` (not the structured
    ``"<category>:<value>"`` shape) is returned lowercased and whitespace-
    collapsed, unchanged otherwise -- this function only canonicalizes the
    structured shape; shape VALIDATION (the ``^[a-z0-9._-]+:[a-z0-9._-]+$``
    pattern) stays the caller's job (``contracts.AssessmentQuestion``,
    ``experience_answers``'s schema-backed record).
    """

    raw = question_id.strip()
    if ":" not in raw:
        return _SPLIT.sub("_", raw.lower()).strip("_")

    category_part, value_part = raw.split(":", 1)
    pool = _tokens(category_part) + _tokens(value_part)
    if not pool:
        return _SPLIT.sub("_", raw.lower()).strip("_")

    category = next((token for token in _CATEGORY_PRIORITY if token in pool), None)
    if category is None:
        category = pool[0]
    remaining = [token for token in pool if token != category]
    # A single-occurrence category token is removed once; a category word
    # that also legitimately repeats in the value stays represented once
    # (repeats collapse -- they never carried extra meaning in the drift
    # observed, and a stable id must not grow with duplicate tokens).
    seen: set[str] = set()
    value_tokens: list[str] = []
    for token in remaining:
        if token in seen:
            continue
        seen.add(token)
        value_tokens.append(token)
    if not value_tokens:
        value_tokens = [category]
    value = "_".join(sorted(value_tokens))
    canonical = f"{category}:{value}"
    if category and value:
        return canonical
    return _SPLIT.sub("_", raw.lower()).strip("_")  # pragma: no cover - degenerate fallback


__all__ = ["normalize_question_id"]
