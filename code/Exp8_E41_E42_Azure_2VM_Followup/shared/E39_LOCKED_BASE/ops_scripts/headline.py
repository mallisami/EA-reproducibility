#!/usr/bin/env python3
"""E39 headline tables from analysis_final/results_E39.json (read-only formatting; no recomputation).
Usage: headline.py RESULTS_JSON INDEPENDENT_JSON QUALITY_JSON OUT_TXT"""
import json, sys

res = json.load(open(sys.argv[1]))
ind = json.load(open(sys.argv[2])) if len(sys.argv) > 2 else {}
qg = json.load(open(sys.argv[3])) if len(sys.argv) > 3 else {}
out = []
P = out.append


def f(x, d=3):
    return 'NA' if x is None else ('%.*f' % (d, x))


def ci(p, lo, hi, d=3):
    return '%s [%s, %s]' % (f(p, d), f(lo, d), f(hi, d))


P('E39 HEADLINE TABLES (generated from %s; analysis %s; confirmatory=%s)' % (
    sys.argv[1], res.get('analysis_version'), res.get('confirmatory')))
cg = res.get('completeness_gate') or {}
P('completeness gate passed=%s design_check=%s n_planned=%s n_observed=%s lock_seed=%s' % (
    cg.get('passed'), cg.get('design_check'), cg.get('n_planned'), cg.get('n_observed'), cg.get('lock_seed')))
cells = res.get('cells') or []


def cell_rows(exp):
    return [c for c in cells if c['experiment'] == exp]


P('')
P('== E39-A  P(INVALID_EXECUTED) primary, Wilson 95% (k/n) per arm x condition x delta')
for c in cell_rows('A'):
    p = c['primary']
    P('  %-52s %4d/%-4d %s  2nd %s  FA=%d 409rv=%d UNK=%d INFRA=%d' % (
        c['label'], p['k_invalid_executed'], p['n_primary_denominator'], ci(p['p_hat'], p['wilson_lo'], p['wilson_hi']),
        f((c.get('secondary') or {}).get('p_hat')), p['counts'].get('FALSE_ABORT', 0), c.get('n_rv_precondition_409', 0),
        p['counts'].get('UNKNOWN', 0), p['counts'].get('INFRA_ERROR', 0)))
A = res.get('E39_A') or {}
P('  -- exposure window (d* = smallest grid delta with p<=0.05 there and above; bootstrap CI; W_A = area 0-200 ms)')
for w in A.get('exposure_window') or []:
    b = w.get('bootstrap') or {}
    P('  %-40s d*=%s ms [%s, %s] not-closed-frac=%s  W_A=%s ms  closed=%s' % (
        w['label'], w.get('d_star_ms'), b.get('ci95_lo_ms'), b.get('ci95_hi_ms'), f(b.get('frac_not_closed')),
        f(w.get('window_area_ms'), 2), w.get('closed_within_grid')))
P('  -- A4 availability cost')
for a in A.get('availability_cost') or []:
    nw = a['no_write']
    P('  %-4s no_write FALSE_ABORT %d/%d %s; no_write 409 %s; async FALSE_ABORT %s (via 409 %s); 409 among dispatched %s' % (
        a['arm'], nw.get('k_false_abort'), nw.get('n_analyzable'), ci(nw.get('p_hat'), nw.get('wilson_lo'), nw.get('wilson_hi')),
        '%d/%d' % (a['no_write_409']['k'], a['no_write_409']['n']),
        '%d/%d' % (a['async_false_abort']['k'], a['async_false_abort']['n']), a['async_false_abort'].get('k_via_409'),
        '%d/%d %s' % (a['rate_409_among_dispatched']['k'], a['rate_409_among_dispatched']['n'],
                      ci(a['rate_409_among_dispatched']['p_hat'], a['rate_409_among_dispatched']['wilson_lo'],
                         a['rate_409_among_dispatched']['wilson_hi']))))
    if a.get('rv_precondition_409_by_attribution'):
        P('       409 attribution: %s' % json.dumps(a['rv_precondition_409_by_attribution']))
m = A.get('mirror') or {}
P('  -- A4m mirror: n=%s lag_ms %s | lag_rv %s' % (m.get('n_with_mirror'), json.dumps(m.get('lag_ms')), json.dumps(m.get('lag_rv'))))
P('     INVALID explained: %s ; not mirrored trials %s' % (json.dumps(m.get('invalid_executed_explained_by_mirror_lag')), m.get('not_mirrored_trials')))
for w in m.get('window_vs_lag') or []:
    P('     %s' % json.dumps(w))
P('  -- uid_rebind_control: %s' % json.dumps(A.get('uid_rebind_control')))
for k in ('agreement_secondary', 'agreement_tertiary'):
    ag = A.get(k) or {}
    P('  -- %s: %s/%s = %s kappa(INVALID)=%s' % (k, ag.get('n_exact_agree'), ag.get('n_both_classified'),
                                              f(ag.get('prop_exact_agree'), 4), f(ag.get('kappa_invalid_executed'), 4)))
P('  -- writer release error (us): %s' % json.dumps(A.get('writer_release_us')))

