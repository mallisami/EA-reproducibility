#!/usr/bin/env python3
"""E38v2 experiment runner (piece 3 + integration).

Evaluates whether a client-side revalidation gate (DCJI / FullRefresh), optionally
backed by a server-side validating admission webhook, keeps an incident-response
Pod DELETE valid when an independent ConfigMap write occurs shortly after the gate.

Modes
  --make-schedule OUT     write a schedule file (e38v2.schedule.v1); no network
  --dry-run               schedule counts + runtime estimate; no network
  --offline-selftest      run a schedule against the in-process fake apiserver
  (live)                  run a LOCKED schedule file against the dedicated kind cluster
                          e38v2 (https://127.0.0.1:<port>, CA pinned, ServiceAccount
                          bearer tokens for the responder / writer / reset identities)

Fixes vs frozen (see DESIGN_NOTES.txt): (a) reset order + separate reset identity,
(b) horizon from SESSION config with fail-closed certificate assertion,
(c) no writer activity inside timed gate regions, (d) no runner-side use-time
oracle, (e) persistent keep-alive connections + Audit-Id capture.
"""
from __future__ import annotations
import argparse, hashlib, http.client, importlib.util, json, os, random, ssl, sys, time
from pathlib import Path
from urllib.parse import urlsplit

from e38v2_model import (frozen_planner_certificate, make_hybrid, core_decision, FLAGS, POD_NAME)
from schema import ARMS, CONDITIONS, DELTA_GRID_MS, validate_record

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
DEFAULT_F5 = WORKSPACE / 'frozen_ref' / 'source_provenance' / 'E37_F5_PHYS_ORIGINAL.json'

EVIDENCE_CMS = tuple('evidence-' + k for k in FLAGS)
OBJ_SCOPE = 'response-scope'
OBJ_APP = 'application-policy'
OBJ_EPOCH = 'e38-epoch'
ALL_CMS = EVIDENCE_CMS + (OBJ_SCOPE, OBJ_APP, OBJ_EPOCH)

ASYNC_CONDITIONS = ('async_scope', 'async_app_policy', 'async_evidence_last_literal')
FORBIDDEN_SEED = 20260912
# E38v2 RERUN: the first E38v2 run's seed may not be reused either.
FORBIDDEN_SEEDS = (20260912, 1772720255)
SCHEDULE_SCHEMA = 'e38v2.schedule.v1'

# Scheduler sizes (interface spec SAMPLE SIZE / STAGES).
STAGE1_ASYNC_N = 39
STAGE1_NO_WRITE_N = 39
STAGE1_UID_N = 10
STAGE2_TARGET_N = 93
STAGE2_TRIAL_ID_BASE = 100000    # stage-2 trial ids are 100001.. (unique across stages)

# Runtime-estimate constants, MEASURED in the live smoke run smoke-20260926b (kind e38v2, gated target
# Pod): mean gate-start-to-next-gate-start per trial = 0.074 s (no_write), 0.140 s (uid_rebind_control),
# 0.122 s (writer trials). WRITER_OVERHEAD_S = 0.122 - 0.074 = 0.048 s remains an ESTIMATE
# (the offline-build values were 2.07 / 4.01 / 0.15 s from the prior E38 run with running Pods).
# E38v2 RERUN (Running+Ready target Pod): re-measured in the rerun smoke smoke-r20260926a (reset start to next
# reset start): no_write 0.578 s, uid_rebind_control 1.204 s, writer trials 0.700 s -> WRITER_OVERHEAD_S =
# 0.700 - 0.578 = 0.122 s (estimate). Used only for the dry-run runtime estimate.
SEC_PER_TRIAL_NON_UID = 0.578
SEC_PER_TRIAL_UID = 1.204
WRITER_OVERHEAD_S = 0.122

REQUIRED_CONTEXT = 'kind-e38v2'
NS_PREFIX = 'e38v2-'
WEBHOOK_ARM = 'A3_dcji_webhook'
WEBHOOK_LABEL = ('e38v2-webhook', 'enforce')
# E38v2 RERUN: the target Pod is a genuinely Running-and-Ready Pod. Image = the pause image that the
# kindest/node:v1.37.0 node image ships (sandbox image, present in the node's containerd store; no pulls:
# imagePullPolicy Never). The first run used python:3.12-slim with a scheduling gate (never started).
TARGET_IMAGE = 'registry.k8s.io/pause:3.10'
READY_TIMEOUT_S = 90.0
READY_POLL_S = 0.02
GONE_TIMEOUT_S = 30.0
DELETE_GRACE_PERIOD_S = 0      # unchanged from the first run (DeleteOptions.gracePeriodSeconds = 0)

# Live-run guards
INFRA_ABORT_FRACTION = 0.05     # abort if INFRA_ERROR fraction exceeds this after >= 100 trials
INFRA_ABORT_CONSECUTIVE = 20
MIN_MEM_AVAILABLE_KB = 2 * 1024 * 1024


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def mono_ns():
    return time.monotonic_ns()


class CIHeaders(dict):
    """Case-insensitive header dict."""
    def __init__(self, pairs):
        super().__init__()
        for k, v in pairs:
            self[k.lower()] = v

    def get(self, key, default=None):
        return super().get(key.lower(), default)


class APIError(RuntimeError):
    pass


