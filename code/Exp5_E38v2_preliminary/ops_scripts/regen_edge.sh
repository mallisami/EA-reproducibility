#!/bin/bash
set -e
E=~/e38v2_20260926
cd $E/analysis
PY=$E/venv_analysis/bin/python
$PY fixtures/synth.py --edge-cases-out fixtures/edge_cases
d=fixtures/edge_cases
$PY oracle.py --raw $d/RAW_TRIALS.jsonl --webhook $d/webhook_log.jsonl --audit $d/audit.log --out $d/oracle_expected_output.csv
sha256sum $d/*
(E38V2_RUNNER_PY=$E/venv_runner/bin/python ./run_tests.sh 2>&1 | tail -4)
rm -rf $E/analysis/__pycache__ $E/analysis/tests/__pycache__ $E/analysis/fixtures/__pycache__
