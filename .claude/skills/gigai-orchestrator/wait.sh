#!/usr/bin/env bash
# Block until a non-heartbeat Run message arrives; auto-ack heartbeat-only deliveries.
RUN="${ORCH_RUN:?set ORCH_RUN to the Run id}"
while true; do
  out=$(orca orchestration check --run $RUN --wait --types worker_done,escalation,question --timeout-ms 3600000 --json 2>&1 | grep -v '_keepalive')
  verdict=$(printf '%s' "$out" | python3 -c "
import sys,json
try: d=json.loads(sys.stdin.read())
except Exception: print('ERR'); sys.exit()
r=d.get('result') or {}
ms=r.get('messages') or []
if r.get('timedOut'): print('TIMEOUT'); sys.exit()
if ms and all(m['type']=='heartbeat' for m in ms): print('HB '+str(r.get('deliveryId')))
else: print('EVENT')")
  case "$verdict" in
    HB*) orca orchestration check --run $RUN --ack "${verdict#HB }" --json >/dev/null 2>&1 ;;
    TIMEOUT) echo "TIMEOUT"; exit 0 ;;
    *) echo "$verdict"; exit 0 ;;
  esac
done
