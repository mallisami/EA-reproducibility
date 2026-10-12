#!/usr/bin/env python3
"""E39 loopback kube-apiserver client (commit-bound arms, mirror controller, NetworkPolicy action).

Same transport contract as e38v2_runner.KubeClient (persistent keep-alive connection, exact
http(s)://127.0.0.1:PORT loopback URL only, pinned CA + bearer token for https, Audit-Id capture,
User-Agent ``e39-runner/<identity>`` so the offline fake maps the identity), with an E39 mutation
allowlist (safety rail) that additionally permits exactly:
  * PATCH of the target Pod restricted to metadata.annotations with the e38.* validity keys
    (optionally carrying metadata.uid / metadata.resourceVersion as preconditions);
  * DELETE of the target Pod with a UID precondition (optionally also a resourceVersion precondition);
  * CREATE/DELETE of isolation NetworkPolicies named ``e39-isolate-*`` (empty rules, podSelector on
    the per-trial label only);
  * ConfigMap PATCH / target Pod CREATE / SSAR / SSR as in E38v2.
Everything else that is not a GET is refused before any byte is sent.

It also opens WATCH streams on a dedicated connection (``open_watch``) for the mirror controller.

Namespace: must start with ``e39-`` and must not be ``*-system`` or ``e39-load``.
PATCH of the target Pod may also set the E39-B arrival sequence annotation ``e38.seq``.
Offline modules only; nothing here contacts a cluster unless a caller passes a live loopback URL.
"""
from __future__ import annotations

import http.client
import json
import socket
import ssl
import time
from urllib.parse import urlsplit, urlencode

POD_NAME = 'e39-target'
NS_PREFIXES = ('e39-',)
EVIDENCE_FLAGS = ('a', 'b', 'c', 'd', 'e', 'f')
EVIDENCE_CMS = tuple('evidence-' + k for k in EVIDENCE_FLAGS)
OBJ_SCOPE = 'response-scope'
OBJ_APP = 'application-policy'
OBJ_EPOCH = 'e38-epoch'
POLICY_CMS = EVIDENCE_CMS + (OBJ_SCOPE, OBJ_APP)
ALL_CMS = POLICY_CMS + (OBJ_EPOCH,)
CM_VALUE_KEY = dict({n: 'value' for n in EVIDENCE_CMS}, **{OBJ_SCOPE: 'allowed', OBJ_APP: 'allowed'})

# Validity records as Pod annotations (A4d source of truth; A4m mirror target)
EVIDENCE_ANN = {k: 'e38.evidence/' + k for k in EVIDENCE_FLAGS}
SCOPE_ANN = 'e38.scope'
APP_ANN = 'e38.app-policy'
ANNOTATION_KEYS = tuple(EVIDENCE_ANN[k] for k in EVIDENCE_FLAGS) + (SCOPE_ANN, APP_ANN)
CM_TO_ANN = dict({'evidence-' + k: EVIDENCE_ANN[k] for k in EVIDENCE_FLAGS},
                 **{OBJ_SCOPE: SCOPE_ANN, OBJ_APP: APP_ANN})
ANN_TO_CM = {v: k for k, v in CM_TO_ANN.items()}
# E39-B (A4d) arrivals: per-arrival sequence annotation so that every arrival is a distinct commit
# (identical merge-PATCHes would be no-ops sharing one resourceVersion). Allowed on PATCH only.
SEQ_ANN = 'e38.seq'
PATCHABLE_ANNOTATION_KEYS = ANNOTATION_KEYS + (SEQ_ANN,)
# e39-system (control plane of the experiment) and e39-load (E39-D load namespace) are never targets
RESERVED_NAMESPACES = ('e39-system', 'e39-load')

NETPOL_API = '/apis/networking.k8s.io/v1'
NETPOL_PREFIX = 'e39-isolate-'
TRIAL_LABEL_KEY = 'e39-trial'
WEBHOOK_LABEL = ('e39-webhook', 'enforce')
DELETE_GRACE_PERIOD_S = 0


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


def check_namespace(namespace):
    if (not isinstance(namespace, str) or not namespace.startswith(NS_PREFIXES) or len(namespace) > 63
            or namespace.endswith('-system') or namespace in RESERVED_NAMESPACES):
        raise ValueError('Namespace must be disposable and prefixed one of %s (not *-system, not %s)'
                         % (NS_PREFIXES, ', '.join(RESERVED_NAMESPACES)))


def check_loopback_url(base):
    parsed = urlsplit(base)
    if (parsed.scheme not in ('http', 'https') or parsed.hostname != '127.0.0.1' or parsed.port is None
            or parsed.username or parsed.password or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment or parsed.netloc != f'127.0.0.1:{parsed.port}'):
        raise ValueError('Only an exact http(s)://127.0.0.1:PORT loopback URL is supported')
    return parsed


