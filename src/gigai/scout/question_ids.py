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
4. Canonicalize EACH SIDE SEPARATELY (Terra review, P0): the category side's
   tokens are synonym-mapped and, if more than one token survives, sorted so
   token order never matters; the value side's tokens are synonym-mapped,
   de-duplicated against the category token (a category word that legitimately
   repeats in the value collapses to one occurrence -- it never carried extra
   meaning in the drift observed), and sorted the same way. The category/value
   BOUNDARY from the raw id is preserved by default: tokens never move from one
   side to the other, so ``location:remote`` and ``remote:location`` -- two
   different real-world facts (geographic eligibility vs. remote-work
   eligibility -- both HARD-when-stated per assess.md's REQUIREMENT CLASSES
   paragraph, but distinct policy questions) -- stay distinct instead of
   dissolving into one pooled bag.
5. Reassemble ``"<category>:<value>"`` from the per-side canonical forms. A
   degenerate result (empty category or empty value) falls back to the
   lowercased, whitespace-collapsed original rather than losing the id.
6. Look the reassembled id up in ``_CROSS_BOUNDARY_ALIASES``, a small,
   explicit, evidence-backed table for the rare case where the SAME fact is
   genuinely rendered with the category/value split in a different place
   across calls (not just a different category WORD for the same split).
   Only pairs traced to an actual r1 rerun go in this table (see its
   docstring) -- it is never grown speculatively, because an unverified entry
   is exactly the false-merge risk this fix exists to close.

This is intentionally syntactic, not semantic: it does not know that
"us_eligible_state" and "us_state" mean the same thing (r1 shows that pair
too, and no controlled term list can safely equate them without risking a
false merge of two actually-different facts). It only removes the
NON-semantic drift a controlled term list can safely catch: word choice
between known synonyms (within a side), separator style, and token order
(within a side) -- plus the handful of cross-boundary renderings r1 actually
produced for one fact, named explicitly rather than inferred.
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

#: Small, explicit, EVIDENCE-BACKED table for the rare case where r1 itself
#: rendered the SAME fact with the category/value split in a different place
#: across calls -- not just a different category WORD (that is handled
#: within-side by ``_TOKEN_SYNONYMS`` above). Keys and values are already the
#: per-side-canonicalized ``"<category>:<value>"`` form (post steps 4-5, pre
#: step 6) so this is a plain lookup, not another round of tokenizing.
#:
#: "ml_tooling:lifecycle" -> "tooling:lifecycle_ml": r1 log, resume
#: r1-ml-staff x posting gh:affirm:7806920003, first vs rerun -- the same
#: "ML lifecycle tooling experience" question (also seen, same fact, as
#: "tooling:ml-lifecycle" / "technology:ml_lifecycle_tooling" against a
#: different posting in the same log; those two unify within-side already,
#: without needing this table, because the drift there is only the category
#: WORD, not the boundary). Every entry must be traced to an actual r1 rerun
#: pair naming the same fact -- this table is never grown speculatively; an
#: unverified entry is exactly the false-merge risk P0 exists to close.
#: A value MUST also be a key mapping to itself (asserted below) so applying
#: this table twice is idempotent.
_CROSS_BOUNDARY_ALIASES: dict[str, str] = {
    "tooling:lifecycle_ml": "tooling:lifecycle_ml",
    "ml_tooling:lifecycle": "tooling:lifecycle_ml",
}

for _alias_key, _alias_value in _CROSS_BOUNDARY_ALIASES.items():
    assert _CROSS_BOUNDARY_ALIASES.get(_alias_value) == _alias_value, (
        f"{_alias_value!r} (aliased from {_alias_key!r}) must map to itself"
    )


def _tokens(part: str) -> list[str]:
    return [_TOKEN_SYNONYMS.get(token, token) for token in _SPLIT.split(part.strip().lower()) if token]


