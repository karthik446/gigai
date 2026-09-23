"""S14 F6: can a v2 active-gig-version pointer ever satisfy the v1 schema?

Run from the repo root:  uv run python <this file>
"""
import json
v1 = json.load(open("src/gigai/schemas/active-gig-version.schema.json"))
v2 = json.load(open("src/gigai/schemas/active-gig-version-v2.schema.json"))
print("v1_additionalProperties:", v1.get("additionalProperties"))
print("v2_required_keys_not_allowed_by_v1:", sorted(set(v2["required"]) - set(v1["properties"])))
