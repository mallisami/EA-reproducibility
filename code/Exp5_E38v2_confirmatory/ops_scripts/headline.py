import csv, json, sys
from collections import Counter
rd = sys.argv[1]
res = json.load(open(rd + '/analysis_final/results_E38v2.json'))
print('gate', res['completeness_gate'], 'confirmatory', res['confirmatory'])
print('versions', res['analysis_version'], res['oracle_version'])
print('overall primary', res['overall_counts_primary'])
print('overall secondary (v1.2)', res['overall_counts_secondary'])
print('overall tertiary (v1 rule)', res['overall_counts_tertiary'])
print('unknown reasons', res['unknown_reasons_primary'])
print('infra reasons', res['infra_error_reasons'])
print('pod events', res['pod_events'])
cells = list(csv.DictReader(open(rd + '/analysis_final/cells_E38v2.csv')))
f = lambda x: '' if x == '' else '%.3f' % float(x)
print('\nHEADLINE P(INVALID_EXECUTED) primary: arm | condition | delta | k/n | p | Wilson95 | sec k/n p | ter k/n p | stage1 k/n | flag')
for c in cells:
    print('%s|%s|%s|%s/%s|%s|[%s,%s]|%s/%s %s|%s/%s %s|%s/%s|%s' % (
        c['arm'], c['condition'], c['delta_ms'], c['k_invalid_executed'], c['n_primary_denominator'], f(c['p_hat']),
        f(c['wilson_lo']), f(c['wilson_hi']), c['sec_k_invalid_executed'], c['sec_n_analyzable'], f(c['sec_p_hat']),
        c['ter_k_invalid_executed'], c['ter_n_analyzable'], f(c['ter_p_hat']), c['stage1_k'], c['stage1_n_analyzable'],
        c['quality_flag']))
print('\nEXPOSURE WINDOW')
for e in csv.DictReader(open(rd + '/analysis_final/exposure_window_E38v2.csv')):
    print(e)
print('\nWEBHOOK LATENCY')
for e in csv.DictReader(open(rd + '/analysis_final/webhook_latency_E38v2.csv')):
    print(e)
print('\nAVAILABILITY')
for e in csv.DictReader(open(rd + '/analysis_final/availability_E38v2.csv')):
    print(e)
for key in ('oracle_agreement', 'oracle_agreement_tertiary'):
    a = res[key]
    print('\n' + key, {k: a[k] for k in ('compared_with', 'n_both_classified', 'n_exact_agree', 'prop_exact_agree',
                                         'kappa_invalid_executed', 'invalid_executed_2x2')})
print('UID', res['uid_rebind_control'])
for r in [r for r in res['writer_release_accuracy'] if r['condition'] == 'ALL']:
    print('writer release', r['arm'], {k: r[k] for k in ('n', 'median', 'p05', 'p95', 'max', 'frac_abs_gt_100us', 'frac_abs_gt_500us')})
rows = list(csv.DictReader(open(rd + '/analysis_final/oracle_trials_E38v2.csv')))
multi = [r for r in rows if 'webhook_multiple_evaluations' in r['flags']]
differ = [r for r in rows if 'webhook_evaluations_differ' in r['flags']]
fng = [r for r in rows if 'webhook_first_evaluation_not_governing' in r['flags']]
a3 = [r for r in rows if r['arm'] == 'A3_dcji_webhook' and r['delete_dispatched'] == 'True']
print('A3 dispatched', len(a3), 'multi-eval', len(multi), 'differ', len(differ), 'first-not-governing', len(fng))
print('multi-eval classes', Counter(r['primary_class'] for r in multi))
print('differ classes', Counter(r['primary_class'] for r in differ))
print('deletion RV from pod watch by arm', Counter(r['arm'] for r in rows if 'delete_rv_from_pod_watch' in r['flags']))
