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

from .adapters import DeterministicAdapter
from .config import ConfigurationError, GigAIConfig, load_config
from .credentials import CredentialReferenceError, reference_is_available
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
    return _report(checks)


def run_mount_probes(workpad_root: Path) -> tuple[DiagnosticCheck, DiagnosticCheck]:
    return (_atomic_replacement_check(workpad_root), _interprocess_lock_check(workpad_root))


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
        configured = any(
            endpoint.name == "offline" and endpoint.adapter == "deterministic"
            for endpoint in config.endpoints
        )
        configured = configured and (
            config.standard_pack.name == PACK_NAME
            and config.standard_pack.version == PACK_VERSION
            and config.standard_pack.content_digest == pack_digest()
        )
        response_valid = DeterministicAdapter().invoke("doctor-probe") == "gigai-offline-ok"
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
            summary if not pack_valid or not response_valid else "offline endpoint is not configured",
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
        return _mount_unavailable("mount.atomic_replace", "atomic replacement", root, started)
    probe: Path | None = None
    replacement: Path | None = None
    try:
        probe_descriptor, probe_name = tempfile.mkstemp(prefix=".gigai-atomic-probe-", dir=root)
        probe = Path(probe_name)
        with os.fdopen(probe_descriptor, "wb") as stream:
            stream.write(b"before\n")
            stream.flush()
            os.fsync(stream.fileno())
        replacement_descriptor, name = tempfile.mkstemp(prefix=".gigai-replace-", dir=root)
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
        return _mount_unavailable("mount.interprocess_lock", "interprocess exclusion", root, started)
    lock_path: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".gigai-lock-probe-", dir=root)
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


def _report(checks: list[DiagnosticCheck]) -> DoctorReport:
    statuses = {check.status for check in checks}
    overall = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    return DoctorReport(
        schema_version=DIAGNOSTIC_SCHEMA_VERSION,
        command="doctor",
        gigai_version=version("gigai"),
        scope="installation",
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
    "run_mount_probes",
]
