#!/bin/bash
set -euo pipefail
E=~/e38v2_20260926
RUN_ID=$1; SEED=$2
cd $E/src
$E/venv_runner/bin/python e38v2_runner.py --make-schedule $E/results/$RUN_ID/SCHEDULE.json --seed $SEED --run-id $RUN_ID --reps 1 | head -30
sha256sum $E/results/$RUN_ID/SCHEDULE.json
