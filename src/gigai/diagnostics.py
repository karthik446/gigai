"""Offline installation and mount diagnostics for GigAI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - v1 rejects non-POSIX before mutation
    fcntl = None  # type: ignore[assignment]

from .adapters import AdapterFactoryError, ModelInvocationError, resolve_model_adapter
from .config import ConfigurationError, GigAIConfig, load_config
from .credentials import CredentialReferenceError, reference_is_available
from .index import JournalIndexError, read_index
from .model_targets import ModelTargetResolutionError
from .standard_pack import PACK_NAME, PACK_VERSION, pack_digest, verify_standard_pack


DIAGNOSTIC_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class DiagnosticCheck:
    id: str
    subject: str
    status: str
    summary: str
    evidence_safe_to_share: tuple[str, ...]
    remediation: str | None
    duration_ms: int


@dataclass(frozen=True)
class DoctorReport:
    schema_version: str
    command: str
    gigai_version: str
    scope: str
    overall_status: str
    checks: tuple[DiagnosticCheck, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def run_doctor(home_root: Path) -> DoctorReport:
    checks: list[DiagnosticCheck] = []
    started = time.monotonic_ns()
    try:
        config = load_config(home_root)
    except ConfigurationError as exc:
        checks.append(
            _check(
                "config.valid",
                "machine configuration",
                "FAIL",
                str(exc),
                (),
                "Repair the configuration or move it aside and run 'gigai setup'.",
                started,
            )
        )
        return _report(checks)

    if config.home_root.resolve(strict=False) != home_root.resolve(strict=False):
        checks.append(
            _check(
                "config.valid",
                "machine configuration",
                "FAIL",
                f"configuration at {home_root} declares a different home root: "
                f"{config.home_root}",
                ("requested_home_matches_config=false",),
                "Run doctor against the configured home or repair the configuration explicitly.",
                started,
            )
        )
        return _report(checks)

    checks.append(
        _check(
            "config.valid",
            "machine configuration",
            "PASS",
            "configuration is typed and uses the supported schema",
            (f"schema_version={config.schema_version}",),
            None,
            started,
        )
    )
    checks.extend(_path_checks(config))
    checks.extend(_credential_checks(config))
    checks.append(_editor_check(config))
    checks.append(_offline_adapter_check(config))
    checks.extend(run_mount_probes(config.workpad_root))
    checks.extend(_journal_index_checks(config))
    return _report(checks)


def run_live_doctor(home_root: Path, model_target: str) -> DoctorReport:
    """Run one explicit, local-only provider probe for a configured target.

    This path is deliberately separate from ``run_doctor`` so offline checks,
    CI, and scenario processes cannot invoke a provider by accident.
    """

    report = run_doctor(home_root)
    checks = list(report.checks)
    started = time.monotonic_ns()
    if any(check.status == "FAIL" for check in checks):
        checks.append(
            _check(
                "adapter.live",
                "configured live model target",
                "FAIL",
                "live model check was not attempted because offline diagnostics failed",
                ("live_call_attempted=false",),
                "Repair failed offline diagnostics before requesting a live check.",
                started,
            )
        )
        return _report(checks, scope="live")
    try:
        config = load_config(home_root)
        binding = resolve_model_adapter(config, model_target)
        target = binding.current.target
        endpoint = binding.current.endpoint
        if endpoint.adapter == "deterministic":
            raise AdapterFactoryError(
                f"model target {model_target!r} is deterministic; --live requires a remote endpoint"
            )
        result = binding.port.invoke(
            binding.request(
                role="live-diagnostic",
                prompt="Return a short confirmation that this GigAI live diagnostic reached the configured model.",
            )
        )
        if result.status != "success" or not result.output_text:
            raise ModelInvocationError(
                "live model diagnostic returned no successful text output"
            )
        checks.append(
            _check(
                "adapter.live",
                "configured live model target",
                "PASS",
                "configured model target returned a successful diagnostic response",
                (
                    f"target={target.name}",
                    f"endpoint_adapter={endpoint.adapter}",
                    f"configured_model={target.model}",
                    f"resolved_model={result.resolved_model}",
                    f"max_output_tokens={target.max_output_tokens}",
                    f"reasoning_effort={target.reasoning_effort or 'provider-default'}",
                    "credential_reference_resolved_at_runtime=true",
                    f"cost_status={result.cost_status}",
                ),
                None,
                started,
            )
        )
    except (
        AdapterFactoryError,
        ConfigurationError,
        CredentialReferenceError,
        ModelInvocationError,
        ModelTargetResolutionError,
        ValueError,
    ) as exc:
        checks.append(
            _check(
                "adapter.live",
                "configured live model target",
                "FAIL",
                str(exc),
                ("live_call_succeeded=false",),
                "Confirm the target, capability policy, output limit, and credential reference before retrying.",
                started,
            )
        )
    return _report(checks, scope="live")


def run_mount_probes(workpad_root: Path) -> tuple[DiagnosticCheck, DiagnosticCheck]:
    return (
        _atomic_replacement_check(workpad_root),
        _interprocess_lock_check(workpad_root),
    )


def _probe_directory(root: Path) -> tuple[Path, bool]:
    """Keep probe files inside the workpad's allowed disposable surface."""

    scratch = root / "scratch"
    if scratch.is_symlink() or (scratch.exists() and not scratch.is_dir()):
        raise OSError("configured workpad scratch surface is unavailable")
    existed = scratch.exists()
    scratch.mkdir(mode=0o700, exist_ok=True)
    return scratch, not existed


