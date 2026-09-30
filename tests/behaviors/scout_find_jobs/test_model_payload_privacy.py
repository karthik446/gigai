"""0110-003 P1: no name or contact value reaches a model or the network (payload capture).

One fixture home carries a resume with DISTINCTIVE fake contact values in
its header and planted inline in its body (the display settings file does
not exist yet in P1, so the values live in the resume itself). Every
model-bound flow then runs IN PROCESS (the api-e2e server is a child
process, so monkeypatches would not reach it) through the production
fixture seams:

- the model: ``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL=1`` +
  ``bindings._patch_test_model_transport`` (the real Ollama adapter over an
  ``httpx.MockTransport``);
- the network: ``GIGAI_SCOUT_FIND_JOBS_TEST_HTTP=1`` (Exa/ATS fixtures) and
  a MockTransport for the prep company research.

Every ``httpx.MockTransport.handle_request`` is recorded (URL, headers,
body). Tripwires record and refuse any real ``httpx.HTTPTransport`` request,
any ``socket.connect`` and any child process that is not ``git`` (the
journal's own), so an un-faked network path or CLI adapter cannot slip
past the capture. Non-vacuous controls: every flow recorded at least one
model payload, every model payload carries the body marker (the rank
digest, which never carries body sentences, carries a skill token), and
the Exa capture is non-empty. A last test disables the strip and shows the
same detector finds the values, so a green run is not a blind detector.

The CLI adapters (never exercised by the test model) get a unit check:
a fake ``codex``/``claude`` executable dumps its argv and stdin, and both
must carry the request prompt on stdin and nothing of it in argv.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from gigai.adapters.claude_cli import ClaudeCLIAdapter
from gigai.adapters.codex_cli import CodexCLIAdapter
from gigai.adapters.port import InvocationRequest
from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.config import Endpoint, GigAIConfig
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.config import load_config
from gigai.private_records import read_record
from gigai.scout import proposal_execution
from gigai.scout.find_jobs import bindings
from gigai.scout.find_jobs.api.extract import ResumeExtractRoutesMixin
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.find_jobs.contracts import (
    AssessInput,
    FindJobsConfig,
    ModelTarget,
    NodeContext,
    PinnedResume,
    SelectedPosting,
    SelectionReason,
    SelectionReasonCode,
    SourceToggles,
)
from gigai.scout.find_jobs.exa_client import ExaSearchClient
from gigai.scout.find_jobs.model_rank import rank_postings
from gigai.scout.find_jobs.rank_digest import CandidatePrefs
from gigai.scout.interview_prep import build_prep
from gigai.scout.interview_prep.prep import InterviewPrepError
from gigai.scout.proposal_execution import assess_node
from gigai.scout.quick_assess import run_quick_assessment
from gigai.scout.tailored_resume import TailorRequest, run_tailored_resume
from gigai.setup import build_config, run_setup
from gigai.workpad import resolve_workpad

from .test_interview_prep_fixtures import POSTING_URL, bound_project, write_acquire_output

# --- the distinctive fake candidate ------------------------------------------------------------

NAME = "Zephyrine Quillfeather"
HEADLINE = "Chief Zeppelin Wrangler"  # Q1: the resume's own headline stays in model text
LOCATION = "Xanadu Springs, ZZ"
WORK_AUTH = "VISA: ZQ-9"
LINKEDIN = "linkedin.com/in/zq-7731"
GITHUB = "github.com/zq-7731"
SITE = "zq7731.example"
EMAIL = "zq7731@example.test"
PHONE = "555-013-7731"
BODY_MARKER = "Glimmerfall"  # a body sentence every full-text model payload must carry
RANK_MARKER = "Postgres"  # the rank digest carries skills, never body sentences

RESUME = f"""{NAME}
{HEADLINE}
{LOCATION} | {WORK_AUTH} | {LINKEDIN} | {GITHUB} | {SITE} | {EMAIL} | {PHONE}

## Summary
Operated the {BODY_MARKER} ledger for 6 years.
Built Python services on Kubernetes and Kafka; write to {EMAIL} or call {PHONE}.
Founded Quillfeather Labs; code at https://{GITHUB}/ledger.

