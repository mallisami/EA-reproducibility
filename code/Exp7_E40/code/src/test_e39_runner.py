"""Offline tests of e39_runner (no cluster; 127.0.0.1 fakes only)."""
import json
import os
import socket
import sys
from collections import Counter
from pathlib import Path

import pytest

import e39_runner as ER
import e39_kube as K

ANALYSIS = Path(ER.WORKSPACE) / 'analysis'
SEED = 2718281


def _common():
    sys.path.insert(0, str(ANALYSIS))
    import e39_common as C
    return C


def _canon_cells(trials):
    C = _common()
    cnt = Counter()
    for t in trials:
        arm_c, impl = C.canon_arm(t['arm'], t.get('client_impl'))
        n = {'experiment': t['experiment'], 'arm_c': arm_c, 'client_impl': impl,
             'condition_c': C.canon_condition(t['condition']), 'delta_ms': t.get('delta_ms'),
             'lambda_per_s': t.get('lambda_per_s'), 'injected_latency_ms': t.get('injected_latency_ms'),
             'acquisition_mode': C.canon_acq(t.get('acquisition_mode')),
             'planning_mode': C.canon_planning(t.get('planning_mode')), 'load_level': C.canon_load(t.get('load_level'))}
        cnt[C.cell_key(n)] += 1
    return cnt


@pytest.fixture(scope='module')
def full():
    return ER.build_stage1(SEED, 'r1')


def test_schedule_counts_equal_analysis_design(full):
    C = _common()
    got = _canon_cells(full)
    for x in ER.EXPERIMENTS:
        design = C.design_cells(x)
        mine = {k: v for k, v in got.items() if k[0] == x}
        if x == 'C':        # design_cells counts pairs per cell; each pair = one A1 and one A2 trial
            assert mine == design, x
        else:
            assert mine == design, x
    totals = Counter(t['experiment'] for t in full)
    assert dict(totals) == ER.DESIGN_TOTALS == {'A': 5344, 'B': 4800, 'C': 3840, 'D': 1872, 'E': 1248, 'F': 936, 'G': 156}
    assert len({t['pair_id'] for t in full if t['experiment'] == 'C'}) == 1920


def test_c_pairs_and_guard_order(full):
    pairs = {}
    for i, t in enumerate(full):
        if t['experiment'] == 'C':
            pairs.setdefault(t['pair_id'], []).append((i, t))
    for pid, lst in pairs.items():
        assert sorted(t['arm'] for _, t in lst) == ['A1_dcji', 'A2_fullrefresh']
        (i1, t1), (i2, t2) = lst
        assert i2 == i1 + 1 and t1['guard_order'] == t2['guard_order']
        first = 'A1_dcji' if t1['guard_order'] == 'dcji_first' else 'A2_fullrefresh'
        assert t1['arm'] == first
        assert (t1['injected_latency_ms'], t1['acquisition_mode'], t1['condition'], t1['planning_mode']) == \
               (t2['injected_latency_ms'], t2['acquisition_mode'], t2['condition'], t2['planning_mode'])
    by_block = Counter((t['block'], t['guard_order']) for _, lst in pairs.items() for _, t in lst[:1])
    for b in range(60):
        assert by_block[(b, 'dcji_first')] == by_block[(b, 'fullrefresh_first')] == 16


def test_schedule_fields_ids_blocks(full):
    ids = [t['trial_id'] for t in full]
    assert ids == list(range(1, len(full) + 1))
    for t in full:
        assert t['schedule_seed'] == SEED and t['stage'] == 1 and t['run_id'] == 'r1'
        assert t['load_level'] in ('idle', 'moderate', 'high')
        assert (t['load_level'] != 'idle') <= (t['experiment'] == 'D')
        assert (t['action_type'] == 'netpol_create') == (t['experiment'] == 'E')
        assert (t['client_impl'] == 'client-go') == (t['arm'] in ER.GO_ARMS)
        assert (t['lambda_per_s'] is not None) == (t['experiment'] == 'B')
        assert (t['injected_latency_ms'] is not None) == (t['experiment'] == 'C')
    # block randomisation: A block 0 contains every A cell once (116 units incl. uid_rebind)
    a0 = [t for t in full if t['experiment'] == 'A' and t['block'] == 0]
    assert len(a0) == 4 * (33 + 1 + 1)
    a20 = [t for t in full if t['experiment'] == 'A' and t['block'] == 20]
    assert len(a20) == 4 * (33 + 1) and not any(t['condition'] == 'uid_rebind_control' for t in a20)
    # E39-D: three contiguous load-level segments per block
    d = [t for t in full if t['experiment'] == 'D']
    switches = sum(1 for a, b in zip(d, d[1:]) if a['load_level'] != b['load_level'])
    assert switches <= 3 * 39 - 1


