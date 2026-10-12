#!/usr/bin/env python3
"""In-process fake kube-apiserver for E38v2 offline self-test and unit tests.

Binds to 127.0.0.1 on an ephemeral port (loopback only, plain HTTP) and implements just
enough of the core/v1 + authorization/authentication surface the runner exercises:
GET/PATCH configmaps, GET/POST pods, DELETE pods with a UID precondition,
SelfSubjectAccessReview, SelfSubjectReview and /version.

etcd-like semantics: a single monotonically increasing global revision counter is
stamped as ``metadata.resourceVersion`` on every write (configmap PATCH, pod create,
pod DELETE), so numeric resourceVersion ordering across objects is meaningful --
matching the oracle's ordering assumption. Every response carries an ``Audit-Id``
header (uuid4).

E38v2 integration (mirrors the live cluster):
  * The validating-webhook simulation (A3) is applied to a pod DELETE only when the
    target Pod carries the label ``e38v2-webhook=enforce`` (VWC objectSelector) and the
    caller is not the reset identity (VWC matchConditions). It re-reads the eight
    ConfigMaps under the store lock and denies (HTTP 403) when V_core is false.
    Each admission appends an e38v2.webhook.v1 entry to ``cluster.webhook_log`` (with
    per-read wall timestamps). The caller identity is taken from the User-Agent
    ``e38v2-runner/<identity>`` (fake only).
  * Every ConfigMap PATCH and pod DELETE appends an audit.k8s.io/v1 ResponseComplete
    event (RequestResponse level) to ``cluster.audit_log``; the UID-precondition check
    happens before admission, as in kube-apiserver.

NOT part of any live run.
"""
from __future__ import annotations
import json, threading, time, uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EVIDENCE_CMS = tuple('evidence-' + k for k in 'abcdef')
OBJ_SCOPE = 'response-scope'
OBJ_APP = 'application-policy'
OBJ_EPOCH = 'e38-epoch'
ALL_CMS = EVIDENCE_CMS + (OBJ_SCOPE, OBJ_APP, OBJ_EPOCH)
POLICY_CMS = EVIDENCE_CMS + (OBJ_SCOPE, OBJ_APP)
POD_NAME = 'e38v2-target'
WEBHOOK_LABEL = ('e38v2-webhook', 'enforce')


def _j(evidence):
    def b(k):
        return evidence[k] == 'true'
    return (b('evidence-a') and b('evidence-b')) or (b('evidence-c') and b('evidence-d')) \
        or (b('evidence-e') and b('evidence-f'))