B = res.get('E39_B') or {}
P('')
P('== E39-B  INVALID_EXECUTED per 1,000 responses (Wilson x 1000); prediction 1-exp(-lambda W_A)')
for c in B.get('per_cell') or []:
    P('  %-4s lambda=%-5s %3d/%-4d %s  pred=%s (W=%s ms, %s) in_CI=%s  gate-adj pred=%s  FA/1000=%s 409/1000=%s' % (
        c['arm'], c['lambda_per_s'], c['k_invalid_executed'], c['n_responses'],
        ci(c['per_1000'], c['per_1000_lo'], c['per_1000_hi'], 1), f(c.get('predicted_per_1000'), 1),
        f(c.get('W_prediction_ms'), 2), c.get('W_prediction_source'), c.get('prediction_in_wilson_ci'),
        f(c.get('predicted_gate_adjusted_per_1000'), 1), f(c.get('false_abort_per_1000'), 1),
        f(c.get('rv_precondition_409_per_1000'), 1)))
for w in B.get('window_fit') or []:
    P('  fitted W %-4s = %s ms [%s, %s] (W_A %s ms from %s; median gate %s ms) %s' % (
        w['arm'], f(w.get('W_hat_ms'), 2), f(w.get('W_lo_ms'), 2), f(w.get('W_hi_ms'), 2), f(w.get('W_from_A_ms'), 2),
        w.get('W_from_A_source'), f(w.get('median_gate_ms'), 2), w.get('boundary') or ''))
for e in B.get('extrapolation_model_based') or []:
    P('  MODEL-BASED EXTRAPOLATION %-4s lambda=%s/s: per 1000 fitted W %s [%s, %s]; from W_A %s' % (
        e['arm'], e['lambda_per_s'], f(e['per_1000_from_fitted_W'], 4), f(e['per_1000_lo'], 4), f(e['per_1000_hi'], 4),
        f(e['per_1000_from_A_window'], 4)))

C = res.get('E39_C') or {}
P('')
P('== E39-C  paired HL shift A2 - A1 (FullRefresh minus DCJI), ms, bootstrap 95%; cost model excess over c=0')
for c in C.get('cells') or []:
    r, g, e2 = c['refresh_ms'], c['gate_ms'], c['e2e_ms']
    cm = c.get('cost_model') or {}
    P('  %-10s %-12s %-9s c=%-6s pairs=%-3s refresh %s [%s,%s] | gate %s | e2e %s [%s,%s] | excess/c=%s in_bounds=%s' % (
        c['acquisition_mode'], c['planning_mode'], c['condition'], c['injected_latency_ms'], c.get('n_pairs_complete'),
        f(r.get('hl_A2_minus_A1_ms'), 1), f(r.get('boot_ci95_lo'), 1), f(r.get('boot_ci95_hi'), 1),
        f(g.get('hl_A2_minus_A1_ms'), 1), f(e2.get('hl_A2_minus_A1_ms'), 1), f(e2.get('boot_ci95_lo'), 1),
        f(e2.get('boot_ci95_hi'), 1), f(cm.get('refresh_ms_ratio_excess_to_c')), cm.get('refresh_ms_excess_within_model_bounds')))
for b in C.get('break_even') or []:
    ov = b.get('planning_overhead_A1_minus_A2_ms')
    P('  break-even %-10s %-12s %-9s c*=%s ms (alpha=%s ms, beta=%s); planning A1-A2 %s ms -> model sequential c* = overhead/3 = %s ms' % (
        b['acquisition_mode'], b['planning_mode'], b['condition'], f(b.get('break_even_c_ms'), 1), f(b.get('alpha_ms'), 2),
        f(b.get('beta_per_ms_c'), 3), f(ov, 2), f(ov / 3.0 if ov is not None else None, 2)))
P('  gate decisions (ea_attack etc.) per cell are in results_E39.json E39_C.cells[].gate_decisions')

D = res.get('E39_D') or {}
P('')
P('== E39-D  P(INVALID_EXECUTED) by scheduled load level (ITT)')
for c in cell_rows('D'):
    p = c['primary']
    P('  %-48s %3d/%-3d %s' % (c['label'], p['k_invalid_executed'], p['n_primary_denominator'],
                               ci(p['p_hat'], p['wilson_lo'], p['wilson_hi'])))
for w in D.get('exposure_window_by_load') or []:
    b = w.get('bootstrap') or {}
    P('  %-36s d*=%s [%s,%s] W=%s ms' % (w['label'], w.get('d_star_ms'), b.get('ci95_lo_ms'), b.get('ci95_hi_ms'),
                                         f(w.get('window_area_ms'), 2)))
for h in D.get('host_trace') or []:
    P('  host %-8s n=%s cpu median %s max %s | free RAM min %s MB | cpu>85: %s ram<2GB: %s throttled: %s effective %s' % (
        h['load_level'], h['n'], f(h['host_cpu_pct'].get('median'), 1), f(h['host_cpu_pct'].get('max'), 1),
        f(h['free_ram_mb'].get('min'), 0), h['n_cpu_gt_85'], h['n_free_ram_lt_2048mb'], h['n_throttled'],
        json.dumps(h['effective_levels'])))
