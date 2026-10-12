#!/usr/bin/env python3
"""E39 experiment runner: orchestrates sub-experiments A-G (protocol/E39_DESIGN_SPEC.txt v1 + E39-F addendum,
protocol/ANALYSIS_PLAN_E39.txt, protocol/E39_RECORD_SCHEMA.json, protocol/E39_ORACLE_INPUT_NOTES.txt).

Defensive evaluation by the paper's authors on their OWN disposable kind cluster "e39". The runner logs raw
facts only (e39.trial.v1); ground truth comes from the offline oracle (analysis/oracle_e39.py) using the
apiserver audit log.

Modes (exactly one)
  --make-schedule OUT       write SCHEDULE.json (e39.schedule.v1; stage 1, or stage 2 = E39-A top-up from
                            analysis/stage2_topup_e39.py output, trial ids >= 100001). No network.
  --dry-run                 counts per sub-experiment + runtime estimate. No network (no socket is opened).
  --offline-selftest        every sub-experiment against 127.0.0.1 fakes (fake_api.FakeApiServer, a TLS twin
                            of it for the client-go responder, tests_harness FakeKube for e40-load); writes the
                            files a live run writes and (if venv_analysis exists) runs the analysis gate on them.
  (live)                    execute a LOCKED schedule (--only X[,Y] selects sub-experiments) against
                            https://127.0.0.1:<port> of kind-e39 (CA pinned, ServiceAccount tokens).

Sub-experiment -> mechanism (all Pod DELETEs use gracePeriodSeconds 0 and a UID precondition)
  A  arms A1/A3 (e38v2 fenced DCJI gate) and A4d/A4m (arms_commit: ONE Pod GET, DELETE with {uid, RV});
     single async writer (separate WriterProcess) released at gate end + delta. A4d writer PATCHes the Pod
     annotation; A4m writer PATCHes the ConfigMap and the mirror controller (separate process, mirror
     identity) copies it. The mirror is STARTED ONLY FOR A4m TRIALS (before the gate, after the fixture reset
     has already written matching annotations) and stopped after the trial, with a LONG periodic resync
     (MIRROR_RESYNC_S = 3600 s, i.e. never inside a trial): a resync running during other arms' trials could
     re-write Pod annotations (bumping the Pod RV -> false 409s) or revert an A4d direct annotation write.
     Watch events alone drive propagation inside an A4m trial; the per-trial mirror log goes to
     mirror_log.jsonl (tagged with trial_id); trial.mirror = writer commit + matching mirror entry.
  B  Poisson arrivals (harness_arrivals.ArrivalWriterProcess), anchor = gate start, halted at DELETE
     response + 50 ms (gate end + 50 ms when not dispatched); first arrival invalidates. Configmap arms use
     seq key e40-arrival-seq, A4d uses Pod annotation e38.seq so every arrival is a distinct commit.
  C  guard-only paired acquisition (harness_delay_proxy.PairedAcquisitionDriver) through the loopback delay
     proxy (responder identity only; c added to single-ConfigMap GETs); no Pod DELETE (analysis plan 5.C:
     "guard-only runs: gate latency incl. planning, labelled"). ea_attack = evidence b,d,f cut to false before
     the guard (reset identity); stable = all true.
  D  as A (A1/A3, async_scope) under background load (harness_load.LoadProcess in e40-load, separate
     identity) with HostGuard throttling; load level switched per (replicate, level) segment (settle time
     before the first trial of a segment); per trial: load_level (effective), requested_level, host.
  E  as A (A1/A3) but the action is CREATE of an isolation NetworkPolicy (action_netpol); A3 labels the
     NetworkPolicy e40-webhook=enforce; the policy is deleted by the reset identity after the trial.
  F  A1 python (as A) + client-go responder (goclient_bridge.GoClient; Go times on CLOCK_MONOTONIC).
     A1_dcji_go: split ops gate_uid -> writer armed at the Go-reported TRUE gate end -> delete_uid
     (delete.t_send_mono_ns / t_resp_mono_ns from Go). A4d_commit_direct_go: combined op gate_delete_rv
     (the binary has no split annotation gate); writer armed at a PREDICTED gate end (running median of the
     true Go gate durations); true gate end, DELETE send/response and the anchor error are recorded in
     trial.go / trial.delete (analysis uses realised commit order).
  G  A1/A2 fenced gate after pre-gate cuts (conditions_e39.apply_condition, reset identity, recorded in
     cut_writes with audit ids / RVs); fixture.initial_values = the reset (pre-cut) state.

Safety rails: context must be exactly kind-e39; API https://127.0.0.1:<port> only (E39Client); namespace
e40-<run_id> (never e40-system / e40-load); identities verified by SelfSubjectReview; LOCK verified with
protocol/lock_hash_e39.py AND it must cover this exact schedule file; forbidden seeds refused; resume skips
recorded trial ids; STOP file; MemAvailable guards; INFRA_ERROR abort guard.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import multiprocessing
import os
import random
import shutil
import ssl
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import e39_kube as K  # noqa: E402
from e39_kube import APIError, E39Client, mono_ns  # noqa: E402

DEFAULT_F5 = WORKSPACE / 'frozen_ref' / 'source_provenance' / 'E37_F5_PHYS_ORIGINAL.json'
DEFAULT_GO_BIN = HERE / 'goclient' / 'bin' / 'e40-goclient'
DEFAULT_GO_BUILD_INFO = HERE / 'goclient' / 'BUILD_INFO.txt'
ANALYSIS_DIR = WORKSPACE / 'analysis'
ANALYSIS_PLAN = WORKSPACE / 'protocol' / 'ANALYSIS_PLAN_E39.txt'

SCHEDULE_SCHEMA = 'e39.schedule.v1'
TRIAL_SCHEMA = 'e39.trial.v1'
SESSION_SCHEMA = 'e39.session.v1'
# 20260912 = E38 original; 1772720255 = first E38v2 run; 154080643 = E38v2 confirmatory rerun
FORBIDDEN_SEEDS = (20260912, 1772720255, 154080643)
REQUIRED_CONTEXT = 'kind-e39'
NS_PREFIX = 'e40-'
SA_PREFIX = 'system:serviceaccount:e40-system:e40-'
EXPERIMENTS = ('A', 'B', 'C', 'D', 'E', 'F', 'G')

A1, A2, A3 = 'A1_dcji', 'A2_fullrefresh', 'A3_dcji_webhook'
A4D, A4M = 'A4d_commit_direct', 'A4m_commit_mirror'
A1GO, A4DGO = 'A1_dcji_go', 'A4d_commit_direct_go'
COMMIT_ARMS = (A4D, A4M)
GO_ARMS = (A1GO, A4DGO)
ASYNC = ('async_scope', 'async_app_policy', 'async_evidence_last_literal')
COND_B = 'poisson_scope'
C_CONDS = ('stable', 'ea_attack')
G_CONDS = ('out_of_model_bdf', 'in_model_ace')

# ---- pre-registered design (ANALYSIS_PLAN_E39 section 3; analysis/e39_common.py design_cells)
DELTAS_A = (0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0)   # extended 2026-09-26
DELTAS_SHORT = (0.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0)
LAMBDAS_B = (1.0, 10.0, 100.0)
LATENCIES_C = (0.0, 50.0, 200.0, 1000.0)
ACQ_MODES = ('sequential', 'parallel')
PLANNING_MODES = ('precomputed', 'per_incident')
LOAD_LEVELS = ('idle', 'moderate', 'high')
N_A_ASYNC, N_A_NO_WRITE, N_A_UID = 39, 39, 10
N_B, N_C_PAIRS, N_D, N_E, N_F, N_G = 400, 60, 39, 39, 39, 39
N_TOPUP_TARGET = 93
ARMS_A = (A1, A3, A4D, A4M)
ARMS_B = (A1, A2, A3, A4D)
ARMS_C = (A1, A2)
ARMS_D = (A1, A3)
ARMS_E = (A1, A3)
ARMS_F = ((A1, 'python'), (A1GO, 'client-go'), (A4DGO, 'client-go'))
ARMS_G = (A1, A2)
DESIGN_TOTALS = {'A': 5344, 'B': 4800, 'C': 3840, 'D': 1872, 'E': 1248, 'F': 936, 'G': 156}
STAGE2_TRIAL_ID_BASE = 100000
CANON_TO_RAW_ARM = {'A1': A1, 'A3': A3, 'A4d': A4D, 'A4m': A4M}

# ---- writer targets (E38v2 semantics). A4d: the same record as a Pod annotation.
WRITER_TARGET = {'async_scope': ('response-scope', 'allowed', 'false'),
                 'async_app_policy': ('application-policy', 'allowed', 'false'),
                 'async_evidence_last_literal': ('evidence-a', 'value', 'false')}
ARRIVAL_WINDOW_MS = 2000.0
ARRIVAL_SEQ_KEY_CM = 'e40-arrival-seq'
EA_ATTACK_CUT = ('b', 'd', 'f')      # E21-style evidence attack on the uncertified literals

# ---- runtime-estimate model (dry-run only; no effect on trials). MEASURED in the e39 smoke run
# smoke-e40-20260926a (324 trials, consecutive-trial monotonic anchors): E39-A pod trial mean 0.773 s (median 0.743),
# uid_rebind_control +0.5..1.2 s, A4m - A4d ~ +0.09 s, E39-E 0.73 s (no extra over A), C overhead at c = 0 ~0.05 s.
SEC_POD_TRIAL = 0.77
SEC_UID_EXTRA = 0.80         # uid_rebind_control: second Pod replacement + Ready wait (smoke)
SEC_MIRROR_EXTRA = 0.10      # A4m: mirror start/stop + propagation wait (smoke ~0.09)
SEC_ARRIVAL_TAIL = 0.05      # B: arrivals continue until DELETE response + 50 ms
SEC_NETPOL_EXTRA = 0.0       # E: NetworkPolicy cleanup (smoke: no measurable extra)
SEC_C_OVERHEAD = 0.05        # C: ConfigMap-only reset + bookkeeping, no Pod (smoke)
SEC_C_PLANNING = 0.01        # C: per-incident planner run (estimate)
SEC_D_LEVEL_SETTLE = 10.0    # D: settle time after a load-level switch (per segment)
MIRROR_RESYNC_S = 3600.0
MIRROR_WAIT_S = 2.0
READY_TIMEOUT_S = 90.0
GONE_TIMEOUT_S = 30.0

INFRA_ABORT_FRACTION = 0.05
INFRA_ABORT_CONSECUTIVE = 20
MIN_MEM_AVAILABLE_KB = 2 * 1024 * 1024
ABORT_MEM_AVAILABLE_KB = int(1.5 * 1024 * 1024)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def log_line(fh, msg):
    line = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()) + ' ' + msg
    if fh is not None:
        fh.write(line + '\n')
        fh.flush()
    else:
        print(line, flush=True)


def check_seed(seed):
    if int(seed) in FORBIDDEN_SEEDS:
        raise ValueError('schedule seed %s is refused (used by a prior locked run: %s)' % (seed, FORBIDDEN_SEEDS))


def parse_only(s):
    if not s:
        return tuple(EXPERIMENTS)
    out = []
    for x in str(s).replace(' ', '').upper().split(','):
        x = x.replace('E39-', '')
        if x not in EXPERIMENTS:
            raise ValueError('unknown sub-experiment %r (expected one of %s)' % (x, ','.join(EXPERIMENTS)))
        if x not in out:
            out.append(x)
    return tuple(out)


# =========================================================================== schedule
def _cells(x, reps=None):
    """[(unit dict, n planned)] for one sub-experiment. reps (smoke/self-test) caps every cell at reps."""
    cap = (lambda n: n) if reps is None else (lambda n: min(n, int(reps)))
    out = []
    if x == 'A':
        for arm in ARMS_A:
            for cond in ASYNC:
                for d in DELTAS_A:
                    out.append(({'arm': arm, 'condition': cond, 'delta_ms': d}, cap(N_A_ASYNC)))
            out.append(({'arm': arm, 'condition': 'no_write', 'delta_ms': None}, cap(N_A_NO_WRITE)))
            out.append(({'arm': arm, 'condition': 'uid_rebind_control', 'delta_ms': None}, cap(N_A_UID)))
    elif x == 'B':
        for arm in ARMS_B:
            for lam in LAMBDAS_B:
                out.append(({'arm': arm, 'condition': COND_B, 'lambda_per_s': lam,
                             'arrival_window_ms': ARRIVAL_WINDOW_MS}, cap(N_B)))
    elif x == 'C':          # a C unit is a PAIR (one A1 + one A2 trial)
        for c in LATENCIES_C:
            for acq in ACQ_MODES:
                for cond in C_CONDS:
                    for pl in PLANNING_MODES:
                        out.append(({'condition': cond, 'injected_latency_ms': c, 'acquisition_mode': acq,
                                     'planning_mode': pl}, cap(N_C_PAIRS)))
    elif x == 'D':
        for arm in ARMS_D:
            for lv in LOAD_LEVELS:
                for d in DELTAS_SHORT:
                    out.append(({'arm': arm, 'condition': 'async_scope', 'delta_ms': d, 'load_level': lv}, cap(N_D)))
    elif x == 'E':
        for arm in ARMS_E:
            for cond in ('async_scope', 'async_evidence_last_literal'):
                for d in DELTAS_SHORT:
                    out.append(({'arm': arm, 'condition': cond, 'delta_ms': d}, cap(N_E)))
    elif x == 'F':
        for arm, impl in ARMS_F:
            for d in DELTAS_SHORT:
                out.append(({'arm': arm, 'client_impl': impl, 'condition': 'async_scope', 'delta_ms': d}, cap(N_F)))
    elif x == 'G':
        for arm in ARMS_G:
            for cond in G_CONDS:
                out.append(({'arm': arm, 'condition': cond, 'delta_ms': None}, cap(N_G)))
    else:
        raise ValueError(x)
    return out


def _entry(x, u, block, rep, stage, seed):
    e = {'stage': stage, 'block': block, 'experiment': x, 'arm': u['arm'], 'condition': u['condition'],
         'delta_ms': None if u.get('delta_ms') is None else float(u['delta_ms']), 'rep': rep, 'schedule_seed': seed,
         'lambda_per_s': None if u.get('lambda_per_s') is None else float(u['lambda_per_s']),
         'injected_latency_ms': None if u.get('injected_latency_ms') is None else float(u['injected_latency_ms']),
         'acquisition_mode': u.get('acquisition_mode'), 'planning_mode': u.get('planning_mode'),
         'pair_id': u.get('pair_id'), 'guard_order': u.get('guard_order'),
         'load_level': u.get('load_level') or 'idle', 'client_impl': u.get('client_impl') or 'python',
         'action_type': 'netpol_create' if x == 'E' else 'pod_delete'}
    if x == 'B':
        e['arrival_window_ms'] = float(u.get('arrival_window_ms', ARRIVAL_WINDOW_MS))
    return e


def build_stage1(seed, run_id, reps=None, experiments=EXPERIMENTS):
    """Stage-1 schedule for the given sub-experiments (always generated in the order A..G, so trial ids of
    a sub-experiment do not depend on which others are included). Randomisation WITHIN sub-experiment
    blocks: block r holds one unit of every cell with n > r, shuffled with random.Random('<seed>|E39-X|1')
    (E39-D: per block, the order of the three load levels is shuffled and each level forms one contiguous
    segment in which arm x delta are shuffled; E39-C: units are pairs, guard orders balanced within a block).
    """
    check_seed(seed)
    trials = []
    tid = 0
    pair_id = 0
    for x in EXPERIMENTS:
        cells = _cells(x, reps)
        rng = random.Random('%d|E39-%s|stage1' % (int(seed), x))
        nblocks = max(n for _, n in cells)
        xs = []
        for b in range(nblocks):
            units = [dict(u) for u, n in cells if b < n]
            if x == 'D':
                levels = list(LOAD_LEVELS)
                rng.shuffle(levels)
                seg = []
                for lv in levels:
                    part = [u for u in units if u['load_level'] == lv]
                    rng.shuffle(part)
                    seg.extend(part)
                units = seg
            else:
                rng.shuffle(units)
            if x == 'C':
                orders = (['dcji_first', 'fullrefresh_first'] * ((len(units) + 1) // 2))[:len(units)]
                rng.shuffle(orders)
                for u, o in zip(units, orders):
                    pair_id += 1
                    arms = (A1, A2) if o == 'dcji_first' else (A2, A1)
                    for arm in arms:
                        xs.append(_entry(x, dict(u, arm=arm, pair_id=pair_id, guard_order=o), b, b, 1, int(seed)))
            else:
                for u in units:
                    xs.append(_entry(x, u, b, b, 1, int(seed)))
        if x in experiments:
            for e in xs:
                tid += 1
                trials.append(dict({'trial_id': tid}, **e))
        else:
            tid += len(xs)
    for t in trials:
        t['run_id'] = run_id
    return trials


def load_topups(topup_file):
    """analysis/stage2_topup_e39.py output (e39.stage2_topup.v1 {cells: [...]}) -> list of E39-A cells."""
    doc = json.loads(Path(topup_file).read_text(encoding='utf-8'))
    cells = doc.get('cells') if isinstance(doc, dict) else doc
    if not isinstance(cells, list):
        raise ValueError('top-up file has no cell list')
    out = []
    for c in cells:
        cell = c.get('cell')
        if cell is not None and str(cell[0]).upper() != 'A':
            raise ValueError('stage-2 top-up applies to E39-A only (cell %r)' % (cell,))
        arm = CANON_TO_RAW_ARM.get(c['arm'], c['arm'])
        if arm not in ARMS_A:
            raise ValueError('stage-2 top-up arm %r is not an E39-A arm' % (c['arm'],))
        if c['condition'] not in ASYNC:
            raise ValueError('stage-2 top-up only for async conditions (got %r)' % (c['condition'],))
        n_add = int(c['n_topup']) if 'n_topup' in c else int(c.get('n_target', N_TOPUP_TARGET)) - N_A_ASYNC
        out.append({'arm': arm, 'condition': c['condition'], 'delta_ms': float(c['delta_ms']), 'n_add': n_add,
                    'n_stage1': int(c.get('n_stage1_planned', N_A_ASYNC))})
    return out


def build_stage2(seed, run_id, topup_file):
    """E39-A stage-2 top-up schedule: trial ids >= 100001; one block per added replicate, shuffled."""
    check_seed(seed)
    cells = load_topups(topup_file)
    rng = random.Random('%d|E39-A|stage2' % int(seed))
    trials = []
    maxadd = max([c['n_add'] for c in cells] or [0])
    for k in range(maxadd):
        units = [c for c in cells if k < c['n_add']]
        units = [dict(c) for c in units]
        rng.shuffle(units)
        for c in units:
            e = _entry('A', c, N_A_ASYNC + k, c['n_stage1'] + k, 2, int(seed))
            trials.append(e)
    for i, t in enumerate(trials):
        t['trial_id'] = STAGE2_TRIAL_ID_BASE + 1 + i
        t['run_id'] = run_id
    return [dict({'trial_id': t.pop('trial_id')}, **t) for t in trials]


def schedule_document(trials, stage, seed, run_id, note=None):
    keys = ('trial_id', 'stage', 'block', 'experiment', 'arm', 'condition', 'delta_ms', 'rep', 'schedule_seed',
            'lambda_per_s', 'injected_latency_ms', 'acquisition_mode', 'planning_mode', 'pair_id', 'guard_order',
            'load_level', 'client_impl', 'action_type', 'arrival_window_ms')
    rows = [{k: t[k] for k in keys if k in t} for t in trials]
    counts = {}
    for t in rows:
        counts[t['experiment']] = counts.get(t['experiment'], 0) + 1
    doc = {'schema': SCHEDULE_SCHEMA, 'run_id': run_id, 'schedule_seed': int(seed), 'stage': stage,
           'n_trials': len(rows), 'counts_by_experiment': counts,
           'randomization': 'within sub-experiment blocks, random.Random("<seed>|E39-<X>|stage<s>")',
           'trials': rows}
    if note:
        doc['note'] = note
    return doc


def load_schedule_file(path):
    doc = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(doc, dict) or doc.get('schema') != SCHEDULE_SCHEMA:
        raise ValueError('not an %s document: %s' % (SCHEDULE_SCHEMA, path))
    check_seed(doc['schedule_seed'])
    specs = []
    for e in doc['trials']:
        s = dict(e)
        s['run_id'] = doc['run_id']
        s.setdefault('schedule_seed', doc['schedule_seed'])
        specs.append(s)
    return doc, specs


# =========================================================================== dry-run estimate
def trial_seconds(s):
    x, arm, cond = s['experiment'], s['arm'], s['condition']
    if x == 'C':
        c = float(s['injected_latency_ms']) / 1000.0
        n_reads = 3 if arm == A1 else 6
        reads = n_reads * c if s['acquisition_mode'] == 'sequential' else c
        plan = SEC_C_PLANNING if (arm == A1 and s['planning_mode'] == 'per_incident') else 0.0
        return SEC_C_OVERHEAD + reads + plan
    t = SEC_POD_TRIAL
    if cond == 'uid_rebind_control':
        t += SEC_UID_EXTRA
    if arm == A4M:
        t += SEC_MIRROR_EXTRA
    if x == 'B':
        t += SEC_ARRIVAL_TAIL
    if x == 'E':
        t += SEC_NETPOL_EXTRA
    return t


def runtime_estimate(specs):
    per = {}
    last_level = None
    for s in specs:
        x = s['experiment']
        d = per.setdefault(x, {'trials': 0, 'seconds': 0.0, 'level_switches': 0, 'injected_latency_s': 0.0})
        d['trials'] += 1
        sec = trial_seconds(s)
        d['seconds'] += sec
        if x == 'C':
            d['injected_latency_s'] += sec - SEC_C_OVERHEAD
        if x == 'D' and s.get('load_level') != last_level:
            d['level_switches'] += 1
            d['seconds'] += SEC_D_LEVEL_SETTLE
            last_level = s.get('load_level')
    total = sum(d['seconds'] for d in per.values())
    for d in per.values():
        d['seconds'] = round(d['seconds'], 1)
        d['hours'] = round(d['seconds'] / 3600.0, 3)
        d['injected_latency_s'] = round(d['injected_latency_s'], 1)
    return {'per_experiment': per, 'total_seconds': round(total, 1), 'total_hours': round(total / 3600.0, 3),
            'budget_hours': 5.0, 'within_budget': total <= 5 * 3600,
            'model': {'sec_per_running_pod_delete_trial_measured_smoke': SEC_POD_TRIAL,
                      'uid_rebind_extra_s_smoke': SEC_UID_EXTRA, 'a4m_mirror_extra_s_smoke': SEC_MIRROR_EXTRA,
                      'b_arrival_tail_s': SEC_ARRIVAL_TAIL, 'e_netpol_cleanup_s_smoke': SEC_NETPOL_EXTRA,
                      'c_overhead_s_smoke': SEC_C_OVERHEAD,
                      'c_reads': 'sequential: n_reads x c (A1 3, A2 6); parallel: c',
                      'c_planning_s_estimate': SEC_C_PLANNING, 'd_level_settle_s': SEC_D_LEVEL_SETTLE,
                      'note': 'constants measured in the e39 smoke run smoke-e40-20260926a (budget 5 h, Amendment 1)'}}


# =========================================================================== host snapshot
class HostSampler:
    """Per-trial host snapshot {cpu_pct (since previous sample), free_ram_mb, load_throttled}."""

    def __init__(self):
        self.prev = None

    def sample(self):
        cpu = mem = None
        try:
            from harness_load import read_proc_stat, read_mem_available_mb
            idle, total = read_proc_stat()
            if self.prev is not None and total > self.prev[1]:
                cpu = round(100.0 * (1.0 - (idle - self.prev[0]) / (total - self.prev[1])), 2)
            self.prev = (idle, total)
            mem = round(read_mem_available_mb(), 1)
        except Exception:
            pass
        return {'cpu_pct': cpu, 'free_ram_mb': mem, 'load_throttled': False}


def mem_available_kb():
    try:
        with open('/proc/meminfo') as fh:
            for line in fh:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


# =========================================================================== child processes
class DualWriterClient:
    """Writer-process client: ConfigMap writes as in E38v2; a 'ConfigMap' name that is an e38.* annotation
    key is written as a merge-PATCH of that annotation on the target Pod (A4d writer)."""

    def __init__(self, client):
        self.c = client

    def connect(self):
        self.c.connect()
        return self

    def close(self):
        self.c.close()

    def patch_configmap(self, name, data):
        if name in K.ANNOTATION_KEYS:
            return self.c.patch_pod_annotations({name: str(data.get(name, next(iter(data.values()))))})
        return self.c.patch_configmap(name, data)


class _TaggedSink:
    def __init__(self, path, tag):
        from mirror_controller import JsonlSink
        self.inner = JsonlSink(path)
        self.tag = tag
        self.records = self.inner.records

    def write(self, rec):
        rec = dict(rec, trial_id=self.tag)
        self.inner.write(rec)


def _mirror_host_main(factory, log_path, conn, resync_s, watch_timeout_s):
    from mirror_controller import MirrorController
    pc, wc = factory(), factory()
    mc, sink = None, None
    while True:
        try:
            msg = conn.recv()
        except EOFError:
            break
        tag = msg[0]
        try:
            if tag == 'quit':
                break
            if tag == 'start':
                sink = _TaggedSink(log_path, msg[1])
                mc = MirrorController(pc, wc, sink, resync_s=resync_s, watch_timeout_s=watch_timeout_s).start()
                end = time.monotonic() + 15
                while mc.stats['watch_starts'] < 1 and time.monotonic() < end:
                    time.sleep(0.001)
                conn.send(('started', dict(mc.stats)))
            elif tag == 'entries':
                conn.send(('entries', list(sink.records) if sink else []))
            elif tag == 'stop':
                recs, st = [], {}
                if mc is not None:
                    mc.stop()
                    recs, st = list(sink.records), dict(mc.stats)
                mc = None
                conn.send(('stopped', recs, st))
            else:
                conn.send(('error', 'unknown %r' % (tag,)))
        except Exception as exc:  # pragma: no cover - reported to the parent
            conn.send(('error', repr(exc)))
    if mc is not None:
        mc.stop()
    for c in (pc, wc):
        try:
            c.close()
        except Exception:
            pass


class MirrorHostProcess:
    """Persistent forked process owning the MIRROR identity; runs one MirrorController per A4m trial."""

    def __init__(self, factory, log_path, resync_s=MIRROR_RESYNC_S, watch_timeout_s=30):
        ctx = multiprocessing.get_context('fork')
        self.conn, child = ctx.Pipe(duplex=True)
        self.proc = ctx.Process(target=_mirror_host_main, args=(factory, str(log_path), child, resync_s,
                                                                   watch_timeout_s), name='e40-mirror', daemon=True)
        self.proc.start()
        child.close()
        self.resync_s = resync_s

    def _call(self, *msg, timeout=30):
        self.conn.send(msg)
        if not self.conn.poll(timeout):
            raise APIError('mirror host not responding to %s' % msg[0])
        r = self.conn.recv()
        if r[0] == 'error':
            raise APIError('mirror host: ' + str(r[1]))
        return r

    def start_trial(self, trial_id):
        return self._call('start', trial_id)[1]

    def entries(self):
        return self._call('entries')[1]

    def stop_trial(self):
        r = self._call('stop')
        return r[1], r[2]

    def alive(self):
        return self.proc.is_alive()

    def stop(self):
        try:
            self.conn.send(('quit',))
        except (OSError, EOFError, BrokenPipeError):
            pass
        self.proc.join(timeout=10)
        if self.proc.is_alive():
            self.proc.terminate()


def make_loopback_cert(dirpath):
    """Ephemeral self-signed cert with IP SAN 127.0.0.1 (openssl CLI) for the TLS listener of the
    loopback delay proxy (live) or the fake apiserver's TLS twin (self-test). Returns (cert, key) or None."""
    exe = shutil.which('openssl')
    if not exe:
        return None
    cert, key = os.path.join(dirpath, 'loopback-cert.pem'), os.path.join(dirpath, 'loopback-key.pem')
    subprocess.run([exe, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', cert, '-days', '2',
                    '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1'], check=True,
                   capture_output=True)
    os.chmod(key, 0o600)
    return cert, key


