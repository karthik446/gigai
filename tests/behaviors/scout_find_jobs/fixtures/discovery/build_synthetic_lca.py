"""Builds a tiny synthetic DOL LCA disclosure workbook for h1b_source tests.

Not real DOL data -- a minimal 98-column-shaped (only the columns
``h1b_source.py`` actually reads) workbook with a handful of synthetic rows
covering: a certified H-1B software-SOC row for a real-looking startup name
(should rank/probe), a non-H-1B row (filtered out), a non-certified row
(filtered out), a non-software-SOC row (filtered out), and a staffing-firm
-pattern employer name (should be de-prioritized by
``is_likely_staffing_or_outsourcing``).
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

_HEADER = ["VISA_CLASS", "CASE_STATUS", "SOC_CODE", "SOC_TITLE", "EMPLOYER_NAME", "CASE_NUMBER"]

_ROWS = [
    ("H-1B", "Certified", "15-1252", "Software Developers", "Testcorp Startup Inc", "I-200-00001"),
    ("H-1B", "Certified", "15-1252", "Software Developers", "Testcorp Startup Inc", "I-200-00002"),
    ("H-1B", "Certified", "15-1252", "Software Developers", "Acme Widgets LLC", "I-200-00003"),
    ("H-1B", "Denied", "15-1252", "Software Developers", "Rejected Sponsor Inc", "I-200-00004"),
    ("H-2B", "Certified", "15-1252", "Software Developers", "Wrong Visa Corp", "I-200-00005"),
    ("H-1B", "Certified", "25-1011", "Business Teachers", "Not Software Corp", "I-200-00006"),
    ("H-1B", "Certified", "15-1252", "Software Developers", "Infosys Limited", "I-200-00007"),
    ("H-1B", "Certified", "15-1251", "Computer Programmers", "Cognizant Technology Solutions", "I-200-00008"),
]


def build(path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(_HEADER)
    for row in _ROWS:
        ws.append(row)
    wb.save(path)
    return path


if __name__ == "__main__":
    import sys

    build(Path(sys.argv[1]))
