"""Phase-2 v2 figures from the CONFIRMATORY E38v2 rerun (e38v2r-20260926t1032z), 2026-09-26.

Adapted from make_figures.py (same style, Okabe-Ito palette, redundant marker/line-style encoding).
Only the E38v2-dependent figures are drawn here; Fig 1, Fig 3 and Fig S1 are unchanged from the draft.
Inputs (passed explicitly):
  --e38v2-cells    rerun analysis_final/cells_E38v2.csv                     -> Fig 2 exposure strips, Fig 4, Fig S2
  --e38v2-timing   E38V2R_TIMING_DESCRIPTIVES.csv (e38v2_timing_descriptives_v2.py on the rerun) -> Fig 2 to scale
  --first-cells    first (preliminary) run analysis_final/cells_E38v2.csv   -> Fig S2
  --comparison     rerun COMPARISON_FIRST_VS_RERUN.csv (Newcombe CI of the difference)       -> Fig S2 markers
Outputs (./figures/): fig2_race_timeline_v2, fig4_e38v2_exposure_v2, figS2_first_vs_rerun_v2 (.png 300 dpi + .svg)
"""
import csv, argparse
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D

OUT = Path('figures'); OUT.mkdir(exist_ok=True)
plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 8.5, 'axes.linewidth': 0.6,
                     'axes.edgecolor': '#444444', 'xtick.color': '#333333', 'ytick.color': '#333333',
                     'axes.labelcolor': '#222222', 'svg.fonttype': 'none'})
OI = {'black': '#000000', 'orange': '#E69F00', 'sky': '#56B4E9', 'green': '#009E73',
      'yellow': '#F0E442', 'blue': '#0072B2', 'verm': '#D55E00', 'purple': '#CC79A7', 'grey': '#7F7F7F'}
ARMS = [('A0_native_uid', 'A0 NativeUID', OI['grey'], 'D', ':'),
        ('A1_dcji', 'A1 DCJI', OI['blue'], 'o', '-'),
        ('A2_fullrefresh', 'A2 FullRefresh', OI['orange'], 's', '--'),
        ('A3_dcji_webhook', 'A3 DCJI + webhook', OI['verm'], '^', '-')]
CONDS = [('async_scope', 'scope write'), ('async_app_policy', 'application-policy write'),
         ('async_evidence_last_literal', 'evidence write (last satisfied literal)')]
GRID = ['-5', '-2', '-1', '0', '0.5', '1', '2', '3', '5', '10', '20', '50']
GRID_LAB = ['−5', '−2', '−1', '0', '.5', '1', '2', '3', '5', '10', '20', '50']

def save(fig, name):
    fig.savefig(OUT / (name + '.png'), dpi=300, bbox_inches='tight')
    fig.savefig(OUT / (name + '.svg'), bbox_inches='tight')
    plt.close(fig)

def read_cells(path):
    return list(csv.DictReader(open(path)))

def cell(rows, arm, cond, d):
    return next(r for r in rows if r['arm'] == arm and r['condition'] == cond and r['delta_ms'] == d)

def read_timing(path):
    return {(r['metric'], r['arm'], r['condition']): float(r['median']) for r in csv.DictReader(open(path))}