# =========================================================================== environment
class Env:
    """Clients and helper processes of one run (live or offline self-test)."""

    def __init__(self, *, base, namespace, run_id, seed, out_dir, experiments, token_dir=None, ca_file=None,
                 load_base=None, go_argv=None, go_version=None, f5_path=DEFAULT_F5, horizon=30.0, selftest=False,
                 latency_scale=1.0, level_settle_s=SEC_D_LEVEL_SETTLE, logfh=None, proxy_tls=None,
                 mirror_wait_s=MIRROR_WAIT_S):
        self.base, self.ns, self.run_id, self.seed = base, namespace, run_id, int(seed)
        self.out = Path(out_dir)
        self.exps = set(experiments)
        self.token_dir = Path(token_dir) if token_dir else None
        self.ca = ca_file
        self.load_base = load_base or base
        self.go_argv, self.go_version = go_argv, go_version
        self.f5_path, self.horizon = f5_path, horizon
        self.selftest = selftest
        self.latency_scale = float(latency_scale)
        self.level_settle_s = float(level_settle_s)
        self.logfh = logfh
        self.mirror_wait_s = mirror_wait_s
        self.host = HostSampler()
        self.go = None
        self.go_gate_hist = {}          # arm -> recent Go gate durations (command send -> last read received)
        self.load_current = None
        self.c_drivers = {}
        self.proxy_delay = None
        self.planner = None
        self.tmp = tempfile.mkdtemp(prefix='e39run_')
        os.chmod(self.tmp, 0o700)
        # ---- fork every helper process BEFORE the parent opens its own connections (E38v2 rule)
        from writer import WriterProcess
        self.wproc = WriterProcess(lambda: DualWriterClient(self.mk('writer')))
        self.wproc.ping()
        self.arrivals = None
        if 'B' in self.exps:
            from harness_arrivals import ArrivalWriterProcess
            self.arrivals = ArrivalWriterProcess(lambda: self.mk('writer'))
            self.arrivals.ping()
        self.mirror = None
        if 'A' in self.exps:
            self.mirror = MirrorHostProcess(lambda: self.mk('mirror'), self.out / 'mirror_log.jsonl')
        self.proxy = None
        self.proxy_cert = None
        if 'C' in self.exps:
            from harness_delay_proxy import DelayProxyProcess
            kw = {'match_user_agent': 'e40-runner/responder'}
            if base.startswith('https://'):
                kw['upstream_ca'] = ca_file
            if proxy_tls:
                kw['tls_cert'], kw['tls_key'] = proxy_tls
                self.proxy_cert = proxy_tls[0]
            self.proxy = DelayProxyProcess(base, **kw)
        self.load = None
        if 'D' in self.exps:
            from harness_load import LoadProcess, load_client_factory, load_object_names
            self.load_names = load_object_names(8)
            tok = str(self.token_dir / 'load.token') if (self.token_dir and self.load_base.startswith('https')) else None
            self.load_factory = load_client_factory(self.load_base, self.load_names, token_path=tok,
                                                    ca_path=ca_file if self.load_base.startswith('https') else None)
            self.load = LoadProcess(guard_kwargs={}, client_factory=self.load_factory, names=self.load_names,
                                    level='idle', n_workers=8, seed=self.seed,
                                    log_fn=None)
        self.responder = self.mk('responder')
        self.reset = self.mk('reset')

    # ---- clients
    def mk(self, identity, base=None, ca=None):
        base = base or self.base
        https = base.startswith('https://')
        tok = str(self.token_dir / ('%s.token' % identity)) if (https and self.token_dir) else None
        if https and tok is None:
            tok = self._dummy_token()
        return E39Client(base, self.ns, identity, token_path=tok, ca_path=(ca or self.ca) if https else None)

    def _dummy_token(self):
        p = os.path.join(self.tmp, 'selftest.token')
        if not os.path.exists(p):
            with open(p, 'w') as fh:
                fh.write('selftest-dummy-token\n')
        return p

    def close(self):
        for c in (getattr(self, 'responder', None), getattr(self, 'reset', None)):
            try:
                c and c.close()
            except Exception:
                pass
        for d in self.c_drivers.values():
            try:
                d.close()
            except Exception:
                pass
        if self.go is not None:
            try:
                self.go.close()
            except Exception:
                pass
        for p in (self.wproc, self.arrivals, self.mirror, self.proxy):
            if p is not None:
                try:
                    p.stop()
                except Exception:
                    pass
        if self.load is not None:
            try:
                self.load_final = self.load.stop()
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- experiment-level preparation
    def prepare(self, spec):
        x = spec['experiment']
        if self.load is not None and x != 'D' and self.load_current not in (None, 'idle'):
            self.load.set_level('idle')
            self.load_current = 'idle'
        if x == 'D':
            if not getattr(self, '_load_objects_ok', False):
                from harness_load import ensure_objects
                c = self.load_factory()
                try:
                    ensure_objects(c, self.load_names)
                finally:
                    c.close()
                self._load_objects_ok = True
            lv = spec['load_level']
            if lv != self.load_current:
                self.load.set_level(lv)
                self.load_current = lv
                log_line(self.logfh, 'LOAD level -> %s (settle %.1f s)' % (lv, self.level_settle_s))
                time.sleep(self.level_settle_s)
        if x == 'F' and spec['arm'] in GO_ARMS and self.go is None:
            import goclient_bridge as gb
            if not self.go_argv:
                raise APIError('E39-F client-go arm scheduled but no Go client configured')
            self.go = gb.GoClient(self.go_argv).start()
            r = self.go.ping()
            if not r.get('ok'):
                raise APIError('Go client ping failed: %r' % (r,))

    def c_driver(self, acq, planning):
        key = (acq, planning)
        if key not in self.c_drivers:
            from harness_delay_proxy import PairedAcquisitionDriver, make_dcji_planner
            if self.planner is None:
                self.planner = make_dcji_planner(str(self.f5_path), self.horizon)
            proxy_base = self.proxy.base_url

            def factory():
                return self.mk('responder', base=proxy_base, ca=self.proxy_cert)
            self.c_drivers[key] = PairedAcquisitionDriver(factory, self.planner, acq, planning, 0.0, self.seed, 1)
        return self.c_drivers[key]

    def set_proxy_delay(self, c_ms):
        applied = float(c_ms) * self.latency_scale
        if applied != self.proxy_delay:
            self.proxy.set_delay_ms(applied)
            self.proxy_delay = applied
        return applied