def k8s_time_us(ns):
    secs, rem = divmod(int(ns), 1_000_000_000)
    dt = datetime(1970, 1, 1) + timedelta(seconds=secs)
    return dt.strftime('%Y-%m-%dT%H:%M:%S') + '.%06dZ' % (rem // 1000)


class FakeCluster:
    """Mutable state store. Thread-safe via a single lock."""

    def __init__(self, namespace, admission_enabled=True):
        self.ns = namespace
        self.admission_enabled = admission_enabled
        self.lock = threading.Lock()
        self._rev = 100
        self.configmaps = {}
        self.pod = None
        self.audit_log = []
        self.webhook_log = []
        self.reset_fixture()

    def _next_rev(self):
        self._rev += 1
        return self._rev

    def reset_fixture(self):
        with self.lock:
            for name in EVIDENCE_CMS:
                self.configmaps[name] = {'data': {'value': 'true'},
                                         'uid': 'cm-' + name, 'resourceVersion': str(self._next_rev())}
            self.configmaps[OBJ_SCOPE] = {'data': {'allowed': 'true'}, 'uid': 'cm-scope',
                                          'resourceVersion': str(self._next_rev())}
            self.configmaps[OBJ_APP] = {'data': {'allowed': 'true'}, 'uid': 'cm-app',
                                        'resourceVersion': str(self._next_rev())}
            self.configmaps[OBJ_EPOCH] = {'data': {'sequence': '0'}, 'uid': 'cm-epoch',
                                          'resourceVersion': str(self._next_rev())}
            self.pod = {'uid': 'pod-' + uuid.uuid4().hex[:12], 'resourceVersion': str(self._next_rev()),
                        'labels': {'e38v2-managed': 'true'}}

    def _v_core(self):
        ev = {name: self.configmaps[name]['data'].get('value') for name in EVIDENCE_CMS}
        scope_ok = self.configmaps[OBJ_SCOPE]['data'].get('allowed') == 'true'
        app_ok = self.configmaps[OBJ_APP]['data'].get('allowed') == 'true'
        return _j(ev) and scope_ok and app_ok


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'  # keep-alive

    def log_message(self, *a):
        pass

    @property
    def cluster(self):
        return self.server.cluster

    def _identity(self):
        ua = self.headers.get('User-Agent', '')
        return ua.split('/', 1)[1] if ua.startswith('e38v2-runner/') else ''

    def _send(self, status, obj, extra_headers=None, audit_id=None):
        body = json.dumps(obj).encode() if obj is not None else b''
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Audit-Id', audit_id or uuid.uuid4().hex)
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _audit(self, audit_id, verb, resource, name, received_ns, code, response_obj, request_obj=None):
        ev = {'kind': 'Event', 'apiVersion': 'audit.k8s.io/v1', 'level': 'RequestResponse',
              'auditID': audit_id, 'stage': 'ResponseComplete', 'requestURI': self.path, 'verb': verb,
              'user': {'username': 'system:serviceaccount:e38v2-system:e38v2-' + (self._identity() or 'unknown')},
              'objectRef': {'resource': resource, 'namespace': self.cluster.ns, 'name': name, 'apiVersion': 'v1'},
              'responseStatus': {'metadata': {}, 'code': code},
              'requestReceivedTimestamp': k8s_time_us(received_ns),
              'stageTimestamp': k8s_time_us(time.time_ns())}
        if request_obj is not None:
            ev['requestObject'] = request_obj
        if response_obj is not None:
            ev['responseObject'] = response_obj
        self.cluster.audit_log.append(ev)

    def _read_body(self):
        n = int(self.headers.get('Content-Length', 0) or 0)
        raw = self.rfile.read(n) if n else b''
        if not raw:
            return None
        return json.loads(raw)

    def _ns_root(self):
        return f'/api/v1/namespaces/{self.cluster.ns}'

    def _cm_obj(self, name):
        cm = self.cluster.configmaps[name]
        return {'apiVersion': 'v1', 'kind': 'ConfigMap',
                'metadata': {'name': name, 'namespace': self.cluster.ns,
                             'uid': cm['uid'], 'resourceVersion': cm['resourceVersion'],
                             'labels': {'e38v2-managed': 'true'}},
                'data': dict(cm['data'])}

    def _pod_obj(self, rv=None):
        p = self.cluster.pod
        if p is None:
            return None
        # E38v2 RERUN: the fake target Pod is Running and Ready as soon as it exists (the live runner
        # waits for Running+Ready before the gate).
        return {'apiVersion': 'v1', 'kind': 'Pod',
                'metadata': {'name': POD_NAME, 'namespace': self.cluster.ns, 'uid': p['uid'],
                             'resourceVersion': rv or p['resourceVersion'], 'labels': dict(p['labels'])},
                'spec': {'nodeName': 'fake-node'},
                'status': {'phase': 'Running',
                           'conditions': [{'type': 'Ready', 'status': 'True'}],
                           'containerStatuses': [{'name': 'target', 'state': {'running': {}}}]}}

    def do_GET(self):
        path = self.path
        if path == '/version':
            return self._send(200, {'major': '1', 'minor': '37', 'gitVersion': 'v1.37.0-fake'})
        root = self._ns_root()
        with self.cluster.lock:
            for name in ALL_CMS:
                if path == f'{root}/configmaps/{name}':
                    return self._send(200, self._cm_obj(name))
            if path == f'{root}/pods/{POD_NAME}':
                obj = self._pod_obj()
                if obj is None:
                    return self._send(404, {'kind': 'Status', 'code': 404, 'reason': 'NotFound'})
                return self._send(200, obj)
        return self._send(404, {'kind': 'Status', 'code': 404, 'reason': 'NotFound'})

    def do_POST(self):
        path = self.path
        body = self._read_body()
        if path == '/apis/authorization.k8s.io/v1/selfsubjectaccessreviews':
            return self._send(201, {'apiVersion': 'authorization.k8s.io/v1',
                'kind': 'SelfSubjectAccessReview', 'status': {'allowed': True}})
        if path == '/apis/authentication.k8s.io/v1/selfsubjectreviews':
            return self._send(201, {'apiVersion': 'authentication.k8s.io/v1', 'kind': 'SelfSubjectReview',
                'status': {'userInfo': {'username': 'system:serviceaccount:e38v2-system:e38v2-' + self._identity()}}})
        root = self._ns_root()
        with self.cluster.lock:
            if path == f'{root}/pods':
                if self.cluster.pod is not None:
                    return self._send(409, {'kind': 'Status', 'code': 409, 'reason': 'AlreadyExists'})
                labels = dict(((body or {}).get('metadata') or {}).get('labels') or {})
                self.cluster.pod = {'uid': 'pod-' + uuid.uuid4().hex[:12],
                                    'resourceVersion': str(self.cluster._next_rev()), 'labels': labels}
                return self._send(201, self._pod_obj())
        return self._send(400, {'kind': 'Status', 'code': 400, 'reason': 'BadRequest'})

    def do_PATCH(self):
        received = time.time_ns()
        path = self.path
        body = self._read_body() or {}
        root = self._ns_root()
        aid = uuid.uuid4().hex
        with self.cluster.lock:
            for name in ALL_CMS:
                if path == f'{root}/configmaps/{name}':
                    data = (body or {}).get('data', {})
                    cm = self.cluster.configmaps[name]
                    cm['data'].update({k: str(v) for k, v in data.items()})
                    cm['resourceVersion'] = str(self.cluster._next_rev())
                    obj = self._cm_obj(name)
                    self._audit(aid, 'patch', 'configmaps', name, received, 200, obj, body)
                    return self._send(200, obj, audit_id=aid)
        return self._send(404, {'kind': 'Status', 'code': 404, 'reason': 'NotFound'})

    def do_DELETE(self):
        received = time.time_ns()
        path = self.path
        body = self._read_body() or {}
        root = self._ns_root()
        aid = uuid.uuid4().hex
        with self.cluster.lock:
            if path == f'{root}/pods/{POD_NAME}':
                p = self.cluster.pod
                if p is None:
                    st = {'kind': 'Status', 'code': 404, 'reason': 'NotFound'}
                    self._audit(aid, 'delete', 'pods', POD_NAME, received, 404, st, body)
                    return self._send(404, st, audit_id=aid)
                want_uid = (body or {}).get('preconditions', {}).get('uid')
                if not want_uid:
                    return self._send(400, {'kind': 'Status', 'code': 400,
                        'reason': 'BadRequest', 'message': 'UID precondition required'})
                if want_uid != p['uid']:
                    st = {'kind': 'Status', 'code': 409, 'reason': 'Conflict',
                          'message': 'Precondition failed: UID in precondition: %s, UID in object meta: %s'
                                     % (want_uid, p['uid'])}
                    self._audit(aid, 'delete', 'pods', POD_NAME, received, 409, st, body)
                    return self._send(409, st, audit_id=aid)
                # validating admission (A3 webhook simulation): objectSelector on the Pod label,
                # reset identity exempt (matchConditions)
                webhook_on = (self.cluster.admission_enabled
                              and p['labels'].get(WEBHOOK_LABEL[0]) == WEBHOOK_LABEL[1]
                              and self._identity() != 'reset')
                if webhook_on:
                    wh_received = time.time_ns()
                    reads = []
                    for name in POLICY_CMS:
                        t_send = time.time_ns()
                        cm = self.cluster.configmaps[name]
                        val = cm['data'].get('value', cm['data'].get('allowed'))
                        reads.append({'object': name, 'resource_version': cm['resourceVersion'], 'value': val,
                                      't_send_wall_ns': t_send, 't_recv_wall_ns': time.time_ns()})
                    ok = self.cluster._v_core()
                    self.cluster.webhook_log.append({
                        'schema': 'e38v2.webhook.v1', 'request_uid': uuid.uuid4().hex, 'pod_name': POD_NAME,
                        'pod_uid': p['uid'], 'received_wall_ns': wh_received, 'reads': reads,
                        'decision': 'allow' if ok else 'deny', 'reason': 'fake V_core=%s' % ok,
                        'completed_wall_ns': time.time_ns()})
                    if not ok:
                        st = {'kind': 'Status', 'code': 403, 'reason': 'Forbidden',
                              'message': 'admission webhook "delete-revalidation" denied the request: V_core false'}
                        self._audit(aid, 'delete', 'pods', POD_NAME, received, 403, st, body)
                        return self._send(403, st, audit_id=aid)
                rv = str(self.cluster._next_rev())
                deleted = self._pod_obj(rv=rv)
                self.cluster.pod = None
                self._audit(aid, 'delete', 'pods', POD_NAME, received, 200, deleted, body)
                return self._send(200, deleted, audit_id=aid)
        return self._send(404, {'kind': 'Status', 'code': 404, 'reason': 'NotFound'})


class FakeApiServer:
    """Threaded 127.0.0.1 fake apiserver context manager."""

    def __init__(self, namespace='e38v2-selftest', admission_enabled=True):
        self.cluster = FakeCluster(namespace, admission_enabled=admission_enabled)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.cluster = self.cluster
        self.host, self.port = self.httpd.server_address
        self.thread = None

    @property
    def base_url(self):
        return f'http://127.0.0.1:{self.port}'

    def start(self):
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=5)

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()
