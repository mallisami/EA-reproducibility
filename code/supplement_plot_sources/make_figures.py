"""Phase-2 figure generation for the EA-TOCTOU / DCJI manuscript (2026-09-26).

Inputs (copied read-only into ./in/):
  figure1_workflow.png/.svg        original Figure 1 (01_JCP_SUBMISSION_DRAFT/Figures)
  A1_cell_estimators.csv           03_REANALYSIS_2026-09-26/latency/out
  t7_scaling.csv                   03_REANALYSIS_2026-09-26/offline/out/offline/t7_scaling
E38v2 inputs are passed explicitly (NOT read from ./in) so the figures can only be drawn from the
CONFIRMATORY E38v2 re-run once it exists:
  --e38v2-cells  <run>/analysis_final/cells_E38v2.csv        -> Fig. 4 and Fig. 2 exposure bands
  --e38v2-timing <E38V2_TIMING_DESCRIPTIVES.csv>             -> Fig. 2 to scale
     (produce with: python e38v2_timing_descriptives.py <run dir> E38V2_TIMING_DESCRIPTIVES.csv)
Without them Fig. 2 is drawn as a not-to-scale schematic with [E38v2-R] labels and Fig. 4 is skipped.
Usage: python make_figures.py [--e38v2-cells PATH] [--e38v2-timing PATH]
Outputs (./figures/): fig1_workflow, fig2_race_timeline, fig3_hl_forest, [fig4_e38v2_exposure],
  figS1_scaling  -- each as .png (300 dpi) and .svg.
Palette: Okabe-Ito (colour-blind safe) with redundant marker/line-style encoding.
"""
import csv, math, re
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyArrowPatch
from lxml import etree
from PIL import Image

IN = Path('in'); OUT = Path('figures'); OUT.mkdir(exist_ok=True)
plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 8.5, 'axes.linewidth': 0.6,
                     'axes.edgecolor': '#444444', 'xtick.color': '#333333', 'ytick.color': '#333333',
                     'axes.labelcolor': '#222222', 'svg.fonttype': 'none'})
OI = {'black': '#000000', 'orange': '#E69F00', 'sky': '#56B4E9', 'green': '#009E73',
      'yellow': '#F0E442', 'blue': '#0072B2', 'verm': '#D55E00', 'purple': '#CC79A7', 'grey': '#7F7F7F'}

def save(fig, name):
    fig.savefig(OUT / (name + '.png'), dpi=300, bbox_inches='tight')
    fig.savefig(OUT / (name + '.svg'), bbox_inches='tight')
    plt.close(fig)

# ---------------------------------------------------------------- Fig 1 (reuse, disclaimer removed)
def fig1():
    svg = etree.parse(str(IN / 'figure1_workflow.svg'))
    root = svg.getroot()
    ns = {'s': 'http://www.w3.org/2000/svg'}
    removed = 0
    for g in root.iter('{http://www.w3.org/2000/svg}g'):
        if g.get('id') == 'text_9':
            for c in g.iter(etree.Comment):
                if 'E38 did not implement' in (c.text or ''):
                    g.getparent().remove(g); removed = 1
                    break
        if removed:
            break
    assert removed == 1, 'disclaimer group not found'
    new_h = 212.0
    vb = root.get('viewBox').split()
    root.set('viewBox', '%s %s %s %s' % (vb[0], vb[1], vb[2], new_h))
    root.set('height', '%spt' % new_h)
    # background rect path still spans 241.7; harmless (clipped by viewBox)
    svg.write(str(OUT / 'fig1_workflow.svg'), xml_declaration=True, encoding='utf-8')
    im = Image.open(IN / 'figure1_workflow.png')
    w, h = im.size
    im.crop((0, 0, w, int(round(h * new_h / 241.704)))).save(OUT / 'fig1_workflow.png', dpi=(300, 300))

# ---------------------------------------------------------------- Fig 2 race timeline (schematic)
def read_timing(path):
    t = {}
    for r in csv.DictReader(open(path)):
        t[(r['metric'], r['arm'], r['condition'])] = float(r['median'])
    return t

