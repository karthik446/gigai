"""S14 F4/F6: run real external-recording records through private_package_provenance.

Run from the repo root:  uv run python <this file> <basetemp>
Checks one record per (family, schema_version) found under runs/: validity against its
own schema, the guard at its real relative path, and the guard when renamed to docs/x.json.
Prints family/version/booleans only.
"""
import json, pathlib, sys
from gigai.package_privacy import private_package_provenance
from gigai.validators import validate_serialized_contract

FAMILIES = {"external-run": "run", "checkpoint": "checkpoint", "receipt": "receipt"}
seen = set()
for path in sorted(pathlib.Path(sys.argv[1]).rglob("runs/*/**/*.json")):
    data = path.read_bytes()
    try:
        version = json.loads(data).get("schema_version")
    except Exception:
        continue
    family = FAMILIES.get(path.name.split("_")[0].replace(".json", ""))
    if not family or (family, version) in seen:
        continue
    seen.add((family, version))
    rel = str(path).split("/gigs/")[1].split("/", 1)[1]
    schema = f"external-recording-{family}{'-v2' if version == '2.0' else ''}.schema.json"
    print(f"{family:10} v{version} valid_against_own_schema={validate_serialized_contract(schema, data).valid} "
          f"guard_in_place={private_package_provenance(rel, data)} guard_renamed_docs_x_json={private_package_provenance('docs/x.json', data)}")
print("families_sampled:", sorted(seen))
