#!/usr/bin/env python3
"""E40 smoke quality gates (protocol section 6). usage: smoke_gate.py oracle_trials_E39.csv RAW_TRIALS.jsonl"""
import csv
import json
import sys
from collections import Counter

rows = list(csv.DictReader(open(sys.argv[1], newline='', encoding='utf-8')))
raw = [json.loads(l) for l in open(sys.argv[2], encoding='utf-8') if l.strip()]
n = len(rows)
infra = sum(r['primary_class'] == 'INFRA_ERROR' for r in rows)
non_infra = [r for r in rows if r['primary_class'] != 'INFRA_ERROR']
failclosed = [r for r in non_infra if r['primary_class'] == 'UNKNOWN' and 'fail_closed' in r['primary_reason']]
unk = [r for r in non_infra if r['primary_class'] == 'UNKNOWN' and r not in failclosed]
cls_rate = 1 - len(unk) / len(non_infra) if non_infra else 0
ctrl_bad = []
for r in rows:
    arm, cond, pc = r['arm'], r['condition'], r['primary_class']
    if cond == 'no_write':
        ok = pc != 'INVALID_EXECUTED' and (pc == 'VALID_EXECUTED' if arm != 'A4d_commit_direct' else pc in ('VALID_EXECUTED', 'FALSE_ABORT'))
        if not ok:
            ctrl_bad.append((r['trial_id'], arm, cond, pc, r['primary_reason']))
    if cond == 'uid_rebind_control' and pc != 'UID_BLOCKED':
        ctrl_bad.append((r['trial_id'], arm, cond, pc, r['primary_reason']))
res = {'n': n, 'infra': infra, 'infra_rate': infra / n if n else None, 'gate_infra_lt_2pct': n and infra / n < 0.02,
       'unknown_non_failclosed': len(unk), 'classification_rate': cls_rate, 'gate_class_ge_98pct': cls_rate >= 0.98,
       'controls_bad': ctrl_bad, 'gate_controls': not ctrl_bad,
       'primary_classes': dict(Counter('%s|%s|%s' % (r['arm'], r['condition'], r['primary_class']) for r in rows).most_common()),
       'unknown_reasons': dict(Counter(r['primary_reason'] for r in unk)),
       'infra_errors': [(t['trial_id'], (t.get('error') or '')[:200]) for t in raw if t.get('status') == 'INFRA_ERROR'][:10],
       'action_rv_sources': dict(Counter(r['action_rv_source'] for r in rows if r['action_rv_source']))}
res['PASS'] = bool(res['gate_infra_lt_2pct'] and res['gate_class_ge_98pct'] and res['gate_controls'])
print(json.dumps({str(k): v for k, v in res.items()}, indent=1, default=str))
