#!/bin/bash
# Progress / host status of an E39 run. Usage: status.sh RUN_ID
RUN_ID=$1
E=~/e39_20260926
R=$E/results/$RUN_ID
date -u +%FT%TZ
if [ -f $R/.runner.pid ] && kill -0 $(cat $R/.runner.pid) 2>/dev/null; then echo "runner RUNNING pid $(cat $R/.runner.pid)"; else echo "runner NOT running"; fi
python3 - $R <<'PY'
import json, sys, collections
r = sys.argv[1]
c = collections.Counter(); inf = collections.Counter(); n = 0
try:
    for l in open(r + '/RAW_TRIALS.jsonl'):
        if not l.strip():
            continue
        d = json.loads(l); n += 1
        c[(d['experiment'], d['stage'])] += 1
        if d['status'] == 'INFRA_ERROR':
            inf[d['experiment']] += 1
except FileNotFoundError:
    pass
print('RAW trials', n, dict(sorted(c.items())), 'INFRA', dict(inf))
PY
tail -2 $R/runner.log 2>/dev/null; tail -2 $R/runner_stdout.log 2>/dev/null
free -m | sed -n 2p; uptime
bash ~/e39_ops/podwatch.sh status $RUN_ID | head -1
sudo du -sh $E/results/_audit_host 2>/dev/null; df -h ~ | tail -1
