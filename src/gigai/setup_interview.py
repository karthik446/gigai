"""Local browser-first setup flow for GigAI configuration."""

from __future__ import annotations

from dataclasses import dataclass
import html
import http.server
import json
import secrets
import subprocess
import sys
import threading
from typing import Callable, Mapping

from .model_discovery import DetectedModel


class SetupInterviewError(ValueError):
    """A browser setup event cannot be accepted."""


@dataclass(frozen=True)
class SetupDraft:
    home_root: str
    workpad_root: str
    editor: str
    open_with_target: bool
    selected_model_target: str
    openai_api_env: str = ""
    openai_api_model: str = ""
    openrouter_api_env: str = ""
    openrouter_api_model: str = ""
    enabled_model_targets: tuple[str, ...] = ()
    reviewer_model_target: str = ""
    verifier_model_target: str = ""
    researcher_model_target: str = ""


class SetupHTTPServer:
    """Short-lived loopback setup page; configuration is applied only on submit."""

    def __init__(
        self,
        draft: SetupDraft,
        *,
        model_options: tuple[Mapping[str, str], ...],
        detected_models: tuple[DetectedModel, ...],
        on_apply: Callable[[SetupDraft], Mapping[str, object]],
        provider_status: Mapping[str, str] | None = None,
        folder_chooser: Callable[[], str | None] | None = None,
        host: str = "127.0.0.1",
        lifetime_seconds: float = 600.0,
    ) -> None:
        if host != "127.0.0.1":
            raise SetupInterviewError("setup server must bind to loopback")
        if lifetime_seconds <= 0:
            raise SetupInterviewError("setup server lifetime must be positive")
        self.draft = draft
        self.model_options = tuple(dict(item) for item in model_options)
        self.detected_models = detected_models
        self.provider_status = dict(provider_status or {})
        self.on_apply = on_apply
        self.folder_chooser = folder_chooser or choose_local_folder
        self.lifetime_seconds = lifetime_seconds
        self.token = secrets.token_urlsafe(24)
        self._lock = threading.RLock()
        self._terminal = threading.Event()
        self._timer: threading.Timer | None = None
        self._closed = False
        self.result: Mapping[str, object] | None = None
        self.error: str | None = None
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _authorized(self) -> bool:
                return self.path == f"/setup/{owner.token}"

            def _json(self, status: int, payload: Mapping[str, object]) -> None:
                body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if not self._authorized():
                    self._json(404, {"error": "not_found"})
                    return
                with owner._lock:
                    body = owner._render_html()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                if not self._authorized():
                    self._json(404, {"error": "not_found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 65536:
                        raise SetupInterviewError("setup event body size is invalid")
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict):
                        raise SetupInterviewError("setup event must be an object")
                    if payload.get("event") == "choose_folder":
                        chosen = owner.folder_chooser()
                        if chosen is None:
                            self._json(200, {"status": "cancelled"})
                        else:
                            self._json(200, {"status": "selected", "path": chosen})
                        return
                    if payload.get("event") != "apply":
                        raise SetupInterviewError("setup requires an apply event")
                    draft = SetupDraft(
                        home_root=_required_text(payload, "home_root"),
                        workpad_root=_required_text(payload, "workpad_root"),
                        editor=_required_text(payload, "editor"),
                        open_with_target=payload.get("open_with_target") is True,
                        selected_model_target=_required_text(payload, "selected_model_target"),
                        openai_api_env=_optional_text(payload, "openai_api_env"),
                        openai_api_model=_optional_text(payload, "openai_api_model"),
                        openrouter_api_env=_optional_text(payload, "openrouter_api_env"),
                        openrouter_api_model=_optional_text(payload, "openrouter_api_model"),
                        enabled_model_targets=_text_array(payload, "enabled_model_targets"),
                        reviewer_model_target=_optional_text(payload, "reviewer_model_target"),
                        verifier_model_target=_optional_text(payload, "verifier_model_target"),
                        researcher_model_target=_optional_text(payload, "researcher_model_target"),
                    )
                    with owner._lock:
                        owner.result = owner.on_apply(draft)
                        owner.draft = draft
                        owner.error = None
                        owner._terminal.set()
                    self._json(200, {"status": "applied", "result": dict(owner.result)})
                except (SetupInterviewError, json.JSONDecodeError, TypeError, ValueError, OSError) as exc:
                    with owner._lock:
                        owner.error = str(exc)
                    self._json(409, {"error": str(exc)})

        self._server = http.server.ThreadingHTTPServer((host, 0), Handler)
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self._server.server_address[0], int(self._server.server_address[1])
        return f"http://{host}:{port}/setup/{self.token}"

    def start(self) -> "SetupHTTPServer":
        if self._thread is not None or self._closed:
            raise SetupInterviewError("setup server is already started")
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._timer = threading.Timer(self.lifetime_seconds, self.close)
        self._timer.daemon = True
        self._timer.start()
        return self

    def wait(self, timeout: float | None = None) -> Mapping[str, object] | None:
        self._terminal.wait(timeout)
        with self._lock:
            return self.result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._terminal.set()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _render_html(self) -> bytes:
        configured_labels = {str(item["label"]) for item in self.model_options}
        roster_html = "".join(
            "<label class='model-option'><input type='checkbox' name='enabled_model_targets' "
            f"value='{html.escape(str(item['id']))}' "
            f"{'checked' if item['id'] in self.draft.enabled_model_targets else ''}>"
            f"<strong>{html.escape(str(item['label']))}</strong>"
            f"<span>{html.escape(str(item['description']))}</span></label>"
            for item in self.model_options
        )
        reviewer_options = _role_select("Reviewer", "reviewer_model_target", self.draft.reviewer_model_target, self.model_options)
        verifier_options = _role_select("Verifier", "verifier_model_target", self.draft.verifier_model_target, self.model_options)
        researcher_options = _role_select("Researcher", "researcher_model_target", self.draft.researcher_model_target, self.model_options)
        creation_options = _role_select("Gig creation", "selected_model_target", self.draft.selected_model_target, self.model_options)
        api_cards = (
            _api_card(
                provider="OpenAI",
                env_field="openai_api_env",
                model_field="openai_api_model",
                env_value=self.draft.openai_api_env,
                model_value=self.draft.openai_api_model,
                status=self.provider_status.get("OpenAI", "Configured" if "OpenAI API" in configured_labels else "Not configured"),
            )
            + _api_card(
                provider="OpenRouter",
                env_field="openrouter_api_env",
                model_field="openrouter_api_model",
                env_value=self.draft.openrouter_api_env,
                model_value=self.draft.openrouter_api_model,
                status=self.provider_status.get("OpenRouter", "Configured" if "OpenRouter API" in configured_labels else "Not configured"),
            )
        )
        cli_cards = "".join(
            "<article class='provider-card'><div><strong>"
            + html.escape(item.name.capitalize())
            + " CLI</strong><span class='provider-status'>"
            + ("Detected — adapter available" if item.executable else "Not detected")
            + "</span></div><p>"
            + (
                "GigAI can invoke this CLI through a bounded read-only model adapter. "
                "Authentication remains owned by the CLI."
                if item.executable
                else "Install this CLI later if you want to use it when GigAI supports its adapter."
            )
            + "</p></article>"
            for item in self.detected_models
        )
        error_html = (
            f"<p class='error' role='alert'>{html.escape(self.error)}</p>" if self.error else ""
        )
        checked = "checked" if self.draft.open_with_target else ""
        body = (
            "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Set up GigAI</title><style>"
            ":root{font:16px system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#172033;background:#f5f7fb}"
            "body{margin:0}.shell{max-width:760px;margin:0 auto;padding:32px 20px 64px}"
            "header{background:#172033;color:white;border-radius:16px;padding:24px;margin-bottom:20px}"
            "h1{margin:0 0 8px;font-size:1.7rem}h2{font-size:1rem;margin:0 0 8px}p{line-height:1.5}"
            ".intro{color:#dbe4f5;margin:0}.card{background:white;border:1px solid #dbe2ef;border-radius:12px;padding:20px;margin:14px 0;box-shadow:0 2px 8px #1720330d}"
            "label.field{display:block;font-weight:700;margin:14px 0}input[type=text]{box-sizing:border-box;width:100%;margin-top:7px;border:1px solid #aab6cc;border-radius:7px;padding:10px;font:inherit}"
            ".model-option{display:block;border:1px solid #dbe2ef;border-radius:9px;padding:12px;margin:10px 0;cursor:pointer}.model-option span{display:block;color:#526079;font-size:.9rem;margin:4px 0 0 24px}.model-option strong{margin-left:6px}"
            ".provider-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}.provider-card{border:1px solid #dbe2ef;border-radius:9px;padding:12px}.provider-card.disabled{background:#f8fafc;color:#71809a}.provider-card p{color:#526079;font-size:.88rem;margin:7px 0 0}.provider-status{float:right;border-radius:999px;background:#dcfce7;color:#166534;padding:3px 8px;font-size:.72rem;font-weight:700}.disabled .provider-status{background:#e2e8f0;color:#526079}.credential-plan{border-top:1px solid #dbe2ef;margin-top:12px;padding-top:12px}.credential-plan strong{display:block}.credential-plan span{display:block;color:#526079;font-size:.88rem;margin-top:4px}.credential-plan.disabled{opacity:.68}"
            "button{border:1px solid #2563eb;border-radius:7px;background:#2563eb;color:white;padding:10px 16px;font:inherit;font-weight:700;cursor:pointer;margin-top:14px}.folder-choice{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.folder-choice button{background:#e8eef8;color:#172033;border-color:#9aa8bf}.error{background:#fee2e2;color:#991b1b;padding:10px;border-radius:8px}.muted{color:#526079;font-size:.9rem}.detected{padding-left:20px;color:#526079}.footer{color:#61708a;font-size:.82rem;margin-top:18px}"
            "</style><main class='shell'><header><h1>Set up GigAI</h1>"
            "<p class='intro'>Choose where GigAI keeps its local state and which model should facilitate Gig creation. Nothing is sent anywhere during setup.</p></header>"
            + error_html
            + "<form id='setup' class='card'>"
            + "<div class='folder-choice'><button id='choose-folder' type='button'>Choose GigAI storage folder</button><span class='muted'>Choose one local folder; GigAI will derive its private workpads underneath it.</span></div>"
            + f"<label class='field'>GigAI home<input name='home_root' type='text' value='{html.escape(self.draft.home_root)}' placeholder='Choose a folder or enter an absolute path' required></label>"
            + f"<label class='field'>Private workpad folder<input name='workpad_root' type='text' value='{html.escape(self.draft.workpad_root)}' placeholder='Derived as <GigAI home>/workpads' required><span class='muted'>This is local filesystem storage for proposals, journals, and Gig state.</span></label>"
            + f"<label class='field'>Editor executable<input name='editor' type='text' value='{html.escape(self.draft.editor)}' required><span class='muted'>Detected automatically when possible; this is only used to open a workpad later.</span></label>"
            + "<section><h2>Model providers</h2><p class='muted'>Configure the models GigAI may use. You can enable more than one; role defaults below do not define a Gig workflow.</p><div class='provider-grid'>"
            + api_cards
            + cli_cards
            + "</div></section>"
            + "<section><h2>Enabled model roster</h2><p class='muted'>Select every real model GigAI may use. At least one usable model is required; GigAI will not silently switch to a demo fixture.</p>"
            + roster_html
            + "</section><section><h2>Machine-wide role defaults</h2><p class='muted'>These are defaults only. Individual Gigs can define and override their own workflow roles.</p>"
            + reviewer_options + verifier_options + researcher_options + creation_options
            + "</section><label><input name='open_with_target' type='checkbox' " + checked + "> Open workpads with their target later</label>"
            + "<button type='submit'>Apply setup</button></form>"
            + "<section class='card'><h2>Credential sources</h2><p class='muted'>Secret values never belong in config.toml, Gig manifests, or logs.</p><div class='credential-plan'><strong>Environment variable</strong><span>Supported now. GigAI stores only the variable name and reads the value at the provider boundary.</span></div><div class='credential-plan disabled'><strong>Protected local .env file</strong><span>Planned: atomic write, restrictive permissions, and runtime loading under the chosen GigAI home.</span></div><div class='credential-plan disabled'><strong>Anthropic API</strong><span>Coming soon; shown here for discoverability but not selectable yet.</span></div></section><p class='footer'>This page is local-only, loopback-bound, token-protected, and expires automatically.</p></main>"
            + "<script>const choose=document.querySelector('#choose-folder');choose.addEventListener('click',async()=>{const r=await fetch(location.href,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event:'choose_folder'})});const b=await r.json();if(!r.ok){alert(b.error||'Folder chooser unavailable');}else if(b.status==='selected'){const f=document.querySelector('#setup');f.home_root.value=b.path;f.workpad_root.value=b.path.replace(/[\\/]$/,'')+'/workpads';}});document.querySelector('#setup').addEventListener('submit',async(e)=>{e.preventDefault();const f=e.currentTarget;const enabled=[...f.querySelectorAll('input[name=enabled_model_targets]:checked')].map(item=>item.value);const selected=f.querySelector('select[name=selected_model_target]');const p={event:'apply',home_root:f.home_root.value,workpad_root:f.workpad_root.value,editor:f.editor.value,open_with_target:f.open_with_target.checked,selected_model_target:selected&&selected.value,reviewer_model_target:f.reviewer_model_target.value,verifier_model_target:f.verifier_model_target.value,researcher_model_target:f.researcher_model_target.value,enabled_model_targets:enabled,openai_api_env:f.openai_api_env.value,openai_api_model:f.openai_api_model.value,openrouter_api_env:f.openrouter_api_env.value,openrouter_api_model:f.openrouter_api_model.value};const r=await fetch(location.href,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});if(!r.ok){const b=await r.json();alert(b.error||'Setup could not be applied');}else{document.body.innerHTML='<main class=\"shell\"><header><h1>GigAI setup complete</h1><p class=\"intro\">Configuration saved. You can close this tab.</p></header></main>';}});</script>"
        ).encode()
        return body


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise SetupInterviewError(f"{key} must be a non-empty NUL-free string")
    return value.strip()


def _optional_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str) or "\0" in value:
        raise SetupInterviewError(f"{key} must be text without NUL bytes")
    return value.strip()


