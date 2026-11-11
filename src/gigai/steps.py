"""Per-step model binding: which model target each of a gig's steps calls (``ctx.model``, ``gigai <gig> steps``).

A gig calls a model by step id, ``ctx.model("judge", text)``. ``resolve_target`` is the one place a step id becomes a
``config.toml`` ``[[model_targets]]`` entry; the first match wins:

1. the operator's override in ``<home>/gigs/<gig>/steps.toml`` (``gigai <gig> steps set <step> <target>``);
2. the manifest's ``[steps].<id>.target``;
3. the ``default`` profile's target for the role ``[steps].<id>.role``, or for the step id itself when it is a profile
   role (reviewer and verifier fall back to critic and adjudicator, researcher and gig_creator to planner).

Any other step id is a ``KeyError`` naming the manifest field. A call's request settings are the manifest's
``[steps].<id>.settings`` under the call's keyword arguments. A claude/codex CLI target carries only ``system``,
``output_mode`` and ``schema``, so any other setting (temperature, tools, logprobs, ...) is a ``StepTargetError``,
both at ``steps set`` time and at call time, before any model call.

A ``deterministic`` target answers from the standard pack plus the gig package's ``responses.json`` entry named after
the target, ``{"<target id>": {"<prompt>": "<text>"}}``: one offline gig can bind two steps to two targets that answer
differently.

``steps.toml`` is one ``[steps]`` table of ``step = "<target>"``. It is written lock-free by an atomic replace (two
concurrent ``steps set`` leave one of the two files), with mode 0600 and never through a symlink.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import stat
import tomllib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import click

from .gigs.home import ensure_gig_folder, gig_folder

if TYPE_CHECKING:
    from .adapters.port import InvocationResult
    from .config import GigAIConfig
    from .evals.records import ItemRef, Postprocess, PromptRef, RunRecord
    from .model_targets import ResolvedModelTarget
    from .sdk.context import GigContext

STEPS_FILE = "steps.toml"
ROLES = ("planner", "critic", "adjudicator", "reviewer", "verifier", "researcher", "gig_creator")
_ROLE_FALLBACK = {"reviewer": "critic", "verifier": "adjudicator", "researcher": "planner", "gig_creator": "planner"}
_CLI_ADAPTERS = frozenset({"claude_cli", "codex_cli"})
#: What a CLI adapter carries (``TEXT_ONLY_REQUEST_FIELDS`` plus gigai's own ``metadata``).
_CLI_SETTINGS = frozenset({"system", "output_mode", "schema", "metadata"})
_WALL_TIME_MS = 600_000


class StepTargetError(ValueError):
    """A step's target cannot be used: not in config.toml, or unable to carry one of the step's settings."""


class StepsFileError(ValueError):
    """``steps.toml`` cannot be used: not TOML, an unknown key, a symlink, or writable by group or others."""


@dataclass(frozen=True)
class Target:
    """The model target step ``step`` resolved to, where from (``override``, ``manifest``, ``role:<role>``)."""

    step: str
    id: str
    adapter: str
    model: str
    source: str
    settings: dict[str, Any]
    resolved: ResolvedModelTarget = field(repr=False)

    def to_json(self) -> dict[str, Any]:
        return {"target": self.id, "adapter": self.adapter, "model": self.model, "source": self.source, "settings": self.settings}


def steps_path(home: Path, gig: str) -> Path:
    """``<home>/gigs/<gig>/steps.toml``; checks the gig name and creates nothing."""

    return gig_folder(home, gig) / STEPS_FILE


def _declared(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    steps = manifest.get("steps")
    return steps if isinstance(steps, dict) else {}


def _refuse_symlinks(path: Path) -> None:
    for part in (path.parent.parent, path.parent, path):
        with contextlib.suppress(FileNotFoundError):
            if stat.S_ISLNK(part.lstat().st_mode):
                raise StepsFileError(f"refusing a symlink in the gig folder: {part}")


def read_overrides(home: Path, gig: str) -> dict[str, str]:
    """The step overrides in ``steps.toml``; ``{}`` when there is none."""

    path = steps_path(home, gig)
    _refuse_symlinks(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {}
    with os.fdopen(descriptor, "rb") as handle:
        if os.fstat(handle.fileno()).st_mode & 0o022:
            raise StepsFileError(f"{path} is writable by group or others; run chmod 600 on it")
        try:
            payload = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise StepsFileError(f"{path} is not TOML: {exc}") from exc
    table = payload.get("steps", {})
    if set(payload) - {"steps"} or not isinstance(table, dict) or any(type(v) is not str or not v for v in table.values()):
        raise StepsFileError(f'{path} must hold only a [steps] table of step = "<model target>" lines')
    return table


def _write_override(home: Path, gig: str, step_id: str, target_id: str | None) -> bool:
    """Set (or, for ``None``, remove) one override by an atomic replace; ``False`` when there was nothing to remove."""

    _refuse_symlinks(steps_path(home, gig))
    overrides = read_overrides(home, gig)
    if target_id is None and overrides.pop(step_id, None) is None:
        return False
    if target_id is not None:
        overrides[step_id] = target_id
    folder = ensure_gig_folder(home, gig)
    _refuse_symlinks(folder / STEPS_FILE)
    lines = "".join(f"{json.dumps(step)} = {json.dumps(target)}\n" for step, target in sorted(overrides.items()))
    temporary = folder / f".{STEPS_FILE}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"# gigai {gig} steps set: per-step model target overrides\n[steps]\n{lines}")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, folder / STEPS_FILE)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return True


def _target(config: GigAIConfig, step_id: str, target_id: str, source: str, settings: dict[str, Any]) -> Target:
    from .model_targets import ModelTargetResolutionError, resolve_model_target

    try:
        resolved = resolve_model_target(config, target_id)
    except ModelTargetResolutionError as exc:
        raise StepTargetError(f"step {step_id!r} ({source}): {exc} in config.toml [[model_targets]]") from exc
    return Target(step_id, target_id, resolved.endpoint.adapter, resolved.target.model, source, settings, resolved)


def resolve_target(home: Path, gig: str, manifest: Mapping[str, Any], step_id: str, config: GigAIConfig) -> Target:
    """The model target step ``step_id`` of ``gig`` calls (the order is in the module docstring)."""

    declared = _declared(manifest)
    if step_id not in declared and step_id not in ROLES:
        raise KeyError(f"step {step_id!r} is not declared in the gig manifest's [steps] table ([steps].{step_id})")
    step = declared.get(step_id, {})
    settings = dict(step.get("settings") or {})
    override = read_overrides(home, gig).get(step_id)
    if override is not None:
        return _target(config, step_id, override, "override", settings)
    if step.get("target"):
        return _target(config, step_id, step["target"], "manifest", settings)
    role = step.get("role") or (step_id if step_id in ROLES else None)
    if role is None:
        raise KeyError(f"step {step_id!r} names no model: set [steps].{step_id}.target or [steps].{step_id}.role")
    if role not in ROLES:
        raise StepTargetError(f"step {step_id!r}: [steps].{step_id}.role {role!r} is not one of {', '.join(ROLES)}")
    profile = next((item for item in config.profiles if item.name == "default"), None)
    if profile is None:
        raise StepTargetError(f"step {step_id!r} uses role {role!r} but config.toml has no 'default' profile")
    target_id = getattr(profile, role) or getattr(profile, _ROLE_FALLBACK[role])
    return _target(config, step_id, target_id, f"role:{role}", settings)


def check_settings(target: Target, settings: Mapping[str, Any]) -> None:
    """Raise ``StepTargetError`` for a setting that is not a request field or that ``target`` cannot carry."""

    from .adapters.port import REQUEST_SETTINGS

    unknown = sorted(set(settings) - set(REQUEST_SETTINGS))
    if unknown:
        raise StepTargetError(f"step {target.step!r}: {unknown[0]!r} is not a request setting ({', '.join(REQUEST_SETTINGS)})")
    refused = sorted(name for name, value in settings.items() if value is not None and name not in _CLI_SETTINGS)
    if target.adapter in _CLI_ADAPTERS and refused:
        raise StepTargetError(
            f"step {target.step!r} sets {refused[0]!r}, which the {target.adapter} target {target.id!r} cannot carry; "
            "bind the step to an API target or drop the setting"
        )


def set_override(home: Path, gig: str, manifest: Mapping[str, Any], step_id: str, target_id: str | None) -> str:
    """``gigai <gig> steps set``: check the step and the target, then write ``steps.toml``; returns what changed."""

    if target_id is None:
        removed = _write_override(home, gig, step_id, None)
        return f"{step_id}: override removed" if removed else f"{step_id}: no override to remove"
    if step_id not in _declared(manifest):
        raise KeyError(f"step {step_id!r} is not declared in the gig manifest's [steps] table ([steps].{step_id})")
    from .config import load_config

    target = _target(load_config(home), step_id, target_id, "override", dict(_declared(manifest)[step_id].get("settings") or {}))
    check_settings(target, target.settings)
    _write_override(home, gig, step_id, target_id)
    return f"{step_id} -> {target_id} (written to {steps_path(home, gig)})"


def stale_overrides(home: Path, gig: str, manifest: Mapping[str, Any]) -> list[str]:
    """``gigai gig check`` warnings: an unreadable ``steps.toml``, an override of a step the manifest lacks."""

    try:
        overrides = read_overrides(home, gig)
    except (ValueError, OSError) as exc:
        return [str(exc)]
    return [
        f"{STEPS_FILE} overrides step {step!r}, which the manifest's [steps] lacks; run `gigai {gig} steps set {step} -`"
        for step in sorted(set(overrides) - set(_declared(manifest)))
    ]


# --- ctx.model ----------------------------------------------------------------------------------------------------


def _package_file(manifest: Mapping[str, Any], name: str) -> bytes | None:
    """File ``name`` of the package of the gig's ``[commands]`` group module; ``None`` when it is missing."""

    group = (manifest.get("commands") or {}).get("group")
    try:
        spec = find_spec(str(group).partition(":")[0]) if group else None
    except (ImportError, ValueError):
        spec = None
    if spec is None or not (spec.submodule_search_locations or spec.origin):
        return None
    folder = Path(next(iter(spec.submodule_search_locations))) if spec.submodule_search_locations else Path(spec.origin).parent
    try:
        return (folder / name).read_bytes()
    except FileNotFoundError:
        return None


