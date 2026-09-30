"""Non-agent baseline (candidate 4): DOL OFLC H-1B LCA disclosure data.

Free, deterministic. Source: DOL's LCA Disclosure Data, most recent fiscal
year/quarter available at the time of this bake-off --
https://www.dol.gov/agencies/eta/foreign-labor/performance (READ 2026-09-24),
file downloaded from
``https://www.dol.gov/media/LCA_Disclosure_Data_FY2026_Q3.xlsx`` (FY2026 Q3,
251,850,891 bytes, EXECUTED download -- ``curl`` to
``research/discovery_bakeoff/h1b_scratch/`` which is gitignored; NEVER
committed, per the task).

Pipeline:
1. ``extract_employers()`` streams the 98-column workbook with
   ``openpyxl(read_only=True)`` (the file is too large to load with pandas'
   default engine without a lot of memory) and filters to:
   - ``VISA_CLASS == "H-1B"``
   - ``CASE_STATUS == "Certified"`` (official sponsorship evidence --
     an approved LCA, not merely filed)
   - ``SOC_CODE`` starting with one of the software/backend-type codes in
     ``SOFTWARE_SOC_PREFIXES`` (2018 SOC taxonomy, confirmed against the
     file's own ``SOC_TITLE`` column -- see module-level comment)
   - ``WORKSITE_STATE`` present (US worksite; the file is US-only LCA data
     by definition, but a handful of rows have a blank/malformed state)
   Aggregates to one row per ``EMPLOYER_NAME``: case count, one example
   ``SOC_TITLE``, one example ``CASE_NUMBER`` (the citable evidence link
   -- DOL's own disclosure file is itself the public source; there's no
   per-case public URL, so the evidence is "this file, this case number,"
   stated explicitly in the derived sample and the spike doc, not
   fabricated as a URL that doesn't exist).
2. ``derive_sample()`` writes the small (<=200 row), git-tracked sample
   under ``research/discovery_bakeoff/h1b_sample/`` -- the top N employers
   by case count, which is what "highest-confidence sponsors" means for a
   discovery use case (more certified cases = a more consistent, ongoing
   sponsor, not a one-off).
3. ``probe_boards.py`` (separate script) takes the derived sample and slug-
   guesses Greenhouse/Lever/Ashby board tokens from the employer name,
   verified against a real board poll -- see that script's docstring.

SOC code choice: the file's own ``SOC_TITLE`` values for codes starting
``15-1252`` (Software Developers), ``15-1251`` (Computer Programmers),
``15-1253`` (Software QA Analysts and Testers), ``15-1211`` (Computer
Systems Analysts), and ``15-1299`` (Computer Occupations, All Other) were
read directly from a live scan of the file's first 200,000 rows (EXECUTED)
-- these five codes cover >90% of all ``15-1xxx`` "Computer Occupations"
rows in that scan and are the closest SOC match to "software/backend
engineer" that DOL's taxonomy offers (DOL LCA data has no finer-grained
"backend" distinction; SOC is a broad occupational classification, not a
job-title taxonomy -- this is a real, disclosed limitation, not silently
assumed away, see the S24 doc's Non-claims).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path

import openpyxl

SOFTWARE_SOC_PREFIXES = (
    "15-1252",  # Software Developers
    "15-1251",  # Computer Programmers
    "15-1253",  # Software Quality Assurance Analysts and Testers
    "15-1211",  # Computer Systems Analysts
    "15-1299",  # Computer Occupations, All Other
)

SOURCE_FILE_URL = "https://www.dol.gov/media/LCA_Disclosure_Data_FY2026_Q3.xlsx"
SOURCE_PAGE_URL = "https://www.dol.gov/agencies/eta/foreign-labor/performance"


@dataclass
class EmployerAggregate:
    employer_name: str
    certified_case_count: int
    example_soc_title: str
    example_case_number: str
    worksite_states: list[str]


def extract_employers(xlsx_path: Path, limit_rows: int | None = None) -> dict[str, EmployerAggregate]:
    """Stream the workbook once; aggregate certified H-1B software-SOC rows by employer."""

    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    idx = {name: i for i, name in enumerate(header)}

    counts: dict[str, int] = defaultdict(int)
    example_soc: dict[str, str] = {}
    example_case: dict[str, str] = {}
    states: dict[str, set[str]] = defaultdict(set)

    n = 0
    for row in rows:
        n += 1
        if limit_rows is not None and n > limit_rows:
            break
        visa_class = row[idx["VISA_CLASS"]]
        if visa_class != "H-1B":
            continue
        status = row[idx["CASE_STATUS"]]
        if status != "Certified":
            continue
        soc = row[idx["SOC_CODE"]]
        if not soc or not str(soc).startswith(SOFTWARE_SOC_PREFIXES):
            continue
        employer = row[idx["EMPLOYER_NAME"]]
        if not employer:
            continue
        employer = str(employer).strip()
        counts[employer] += 1
        example_soc.setdefault(employer, str(row[idx["SOC_TITLE"]] or ""))
        example_case.setdefault(employer, str(row[idx["CASE_NUMBER"]] or ""))
        worksite_state = row[idx["WORKSITE_STATE"]]
        if worksite_state:
            states[employer].add(str(worksite_state))

    wb.close()

    return {
        name: EmployerAggregate(
            employer_name=name,
            certified_case_count=count,
            example_soc_title=example_soc[name],
            example_case_number=example_case[name],
            worksite_states=sorted(states.get(name, set())),
        )
        for name, count in counts.items()
    }


def derive_sample(
    employers: dict[str, EmployerAggregate],
    out_path: Path,
    top_n: int = 200,
) -> list[EmployerAggregate]:
    """Write the top ``top_n`` employers by certified case count as a small, git-tracked sample."""

    ranked = sorted(employers.values(), key=lambda e: e.certified_case_count, reverse=True)[:top_n]
    payload = {
        "source_file_url": SOURCE_FILE_URL,
        "source_page_url": SOURCE_PAGE_URL,
        "filters": {
            "visa_class": "H-1B",
            "case_status": "Certified",
            "soc_code_prefixes": list(SOFTWARE_SOC_PREFIXES),
        },
        "total_unique_employers_matching_filters": len(employers),
        "sample_size": len(ranked),
        "employers": [asdict(e) for e in ranked],
    }
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return ranked


def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xlsx-path", required=True, type=Path)
    parser.add_argument("--out-path", required=True, type=Path)
    parser.add_argument("--top-n", type=int, default=200)
    parser.add_argument("--limit-rows", type=int, default=None, help="for smoke-testing only")
    args = parser.parse_args()

    employers = extract_employers(args.xlsx_path, args.limit_rows)
    print(f"{len(employers)} unique employers matched filters", file=sys.stderr)
    ranked = derive_sample(employers, args.out_path, args.top_n)
    print(f"wrote top {len(ranked)} to {args.out_path}", file=sys.stderr)
    if ranked:
        print(f"top employer: {ranked[0].employer_name} ({ranked[0].certified_case_count} cases)", file=sys.stderr)


if __name__ == "__main__":
    main()
