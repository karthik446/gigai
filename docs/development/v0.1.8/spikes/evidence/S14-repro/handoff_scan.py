"""S14 F6: validate every journal handoff under a basetemp against handoff-frontmatter.

Run from the repo root:  uv run python <this file> <basetemp>
Prints counts only (no handoff bodies, paths, IDs, or capability values).
"""
import collections, pathlib, re, sys
from gigai import journal
from gigai.canonical import canonical_json_bytes
from gigai.validators import validate_serialized_contract

total = ok = 0
extra, other, by_transition = collections.Counter(), collections.Counter(), collections.Counter()
for path in pathlib.Path(sys.argv[1]).rglob("handoffs/*.txt"):
    meta = journal._read_handoff(path)
    total += 1
    report = validate_serialized_contract("handoff-frontmatter.schema.json", canonical_json_bytes(meta))
    if report.valid:
        ok += 1
        continue
    by_transition[meta.get("transition")] += 1
    for finding in report.findings:
        if "Additional properties" in finding.message:
            for key in re.findall(r"'([a-z_]+)'", finding.message):
                extra[key] += 1
        else:
            other[f"{finding.location}: {finding.message[:70]}"] += 1
print(f"handoffs={total} valid={ok} invalid={total - ok}")
print("unexpected_keys:", dict(extra))
print("other_findings:", dict(other.most_common(10)))
print("invalid_by_transition:", dict(by_transition.most_common(20)))
