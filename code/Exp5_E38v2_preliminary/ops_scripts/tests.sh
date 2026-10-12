#!/bin/bash
E=~/e38v2_20260926
cd $E
echo "=== src (runner)"
(cd src && $E/venv_runner/bin/python -m pytest -q -p no:cacheprovider test_e38v2.py 2>&1 | tail -15)
echo "=== webhook"
(cd webhook && python3 -m unittest discover -s tests 2>&1 | tail -6)
echo "=== deploy"
$E/venv_deploy/bin/python deploy/tests/validate_deploy.py 2>&1 | tail -4
echo "=== analysis"
(cd analysis && E38V2_RUNNER_PY=$E/venv_runner/bin/python ./run_tests.sh 2>&1 | tail -15)
echo "=== lock"
(cd protocol && python3 -m unittest discover -s tests 2>&1 | tail -6)
find $E -name __pycache__ -path "$E/src/*" -o -name __pycache__ -path "$E/analysis/*" -o -name __pycache__ -path "$E/webhook/*" -o -name __pycache__ -path "$E/protocol/*" | xargs rm -rf
