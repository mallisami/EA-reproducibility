#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
MODE=${1:-}

python3 tools/verify_freeze.py --root "$ROOT"

# Shell syntax.
while IFS= read -r -d '' f; do bash -n "$f"; done < <(find . -type f -name '*.sh' -print0)

# Python syntax without importing live-cluster modules.
python3 -m compileall -q tools E41_MULTIHOST_REPLICATION E42_BENIGN_CHURN ops shared/E39_LOCKED_BASE/src shared/E39_LOCKED_BASE/analysis

# Frozen schedule + lock invariants and exact counts.
python3 - <<'PY'
import json
from pathlib import Path
import sys
root=Path('.').resolve()
sys.path.insert(0,str(root/'tools'))
from lock_util import verify_lock
cases=[
 ('E41 smoke','E41_MULTIHOST_REPLICATION/protocol/SCHEDULE_E41_SMOKE.json','E41_MULTIHOST_REPLICATION/protocol/LOCK_E41_SMOKE.json',78),
 ('E41 main','E41_MULTIHOST_REPLICATION/protocol/SCHEDULE_E41_MAIN.json','E41_MULTIHOST_REPLICATION/protocol/LOCK_E41_MAIN.json',2955),
 ('E42 smoke','E42_BENIGN_CHURN/protocol/SCHEDULE_E42_SMOKE.json','E42_BENIGN_CHURN/protocol/LOCK_E42_SMOKE.json',18),
 ('E42 main','E42_BENIGN_CHURN/protocol/SCHEDULE_E42_MAIN.json','E42_BENIGN_CHURN/protocol/LOCK_E42_MAIN.json',702),
]
for name,srel,lrel,n in cases:
    d=json.loads((root/srel).read_text())
    trials=d.get('trials',[])
    if len(trials)!=n: raise SystemExit(f'{name}: expected {n}, got {len(trials)}')
    if len({t['trial_id'] for t in trials})!=n: raise SystemExit(f'{name}: duplicate trial_id')
    verify_lock(root/lrel,root,root/srel,d['run_id'],d['schedule_seed'])
print('schedule/lock validation PASS')
PY

# Requirements files must be parseable as PEP 508 requirements (comments/blank lines ignored).
python3 - <<'PY'
from pathlib import Path
try:
    from packaging.requirements import Requirement
except Exception:
    print('packaging module unavailable; requirement syntax check deferred until bootstrap')
    raise SystemExit(0)
for rel in ['shared/requirements-runner.txt','shared/requirements-analysis.txt','shared/requirements-deploy.txt']:
    for i,line in enumerate(Path(rel).read_text().splitlines(),1):
        x=line.strip()
        if not x or x.startswith('#'): continue
        try: Requirement(x)
        except Exception as e: raise SystemExit(f'{rel}:{i}: invalid requirement {x!r}: {e}')
print('requirements syntax PASS')
PY

if [[ "$MODE" == "--post-bootstrap" ]]; then
  [[ -x .venv_deploy/bin/python ]] || { echo 'deploy venv missing' >&2; exit 3; }
  TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
  .venv_deploy/bin/python ops/render_multihost.py verify-render "$TMP/render" >/dev/null
  .venv_deploy/bin/python - <<PY
import yaml, pathlib
p=pathlib.Path('$TMP/render/webhook.yaml')
docs=list(yaml.safe_load_all(p.read_text()))
d=[x for x in docs if isinstance(x,dict) and x.get('kind')=='Deployment'][0]
s=d['spec']['template']['spec']
assert s['nodeSelector']=={'e41-role':'workload'}
c=[x for x in s['containers'] if x['name']=='webhook'][0]
assert c['imagePullPolicy']=='IfNotPresent'
assert any(v.get('name')=='log' and 'emptyDir' in v for v in s['volumes'])
assert 'CA_BUNDLE_PLACEHOLDER' not in pathlib.Path('$TMP/render/webhook-config.yaml').read_text()
print('render/certificate validation PASS')
PY
fi

echo 'PACKAGE VERIFICATION PASS'