# =========================================================================== trial building blocks
def _not_dispatched(uid=''):
    return {'dispatched': False, 't_send_mono_ns': None, 't_send_wall_ns': None, 't_resp_mono_ns': None,
            'http_status': None, 'reason': None, 'audit_id': None, 'expected_uid': uid or '',
            'deleted_object_resource_version': None}


def base_record(spec):
    r = {'schema': TRIAL_SCHEMA, 'run_id': spec['run_id'], 'trial_id': spec['trial_id'], 'stage': spec['stage'],
         'block': spec['block'], 'experiment': spec['experiment'], 'arm': spec['arm'],
         'client_impl': spec.get('client_impl') or 'python', 'action_type': spec.get('action_type') or 'pod_delete',
         'condition': spec['condition'], 'delta_ms': spec.get('delta_ms'), 'rep': spec['rep'],
         'schedule_seed': spec['schedule_seed'], 'lambda_per_s': spec.get('lambda_per_s'),
         'injected_latency_ms': spec.get('injected_latency_ms'), 'acquisition_mode': spec.get('acquisition_mode'),
         'planning_mode': spec.get('planning_mode'), 'planning_ms': None, 'guard_order': spec.get('guard_order'),
         'pair_id': spec.get('pair_id'), 'load_level': spec.get('load_level') or 'idle', 'load_observed_rps': None,
         'host': {'cpu_pct': None, 'free_ram_mb': None, 'load_throttled': False},
         'fixture': None, 'gate': None, 'writer': None, 'writer_arrivals': None, 'delete': _not_dispatched(),
         'delete_preconditions': {'uid': None, 'resource_version': None}, 'bound_state': None, 'mirror': None,
         'netpol': None, 'effect': {'verified': False, 't_verified_mono_ns': None}, 'go_client_version': None,
         'webhook_request_uid': None, 'status': 'COMPLETE', 'error': None}
    if spec['experiment'] == 'D':
        r['requested_level'] = spec.get('load_level')
    return r


