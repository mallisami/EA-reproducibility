"""E39 A4d commit-bound arm + fake apiserver extensions (offline, 127.0.0.1 fake only)."""
import itertools
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'webhook'))

import arms_commit as AC  # noqa: E402
import e39_kube as K  # noqa: E402
from e39_fragments import validate_fragment  # noqa: E402
from fake_api import FakeApiServer  # noqa: E402

NS = 'e40-selftest'


@pytest.fixture
def api():
    srv = FakeApiServer(namespace=NS).start()
    yield srv
    srv.stop()


@pytest.fixture
def clients(api):
    cs = {i: K.E39Client(api.base_url, NS, i) for i in ('responder', 'writer', 'reset', 'mirror')}
    yield cs
    for c in cs.values():
        c.close()


def fresh_pod(reset, overrides=None):
    pod, _ = reset.get_pod()
    if pod is not None:
        st, _, _ = reset.delete_pod(pod['metadata']['uid'])
        assert st == 200
    st, _, obj = reset.create_target_pod(annotations=AC.fixture_annotations(overrides))
    assert st == 201
    return obj['metadata']['uid']


def deletes(api):
    return [e for e in api.cluster.audit_log if e['verb'] == 'delete' and e['objectRef']['resource'] == 'pods']


# ------------------------------------------------------------------ fake apiserver extensions
def test_fake_delete_rv_precondition(api, clients):
    r = clients['reset']
    uid = fresh_pod(r)
    pod, _ = r.get_pod()
    rv = pod['metadata']['resourceVersion']
    st, h, obj = r.delete_pod(uid, resource_version=str(int(rv) - 1))
    assert st == 409 and 'ResourceVersion in the precondition' in obj['message']
    assert h.get('Audit-Id') and api.cluster.audit_log[-1]['responseStatus']['code'] == 409
    assert api.cluster.audit_log[-1]['requestObject']['preconditions']['resourceVersion'] == str(int(rv) - 1)
    st, _, obj = r.delete_pod(uid, resource_version=rv)
    assert st == 200 and int(obj['metadata']['resourceVersion']) > int(rv)


def test_fake_delete_without_rv_unchanged(api, clients):
    r = clients['reset']
    uid = r.get_pod()[0]['metadata']['uid']
    st, _, obj = r.delete_pod('wrong-uid')
    assert st == 409 and 'UID in the precondition' in obj['message']
    assert r.delete_pod(uid)[0] == 200


def test_fake_pod_annotation_patch(api, clients):
    r = clients['reset']
    uid = fresh_pod(r)
    pod, _ = r.get_pod()
    assert pod['metadata']['annotations'] == AC.fixture_annotations()
    rv0 = int(pod['metadata']['resourceVersion'])
    st, h, obj = clients['writer'].patch_pod_annotations({'e38.scope': 'false'})
    assert st == 200 and int(obj['metadata']['resourceVersion']) > rv0
    assert obj['metadata']['annotations']['e38.scope'] == 'false'
    ev = api.cluster.audit_log[-1]
    assert (ev['verb'], ev['objectRef']['resource'], ev['auditID']) == ('patch', 'pods', h.get('Audit-Id'))
    assert ev['user']['username'].endswith('e40-writer')
    # null removes the key
    st, _, obj = r.patch_pod_annotations({'e38.scope': None})
    assert st == 200 and 'e38.scope' not in obj['metadata']['annotations']
    # uid precondition in the patch body
    st, _, obj = r.patch_pod_annotations({'e38.scope': 'true'}, uid='other')
    assert st == 409 and 'UID in precondition' in obj['message']
    # resourceVersion precondition in the patch body
    st, _, _ = r.patch_pod_annotations({'e38.scope': 'true'}, resource_version='1')
    assert st == 409
    st, _, _ = r.patch_pod_annotations({'e38.scope': 'true'}, uid=uid)
    assert st == 200


def test_fake_pod_patch_rejects_non_annotation_fields(api):
    import http.client
    import json
    c = http.client.HTTPConnection('127.0.0.1', api.port)
    body = json.dumps({'metadata': {'labels': {'x': 'y'}}})
    c.request('PATCH', f'/api/v1/namespaces/{NS}/pods/e40-target', body=body,
              headers={'Content-Type': 'application/merge-patch+json', 'Content-Length': str(len(body))})
    assert c.getresponse().status == 422
    c.close()


