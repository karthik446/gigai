"""Parse commit-plan.md into ordered commits; --apply stages and commits each one."""
import re, subprocess, sys
plan = open(".orchestrator/runs/v0.1.8/release/commit-plan.md").read()
sections = re.split(r"^## Commit (\d+): .*$", plan, flags=re.M)
commits = []
for i in range(1, len(sections), 2):
    n, body = int(sections[i]), sections[i + 1]
    m = re.search(r"\*\*Message:\*\*\s*```[a-z]*\n(.*?)\n```", body, re.S)
    msg = m.group(1).strip() if m else None
    files = re.findall(r"^- `([^`]+)`", body, re.M)
    commits.append((n, msg, files))
apply = "--apply" in sys.argv
for n, msg, files in commits:
    first = (msg or "<NO MESSAGE>").splitlines()[0]
    coauth = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" in (msg or "")
    print(f"{n:>2} files={len(files):>4} coauthor={coauth} :: {first[:90]}")
    if apply:
        if not msg or not files:
            sys.exit(f"commit {n}: missing message or files")
        if not coauth:
            msg += "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
        for k in range(0, len(files), 100):
            subprocess.run(["git", "add", "--all", "--", *files[k:k + 100]], check=True)
        subprocess.run(["git", "commit", "-q", "-m", msg], check=True)
print("total files", sum(len(f) for _, _, f in commits))