class KubeClient:
    """Persistent keep-alive connection to kube-apiserver on the LOOPBACK interface only.

    One instance per identity (responder / writer / reset).
      http://127.0.0.1:PORT   offline fake apiserver only (no credentials sent)
      https://127.0.0.1:PORT  live kind cluster: CA pinned (ca_path), bearer token (token_path)
    Enforces the mutation allowlist (safety rail) and captures the Audit-Id header (fix e).
    """

    def __init__(self, base, namespace, identity, token_path=None, context=None, ca_path=None,
                 timeout_s=20.0):
        parsed = urlsplit(base)
        if (parsed.scheme not in ('http', 'https') or parsed.hostname != '127.0.0.1' or parsed.port is None
                or parsed.username or parsed.password or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment or parsed.netloc != f'127.0.0.1:{parsed.port}'):
            raise ValueError('Only an exact http(s)://127.0.0.1:PORT loopback URL is supported')
        if not namespace.startswith(NS_PREFIX) or len(namespace) > 63 or namespace == 'e38v2-system':
            raise ValueError('Namespace must be disposable and prefixed ' + NS_PREFIX)
        self.scheme = parsed.scheme
        if self.scheme == 'https' and (not ca_path or not token_path):
            raise ValueError('https requires a pinned CA file and a bearer token file')
        self.base = base.rstrip('/')
        self.host = parsed.hostname
        self.port = parsed.port
        self.ns = namespace
        self.identity = identity
        self.token_path = token_path
        self.context = context
        self.ca_path = ca_path
        self.timeout_s = timeout_s
        self.conn = None
        self.connections_opened = 0
        self._token = None
        self._ssl = None
        if self.scheme == 'https':
            ctx = ssl.create_default_context(cafile=ca_path)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            self._ssl = ctx
            with open(token_path, encoding='utf-8') as fh:
                self._token = fh.read().strip()
            if not self._token:
                raise ValueError('empty token file for ' + identity)

    def connect(self):
        if self.conn is None:
            if self.scheme == 'https':
                self.conn = http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout_s,
                                                        context=self._ssl)
            else:
                self.conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout_s)
            self.conn.connect()
            self.connections_opened += 1
        return self

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            finally:
                self.conn = None

    def _ns_root(self):
        return f'/api/v1/namespaces/{self.ns}'

    def _check_allowed(self, method, path, body):
        if method == 'GET':
            return
        ns_root = self._ns_root()
        allowed_cms = {f'{ns_root}/configmaps/{x}' for x in ALL_CMS}
        if method == 'POST' and path in ('/apis/authorization.k8s.io/v1/selfsubjectaccessreviews',
                                         '/apis/authentication.k8s.io/v1/selfsubjectreviews'):
            return
        elif method == 'POST' and path == f'{ns_root}/pods':
            md = (body or {}).get('metadata', {})
            labels = md.get('labels', {})
            if (md.get('name') == POD_NAME and labels.get('e38v2-managed') == 'true'
                    and labels.get(WEBHOOK_LABEL[0], WEBHOOK_LABEL[1]) == WEBHOOK_LABEL[1]):
                return
        elif method == 'PATCH' and path in allowed_cms:
            if isinstance((body or {}).get('data'), dict):
                return
        elif method == 'DELETE' and path == f'{ns_root}/pods/{POD_NAME}':
            if (body or {}).get('preconditions', {}).get('uid'):
                return
        raise APIError(f'WRITE BLOCKED: unapproved {method} {path} (safety rail)')

    def request(self, method, path, body=None, extra_headers=None):
        self._check_allowed(method, path, body)
        payload = json.dumps(body, sort_keys=True, separators=(',', ':')).encode() if body is not None else None
        headers = {'Content-Type': 'application/merge-patch+json' if method == 'PATCH' else 'application/json',
                   'Accept': 'application/json', 'User-Agent': 'e38v2-runner/' + self.identity}
        if self._token:
            headers['Authorization'] = 'Bearer ' + self._token
        if payload is not None:
            headers['Content-Length'] = str(len(payload))
        if extra_headers:
            headers.update(extra_headers)
        for attempt in (0, 1):
            reused = self.conn is not None
            self.connect()
            try:
                self.conn.request(method, path, body=payload, headers=headers)
                resp = self.conn.getresponse()
                raw = resp.read()
            except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError,
                    http.client.CannotSendRequest) as exc:
                # stale keep-alive connection closed by the server before our request: retry once
                self.close()
                if attempt == 0 and reused:
                    continue
                raise APIError(f'{method} {path}: {exc!r}') from exc
            except (http.client.HTTPException, OSError) as exc:
                self.close()
                raise APIError(f'{method} {path}: {exc!r}') from exc
            status = resp.status
            rheaders = CIHeaders(resp.getheaders())
            if resp.will_close:
                self.close()
            try:
                obj = json.loads(raw) if raw else None
            except ValueError:
                obj = None
            return status, rheaders, obj
        raise APIError(f'{method} {path}: unreachable')

    # --- typed helpers ---
    def get_version(self):
        return self.request('GET', '/version')

    def get_configmap(self, name):
        return self.request('GET', f'{self._ns_root()}/configmaps/{name}')

    def patch_configmap(self, name, data):
        body = {'data': {k: str(v) for k, v in data.items()}}
        return self.request('PATCH', f'{self._ns_root()}/configmaps/{name}', body)

    def get_pod(self):
        status, h, obj = self.request('GET', f'{self._ns_root()}/pods/{POD_NAME}')
        if status == 404:
            return None, h
        if status != 200:
            raise APIError(f'GET pod HTTP {status}')
        return obj, h

    def create_pod(self, webhook=False):
        """Target Pod (E38v2 RERUN): a normal, schedulable Pod running the node's pause image
        (imagePullPolicy Never, no pulls); callers wait until it is Running and Ready
        (wait_pod_ready). The first run's scheduling gate is removed. A3 trials carry the webhook
        objectSelector label."""
        labels = {'app': 'e38v2', 'e38v2-managed': 'true'}
        if webhook:
            labels[WEBHOOK_LABEL[0]] = WEBHOOK_LABEL[1]
        body = {'apiVersion': 'v1', 'kind': 'Pod',
                'metadata': {'name': POD_NAME, 'namespace': self.ns, 'labels': labels},
                'spec': {'restartPolicy': 'Never', 'terminationGracePeriodSeconds': 0,
                         'automountServiceAccountToken': False, 'enableServiceLinks': False,
                         'containers': [{'name': 'target', 'image': TARGET_IMAGE, 'imagePullPolicy': 'Never'}]}}
        status, h, obj = self.request('POST', f'{self._ns_root()}/pods', body)
        if status not in (200, 201):
            raise APIError(f'create pod HTTP {status}: {(obj or {}).get("message")}')
        return obj, h

    def delete_pod(self, uid):
        opts = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'gracePeriodSeconds': DELETE_GRACE_PERIOD_S,
                'preconditions': {'uid': uid}}
        return self.request('DELETE', f'{self._ns_root()}/pods/{POD_NAME}', opts)

    def ssar(self):
        body = {'apiVersion': 'authorization.k8s.io/v1', 'kind': 'SelfSubjectAccessReview',
                'spec': {'resourceAttributes': {'namespace': self.ns, 'verb': 'delete',
                         'group': '', 'resource': 'pods', 'name': POD_NAME}}}
        status, h, obj = self.request('POST', '/apis/authorization.k8s.io/v1/selfsubjectaccessreviews', body)
        allowed = bool(obj and obj.get('status', {}).get('allowed') is True)
        return allowed, h

    def whoami(self):
        body = {'apiVersion': 'authentication.k8s.io/v1', 'kind': 'SelfSubjectReview'}
        status, h, obj = self.request('POST', '/apis/authentication.k8s.io/v1/selfsubjectreviews', body)
        if status not in (200, 201):
            raise APIError(f'SelfSubjectReview HTTP {status}')
        return ((obj or {}).get('status') or {}).get('userInfo', {}).get('username')


# --------------------------------------------------------------------------- #
# Fixture reset (FIX (a)): restore ConfigMaps FIRST, then replace the Pod, using
# a dedicated reset identity distinct from responder and writer identities.
# async_evidence_last_literal: c and e are cut to false as part of the fixture
# (before the Pod is replaced), and initial_values records that state.
# --------------------------------------------------------------------------- #
def _patch_ok(client, name, data):
    status, _, _ = client.patch_configmap(name, data)
    if status != 200:
        raise APIError(f'reset patch {name} HTTP {status}')


