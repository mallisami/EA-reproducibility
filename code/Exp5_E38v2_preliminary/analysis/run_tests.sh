#!/usr/bin/env bash
# Offline tests for the E38v2 oracle/analysis (synthetic data only; no cluster access).
set -euo pipefail
cd "$(dirname "$0")"
PY="${PY:-$HOME/e38v2_20260926/venv_analysis/bin/python}"
"$PY" -m pytest -q -p no:cacheprovider tests/ "$@"
