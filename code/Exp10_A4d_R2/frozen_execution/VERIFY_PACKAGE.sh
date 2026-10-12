#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$ROOT/tools/verify_freeze.py" --root "$ROOT"
python3 "$ROOT/tools/validate_package.py"
# Static syntax/compile checks do not require live Kubernetes.
while IFS= read -r -d '' f; do bash -n "$f"; done < <(find "$ROOT" -type f -name '*.sh' -print0)
python3 -m compileall -q "$ROOT/A4D_CONFIRMATION" "$ROOT/ops" "$ROOT/tools" "$ROOT/shared/E39_LOCKED_BASE/src" "$ROOT/shared/E39_LOCKED_BASE/analysis"
python3 "$ROOT/tools/selftest_instrumentation.py"
python3 "$ROOT/tools/selftest_guardian.py"
python3 - <<'PY' "$ROOT"
import pathlib,sys
root=pathlib.Path(sys.argv[1])
for n in ('requirements-runner.txt','requirements-analysis.txt','requirements-deploy.txt'):
 for i,line in enumerate((root/'shared'/n).read_text().splitlines(),1):
  s=line.strip()
  if not s or s.startswith('#'): continue
  if s.lower().startswith('python '): raise SystemExit(f'invalid pip requirement {n}:{i}: {s}')
print('requirements syntax PASS')
PY
if [[ "${1:-}" == --post-bootstrap ]]; then
 "$ROOT/.venv_runner/bin/python" -m pytest -q "$ROOT/shared/E39_LOCKED_BASE/src/test_e39_commit.py" "$ROOT/shared/E39_LOCKED_BASE/src/test_e39_mirror.py"
 "$ROOT/.venv_analysis/bin/python" -m pytest -q "$ROOT/shared/E39_LOCKED_BASE/analysis/tests"
fi
echo 'PACKAGE VERIFICATION PASS'
