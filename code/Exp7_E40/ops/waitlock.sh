#!/usr/bin/env bash
# wait (<= ~9.5 min) for the E39 LOCK; print status/seed/run_id
P=$HOME/e39_20260926/protocol
for i in $(seq 1 19); do
  if ls $P | grep -q '^LOCK_E39.*json'; then
    sleep 20; ls -la $P | grep -i lock
    python3 -c "import json;L=json.load(open('$P/LOCK_E39.json'));print(L.get('status'),L.get('schedule_seed'),L.get('run_id'),len(L.get('files') or ()))"
    date -u; exit 0
  fi
  sleep 30
done
date -u; ls $HOME/e39_20260926/results/ ; ls $HOME/e39_20260926/results/e39-20260926t1515z 2>/dev/null
