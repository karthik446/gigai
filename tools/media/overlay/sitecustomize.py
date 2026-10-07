"""The media build's fixture-model overlay, loaded only by the demo Scout server's child process.

`demo_home` puts this folder on the child's PYTHONPATH and sets GIGAI_MEDIA_OVERLAY=1. It wraps the
repo's fixture model (`gigai.scout.find_jobs.bindings._test_model_handler`) so the demo's DATA looks
like a model's: a spread of rank scores (the fixture gives every posting 60) and an assessment that
has the posting's four requirements plus a few nice-to-haves (the fixture gives two rows, which the
product rightly calls a thin posting and shows no Fit chip for). The product code and the UI are
untouched; only the fixture's answers change. Nothing here runs outside the media build.
"""

import json
import os

if os.environ.get("GIGAI_MEDIA_OVERLAY") == "1":
    from gigai.scout.find_jobs import bindings

    _original = bindings._test_model_handler

    # Rank by posting title (the digest line reads "pNNN | <title> @ <company> | ..."): flattering, true to the
    # persona (senior Python / Postgres / GCP backend work first, on-site and infra-only roles last).
    RANKS = {
        "Senior Software Engineer, Scheduling": 92,
        "Senior Backend Engineer, Payments": 91,
        "Staff Software Engineer, Fleet Services": 88,
        "Senior Software Engineer, Data Platform": 86,
        "Software Engineer, Billing": 84,
        "Senior Backend Engineer, Search": 81,
        "Platform Engineer, Developer Experience": 78,
        "Software Engineer, Marketplace": 76,
        "Backend Engineer, Records": 72,
        "Platform Engineer": 69,
        "Site Reliability Engineer, Platform": 66,
        "Site Reliability Engineer": 63,
        "Infrastructure Engineer": 58,
    }
    NICE = ("Kubernetes", "Terraform", "Healthcare or utilities domain", "Mentoring junior engineers")
    UNMET = (1, 0, 2)  # nice-to-haves not met, by posting: Fit 92%, 100%, 83%

    def _rank_score(line: str) -> int:
        title = line.split(" | ")[1].split(" @ ")[0].strip() if line.count(" | ") >= 1 else ""
        return RANKS.get(title, 54)

    def handler(request):
        import httpx

        response = _original(request)
        if request.method != "POST" or request.url.path != "/api/chat" or response.status_code != 200:
            return response
        prompt = bindings._test_model_prompt(request)
        payload = response.json()
        try:
            content = json.loads(payload["message"]["content"])
        except (KeyError, ValueError):
            return response
        if bindings.TEST_MODEL_RANK_MARKER in prompt and isinstance(content, list):
            lines = {}
            in_postings = False
            for line in prompt.splitlines():
                if line.startswith("POSTINGS ("):
                    in_postings = True
                    continue
                if in_postings and " | " in line:
                    lines[line.split(" | ", 1)[0].strip()] = line
            for item in content:
                score = _rank_score(lines.get(item["posting_id"], ""))
                item["score"], item["reasons"] = score, [f"fixture score {score}"]
        elif isinstance(content, dict) and content.get("verdict") == "matched_above_threshold" and len(content.get("matrix", [])) == 2:
            titles = sorted((title for title in RANKS if title in prompt), key=len, reverse=True)
            unmet = UNMET[list(RANKS).index(titles[0]) % len(UNMET)] if titles else 0
            met = lambda name, kind, evidence: {"requirement": name, "class": kind, "resume_evidence": [evidence], "status": "met"}  # noqa: E731
            matrix = [
                met("Python", "hard", "Built Python services"),
                met("Postgres, including schema changes on live tables", "hard", "Ran Postgres migrations on live tables"),
                content["matrix"][1],
                met("Clear written communication", "hard", "Wrote the runbooks for the team"),
            ]
            for index, name in enumerate(NICE):
                if index < len(NICE) - unmet:
                    matrix.append(met(name, "nice_to_have", "On the resume"))
                else:
                    matrix.append({"requirement": name, "class": "nice_to_have", "resume_evidence": [], "status": "not_met"})
            content["matrix"] = matrix
        else:
            return response
        payload["message"]["content"] = json.dumps(content, separators=(",", ":"))
        return httpx.Response(200, json=payload, request=request)

    bindings._test_model_handler = handler