def read_cells(path):
    return list(csv.DictReader(open(path)))

def band_summary(rows):
    """Measured exposure summary strings for the Fig. 2 bands (only when cell data are given)."""
    def p(arm, cond, d):
        r = next(r for r in rows if r['arm'] == arm and r['condition'] == cond and r['delta_ms'] == d)
        return float(r['p_hat'])
    client = [a for a in ('A0_native_uid', 'A1_dcji', 'A2_fullrefresh')]
    conds = ('async_scope', 'async_app_policy', 'async_evidence_last_literal')
    early = [p(a, c, d) for a in client for c in conds for d in ('0', '0.5', '1', '2')]
    d10 = max(p(a, c, '10') for a in client for c in conds)
    d20 = max(p(a, c, '20') for a in client for c in conds)
    a3 = [p('A3_dcji_webhook', c, d) for c in ('async_scope', 'async_app_policy') for d in ('10', '20')]
    return ('measured exposure, A0–A2:\nP = %.2f–%.2f for δ = 0–2 ms;\n≤ %.3f at δ = 10 ms; ≤ %.3f at 20 ms' % (min(early), max(early), d10, d20),
            'scope/app-policy writes:\n%.2f–%.2f (δ = 10–20 ms)' % (min(a3), max(a3)))

def fig2(timing_path=None, cells_path=None):
    """Race-timeline schematic. Without E38v2 inputs it is drawn NOT TO SCALE with [E38v2-R] labels;
    with --e38v2-timing (E38V2_TIMING_DESCRIPTIVES.csv) and --e38v2-cells (cells_E38v2.csv) it uses
    the measured medians and exposure estimates."""
    schematic = timing_path is None
    if schematic:
        gate_a1, rtt_a1, rtt_a3, wh_eval, send = 10.0, 9.0, 30.0, 11.0, 0.05
        lab_gate = 'client gate reads\n[E38v2-R: median]'
        lab_rtt = 'DELETE round trip [E38v2-R: median, no_write]'
        lab_wh = 'webhook reads: evidence a–f first,\nscope, app-policy last [E38v2-R: eval. median]'
        lab_send = 'gate end → DELETE send: [E38v2-R: median]'
    else:
        t = read_timing(timing_path)
        gate_a1 = t[('gate_duration_ms', 'A1_dcji', 'all')]
        rtt_a1 = t[('delete_rtt_accepted_ms', 'A1_dcji', 'no_write')]
        rtt_a3 = t[('delete_rtt_accepted_ms', 'A3_dcji_webhook', 'no_write')]
        wh_eval = t[('webhook_evaluation_ms(received->completed, evaluated calls)', 'A3_dcji_webhook', 'all')]
        send = t[('gate_end_to_delete_send_ms', 'A1_dcji', 'all_dispatched')]
        lab_gate = 'client gate reads\n(median %.1f ms)' % gate_a1
        lab_rtt = 'DELETE round trip, median %.1f ms (no_write)'
        lab_wh = 'webhook reads: evidence a–f first,\nscope, app-policy last (eval. median %.1f ms)' % wh_eval
        lab_send = 'gate end → DELETE send: median %.3f ms' % send
    if cells_path:
        top_txt, bot_txt = band_summary(read_cells(cells_path))
    else:
        top_txt = 'measured exposure, A0–A2 (shaded):\n[E38v2-R: P(invalid) by δ]'
        bot_txt = 'scope/app-policy writes exposed:\n[E38v2-R: δ range and P(invalid)]'
    fig, axes = plt.subplots(2, 1, figsize=(7.0, 3.9), sharex=True)
    lanes = [('Client gate only\n(A1 DCJI; A0/A2 analogous)', rtt_a1, None),
             ('Client gate +\nvalidating webhook (A3)', rtt_a3, wh_eval)]
    for ax, (title, rtt, whe) in zip(axes, lanes):
        y = 0.5
        ax.add_patch(Rectangle((-gate_a1, y - 0.18), gate_a1, 0.36, fc='#DCE9F5', ec=OI['blue'], lw=0.8))
        ax.text(-gate_a1 / 2, y, lab_gate, ha='center', va='center', fontsize=7.2)
        ax.add_patch(Rectangle((send, y - 0.18), rtt - send, 0.36, fc='#F2F2F2', ec='#555555', lw=0.8))
        if whe is None:
            ax.text(rtt / 2 + 0.3, y, 'admission\n+ commit', ha='center', va='center', fontsize=7)
        else:
            ws = 1.0
            ax.add_patch(Rectangle((ws, y - 0.18), whe, 0.36, fc='#FBE3CF', ec=OI['verm'], lw=0.8, hatch='////'))
            ax.text(ws + whe / 2, y + 0.30, lab_wh, ha='center', va='bottom', fontsize=6.8, color='#222222')
            ax.text(ws + whe + (rtt - ws - whe) / 2, y, 'storage commit\n(re-evaluations\npossible)', ha='center', va='center', fontsize=7)
        ax.axvline(0, color='#222222', lw=0.9, ls='--')
        ax.annotate('', xy=(rtt, y - 0.30), xytext=(0, y - 0.30), arrowprops=dict(arrowstyle='<->', lw=0.7, color='#333333'))
        ax.text(rtt / 2, y - 0.42, lab_rtt if schematic else lab_rtt % rtt, ha='center', va='top', fontsize=7)
        ax.plot([rtt], [y], marker='v', color='#222222', ms=5)
        ax.text(rtt + 0.6, y + 0.12, 'commit /\nresponse', fontsize=6.8, va='bottom')
        ax.set_ylim(-0.25, 1.25); ax.set_yticks([])
        ax.set_ylabel(title, rotation=0, ha='right', va='center', fontsize=7.8, labelpad=4)
        for s in ('top', 'right', 'left'):
            ax.spines[s].set_visible(False)
    axes[0].axvspan(0, min(5, rtt_a1), color=OI['sky'], alpha=0.22, lw=0)
    axes[0].set_ylim(-0.25, 1.6)
    axes[0].text(12.5, 1.32, top_txt, ha='left', va='center', fontsize=6.8)
    axes[1].axvspan(wh_eval + 1.0 - 2.0, rtt_a3 * 0.65, color=OI['verm'], alpha=0.15, lw=0)
    axes[1].text((wh_eval + rtt_a3 * 0.65) / 2, 1.13, bot_txt, ha='center', fontsize=6.8)
    axes[1].text(-0.5, 1.13, 'early writes to late-read objects rejected;\nevidence (read first) exposed from δ ≈ 0', ha='right', fontsize=6.8)
    axes[0].text(0.3, -0.2, lab_send, fontsize=6.5, color='#444444')
    axes[1].set_xlim(-gate_a1 - 1, max(40, rtt_a3 + 8))
    if schematic:
        axes[1].set_xticks([0]); axes[1].set_xticklabels(['gate end'])
        axes[1].set_xlabel('time (schematic, not to scale); writer released at δ ≥ 0 after gate end')
    else:
        axes[1].set_xlabel('time relative to client gate end (ms); writer released at δ ≥ 0 after gate end')
    fig.tight_layout(h_pad=1.2)
    save(fig, 'fig2_race_timeline')

