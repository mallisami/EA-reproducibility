#!/bin/bash
E=~/e38v2r_20260926
cd $E
echo "=== src (runner + pod_watch)"
(cd src && $E/venv_runner/bin/python -m pytest -q -p no:cacheprovider test_e38v2.py test_pod_watch.py 2>&1 | tail -15)
echo "=== webhook"
(cd webhook && python3 -m unittest discover -s tests 2>&1 | tail -6)
echo "=== deploy"
$E/venv_deploy/bin/python deploy/tests/validate_deploy.py 2>&1 | tail -4
echo "=== analysis"
(cd analysis && E38V2_RUNNER_PY=$E/venv_runner/bin/python ./run_tests.sh 2>&1 | tail -25)
echo "=== lock"
(cd protocol && python3 -m unittest discover -s tests 2>&1 | tail -6)
find $E/src $E/analysis $E/webhook $E/protocol $E/deploy -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
true