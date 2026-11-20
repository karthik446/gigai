"""0.3.4 (gigai-9j8, UI rules): a posting the server could not read is said with the server's reason and the page's paste path.

Pure JavaScript under the system ``node`` (a LOUD skip without it). The two server messages are the REAL ones:
``job_input.resolve_job`` over an ``httpx.MockTransport`` (a script-only page; an HTTP 503), so a rewording of the
server's CLI tail ("pass --job-text with the posting text instead") that the page no longer recognises fails here.

* ``assessModel.jobTextError``: ``job_text_unavailable`` (422) and ``job_fetch_failed`` (502) keep the server's reason and
  replace its CLI tail with the form's own words ("Switch to Paste text and paste the posting's text instead."). Before,
  the 422 read "The server rejected this request as invalid. Reload and try again." (``api.js``'s generic 422 text) and
  nothing said to paste the posting. Any other error answers null, so the form's other rules are untouched.
* ``answersModel.reassessErrorText``: the job page's re-assess (``POST /api/answers``, which answers the assess family as
  409) says the same reason with the job page's words ("Assess it again from Assess a job with the posting text pasted."),
  and ``reassess_unavailable`` (a job assessed from pasted text) likewise. Before: "The configuration changed since it was
  loaded. Reload the config and try again." (the generic 409 text).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.find_jobs.assess_contracts import AssessJobInput
from gigai.scout.find_jobs.contracts import FindJobsContractError
from gigai.scout.find_jobs.job_input import resolve_job

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

NODE_SCRIPT = """
const assess = await import(__ASSESS_URL__);
const answers = await import(__ANSWERS_URL__);
const input = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify({
  form: Object.fromEntries(input.errors.map((err) => [err.code, assess.jobTextError(err)])),
  reassess: Object.fromEntries(input.errors.map((err) => [err.code, answers.reassessErrorText(err)])),
  other: [assess.jobTextError(input.other), assess.jobTextError(null), assess.jobTextError({ status: 422, code: "job_text_unavailable", message: "", detail: "" })],
  otherReassess: answers.reassessErrorText(input.other),
}));
"""

#: ``api.js`` ``STATUS_MESSAGES``: what the form showed for the status before (502 has none: the server's message).
GENERIC_422 = "The server rejected this request as invalid. Reload and try again."
GENERIC_409 = "The configuration changed since it was loaded. Reload the config and try again."
PASTE_HINT = "Switch to Paste text and paste the posting's text instead."
REASSESS_HINT = "Assess it again from Assess a job with the posting text pasted."
CLI_TAIL = "pass --job-text with the posting text instead"
#: ``find_jobs/api/answers.py``: a job assessed from pasted text cannot be re-assessed from the store.
REASSESS_UNAVAILABLE = "this job was assessed from pasted text, which is never stored; re-assess with --job-text again"


def _site(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/empty":
        return httpx.Response(200, text="<html><body><div id='root'></div><script>boot()</script></body></html>", request=request)
    return httpx.Response(503, text="<html>secret body text 12345</html>", request=request)


def _server_message(path: str) -> tuple[str, str]:
    """(code, message) as ``resolve_job`` raises it for ``https://careers.example.test/<path>``; no network."""
    with httpx.Client(transport=httpx.MockTransport(_site)) as client, pytest.raises(FindJobsContractError) as excinfo:
        resolve_job(AssessJobInput(job_url=f"https://careers.example.test/{path}"), client=client)
    return excinfo.value.code, str(excinfo.value)


@pytest.fixture(scope="module")
def messages() -> dict[str, str]:
    said = dict(_server_message(path) for path in ("empty", "down"))
    assert set(said) == {"job_text_unavailable", "job_fetch_failed"}, said
    said["reassess_unavailable"] = REASSESS_UNAVAILABLE
    for code, message in said.items():
        assert "--job-text" in message, (code, message)  # the CLI tail the page replaces
    return said


@pytest.fixture(scope="module")
def out(messages: dict[str, str]) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD SKIP: node is not on PATH; the UI model cannot run")
    # Each error as the page's ApiError holds it after this fix: `message` is the server's own (api.js
    # CODES_WITH_OWN_MESSAGE) and `detail` always is. The status is the assess route's (find_jobs/api/assess.py) for
    # the form; the job page's re-assess sees them as 409 (answers.py), which the rule must not depend on.
    errors = [
        {"status": 422, "code": "job_text_unavailable", "message": messages["job_text_unavailable"], "detail": messages["job_text_unavailable"], "reason": "no_text"},
        {"status": 502, "code": "job_fetch_failed", "message": messages["job_fetch_failed"], "detail": messages["job_fetch_failed"], "reason": "network_error"},
        {"status": 409, "code": "reassess_unavailable", "message": messages["reassess_unavailable"], "detail": messages["reassess_unavailable"]},
    ]
    other = {"status": 422, "code": "invalid_value", "message": GENERIC_422, "detail": "job_url: not a URL"}
    proc = subprocess.run(
        [node, "--input-type=module", "-e",
         NODE_SCRIPT.replace("__ASSESS_URL__", json.dumps((UI_SRC / "assessModel.js").as_uri())).replace("__ANSWERS_URL__", json.dumps((UI_SRC / "answersModel.js").as_uri())),
         json.dumps({"errors": errors, "other": other})],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_form_says_the_servers_reason_and_the_paste_path_in_its_own_words(out: dict, messages: dict[str, str]) -> None:
    for code in ("job_text_unavailable", "job_fetch_failed"):
        text = out["form"][code]
        reason = messages[code].split("; " + CLI_TAIL)[0]
        assert text == f"{reason[0].upper()}{reason[1:]}. {PASTE_HINT}", (code, text)
        assert "--job-text" not in text and GENERIC_422 not in text, (code, text)
    assert "no posting text" in out["form"]["job_text_unavailable"]  # the server's reason for a script-only page
    assert "HTTP 503" in out["form"]["job_fetch_failed"] and "secret body text" not in out["form"]["job_fetch_failed"]


def test_any_other_error_is_left_to_the_forms_other_rules(out: dict) -> None:
    assert out["other"][0] is None and out["other"][1] is None
    # A server that said nothing: the code's own plain reason, never an empty sentence.
    assert out["other"][2] == f"The page at this URL carries no posting text. {PASTE_HINT}"
    assert out["otherReassess"] == GENERIC_422  # err.message, as before


def test_the_job_pages_reassess_says_the_reason_with_its_own_paste_words(out: dict, messages: dict[str, str]) -> None:
    for code in ("job_text_unavailable", "job_fetch_failed"):
        text = out["reassess"][code]
        reason = messages[code].split("; " + CLI_TAIL)[0]
        assert text == f"{reason[0].upper()}{reason[1:]}. {REASSESS_HINT} The assessment shown stays.", (code, text)
        assert GENERIC_409 not in text and "--job-text" not in text
    assert out["reassess"]["reassess_unavailable"] == f"This job was assessed from pasted text, which is never stored. {REASSESS_HINT} The assessment shown stays."