def fixture_values(spec):
    """Policy values written by the reset (ConfigMap-keyed) = S0 of the trial."""
    v = {n: 'true' for n in K.POLICY_CMS}
    if spec['condition'] == 'async_evidence_last_literal':
        v['evidence-c'] = 'false'      # J = a AND b before the gate (E38v2 fixture semantics)
        v['evidence-e'] = 'false'
    if spec['condition'] == 'ea_attack':
        for k in EA_ATTACK_CUT:
            v['evidence-' + k] = 'false'
    return v


def restore_configmaps(env, values):
    for name in K.POLICY_CMS:
        st, _, _ = env.reset.patch_configmap(name, {K.CM_VALUE_KEY[name]: values[name]})
        if st != 200:
            raise APIError('reset patch %s HTTP %s' % (name, st))
    st, _, _ = env.reset.patch_configmap(K.OBJ_EPOCH, {'sequence': '0'})
    if st != 200:
        raise APIError('reset patch e38-epoch HTTP %s' % st)


def _wait_gone(client, timeout_s=GONE_TIMEOUT_S):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        pod, _ = client.get_pod()
        if pod is None:
            return
        time.sleep(0.02)
    raise APIError('old target pod did not disappear within %.0f s' % timeout_s)


def create_target(env, spec, values, labels=None):
    """Replace the target Pod (reset identity): delete the old one (uid precondition), wait gone, create,
    wait Running+Ready. A4 arms carry the policy values as annotations; A3 (Pod DELETE) carries the webhook
    objectSelector label."""
    import e38v2_runner as R38
    arm = spec['arm']
    pod, _ = env.reset.get_pod()
    if pod is not None:
        st, _, _ = env.reset.delete_pod(pod['metadata']['uid'])
        if st not in (200, 202, 404):
            raise APIError('reset delete failed HTTP %s' % st)
        _wait_gone(env.reset)
    anns = {K.CM_TO_ANN[n]: values[n] for n in K.POLICY_CMS} if arm in (A4D, A4M, A4DGO) else None
    webhook = arm == A3 and spec.get('action_type', 'pod_delete') == 'pod_delete'
    st, _, obj = env.reset.create_target_pod(annotations=anns, extra_labels=labels, webhook=webhook)
    if st not in (200, 201):
        raise APIError('create target pod HTTP %s: %s' % (st, (obj or {}).get('message')))
    uid = ((obj or {}).get('metadata') or {}).get('uid')
    ready = R38.wait_pod_ready(env.reset, uid, timeout_s=READY_TIMEOUT_S)
    return uid, ready, anns


def reset_trial(env, spec, rec, labels=None):
    values = fixture_values(spec)
    rec['fixture'] = {'reset_ok': False, 'pod_uid_decision': '', 'initial_values': dict(values, **{K.OBJ_EPOCH: '0'})}
    restore_configmaps(env, values)
    uid, ready, anns = create_target(env, spec, values, labels)
    rec['fixture'].update({'reset_ok': True, 'pod_uid_decision': uid, 'pod_ready': ready,
                           'pod_annotations': anns, 'pod_labels': labels})
    rec['delete'] = _not_dispatched(uid)
    rec['delete_preconditions'] = {'uid': uid, 'resource_version': None}
    return uid


def build_writer(env, spec):
    from writer import ProcessPostGateWriter
    obj, key, value = WRITER_TARGET[spec['condition']]
    if spec['arm'] in (A4D, A4DGO):
        ann = K.CM_TO_ANN[obj]
        return ProcessPostGateWriter(env.wproc, ann, ann, value, spec['delta_ms'])
    return ProcessPostGateWriter(env.wproc, obj, key, value, spec['delta_ms'])


def python_delete(env, uid):
    t_send, t_send_wall = mono_ns(), time.time_ns()
    status, h, obj = env.responder.delete_pod(uid)
    t_resp = mono_ns()
    ok = status in (200, 202)
    d = {'dispatched': True, 't_send_mono_ns': t_send, 't_send_wall_ns': t_send_wall, 't_resp_mono_ns': t_resp,
         'http_status': status, 'reason': None if ok else (obj or {}).get('reason'),
         'audit_id': h.get('Audit-Id'), 'expected_uid': uid,
         'deleted_object_resource_version': ((obj or {}).get('metadata') or {}).get('resourceVersion') if ok else None,
         'response_message': None if ok or not isinstance(obj, dict) else obj.get('message')}
    return d, ok, obj


def verify_gone(env, uid, delete_obj):
    t = mono_ns()
    pod, _ = env.responder.get_pod()
    gone = pod is None or pod['metadata'].get('uid') != uid
    eff = {'verified': gone, 't_verified_mono_ns': t,
           'delete_response_deletion_timestamp': (((delete_obj or {}).get('metadata') or {}).get('deletionTimestamp')
                                                  if isinstance(delete_obj, dict) else None)}
    polls, gone_t = 1, (t if gone else None)
    end = time.monotonic() + GONE_TIMEOUT_S
    while gone_t is None and time.monotonic() < end:
        time.sleep(0.02)
        pod, _ = env.responder.get_pod()
        polls += 1
        if pod is None or pod['metadata'].get('uid') != uid:
            gone_t = mono_ns()
    eff['t_gone_mono_ns'] = gone_t
    eff['gone_polls'] = polls
    return eff


def uid_rebind(env, spec, rec, values):
    import e38v2_runner as R38
    pod, _ = env.reset.get_pod()
    if pod is not None:
        env.reset.delete_pod(pod['metadata']['uid'])
        _wait_gone(env.reset)
    anns = {K.CM_TO_ANN[n]: values[n] for n in K.POLICY_CMS} if spec['arm'] in COMMIT_ARMS else None
    st, _, obj = env.reset.create_target_pod(annotations=anns, webhook=spec['arm'] == A3)
    if st not in (200, 201):
        raise APIError('uid_rebind: create replacement HTTP %s' % st)
    new_uid = ((obj or {}).get('metadata') or {}).get('uid')
    rdy = R38.wait_pod_ready(env.reset, new_uid, timeout_s=READY_TIMEOUT_S)
    rec['uid_rebind'] = {'replacement_pod_uid': new_uid, 't_replaced_mono_ns': mono_ns(),
                         'replacement_ready_mono_ns': rdy['t_ready_mono_ns']}


def python_gate(env, arm, uid):
    import e38v2_runner as R38
    g = R38.run_gate(env.responder, arm, uid, writer=None)
    g['pod_state'] = R38.pod_state(g.pop('_pod_obj', None))
    return g


def wall_minus_mono():
    return time.time_ns() - time.monotonic_ns()


# =========================================================================== one trial per sub-experiment
def trial_pod_action(env, spec, rec):
    """A, D, F(python) and G: fenced gate (A1/A2/A3) or commit-bound gate (A4d/A4m), then Pod DELETE."""
    import arms_commit as AC
    import conditions_e39 as CG
    arm, cond = spec['arm'], spec['condition']
    uid = reset_trial(env, spec, rec)
    values = fixture_values(spec)
    writer = None
    mirror_on = False
    try:
        if spec['experiment'] == 'G':
            rec['cut_writes'] = CG.apply_condition(env.reset, cond, CG.STORAGE_CONFIGMAP)
        if arm == A4M:
            st = env.mirror.start_trial(spec['trial_id'])
            mirror_on = True
            rec['mirror_meta'] = {'resync_s': env.mirror.resync_s, 'start_stats': st}
        if cond in ASYNC:
            writer = build_writer(env, spec).prearm()
        if arm in COMMIT_ARMS:
            def after_gate(g):
                if writer is not None:
                    writer.arm(g['gate']['t_end_mono_ns'])
                if cond == 'uid_rebind_control':
                    uid_rebind(env, spec, rec, values)
            res = AC.run_commit_bound(env.responder, arm, uid, after_gate=after_gate)
            rec['gate'] = res['gate']
            rec['bound_state'] = res['bound_state']
            rec['delete_preconditions'] = res['delete_preconditions']
            d = dict(res['delete'])
            d['conflict_kind'] = res.get('conflict_kind')
            d.setdefault('response_message', None)
            rec['delete'] = d
            if d.get('http_status') in (200, 202):
                rec['effect'] = verify_gone(env, uid, None)
            if res.get('outcome') == 'ERROR' and d.get('dispatched'):
                raise APIError('commit DELETE transport error: %s' % d.get('reason'))
        else:
            g = python_gate(env, arm, uid)
            rec['gate'] = g
            if writer is not None:
                writer.arm(g['t_end_mono_ns'])
            if cond == 'uid_rebind_control':
                uid_rebind(env, spec, rec, values)
            if g['decision'] == 'EXECUTE':
                d, ok, obj = python_delete(env, uid)
                rec['delete'] = d
                if ok:
                    rec['effect'] = verify_gone(env, uid, obj)
            if g['decision'] == 'UNKNOWN_COHERENCE':
                rec['status'] = 'UNKNOWN'
        if writer is not None:
            res_w = writer.join(timeout=30)
            if res_w is None:
                raise APIError('writer produced no result')
            rec['writer'] = res_w
            if res_w.get('error'):
                rec['status'] = 'INFRA_ERROR'
                rec['error'] = 'writer_error: ' + res_w['error']
        if mirror_on:
            finish_mirror(env, rec)
            mirror_on = False
    finally:
        if writer is not None and rec.get('writer') is None:
            try:
                writer.cancel()
            except Exception:
                pass
        if mirror_on:
            try:
                env.mirror.stop_trial()
            except Exception:
                pass


