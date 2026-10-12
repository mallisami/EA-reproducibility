#!/usr/bin/env bash
# Create a fresh Python 3.12 virtual environment for the reproduction levels L0-L2.
# Usage (from the package root):  bash environment/setup_python_env.sh [VENV_DIR]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${1:-$HERE/../.venv}"
PY="${PYTHON:-python3.12}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
"$PY" - <<'EOF'
import sys
v = sys.version_info
print("Python", sys.version.split()[0])
if (v.major, v.minor) != (3, 12):
    print("WARNING: the analysis was run with Python 3.12.3; other versions are untested", file=sys.stderr)
EOF
"$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip >/dev/null
"$VENV/bin/python" -m pip install -r "$HERE/requirements/requirements-analysis.txt"
"$VENV/bin/python" -m pip freeze > "$VENV/pip_freeze_installed.txt"
echo "venv ready: $VENV   (activate with: . $VENV/bin/activate)"
