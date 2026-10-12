"""Final-draft v3 figures from E39 (run e39-20260926t1515z) and E40 (AKS run a20260926), 2026-09-27.

Same style as make_figures_v2.py (Okabe-Ito palette, redundant marker / line-style encoding, 300 dpi PNG + SVG).
Inputs (copied read-only from the result exports):
  e39_in/cells_E39.csv, e39_in/C_latency_E39.csv, e39_in/B_rates_E39.csv   (07_E39_RESULTS/.../analysis_final/)
  e40_in/cells_E40.csv (= results/a20260926/analysis/cells_E39.csv of the E40 export),
  e40_in/client_secondary_cells_E40.csv                                   (08_E40_AKS_RESULTS tarball)
Outputs (./figures/):
  fig5_e39a_exposure_v3   E39-A exposure curves, arms A1/A3/A4d/A4m x condition, Wilson 95% CI
  fig6_e39b_rates_v3      E39-B invalid actions per 1,000 responses by arm x lambda (log y), Wilson CI;
                          panel (b) A4d HTTP 409 (correct aborts) per 1,000
  figS3_e39c_cost_v3      E39-C paired HL shift FullRefresh - DCJI vs injected read latency c, with 3c line
  figS4_e39d_load_v3      E39-D exposure curves by scheduled load level (ITT)
  figS5_e39e_netpol_v3    E39-E NetworkPolicy CREATE, A1 vs A3
  figS6_e39f_client_v3    E39-F client-go vs Python
  figS7_e40_aks_vs_kind_v3 E40 AKS vs E39-A kind (A1, A3, A4d; scope and evidence writes)
No value is computed here that is reported as a new result except Wilson bounds for the 409 counts (panel 6b),
which follow from the counts in B_rates_E39.csv (409 per 1,000 x 400 / 1,000).
"""
import csv, math
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

OUT = Path('figures'); OUT.mkdir(exist_ok=True)
plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 8.5, 'axes.linewidth': 0.6,
                     'axes.edgecolor': '#444444', 'xtick.color': '#333333', 'ytick.color': '#333333',
                     'axes.labelcolor': '#222222', 'svg.fonttype': 'none'})
OI = {'black': '#000000', 'orange': '#E69F00', 'sky': '#56B4E9', 'green': '#009E73',
      'yellow': '#F0E442', 'blue': '#0072B2', 'verm': '#D55E00', 'purple': '#CC79A7', 'grey': '#7F7F7F'}
ARMS = {'A1': ('A1 DCJI client gate', OI['blue'], 'o', '-'),
        'A2': ('A2 FullRefresh client gate', OI['orange'], 's', '--'),
        'A3': ('A3 DCJI + validating webhook', OI['verm'], '^', '-'),
        'A4d': ('A4d commit-bound (direct)', OI['green'], 'D', '-'),
        'A4m': ('A4m commit-bound (mirrored)', OI['purple'], 'v', ':')}
CONDS = [('async_scope', 'scope write'), ('async_app_policy', 'application-policy write'),
         ('async_evidence_last_literal', 'evidence write (last satisfied literal)')]
GRID_A = ['0', '0.5', '1', '2', '3', '5', '10', '20', '50', '100', '200']
GRID_8 = ['0', '2', '5', '10', '20', '50', '100', '200']


def save(fig, name):
    fig.savefig(OUT / (name + '.png'), dpi=300, bbox_inches='tight')
    fig.savefig(OUT / (name + '.svg'), bbox_inches='tight')
    plt.close(fig)


def cells(path):
    return {r['cell']: r for r in csv.DictReader(open(path))}


def pci(r):
    p = float(r['p_hat']); return p, p - float(r['wilson_lo']), float(r['wilson_hi']) - p