def _cleanup_probe_directory(directory: Path | None, created: bool) -> None:
    if directory is not None and created:
        try:
            directory.rmdir()
        except OSError:
            pass


def _journal_index_checks(config: GigAIConfig) -> tuple[DiagnosticCheck, ...]:
    """Check managed journals by reconstructing only their disposable index."""

    started = time.monotonic_ns()
    workpads = tuple(sorted(config.workpad_root.glob("projects/*/gigs/*")))
    if not workpads:
        return (
            _check(
                "journal.index",
                "managed private journals",
                "PASS",
                "no managed workpads require journal indexing",
                ("managed_workpads=0",),
                None,
                started,
            ),
        )
    checked = 0
    try:
        for workpad in workpads:
            if workpad.is_symlink() or not workpad.is_dir():
                raise JournalIndexError("managed workpad is unavailable or redirected")
            project = _git_config(workpad, "gigai.project-id")
            gig = _git_config(workpad, "gigai.gig-id")
            if project is None or gig is None:
                raise JournalIndexError("managed workpad lacks Git ownership markers")
            read_index(workpad=workpad, project_id=project, gig_id=gig)
            checked += 1
    except (JournalIndexError, OSError) as exc:
        return (
            _check(
                "journal.index",
                "managed private journals",
                "FAIL",
                str(exc),
                (f"indexed_workpads={checked}",),
                "Repair the authoritative journal; do not trust or edit state.sqlite as a substitute.",
                started,
            ),
        )
    return (
        _check(
            "journal.index",
            "managed private journals",
            "PASS",
            "all managed journals have a matching rebuildable index",
            (f"managed_workpads={checked}",),
            None,
            started,
        ),
    )


def _git_config(workpad: Path, key: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", os.fspath(workpad), "config", "--local", "--get", key],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\n")


def render_report_json(report: DoctorReport) -> str:
    return json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"


def _path_checks(config: GigAIConfig) -> tuple[DiagnosticCheck, ...]:
    checks: list[DiagnosticCheck] = []
    for identifier, subject, path in (
        ("path.home", "GigAI home", config.home_root),
        ("path.workpad", "configured workpad authority", config.workpad_root),
    ):
        started = time.monotonic_ns()
        if not path.is_dir():
            checks.append(
                _check(
                    identifier,
                    subject,
                    "FAIL",
                    f"required directory is unavailable: {path}",
                    ("configured_path_available=false",),
                    "Restore or mount the configured directory; GigAI will not choose a fallback.",
                    started,
                )
            )
        elif not os.access(path, os.R_OK | os.W_OK | os.X_OK):
            checks.append(
                _check(
                    identifier,
                    subject,
                    "FAIL",
                    f"required directory is not readable and writable: {path}",
                    ("configured_path_writable=false",),
                    "Correct directory ownership or permissions.",
                    started,
                )
            )
        else:
            checks.append(
                _check(
                    identifier,
                    subject,
                    "PASS",
                    f"configured directory is available: {path}",
                    ("configured_path_available=true", "configured_path_writable=true"),
                    None,
                    started,
                )
            )
    return tuple(checks)


