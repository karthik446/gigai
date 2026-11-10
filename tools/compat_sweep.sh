#!/usr/bin/env bash
# Compatibility sweep (PLAN §1.1): an OLD and a NEW gigai against the public command list and a fixture home.
#
# usage: tools/compat_sweep.sh <old-gigai> <new-gigai> <fixture-home.tar.zst>      (or: make compat-sweep)
#   e.g. tools/compat_sweep.sh "uvx --from gigai==0.1.11.13 gigai" "uv run --locked gigai" \
#          tests/fixtures/homes/0.1.11.13.tar.zst
# OLD and NEW are command strings split on spaces. Runs from the repository root.
#
# Output: `commands: <N>` (the entries of NEW's src/gigai/data/cli/commands.yaml), `old commands: <M>`
# (`$OLD agent-context --json`), then for every command in either list
#   OK <cmd>       `<bin> <cmd> --help` exits 0 on both, no traceback, byte-identical output
#   DIFF <cmd>     the two outputs differ
#   FAIL <cmd>     missing in NEW (an OLD command), NEW fails, or OLD fails / lacks it (a new entry)
#   ALLOW <cmd>    a DIFF, new or removed entry listed in tools/compat_sweep.allow (one command per line)
# then `FAIL stale allow row <x>` for every allow row that matched nothing, the journeys on NEW (on the
# fixture home and again on its operator-sized nested home `op/home`: OK/FAIL journey[home|op] <cmd>),
# and a summary. Exit 1 on any FAIL or DIFF.
#
# Never touches ~/.gigai or ~/Documents/GigAI: every gigai call runs with HOME=<scratch> and
# GIGAI_HOME=<scratch>/home (or <scratch>/home/op and <scratch>/home/op/home), unpacked from the
# tarball by tests/support/fixture_homes.py (which writes the scratch path back into the home).
# `scout sources update` and `scout new` run with the find-jobs test seams
# (GIGAI_SCOUT_FIND_JOBS_TEST_HTTP/_TEST_MODEL: fixture transport and fake model, read by the CLI
# itself, so no board or model is asked) and unpaced like tools/core_flow.py (the op home's saved
# preferences seed ~14,860 catalog boards; at the polite pace that pass alone outlasts the timeout,
# unpaced it takes ~10 s). The scratch dir is deleted on a clean run and kept (path printed) otherwise.
#
# Environment: COMPAT_SWEEP_PYTHON (default python3; stdlib only), COMPAT_SWEEP_JOBS (parallel
# --help runs, default 4), COMPAT_SWEEP_TIMEOUT (seconds per gigai call, default 180; a call over
# it is killed and FAILs), GIGAI_TEST_LATENCY_SCALE (widens `scout run`'s 15 s health wait; default 4).
set -uo pipefail

