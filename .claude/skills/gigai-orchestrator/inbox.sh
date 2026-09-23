#!/usr/bin/env bash
# Print all pending Run messages (non-blocking) without acking.
orca orchestration check --run "${ORCH_RUN:?set ORCH_RUN}" --json 2>&1 | python3 -c "
import sys,json
d=json.load(sys.stdin); r=d.get('result') or {}
print('delivery',r.get('deliveryId'),'count',r.get('count'))
for m in r.get('messages',[]):
    p=json.loads(m['payload'] or '{}')
    print('---',m['type'],'|',m['subject'],'|',p.get('taskId'),p.get('outcome',''),p.get('phase',''))
    if m['type']!='heartbeat': print(m['body'])
if not d.get('ok'): print(d)"