# ---------------------------------------------------------------- Fig 2 race timeline, to scale
def fig2(timing_path, cells_path):
    t = read_timing(timing_path)
    rows = read_cells(cells_path)
    gate = {a: t[('gate_duration_ms', a, 'all')] for a, *_ in ARMS}
    send = t[('gate_end_to_delete_send_ms', 'A1_dcji', 'all_dispatched')]
    rtt_a1 = t[('delete_rtt_accepted_ms', 'A1_dcji', 'no_write')]
    rtt_a3 = t[('delete_rtt_accepted_ms', 'A3_dcji_webhook', 'no_write')]
    WH = 'A3_dcji_webhook'
    w_recv = t[('webhook_first_call_received_after_gate_end_ms', WH, 'no_write')]
    w_ev = t[('webhook_first_call_evidence_a_read_after_gate_end_ms', WH, 'no_write')]
    w_sc = t[('webhook_first_call_scope_read_after_gate_end_ms', WH, 'no_write')]
    w_ap = t[('webhook_first_call_app_policy_read_after_gate_end_ms', WH, 'no_write')]
    w_done = t[('webhook_first_call_completed_after_gate_end_ms', WH, 'no_write')]
    w_2nd = t[('webhook_second_call_received_after_gate_end_ms', WH, 'no_write')]
    deltas = ['0', '0.5', '1', '2', '3', '5', '10', '20', '50']
    xd = [float(d) for d in deltas]

    fig = plt.figure(figsize=(7.2, 5.4))
    gs = fig.add_gridspec(4, 1, height_ratios=[1.0, 0.95, 1.25, 0.95], hspace=0.12)
    axs = [fig.add_subplot(gs[i]) for i in range(4)]
    for ax in axs[1:]:
        ax.sharex(axs[0])
    XMIN, XMAX = -gate['A2_fullrefresh'] - 2.0, 56.0

    def lane(ax, rtt, title):
        y = 0.5
        g = gate['A1_dcji']
        ax.add_patch(Rectangle((-g, y - 0.2), g, 0.4, fc='#DCE9F5', ec=OI['blue'], lw=0.8))
        ax.text(-g / 2, y, 'A1 client gate\nreads', ha='center', va='center', fontsize=6.8)
        ax.add_patch(Rectangle((send, y - 0.2), rtt - send, 0.4, fc='#F2F2F2', ec='#555555', lw=0.8, zorder=1))
        ax.plot([rtt], [y + 0.28], marker='v', color='#222222', ms=5)
        ax.axvline(0, color='#222222', lw=0.9, ls='--')
        ax.set_ylim(-0.1, 1.25); ax.set_yticks([])
        ax.set_ylabel(title, rotation=0, ha='right', va='center', fontsize=7.6, labelpad=4)
        for s in ('top', 'right', 'left', 'bottom'):
            ax.spines[s].set_visible(False)
        ax.tick_params(axis='x', labelbottom=False, bottom=False)
        return y

    # lane 1: client gate only
    y = lane(axs[0], rtt_a1, 'Client gate only\n(A1; A0/A2 alike)')
    axs[0].text(rtt_a1 + 0.8, y, 'DELETE admission + commit:\nresponse at %.1f ms (median, no_write)' % rtt_a1,
                ha='left', va='center', fontsize=6.8)
    axs[0].annotate('', xy=(-gate['A2_fullrefresh'], 1.05), xytext=(-gate['A0_native_uid'], 1.05),
                    arrowprops=dict(arrowstyle='<->', lw=0.6, color='#555555'))
    axs[0].text(0.8, 1.05,
                '← gate duration medians: A0 %.1f, A1 %.1f, A2 %.1f ms' % (gate['A0_native_uid'], gate['A1_dcji'], gate['A2_fullrefresh']),
                ha='left', va='center', fontsize=6.5, color='#444444')

    # lane 2: webhook
    y = lane(axs[2], rtt_a3, 'Client gate +\nvalidating webhook\n(A3)')
    axs[2].add_patch(Rectangle((w_recv, y - 0.2), w_done - w_recv, 0.4, fc='#FBE3CF', ec=OI['verm'], lw=0.8,
                               hatch='////', zorder=2))
    for xx, lab in ((w_ev, 'evidence a\nread'), (w_sc, 'scope\nread'), (w_ap, 'app-policy\nread')):
        axs[2].plot([xx, xx], [y - 0.2, y + 0.2], color=OI['verm'], lw=1.1, zorder=3)
    axs[2].text(w_ev, y - 0.24, 'evidence a', ha='center', va='top', fontsize=6.2, color='#222222')
    axs[2].text((w_sc + w_ap) / 2, y - 0.24, 'scope / app-policy', ha='center', va='top', fontsize=6.2, color='#222222')
    axs[2].text((w_recv + w_done) / 2, y + 0.26, '1st webhook evaluation', ha='center', va='bottom', fontsize=6.4)
    axs[2].plot([w_2nd], [y], marker='|', color=OI['verm'], ms=11, mew=1.4, zorder=3)
    axs[2].text(w_2nd + 0.5, y + 0.26, '2nd webhook call\nreceived (median %.1f ms)' % w_2nd, ha='left', va='bottom', fontsize=6.2)
    axs[2].text(rtt_a3 - 0.3, y - 0.24, 'response %.1f ms' % rtt_a3, ha='right', va='top', fontsize=6.4)

    # exposure strips
    def strip(ax, series, note):
        for lab, arm_list, conds, col, mk, off in series:
            lo, hi, ks, ns = [], [], [], []
            for d in deltas:
                ps = []; k = n = 0
                for a in arm_list:
                    for c in conds:
                        r = cell(rows, a, c, d)
                        ps.append(float(r['p_hat'])); k += int(r['k_invalid_executed']); n += int(r['n_primary_denominator'])
                lo.append(min(ps)); hi.append(max(ps)); ks.append(k); ns.append(n)
            xs = [x + off for x in xd]
            ax.vlines(xs, lo, hi, color=col, lw=2.2, alpha=0.55)
            ax.plot(xs, [k / n for k, n in zip(ks, ns)], ls='none', marker=mk, color=col, ms=4, mec='white', mew=0.5, label=lab)
        ax.axvline(0, color='#222222', lw=0.9, ls='--')
        ax.set_ylim(-0.05, 1.08); ax.set_yticks([0, 0.5, 1])
        ax.set_ylabel('P(invalid)', fontsize=7)
        ax.grid(axis='y', color='#E3E3E3', lw=0.5); ax.set_axisbelow(True)
        for s in ('top', 'right'):
            ax.spines[s].set_visible(False)
        ax.legend(loc='upper right', fontsize=6.3, frameon=False, handletextpad=0.3)
        ax.text(XMIN + 0.5, 0.5, note, fontsize=6.3, va='center', ha='left', color='#444444')
    cl = ['A0_native_uid', 'A1_dcji', 'A2_fullrefresh']
    allc = [c for c, _ in CONDS]
    strip(axs[1], [('A0–A2, all three writes: pooled p̂ (marker), cell range (bar)', cl, allc, OI['blue'], 'o', 0.0)],
          'writer released δ ms\nafter gate end\n(δ grid 0–50 ms)')
    strip(axs[3], [('A3 scope / app-policy writes', ['A3_dcji_webhook'], ['async_scope', 'async_app_policy'], OI['verm'], '^', -0.35),
                   ('A3 evidence write', ['A3_dcji_webhook'], ['async_evidence_last_literal'], OI['purple'], 'D', 0.35)],
          'writer released δ ms\nafter gate end')
    axs[1].tick_params(axis='x', labelbottom=False)
    axs[3].set_xlim(XMIN, XMAX)
    axs[3].set_xticks([-20, -10, 0, 5, 10, 20, 30, 40, 50])
    axs[3].set_xlabel('time relative to client gate end (ms), to scale; median intervals from the confirmatory run')
    save(fig, 'fig2_race_timeline_v2')

