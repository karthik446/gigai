"""0.1.11 N3 (SPEC 1.4): one requirement list per posting text, with stable ids.

The same posting had 12, 18, 15 and 24 rows across four assessments
(0110-10-10), so a before and an after could not be compared.  From v9 on a
posting's requirements are extracted ONCE, by its first v9 assessment (that
assessment's own matrix: no second call), stored here, and every later
assessment of the same posting text is given the list and must answer exactly
its rows, by id.

WHERE.  ``<home>/scout/<project>/requirements/<sha256 of posting_sha256>.json``,
schema ``scout-posting-requirements:1``.  ``posting_sha256`` is
``assessment_basis.posting_sha256(title, text)``, the digest an assessment
already seals.  Per posting, not per profile: the requirements are the
posting's.  A plain file, like every per-job store; it holds the posting's own
requirement words (public text), never a resume line or an answer.

IDS.  ``req-`` plus the first 6 hex characters of ``sha256(posting_sha256 +
"\\n" + the requirement text, whitespace-folded and case-folded)``, lengthened
on a collision inside the list.  Content-derived: a list extracted again under
a later ``RULES_VERSION`` keeps the id of every row whose text did not change.
The same words twice in one matrix: the later row's id also folds in its turn
(``#2``), so every row has its own id.

ROWS ABOUT THE CANDIDATE, NOT THE JOB (location, region, work mode,
sponsorship) are not in the list: whether such a row exists depends on who is
assessed.  An assessment returns them with one of the four fixed ids
(``ELIGIBILITY_ROW_IDS``) and code recognises them by id; comparisons between
two assessments are made on the list's rows only.

FROZEN.  A list never changes: another posting text is another digest, so
another file; ``RULES_VERSION`` is bumped on purpose with a prompt change to
what counts as a requirement, and the list of an older version is moved aside
(``<sha>.<version>.json``), never deleted.

TWO FIRST ASSESSMENTS AT ONCE.  The first to write wins (``store_first``:
under the folder's lock, held for the file read and write only, never across a
model call).  The other keeps its own rows with ``requirements_ref.list =
"own"``: nothing is compared with it, and its next assessment reads the stored
list.

LATER ASSESSMENTS (``check_listed``, called at the model boundary when the
prompt carried the list): the matrix must hold exactly the listed ids, each
once.  A missing, unknown or repeated id is a validation error with the one
retry, then ``model_output_invalid``.  Each row's ``requirement``,
``class_basis`` and ``alternatives`` are then the list's; so is ``class``,
except that a row the candidate's own facts disclaim may come back ``hard``
where the list says ``askable`` (``class_from: "disclaimer"``).

Pure except ``read_list`` / ``store_first`` (one small file each).  No model call.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
from typing import Iterator

from ..canonical import canonical_json_digest, digest_imported_bytes, parse_json_bytes
from .find_jobs.assess_contracts import REQUIREMENTS_LIST_OWN, REQUIREMENTS_LIST_STORED, RequirementsRef
from .find_jobs.contracts import (
    CLASS_FROM_DISCLAIMER,
    ELIGIBILITY_ROW_IDS,
    MAX_ALTERNATIVE_CHARS,
    MAX_CLASS_BASIS_CHARS,
    MAX_ROW_ALTERNATIVES,
    FindJobsContractError,
    is_row_id,
)
from .find_jobs.discovery.storage import atomic_write, project_id

SCHEMA_VERSION = "scout-posting-requirements:1"
#: Names what counts as a requirement row. Bumped on purpose, with the prompt; an older list is moved aside.
RULES_VERSION = "req-rules:1"
ID_PREFIX = "req-"
ID_HEX_CHARS = 6
_CLASSES = ("hard", "askable", "list_item", "nice_to_have")


class RequirementsListError(ValueError):
    """A stored requirement list that cannot be read as one."""


def _hex(text: str) -> str:
    return digest_imported_bytes(text.encode("utf-8")).removeprefix("sha256:")


def fold(text: str) -> str:
    """A requirement's text as it is compared and digested: whitespace-folded and case-folded."""

    return " ".join(str(text).split()).casefold()


def is_eligibility_id(value: object) -> bool:
    return value in ELIGIBILITY_ROW_IDS


def assign_ids(posting_sha256: str, requirements: Sequence[str]) -> tuple[str, ...]:
    """The id of each requirement text, in order (the module text: content-derived, unique inside the list)."""

    seen: dict[str, int] = {}
    digests: list[str] = []
    for text in requirements:
        folded = fold(text)
        seen[folded] = seen.get(folded, 0) + 1
        turn = "" if seen[folded] == 1 else f"\n#{seen[folded]}"
        digests.append(_hex(f"{posting_sha256}\n{folded}{turn}"))
    ids: list[str] = []
    for place, digest in enumerate(digests):
        length = ID_HEX_CHARS
        # Lengthened until no other row of the list begins the same (two digests differ, so this ends).
        while any(other[:length] == digest[:length] for index, other in enumerate(digests) if index != place) and length < len(digest):
            length += 1
        ids.append(ID_PREFIX + digest[:length])
    return tuple(ids)


