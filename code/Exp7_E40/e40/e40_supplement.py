#!/usr/bin/env python3
"""E40 supplement (protocol/E40_PROTOCOL.txt sections 4 and 8). Offline; standard library + numpy (bootstrap only).

  1. SECONDARY oracle (client-side timestamps, one node clock) per trial, agreement with the PRIMARY (E39 oracle row).
  2. Per-cell secondary P(INVALID_EXECUTED) with Wilson CIs.
  3. Webhook latency cost: DELETE round trip A3 - A1 (accepted DELETEs): medians, two-sample Hodges-Lehmann shift,
     percentile bootstrap CI (B = 2000, seed 4026); webhook evaluation time (completed - received) from webhook_log.
  4. A4d availability cost: 409 / FALSE_ABORT in no_write and async cells; 409 attribution via pod_events.jsonl
     (Pod revision named in the 409 message == writer PATCH RV -> writer; else a pod-watch MODIFIED event at that RV
     -> non-experiment Pod update (kubelet status); else unattributed).
  5. Comparison with reference kind runs (E38v2 confirmatory rerun cells_E38v2.csv; E39 cells_E39.csv if given):
     per-cell difference p_AKS - p_kind with the Newcombe (1998) method-10 hybrid-score 95% CI.
Usage: e40_supplement.py --run-dir RUN --analysis-dir OUT_OF_analyze_e39 --out DIR
                         [--ref-e38v2r cells_E38v2.csv] [--ref-e39 cells_E39.csv]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

Z = 1.959963984540054
ARM_SHORT = {'A1_dcji': 'A1', 'A3_dcji_webhook': 'A3', 'A4d_commit_direct': 'A4d'}
WRITER_OBJ = {'async_scope': ('response-scope', 'e38.scope'), 'async_evidence_last_literal': ('evidence-a', 'e38.evidence/a')}


def wilson(k, n):
    if not n:
        return (None, None, None)
    p = k / n
    den = 1 + Z * Z / n
    c = (p + Z * Z / (2 * n)) / den
    h = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / den
    return (p, max(0.0, c - h), min(1.0, c + h))


def newcombe(k1, n1, k2, n2):
    p1, l1, u1 = wilson(k1, n1)
    p2, l2, u2 = wilson(k2, n2)
    if p1 is None or p2 is None:
        return None
    d = p1 - p2
    return (d, d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2), d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2))


def load_jsonl(p):
    out = []
    if p and Path(p).is_file():
        for line in Path(p).read_text(encoding='utf-8').splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def read_csv(p):
    with open(p, newline='', encoding='utf-8') as fh:
        return list(csv.DictReader(fh))


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def secondary(t, orow, wh_by_pod):
    """Client-time secondary class for one trial (see protocol section 4)."""
    pc = orow['primary_class']
    cond = t['condition']
    if pc == 'INFRA_ERROR' or cond not in WRITER_OBJ:
        return pc, 'same_as_primary(%s)' % ('infra' if pc == 'INFRA_ERROR' else 'control'), None
    if pc == 'UNKNOWN' and 'fail_closed' in (orow.get('primary_reason') or '') or pc == 'UNKNOWN':
        return 'UNKNOWN', 'primary_unknown(%s)' % orow.get('primary_reason'), None
    w = t.get('writer') or {}
    d = t.get('delete') or {}
    g = t.get('gate') or {}
    if w.get('http_status') is None or not (200 <= int(w['http_status']) < 300):
        return pc, 'writer_not_committed(same_as_primary)', None
    wr = w.get('t_resp_mono_ns')
    if wr is None:
        return 'UNKNOWN', 'writer_t_resp_missing', None
    invalidating = str(orow.get('v_post')) == '0'
    hs = d.get('http_status')
    overlap = None
    if d.get('dispatched') and hs in (200, 202):
        ds, dr = d.get('t_send_mono_ns'), d.get('t_resp_mono_ns')
        ws = w.get('actual_release_mono_ns')
        if ws is not None and ds is not None and dr is not None:
            overlap = bool(ws < dr and wr > ds)
        before = wr < dr
        if before and invalidating:
            return 'INVALID_EXECUTED', 'writer_resp_before_delete_resp(client)', overlap
        return 'VALID_EXECUTED', ('writer_resp_before_delete_resp_not_invalidating' if before
                                  else 'writer_resp_after_delete_resp(client)'), overlap
    if not d.get('dispatched'):
        if g.get('decision') != 'ABORT_REPLAN':
            return 'UNKNOWN', 'not_dispatched(%s)' % g.get('decision'), None
        reads = [r for r in (g.get('reads') or []) if isinstance(r, dict) and r.get('t_send_mono_ns')]
        if t['arm'] == 'A4d_commit_direct':
            ref = reads[0]['t_send_mono_ns'] if reads else None
        else:
            m = [r for r in reads if r.get('object') == WRITER_OBJ[cond][0]]
            ref = m[-1]['t_send_mono_ns'] if m else None
        if ref is None:
            return 'UNKNOWN', 'gate_read_time_missing', None
        seen = wr < ref
        return ('CORRECT_ABORT' if (seen and invalidating) else 'FALSE_ABORT'), 'gate_read_vs_writer_resp(client)', None
    if hs == 409:
        if pc == 'UID_BLOCKED':
            return 'UID_BLOCKED', 'uid_409', None
        dr = d.get('t_resp_mono_ns')
        seen = dr is not None and wr < dr
        return ('CORRECT_ABORT' if (seen and invalidating) else 'FALSE_ABORT'), 'rv409_writer_resp_vs_delete_resp(client)', None
    if hs is not None and 400 <= int(hs) < 500:
        ents = [e for e in wh_by_pod.get(str(d.get('expected_uid') or ''), []) if not e.get('pod_terminating')
                and e.get('decision') == 'deny']
        if not ents:
            return 'UNKNOWN', 'webhook_deny_entry_missing', None
        e = ents[-1]
        m = [r for r in (e.get('reads') or []) if r.get('object') == WRITER_OBJ[cond][0]]
        if not m or not m[-1].get('t_send_wall_ns'):
            return 'UNKNOWN', 'webhook_read_time_missing', None
        off = (g.get('t_end_wall_ns') or 0) - (g.get('t_end_mono_ns') or 0)
        if not g.get('t_end_wall_ns'):
            return 'UNKNOWN', 'wall_offset_missing', None
        seen = (wr + off) < m[-1]['t_send_wall_ns']
        return ('CORRECT_ABORT' if (seen and invalidating) else 'FALSE_ABORT'), 'webhook_read_vs_writer_resp(client_wall)', None
    return 'UNKNOWN', 'action_http_%s' % hs, None


def hl_shift(x, y, reps=2000, seed=4026):
    import numpy as np
    x, y = np.asarray(x, float), np.asarray(y, float)
    if not len(x) or not len(y):
        return None
    est = float(np.median(np.subtract.outer(x, y)))
    rng = np.random.default_rng(seed)
    bs = []
    for _ in range(reps):
        xb = x[rng.integers(0, len(x), len(x))]
        yb = y[rng.integers(0, len(y), len(y))]
        bs.append(np.median(np.subtract.outer(xb, yb)))
    return {'hl_shift_ms': est, 'ci95': [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))],
            'median_x': float(np.median(x)), 'median_y': float(np.median(y)), 'n_x': int(len(x)), 'n_y': int(len(y)),
            'p95_x': float(np.percentile(x, 95)), 'p95_y': float(np.percentile(y, 95))}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-dir', required=True)
    ap.add_argument('--analysis-dir', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--ref-e38v2r')
    ap.add_argument('--ref-e39')
    a = ap.parse_args(argv)
    rd, ad, out = Path(a.run_dir), Path(a.analysis_dir), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    trials = {t['trial_id']: t for t in load_jsonl(rd / 'RAW_TRIALS.jsonl')}
    orows = {int(r['trial_id']): r for r in read_csv(next(ad.glob('oracle_trials_E39.csv')))}
    wh = load_jsonl(rd / 'webhook_log.jsonl')
    wh_by_pod = defaultdict(list)
    for e in wh:
        wh_by_pod[str(e.get('pod_uid') or '')].append(e)
    pev = load_jsonl(rd / 'pod_events.jsonl')
    rows = []
    for tid, t in sorted(trials.items()):
        o = orows.get(int(tid))
        if o is None:
            continue
        sc, sr, ov = secondary(t, o, wh_by_pod)
        rows.append({'trial_id': tid, 'arm': ARM_SHORT.get(t['arm'], t['arm']), 'condition': t['condition'],
                     'delta_ms': t.get('delta_ms'), 'primary_class': o['primary_class'],
                     'primary_reason': o['primary_reason'], 'client_secondary_class': sc, 'client_secondary_reason': sr,
                     'overlap_writer_delete': '' if ov is None else int(ov), 'delete_rtt_ms': o.get('delete_rtt_ms'),
                     'action_http_status': o.get('action_http_status'), 'conflict_kind': o.get('conflict_kind'),
                     'conflict_current_rv': o.get('conflict_current_rv'), 'first_write_rv': o.get('first_write_rv'),
                     'action_rv': o.get('action_rv'), 'action_rv_source': o.get('action_rv_source'),
                     'flags': o.get('flags')})
    with open(out / 'client_secondary_E40.csv', 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator='\n')
        w.writeheader()
        w.writerows(rows)
    NA = ('UNKNOWN', 'INFRA_ERROR')
    both = [r for r in rows if r['primary_class'] not in NA and r['client_secondary_class'] not in NA]
    agree = sum(r['primary_class'] == r['client_secondary_class'] for r in both)
    tab = Counter((r['primary_class'] == 'INVALID_EXECUTED', r['client_secondary_class'] == 'INVALID_EXECUTED') for r in both)
    n = len(both)
    kappa = None
    if n:
        A, B, C, D = tab[(True, True)], tab[(True, False)], tab[(False, True)], tab[(False, False)]
        po = (A + D) / n
        pe = ((A + B) * (A + C) + (C + D) * (B + D)) / (n * n)
        kappa = None if pe == 1 else (po - pe) / (1 - pe)
    # per-cell secondary
    cells = defaultdict(lambda: {'k': 0, 'n': 0, 'unk': 0, 'k1': 0, 'n1': 0})
    for r in rows:
        key = (r['arm'], r['condition'], r['delta_ms'])
        c = cells[key]
        if r['client_secondary_class'] in NA:
            c['unk'] += 1
        else:
            c['n'] += 1
            c['k'] += r['client_secondary_class'] == 'INVALID_EXECUTED'
        if r['primary_class'] not in NA:
            c['n1'] += 1
            c['k1'] += r['primary_class'] == 'INVALID_EXECUTED'
    cell_rows = []
    for (arm, cond, d), c in sorted(cells.items(), key=lambda kv: (kv[0][0], kv[0][1], -1 if kv[0][2] is None else kv[0][2])):
        p, lo, hi = wilson(c['k'], c['n'])
        cell_rows.append({'arm': arm, 'condition': cond, 'delta_ms': '' if d is None else d, 'sec_k': c['k'],
                          'sec_n': c['n'], 'sec_p': p, 'sec_lo': lo, 'sec_hi': hi, 'sec_unknown': c['unk'],
                          'prim_k_simple': c['k1'], 'prim_n_simple': c['n1']})
    with open(out / 'client_secondary_cells_E40.csv', 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(cell_rows[0].keys()), lineterminator='\n')
        w.writeheader()
        w.writerows(cell_rows)
    ov = [r['overlap_writer_delete'] for r in rows if r['overlap_writer_delete'] != '']
    # webhook latency cost
    def rtts(arm, conds=None):
        return [float(r['delete_rtt_ms']) for r in rows if r['arm'] == arm and r['delete_rtt_ms'] not in ('', None)
                and str(r['action_http_status']) in ('200', '202') and (conds is None or r['condition'] in conds)]
    wl = {'A3_minus_A1_all_accepted': hl_shift(rtts('A3'), rtts('A1')),
          'A3_minus_A1_no_write': hl_shift(rtts('A3', ('no_write',)), rtts('A1', ('no_write',))),
          'A4d_minus_A1_all_accepted': hl_shift(rtts('A4d'), rtts('A1'))}
    ev = [(e['completed_wall_ns'] - e['received_wall_ns']) / 1e6 for e in wh
          if not e.get('pod_terminating') and e.get('completed_wall_ns') and e.get('received_wall_ns')]
    ev.sort()
    wl['webhook_evaluation_ms'] = ({'n': len(ev), 'median': ev[len(ev) // 2], 'p95': ev[int(0.95 * (len(ev) - 1))],
                                    'max': ev[-1]} if ev else None)
    wl['webhook_calls_per_accepted_A3_delete'] = None
    a3_uids = [str((trials[r['trial_id']].get('delete') or {}).get('expected_uid')) for r in rows
               if r['arm'] == 'A3' and str(r['action_http_status']) in ('200', '202')]
    if a3_uids:
        wl['webhook_calls_per_accepted_A3_delete'] = sum(len(wh_by_pod.get(u, [])) for u in a3_uids) / len(a3_uids)
    # A4d availability + 409 attribution
    pev_by_rv = defaultdict(list)
    for e in pev:
        if e.get('rv') is not None:
            pev_by_rv[str(e['rv'])].append(e)
    av = {}
    for pop in ('no_write', 'async'):
        rs = [r for r in rows if r['arm'] == 'A4d' and ((r['condition'] == 'no_write') if pop == 'no_write'
                                                        else r['condition'] in WRITER_OBJ)]
        disp = [r for r in rs if r['action_http_status'] not in ('', None)]
        c409 = [r for r in disp if str(r['action_http_status']) == '409']
        fa = [r for r in rs if r['primary_class'] == 'FALSE_ABORT']
        attr = Counter()
        for r in c409:
            y = str(r['conflict_current_rv'] or '')
            if not y:
                attr['no_rv_in_message'] += 1
            elif y == str(r['first_write_rv'] or ''):
                attr['writer'] += 1
            elif pev_by_rv.get(y):
                attr['other_pod_update(pod_watch:%s)' % pev_by_rv[y][0].get('type')] += 1
            else:
                attr['unattributed'] += 1
        n409, nd, nfa, nn = len(c409), len(disp), len(fa), len(rs)
        av[pop] = {'n': nn, 'n_dispatched': nd, 'n_409': n409, 'rate_409_among_dispatched': wilson(n409, nd),
                   'n_false_abort': nfa, 'false_abort_rate': wilson(nfa, nn), 'attribution_409': dict(attr),
                   'false_abort_by_reason': dict(Counter(r['primary_reason'] for r in fa))}
    # comparison with kind reference runs
    e40c = {}
    for c in read_csv(next(ad.glob('cells_E39.csv'))):
        parts = c['cell'].split('/')
        if parts[0] != 'A' or parts[3] not in WRITER_OBJ:
            continue
        e40c[(parts[1], parts[3], float(parts[4]))] = (int(c['k_invalid_executed']), int(c['n_primary_denominator']))
    comps = []
    refs = []
    if a.ref_e38v2r and Path(a.ref_e38v2r).is_file():
        m = {}
        for c in read_csv(a.ref_e38v2r):
            if c['condition'] in WRITER_OBJ and c['arm'] in ARM_SHORT and fnum(c['delta_ms']) is not None:
                m[(ARM_SHORT[c['arm']], c['condition'], float(c['delta_ms']))] = (int(c['k_invalid_executed']),
                                                                                int(c['n_primary_denominator']))
        refs.append(('E38v2_rerun_kind', m))
    if a.ref_e39 and Path(a.ref_e39).is_file():
        m = {}
        for c in read_csv(a.ref_e39):
            parts = c['cell'].split('/')
            if parts[0] == 'A' and parts[3] in WRITER_OBJ and parts[2] == 'python':
                m[(parts[1], parts[3], float(parts[4]))] = (int(c['k_invalid_executed']), int(c['n_primary_denominator']))
        refs.append(('E39A_kind', m))
    for name, m in refs:
        for key in sorted(e40c):
            if key not in m:
                continue
            k1, n1 = e40c[key]
            k2, n2 = m[key]
            nc = newcombe(k1, n1, k2, n2)
            comps.append({'reference': name, 'arm': key[0], 'condition': key[1], 'delta_ms': key[2],
                          'k_aks': k1, 'n_aks': n1, 'p_aks': k1 / n1 if n1 else None, 'k_kind': k2, 'n_kind': n2,
                          'p_kind': k2 / n2 if n2 else None, 'diff': None if nc is None else nc[0],
                          'newcombe_lo': None if nc is None else nc[1], 'newcombe_hi': None if nc is None else nc[2]})
    if comps:
        with open(out / 'comparison_kind_vs_aks_E40.csv', 'w', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=list(comps[0].keys()), lineterminator='\n')
            w.writeheader()
            w.writerows(comps)
    summ = {'schema': 'e40.supplement.v1', 'n_trials': len(rows),
            'agreement_primary_vs_client_secondary': {'n_both_classified': n, 'n_exact_agree': agree,
                                                      'prop': agree / n if n else None, 'kappa_invalid': kappa,
                                                      'invalid_2x2': {str(k): v for k, v in tab.items()}},
            'overlap_accepted_deletes': {'n': len(ov), 'n_overlap': sum(ov), 'prop': (sum(ov) / len(ov)) if ov else None},
            'webhook_latency': wl, 'a4d_availability': av,
            'comparison_references': [r[0] for r in refs], 'n_comparison_cells': len(comps),
            'secondary_reasons': dict(Counter(r['client_secondary_reason'] for r in rows))}
    (out / 'SUPPLEMENT_E40.json').write_text(json.dumps(summ, indent=1, sort_keys=True, default=str) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in summ.items() if k != 'secondary_reasons'}, indent=1, default=str)[:6000])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