def _prompt(manifest: Mapping[str, Any], step_id: str) -> PromptRef | None:
    """The step's prompt: the ``[[evals.steps]]`` entry's ``prompt`` table, else the ``[steps].<id>.prompt`` file."""

    from .evals.records import PromptRef

    evals = manifest.get("evals")
    entries = evals.get("steps") if isinstance(evals, dict) else None
    entry = next((item for item in entries or () if isinstance(item, dict) and item.get("id") == step_id), {})
    prompt = entry.get("prompt")
    if isinstance(prompt, dict):
        prompt_id, version, file = prompt.get("id"), prompt.get("version"), prompt.get("file")
    else:
        prompt_id, version, file = step_id, "1", _declared(manifest).get(step_id, {}).get("prompt")
    text = _package_file(manifest, file) if isinstance(file, str) else None
    return None if text is None else PromptRef.of(str(prompt_id), str(version), text.decode("utf-8"))


def _adhoc_run(ctx: GigContext, input: str) -> RunRecord:
    from .canonical import digest_imported_bytes
    from .evals.records import Dataset, RunRecord, new_id

    manifest = json.dumps(ctx.manifest, sort_keys=True, default=str).encode("utf-8")
    dataset = Dataset("ctx.model", "1", digest_imported_bytes(input.encode("utf-8")), {"source": "ctx.model"})
    return RunRecord(new_id("run"), ctx.name, digest_imported_bytes(manifest), dataset)


