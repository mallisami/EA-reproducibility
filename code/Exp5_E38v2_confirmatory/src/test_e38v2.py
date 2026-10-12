#!/usr/bin/env python3
"""Offline unit tests for the E38v2 runner. Run: pytest -q test_e38v2.py

No network access other than the 127.0.0.1 in-process fake apiserver.
"""
from __future__ import annotations
import json, socket, time
import pytest

import e38v2_runner as R
import e38v2_model as M
from schema import ARMS, CONDITIONS, DELTA_GRID_MS, validate_record
from fake_api import FakeApiServer

F5 = R.DEFAULT_F5


# ------------------------- fixtures -------------------------
@pytest.fixture
def server():
    s = FakeApiServer(namespace='e38v2-test').start()
    yield s
    s.stop()


@pytest.fixture
def clients(server):
    base = server.base_url
    ns = 'e38v2-test'
    cl = {
        'responder': R.KubeClient(base, ns, 'responder', token_path='/t/responder', context='e38v2'),
        'writer': R.KubeClient(base, ns, 'writer', token_path='/t/writer', context='e38v2'),
        'reset': R.KubeClient(base, ns, 'reset', token_path='/t/reset', context='e38v2'),
    }
    for c in cl.values():
        c.connect()
    yield cl
    for c in cl.values():
        c.close()


def _spec(arm, cond, delta, tid=1, rep=0, seed=3802):
    return {'run_id': 'e38v2test', 'trial_id': tid, 'stage': 1, 'block': 0,
            'arm': arm, 'condition': cond, 'delta_ms': delta, 'rep': rep, 'schedule_seed': seed}


# ------------------------- (b) horizon passing -------------------------
def test_certificate_horizon_stable():
    for h in (5.0, 10.0, 30.0, 60.0, 30.0):
        cert = M.frozen_planner_certificate(h, F5)
        assert cert['certificate'] == ['a', 'c', 'e', 'scope', 'uid']
        assert cert['horizon_s'] == h
        assert len(cert['f5_sha256']) == 64


def test_certificate_requires_f5_path():
    with pytest.raises(ValueError):
        M.frozen_planner_certificate(30.0, None)


def test_certificate_fail_closed_on_tampered_f5(tmp_path):
    bad = json.loads(F5.read_text())
    # Break justification so the exploitable certificate changes / disappears.
    bad['policy_profiles']['balanced']['justification'] = {'all': [{'predicate': 'flag_a'}]}
    p = tmp_path / 'bad_f5.json'
    p.write_text(json.dumps(bad))
    with pytest.raises(RuntimeError):
        M.frozen_planner_certificate(30.0, p)


# ------------------------- (a) reset order + identity separation -------------------------
def test_reset_restores_configmaps_before_replacing_pod(clients, server):
    # dirty the fixture first
    clients['writer'].patch_configmap('evidence-a', {'value': 'false'})
    clients['writer'].patch_configmap(R.OBJ_SCOPE, {'allowed': 'false'})
    old_uid = server.cluster.pod['uid']
    fixture, uid = R.reset_fixture(clients['reset'])
    assert fixture['reset_ok'] is True
    # ConfigMaps restored
    assert server.cluster.configmaps['evidence-a']['data']['value'] == 'true'
    assert server.cluster.configmaps[R.OBJ_SCOPE]['data']['allowed'] == 'true'
    # Pod replaced (new uid)
    assert uid != old_uid and uid == server.cluster.pod['uid']


def test_identities_are_distinct(clients):
    ids = {c.identity for c in clients.values()}
    assert ids == {'responder', 'writer', 'reset'}
    tokens = {c.token_path for c in clients.values()}
    assert len(tokens) == 3  # distinct token paths