## Experience
**Staff Engineer -- Acme** (2019-2023)
- Built Python services on AWS with Postgres.
"""

#: Every value that must never leave: the name (whole and per token) and each contact value.
FORBIDDEN = (
    NAME, "Zephyrine", "Quillfeather", "Xanadu", "ZQ-9", LINKEDIN, GITHUB, "zq-7731", SITE, "zq7731", EMAIL, PHONE, "013-7731",
)

_POSTING = (
    "Acme is hiring a Staff Software Engineer to build reliable Python services on Kubernetes. "
    "Requirements: Python in production; Kubernetes; Kafka; GCP experience is a plus. Remote within the US."
)


def leaks(data: bytes | str) -> list[str]:
    """The forbidden values found in ``data`` (case-insensitive)."""

    text = (data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data).lower()
    return [value for value in FORBIDDEN if value.lower() in text]


# --- capture + tripwires ------------------------------------------------------------------------


@dataclass
class Capture:
    flow: str = "setup"
    requests: list[tuple[str, str, bytes]] = field(default_factory=list)  # (flow, url, headers + body)
    tripped: list[str] = field(default_factory=list)

    def model_payloads(self, flow: str) -> list[bytes]:
        return [data for name, url, data in self.requests if name == flow and url.endswith("/api/chat")]

    def leaks_in(self, flow: str) -> dict[str, list[str]]:
        return {url: leaks(data) for name, url, data in self.requests if name == flow and leaks(data)}

    @contextmanager
    def running(self, flow: str) -> Iterator[None]:
        """Tag what ``flow`` sends; when it finishes, nothing it sent may carry a forbidden value."""

        self.flow = flow
        try:
            yield
        finally:
            self.flow = "between"
        assert self.leaks_in(flow) == {}, f"{flow} sent a name or contact value"


def arm(monkeypatch: pytest.MonkeyPatch) -> Capture:
    """Start recording and refusing (after the fixture home is built: setup itself probes with a child process)."""

    seen = Capture()
    original_mock = httpx.MockTransport.handle_request

    def recording(self: httpx.MockTransport, request: httpx.Request) -> httpx.Response:
        headers = "\n".join(f"{key}: {value}" for key, value in request.headers.items())
        seen.requests.append((seen.flow, str(request.url), headers.encode("utf-8") + b"\n\n" + request.read()))
        return original_mock(self, request)

    def refuse_http(self: object, request: httpx.Request) -> httpx.Response:
        seen.tripped.append(f"real HTTP transport: {request.method} {request.url}")
        raise httpx.ConnectError("payload-privacy tripwire: a real HTTP transport was used", request=request)

    def refuse_connect(self: socket.socket, address: object) -> None:
        seen.tripped.append(f"socket.connect {address!r}")
        raise OSError("payload-privacy tripwire: a socket connect was attempted")

    original_popen_init = subprocess.Popen.__init__

    def guarded_popen(self: subprocess.Popen, args: object, *rest: object, **kwargs: object) -> None:
        argv0 = args if isinstance(args, (str, bytes, Path)) else next(iter(args))  # type: ignore[call-overload]
        if Path(str(argv0)).name != "git":  # the journal's git spawns are local, never a model
            seen.tripped.append(f"subprocess {args!r}")
            setattr(self, "_child_created", False)  # keeps Popen.__del__ quiet
            raise OSError("payload-privacy tripwire: a non-git child process was started")
        original_popen_init(self, args, *rest, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx.MockTransport, "handle_request", recording)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse_http)
    monkeypatch.setattr(socket.socket, "connect", refuse_connect)
    monkeypatch.setattr(subprocess.Popen, "__init__", guarded_popen)
    return seen


# --- the fixture home -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Home:
    home: Path
    target: Path
    gig_id: str
    config: GigAIConfig
    profile_id: str
    record_id: str
    revision_id: str


def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Home:
    home, target, gig_id = bound_project(tmp_path)
    from .test_interview_prep_fixtures import add_resume

    add_resume(home, target, gig_id, tmp_path, text=RESUME.encode("utf-8"))
    run_setup(build_config(
        home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False,
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="local-test", adapter="ollama_local", base_url="http://127.0.0.1:11434"),
        ),
        model_targets=(
            ConfigModelTarget(name="offline-default", endpoint="offline", model="fixture-v1", capabilities=("text",), max_output_tokens=64),
            ConfigModelTarget(
                name="ollama_local", endpoint="local-test", model=bindings.TEST_MODEL_NAME, capabilities=("text",),
                max_output_tokens=512, model_digest=bindings.TEST_MODEL_DIGEST, context_tokens=2048, max_response_bytes=65536,
            ),
        ),
    ))
    find_jobs = FindJobsConfig(
        roles=("staff software engineer",), merged_queries=("staff software engineer",), location="Denver, CO", remote=True,
        published_after=None, sources=SourceToggles(exa=True, ats=True, hiringcafe=False), countries=("US",),
        visa_sponsorship_required=False,
    )
    (target / "find-jobs.json").write_bytes(canonical_json_bytes(find_jobs.to_json()))
    monkeypatch.setenv("GIGAI_HOME", str(home))
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", "1")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    monkeypatch.setenv("EXA_API_KEY", "payload-privacy-exa-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-payload-privacy")
    config = load_config(home)
    # The patch below replaces a module attribute for good; monkeypatch restores it after the test.
    monkeypatch.setattr(proposal_execution, "resolve_model_adapter", proposal_execution.resolve_model_adapter)
    bindings._patch_test_model_transport(config)

    from gigai.scout.profile_records import selected_profile

    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    profile = selected_profile(resolved, home_root=home, target=target)
    assert profile is not None
    return Home(home, target, gig_id, config, profile.profile_id, profile.resume_ref.record_id, profile.resume_ref.revision_id)


class _Handler(ResumeExtractRoutesMixin):
    """The extract route's handler, driven in process (no socket)."""

    def __init__(self, fixture: Home, body: object) -> None:
        self._backend = SimpleNamespace(home_root=fixture.home, target=fixture.target)
        self._body = body
        self.responses: list[tuple[int, object]] = []

    def _read_json_body(self) -> object:
        return self._body

    def _error(self, status: object, code: str, message: str) -> None:
        self.responses.append((int(status), code))  # type: ignore[call-overload]

    def _write_json(self, status: object, payload: object) -> None:
        self.responses.append((int(status), payload))  # type: ignore[call-overload]


