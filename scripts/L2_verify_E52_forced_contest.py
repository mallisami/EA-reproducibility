#!/usr/bin/env python3
import filecmp, os, shutil, subprocess, sys, tarfile, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments'/'Exp17_E52_Forced_Contest_Replication'
TAR=EXP/'raw'/'E52_RESULTS.tar.gz'
CODE=ROOT/'code'/'Exp17_E52_Forced_Contest_Replication'/'analyze_e52_with_frozen_e39_oracle.py'
ORACLE=ROOT/'code'/'Exp6_E39'/'analysis'
CAN=EXP/'results'
NEEDED=[
 'E39_LOCKED_BASE/results/e52/RAW_TRIALS.jsonl',
 'E39_LOCKED_BASE/results/e52/audit.log',
 'E39_LOCKED_BASE/results/e52/webhook_log.jsonl',
 'E39_LOCKED_BASE/results/e52/pod_events.jsonl',
]
with tempfile.TemporaryDirectory(prefix='ea_e52_') as td:
    td=Path(td)
    with tarfile.open(TAR,'r:gz') as tf:
        names=set(tf.getnames())
        miss=[x for x in NEEDED if x not in names]
        if miss: raise SystemExit(f'MISSING archive members: {miss}')
        for x in NEEDED:
            try:
                tf.extract(x, td, filter='data')
            except TypeError:  # Python < 3.12 compatibility
                tf.extract(x, td)
    run=td/'E39_LOCKED_BASE'/'results'/'e52'
    out=td/'out'
    subprocess.check_call([sys.executable,str(CODE),str(run),str(ORACLE),str(out)])
    files=['E52_frozen_oracle_summary.json','E52_frozen_oracle_cells.csv']
    ok=True
    for f in files:
        same=filecmp.cmp(out/f,CAN/f,shallow=False)
        print(f'{f}:', 'PASS byte-identical' if same else 'FAIL')
        ok &= same
    if not ok: raise SystemExit(1)
print('E52 forced-contest replication: PASS')
