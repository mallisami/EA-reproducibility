#!/bin/bash
# grep the apiserver container log for a list of audit IDs and for lines in their time windows
set -uo pipefail
IN=${1:-/tmp/explore1.jsonl}
docker exec e38v2-control-plane sh -c 'cat /var/log/pods/*kube-apiserver*/kube-apiserver/0.log' > /tmp/apisrv_explore.log
ls -la /tmp/apisrv_explore.log
python3 - "$IN" <<'PY'
import json, sys, re, datetime
recs = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
lines = open('/tmp/apisrv_explore.log', errors='replace').read().splitlines()
def ts(l):
    # CRI prefix: 2026-09-26T09:15:58.429054181Z stderr F ...
    try:
        s = l.split(' ', 1)[0]
        d = datetime.datetime.strptime(s[:26], '%Y-%m-%dT%H:%M:%S.%f').replace(tzinfo=datetime.timezone.utc)
        return int(d.timestamp() * 1e6) * 1000
    except Exception:
        return None
for r in recs:
    print('=' * 30, r['audit_id'], r['uid'], 'rtt_ms', round(r['rtt_ms'], 1))
    lo, hi = r['wall_send'] - 2_000_000, r['wall_resp'] + 20_000_000
    for l in lines:
        t = ts(l)
        if t is None or t < lo or t > hi:
            continue
        body = l.split(' ', 3)[3] if l.count(' ') >= 3 else l
        if 'queueset.go' in body or 'apf_' in body and 'httplog' not in body:
            continue
        if ('openapi' in body) or ('leases' in body and 'e38v2' not in body):
            continue
        print(body[:420])
PY