def test_reset_configmap_restore_precedes_pod_replace_ordering(clients, server, monkeypatch):
    order = []
    real_patch = R.KubeClient.patch_configmap
    real_create = R.KubeClient.create_pod

    def spy_patch(self, name, data):
        order.append(('patch', name))
        return real_patch(self, name, data)

    def spy_create(self, **kw):
        order.append(('create_pod', None))
        return real_create(self, **kw)

    monkeypatch.setattr(R.KubeClient, 'patch_configmap', spy_patch)
    monkeypatch.setattr(R.KubeClient, 'create_pod', spy_create)
    R.reset_fixture(clients['reset'])
    kinds = [k for k, _ in order]
    assert 'create_pod' in kinds
    # every configmap patch happens before the pod is (re)created
    assert kinds.index('create_pod') > max(i for i, k in enumerate(kinds) if k == 'patch')


# ------------------------- scheduler -------------------------
def test_stage1_schedule_balance():
    sched = R.build_schedule(1, 3802, 'r')
    counts = R.schedule_counts(sched)
    assert counts['total'] == 5812
    assert counts['by_condition']['no_write'] == 156
    assert counts['by_condition']['uid_rebind_control'] == 40
    for cond in R.ASYNC_CONDITIONS:
        assert counts['by_condition'][cond] == 4 * 12 * 39
    # every async cell has exactly 39
    cells = {}
    for u in sched:
        if u['condition'] in R.ASYNC_CONDITIONS:
            cells[(u['arm'], u['condition'], u['delta_ms'])] = cells.get((u['arm'], u['condition'], u['delta_ms']), 0) + 1
    assert set(cells.values()) == {39}


def test_stage1_schedule_determinism():
    a = R.build_schedule(1, 12345, 'r')
    b = R.build_schedule(1, 12345, 'r')
    c = R.build_schedule(1, 99999, 'r')
    assert [(_x['arm'], _x['condition'], _x['delta_ms'], _x['trial_id']) for _x in a] == \
           [(_x['arm'], _x['condition'], _x['delta_ms'], _x['trial_id']) for _x in b]
    assert [(_x['arm'], _x['condition'], _x['delta_ms']) for _x in a] != \
           [(_x['arm'], _x['condition'], _x['delta_ms']) for _x in c]


def test_seed_refusal():
    with pytest.raises(ValueError):
        R.build_schedule(1, R.FORBIDDEN_SEED, 'r')
    # E38v2 RERUN: the first E38v2 run's seed is refused as well
    with pytest.raises(ValueError):
        R.build_schedule(1, 1772720255, 'r')


# ------------------------- E38v2 RERUN: running target Pod -------------------------
def test_target_pod_spec_is_running_pause_pod(clients, monkeypatch):
    sent = []
    real = R.KubeClient.request

    def spy(self, method, path, body=None, extra_headers=None):
        if method == 'POST' and path.endswith('/pods'):
            sent.append(body)
        return real(self, method, path, body, extra_headers)
    monkeypatch.setattr(R.KubeClient, 'request', spy)
    fx, uid = R.reset_fixture(clients['reset'], webhook=True)   # replaces the fake's initial Pod
    assert fx['reset_ok'] is True and fx['pod_ready']['ready'] is True
    spec = sent[-1]['spec']
    assert 'schedulingGates' not in spec
    c = spec['containers'][0]
    assert c['image'] == R.TARGET_IMAGE == 'registry.k8s.io/pause:3.10'
    assert c['imagePullPolicy'] == 'Never' and 'command' not in c
    assert sent[-1]['metadata']['labels'][R.WEBHOOK_LABEL[0]] == R.WEBHOOK_LABEL[1]


def test_delete_options_unchanged_grace_zero(clients, monkeypatch):
    sent = []
    real = R.KubeClient.request

    def spy(self, method, path, body=None, extra_headers=None):
        if method == 'DELETE':
            sent.append(body)
        return real(self, method, path, body, extra_headers)
    monkeypatch.setattr(R.KubeClient, 'request', spy)
    R.one_trial(clients, _spec('A1_dcji', 'no_write', None))
    assert sent and all(b['gracePeriodSeconds'] == 0 and b['preconditions']['uid'] for b in sent)