@dataclass(frozen=True)
class ListedRequirement:
    """One row of a posting's requirement list: its id, the posting's words, the class and the wording behind it."""

    id: str
    requirement: str
    requirement_class: str
    class_basis: str = ""
    alternatives: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id, "requirement": self.requirement, "class": self.requirement_class, "class_basis": self.class_basis,
            "alternatives": list(self.alternatives),
        }

    @classmethod
    def from_json(cls, obj: object) -> "ListedRequirement":
        if type(obj) is not dict or set(obj) != {"id", "requirement", "class", "class_basis", "alternatives"}:
            raise RequirementsListError("a requirement list row holds id, requirement, class, class_basis and alternatives")
        row_id, requirement, klass, basis, alternatives = (obj[key] for key in ("id", "requirement", "class", "class_basis", "alternatives"))
        if not is_row_id(row_id) or is_eligibility_id(row_id):
            raise RequirementsListError("a requirement list row id must be req-<hex>")
        if type(requirement) is not str or not requirement.strip() or klass not in _CLASSES:
            raise RequirementsListError("a requirement list row needs its requirement words and a known class")
        if type(basis) is not str or len(basis) > MAX_CLASS_BASIS_CHARS:
            raise RequirementsListError("a requirement list row's class_basis is a string of at most 200 characters")
        if (
            type(alternatives) is not list or len(alternatives) > MAX_ROW_ALTERNATIVES
            or any(type(item) is not str or not item or len(item) > MAX_ALTERNATIVE_CHARS for item in alternatives)
        ):
            raise RequirementsListError("a requirement list row's alternatives are at most 6 short strings")
        return cls(row_id, requirement, klass, basis, tuple(alternatives))  # type: ignore[arg-type]

    def prompt_line(self) -> str:
        """``id | class | requirement | the posting wording behind the class``, on one line."""

        requirement = " ".join(self.requirement.split())
        alternatives = f" (any one of: {', '.join(self.alternatives)})" if self.alternatives else ""
        return f"{self.id} | {self.requirement_class} | {requirement}{alternatives} | {' '.join(self.class_basis.split())}"


def rows_digest(rows: Iterable[ListedRequirement]) -> str:
    """The digest of a list's canonical rows: what two assessments must share to be compared row by row."""

    return canonical_json_digest([row.to_json() for row in rows])


@dataclass(frozen=True)
class ExtractedBy:
    """Which assessment extracted a list: the prompt version, the model id and the profile it was made for (ids only)."""

    prompt_version: str | None
    model: str | None
    profile_id: str | None

    def to_json(self) -> dict[str, object]:
        return {"prompt_version": self.prompt_version, "model": self.model, "profile_id": self.profile_id}


@dataclass(frozen=True)
class PostingRequirements:
    """A posting's stored requirement list (the module text)."""

    posting_sha256: str
    rules_version: str
    extracted_at: str
    extracted_by: ExtractedBy
    rows: tuple[ListedRequirement, ...]

    @property
    def digest(self) -> str:
        return rows_digest(self.rows)

    def ids(self) -> tuple[str, ...]:
        return tuple(row.id for row in self.rows)

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "posting_sha256": self.posting_sha256,
            "rules_version": self.rules_version,
            "digest": self.digest,
            "extracted_at": self.extracted_at,
            "extracted_by": self.extracted_by.to_json(),
            "rows": [row.to_json() for row in self.rows],
        }

    @classmethod
    def from_json(cls, obj: object) -> "PostingRequirements":
        keys = {"schema_version", "posting_sha256", "rules_version", "digest", "extracted_at", "extracted_by", "rows"}
        if type(obj) is not dict or set(obj) != keys or obj["schema_version"] != SCHEMA_VERSION:
            raise RequirementsListError("not a scout-posting-requirements:1 file")
        by = obj["extracted_by"]
        if type(by) is not dict or set(by) != {"prompt_version", "model", "profile_id"} or any(value is not None and type(value) is not str for value in by.values()):
            raise RequirementsListError("extracted_by holds prompt_version, model and profile_id")
        if type(obj["rows"]) is not list or any(type(obj[key]) is not str for key in ("posting_sha256", "rules_version", "digest", "extracted_at")):
            raise RequirementsListError("a requirement list holds its digests, its date and its rows")
        rows = tuple(ListedRequirement.from_json(item) for item in obj["rows"])
        if len({row.id for row in rows}) != len(rows):
            raise RequirementsListError("a requirement list holds each id once")
        found = cls(obj["posting_sha256"], obj["rules_version"], obj["extracted_at"], ExtractedBy(by["prompt_version"], by["model"], by["profile_id"]), rows)
        if found.digest != obj["digest"]:
            raise RequirementsListError("the requirement list's digest is not the digest of its rows")
        return found

    def ref(self, kind: str = REQUIREMENTS_LIST_STORED) -> RequirementsRef:
        return RequirementsRef(self.posting_sha256, self.rules_version, self.digest, len(self.rows), kind)

    def prompt_block(self) -> str:
        """The REQUIREMENTS block of a prompt: one row a line (the posting's words; the caller fences it as untrusted)."""

        return "\n".join(row.prompt_line() for row in self.rows)