def wilson(k, n, z=1.959963984540054):
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def style(ax, grid, labels=True):
    ax.set_xticks(range(len(grid)))
    if labels:
        ax.set_xticklabels([g.replace('0.5', '.5') for g in grid], fontsize=6.8)
    ax.set_ylim(-0.04, 1.06)
    ax.grid(axis='y', color='#DDDDDD', lw=0.5); ax.set_axisbelow(True)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)


def curve(ax, rows, key, grid, arm, off, lab=None, filled=True, col=None, mk=None, ls=None):
    _, c0, m0, l0 = ARMS[arm]
    col = col or c0; mk = mk or m0; ls = ls or l0
    xs, ps, lo, hi = [], [], [], []
    for i, d in enumerate(grid):
        r = rows[key(d)]
        p, a, b = pci(r); xs.append(i + off); ps.append(p); lo.append(a); hi.append(b)
    ax.errorbar(xs, ps, yerr=[lo, hi], color=col, marker=mk, ms=3.6, lw=1.0, ls=ls, elinewidth=0.6, capsize=0,
                mfc=col if filled else 'white', mec=col if not filled else 'white', mew=0.5 if filled else 0.8,
                label=lab)


# ---------------------------------------------------------------- Fig 5: E39-A exposure curves
def fig5(c39):
    offs = {'A1': -0.18, 'A3': -0.06, 'A4d': 0.06, 'A4m': 0.18}
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 3.0), sharey=True)
    for ax, (cond, title), tag in zip(axes, CONDS, 'abc'):
        for arm in ('A1', 'A3', 'A4m', 'A4d'):
            curve(ax, c39, lambda d: 'A/%s/python/%s/%s' % (arm, cond, d), GRID_A, arm, offs[arm], ARMS[arm][0])
        style(ax, GRID_A)
        ax.tick_params(axis='x', labelsize=6.0)
        ax.set_title('(%s) %s' % (tag, title), fontsize=7.6)
    axes[0].set_ylabel('P(invalid DELETE takes effect)\nprimary oracle, Wilson 95% CI')
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc='upper center', ncol=2, fontsize=7, frameon=False, bbox_to_anchor=(0.5, 1.08))
    fig.supxlabel('writer release δ after the end of the client gate (ms; ordinal grid)', fontsize=7.5)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    save(fig, 'fig5_e39a_exposure_v3')