# ---------------------------------------------------------------- Fig 4 exposure curves
def fig4(cells_path, name='fig4_e38v2_exposure_v2'):
    rows = read_cells(cells_path)
    xpos = {d: i + (0.0 if i < 3 else 0.6) for i, d in enumerate(GRID)}
    offs = {'A0_native_uid': -0.18, 'A1_dcji': -0.06, 'A2_fullrefresh': 0.06, 'A3_dcji_webhook': 0.18}
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.9), sharey=True)
    for ax, (cond, title), tag in zip(axes, CONDS, 'abc'):
        ax.axvspan(-0.5, 2.5, color='#EEEEEE', lw=0)
        for arm, lab, col, mk, ls in ARMS:
            xs, ps, los, his = [], [], [], []
            for d in GRID:
                r = cell(rows, arm, cond, d)
                p = float(r['p_hat']); lo = float(r['wilson_lo']); hi = float(r['wilson_hi'])
                xs.append(xpos[d] + offs[arm]); ps.append(p); los.append(p - lo); his.append(hi - p)
            ax.errorbar(xs[3:], ps[3:], yerr=[los[3:], his[3:]], color=col, marker=mk, ms=3.6, lw=1.0, ls=ls,
                        elinewidth=0.6, capsize=0, label=lab, mec='white', mew=0.4)
            ax.errorbar(xs[:3], ps[:3], yerr=[los[:3], his[:3]], color=col, marker=mk, ms=3.6, lw=0,
                        elinewidth=0.6, capsize=0, mec='white', mew=0.4)
        ax.set_xticks([xpos[d] for d in GRID])
        ax.set_xticklabels(GRID_LAB, fontsize=6.8)
        ax.set_title('(%s) %s' % (tag, title), fontsize=8)
        ax.set_ylim(-0.04, 1.06)
        ax.grid(axis='y', color='#DDDDDD', lw=0.5); ax.set_axisbelow(True)
        ax.text(1.0, 0.52, 'released at\nfence read', ha='center', va='center', fontsize=6.2, color='#555555')
        for s in ('top', 'right'):
            ax.spines[s].set_visible(False)
        ax.set_xlabel('nominal δ after gate end (ms; ordinal grid)', fontsize=7)
    axes[0].set_ylabel('P(invalid DELETE takes effect)\nprimary oracle, Wilson 95% CI')
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', ncol=4, fontsize=7.2, frameon=False, bbox_to_anchor=(0.5, 1.04))
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    save(fig, name)

