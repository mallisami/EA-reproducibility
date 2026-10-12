#!/usr/bin/env bash
set -euo pipefail
RUN_ID=${1:?}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RT="$ROOT/runtime/$RUN_ID"; RES="$RT/results"
mkdir -p "$RES"
k(){ sudo k3s kubectl "$@"; }
POD=$(k -n e39-system get pod -l app=e39-webhook -o jsonpath='{.items[0].metadata.name}')
# Read the emptyDir-backed JSONL through Python (python image is guaranteed; no tar/kubectl-cp dependency).
k -n e39-system exec "$POD" -- python -c "import pathlib; p=pathlib.Path('/var/log/e39-webhook/$RUN_ID/webhook_log.jsonl'); print(p.read_text() if p.exists() else '', end='')" \
  > "$RES/webhook_log.jsonl"
# Concatenate audit files oldest->newest. The experiment remains namespace/Audit-ID scoped during analysis.
sudo python3 - <<'PY' > "$RES/audit.log"
from pathlib import Path
p=Path('/var/lib/rancher/k3s/server/logs')
fs=sorted(p.glob('audit.log*'), key=lambda x:x.stat().st_mtime)
for f in fs:
    try:
        print(f.read_text(), end='')
    except UnicodeDecodeError:
        pass
PY
sudo journalctl -u k3s --no-pager > "$RES/k3s_server_journal.log"
k get nodes -o wide --show-labels > "$RES/nodes_at_collection.txt"
k -n e39-system get pod -l app=e39-webhook -o wide > "$RES/webhook_pod_at_collection.txt"
sha256sum "$RES"/* 2>/dev/null | sort > "$RES/RUN_OUTPUTS.sha256"
wc -l "$RES"/*.jsonl "$RES/audit.log" 2>/dev/null || true
echo "collected $RUN_ID -> $RES"