def pod_state(obj):
    """Compact running-state summary of a Pod object (E38v2 RERUN): phase, Ready condition, node,
    resourceVersion, uid, whether a deletionTimestamp is set, container state."""
    if not isinstance(obj, dict):
        return None
    md = obj.get('metadata') or {}
    st = obj.get('status') or {}
    ready = None
    for c in st.get('conditions') or []:
        if c.get('type') == 'Ready':
            ready = c.get('status') == 'True'
    cstate = None
    for cs in st.get('containerStatuses') or []:
        s = cs.get('state') or {}
        cstate = 'running' if 'running' in s else ('terminated' if 'terminated' in s else
                                                   ('waiting' if 'waiting' in s else None))
    return {'phase': st.get('phase'), 'ready': ready, 'node': (obj.get('spec') or {}).get('nodeName'),
            'resource_version': md.get('resourceVersion'), 'uid': md.get('uid'),
            'terminating': bool(md.get('deletionTimestamp')), 'container_state': cstate}


def wait_pod_ready(client, uid, timeout_s=None, poll_s=None):
    """Poll GET until the Pod with this uid is Running and Ready (E38v2 RERUN). Raises APIError on
    timeout, disappearance, uid change or a terminal phase. Returns a record of the wait."""
    timeout_s = READY_TIMEOUT_S if timeout_s is None else timeout_s
    poll_s = READY_POLL_S if poll_s is None else poll_s
    t0 = mono_ns()
    polls = 0
    end = time.monotonic() + timeout_s
    last = None
    while True:
        obj, _ = client.get_pod()
        polls += 1
        s = pod_state(obj)
        last = s
        if s is None or s['uid'] != uid:
            raise APIError('wait_ready: target pod vanished or was replaced (uid %r)' % (None if s is None else s['uid']))
        if s['phase'] in ('Failed', 'Succeeded') or s['terminating']:
            raise APIError('wait_ready: target pod reached %s (terminating=%s)' % (s['phase'], s['terminating']))
        if s['phase'] == 'Running' and s['ready'] is True:
            t1 = mono_ns()
            return {'ready': True, 't_wait_start_mono_ns': t0, 't_ready_mono_ns': t1,
                    't_ready_wall_ns': time.time_ns(), 'wait_ms': (t1 - t0) / 1e6, 'polls': polls,
                    'phase': s['phase'], 'node': s['node'], 'resource_version': s['resource_version']}
        if time.monotonic() > end:
            raise APIError('wait_ready: pod not Running+Ready within %.0f s (last state %r)' % (timeout_s, last))
        time.sleep(poll_s)


def reset_fixture(reset_client, webhook=False, condition=None):
    initial = {}
    try:
        for name in EVIDENCE_CMS:
            _patch_ok(reset_client, name, {'value': 'true'})
            initial[name] = 'true'
        _patch_ok(reset_client, OBJ_SCOPE, {'allowed': 'true'});  initial[OBJ_SCOPE] = 'true'
        _patch_ok(reset_client, OBJ_APP, {'allowed': 'true'});    initial[OBJ_APP] = 'true'
        _patch_ok(reset_client, OBJ_EPOCH, {'sequence': '0'});    initial[OBJ_EPOCH] = '0'
        if condition == 'async_evidence_last_literal':
            # J = a AND b before the gate (fixture prep by the reset identity, not the writer)
            _patch_ok(reset_client, 'evidence-c', {'value': 'false'}); initial['evidence-c'] = 'false'
            _patch_ok(reset_client, 'evidence-e', {'value': 'false'}); initial['evidence-e'] = 'false'
        # THEN replace the Pod
        pod, _ = reset_client.get_pod()
        if pod is not None:
            uid = pod['metadata']['uid']
            status, _, _ = reset_client.delete_pod(uid)
            if status not in (200, 202):
                raise APIError(f'reset delete failed HTTP {status}')
            end = time.monotonic() + 30
            while time.monotonic() < end:
                pod, _ = reset_client.get_pod()
                if pod is None:
                    break
                time.sleep(0.02)
            else:
                raise APIError('reset: old pod did not disappear within 30 s')
        obj, _ = reset_client.create_pod(webhook=webhook)
        decision_uid = (obj or {}).get('metadata', {}).get('uid')
        if not decision_uid:
            pod, _ = reset_client.get_pod()
            decision_uid = pod['metadata']['uid']
        # E38v2 RERUN: the trial starts only once the target Pod is Running and Ready
        ready = wait_pod_ready(reset_client, decision_uid)
    except Exception as exc:
        return {'reset_ok': False, 'pod_uid_decision': '', 'initial_values': initial,
                'error': repr(exc)}, None
    return {'reset_ok': True, 'pod_uid_decision': decision_uid, 'initial_values': initial,
            'pod_ready': ready}, decision_uid


# --------------------------------------------------------------------------- #
# Gate. No writer activity is issued inside this timed region (fix c); the only
# writer interaction is a single non-blocking ``writer.arm(...)`` for negative
# delta, done immediately before the final fence read is sent.
# --------------------------------------------------------------------------- #
def _record_read(client, name, kind='cm'):
    t_send = mono_ns()
    if kind == 'pod':
        obj, _ = client.get_pod()
        t_recv = mono_ns()
        if obj is None:
            return {'object': POD_NAME, 'resource_version': None, 'value': None,
                    't_send_mono_ns': t_send, 't_recv_mono_ns': t_recv}, None
        return ({'object': POD_NAME,
                 'resource_version': obj['metadata'].get('resourceVersion'),
                 'value': obj['metadata'].get('uid'),
                 't_send_mono_ns': t_send, 't_recv_mono_ns': t_recv}, obj)
    status, _, obj = client.request('GET', f'{client._ns_root()}/configmaps/{name}')
    t_recv = mono_ns()
    if status != 200:
        raise APIError(f'gate read {name} HTTP {status}')
    data = (obj or {}).get('data', {})
    val = data.get('value', data.get('allowed', data.get('sequence')))
    return ({'object': name, 'resource_version': (obj or {}).get('metadata', {}).get('resourceVersion'),
             'value': val, 't_send_mono_ns': t_send, 't_recv_mono_ns': t_recv}, obj)