def _research_handler(request: httpx.Request) -> httpx.Response:
    if request.method == "POST":
        text = json.dumps({"claims": [{"claim": "Acme runs Python services.", "source_url": "https://acme.example/eng"}]})
        return httpx.Response(
            200,
            json={"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}], "usage": {"input_tokens": 1, "output_tokens": 1}},
        )
    return httpx.Response(200)


def _run_every_flow(fixture: Home, seen: Capture, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = fixture.home, fixture.target
    posting = write_acquire_output(home, target, "run_1")
    job = AssessJobInput(job_text=_POSTING, title="Staff Software Engineer", company="Acme")

    # 1. The find-jobs run's assess node (the run's own model call on the pinned resume).
    with seen.running("assess_node"):
        resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=fixture.gig_id, allow_semantic_state=True)
        run_dir = resolved.path / "runs" / "run_1"
        context = NodeContext(
            run_id="run_00000000-0000-4000-8000-000000000301", project_id=resolved.project_id, gig_id=fixture.gig_id,
            graph_id="graph_find_jobs_test", graph_version=1, goal_slug="assess", manifest_digest="sha256:" + "0" * 64,
            operation_key="payload-privacy-assess", target_observation_digest="sha256:" + "0" * 64,
            workpad_path=str(resolved.path), redeemed_consent_ref="none", model_target=ModelTarget.OLLAMA_LOCAL,
        )
        assess_input = AssessInput(
            acquire_batch_ref=str(run_dir / "outputs" / "acquire.json"), acquire_output_digest="sha256:" + "0" * 64,
            selected_postings=(SelectedPosting(posting.normalized_url, posting.url, posting.content_sha256, True),),
            selection_cap=10, selection_reasons=(SelectionReason(posting.normalized_url, SelectionReasonCode.NEW),),
            pinned_resume=PinnedResume(fixture.record_id, fixture.revision_id, str(digest_imported_bytes(RESUME.encode("utf-8")))),
            target=str(target), model_target=ModelTarget.OLLAMA_LOCAL, answer_association_version="scout-answer-association:1",
        )
        output = assess_node(context, assess_input, home_root=home, target=target, config=fixture.config)
        assert len(output.assessments) == 1, output

    # 2. Rank (the run's rank step and the re-rank API both call rank_postings on the raw stored resume).
    with seen.running("rank"):
        record = read_record(
            home_root=home, requested_target=target, record_id=fixture.record_id, revision_id=fixture.revision_id,
            content=True, gig_id=fixture.gig_id,
        )
        content = record["content"]
        assert isinstance(content, bytes)
        result = rank_postings(
            [posting], resume_text=content.decode("utf-8"), prefs=CandidatePrefs(titles=("Staff Software Engineer",)),
            model_target="ollama_local", home_root=home, config=fixture.config,
        )
        assert result.postings and result.postings[0].scored, result

    # 3. Quick assess: the selected profile, then pasted text.
    for flow, resume in (("quick_assess_profile", AssessResumeInput()), ("quick_assess_pasted", AssessResumeInput(resume_text=RESUME))):
        with seen.running(flow):
            response = run_quick_assessment(AssessRequest(job=job, resume=resume), home_root=home, target=target, config=fixture.config)
            assert response.result is not None and Path(response.stored_path).is_file(), response

    # 4. Tailor: the selected profile, then pasted text.
    for flow, resume in (("tailor_profile", AssessResumeInput()), ("tailor_pasted", AssessResumeInput(resume_text=RESUME))):
        with seen.running(flow):
            tailored = run_tailored_resume(TailorRequest(job=job, resume=resume), home_root=home, target=target, config=fixture.config)
            assert tailored.result.header == () and tailored.result.sections
            assert "Zephyrine" not in tailored.markdown, "headerless: the name is never copied into the result"

    # 5. Resume extract: profile_id, a stored resume_ref, pasted text.
    for flow, body in (
        ("extract_profile", {"profile_id": fixture.profile_id}),
        ("extract_resume_ref", {"resume_ref": {"record_id": fixture.record_id, "revision_id": fixture.revision_id}}),
        ("extract_pasted", {"resume_text": RESUME}),
    ):
        with seen.running(flow):
            handler = _Handler(fixture, body)
            handler._handle_post_resume_extract()
            assert handler.responses and handler.responses[0][0] == 200, handler.responses

    # 6. Interview prep: company research (web search) + question categories (a model call).
    with seen.running("prep"), monkeypatch.context() as scoped:
        real_client = httpx.Client

        def client(*args: object, **kwargs: object) -> httpx.Client:
            kwargs["transport"] = kwargs.get("transport") or httpx.MockTransport(_research_handler)
            return real_client(*args, **kwargs)  # type: ignore[arg-type]

        scoped.setattr(httpx, "Client", client)
        # The fixture model answers a category prompt with an assessment, so the
        # prediction is rejected AFTER the payload went out -- what this test needs.
        with pytest.raises(InterviewPrepError) as info:
            build_prep(home_root=home, target=target, posting_url=POSTING_URL, gig_id=fixture.gig_id)
        assert info.value.code == "category_prediction_invalid", info.value

    # 7. Exa search (queries come from config, never resume text).
    with seen.running("exa"):
        config = FindJobsConfig.from_json(json.loads((target / "find-jobs.json").read_text(encoding="utf-8")))
        with bindings._http_client() as http:
            ExaSearchClient().search(http, config, home_root=home)


_MODEL_FLOWS = (
    "assess_node", "rank", "quick_assess_profile", "quick_assess_pasted", "tailor_profile", "tailor_pasted",
    "extract_profile", "extract_resume_ref", "extract_pasted", "prep",
)


def test_no_name_or_contact_value_reaches_any_model_or_network_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _home(tmp_path, monkeypatch)
    capture = arm(monkeypatch)
    _run_every_flow(fixture, capture, monkeypatch)

    assert capture.tripped == [], "an un-faked network path or child process ran"
    # Non-vacuous controls: every flow reached the model, with resume text in it.
    for flow in _MODEL_FLOWS:
        payloads = capture.model_payloads(flow)
        assert payloads, f"{flow} sent no model payload"
        marker = RANK_MARKER if flow == "rank" else BODY_MARKER
        for payload in payloads:
            assert marker.encode() in payload, f"{flow}: the model payload carries no resume text ({marker})"
    assert any(HEADLINE.encode() in payload for payload in capture.model_payloads("assess_node")), "Q1: the headline stays"
    exa = [data for flow, url, data in capture.requests if flow == "exa" and "api.exa.ai" in url]
    assert exa and b"staff software engineer" in exa[0]
    assert any(flow == "prep" and "api.openai.com" in url for flow, url, _ in capture.requests), "the web search was captured"
    # The assertion itself: no captured request carries any forbidden value.
    found = {(flow, url): leaks(data) for flow, url, data in capture.requests if leaks(data)}
    assert found == {}


def test_the_detector_finds_the_values_when_the_strip_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control: the same capture, with ``model_resume`` made a no-op in every builder, sees the leaks."""

    from gigai.scout import assessment_core, resume_privacy, tailored_resume
    from gigai.scout.find_jobs.api import extract
    from gigai.scout.interview_prep import categories

    def no_strip(text: str) -> resume_privacy.ModelResume:
        numbered = [line.strip() for line in text.splitlines() if line.strip()]
        return resume_privacy.ModelResume(text, tuple(enumerate(numbered, 1)), frozenset())

    for module in (assessment_core, tailored_resume, extract, categories):
        monkeypatch.setattr(module, "model_resume", no_strip)
    fixture = _home(tmp_path, monkeypatch)
    capture = arm(monkeypatch)
    capture.flow = "quick_assess_profile"  # tagged by hand: ``running`` would (rightly) fail on the leak
    run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_text=_POSTING, title="Staff Software Engineer", company="Acme")),
        home_root=fixture.home, target=fixture.target, config=fixture.config,
    )
    payloads = capture.model_payloads("quick_assess_profile")
    assert payloads and set(leaks(payloads[0])) == set(FORBIDDEN)


# --- the CLI adapters: the prompt goes on stdin, nothing else is sent -------------------------------------


_CODEX_FEATURES = "shell_tool stable true\nmemories stable false\n"
_CLAUDE_HELP = "  --setting-sources <s>\n  --strict-mcp-config\n  --tools <tools...>\n"


def _dumping_cli(tmp_path: Path, name: str, probe_args: str, probe_out: str, reply: str) -> Path:
    """A fake CLI that answers a capability probe (if the adapter runs one), then dumps argv + stdin and replies.

    Each test gets its own executable path, so no probe cache can carry over between tests.
    """

    exe = tmp_path / name
    exe.write_text(
        "#!/bin/sh\n"
        f"if [ \"$*\" = \"{probe_args}\" ]; then printf '%s' '{probe_out}'; exit 0; fi\n"
        f"printf '%s\\n' \"$@\" > {tmp_path / (name + '.argv')}\n"
        f"cat > {tmp_path / (name + '.stdin')}\n"
        f"echo '{reply}'\n"
    )
    exe.chmod(0o755)
    return exe


@pytest.mark.parametrize("kind", ["codex_cli", "claude_cli"])
def test_a_cli_adapter_sends_the_request_prompt_on_stdin_and_nothing_else(tmp_path: Path, kind: str) -> None:
    from gigai.scout.assessment_core import AssessContext, AssessJob, render_assess_prompt

    prompt = render_assess_prompt(
        AssessJob(title="Staff Software Engineer", company="Acme", location="Remote", posting_text=_POSTING),
        AssessContext(resume_text=RESUME, visa_sponsorship_required=False),
    )
    assert BODY_MARKER in prompt and leaks(prompt) == []
    request = InvocationRequest(
        target_name="t", endpoint_name="e", model="default", role="reviewer", prompt=prompt,
        target_capabilities=frozenset({"text"}), reasoning_effort="low",
    )
    if kind == "codex_cli":
        ok = '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}'
        adapter: object = CodexCLIAdapter(executable=str(_dumping_cli(tmp_path, "codex", "features list", _CODEX_FEATURES, ok)))
        name = "codex"
    else:
        ok = '{"result":"ok","subtype":"success"}'
        adapter = ClaudeCLIAdapter(executable=str(_dumping_cli(tmp_path, "claude", "--help", _CLAUDE_HELP, ok)))
        name = "claude"
    adapter.invoke(request)  # type: ignore[attr-defined]
    stdin = (tmp_path / f"{name}.stdin").read_text(encoding="utf-8")
    argv = (tmp_path / f"{name}.argv").read_text(encoding="utf-8")
    assert stdin == prompt, "the child's stdin is exactly the request prompt"
    assert BODY_MARKER not in argv and "Staff Software Engineer" not in argv, "no prompt text in argv"
    assert leaks(stdin) == [] and leaks(argv) == []
