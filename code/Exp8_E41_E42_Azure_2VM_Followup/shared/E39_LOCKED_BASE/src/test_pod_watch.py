#!/usr/bin/env python3
"""Offline tests for src/pod_watch.py (E38v2 RERUN pod-event recorder). No network: the HTTPS
connection is replaced by a scripted fake."""
from __future__ import annotations
import json

import pytest

import pod_watch as PW


def pod(uid, rv, phase='Running', ready=True, dts=None, cstate='running'):
    md = {'uid': uid, 'name': 'e39-target', 'resourceVersion': str(rv), 'labels': {'e39-webhook': 'enforce'}}
    if dts:
        md['deletionTimestamp'] = dts
        md['deletionGracePeriodSeconds'] = 0
    return {'metadata': md, 'spec': {'nodeName': 'n1'},
            'status': {'phase': phase, 'conditions': [{'type': 'Ready', 'status': 'True' if ready else 'False'}],
                       'containerStatuses': [{'state': {cstate: {}}}]}}


class FakeResp:
    def __init__(self, status, body=b'', lines=()):
        self.status = status
        self._body = body
        self._lines = [l if isinstance(l, bytes) else l.encode() for l in lines]

    def read(self):
        return self._body

    def readline(self):
        return self._lines.pop(0) if self._lines else b''


class FakeConn:
    def __init__(self, script, log):
        self.script, self.log = script, log

    def request(self, method, path, headers=None):
        assert method == 'GET'
        self.log.append(path)

    def getresponse(self):
        return self.script.pop(0)

    def close(self):
        pass


def make_watcher(tmp_path, script, monkeypatch):
    ca = tmp_path / 'ca'
    ca.write_text('x')
    tok = tmp_path / 'tok'
    tok.write_text('secret\n')
    monkeypatch.setattr(PW.ssl, 'create_default_context', lambda cafile=None: object())
    rec = PW.Recorder(str(tmp_path / 'ev.jsonl'))
    w = PW.Watcher('https://127.0.0.1:6443', str(ca), str(tok), 'e39-x', rec, stop_file=str(tmp_path / 'STOP'))
    paths = []
    monkeypatch.setattr(w, '_conn', lambda timeout: FakeConn(script, paths))
    return w, rec, paths


def test_summarize_fields():
    s = PW.summarize(pod('u1', 10, dts='2026-09-26T10:00:00Z', cstate='terminated'))
    assert s == {'uid': 'u1', 'name': 'e39-target', 'rv': '10', 'deletion_timestamp': '2026-09-26T10:00:00Z',
                 'deletion_grace_period_seconds': 0, 'phase': 'Running', 'ready': True,
                 'container_state': 'terminated', 'node': 'n1', 'webhook_label': True}


def test_list_then_watch_continues_from_last_rv_and_records_events(tmp_path, monkeypatch):
    lst = FakeResp(200, json.dumps({'metadata': {'resourceVersion': '100'}, 'items': [pod('u0', 99)]}).encode())
    ev = [json.dumps({'type': 'ADDED', 'object': pod('u1', 101, phase='Pending', ready=False)}),
          json.dumps({'type': 'MODIFIED', 'object': pod('u1', 102)}),
          json.dumps({'type': 'MODIFIED', 'object': pod('u1', 103, dts='2026-09-26T10:00:00Z')}),
          json.dumps({'type': 'DELETED', 'object': pod('u1', 104, dts='2026-09-26T10:00:00Z')}),
          json.dumps({'type': 'BOOKMARK', 'object': {'metadata': {'resourceVersion': '110'}}})]
    w1 = FakeResp(200, lines=ev)
    gone = FakeResp(200, lines=[json.dumps({'type': 'ERROR', 'object': {'code': 410, 'message': 'too old'}})])
    lst2 = FakeResp(200, json.dumps({'metadata': {'resourceVersion': '120'}, 'items': []}).encode())
    script = [lst, w1, gone, lst2]
    w, rec, paths = make_watcher(tmp_path, script, monkeypatch)
    w.list_pods()
    assert w.rv == '100'
    assert w.watch_once() == 'ok'
    assert 'resourceVersion=100' in paths[1] and 'watch=1' in paths[1] and 'allowWatchBookmarks=true' in paths[1]
    assert w.rv == '110'
    assert w.watch_once() == 'gone'
    assert 'resourceVersion=110' in paths[2]
    w.list_pods()
    rec.close()
    out = [json.loads(l) for l in (tmp_path / 'ev.jsonl').read_text().splitlines()]
    types = [o['type'] for o in out]
    assert types == ['LIST', 'ADDED', 'MODIFIED', 'MODIFIED', 'DELETED', 'BOOKMARK', 'ERROR']
    dele = out[4]
    assert dele['uid'] == 'u1' and dele['rv'] == '104' and dele['schema'] == PW.SCHEMA
    assert out[3]['deletion_timestamp'] and out[2]['deletion_timestamp'] is None
    assert all(o['t_recv_wall_ns'] > 0 for o in out) and [o['seq'] for o in out] == list(range(1, 8))


def test_rails_reject_remote_or_system_namespace(tmp_path, monkeypatch):
    (tmp_path / 'tok').write_text('t')
    monkeypatch.setattr(PW.ssl, 'create_default_context', lambda cafile=None: object())
    rec = PW.Recorder(str(tmp_path / 'e.jsonl'))
    with pytest.raises(SystemExit):
        PW.Watcher('https://10.0.0.1:6443', 'ca', str(tmp_path / 'tok'), 'e39-x', rec)
    with pytest.raises(SystemExit):
        PW.Watcher('https://127.0.0.1:6443', 'ca', str(tmp_path / 'tok'), 'e39-system', rec)
    with pytest.raises(SystemExit):
        PW.Watcher('http://127.0.0.1:6443', 'ca', str(tmp_path / 'tok'), 'e39-x', rec)
