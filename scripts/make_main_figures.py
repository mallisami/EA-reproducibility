from pathlib import Path
import re
import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.pyplot as plt
from scipy.stats import binom

ROOT = Path(__file__).resolve().parent.parent
INPUTS = ROOT / 'data' / 'figure_inputs'
HERE = ROOT / 'outputs' / 'figures'
HERE.mkdir(parents=True, exist_ok=True)

# Vector-friendly scientific-figure defaults. Sizes are chosen for full-text-width
# Springer figures so labels remain readable after inclusion in the manuscript.
mpl.rcParams.update({
    'font.family': 'DejaVu Sans',
    'font.size': 9.0,
    'axes.labelsize': 9.3,
    'axes.titlesize': 9.3,
    'xtick.labelsize': 8.4,
    'ytick.labelsize': 8.4,
    'legend.fontsize': 8.2,
    'axes.linewidth': 0.8,
    'xtick.major.width': 0.8,
    'ytick.major.width': 0.8,
    'xtick.minor.width': 0.6,
    'ytick.minor.width': 0.6,
    'xtick.major.size': 3.5,
    'ytick.major.size': 3.5,
    'xtick.minor.size': 2.0,
    'ytick.minor.size': 2.0,
    'lines.linewidth': 1.45,
    'lines.markersize': 4.8,
    'errorbar.capsize': 2.0,
    'pdf.fonttype': 42,
    'ps.fonttype': 42,
    'savefig.bbox': 'tight',
    'savefig.pad_inches': 0.03,
})

COLORS = mpl.rcParams['axes.prop_cycle'].by_key()['color']


def _clean_tex(s):
    return s.strip().replace('{[}', '[').replace('{]}', ']')


def _parse_ci(s):
    m = re.match(r'\[([0-9.]+),\s*([0-9.]+)\]', s)
    return (float(m.group(1)), float(m.group(2))) if m else (np.nan, np.nan)


def parse_table(label, arm_pat, columns):
    return pd.read_csv(INPUTS / (label + '.csv'), dtype=str, keep_default_na=False)[columns]


def wilson(k, n, z=1.959963984540054):
    k = np.asarray(k, dtype=float)
    n = np.asarray(n, dtype=float)
    p = np.divide(k, n, out=np.zeros_like(k), where=n > 0)
    den = 1 + z*z/n
    ctr = (p + z*z/(2*n)) / den
    half = z*np.sqrt((p*(1-p)/n) + z*z/(4*n*n)) / den
    return np.clip(ctr-half, 0, 1), np.clip(ctr+half, 0, 1)


def style_axis(ax, y_label=False):
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.tick_params(axis='both', which='both', direction='out')
    ax.grid(False)
    ax.set_ylim(-0.04, 1.08)
    ax.set_yticks(np.arange(0, 1.01, 0.2))
    if not y_label:
        ax.tick_params(labelleft=False)


def set_delay_axis(ax, all_ticks, major_ticks, xlim_max=None, **_ignored):
    # Smooth quantitative delay axis: use a log(1+x) transform so 0 ms remains
    # valid, the dense short-delay region receives more space, and the long tail
    # is compressed without the visual discontinuity of a piecewise linear/log axis.
    forward = lambda x: np.log10(np.asarray(x, dtype=float) + 1.0)
    inverse = lambda y: np.power(10.0, np.asarray(y, dtype=float)) - 1.0
    ax.set_xscale('function', functions=(forward, inverse))
    ax.set_xticks(major_ticks)
    ax.set_xticklabels([f'{x:g}' for x in major_ticks], rotation=0, ha='center')
    minor = [x for x in all_ticks if x not in major_ticks]
    if minor:
        ax.set_xticks(minor, minor=True)
        ax.tick_params(axis='x', which='minor', labelbottom=False)
    if xlim_max is None:
        xlim_max = max(all_ticks)
    ax.set_xlim(0, xlim_max)


