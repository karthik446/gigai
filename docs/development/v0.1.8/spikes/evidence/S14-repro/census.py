"""S14 census: per-schema filename/$id references in src, schema $refs, test hits.

Run from the repo root:  uv run python <this file> <out.json>
Grep-based: a name assembled at runtime can be missed (see S14 audit, Method).
"""
import json, re, subprocess, pathlib, sys

sd = pathlib.Path("src/gigai/schemas")
files = sorted(p.name for p in sd.glob("*.schema.json"))
ids = {f: json.loads((sd / f).read_text())["$id"] for f in files}
val = pathlib.Path("src/gigai/validators.py").read_text().splitlines()
reg_lines, inblk = set(), False
for i, line in enumerate(val, 1):  # SCHEMA_NAMES / _VERSIONED_SCHEMA_NAMES tuples
    if re.match(r"^(SCHEMA_NAMES|_VERSIONED_SCHEMA_NAMES)\b", line):
        inblk = True
    if inblk:
        reg_lines.add(i)
    if inblk and line.strip() == ")":
        inblk = False

def grep(pat, paths):
    r = subprocess.run(["grep", "-rnF", pat, *paths, "--include=*.py"], capture_output=True, text=True)
    return [x for x in r.stdout.splitlines() if x]

def in_registry(hit):
    return hit.startswith("src/gigai/validators.py:") and int(hit.split(":")[1]) in reg_lines

out = []
for f in files:
    src = [x for x in grep(f, ["src/gigai"]) if not x.startswith("src/gigai/schemas/")]
    r = subprocess.run(["grep", "-lF", ids[f], *[str(sd / g) for g in files if g != f]], capture_output=True, text=True)
    out.append({
        "file": f, "id": ids[f], "registry": any(in_registry(x) for x in src),
        "src": [x for x in src if not in_registry(x)],
        "urn_py": [x for x in grep(ids[f], ["src/gigai"]) if not x.startswith("src/gigai/schemas/")],
        "ref_by": [pathlib.Path(x).name for x in r.stdout.split()],
        "tests": len(grep(f, ["tests"])),
    })
json.dump(out, open(sys.argv[1], "w"), indent=1)
print(f"schemas={len(out)} with_src_call_site={sum(1 for o in out if o['src'])} in_registry={sum(o['registry'] for o in out)}")
