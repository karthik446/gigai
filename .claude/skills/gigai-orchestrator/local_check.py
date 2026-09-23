#!/usr/bin/env python3
"""Ask a LOCAL Ollama model a verification question and log how it did.

  local_check.py ask  --kind claim --q "question" [--files a.py b.md] [--stdin]
                      [--model muse-glimmer] [--think low] [--ctx 65536] [--ref P3-trace]
  local_check.py grade <id> right|wrong|partial "what it got right or wrong"
  local_check.py stats

Kinds: claim, logic, count, bulk-read, hallucination, lint-triage.
Every ask logs a row with verdict "pending"; the coordinator grades it after
cross-checking. Answers are leads, not evidence. Local-only: refuses cloud
models and non-local Ollama hosts.
"""
import argparse, hashlib, json, os, subprocess, sys, time, urllib.parse, urllib.request, uuid
from collections import Counter, defaultdict

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
if not OLLAMA.startswith("http"):
    OLLAMA = "http://" + OLLAMA
SYSTEM = ("You are a code and document verifier. Answer only from the supplied files. "
          "Cite file:line for every claim. If the files do not settle the question, say "
          "UNSURE and name what is missing. Be brief.")
KINDS = ["claim", "logic", "count", "bulk-read", "hallucination", "lint-triage"]


def log_path():
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                          text=True).stdout.strip() or "."
    return os.environ.get("GIGAI_LOCAL_LOG", os.path.join(root, ".orchestrator/local-models.jsonl"))


def append(row):
    with open(log_path(), "a") as f:
        f.write(json.dumps(row) + "\n")


def require_local(model):
    host = urllib.parse.urlparse(OLLAMA).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        sys.exit(f"refused: OLLAMA_HOST {OLLAMA} is not local")
    if "cloud" in model.lower():
        sys.exit(f"refused: {model} is a cloud model (prompts would leave this machine)")
    tags = json.load(urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=10))["models"]
    name = model if ":" in model else model + ":latest"
    entry = next((m for m in tags if m["name"] == name), None)
    if entry is None:
        sys.exit(f"refused: {model} is not pulled locally (ollama list)")
    if entry.get("remote_host") or entry.get("remote_model") or entry.get("size", 0) < 100_000_000:
        sys.exit(f"refused: {model} has no local weights")


def ask(a):
    require_local(a.model)
    parts, files = [], []
    for p in a.files or []:
        text = open(p).read()
        numbered = "\n".join(f"{i}: {l}" for i, l in enumerate(text.splitlines(), 1))
        parts.append(f"=== {p} ===\n{numbered}")
        files.append({"path": p, "sha1": hashlib.sha1(text.encode()).hexdigest()[:12]})
    if a.stdin:
        parts.append("=== stdin ===\n" + sys.stdin.read())
    prompt = "\n\n".join(parts + [f"QUESTION: {a.q}"])
    think = False if a.think == "false" else a.think
    print(f">> {time.strftime('%H:%M:%S')} {a.kind} {a.ref}: {a.model} think={a.think} "
          f"ctx={a.ctx} ~{len(prompt) // 4} tokens in, waiting for answer...", flush=True)
    body = {"model": a.model, "stream": False, "think": think,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": prompt}],
            "options": {"num_ctx": a.ctx, "temperature": 0, "seed": 42}}
    t0 = time.time()
    req = urllib.request.Request(f"{OLLAMA}/api/chat", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=a.timeout))
    wall = round(time.time() - t0, 2)
    pin = r.get("prompt_eval_count", 0)
    truncated = pin >= int(a.ctx * 0.95)
    answer = r["message"]["content"].strip()
    row = {"id": uuid.uuid4().hex[:8], "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "model": a.model, "kind": a.kind, "think": a.think, "ctx": a.ctx, "ref": a.ref,
           "q": a.q, "files": files, "prompt_chars": len(prompt), "prompt_tokens": pin,
           "output_tokens": r.get("eval_count"), "wall_s": wall,
           "done_reason": r.get("done_reason"), "truncated": truncated,
           "answer": answer, "verdict": "pending"}
    append(row)
    print(f"[{row['id']}] {a.model} think={a.think} {wall}s in={pin} out={row['output_tokens']}"
          f" done={row['done_reason']}" + ("  !! PROMPT TRUNCATED: raise --ctx" if truncated else ""),
          flush=True)
    print(answer, flush=True)


def grade(a):
    append({"grade_of": a.id, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "verdict": a.verdict, "note": a.note})
    print(f"graded {a.id}: {a.verdict}")


def stats(_):
    asks, grades = {}, {}
    for line in open(log_path()):
        row = json.loads(line)
        if "grade_of" in row:
            grades[row["grade_of"]] = row
        else:
            asks[row["id"]] = row
    table, walls = defaultdict(Counter), defaultdict(list)
    for i, row in asks.items():
        key = (row["model"], row.get("kind", "?"))
        table[key][grades.get(i, {}).get("verdict", "pending")] += 1
        walls[key].append(row["wall_s"])
    for (model, kind), c in sorted(table.items()):
        w = sorted(walls[(model, kind)])
        print(f"{model:14} {kind:13} {dict(c)}  median {w[len(w) // 2]}s")
    misses = [(i, g) for i, g in grades.items() if g["verdict"] != "right"][-5:]
    for i, g in misses:
        print(f"  miss {i} {asks.get(i, {}).get('model', '?')} {g['verdict']}: {g['note']}")


p = argparse.ArgumentParser()
s = p.add_subparsers(dest="cmd", required=True)
x = s.add_parser("ask"); x.set_defaults(fn=ask)
x.add_argument("--q", required=True); x.add_argument("--kind", required=True, choices=KINDS)
x.add_argument("--files", nargs="*"); x.add_argument("--stdin", action="store_true")
x.add_argument("--model", default="muse-glimmer")
x.add_argument("--think", default="low", choices=["false", "low", "medium", "high", "xhigh", "max"])
x.add_argument("--ctx", type=int, default=65536); x.add_argument("--ref", default="")
x.add_argument("--timeout", type=int, default=900)
g = s.add_parser("grade"); g.set_defaults(fn=grade)
g.add_argument("id"); g.add_argument("verdict", choices=["right", "wrong", "partial"])
g.add_argument("note")
s.add_parser("stats").set_defaults(fn=stats)
a = p.parse_args(); a.fn(a)