def run_gate(responder, arm, decision_uid, writer=None):
    reads = []
    ssar_allowed = None
    pod_objs = []
    t_start = mono_ns()

    def read_cm(name):
        rec, obj = _record_read(responder, name, kind='cm')
        reads.append(rec)
        return rec['value']

    def read_pod():
        rec, obj = _record_read(responder, None, kind='pod')
        reads.append(rec)
        pod_objs.append(obj)     # summarised only after the timed region (E38v2 RERUN)
        return rec['value']

    def arm_before_final(final_read_callable):
        if writer is not None and writer.negative:
            writer.arm(mono_ns())  # anchor = start of final fence read
        return final_read_callable()

    if arm == 'A0_native_uid':
        # Baseline: no ConfigMap re-read. A single pod read serves as the timed
        # final fence read and confirms the current uid.
        arm_before_final(read_pod)
        t_end = mono_ns(); t_end_wall = time.time_ns()
        return {'decision': 'EXECUTE', 't_start_mono_ns': t_start, 't_end_mono_ns': t_end,
                't_end_wall_ns': t_end_wall, 'reads': reads, 'ssar_allowed': ssar_allowed,
                '_pod_obj': pod_objs[-1] if pod_objs else None}

    # DCJI (A1/A3) and FullRefresh (A2) share the fenced structure.
    seq_before = read_cm(OBJ_EPOCH)
    measured = {}
    cert_flags = ('a', 'c', 'e') if arm in ('A1_dcji', WEBHOOK_ARM) else tuple(FLAGS)
    for k in cert_flags:
        measured[k] = (read_cm('evidence-' + k) == 'true')
    scope_ok = (read_cm(OBJ_SCOPE) == 'true')
    app_ok = (read_cm(OBJ_APP) == 'true')
    observed_uid = read_pod()
    ssar_allowed, _ = responder.ssar()
    seq_after = arm_before_final(lambda: read_cm(OBJ_EPOCH))
    t_end = mono_ns(); t_end_wall = time.time_ns()

    coherent = (seq_before is not None and seq_after is not None
                and seq_before == seq_after and int(seq_before) % 2 == 0)
    evidence = make_hybrid({k: True for k in FLAGS}, measured)
    uid_match = (observed_uid == decision_uid)
    decision = core_decision(evidence, scope_ok, app_ok, uid_match, bool(ssar_allowed), coherent)
    return {'decision': decision, 't_start_mono_ns': t_start, 't_end_mono_ns': t_end,
            't_end_wall_ns': t_end_wall, 'reads': reads, 'ssar_allowed': ssar_allowed,
            '_pod_obj': pod_objs[-1] if pod_objs else None}


# --------------------------------------------------------------------------- #
# One trial. The runner logs raw facts only (fix d: NO use-time oracle here).
# --------------------------------------------------------------------------- #
WRITER_TARGET = {'async_scope': (OBJ_SCOPE, 'allowed', 'false'),
                 'async_app_policy': (OBJ_APP, 'allowed', 'false'),
                 'async_evidence_last_literal': ('evidence-a', 'value', 'false')}


def build_writer(condition, delta_ms, writer):
    """writer: a WriterProcess (live / selftest) or a KubeClient (thread writer, unit tests)."""
    if condition not in WRITER_TARGET:
        return None
    obj, key, value = WRITER_TARGET[condition]
    from writer import PostGateWriter, ProcessPostGateWriter, WriterProcess
    if isinstance(writer, WriterProcess):
        return ProcessPostGateWriter(writer, obj, key, value, delta_ms)
    return PostGateWriter(writer, obj, key, value, delta_ms)


def one_trial(clients, spec, fake_cluster=None):
    """clients: dict(responder=, writer=, reset=); writer is a WriterProcess or a KubeClient.
    spec: dict from the schedule."""
    responder = clients['responder']; reset = clients['reset']
    arm = spec['arm']; condition = spec['condition']; delta_ms = spec['delta_ms']
    webhook = (arm == WEBHOOK_ARM)

    record = {'schema': 'e38v2.trial.v1', 'run_id': spec['run_id'], 'trial_id': spec['trial_id'],
              'stage': spec['stage'], 'block': spec['block'], 'arm': arm, 'condition': condition,
              'delta_ms': delta_ms, 'rep': spec['rep'], 'schedule_seed': spec['schedule_seed'],
              'fixture': None, 'gate': None, 'writer': None,
              'delete': {'dispatched': False, 't_send_mono_ns': None, 't_send_wall_ns': None,
                         't_resp_mono_ns': None, 'http_status': None, 'reason': None,
                         'audit_id': None, 'expected_uid': '', 'deleted_object_resource_version': None},
              'effect': {'verified': False, 't_verified_mono_ns': None},
              'webhook_request_uid': None, 'status': 'COMPLETE', 'error': None}
    writer = None
    try:
        fixture, decision_uid = reset_fixture(reset, webhook=webhook, condition=condition)
        record['fixture'] = {'reset_ok': fixture['reset_ok'],
                             'pod_uid_decision': fixture['pod_uid_decision'],
                             'initial_values': fixture['initial_values']}
        if fixture.get('pod_ready') is not None:
            record['fixture']['pod_ready'] = fixture['pod_ready']
        record['delete']['expected_uid'] = fixture['pod_uid_decision']
        if not fixture['reset_ok']:
            record['status'] = 'INFRA_ERROR'
            record['error'] = 'reset_failed: ' + str(fixture.get('error'))
            return record

        # pre-arm the async writer (own keep-alive connection, waiting) BEFORE the gate.
        if condition in ASYNC_CONDITIONS:
            writer = build_writer(condition, delta_ms, clients['writer'])
            writer.prearm()

        gate = run_gate(responder, arm, decision_uid, writer=writer)
        record['gate'] = gate

        # non-negative delta: arm the writer at the (now known) gate end.
        if writer is not None and not writer.negative:
            writer.arm(gate['t_end_mono_ns'])

        # uid_rebind_control: replace the Pod AFTER the gate (positive control).
        if condition == 'uid_rebind_control':
            pod, _ = reset.get_pod()
            if pod is not None:
                reset.delete_pod(pod['metadata']['uid'])
                end = time.monotonic() + 30
                while time.monotonic() < end:
                    pod, _ = reset.get_pod()
                    if pod is None:
                        break
                    time.sleep(0.02)
            obj, _ = reset.create_pod(webhook=webhook)
            new_uid = (obj or {}).get('metadata', {}).get('uid')
            # E38v2 RERUN: the replacement Pod is also Running+Ready before the stale-UID DELETE
            rdy = wait_pod_ready(reset, new_uid)
            record['uid_rebind'] = {'replacement_pod_uid': new_uid, 't_replaced_mono_ns': mono_ns(),
                                    'replacement_ready_mono_ns': rdy['t_ready_mono_ns']}

        # DELETE (responder identity) when the client gate accepts.
        if gate['decision'] == 'EXECUTE':
            t_send = mono_ns(); t_send_wall = time.time_ns()
            status, headers, obj = responder.delete_pod(decision_uid)
            t_resp = mono_ns()
            deleted_rv = None
            reason = None
            if isinstance(obj, dict):
                if status in (200, 202):
                    deleted_rv = obj.get('metadata', {}).get('resourceVersion')
                else:
                    reason = ('%s: %s' % (obj.get('reason') or '', obj.get('message') or ''))[:500]
            record['delete'] = {'dispatched': True, 't_send_mono_ns': t_send,
                                't_send_wall_ns': t_send_wall, 't_resp_mono_ns': t_resp,
                                'http_status': status, 'reason': reason,
                                'audit_id': headers.get('Audit-Id'),
                                'expected_uid': decision_uid,
                                'deleted_object_resource_version': deleted_rv}
            # webhook_request_uid is not visible to the client; the analysis joins the webhook
            # log on pod_uid == delete.expected_uid.
            if status in (200, 202):
                pod, _ = responder.get_pod()
                record['effect'] = {'verified': pod is None or pod['metadata'].get('uid') != decision_uid,
                                    't_verified_mono_ns': mono_ns()}
                # E38v2 RERUN: DELETE accepted (response carries deletionTimestamp) vs Pod object gone
                md = (obj or {}).get('metadata') or {} if isinstance(obj, dict) else {}
                record['effect']['delete_response_deletion_timestamp'] = md.get('deletionTimestamp')
                polls, gone_t = 1, (record['effect']['t_verified_mono_ns'] if record['effect']['verified'] else None)
                end = time.monotonic() + GONE_TIMEOUT_S
                while gone_t is None and time.monotonic() < end:
                    time.sleep(READY_POLL_S)
                    pod, _ = responder.get_pod()
                    polls += 1
                    if pod is None or pod['metadata'].get('uid') != decision_uid:
                        gone_t = mono_ns()
                record['effect']['t_gone_mono_ns'] = gone_t
                record['effect']['gone_polls'] = polls
        # pod state observed by the gate's last pod read (summarised after the timed path)
        record['gate']['pod_state'] = pod_state(record['gate'].pop('_pod_obj', None))
        if gate['decision'] == 'UNKNOWN_COHERENCE':
            record['status'] = 'UNKNOWN'

        # collect the asynchronous writer result (also after an abort: the oracle needs its RV)
        if writer is not None:
            res = writer.join(timeout=30)
            if res is None:
                raise APIError('writer produced no result')
            record['writer'] = res
            if res.get('error'):
                record['status'] = 'INFRA_ERROR'
                record['error'] = 'writer_error: ' + res['error']
    except APIError as exc:
        record['status'] = 'INFRA_ERROR'
        record['error'] = repr(exc)
    except Exception as exc:  # pragma: no cover - defensive
        record['status'] = 'INFRA_ERROR'
        record['error'] = repr(exc)
    finally:
        if isinstance(record.get('gate'), dict) and '_pod_obj' in record['gate']:
            record['gate']['pod_state'] = pod_state(record['gate'].pop('_pod_obj', None))
        if writer is not None and record['writer'] is None:
            try:
                writer.cancel()
                if writer.result is not None:
                    record['writer'] = writer.result
            except Exception as exc:
                record['error'] = (record['error'] or '') + ' | writer_cancel: ' + repr(exc)
                record['status'] = 'INFRA_ERROR'
    return record


