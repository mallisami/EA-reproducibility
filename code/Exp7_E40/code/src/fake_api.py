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
    target Pod carries the label ``e40-webhook=enforce`` (VWC objectSelector) and the
    caller is not the reset identity (VWC matchConditions). It re-reads the eight
    ConfigMaps under the store lock and denies (HTTP 403) when V_core is false.
    Each admission appends an e38v2.webhook.v1 entry to ``cluster.webhook_log`` (with
    per-read wall timestamps). The caller identity is taken from the User-Agent
    ``e40-runner/<identity>`` (fake only).
  * Every ConfigMap PATCH and pod DELETE appends an audit.k8s.io/v1 ResponseComplete
    event (RequestResponse level) to ``cluster.audit_log``; the UID-precondition check
    happens before admission, as in kube-apiserver.

E39 extensions (additive; every E38v2 route and response is unchanged):
  * DELETE pods: DeleteOptions.preconditions.resourceVersion is honoured (checked after the UID
    precondition and BEFORE admission, as in the etcd3 store) -> 409 Conflict on mismatch.
  * PATCH pods/<target> (merge patch): metadata.annotations (null deletes a key), optional
    metadata.uid / metadata.resourceVersion preconditions (409); bumps the Pod RV; audited.
    POST pods keeps metadata.annotations; GET pods returns them.
  * LIST configmaps (GET .../configmaps) with metadata.resourceVersion = current global revision, and
    WATCH configmaps (GET .../configmaps?watch=true&resourceVersion=RV&timeoutSeconds=N): chunked
    newline-delimited JSON events (ADDED/MODIFIED/ERROR). RV older than the compaction point ->
    ERROR event with Status 410 Expired. Test hooks on FakeCluster: compact(), close_watches(),
    watch_paused (events are held back while True).
  * NetworkPolicy CREATE/GET/DELETE under /apis/networking.k8s.io/v1/namespaces/<ns>/networkpolicies
    with Audit-Id and audit events (objectRef.apiGroup networking.k8s.io). Admission simulation for
    CREATE when the new object carries e40-webhook=enforce (objectSelector on the new object) and the
    caller is not the reset identity: V_core over the eight ConfigMaps, 403 on deny, webhook_log entry.