# --- extraction: a first assessment's matrix becomes the list ---------------------------------------


def _row_value(row: object, name: str) -> object:
    if isinstance(row, Mapping):
        return row.get(name)
    value = getattr(row, "requirement_class" if name == "class" else name, None)
    return getattr(value, "value", value)


def extracted(
    posting_sha256: str, matrix: Sequence[object], *, extracted_at: str, prompt_version: str | None = None, model: str | None = None,
    profile_id: str | None = None,
) -> tuple[PostingRequirements, tuple[str, ...]]:
    """``(the list a first assessment's matrix makes, the id of every row of the matrix in its order)``. Pure.

    A row that carries one of the ``elig-`` ids keeps it and is not in the
    list; every other row gets its content-derived id.  A row with no class
    is listed ``hard``, as every reader has always read it.
    """

    listed = [row for row in matrix if not is_eligibility_id(_row_value(row, "id"))]
    ids = iter(assign_ids(posting_sha256, [str(_row_value(row, "requirement") or "") for row in listed]))
    rows: list[ListedRequirement] = []
    row_ids: list[str] = []
    for row in matrix:
        given = _row_value(row, "id")
        if is_eligibility_id(given):
            row_ids.append(str(given))
            continue
        row_id = next(ids)
        row_ids.append(row_id)
        klass = _row_value(row, "class")
        rows.append(ListedRequirement(
            row_id, str(_row_value(row, "requirement") or ""), klass if klass in _CLASSES else "hard",  # type: ignore[arg-type]
            str(_row_value(row, "class_basis") or ""), tuple(_row_value(row, "alternatives") or ()),  # type: ignore[arg-type]
        ))
    found = PostingRequirements(posting_sha256, RULES_VERSION, extracted_at, ExtractedBy(prompt_version, model, profile_id), tuple(rows))
    return found, tuple(row_ids)


# --- a later assessment: exactly the listed rows ------------------------------------------------------


def _named(ids: Sequence[str]) -> str:
    return ", ".join(ids[:6]) + (f" +{len(ids) - 6} more" if len(ids) > 6 else "")


def check_listed(rows: Sequence[Mapping[str, object]], listed: Sequence[ListedRequirement]) -> list[dict[str, object]]:
    """``rows`` (a normalized matrix) checked against the list the prompt carried, each listed row made the list's.

    Raises ``FindJobsContractError`` (the model boundary's validation error,
    fed back on the one retry) when the matrix does not hold exactly the
    listed ids, each once, or repeats an ``elig-`` id.  The answer is the
    rows in the model's order, each listed one carrying the list's
    ``requirement``, ``class_basis`` and ``alternatives`` and the list's
    ``class`` (or ``hard`` with ``class_from: "disclaimer"`` where the list
    says ``askable``).  Pure.
    """

    by_id = {row.id: row for row in listed}
    seen: list[str] = []
    unknown: list[str] = []
    repeated: list[str] = []
    without = 0
    for row in rows:
        row_id = row.get("id")
        if row_id is None:
            without += 1
        elif row_id in seen:
            repeated.append(str(row_id))
        elif row_id in by_id or is_eligibility_id(row_id):
            seen.append(str(row_id))
        else:
            unknown.append(str(row_id))
    missing = [row_id for row_id in by_id if row_id not in seen]
    if missing or unknown or repeated or without:
        problems: list[str] = []
        if missing:
            problems.append(f"{len(missing)} {'is' if len(missing) == 1 else 'are'} missing: {_named(missing)}")
        if unknown:
            problems.append(f"{len(unknown)} {'is' if len(unknown) == 1 else 'are'} not in the list: {_named(unknown)}")
        if repeated:
            problems.append(f"{len(repeated)} {'is' if len(repeated) == 1 else 'are'} repeated: {_named(repeated)}")
        if without:
            problems.append(f"{without} row{'' if without == 1 else 's'} carr{'ies' if without == 1 else 'y'} no id")
        raise FindJobsContractError(
            "invalid_value",
            # The numbers first: the model boundary feeds back the first 300 characters of this.
            f"matrix must hold exactly the {len(by_id)} listed requirement ids, each once; {'; '.join(problems)}. "
            "A row about the candidate's own location, region, work mode or sponsorship carries its elig- id",
        )
    out: list[dict[str, object]] = []
    for row in rows:
        known = by_id.get(str(row.get("id")))
        if known is None:
            out.append(dict(row))  # an elig- row: the assessment's own
            continue
        mine = dict(row)
        mine["requirement"] = known.requirement
        mine.pop("class_basis", None)
        mine.pop("alternatives", None)
        mine.pop("class_from", None)
        if known.class_basis:
            mine["class_basis"] = known.class_basis
        if known.alternatives:
            mine["alternatives"] = list(known.alternatives)
        if row.get("class") == "hard" and known.requirement_class == "askable":
            mine["class_from"] = CLASS_FROM_DISCLAIMER  # the candidate's own facts disclaim it (assess.md REQUIREMENT CLASSES)
        else:
            mine["class"] = known.requirement_class
        out.append(mine)
    return out


