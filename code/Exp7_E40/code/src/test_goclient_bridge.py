"""pytest for goclient_bridge using a stub subprocess (no real binary/cluster).

The stub is a tiny Python program that echoes canned JSON results per command,
so the bridge's process management, line protocol, and e39.trial.v1 mapping are
exercised without contacting any cluster or building the Go binary.
"""
import os
import sys

import goclient_bridge as gb

STUB = r"""
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    cmd = json.loads(line)
    op = cmd.get("op")
    if op == "ping":
        print(json.dumps({"op": "ping", "ok": True}))
    elif op == "quit":
        print(json.dumps({"op": "quit", "ok": True}))
        break
    elif op == "gate_delete_uid":
        print(json.dumps({
            "op": op, "ok": True, "valid": True, "gate_decision": "delete",
            "pod_uid": "uid-123", "pod_resource_version": "200",
            "deleted": True, "delete_status": 200,
            "delete_audit_id": "aud-pod-del", "deleted_resource_version": "201",
            "reads": {
                "evidence-a": {"resourceVersion": "100", "value": "v-a",
                               "allowed": True, "t_send": 10, "t_recv": 20,
                               "audit_id": "aud-cm-evidence-a"},
                cmd.get("pod", "p"): {"resourceVersion": "200", "t_send": 30,
                                      "t_recv": 40, "audit_id": "aud-pod-get"},
            },
        }))
    elif op == "gate_delete_rv":
        print(json.dumps({
            "op": op, "ok": True, "valid": False, "gate_decision": "hold",
            "pod_uid": "uid-123", "pod_resource_version": "200",
            "deleted": False, "delete_status": 0, "reads": {},
        }))
    elif op == "gate_uid":
        print(json.dumps({
            "op": op, "ok": True, "valid": True, "gate_decision": "delete",
            "gate_reason": "allow: J via a&b; scope_allowed; app_policy_allowed",
            "t_gate_end_mono_ns": 12345,
            "pod_uid": "uid-123", "pod_resource_version": "200",
            "reads": {cmd.get("pod", "p"): {"resourceVersion": "200", "t_send": 30,
                                            "t_recv": 40, "audit_id": "aud-pod-get"}},
        }))
    elif op == "delete_uid":
        print(json.dumps({
            "op": op, "ok": True, "deleted": True, "delete_status": 200,
            "delete_audit_id": "aud-pod-del", "deleted_resource_version": "201",
            "t_send_mono_ns": 5000, "t_resp_mono_ns": 5200,
        }))
    elif op == "delete_rv":
        print(json.dumps({
            "op": op, "ok": True, "deleted": True, "delete_status": 200,
            "delete_audit_id": "aud-pod-del", "deleted_resource_version": "201",
            "t_send_mono_ns": 6000, "t_resp_mono_ns": 6200,
        }))
    else:
        print(json.dumps({"op": op, "ok": False, "error": "unknown op"}))
    sys.stdout.flush()
"""


def _stub_argv():
    return [sys.executable, "-c", STUB]


def test_ping():
    with gb.GoClient(_stub_argv()) as c:
        r = c.ping()
        assert r["ok"] is True
        assert r["op"] == "ping"


def test_gate_delete_uid_mapping():
    with gb.GoClient(_stub_argv()) as c:
        raw = c.gate_delete_uid("e40-test", "victim", "uid-123", {"b": True})
        trial = gb.map_to_trial(raw)

    assert trial["schema"] == "e39.trial.v1"
    assert trial["client_impl"] == "client-go"
    assert trial["op"] == "gate_delete_uid"
    assert trial["gate_valid"] is True
    assert trial["gate_decision"] == "delete"
    assert trial["deleted"] is True
    assert trial["delete_status"] == 200
    assert trial["delete_audit_id"] == "aud-pod-del"
    assert trial["deleted_resource_version"] == "201"
    ea = trial["reads"]["evidence-a"]
    assert ea["resource_version"] == "100"
    assert ea["value"] == "v-a"
    assert ea["allowed"] is True
    assert ea["audit_id"] == "aud-cm-evidence-a"
    assert ea["t_send_ns"] == 10 and ea["t_recv_ns"] == 20
    assert trial["reads"]["victim"]["audit_id"] == "aud-pod-get"


def test_gate_delete_rv_hold_mapping():
    with gb.GoClient(_stub_argv()) as c:
        raw = c.gate_delete_rv("e40-test", "victim", "uid-123")
        trial = gb.map_to_trial(raw)
    assert trial["gate_valid"] is False
    assert trial["gate_decision"] == "hold"
    assert trial["deleted"] is False
    assert trial["client_impl"] == "client-go"


def test_split_ops_mapping():
    with gb.GoClient(_stub_argv()) as c:
        gate = gb.map_to_trial(c.gate_uid("e40-test", "victim", {"b": True}))
        assert gate["op"] == "gate_uid"
        assert gate["gate_valid"] is True
        assert gate["t_gate_end_mono_ns"] == 12345
        assert gate["gate_reason"].startswith("allow: J via")
        assert gate["deleted"] is False

        du = gb.map_to_trial(c.delete_uid("e40-test", "victim", "uid-123"))
        assert du["deleted"] is True and du["delete_status"] == 200
        assert du["t_send_mono_ns"] == 5000 and du["t_resp_mono_ns"] == 5200

        dv = gb.map_to_trial(c.delete_rv("e40-test", "victim", "uid-123", "200"))
        assert dv["deleted"] is True and dv["client_impl"] == "client-go"
        assert dv["t_send_mono_ns"] == 6000 and dv["t_resp_mono_ns"] == 6200


def test_context_manager_closes():
    c = gb.GoClient(_stub_argv())
    c.start()
    assert c.ping()["ok"] is True
    c.close()
    assert c._proc is None
