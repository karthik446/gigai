"""0110-007: the route table (``api/openapi.py``) cannot drift from the dispatcher.

``server.py``'s dispatch is a hand-written ``if path == ...`` chain, so the table is checked against
an AST scan of it (``route_inventory.discover_routes``, the same scan ``test_route_inventory.py``
uses): a route served without a table entry, a table entry the dispatcher no longer serves, an
entry with no description, no example, no effect or no cost class, and a generated document that
is not a valid OpenAPI 3.1 structure all fail here. No server is started.

The document is checked by ``openapi.validate_document`` (a hand-rolled structural check of the
version, ``info``, paths, parameters, request bodies, responses and ``$ref``s) and, for every
embedded schema and every 200 example, by ``jsonschema`` -- already a declared runtime
dependency of gigai, so no new package.
"""

from __future__ import annotations

import ast
import copy
import re
from pathlib import Path

import jsonschema

from gigai.scout.find_jobs.api import openapi
from tests.api_e2e.route_inventory import Route, discover_routes

_SERVER = Path(openapi.__file__).with_name("server.py")
_TABLE = {Route(route.method, route.path) for route in openapi.ROUTES}


def _do_get_literals() -> set[str]:
    tree = ast.parse(_SERVER.read_text(encoding="utf-8"))
    method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "do_GET")
    return {node.value for node in ast.walk(method) if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def test_every_route_the_dispatcher_serves_has_a_table_entry_and_the_reverse() -> None:
    served = set(discover_routes())
    # ``GET /api`` (and ``/api/``) is matched by ``path in ("/api", "/api/")``, a shape the scan
    # does not read; it is pinned below by reading do_GET's own literals.
    assert {"/api", "/api/"} <= _do_get_literals(), "do_GET no longer answers /api"
    tabled = _TABLE - {Route("GET", "/api")}
    missing = served - tabled
    assert not missing, f"served with no entry in api/openapi.py ROUTES: {sorted(missing)}"
    stale = tabled - served
    assert not stale, f"ROUTES names routes the dispatcher does not serve: {sorted(stale)}"


def test_the_table_has_no_duplicate_entries() -> None:
    keys = [route.key for route in openapi.ROUTES]
    assert len(keys) == len(set(keys))


def test_every_route_is_described_and_exemplified() -> None:
    for route in openapi.ROUTES:
        where = f"{route.method} {route.path}"
        assert route.summary.strip(), f"{where}: no summary"
        assert route.effect in ("read", "write"), where
        assert route.external in ("none", "model", "network"), where
        assert route.example, f"{where}: no response example"
        if route.method in ("POST", "PUT") and any(p.required for p in route.params if p.where == "body"):
            assert route.request_example, f"{where}: a body with required keys needs a request example"
        for param in route.params:
            assert param.description.strip(), f"{where}: parameter {param.name} has no description"
        if route.method == "GET":
            assert route.effect == "read", f"{where}: a GET must not write"
        if route.schema_version:
            assert route.example.get("schema_version") == route.schema_version, f"{where}: example lacks its schema_version"
        placeholders = set(re.findall(r"\{([a-z_]+)\}", route.path))
        assert placeholders == {p.name for p in route.params if p.where == "path"}, f"{where}: path params differ from the template"
        assert route.errors or route.method == "GET" or route.path in {"/api/health"}, f"{where}: no error codes (writes name theirs)"


def test_the_generated_document_is_valid_openapi_31() -> None:
    document = openapi.openapi_document(version="0.1.10")
    assert openapi.validate_document(document) == []
    assert document["openapi"].startswith("3.1")
    operations = [op for item in document["paths"].values() for op in item.values()]
    assert len(operations) == len(openapi.ROUTES)
    for operation in operations:
        assert operation["x-gigai-effect"] in ("read", "write")
        assert operation["x-gigai-external"] in ("none", "model", "network")
        assert operation["description"].strip()
    validator = jsonschema.Draft202012Validator
    for item in document["paths"].values():
        for operation in item.values():
            body = operation.get("requestBody")
            if body:
                validator.check_schema(body["content"]["application/json"]["schema"])
            for status, response in operation["responses"].items():
                for content_type, media in response.get("content", {}).items():
                    if "$ref" in media["schema"]:
                        continue
                    validator.check_schema(media["schema"])
                    if status == "200" and content_type == "application/json":
                        jsonschema.validate(media["example"], media["schema"])
    validator.check_schema(document["components"]["schemas"]["Error"])


def test_the_structure_check_rejects_a_broken_document() -> None:
    good = openapi.openapi_document(version="0.1.10")
    assert openapi.validate_document(good) == []

    wrong_version = copy.deepcopy(good)
    wrong_version["openapi"] = "3.0.3"
    assert any("3.1" in problem for problem in openapi.validate_document(wrong_version))

    no_responses = copy.deepcopy(good)
    del no_responses["paths"]["/api/health"]["get"]["responses"]
    assert any("responses" in problem for problem in openapi.validate_document(no_responses))

    lost_path_param = copy.deepcopy(good)
    lost_path_param["paths"]["/api/runs/{run_id}"]["get"]["parameters"] = []
    assert any("path parameters" in problem for problem in openapi.validate_document(lost_path_param))

    dangling_ref = copy.deepcopy(good)
    dangling_ref["paths"]["/api/health"]["get"]["responses"]["500"] = {
        "description": "x",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Missing"}}},
    }
    assert any("$ref" in problem for problem in openapi.validate_document(dangling_ref))


def test_a_route_without_a_table_entry_is_caught() -> None:
    """Proves the drift check is load-bearing: drop one real route from a copy of the table."""

    tampered = _TABLE - {Route("GET", "/api/jobs")}
    assert Route("GET", "/api/jobs") in set(discover_routes()) - tampered


def test_route_for_matches_parametric_paths_and_allowed_keys_come_from_the_table() -> None:
    assert openapi.route_for("GET", "/api/runs/run_1/results").path == "/api/runs/{run_id}/results"
    assert openapi.route_for("PUT", "/api/profiles/p1").path == "/api/profiles/{profile_id}"
    assert openapi.route_for("GET", "/api/runs/run_1/results/extra") is None
    assert openapi.route_for("DELETE", "/api/profiles/p1") is None
    assert openapi.allowed_keys(openapi.route_for("GET", "/api/jobs")) == ["url"]
    assert openapi.allowed_keys(openapi.route_for("POST", "/api/watchlist")) == ["url"]
    assert openapi.allowed_keys(openapi.route_for("GET", "/api/runs/r/results")) == ["limit", "offset"]


def test_with_allowed_keys_lifts_a_nested_objects_own_keys_and_leaves_unrelated_errors_alone() -> None:
    bare = {"error": {"code": "unknown_key", "message": "job contains unknown key(s): ['x']"}}
    assert openapi.with_allowed_keys("POST", "/api/assess", bare) == bare
    nested = {"error": {"code": "unknown_key", "message": "job contains unknown key(s): ['x'] (allowed: a, b)"}}
    served_nested = openapi.with_allowed_keys("POST", "/api/assess", nested)["error"]
    assert served_nested["allowed_keys"] == ["a", "b"] and served_nested["message"] == nested["error"]["message"]
    other = {"error": {"code": "not_found", "message": "no such route"}}
    assert openapi.with_allowed_keys("GET", "/api/jobs", other) == other
    top = {"error": {"code": "unknown_key", "message": "assess_request contains unknown key(s): ['x']"}}
    served = openapi.with_allowed_keys("POST", "/api/assess", top)
    assert "job" in served["error"]["allowed_keys"] and served["error"]["message"].startswith("assess_request contains")


def test_every_operation_has_a_short_summary_a_known_tag_a_description_and_an_example() -> None:
    document = openapi.openapi_document(version="0.1.10")
    assert [tag["name"] for tag in document["tags"]] == list(openapi.TAGS)
    seen_tags: set[str] = set()
    for path, item in document["paths"].items():
        for method, operation in item.items():
            where = f"{method.upper()} {path}"
            assert 0 < len(operation["summary"]) <= 80, f"{where}: summary must be one short line"
            assert operation["summary"] != operation["description"], f"{where}: description adds nothing to the summary"
            assert len(operation["tags"]) == 1 and operation["tags"][0] in openapi.TAGS, f"{where}: tag not in the fixed list"
            seen_tags.update(operation["tags"])
            assert operation["description"].strip(), where
            ok = operation["responses"]["200"]["content"]
            assert any("example" in media for media in ok.values()) or "application/json" not in ok, f"{where}: no example"
    assert seen_tags == set(openapi.TAGS), f"unused tags: {set(openapi.TAGS) - seen_tags}"
    assert len({route.summary for route in openapi.ROUTES}) == len(openapi.ROUTES), "summaries must be unique (they are page titles)"


def test_placeholders_in_descriptions_are_code_spans() -> None:
    """``<url>`` outside backticks is read as HTML by the docs renderer and vanishes."""

    document = openapi.openapi_document(version="0.1.10")
    texts: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "description" and isinstance(value, str):
                    texts.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(document)
    for text in texts:
        bare = re.sub(r"`[^`]*`", "", text)
        assert not re.search(r"<[A-Za-z][^>]*>", bare), f"unescaped placeholder in: {text}"
