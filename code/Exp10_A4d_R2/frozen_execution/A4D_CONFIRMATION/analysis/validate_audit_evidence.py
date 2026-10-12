#!/usr/bin/env python3
"""Final all-trial evidence-completeness gate.

The gate is independent of scientific outcome direction. For a successful Pod DELETE,
ResponseComplete is always required. If that response object omits resourceVersion, the
only accepted fallback is exactly one UID-matched DELETED Pod-watch event with a numeric
resourceVersion. Missing, duplicate, malformed, GAP/ERROR, or conflicting fallback evidence
fails the run before scientific analysis.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def events_by_id(path):
    by = {}
    bad = 0
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except Exception:
            bad += 1
            continue
        if e.get("auditID") and e.get("stage") == "ResponseComplete":
            by.setdefault(e["auditID"], []).append(e)
    return by, bad


def load_pod_events(path):
    """Return (deleted_by_uid, total_records, errors).

    Only DELETED events are eligible as DELETE-RV fallback evidence. Any malformed JSON or
    explicit GAP/ERROR marker invalidates that observation stream for this confirmation run.
    """
    deleted = {}
    errors = []
    n = 0
    p = Path(path)
    if not p.is_file():
        return deleted, n, ["pod-event log missing"]
    for lineno, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        n += 1
        try:
            d = json.loads(line)
        except Exception:
            errors.append(f"malformed pod-event JSON line {lineno}")
            continue
        typ = str(d.get("type", "")).upper()
        if typ in ("GAP", "ERROR"):
            errors.append(f"pod-event {typ} record at line {lineno}")
        if typ == "DELETED":
            uid = d.get("uid")
            rv = d.get("rv")
            if not uid:
                errors.append(f"DELETED pod-event missing uid at line {lineno}")
                continue
            deleted.setdefault(str(uid), []).append((rv, lineno))
    return deleted, n, errors


def numeric_rv(rv):
    if rv is None:
        return None
    try:
        return int(str(rv))
    except (TypeError, ValueError):
        return None


def resolve_successful_delete_rv(trial_id, de, audit_event, deleted_by_uid):
    """Validate successful DELETE commit evidence.

    ResponseComplete is mandatory. Prefer its responseObject.metadata.resourceVersion. When
    absent, accept exactly one UID-matched DELETED watch event. If both audit/client/watch RVs
    are available, they must agree wherever their semantics are compared by the locked oracle.
    """
    errs = []
    audit_rv = ((audit_event.get("responseObject") or {}).get("metadata") or {}).get("resourceVersion")
    raw_rv = de.get("deleted_object_resource_version")
    uid = de.get("expected_uid")

    if audit_rv is not None and numeric_rv(audit_rv) is None:
        errs.append(f"trial {trial_id} successful DELETE audit resourceVersion non-numeric: {audit_rv}")
        return None, None, errs
    if raw_rv is not None and numeric_rv(raw_rv) is None:
        errs.append(f"trial {trial_id} successful DELETE raw resourceVersion non-numeric: {raw_rv}")
        return None, None, errs
    if audit_rv is not None and raw_rv is not None and str(audit_rv) != str(raw_rv):
        errs.append(f"trial {trial_id} DELETE RV mismatch audit={audit_rv} raw={raw_rv}")
        return None, None, errs

    # Audit response RV is sufficient primary evidence. A single available watch event is
    # corroborative and must not contradict it.
    if audit_rv is not None:
        if uid:
            ds = deleted_by_uid.get(str(uid), [])
            if len(ds) == 1:
                watch_rv, line = ds[0]
                if numeric_rv(watch_rv) is None:
                    errs.append(f"trial {trial_id} UID-matched DELETED watch RV non-numeric at line {line}: {watch_rv}")
                elif str(watch_rv) != str(audit_rv):
                    errs.append(f"trial {trial_id} DELETE RV conflict audit={audit_rv} watch={watch_rv}")
        return audit_rv, "audit", errs

    # Narrow sanctioned fallback: exactly one DELETED event for the exact target UID.
    if not uid:
        errs.append(f"trial {trial_id} successful DELETE lacks expected_uid required for watch fallback")
        return None, None, errs
    ds = deleted_by_uid.get(str(uid), [])
    if len(ds) == 0:
        errs.append(f"trial {trial_id} successful DELETE lacks audit RV and UID-matched DELETED watch fallback")
        return None, None, errs
    if len(ds) != 1:
        errs.append(f"trial {trial_id} successful DELETE UID {uid} DELETED watch matches={len(ds)}; expected exactly 1")
        return None, None, errs
    watch_rv, line = ds[0]
    if numeric_rv(watch_rv) is None:
        errs.append(f"trial {trial_id} successful DELETE fallback RV non-numeric at pod-event line {line}: {watch_rv}")
        return None, None, errs
    if raw_rv is not None and str(watch_rv) != str(raw_rv):
        errs.append(f"trial {trial_id} DELETE RV conflict raw={raw_rv} watch={watch_rv}")
        return None, None, errs
    return watch_rv, "pod_watch_deleted", errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--audit", required=True)
    ap.add_argument("--pod-events", required=True)
    ap.add_argument("--expected", type=int, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    rows = [json.loads(x) for x in Path(a.raw).read_text(encoding="utf-8").splitlines() if x.strip()]
    by, audit_bad = events_by_id(a.audit)
    deleted_by_uid, pe_n, pe_errors = load_pod_events(a.pod_events)
    errs = list(pe_errors)
    critical = 0
    matched = 0
    delete_rv_sources = {"audit": 0, "pod_watch_deleted": 0}

    if len(rows) != a.expected:
        errs.append(f"raw record count {len(rows)} != expected {a.expected}")
    if len({r.get("trial_id") for r in rows}) != len(rows):
        errs.append("duplicate trial_id")
    if any(r.get("arm") != "A4d_commit_direct" for r in rows):
        errs.append("non-A4d trial present")

    for r in rows:
        if r.get("status") != "COMPLETE":
            if r.get("status") == "INFRA_ERROR":
                errs.append(f"trial {r.get('trial_id')} infrastructure error")
            continue
        wr = r.get("writer") or {}
        de = r.get("delete") or {}
        req = []
        if wr.get("audit_id"):
            req.append(("writer", wr["audit_id"]))
        if de.get("dispatched") and de.get("audit_id"):
            req.append(("delete", de["audit_id"]))
        for role, aid in req:
            critical += 1
            es = by.get(aid, [])
            if len(es) != 1:
                errs.append(f"trial {r.get('trial_id')} {role} audit_id {aid} ResponseComplete matches={len(es)}")
                continue
            e = es[0]
            if role == "writer" and wr.get("http_status") in (200, 201, 202):
                arv = ((e.get("responseObject") or {}).get("metadata") or {}).get("resourceVersion")
                if arv is None:
                    errs.append(f"trial {r.get('trial_id')} writer responseObject.resourceVersion missing")
                    continue
                if numeric_rv(arv) is None:
                    errs.append(f"trial {r.get('trial_id')} writer responseObject.resourceVersion non-numeric: {arv}")
                    continue
                if wr.get("resulting_resource_version") is not None and str(arv) != str(wr.get("resulting_resource_version")):
                    errs.append(f"trial {r.get('trial_id')} writer RV mismatch audit={arv} raw={wr.get('resulting_resource_version')}")
                    continue
            if role == "delete":
                st = de.get("http_status")
                if st in (200, 201, 202):
                    _, src, de_errs = resolve_successful_delete_rv(r.get("trial_id"), de, e, deleted_by_uid)
                    if de_errs:
                        errs.extend(de_errs)
                        continue
                    delete_rv_sources[src] += 1
                elif st == 409:
                    rs = e.get("responseStatus") or {}
                    if int(rs.get("code") or 0) != 409 or not (rs.get("message") or ""):
                        errs.append(f"trial {r.get('trial_id')} DELETE 409 responseStatus incomplete")
                        continue
            matched += 1

    if audit_bad:
        errs.append(f"{audit_bad} malformed audit JSON line(s)")

    out = {
        "schema": "a4d.audit.evidence.v2",
        "expected_trials": a.expected,
        "raw_trials": len(rows),
        "critical_audit_records_required": critical,
        "critical_audit_records_validated": matched,
        "successful_delete_rv_sources": delete_rv_sources,
        "pod_event_records": pe_n,
        "errors": errs[:200],
        "error_count": len(errs),
        "PASS": len(errs) == 0,
    }
    Path(a.out).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    return 0 if out["PASS"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
