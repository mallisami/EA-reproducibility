import csv, math, sys
from collections import defaultdict
rows = list(csv.DictReader(open(sys.argv[1] + '/analysis_final/oracle_trials_E38v2.csv')))


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return None, None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z / d * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return p, max(0, c - h), min(1, c + h)


by = defaultdict(lambda: [0, 0, 0, 0, 0])
for r in rows:
    if r['arm'] != 'A3_dcji_webhook' or not r['condition'].startswith('async'):
        continue
    key = (r['condition'], r['delta_ms'])
    b = by[key]
    inv = r['primary_class'] == 'INVALID_EXECUTED'
    differ = 'webhook_evaluations_differ' in r['flags']
    multi = 'webhook_multiple_evaluations' in r['flags']
    b[0] += inv
    b[1] += 1
    b[2] += multi
    b[3] += differ
    b[4] += inv and differ
print('A3 cell | k/n primary | multi-eval | evaluations differ | v1-rule sensitivity (differ -> UNKNOWN, excluded): k/n p [Wilson]')
for (c, d), (k, n, m, df, kd) in sorted(by.items(), key=lambda x: (x[0][0], float(x[0][1]))):
    p, lo, hi = wilson(k - kd, n - df)
    print('%s %s | %d/%d | %d | %d | %d/%d %s' % (c, d, k, n, m, df, k - kd, n - df,
          'NA' if p is None else '%.3f [%.3f,%.3f]' % (p, lo, hi)))
