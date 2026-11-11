#!/usr/bin/env bash
# Build the baseline fixture home tests/fixtures/homes/0.1.11.13.{tar.zst,counts} with the RELEASED gigai.
#
# usage: HOME=/private/tmp/<empty-scratch-dir> bash tools/build_fixture_home.sh      (or: make fixture-home)
#
# Everything is synthetic and lives under $HOME, which must be an empty (or absent) directory under
# /private/tmp or $TMPDIR: the real ~/.gigai and ~/Documents/GigAI are never read or written.
#   1. `gigai setup` (offline fake-model endpoint, as tests/support/setup_home.py) -> $HOME/home
#   2. `gigai scout install` (default target $HOME/home/scout)
#   3. tests/support/operator_home.py at $HOME/home/op (PLAN §1.2: 300 postings, 50 companies; it
#      builds its own nested home there), run under the released wheel, not the worktree's src/
#   4. seal (tests/support/fixture_homes.py): the build path becomes a token, runtime-discovery
#      snapshots are dropped, and the build fails if any machine-specific string is left
#   5. .counts (every SQLite file -> table -> rows) and a deterministic tar --zstd of $HOME/home
# PATH is cut to the system dirs for the gigai calls so setup's runtime discovery finds no local
# agent CLI (claude/codex): the packed config.toml does not depend on the build machine.
set -euo pipefail

VERSION=0.1.11.13
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
OUT=$ROOT/tests/fixtures/homes
ZEROS=0000000000000000000000000000000000000000000000000000000000000000

die() { echo "build_fixture_home: $*" >&2; exit 2; }

# --- refuse anything that is not a scratch HOME --------------------------------------------------
REAL_HOME=$(eval echo "~$(id -un)")
[ -n "${HOME:-}" ] || die "HOME is not set"
case "$HOME" in /*) ;; *) die "HOME must be absolute: $HOME" ;; esac
case "$HOME" in *..*) die "HOME must not contain '..': $HOME" ;; esac
under_scratch() {  # strictly below /tmp, /private/tmp or $TMPDIR (as given or resolved); `/` or a missing TMPDIR adds no base
  local base
  for base in /tmp /private/tmp ${TMPDIR:+"$TMPDIR"} ${TMPDIR:+"$(cd "$TMPDIR" 2> /dev/null && pwd -P)"}; do
    [ -n "${base%/}" ] || continue
    case "$1/" in "${base%/}"/?*) return 0 ;; esac
  done
  return 1
}
if ! under_scratch "$HOME" || [ "$HOME" = "$REAL_HOME" ]; then
  die "refusing HOME=$HOME: set HOME to an empty scratch dir under /private/tmp or \$TMPDIR"
fi
if [ -e "$HOME" ] && [ -n "$(ls -A "$HOME")" ]; then
  die "HOME=$HOME is not empty; start from an empty scratch dir"
fi
mkdir -p "$HOME"
HOME=$(cd "$HOME" && pwd -P)
under_scratch "$HOME" || die "refusing HOME=$HOME: it resolves outside the scratch dirs"
export HOME
H=$HOME/home

# uv's own cache and managed Pythons (not gigai data) stay where they are, so uvx reaches only PyPI.
UVX=$(command -v uvx) || die "uvx not found"
UV=$(command -v uv) || die "uv not found"
export UV_CACHE_DIR=${UV_CACHE_DIR:-$(HOME=$REAL_HOME "$UV" cache dir)}
export UV_PYTHON_INSTALL_DIR=${UV_PYTHON_INSTALL_DIR:-$(HOME=$REAL_HOME "$UV" python dir)}
while IFS= read -r name; do unset "$name"; done < <(env | sed -n 's/^\(GIGAI_[A-Za-z0-9_]*\)=.*/\1/p')
CALLER_PATH=$PATH  # tar --zstd may need an external zstd (bsdtar); only the gigai calls get the cut PATH
export PATH=/usr/bin:/bin:/usr/sbin:/sbin

GIGAI=("$UVX" --from "gigai==$VERSION" gigai)
GIGAI_PY=("$UVX" --from "gigai==$VERSION" python)

echo "== gigai $VERSION setup -> $H"
"${GIGAI[@]}" setup --non-interactive --home "$H" --workpad-root "$H/workpads" --editor /usr/bin/true \
  --endpoint ollama_local=ollama_local:http://127.0.0.1:11434 \
  --model-target "ollama_local=ollama_local:scout-test:latest@sha256:$ZEROS" \
  --create-model-target ollama_local --json > "$HOME/setup.json"

echo "== gigai $VERSION scout install"
"${GIGAI[@]}" scout install --home "$H" --username fixture-owner --json > "$HOME/scout-install.json"

echo "== operator_home $H/op (300 postings, 50 companies)"
(cd "$ROOT" && "${GIGAI_PY[@]}" -m tests.support.operator_home "$H/op" --postings 300 --companies 50 \
  > "$HOME/operator-home.out" 2> "$HOME/operator-home.err") \
  || { tail -n 20 "$HOME/operator-home.err" >&2; die "operator_home failed"; }

echo "== seal"
forbidden=("$HOME" "$REAL_HOME" /Users/ /private/tmp /private/var/folders karthik "/$(id -un)/")
host=$(hostname 2>/dev/null || true)
[ -n "$host" ] && forbidden+=("$host")
(cd "$ROOT" && "${GIGAI_PY[@]}" -m tests.support.fixture_homes seal "$H" "${forbidden[@]}")

echo "== counts"
mkdir -p "$OUT"
(cd "$ROOT" && "${GIGAI_PY[@]}" -m tests.support.fixture_homes counts "$H") > "$OUT/$VERSION.counts"
# Opening the WAL stores leaves -shm (the WAL index) and empty -wal files: not home data, not packed.
if [ -n "$(find "$H" -name '*-wal' -size +0c)" ]; then die "a -wal file holds data after seal"; fi
find "$H" \( -name '*-wal' -o -name '*-shm' \) -delete

echo "== pack"
(cd "$H" && find . -mindepth 1 | LC_ALL=C sort) > "$HOME/pack.list"
find "$H" -exec touch -h -t 202601010000 {} +
export PATH=$CALLER_PATH
if tar --version 2>/dev/null | grep -q 'GNU tar'; then
  owner=(--owner=0 --group=0 --numeric-owner --no-recursion)
else
  owner=(--uid 0 --gid 0 --uname '' --gname '' -n)
fi
COPYFILE_DISABLE=1 tar --zstd -cf "$OUT/$VERSION.tar.zst.tmp" "${owner[@]}" -C "$H" -T "$HOME/pack.list"
mv "$OUT/$VERSION.tar.zst.tmp" "$OUT/$VERSION.tar.zst"

echo "tarball: tests/fixtures/homes/$VERSION.tar.zst ($(wc -c < "$OUT/$VERSION.tar.zst" | tr -d ' ') bytes, $(wc -l < "$HOME/pack.list" | tr -d ' ') entries)"
echo "counts:  tests/fixtures/homes/$VERSION.counts ($(wc -l < "$OUT/$VERSION.counts" | tr -d ' ') tables)"
echo "scratch: $HOME (safe to delete)"
