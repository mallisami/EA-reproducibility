#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

command -v python3 >/dev/null || { echo 'python3 required'; exit 2; }
python3 - <<'PY'
import sys
if sys.version_info < (3,12):
    raise SystemExit('Python >=3.12 is required')
print('python',sys.version.split()[0])
PY

# Ubuntu 24.04 images sometimes omit venv even when python3 exists.
if ! python3 -m venv --help >/dev/null 2>&1; then
  echo 'python3-venv is missing; installing required OS packages...'
  sudo apt-get update
  sudo apt-get install -y python3-venv python3-pip
fi
command -v openssl >/dev/null || {
  echo 'openssl is missing; installing it...'
  sudo apt-get update
  sudo apt-get install -y openssl
}

for pair in runner:requirements-runner.txt analysis:requirements-analysis.txt deploy:requirements-deploy.txt; do
  name=${pair%%:*}; req=${pair#*:}
  rm -rf ".venv_${name}"
  python3 -m venv ".venv_${name}"
  ".venv_${name}/bin/python" -m pip install --upgrade pip
  ".venv_${name}/bin/pip" install -r "shared/$req"
done

# Bounded offline regression suite. Long integration/self-tests are intentionally not
# part of bootstrap; the live smoke/preflight gates exercise the deployment-specific path.
.venv_runner/bin/python -m pytest -q \
  shared/E39_LOCKED_BASE/src/test_e39_commit.py \
  shared/E39_LOCKED_BASE/src/test_e39_mirror.py
.venv_runner/bin/python -m pytest -q \
  shared/E39_LOCKED_BASE/src/test_e39_runner.py::test_schedule_counts_equal_analysis_design \
  shared/E39_LOCKED_BASE/src/test_e39_runner.py::test_schedule_determinism_and_only \
  shared/E39_LOCKED_BASE/src/test_e39_runner.py::test_dry_run_no_network \
  shared/E39_LOCKED_BASE/src/test_e39_runner.py::test_live_rails \
  shared/E39_LOCKED_BASE/src/test_e39_runner.py::test_e39client_namespace_and_seq_annotation_rail \
  shared/E39_LOCKED_BASE/src/test_e39_runner.py::test_arrivals_pod_annotation_path_with_e39client
.venv_analysis/bin/python -m pytest -q shared/E39_LOCKED_BASE/analysis/tests

bash VERIFY_PACKAGE.sh --post-bootstrap

echo 'Python environments, offline regression tests, and package verification PASS.'
