"""Black-box process harness shared by GigAI implementation goals.

The harness never imports a Click command object. It invokes an installed
console script, gives it explicit isolated roots, captures exact tree and Git
state, and fails closed when effects exceed a scenario's declaration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
from typing import Mapping, Sequence


_SECRET_NAME = re.compile(r"(?:credential|password|secret|token|api_?key)", re.I)
_SUBSTITUTE_NAME = re.compile(r"[a-z][a-z0-9_-]*\Z")
_MANIFEST_CAPTURE_ATTEMPTS = 3
_MANIFEST_CAPTURE_RETRY_SECONDS = 0.01


@dataclass(frozen=True)
class FileState:
    path: str
    kind: str
    mode: str
    size: int | None = None
    sha256: str | None = None
    target: str | None = None


@dataclass(frozen=True)
class GitState:
    present: bool
    head: str | None = None
    branch: str | None = None
    status: tuple[str, ...] = ()
    working_diff_sha256: str | None = None
    staged_diff_sha256: str | None = None


@dataclass(frozen=True)
class TreeManifest:
    files: tuple[FileState, ...]
    git: GitState

    @classmethod
    def capture(cls, root: Path) -> TreeManifest:
        root = root.resolve(strict=True)
        failure: FileNotFoundError | None = None
        for attempt in range(_MANIFEST_CAPTURE_ATTEMPTS):
            try:
                return cls._capture_once(root)
            except FileNotFoundError as exc:
                failure = exc
                if attempt + 1 < _MANIFEST_CAPTURE_ATTEMPTS:
                    time.sleep(_MANIFEST_CAPTURE_RETRY_SECONDS)
        assert failure is not None
        raise failure

    @classmethod
    def _capture_once(cls, root: Path) -> TreeManifest:
        files: list[FileState] = []
        paths: list[Path] = []
        for directory, child_directories, child_files in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            # Git internals are implementation state, not scenario-visible
            # workpad content. A workpad contains nested private repositories,
            # so prune every .git directory rather than only one at the root.
            child_directories[:] = [
                name for name in child_directories if name != ".git"
            ]
            paths.extend(directory_path / name for name in child_directories)
            paths.extend(directory_path / name for name in child_files)

        for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
            relative = path.relative_to(root)
            info = path.lstat()
            mode = f"{stat.S_IMODE(info.st_mode):04o}"
            if path.is_symlink():
                files.append(
                    FileState(
                        path=relative.as_posix(),
                        kind="symlink",
                        mode=mode,
                        target=os.readlink(path),
                    )
                )
            elif path.is_dir():
                files.append(FileState(path=relative.as_posix(), kind="directory", mode=mode))
            elif path.is_file():
                payload = path.read_bytes()
                files.append(
                    FileState(
                        path=relative.as_posix(),
                        kind="file",
                        mode=mode,
                        size=len(payload),
                        sha256=hashlib.sha256(payload).hexdigest(),
                    )
                )
            else:
                files.append(FileState(path=relative.as_posix(), kind="other", mode=mode))
        return cls(files=tuple(files), git=_capture_git_state(root))

    def changed_paths(self, other: TreeManifest) -> frozenset[str]:
        before = {item.path: item for item in self.files}
        after = {item.path: item for item in other.files}
        changed = {
            path
            for path in before.keys() | after.keys()
            if before.get(path) != after.get(path)
        }
        if self.git != other.git:
            changed.add("@git")
        return frozenset(changed)


@dataclass(frozen=True)
class ScenarioRoots:
    scenario: Path
    home: Path
    target: Path
    workpad: Path
    fixtures: Path
    artifacts: Path
    guard: Path

    @classmethod
    def create(cls, scenario: Path) -> ScenarioRoots:
        scenario.mkdir(parents=True, exist_ok=False)
        roots = cls(
            scenario=scenario,
            home=scenario / "home",
            target=scenario / "target",
            workpad=scenario / "workpad",
            fixtures=scenario / "fixtures",
            artifacts=scenario / "artifacts",
            guard=scenario / "guard",
        )
        for root in (
            roots.home,
            roots.target,
            roots.workpad,
            roots.fixtures,
            roots.artifacts,
            roots.guard,
        ):
            root.mkdir()
        (roots.home / "tmp").mkdir()
        (roots.artifacts / "tmp").mkdir()
        shutil.copy2(Path(__file__).with_name("sitecustomize.py"), roots.guard)
        return roots


@dataclass(frozen=True)
class CommandTarget:
    executable: Path
    argv_prefix: tuple[str, ...] = ()
    allowed_read_roots: tuple[Path, ...] = ()


@dataclass(frozen=True)
class InstalledGigAI:
    command: CommandTarget
    distribution_version: str

    @classmethod
    def current(cls) -> InstalledGigAI:
        from importlib.metadata import version

        configured = os.environ.get("GIGAI_TEST_EXECUTABLE")
        executable = Path(configured) if configured else Path(sys.executable).parent / "gigai"
        if not executable.is_file():
            discovered = shutil.which("gigai")
            if discovered is None:
                raise RuntimeError("the installed gigai console script was not found")
            executable = Path(discovered)

        package_spec = importlib.util.find_spec("gigai")
        if package_spec is None or package_spec.origin is None:
            raise RuntimeError("the installed gigai distribution was not found")
        package_root = Path(package_spec.origin).resolve().parent
        executable_prefix = executable.resolve().parent.parent
        target_python = executable.resolve().parent / "python"
        target_base_prefix = (
            target_python.resolve().parent.parent
            if target_python.exists()
            else executable_prefix
        )
        return cls(
            command=CommandTarget(
                executable=executable.resolve(),
                allowed_read_roots=tuple(
                    dict.fromkeys(
                        (
                            executable_prefix,
                            target_base_prefix,
                            Path(sys.prefix).resolve(),
                            Path(sys.base_prefix).resolve(),
                            package_root,
                        )
                    )
                ),
            ),
            distribution_version=version("gigai"),
        )


@dataclass(frozen=True)
class ScenarioSpec:
    name: str
    argv: tuple[str, ...]
    expected_exit_codes: frozenset[int] = frozenset({0})
    expected_target_changes: frozenset[str] = frozenset()
    expected_workpad_changes: frozenset[str] = frozenset()
    expected_home_changes: frozenset[str] = frozenset()
    allowed_subprocesses: tuple[Path, ...] = ()
    extra_env: tuple[tuple[str, str], ...] = ()
    stdin: str | None = None
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not _SUBSTITUTE_NAME.fullmatch(self.name):
            raise ValueError("scenario name must use lowercase letters, digits, '_' or '-'")
        _require_structured_argv(self.argv)


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_ns: int
    timed_out: bool
    guard_events: tuple[dict[str, object], ...]
    target_before: TreeManifest
    target_after: TreeManifest
    workpad_before: TreeManifest
    workpad_after: TreeManifest
    home_before: TreeManifest
    home_after: TreeManifest
    fixtures_before: TreeManifest
    fixtures_after: TreeManifest
    violations: tuple[str, ...]
    artifact: Path


class ScenarioViolation(AssertionError):
    def __init__(self, result: ScenarioResult) -> None:
        self.result = result
        super().__init__(
            f"scenario {result.name!r} failed closed: {', '.join(result.violations)}; "
            f"artifact=<scenario-artifacts>/{result.artifact.name}"
        )


class ScenarioHarness:
    def __init__(
        self,
        command: CommandTarget,
        roots: ScenarioRoots,
        *,
        real_home: Path | None = None,
    ) -> None:
        self.command = command
        self.roots = roots
        self.real_home = (real_home or Path.home()).resolve()

    def run(self, spec: ScenarioSpec) -> ScenarioResult:
        before = self._manifests()
        guard_log = self.roots.artifacts / f"{spec.name}-guard.jsonl"
        environment = self._environment(spec, guard_log)
        argv = (
            os.fspath(self.command.executable),
            *self.command.argv_prefix,
            *spec.argv,
        )
        started = time.monotonic_ns()
        timed_out = False
        try:
            completed = subprocess.run(
                argv,
                cwd=self.roots.target,
                env=environment,
                capture_output=True,
                text=True,
                input=spec.stdin,
                timeout=spec.timeout_seconds,
                check=False,
                shell=False,
            )
            exit_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as error:
            timed_out = True
            exit_code = 124
            stdout = _decode_timeout_stream(error.stdout)
            stderr = _decode_timeout_stream(error.stderr)
        duration_ns = time.monotonic_ns() - started
        after = self._manifests()
        guard_events = _read_guard_events(guard_log)

        changes = {
            "target": before["target"].changed_paths(after["target"]),
            "workpad": before["workpad"].changed_paths(after["workpad"]),
            "home": before["home"].changed_paths(after["home"]),
            "fixtures": before["fixtures"].changed_paths(after["fixtures"]),
        }
        expected = {
            "target": spec.expected_target_changes,
            "workpad": spec.expected_workpad_changes,
            "home": spec.expected_home_changes,
            "fixtures": frozenset(),
        }
        violations: list[str] = []
        if timed_out:
            violations.append("process_timeout")
        if exit_code not in spec.expected_exit_codes:
            violations.append(f"unexpected_exit:{exit_code}")
        for root_name in ("target", "workpad", "home", "fixtures"):
            if changes[root_name] != expected[root_name]:
                violations.append(
                    f"unexpected_{root_name}_changes:"
                    f"expected={sorted(expected[root_name])}:actual={sorted(changes[root_name])}"
                )
        violations.extend(
            str(event.get("kind", "guard_violation")) for event in guard_events
        )

        artifact = self.roots.artifacts / f"{spec.name}.json"
        result = ScenarioResult(
            name=spec.name,
            argv=argv,
            environment=tuple(sorted(environment.items())),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ns=duration_ns,
            timed_out=timed_out,
            guard_events=guard_events,
            target_before=before["target"],
            target_after=after["target"],
            workpad_before=before["workpad"],
            workpad_after=after["workpad"],
            home_before=before["home"],
            home_after=after["home"],
            fixtures_before=before["fixtures"],
            fixtures_after=after["fixtures"],
            violations=tuple(violations),
            artifact=artifact,
        )
        self._write_artifact(result, spec)
        if violations:
            raise ScenarioViolation(result)
        return result

    def _manifests(self) -> dict[str, TreeManifest]:
        return {
            "target": TreeManifest.capture(self.roots.target),
            "workpad": TreeManifest.capture(self.roots.workpad),
            "home": TreeManifest.capture(self.roots.home),
            "fixtures": TreeManifest.capture(self.roots.fixtures),
        }

    def _environment(self, spec: ScenarioSpec, guard_log: Path) -> dict[str, str]:
        allowed_reads = (
            *self.command.allowed_read_roots,
            self.roots.target,
            self.roots.workpad,
            self.roots.home,
            self.roots.fixtures,
            self.roots.artifacts,
            self.roots.guard,
        )
        allowed_writes = (
            self.roots.target,
            self.roots.workpad,
            self.roots.home,
            self.roots.artifacts,
        )
        environment = {
            "HOME": os.fspath(self.roots.home),
            "PATH": os.pathsep.join(
                (os.fspath(self.command.executable.parent), "/usr/bin", "/bin")
            ),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": os.fspath(self.roots.guard),
            "TMPDIR": os.fspath(self.roots.artifacts / "tmp"),
            "GIGAI_HOME": os.fspath(self.roots.home),
            "GIGAI_TARGET_ROOT": os.fspath(self.roots.target),
            "GIGAI_WORKPAD_ROOT": os.fspath(self.roots.workpad),
            "GIGAI_FIXTURE_ROOT": os.fspath(self.roots.fixtures),
            "GIGAI_HARNESS_ALLOWED_READ_ROOTS": json.dumps(
                [os.fspath(path.resolve()) for path in allowed_reads]
            ),
            "GIGAI_HARNESS_ALLOWED_WRITE_ROOTS": json.dumps(
                [os.fspath(path.resolve()) for path in allowed_writes]
            ),
            "GIGAI_HARNESS_FORBIDDEN_READ_ROOTS": json.dumps(
                [os.fspath(self.real_home)]
            ),
            "GIGAI_HARNESS_ALLOWED_EXECUTABLES": json.dumps(
                [os.fspath(path.resolve()) for path in spec.allowed_subprocesses]
            ),
            "GIGAI_HARNESS_GUARD_LOG": os.fspath(guard_log),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "NO_PROXY": "*",
            "no_proxy": "*",
        }
        for key, value in spec.extra_env:
            if not isinstance(key, str) or not isinstance(value, str) or "\0" in key + value:
                raise TypeError("scenario environment must contain NUL-free strings")
            if key in environment or key.startswith("GIGAI_HARNESS_"):
                raise ValueError(f"scenario cannot override harness-owned environment key {key!r}")
            environment[key] = value
        return environment

    def _write_artifact(self, result: ScenarioResult, spec: ScenarioSpec) -> None:
        payload = asdict(result)
        payload["artifact"] = self._normalize(os.fspath(result.artifact), spec)
        payload = self._sanitize(payload, spec)
        result.artifact.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _sanitize(self, value: object, spec: ScenarioSpec) -> object:
        secret_values = {
            item_value
            for key, item_value in spec.extra_env
            if _SECRET_NAME.search(key) and item_value
        }
        if isinstance(value, str):
            sanitized = self._normalize(value, spec)
            for secret in secret_values:
                sanitized = sanitized.replace(secret, "<redacted>")
            return sanitized
        if isinstance(value, dict):
            return {
                str(key): (
                    "<redacted>"
                    if _SECRET_NAME.search(str(key))
                    else self._sanitize(item, spec)
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self._sanitize(item, spec) for item in value]
        return value

    def _normalize(self, value: str, spec: ScenarioSpec) -> str:
        replacements: dict[str, str] = {
            os.fspath(self.command.executable): "$GIGAI_EXECUTABLE",
            os.fspath(self.command.executable.parent): "$GIGAI_BIN",
            os.fspath(self.real_home): "$REAL_HOME",
            os.fspath(self.roots.artifacts): "$ARTIFACTS",
            os.fspath(self.roots.fixtures): "$FIXTURES",
            os.fspath(self.roots.workpad): "$WORKPAD",
            os.fspath(self.roots.target): "$TARGET",
            os.fspath(self.roots.home): "$HOME",
            os.fspath(self.roots.guard): "$GUARD",
            os.fspath(self.roots.scenario): "$SCENARIO",
        }
        root_tokens = (
            (self.roots.artifacts, "$ARTIFACTS"),
            (self.roots.fixtures, "$FIXTURES"),
            (self.roots.workpad, "$WORKPAD"),
            (self.roots.target, "$TARGET"),
            (self.roots.home, "$HOME"),
            (self.roots.guard, "$GUARD"),
            (self.roots.scenario, "$SCENARIO"),
        )
        for root, token in root_tokens:
            replacements[os.fspath(root.resolve())] = token
        for index, root in enumerate(self.command.allowed_read_roots, start=1):
            replacements[os.fspath(root)] = f"$RUNTIME_{index}"
        for source in sorted(replacements, key=len, reverse=True):
            value = value.replace(source, replacements[source])
        for key, secret in spec.extra_env:
            if _SECRET_NAME.search(key) and secret:
                value = value.replace(secret, "<redacted>")
        return value


def copy_fixture_repository(
    kind: str,
    destination: Path,
    *,
    fixture_root: Path | None = None,
) -> None:
    if kind not in {"python", "non-python"}:
        raise ValueError("fixture kind must be 'python' or 'non-python'")
    source = Path(__file__).parents[1] / "fixtures" / "targets" / kind
    if fixture_root is not None:
        staged_source = fixture_root / kind
        shutil.copytree(source, staged_source)
        source = staged_source
    shutil.copytree(source, destination, dirs_exist_ok=True)
    git_env = {
        "HOME": os.fspath(destination.parent / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
    }
    _run_git(destination, git_env, "init", "--initial-branch=main", "--quiet")
    _run_git(destination, git_env, "add", "--all")
    _run_git(
        destination,
        git_env,
        "-c",
        "user.name=GigAI Scenario",
        "-c",
        "user.email=scenario@gigai.invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture baseline",
    )


def create_recording_substitute(root: Path, name: str) -> Path:
    if not _SUBSTITUTE_NAME.fullmatch(name):
        raise ValueError("substitute name must use lowercase letters, digits, '_' or '-'")
    executable = root / name
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

record = {
    "argv": sys.argv[1:],
    "boundary": os.environ["GIGAI_RECORDING_BOUNDARY"],
}
Path(os.environ["GIGAI_RECORDING_LOG"]).write_text(
    json.dumps(record, sort_keys=True, separators=(",", ":")) + "\\n",
    encoding="utf-8",
)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def invoke_recording_substitute(
    executable: Path,
    argv: Sequence[str],
    *,
    boundary: str,
    log: Path,
) -> subprocess.CompletedProcess[str]:
    structured = _require_structured_argv(argv)
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIGAI_RECORDING_BOUNDARY": boundary,
        "GIGAI_RECORDING_LOG": os.fspath(log),
    }
    return subprocess.run(
        [os.fspath(executable), *structured],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )


def _require_structured_argv(argv: Sequence[str]) -> tuple[str, ...]:
    if isinstance(argv, (str, bytes)):
        raise TypeError("argv must be a sequence of strings, never a shell string")
    structured = tuple(argv)
    if any(not isinstance(item, str) or "\0" in item for item in structured):
        raise TypeError("argv must contain only NUL-free strings")
    return structured


def _capture_git_state(root: Path) -> GitState:
    env = {
        "HOME": os.fspath(root.parent / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    inside = _run_git(root, env, "rev-parse", "--is-inside-work-tree", check=False)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return GitState(present=False)

    head_process = _run_git(root, env, "rev-parse", "--verify", "HEAD", check=False)
    branch_process = _run_git(
        root, env, "symbolic-ref", "--short", "--quiet", "HEAD", check=False
    )
    status_process = _run_git(
        root,
        env,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "-z",
    )
    working_diff = _run_git(root, env, "diff", "--binary", "--no-ext-diff", text=False)
    staged_diff = _run_git(
        root, env, "diff", "--cached", "--binary", "--no-ext-diff", text=False
    )
    return GitState(
        present=True,
        head=head_process.stdout.strip() if head_process.returncode == 0 else None,
        branch=branch_process.stdout.strip() if branch_process.returncode == 0 else None,
        status=tuple(item for item in status_process.stdout.split("\0") if item),
        working_diff_sha256=hashlib.sha256(working_diff.stdout).hexdigest(),
        staged_diff_sha256=hashlib.sha256(staged_diff.stdout).hexdigest(),
    )


def _run_git(
    root: Path,
    env: Mapping[str, str],
    *args: str,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        env=dict(env),
        capture_output=True,
        text=text,
        check=check,
        shell=False,
    )


def _read_guard_events(path: Path) -> tuple[dict[str, object], ...]:
    if not path.exists():
        return ()
    return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())


def _decode_timeout_stream(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
