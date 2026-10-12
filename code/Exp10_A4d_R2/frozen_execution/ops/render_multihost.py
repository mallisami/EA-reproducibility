#!/usr/bin/env python3
from __future__ import annotations
import argparse,subprocess
from pathlib import Path
import yaml
p=argparse.ArgumentParser(); p.add_argument('run_id'); p.add_argument('outdir'); a=p.parse_args()
ROOT=Path(__file__).resolve().parents[1]; BASE=ROOT/'shared'/'E39_LOCKED_BASE'; out=Path(a.outdir).resolve()
subprocess.run([str(BASE/'deploy'/'render.sh'),a.run_id,str(out)],check=True)
wp=out/'webhook.yaml'; docs=list(yaml.safe_load_all(wp.read_text()))
for d in docs:
    if not isinstance(d,dict) or d.get('kind')!='Deployment': continue
    spec=d['spec']['template']['spec']; spec['nodeSelector']={'a4d-role':'workload'}
    for c in spec.get('containers',[]):
        if c.get('name')=='webhook': c['imagePullPolicy']='IfNotPresent'
    for v in spec.get('volumes',[]):
        if v.get('name')=='log': v.pop('hostPath',None); v['emptyDir']={}
wp.write_text('\n---\n'.join(yaml.safe_dump(d,sort_keys=False) for d in docs if d is not None),encoding='utf-8')
subprocess.run([str(BASE/'deploy'/'gen_certs.sh'),str(out)],check=True)
print(f'rendered A4d multi-host manifests in {out}')
