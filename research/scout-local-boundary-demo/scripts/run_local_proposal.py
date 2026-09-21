#!/usr/bin/env python3
"""Bounded synthetic Ollama proposal request for the Scout privacy spike.

This helper refuses non-loopback URLs and never falls back to a hosted endpoint.
It is intentionally not a GigAI production caller.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


MODEL_ALLOWLIST = {
    "qwen3.8:latest": "22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643",
}
LOOPBACK_PORT = 11499


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, _fp, _code, _msg, _headers, _newurl):
        raise RuntimeError("redirect refused")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="http://127.0.0.1:11499")
    parser.add_argument("--model", default="qwen3.8:latest")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--think", action="store_true", help="allow model thinking; off by default for bounded proposal output")
    return parser.parse_args()


def main() -> int:
    args = _args()
    if args.model not in MODEL_ALLOWLIST:
        raise SystemExit("model is not in the exact installed local allowlist")
    base = urlsplit(args.host.rstrip("/"))
    if (
        base.scheme != "http"
        or base.hostname != "127.0.0.1"
        or base.port != LOOPBACK_PORT
        or base.path
        or base.username is not None
        or base.password is not None
        or base.query
        or base.fragment
    ):
        raise SystemExit("refusing endpoint outside numeric loopback 127.0.0.1:11499")
    endpoint = f"http://127.0.0.1:{LOOPBACK_PORT}/api/chat"
    if args.timeout <= 0 or args.timeout > 180:
        raise SystemExit("timeout must be in (0, 180]")
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    prompt = json.dumps(fixture, sort_keys=True, separators=(",", ":"))
    request_value = {
        "model": args.model,
        "stream": False,
        "think": args.think,
        "messages": [{"role": "user", "content": prompt}],
        "format": {
            "type": "object",
            "properties": {
                "why_fits": {"type": "array", "items": {"type": "string"}},
                "hard_blockers_or_unknowns": {"type": "array", "items": {"type": "string"}},
                "proposed_resume_focus": {"type": "array", "items": {"type": "string"}},
                "focused_questions": {"type": "array", "items": {"type": "string"}},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "why_fits",
                "hard_blockers_or_unknowns",
                "proposed_resume_focus",
                "focused_questions",
                "evidence_refs",
            ],
            "additionalProperties": False,
        },
        "options": {"num_ctx": 4096, "num_predict": 650, "temperature": 0.2},
    }
    body = json.dumps(request_value, separators=(",", ":")).encode("utf-8")
    started = time.monotonic()
    request = Request(endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
    # Do not let ambient proxy variables or redirects turn this bounded local
    # request into hosted/cloud traffic; the server-side policy remains separate.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "127.0.0.1"
    opener = build_opener(ProxyHandler({}), _NoRedirect)
    with opener.open(request, timeout=args.timeout) as response:  # noqa: S310 - exact loopback validated above
        raw = response.read(512_001)
    elapsed = time.monotonic() - started
    if len(raw) > 512_000:
        raise SystemExit("response exceeded local evidence bound")
    payload = json.loads(raw.decode("utf-8"))
    content = payload.get("message", {}).get("content", "")
    try:
        structured = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        structured = None
    result = {
        "endpoint": endpoint,
        "model_requested": args.model,
        "model_expected_digest": MODEL_ALLOWLIST[args.model],
        "elapsed_seconds": round(elapsed, 3),
        "options": request_value["options"],
        "structured_output": structured,
        "structured_output_valid": isinstance(structured, dict),
        "date_like_tokens": re.findall(r"\b(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}\b", content),
        "response": payload,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "elapsed_seconds": round(elapsed, 3), "model": payload.get("model")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