def finish_mirror(env, rec):
    """Wait (bounded) until the mirror has processed the writer's ConfigMap commit, stop it, and build the
    trial.mirror fragment (writer commit facts + the mirror entry that propagated it)."""
    import arms_commit as AC
    w = rec.get('writer')
    src_obj = src_rv = None
    if isinstance(w, dict) and w.get('http_status') == 200 and w.get('resulting_resource_version'):
        src_obj, src_rv = w['object'], w['resulting_resource_version']
        end = time.monotonic() + env.mirror_wait_s
        while time.monotonic() < end:
            ents = env.mirror.entries()
            if any(e.get('kind') == 'update' and e.get('source_object') == src_obj
                   and _rv_ge(e.get('source_commit_rv'), src_rv) for e in ents):
                break
            time.sleep(0.005)
    entries, stats = env.mirror.stop_trial()
    rec['mirror_meta'] = dict(rec.get('mirror_meta') or {}, stop_stats=stats,
                              n_updates=sum(1 for e in entries if e.get('kind') == 'update' and not e.get('skipped')))
    if src_obj is not None:
        ent = AC.find_mirror_entry(entries, src_obj, src_rv)
        src_wall = w['t_resp_mono_ns'] + wall_minus_mono() if w.get('t_resp_mono_ns') else None
        rec['mirror'] = AC.mirror_fragment(src_obj, src_rv, src_wall, ent)
        rec['mirror_meta']['source_commit_wall_ns_from'] = 'writer response receipt (mono -> wall offset)'
        pm = [e for e in entries if e.get('kind') == 'update' and e.get('source_object') == src_obj
              and e.get('error') == 'pod_missing']
        rec['mirror_meta']['pod_missing_on_propagation'] = bool(pm)


def _rv_ge(a, b):
    try:
        return int(a) >= int(b)
    except (TypeError, ValueError):
        return False


def trial_b(env, spec, rec):
    """E39-B Poisson arrivals from gate start until DELETE response + 50 ms."""
    import arms_commit as AC
    from harness_arrivals import ArrivalWriter
    arm = spec['arm']
    uid = reset_trial(env, spec, rec)
    window = float(spec.get('arrival_window_ms') or ARRIVAL_WINDOW_MS)
    if arm == A4D:
        aw = ArrivalWriter.for_trial(env.arrivals, 'async_scope', env.seed, spec['trial_id'], spec['lambda_per_s'],
                                     window, target_kind='pod_annotation', seq_key=K.SEQ_ANN,
                                     target=(K.SCOPE_ANN, K.SCOPE_ANN, 'false'), pod_uid=uid)
    else:
        aw = ArrivalWriter.for_trial(env.arrivals, 'async_scope', env.seed, spec['trial_id'], spec['lambda_per_s'],
                                     window, seq_key=ARRIVAL_SEQ_KEY_CM)
    aw.prearm()
    done = False
    try:
        anchor = mono_ns()
        aw.arm(anchor)
        t_end_action = None
        if arm == A4D:
            res = AC.run_commit_bound(env.responder, arm, uid)
            rec['gate'], rec['bound_state'] = res['gate'], res['bound_state']
            rec['delete_preconditions'] = res['delete_preconditions']
            d = dict(res['delete'])
            d['conflict_kind'] = res.get('conflict_kind')
            d.setdefault('response_message', None)
            rec['delete'] = d
            t_end_action = d.get('t_resp_mono_ns') or res['gate']['t_end_mono_ns']
        else:
            g = python_gate(env, arm, uid)
            rec['gate'] = g
            t_end_action = g['t_end_mono_ns']
            if g['decision'] == 'EXECUTE':
                d, ok, obj = python_delete(env, uid)
                rec['delete'] = d
                t_end_action = d['t_resp_mono_ns']
            elif g['decision'] == 'UNKNOWN_COHERENCE':
                rec['status'] = 'UNKNOWN'
        aw.halt_after_delete(t_end_action)
        out = aw.join()
        done = True
        rec['writer_arrivals'] = out['writer_arrivals']
        rec['arrivals_meta'] = dict(out['meta'], anchor='gate_start', seq_key=aw.job.get('seq_key'),
                                    target_kind=aw.job.get('target_kind'))
        if any(a.get('error') for a in out['writer_arrivals']):
            rec['status'] = 'INFRA_ERROR'
            rec['error'] = 'arrival_error: ' + next(a['error'] for a in out['writer_arrivals'] if a.get('error'))
        if rec['delete'].get('http_status') in (200, 202):
            rec['effect'] = verify_gone(env, uid, None)
    finally:
        if not done:
            try:
                aw.cancel()
            except Exception:
                pass


def trial_c(env, spec, rec):
    """E39-C guard-only paired acquisition through the delay proxy (no Pod DELETE)."""
    values = fixture_values(spec)
    rec['fixture'] = {'reset_ok': False, 'pod_uid_decision': '', 'initial_values': dict(values),
                      'condition_storage': 'configmap'}
    restore_configmaps(env, values)
    rec['fixture']['reset_ok'] = True
    applied = env.set_proxy_delay(spec['injected_latency_ms'])
    drv = env.c_driver(spec['acquisition_mode'], spec['planning_mode'])
    g = drv.run_guard(spec['arm'])
    rec['planning_ms'] = g['planning_ms']
    reads = [{'object': r['object'], 'resource_version': r['resource_version'], 'value': r['value'],
              't_send_mono_ns': r['t_send_mono_ns'], 't_recv_mono_ns': r['t_recv_mono_ns'],
              'audit_id': r.get('audit_id'), 'http_status': r.get('http_status')} for r in g['reads']]
    rec['acquisition'] = {'t_start_mono_ns': g['t_start_mono_ns'], 't_plan_end_mono_ns': g['t_plan_end_mono_ns'],
                          't_end_mono_ns': g['t_end_mono_ns'], 'refresh_ms': g['refresh_ms'], 'gate_ms': g['gate_ms'],
                          'justified': g['justified'], 'flags_read': list(g['flags_read']), 'n_reads': g['n_reads'],
                          'all_reads_ok': g['all_reads_ok'], 'applied_delay_ms': applied, 'guard_only': True}
    rec['gate'] = {'decision': 'EXECUTE' if g['justified'] else 'ABORT_REPLAN', 't_start_mono_ns': g['t_start_mono_ns'],
                   't_end_mono_ns': g['t_end_mono_ns'],
                   't_end_wall_ns': g['t_end_mono_ns'] + wall_minus_mono(), 'reads': reads, 'ssar_allowed': None}
    if not g['all_reads_ok']:
        rec['status'] = 'INFRA_ERROR'
        rec['error'] = 'C guard read failed: %s' % [r.get('http_status') for r in reads]


def trial_e(env, spec, rec):
    """E39-E: gate (A1/A3), then CREATE of the isolation NetworkPolicy; cleanup by the reset identity."""
    import action_netpol as NP
    arm = spec['arm']
    label = NP.trial_label_value(env.run_id, spec['trial_id'])
    name = NP.netpol_name(spec['trial_id'], env.run_id)
    uid = reset_trial(env, spec, rec, labels=NP.target_pod_labels(label))
    writer = build_writer(env, spec).prearm()
    created = False
    try:
        g = python_gate(env, arm, uid)
        rec['gate'] = g
        writer.arm(g['t_end_mono_ns'])
        if g['decision'] == 'EXECUTE':
            frag = NP.create_isolation_netpol(env.responder, name, label, webhook=(arm == A3))
            rec['netpol'] = frag
            created = frag.get('http_status') in (200, 201)
            if frag.get('http_status') is None:
                raise APIError('netpol CREATE transport error: %s' % frag.get('reason'))
            eff = NP.netpol_effect(env.responder, name)
            rec['effect'] = {'verified': bool(eff['exists']), 't_verified_mono_ns': eff['t_checked_mono_ns'],
                             'netpol_exists': bool(eff['exists']), 'netpol_resource_version': eff['resource_version']}
        elif g['decision'] == 'UNKNOWN_COHERENCE':
            rec['status'] = 'UNKNOWN'
        res_w = writer.join(timeout=30)
        if res_w is None:
            raise APIError('writer produced no result')
        rec['writer'] = res_w
        if res_w.get('error'):
            rec['status'] = 'INFRA_ERROR'
            rec['error'] = 'writer_error: ' + res_w['error']
    finally:
        if rec.get('writer') is None:
            try:
                writer.cancel()
            except Exception:
                pass
        if created or rec.get('netpol'):
            try:
                st = NP.cleanup_isolation_netpol(env.reset, name)
                rec['effect']['netpol_cleanup_status'] = st
            except Exception as exc:
                rec['status'] = 'INFRA_ERROR'
                rec['error'] = (rec.get('error') or '') + ' | netpol_cleanup: %r' % (exc,)


def _go_reads(raw, offset_ns, pod_name):
    out = []
    for name, info in sorted((raw.get('reads') or {}).items(), key=lambda kv: (kv[1] or {}).get('t_send') or 0):
        info = info or {}
        if name == pod_name:
            val = raw.get('pod_uid') or None
        elif name in ('response-scope', 'application-policy'):
            al = info.get('allowed')
            val = None if al is None else ('true' if al else 'false')
        else:
            val = info.get('value') if info.get('value') not in (None, '') else None
        ts, tr = info.get('t_send'), info.get('t_recv')
        out.append({'object': name, 'resource_version': info.get('resourceVersion') or None, 'value': val,
                    't_send_mono_ns': int(ts - offset_ns) if isinstance(ts, int) else 0,
                    't_recv_mono_ns': int(tr - offset_ns) if isinstance(tr, int) else 0,
                    'audit_id': info.get('audit_id') or None})
    return out


def _go_int(v):
    return int(v) if isinstance(v, int) and v > 0 else None


