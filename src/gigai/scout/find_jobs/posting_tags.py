"""Rules tagger: level and coarse function from a posting title (no model).

``tag_title`` is pure and deterministic. Level is a closed word list and is
the authoritative level tag. Function is a coarse family; ``None`` means the
rules found no family (a model may fill it later, see ``tag_store``). The
rules are the spike's (``research/posting-index-spike``, P1) with the
look-alike fixes: every word pattern is matched on word boundaries (so ``cto``
never fires inside "dire\\ *cto*\\ r", the same class of bug as "ai" in
"maintain"), and sales/solutions engineering, mechanical/hardware engineering,
data, AI and security families are tried before the generic software family,
so "Sales Engineering Director" and "Director, Mechanical Engineering" are not
software engineering even though the 021 prefilter lets them through.

``tag_new_titles`` is the entry point for the Update sources hook (not wired
here): it tags only titles the store has not seen at the current tagger
version and writes nothing for the rest.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from pathlib import Path
from typing import Iterable

from .tag_store import SOURCE_RULES, TagStore, TitleTag

#: Bump when the rules below change: stored rows at an older version are re-tagged.
TAGGER_VERSION = 1

# Levels (leadership first; the first pattern that matches wins).
LEVEL_INTERN = "intern"
LEVEL_JUNIOR = "junior"
LEVEL_MID = "mid"
LEVEL_SENIOR = "senior"
LEVEL_LEAD = "lead"
LEVEL_STAFF = "staff"
LEVEL_PRINCIPAL = "principal"
LEVEL_MANAGER = "manager"
LEVEL_DIRECTOR = "director"
LEVEL_VP = "vp"
LEVEL_HEAD = "head"
LEVEL_CHIEF = "chief"

LEVELS = (
    LEVEL_INTERN, LEVEL_JUNIOR, LEVEL_MID, LEVEL_SENIOR, LEVEL_LEAD, LEVEL_STAFF,
    LEVEL_PRINCIPAL, LEVEL_MANAGER, LEVEL_DIRECTOR, LEVEL_VP, LEVEL_HEAD, LEVEL_CHIEF,
)

FUNCTIONS = (
    "software", "ai_ml", "data", "product", "design", "security_it", "hardware", "sales",
    "solutions", "marketing", "customer", "operations", "finance", "legal", "people",
    "healthcare", "research",
)


def normalize_title(title: str) -> str:
    """The store key text: NFKC, lower-cased, punctuation and spacing folded.

    Punctuation-insensitive on purpose (the rules are), so "Director, Engineering"
    and "Director Engineering" share one tag row.
    """

    text = unicodedata.normalize("NFKC", title).lower()
    return re.sub(r"[^a-z0-9+#./&]+", " ", text).strip()


_LEVEL_RULES: tuple[tuple[str, str], ...] = (
    (LEVEL_CHIEF, r"chief (?!of staff\b)\w+( \w+)? officer|ceo|cto|cfo|coo|cmo|cio|ciso|cro|cpo|chief of (?!staff\b)\w+|(?<!vice )president"),
    (LEVEL_VP, r"vp|svp|evp|avp|vice president"),
    (LEVEL_HEAD, r"head"),
    (LEVEL_DIRECTOR, r"director|dir\.?"),
    (LEVEL_MANAGER, r"manager|mgr|management lead"),
    (LEVEL_PRINCIPAL, r"principal|distinguished|fellow|chief engineer|chief architect"),
    (LEVEL_STAFF, r"staff|member of technical staff"),
    (LEVEL_LEAD, r"lead|team lead|tech lead|leader"),
    (LEVEL_SENIOR, r"senior|sr\.?|iii|iv"),
    (LEVEL_INTERN, r"intern|internship|co-?op|working student|werkstudent|apprentice"),
    (LEVEL_JUNIOR, r"junior|jr\.?|entry level|associate|assistant|trainee|graduate|new grad"),
)

# Family order matters: look-alike families come before software. Alternatives
# that are word stems end in \w*; everything else is a whole word.
_FUNCTION_RULES: tuple[tuple[str, str], ...] = (
    ("solutions", r"(sales|solutions?|customer|field|pre-?sales|professional services|implementation|forward deployed) (engineer\w*|architect\w*|consultant\w*)|se"),
    ("customer", r"customer (success|support|service|experience|care)|support (engineer\w*|specialist|agent|advisor)|technical support|member (care|support)|client (relations|success|services)"),
    ("legal", r"counsel|legal|attorney|paralegal|compliance|privacy"),
    ("people", r"recruit\w*|talent|people|human resources|hr|compensation"),
    ("finance", r"financ\w*|account(ing|ant)|controller|tax|treasury|fp&a|payroll|audit\w*"),
    ("healthcare", r"nurse|rn|physician|clinical|therap\w*|patient|medical|pharmac\w*|psycholog\w*|bcba|lcsw|health center|pathologist|hospice"),
    ("ai_ml", r"ai|ml|machine learning|genai|llm|applied (ai|scientist|science)|deep learning|computer vision|nlp"),
    ("data", r"data|analytics|business intelligence|bi"),
    ("security_it", r"security|cyber\w*|it|information technology|sysadmin|grc"),
    ("product", r"product (manager|management|owner|lead)|head of product|director of product|product"),
    ("design", r"design(er)?|creative|ux|ui|art director|illustrat\w*"),
    ("hardware", r"mechanical|electrical|hardware|firmware|manufactur\w*|civil|structural|process engineer\w*|rf|optical|photonics|mechatronic\w*|test engineer\w*|quality engineer\w*|chemical|industrial|avionics|propulsion|robotics|technician|systems engineer\w*|project engineer\w*|dft|embedded"),
    ("operations", r"(program|project) manager|technical program\w*|chief of staff"),
    ("software", r"software|engineer\w*|developer|devops|sre|platform|backend|back-end|frontend|front-end|full.?stack|ios|android|architect\w*|qa|quality assurance|programmer|technology|cto"),
    ("marketing", r"marketing|brand|content|seo|communications|pr|growth|social media|demand gen"),
    ("sales", r"sales|account (executive|manager|director)|business development|bd|partner\w*|sdr|bdr|revenue|alliances?"),
    ("research", r"scientist|research\w*|chemist|biolog\w*|lab|r&d"),
    ("operations", r"operations?|ops|supply chain|logistics|procurement|strategy|office manager|general manager|delivery"),
)

_B = r"(?<![a-z0-9])(?:{})(?![a-z0-9])"
_LEVEL_RES = tuple((name, re.compile(_B.format(pat))) for name, pat in _LEVEL_RULES)
_FUNCTION_RES = tuple((name, re.compile(_B.format(pat))) for name, pat in _FUNCTION_RULES)


@dataclass(frozen=True, slots=True)
class RulesTag:
    level: str
    function: str | None


def tag_title(title: str) -> RulesTag:
    """Level and function for one title. Level defaults to ``mid`` (plain IC); function to ``None``."""

    norm = normalize_title(title)
    level_text = norm.replace("chief of staff", " ")  # an operations role, not "staff" level
    level = LEVEL_MID
    for name, pattern in _LEVEL_RES:
        if pattern.search(level_text):
            level = name
            break
    function = None
    for name, pattern in _FUNCTION_RES:
        if pattern.search(norm):
            function = name
            break
    return RulesTag(level, function)


@dataclass(frozen=True, slots=True)
class TagCounts:
    """What one ``tag_new_titles`` pass saw: distinct titles, already tagged, newly tagged."""

    distinct: int
    already_tagged: int
    tagged: int


def tag_new_titles(store: TagStore, titles: Iterable[str]) -> TagCounts:
    """Rules-tag the titles the store has no current-version row for.

    Idempotent: titles already tagged at the store's tagger version are not
    touched, so a second call over the same titles writes nothing. Blank
    titles are ignored. Safe to call from the single writer thread while
    other threads read.
    """

    keys = dict.fromkeys(k for k in (normalize_title(t) for t in titles if isinstance(t, str)) if k)
    known = store.get_many(keys)
    fresh = []
    for key in keys:
        if key in known:
            continue
        rules = tag_title(key)
        fresh.append(
            TitleTag(
                title_key=key,
                level=rules.level,
                level_source=SOURCE_RULES,
                function=rules.function,
                function_source=SOURCE_RULES if rules.function else None,
                tagger_version=store.tagger_version,
            )
        )
    written = store.write_rules(fresh) if fresh else 0
    return TagCounts(distinct=len(keys), already_tagged=len(known), tagged=written)


def default_store(home_root: Path) -> TagStore:
    """The product store at ``<home>/cache/scout/tags.sqlite`` for this tagger version."""

    return TagStore.for_home(home_root, tagger_version=TAGGER_VERSION)
