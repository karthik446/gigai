from __future__ import annotations

import ast
from pathlib import Path


PRODUCT_ROOT = Path(__file__).parents[1] / "src" / "gigai"
CANONICAL_MODULE = PRODUCT_ROOT / "canonical.py"
CANONICAL_API_NAMES = {
    "canonical_json_bytes",
    "canonical_json_digest",
    "canonicalize_owned_text",
    "digest_imported_bytes",
    "digest_owned_text",
    "parse_json_front_matter",
    "render_json_front_matter",
}


def test_canonical_module_owns_all_product_sha256_implementation() -> None:
    violations: list[str] = []
    for path in PRODUCT_ROOT.rglob("*.py"):
        if path == CANONICAL_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                if any(name == "hashlib" or name.startswith("hashlib.") for name in names):
                    violations.append(f"{path.relative_to(PRODUCT_ROOT)} imports hashlib")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "sha256"
            ):
                violations.append(f"{path.relative_to(PRODUCT_ROOT)} calls sha256")
    assert violations == []


def test_no_second_product_module_defines_canonical_api() -> None:
    violations: list[str] = []
    for path in PRODUCT_ROOT.rglob("*.py"):
        if path == CANONICAL_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in CANONICAL_API_NAMES
            ):
                violations.append(f"{path.relative_to(PRODUCT_ROOT)} defines {node.name}")
    assert violations == []