def test_schedule_determinism_and_only(full):
    again = ER.build_stage1(SEED, 'r1')
    assert [(t['trial_id'], t['arm'], t['condition'], t['delta_ms']) for t in again] == \
           [(t['trial_id'], t['arm'], t['condition'], t['delta_ms']) for t in full]
    other = ER.build_stage1(SEED + 1, 'r1')
    assert [(t['arm'], t['condition'], t['delta_ms']) for t in other] != \
           [(t['arm'], t['condition'], t['delta_ms']) for t in full]
    g_only = ER.build_stage1(SEED, 'r1', experiments=('G',))
    assert g_only == [t for t in full if t['experiment'] == 'G']


@pytest.mark.parametrize('seed', [20260912, 1772720255, 154080643])
def test_seed_refusal(seed, tmp_path):
    with pytest.raises(ValueError):
        ER.build_stage1(seed, 'r')
    with pytest.raises(SystemExit):
        ER.main(['--make-schedule', str(tmp_path / 'S.json'), '--seed', str(seed), '--run-id', 'r'])
    assert not (tmp_path / 'S.json').exists()
    doc = ER.schedule_document(ER.build_stage1(SEED, 'r', reps=1, experiments=('G',)), 1, SEED, 'r')
    doc['schedule_seed'] = seed
    p = tmp_path / 'bad.json'
    p.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        ER.load_schedule_file(p)


def test_stage2_topup(tmp_path):
    cells = [{'cell': ['A', 'A1', 'python', 'async_scope', 2.0], 'arm': 'A1', 'condition': 'async_scope',
              'delta_ms': 2.0, 'n_stage1_planned': 39, 'n_topup': 54, 'n_target': 93},
             {'cell': ['A', 'A4m', 'python', 'async_app_policy', 0.5], 'arm': 'A4m', 'condition': 'async_app_policy',
              'delta_ms': 0.5, 'n_stage1_planned': 39, 'n_topup': 54, 'n_target': 93}]
    f = tmp_path / 'stage2_cells_E39.json'
    f.write_text(json.dumps({'schema': 'e39.stage2_topup.v1', 'cells': cells}))
    s2 = ER.build_stage2(SEED, 'r1', f)
    assert len(s2) == 108 and min(t['trial_id'] for t in s2) == 100001
    assert {t['experiment'] for t in s2} == {'A'} and {t['stage'] for t in s2} == {2}
    assert Counter(t['arm'] for t in s2) == {'A1_dcji': 54, 'A4m_commit_mirror': 54}
    assert sorted({t['rep'] for t in s2}) == list(range(39, 93))
    bad = tmp_path / 'bad.json'
    bad.write_text(json.dumps({'cells': [dict(cells[0], cell=['B', 'A1', 'async_scope', 1.0])]}))
    with pytest.raises(ValueError):
        ER.build_stage2(SEED, 'r1', bad)
    out = tmp_path / 'S2.json'
    assert ER.main(['--make-schedule', str(out), '--stage', '2', '--topup-file', str(f), '--seed', str(SEED),
                    '--run-id', 'r1']) == 0
    doc = json.loads(out.read_text())
    assert doc['schema'] == 'e39.schedule.v1' and doc['stage'] == 2 and doc['n_trials'] == 108