def errplot(ax, x, p, lo, hi, *, color, marker, linestyle, label=None, zorder=3):
    yerr = np.maximum(np.vstack([p-lo, hi-p]), 0.0)
    ax.errorbar(
        x, p, yerr=yerr, color=color, marker=marker, linestyle=linestyle,
        linewidth=1.45, markersize=4.8, markeredgewidth=0.8,
        elinewidth=0.8, capsize=2.0, capthick=0.8,
        label=label, zorder=zorder
    )


# ---------- Figure 2: controlled-delay exposure ----------
s1_cols = ['arm','write','delta','n','k','p','ci','stage1','sec_p','ter_p','uid409']
s1 = parse_table('tab-S1', r'A(?:0|1|2|3)', s1_cols)
for c in ['n','k','p']:
    s1[c] = pd.to_numeric(s1[c], errors='coerce')
s1[['lo','hi']] = s1['ci'].apply(lambda x: pd.Series(_parse_ci(x)))
s1['delta_num'] = pd.to_numeric(s1['delta'], errors='coerce')

# Three stacked panels give the short-delay transition region enough physical width
# at final journal size while retaining every experimental condition.
fig, axes = plt.subplots(3, 1, figsize=(7.25, 5.35), sharex=True, sharey=True)
write_titles = [('scope', '(a) Scope-state write'), ('app-policy', '(b) Application-policy write'), ('evidence', '(c) Evidence write')]
series2 = [
    ('A0', 'UID-only client gate', COLORS[0], 'D', ':'),
    ('A1', 'Selective client gate (DCJI)', COLORS[1], 'o', '-'),
    ('A2', 'Full-refresh client gate', COLORS[2], 's', '--'),
    ('A3', 'External-state webhook', COLORS[3], '^', '-.'),
]
all_ticks2 = [0, 0.5, 1, 2, 3, 5, 10, 20, 50]
handles = labels = None
for idx, (ax, (write, title)) in enumerate(zip(axes, write_titles)):
    for arm, label, color, marker, ls in series2:
        d = s1[(s1.arm == arm) & (s1.write == write) & (s1.delta_num >= 0)].sort_values('delta_num')
        errplot(ax, d.delta_num.to_numpy(), d.p.to_numpy(), d.lo.to_numpy(), d.hi.to_numpy(),
                color=color, marker=marker, linestyle=ls, label=label, zorder=4 if arm == 'A3' else 3)
    style_axis(ax, y_label=True)
    set_delay_axis(ax, all_ticks2, all_ticks2, xlim_max=50)
    ax.set_title(title, loc='left', pad=4.0, fontsize=9.6, fontweight='semibold')
    if idx < 2:
        ax.tick_params(labelbottom=False)
    if idx == 0:
        handles, labels = ax.get_legend_handles_labels()
fig.supylabel('Probability of later-effect invalidity', x=0.012, fontsize=9.6)
fig.supxlabel(r'Scheduled writer-release delay after client-gate end, $\delta$ (ms)', y=0.028, fontsize=9.6)
fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 0.997), ncol=2,
           frameon=False, handlelength=2.7, columnspacing=1.6, borderaxespad=0)
fig.subplots_adjust(left=0.105, right=0.995, bottom=0.13, top=0.86, hspace=0.28)
fig.savefig(HERE/'Fig2.pdf')
fig.savefig(HERE/'Fig2.png', dpi=600)
plt.close(fig)

# ---------- Figure 3: extended-delay target binding ----------
s8_cols = ['arm','write','delta','n','k','p','ci','stage1','sec_p','version_conflict','ca','unk','uid']
s8 = parse_table('tab-S8', r'A(?:1|3|4d|4m)', s8_cols)
for c in ['n','k','p','version_conflict','ca','unk']:
    s8[c] = pd.to_numeric(s8[c], errors='coerce')
s8[['lo','hi']] = s8['ci'].apply(lambda x: pd.Series(_parse_ci(x)))
s8['delta_num'] = pd.to_numeric(s8['delta'], errors='coerce')