def call(
    ctx: GigContext,
    step_id: str,
    input: str,
    *,
    run: RunRecord | None,
    item: ItemRef | None,
    settings: Mapping[str, Any],
    repeat_index: int = 0,
    postprocess: Postprocess | None = None,
) -> InvocationResult:
    """``ctx.model``: resolve, refuse what the target cannot carry, call, and trace when ``evals.sqlite`` exists."""

    from .adapters.factory import resolve_model_adapter
    from .adapters.port import RetryPolicy
    from .config import load_config
    from .evals import records
    from .model_call import invoke_bounded

    config = load_config(ctx.home)
    target = resolve_target(ctx.home, ctx.name, ctx.manifest, step_id, config)
    merged = {**target.settings, **settings}
    check_settings(target, merged)
    if isinstance(merged.get("retry_policy"), Mapping):
        merged["retry_policy"] = RetryPolicy(**merged["retry_policy"])
    binding = resolve_model_adapter(config, target.id, home_root=ctx.home)
    if target.adapter == "deterministic":
        canned = json.loads(_package_file(ctx.manifest, "responses.json") or b"{}").get(target.id)
        if isinstance(canned, dict):
            binding.port._responses = {**binding.port._responses, **canned}  # type: ignore[attr-defined]
    role = target.source.removeprefix("role:") if target.source.startswith("role:") else "reviewer"
    request = dataclasses.replace(binding.request(role=role, prompt=input), **merged)
    try:
        result = invoke_bounded(
            binding.port, request, max_wall_time_ms=_WALL_TIME_MS, max_output_tokens=target.resolved.target.max_output_tokens
        )
    finally:
        binding.close()
    if records.evals_db_path(ctx.name, ctx.home).is_file():
        parsed, status = postprocess(result.output_text) if postprocess is not None else (None, "not_parsed")
        ctx.trace(
            step_id,
            input,
            result.output_text,
            run=run or _adhoc_run(ctx, input),
            item=item or records.ItemRef("adhoc", "1"),
            prompt=_prompt(ctx.manifest, step_id),
            model=records.model_call(binding, request, result),
            repeat_index=repeat_index,
            output_parsed=parsed,
            parse_status=status,
        )
    return result