# --------------------------------------------------------------------------- #
# Scheduler
# --------------------------------------------------------------------------- #
def _load_topups(topup_file):
    """Accepts analysis/stage2_topup.py output (e38v2.stage2_topup.v1: {"cells": [..n_topup..]})
    or a bare list of {arm, condition, delta_ms, n_additional | n_target}."""
    doc = json.loads(Path(topup_file).read_text())
    cells = doc.get('cells') if isinstance(doc, dict) else doc
    if not isinstance(cells, list):
        raise ValueError('top-up file has no cell list')
    out = []
    for c in cells:
        if 'n_topup' in c:
            n_add = int(c['n_topup'])
        elif c.get('n_additional') is not None:
            n_add = int(c['n_additional'])
        else:
            n_add = int(c.get('n_target', STAGE2_TARGET_N)) - STAGE1_ASYNC_N
        if c['condition'] not in ASYNC_CONDITIONS:
            raise ValueError('stage-2 top-up only for async conditions')
        out.append({'arm': c['arm'], 'condition': c['condition'],
                    'delta_ms': None if c.get('delta_ms') is None else float(c['delta_ms']),
                    'n_add': n_add, 'n_stage1': int(c.get('n_stage1_planned', STAGE1_ASYNC_N))})
    return out


def build_schedule(stage, seed, run_id, topup_file=None, reps=None):
    """reps: number of stage-1 replicate blocks (default 39 = pre-registered design; smoke uses 1)."""
    if seed in FORBIDDEN_SEEDS:
        raise ValueError(f'seed {seed} is refused (used by a prior locked run: {FORBIDDEN_SEEDS})')
    schedule = []
    if stage == 1:
        rng = random.Random(seed)
        blocks = STAGE1_ASYNC_N if reps is None else int(reps)
        uid_blocks = STAGE1_UID_N if reps is None else min(int(reps), STAGE1_UID_N)
        for block in range(blocks):
            units = []
            for cond in ASYNC_CONDITIONS:
                for arm in ARMS:
                    for delta in DELTA_GRID_MS:
                        units.append({'arm': arm, 'condition': cond, 'delta_ms': float(delta)})
            for arm in ARMS:
                units.append({'arm': arm, 'condition': 'no_write', 'delta_ms': None})
            if block < uid_blocks:
                for arm in ARMS:
                    units.append({'arm': arm, 'condition': 'uid_rebind_control', 'delta_ms': None})
            rng.shuffle(units)   # randomize arm and delta within the block
            for u in units:
                u.update(block=block, rep=block, stage=1)
                schedule.append(u)
        first_id = 1
    elif stage == 2:
        if not topup_file:
            raise ValueError('stage 2 requires --topup-file')
        rng = random.Random(seed * 1000 + 2)
        cells = _load_topups(topup_file)
        maxadd = max([c['n_add'] for c in cells] or [0])
        for k in range(maxadd):
            units = [{'arm': c['arm'], 'condition': c['condition'], 'delta_ms': c['delta_ms'],
                      'rep': c['n_stage1'] + k} for c in cells if k < c['n_add']]
            rng.shuffle(units)
            for u in units:
                u.update(block=STAGE1_ASYNC_N + k, stage=2)
                schedule.append(u)
        first_id = STAGE2_TRIAL_ID_BASE + 1
    else:
        raise ValueError('stage must be 1 or 2')
    for i, u in enumerate(schedule):
        u['trial_id'] = first_id + i
        u['run_id'] = run_id
        u['schedule_seed'] = seed
    return schedule


def schedule_document(schedule, stage, seed, run_id, note=None):
    trials = [{k: u[k] for k in ('trial_id', 'stage', 'block', 'arm', 'condition', 'delta_ms', 'rep',
                                 'schedule_seed')} for u in schedule]
    doc = {'schema': SCHEDULE_SCHEMA, 'run_id': run_id, 'schedule_seed': seed, 'stage': stage,
           'n_trials': len(trials), 'trials': trials}
    if note:
        doc['note'] = note
    return doc