fig, axes = plt.subplots(3, 1, figsize=(7.25, 5.45), sharex=True, sharey=True)
series3 = [
    ('A1', 'Selective client gate (DCJI)', COLORS[0], 'o', '-'),
    ('A3', 'External-state webhook', COLORS[1], '^', '-.'),
    ('A4m', 'Mirrored target condition', COLORS[2], 'v', ':'),
    ('A4d', 'Direct target binding', COLORS[3], 'D', ':'),
]
all_ticks3 = [0, 0.5, 1, 2, 3, 5, 10, 20, 50, 100, 200]
for idx, (ax, (write, title)) in enumerate(zip(axes, write_titles)):
    for arm, label, color, marker, ls in series3:
        d = s8[(s8.arm == arm) & (s8.write == write) & (s8.delta_num >= 0)].sort_values('delta_num')
        errplot(ax, d.delta_num.to_numpy(), d.p.to_numpy(), d.lo.to_numpy(), d.hi.to_numpy(),
                color=color, marker=marker, linestyle=ls, label=label,
                zorder=5 if arm == 'A4d' else 3)
    style_axis(ax, y_label=True)
    set_delay_axis(ax, all_ticks3, all_ticks3, xlim_max=200)
    ax.set_title(title, loc='left', pad=4.0, fontsize=9.6, fontweight='semibold')
    if idx < 2:
        ax.tick_params(labelbottom=False)
    if idx == 0:
        handles, labels = ax.get_legend_handles_labels()
fig.supylabel('Probability of later-effect invalidity', x=0.012, fontsize=9.6)
fig.supxlabel(r'Writer-release delay after client-gate end, $\delta$ (ms)', y=0.028, fontsize=9.6)
fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 0.997), ncol=2,
           frameon=False, handlelength=2.7, columnspacing=1.6, borderaxespad=0)
fig.subplots_adjust(left=0.105, right=0.995, bottom=0.13, top=0.86, hspace=0.28)
fig.savefig(HERE/'Fig3.pdf')
fig.savefig(HERE/'Fig3.png', dpi=600)
plt.close(fig)

# ---------- Figure 4: endpoint sensitivity ----------
# Aggregate counts are taken directly from Supplement Table S8a.
delays4 = np.array([0,0.5,1,2,3,5,10,20,50,100,200], dtype=float)
endpoint_data = pd.read_csv(INPUTS / 'tab-S8a.csv')
agg = {}
for arm, title in [('A1','Selective client gate (DCJI)'),('A3','External-state webhook'),
                   ('A4m','Mirrored target condition'),('A4d','Direct target binding')]:
    d = endpoint_data[endpoint_data.arm == arm].sort_values('delta')
    agg[arm] = {'title':title, **{c:d[c].tolist() for c in ['n','final','mark']}}

fig, axes = plt.subplots(2, 2, figsize=(7.25, 5.15), sharex=True, sharey=True)
axes = axes.ravel()
major4 = [0,1,5,20,200]
endpoint_specs = [
    ('final', 'Final removal', COLORS[0], 'o', '-'),
    ('mark', 'Deletion-start marker', COLORS[1], 's', '--'),
]
for i, (arm, dct) in enumerate(agg.items()):
    ax = axes[i]
    n = np.asarray(dct['n'], float)
    for key, label, color, marker, ls in endpoint_specs:
        k = np.asarray(dct[key], float)
        p = k/n
        lo, hi = wilson(k, n)
        errplot(ax, delays4, p, lo, hi, color=color, marker=marker, linestyle=ls, label=label,
                zorder=4 if key == 'mark' else 3)
    style_axis(ax, y_label=(i % 2 == 0))
    set_delay_axis(ax, list(delays4), major4, linthresh=1.0, linscale=1.0)
    panel = ['(a)','(b)','(c)','(d)'][i]
    ax.text(0.0, 1.055, panel, transform=ax.transAxes, fontsize=9.2, fontweight='bold', ha='left', va='bottom')
    ax.set_title(dct['title'], loc='center', pad=7.0, fontsize=9.1, fontweight='normal')
    if i < 2:
        ax.tick_params(labelbottom=False)
