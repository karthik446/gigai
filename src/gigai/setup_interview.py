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
from pathlib import Path
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
    verified_model_targets: tuple[str, ...] = ()


class SetupHTTPServer:
    """Short-lived loopback setup page; configuration is applied only on submit."""

    def __init__(
        self,
        draft: SetupDraft,
        *,
        model_options: tuple[Mapping[str, str], ...],
        detected_models: tuple[DetectedModel, ...],
        on_apply: Callable[[SetupDraft], Mapping[str, object]],
        on_probe: Callable[[str, SetupDraft], Mapping[str, object]] | None = None,
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
        self.on_probe = on_probe
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

            def _stylesheet_authorized(self) -> bool:
                return self.path == f"/setup/{owner.token}.css"

            def _json(self, status: int, payload: Mapping[str, object]) -> None:
                body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if self._stylesheet_authorized():
                    body = owner._render_css()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/css; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
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
                    event = payload.get("event")
                    if event not in {"apply", "probe"}:
                        raise SetupInterviewError("setup requires an apply or probe event")
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
                        verified_model_targets=_text_array(payload, "verified_model_targets"),
                    )
                    if event == "probe":
                        if owner.on_probe is None:
                            raise SetupInterviewError("readiness probes are unavailable")
                        target_name = _required_text(payload, "target_name")
                        result = owner.on_probe(target_name, draft)
                        self._json(200, {"status": "probed", "result": dict(result)})
                        return
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

        def target_kind(item: Mapping[str, str]) -> str:
            return str(item.get("kind", "cli"))

        def api_fields(item: Mapping[str, str]) -> str:
            fields = {
                "openai-default": ("openai_api_env", "openai_api_model", "OPENAI_API_KEY"),
                "openrouter-default": (
                    "openrouter_api_env",
                    "openrouter_api_model",
                    "OPENROUTER_API_KEY",
                ),
            }
            env_field, model_field, placeholder = fields.get(str(item["id"]), ("", "", ""))
            if not env_field:
                return ""
            return (
                "<div class='api-inline' data-api-inline='"
                + html.escape(str(item["id"]))
                + "'><p>GigAI stores only the environment-variable name. The secret value is read at invocation time.</p>"
                + "<label class='field'>Environment variable<input data-api-env name='"
                + env_field
                + "' type='text' value='"
                + html.escape(getattr(self.draft, env_field))
                + "' placeholder='"
                + placeholder
                + "'></label><label class='field'>Model<input name='"
                + model_field
                + "' type='text' value='"
                + html.escape(getattr(self.draft, model_field))
                + "' placeholder='Provider model name'></label></div>"
            )

        roster_html = "".join(
            "<div class='target-option' data-verified='"
            + ("true" if item["id"] in self.draft.verified_model_targets else "false")
            + "' data-target-kind='"
            + html.escape(target_kind(item))
            + "' data-target-id='"
            + html.escape(str(item["id"]))
            + "'><label class='model-option'><input class='target-toggle' type='checkbox' name='enabled_model_targets' "
            + f"value='{html.escape(str(item['id']))}' "
            + f"{'checked' if item['id'] in self.draft.enabled_model_targets else ''} "
            + f"{_model_option_disabled(item['id'], self.draft)}>"
            + "<span class='target-copy'><strong>"
            + html.escape(str(item["label"]))
            + "</strong><span>"
            + html.escape(str(item["description"]))
            + "</span></span><span class='provider-status'>"
            + (
                "Verified"
                if item["id"] in self.draft.verified_model_targets
                else "Check readiness"
                if not _model_option_disabled(item["id"], self.draft)
                else "Needs setup"
            )
            + "</span></label>"
            + (
                "<button type='button' class='probe-button' data-probe='"
                + html.escape(str(item["id"]))
                + "'>Check readiness</button>"
                if self.on_probe is not None
                else ""
            )
            + api_fields(item)
            + "</div>"
            for item in self.model_options
        )
        reviewer_options = _role_select("Reviewer", "reviewer_model_target", self.draft.reviewer_model_target, self.model_options, self.draft)
        verifier_options = _role_select("Verifier", "verifier_model_target", self.draft.verifier_model_target, self.model_options, self.draft)
        researcher_options = _role_select("Researcher", "researcher_model_target", self.draft.researcher_model_target, self.model_options, self.draft)
        creation_options = _role_select("Gig creation", "selected_model_target", self.draft.selected_model_target, self.model_options, self.draft)
        error_html = (
            f"<p class='error' role='alert'>{html.escape(self.error)}</p>" if self.error else ""
        )
        checked = "checked" if self.draft.open_with_target else ""
        has_cli = any(target_kind(item) == "cli" for item in self.model_options)
        has_api = any(target_kind(item) == "api" for item in self.model_options)
        initial_access = "both" if has_cli and has_api else "cli" if has_cli else "api"
        access_choices = "".join(
            "<label class='access-option'><input type='radio' name='model_access' value='"
            + access
            + "' data-access='"
            + access
            + "'"
            + (" checked" if access == initial_access else "")
            + "><strong>"
            + label
            + "</strong><span>"
            + description
            + "</span></label>"
            for access, label, description, available in (
                (
                    "cli",
                    "CLI only",
                    "Use detected Claude or Codex installations; authentication stays with those tools.",
                    has_cli,
                ),
                (
                    "api",
                    "API only",
                    "Use providers configured through environment-variable references.",
                    has_api,
                ),
                (
                    "both",
                    "Both CLI and API",
                    "Keep both boundaries available and choose defaults by role.",
                    has_cli and has_api,
                ),
            )
            if available
        )
        body = (
            "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Set up GigAI</title><link rel='stylesheet' href='"
            + self.url
            + ".css'><main class='shell'><header><h1>Set up GigAI</h1>"
            "<p class='intro'>Choose a private place for GigAI and the models that can help define Gigs. Nothing is uploaded during setup.</p>"
            "<p class='privacy'>Private by default · setup does not upload your files</p></header>"
            + error_html
            + "<form id='setup' class='card setup-form'>"
            + "<div class='steps'><span class='step active' data-step-indicator='workspace'>1 Workspace</span><span class='step' data-step-indicator='access'>2 Access</span><span class='step' data-step-indicator='models'>3 Models</span><span class='step' data-step-indicator='roles'>4 Roles</span><span class='step' data-step-indicator='ready'>5 Ready</span></div>"
            + "<section class='setup-step active' data-step='workspace'><div class='question-grid'><div><div class='screen-kicker'>Question 1 of 5 · Workspace</div><h2>Where should GigAI keep its private data?</h2><p class='muted'>Choose one local folder. GigAI derives its private workpads underneath it.</p><div class='folder-choice'><button id='choose-folder' type='button'>Choose folder</button></div>"
            + f"<label class='field'>GigAI home<input name='home_root' type='text' value='{html.escape(self.draft.home_root)}' placeholder='Choose a folder or enter an absolute path' required></label>"
            + f"<label class='field'>Private workpad folder<input name='workpad_root' type='text' value='{html.escape(self.draft.workpad_root)}' placeholder='Derived as <GigAI home>/workpads' required><span class='muted'>Local storage for proposals, journals, and Gig state.</span></label>"
            + f"<label class='field'>Editor executable<input name='editor' type='text' value='{html.escape(self.draft.editor)}' required><span class='muted'>Used only to open a workpad later.</span></label><div class='setup-actions'><button type='button' data-next='access'>Continue</button></div></div><aside class='context-panel'><strong>Gig definition</strong><p>A repeatable unit of work with stable Goals, changing inputs, and reviewable results.</p><p>Setup chooses how GigAI helps define that work; it does not define the Gig itself.</p></aside></div></section>"
            + "<section class='setup-step' data-step='access'><div class='question-grid'><div><div class='screen-kicker'>Question 2 of 5 · Access boundary</div><h2>How should GigAI reach models?</h2><p class='muted'>Choose the simplest boundary. This decides which model choices appear next.</p><div class='access-grid'>"
            + access_choices
            + "</div><div class='setup-actions'><button type='button' class='secondary' data-back='workspace'>Back</button><button type='button' data-next='models'>Continue</button></div></div><aside class='context-panel'><strong>Why this matters</strong><p>CLI tools keep authentication with the installed tool. APIs use a configured environment reference.</p><p>You can change this setup later without changing a Gig's definition.</p></aside></div></section>"
            + "<section class='setup-step' data-step='models'><div class='question-grid'><div><div class='screen-kicker'>Question 3 of 5 · Available models</div><h2>Which models should be available?</h2><p class='muted'>Select every model GigAI may use. Selecting an API provider expands its configuration here.</p><div class='model-roster'>"
            + roster_html
            + "</div><div class='setup-actions'><button type='button' class='secondary' data-back='access'>Back</button><button type='button' data-next='roles'>Continue to roles</button></div></div><aside class='context-panel'><strong>Model roster</strong><p>Multiple models can be enabled. Role defaults later choose from this roster.</p><p>Detection alone never grants invocation authority; only usable, configured targets can be enabled.</p></aside></div></section>"
            + "<section class='setup-step' data-step='roles'><div class='question-grid'><div><div class='screen-kicker'>Question 4 of 5 · Machine defaults</div><h2>Which defaults should GigAI use?</h2><p class='muted'>These are machine defaults only. Each Gig can define and override its own workflow roles.</p><div class='role-grid'>"
            + reviewer_options
            + verifier_options
            + researcher_options
            + creation_options
            + "</div><div class='setup-actions'><button type='button' class='secondary' data-back='models'>Back</button><button type='button' data-next='ready'>Review setup</button></div></div><aside class='context-panel'><strong>Four machine defaults</strong><p>Reviewer and verifier are distinct. Researcher gathers bounded context. Gig creator maps to the registered <code>gig-builder</code> model purpose.</p><p>Planner, critic, adjudicator, and implementer belong to individual Gigs.</p></aside></div></section>"
            + "<section class='setup-step' data-step='ready'><div class='question-grid'><div><div class='screen-kicker'>Question 5 of 5 · Confirmation</div><h2>Your GigAI starting setup</h2><p class='muted'>Review the choices collected across setup. Nothing runs until you create and approve a Gig.</p><div class='summary' data-summary></div><div class='setup-actions'><button type='button' class='secondary' data-back='roles'>Back</button><button type='submit'>Apply setup and define a Gig</button></div></div><aside class='context-panel'><strong>Ready</strong><p>Applying setup publishes one typed configuration atomically. Re-running setup updates the same configuration; it does not create a second store.</p></aside></div></section>"
            + "<label class='advanced-toggle'><input name='open_with_target' type='checkbox' "
            + checked
            + "> Open workpads with their target later</label></form><p class='footer'>This page is local-only, loopback-bound, token-protected, and expires automatically.</p></main>"
            + "<script>const form=document.querySelector('#setup');const choose=document.querySelector('#choose-folder');const initialAccess='"
            + initial_access
            + "';let access=initialAccess;const accessLabels={cli:'CLI only',api:'API only',both:'CLI and API'};function verified(){return new Set([...form.querySelectorAll('.target-option')].filter(card=>card.dataset.verified==='true').map(card=>card.dataset.targetId));}function screen(name){for(const item of form.querySelectorAll('[data-step]'))item.classList.toggle('active',item.dataset.step===name);for(const item of form.querySelectorAll('[data-step-indicator]')){const order=['workspace','access','models','roles','ready'];const current=order.indexOf(name);const index=order.indexOf(item.dataset.stepIndicator);item.classList.toggle('active',index===current);item.classList.toggle('done',index>=0&&index<current);}if(name==='ready')summary();}function refresh(){const checkedVerified=verified();for(const card of form.querySelectorAll('.target-option')){const visible=access==='both'||card.dataset.targetKind===access;card.hidden=!visible;const input=card.querySelector('.target-toggle');card.classList.toggle('selected',Boolean(input&&input.checked));const inline=card.querySelector('.api-inline');if(inline)inline.classList.toggle('expanded',Boolean(input&&input.checked));const status=card.querySelector('.provider-status');if(status&&!_model_option_disabled_placeholder(card))status.textContent=card.dataset.verified==='true'?'Verified':'Check readiness';}for(const input of form.querySelectorAll('.target-toggle')){if(input.value==='openai-default'||input.value==='openrouter-default'){const field=input.value==='openai-default'?form.openai_api_env:form.openrouter_api_env;const ready=Boolean(field&&field.value.trim());input.disabled=!ready;if(!ready)input.checked=false;}}const enabled=new Set([...form.querySelectorAll('.target-toggle:checked')].map(item=>item.value));for(const select of form.querySelectorAll('select')){for(const option of select.options){option.disabled=!enabled.has(option.value)||!checkedVerified.has(option.value);}}}function _model_option_disabled_placeholder(card){const input=card.querySelector('.target-toggle');return input&&input.disabled;}function summary(){const enabled=[...form.querySelectorAll('.target-toggle:checked')].map(input=>input.closest('.target-option').querySelector('strong').textContent);const verifiedModels=[...verified()].map(id=>id);const roles=[...form.querySelectorAll('.role-grid select')].map(select=>select.closest('.field').firstChild.textContent.trim()+': '+select.value);form.querySelector('[data-summary]').innerHTML='<div class=\"summary-row\"><strong>Workspace</strong><span>'+form.home_root.value+'</span></div><div class=\"summary-row\"><strong>Access</strong><span>'+accessLabels[access]+'</span></div><div class=\"summary-row\"><strong>Models</strong><span>'+ (enabled.join(', ')||'None selected')+'</span></div><div class=\"summary-row\"><strong>Verified</strong><span>'+ (verifiedModels.join(', ')||'None yet')+'</span></div><div class=\"summary-row\"><strong>Role defaults</strong><span>'+roles.join(' · ')+'</span></div>';}async function probe(button){button.disabled=true;button.textContent='Checking…';const p={event:'probe',target_name:button.dataset.probe,openai_api_env:form.openai_api_env.value,openai_api_model:form.openai_api_model.value,openrouter_api_env:form.openrouter_api_env.value,openrouter_api_model:form.openrouter_api_model.value,verified_model_targets:[...verified()]};const r=await fetch(location.href,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});const b=await r.json();button.disabled=false;if(!r.ok||!b.result||b.result.readiness!=='usable'){button.textContent='Check readiness';alert((b.result&&b.result.reason)||b.error||'Readiness check failed');return;}const card=button.closest('.target-option');card.dataset.verified='true';button.textContent='Verified';refresh();}for(const input of form.querySelectorAll('[data-access]'))input.addEventListener('change',()=>{access=input.value;refresh();});for(const input of form.querySelectorAll('.target-toggle'))input.addEventListener('change',refresh);for(const input of form.querySelectorAll('[data-api-env]'))input.addEventListener('input',()=>{const card=input.closest('.target-option');if(card){card.dataset.verified='false';const button=card.querySelector('[data-probe]');if(button)button.textContent='Check readiness';}refresh();});for(const button of form.querySelectorAll('[data-probe]'))button.addEventListener('click',()=>probe(button));for(const button of form.querySelectorAll('[data-next]'))button.addEventListener('click',()=>{refresh();screen(button.dataset.next);});for(const button of form.querySelectorAll('[data-back]'))button.addEventListener('click',()=>screen(button.dataset.back));choose.addEventListener('click',async()=>{const r=await fetch(location.href,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event:'choose_folder'})});const b=await r.json();if(!r.ok){alert(b.error||'Folder chooser unavailable');}else if(b.status==='selected'){form.home_root.value=b.path;form.workpad_root.value=b.path.replace(/[\\/]$/,'')+'/workpads';}});form.addEventListener('submit',async e=>{e.preventDefault();refresh();const enabled=[...form.querySelectorAll('.target-toggle:checked')].map(item=>item.value);const selected=form.querySelector('select[name=selected_model_target]');const p={event:'apply',home_root:form.home_root.value,workpad_root:form.workpad_root.value,editor:form.editor.value,open_with_target:form.open_with_target.checked,selected_model_target:selected&&selected.value,reviewer_model_target:form.reviewer_model_target.value,verifier_model_target:form.verifier_model_target.value,researcher_model_target:form.researcher_model_target.value,enabled_model_targets:enabled,verified_model_targets:[...verified()],openai_api_env:form.openai_api_env.value,openai_api_model:form.openai_api_model.value,openrouter_api_env:form.openrouter_api_env.value,openrouter_api_model:form.openrouter_api_model.value};const r=await fetch(location.href,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});if(!r.ok){const b=await r.json();alert(b.error||'Setup could not be applied');}else{document.body.innerHTML='<main class=\"shell\"><header><h1>GigAI setup complete</h1><p class=\"intro\">Configuration saved. You can close this tab.</p></header></main>';}});refresh();screen('workspace');</script>"
        ).encode()
        return body

    def _render_css(self) -> bytes:
        return (Path(__file__).parent / "static" / "setup.css").read_bytes()


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
    draft: SetupDraft,
) -> str:
    option_html = "".join(
        f"<option value='{html.escape(str(item['id']))}' "
        f"{'selected' if item['id'] == selected and not _model_option_disabled(item['id'], draft) else ''} "
        f"{_model_option_disabled(item['id'], draft)}>"
        f"{html.escape(str(item['label']))}</option>"
        for item in options
    )
    return (
        f"<label class='field'>{html.escape(label)} default"
        f"<span class='select-wrap'><select name='{html.escape(name)}' required>{option_html}</select></span>"
        "</label>"
    )


def _model_option_disabled(target_id: str, draft: SetupDraft) -> str:
    if target_id == "openai-default" and not draft.openai_api_env.strip():
        return "disabled"
    if target_id == "openrouter-default" and not draft.openrouter_api_env.strip():
        return "disabled"
    return ""


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
    open_attribute = " open" if env_value else ""
    detail = (
        "GigAI stores only the environment-variable name; the secret value is read at invocation time."
        if configured
        else "Add the environment-variable name used by this provider. GigAI never receives the secret value."
    )
    placeholder = provider.upper().replace(" ", "_") + "_API_KEY"
    return (
        "<li class='provider-item'><details class='provider-config'"
        + open_attribute
        + "><summary><strong>"
        + provider
        + " API</strong><span class='provider-status'>"
        + status
        + "</span></summary><div class='provider-fields'><p>"
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
        + "' placeholder='Provider model name'></label></div></details></li>"
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