if [ $# -ne 3 ]; then
  echo "usage: tools/compat_sweep.sh <old-gigai> <new-gigai> <fixture-home.tar.zst>" >&2
  exit 2
fi
OLD=$1 NEW=$2 FIX=$3
case "$FIX" in /*) ;; *) FIX=$PWD/$FIX ;; esac
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
ALLOW=${COMPAT_SWEEP_ALLOW:-$ROOT/tools/compat_sweep.allow}
PYTHON=${COMPAT_SWEEP_PYTHON:-python3}
JOBS=${COMPAT_SWEEP_JOBS:-4}
TIMEOUT=${COMPAT_SWEEP_TIMEOUT:-180}
cd "$ROOT" || exit 2
[ -f "$FIX" ] || { echo "compat_sweep: no fixture home at $FIX" >&2; exit 2; }
[ -f "$ALLOW" ] || { echo "compat_sweep: missing $ALLOW" >&2; exit 2; }

S=$(mktemp -d "${TMPDIR:-/tmp}/compat.XXXXXX") || exit 2
S=$(cd "$S" && pwd -P)
STARTED=()  # the GIGAI_HOMEs whose `scout run` was attempted
finish() {
  local home
  for home in ${STARTED[@]+"${STARTED[@]}"}; do
    HOME=$(dirname "$home") GIGAI_HOME=$home $NEW scout stop --home "$home" --json > /dev/null 2>&1 || true
  done
}
trap finish EXIT

# --- NEW's command list, the fixture home, free ports (before HOME moves) -------------------------
$PYTHON - "$ROOT/src/gigai/data/cli/commands.yaml" "$S/new.txt" <<'EOF' || exit 2
import json, pathlib, sys
commands = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["commands"]  # JSON is valid YAML
pathlib.Path(sys.argv[2]).write_text("".join(f"{name}\n" for name in commands), encoding="utf-8")
print("commands:", len(commands))
EOF
$PYTHON -m tests.support.fixture_homes unpack "$FIX" "$S/home" > /dev/null || exit 2
read -r PORT PORT_OP < <($PYTHON -c '
import socket
sockets = [socket.socket() for _ in range(2)]
for s in sockets:
    s.bind(("127.0.0.1", 0))
print(*(s.getsockname()[1] for s in sockets))') || exit 2
# Every gigai call runs under this: killed (whole process group) after $TIMEOUT s, exit 124.
cat > "$S/limit.py" <<'EOF'
import os, signal, subprocess, sys
seconds, argv = float(sys.argv[1]), sys.argv[2:]
child = subprocess.Popen(argv, start_new_session=True)
try:
    sys.exit(child.wait(timeout=seconds))
except subprocess.TimeoutExpired:
    os.killpg(child.pid, signal.SIGKILL)
    child.wait()
    print(f"compat_sweep: killed after {seconds:g} s: {' '.join(argv)}", file=sys.stderr)
    sys.exit(124)
EOF
LIMIT="$PYTHON $S/limit.py $TIMEOUT"

# uv's cache and managed Pythons (not gigai data) stay where they are when HOME moves.
if command -v uv > /dev/null 2>&1; then
  export UV_CACHE_DIR=${UV_CACHE_DIR:-$(uv cache dir 2> /dev/null)}
  export UV_PYTHON_INSTALL_DIR=${UV_PYTHON_INSTALL_DIR:-$(uv python dir 2> /dev/null)}
fi
export HOME=$S GIGAI_HOME=$S/home COLUMNS=80
export GIGAI_TEST_LATENCY_SCALE=${GIGAI_TEST_LATENCY_SCALE:-4}
unset GIGAI_WORKPAD_ROOT

ok=0 diff=0 fail=0 allow=0
# --- OLD's command list; the sweep covers the union -----------------------------------------------
: > "$S/old.txt"
if $LIMIT $OLD agent-context --json > "$S/old.json" 2> "$S/old.err"; then
  $PYTHON - "$S/old.json" "$S/old.txt" <<'EOF' || { echo "FAIL old agent-context --json -- unreadable"; fail=$((fail + 1)); }
import json, pathlib, sys
commands = [entry["command"] for entry in json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["commands"]]
pathlib.Path(sys.argv[2]).write_text("".join(f"{name}\n" for name in commands), encoding="utf-8")
print("old commands:", len(commands))
EOF
else
  echo "FAIL old agent-context --json -- exit $?, see $S/old.err"; fail=$((fail + 1))
fi
{ cat "$S/new.txt"; grep -Fxv -f "$S/new.txt" "$S/old.txt"; } > "$S/commands.txt"

# --- 1. every command answers --help the same way on both ----------------------------------------
mkdir "$S/help"
help_one() {
  local key=${1// /_} which bin
  for which in OLD NEW; do
    bin=${!which}
    $LIMIT $bin $1 --help > "$S/help/$which.$key" 2>&1
    echo $? > "$S/help/$which.$key.rc"
  done
}
export -f help_one
export S OLD NEW LIMIT
xargs -P "$JOBS" -I{} bash -c 'help_one "$1"' _ {} < "$S/commands.txt"

grep -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' "$ALLOW" > "$S/allow.rows"
: > "$S/allow.used"
allowed() { grep -Fxq -- "$1" "$S/allow.rows" && echo "$1" >> "$S/allow.used"; }
listed() { grep -Fxq -- "$1" "$2"; }
broken() { [ "$(cat "$1.rc")" != 0 ] || grep -q 'Traceback (most recent call last)' "$1"; }
why() {  # why <side> <output file>
  case "$(cat "$2.rc")" in
    0) echo "$1 --help printed a traceback" ;;
    124) echo "$1 --help timed out after ${TIMEOUT} s" ;;
    *) echo "$1 --help exited $(cat "$2.rc")" ;;
  esac
}
while IFS= read -r c; do
  old=$S/help/OLD.${c// /_} new=$S/help/NEW.${c// /_}
  if ! listed "$c" "$S/new.txt"; then
    if allowed "$c"; then echo "ALLOW $c -- missing in NEW"; allow=$((allow + 1))
    else echo "FAIL $c -- missing in NEW"; fail=$((fail + 1)); fi
  elif broken "$new"; then
    echo "FAIL $c -- $(why new "$new")"; fail=$((fail + 1))
  elif ! listed "$c" "$S/old.txt" || broken "$old"; then
    if allowed "$c"; then echo "ALLOW $c -- new entry"; allow=$((allow + 1))
    elif ! listed "$c" "$S/old.txt"; then echo "FAIL $c -- new entry not in tools/compat_sweep.allow"; fail=$((fail + 1))
    else echo "FAIL $c -- $(why old "$old")"; fail=$((fail + 1)); fi
  elif ! cmp -s "$old" "$new"; then
    if allowed "$c"; then echo "ALLOW $c -- --help differs"; allow=$((allow + 1))
    else echo "DIFF $c"; diff=$((diff + 1)); fi
  else
    echo "OK $c"; ok=$((ok + 1))
  fi
done < "$S/commands.txt"
while IFS= read -r row; do
  if ! grep -Fxq -- "$row" "$S/allow.used"; then echo "FAIL stale allow row $row"; fail=$((fail + 1)); fi
done < "$S/allow.rows"

# --- 2. journeys on NEW: the fixture home, then its operator-sized nested home --------------------
journey() {  # journey <label> <name> <command...>
  local label=$1 name=$2 out rc
  shift 2
  out=$S/journey.$label.$(echo "$name" | tr ' /' '__').out
  $LIMIT "$@" > "$out" 2>&1
  rc=$?
  if [ "$rc" = 0 ] && ! grep -q 'Traceback (most recent call last)' "$out"; then
    echo "OK journey[$label] $name"; ok=$((ok + 1))
  else
    case "$rc" in 0) rc="0 with a traceback" ;; 124) rc="124 (timed out after ${TIMEOUT} s)" ;; esac
    echo "FAIL journey[$label] $name -- exit $rc, output in $out"; fail=$((fail + 1))
  fi
}
journeys() {  # journeys <label> <HOME> <GIGAI_HOME> <port>
  local label=$1 port=$4
  export HOME=$2 GIGAI_HOME=$3
  local seams=(env GIGAI_SCOUT_FIND_JOBS_TEST_HTTP=1 GIGAI_SCOUT_FIND_JOBS_TEST_MODEL=1 GIGAI_SCOUT_AUTO_REFRESH=0
    GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS=0 GIGAI_SCOUT_ATS_PROVIDER_FLOORS=0 GIGAI_SCOUT_ATS_CONCURRENCY=8)
  journey "$label" "doctor --json" $NEW doctor --json
  journey "$label" "gigs --json" $NEW gigs --target "$GIGAI_HOME/scout" --json   # the cwd is this repo, not the target
  journey "$label" "scout status --json" $NEW scout status --json
  journey "$label" "scout profile list --json" $NEW scout profile list --json
  journey "$label" "scout jobs list --json" $NEW scout jobs list --json
  journey "$label" "scout sources update --json" "${seams[@]}" $NEW scout sources update --json
  journey "$label" "scout new --json" "${seams[@]}" $NEW scout new --json
  journey "$label" "scout resume folder --json" $NEW scout resume folder --json   # 0.1.11.13 has no `scout resume list`
  STARTED+=("$GIGAI_HOME")
  journey "$label" "scout run --json" $NEW scout run --home "$GIGAI_HOME" --port "$port" --no-browser --json
  journey "$label" "scout stop --json" $NEW scout stop --home "$GIGAI_HOME" --json
}
journeys home "$S" "$S/home" "$PORT"
journeys op "$S/home/op" "$S/home/op/home" "$PORT_OP"
export HOME=$S GIGAI_HOME=$S/home

echo "sweep: $ok OK, $diff DIFF, $fail FAIL, $allow ALLOW"
if [ $((diff + fail)) -ne 0 ]; then
  echo "sweep dir: $S"
  exit 1
fi
STARTED=()
rm -rf "$S"
