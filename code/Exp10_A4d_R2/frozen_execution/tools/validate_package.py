#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
errs=[]
for name,want,runid in [('MAIN',985,'a4dcr-r2-20261001'),('SMOKE',26,'a4dcs-r2-20261001')]:
 p=ROOT/f'A4D_CONFIRMATION/protocol/SCHEDULE_A4D_{name}.json'; d=json.loads(p.read_text()); t=d['trials']
 if len(t)!=want: errs.append(f'{name} count {len(t)} != {want}')
 if d['run_id']!=runid: errs.append(f'{name} run_id mismatch')
 if set(x.get('arm') for x in t)!={'A4d_commit_direct'}: errs.append(f'{name} contains non-A4d arm')
 asyncs=[x for x in t if str(x.get('condition','')).startswith('async_')]
 ds=sorted({float(x['delta_ms']) for x in asyncs}); expected=[0.0,2.0,5.0,10.0,20.0,50.0,100.0,200.0]
 if ds!=expected: errs.append(f'{name} delay grid {ds}')
 if set(x['condition'] for x in asyncs)!={'async_scope','async_app_policy','async_evidence_last_literal'}: errs.append(f'{name} condition set mismatch')
 if name=='MAIN':
  from collections import Counter
  c=Counter(x['condition'] for x in t)
  if [c[k] for k in ('async_scope','async_app_policy','async_evidence_last_literal','no_write','uid_rebind_control')]!=[312,312,312,39,10]: errs.append(f'MAIN condition counts {c}')
for p in [ROOT/'A4D_CONFIRMATION/protocol/LOCK_A4D_MAIN.json',ROOT/'A4D_CONFIRMATION/protocol/LOCK_A4D_SMOKE.json']:
 d=json.loads(p.read_text());
 if not str(d.get('status','')).startswith('LOCKED'): errs.append(f'{p.name} not locked')
# Retention flags must remain in both installer and finalizer.
for rel in ('01_ENVIRONMENT/server/01_install_k3s_server.sh','01_ENVIRONMENT/server/03_finalize_cluster.sh'):
 s=(ROOT/rel).read_text()
 for x in ('audit-log-maxage=30','audit-log-maxbackup=20','audit-log-maxsize=1024'):
  if x not in s: errs.append(f'{rel} missing {x}')
for rel in ('ops/start_audit_stream.sh','ops/audit_guardian.py','A4D_CONFIRMATION/analysis/validate_audit_evidence.py'):
 if not (ROOT/rel).is_file(): errs.append(f'missing {rel}')
# R2 must keep the Pod-watch fallback wired through guardian, preflight, and final gate.
sg=(ROOT/'ops/start_guardian.sh').read_text(); run=(ROOT/'ops/run_a4d.sh').read_text(); proto=(ROOT/'A4D_CONFIRMATION/protocol/A4D_CONFIRMATION_PROTOCOL.md').read_text()
for token in ('--pod-events', '--podwatch-pid-file'):
 if token not in sg: errs.append(f'start_guardian missing {token}')
if '--pod-events "$RES/pod_events.jsonl" --probe' not in run: errs.append('run_a4d preflight validator missing pod-events input')
if 'exactly one `DELETED` Pod-watch event' not in proto: errs.append('protocol missing R2 successful-DELETE watch fallback rule')
print(json.dumps({'PASS':not errs,'errors':errs},indent=2)); raise SystemExit(0 if not errs else 4)
