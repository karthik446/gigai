"""Installation and mount diagnostics for GigAI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib.metadata import version
import json
import os
from pathlib import Path
import shlex
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
from .canonical import EntityPrefix, InvalidIdentifierError, validate_entity_id
from .config import ConfigurationError, GigAIConfig, load_config
from .credentials import CredentialReferenceError, reference_is_available
from .index import JournalIndexError, read_index
from .model_targets import ModelTargetResolutionError


DIAGNOSTIC_SCHEMA_VERSION = "1.0"
#: RJ2: how far :func:`journal_repair_refusal` follows an error's causes when it looks for the journal's refusal.
_CAUSE_CHAIN_LINKS = 16


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


def _checked_config(home_root: Path, checks: list[DiagnosticCheck]) -> GigAIConfig | None:
    """The home's configuration, with its ``config.valid`` check added; ``None`` when that check failed."""

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
        return None

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
        return None

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
    return config


def run_doctor(home_root: Path) -> DoctorReport:
    checks: list[DiagnosticCheck] = []
    config = _checked_config(home_root, checks)
    if config is None:
        return _report(checks)
    checks.extend(_path_checks(config))
    checks.extend(_credential_checks(config))
    checks.append(_editor_check(config))
    checks.extend(run_mount_probes(config.workpad_root))
    checks.extend(_journal_index_checks(config))
    checks.extend(_assessment_model_checks(config))
    return _report(checks)


def run_journal_repair(home_root: Path) -> DoctorReport:
    """0110-10-17: finish the interrupted journal write of every managed workpad, and report what was finished.

    A journal write records a transaction before it replaces a file. A process that dies after that leaves a
    workpad whose every later write is refused until ``journal.reconcile_journal`` has run, and nothing else
    runs it. This asks it of each managed workpad in turn (the ones ``journal.index`` checks) and decides
    nothing itself: a workpad with no interrupted write is left as it was, one that cannot be reconciled is
    reported with the journal's own refusal and the others are still visited.
    """

    # ``journal`` imports this module (the mount probes), so it is imported here.
    from .journal import JournalConflictError, JournalError, reconcile_journal

    checks: list[DiagnosticCheck] = []
    config = _checked_config(home_root, checks)
    if config is None:
        return _report(checks, scope="journal_repair")
    started = time.monotonic_ns()
    workpads = tuple(sorted(config.workpad_root.resolve(strict=False).glob("projects/*/gigs/*")))
    finished: list[str] = []
    failed: list[str] = []
    for workpad in workpads:
        try:
            if workpad.is_symlink() or not workpad.is_dir():
                raise JournalIndexError("managed workpad is unavailable or redirected")
            # RJ2: the path says whose workpad this is (projects/<project id>/gigs/<gig id>). The ids used to be
            # read from the workpad's own Git markers, which reconcile_journal then compared with themselves.
            project, gig = workpad.parent.parent.name, workpad.name
            for key, named, prefix in (("gigai.project-id", project, EntityPrefix.PROJECT), ("gigai.gig-id", gig, EntityPrefix.GIG)):
                marker = _git_config(workpad, key)
                if marker is None:
                    raise JournalIndexError("managed workpad lacks Git ownership markers")
                if marker != named:
                    raise JournalConflictError(
                        f"ownership markers differ from the workpad's path: {key} is {_id_or_not(marker, prefix)}, the directory is {named}"
                    )
            result = reconcile_journal(workpad=workpad, project_id=project, gig_id=gig)
        except (JournalError, JournalIndexError, OSError) as exc:
            failed.append(f"failed_gig={workpad.name} code={getattr(exc, 'code', type(exc).__name__)} reason={exc}")
            continue
        if result.reconciled:
            finished.append(f"finished_gig={workpad.name} sequence={result.sequence} commit={result.commit}")
    counts = (f"managed_workpads={len(workpads)}", f"finished_writes={len(finished)}", f"failed_journals={len(failed)}")
    if failed:
        status = "FAIL"
        summary = f"{len(failed)} of {len(workpads)} managed journals could not be reconciled"
        if finished:
            summary += f"; finished {len(finished)} interrupted journal write{'s' if len(finished) != 1 else ''}"
        remediation: str | None = (
            "Stop a running Scout server and run the command again; if the reason stays, leave the workpad as "
            "it is and report the reason."
        )
    elif finished:
        status = "PASS"
        summary = (
            f"finished {len(finished)} interrupted journal write{'s' if len(finished) != 1 else ''} "
            f"in {len(workpads)} managed journals: writes are accepted again"
        )
        remediation = None
    else:
        status = "PASS"
        summary = f"no interrupted journal write in {len(workpads)} managed journals: nothing to repair"
        remediation = None
    checks.append(
        _check("journal.repair", "managed private journals", status, summary, (*counts, *finished, *failed), remediation, started)
    )
    return _report(checks, scope="journal_repair")