# --- the store ------------------------------------------------------------------------------------------


def requirements_dir(home_root: Path, target: Path) -> Path:
    return Path(home_root) / "scout" / project_id(Path(home_root), Path(target)) / "requirements"


def requirements_path(home_root: Path, target: Path, posting_sha256: str) -> Path:
    return requirements_dir(home_root, target) / f"{_hex(posting_sha256)}.json"


def _read(path: Path) -> PostingRequirements | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return PostingRequirements.from_json(parse_json_bytes(path.read_bytes()))
    except (OSError, ValueError):
        return None


def read_list(home_root: Path, target: Path, posting_sha256: str) -> PostingRequirements | None:
    """The stored list of this posting text under the CURRENT rules, or ``None`` (none, unreadable, or an older ``RULES_VERSION``)."""

    found = _read(requirements_path(home_root, target, posting_sha256))
    return found if found is not None and found.rules_version == RULES_VERSION and found.posting_sha256 == posting_sha256 else None


@contextmanager
def _folder_lock(folder: Path) -> Iterator[None]:
    """One writer at a time for the lists in ``folder``: a lock on the folder itself, held for file reads and writes only."""

    folder.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(folder, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def store_first(home_root: Path, target: Path, mine: PostingRequirements) -> tuple[PostingRequirements, bool]:
    """``(the stored list, whether ``mine`` is it)``: the first to write wins (the module text).

    A list already stored under the current rules is returned untouched. A
    file of an older ``RULES_VERSION`` is moved aside first and kept.
    """

    path = requirements_path(home_root, target, mine.posting_sha256)
    with _folder_lock(path.parent):
        stored = _read(path)
        if stored is not None and stored.posting_sha256 == mine.posting_sha256:
            if stored.rules_version == mine.rules_version:
                return stored, False
            aside = path.with_name(f"{path.stem}.{stored.rules_version.replace(':', '-')}.json")
            if not aside.exists():
                os.replace(path, aside)
        atomic_write(path, json.dumps(mine.to_json(), indent=2, sort_keys=True).encode("utf-8"))
    return mine, True


def settle_first(home_root: Path, target: Path, mine: PostingRequirements) -> RequirementsRef:
    """Store ``mine`` unless a list is stored already, and say which list a first assessment's rows are.

    ``stored`` when ``mine`` was written, or when the stored list has the
    very same rows (the same digest: the ids are content-derived).  ``own``
    when another first assessment wrote other rows first.
    """

    stored, won = store_first(home_root, target, mine)
    if won or stored.digest == mine.digest:
        return stored.ref(REQUIREMENTS_LIST_STORED)
    return mine.ref(REQUIREMENTS_LIST_OWN)


def comparable(first: RequirementsRef | None, second: RequirementsRef | None) -> bool:
    """Whether two assessments can be compared row by row: both name a list, and it is the same rows."""

    return first is not None and second is not None and first.rows_digest == second.rows_digest


__all__ = [
    "ID_PREFIX",
    "RULES_VERSION",
    "SCHEMA_VERSION",
    "ExtractedBy",
    "ListedRequirement",
    "PostingRequirements",
    "RequirementsListError",
    "assign_ids",
    "check_listed",
    "comparable",
    "extracted",
    "fold",
    "is_eligibility_id",
    "read_list",
    "requirements_dir",
    "requirements_path",
    "rows_digest",
    "settle_first",
    "store_first",
]
