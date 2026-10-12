#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def load_events(p):
    out = {}
    bad = 0
    for line in Path(p).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except Exception:
            bad += 1
            continue
        if e.get("stage") == "ResponseComplete" and e.get("auditID"):
            out.setdefault(e["auditID"], []).append(e)
    return out, bad


def load_pod_events(p):
    deleted = {}
    errs = []
    path = Path(p)
    if not path.is_file():
        return deleted, ["pod-event log missing"]
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except Exception:
            errs.append(f"malformed pod-event JSON line {lineno}")
            continue
        typ = str(e.get("type", "")).upper()
        if typ in ("GAP", "ERROR"):
            errs.append(f"pod-event {typ} at line {lineno}")
        if typ == "DELETED":
            uid = e.get("uid")
            if not uid:
                errs.append(f"DELETED event missing uid at line {lineno}")
            else:
                deleted.setdefault(str(uid), []).append(e.get("rv"))
    return deleted, errs


def numeric_rv(v):
    if v is None:
        return None
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return None


def current_delete_check(op, event, deleted):
    audit_rv = ((event.get("responseObject") or {}).get("metadata") or {}).get("resourceVersion")
    raw_rv = op.get("rv")
    if audit_rv is not None:
        if numeric_rv(audit_rv) is None:
            return False, f"audit delete RV non-numeric: {audit_rv}", "audit"
        if raw_rv is not None and str(audit_rv) != str(raw_rv):
            return False, f"delete RV conflict audit={audit_rv} runner={raw_rv}", "audit"
        return True, f"delete_response_rv={audit_rv}", "audit"
    uid = op.get("expected_uid")
    if not uid:
        return False, "audit delete RV missing and probe expected_uid unavailable", None
    ds = deleted.get(str(uid), [])
    if len(ds) == 0:
        return False, "audit delete RV missing; awaiting UID-matched DELETED watch event", None
    if len(ds) != 1:
        return False, f"UID {uid} DELETED watch matches={len(ds)}", None
    wrv = ds[0]
    if numeric_rv(wrv) is None:
        return False, f"UID-matched DELETED watch RV non-numeric: {wrv}", None
    if raw_rv is not None and str(wrv) != str(raw_rv):
        return False, f"delete RV conflict runner={raw_rv} watch={wrv}", None
    return True, f"delete_response_rv=missing; fallback_watch_rv={wrv}", "pod_watch_deleted"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", required=True)
    ap.add_argument("--pod-events", required=True)
    ap.add_argument("--probe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--wait-seconds", type=float, default=15)
    a = ap.parse_args()
    probe = json.loads(Path(a.probe).read_text())
    deadline = time.time() + a.wait_seconds
    by = {}
    audit_bad = 0
    deleted = {}
    pod_errs = []
    watched_ops = [o for o in probe.get("ops", []) if o.get("audit_id") and o.get("op") in ("writer_patch", "delete_stale_rv", "writer_restore", "delete_current_rv")]

    while time.time() < deadline:
        by, audit_bad = load_events(a.audit)
        deleted, pod_errs = load_pod_events(a.pod_events)
        all_audit = all(o["audit_id"] in by for o in watched_ops)
        fallback_ready = True
        for o in watched_ops:
            if o.get("op") != "delete_current_rv" or o.get("status") not in (200, 202):
                continue
            es = by.get(o["audit_id"], [])
            if len(es) != 1:
                fallback_ready = False
                continue
            arv = ((es[0].get("responseObject") or {}).get("metadata") or {}).get("resourceVersion")
            if arv is None:
                uid = o.get("expected_uid")
                fallback_ready = fallback_ready and bool(uid) and len(deleted.get(str(uid), [])) >= 1
        if all_audit and fallback_ready:
            break
        time.sleep(1)

    checks = {"api_preflight_pass": bool(probe.get("ALL_API_PASS")), "audit_stream_json_valid": audit_bad == 0, "pod_watch_stream_valid": not pod_errs}
    detail = []
    for o in watched_ops:
        es = by.get(o["audit_id"], [])
        ok = len(es) == 1
        why = ""
        source = None
        if ok:
            e = es[0]
            status = o.get("status")
            if o["op"].startswith("writer_") and status == 200:
                arv = ((e.get("responseObject") or {}).get("metadata") or {}).get("resourceVersion")
                ok = arv is not None and numeric_rv(arv) is not None and str(arv) == str(o.get("rv"))
                why = f'audit_rv={arv} runner_rv={o.get("rv")}'
                source = "audit"
            elif o["op"] == "delete_stale_rv":
                rs = e.get("responseStatus") or {}
                ok = int(rs.get("code") or 0) == 409 and "ResourceVersion in the precondition" in (rs.get("message") or "")
                why = rs.get("message") or ""
                source = "audit"
            elif o["op"] == "delete_current_rv" and status in (200, 202):
                ok, why, source = current_delete_check(o, e, deleted)
        detail.append({"op": o["op"], "audit_id": o["audit_id"], "matches": len(es), "rv_source": source, "PASS": ok, "detail": why})
        checks[o["op"]] = ok

    if audit_bad:
        detail.append({"op": "audit_stream", "PASS": False, "detail": f"malformed JSON lines={audit_bad}"})
    for x in pod_errs:
        detail.append({"op": "pod_watch", "PASS": False, "detail": x})

    out = {"schema": "a4d.preflight.audit.v2", "run_id": probe["run_id"], "checks": checks, "details": detail, "PASS": all(checks.values())}
    Path(a.out).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    return 0 if out["PASS"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
