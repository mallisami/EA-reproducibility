"""E39-E NetworkPolicy action (fake CREATE + admission sim) and E39-G condition setup; fragment schemas."""
import os
import sys

import jsonschema
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import action_netpol as NP  # noqa: E402
import arms_commit as AC  # noqa: E402
import conditions_e39 as CE  # noqa: E402
import e39_kube as K  # noqa: E402
from e39_fragments import validate_fragment  # noqa: E402
from fake_api import FakeApiServer  # noqa: E402

NS = 'e39-selftest'


@pytest.fixture
def api():
    srv = FakeApiServer(namespace=NS).start()
    yield srv
    srv.stop()


@pytest.fixture
def clients(api):
    cs = {i: K.E39Client(api.base_url, NS, i) for i in ('responder', 'writer', 'reset')}
    yield cs
    for c in cs.values():
        c.close()


# ------------------------------------------------------------------ NetworkPolicy action
def test_build_isolation_netpol_shape():
    body = NP.build_isolation_netpol(NS, 'e39-isolate-3', 'run-t3', webhook=True)
    assert body['spec'] == {'podSelector': {'matchLabels': {'e39-trial': 'run-t3'}},
                            'policyTypes': ['Ingress', 'Egress']}
    assert body['metadata']['labels']['e39-webhook'] == 'enforce'
    assert 'e39-webhook' not in NP.build_isolation_netpol(NS, 'e39-isolate-3', 'x')['metadata']['labels']
    assert K._is_isolation_netpol(body, NS)
    assert NP.target_pod_labels('run-t3') == {'e39-trial': 'run-t3'}


def test_trial_label_value_dns_safe():
    import re
    for run_id, tid in (('r20260926a', 1), ('R_2026/09/26 x' * 10, 123456), ('e39', 100001)):
        v = NP.trial_label_value(run_id, tid)
        assert re.match(r'^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$', v), v
        assert NP.trial_label_value(run_id, tid) == v
        n = NP.netpol_name(tid, run_id)
        assert n.startswith('e39-isolate-') and len(n) <= 63
    assert NP.trial_label_value('r', 1) != NP.trial_label_value('r', 2)


def test_netpol_create_201_record(api, clients):
    frag = NP.create_isolation_netpol(clients['responder'], 'e39-isolate-1', 'lbl-1')
    assert frag['http_status'] == 201 and frag['created_resource_version'] and frag['audit_id']
    assert frag['t_resp_mono_ns'] >= frag['t_send_mono_ns']
    validate_fragment('netpol', frag)
    ev = [a for a in api.cluster.audit_log if a['auditID'] == frag['audit_id']][0]
    assert ev['verb'] == 'create' and ev['objectRef']['resource'] == 'networkpolicies'
    assert ev['objectRef']['apiGroup'] == 'networking.k8s.io' and ev['responseStatus']['code'] == 201
    assert ev['responseObject']['metadata']['resourceVersion'] == frag['created_resource_version']
    eff = NP.netpol_effect(clients['responder'], 'e39-isolate-1')
    assert eff['exists'] and eff['resource_version'] == frag['created_resource_version']
    # second CREATE of the same name -> 409 AlreadyExists (no retry)
    frag2 = NP.create_isolation_netpol(clients['responder'], 'e39-isolate-1', 'lbl-1')
    assert frag2['http_status'] == 409 and frag2['reason'] == 'AlreadyExists'
    validate_fragment('netpol', frag2)
    # reset cleanup
    assert NP.cleanup_isolation_netpol(clients['reset'], 'e39-isolate-1') == 200
    assert NP.cleanup_isolation_netpol(clients['reset'], 'e39-isolate-1') == 404
    assert not NP.netpol_effect(clients['responder'], 'e39-isolate-1')['exists']


def test_netpol_rv_orders_with_configmap_writes(api, clients):
    """Global revision: a ConfigMap write committed before the CREATE has a smaller RV (oracle primary rule)."""
    st, _, cm = clients['writer'].patch_configmap('response-scope', {'allowed': 'false'})
    frag = NP.create_isolation_netpol(clients['responder'], 'e39-isolate-2', 'l2')
    assert int(cm['metadata']['resourceVersion']) < int(frag['created_resource_version'])


