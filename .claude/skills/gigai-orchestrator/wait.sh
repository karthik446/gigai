#!/usr/bin/env bash
# Block until a non-heartbeat Run message arrives; auto-ack heartbeat-only deliveries.
RUN="${ORCH_RUN:?set ORCH_RUN to the Run id}"
SWEEP="$(dirname "$0")/sweep_workers.py"
MAX_WAITS="${MAX_WAITS:-12}"  # 12 x 5 min = 1 h, then report TIMEOUT
waits=0
while true; do
  python3 "$SWEEP" 2>/dev/null | sed "s/^/[sweep] /"  # close idle finished workers/tabs (IDLE_MIN, default 5)
  out=$(orca orchestration check --run $RUN --wait --types worker_done,escalation,question --timeout-ms 300000 --json 2>&1 | grep -v '_keepalive')
  verdict=$(printf '%s' "$out" | python3 -c "
import sys,json
try: d=json.loads(sys.stdin.read())
except Exception: print('ERR'); sys.exit()
r=d.get('result') or {}
ms=r.get('messages') or []
if r.get('timedOut'): print('TIMEOUT'); sys.exit()
if not ms: print('EMPTY'); sys.exit()
if ms and all(m['type']=='heartbeat' for m in ms): print('HB '+str(r.get('deliveryId')))
else: print('EVENT')")
  case "$verdict" in
    HB*) orca orchestration check --run $RUN --ack "${verdict#HB }" --json >/dev/null 2>&1 ;;
    EMPTY) ;;  # woke with nothing to read (e.g. a filtered heartbeat): keep waiting
    TIMEOUT) waits=$((waits + 1)); [ "$waits" -ge "$MAX_WAITS" ] && { echo "TIMEOUT"; exit 0; } ;;
    *) echo "$verdict"; exit 0 ;;
  esac
done
