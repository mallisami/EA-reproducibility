#!/bin/bash
# E38v2 RERUN: copy webhook + audit logs into the run dir (sudo only to read root-owned audit files), snapshot the
# pod-event log (complete lines only) and the kube-apiserver container log (read-only docker exec into the
# e38v2 node). Usage: collect_logs.sh RUN_ID [SNAPSHOT_DIR]
set -euo pipefail
RUN_ID=$1; SNAP=${2:-}
E=~/e38v2r_20260926
R=$E/results/$RUN_ID
cp $E/results/_webhook_host/$RUN_ID/webhook_log.jsonl $R/webhook_log.jsonl
sudo sh -c "cat \$(ls -tr $E/results/_audit_host/audit-*.log 2>/dev/null) $E/results/_audit_host/audit.log" > $R/audit.log
docker exec e38v2-control-plane sh -c 'ls -la /var/log/pods/kube-system_kube-apiserver-*/kube-apiserver/' > $R/apiserver_logfiles.txt
docker exec e38v2-control-plane sh -c 'cd /var/log/pods/kube-system_kube-apiserver-*/kube-apiserver/ && for f in $(ls -tr); do case $f in *.gz) zcat $f;; *) cat $f;; esac; done' > $R/apiserver.log
if [ -n "$SNAP" ]; then
  mkdir -p $SNAP
  n=$(wc -l < $R/pod_events.jsonl); head -n $n $R/pod_events.jsonl > $SNAP/pod_events.jsonl
  echo "pod_events snapshot: $n complete lines -> $SNAP/pod_events.jsonl"
fi
sha256sum $R/webhook_log.jsonl $R/audit.log $R/apiserver.log
wc -l $R/webhook_log.jsonl $R/audit.log $R/RAW_TRIALS.jsonl $R/pod_events.jsonl $R/apiserver.log 2>/dev/null || true
cat $R/apiserver_logfiles.txt
ls -la $E/results/_audit_host/