def test_trial_records_ready_gate_state_and_gone(clients):
    rec = R.one_trial(clients, _spec('A1_dcji', 'no_write', None))
    validate_record(rec)
    pr = rec['fixture']['pod_ready']
    assert pr['ready'] is True and pr['phase'] == 'Running' and pr['t_ready_mono_ns'] >= pr['t_wait_start_mono_ns']
    ps = rec['gate']['pod_state']
    assert ps['phase'] == 'Running' and ps['ready'] is True and ps['uid'] == rec['delete']['expected_uid']
    assert '_pod_obj' not in rec['gate']
    assert rec['effect']['verified'] is True and rec['effect']['t_gone_mono_ns'] is not None
    assert rec['effect']['gone_polls'] >= 1
    # A0 gate (single pod read) records the pod state as well
    rec0 = R.one_trial(clients, _spec('A0_native_uid', 'async_scope', 1.0))
    validate_record(rec0)
    assert rec0['gate']['pod_state']['phase'] == 'Running'


def test_wait_pod_ready_times_out_on_non_running_pod(clients, server, monkeypatch):
    fixture, uid = R.reset_fixture(clients['reset'])
    real = server.httpd.RequestHandlerClass._pod_obj

    def pending(self, rv=None):
        o = real(self, rv)
        if o is not None:
            o['status'] = {'phase': 'Pending'}
        return o
    monkeypatch.setattr(server.httpd.RequestHandlerClass, '_pod_obj', pending)
    with pytest.raises(R.APIError):
        R.wait_pod_ready(clients['reset'], uid, timeout_s=0.3, poll_s=0.02)
    monkeypatch.setattr(R, 'READY_TIMEOUT_S', 0.3)
    fx, u = R.reset_fixture(clients['reset'])
    assert fx['reset_ok'] is False and 'wait_ready' in fx['error']


def test_stage2_topup(tmp_path):
    tf = tmp_path / 'topup.json'
    tf.write_text(json.dumps([
        {'arm': 'A1_dcji', 'condition': 'async_scope', 'delta_ms': 1.0, 'n_additional': 54},
        {'arm': 'A0_native_uid', 'condition': 'async_scope', 'delta_ms': 1.0, 'n_target': 93},
    ]))
    sched = R.build_schedule(2, 3802, 'r', topup_file=tf)
    assert len(sched) == 54 + (93 - 39)
    assert all(u['stage'] == 2 for u in sched)
    # stage-2 trial ids never collide with stage-1 ids
    assert min(u['trial_id'] for u in sched) == R.STAGE2_TRIAL_ID_BASE + 1
    assert len({u['trial_id'] for u in sched}) == len(sched)


def test_stage2_accepts_stage2_topup_output(tmp_path):
    # exact output format of analysis/stage2_topup.py (e38v2.stage2_topup.v1)
    doc = {'schema': 'e38v2.stage2_topup.v1', 'run_id': 'r', 'n_cells': 2, 'n_new_trials': 108,
           'cells': [{'order': 0, 'arm': 'A0_native_uid', 'condition': 'async_scope', 'delta_ms': 1.0,
                      'n_stage1_planned': 39, 'p_hat_stage1': 0.5, 'n_topup': 54, 'n_target': 93, 'flag': ''},
                     {'order': 1, 'arm': 'A3_dcji_webhook', 'condition': 'async_app_policy', 'delta_ms': -1.0,
                      'n_stage1_planned': 39, 'p_hat_stage1': 0.2, 'n_topup': 54, 'n_target': 93, 'flag': ''}],
           'undefined_cells': []}
    tf = tmp_path / 'stage2_cells_E38v2.json'
    tf.write_text(json.dumps(doc))
    sched = R.build_schedule(2, 3802, 'r', topup_file=tf)
    cells = {}
    for u in sched:
        cells[(u['arm'], u['condition'], u['delta_ms'])] = cells.get((u['arm'], u['condition'], u['delta_ms']), 0) + 1
    assert cells == {('A0_native_uid', 'async_scope', 1.0): 54, ('A3_dcji_webhook', 'async_app_policy', -1.0): 54}
    reps = sorted(u['rep'] for u in sched if u['arm'] == 'A0_native_uid')
    assert reps == list(range(39, 93))
    out = tmp_path / 'S2.json'
    assert R.main(['--make-schedule', str(out), '--stage', '2', '--topup-file', str(tf), '--seed', '3802',
                   '--run-id', 'r']) == 0
    d = json.loads(out.read_text())
    assert d['schema'] == 'e38v2.schedule.v1' and d['stage'] == 2 and d['schedule_seed'] == 3802
    assert len(d['trials']) == 108


