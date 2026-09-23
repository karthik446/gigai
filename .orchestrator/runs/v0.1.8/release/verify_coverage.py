import subprocess, sys, re
from pathlib import Path

REPO = Path("/Users/kar/orca/workspaces/gigai/gigai-v0.1.8")
PLAN = Path("/Users/kar/orca/workspaces/gigai/gigai-v0.1.8/.orchestrator/runs/v0.1.8/release/commit-plan.md")

# Get canonical file list from git status
out = subprocess.run(["git", "status", "--porcelain=v1", "--untracked-files=all"],
                      cwd=REPO, capture_output=True, text=True, check=True).stdout
all_files = set()
for line in out.splitlines():
    code = line[:2]
    path = line[3:]
    all_files.add(path)

print(f"Total files in git status: {len(all_files)}")

# Parse commit plan: look for lines starting with "- `path`" under each commit's Files section
text = PLAN.read_text()
assigned = []
for m in re.finditer(r"^- `([^`]+)`", text, re.MULTILINE):
    assigned.append(m.group(1))

assigned_set = set(assigned)
dupes = [p for p in set(assigned) if assigned.count(p) > 1]
unassigned = all_files - assigned_set
extra = assigned_set - all_files

print(f"Assigned (unique): {len(assigned_set)}")
print(f"Assigned (total mentions): {len(assigned)}")
print(f"Duplicates: {len(dupes)}")
for d in dupes[:20]:
    print(f"  DUP: {d}")
print(f"Unassigned (in git status but not in plan): {len(unassigned)}")
for u in sorted(unassigned)[:50]:
    print(f"  MISSING: {u}")
print(f"Extra (in plan but not in git status): {len(extra)}")
for e in sorted(extra)[:50]:
    print(f"  EXTRA: {e}")