# ---------------------------------------------------------------- Fig 6: E39-B rates per 1,000
def fig6(bpath):
    rows = list(csv.DictReader(open(bpath)))
    lams = [1, 10, 100]
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(7.2, 3.0), gridspec_kw={'width_ratios': [1.6, 1.0]})
    offs = {'A1': 0.90, 'A2': 0.97, 'A3': 1.04, 'A4d': 1.11}
    for arm in ('A1', 'A2', 'A3'):
        lab, col, mk, ls = ARMS[arm]
        xs, ys, lo, hi = [], [], [], []
        for lam in lams:
            r = next(x for x in rows if x['arm'] == arm and float(x['lambda_per_s']) == lam)
            y = float(r['per_1000']); xs.append(lam * offs[arm]); ys.append(y)
            lo.append(y - float(r['per_1000_lo'])); hi.append(float(r['per_1000_hi']) - y)
        ax.errorbar(xs, ys, yerr=[lo, hi], color=col, marker=mk, ms=4.2, lw=1.0, ls=ls, elinewidth=0.7, capsize=0,
                    mec='white', mew=0.5, label=lab)
    lab, col, mk, _ = ARMS['A4d']
    for lam in lams:
        r = next(x for x in rows if x['arm'] == 'A4d' and float(x['lambda_per_s']) == lam)
        assert r['k_invalid_executed'] == '0'
        up = float(r['per_1000_hi'])
        ax.plot([lam * offs['A4d']] * 2, [1.0, up], color=col, lw=1.0)
        ax.plot([lam * offs['A4d']], [up], marker='_', color=col, ms=8, mew=1.4)
        ax.plot([lam * offs['A4d']], [1.0], marker='v', color=col, ms=5)
    ax.plot([], [], color=col, marker='v', ls='-', label='A4d commit-bound: 0/400 (bar = Wilson upper bound)')
    ax.set_xscale('log'); ax.set_yscale('log')
    ax.set_xticks(lams); ax.set_xticklabels(['1', '10', '100'])
    ax.set_ylim(0.8, 1000); ax.set_xlim(0.6, 160)
    ax.set_xlabel('invalidating-write arrival rate λ (per s; Poisson)', fontsize=7.5)
    ax.set_ylabel('invalid actions per 1,000 responses\n(Wilson 95% CI; log scale)')
    ax.set_title('(a) invalid actions executed', fontsize=8)
    ax.grid(color='#E3E3E3', lw=0.5, which='major'); ax.set_axisbelow(True)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    ax.legend(loc='upper center', fontsize=6.4, frameon=False, ncol=2, bbox_to_anchor=(0.5, -0.2))
    # panel b: A4d 409 cost
    ks = []
    for lam in lams:
        r = next(x for x in rows if x['arm'] == 'A4d' and float(x['lambda_per_s']) == lam)
        k = round(float(r['rv_precondition_409_per_1000']) * 400 / 1000); ks.append(k)
    ys = [k / 400 * 1000 for k in ks]
    cis = [wilson(k, 400) for k in ks]
    bx.errorbar(lams, ys, yerr=[[y - c[0] * 1000 for y, c in zip(ys, cis)], [c[1] * 1000 - y for y, c in zip(ys, cis)]],
                color=OI['green'], marker='D', ms=4, lw=1.0, elinewidth=0.7, capsize=0, mec='white', mew=0.5)
    for lam, y, k in zip(lams, ys, ks):
        bx.text(lam * 1.18, y, '%g (%d/400)' % (y, k), fontsize=6.4, va='center')
    bx.set_xscale('log'); bx.set_yscale('log'); bx.set_xticks(lams); bx.set_xticklabels(['1', '10', '100'])
    bx.set_ylim(0.3, 300); bx.set_xlim(0.6, 400)
    bx.set_title('(b) A4d cost: HTTP 409 aborts per 1,000\n(all writer-caused, all correct aborts)', fontsize=8)
    bx.set_xlabel('λ (per s)', fontsize=7.5)
    bx.grid(color='#E3E3E3', lw=0.5); bx.set_axisbelow(True)
    for s in ('top', 'right'):
        bx.spines[s].set_visible(False)
    fig.tight_layout()
    save(fig, 'fig6_e39b_rates_v3')


