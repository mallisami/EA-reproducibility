import csv, json, sys
rd = sys.argv[1]
res = json.load(open(rd + '/analysis_final/results_E38v2.json'))
print('gate', res['completeness_gate'], 'confirmatory', res['confirmatory'])
print('overall primary', res['overall_counts_primary'])
print('overall secondary', res['overall_counts_secondary'])
print('unknown reasons', res['unknown_reasons_primary'])
cells = list(csv.DictReader(open(rd + '/analysis_final/cells_E38v2.csv')))
f = lambda x: '' if x == '' else '%.3f' % float(x)
print('\nHEADLINE P(INVALID_EXECUTED) primary: arm | condition | delta | k/n | p | Wilson95 | (S2 excl fail-closed p) | stage1 k/n')
for c in cells:
    print('%s|%s|%s|%s/%s|%s|[%s,%s]|%s|%s/%s|%s' % (c['arm'], c['condition'], c['delta_ms'], c['k_invalid_executed'],
          c['n_primary_denominator'], f(c['p_hat']), f(c['wilson_lo']), f(c['wilson_hi']), f(c['p_hat_exclude_failclosed']),
          c['stage1_k'], c['stage1_n_analyzable'], c['quality_flag']))
print('\nEXPOSURE WINDOW')
for e in csv.DictReader(open(rd + '/analysis_final/exposure_window_E38v2.csv')):
    print(e)
print('\nWEBHOOK LATENCY')
for e in csv.DictReader(open(rd + '/analysis_final/webhook_latency_E38v2.csv')):
    print(e)
print('\nAVAILABILITY')
for e in csv.DictReader(open(rd + '/analysis_final/availability_E38v2.csv')):
    print(e)
a = res['oracle_agreement']
print('\nAGREEMENT', {k: a[k] for k in ('n_both_classified', 'n_exact_agree', 'prop_exact_agree', 'kappa_invalid_executed', 'invalid_executed_2x2')})
print('UID', res['uid_rebind_control'])
wr = [r for r in res['writer_release_accuracy'] if r['condition'] == 'ALL']
for r in wr:
    print('writer release', r['arm'], {k: r[k] for k in ('n', 'median', 'p05', 'p95', 'max', 'frac_abs_gt_100us', 'frac_abs_gt_500us')})
rows = list(csv.DictReader(open(rd + '/analysis_final/oracle_trials_E38v2.csv')))
multi = [r for r in rows if 'webhook_multiple_evaluations' in r['flags']]
differ = [r for r in rows if 'webhook_evaluations_differ' in r['flags']]
fng = [r for r in rows if 'webhook_first_evaluation_not_governing' in r['flags']]
a3 = [r for r in rows if r['arm'] == 'A3_dcji_webhook' and r['delete_dispatched'] == 'True']
print('A3 dispatched', len(a3), 'multi-eval', len(multi), 'differ', len(differ), 'first-not-governing', len(fng))
from collections import Counter
print('multi-eval classes', Counter(r['primary_class'] for r in multi))
print('differ classes', Counter(r['primary_class'] for r in differ))