def test_netpol_fake_webhook_deny_allow(api, clients):
    n0 = len(api.cluster.webhook_log)
    ok = NP.create_isolation_netpol(clients['responder'], 'e39-isolate-10', 'l10', webhook=True)
    assert ok['http_status'] == 201
    clients['writer'].patch_configmap('response-scope', {'allowed': 'false'})
    denied = NP.create_isolation_netpol(clients['responder'], 'e39-isolate-11', 'l11', webhook=True)
    assert denied['http_status'] == 403 and denied['reason'] == 'Forbidden'
    assert denied['created_resource_version'] is None and denied['audit_id']
    validate_fragment('netpol', denied)
    logs = api.cluster.webhook_log[n0:]
    assert [(w['object_name'], w['decision']) for w in logs] == [('e39-isolate-10', 'allow'),
                                                               ('e39-isolate-11', 'deny')]
    assert all(w['resource'] == 'networkpolicies' and len(w['reads']) == 8 for w in logs)
    # without the webhook label (A1): not routed to admission
    plain = NP.create_isolation_netpol(clients['responder'], 'e39-isolate-12', 'l12')
    assert plain['http_status'] == 201 and len(api.cluster.webhook_log) == n0 + 2
    ev = [a for a in api.cluster.audit_log if a['auditID'] == denied['audit_id']][0]
    assert ev['responseStatus']['code'] == 403


def test_netpol_admission_disabled():
    with FakeApiServer(namespace=NS, admission_enabled=False) as srv:
        c = K.E39Client(srv.base_url, NS, 'responder')
        w = K.E39Client(srv.base_url, NS, 'writer')
        w.patch_configmap('response-scope', {'allowed': 'false'})
        assert NP.create_isolation_netpol(c, 'e39-isolate-1', 'l', webhook=True)['http_status'] == 201
        c.close()
        w.close()


def test_netpol_safety_rail(clients):
    c = clients['responder']
    good = NP.build_isolation_netpol(NS, 'e39-isolate-5', 'l5')
    bad_rules = dict(good, spec=dict(good['spec'], ingress=[{'from': [{'podSelector': {}}]}]))
    bad_name = dict(good, metadata=dict(good['metadata'], name='allow-all'))
    bad_sel = dict(good, spec=dict(good['spec'], podSelector={}))
    bad_types = dict(good, spec=dict(good['spec'], policyTypes=['Ingress']))
    for body in (bad_rules, bad_name, bad_sel, bad_types):
        with pytest.raises(K.APIError):
            c.create_netpol(body)
    with pytest.raises(K.APIError):
        c.delete_netpol('allow-all')


# ------------------------------------------------------------------ E39-G conditions
def test_condition_values_and_validity():
    v = CE.condition_values(CE.OUT_OF_MODEL)
    assert [k for k, x in v.items() if x == 'false'] == ['evidence-b', 'evidence-d', 'evidence-f']
    assert v['response-scope'] == v['application-policy'] == 'true'
    w = CE.condition_values(CE.IN_MODEL)
    assert [k for k, x in w.items() if x == 'false'] == ['evidence-a', 'evidence-c', 'evidence-e']
    assert CE.policy_valid(v) is False and CE.policy_valid(w) is False
    assert CE.policy_valid({n: 'true' for n in K.POLICY_CMS}) is True
    # annotation view evaluates identically
    assert AC.evaluate_annotations(CE.condition_annotations(CE.OUT_OF_MODEL))[0] is False
    assert CE.initial_values(CE.IN_MODEL, 'annotation')['e38.evidence/a'] == 'false'
    assert CE.expected_outcome(CE.OUT_OF_MODEL, 'A1_dcji') == 'EXECUTE'
    assert CE.expected_outcome(CE.OUT_OF_MODEL, 'A2_fullrefresh') == 'ABORT'
    assert CE.expected_outcome(CE.IN_MODEL, 'A1_dcji') == 'ABORT'
    with pytest.raises(ValueError):
        CE.condition_values('async_scope')
    with pytest.raises(ValueError):
        CE.initial_values(CE.IN_MODEL, 'etcd')


