from __future__ import annotations

import ast
from pathlib import Path


G21_MODULES = (
    Path(__file__).parents[1] / "src/gigai/occurrence.py",
    Path(__file__).parents[1] / "src/gigai/comparison.py",
)
FORBIDDEN_IMPORTS = {
    "socket",
    "subprocess",
    "httpx",
    "requests",
    "urllib",
    "credentials",
    "target_effect",
    "schedule",
    "apscheduler",
    "croniter",
    "threading",
    "asyncio",
}


def test_g21_modules_have_no_scheduler_provider_or_effectful_imports() -> None:
    for path in G21_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        assert not imported & FORBIDDEN_IMPORTS, (path, imported & FORBIDDEN_IMPORTS)


def test_g21_runtime_has_no_background_or_retry_process_constructs() -> None:
    source = "\n".join(path.read_text(encoding="utf-8").lower() for path in G21_MODULES)
    tree = [ast.parse(path.read_text(encoding="utf-8"), filename=str(path)) for path in G21_MODULES]
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for module in tree
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) or isinstance(node.func, ast.Name))
    }
    assert not called & {"sleep", "create_task", "start_new_thread", "Popen"}
    assert "cron" not in source
    assert "apscheduler" not in source
