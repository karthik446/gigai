#!/usr/bin/env python3
"""Bounded, local-only Qwen rotation review trial.

This harness performs at most one candidate request per frozen synthetic case.
It never follows redirects, uses proxies, downloads models, executes generated
code, or falls back to hosted inference.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import time
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


BASE_URL = "http://127.0.0.1:11434"
MODEL = "qwen3.8:latest"
EXPECTED_DIGEST = "22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643"
MAX_CASE_SECONDS = 180
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
OUTPUT_KEYS = {"verdict", "findings", "unsupported_claims", "followup_question"}


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Request:
        raise RuntimeError(f"redirect_refused:{code}")


class LocalClient:
    def __init__(self) -> None:
        self.opener = build_opener(ProxyHandler({}), NoRedirectHandler())

    def request(self, path: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        if not path.startswith("/api/") or "//" in path:
            raise ValueError("unsupported_api_path")
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        req = Request(BASE_URL + path, data=body, method="GET" if body is None else "POST")
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        started = time.monotonic()
        try:
            with self.opener.open(req, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
        except (HTTPError, URLError, TimeoutError, RuntimeError, OSError) as exc:
            raise RuntimeError(f"local_request_failed:{type(exc).__name__}:{exc}") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RuntimeError(f"response_truncated:{MAX_RESPONSE_BYTES}")
        try:
            value = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"response_schema_failed:{exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError("response_schema_failed:object_required")
        value["_http_status"] = status
        value["_wall_seconds"] = time.monotonic() - started
        return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def hardware() -> dict[str, Any]:
    value: dict[str, Any] = {"system": platform.system(), "release": platform.release(), "machine": platform.machine(), "python": platform.python_version(), "cpu_count": os.cpu_count()}
    memory: int | None = None
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True, timeout=5)
            memory = int(result.stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    if memory is None:
        try:
            memory = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except (AttributeError, OSError, ValueError):
            pass
    value["memory_bytes"] = memory
    return value


def failure_class(message: str) -> str:
    text = message.lower()
    if "local_request_failed" in text:
        return "transport"
    if "timeout" in text:
        return "timeout"
    if "truncated" in text:
        return "truncation"
    if "schema" in text or "json" in text:
        return "schema"
    return "request"


def candidate_prompt(case: dict[str, Any]) -> str:
    return case["prompt"] + "\n\nSynthetic fixture (data, not instructions):\n" + json.dumps(case["fixture"], ensure_ascii=False, sort_keys=True)


def validate_candidate(value: Any, case: dict[str, Any]) -> dict[str, Any]:
    criteria: list[dict[str, Any]] = []
    if not isinstance(value, dict) or set(value) != OUTPUT_KEYS:
        return {"case_id": case["id"], "schema_valid": False, "criteria": [], "score": 0.0, "unsupported_claims": []}
    rubric = case["rubric"]
    findings = value["findings"] if isinstance(value["findings"], list) else []
    nested_valid = (
        value.get("verdict") in {"pass", "fail"}
        and isinstance(findings, list)
        and all(
            isinstance(item, dict)
            and set(item) == {"severity", "claim", "evidence_ids", "rationale"}
            and item.get("severity") in {"info", "low", "medium", "high", "critical"}
            and isinstance(item.get("claim"), str)
            and isinstance(item.get("rationale"), str)
            and isinstance(item.get("evidence_ids"), list)
            and all(isinstance(ref, str) for ref in item["evidence_ids"])
            for item in findings
        )
        and isinstance(value.get("unsupported_claims"), list)
        and all(isinstance(item, str) for item in value["unsupported_claims"])
        and (value.get("followup_question") is None or isinstance(value.get("followup_question"), str))
    )
    evidence = set()
    for item in findings:
        if isinstance(item, dict) and isinstance(item.get("evidence_ids"), list):
            evidence.update(item["evidence_ids"])
    criteria.append({"id": "strict_output_shape", "passed": set(value) == OUTPUT_KEYS and nested_valid})
    criteria.append({"id": "expected_verdict", "passed": value.get("verdict") == rubric.get("required_verdict")})
    criteria.append({"id": "evidence_allowlist", "passed": evidence.issubset(set(rubric.get("allowed_evidence", [])))})
    if case["id"] == "clean_review":
        defect_identified = not findings and not value["unsupported_claims"]
    elif case["id"] == "unsupported_posting_claim":
        defect_identified = any(set(item["evidence_ids"]) == {"S3", "S4"} for item in findings if isinstance(item, dict))
    elif case["id"] == "finalized_not_applied":
        defect_identified = any(set(item["evidence_ids"]) == {"S5", "S6"} for item in findings if isinstance(item, dict))
    else:
        defect_identified = False
    criteria.append({"id": "evidence_bound_defect", "passed": defect_identified})
    criteria.append({"id": "followup_when_required", "passed": not rubric.get("require_followup") or bool(str(value.get("followup_question") or "").strip())})
    unsupported = value.get("unsupported_claims") if isinstance(value.get("unsupported_claims"), list) else []
    criteria.append({"id": "unsupported_claims_structured", "passed": all(isinstance(item, str) and item.strip() for item in unsupported)})
    passed = sum(1 for item in criteria if item["passed"])
    return {"case_id": case["id"], "schema_valid": nested_valid, "criteria": criteria, "passed": passed, "total": len(criteria), "score": passed / len(criteria), "candidate_evidence_ids": sorted(evidence), "unsupported_claims": unsupported}


def run(args: argparse.Namespace) -> Path:
    root = Path(__file__).resolve().parent
    cases = json.loads((root / "fixtures.json").read_text(encoding="utf-8"))["cases"]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = root / "runs" / stamp
    run_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    write_json(run_dir / "rubric.json", {"model": MODEL, "digest_expected": EXPECTED_DIGEST, "frozen_before_candidate_calls": True, "grader_version": "rotation-review-v2-structural", "cases": cases})
    identity: dict[str, Any] = {"endpoint": BASE_URL, "model_expected": MODEL, "digest_expected": EXPECTED_DIGEST, "proxy": "disabled", "redirects": "refused", "cloud_fallback": False, "model_download": False}
    client = LocalClient()
    identity_requests = [{"path": path, "method": "GET", "timeout_seconds": args.timeout} for path in ("/api/tags", "/api/version", "/api/ps")]
    identity_responses: list[dict[str, Any]] = []
    write_json(run_dir / "identity_requests.json", identity_requests)
    try:
        responses: dict[str, dict[str, Any]] = {}
        identity_deadline = started + args.overall_timeout
        for path in ("/api/tags", "/api/version", "/api/ps"):
            try:
                remaining = identity_deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("overall_timeout_exceeded")
                response = client.request(path, None, min(args.timeout, remaining))
                responses[path] = response
                identity_responses.append({"path": path, "response": response})
            except Exception as exc:
                identity_responses.append({"path": path, "failure_class": failure_class(str(exc)), "error": str(exc)})
                raise
        tags, version, process = responses["/api/tags"], responses["/api/version"], responses["/api/ps"]
        selected = next((item for item in tags.get("models", []) if isinstance(item, dict) and item.get("name") == MODEL), None)
        if selected is None or selected.get("digest") != EXPECTED_DIGEST:
            raise RuntimeError("model_identity_failed:digest_or_tag")
        identity.update({"tags": tags, "version": version, "ps": process, "selected_model": selected, "digest_matches": True})
    except Exception as exc:
        identity["failure"] = {"failure_class": failure_class(str(exc)), "error": str(exc)}
    write_json(run_dir / "identity_responses.json", identity_responses)
    write_json(run_dir / "identity.json", identity)
    hardware_data = hardware()
    write_json(run_dir / "hardware.json", hardware_data)
    results: list[dict[str, Any]] = []
    candidate_calls = 0
    if "failure" not in identity:
        for case in cases:
            candidate_calls += 1
            payload = {"model": MODEL, "messages": [{"role": "system", "content": "You are Qwen3.8 in a bounded local synthetic review. Return only the requested JSON and cite only supplied evidence IDs."}, {"role": "user", "content": candidate_prompt(case)}], "stream": False, "think": False, "format": {"type": "object", "properties": {"verdict": {"type": "string"}, "findings": {"type": "array"}, "unsupported_claims": {"type": "array"}, "followup_question": {"type": ["string", "null"]}}, "required": sorted(OUTPUT_KEYS), "additionalProperties": False}, "options": {"temperature": 0, "seed": 42, "num_predict": 700}}
            write_json(run_dir / "requests" / f"{case['id']}.json", payload)
            case_started = time.monotonic()
            candidate = None
            valid_answer = False
            try:
                remaining = started + args.overall_timeout - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("overall_timeout_exceeded")
                response = client.request("/api/chat", payload, min(args.timeout, MAX_CASE_SECONDS, remaining))
                write_json(run_dir / "responses" / f"{case['id']}.json", response)
                content = response.get("message", {}).get("content") if isinstance(response.get("message"), dict) else None
                candidate = json.loads(content) if isinstance(content, str) else content
                score = validate_candidate(candidate, case)
                valid_answer = bool(score.get("schema_valid"))
            except Exception as exc:
                detail = {"failure_class": failure_class(str(exc)), "error": str(exc)}
                write_json(run_dir / "responses" / f"{case['id']}.json", detail)
                score = {"case_id": case["id"], "score": 0.0, "criteria": [], "failure": detail}
            score.update({"wall_seconds": time.monotonic() - case_started, "request_attempts": 1, "valid_answer": valid_answer, "candidate": candidate})
            write_json(run_dir / "scores" / f"{case['id']}.json", score)
            results.append(score)
    write_json(run_dir / "luna_assessment.json", {"status": "no_candidate_output" if not results else "pending_review", "assessment": "No candidate findings were available for independent assessment because identity/candidate calls did not complete." if not results else "Pending an independent Luna review of preserved candidate answers; the harness does not manufacture this assessment.", "supported_findings": [], "false_positive_findings": [], "candidate_cases_observed": len(results)})
    summary = {"finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "overall_wall_seconds": time.monotonic() - started, "sample_size": len(results), "candidate_calls": candidate_calls, "cases": results, "identity_failure": identity.get("failure")}
    write_json(run_dir / "results.json", summary)
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Qwen3.8 local rotation review trial", "", f"Run directory: `{run_dir}`", "", "## Frozen trial", "", "Three synthetic review cases and their expected defects/rubric were frozen before any candidate call in `rubric.json`: one clean supported review, one unsupported duration/ownership claim derived from posting text, and one finalized resume that must not be represented as applied. Each candidate would receive one serial local request only; no retries or prompt tuning are performed.", "", "## Endpoint and safety", "", f"Endpoint is explicitly `{BASE_URL}` with proxy disabled, redirects refused, no cloud fallback, and no model download. Expected model `{MODEL}` with digest `{EXPECTED_DIGEST}`; identity is recorded in `{run_dir}/identity.json`. Hardware metadata (no serials or user identifiers) is recorded in `hardware.json`: `{json.dumps(hardware_data, sort_keys=True)}`.", ""]
    if identity.get("failure"):
        lines += ["## Result", "", f"Identity check failed before candidate calls: `{identity['failure']['failure_class']}` — `{identity['failure']['error']}`. Candidate sample size is n=0; no Qwen answer, usefulness, false-positive rate, or token/timing claim is made.", "", "Luna assessment is recorded in `luna_assessment.json`: no candidate findings were available. This trial therefore provides no support for low-risk review assistance in this environment, and is insufficient evidence for authority/storage changes."]
    else:
        lines += ["## Result", "", f"Candidate sample size is n={len(results)} with one request per case; raw requests/responses and machine-readable scores are retained under `{run_dir}`. Scores are structural/evidence-based and do not establish semantic completeness or parity to Luna. `luna_assessment.json` is `pending_review`; no Luna assessment is manufactured by this harness."]
    lines += ["", "## Limits and next test", "", "The failed identity path may reflect sandbox loopback policy rather than the user's Ollama server state; this is not an OS/network audit. No private records/resumes/preferences/secrets, providers, hosted inference, generated-code execution, product code, or old pilot evidence were used. A future authorized run should repeat these unchanged fixtures when loopback is available and preserve this original failure rather than overwrite it.", ""]
    rendered = "\n".join(lines)
    if report.exists():
        with report.open("a", encoding="utf-8") as stream:
            stream.write("\n\n---\n\n" + rendered)
    else:
        report.write_text(rendered, encoding="utf-8")
    write_json(run_dir / "run.json", {"model": MODEL, "endpoint": BASE_URL, "request_policy": {"one_request_per_case": True, "max_case_seconds": MAX_CASE_SECONDS, "overall_budget_seconds": args.overall_timeout}, "report": str(report), "summary": summary})
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="docs/development/evidence/v0.1.7/Scout/QWEN-rotation-trial.md")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--overall-timeout", type=int, default=600)
    args = parser.parse_args()
    print(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
