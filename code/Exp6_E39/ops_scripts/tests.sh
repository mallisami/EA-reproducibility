#!/bin/bash
# All offline suites + offline self-test. Usage: tests.sh TAG
TAG=${1:-x}
E=~/e39_20260926
L=~/e39_ops/tests_$TAG.log
cd $E
{
echo "== $(date -u +%FT%TZ) src pytest"
(cd src && ../venv_runner/bin/python -m pytest -q -p no:cacheprovider 2>&1 | tail -3)
echo "== webhook unittest"
(cd webhook && python3 -m unittest discover -s tests 2>&1 | tail -3)
echo "== deploy validate"
(cd deploy && ls tests && python3 tests/validate_deploy.py 2>&1 | tail -3)
echo "== analysis + lock tool"
sh analysis/run_tests.sh 2>&1 | tail -2
echo "== offline self-test"
rm -rf /tmp/e39_selftest
(cd src && ../venv_runner/bin/python e39_runner.py --offline-selftest --output-dir /tmp/e39_selftest > /tmp/e39_selftest.log 2>&1; echo "selftest rc=$?")
python3 -c "import json; d=json.load(open('/tmp/e39_selftest/SELFTEST_SUMMARY.json')); print('selftest trials', d['trials_written'], 'schema_invalid', d['schema_invalid'], 'gate', d['analysis']['completeness_gate']['passed'], 'analysis rc', d['analysis']['exit_code'], 'infra', {k: v['INFRA_ERROR'] for k, v in d['by_experiment'].items()})"
grep -c 'gate_uid+delete_uid' /tmp/e39_selftest/RAW_TRIALS.jsonl
rm -rf $E/src/__pycache__/*pytest* $E/analysis/__pycache__
echo "== $(date -u +%FT%TZ) done"
} > $L 2>&1
cat $L
