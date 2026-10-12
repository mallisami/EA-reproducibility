#!/bin/sh
# E39 offline tests (analysis + lock tool). No cluster access. Temporary files stay under analysis/.pytest_tmp.
set -e
cd "$(dirname "$0")"
E=$(cd .. && pwd)
rm -rf .pytest_tmp
"$E/venv_analysis/bin/python" -m pytest -q -p no:cacheprovider --basetemp "$PWD/.pytest_tmp" tests "$E/protocol/tests" "$@"
rc=$?
rm -rf .pytest_tmp
exit $rc
