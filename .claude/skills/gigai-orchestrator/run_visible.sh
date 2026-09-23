#!/bin/bash
# Run a command in a named, visible Orca tab; tee to a workpad log; end with an EXIT marker.
#   run_visible.sh "TEST make test" make test
#   run_visible.sh "LOCAL claim P3" python3 .claude/skills/gigai-orchestrator/local_check.py ask ...
# Prints the log path. Wait with:  until grep -q '^=== EXIT' <log>; do sleep 10; done
set -euo pipefail
if [ "${1:-}" = "--inner" ]; then            # runs inside the tab, always under bash
  log="$2"; shift 2
  set +e
  "$@" 2>&1 | tee "$log"
  echo "=== EXIT ${PIPESTATUS[0]} ===" | tee -a "$log"
  exit 0
fi
title="$1"; shift
self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
root="$(git rev-parse --show-toplevel)"
slug="$(printf '%s' "$title" | tr -cs 'A-Za-z0-9' '-' | tr 'A-Z' 'a-z')"
log="$root/.orchestrator/logs/$(date +%H%M%S)-${slug%-}.log"
mkdir -p "$root/.orchestrator/logs"
inner="cd $(printf '%q' "$root") && bash $(printf '%q' "$self") --inner $(printf '%q' "$log") $(printf '%q ' "$@")"
"${ORCA_CLI_COMMAND:-orca}" terminal create --worktree current --title "$title" --command "$inner" --json >/dev/null
echo "$log"