P('  gate_ms by load: %s' % json.dumps({k: f(v.get('median'), 2) for k, v in (D.get('gate_ms_by_load') or {}).items()}))
P('  delete RTT ms by load: %s' % json.dumps({k: f(v.get('median'), 2) for k, v in (D.get('delete_rtt_ms_by_load') or {}).items()}))

E_ = res.get('E39_E') or {}
P('')
P('== E39-E  NetworkPolicy CREATE: P(INVALID_EXECUTED)')
for c in cell_rows('E'):
    p = c['primary']
    P('  %-52s %3d/%-3d %s' % (c['label'], p['k_invalid_executed'], p['n_primary_denominator'],
                               ci(p['p_hat'], p['wilson_lo'], p['wilson_hi'])))
for w in E_.get('exposure_window') or []:
    P('  %-36s d*=%s W=%s ms' % (w['label'], w.get('d_star_ms'), f(w.get('window_area_ms'), 2)))
for d in E_.get('A3_minus_A1_by_delta') or []:
    P('  A3-A1 %-28s delta=%-5s %s' % (d['condition'], d['delta_ms'], ci(d['A3_minus_A1'], d['newcombe_lo'], d['newcombe_hi'])))
P('  structural limitation: %s' % E_.get('structural_limitation'))

F_ = res.get('E39_F') or {}
P('')
P('== E39-F  client-go vs Python')
for c in cell_rows('F'):
    p = c['primary']
    P('  %-52s %3d/%-3d %s  409rv=%d' % (c['label'], p['k_invalid_executed'], p['n_primary_denominator'],
                                         ci(p['p_hat'], p['wilson_lo'], p['wilson_hi']), c.get('n_rv_precondition_409', 0)))
for w in F_.get('exposure_window_by_impl') or []:
    P('  %-36s d*=%s W=%s ms' % (w['label'], w.get('d_star_ms'), f(w.get('window_area_ms'), 2)))
for l in F_.get('latency_per_arm') or []:
    P('  %-4s %-9s DELETE RTT median %s ms; gate-end->send median %s ms (n=%s)' % (
        l['arm'], l['client_impl'], f(l['delete_rtt_ms'].get('median'), 2), f(l['gate_end_to_send_ms'].get('median'), 3),
        l['delete_rtt_ms'].get('n')))
for l in F_.get('latency_comparisons') or []:
    P('  %s: RTT HL %s [%s,%s] ms; gate-end->send HL %s [%s,%s] ms' % (
        l['comparison'], f(l['delete_rtt_ms'].get('hl_ms'), 2), f(l['delete_rtt_ms'].get('boot_ci95_lo'), 2),
        f(l['delete_rtt_ms'].get('boot_ci95_hi'), 2), f(l['gate_end_to_send_ms'].get('hl_ms'), 3),
        f(l['gate_end_to_send_ms'].get('boot_ci95_lo'), 3), f(l['gate_end_to_send_ms'].get('boot_ci95_hi'), 3)))
for d in F_.get('A1_go_minus_python_by_delta') or []:
    P('  A1 go-python delta=%-5s %s' % (d['delta_ms'], ci(d['go_minus_python'], d['newcombe_lo'], d['newcombe_hi'])))
P('  go_client_versions: %s' % F_.get('go_client_versions'))

G = res.get('E39_G') or {}
P('')
P('== E39-G  out-of-model robustness')
for g in G.get('by_condition') or []:
    a1, a2 = g['A1_dcji'], g['A2_fullrefresh']
    dd, ex = g['dcji_minus_fullrefresh'], g['dcji_minus_fullrefresh_exact_unconditional']
    P('  %-22s DCJI %d/%d %s | FullRefresh %d/%d %s | diff %s (exact %s, %s)' % (
        g['condition'], a1['k'], a1['n'], ci(a1['p_hat'], a1['wilson_lo'], a1['wilson_hi']), a2['k'], a2['n'],
        ci(a2['p_hat'], a2['wilson_lo'], a2['wilson_hi']), ci(dd['diff'], dd['newcombe_lo'], dd['newcombe_hi']),
        f(ex.get('lo')), f(ex.get('hi'))))
    P('      A1 counts %s | A2 counts %s' % (json.dumps(a1['counts']), json.dumps(a2['counts'])))
P('')
cmp_ = (ind or {}).get('comparison') or {}
P('== Independent recomputation (standard library only): %s differences' % cmp_.get('n_differences'))
for d in cmp_.get('differences') or []:
    P('   %s' % json.dumps(d))
if qg:
    P('== Operational quality gates: %s' % json.dumps(qg.get('GATES')))
    P('   INFRA %s/%s, audit ids joined %s, primary vs secondary agreement %s, classes %s' % (
        qg.get('infra_error'), qg.get('n_trials'), qg.get('audit_ids_joined'), f(qg.get('agreement_primary_secondary'), 4),
        json.dumps(qg.get('primary_classes'))))
    P('   UNKNOWN reasons: %s' % json.dumps(qg.get('unknown_reasons')))
open(sys.argv[4], 'w').write('\n'.join(out) + '\n')
print('\n'.join(out))