def trial_f_go(env, spec, rec):
    """E39-F client-go responder (binary with split ops; Go times are CLOCK_MONOTONIC ns, the same clock as
    time.monotonic_ns()).
    A1_dcji_go: split flow -- Go op gate_uid (ConfigMap reads + Pod GET + decision, returns t_gate_end_mono_ns);
      the writer is armed at that TRUE gate end; then Go op delete_uid (DELETE with UID precondition, returns
      t_send_mono_ns / t_resp_mono_ns) if the gate decided delete.
    A4d_commit_direct_go: the binary has no split annotation gate (gate_uid decides from ConfigMaps only), so
      the combined op gate_delete_rv (one Pod GET, annotation decision, DELETE with {uid, RV}) is kept; the
      writer is armed at a PREDICTED gate end (command send + running median of previous TRUE Go gate
      durations); the true gate end and the DELETE send time reported by Go are recorded, as is the anchor error."""
    arm = spec['arm']
    uid = reset_trial(env, spec, rec)
    rec['go_client_version'] = env.go_version or 'unknown'
    writer = build_writer(env, spec).prearm()
    try:
        offset = wall_minus_mono()               # wall = mono + offset (Go times are already mono)
        predicted = None
        t_cmd = mono_ns()
        if arm == A1GO:
            raw = env.go.gate_uid(env.ns, K.POD_NAME, {'b': True, 'd': True, 'f': True}, expected_uid=uid)
            t_gate_back = mono_ns()
            g_end = _go_int(raw.get('t_gate_end_mono_ns'))
            gate_ok = bool(raw.get('ok')) and not raw.get('error') and g_end is not None
            writer.arm(g_end if g_end is not None else t_gate_back)
            dres = None
            if gate_ok and raw.get('gate_decision') == 'delete':
                dres = env.go.delete_uid(env.ns, K.POD_NAME, uid)
            t_back = mono_ns()
            ops = [raw.get('op')] + ([dres.get('op')] if dres is not None else [])
        else:
            hist = env.go_gate_hist.setdefault(arm, [])
            est = int(statistics.median(hist[-25:])) if hist else 3_000_000
            predicted = t_cmd + est
            writer.arm(predicted)
            raw = env.go.gate_delete_rv(env.ns, K.POD_NAME, uid)
            t_back = mono_ns()
            t_gate_back = None
            g_end = _go_int(raw.get('t_gate_end_mono_ns'))
            gate_ok = bool(raw.get('ok')) and not raw.get('error') and g_end is not None
            if g_end is not None:
                hist.append(max(g_end - t_cmd, 0))
            dres = raw if raw.get('gate_decision') == 'delete' else None
            ops = [raw.get('op')]
        reads = _go_reads(raw, 0, K.POD_NAME)
        valid = [r for r in reads if r['t_send_mono_ns'] and r['t_recv_mono_ns']]
        g_start = min((r['t_send_mono_ns'] for r in valid), default=t_cmd)
        if g_end is None:
            g_end = max((r['t_recv_mono_ns'] for r in valid), default=t_back)
        rec['gate'] = {'decision': 'EXECUTE' if raw.get('valid') else 'ABORT_REPLAN', 't_start_mono_ns': g_start,
                       't_end_mono_ns': g_end, 't_end_wall_ns': g_end + offset, 'reads': reads, 'ssar_allowed': None}
        dispatched = dres is not None
        status = (dres or {}).get('delete_status') or None
        ok = dispatched and status in (200, 202)
        rv_pre = raw.get('pod_resource_version') or None
        if arm == A4DGO:
            rec['bound_state'] = {'pod_rv_at_gate': str(rv_pre or ''), 'annotations_at_gate': {}}
            rec['delete_preconditions'] = {'uid': uid, 'resource_version': rv_pre}
        if dispatched:
            t_send = _go_int(dres.get('t_send_mono_ns'))
            t_resp = _go_int(dres.get('t_resp_mono_ns'))
            rec['delete'] = {'dispatched': True, 't_send_mono_ns': t_send,
                             't_send_wall_ns': (t_send + offset) if t_send is not None else None,
                             't_resp_mono_ns': t_resp if t_resp is not None else t_back,
                             't_resp_is_upper_bound': t_resp is None, 'http_status': status,
                             'reason': None if ok else (dres.get('delete_reason') or None),
                             'audit_id': dres.get('delete_audit_id') or None, 'expected_uid': uid,
                             'deleted_object_resource_version': (dres.get('deleted_resource_version') or None) if ok else None,
                             'response_message': None if ok else (dres.get('delete_message') or None)}
            if status == 409:
                import arms_commit as AC
                rec['delete']['conflict_kind'] = AC.classify_conflict(409, {'message': dres.get('delete_message') or ''})
            if ok:
                rec['effect'] = verify_gone(env, uid, None)
        rec['go'] = {'op': '+'.join(o or '?' for o in ops), 'ok': raw.get('ok'),
                     'error': raw.get('error') or (dres or {}).get('error') or None,
                     'valid': raw.get('valid'), 'gate_decision': raw.get('gate_decision'),
                     'cmd_send_mono_ns': t_cmd, 'gate_result_recv_mono_ns': t_gate_back, 'result_recv_mono_ns': t_back,
                     'wall_minus_mono_ns': offset, 'writer_armed_at': 'true_gate_end' if arm == A1GO else 'predicted_gate_end',
                     'predicted_gate_end_mono_ns': predicted, 'gate_end_mono_ns': g_end,
                     'anchor_error_ms': ((predicted - g_end) / 1e6) if predicted is not None else 0.0,
                     'gate_end_to_delete_send_ms': (((rec['delete'].get('t_send_mono_ns') or 0) - g_end) / 1e6
                                                    if dispatched and rec['delete'].get('t_send_mono_ns') else None),
                     'note': ('split ops gate_uid + delete_uid; writer armed at the Go-reported gate end'
                              if arm == A1GO else
                              'combined op gate_delete_rv (no split annotation gate in the binary); writer armed at '
                              'predicted gate end; true gate end and DELETE send reported by Go')}
        err = raw.get('error') or (dres or {}).get('error')
        if not gate_ok or not raw.get('ok') or err or (dres is not None and not dres.get('ok')):
            rec['status'] = 'INFRA_ERROR'
            rec['error'] = 'go_client: %s' % (err or 'ok=false or missing t_gate_end_mono_ns')
        res_w = writer.join(timeout=30)
        if res_w is None:
            raise APIError('writer produced no result')
        rec['writer'] = res_w
        if res_w.get('error'):
            rec['status'] = 'INFRA_ERROR'
            rec['error'] = 'writer_error: ' + res_w['error']
    finally:
        if rec.get('writer') is None:
            try:
                writer.cancel()
            except Exception:
                pass


def run_trial(env, spec):
    rec = base_record(spec)
    x = spec['experiment']
    try:
        if x == 'D':
            snap = env.load.snapshot()
            rec['load_level'] = snap.get('load_level') or spec['load_level']
            rec['requested_level'] = snap.get('requested_level') or spec['load_level']
            rec['load_observed_rps'] = snap.get('load_observed_rps')
            h = snap.get('host') or {}
            rec['host'] = {'cpu_pct': h.get('cpu_pct'), 'free_ram_mb': h.get('free_ram_mb'),
                           'load_throttled': bool(h.get('load_throttled'))}
            rec['load'] = {k: snap.get(k) for k in ('target_rps', 'completed', 'errors', 'dropped_ticks')}
            rec['load']['n_throttle_events'] = len(snap.get('events') or [])
        else:
            rec['host'] = env.host.sample()
        if x == 'B':
            trial_b(env, spec, rec)
        elif x == 'C':
            trial_c(env, spec, rec)
        elif x == 'E':
            trial_e(env, spec, rec)
        elif x == 'F' and spec['arm'] in GO_ARMS:
            trial_f_go(env, spec, rec)
        else:
            trial_pod_action(env, spec, rec)
    except Exception as exc:
        rec['status'] = 'INFRA_ERROR'
        rec['error'] = ((rec.get('error') or '') + ' ' + repr(exc)).strip()[:2000]
        if isinstance(rec.get('fixture'), dict) and rec['fixture'].get('reset_ok') is False:
            rec['error'] = 'reset_failed: ' + rec['error']
    if rec.get('fixture') is None:
        rec['fixture'] = {'reset_ok': False, 'pod_uid_decision': '', 'initial_values': {}}
    return rec


# =========================================================================== trial loop
def run_trials(env, specs, raw_path, logfh=None, guards=True, stop_file=None):
    from schema_e39 import validate_record_e39
    raw_path = Path(raw_path)
    done = set()
    if raw_path.exists():
        for line in raw_path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                done.add(json.loads(line)['trial_id'])
    todo = [s for s in specs if s['trial_id'] not in done]
    if done:
        log_line(logfh, 'RESUME: %d trials already recorded, %d to run' % (len(done), len(todo)))
    n = infra = consec = 0
    t0 = time.monotonic()
    stats = {'written': 0, 'infra': 0, 'aborted': None, 'skipped_recorded': len(specs) - len(todo)}
    with raw_path.open('a', encoding='utf-8') as fh:
        for spec in todo:
            if stop_file is not None and Path(stop_file).exists():
                stats['aborted'] = 'stop_file'
                log_line(logfh, 'STOP file present: stopping before trial %s' % spec['trial_id'])
                break
            if guards:
                m = mem_available_kb()
                if m is not None and m < ABORT_MEM_AVAILABLE_KB:
                    stats['aborted'] = 'mem_available_below_1.5GiB (%d kB)' % m
                    log_line(logfh, 'ABORT: ' + stats['aborted'])
                    break
                while True:
                    m = mem_available_kb()
                    if m is None or m >= MIN_MEM_AVAILABLE_KB:
                        break
                    log_line(logfh, 'PAUSE: MemAvailable %d kB < 2 GiB; sleeping 30 s' % m)
                    time.sleep(30)
            try:
                env.prepare(spec)
            except Exception as exc:
                stats['aborted'] = 'prepare_failed: %r' % (exc,)
                log_line(logfh, 'ABORT: ' + stats['aborted'])
                break
            rec = run_trial(env, spec)
            try:
                validate_record_e39(rec)
            except Exception as exc:
                rec['status'] = 'INFRA_ERROR'
                rec['error'] = ((rec.get('error') or '') + ' | schema_validation: ' + repr(exc))[:2000]
            fh.write(json.dumps(rec, sort_keys=True) + '\n')
            fh.flush()
            n += 1
            stats['written'] = n
            if rec['status'] == 'INFRA_ERROR':
                infra += 1
                consec += 1
                stats['infra'] = infra
                log_line(logfh, 'INFRA_ERROR trial %s: %s' % (spec['trial_id'], rec['error']))
                env.responder.close()
                env.reset.close()
            else:
                consec = 0
            if n % 50 == 0 or n == len(todo):
                el = time.monotonic() - t0
                log_line(logfh, 'PROGRESS %d/%d (total recorded %d) infra=%d elapsed=%.0fs rate=%.3fs/trial mem_kb=%s'
                         % (n, len(todo), len(done) + n, infra, el, el / n, mem_available_kb()))
            if guards and (consec >= INFRA_ABORT_CONSECUTIVE or (n >= 100 and infra / n > INFRA_ABORT_FRACTION)):
                stats['aborted'] = 'infra_error_guard (infra=%d, n=%d, consecutive=%d)' % (infra, n, consec)
                log_line(logfh, 'ABORT: ' + stats['aborted'])
                break
            for name in ('wproc', 'arrivals', 'mirror'):
                p = getattr(env, name)
                if p is not None and not p.alive():
                    stats['aborted'] = '%s process died' % name
                    break
            if stats['aborted']:
                log_line(logfh, 'ABORT: ' + stats['aborted'])
                break
    stats['elapsed_s'] = round(time.monotonic() - t0, 1)
    return stats


