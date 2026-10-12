#!/bin/bash
# E39: copy webhook + audit logs into the run dir (sudo only to READ root-owned audit files of the e39 cluster),
# snapshot the pod-event log (complete lines only) and the kube-apiserver container log (read-only docker exec
# into the e39 node). Usage: collect_logs.sh RUN_ID [SNAPSHOT_DIR]
set -euo pipefail
RUN_ID=$1; SNAP=${2:-}
E=~/e39_20260926
R=$E/results/$RUN_ID
cp $E/results/_webhook_host/$RUN_ID/webhook_log.jsonl $R/webhook_log.jsonl
sudo sh -c "cat \$(ls -tr $E/results/_audit_host/audit-*.log 2>/dev/null) $E/results/_audit_host/audit.log" > $R/audit.log
docker exec e39-control-plane sh -c 'ls -la /var/log/pods/kube-system_kube-apiserver-*/kube-apiserver/' > $R/apiserver_logfiles.txt
docker exec e39-control-plane sh -c 'cd /var/log/pods/kube-system_kube-apiserver-*/kube-apiserver/ && for f in $(ls -tr); do case $f in *.gz) zcat $f;; *) cat $f;; esac; done' > $R/apiserver.log
if [ -f $R/pod_events.jsonl ]; then
  n=$(wc -l < $R/pod_events.jsonl)
  if [ -n "$SNAP" ]; then mkdir -p $SNAP; head -n $n $R/pod_events.jsonl > $SNAP/pod_events.jsonl; echo "pod_events snapshot: $n lines -> $SNAP"; fi
fi
sha256sum $R/webhook_log.jsonl $R/audit.log $R/apiserver.log
wc -l $R/webhook_log.jsonl $R/audit.log $R/RAW_TRIALS.jsonl $R/pod_events.jsonl $R/mirror_log.jsonl $R/apiserver.log 2>/dev/null || true
sudo ls -la $E/results/_audit_host/ | tail -5