# --- verbs --------------------------------------------------------------------------------------------------------


def _fail(gig: str, exc: Exception) -> NoReturn:
    click.echo(f"gigai {gig}: {exc.args[0] if isinstance(exc, KeyError) else exc}", err=True)
    raise click.exceptions.Exit(1)


def _overrides(ctx: GigContext) -> dict[str, str]:
    try:
        return read_overrides(ctx.home, ctx.name)
    except (ValueError, OSError) as exc:
        _fail(ctx.name, exc)


def listing(ctx: GigContext) -> dict[str, dict[str, Any]]:
    """``gigai <gig> steps --json``: each declared or overridden step's target, or why it does not resolve."""

    from .config import ConfigurationError, load_config

    steps = sorted(set(_declared(ctx.manifest)) | set(_overrides(ctx)))
    try:
        config = load_config(ctx.home)
    except ConfigurationError as exc:
        return {step: {"error": str(exc)} for step in steps}
    rows = {}
    for step in steps:
        try:
            rows[step] = resolve_target(ctx.home, ctx.name, ctx.manifest, step, config).to_json()
        except (KeyError, ValueError) as exc:
            rows[step] = {"error": exc.args[0] if isinstance(exc, KeyError) else str(exc)}
    return rows


def step_verbs(verbs: Mapping[str, click.Command]) -> dict[str, click.Command]:
    """Core's ``steps`` group and a ``status`` showing the step overrides; ``ctx.obj`` is the gig's ``GigContext``.

    A process-mode gig's ``status`` keeps its text; its ``--json`` gains ``step_overrides``.
    """

    @click.group("steps", invoke_without_command=True)
    @click.option("--json", "as_json", is_flag=True, help="Print JSON keyed by step id.")
    @click.pass_context
    def steps(click_ctx: click.Context, as_json: bool) -> None:
        """Show which model target each step of this gig calls, and why (override, manifest or profile role)."""

        if click_ctx.invoked_subcommand is not None:
            return
        rows = listing(click_ctx.obj)
        if as_json:
            click.echo(json.dumps(rows, indent=2, sort_keys=True))
            return
        if not rows:
            click.echo("no steps: the manifest declares no [steps]")
        for step, row in rows.items():
            detail = row.get("error") or f"{row['target']} ({row['adapter']} {row['model']}, {row['source']})"
            click.echo(f"{step}: {detail}")

    @steps.command("set")
    @click.argument("step")
    @click.argument("target")
    @click.pass_obj
    def set_step(ctx: GigContext, step: str, target: str) -> None:
        """Bind STEP to model TARGET (a config.toml [[model_targets]] name) in steps.toml; TARGET '-' removes it."""

        from .config import ConfigurationError

        try:
            click.echo(set_override(ctx.home, ctx.name, ctx.manifest, step, None if target == "-" else target))
        except (KeyError, ValueError, OSError, ConfigurationError) as exc:
            _fail(ctx.name, exc)

    own = verbs.get("status")
    if own is not None:

        def wrapped(as_json: bool = False, **params: Any) -> None:
            if not as_json:
                return own.callback(as_json=False, **params)
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                own.callback(as_json=True, **params)
            payload = {**json.loads(buffer.getvalue()), "step_overrides": _overrides(click.get_current_context().obj)}
            click.echo(json.dumps(payload, indent=2, sort_keys=True))

        return {"steps": steps, "status": click.Command("status", callback=wrapped, params=own.params, help=own.help)}

    @click.command("status")
    @click.option("--json", "as_json", is_flag=True, help="Print JSON.")
    @click.pass_obj
    def status(ctx: GigContext, as_json: bool) -> None:
        """Show this gig's gig folder and its step overrides (set with `steps set`)."""

        overrides, folder = _overrides(ctx), str(gig_folder(ctx.home, ctx.name))
        if as_json:
            from .sdk.budget import status_fields

            payload = {"gig": ctx.name, "folder": folder, "step_overrides": overrides, **status_fields(ctx.home, ctx.name, ctx.manifest)}
            click.echo(json.dumps(payload, indent=2, sort_keys=True))
            return
        click.echo(f"gig folder: {folder}")
        click.echo("".join(f"step override: {step} -> {target}\n" for step, target in overrides.items()) or "step overrides: none\n", nl=False)

    return {"steps": steps, "status": status}