# =========================================================================== LOCK
def _lock_tool():
    p = WORKSPACE / 'protocol' / 'lock_hash_e39.py'
    spec = importlib.util.spec_from_file_location('e39_lock_hash', p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def verify_lock(lock_path, schedule_path, schedule_doc):
    """Refuses (APIError) unless protocol/lock_hash_e39.py verifies the LOCK completely AND the LOCK covers
    this exact schedule file (stage 1: schedule_sha256; stage 2: stage2_schedule_sha256), seed and run_id."""
    lh = _lock_tool()
    lock = json.loads(Path(lock_path).read_text(encoding='utf-8'))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = lh.main(['verify', str(lock_path)])
    if rc != 0:
        raise APIError('LOCK verify failed: ' + buf.getvalue().strip().replace('\n', ' | ')[:2000])
    if not str(lock.get('status', '')).startswith('LOCKED'):
        raise APIError('LOCK status is not LOCKED')
    stage = int(schedule_doc.get('stage', 1))
    key = 'schedule_sha256' if stage == 1 else 'stage2_schedule_sha256'
    if lock.get(key) != sha256_file(schedule_path):
        raise APIError('LOCK %s does not match %s' % (key, schedule_path))
    if lock.get('schedule_seed') != schedule_doc.get('schedule_seed'):
        raise APIError('LOCK seed != schedule seed')
    if int(schedule_doc.get('schedule_seed')) in FORBIDDEN_SEEDS:
        raise APIError('forbidden schedule seed')
    if lock.get('run_id') and lock.get('run_id') != schedule_doc.get('run_id'):
        raise APIError('LOCK run_id != schedule run_id')
    return lock


# =========================================================================== session
def file_hashes():
    out = {}
    for p in sorted(HERE.glob('*.py')) + sorted((HERE / 'flashir_core').glob('*.py')):
        out[str(p.relative_to(HERE)).replace(os.sep, '/')] = sha256_file(p)
    for p in (DEFAULT_GO_BIN, HERE / 'goclient' / 'go.sum', HERE / 'goclient' / 'go.mod'):
        if p.is_file():
            out[str(p.relative_to(HERE)).replace(os.sep, '/')] = sha256_file(p)
    return out


def go_client_version(build_info=DEFAULT_GO_BUILD_INFO):
    try:
        for line in Path(build_info).read_text().splitlines():
            if line.startswith('client_go_version:'):
                return line.split('client-go', 1)[1].strip() if 'client-go' in line else line.split(':', 1)[1].strip()
    except OSError:
        pass
    return None


def build_session(**kw):
    s = {'schema': SESSION_SCHEMA, 'created_wall': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
         'clock_anchor': {'mono_ns': mono_ns(), 'wall_ns': time.time_ns()}, 'file_sha256': file_hashes(),
         'python': sys.version.split()[0], 'switchinterval_s': sys.getswitchinterval(),
         'mirror': {'started_only_for': 'A4m trials', 'resync_s': MIRROR_RESYNC_S, 'wait_for_propagation_s': MIRROR_WAIT_S},
         'runtime_model': runtime_estimate([])['model']}
    s.update(kw)
    return s


# =========================================================================== offline self-test
def _start_tls_twin(fake, cert, key):
    import fake_api
    httpd = fake_api._QuietServer(('127.0.0.1', 0), fake_api._Handler)
    httpd.daemon_threads = True
    httpd.cluster = fake.cluster
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    return httpd


def write_selftest_lock(out_dir, schedule_path, seed, run_id):
    lock = {'schema': 'e39.lock.v1', 'status': 'LOCKED-SELFTEST-OFFLINE-NOT-FOR-DATA', 'selftest': True,
            'note': 'offline self-test only: lets analysis/analyze_e39.py run its completeness gate on fake data',
            'schedule_seed': int(seed), 'run_id': run_id,
            'design': {'schedule_seed': int(seed), 'forbidden_schedule_seeds': list(FORBIDDEN_SEEDS)},
            'schedule_path': str(schedule_path), 'schedule_sha256': sha256_file(schedule_path),
            'analysis_plan_path': str(ANALYSIS_PLAN), 'analysis_plan_sha256': sha256_file(ANALYSIS_PLAN)}
    p = Path(out_dir) / 'LOCK_SELFTEST.json'
    p.write_text(json.dumps(lock, indent=2) + '\n', encoding='utf-8')
    return p


def run_analysis_gate(out_dir, lock_path, experiments, python=None, reps=100):
    """Run analysis/analyze_e39.py (design check off: the self-test has 1 replicate per cell) on the
    self-test output. Returns (exit code, summary dict)."""
    py = python or str(WORKSPACE / 'venv_analysis' / 'bin' / 'python')
    if not Path(py).exists():
        return None, {'skipped': 'no analysis interpreter at %s' % py}
    outa = Path(out_dir) / 'analysis_selftest'
    cmd = [py, str(ANALYSIS_DIR / 'analyze_e39.py'), '--run-dir', str(out_dir), '--lock', str(lock_path),
           '--lock-root', str(WORKSPACE), '--analysis-plan', str(ANALYSIS_PLAN), '--stage', '1',
           '--experiments', ','.join(experiments), '--design-check', 'off', '--out', str(outa),
           '--bootstrap-reps', str(reps)]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ANALYSIS_DIR), timeout=1800)
    summ = {'exit_code': p.returncode, 'stderr_tail': p.stderr[-2000:], 'stdout_tail': p.stdout[-1500:]}
    res = outa / 'results_E39.json'
    if res.is_file():
        doc = json.loads(res.read_text(encoding='utf-8'))
        summ['completeness_gate'] = doc.get('completeness_gate')
    ref = outa / 'REFUSED_E39.json'
    if ref.is_file():
        summ['refused'] = json.loads(ref.read_text(encoding='utf-8'))
    oc = outa / 'oracle_E39.csv'
    if oc.is_file():
        import csv
        cls = {}
        with open(oc, newline='', encoding='utf-8') as fh:
            for r in csv.DictReader(fh):
                k = '%s/%s' % (r.get('experiment'), r.get('primary_class'))
                cls[k] = cls.get(k, 0) + 1
        summ['primary_classes'] = dict(sorted(cls.items()))
    return p.returncode, summ