def _text_array(payload: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key, ())
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() or "\0" in item for item in value
    ):
        raise SetupInterviewError(f"{key} must be an array of non-empty NUL-free strings")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _role_select(
    label: str,
    name: str,
    selected: str,
    options: tuple[Mapping[str, str], ...],
) -> str:
    option_html = "".join(
        f"<option value='{html.escape(str(item['id']))}' "
        f"{'selected' if item['id'] == selected else ''}>"
        f"{html.escape(str(item['label']))}</option>"
        for item in options
    )
    return (
        f"<label class='field'>{html.escape(label)} default"
        f"<select name='{html.escape(name)}' required>{option_html}</select>"
        "</label>"
    )


def _api_card(
    *,
    provider: str,
    env_field: str,
    model_field: str,
    env_value: str,
    model_value: str,
    status: str,
) -> str:
    configured = status.startswith("Configured") or status.startswith("Reference")
    detail = (
        "GigAI stores only the environment-variable name; the secret value is read at invocation time."
        if configured
        else "Add the environment-variable name used by this provider. GigAI never receives the secret value."
    )
    placeholder = provider.upper().replace(" ", "_") + "_API_KEY"
    return (
        "<article class='provider-card'><div><strong>"
        + provider
        + " API</strong><span class='provider-status'>"
        + status
        + "</span></div><p>"
        + detail
        + "</p><label class='field'>Environment variable<input name='"
        + env_field
        + "' type='text' value='"
        + html.escape(env_value)
        + "' placeholder='"
        + placeholder
        + "'></label><label class='field'>Model<input name='"
        + model_field
        + "' type='text' value='"
        + html.escape(model_value)
        + "' placeholder='Provider model name'></label></article>"
    )


def choose_local_folder() -> str | None:
    """Open the native local folder chooser where the host supports one."""

    if sys.platform != "darwin":
        raise SetupInterviewError(
            "native folder chooser is currently supported on macOS; enter an absolute path"
        )
    result = subprocess.run(
        [
            "/usr/bin/osascript",
            "-e",
            'POSIX path of (choose folder with prompt "Choose GigAI storage folder")',
        ],
        check=False,
        shell=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        return None
    selected = result.stdout.strip()
    return selected or None


__all__ = [
    "SetupDraft",
    "SetupHTTPServer",
    "SetupInterviewError",
    "choose_local_folder",
]
