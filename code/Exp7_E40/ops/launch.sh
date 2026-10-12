#!/usr/bin/env bash
# launch.sh RUN_ID [LOCK]  : copy schedule (+lock) into the runner pod, start pod_watch + runner detached inside it
set -euo pipefail
W=$HOME/e40_20260926; RUN=$1; LOCK=${2:-}
K="nice kubectl --kubeconfig $W/kubeconfig -n e40-system"
P=$($K get pod -l app=e40-runner -o jsonpath='{.items[0].metadata.name}')
$K exec $P -c runner -- mkdir -p /out/$RUN
$K cp $W/results/$RUN/SCHEDULE.json $P:/out/$RUN/SCHEDULE.json -c runner
LA="--unlocked-smoke"
if [ -n "$LOCK" ]; then $K cp $LOCK $P:/out/$RUN/LOCK_E40.json -c runner; LA="--lock /out/$RUN/LOCK_E40.json"; fi
CE=$(cat $W/ops/CLUSTER_CREATE_START_EPOCH)
$K exec $P -c runner -- sh -c "cd /e40 && sha256sum /out/$RUN/SCHEDULE.json && \
  (nohup python e40/e40_podwatch.py --namespace e40-$RUN --out /out/$RUN/pod_events.jsonl --stop-file /out/$RUN/PODWATCH_STOP > /out/$RUN/podwatch_stdout.log 2>&1 &) && sleep 2 && \
  (nohup python e40/e40_main.py --run --schedule /out/$RUN/SCHEDULE.json --run-id $RUN --output-dir /out/$RUN $LA --cluster-created-epoch $CE > /out/$RUN/runner_stdout.log 2>&1; echo EXIT=\$? > /out/$RUN/EXIT) > /dev/null 2>&1 &
  sleep 1; echo launched"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) launched run $RUN in $P (lock: ${LOCK:-none, unlocked smoke})" >> $W/ops/LOG_E40_OPERATIONS.txt