def _credential_checks(config: GigAIConfig) -> tuple[DiagnosticCheck, ...]:
    checks: list[DiagnosticCheck] = []
    for reference in config.credentials:
        started = time.monotonic_ns()
        try:
            available = reference_is_available(reference)
        except CredentialReferenceError as exc:
            checks.append(
                _check(
                    f"credential.{reference.name}",
                    f"credential reference {reference.name}",
                    "FAIL",
                    str(exc),
                    (f"kind={reference.kind}",),
                    "Record a valid environment or external secret-manager reference.",
                    started,
                )
            )
            continue
        if available is False:
            checks.append(
                _check(
                    f"credential.{reference.name}",
                    f"credential reference {reference.name}",
                    "WARN",
                    f"environment reference {reference.reference!r} is not currently present",
                    (f"kind={reference.kind}", "reference_present=false"),
                    "Provide the referenced environment variable only when a later live operation needs it.",
                    started,
                )
            )
        else:
            summary = (
                "referenced environment variable is present"
                if available is True
                else "external secret-manager reference is syntactically valid"
            )
            checks.append(
                _check(
                    f"credential.{reference.name}",
                    f"credential reference {reference.name}",
                    "PASS",
                    summary,
                    (f"kind={reference.kind}", "reference_valid=true"),
                    None,
                    started,
                )
            )
    return tuple(checks)


def _editor_check(config: GigAIConfig) -> DiagnosticCheck:
    started = time.monotonic_ns()
    executable = config.editor_argv[0]
    resolved = shutil.which(executable)
    if resolved is None:
        return _check(
            "editor.resolved",
            "configured editor",
            "FAIL",
            f"configured editor executable {executable!r} cannot be resolved",
            ("argv_structured=true", "executable_resolved=false"),
            "Rerun setup with an executable editor command.",
            started,
        )
    return _check(
        "editor.resolved",
        "configured editor",
        "PASS",
        "configured editor executable resolves without shell parsing",
        ("argv_structured=true", "executable_resolved=true"),
        None,
        started,
    )


def _offline_adapter_check(config: GigAIConfig) -> DiagnosticCheck:
    started = time.monotonic_ns()
    try:
        pack_valid, summary = verify_standard_pack(config.home_root)
        offline_target = next(
            (
                target.name
                for target in config.model_targets
                if any(
                    endpoint.name == target.endpoint
                    and endpoint.adapter == "deterministic"
                    for endpoint in config.endpoints
                )
            ),
            None,
        )
        configured = offline_target is not None
        configured = configured and (
            config.standard_pack.name == PACK_NAME
            and config.standard_pack.version == PACK_VERSION
            and config.standard_pack.content_digest == pack_digest()
        )
        if offline_target is None:
            raise AdapterFactoryError("no deterministic model target is configured")
        binding = resolve_model_adapter(config, offline_target)
        response_valid = (
            binding.port.invoke(
                binding.request(role="offline-diagnostic", prompt="doctor-probe")
            ).output_text
            == "gigai-offline-ok"
        )
    except Exception as exc:  # the check converts package corruption to a diagnostic
        pack_valid = False
        configured = False
        response_valid = False
        summary = f"deterministic adapter failed its fixture probe: {exc}"
    if not (pack_valid and configured and response_valid):
        return _check(
            "adapter.offline",
            "deterministic offline adapter",
            "FAIL",
            summary
            if not pack_valid or not response_valid
            else "offline endpoint is not configured",
            ("network_used=false", "credential_used=false"),
            "Rerun setup to restore the immutable standard pack and offline endpoint.",
            started,
        )
    return _check(
        "adapter.offline",
        "deterministic offline adapter",
        "PASS",
        "deterministic adapter returned the installed fixture response",
        ("network_used=false", "credential_used=false"),
        None,
        started,
    )