NOT part of any live run.
"""
from __future__ import annotations
import json, threading, time, uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

EVIDENCE_CMS = tuple('evidence-' + k for k in 'abcdef')
OBJ_SCOPE = 'response-scope'
OBJ_APP = 'application-policy'
OBJ_EPOCH = 'e38-epoch'
ALL_CMS = EVIDENCE_CMS + (OBJ_SCOPE, OBJ_APP, OBJ_EPOCH)
POLICY_CMS = EVIDENCE_CMS + (OBJ_SCOPE, OBJ_APP)
POD_NAME = 'e40-target'
WEBHOOK_LABEL = ('e40-webhook', 'enforce')
NETPOL_GROUP = 'networking.k8s.io'
MAX_EVENTS = 20000


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
        # E39: condition variable on the SAME store lock (watch streams wait on it)
        self.cond = threading.Condition(self.lock)
        self._rev = 100
        self.configmaps = {}
        self.pod = None
        self.netpols = {}
        self.audit_log = []
        self.webhook_log = []
        self.events = []            # E39: (rev:int, resource, type, object) for configmaps
        self.compacted_rev = 0      # E39: watches from an RV below this get 410 Expired
        self.watch_generation = 0   # E39: close_watches() bumps it; open streams end
        self.watch_paused = False   # E39: hold back event delivery (stalled-watch simulation)
        self.closing = False
        self.reset_fixture()

    def _next_rev(self):
        self._rev += 1
        return self._rev

    def cm_object(self, name):
        cm = self.configmaps[name]
        return {'apiVersion': 'v1', 'kind': 'ConfigMap',
                'metadata': {'name': name, 'namespace': self.ns,
                             'uid': cm['uid'], 'resourceVersion': cm['resourceVersion'],
                             'labels': {'e40-managed': 'true'}},
                'data': dict(cm['data'])}

    def _emit(self, resource, typ, obj):
        """Caller holds the lock."""
        self.events.append((int(obj['metadata']['resourceVersion']), resource, typ, obj))
        if len(self.events) > MAX_EVENTS:
            drop = len(self.events) - MAX_EVENTS
            self.compacted_rev = max(self.compacted_rev, self.events[drop - 1][0])
            del self.events[:drop]
        self.cond.notify_all()

    # ---- E39 test hooks
    def compact(self):
        """Forget all history up to now: watches from an older RV get 410 Expired."""
        with self.lock:
            self.compacted_rev = self._rev
            self.events = []

    def close_watches(self):
        with self.lock:
            self.watch_generation += 1
            self.cond.notify_all()

    def set_watch_paused(self, paused):
        with self.lock:
            self.watch_paused = bool(paused)
            self.cond.notify_all()

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
            for name in ALL_CMS:
                self._emit('configmaps', 'MODIFIED', self.cm_object(name))
            self.pod = {'uid': 'pod-' + uuid.uuid4().hex[:12], 'resourceVersion': str(self._next_rev()),
                        'labels': {'e40-managed': 'true'}, 'annotations': {}}

    def _v_core(self):
        ev = {name: self.configmaps[name]['data'].get('value') for name in EVIDENCE_CMS}
        scope_ok = self.configmaps[OBJ_SCOPE]['data'].get('allowed') == 'true'
        app_ok = self.configmaps[OBJ_APP]['data'].get('allowed') == 'true'
        return _j(ev) and scope_ok and app_ok


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'  # keep-alive
    # E39: TCP_NODELAY on the fake's sockets (headers and body are separate writes; without it every
    # response can stall ~40 ms on Nagle + delayed ACK, which made the offline self-test ~20x slower)
    disable_nagle_algorithm = True

    def log_message(self, *a):
        pass

    @property
    def cluster(self):
        return self.server.cluster

    def _identity(self):
        ua = self.headers.get('User-Agent', '')
        return ua.split('/', 1)[1] if ua.startswith('e40-runner/') else ''

    def _send(self, status, obj, extra_headers=None, audit_id=None):
        if isinstance(obj, dict) and obj.get('kind') == 'Status' and 'apiVersion' not in obj:
            # E39: a real metav1.Status (apiVersion v1, status Failure) so client-go (E39-F responder) decodes
            # the apiserver message (e.g. the 409 precondition text) instead of a generic error string
            obj = dict(obj, apiVersion='v1', metadata={}, status='Failure' if status >= 400 else 'Success')
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

    def _audit(self, audit_id, verb, resource, name, received_ns, code, response_obj, request_obj=None,
               api_group=None):
        oref = {'resource': resource, 'namespace': self.cluster.ns, 'name': name, 'apiVersion': 'v1'}
        if api_group:
            oref['apiGroup'] = api_group
        ev = {'kind': 'Event', 'apiVersion': 'audit.k8s.io/v1', 'level': 'RequestResponse',
              'auditID': audit_id, 'stage': 'ResponseComplete', 'requestURI': self.path, 'verb': verb,
              'user': {'username': 'system:serviceaccount:e40-system:e40-' + (self._identity() or 'unknown')},
              'objectRef': oref,
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
        return self.cluster.cm_object(name)

    def _pod_obj(self, rv=None):
        p = self.cluster.pod
        if p is None:
            return None
        # E38v2 RERUN: the fake target Pod is Running and Ready as soon as it exists (the live runner
        # waits for Running+Ready before the gate).
        md = {'name': POD_NAME, 'namespace': self.cluster.ns, 'uid': p['uid'],
              'resourceVersion': rv or p['resourceVersion'], 'labels': dict(p['labels'])}
        if p.get('annotations'):
            md['annotations'] = dict(p['annotations'])   # E39
        return {'apiVersion': 'v1', 'kind': 'Pod',
                'metadata': md,
                'spec': {'nodeName': 'fake-node'},
                'status': {'phase': 'Running',
                           'conditions': [{'type': 'Ready', 'status': 'True'}],
                           'containerStatuses': [{'name': 'target', 'state': {'running': {}}}]}}

    # ---- E39: NetworkPolicy objects
    def _netpol_root(self):
        return f'/apis/{NETPOL_GROUP}/v1/namespaces/{self.cluster.ns}/networkpolicies'

    def _netpol_obj(self, name, rv=None):
        n = self.cluster.netpols[name]
        return {'apiVersion': f'{NETPOL_GROUP}/v1', 'kind': 'NetworkPolicy',
                'metadata': {'name': name, 'namespace': self.cluster.ns, 'uid': n['uid'],
                             'resourceVersion': rv or n['resourceVersion'], 'labels': dict(n['labels'])},
                'spec': json.loads(json.dumps(n['spec']))}

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
        # ---- E39 routes (paths with a query string or new resources)
        parts = urlsplit(path)
        q = parse_qs(parts.query)
        if parts.path == f'{root}/configmaps':
            if (q.get('watch') or [''])[0] in ('1', 'true'):
                return self._watch_configmaps(q)
            with self.cluster.lock:
                items = [self._cm_obj(n) for n in ALL_CMS]
                lst = {'apiVersion': 'v1', 'kind': 'ConfigMapList',
                       'metadata': {'resourceVersion': str(self.cluster._rev)}, 'items': items}
            return self._send(200, lst)
        if parts.path.startswith(self._netpol_root() + '/'):
            name = parts.path[len(self._netpol_root()) + 1:]
            with self.cluster.lock:
                if name in self.cluster.netpols:
                    return self._send(200, self._netpol_obj(name))
        return self._send(404, {'kind': 'Status', 'code': 404, 'reason': 'NotFound'})

    def _chunk(self, data):
        self.wfile.write(b'%x\r\n' % len(data) + data + b'\r\n')
        self.wfile.flush()

    def _watch_configmaps(self, q):
        """E39: chunked watch stream of configmap events after resourceVersion."""
        try:
            since = int((q.get('resourceVersion') or ['0'])[0] or 0)
        except ValueError:
            return self._send(400, {'kind': 'Status', 'code': 400, 'reason': 'BadRequest'})
        try:
            timeout_s = float((q.get('timeoutSeconds') or ['30'])[0])
        except ValueError:
            timeout_s = 30.0
        self.close_connection = True
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Transfer-Encoding', 'chunked')
        self.send_header('Audit-Id', uuid.uuid4().hex)
        self.end_headers()
        c = self.cluster
        end = time.monotonic() + timeout_s
        try:
            with c.lock:
                gen = c.watch_generation
                expired = since and since < c.compacted_rev
                initial = []
                if not since:   # no RV / RV "0": synthetic ADDED for the current state, then changes
                    initial = [self._cm_obj(n) for n in ALL_CMS]
                    since = c._rev
            for o in initial:
                self._chunk(json.dumps({'type': 'ADDED', 'object': o}).encode() + b'\n')
            if expired:
                ev = {'type': 'ERROR', 'object': {'kind': 'Status', 'apiVersion': 'v1', 'status': 'Failure',
                                                  'code': 410, 'reason': 'Expired',
                                                  'message': 'too old resource version: %d (%d)'
                                                             % (since, c.compacted_rev)}}
                self._chunk(json.dumps(ev).encode() + b'\n')
            else:
                while True:
                    with c.lock:
                        batch = []
                        while not batch:
                            if c.closing or c.watch_generation != gen or time.monotonic() >= end:
                                break
                            if not c.watch_paused:
                                batch = [(r, t, o) for (r, res, t, o) in c.events
                                         if res == 'configmaps' and r > since]
                            if not batch:
                                c.cond.wait(min(0.05, max(0.0, end - time.monotonic())))
                    if not batch:
                        break
                    for r, t, o in batch:
                        self._chunk(json.dumps({'type': t, 'object': o}).encode() + b'\n')
                        since = r
            self.wfile.write(b'0\r\n\r\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def do_POST(self):
        path = self.path
        body = self._read_body()
        if path == '/apis/authorization.k8s.io/v1/selfsubjectaccessreviews':
            return self._send(201, {'apiVersion': 'authorization.k8s.io/v1',
                'kind': 'SelfSubjectAccessReview', 'status': {'allowed': True}})
        if path == '/apis/authentication.k8s.io/v1/selfsubjectreviews':
            return self._send(201, {'apiVersion': 'authentication.k8s.io/v1', 'kind': 'SelfSubjectReview',
                'status': {'userInfo': {'username': 'system:serviceaccount:e40-system:e40-' + self._identity()}}})
        root = self._ns_root()
        with self.cluster.lock:
            if path == f'{root}/pods':
                if self.cluster.pod is not None:
                    return self._send(409, {'kind': 'Status', 'code': 409, 'reason': 'AlreadyExists'})
                labels = dict(((body or {}).get('metadata') or {}).get('labels') or {})
                anns = {k: str(v) for k, v in
                        (((body or {}).get('metadata') or {}).get('annotations') or {}).items()}
                self.cluster.pod = {'uid': 'pod-' + uuid.uuid4().hex[:12],
                                    'resourceVersion': str(self.cluster._next_rev()), 'labels': labels,
                                    'annotations': anns}
                return self._send(201, self._pod_obj())
        if path == self._netpol_root():
            return self._create_netpol(body)
        return self._send(400, {'kind': 'Status', 'code': 400, 'reason': 'BadRequest'})

    def _create_netpol(self, body):
        """E39: NetworkPolicy CREATE with Audit-Id, audit event and optional admission simulation."""
        received = time.time_ns()
        aid = uuid.uuid4().hex
        md = (body or {}).get('metadata') or {}
        name = md.get('name')
        grp = NETPOL_GROUP
        if (not isinstance(body, dict) or body.get('kind') != 'NetworkPolicy' or not name
                or not isinstance(body.get('spec'), dict)):
            st = {'kind': 'Status', 'code': 422, 'reason': 'Invalid'}
            with self.cluster.lock:
                self._audit(aid, 'create', 'networkpolicies', name or '', received, 422, st, body, api_group=grp)
            return self._send(422, st, audit_id=aid)
        labels = dict(md.get('labels') or {})
        with self.cluster.lock:
            if name in self.cluster.netpols:
                st = {'kind': 'Status', 'code': 409, 'reason': 'AlreadyExists',
                      'message': 'networkpolicies.networking.k8s.io "%s" already exists' % name}
                self._audit(aid, 'create', 'networkpolicies', name, received, 409, st, body, api_group=grp)
                return self._send(409, st, audit_id=aid)
            webhook_on = (self.cluster.admission_enabled
                          and labels.get(WEBHOOK_LABEL[0]) == WEBHOOK_LABEL[1]
                          and self._identity() != 'reset')
            if webhook_on:
                wh_received = time.time_ns()
                reads = []
                for n in POLICY_CMS:
                    t_send = time.time_ns()
                    cm = self.cluster.configmaps[n]
                    val = cm['data'].get('value', cm['data'].get('allowed'))
                    reads.append({'object': n, 'resource_version': cm['resourceVersion'], 'value': val,
                                  't_send_wall_ns': t_send, 't_recv_wall_ns': time.time_ns()})
                ok = self.cluster._v_core()
                self.cluster.webhook_log.append({
                    'schema': 'e38v2.webhook.v1', 'request_uid': uuid.uuid4().hex, 'pod_name': '',
                    'pod_uid': '', 'resource': 'networkpolicies', 'operation': 'CREATE', 'object_name': name,
                    'received_wall_ns': wh_received, 'reads': reads,
                    'decision': 'allow' if ok else 'deny', 'reason': 'fake V_core=%s' % ok,
                    'completed_wall_ns': time.time_ns()})
                if not ok:
                    st = {'kind': 'Status', 'code': 403, 'reason': 'Forbidden',
                          'message': 'admission webhook "delete-revalidation" denied the request: V_core false'}
                    self._audit(aid, 'create', 'networkpolicies', name, received, 403, st, body, api_group=grp)
                    return self._send(403, st, audit_id=aid)
            self.cluster.netpols[name] = {'uid': 'np-' + uuid.uuid4().hex[:12],
                                          'resourceVersion': str(self.cluster._next_rev()),
                                          'labels': labels, 'spec': body['spec']}
            obj = self._netpol_obj(name)
            self._audit(aid, 'create', 'networkpolicies', name, received, 201, obj, body, api_group=grp)
            return self._send(201, obj, audit_id=aid)

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
                    self.cluster._emit('configmaps', 'MODIFIED', obj)   # E39 watch
                    return self._send(200, obj, audit_id=aid)
            if path == f'{root}/pods/{POD_NAME}':
                return self._patch_pod(aid, received, body)
        return self._send(404, {'kind': 'Status', 'code': 404, 'reason': 'NotFound'})

    def _patch_pod(self, aid, received, body):
        """E39: merge-patch of the target Pod's annotations (caller holds the lock)."""
        p = self.cluster.pod
        if p is None:
            st = {'kind': 'Status', 'code': 404, 'reason': 'NotFound'}
            self._audit(aid, 'patch', 'pods', POD_NAME, received, 404, st, body)
            return self._send(404, st, audit_id=aid)
        md = (body or {}).get('metadata') or {}
        if not isinstance(md, dict) or set(body or {}) - {'metadata'} or set(md) - {'annotations', 'uid',
                                                                                     'resourceVersion'}:
            st = {'kind': 'Status', 'code': 422, 'reason': 'Invalid',
                  'message': 'fake apiserver supports metadata.annotations patches only'}
            self._audit(aid, 'patch', 'pods', POD_NAME, received, 422, st, body)
            return self._send(422, st, audit_id=aid)
        if md.get('uid') and md['uid'] != p['uid']:
            st = {'kind': 'Status', 'code': 409, 'reason': 'Conflict',
                  'message': 'Precondition failed: UID in precondition: %s, UID in object meta: %s'
                             % (md['uid'], p['uid'])}
            self._audit(aid, 'patch', 'pods', POD_NAME, received, 409, st, body)
            return self._send(409, st, audit_id=aid)
        if md.get('resourceVersion') and str(md['resourceVersion']) != p['resourceVersion']:
            st = {'kind': 'Status', 'code': 409, 'reason': 'Conflict',
                  'message': 'Operation cannot be fulfilled on pods "%s": the object has been modified; '
                             'please apply your changes to the latest version and try again' % POD_NAME}
            self._audit(aid, 'patch', 'pods', POD_NAME, received, 409, st, body)
            return self._send(409, st, audit_id=aid)
        anns = p.setdefault('annotations', {})
        for k, v in (md.get('annotations') or {}).items():
            if v is None:
                anns.pop(k, None)
            else:
                anns[k] = str(v)
        p['resourceVersion'] = str(self.cluster._next_rev())
        obj = self._pod_obj()
        self._audit(aid, 'patch', 'pods', POD_NAME, received, 200, obj, body)
        return self._send(200, obj, audit_id=aid)

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
                    # message format of kube-apiserver v1.37 (observed in the e39 probe 2026-09-26)
                    st = {'kind': 'Status', 'code': 409, 'reason': 'Conflict',
                          'message': 'Operation cannot be fulfilled on Pod "%s": the UID in the precondition (%s) '
                                     'does not match the UID in record (%s). The object might have been deleted '
                                     'and then recreated' % (POD_NAME, want_uid, p['uid'])}
                    self._audit(aid, 'delete', 'pods', POD_NAME, received, 409, st, body)
                    return self._send(409, st, audit_id=aid)
                # E39: resourceVersion precondition (storage-level, before admission)
                want_rv = (body or {}).get('preconditions', {}).get('resourceVersion')
                if want_rv and str(want_rv) != p['resourceVersion']:
                    st = {'kind': 'Status', 'code': 409, 'reason': 'Conflict',
                          'message': 'Operation cannot be fulfilled on Pod "%s": the ResourceVersion in the '
                                     'precondition (%s) does not match the ResourceVersion in record (%s). The '
                                     'object might have been modified' % (POD_NAME, want_rv, p['resourceVersion'])}
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
            # E39: NetworkPolicy DELETE (reset cleanup)
            if path.startswith(self._netpol_root() + '/'):
                name = path[len(self._netpol_root()) + 1:]
                if name in self.cluster.netpols:
                    obj = self._netpol_obj(name, rv=str(self.cluster._next_rev()))
                    del self.cluster.netpols[name]
                    self._audit(aid, 'delete', 'networkpolicies', name, received, 200, obj, body,
                                api_group=NETPOL_GROUP)
                    return self._send(200, obj, audit_id=aid)
                st = {'kind': 'Status', 'code': 404, 'reason': 'NotFound'}
                self._audit(aid, 'delete', 'networkpolicies', name, received, 404, st, body, api_group=NETPOL_GROUP)
                return self._send(404, st, audit_id=aid)
        return self._send(404, {'kind': 'Status', 'code': 404, 'reason': 'NotFound'})


class _QuietServer(ThreadingHTTPServer):
    """E39: a client closing an idle keep-alive / watch connection is not an error worth a traceback."""

    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class FakeApiServer:
    """Threaded 127.0.0.1 fake apiserver context manager."""

    def __init__(self, namespace='e40-selftest', admission_enabled=True):
        self.cluster = FakeCluster(namespace, admission_enabled=admission_enabled)
        self.httpd = _QuietServer(('127.0.0.1', 0), _Handler)
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
        with self.cluster.lock:        # E39: end open watch streams
            self.cluster.closing = True
            self.cluster.cond.notify_all()
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=5)

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()