# ------------------------------------------------------------------ safety rail
def test_client_safety_rail(api, clients):
    w = clients['writer']
    with pytest.raises(K.APIError):
        w.request('PATCH', f'{w._ns_root()}/pods/e40-target', {'metadata': {'labels': {'a': 'b'}}})
    with pytest.raises(K.APIError):
        w.request('PATCH', f'{w._ns_root()}/pods/e40-target', {'metadata': {'annotations': {'other': 'x'}}})
    with pytest.raises(K.APIError):
        w.request('DELETE', f'{w._ns_root()}/pods/e40-target', {'preconditions': {}})
    with pytest.raises(K.APIError):
        w.request('DELETE', f'{w._ns_root()}/configmaps/evidence-a')
    for bad_url in ('http://localhost:1', 'http://10.0.0.1:443', 'http://127.0.0.1:1/x'):
        with pytest.raises(ValueError):
            K.E39Client(bad_url, NS, 'x')
    for bad_ns in ('default', 'e40-system', 'e40-system', 'kube-system'):
        with pytest.raises(ValueError):
            K.E39Client(api.base_url, bad_ns, 'x')


# ------------------------------------------------------------------ policy
def test_evaluate_annotations_matches_webhook_policy():
    import policy as WP
    for bits in itertools.product(('true', 'false'), repeat=8):
        vals = dict(zip(WP.CONFIGMAPS, bits))
        ann = {K.CM_TO_ANN[n]: v for n, v in vals.items()}
        assert AC.evaluate_annotations(ann)[0] == WP.evaluate(vals)[0]
    for bad in ('TRUE', '', None, 'yes'):
        ann = AC.fixture_annotations()
        if bad is None:
            del ann['e38.evidence/f']
        else:
            ann['e38.evidence/f'] = bad
        ok, reason = AC.evaluate_annotations(ann)
        assert not ok and 'missing_or_unparseable' in reason   # fail closed even if f not needed


def test_key_helpers():
    assert AC.to_annotation_key('b') == 'e38.evidence/b'
    assert AC.to_annotation_key('response-scope') == 'e38.scope'
    assert AC.to_annotation_key('app_policy') == 'e38.app-policy'
    assert AC.fixture_annotations({'scope': False})['e38.scope'] == 'false'
    assert set(AC.fixture_annotations()) == set(K.ANNOTATION_KEYS) and len(K.ANNOTATION_KEYS) == 8
    with pytest.raises(KeyError):
        AC.to_annotation_key('nope')


# ------------------------------------------------------------------ A4d
def test_a4d_valid_executes_200(api, clients):
    uid = fresh_pod(clients['reset'])
    res = AC.run_commit_bound(clients['responder'], AC.ARM_DIRECT, uid)
    assert res['gate']['decision'] == 'EXECUTE' and len(res['gate']['reads']) == 1
    assert res['outcome'] == 'EXECUTED' and res['delete']['http_status'] == 200
    assert res['delete_preconditions'] == {'uid': uid, 'resource_version': res['bound_state']['pod_rv_at_gate']}
    assert res['bound_state']['annotations_at_gate'] == AC.fixture_annotations()
    assert clients['reset'].get_pod()[0] is None
    d = deletes(api)[-1]
    assert d['requestObject']['preconditions'] == {'uid': uid,
                                                   'resourceVersion': res['bound_state']['pod_rv_at_gate']}
    assert d['auditID'] == res['delete']['audit_id']
    validate_fragment('bound_state', res['bound_state'])
    validate_fragment('delete_preconditions', res['delete_preconditions'])


def test_a4d_change_after_gate_409_no_retry(api, clients):
    uid = fresh_pod(clients['reset'])
    wrec = {}

    def writer(g):
        wrec.update(AC.write_annotation(clients['writer'], 'scope', 'false', pod_uid=uid))

    n0 = len(deletes(api))
    res = AC.run_commit_bound(clients['responder'], AC.ARM_DIRECT, uid, after_gate=writer)
    assert res['gate']['decision'] == 'EXECUTE'
    assert res['outcome'] == 'CONFLICT_409' and res['conflict_kind'] == 'resource_version'
    assert res['delete']['http_status'] == 409 and res['delete']['reason'] == 'Conflict'
    assert len(deletes(api)) == n0 + 1          # exactly one DELETE: no automatic retry
    pod, _ = clients['reset'].get_pod()
    assert pod is not None and pod['metadata']['uid'] == uid
    assert int(wrec['resulting_resource_version']) > int(res['bound_state']['pod_rv_at_gate'])
    assert wrec['http_status'] == 200 and wrec['object'] == 'e38.scope' and wrec['audit_id']