handles, labels = axes[0].get_legend_handles_labels()
fig.supylabel('Probability of invalidity', x=0.012, fontsize=9.3)
fig.supxlabel(r'Writer-release delay after client-gate end, $\delta$ (ms)', y=0.025, fontsize=9.3)
fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 0.997), ncol=2,
           frameon=False, handlelength=2.5, columnspacing=1.6, borderaxespad=0)
fig.subplots_adjust(left=0.09, right=0.995, bottom=0.13, top=0.84, hspace=0.30, wspace=0.18)
fig.savefig(HERE/'Fig4.pdf')
fig.savefig(HERE/'Fig4.png', dpi=600)
plt.close(fig)

# ---------- Figure 5: writer intensity ----------
s10 = pd.read_csv(INPUTS / 'tab-S10a.csv', dtype=str, keep_default_na=False)
s10['invalid'] = s10['invalid_ci'].str.extract(r'([0-9.]+)')[0].astype(float)
for c in ['lambda', 'abort', 'valid']:
    s10[c] = s10[c].astype(float)

fig, axes = plt.subplots(1, 3, figsize=(7.25, 3.52), sharex=True)
series5 = [
    ('A1', 'Selective client gate (DCJI)', COLORS[0], 'o', '-'),
    ('A2', 'Full-refresh client gate', COLORS[1], 's', '--'),
    ('A3', 'External-state webhook', COLORS[2], '^', '-.'),
    ('A4d', 'Direct target binding', COLORS[3], 'D', ':'),
]
panels = [('invalid', '(a)  Invalid executions'), ('abort', '(b)  Correct aborts'), ('valid', '(c)  Valid executions')]
for j, (ax, (col, title)) in enumerate(zip(axes, panels)):
    for arm, label, color, marker, ls in series5:
        d = s10[s10.arm == arm].sort_values('lambda')
        x = d['lambda'].to_numpy(float)
        y = d[col].to_numpy(float)
        # Each stress cell has 400 responses; percentile bootstrap of the
        # empirical response indicator is the Binomial(400, p_hat) percentile interval.
        n = 400
        p = y/1000.0
        lo = binom.ppf(0.025, n, p)/n*1000.0
        hi = binom.ppf(0.975, n, p)/n*1000.0
        yerr = np.vstack([y-lo, hi-y])
        ax.errorbar(x, y, yerr=yerr, color=color, marker=marker, linestyle=ls,
                    linewidth=1.45, markersize=4.8, markeredgewidth=0.8,
                    elinewidth=0.8, capsize=2.0, capthick=0.8, label=label,
                    zorder=4 if arm == 'A4d' else 3)
    ax.set_xscale('log', base=10)
    ax.set_xticks([1,10,100])
    ax.set_xticklabels(['1','10','100'], rotation=0)
    ax.set_xlim(0.8, 125)
    ax.set_ylim(-20, 1025)
    ax.set_yticks([0,200,400,600,800,1000])
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.tick_params(axis='both', which='both', direction='out')
    ax.grid(False)
    panel = ['(a)', '(b)', '(c)'][j]
    ax.text(0.0, 1.055, panel, transform=ax.transAxes, fontsize=9.2, fontweight='bold', ha='left', va='bottom')
    ax.set_title(title.split('  ',1)[1], loc='center', pad=7.0, fontsize=9.1, fontweight='normal')
    ax.set_xlabel(r'Writer rate $\lambda$ (s$^{-1}$)')
    if j == 0:
        handles, labels = ax.get_legend_handles_labels()
    if j > 0:
        # Retain numeric scale in each panel because the three outcomes are
        # independently interpreted in the manuscript.
        pass
fig.supylabel('Responses per 1,000', x=0.012, fontsize=9.3)
fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 0.995), ncol=2,
           frameon=False, handlelength=2.5, columnspacing=1.5, borderaxespad=0)
fig.subplots_adjust(left=0.09, right=0.995, bottom=0.19, top=0.74, wspace=0.22)
fig.savefig(HERE/'Fig5.pdf')
fig.savefig(HERE/'Fig5.png', dpi=600)
plt.close(fig)

print('Generated Fig2-Fig5 as PDF and 600-dpi PNG.')