def load_schedule_file(path):
    doc = json.loads(Path(path).read_text())
    if not isinstance(doc, dict) or doc.get('schema') != SCHEDULE_SCHEMA:
        raise ValueError('not an e38v2.schedule.v1 document: ' + str(path))
    specs = []
    for e in doc['trials']:
        s = dict(e)
        s['run_id'] = doc['run_id']
        s.setdefault('schedule_seed', doc['schedule_seed'])
        specs.append(s)
    return doc, specs


def schedule_counts(schedule):
    cells, arms, conds = {}, {}, {}
    uid_count = writer_count = 0
    for u in schedule:
        key = (u['arm'], u['condition'], u['delta_ms'])
        cells[key] = cells.get(key, 0) + 1
        arms[u['arm']] = arms.get(u['arm'], 0) + 1
        conds[u['condition']] = conds.get(u['condition'], 0) + 1
        if u['condition'] == 'uid_rebind_control':
            uid_count += 1
        if u['condition'] in ASYNC_CONDITIONS:
            writer_count += 1
    return {'total': len(schedule), 'by_arm': arms, 'by_condition': conds,
            'distinct_cells': len(cells), 'uid_trials': uid_count,
            'writer_trials': writer_count, 'non_uid_trials': len(schedule) - uid_count}


def runtime_estimate(counts, sec_non_uid=None, sec_uid=None, writer_overhead=None):
    a = SEC_PER_TRIAL_NON_UID if sec_non_uid is None else sec_non_uid
    b = SEC_PER_TRIAL_UID if sec_uid is None else sec_uid
    c = WRITER_OVERHEAD_S if writer_overhead is None else writer_overhead
    seconds = counts['non_uid_trials'] * a + counts['uid_trials'] * b + counts['writer_trials'] * c
    return {'seconds': round(seconds, 1), 'hours': round(seconds / 3600.0, 3),
            'model': {'sec_per_trial_non_uid': a, 'sec_per_trial_uid': b, 'writer_overhead_s': c,
                      'writer_overhead_is_estimate': True}}


