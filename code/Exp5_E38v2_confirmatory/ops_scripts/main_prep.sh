#!/bin/bash
# Main run preparation: setup, pod-event recorder, probes, RV-order probe + schedule + LOCK. Usage: main_prep.sh RUN_ID SEED
set -uo pipefail
RUN_ID=$1; SEED=$2
cd ~/e38v2r_ops
echo "=== setup $(date -u +%FT%TZ)"; bash setup_run.sh $RUN_ID || { echo SETUP_FAILED; exit 1; }
echo "=== podwatch $(date -u +%FT%TZ)"; bash podwatch.sh start $RUN_ID
echo "=== probes $(date -u +%FT%TZ)"; bash probes.sh $RUN_ID
echo "=== lock $(date -u +%FT%TZ)"; bash lock_stage1.sh $RUN_ID $SEED || { echo LOCK_FAILED; exit 2; }
echo "=== done $(date -u +%FT%TZ)"
