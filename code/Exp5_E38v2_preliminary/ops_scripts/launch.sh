#!/bin/bash
# Launch a live runner detached. Usage: launch.sh RUN_ID SCHEDULE_FILE [LOCK_FILE|--unlocked-smoke]
set -euo pipefail
RUN_ID=$1; SCHED=$2; LOCKARG=${3:-}
E=~/e38v2_20260926
R=$E/results/$RUN_ID
T=$E/.tokens_$RUN_ID
mkdir -p $R
if [ "$LOCKARG" = "--unlocked-smoke" ]; then L="--unlocked-smoke"; else L="--lock $LOCKARG"; fi
cd $E/src
setsid nohup $E/venv_runner/bin/python e38v2_runner.py --run-id $RUN_ID --namespace e38v2-$RUN_ID \
  --context kind-e38v2 --api-server "$(cat $T/server.txt)" --ca-file $T/ca.crt --token-dir $T \
  --schedule $SCHED --output-dir $R $L --ack-disposable > $R/runner_stdout.log 2>&1 < /dev/null &
echo "launched pid $!"
sleep 5
tail -5 $R/runner.log 2>/dev/null; tail -5 $R/runner_stdout.log
