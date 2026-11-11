"""P3c: Scout is a gig. No core module imports it; `gigai.cli` mounts it through its `gigai.gigs` entry point.

Core is every module of the `gigai` package outside `gigai/scout/`. Core reaches Scout objects only through named
hooks (`gigai.sdk.hooks`), whose bridge rows are strings, not import statements.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import gigai

SRC = Path(gigai.__file__).resolve().parent


def _scout_imports(relative: Path, text: str) -> list[tuple[int, str]]:
    """``(line, module)`` for every import statement in module ``gigai/<relative>`` that names ``gigai.scout`` or below."""

    package = ["gigai", *relative.parts[:-1]]
    found = []
    for node in ast.walk(ast.parse(text, filename=str(relative))):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            module = ".".join([*base, *([node.module] if node.module else [])])
            # `from gigai import scout` / `from . import scout` name the package in the imported names.
            modules = [module, *(f"{module}.{alias.name}" for alias in node.names)]
        else:
            continue
        found += [(node.lineno, name) for name in modules if name == "gigai.scout" or name.startswith("gigai.scout.")]
    return found


def test_no_core_module_imports_scout():
    core = [path for path in sorted(SRC.rglob("*.py")) if path.relative_to(SRC).parts[0] != "scout"]
    assert len(core) > 50  # the scan really walked the package

    offenders = {
        str(relative): hits
        for relative in (path.relative_to(SRC) for path in core)
        if (hits := _scout_imports(relative, (SRC / relative).read_text(encoding="utf-8")))
    }

    assert offenders == {}


def test_the_scan_sees_every_spelling_of_a_scout_import():
    # cli.py's shape until P3c (`from .scout.report_cli import ...`) and the other spellings, from gigai/gigs/.
    text = (
        "from ..scout.report_cli import report_group\n"
        "from .. import scout\n"
        "import gigai.scout.gig\n"
        "from gigai.scout.answer_cli import answer_group\n"
        "from gigai import scout as s\n"
        "def lazy():\n    from ..scout import interview_cli\n"
        "from ..sdk import hooks\n"
        "from .scout import nothing\n"  # gigai.gigs.scout: not Scout
        "x = 'gigai.scout.contracts:normalize_url'\n"
    )

    assert sorted({line for line, _ in _scout_imports(Path("gigs", "sample.py"), text)}) == [1, 2, 3, 4, 5, 7]
    assert {line for line, _ in _scout_imports(Path("cli.py"), "from .scout.report_cli import report_group\n")} == {1}


def test_importing_the_cli_and_rendering_root_help_load_no_scout_module():
    code = (
        "import json, sys\n"
        "from click.testing import CliRunner\n"
        "import gigai.cli\n"
        "after_import = sorted(m for m in sys.modules if m.startswith('gigai.scout'))\n"
        "result = CliRunner().invoke(gigai.cli.cli, ['--help'], prog_name='gigai')\n"
        "after_help = sorted(m for m in sys.modules if m.startswith('gigai.scout'))\n"
        "print(json.dumps([after_import, after_help, result.exit_code, type(gigai.cli.cli.commands['scout']).__name__]))\n"
    )
    env = {key: value for key, value in os.environ.items() if key != "_GIGAI_COMPLETE"}
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120, check=False)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == [[], [], 0, "LazyGig"]