# ---------------------------------------------------------------- Fig S3: E39-C cost vs c
def figS3(cpath):
    rows = list(csv.DictReader(open(cpath)))
    cs = [0, 50, 200, 1000]
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(7.2, 3.0))
    series = [('sequential', 'precomputed', OI['blue'], 'o', '-'), ('sequential', 'per_incident', OI['sky'], 's', '--'),
              ('parallel', 'precomputed', OI['orange'], '^', '-'), ('parallel', 'per_incident', OI['verm'], 'v', '--')]
    for axx, metric, title in ((ax, 'refresh_ms', '(a) refresh (read) latency saving'),
                               (bx, 'e2e_ms', '(b) end-to-end incl. planning')):
        for acq, plan, col, mk, ls in series:
            for cond, filled in (('stable', True), ('ea_attack', False)):
                ys, lo, hi = [], [], []
                for c in cs:
                    r = next(x for x in rows if float(x['injected_latency_ms']) == c and x['acquisition_mode'] == acq
                             and x['condition'] == cond and x['planning_mode'] == plan and x['metric'] == metric)
                    y = float(r['hl_A2_minus_A1_ms']); ys.append(y)
                    lo.append(y - float(r['boot_ci95_lo'])); hi.append(float(r['boot_ci95_hi']) - y)
                axx.errorbar(cs, ys, yerr=[lo, hi], color=col, marker=mk, ms=3.8, lw=0.9, ls=ls, elinewidth=0.6,
                             capsize=0, mfc=col if filled else 'white', mec=col, mew=0.7,
                             label='%s, %s' % (acq, plan.replace('_', '-')) if filled else None)
        axx.plot(cs, [3 * c for c in cs], color='#555555', lw=0.8, ls=':', label='3c (cost model, sequential)')
        axx.set_xscale('symlog', linthresh=50); axx.set_yscale('symlog', linthresh=20)
        axx.set_xticks(cs); axx.set_xticklabels([str(c) for c in cs])
        axx.axhline(0, color='#222222', lw=0.6)
        axx.set_xlabel('injected latency per ConfigMap GET, c (ms)', fontsize=7.5)
        axx.set_title(title, fontsize=8)
        axx.grid(color='#E3E3E3', lw=0.5); axx.set_axisbelow(True)
        for s in ('top', 'right'):
            axx.spines[s].set_visible(False)
    ax.set_ylabel('paired HL shift, FullRefresh − DCJI (ms)\n(positive = DCJI faster; symlog)')
    h, l = ax.get_legend_handles_labels()
    h.append(Line2D([], [], color='#555555', marker='o', mfc='white', ls='none')); l.append('open markers: ea_attack; filled: stable')
    fig.legend(h, l, loc='upper center', ncol=3, fontsize=6.6, frameon=False, bbox_to_anchor=(0.5, 1.08))
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    save(fig, 'figS3_e39c_cost_v3')


# ---------------------------------------------------------------- Fig S4: E39-D load
def figS4(c39):
    loads = [('idle', OI['sky'], 'o', ':'), ('moderate', OI['orange'], 's', '--'), ('high', OI['verm'], '^', '-')]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8), sharey=True)
    for ax, arm, tag in zip(axes, ('A1', 'A3'), 'ab'):
        for i, (ld, col, mk, ls) in enumerate(loads):
            curve(ax, c39, lambda d: 'D/%s/%s/async_scope/%s' % (arm, ld, d), GRID_8, arm, (i - 1) * 0.12,
                  '%s load' % ld, col=col, mk=mk, ls=ls)
        style(ax, GRID_8)
        ax.set_title('(%s) %s, scope write' % (tag, ARMS[arm][0]), fontsize=8)
        ax.set_xlabel('δ after gate end (ms; ordinal grid)', fontsize=7)
    axes[0].set_ylabel('P(invalid DELETE takes effect)\nWilson 95% CI (ITT by scheduled load)')
    axes[1].legend(loc='upper right', fontsize=6.8, frameon=False)
    fig.tight_layout()
    save(fig, 'figS4_e39d_load_v3')


# ---------------------------------------------------------------- Fig S5: E39-E NetworkPolicy
def figS5(c39):
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8), sharey=True)
    for ax, (cond, title), tag in zip(axes, [CONDS[0], CONDS[2]], 'ab'):
        for arm, off in (('A1', -0.08), ('A3', 0.08)):
            curve(ax, c39, lambda d: 'E/%s/%s/%s' % (arm, cond, d), GRID_8, arm, off, ARMS[arm][0])
        style(ax, GRID_8)
        ax.set_title('(%s) NetworkPolicy CREATE, %s' % (tag, title), fontsize=8)
        ax.set_xlabel('δ after gate end (ms; ordinal grid)', fontsize=7)
    axes[0].set_ylabel('P(invalid CREATE committed)\nWilson 95% CI')
    axes[1].legend(loc='upper right', fontsize=6.8, frameon=False)
    fig.tight_layout()
    save(fig, 'figS5_e39e_netpol_v3')


