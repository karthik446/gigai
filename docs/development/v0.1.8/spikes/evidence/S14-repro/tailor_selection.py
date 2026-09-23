"""S14 F6: validate TailorSelection.to_json() for the test fixture's shape.

Run from the repo root:  uv run python <this file>
Covers ONE shape (proposal and answer present); other shapes are unchecked.
"""
import sys
sys.path.insert(0, "tests/behaviors/scout_tracking_reporting")
import test_scout_r2_tailor as fixture
from gigai.canonical import canonical_json_bytes
from gigai.validators import validate_serialized_contract

report = validate_serialized_contract("scout-tailor-selection-v2.schema.json",
                                      canonical_json_bytes(fixture._selection().to_json()))
print("fixture_shape=proposal+answer valid=" + str(report.valid), [f.message[:100] for f in report.findings][:3])