def test_a4d_benign_write_after_gate_also_409(api, clients):
    """Any post-gate commit to the Pod (even one that keeps the policy valid) aborts: FALSE_ABORT source."""
    uid = fresh_pod(clients['reset'])
    res = AC.run_commit_bound(clients['responder'], AC.ARM_DIRECT, uid,
                              after_gate=lambda g: AC.write_annotation(clients['writer'], 'b', 'true'))
    assert res['outcome'] == 'CONFLICT_409' and res['conflict_kind'] == 'resource_version'


def test_a4d_invalid_before_gate_aborts_without_delete(api, clients):
    uid = fresh_pod(clients['reset'], {'a': 'false', 'c': 'false', 'e': 'false'})
    n0 = len(deletes(api))
    res = AC.run_commit_bound(clients['responder'], AC.ARM_DIRECT, uid)
    assert res['gate']['decision'] == 'ABORT_REPLAN' and res['outcome'] == 'GATE_ABORT'
    assert 'J_false' in res['gate_reason'] and res['delete']['dispatched'] is False
    assert len(deletes(api)) == n0


def test_a4d_uid_rebind_after_gate_409_uid(api, clients):
    uid = fresh_pod(clients['reset'])

    def replace(g):
        fresh_pod(clients['reset'])

    res = AC.run_commit_bound(clients['responder'], AC.ARM_DIRECT, uid, after_gate=replace)
    assert res['outcome'] == 'CONFLICT_409' and res['conflict_kind'] == 'uid'


def test_a4d_gate_uid_mismatch_aborts(api, clients):
    fresh_pod(clients['reset'])
    res = AC.run_commit_bound(clients['responder'], AC.ARM_DIRECT, 'pod-stale')
    assert res['gate']['decision'] == 'ABORT_REPLAN' and 'uid_changed' in res['gate_reason']


def test_a4d_pod_missing(api, clients):
    r = clients['reset']
    r.delete_pod(r.get_pod()[0]['metadata']['uid'])
    res = AC.run_commit_bound(clients['responder'], AC.ARM_DIRECT, 'x')
    assert res['outcome'] == 'GATE_ABORT' and res['bound_state'] is None
    validate_fragment('bound_state', None)


def test_a4d_with_webhook_label_rv_checked_before_admission(api, clients):
    """RV precondition fails before admission: no webhook invocation on a 409."""
    r = clients['reset']
    r.delete_pod(r.get_pod()[0]['metadata']['uid'])
    st, _, obj = r.create_target_pod(annotations=AC.fixture_annotations(), webhook=True)
    uid = obj['metadata']['uid']
    n_wh = len(api.cluster.webhook_log)
    res = AC.run_commit_bound(clients['responder'], AC.ARM_DIRECT, uid,
                              after_gate=lambda g: AC.write_annotation(clients['writer'], 'a', 'false'))
    assert res['outcome'] == 'CONFLICT_409' and len(api.cluster.webhook_log) == n_wh


def test_run_commit_bound_rejects_other_arms(clients):
    with pytest.raises(ValueError):
        AC.run_commit_bound(clients['responder'], 'A1_dcji', 'x')


def test_classify_conflict():
    assert AC.classify_conflict(200, {}) is None
    assert AC.classify_conflict(409, {'message': 'Precondition failed: UID in precondition: a'}) == 'uid'
    # kube-apiserver v1.37 wording (e39 probe 2026-09-26)
    assert AC.classify_conflict(409, {'message': 'Operation cannot be fulfilled on Pod "p": the ResourceVersion in the precondition (1293) does not match the ResourceVersion in record (1294). The object might have been modified'}) == 'resource_version'
    assert AC.classify_conflict(409, {'message': 'Operation cannot be fulfilled on Pod "p": the UID in the precondition (x) does not match the UID in record (y). The object might have been deleted and then recreated'}) == 'uid'
    assert AC.classify_conflict(409, {'message': 'x'}) == 'other'
