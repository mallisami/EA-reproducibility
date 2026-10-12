#!/bin/bash
# E39 main driver (detached): stage 1 per sub-experiment in the order A G E F B D C, then the E39-A stage-2
# top-up per ANALYSIS_PLAN section 6. Stops at the first failure (non-zero runner exit, LOCK verify failure).
# Usage: run_all.sh RUN_ID SEED [FIRST_EXPERIMENT]
set -uo pipefail
RUN_ID=$1; SEED=$2; FIRST=${3:-A}
E=~/e39_20260926
R=$E/results/$RUN_ID
T=$E/.tokens_$RUN_ID
PYA=$E/venv_analysis/bin/python
PYR=$E/venv_runner/bin/python
LOG=$R/DRIVER.log
cd $E
log() { echo "$(date -u +%FT%TZ) $*" | tee -a $LOG; }
rm -f $R/DRIVER_DONE
( while [ ! -f $R/DRIVER_DONE ]; do { date -u +%FT%TZ; free -m | sed -n 2p; uptime; } >> $R/host_watch.txt; sleep 600; done ) &
run_x() {
  local X=$1 SCHED=$2
  python3 protocol/lock_hash_e39.py verify protocol/LOCK_E39.json > $R/lock_verify_before_${X}_$(date -u +%H%M%S).txt 2>&1 \
    || { log "LOCK verify FAILED before $X"; return 9; }
  log "start $X ($SCHED)"
  $PYR src/e39_runner.py --run-id $RUN_ID --namespace e39-$RUN_ID --context kind-e39 --api-server "$(cat $T/server.txt)" \
    --ca-file $T/ca.crt --token-dir $T --schedule $SCHED --output-dir $R --lock protocol/LOCK_E39.json --only $X \
    --ack-disposable >> $R/runner_stdout.log 2>&1 < /dev/null
  local rc=$?
  log "end $X rc=$rc; $(tail -1 $R/runner.log)"
  return $rc
}
started=0
for X in A G E F B D C; do
  [ "$X" = "$FIRST" ] && started=1
  [ $started = 1 ] || continue
  if [ -f $R/STOP ]; then log "STOP file present; driver stops before $X"; touch $R/DRIVER_DONE; exit 4; fi
  run_x $X $R/SCHEDULE.json || { log "driver ABORT after $X"; touch $R/DRIVER_DONE; exit 5; }
done
log "stage 1 complete; E39-A stage-1 analysis + stage-2 top-up"
bash ~/e39_ops/collect_logs.sh $RUN_ID $R/stage1_inputs >> $LOG 2>&1
$PYA analysis/analyze_e39.py --run-dir $R --lock protocol/LOCK_E39.json --stage 1 --experiments A \
   --pod-events $R/stage1_inputs/pod_events.jsonl --out $R/analysis_A_stage1 >> $LOG 2>&1
rc=$?; log "analysis A stage 1 rc=$rc"
[ $rc = 0 ] || { touch $R/DRIVER_DONE; exit 6; }
$PYA analysis/stage2_topup_e39.py --results $R/analysis_A_stage1/results_E39.json --out $R/stage2 >> $LOG 2>&1
rc=$?; log "stage2_topup rc=$rc"
[ $rc = 0 ] || { touch $R/DRIVER_DONE; exit 7; }
$PYR src/e39_runner.py --make-schedule $R/SCHEDULE_STAGE2.json --stage 2 --topup-file $R/stage2/stage2_cells_E39.json \
   --seed $SEED --run-id $RUN_ID >> $LOG 2>&1
rc=$?; log "make stage-2 schedule rc=$rc"
[ $rc = 0 ] || { touch $R/DRIVER_DONE; exit 8; }
cp protocol/LOCK_E39.json $R/LOCK_E39_stage1_copy.json
python3 protocol/lock_hash_e39.py add-stage2 protocol/LOCK_E39.json --schedule2 results/$RUN_ID/SCHEDULE_STAGE2.json \
   --topup results/$RUN_ID/stage2/stage2_cells_E39.json >> $LOG 2>&1
rc=$?; log "lock add-stage2 rc=$rc; $(sha256sum protocol/LOCK_E39.json)"
[ $rc = 0 ] || { touch $R/DRIVER_DONE; exit 9; }
python3 -c "import json; d=json.load(open('$R/SCHEDULE_STAGE2.json')); print('stage-2 trials', d.get('n_trials'))" >> $LOG 2>&1
run_x A $R/SCHEDULE_STAGE2.json || { log "driver ABORT in stage 2"; touch $R/DRIVER_DONE; exit 10; }
log "ALL RUNS COMPLETE"
touch $R/DRIVER_DONE