# ---------------------------------------------------------------- Fig 4 E38v2 exposure curves (parameterized)
def fig4(cells_path, name='fig4_e38v2_exposure'):
    rows = read_cells(cells_path)
    conds = [('async_scope', '(a) scope write'), ('async_app_policy', '(b) application-policy write'),
             ('async_evidence_last_literal', '(c) evidence write (last satisfied literal)')]
    arms = [('A0_native_uid', 'A0 NativeUID', OI['grey'], 'D', ':'),
            ('A1_dcji', 'A1 DCJI', OI['blue'], 'o', '-'),
            ('A2_fullrefresh', 'A2 FullRefresh', OI['orange'], 's', '--'),
            ('A3_dcji_webhook', 'A3 DCJI + webhook', OI['verm'], '^', '-')]
    grid = ['-5', '-2', '-1', '0', '0.5', '1', '2', '3', '5', '10', '20', '50']
    xpos = {d: i + (0.0 if i < 3 else 0.6) for i, d in enumerate(grid)}
    offs = {'A0_native_uid': -0.18, 'A1_dcji': -0.06, 'A2_fullrefresh': 0.06, 'A3_dcji_webhook': 0.18}
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.9), sharey=True)
    for ax, (cond, title) in zip(axes, conds):
        ax.axvspan(-0.5, 2.5, color='#EEEEEE', lw=0)
        for arm, lab, col, mk, ls in arms:
            xs, ps, los, his = [], [], [], []
            for d in grid:
                r = next(r for r in rows if r['arm'] == arm and r['condition'] == cond and r['delta_ms'] == d)
                p = float(r['p_hat']); lo = float(r['wilson_lo']); hi = float(r['wilson_hi'])
                xs.append(xpos[d] + offs[arm]); ps.append(p); los.append(p - lo); his.append(hi - p)
            ax.errorbar(xs[3:], ps[3:], yerr=[los[3:], his[3:]], color=col, marker=mk, ms=3.6, lw=1.0, ls=ls,
                        elinewidth=0.6, capsize=0, label=lab, mec='white', mew=0.4)
            ax.errorbar(xs[:3], ps[:3], yerr=[los[:3], his[:3]], color=col, marker=mk, ms=3.6, lw=0,
                        elinewidth=0.6, capsize=0, mec='white', mew=0.4)
        ax.set_xticks([xpos[d] for d in grid])
        ax.set_xticklabels(['−5', '−2', '−1', '0', '.5', '1', '2', '3', '5', '10', '20', '50'], fontsize=6.8)
        ax.set_title(title, fontsize=8)
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