# --------------------------------------------------------------------------- #
# LOCK verification (protocol/lock_hash.py format; the lock tool is itself locked)
# --------------------------------------------------------------------------- #
def _lock_tool():
    p = WORKSPACE / 'protocol' / 'lock_hash.py'
    spec = importlib.util.spec_from_file_location('e38v2_lock_hash', p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def verify_lock(lock_path, schedule_path, schedule_doc, stage):
    """Refuses (APIError) unless the lock verifies completely AND covers the exact schedule file."""
    lh = _lock_tool()
    lock = json.loads(Path(lock_path).read_text())
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = lh.main(['verify', str(lock_path)])
    if rc != 0:
        raise APIError('LOCK verify failed: ' + buf.getvalue().strip().replace('\n', ' | ')[:2000])
    if not str(lock.get('status', '')).startswith('LOCKED'):
        raise APIError('LOCK status is not LOCKED')
    key = 'schedule_sha256' if stage == 1 else 'stage2_schedule_sha256'
    if lock.get(key) != sha256_file(schedule_path):
        raise APIError(f'LOCK {key} does not match {schedule_path}')
    if lock.get('schedule_seed') != schedule_doc.get('schedule_seed'):
        raise APIError('LOCK seed != schedule seed')
    if lock.get('run_id') and lock.get('run_id') != schedule_doc.get('run_id'):
        raise APIError('LOCK run_id != schedule run_id')
    return lock


# --------------------------------------------------------------------------- #
# Session metadata
# --------------------------------------------------------------------------- #
def build_session(run_id, stage, seed, horizon, cert, namespace, context, identities, extra=None):
    file_hashes = {}
    for p in sorted(HERE.glob('*.py')) + sorted((HERE / 'flashir_core').glob('*.py')):
        file_hashes[str(p.relative_to(HERE)).replace(os.sep, '/')] = sha256_file(p)
    s = {
        'schema': 'e38v2.session.v1',
        'run_id': run_id, 'stage': stage, 'schedule_seed': seed,
        'horizon_s': horizon,
        'clock_anchor': {'mono_ns': mono_ns(), 'wall_ns': time.time_ns()},
        'namespace': namespace, 'context': context,
        'certificate': cert['certificate'],
        'certificate_invariant_horizons': cert['invariant_horizons_checked'],
        'f5_path': cert['f5_path'], 'f5_sha256': cert['f5_sha256'],
        'identities': identities,
        'file_sha256': file_hashes,
        'python': sys.version.split()[0],
        'switchinterval_s': sys.getswitchinterval(),
        'created_wall': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    if extra:
        s.update(extra)
    return s


# --------------------------------------------------------------------------- #
# Trial loop (shared by live mode and offline self-test)
# --------------------------------------------------------------------------- #
def mem_available_kb():
    try:
        with open('/proc/meminfo') as fh:
            for line in fh:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def log_line(fh, msg):
    line = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()) + ' ' + msg
    if fh is not None:
        fh.write(line + '\n')
        fh.flush()
    else:
        print(line, flush=True)


def run_trials(clients, specs, raw_path, logfh=None, guards=True, stop_file=None, remake_writer=None):
    """Runs specs in order, appending one validated record per trial to raw_path.
    Resume: trial_ids already in raw_path are skipped (never re-run)."""
    done = set()
    if raw_path.exists():
        for line in raw_path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                done.add(json.loads(line)['trial_id'])
    todo = [s for s in specs if s['trial_id'] not in done]
    if done:
        log_line(logfh, f'RESUME: {len(done)} trials already recorded, {len(todo)} to run')
    n = infra = consec = 0
    t0 = time.monotonic()
    stats = {'written': 0, 'infra': 0, 'aborted': None}
    with raw_path.open('a', encoding='utf8') as fh:
        for spec in todo:
            if stop_file is not None and Path(stop_file).exists():
                stats['aborted'] = 'stop_file'
                log_line(logfh, 'STOP file present: stopping before trial %s' % spec['trial_id'])
                break
            if guards:
                while True:
                    m = mem_available_kb()
                    if m is None or m >= MIN_MEM_AVAILABLE_KB:
                        break
                    log_line(logfh, f'PAUSE: MemAvailable {m} kB < 2 GiB; sleeping 30 s')
                    time.sleep(30)
            rec = one_trial(clients, spec)
            try:
                validate_record(rec)
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
                log_line(logfh, f'INFRA_ERROR trial {spec["trial_id"]}: {rec["error"]}')
                if remake_writer is not None and 'writer' in (rec.get('error') or ''):
                    clients['writer'] = remake_writer(clients.get('writer'))
                    log_line(logfh, 'writer process restarted')
                for k in ('responder', 'reset'):
                    clients[k].close()
            else:
                consec = 0
            if n % 50 == 0 or n == len(todo):
                el = time.monotonic() - t0
                log_line(logfh, f'PROGRESS {n}/{len(todo)} (total recorded {len(done) + n}) infra={infra} '
                                f'elapsed={el:.0f}s rate={el / n:.3f}s/trial mem_kb={mem_available_kb()}')
            if guards and (consec >= INFRA_ABORT_CONSECUTIVE
                           or (n >= 100 and infra / n > INFRA_ABORT_FRACTION)):
                stats['aborted'] = f'infra_error_guard (infra={infra}, n={n}, consecutive={consec})'
                log_line(logfh, 'ABORT: ' + stats['aborted'])
                break
            if clients.get('writer') is not None and hasattr(clients['writer'], 'alive') \
                    and not clients['writer'].alive():
                if remake_writer is None:
                    stats['aborted'] = 'writer_process_died'
                    break
                clients['writer'] = remake_writer(None)
                log_line(logfh, 'writer process died; restarted')
    stats['elapsed_s'] = round(time.monotonic() - t0, 1)
    return stats


# --------------------------------------------------------------------------- #
# --offline-selftest
# --------------------------------------------------------------------------- #
def offline_selftest(output_dir, seed, horizon, f5_path, reps=1, run_id='e38v2selftest', use_process_writer=True):
    """Runs a (reps-block) stage-1 schedule against the fake apiserver and writes the same files a
    live run produces: SCHEDULE.json, RAW_TRIALS.jsonl, SESSION.json, audit.log, webhook_log.jsonl."""
    from fake_api import FakeApiServer
    from writer import WriterProcess
    cert = frozen_planner_certificate(horizon, f5_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    namespace = 'e38v2-selftest'
    schedule = build_schedule(1, seed, run_id, reps=reps)
    doc = schedule_document(schedule, 1, seed, run_id, note='offline self-test schedule')
    (output_dir / 'SCHEDULE.json').write_text(json.dumps(doc, indent=1) + '\n')
    _, specs = load_schedule_file(output_dir / 'SCHEDULE.json')
    server = FakeApiServer(namespace=namespace).start()
    base = server.base_url
    wproc = None
    try:
        if use_process_writer:
            wproc = WriterProcess(lambda: KubeClient(base, namespace, 'writer', context=REQUIRED_CONTEXT))
            wproc.ping()
        clients = {
            'responder': KubeClient(base, namespace, 'responder', context=REQUIRED_CONTEXT),
            'writer': wproc if wproc is not None else KubeClient(base, namespace, 'writer', context=REQUIRED_CONTEXT),
            'reset': KubeClient(base, namespace, 'reset', context=REQUIRED_CONTEXT),
        }
        identities = {k: {'identity': k, 'context': REQUIRED_CONTEXT} for k in clients}
        raw_path = output_dir / 'RAW_TRIALS.jsonl'
        if raw_path.exists():
            raw_path.unlink()
        stats = run_trials(clients, specs, raw_path, guards=False)
        session = build_session(run_id, 1, seed, horizon, cert, namespace, REQUIRED_CONTEXT, identities,
                                extra={'selftest': True, 'raw_sha256': sha256_file(raw_path),
                                       'trials_written': stats['written']})
        (output_dir / 'SESSION.json').write_text(json.dumps(session, indent=2) + '\n')
        with (output_dir / 'audit.log').open('w') as fh:
            for ev in server.cluster.audit_log:
                fh.write(json.dumps(ev, sort_keys=True) + '\n')
        with (output_dir / 'webhook_log.jsonl').open('w') as fh:
            for ev in server.cluster.webhook_log:
                fh.write(json.dumps(ev, sort_keys=True) + '\n')
        recs = [json.loads(l) for l in raw_path.read_text().splitlines() if l.strip()]
        offs = sorted(r['writer']['actual_release_mono_ns'] - r['writer']['intended_release_mono_ns']
                      for r in recs if r['writer'] and r['delta_ms'] is not None and r['delta_ms'] >= 0)
        summary = {'trials': len(recs), 'infra_error': sum(r['status'] == 'INFRA_ERROR' for r in recs),
                   'writer_trials': sum(1 for r in recs if r['writer']),
                   'audit_events': len(server.cluster.audit_log),
                   'webhook_entries': len(server.cluster.webhook_log)}
        if offs:
            summary['release_offset_ns_nonneg_delta'] = {'min': offs[0], 'median': offs[len(offs) // 2],
                                                         'max': offs[-1]}
        print(json.dumps(summary, indent=2))
        return 0
    finally:
        for k in ('responder', 'reset'):
            try:
                clients[k].close()
            except Exception:
                pass
        if wproc is not None:
            wproc.stop()
        server.stop()


# --------------------------------------------------------------------------- #
# Live mode
# --------------------------------------------------------------------------- #
def live_run(a, cert):
    from writer import WriterProcess
    doc, specs = load_schedule_file(a.schedule)
    stage = int(doc['stage'])
    if doc['run_id'] != a.run_id:
        raise APIError('schedule run_id %r != --run-id %r' % (doc['run_id'], a.run_id))
    if a.namespace != NS_PREFIX + a.run_id:
        raise APIError('namespace must be e38v2-<run_id>')
    lock = None
    if a.lock:
        lock = verify_lock(a.lock, a.schedule, doc, stage)
    elif not a.unlocked_smoke:
        raise APIError('live run requires --lock (or --unlocked-smoke for a smoke run)')
    out = Path(a.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    logfh = open(out / 'runner.log', 'a', encoding='utf-8')
    sys.setswitchinterval(a.switch_interval)
    tok = Path(a.token_dir)

    def mk(identity):
        return KubeClient(a.api_server, a.namespace, identity, token_path=str(tok / f'{identity}.token'),
                          context=a.context, ca_path=a.ca_file)

    def remake_writer(old):
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
        w = WriterProcess(lambda: mk('writer'))
        w.ping()
        return w

    # fork the writer process BEFORE the parent opens its own connections
    wproc = remake_writer(None)
    clients = {'responder': mk('responder'), 'writer': wproc, 'reset': mk('reset')}
    try:
        # cluster identity checks (CA pinning already rejects any other cluster's apiserver)
        st, _, ver = clients['reset'].get_version()
        if st != 200 or not str((ver or {}).get('gitVersion', '')).startswith('v1.37'):
            raise APIError('unexpected apiserver version: %r' % (ver,))
        who = {k: clients[k].whoami() for k in ('responder', 'reset')}
        wc = mk('writer')
        who['writer'] = wc.whoami()
        wc.close()
        for k, u in who.items():
            if u != f'system:serviceaccount:e38v2-system:e38v2-{k}':
                raise APIError(f'identity {k} authenticated as {u!r}')
        st, _, _ = clients['reset'].get_configmap(OBJ_EPOCH)
        if st != 200:
            raise APIError(f'fixture ConfigMaps missing in {a.namespace} (HTTP {st})')
        allowed, _ = clients['responder'].ssar()
        if not allowed:
            raise APIError('responder SSAR delete pods not allowed')
        identities = {k: {'username': who[k], 'token_file': f'{k}.token (not exported)', 'context': a.context}
                      for k in who}
        session_path = out / ('SESSION.json' if stage == 1 else 'SESSION_STAGE2.json')
        session = build_session(a.run_id, stage, doc['schedule_seed'], a.horizon_seconds, cert, a.namespace,
                                a.context, identities,
                                extra={'api_server': a.api_server, 'ca_sha256': sha256_file(a.ca_file),
                                       'apiserver_version': ver.get('gitVersion'),
                                       'schedule_file': str(a.schedule), 'schedule_sha256': sha256_file(a.schedule),
                                       'n_scheduled': len(specs),
                                       'lock_file': str(a.lock) if a.lock else None,
                                       'lock_sha256_field': (lock or {}).get('lock_sha256'),
                                       'lock_status': (lock or {}).get('status'),
                                       'unlocked_smoke': bool(a.unlocked_smoke),
                                       'writer_impl': 'separate process (WriterProcess)',
                                       'target_pod': TARGET_IMAGE + ' (imagePullPolicy Never), Running+Ready before the gate',
                                       'delete_options': {'gracePeriodSeconds': DELETE_GRACE_PERIOD_S,
                                                          'preconditions': 'uid'},
                                       'runtime_constants': {'sec_per_trial_non_uid': SEC_PER_TRIAL_NON_UID,
                                                             'sec_per_trial_uid': SEC_PER_TRIAL_UID,
                                                             'writer_overhead_s_estimate': WRITER_OVERHEAD_S}})
        if session_path.exists():
            session_path = out / (session_path.stem + '_resume_%d.json' % int(time.time()))
        session_path.write_text(json.dumps(session, indent=2) + '\n')
        log_line(logfh, f'START stage={stage} run_id={a.run_id} trials={len(specs)} identities={who} '
                        f'lock={(lock or {}).get("status")} session={session_path.name}')
        stats = run_trials(clients, specs, out / 'RAW_TRIALS.jsonl', logfh=logfh, guards=True,
                           stop_file=out / 'STOP', remake_writer=remake_writer)
        log_line(logfh, 'END ' + json.dumps(stats))
        return 0 if stats['aborted'] is None else 3
    finally:
        for k in ('responder', 'reset'):
            clients[k].close()
        try:
            clients['writer'].stop()
        except Exception:
            pass
        logfh.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--stage', type=int, choices=[1, 2], default=1)
    ap.add_argument('--topup-file', type=Path, help='stage2_cells_E38v2.json from analysis/stage2_topup.py')
    ap.add_argument('--seed', type=int, default=3802)
    ap.add_argument('--reps', type=int, help='stage-1 replicate blocks (smoke only; default = design 39)')
    ap.add_argument('--horizon-seconds', type=float, default=30.0)
    ap.add_argument('--f5-path', type=Path, default=DEFAULT_F5)
    ap.add_argument('--run-id', default='e38v2run')
    ap.add_argument('--namespace', default='e38v2-run')
    ap.add_argument('--context', default=REQUIRED_CONTEXT)
    ap.add_argument('--api-server', help='https://127.0.0.1:<port> of the kind-e38v2 apiserver')
    ap.add_argument('--ca-file', help='cluster CA (from `kind get kubeconfig --name e38v2`)')
    ap.add_argument('--token-dir', help='directory with responder.token, writer.token, reset.token')
    ap.add_argument('--schedule', type=Path, help='schedule file to execute (live)')
    ap.add_argument('--make-schedule', type=Path, help='write the schedule document to this path and exit')
    ap.add_argument('--output-dir', type=Path)
    ap.add_argument('--lock', type=Path)
    ap.add_argument('--unlocked-smoke', action='store_true', help='allow a live run without LOCK (smoke only)')
    ap.add_argument('--switch-interval', type=float, default=5e-5,
                    help='sys.setswitchinterval for the responder process (default 50 us)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--offline-selftest', action='store_true')
    ap.add_argument('--selftest-reps', type=int, default=1)
    ap.add_argument('--ack-disposable', action='store_true')
    a = ap.parse_args(argv)

    # Certificate (fix b): fail closed if it is not {a,c,e,uid,scope} at the horizon.
    cert = frozen_planner_certificate(a.horizon_seconds, a.f5_path)

    if a.offline_selftest:
        if not a.output_dir:
            ap.error('--offline-selftest requires --output-dir')
        return offline_selftest(a.output_dir, a.seed, a.horizon_seconds, a.f5_path,
                                reps=a.selftest_reps, run_id=a.run_id)

    if a.make_schedule or a.dry_run:
        schedule = build_schedule(a.stage, a.seed, a.run_id, topup_file=a.topup_file, reps=a.reps)
        counts = schedule_counts(schedule)
        est = runtime_estimate(counts)
        if a.make_schedule:
            if a.make_schedule.exists():
                ap.error(f'refusing to overwrite {a.make_schedule}')
            doc = schedule_document(schedule, a.stage, a.seed, a.run_id,
                                    note=None if a.reps is None else f'reps={a.reps} (smoke/pilot)')
            a.make_schedule.parent.mkdir(parents=True, exist_ok=True)
            a.make_schedule.write_text(json.dumps(doc, indent=1) + '\n')
        out = {'stage': a.stage, 'seed': a.seed, 'run_id': a.run_id, 'horizon_s': a.horizon_seconds,
               'certificate': cert['certificate'], 'counts': counts, 'runtime_estimate': est,
               'schedule_file': str(a.make_schedule) if a.make_schedule else None}
        print(json.dumps(out, indent=2))
        if a.dry_run and a.output_dir:
            a.output_dir.mkdir(parents=True, exist_ok=True)
            (a.output_dir / 'SCHEDULE.json').write_text(
                json.dumps(schedule_document(schedule, a.stage, a.seed, a.run_id), indent=1) + '\n')
        return 0

    # ---- live run. Safety rails: ----
    if a.context != REQUIRED_CONTEXT:
        ap.error(f'refusing to run: --context must be exactly {REQUIRED_CONTEXT!r}')
    if not a.namespace.startswith(NS_PREFIX) or a.namespace == 'e38v2-system':
        ap.error(f'refusing to run: --namespace must start with {NS_PREFIX!r}')
    if not a.ack_disposable:
        ap.error('live execution requires --ack-disposable (operator acknowledges destructive '
                 'Pod/ConfigMap changes on the disposable e38v2 kind cluster)')
    if not a.output_dir:
        ap.error('a real run requires --output-dir')
    for need in ('api_server', 'ca_file', 'token_dir', 'schedule'):
        if not getattr(a, need):
            ap.error(f'live run requires --{need.replace("_", "-")}')
    if not str(a.api_server).startswith('https://127.0.0.1:'):
        ap.error('live run requires an https://127.0.0.1:<port> API endpoint')
    try:
        return live_run(a, cert)
    except APIError as exc:
        ap.error(str(exc))


if __name__ == '__main__':
    sys.exit(main())