# ---------------------------------------------------------------- Fig S6: E39-F client-go
def figS6(c39):
    fig, ax = plt.subplots(1, 1, figsize=(4.6, 2.8))
    curve(ax, c39, lambda d: 'F/A1/python/async_scope/%s' % d, GRID_8, 'A1', -0.1, 'A1 Python client', filled=True)
    curve(ax, c39, lambda d: 'F/A1/client-go/async_scope/%s' % d, GRID_8, 'A1', 0.0, 'A1 client-go', filled=False, ls='--')
    curve(ax, c39, lambda d: 'F/A4d/client-go/async_scope/%s' % d, GRID_8, 'A4d', 0.1, 'A4d client-go')
    style(ax, GRID_8)
    ax.set_xlabel('δ after gate end (ms; ordinal grid)', fontsize=7)
    ax.set_ylabel('P(invalid DELETE takes effect)\nWilson 95% CI')
    ax.set_title('E39-F: scope write, Python vs client-go responder', fontsize=8)
    ax.legend(loc='upper right', fontsize=6.8, frameon=False)
    fig.tight_layout()
    save(fig, 'figS6_e39f_client_v3')


# ---------------------------------------------------------------- Fig S7: E40 AKS vs E39-A kind
def figS7(c39, c40, secpath):
    sec = {(r['arm'], r['condition'], r['delta_ms']): r for r in csv.DictReader(open(secpath))}
    conds = [CONDS[0], CONDS[2]]
    fig, axes = plt.subplots(2, 3, figsize=(7.2, 4.8), sharex=True, sharey=True)
    for i, (cond, clab) in enumerate(conds):
        for j, arm in enumerate(('A1', 'A3', 'A4d')):
            ax = axes[i][j]
            curve(ax, c39, lambda d: 'A/%s/python/%s/%s' % (arm, cond, d), GRID_8, arm, -0.1,
                  'kind (E39-A, audit-log oracle)', filled=False, ls='--')
            curve(ax, c40, lambda d: 'A/%s/python/%s/%s' % (arm, cond, d), GRID_8, arm, 0.1,
                  'AKS (E40, client-RV primary oracle)', filled=True, ls='-')
            if arm == 'A4d':
                ys = [float(sec[(arm, cond, '%.1f' % float(d))]['sec_p']) for d in GRID_8]
                ax.plot([k + 0.1 for k in range(len(GRID_8))], ys, ls='none', marker='x', color='#333333', ms=4,
                        label='AKS client-time secondary rule')
            style(ax, GRID_8, labels=(i == 1))
            if i == 0:
                ax.set_title(ARMS[arm][0], fontsize=7.8)
            if j == 0:
                ax.set_ylabel('%s\nP(invalid)' % clab.split(' (')[0], fontsize=7.4)
            if i == 1:
                ax.set_xlabel('δ (ms; ordinal)', fontsize=7)
    h = [Line2D([], [], color='#333333', marker='o', mfc='white', ls='--', lw=0.9, label='kind cluster (E39-A; audit-log commit-order oracle)'),
         Line2D([], [], color='#333333', marker='o', ls='-', lw=0.9, label='AKS managed control plane (E40; client-recorded RV oracle)'),
         Line2D([], [], color='#333333', marker='x', ls='none', label='AKS, client-time secondary rule (A4d only)')]
    fig.legend(handles=h, loc='upper center', ncol=2, fontsize=6.8, frameon=False, bbox_to_anchor=(0.5, 1.04))
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    save(fig, 'figS7_e40_aks_vs_kind_v3')


if __name__ == '__main__':
    c39 = cells('e39_in/cells_E39.csv'); c40 = cells('e40_in/cells_E40.csv')
    fig5(c39)
    fig6('e39_in/B_rates_E39.csv')
    figS3('e39_in/C_latency_E39.csv')
    figS4(c39)
    figS5(c39)
    figS6(c39)
    figS7(c39, c40, 'e40_in/client_secondary_cells_E40.csv')
    for p in sorted(OUT.iterdir()):
        if '_v3' in p.name:
            print(p.name, p.stat().st_size)