def journal_repair_refusal(exc: BaseException, home_root: Path | None) -> tuple[str, str] | None:
    """0110-10-17: ``(message, command)`` of a write refused behind an interrupted journal write; else ``None``.

    The journal's refusal names ``gigai doctor --repair-journal``. Here the command gets ``--home`` when the
    home is not the default one, so it can be run as written.

    RJ2: the refusal may be the cause of what reached the boundary (a caller that catches a
    ``JournalConflictError`` raises its own error from it), so it is looked for along the chain a traceback
    would print: at most ``_CAUSE_CHAIN_LINKS`` links, and no link twice. Only a journal error whose raise
    site set ``next_action`` counts, wherever it is in the chain.
    """

    # ``journal`` and ``setup`` import this module, so they are imported here.
    from .journal import JournalError
    from .setup import default_home_root

    refusal: BaseException | None = exc
    command: str | None = None
    seen: set[int] = set()
    while refusal is not None and id(refusal) not in seen and len(seen) < _CAUSE_CHAIN_LINKS:
        seen.add(id(refusal))
        command = getattr(refusal, "next_action", None) if isinstance(refusal, JournalError) else None
        if command:
            break
        refusal = refusal.__cause__ or (None if refusal.__suppress_context__ else refusal.__context__)
    if not command:
        return None
    for_home = command
    if home_root is not None and home_root.expanduser().resolve(strict=False) != default_home_root().expanduser().resolve(strict=False):
        for_home = f"{command} --home {shlex.quote(os.fspath(home_root))}"
    return str(refusal).replace(command, for_home), for_home


def run_live_doctor(home_root: Path, model_target: str) -> DoctorReport:
    """Run one explicit, local-only provider probe for a configured target.

    This path is deliberately separate from ``run_doctor`` so CI and scenario
    processes cannot invoke a provider by accident.
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
                "Repair the authoritative journal; do not trust or edit state.sqlite as a substitute. "
                "If a save was interrupted (a crash, a power loss), run 'gigai doctor --repair-journal' to finish it.",
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


def _id_or_not(marker: str, prefix: EntityPrefix) -> str:
    """A Git ownership marker as a refusal may print it: the id it holds, and nothing else it might hold."""

    try:
        return validate_entity_id(marker, expected_prefix=prefix)
    except InvalidIdentifierError:
        return f"not a {prefix.value} id"


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


def _assessment_model_checks(config: GigAIConfig) -> list[DiagnosticCheck]:
    """0.1.11 MODELPIN: which model assessments use, one check per enabled CLI target (none when there is none).

    A ``default`` model on ``claude_cli`` asks for the evaluated model (one fallback call on the CLI's default when it
    refuses); a model set in the target's configuration is asked as set. ``evaluated_models`` decides the rest.
    """

    from .scout.evaluated_models import ASKED_BY_DEFAULT, RESULTS_PAGE, asked_model, evaluated_for, model_notice

    endpoints = {endpoint.name: endpoint.adapter for endpoint in config.endpoints}
    found: list[DiagnosticCheck] = []
    for target in config.model_targets:
        adapter = endpoints.get(target.endpoint)
        if not target.enabled or adapter not in ("claude_cli", "codex_cli"):
            continue
        started = time.monotonic_ns()
        asks = asked_model(adapter, target.model)
        measured = evaluated_for(adapter)
        notice = model_notice(adapter, asks)
        summary = f"assessments on {target.name} use {asks if asks != 'default' else 'the CLI default model'}"
        if notice is not None:
            summary += f"; GigAI's accuracy results are for {' or '.join(measured)}, so these carry a notice ({RESULTS_PAGE})"
        elif asks == target.model and target.model != "default":
            summary += " (set in the target's configuration)"
        if adapter in ASKED_BY_DEFAULT and target.model == "default":
            summary += "; one fallback call on the CLI default if the CLI refuses it"
        found.append(
            _check(
                f"assessment.model.{target.name}",
                "assessment model",
                "PASS",
                summary,
                (f"target={target.name}", f"asks={asks}", f"evaluated={','.join(measured)}", f"notice={'yes' if notice else 'no'}"),
                None,
                started,
            )
        )
    return found


def _editor_check(config: GigAIConfig) -> DiagnosticCheck:
    started = time.monotonic_ns()
    if not config.editor_argv:
        return _check(
            "editor.resolved",
            "configured editor",
            "PASS",
            "editor: not set",
            ("argv_structured=true", "editor_set=false"),
            None,
            started,
        )
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
    "journal_repair_refusal",
    "render_report_json",
    "run_doctor",
    "run_journal_repair",
    "run_live_doctor",
    "run_mount_probes",
]
