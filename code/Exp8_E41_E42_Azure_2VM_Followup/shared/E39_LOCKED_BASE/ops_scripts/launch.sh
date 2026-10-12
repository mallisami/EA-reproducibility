#!/bin/bash
# Launch the E39 runner detached (pod-event recorder must be running).
# Usage: launch.sh RUN_ID SCHEDULE_FILE LOCK_FILE|--unlocked-smoke [ONLY]
set -euo pipefail
RUN_ID=$1; SCHED=$2; LOCKARG=$3; ONLY=${4:-}
E=~/e39_20260926
R=$E/results/$RUN_ID
T=$E/.tokens_$RUN_ID
mkdir -p $R
PW=$(bash ~/e39_ops/podwatch.sh status $RUN_ID || true)
case "$PW" in "running pid"*) ;; *) echo "pod-event recorder not running"; exit 5;; esac
if [ "$LOCKARG" = "--unlocked-smoke" ]; then L="--unlocked-smoke"; else L="--lock $LOCKARG"; fi
O=""; [ -n "$ONLY" ] && O="--only $ONLY"
cd $E
echo "$(date -u +%FT%TZ) launch $RUN_ID sched=$SCHED lock=$LOCKARG only=$ONLY" >> $R/LAUNCHES.txt
setsid nohup $E/venv_runner/bin/python src/e39_runner.py --run-id $RUN_ID --namespace e39-$RUN_ID \
  --context kind-e39 --api-server "$(cat $T/server.txt)" --ca-file $T/ca.crt --token-dir $T \
  --schedule $SCHED --output-dir $R $L $O --ack-disposable >> $R/runner_stdout.log 2>&1 < /dev/null &
echo "launched pid $!"
echo $! > $R/.runner.pid
sleep 10
tail -3 $R/runner.log 2>/dev/null || true; tail -5 $R/runner_stdout.log