# ---------------------------------------------------------------- Fig 3 HL forest
def fig3():
    rows = list(csv.DictReader(open(IN / 'A1_cell_estimators.csv')))
    sel = [r for r in rows if r['dataset'] in ('E21', 'E37') and not r['cell'].startswith('POOLED')]
    sel += [r for r in rows if r['dataset'] in ('E35', 'E26')]
    fig, ax = plt.subplots(figsize=(6.8, 7.2))
    ypos = []; labels = []
    y = 0
    colors = {'E21': OI['blue'], 'E37': OI['verm'], 'E35': OI['green'], 'E26': OI['purple']}
    markers = {'E21': 'o', 'E37': 's', 'E35': 'D', 'E26': '^'}
    prev = None
    for r in sel:
        if prev is not None and r['dataset'] != prev:
            y += 0.8
        prev = r['dataset']
        hl, lo, hi, mean = float(r['hl']), float(r['hl_lo']), float(r['hl_hi']), float(r['mean'])
        c = colors[r['dataset']]
        ax.plot([lo, hi], [y, y], color=c, lw=1.4)
        ax.plot(hl, y, marker=markers[r['dataset']], color=c, ms=5.5, mec='white', mew=0.6, ls='none')
        ax.plot(mean, y, marker='|', color='#222222', ms=7, mew=1.0, ls='none')
        cell = r['cell'].replace('pooled(all conditions)', 'idle/sequential (pooled conditions)')
        cell = cell.replace('ea_attack', 'EA-attack').replace('irrelevant_mutation', 'irrelevant')
        labels.append('%s  %s' % (r['dataset'], cell)); ypos.append(y)
        y += 1
    ax.axvline(0, color='#555555', lw=0.7)
    ax.set_yticks(ypos); ax.set_yticklabels(labels, fontsize=7)
    ax.invert_yaxis()
    ax.set_xlabel('paired refresh-latency difference ΔL = DCJI − FullRefresh (ms); negative favours DCJI')
    ax.grid(axis='x', color='#DDDDDD', lw=0.5)
    ax.set_axisbelow(True)
    from matplotlib.lines import Line2D
    h = [Line2D([], [], color=OI['blue'], marker='o', lw=1.4, label='E21 HL, 95% bootstrap CI'),
         Line2D([], [], color=OI['verm'], marker='s', lw=1.4, label='E37 HL, 95% bootstrap CI'),
         Line2D([], [], color=OI['green'], marker='D', lw=1.4, label='E35 HL, 95% bootstrap CI'),
         Line2D([], [], color=OI['purple'], marker='^', lw=1.4, label='E26 HL, 95% bootstrap CI'),
         Line2D([], [], color='#222222', marker='|', ls='none', ms=7, label='protocol mean')]
    ax.legend(handles=h, loc='lower left', fontsize=7, frameon=False)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    save(fig, 'fig3_hl_forest')