@pytest.mark.parametrize('condition', [CE.OUT_OF_MODEL, CE.IN_MODEL])
def test_apply_condition_configmap(api, clients, condition):
    recs = CE.apply_condition(clients['reset'], condition, CE.STORAGE_CONFIGMAP)
    assert [r['object'] for r in recs] == ['evidence-' + k for k in CE.CUT_FLAGS[condition]]
    assert all(r['http_status'] == 200 and r['audit_id'] and r['resulting_resource_version'] for r in recs)
    ok, obs = CE.verify_condition(clients['responder'], condition, CE.STORAGE_CONFIGMAP)
    assert ok, obs
    # epoch untouched, ConfigMap ground truth invalid
    assert api.cluster.configmaps['e38-epoch']['data'] == {'sequence': '0'}
    assert api.cluster._v_core() is False


@pytest.mark.parametrize('condition', [CE.OUT_OF_MODEL, CE.IN_MODEL])
def test_apply_condition_annotation(api, clients, condition):
    r = clients['reset']
    r.delete_pod(r.get_pod()[0]['metadata']['uid'])
    st, _, pod = r.create_target_pod(annotations=AC.fixture_annotations())
    uid = pod['metadata']['uid']
    recs = CE.apply_condition(r, condition, CE.STORAGE_ANNOTATION, pod_uid=uid)
    assert len(recs) == 1 and recs[0]['http_status'] == 200
    ok, obs = CE.verify_condition(clients['responder'], condition, CE.STORAGE_ANNOTATION)
    assert ok, obs
    # the ConfigMaps are untouched in annotation storage
    assert CE.observe(clients['responder'], CE.STORAGE_CONFIGMAP) == {n: 'true' for n in K.POLICY_CMS}
    # A4d gate sees the invalid state and aborts (commit-bound arms evaluate all eight)
    res = AC.run_commit_bound(clients['responder'], AC.ARM_DIRECT, uid)
    assert res['outcome'] == 'GATE_ABORT' and 'J_false' in res['gate_reason']


def test_apply_condition_annotation_wrong_uid_raises(api, clients):
    with pytest.raises(K.APIError):
        CE.apply_condition(clients['reset'], CE.OUT_OF_MODEL, CE.STORAGE_ANNOTATION, pod_uid='stale')


# ------------------------------------------------------------------ fragment schema negatives
def test_fragment_schema_rejects_malformed():
    bad = [('bound_state', {'pod_rv_at_gate': 5, 'annotations_at_gate': {}}),
           ('bound_state', {'pod_rv_at_gate': '5', 'annotations_at_gate': {'other': 'x'}}),
           ('delete_preconditions', {'uid': 'x'}),
           ('mirror', {'source_object': 'e38-epoch', 'source_commit_rv': '1', 'source_commit_wall_ns': 1,
                       'mirror_commit_rv': None, 'mirror_commit_wall_ns': None, 'mirror_audit_id': None}),
           ('netpol', {'name': 'allow-all', 't_send_mono_ns': 1, 't_resp_mono_ns': 2, 'http_status': 201,
                       'audit_id': 'a', 'created_resource_version': '3'}),
           ('netpol', {'name': 'e39-isolate-1', 't_send_mono_ns': 1, 't_resp_mono_ns': 2, 'http_status': 201,
                       'audit_id': 'a', 'created_resource_version': '3', 'extra': 1})]
    for name, frag in bad:
        with pytest.raises(jsonschema.ValidationError):
            validate_fragment(name, frag)
    for name in ('bound_state', 'mirror', 'netpol'):
        validate_fragment(name, None)
    validate_fragment('delete_preconditions', {'uid': None, 'resource_version': None})