# ---------------------------------------------------------------- Fig S2 first vs rerun
def figS2(first_path, rerun_path, comp_path, name='figS2_first_vs_rerun_v2'):
    first = read_cells(first_path); rerun = read_cells(rerun_path)
    comp = {(r['arm'], r['condition'], r['delta_ms']): r for r in csv.DictReader(open(comp_path))}
    xpos = {d: i + (0.0 if i < 3 else 0.6) for i, d in enumerate(GRID)}
    fig, axes = plt.subplots(4, 3, figsize=(7.2, 8.4), sharex=True, sharey=True)
    for i, (arm, alab, col, mk, _) in enumerate(ARMS):
        for j, (cond, clab) in enumerate(CONDS):
            ax = axes[i][j]
            ax.axvspan(-0.5, 2.5, color='#EEEEEE', lw=0)
            for rows, off, filled, ls, lab in ((first, -0.12, False, '--', 'first run (preliminary, scheduling-gated Pod)'),
                                               (rerun, 0.12, True, '-', 'confirmatory rerun (Running+Ready Pod)')):
                xs, ps, lo, hi = [], [], [], []
                for d in GRID:
                    r = cell(rows, arm, cond, d)
                    p = float(r['p_hat'])
                    xs.append(xpos[d] + off); ps.append(p)
                    lo.append(p - float(r['wilson_lo'])); hi.append(float(r['wilson_hi']) - p)
                ax.errorbar(xs[3:], ps[3:], yerr=[lo[3:], hi[3:]], color=col, marker=mk, ms=3.4, lw=0.9, ls=ls,
                            elinewidth=0.5, capsize=0, mfc=col if filled else 'white', mec=col, mew=0.7, label=lab)
                ax.errorbar(xs[:3], ps[:3], yerr=[lo[:3], hi[:3]], color=col, marker=mk, ms=3.4, lw=0,
                            elinewidth=0.5, capsize=0, mfc=col if filled else 'white', mec=col, mew=0.7)
            for d in GRID:
                c = comp[(arm, cond, d)]
                if c['ci_excludes_0'] == 'True':
                    ax.text(xpos[d], 1.07, '*', ha='center', va='center', fontsize=9, color='#222222')
            ax.set_ylim(-0.05, 1.14); ax.set_yticks([0, 0.5, 1])
            ax.grid(axis='y', color='#DDDDDD', lw=0.5); ax.set_axisbelow(True)
            for s in ('top', 'right'):
                ax.spines[s].set_visible(False)
            if i == 0:
                ax.set_title(clab, fontsize=7.8)
            if j == 0:
                ax.set_ylabel('%s\nP(invalid)' % alab, fontsize=7.4)
            if i == 3:
                ax.set_xticks([xpos[d] for d in GRID]); ax.set_xticklabels(GRID_LAB, fontsize=6.2)
                ax.set_xlabel('nominal δ (ms; ordinal)', fontsize=7)
    h = [Line2D([], [], color='#333333', marker='o', mfc='white', ls='--', lw=0.9, label='first run (preliminary; scheduling-gated Pod)'),
         Line2D([], [], color='#333333', marker='o', ls='-', lw=0.9, label='confirmatory rerun (Running-and-Ready Pod)'),
         Line2D([], [], color='#222222', marker='$*$', ls='none', ms=7, label='95% Newcombe CI of difference excludes 0')]
    fig.legend(handles=h, loc='upper center', ncol=2, fontsize=7, frameon=False, bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    save(fig, name)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--e38v2-cells', required=True)
    ap.add_argument('--e38v2-timing', required=True)
    ap.add_argument('--first-cells', required=True)
    ap.add_argument('--comparison', required=True)
    a = ap.parse_args()
    fig2(a.e38v2_timing, a.e38v2_cells)
    fig4(a.e38v2_cells)
    figS2(a.first_cells, a.e38v2_cells, a.comparison)
    for p in sorted(OUT.iterdir()):
        print(p.name, p.stat().st_size)
