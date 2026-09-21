"""Pure role-text boundary tests; not Plan publication or Run acceptance proof."""

from copy import deepcopy
from importlib import resources
import json

from jsonschema import Draft202012Validator
import pytest

from gigai.scout_inputs import (
    ScoutInputError,
    resolve_external_input,
    revalidate_external_input,
    validate_role_request,
)


def _request():
    return {
        "family": "role_request",
        "role_title": " Forward Deployed Engineer ",
        "role_context": "Denver; customer-facing engineering",
    }


def _validator():
    schema = json.loads(resources.files("gigai.schemas").joinpath(
        "external-recording-plan-v2.schema.json"
    ).read_text())
    return Draft202012Validator(schema["$defs"]["role_request"])


def test_role_request_is_exact_inert_plan_data_not_a_native_record():
    supplied = _request()
    result = validate_role_request(supplied)
    assert result == supplied
    assert result is not supplied
    assert _validator().is_valid(result)
    assert resolve_external_input(None, None, supplied, allow_role_request=True) == result
    revalidate_external_input(None, None, result, allow_role_request=True)
    # No synthetic record ID, revision ID, actor or approval is invented.
    assert set(result) == {"family", "role_title", "role_context"}
    newer = deepcopy(supplied)
    newer["role_title"] = "Staff Software Engineer"
    assert validate_role_request(newer) != result
    assert result["role_title"] == " Forward Deployed Engineer "


def test_v1_callers_do_not_admit_role_requests_implicitly():
    with pytest.raises(ScoutInputError, match="version 2"):
        resolve_external_input(None, None, _request())
    with pytest.raises(ScoutInputError, match="version 2"):
        revalidate_external_input(None, None, _request())


@pytest.mark.parametrize("field,value", [
    ("family", "g45_run_input"), ("family", []),
    ("role_title", None), ("role_title", []), ("role_title", ""),
    ("role_title", " \t\n"), ("role_title", "a" * 301),
    ("role_context", []), ("role_context", ""),
    ("role_context", " \t"), ("role_context", "a" * 4097),
])
def test_malformed_role_request_has_typed_redacted_refusal_and_schema_parity(field, value):
    candidate = _request()
    candidate[field] = value
    with pytest.raises(ScoutInputError, match="^role request is invalid$"):
        validate_role_request(candidate)
    assert not _validator().is_valid(candidate)


@pytest.mark.parametrize("field", ["family", "role_title", "role_context"])
def test_role_request_missing_fields_refuse(field):
    candidate = _request()
    del candidate[field]
    with pytest.raises(ScoutInputError):
        validate_role_request(candidate)
    assert not _validator().is_valid(candidate)


def test_extra_authority_fields_refuse_but_unknown_context_is_explicit():
    candidate = _request()
    candidate["confirmed"] = True
    with pytest.raises(ScoutInputError):
        validate_role_request(candidate)
    assert not _validator().is_valid(candidate)
    del candidate["confirmed"]
    candidate["role_context"] = None
    assert validate_role_request(candidate) == candidate
    assert _validator().is_valid(candidate)


@pytest.mark.parametrize("family", [[], {}, None, 42])
def test_malformed_family_does_not_escape_as_unhashable_type_error(family):
    candidate = {"family": family}
    with pytest.raises(ScoutInputError):
        resolve_external_input(None, None, candidate, allow_role_request=True)
    with pytest.raises(ScoutInputError):
        revalidate_external_input(None, None, candidate, allow_role_request=True)
