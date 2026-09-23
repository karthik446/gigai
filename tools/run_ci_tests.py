"""Run the repository's reproducible offline CI phases.

The source suite is intentionally one unfiltered pytest invocation.  It uses a
bounded, resource-aware xdist plan; installed test candidates are discovered as
explicit AST-derived node ids so a filename move cannot silently drop them and
a broad ``-k`` expression cannot run unrelated tests.  Pytest collection and
the repository's conftest/marker policy remain authoritative when those ids
execute; the runner does not invoke providers, models, or live/UAT targets.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
BEHAVIOR_MANIFEST = "research/evals/g28/g26-g27-manifest.json"
BEHAVIOR_OBSERVATIONS = "research/evals/g28/g26-g27-deterministic-observations.json"
BEHAVIOR_SPLITS = ("development", "calibration", "final_held_out_acceptance")
DEFAULT_WHEEL_PYTHON = ROOT / ".wheel-venv" / "bin" / "python"
DEFAULT_XDIST_WORKERS = "auto"
DEFAULT_XDIST_MAX_WORKERS = 14
DEFAULT_XDIST_DIST = "worksteal"
SAFE_XDIST_DISTS = frozenset(("load", "loadfile", "loadscope", "worksteal"))


class VirtualenvSafetyError(SystemExit):
    """Raised (as a SystemExit) when a venv target fails the safety guard.

    A managed CPython install (``uv python install``), a pyenv version, or a
    system Python directory must never be handed to ``uv venv``/``uv pip
    install`` as the venv target: those commands create or overwrite
    ``bin/python*``, ``pyvenv.cfg``, and site-packages in place, which is
    destructive to a real interpreter install rather than a disposable venv.
    """


def _unresolved_absolute(path: str | Path) -> Path:
    """Make ``path`` absolute against the repo root without following symlinks.

    ``Path.resolve()``/``os.path.realpath`` walk every symlink component,
    which is exactly wrong here: ``.wheel-venv/bin/python`` is a symlink
    ``uv venv`` itself creates, pointing at a real interpreter (often a
    ``uv``-managed CPython install under ``~/.local/share/uv/python/...``).
    Resolving it before deriving the venv directory silently swaps "the
    disposable venv we created" for "the real interpreter install it points
    at". ``os.path.abspath`` only joins against the cwd and lexically
    collapses ``.``/``..``; it never touches the filesystem or a symlink.
    """

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    return Path(os.path.abspath(candidate))


def _rooted(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def _forbidden_venv_roots() -> tuple[Path, ...]:
    """Directories that must never be treated as a disposable venv target.

    Each is a real interpreter/toolchain install tree, not a venv: ``uv``'s
    managed CPython downloads, a pyenv version root, and the two
    conventional system-wide install prefixes.
    """

    return (
        Path.home() / ".local" / "share" / "uv" / "python",
        Path.home() / ".pyenv",
        Path("/usr"),
        Path("/opt"),
    )


def _is_within(path: Path, ancestor: Path) -> bool:
    try:
        path.relative_to(ancestor)
    except ValueError:
        return False
    return True


def _ensure_safe_virtualenv_dir(virtualenv: Path, *, allowed_roots: tuple[Path, ...] = ()) -> None:
    """Fail closed before any ``uv venv``/``uv pip install`` touches ``virtualenv``.

    ``virtualenv`` must already be an unresolved absolute path (see
    ``_unresolved_absolute``); this function does not itself resolve
    symlinks, so a caller that passes a resolved/symlink-followed path
    defeats the guard. It must be inside the repository root or one of the
    explicitly allowed extra roots (e.g. a test's own temp dir), must not be
    inside any forbidden interpreter/toolchain root, and if it already
    exists on disk it must look like a venv (contain ``pyvenv.cfg``) rather
    than an arbitrary directory such as an interpreter's own install tree.
    """

    permitted_roots = (ROOT, *allowed_roots)
    if not any(_is_within(virtualenv, root) for root in permitted_roots):
        raise VirtualenvSafetyError(
            f"refusing to create/use a venv outside the repository: {virtualenv}"
        )
    for forbidden in _forbidden_venv_roots():
        if _is_within(virtualenv, forbidden):
            raise VirtualenvSafetyError(
                f"refusing to create/use a venv inside a managed interpreter "
                f"or toolchain root ({forbidden}): {virtualenv}"
            )
    if virtualenv.exists() and not (virtualenv / "pyvenv.cfg").is_file():
        raise VirtualenvSafetyError(
            f"refusing to reuse {virtualenv}: it exists but has no "
            f"pyvenv.cfg, so it does not look like a venv"
        )


def _run(command: list[str], *, env: dict[str, str] | None = None) -> int:
    print("+", " ".join(str(item) for item in command), flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=False,
        shell=False,
    )
    return completed.returncode


def _offline_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ)
    environment["GIGAI_G30_UAT"] = "0"
    if extra:
        environment.update(extra)
    return environment


def _positive_int(value: str, *, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise SystemExit(f"{label} must be a positive integer: {value!r}") from exc
    if parsed < 1:
        raise SystemExit(f"{label} must be a positive integer: {value!r}")
    return parsed


def _xdist_options(
    requested: str,
    maximum: str,
    distribution: str,
) -> list[str]:
    maximum_count = _positive_int(maximum, label="--xdist-max-workers")
    cpu_count = max(os.cpu_count() or 1, 1)
    if distribution not in SAFE_XDIST_DISTS:
        allowed = ", ".join(sorted(SAFE_XDIST_DISTS))
        raise SystemExit(
            f"--xdist-dist must preserve one execution per selected case; "
            f"choose one of {allowed}"
        )
    normalized = requested.strip().lower()
    if normalized == "auto":
        requested_count = maximum_count
    else:
        requested_count = _positive_int(requested, label="--xdist-workers")
    actual_count = min(requested_count, maximum_count, cpu_count)
    plan = {
        "kind": "xdist-plan",
        "lane": "source",
        "requested_workers": requested,
        "max_workers": maximum_count,
        "cpu_count": cpu_count,
        "actual_workers": actual_count,
        "distribution": distribution,
        "offline_gigai_g30_uat": "0",
        "bounded": True,
    }
    print(json.dumps(plan, sort_keys=True), flush=True)
    if actual_count == 1:
        return []
    return ["-n", str(actual_count), f"--dist={distribution}"]


def _project_cli() -> Path:
    candidate = Path(sys.executable).parent / "gigai"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    discovered = shutil.which("gigai")
    if discovered is None:
        raise SystemExit("the locked project environment has no gigai console script")
    return Path(discovered)


def _test_roots() -> tuple[Path, ...]:
    try:
        config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        configured = config["tool"]["pytest"]["ini_options"]["testpaths"]
    except (KeyError, OSError, tomllib.TOMLDecodeError):
        configured = ("tests",)
    if isinstance(configured, str):
        configured = (configured,)
    roots = tuple(_rooted(path) for path in configured)
    missing = tuple(path for path in roots if not path.is_dir())
    if missing:
        rendered = ", ".join(os.fspath(path) for path in missing)
        raise SystemExit(f"configured pytest test root(s) are missing: {rendered}")
    return roots


class _TestFunctionCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self._classes: list[str] = []
        self.functions: list[tuple[str, str, int, int]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._classes.append(node.name)
        self.generic_visit(node)
        self._classes.pop()

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.name.startswith("test_"):
            self.functions.append(
                (
                    "::".join((*self._classes, node.name)),
                    ast.get_source_segment(self._source, node) or "",
                    node.lineno,
                    node.end_lineno or node.lineno,
                )
            )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def collect(self, source: str) -> list[tuple[str, str, int, int]]:
        self._source = source
        self.visit(ast.parse(source))
        return self.functions


def _installed_nodeids() -> list[str]:
    nodeids: list[str] = []
    for root in _test_roots():
        for path in sorted(root.rglob("test*.py")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            try:
                source = path.read_text(encoding="utf-8")
                functions = _TestFunctionCollector().collect(source)
            except (OSError, UnicodeError, SyntaxError):
                continue
            file_is_installed = path.name.endswith("_installed_scenarios.py")
            for qualified_name, body, _start, _end in functions:
                body_lower = body.lower()
                if file_is_installed or any(
                    token in body_lower
                    for token in (
                        "installedgigai",
                        "installed_gigai",
                        "gigai_test_executable",
                    )
                ):
                    relative = path.relative_to(ROOT).as_posix()
                    nodeids.append(f"{relative}::{qualified_name}")
    return sorted(dict.fromkeys(nodeids))


def _print_installed_selection(nodeids: list[str]) -> None:
    print(
        json.dumps(
            {
                "kind": "installed-test-selection",
                "authority": "AST candidate discovery; pytest collection remains execution authority",
                "count": len(nodeids),
                "nodeids": nodeids,
            },
            sort_keys=True,
        )
    )


def _run_source(
    *,
    xdist_workers: str,
    xdist_max_workers: str,
    xdist_dist: str,
) -> int:
    options = _xdist_options(xdist_workers, xdist_max_workers, xdist_dist)
    return _run(
        [sys.executable, "-m", "pytest", *options],
        env=_offline_environment(),
    )


def _run_behavior() -> int:
    cli = _project_cli()
    with tempfile.TemporaryDirectory(prefix="gigai-g28-behavior-") as raw_dir:
        output_dir = Path(raw_dir)
        for split in BEHAVIOR_SPLITS:
            command = [
                os.fspath(cli),
                "internal",
                "eval",
                "behavior",
                "--manifest",
                BEHAVIOR_MANIFEST,
                "--observations",
                BEHAVIOR_OBSERVATIONS,
                "--split",
                split,
                "--output",
                os.fspath(output_dir / f"behavior-{split}.json"),
            ]
            returncode = _run(command, env=_offline_environment())
            if returncode:
                return returncode
    return 0


def _resolve_wheel_python(value: str | Path) -> Path:
    """Make the wheel-lane Python path absolute without following symlinks.

    ``.wheel-venv/bin/python`` is a symlink ``uv venv`` creates, pointing at
    a real interpreter. This must stay unresolved so callers that derive the
    venv directory from it (``_run_wheel``) land on the venv itself, never
    on whatever real interpreter install the symlink happens to point at.
    """

    return _unresolved_absolute(value)


def _run_installed(wheel_python: Path, *, list_only: bool = False) -> int:
    wheel_python = _resolve_wheel_python(wheel_python)
    if list_only:
        nodeids = _installed_nodeids()
        if not nodeids:
            print("no installed test node ids were discovered", file=sys.stderr)
            return 2
        _print_installed_selection(nodeids)
        return 0
    if not wheel_python.is_file():
        print(f"installed lane Python is missing: {wheel_python}", file=sys.stderr)
        return 2

    verifiers = sorted((ROOT / "tools").glob("verify_installed_*.py"))
    if not verifiers:
        print("no installed verifiers were discovered", file=sys.stderr)
        return 2
    environment = _offline_environment(
        {"GIGAI_TEST_EXECUTABLE": os.fspath(wheel_python.parent / "gigai")}
    )
    for verifier in verifiers:
        returncode = _run(
            [os.fspath(wheel_python), os.fspath(verifier)],
            env=environment,
        )
        if returncode:
            return returncode

    nodeids = _installed_nodeids()
    if not nodeids:
        print("no installed test node ids were discovered", file=sys.stderr)
        return 2
    _print_installed_selection(nodeids)
    return _run([sys.executable, "-m", "pytest", *nodeids], env=environment)


def _run_wheel(wheel_python: Path) -> int:
    wheel_python = _resolve_wheel_python(wheel_python)
    virtualenv = wheel_python.parent.parent
    _ensure_safe_virtualenv_dir(virtualenv)
    with tempfile.TemporaryDirectory(prefix="gigai-wheel-build-") as raw_dir:
        build_dir = Path(raw_dir)
        returncode = _run(
            ["uv", "build", "--wheel", "--out-dir", os.fspath(build_dir)]
        )
        if returncode:
            return returncode
        wheels = sorted(build_dir.glob("gigai-*.whl"))
        if len(wheels) != 1:
            print(
                f"expected exactly one GigAI wheel, found {len(wheels)}: "
                + ", ".join(path.name for path in wheels),
                file=sys.stderr,
            )
            return 2
        requirements = build_dir / "requirements.txt"
        returncode = _run(
            [
                "uv",
                "export",
                "--locked",
                "--no-dev",
                "--no-editable",
                "--no-emit-project",
                "--output-file",
                os.fspath(requirements),
            ]
        )
        if returncode:
            return returncode
        returncode = _run(
            [
                "uv",
                "venv",
                "--allow-existing",
                "--python",
                "3.11",
                os.fspath(virtualenv),
            ]
        )
        if returncode:
            return returncode
        returncode = _run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                os.fspath(wheel_python),
                "--requirements",
                os.fspath(requirements),
            ]
        )
        if returncode:
            return returncode
        returncode = _run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                os.fspath(wheel_python),
                "--no-deps",
                "--reinstall",
                os.fspath(wheels[0]),
            ]
        )
        if returncode:
            return returncode
        return _run_installed(wheel_python)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase", choices=("source", "behavior", "wheel", "installed")
    )
    parser.add_argument(
        "--wheel-python",
        default=os.fspath(DEFAULT_WHEEL_PYTHON),
        help="Python executable for the installed wheel lane",
    )
    parser.add_argument(
        "--list-installed",
        action="store_true",
        help="print direct installed-test node ids without executing tests",
    )
    parser.add_argument(
        "--xdist-workers",
        default=DEFAULT_XDIST_WORKERS,
        help="source-lane worker request: auto (capped) or a positive integer",
    )
    parser.add_argument(
        "--xdist-max-workers",
        default=str(DEFAULT_XDIST_MAX_WORKERS),
        help="hard source-lane worker cap, never above available CPUs",
    )
    parser.add_argument(
        "--xdist-dist",
        default=DEFAULT_XDIST_DIST,
        choices=tuple(sorted(SAFE_XDIST_DISTS)),
        help="source-lane xdist scheduler; each safe choice runs a case once",
    )
    args = parser.parse_args()
    if args.phase == "source":
        return _run_source(
            xdist_workers=args.xdist_workers,
            xdist_max_workers=args.xdist_max_workers,
            xdist_dist=args.xdist_dist,
        )
    if (
        args.xdist_workers != DEFAULT_XDIST_WORKERS
        or args.xdist_max_workers != str(DEFAULT_XDIST_MAX_WORKERS)
        or args.xdist_dist != DEFAULT_XDIST_DIST
    ):
        raise SystemExit("xdist worker options are valid only for the source phase")
    if args.phase == "behavior":
        return _run_behavior()
    if args.phase == "wheel":
        return _run_wheel(Path(args.wheel_python))
    return _run_installed(Path(args.wheel_python), list_only=args.list_installed)


if __name__ == "__main__":
    raise SystemExit(main())