def offline_selftest(output_dir, seed=3939, run_id='selftest', reps=1, experiments=EXPERIMENTS,
                     latency_scale=0.02, f5_path=DEFAULT_F5, horizon=30.0, go_bin=DEFAULT_GO_BIN, analysis=True,
                     analysis_python=None, level_settle_s=0.4):
    from e38v2_model import frozen_planner_certificate
    from fake_api import FakeApiServer
    sys.path.insert(0, str(HERE / 'tests_harness'))
    from harness_fakes import FakeKube
    cert = frozen_planner_certificate(horizon, f5_path)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name in ('RAW_TRIALS.jsonl', 'mirror_log.jsonl'):
        if (out / name).exists():
            (out / name).unlink()
    namespace = NS_PREFIX + run_id
    exps = list(experiments)
    notes = []
    tmp = tempfile.mkdtemp(prefix='e39self_')
    tls = make_loopback_cert(tmp) if 'F' in exps else None
    if 'F' in exps and (not Path(go_bin).is_file() or tls is None):
        notes.append('E39-F SKIPPED in self-test: %s' % ('Go client binary missing at %s' % go_bin
                                                          if not Path(go_bin).is_file() else 'openssl missing (TLS twin)'))
        print('SELFTEST: ' + notes[-1], file=sys.stderr)
        exps.remove('F')
    trials = build_stage1(seed, run_id, reps=reps, experiments=exps)
    doc = schedule_document(trials, 1, seed, run_id, note='offline self-test schedule (reps=%s)' % reps)
    sched_path = out / 'SCHEDULE.json'
    sched_path.write_text(json.dumps(doc, indent=1) + '\n', encoding='utf-8')
    _, specs = load_schedule_file(sched_path)
    fake = FakeApiServer(namespace=namespace).start()
    fk = FakeKube().start() if 'D' in exps else None
    twin = None
    go_argv = None
    if 'F' in exps:
        twin = _start_tls_twin(fake, *tls)
        go_argv = [str(go_bin), '--server', 'https://127.0.0.1:%d' % twin.server_address[1], '--ca-file', tls[0],
                   '--token-file', os.path.join(tmp, 'go.token'), '--namespace-prefix', NS_PREFIX]
        with open(os.path.join(tmp, 'go.token'), 'w') as fh:
            fh.write('selftest-dummy\n')
    env = None
    try:
        env = Env(base=fake.base_url, namespace=namespace, run_id=run_id, seed=seed, out_dir=out, experiments=exps,
                  load_base=fk.base_url if fk else None, go_argv=go_argv, go_version=go_client_version(),
                  f5_path=f5_path, horizon=horizon, selftest=True, latency_scale=latency_scale,
                  level_settle_s=level_settle_s, mirror_wait_s=1.0)
        t0 = time.monotonic()
        stats = run_trials(env, specs, out / 'RAW_TRIALS.jsonl', guards=False)
        elapsed = time.monotonic() - t0
    finally:
        if env is not None:
            env.close()
        fake.stop()
        if fk is not None:
            fk.stop()
        if twin is not None:
            twin.shutdown()
            twin.server_close()
        shutil.rmtree(tmp, ignore_errors=True)
    with (out / 'audit.log').open('w', encoding='utf-8') as fh:
        for ev in fake.cluster.audit_log:
            fh.write(json.dumps(ev, sort_keys=True) + '\n')
    with (out / 'webhook_log.jsonl').open('w', encoding='utf-8') as fh:
        for ev in fake.cluster.webhook_log:
            fh.write(json.dumps(ev, sort_keys=True) + '\n')
    recs = [json.loads(line) for line in (out / 'RAW_TRIALS.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    from schema_e39 import validate_record_e39
    invalid = []
    for r in recs:
        try:
            validate_record_e39(r)
        except Exception as exc:
            invalid.append((r['trial_id'], repr(exc)[:300]))
    by = {}
    for r in recs:
        d = by.setdefault(r['experiment'], {'trials': 0, 'COMPLETE': 0, 'UNKNOWN': 0, 'INFRA_ERROR': 0,
                                            'dispatched': 0, 'gate_EXECUTE': 0})
        d['trials'] += 1
        d[r['status']] += 1
        d['dispatched'] += bool((r.get('delete') or {}).get('dispatched') or (r.get('netpol') or {}).get('http_status'))
        d['gate_EXECUTE'] += (r.get('gate') or {}).get('decision') == 'EXECUTE'
    infra_examples = [(r['trial_id'], r['experiment'], r['arm'], r['error']) for r in recs
                      if r['status'] == 'INFRA_ERROR'][:10]
    lock_path = write_selftest_lock(out, sched_path, seed, run_id)
    session = build_session(run_id=run_id, stage=1, schedule_seed=seed, namespace=namespace, context='offline-fake',
                            selftest=True, latency_scale_applied_to_proxy=latency_scale, notes=notes,
                            certificate=cert['certificate'], f5_sha256=cert['f5_sha256'],
                            schedule_sha256=sha256_file(sched_path), trials_written=stats['written'],
                            go_client_version=go_client_version() if 'F' in exps else None)
    (out / 'SESSION.json').write_text(json.dumps(session, indent=2) + '\n', encoding='utf-8')
    summary = {'trials_scheduled': len(specs), 'trials_written': len(recs), 'elapsed_s': round(elapsed, 1),
               'schema_invalid': invalid, 'by_experiment': by, 'infra_examples': infra_examples,
               'audit_events': len(fake.cluster.audit_log), 'webhook_entries': len(fake.cluster.webhook_log),
               'notes': notes, 'run_stats': stats}
    if analysis:
        rc, asumm = run_analysis_gate(out, lock_path, exps, python=analysis_python)
        summary['analysis'] = asumm
    (out / 'SELFTEST_SUMMARY.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2, sort_keys=True))
    ok = (not invalid and len(recs) == len(specs) and not infra_examples
          and (not analysis or summary['analysis'].get('exit_code') in (0, None)))
    return 0 if ok else 1


# =========================================================================== live mode
def live_run(a):
    doc, specs = load_schedule_file(a.schedule)
    only = parse_only(a.only)
    specs = [s for s in specs if s['experiment'] in only]
    if not specs:
        raise APIError('no scheduled trials for --only %s in %s' % (','.join(only), a.schedule))
    if doc['run_id'] != a.run_id:
        raise APIError('schedule run_id %r != --run-id %r' % (doc['run_id'], a.run_id))
    if a.namespace != NS_PREFIX + a.run_id:
        raise APIError('namespace must be e40-<run_id>')
    K.check_namespace(a.namespace)
    lock = None
    if a.lock:
        lock = verify_lock(a.lock, a.schedule, doc)
    elif not a.unlocked_smoke:
        raise APIError('live run requires --lock (or --unlocked-smoke for a smoke run)')
    out = Path(a.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    logfh = open(out / 'runner.log', 'a', encoding='utf-8')
    sys.setswitchinterval(a.switch_interval)
    from e38v2_model import frozen_planner_certificate
    cert = frozen_planner_certificate(a.horizon_seconds, a.f5_path)
    exps = sorted({s['experiment'] for s in specs})
    tok = Path(a.token_dir)
    go_argv = None
    if 'F' in exps:
        go_argv = [str(a.go_binary), '--server', a.api_server, '--ca-file', a.ca_file,
                   '--token-file', str(tok / 'responder.token'), '--namespace-prefix', NS_PREFIX]
    proxy_tls = None
    certdir = None
    if 'C' in exps:
        certdir = tempfile.mkdtemp(prefix='e39proxy_')
        os.chmod(certdir, 0o700)
        proxy_tls = make_loopback_cert(certdir)
        if proxy_tls is None:
            raise APIError('E39-C needs openssl to create the loopback TLS certificate of the delay proxy')
    env = Env(base=a.api_server, namespace=a.namespace, run_id=a.run_id, seed=doc['schedule_seed'], out_dir=out,
              experiments=exps, token_dir=tok, ca_file=a.ca_file, load_base=a.api_server, go_argv=go_argv,
              go_version=go_client_version(), f5_path=a.f5_path, horizon=a.horizon_seconds, logfh=logfh,
              proxy_tls=proxy_tls, level_settle_s=a.level_settle_s)
    try:
        st, _, ver = env.reset.get_version()
        if st != 200 or not str((ver or {}).get('gitVersion', '')).startswith('v1.37'):
            raise APIError('unexpected apiserver version: %r' % (ver,))
        who = {'responder': env.responder.whoami(), 'reset': env.reset.whoami()}
        for ident in ('writer',) + (('mirror',) if 'A' in exps else ()):
            c = env.mk(ident)
            try:
                who[ident] = c.whoami()
            finally:
                c.close()
        for k, u in who.items():
            if u != SA_PREFIX + k:
                raise APIError('identity %s authenticated as %r' % (k, u))
        st, _, _ = env.reset.get_configmap(K.OBJ_EPOCH)
        if st != 200:
            raise APIError('fixture ConfigMaps missing in %s (HTTP %s)' % (a.namespace, st))
        allowed, _ = env.responder.ssar()
        if not allowed:
            raise APIError('responder SSAR delete pods not allowed')
        name = ('SESSION_STAGE%d_%s.json' % (int(doc['stage']), ''.join(exps)))
        sp = out / name
        if sp.exists():
            sp = out / (sp.stem + '_resume_%d.json' % int(time.time()))
        session = build_session(run_id=a.run_id, stage=doc['stage'], schedule_seed=doc['schedule_seed'],
                                namespace=a.namespace, context=a.context, experiments=exps,
                                identities={k: {'username': v, 'token_file': '%s.token (not exported)' % k}
                                            for k, v in who.items()},
                                api_server=a.api_server, ca_sha256=sha256_file(a.ca_file),
                                apiserver_version=ver.get('gitVersion'), certificate=cert['certificate'],
                                f5_path=cert['f5_path'], f5_sha256=cert['f5_sha256'],
                                schedule_file=str(a.schedule), schedule_sha256=sha256_file(a.schedule),
                                n_scheduled=len(specs), lock_file=str(a.lock) if a.lock else None,
                                lock_sha256_field=(lock or {}).get('lock_sha256'), lock_status=(lock or {}).get('status'),
                                unlocked_smoke=bool(a.unlocked_smoke), go_argv=go_argv,
                                go_client_version=env.go_version if 'F' in exps else None,
                                load_level_settle_s=a.level_settle_s)
        sp.write_text(json.dumps(session, indent=2) + '\n', encoding='utf-8')
        log_line(logfh, 'START run_id=%s stage=%s experiments=%s trials=%d identities=%s lock=%s session=%s'
                 % (a.run_id, doc['stage'], ','.join(exps), len(specs), who, (lock or {}).get('status'), sp.name))
        stats = run_trials(env, specs, out / 'RAW_TRIALS.jsonl', logfh=logfh, guards=True, stop_file=out / 'STOP')
        log_line(logfh, 'END ' + json.dumps(stats))
        return 0 if stats['aborted'] is None else 3
    finally:
        env.close()
        if getattr(env, 'load_final', None):
            (out / ('load_final_%d.json' % int(time.time()))).write_text(json.dumps(env.load_final, indent=1) + '\n')
        if certdir:
            shutil.rmtree(certdir, ignore_errors=True)
        logfh.close()


# =========================================================================== CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--make-schedule', type=Path, help='write the schedule document to this path and exit')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--offline-selftest', action='store_true')
    ap.add_argument('--stage', type=int, choices=[1, 2], default=1)
    ap.add_argument('--topup-file', type=Path, help='stage2_cells_E39.json from analysis/stage2_topup_e39.py')
    ap.add_argument('--seed', type=int, help='schedule seed (fresh; never 20260912 / 1772720255 / 154080643)')
    ap.add_argument('--reps', type=int, help='cap every cell at REPS (smoke/pilot schedules only)')
    ap.add_argument('--only', help='comma list of sub-experiments to include/run (default all: A..G)')
    ap.add_argument('--run-id', default='run')
    ap.add_argument('--namespace', help='e40-<run_id> (live)')
    ap.add_argument('--context', default=REQUIRED_CONTEXT)
    ap.add_argument('--api-server', help='https://127.0.0.1:<port> of the kind-e39 apiserver')
    ap.add_argument('--ca-file', help='cluster CA (from `kind get kubeconfig --name e39`)')
    ap.add_argument('--token-dir', help='directory with responder/writer/reset/mirror/load .token files')
    ap.add_argument('--schedule', type=Path, help='schedule file to execute (live) or to estimate (dry-run)')
    ap.add_argument('--output-dir', type=Path)
    ap.add_argument('--lock', type=Path)
    ap.add_argument('--unlocked-smoke', action='store_true', help='allow a live run without LOCK (smoke only)')
    ap.add_argument('--ack-disposable', action='store_true')
    ap.add_argument('--go-binary', type=Path, default=DEFAULT_GO_BIN)
    ap.add_argument('--horizon-seconds', type=float, default=30.0)
    ap.add_argument('--f5-path', type=Path, default=DEFAULT_F5)
    ap.add_argument('--switch-interval', type=float, default=5e-5)
    ap.add_argument('--level-settle-s', type=float, default=SEC_D_LEVEL_SETTLE)
    ap.add_argument('--selftest-reps', type=int, default=1)
    ap.add_argument('--selftest-latency-scale', type=float, default=0.02,
                    help='self-test only: fraction of c actually injected by the proxy (records keep scheduled c)')
    ap.add_argument('--no-analysis', action='store_true', help='self-test: skip the analysis gate')
    a = ap.parse_args(argv)
    modes = sum(bool(x) for x in (a.make_schedule, a.dry_run, a.offline_selftest))
    if modes > 1:
        ap.error('choose one of --make-schedule / --dry-run / --offline-selftest')
    try:
        only = parse_only(a.only)
    except ValueError as exc:
        ap.error(str(exc))
    if a.seed is not None and a.seed in FORBIDDEN_SEEDS:
        ap.error('seed %d is refused (prior locked runs: %s)' % (a.seed, FORBIDDEN_SEEDS))

    if a.offline_selftest:
        if not a.output_dir:
            ap.error('--offline-selftest requires --output-dir')
        return offline_selftest(a.output_dir, seed=a.seed if a.seed is not None else 3939, reps=a.selftest_reps,
                                experiments=only, latency_scale=a.selftest_latency_scale, f5_path=a.f5_path,
                                horizon=a.horizon_seconds, go_bin=a.go_binary, analysis=not a.no_analysis)

    if a.make_schedule or a.dry_run:
        if a.dry_run and a.schedule:
            doc, trials = load_schedule_file(a.schedule)
            trials = [t for t in trials if t['experiment'] in only]
            stage, seed, run_id = doc['stage'], doc['schedule_seed'], doc['run_id']
        else:
            if a.seed is None:
                ap.error('--seed is required to build a schedule')
            seed, run_id, stage = a.seed, a.run_id, a.stage
            if stage == 2:
                if not a.topup_file:
                    ap.error('stage 2 requires --topup-file')
                trials = build_stage2(seed, run_id, a.topup_file)
            else:
                trials = build_stage1(seed, run_id, reps=a.reps, experiments=only)
        counts = {}
        for t in trials:
            counts[t['experiment']] = counts.get(t['experiment'], 0) + 1
        if a.make_schedule:
            if a.make_schedule.exists():
                ap.error('refusing to overwrite %s' % a.make_schedule)
            note = None if a.reps is None else 'reps=%d (smoke/pilot; NOT the pre-registered design)' % a.reps
            doc = schedule_document(trials, stage, seed, run_id, note=note)
            a.make_schedule.parent.mkdir(parents=True, exist_ok=True)
            a.make_schedule.write_text(json.dumps(doc, indent=1) + '\n', encoding='utf-8')
        est = runtime_estimate(trials)
        res = {'stage': stage, 'seed': seed, 'run_id': run_id, 'counts_by_experiment': counts,
               'design_totals_stage1': DESIGN_TOTALS, 'total_trials': len(trials),
               'c_pairs': counts.get('C', 0) // 2, 'runtime_estimate': est,
               'schedule_file': str(a.make_schedule) if a.make_schedule else (str(a.schedule) if a.schedule else None)}
        print(json.dumps(res, indent=2))
        if a.dry_run and a.output_dir:
            a.output_dir.mkdir(parents=True, exist_ok=True)
            (a.output_dir / 'DRY_RUN.json').write_text(json.dumps(res, indent=2) + '\n', encoding='utf-8')
        return 0

    # ---- live run: safety rails
    if a.context != REQUIRED_CONTEXT:
        ap.error('refusing to run: --context must be exactly %r' % REQUIRED_CONTEXT)
    if not a.namespace or not a.namespace.startswith(NS_PREFIX) or a.namespace in K.RESERVED_NAMESPACES \
            or a.namespace.endswith('-system'):
        ap.error('refusing to run: --namespace must be e40-<run_id> (not e40-system / e40-load)')
    if not a.ack_disposable:
        ap.error('live execution requires --ack-disposable (operator acknowledges destructive Pod/ConfigMap/'
                 'NetworkPolicy changes on the disposable e39 kind cluster)')
    if not a.output_dir:
        ap.error('a real run requires --output-dir')
    for need in ('api_server', 'ca_file', 'token_dir', 'schedule'):
        if not getattr(a, need):
            ap.error('live run requires --%s' % need.replace('_', '-'))
    if not str(a.api_server).startswith('https://127.0.0.1:'):
        ap.error('live run requires an https://127.0.0.1:<port> API endpoint')
    try:
        K.check_loopback_url(a.api_server)
    except ValueError as exc:
        ap.error(str(exc))
    try:
        return live_run(a)
    except (APIError, ValueError) as exc:
        ap.error(str(exc))


if __name__ == '__main__':
    sys.exit(main())
