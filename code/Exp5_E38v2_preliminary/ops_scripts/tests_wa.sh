#!/bin/bash
E=~/e38v2_20260926
cd $E
echo "=== webhook"
(cd webhook && python3 -m unittest discover -s tests 2>&1 | tail -4)
echo "=== analysis"
(cd analysis && E38V2_RUNNER_PY=$E/venv_runner/bin/python ./run_tests.sh 2>&1 | tail -6)
rm -rf $E/analysis/__pycache__ $E/analysis/tests/__pycache__ $E/analysis/fixtures/__pycache__ $E/webhook/__pycache__ $E/webhook/tests/__pycache__ $E/src/__pycache__
