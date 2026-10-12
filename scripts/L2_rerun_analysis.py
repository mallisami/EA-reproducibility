#!/usr/bin/env python3
"""L2 - re-run the locked analyses from the raw logs and compare with the packaged processed results.

For each selected step the script
  1. verifies the SHA-256 of the raw export tarball in experiments/<exp>/data/raw/ against
     provenance/RAW_DATA_HASHES.csv (redacted package copy; the pre-redaction hash is listed there too);
  2. extracts it into a scratch directory (outputs/L2/_work/<step>/);
  3. builds a "lock view" of the experiment's LOCK file: every locked file is hashed in code/<tree>; hashes must
     match the LOCK except for the documented substitutions in provenance/LOCK_SUBSTITUTIONS.json (files
     redacted for privacy or withheld third-party/binary artefacts). Any other mismatch aborts the step;
  4. runs the authors' UNMODIFIED analysis code from code (oracle -> analysis -> independent recomputation ->
     supplementary scripts) exactly as in the original ops scripts (final.sh / collect.sh / analyze_main.sh);
  5. compares every regenerated result file with the packaged copy in experiments/<exp>/data/processed/ (CSV:
     cell by cell, exact string, then numeric with relative tolerance 1e-9; text/JSON: exact after removing
     machine-specific absolute paths).
Steps:  e38v2p  E38v2 preliminary run        (Exp5/Preliminary; Supplement S5, Table S7a/S7b)       ~0.5 min
        e38v2r  E38v2 confirmatory run       (Exp5/Confirmatory; updated Figure 2)     ~1 min
        e39     E39 A-G                      (Exp6; updated Figures 3-4)                ~5-12 min
                (about 2-9 min of it is the operational quality-gate script, which schema-validates all 20,734 records)
        e40     E40 AKS (exploratory)        (Exp7; Fig S7, Tables S22-S24)                           ~1-2 min
        latency E21/E37/E35/E26/E25 robust statistics (Exp2; acquisition-cost analyses)                      ~9-10 min
Usage: python scripts/L2_rerun_analysis.py [--steps e38v2p,e38v2r,e39,e40,latency] [--keep-work]
Needs ~4 GB free disk for the E39 step (audit + API-server logs are ~1.3 GB uncompressed) and ~4 GB RAM.
(Original timing estimates are hardware dependent.)
Exit code 0 if every compared file matches, 1 otherwise. Report: outputs/L2/L2_REPORT.{json,txt}.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from repro_lib import ROOT, EXP, CODE, OUT, Log, sha256_file

PROV = ROOT / 'provenance'
X5P = EXP / 'Exp5_E38v2_Actuation_Exposure' / 'Preliminary'
X5C = EXP / 'Exp5_E38v2_Actuation_Exposure' / 'Confirmatory'
X6 = EXP / 'Exp6_E39_Commit_Bound_Enforcement'
X7 = EXP / 'Exp7_E40_AKS_Replication_Exploratory'
X2 = EXP / 'Exp2_Physical_Acquisition_Cost'
W_EXTERNAL_A2 = '12.3883385'   # value passed by final.sh (LOG_E39_OPERATIONS.txt): trapezoid area of the E38v2 rerun A2 scope curve


def run(cmd, cwd, log, env=None, stdout_path=None, check=True):
    log('$', ' '.join(str(c) for c in cmd))
    fh = open(stdout_path, 'w', encoding='utf-8') if stdout_path else None
    p = subprocess.run([str(c) for c in cmd], cwd=str(cwd), env=env, stdout=fh or subprocess.PIPE,
                       stderr=subprocess.STDOUT if fh is None else subprocess.PIPE, text=True)
    if fh:
        fh.close()
        tail = Path(stdout_path).read_text(encoding='utf-8', errors='replace').strip().splitlines()[-6:]
        if p.stderr:
            tail += p.stderr.strip().splitlines()[-6:]
    else:
        tail = (p.stdout or '').strip().splitlines()[-8:]
    for line in tail:
        log('    ', line[:200])
    if check and p.returncode != 0:
        raise RuntimeError('command failed rc=%d: %s' % (p.returncode, cmd[1] if len(cmd) > 1 else cmd[0]))
    return p.returncode


def raw_hash_table():
    t = {}
    with open(PROV / 'RAW_DATA_HASHES.csv', newline='', encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            t[r['package_path']] = r
    return t


def extract(tgz, dest, log):
    rel = str(tgz.relative_to(ROOT)).replace('\\', '/')
    rec = raw_hash_table().get(rel)
    got = sha256_file(tgz)
    if rec is None or rec['package_sha256'] != got:
        raise RuntimeError('raw tarball hash not as recorded: %s (%s)' % (rel, got))
    log('raw tarball verified: %s sha256=%s (pre-redaction original %s)' % (rel, got[:16], rec['original_sha256'][:16]))
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tgz, 'r:gz') as tf:
        try:
            tf.extractall(dest, filter='data')
        except TypeError:
            tf.extractall(dest)
    return dest


def prepare_e39_audit_view(rd, log):
    """Verify the complete extracted audit evidence and use a JSONL name for temporary data."""
    source = rd / 'audit.log'
    expected = '078e74f4c4bcb4243c91dcd2677d30b37b09c33190a1295727599daa68f32a89'
    if source.stat().st_size != 621803235 or sha256_file(source) != expected:
        raise RuntimeError('Extracted E39 audit evidence differs from its canonical archive member')
    target = rd / 'audit.jsonl'
    source.rename(target)
    log('Extracted E39 audit evidence verified: 621803235 bytes; complete archive member hash matches')
    return target



def lock_view(lock_path, lock_root, step, out_path, log):
    """Write a LOCK copy whose file hashes match the package tree, allowing only documented substitutions."""
    subs = json.load(open(PROV / 'LOCK_SUBSTITUTIONS.json', encoding='utf-8')).get(step, [])
    subs = {s['path']: s for s in subs}
    lock = json.load(open(lock_path, encoding='utf-8'))
    files = lock.get('files') or {}
    entries = {}
    if isinstance(files, list):
        for ent in files + list(lock.get('pinned') or []):
            entries[ent['path']] = ent.get('sha256')
    else:
        entries = dict(files)
    newfiles, applied, errors = {}, [], []
    for rel, h in sorted(entries.items()):
        p = Path(lock_root) / rel
        got = sha256_file(p) if p.is_file() else None
        if got == str(h).lower():
            newfiles[rel] = h
            continue
        s = subs.get(rel)
        if s and s['lock_sha256'] == str(h).lower():
            if s['action'] == 'WITHHELD' and got is None:
                applied.append('%s: WITHHELD (%s)' % (rel, s['reason']))
                continue
            if s['action'] == 'REDACTED' and got == s['package_sha256']:
                newfiles[rel] = got
                applied.append('%s: REDACTED copy %s (%s)' % (rel, got[:12], s['reason']))
                continue
        errors.append('%s: lock %s, package %s' % (rel, str(h)[:12], (got or 'MISSING')[:12]))
    if errors:
        for e in errors:
            log('LOCK MISMATCH', e)
        raise RuntimeError('%d undocumented LOCK mismatches in %s' % (len(errors), step))
    lock['files'] = newfiles
    lock.pop('pinned', None)
    lock['_reproduction_lock_view'] = {'original_lock_sha256': sha256_file(lock_path), 'substitutions': applied,
                                   'note': 'generated by scripts/L2_rerun_analysis.py; see provenance/LOCK_SUBSTITUTIONS.json'}
    json.dump(lock, open(out_path, 'w', encoding='utf-8'), indent=1)
    log('lock view: %d files verified unchanged, %d documented substitutions' % (len(newfiles) - sum('REDACTED' in a for a in applied), len(applied)))
    for a in applied:
        log('    substitution:', a)
    return applied


# ------------------------------------------------------------------ comparison
PATH_RE = re.compile(r'(/[^\s",]*?/(results|_work|analysis_final|analysis)/[^\s",]*)')


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def compare_csv(a, b):
    ra = list(csv.reader(open(a, newline='', encoding='utf-8')))
    rb = list(csv.reader(open(b, newline='', encoding='utf-8')))
    if len(ra) != len(rb):
        return 'DIFFERENT', 'rows %d vs %d' % (len(ra), len(rb))
    status, first = 'IDENTICAL', ''
    for i, (x, y) in enumerate(zip(ra, rb)):
        if x == y:
            continue
        if len(x) != len(y):
            return 'DIFFERENT', 'row %d: %d vs %d fields' % (i, len(x), len(y))
        for j, (u, v) in enumerate(zip(x, y)):
            if u == v:
                continue
            fu, fv = _num(u), _num(v)
            if fu is not None and fv is not None and (fu == fv or (math.isfinite(fu) and abs(fu - fv) <= 1e-9 * max(1.0, abs(fu), abs(fv)))):
                status = 'NUMERICALLY_EQUAL'
                continue
            return 'DIFFERENT', 'row %d col %d (%s): %r vs %r' % (i, j, ra[0][j] if j < len(ra[0]) else '?', u[:40], v[:40])
    return status, first


def compare_text(a, b):
    ta = Path(a).read_text(encoding='utf-8', errors='replace'); tb = Path(b).read_text(encoding='utf-8', errors='replace')
    if ta == tb:
        return 'IDENTICAL', ''
    na = PATH_RE.sub('<PATH>', ta); nb = PATH_RE.sub('<PATH>', tb)
    if na == nb:
        return 'IDENTICAL_EXCEPT_PATHS', ''
    la, lb = na.splitlines(), nb.splitlines()
    for i, (x, y) in enumerate(zip(la, lb)):
        if x != y:
            return 'DIFFERENT', 'line %d: %r vs %r' % (i + 1, x[:80], y[:80])
    return 'DIFFERENT', 'line count %d vs %d' % (len(la), len(lb))


def compare_json_keys(a, b, keys):
    ja = json.load(open(a, encoding='utf-8')); jb = json.load(open(b, encoding='utf-8'))
    missing = [k for k in keys if k not in jb or k not in ja]
    if missing:
        return 'DIFFERENT', 'keys missing: %s' % missing
    diffs = [k for k in keys if ja.get(k) != jb.get(k)]
    return ('IDENTICAL_SELECTED_KEYS' if not diffs else 'DIFFERENT'), ('keys differ: %s' % diffs if diffs else 'keys: %s' % keys)


def cmp(report, step, regenerated, packaged, log, kind='auto', keys=None):
    rel = str(packaged.relative_to(ROOT)).replace('\\', '/')
    if not regenerated.exists():
        st, note = 'NOT_REGENERATED', str(regenerated)
    elif not packaged.exists():
        st, note = 'NO_PACKAGED_COPY', rel
    elif kind == 'json' and keys:
        st, note = compare_json_keys(regenerated, packaged, keys)
    elif regenerated.suffix == '.csv':
        st, note = compare_csv(regenerated, packaged)
    else:
        st, note = compare_text(regenerated, packaged)
    report.append({'step': step, 'packaged_file': rel, 'status': st, 'note': note})
    log('compare %-8s %-95s %s %s' % (step, rel[-95:], st, note[:120]))


# ------------------------------------------------------------------ steps
def step_e38v2(tag, xdir, tree, run_id, log, work, report, py):
    w = work / tag
    rd = extract(xdir / 'data' / 'raw' / ('%s_EXPORT_%s.REDACTED.tar.gz' % ('e38v2' if tag == 'e38v2p' else 'e38v2r', run_id)), w, log) / run_id
    lv = w / 'LOCK_view.json'
    lock_view(rd / 'LOCK_E38v2.json', tree, tag, lv, log)
    out = w / 'analysis_final'
    run([py, 'analyze_e38v2.py', '--run-dir', rd, '--lock', lv, '--lock-root', tree, '--stage', '2', '--pod-events', rd / 'pod_events.jsonl',
         '--schedule', rd / 'SCHEDULE.json', '--schedule', rd / 'SCHEDULE_STAGE2.json', '--out', out] if tag == 'e38v2r' else
        [py, 'analyze_e38v2.py', '--run-dir', rd, '--lock', lv, '--lock-root', tree, '--stage', '2',
         '--schedule', rd / 'SCHEDULE.json', '--schedule', rd / 'SCHEDULE_STAGE2.json', '--out', out],
        tree / 'analysis', log, stdout_path=w / 'analysis_stdout.txt')
    for f in sorted(p.name for p in (xdir / 'data' / 'processed' / 'analysis_final').iterdir()):
        if f.endswith('.csv'):
            cmp(report, tag, out / f, xdir / 'data' / 'processed' / 'analysis_final' / f, log)
    # stage-1-only analysis on the final files (sensitivity S4 / stage-1 columns of Tables S7a and S11)
    out1 = w / 'analysis_stage1_on_final_files'
    # (both schedules are passed, as in the authors' corrected final.sh run - deviation R13 - because the final LOCK carries
    # the stage-2 schedule hash; --stage 1 then restricts the analysis to stage-1 trials)
    run([py, 'analyze_e38v2.py', '--run-dir', rd, '--lock', lv, '--lock-root', tree, '--stage', '1', '--out', out1,
         '--schedule', rd / 'SCHEDULE.json', '--schedule', rd / 'SCHEDULE_STAGE2.json']
        + (['--pod-events', rd / 'pod_events.jsonl'] if tag == 'e38v2r' else []), tree / 'analysis', log,
        stdout_path=w / 'analysis_stage1_stdout.txt')
    cmp(report, tag, out1 / 'cells_E38v2.csv', xdir / 'data' / 'processed' / 'analysis_stage1_on_final_files' / 'cells_E38v2.csv', log)
    ops = tree / 'ops_scripts'
    if tag == 'e38v2r':
        run([py, ops / 'independent_recompute.py', rd, out / 'cells_E38v2.csv', '2', rd / 'pod_events.jsonl'], ops, log,
            stdout_path=w / 'INDEPENDENT_RECOMPUTE.txt')
        cmp(report, tag, w / 'INDEPENDENT_RECOMPUTE.txt', xdir / 'data' / 'processed' / 'INDEPENDENT_RECOMPUTE.txt', log)
        # A3 multi-evaluation sensitivity (Table S14) reads <run>/analysis_final
        shutil.rmtree(rd / 'analysis_final', ignore_errors=True); shutil.copytree(out, rd / 'analysis_final')
        run([py, ops / 'a3_sensitivity.py', rd], ops, log, stdout_path=w / 'A3_MULTI_EVAL_SENSITIVITY.txt')
        cmp(report, tag, w / 'A3_MULTI_EVAL_SENSITIVITY.txt', xdir / 'data' / 'processed' / 'A3_MULTI_EVAL_SENSITIVITY.txt', log)
        # running-Pod report (Table S15b)
        run([py, ops / 'running_pod_report.py', rd, out / 'oracle_trials_E38v2.csv', rd / 'pod_events.jsonl', w / 'RUNNING_POD_REPORT.json'],
            ops, log, stdout_path=w / 'RUNNING_POD_REPORT.txt')
        cmp(report, tag, w / 'RUNNING_POD_REPORT.json', xdir / 'data' / 'processed' / 'RUNNING_POD_REPORT.json', log)
        # API-server log mechanism table (Table S13)
        run([py, ops / 'apiserver_webhook_map.py', rd, rd / 'apiserver.log', out / 'oracle_trials_E38v2.csv', w / 'MECHANISM_APISERVER.json'],
            ops, log, stdout_path=w / 'MECHANISM_APISERVER_SUMMARY.txt')
        cmp(report, tag, w / 'MECHANISM_APISERVER_SUMMARY.txt', xdir / 'data' / 'processed' / 'MECHANISM_APISERVER_SUMMARY.txt', log)
        # preliminary vs confirmatory comparison (Table S12, Fig S2): needs the preliminary analysis_final
        prelim_out = work / 'e38v2p' / 'analysis_final'
        first = prelim_out if prelim_out.exists() else X5P / 'data' / 'processed' / 'analysis_final'
        run([py, ops / 'compare_runs.py', first, out, w / 'COMPARISON_FIRST_VS_RERUN.csv'], ops, log, stdout_path=w / 'COMPARISON_FIRST_VS_RERUN.txt')
        cmp(report, tag, w / 'COMPARISON_FIRST_VS_RERUN.csv', xdir / 'data' / 'processed' / 'COMPARISON_FIRST_VS_RERUN.csv', log)
        # post-hoc timing descriptives for Figure 2
        mb = CODE / 'analysis_utilities'
        run([py, mb / 'e38v2_timing_descriptives_v2.py', rd, w / 'E38V2R_TIMING_DESCRIPTIVES.csv'], mb, log)
        cmp(report, tag, w / 'E38V2R_TIMING_DESCRIPTIVES.csv', xdir / 'data' / 'processed' / 'E38V2R_TIMING_DESCRIPTIVES.csv', log)
        # headline tables (reads only <run>/analysis_final, which now holds the regenerated analysis)
        run([py, ops / 'headline.py', rd], ops, log, stdout_path=w / 'HEADLINE_TABLES.txt')
        cmp(report, tag, w / 'HEADLINE_TABLES.txt', xdir / 'results' / 'HEADLINE_TABLES.txt', log)
    else:
        shutil.rmtree(rd / 'analysis_final', ignore_errors=True); shutil.copytree(out, rd / 'analysis_final')
        run([py, ops / 'a3_sensitivity.py', rd], ops, log, stdout_path=w / 'A3_MULTI_EVAL_SENSITIVITY.txt')
        cmp(report, tag, w / 'A3_MULTI_EVAL_SENSITIVITY.txt', xdir / 'data' / 'processed' / 'A3_MULTI_EVAL_SENSITIVITY.txt', log)
        run([py, ops / 'independent_recompute.py', rd, out / 'cells_E38v2.csv', '2'], ops, log, stdout_path=w / 'INDEPENDENT_RECOMPUTE.txt')
        cmp(report, tag, w / 'INDEPENDENT_RECOMPUTE.txt', xdir / 'data' / 'processed' / 'INDEPENDENT_RECOMPUTE.txt', log)
        run([py, ops / 'headline.py', rd], ops, log, stdout_path=w / 'HEADLINE_E38v2.txt')
        cmp(report, tag, w / 'HEADLINE_E38v2.txt', xdir / 'results' / 'HEADLINE_E38v2.txt', log)


def step_e39(log, work, report, py):
    tree = CODE / 'Exp6_E39'
    w = work / 'e39'
    rd = extract(X6 / 'data' / 'raw' / 'e39_EXPORT_e39-20260926t1515z.REDACTED.tar.gz', w, log) / 'e39-20260926t1515z'
    audit_data = prepare_e39_audit_view(rd, log)
    lv = w / 'LOCK_view.json'
    lock_view(rd / 'LOCK_E39.json', tree, 'e39', lv, log)
    out = w / 'analysis_final'
    run([py, 'analysis/analyze_e39.py', '--run-dir', rd, '--audit', audit_data, '--lock', lv, '--lock-root', tree, '--stage', '2',
         '--schedule', rd / 'SCHEDULE.json', '--schedule', rd / 'SCHEDULE_STAGE2.json', '--pod-events', rd / 'pod_events.jsonl',
         '--w-external', 'A2=' + W_EXTERNAL_A2, '--out', out], tree, log, stdout_path=w / 'analysis_stdout.txt')
    for f in ('cells_E39.csv', 'exposure_E39.csv', 'B_rates_E39.csv', 'C_latency_E39.csv', 'oracle_trials_E39.csv'):
        cmp(report, 'e39', out / f, X6 / 'data' / 'processed' / 'analysis_final' / f, log)
    run([py, 'analysis/recompute_independent_e39.py', '--raw', rd / 'RAW_TRIALS.jsonl', '--audit', audit_data,
         '--schedule', rd / 'SCHEDULE.json', '--schedule', rd / 'SCHEDULE_STAGE2.json', '--compare', out / 'results_E39.json',
         '--out', out / 'independent_E39.json'], tree, log, stdout_path=w / 'independent_stdout.txt', check=False)
    cmp(report, 'e39', out / 'independent_E39.json', X6 / 'data' / 'processed' / 'analysis_final' / 'independent_E39.json', log,
        kind='json', keys=['cells', 'comparison'])
    ops = tree / 'ops_scripts'
    # operational quality gates and headline tables (HEADLINE_TABLES_E39.txt holds e.g. the dispatched-DELETE denominators of Table 7)
    bootstrap = ("import builtins,os,runpy,sys; old=builtins.open; "
                 "alias=%r; target=%r; builtins.open=lambda p,*a,**k:old(target if isinstance(p,(str,os.PathLike)) and os.fspath(p)==alias else p,*a,**k); "
                 "sys.argv=[%r]+sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')") % (str(rd/'audit.log'),str(audit_data),str(ops/'quality_gates.py'))
    run([py, '-c', bootstrap, tree, rd, out / 'oracle_trials_E39.csv', out / 'QUALITY_GATES_E39.json'], ops, log,
        stdout_path=w / 'quality_gates_stdout.txt')
    cmp(report, 'e39', out / 'QUALITY_GATES_E39.json', X6 / 'data' / 'processed' / 'analysis_final' / 'QUALITY_GATES_E39.json', log,
        kind='json')
    # relative paths, run from the directory holding analysis_final/ (as the authors did; the paths are echoed in line 1)
    run([py, ops / 'headline.py', 'analysis_final/results_E39.json', 'analysis_final/independent_E39.json',
         'analysis_final/QUALITY_GATES_E39.json', 'HEADLINE_TABLES_E39.txt'], w, log, stdout_path=w / 'headline_stdout.txt')
    cmp(report, 'e39', w / 'HEADLINE_TABLES_E39.txt', X6 / 'results' / 'HEADLINE_TABLES_E39.txt', log)
    run([py, ops / 'd_split.py', rd / 'RAW_TRIALS.jsonl', out / 'oracle_trials_E39.csv', w / 'EXPLORATORY_D_SPLIT_BY_CLUSTER_STOP.txt'], ops, log)
    cmp(report, 'e39', w / 'EXPLORATORY_D_SPLIT_BY_CLUSTER_STOP.txt', X6 / 'data' / 'processed' / 'EXPLORATORY_D_SPLIT_BY_CLUSTER_STOP.txt', log)


def step_e40(log, work, report, py):
    tree = CODE / 'Exp7_E40'
    w = work / 'e40'
    base = extract(X7 / 'data' / 'raw' / 'E40_EXPORT_a20260926.REDACTED.tar.gz', w, log)
    rd = base / 'results' / 'a20260926'
    lv = w / 'LOCK_view.json'
    lock_view(tree / 'protocol' / 'LOCK_E40.json', tree, 'e40', lv, log)
    out = rd / 'analysis_regen'
    run([py, 'analyze_e39.py', '--run-dir', rd, '--lock', lv, '--lock-root', tree, '--analysis-plan',
         tree / 'code' / 'protocol' / 'ANALYSIS_PLAN_E39.txt', '--stage', '1', '--experiments', 'A', '--design-check', 'off',
         '--out', out], tree / 'code' / 'analysis', log, stdout_path=w / 'analysis_stdout.txt')
    for f in ('cells_E39.csv', 'exposure_E39.csv', 'oracle_trials_E39.csv', 'stage2_candidates_E39.csv'):
        cmp(report, 'e40', out / f, X7 / 'data' / 'processed' / 'analysis' / f, log)
    run([py, 'code/analysis/recompute_independent_e39.py', '--raw', rd / 'RAW_TRIALS.jsonl', '--audit', rd / 'audit.log',
         '--schedule', rd / 'SCHEDULE.json', '--compare', out / 'results_E39.json', '--out', out / 'independent_E40.json'],
        tree, log, stdout_path=w / 'independent_stdout.txt', check=False)
    # supplement (client-time secondary rule, AKS-kind comparison, webhook latency): as run on 2026-09-26, with the E38v2
    # rerun cells as the kind reference (E39 cells did not exist yet; deviation D12)
    ref = X5C / 'data' / 'processed' / 'analysis_final' / 'cells_E38v2.csv'
    sup = w / 'supplement'
    run([py, 'e40/e40_supplement.py', '--run-dir', rd, '--analysis-dir', out, '--out', sup, '--ref-e38v2r', ref], tree, log,
        stdout_path=w / 'supplement_stdout.txt')
    for f in ('client_secondary_cells_E40.csv', 'comparison_kind_vs_aks_E40.csv', 'client_secondary_E40.csv'):
        cmp(report, 'e40', sup / f, X7 / 'data' / 'processed' / 'supplement' / f, log)
    cmp(report, 'e40', sup / 'SUPPLEMENT_E40.json', X7 / 'data' / 'processed' / 'supplement' / 'SUPPLEMENT_E40.json', log,
        kind='json', keys=['n_trials', 'agreement_primary_vs_client_secondary', 'overlap_accepted_deletes', 'webhook_latency',
                           'a4d_availability', 'secondary_reasons', 'n_comparison_cells'])
    cmp(report, 'e40', out / 'independent_E40.json', X7 / 'data' / 'processed' / 'analysis' / 'independent_E40.json', log,
        kind='json', keys=['cells', 'comparison'])
    # exploratory A4d mechanism classification (Table S24) reads <run>/analysis/oracle_trials_E39.csv
    # (the export's own analysis/ and supplement/ copies are removed first so that only regenerated files are compared)
    shutil.rmtree(rd / 'analysis', ignore_errors=True); shutil.copytree(out, rd / 'analysis')
    mech = rd / 'supplement' / 'A4D_MECHANISM_EXPLORATORY.json'
    mech.unlink(missing_ok=True)
    run([py, 'e40/a4d_mechanism.py', rd], tree, log, stdout_path=w / 'A4D_MECHANISM_stdout.txt')
    cmp(report, 'e40', mech, X7 / 'results' / 'A4D_MECHANISM_EXPLORATORY.json', log)


DS = {
    "E21": ('03_extended_study_v9/e21_physical_cost/results', 'E21/results'),
    "E37": ('20_external_host_validation/E37_full_e21_independent_host/results', 'E37/results'),
    "E35": ('20_external_host_validation/E35_independent_host_e21_replication/results', 'E35/results'),
    "E26": ('03_extended_study_v9/e26_independent_cluster_replication/results', 'E26/results'),
    "E25": ('03_extended_study_v9/e25_physical_multicut/results', 'E25/results'),
}


def step_latency(log, work, report, py):
    """latency_reanalysis.py reads ~/dcji_reanalysis_20260926/inputs/frozen/<original paths>; we stage the packaged raw
    files under a scratch HOME so that the script runs unmodified."""
    w = work / 'latency'
    home = w / 'home'
    base = home / 'dcji_reanalysis_20260926'
    for ds, (orig, pkg) in DS.items():
        dst = base / 'inputs' / 'frozen' / orig
        dst.mkdir(parents=True, exist_ok=True)
        for f in (X2 / 'data' / 'raw' / pkg).iterdir():
            shutil.copy2(f, dst / f.name)
    v25 = base / 'inputs' / 'frozen' / '18_revision_v25'
    v25.mkdir(parents=True, exist_ok=True)
    shutil.copy2(X2 / 'data' / 'raw' / 'E21' / 'E21_GUARD_ORDER_SENSITIVITY_V25.csv', v25 / 'E21_GUARD_ORDER_SENSITIVITY_V25.csv')
    env = dict(os.environ); env['DISCOVER_REPRO_TASK_HOME'] = str(home)
    bootstrap = ("import os,runpy; task_home=os.environ['DISCOVER_REPRO_TASK_HOME']; "
                 "original=os.path.expanduser; os.path.expanduser=lambda p: "
                 "task_home if p=='~' else task_home+'/'+p[2:] if p.startswith('~/') else original(p); "
                 "runpy.run_path(%r,run_name='__main__')" % str(CODE / 'Exp2_latency_reanalysis' / 'latency_reanalysis.py'))
    run([py, '-c', bootstrap], w, log, env=env, stdout_path=w / 'latency_stdout.txt')
    outd = base / 'out' / 'latency'
    for f in sorted((X2 / 'data' / 'processed' / 'latency_reanalysis').iterdir()):
        if f.suffix == '.csv':
            cmp(report, 'latency', outd / f.name, f, log)
    cmp(report, 'latency', outd / 'A0_facts.json', X2 / 'data' / 'processed' / 'latency_reanalysis' / 'A0_facts.json', log)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--steps', default='e38v2p,e38v2r,e39,e40,latency')
    ap.add_argument('--keep-work', action='store_true')
    ap.add_argument('--output-dir', type=Path, help='optional separate output directory for a selected replay')
    a = ap.parse_args()
    out = a.output_dir.resolve() if a.output_dir else OUT / 'L2'
    out.mkdir(parents=True, exist_ok=True)
    work = out / '_work'
    log = Log(out / 'L2.log')
    py = sys.executable
    log('package root:', ROOT, '| python', sys.version.split()[0])
    report, timings, failures = [], {}, []
    steps = [s.strip() for s in a.steps.split(',') if s.strip()]
    for s in steps:
        t0 = log.elapsed()
        log('=' * 20, 'step', s)
        try:
            if s == 'e38v2p':
                step_e38v2('e38v2p', X5P, CODE / 'Exp5_E38v2_preliminary', 'e38v2-20260926t0813z', log, work, report, py)
            elif s == 'e38v2r':
                step_e38v2('e38v2r', X5C, CODE / 'Exp5_E38v2_confirmatory', 'e38v2r-20260926t1032z', log, work, report, py)
            elif s == 'e39':
                step_e39(log, work, report, py)
            elif s == 'e40':
                step_e40(log, work, report, py)
            elif s == 'latency':
                step_latency(log, work, report, py)
            else:
                raise RuntimeError('unknown step %s' % s)
        except Exception as ex:  # noqa: BLE001
            failures.append('%s: %s' % (s, ex))
            log('STEP FAILED', s, ex)
        timings[s] = round(log.elapsed() - t0, 1)
        log('step %s finished in %.1f s' % (s, timings[s]))
    ok_status = ('IDENTICAL', 'NUMERICALLY_EQUAL', 'IDENTICAL_EXCEPT_PATHS', 'IDENTICAL_SELECTED_KEYS')
    bad = [r for r in report if r['status'] not in ok_status]
    summary = {'python': sys.version.split()[0], 'steps': steps, 'timings_s': timings, 'elapsed_s': round(log.elapsed(), 1),
               'n_compared': len(report), 'n_match': len(report) - len(bad), 'n_mismatch': len(bad), 'step_failures': failures,
               'comparisons': report}
    json.dump(summary, open(out / 'L2_REPORT.json', 'w', encoding='utf-8'), indent=1)
    with open(out / 'L2_REPORT.txt', 'w', encoding='utf-8') as fh:
        fh.write('L2 summary: %d files compared, %d match, %d mismatch, step failures: %s; elapsed %.1f s\n' % (
            len(report), len(report) - len(bad), len(bad), failures or 'none', log.elapsed()))
        fh.write('timings (s): %s\n' % json.dumps(timings))
        for r in report:
            fh.write('%-8s %-24s %s %s\n' % (r['step'], r['status'], r['packaged_file'], r['note'][:150]))
    if not a.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    log('L2 done: %d compared, %d mismatch, failures=%s' % (len(report), len(bad), failures))
    sys.exit(0 if not bad and not failures else 1)


if __name__ == '__main__':
    main()