class E39Client:
    """One instance per identity (responder / writer / reset / mirror)."""

    def __init__(self, base, namespace, identity, token_path=None, ca_path=None, timeout_s=20.0,
                 pod_name=POD_NAME):
        parsed = check_loopback_url(base)
        check_namespace(namespace)
        self.scheme = parsed.scheme
        if self.scheme == 'https' and (not ca_path or not token_path):
            raise ValueError('https requires a pinned CA file and a bearer token file')
        self.base = base.rstrip('/')
        self.host = parsed.hostname
        self.port = parsed.port
        self.ns = namespace
        self.identity = identity
        self.pod_name = pod_name
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

    # ------------------------------------------------------------------ transport
    def _new_conn(self, timeout):
        if self.scheme == 'https':
            return http.client.HTTPSConnection(self.host, self.port, timeout=timeout, context=self._ssl)
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)

    def connect(self):
        if self.conn is None:
            self.conn = self._new_conn(self.timeout_s)
            self.conn.connect()
            try:
                self.conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
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

    def _netpol_root(self):
        return f'{NETPOL_API}/namespaces/{self.ns}/networkpolicies'

    def _headers(self, method):
        h = {'Content-Type': 'application/merge-patch+json' if method == 'PATCH' else 'application/json',
             'Accept': 'application/json', 'User-Agent': 'e39-runner/' + self.identity}
        if self._token:
            h['Authorization'] = 'Bearer ' + self._token
        return h

    # ------------------------------------------------------------------ safety rail
    def _check_allowed(self, method, path, body):
        if method == 'GET':
            return
        ns_root = self._ns_root()
        pod_path = f'{ns_root}/pods/{self.pod_name}'
        body = body if isinstance(body, dict) else {}
        if method == 'POST' and path in ('/apis/authorization.k8s.io/v1/selfsubjectaccessreviews',
                                         '/apis/authentication.k8s.io/v1/selfsubjectreviews'):
            return
        if method == 'POST' and path == f'{ns_root}/pods':
            md = body.get('metadata') or {}
            labels = md.get('labels') or {}
            anns = md.get('annotations') or {}
            if (md.get('name') == self.pod_name and labels.get('e39-managed') == 'true'
                    and labels.get(WEBHOOK_LABEL[0], WEBHOOK_LABEL[1]) == WEBHOOK_LABEL[1]
                    and set(anns) <= set(ANNOTATION_KEYS)):
                return
        elif method == 'PATCH' and path in {f'{ns_root}/configmaps/{x}' for x in ALL_CMS}:
            if isinstance(body.get('data'), dict) and set(body) == {'data'}:
                return
        elif method == 'PATCH' and path == pod_path:
            md = body.get('metadata')
            if (set(body) == {'metadata'} and isinstance(md, dict)
                    and set(md) <= {'annotations', 'uid', 'resourceVersion'}
                    and isinstance(md.get('annotations'), dict) and md['annotations']
                    and set(md['annotations']) <= set(PATCHABLE_ANNOTATION_KEYS)):
                return
        elif method == 'DELETE' and path == pod_path:
            if (body.get('preconditions') or {}).get('uid'):
                return
        elif method == 'POST' and path == self._netpol_root():
            if _is_isolation_netpol(body, self.ns):
                return
        elif method == 'DELETE' and path.startswith(self._netpol_root() + '/' + NETPOL_PREFIX):
            if '/' not in path[len(self._netpol_root()) + 1:]:
                return
        raise APIError(f'WRITE BLOCKED: unapproved {method} {path} (safety rail)')

    def request(self, method, path, body=None, extra_headers=None):
        self._check_allowed(method, path, body)
        payload = json.dumps(body, sort_keys=True, separators=(',', ':')).encode() if body is not None else None
        headers = self._headers(method)
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

    # ------------------------------------------------------------------ watch
    def open_watch(self, resource_path, resource_version=None, timeout_seconds=30, socket_timeout_s=None,
                   extra_query=None):
        """Open a WATCH stream on a DEDICATED connection. Returns (conn, response).

        The caller reads newline-delimited JSON events with response.readline() and must close conn.
        """
        q = {'watch': 'true', 'allowWatchBookmarks': 'true', 'timeoutSeconds': str(int(timeout_seconds))}
        if resource_version:
            q['resourceVersion'] = str(resource_version)
        q.update(extra_query or {})
        path = resource_path + '?' + urlencode(q)
        conn = self._new_conn(socket_timeout_s if socket_timeout_s is not None else timeout_seconds + 10)
        try:
            conn.request('GET', path, headers=self._headers('GET'))
            resp = conn.getresponse()
        except (http.client.HTTPException, OSError) as exc:
            conn.close()
            raise APIError(f'WATCH {path}: {exc!r}') from exc
        return conn, resp

    # ------------------------------------------------------------------ typed helpers
    def get_version(self):
        return self.request('GET', '/version')

    def ssar(self):
        """SelfSubjectAccessReview: may this identity DELETE the target Pod? -> (allowed, headers)."""
        body = {'apiVersion': 'authorization.k8s.io/v1', 'kind': 'SelfSubjectAccessReview',
                'spec': {'resourceAttributes': {'namespace': self.ns, 'verb': 'delete',
                                                'group': '', 'resource': 'pods', 'name': self.pod_name}}}
        status, h, obj = self.request('POST', '/apis/authorization.k8s.io/v1/selfsubjectaccessreviews', body)
        return bool(obj and (obj.get('status') or {}).get('allowed') is True), h

    def whoami(self):
        body = {'apiVersion': 'authentication.k8s.io/v1', 'kind': 'SelfSubjectReview'}
        status, h, obj = self.request('POST', '/apis/authentication.k8s.io/v1/selfsubjectreviews', body)
        if status not in (200, 201):
            raise APIError(f'SelfSubjectReview HTTP {status}')
        return (((obj or {}).get('status') or {}).get('userInfo') or {}).get('username')

    def get_pod(self):
        status, h, obj = self.request('GET', f'{self._ns_root()}/pods/{self.pod_name}')
        if status == 404:
            return None, h
        if status != 200:
            raise APIError(f'GET pod HTTP {status}')
        return obj, h

    def get_configmap(self, name):
        return self.request('GET', f'{self._ns_root()}/configmaps/{name}')

    def list_configmaps(self):
        return self.request('GET', f'{self._ns_root()}/configmaps')

    def patch_configmap(self, name, data):
        return self.request('PATCH', f'{self._ns_root()}/configmaps/{name}',
                            {'data': {k: str(v) for k, v in data.items()}})

    def patch_pod_annotations(self, annotations, uid=None, resource_version=None):
        md = {'annotations': {k: (None if v is None else str(v)) for k, v in annotations.items()}}
        if uid:
            md['uid'] = uid
        if resource_version:
            md['resourceVersion'] = str(resource_version)
        return self.request('PATCH', f'{self._ns_root()}/pods/{self.pod_name}', {'metadata': md})

    def create_target_pod(self, annotations=None, extra_labels=None, webhook=False,
                          image='registry.k8s.io/pause:3.10'):
        """Target Pod as in E38v2 RERUN (pause image, imagePullPolicy Never) plus E39 validity
        annotations (A4d/A4m) and extra labels (e.g. the per-trial NetworkPolicy selector label)."""
        labels = {'app': 'e39', 'e39-managed': 'true'}
        labels.update(extra_labels or {})
        if webhook:
            labels[WEBHOOK_LABEL[0]] = WEBHOOK_LABEL[1]
        md = {'name': self.pod_name, 'namespace': self.ns, 'labels': labels}
        if annotations:
            md['annotations'] = {k: str(v) for k, v in annotations.items()}
        body = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': md,
                'spec': {'restartPolicy': 'Never', 'terminationGracePeriodSeconds': 0,
                         'automountServiceAccountToken': False, 'enableServiceLinks': False,
                         'containers': [{'name': 'target', 'image': image, 'imagePullPolicy': 'Never'}]}}
        return self.request('POST', f'{self._ns_root()}/pods', body)

    def delete_pod(self, uid, resource_version=None):
        pre = {'uid': uid}
        if resource_version:
            pre['resourceVersion'] = str(resource_version)
        opts = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'gracePeriodSeconds': DELETE_GRACE_PERIOD_S,
                'preconditions': pre}
        return self.request('DELETE', f'{self._ns_root()}/pods/{self.pod_name}', opts)

    def create_netpol(self, body):
        return self.request('POST', self._netpol_root(), body)

    def get_netpol(self, name):
        return self.request('GET', f'{self._netpol_root()}/{name}')

    def delete_netpol(self, name):
        return self.request('DELETE', f'{self._netpol_root()}/{name}',
                            {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'gracePeriodSeconds': 0})


def _is_isolation_netpol(body, namespace):
    """Exactly the E39-E isolation policy shape: podSelector on the per-trial label only,
    policyTypes Ingress+Egress, no ingress/egress rules."""
    try:
        md = body.get('metadata') or {}
        spec = body.get('spec') or {}
        name = md.get('name') or ''
        if body.get('kind') != 'NetworkPolicy' or body.get('apiVersion') != 'networking.k8s.io/v1':
            return False
        if not name.startswith(NETPOL_PREFIX) or md.get('namespace', namespace) != namespace:
            return False
        sel = spec.get('podSelector') or {}
        if set(sel) != {'matchLabels'} or set(sel['matchLabels']) != {TRIAL_LABEL_KEY}:
            return False
        if sorted(spec.get('policyTypes') or []) != ['Egress', 'Ingress']:
            return False
        if spec.get('ingress') or spec.get('egress'):
            return False
        if not set(spec) <= {'podSelector', 'policyTypes', 'ingress', 'egress'}:
            return False
        return True
    except (AttributeError, TypeError):
        return False