# ---------------------------------------------------------------- Fig S1 scaling (supplement)
def figS1():
    rows = list(csv.DictReader(open(IN / 't7_scaling.csv')))
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.7))
    for core, col, mk, ls, lab in (('orig', OI['blue'], 'o', '-', 'locked core'), ('patched', OI['verm'], 's', '--', 'patched core')):
        rs = [r for r in rows if r['experiment'] == 'E12c' and r['core'] == core]
        n = [int(r['size']) for r in rs]; t = [float(r['median_runtime_s']) for r in rs]
        ev = [int(r['subset_evals']) for r in rs]
        axes[0].plot(n, t, color=col, marker=mk, ls=ls, lw=1.2, ms=4, label=lab)
    rs = [r for r in rows if r['experiment'] == 'E12c' and r['core'] == 'orig']
    axes[0].plot([int(r['size']) for r in rs], [float(r['frozen_median_runtime_s']) for r in rs], color='#222222',
                 marker='x', ls=':', lw=0.8, ms=4, label='frozen record (original host)')
    axes[0].set_yscale('log'); axes[0].set_xlabel('candidate observations n'); axes[0].set_ylabel('median planning time (s)')
    axes[0].set_title('(a) E12c exhaustive subset search (cap 100,000)', fontsize=8)
    axes[0].legend(fontsize=6.8, frameon=False)
    # (b) capped search at n = 20/24
    cats, vals, cols, txt = [], [], [], []
    for size in ('20', '24'):
        for core in ('orig', 'patched'):
            r = next(r for r in rows if r['experiment'] == 'E12c_ext' and r['size'] == size and r['core'] == core and r['cap'] == '1000')
            cats.append('n=%s\n%s' % (size, 'locked' if core == 'orig' else 'patched'))
            if r['median_runtime_s']:
                vals.append(float(r['median_runtime_s'])); txt.append('%.2f s\n%s MB' % (float(r['median_runtime_s']), r['median_maxrss_mb']))
            else:
                vals.append(0); txt.append('MemoryError')
            cols.append(OI['blue'] if core == 'orig' else OI['verm'])
    axes[1].bar(range(4), vals, color=cols, width=0.6)
    for i, (v, tx) in enumerate(zip(vals, txt)):
        axes[1].text(i, v + 0.08, tx, ha='center', va='bottom', fontsize=6.8)
    axes[1].set_xticks(range(4)); axes[1].set_xticklabels(cats, fontsize=7)
    axes[1].set_ylabel('median time (s), cap = 1,000'); axes[1].set_ylim(0, 5.6)
    axes[1].set_title('(b) capped search beyond n = 16', fontsize=8)
    for ax in axes:
        for s in ('top', 'right'):
            ax.spines[s].set_visible(False)
    fig.tight_layout()
    save(fig, 'figS1_scaling')

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Phase-2 figures. E38v2-dependent figures are drawn only from the results path given.')
    ap.add_argument('--e38v2-cells', help='analysis_final/cells_E38v2.csv of the CONFIRMATORY E38v2 run (Fig. 4; Fig. 2 bands)')
    ap.add_argument('--e38v2-timing', help='E38V2_TIMING_DESCRIPTIVES.csv computed by e38v2_timing_descriptives.py on the confirmatory run (Fig. 2)')
    a = ap.parse_args()
    fig1(); fig3(); figS1()
    fig2(a.e38v2_timing, a.e38v2_cells)
    if a.e38v2_cells:
        fig4(a.e38v2_cells)
    else:
        print('Fig. 4 NOT generated: pass --e38v2-cells <confirmatory cells_E38v2.csv>')
    for p in sorted(OUT.iterdir()):
        print(p.name, p.stat().st_size)