def _atomic_replacement_check(root: Path) -> DiagnosticCheck:
    started = time.monotonic_ns()
    if not root.is_dir():
        return _mount_unavailable(
            "mount.atomic_replace", "atomic replacement", root, started
        )
    probe: Path | None = None
    replacement: Path | None = None
    probe_directory: Path | None = None
    probe_directory_created = False
    try:
        probe_directory, probe_directory_created = _probe_directory(root)
        probe_descriptor, probe_name = tempfile.mkstemp(
            prefix=".gigai-atomic-probe-", dir=probe_directory
        )
        probe = Path(probe_name)
        with os.fdopen(probe_descriptor, "wb") as stream:
            stream.write(b"before\n")
            stream.flush()
            os.fsync(stream.fileno())
        replacement_descriptor, name = tempfile.mkstemp(
            prefix=".gigai-replace-", dir=probe_directory
        )
        replacement = Path(name)
        with os.fdopen(replacement_descriptor, "wb") as stream:
            stream.write(b"after\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(replacement, probe)
        if probe.read_bytes() != b"after\n":
            raise OSError("replacement readback did not match")
        directory_descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        return _check(
            "mount.atomic_replace",
            "configured workpad atomic replacement",
            "PASS",
            "write, fsync, replace, and readback succeeded on the configured mount",
            ("probe_on_configured_mount=true",),
            None,
            started,
        )
    except OSError as exc:
        return _check(
            "mount.atomic_replace",
            "configured workpad atomic replacement",
            "FAIL",
            f"atomic replacement probe failed on {root}: {exc}",
            ("probe_on_configured_mount=true",),
            "Restore a writable local filesystem that supports atomic replacement.",
            started,
        )
    finally:
        if probe is not None:
            probe.unlink(missing_ok=True)
        if replacement is not None:
            replacement.unlink(missing_ok=True)
        _cleanup_probe_directory(probe_directory, probe_directory_created)


def _interprocess_lock_check(root: Path) -> DiagnosticCheck:
    started = time.monotonic_ns()
    if fcntl is None:
        return _check(
            "mount.interprocess_lock",
            "configured workpad interprocess exclusion",
            "FAIL",
            "POSIX advisory locks are unavailable on this platform",
            ("platform_supported=false",),
            "Use GigAI v1 on macOS or Linux.",
            started,
        )
    if not root.is_dir():
        return _mount_unavailable(
            "mount.interprocess_lock", "interprocess exclusion", root, started
        )
    lock_path: Path | None = None
    probe_directory: Path | None = None
    probe_directory_created = False
    try:
        probe_directory, probe_directory_created = _probe_directory(root)
        descriptor, name = tempfile.mkstemp(
            prefix=".gigai-lock-probe-", dir=probe_directory
        )
        os.close(descriptor)
        lock_path = Path(name)
        with lock_path.open("a+b") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "gigai.diagnostics",
                    "--contend-lock",
                    os.fspath(lock_path),
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                shell=False,
            )
            fcntl.flock(stream, fcntl.LOCK_UN)
        if completed.returncode != 0 or completed.stdout != "blocked\n":
            raise OSError(
                f"contending process did not observe exclusion "
                f"(exit={completed.returncode}, output={completed.stdout!r})"
            )
        return _check(
            "mount.interprocess_lock",
            "configured workpad interprocess exclusion",
            "PASS",
            "a second process was excluded by an advisory lock on the configured mount",
            ("probe_on_configured_mount=true", "contender=structured-python-argv"),
            None,
            started,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _check(
            "mount.interprocess_lock",
            "configured workpad interprocess exclusion",
            "FAIL",
            f"interprocess exclusion probe failed on {root}: {exc}",
            ("probe_on_configured_mount=true", "contender=structured-python-argv"),
            "Use a filesystem that supports local advisory locks.",
            started,
        )
    finally:
        if lock_path is not None:
            lock_path.unlink(missing_ok=True)
        _cleanup_probe_directory(probe_directory, probe_directory_created)


def _contend_lock(path: Path) -> int:
    if fcntl is None:
        return 2
    with path.open("a+b") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("blocked")
            return 0
        fcntl.flock(stream, fcntl.LOCK_UN)
    print("acquired")
    return 1


def _mount_unavailable(
    identifier: str, subject: str, root: Path, started: int
) -> DiagnosticCheck:
    return _check(
        identifier,
        subject,
        "FAIL",
        f"configured workpad root is unavailable: {root}",
        ("configured_mount_available=false",),
        "Restore the configured mount; GigAI will not probe a fallback directory.",
        started,
    )


def _check(
    identifier: str,
    subject: str,
    status: str,
    summary: str,
    evidence: tuple[str, ...],
    remediation: str | None,
    started: int,
) -> DiagnosticCheck:
    return DiagnosticCheck(
        id=identifier,
        subject=subject,
        status=status,
        summary=summary,
        evidence_safe_to_share=evidence,
        remediation=remediation,
        duration_ms=max(0, (time.monotonic_ns() - started) // 1_000_000),
    )


def _report(
    checks: list[DiagnosticCheck], *, scope: str = "installation"
) -> DoctorReport:
    statuses = {check.status for check in checks}
    overall = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    return DoctorReport(
        schema_version=DIAGNOSTIC_SCHEMA_VERSION,
        command="doctor",
        gigai_version=version("gigai"),
        scope=scope,
        overall_status=overall,
        checks=tuple(checks),
    )


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--contend-lock":
        raise SystemExit(_contend_lock(Path(sys.argv[2])))
    raise SystemExit("diagnostics is not a public module CLI")


__all__ = [
    "DIAGNOSTIC_SCHEMA_VERSION",
    "DiagnosticCheck",
    "DoctorReport",
    "render_report_json",
    "run_doctor",
    "run_live_doctor",
    "run_mount_probes",
]