def test_dry_run_no_network(monkeypatch, capsys, tmp_path):
    def boom(*a, **k):
        raise AssertionError('network access during dry-run')
    monkeypatch.setattr(socket, 'socket', boom)
    monkeypatch.setattr(socket, 'create_connection', boom)
    assert ER.main(['--dry-run', '--seed', str(SEED), '--run-id', 'r1', '--output-dir', str(tmp_path)]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res['counts_by_experiment'] == ER.DESIGN_TOTALS and res['c_pairs'] == 1920
    per = res['runtime_estimate']['per_experiment']
    assert set(per) == set(ER.EXPERIMENTS)
    assert per['A']['seconds'] > 5344 * 0.76 and per['C']['injected_latency_s'] > 0
    assert (tmp_path / 'DRY_RUN.json').is_file()


def test_runtime_estimate_c_model():
    s = {'experiment': 'C', 'arm': 'A2_fullrefresh', 'condition': 'stable', 'injected_latency_ms': 1000.0,
         'acquisition_mode': 'sequential', 'planning_mode': 'precomputed'}
    assert ER.trial_seconds(s) == pytest.approx(ER.SEC_C_OVERHEAD + 6.0)
    s.update(arm='A1_dcji', acquisition_mode='parallel', planning_mode='per_incident')
    assert ER.trial_seconds(s) == pytest.approx(ER.SEC_C_OVERHEAD + 1.0 + ER.SEC_C_PLANNING)


def test_live_rails(tmp_path):
    base = ['--api-server', 'https://127.0.0.1:6443', '--ca-file', 'ca', '--token-dir', 't', '--schedule', 's',
            '--output-dir', str(tmp_path), '--ack-disposable']
    for bad in (['--context', 'kind-e38v2', '--namespace', 'e40-x'],
                ['--context', 'e39', '--namespace', 'e40-x'],
                ['--context', 'kind-e39', '--namespace', 'e40-system'],
                ['--context', 'kind-e39', '--namespace', 'e40-load'],
                ['--context', 'kind-e39', '--namespace', 'e38v2-x']):
        with pytest.raises(SystemExit):
            ER.main(bad + base)
    with pytest.raises(SystemExit):     # not loopback
        ER.main(['--context', 'kind-e39', '--namespace', 'e40-x', '--api-server', 'https://10.0.0.1:6443']
                + base[2:])
    with pytest.raises(SystemExit):     # no --ack-disposable
        ER.main(['--context', 'kind-e39', '--namespace', 'e40-x'] + base[:-1])
    # unlocked live run refused before any connection (schedule exists, no --lock)
    sch = tmp_path / 'SCHEDULE.json'
    sch.write_text(json.dumps(ER.schedule_document(ER.build_stage1(SEED, 'x', reps=1, experiments=('G',)), 1, SEED, 'x')))
    args = ['--context', 'kind-e39', '--namespace', 'e40-x', '--run-id', 'x', '--api-server', 'https://127.0.0.1:6443',
            '--ca-file', 'ca', '--token-dir', 't', '--schedule', str(sch), '--output-dir', str(tmp_path / 'o'),
            '--ack-disposable']
    with pytest.raises(SystemExit):
        ER.main(args)
    # a LOCK that is not LOCKED is refused
    lock = tmp_path / 'LOCK.json'
    lock.write_text(json.dumps({'schema': 'e39.lock.v1', 'status': 'TEMPLATE', 'files': []}))
    with pytest.raises(SystemExit):
        ER.main(args + ['--lock', str(lock)])


def test_e39client_namespace_and_seq_annotation_rail():
    for bad in ('e40-system', 'e40-load', 'e38v2-x', 'default'):
        with pytest.raises(ValueError):
            K.E39Client('http://127.0.0.1:1', bad, 'writer')
    c = K.E39Client('http://127.0.0.1:1', 'e40-x', 'writer')
    # e38.seq is allowed on PATCH (checked before any byte is sent: the rail passes, the connect fails)
    with pytest.raises((K.APIError, OSError)) as ei:
        c.patch_pod_annotations({'e38.scope': 'false', K.SEQ_ANN: '3'})
    assert 'safety rail' not in str(ei.value)
    with pytest.raises(K.APIError) as ei:
        c.patch_pod_annotations({'other': 'x'})
    assert 'safety rail' in str(ei.value)
    # ...but not on Pod CREATE
    with pytest.raises(K.APIError) as ei:
        c.create_target_pod(annotations={K.SEQ_ANN: '1'})
    assert 'safety rail' in str(ei.value)


def test_arrivals_pod_annotation_path_with_e39client():
    from fake_api import FakeApiServer
    from harness_arrivals import ArrivalWriterProcess, ArrivalWriter
    import time
    with FakeApiServer(namespace='e40-arrtest') as srv:
        base, ns = srv.base_url, srv.cluster.ns
        uid = srv.cluster.pod['uid']
        wp = ArrivalWriterProcess(lambda: K.E39Client(base, ns, 'writer'))
        try:
            aw = ArrivalWriter(wp, K.SCOPE_ANN, K.SCOPE_ANN, 'false', [1.0, 3.0, 6.0], window_ms=200.0,
                               target_kind='pod_annotation', seq_key=K.SEQ_ANN, pod_uid=uid)
            aw.prearm()
            a0 = time.monotonic_ns()
            aw.arm(a0)
            time.sleep(0.05)
            aw.halt(a0 + 100_000_000)
            res = aw.join()
        finally:
            wp.stop()
    arr = res['writer_arrivals']
    assert [a['http_status'] for a in arr] == [200, 200, 200]
    rvs = [int(a['resulting_resource_version']) for a in arr]
    assert rvs == sorted(rvs) and len(set(rvs)) == 3
    assert srv.cluster.pod['annotations'][K.SCOPE_ANN] == 'false'
    assert srv.cluster.pod['annotations'][K.SEQ_ANN] == '2'
    pats = [e for e in srv.cluster.audit_log if e['verb'] == 'patch' and e['objectRef']['resource'] == 'pods']
    assert {e['auditID'] for e in pats} >= {a['audit_id'] for a in arr}


def test_mirror_host_process_propagates_and_builds_fragment(tmp_path):
    """Per-trial mirror (A4m): start -> writer ConfigMap PATCH -> mirror PATCHes the Pod annotation ->
    stop; trial.mirror built from writer commit + mirror entry (mirror RV > source RV)."""
    import time
    import arms_commit as AC
    from fake_api import FakeApiServer
    with FakeApiServer(namespace='e40-mirrortest') as srv:
        base, ns = srv.base_url, srv.cluster.ns
        mh = ER.MirrorHostProcess(lambda: K.E39Client(base, ns, 'mirror'), tmp_path / 'mirror_log.jsonl')
        try:
            reset = K.E39Client(base, ns, 'reset')
            pod, _ = reset.get_pod()
            reset.patch_pod_annotations(AC.fixture_annotations(), uid=pod['metadata']['uid'])
            st = mh.start_trial(7)
            assert st['watch_starts'] >= 1
            w = K.E39Client(base, ns, 'writer')
            status, h, obj = w.patch_configmap('response-scope', {'allowed': 'false'})
            src_rv = obj['metadata']['resourceVersion']
            end = time.monotonic() + 5
            while time.monotonic() < end:
                if any(e.get('kind') == 'update' and e.get('http_status') == 200 for e in mh.entries()):
                    break
                time.sleep(0.01)
            entries, stats = mh.stop_trial()
        finally:
            mh.stop()
        assert srv.cluster.pod['annotations'][K.SCOPE_ANN] == 'false'
    ent = AC.find_mirror_entry(entries, 'response-scope', src_rv)
    frag = AC.mirror_fragment('response-scope', src_rv, 1, ent)
    assert int(frag['mirror_commit_rv']) > int(src_rv) and frag['mirror_audit_id']
    assert stats['updates'] == 1 and all(e['trial_id'] == 7 for e in entries)
    assert (tmp_path / 'mirror_log.jsonl').is_file()


def test_go_client_against_tls_fake_409_message(tmp_path):
    """Real e40-goclient binary vs the fake's TLS twin: a stale-UID precondition DELETE returns 409 and the
    full apiserver message reaches the record (client-go decodes metav1.Status)."""
    import arms_commit as AC
    import goclient_bridge as gb
    from fake_api import FakeApiServer
    if not ER.DEFAULT_GO_BIN.is_file():
        pytest.skip('Go client binary not built')
    tls = ER.make_loopback_cert(str(tmp_path))
    if tls is None:
        pytest.skip('openssl missing')
    tok = tmp_path / 'tok'
    tok.write_text('dummy\n')
    with FakeApiServer(namespace='e40-gotest') as srv:
        twin = ER._start_tls_twin(srv, *tls)
        try:
            reset = K.E39Client(srv.base_url, 'e40-gotest', 'reset')
            pod, _ = reset.get_pod()
            reset.patch_pod_annotations(AC.fixture_annotations(), uid=pod['metadata']['uid'])
            argv = [str(ER.DEFAULT_GO_BIN), '--server', 'https://127.0.0.1:%d' % twin.server_address[1],
                    '--ca-file', tls[0], '--token-file', str(tok), '--namespace-prefix', 'e40-']
            with gb.GoClient(argv) as go:
                raw = go.gate_delete_rv('e40-gotest', K.POD_NAME, 'not-the-uid')
                assert raw['ok'] and raw['valid'] and raw['delete_status'] == 409
                assert 'UID in the precondition' in raw['delete_message']
                raw2 = go.gate_delete_rv('e40-gotest', K.POD_NAME, pod['metadata']['uid'])
                assert raw2['delete_status'] == 200 and raw2['deleted'] and raw2['delete_audit_id']
        finally:
            twin.shutdown()
            twin.server_close()


@pytest.fixture(scope='module')
def selftest(tmp_path_factory):
    out = tmp_path_factory.mktemp('selftest')
    rc = ER.offline_selftest(out, seed=3939, reps=1, analysis=True)
    return rc, out


def test_offline_selftest_all_subexperiments(selftest):
    rc, out = selftest
    summ = json.loads((out / 'SELFTEST_SUMMARY.json').read_text())
    recs = [json.loads(l) for l in (out / 'RAW_TRIALS.jsonl').read_text().splitlines() if l.strip()]
    sched = json.loads((out / 'SCHEDULE.json').read_text())
    assert len(recs) == sched['n_trials'] == summ['trials_written']
    assert summ['schema_invalid'] == []
    exps = {r['experiment'] for r in recs}
    expected = set(ER.EXPERIMENTS) if not summ['notes'] else set(ER.EXPERIMENTS) - {'F'}
    assert exps == expected
    assert not [r for r in recs if r['status'] == 'INFRA_ERROR'], summ['infra_examples']
    from schema_e39 import validate_record_e39
    for r in recs:
        validate_record_e39(r)
    byx = {x: [r for r in recs if r['experiment'] == x] for x in exps}
    # A: A4m trials carry mirror facts; 409 messages recorded in full
    a4m = [r for r in byx['A'] if r['arm'] == 'A4m_commit_mirror' and r['writer']]
    assert a4m and all(r['mirror'] is not None and r['mirror']['source_commit_rv'] for r in a4m)
    # either the mirror propagated the writer's ConfigMap commit to the Pod, or the Pod was already deleted
    # (DELETE before the mirror: the A4m window = mirror lag); never silently missing
    assert all(r['mirror']['mirror_commit_rv'] or r['mirror_meta'].get('pod_missing_on_propagation')
               for r in a4m)
    mlog = [json.loads(l) for l in (out / 'mirror_log.jsonl').read_text().splitlines() if l.strip()]
    assert {e['trial_id'] for e in mlog} == {r['trial_id'] for r in byx['A'] if r['arm'] == 'A4m_commit_mirror'}
    assert all(e.get('resync_s') == ER.MIRROR_RESYNC_S for e in mlog if e['kind'] == 'start')
    c409 = [r for r in recs if r['delete'].get('http_status') == 409]
    assert c409 and all('in the precondition' in (r['delete'].get('response_message') or '') for r in c409)
    # B: arrivals; C: acquisition timings; D: requested_level + load_level; G: cut writes; E: netpol
    assert all(isinstance(r['writer_arrivals'], list) for r in byx['B'])
    assert all(r['acquisition']['refresh_ms'] >= 0 and r['acquisition']['gate_ms'] >= r['acquisition']['refresh_ms']
               for r in byx['C'])
    assert all(r['requested_level'] == next(t for t in sched['trials'] if t['trial_id'] == r['trial_id'])['load_level']
               for r in byx['D'])
    assert all(len(r['cut_writes']) == 3 and all(w['audit_id'] for w in r['cut_writes']) for r in byx['G'])
    dcji_oom = [r for r in byx['G'] if r['arm'] == 'A1_dcji' and r['condition'] == 'out_of_model_bdf']
    assert dcji_oom and all(r['gate']['decision'] == 'EXECUTE' for r in dcji_oom)
    assert any(r['netpol'] and r['netpol']['http_status'] == 201 for r in byx['E'])
    for r in recs:
        assert r['host'] is not None
    assert (out / 'audit.log').stat().st_size > 0 and (out / 'mirror_log.jsonl').is_file()
    if 'F' in exps:
        go = [r for r in byx['F'] if r['client_impl'] == 'client-go']
        assert go and all(r['go_client_version'] and r['go']['op'] in ('gate_uid', 'gate_uid+delete_uid', 'gate_delete_rv')
                          for r in go)
        a1go = [r for r in go if r['arm'] == ER.A1GO]
        assert a1go and all(r['go']['writer_armed_at'] == 'true_gate_end' for r in a1go)
        disp = [r for r in go if r['delete'].get('dispatched')]
        assert disp and all(r['delete']['t_send_mono_ns'] and r['delete']['t_send_mono_ns'] >= r['gate']['t_end_mono_ns']
                            and r['delete']['t_resp_mono_ns'] >= r['delete']['t_send_mono_ns'] for r in disp)
        # writer release (A1/go) is anchored at the Go gate end on the same monotonic clock
        for r in a1go:
            w = r.get('writer') or {}
            rel = w.get('t_release_actual_mono_ns') or w.get('t_actual_release_mono_ns')
            if rel:
                assert rel >= r['gate']['t_end_mono_ns']


def test_go_split_ops_against_tls_fake(tmp_path):
    """Split ops of the real binary: gate_uid (no DELETE, t_gate_end on CLOCK_MONOTONIC) then delete_uid /
    delete_rv with t_send/t_resp; stale RV -> 409 with the ResourceVersion precondition message."""
    import time as _t
    import arms_commit as AC
    import goclient_bridge as gb
    from fake_api import FakeApiServer
    if not ER.DEFAULT_GO_BIN.is_file():
        pytest.skip('Go client binary not built')
    tls = ER.make_loopback_cert(str(tmp_path))
    if tls is None:
        pytest.skip('openssl missing')
    tok = tmp_path / 'tok'
    tok.write_text('dummy\n')
    with FakeApiServer(namespace='e40-gotest') as srv:
        twin = ER._start_tls_twin(srv, *tls)
        try:
            reset = K.E39Client(srv.base_url, 'e40-gotest', 'reset')
            pod, _ = reset.get_pod()
            uid = pod['metadata']['uid']
            argv = [str(ER.DEFAULT_GO_BIN), '--server', 'https://127.0.0.1:%d' % twin.server_address[1],
                    '--ca-file', tls[0], '--token-file', str(tok), '--namespace-prefix', 'e40-']
            with gb.GoClient(argv) as go:
                t0 = _t.monotonic_ns()
                g = go.gate_uid('e40-gotest', K.POD_NAME, {'b': True, 'd': True, 'f': True}, expected_uid=uid)
                t1 = _t.monotonic_ns()
                assert g['ok'] and not g.get('deleted') and not g.get('delete_status')
                assert t0 <= g['t_gate_end_mono_ns'] <= t1
                rv = g['pod_resource_version']
                reset.patch_pod_annotations(AC.fixture_annotations(), uid=uid)   # bumps the Pod RV
                bad = go.delete_rv('e40-gotest', K.POD_NAME, uid, rv)
                assert bad['delete_status'] == 409 and 'ResourceVersion in the precondition' in bad['delete_message']
                d = go.delete_uid('e40-gotest', K.POD_NAME, uid)
                assert d['delete_status'] == 200 and d['t_send_mono_ns'] <= d['t_resp_mono_ns']
                assert d['t_send_mono_ns'] >= g['t_gate_end_mono_ns'] and d['delete_audit_id']
        finally:
            twin.shutdown()
            twin.server_close()


def test_offline_selftest_analysis_gate(selftest):
    rc, out = selftest
    summ = json.loads((out / 'SELFTEST_SUMMARY.json').read_text())
    a = summ.get('analysis') or {}
    if a.get('skipped'):
        pytest.skip(a['skipped'])
    assert a['exit_code'] == 0, a
    assert (a.get('completeness_gate') or {}).get('passed') is True, a
    assert rc == 0


def test_resume_skips_recorded_trials(tmp_path):
    """Resume: trial ids already in RAW_TRIALS.jsonl are never re-run."""
    trials = ER.build_stage1(SEED, 'rs', reps=1, experiments=('G',))
    raw = tmp_path / 'RAW_TRIALS.jsonl'
    raw.write_text(json.dumps({'trial_id': trials[0]['trial_id']}) + '\n')

    class Dummy:
        def __init__(self):
            self.ran = []
            self.responder = self.reset = type('C', (), {'close': lambda s: None})()
            self.wproc = self.arrivals = self.mirror = None

        def prepare(self, spec):
            pass
    d = Dummy()
    orig = ER.run_trial
    try:
        def fake_run(env, spec):
            env.ran.append(spec['trial_id'])
            r = ER.base_record(spec)
            r['status'] = 'INFRA_ERROR'
            r['error'] = 'dummy'
            return r
        ER.run_trial = fake_run
        st = ER.run_trials(d, trials, raw, guards=False)
    finally:
        ER.run_trial = orig
    assert d.ran == [t['trial_id'] for t in trials[1:]] and st['skipped_recorded'] == 1
