#!/bin/bash
# Copy webhook + audit logs into the run dir (sudo only to read root-owned audit files). Usage: collect_logs.sh RUN_ID
set -euo pipefail
RUN_ID=$1
E=~/e38v2_20260926
R=$E/results/$RUN_ID
cp $E/results/_webhook_host/$RUN_ID/webhook_log.jsonl $R/webhook_log.jsonl
sudo sh -c "cat \$(ls -tr $E/results/_audit_host/audit-*.log 2>/dev/null) $E/results/_audit_host/audit.log" > $R/audit.log
sha256sum $R/webhook_log.jsonl $R/audit.log
wc -l $R/webhook_log.jsonl $R/audit.log $R/RAW_TRIALS.jsonl
ls -la $E/results/_audit_host/