def _side(tokens: list[str], *, exclude: frozenset[str] = frozenset()) -> list[str]:
    """Deduplicate and sort one side's tokens, independent of the other side.

    ``exclude`` drops a token already represented on the OTHER side (the
    value side excludes the category's own tokens) so a category word that
    legitimately repeats in the value collapses to one occurrence instead of
    growing the id -- but only within this one side; nothing moves across
    the boundary.
    """

    seen: set[str] = set()
    kept: list[str] = []
    for token in tokens:
        if token in exclude or token in seen:
            continue
        seen.add(token)
        kept.append(token)
    return sorted(kept)


def normalize_question_id(question_id: str) -> str:
    """Canonical form of ``question_id``: stable across the drift S29 r1 found.

    Never raises. A value with no ``:`` (not the structured
    ``"<category>:<value>"`` shape) is returned lowercased and whitespace-
    collapsed, unchanged otherwise -- this function only canonicalizes the
    structured shape; shape VALIDATION (the ``^[a-z0-9._-]+:[a-z0-9._-]+$``
    pattern) stays the caller's job (``contracts.AssessmentQuestion``,
    ``experience_answers``'s schema-backed record).

    Idempotent: ``normalize_question_id(normalize_question_id(x)) ==
    normalize_question_id(x)`` for any ``x`` -- both the per-side
    canonicalization (sorted, deduplicated tokens re-sort/re-dedupe to
    themselves) and ``_CROSS_BOUNDARY_ALIASES`` (asserted self-mapping on
    every value) are fixed points.
    """

    raw = question_id.strip()
    if ":" not in raw:
        return _SPLIT.sub("_", raw.lower()).strip("_")

    category_part, value_part = raw.split(":", 1)
    category_tokens = _tokens(category_part)
    value_tokens_raw = _tokens(value_part)
    if not category_tokens and not value_tokens_raw:
        return _SPLIT.sub("_", raw.lower()).strip("_")

    category_tokens_deduped = _side(category_tokens)
    value_tokens = _side(value_tokens_raw, exclude=frozenset(category_tokens_deduped))
    if not value_tokens:
        # Every value token echoed the category (e.g. "cloud:cloud"): keep
        # the deduped-but-unfiltered value rather than losing the side.
        value_tokens = _side(value_tokens_raw)
    if not category_tokens_deduped:
        category_tokens_deduped = _side(value_tokens_raw)[:1]

    category = "_".join(category_tokens_deduped)
    value = "_".join(value_tokens)
    if not category or not value:
        return _SPLIT.sub("_", raw.lower()).strip("_")  # pragma: no cover - degenerate fallback

    canonical = f"{category}:{value}"
    return _CROSS_BOUNDARY_ALIASES.get(canonical, canonical)


#: The ``experience_question.question_id`` contract (Terra review P2):
#: ``src/gigai/schemas/native-record-content.schema.json``'s own pattern for
#: ``$defs.experience_question.properties.question_id``, duplicated here (not
#: imported from the schema file) so this module stays pure/schema-free, as
#: its own docstring promises ("no model call, no I/O"). Every write surface
#: (``experience_answers.record_answer``, the API route, the CLI) validates
#: the NORMALIZED id against this pattern before any write is attempted, so
#: an invalid id fails as a typed 422/``answer_invalid`` instead of reaching
#: native-record schema validation and surfacing as a generic conflict.
QUESTION_ID_CONTRACT_PATTERN = re.compile(r"\A[A-Za-z0-9._:-]{1,128}\Z")


def is_valid_question_id(question_id: str) -> bool:
    """``True`` iff ``question_id`` fits the ``experience_question`` contract.

    Callers validate the NORMALIZED id (``normalize_question_id(question_id)``),
    not the raw one -- normalization can only narrow the character set (it
    lowercases and rejoins with ``_``/``:``), so this checks the id actually
    written, not an arbitrary raw string the caller never persists.
    """

    return bool(QUESTION_ID_CONTRACT_PATTERN.fullmatch(question_id))


__all__ = ["normalize_question_id", "is_valid_question_id", "QUESTION_ID_CONTRACT_PATTERN"]