def test_make_schedule_document_format(tmp_path):
    out = tmp_path / 'SCHEDULE.json'
    assert R.main(['--make-schedule', str(out), '--seed', '777', '--run-id', 'rr']) == 0
    d = json.loads(out.read_text())
    assert d['schema'] == 'e38v2.schedule.v1' and d['run_id'] == 'rr' and d['schedule_seed'] == 777
    assert len(d['trials']) == 5812
    assert set(d['trials'][0]) == {'trial_id', 'stage', 'block', 'arm', 'condition', 'delta_ms', 'rep',
                                   'schedule_seed'}
    with pytest.raises(SystemExit):   # never overwrite
        R.main(['--make-schedule', str(out), '--seed', '777', '--run-id', 'rr'])
    smoke = tmp_path / 'SMOKE.json'
    assert R.main(['--make-schedule', str(smoke), '--seed', '777', '--run-id', 'rr', '--reps', '1']) == 0
    assert len(json.loads(smoke.read_text())['trials']) == 144 + 4 + 4


def test_delta_grid_matches_spec():
    assert DELTA_GRID_MS == (-5.0, -2.0, -1.0, 0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 20.0, 50.0)


# ------------------------- writer release accuracy -------------------------
def test_positive_delta_release_accuracy(clients):
    """actual-intended for a positive delta should be small (busy-wait jitter)."""
    offsets = []
    for _ in range(8):
        rec = R.one_trial(clients, _spec('A1_dcji', 'async_scope', 2.0))
        assert rec['writer'] is not None
        offsets.append(rec['writer']['actual_release_mono_ns'] - rec['writer']['intended_release_mono_ns'])
    # busy-wait fires at or just after target: small non-negative offsets (< 3 ms)
    assert all(o >= -50_000 for o in offsets)
    assert max(offsets) < 3_000_000, offsets


def test_negative_delta_writer_fires(clients):
    rec = R.one_trial(clients, _spec('A1_dcji', 'async_scope', -2.0))
    assert rec['writer'] is not None
    assert rec['writer']['http_status'] == 200
    # intended is before the arming instant; actual is after -> positive offset
    assert rec['writer']['actual_release_mono_ns'] > rec['writer']['intended_release_mono_ns']


def test_release_offset_distribution(clients, capsys):
    pos = []
    for d in (0.5, 1.0, 2.0, 5.0):
        for _ in range(5):
            rec = R.one_trial(clients, _spec('A2_fullrefresh', 'async_app_policy', d))
            w = rec['writer']
            pos.append(w['actual_release_mono_ns'] - w['intended_release_mono_ns'])
    pos.sort()
    med = pos[len(pos) // 2]
    print('positive-delta release offset ns: min=%d median=%d max=%d' % (pos[0], med, pos[-1]))
    assert med < 2_000_000


# ------------------------- record schema for every condition/arm -------------------------
def test_schema_all_arms_conditions(clients):
    for arm in ARMS:
        for cond in CONDITIONS:
            delta = 1.0 if cond in R.ASYNC_CONDITIONS else None
            rec = R.one_trial(clients, _spec(arm, cond, delta))
            validate_record(rec)
            assert rec['status'] in ('COMPLETE', 'UNKNOWN', 'INFRA_ERROR')
            if cond in ('no_write', 'uid_rebind_control'):
                assert rec['writer'] is None and rec['delta_ms'] is None


def test_uid_rebind_control_yields_409(clients):
    for arm in ARMS:
        rec = R.one_trial(clients, _spec(arm, 'uid_rebind_control', None))
        if rec['gate']['decision'] == 'EXECUTE':
            # pod replaced after gate -> stale uid precondition -> 409, no effect
            assert rec['delete']['http_status'] == 409
            assert rec['effect']['verified'] is False


def test_no_write_executes_cleanly(clients):
    rec = R.one_trial(clients, _spec('A1_dcji', 'no_write', None))
    assert rec['gate']['decision'] == 'EXECUTE'
    assert rec['delete']['http_status'] in (200, 202)
    assert rec['effect']['verified'] is True
    assert rec['delete']['audit_id'] and len(rec['delete']['audit_id']) >= 8


def test_a3_webhook_selected_by_pod_label_and_joinable_by_pod_uid(clients, server):
    # A3 target Pods carry e38v2-webhook=enforce (VWC objectSelector); the webhook entry is joined
    # on pod_uid == delete.expected_uid (the runner cannot see the AdmissionReview uid).
    saw = 0
    for _ in range(6):
        rec = R.one_trial(clients, _spec('A3_dcji_webhook', 'async_scope', 1.0))
        assert rec['webhook_request_uid'] is None
        if rec['gate']['decision'] == 'EXECUTE':
            ents = [w for w in server.cluster.webhook_log if w['pod_uid'] == rec['delete']['expected_uid']]
            assert len(ents) == 1
            assert all('t_send_wall_ns' in r for r in ents[0]['reads'])
            saw += 1
    assert saw
    # non-webhook arms: never intercepted
    n_before = len(server.cluster.webhook_log)
    for arm in ('A0_native_uid', 'A1_dcji', 'A2_fullrefresh'):
        R.one_trial(clients, _spec(arm, 'async_scope', 1.0))
    assert len(server.cluster.webhook_log) == n_before


def test_a3_webhook_denies_invalid_state(clients, server):
    # write released well before the DELETE admission (negative delta) on A3 -> deny or gate abort
    outcomes = set()
    for _ in range(5):
        rec = R.one_trial(clients, _spec('A3_dcji_webhook', 'async_app_policy', 0.0))
        if rec['delete']['dispatched']:
            outcomes.add(rec['delete']['http_status'])
    assert outcomes <= {200, 403}


def test_writer_new_value_is_string_and_fixture_values(clients):
    rec = R.one_trial(clients, _spec('A1_dcji', 'async_evidence_last_literal', 5.0))
    assert rec['writer']['new_value'] == 'false' and rec['writer']['object'] == 'evidence-a'
    iv = rec['fixture']['initial_values']
    assert iv['evidence-c'] == 'false' and iv['evidence-e'] == 'false' and iv['evidence-a'] == 'true'
    rec = R.one_trial(clients, _spec('A1_dcji', 'async_scope', 5.0))
    assert rec['writer']['new_value'] == 'false' and rec['fixture']['initial_values']['evidence-c'] == 'true'


def test_uid_rebind_logs_replacement_uid(clients):
    rec = R.one_trial(clients, _spec('A1_dcji', 'uid_rebind_control', None))
    validate_record(rec)
    ru = rec['uid_rebind']['replacement_pod_uid']
    assert ru and ru != rec['delete']['expected_uid']


def test_process_writer_end_to_end(server):
    from writer import WriterProcess
    base, ns = server.base_url, 'e38v2-test'
    wp = WriterProcess(lambda: R.KubeClient(base, ns, 'writer', context=R.REQUIRED_CONTEXT))
    try:
        assert wp.ping() is None
        cl = {'responder': R.KubeClient(base, ns, 'responder'), 'writer': wp, 'reset': R.KubeClient(base, ns, 'reset')}
        offs = []
        for d in (-2.0, 0.0, 2.0, 5.0):
            rec = R.one_trial(cl, _spec('A2_fullrefresh', 'async_scope', d))
            validate_record(rec)
            assert rec['status'] == 'COMPLETE' and rec['writer']['http_status'] == 200
            if d >= 0:
                offs.append(rec['writer']['actual_release_mono_ns'] - rec['writer']['intended_release_mono_ns'])
        assert max(offs) < 2_000_000
        # a cancelled job (exception path) leaves the process reusable
        h = R.build_writer('async_scope', 1.0, wp).prearm()
        h.cancel()
        rec = R.one_trial(cl, _spec('A1_dcji', 'async_app_policy', 1.0))
        assert rec['writer']['http_status'] == 200
    finally:
        wp.stop()


def test_a0_native_uid_always_dispatches_when_pod_present(clients):
    rec = R.one_trial(clients, _spec('A0_native_uid', 'no_write', None))
    assert rec['gate']['decision'] == 'EXECUTE'
    assert rec['delete']['dispatched'] is True
    assert rec['gate']['ssar_allowed'] is None  # baseline does no SSAR


def test_async_evidence_last_literal_cuts_ce_pregate(clients):
    rec = R.one_trial(clients, _spec('A2_fullrefresh', 'async_evidence_last_literal', 50.0))
    # gate reads should show c,e already false before the gate
    vals = {r['object']: r['value'] for r in rec['gate']['reads']}
    assert vals.get('evidence-c') == 'false'
    assert vals.get('evidence-e') == 'false'


# ------------------------- Audit-Id capture -------------------------
def test_audit_id_on_writer_and_delete(clients):
    rec = R.one_trial(clients, _spec('A1_dcji', 'async_app_policy', 3.0))
    assert rec['writer']['audit_id'] and len(rec['writer']['audit_id']) >= 8
    if rec['delete']['dispatched']:
        assert rec['delete']['audit_id']


# ------------------------- safety rails -------------------------
def test_reject_non_loopback_url():
    with pytest.raises(ValueError):
        R.KubeClient('http://10.0.0.1:8080', 'e38v2-x', 'responder')
    with pytest.raises(ValueError):
        R.KubeClient('https://localhost:6443', 'e38v2-x', 'responder', token_path='t', ca_path='c')
    with pytest.raises(ValueError):   # https requires pinned CA + token
        R.KubeClient('https://127.0.0.1:6443', 'e38v2-x', 'responder')
    with pytest.raises(ValueError):
        R.KubeClient('https://127.0.0.1:6443/api', 'e38v2-x', 'responder', token_path='t', ca_path='c')
    with pytest.raises(ValueError):   # control namespace is never a test namespace
        R.KubeClient('http://127.0.0.1:6443', 'e38v2-system', 'responder')


def test_reject_bad_namespace(server):
    with pytest.raises(ValueError):
        R.KubeClient(server.base_url, 'prod-ns', 'responder')


def test_mutation_allowlist_blocks_unknown_configmap(clients):
    with pytest.raises(R.APIError):
        clients['writer'].patch_configmap('not-allowed-cm', {'value': 'x'})


def test_delete_requires_uid_precondition(clients):
    with pytest.raises(R.APIError):
        clients['responder'].request('DELETE',
            f'/api/v1/namespaces/e38v2-test/pods/{R.POD_NAME}', {'apiVersion': 'v1'})


def test_context_and_namespace_rails_cli(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError('network access attempted by a refused live run')
    monkeypatch.setattr(socket, 'socket', boom)
    base = ['--api-server', 'https://127.0.0.1:6443', '--ca-file', 'x', '--token-dir', 'y',
            '--schedule', 'z', '--output-dir', str(tmp_path / 'o'), '--ack-disposable']
    for bad in (['--context', 'kind-e38', '--namespace', 'e38v2-x'],          # wrong context
                ['--context', 'e38v2', '--namespace', 'e38v2-x'],             # not the kind context name
                ['--context', 'kind-e38v2', '--namespace', 'prod'],           # non-disposable namespace
                ['--context', 'kind-e38v2', '--namespace', 'e38v2-system']):  # control namespace
        with pytest.raises(SystemExit):
            R.main(bad + base)
    with pytest.raises(SystemExit):   # remote endpoint refused
        R.main(['--context', 'kind-e38v2', '--namespace', 'e38v2-x', '--api-server', 'https://10.1.1.1:6443',
                '--ca-file', 'x', '--token-dir', 'y', '--schedule', 'z', '--output-dir', str(tmp_path / 'o'),
                '--ack-disposable'])
    with pytest.raises(SystemExit):   # missing --ack-disposable
        R.main(['--context', 'kind-e38v2', '--namespace', 'e38v2-x'] + base[:-1])


def test_live_run_requires_lock_or_explicit_smoke(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError('network access attempted before the lock check')
    monkeypatch.setattr(socket, 'socket', boom)
    sched = tmp_path / 'S.json'
    R.main(['--make-schedule', str(sched), '--seed', '5', '--run-id', 'x', '--reps', '1'])
    with pytest.raises(SystemExit):
        R.main(['--context', 'kind-e38v2', '--namespace', 'e38v2-x', '--run-id', 'x', '--api-server',
                'https://127.0.0.1:6443', '--ca-file', 'x', '--token-dir', 'y', '--schedule', str(sched),
                '--output-dir', str(tmp_path / 'o'), '--ack-disposable'])


def test_lock_mismatch_refused(tmp_path):
    sched = tmp_path / 'S.json'
    R.main(['--make-schedule', str(sched), '--seed', '5', '--run-id', 'x', '--reps', '1'])
    doc = json.loads(sched.read_text())
    lock = tmp_path / 'lock.json'
    lock.write_text(json.dumps({'status': 'LOCKED-x', 'root': str(tmp_path), 'schedule_seed': 5,
                                'design': {'schedule_seed': 5}, 'schedule_sha256': '0' * 64,
                                'files': [], 'pinned': [], 'lock_sha256': None}))
    with pytest.raises(R.APIError):
        R.verify_lock(lock, sched, doc, 1)


# ------------------------- dry-run: no network -------------------------
def test_dry_run_no_network(monkeypatch, capsys, tmp_path):
    def boom(*a, **k):
        raise AssertionError('network access attempted during --dry-run')
    monkeypatch.setattr(socket, 'socket', boom)
    rc = R.main(['--dry-run', '--stage', '1', '--seed', '777', '--output-dir', str(tmp_path / 'd')])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out['counts']['total'] == 5812
    assert out['runtime_estimate']['seconds'] > 0
    assert (tmp_path / 'd' / 'SCHEDULE.json').is_file()


def test_dry_run_runtime_estimate_uses_constants(tmp_path):
    sched = R.build_schedule(1, 42, 'r')
    counts = R.schedule_counts(sched)
    est = R.runtime_estimate(counts)
    expected = (counts['non_uid_trials'] * R.SEC_PER_TRIAL_NON_UID
                + counts['uid_trials'] * R.SEC_PER_TRIAL_UID
                + counts['writer_trials'] * R.WRITER_OVERHEAD_S)
    assert abs(est['seconds'] - round(expected, 1)) < 0.05


# ------------------------- offline-selftest produces conforming raw -------------------------
def test_offline_selftest_produces_valid_raw(tmp_path):
    rc = R.offline_selftest(tmp_path / 'st', 4242, 30.0, F5, reps=1)
    assert rc == 0
    raw = (tmp_path / 'st' / 'RAW_TRIALS.jsonl').read_text().splitlines()
    assert len(raw) > 0
    for line in raw:
        validate_record(json.loads(line))
    session = json.loads((tmp_path / 'st' / 'SESSION.json').read_text())
    assert 'clock_anchor' in session and 'f5_sha256' in session
    assert session['certificate'] == ['a', 'c', 'e', 'scope', 'uid']
    assert len(session['identities']) == 3
