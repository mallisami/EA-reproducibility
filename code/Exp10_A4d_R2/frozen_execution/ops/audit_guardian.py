#!/usr/bin/env python3
"""Online instrumentation guardian.

Stops only for evidence/infrastructure failure, never scientific outcome. Successful DELETEs
always require their Audit-ID ResponseComplete record. If that record omits resourceVersion,
exactly one UID-matched DELETED Pod-watch event with a numeric resourceVersion is required as
the sanctioned fallback.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def numeric_rv(rv):
    if rv is None:
        return None
    try:
        return int(str(rv))
    except (TypeError, ValueError):
        return None


def process_alive(pid_file):
    try:
        pid = int(Path(pid_file).read_text().strip())
        return Path(f"/proc/{pid}").exists()
    except Exception:
        return False


def critical_ok(row, event_list, deleted_by_uid):
    if not event_list:
        return False, "missing ResponseComplete", None, False
    if len(event_list) != 1:
        return False, f"ResponseComplete matches={len(event_list)}", None, True
    event = event_list[0]
    if event.get("stage") != "ResponseComplete":
        return False, "missing ResponseComplete", None, False

    role = row["role"]
    raw = row["raw"]
    if role == "writer":
        wr = raw.get("writer") or {}
        status = wr.get("http_status")
        want = wr.get("resulting_resource_version")
        if status in (200, 201, 202):
            got = ((event.get("responseObject") or {}).get("metadata") or {}).get("resourceVersion")
            if got is None:
                return False, "writer ResponseComplete lacks responseObject.resourceVersion", None, False
            if numeric_rv(got) is None:
                return False, f"writer responseObject.resourceVersion non-numeric: {got}", None, True
            if want is not None and str(got) != str(want):
                return False, f"writer RV mismatch audit={got} raw={want}", None, True
        return True, "ok", None, False

    if role == "delete":
        de = raw.get("delete") or {}
        status = de.get("http_status")
        if status in (200, 201, 202):
            got = ((event.get("responseObject") or {}).get("metadata") or {}).get("resourceVersion")
            raw_rv = de.get("deleted_object_resource_version")
            if got is not None:
                if numeric_rv(got) is None:
                    return False, f"successful DELETE audit RV non-numeric: {got}", None, True
                if raw_rv is not None and str(got) != str(raw_rv):
                    return False, f"DELETE RV mismatch audit={got} raw={raw_rv}", None, True
                return True, "ok:audit_rv", None, False

            uid = de.get("expected_uid")
            if not uid:
                return False, "successful DELETE lacks expected_uid required for watch fallback", None, True
            ds = deleted_by_uid.get(str(uid), [])
            if not ds:
                return False, "successful DELETE audit RV absent; awaiting UID-matched DELETED watch event", None, False
            if len(ds) != 1:
                return False, f"successful DELETE UID {uid} DELETED watch matches={len(ds)}", None, True
            watch_rv = ds[0]
            if numeric_rv(watch_rv) is None:
                return False, f"successful DELETE UID {uid} watch RV non-numeric: {watch_rv}", None, True
            if raw_rv is not None and str(watch_rv) != str(raw_rv):
                return False, f"DELETE RV conflict raw={raw_rv} watch={watch_rv}", None, True
            return True, "ok:pod_watch_deleted", str(uid), False
        if status == 409:
            rs = event.get("responseStatus") or {}
            if int(rs.get("code") or 0) != 409:
                return False, "DELETE 409 audit code missing", None, True
            if not (rs.get("message") or ""):
                return False, "DELETE 409 audit message missing", None, True
        return True, "ok", None, False

    return True, "ok", None, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--raw", required=True)
    ap.add_argument("--audit-stream", required=True)
    ap.add_argument("--stream-pid-file", required=True)
    ap.add_argument("--pod-events", required=True)
    ap.add_argument("--podwatch-pid-file", required=True)
    ap.add_argument("--stop-file", required=True)
    ap.add_argument("--status", required=True)
    ap.add_argument("--grace-seconds", type=float, default=90)
    ap.add_argument("--min-free-gib", type=float, default=5.0)
    a = ap.parse_args()

    rawp = Path(a.raw)
    audp = Path(a.audit_stream)
    podp = Path(a.pod_events)
    stopp = Path(a.stop_file)
    statusp = Path(a.status)
    rawpos = audpos = podpos = 0
    events = {}
    deleted_by_uid = {}
    pending = {}
    fallback_used = {}
    nraw = 0
    last_report = 0

    def fail(reason, details=None):
        stopp.touch()
        obj = {
            "schema": "a4d.guardian.v2",
            "run_id": a.run_id,
            "PASS": False,
            "reason": reason,
            "details": details,
            "n_raw_seen": nraw,
            "pending": len(pending),
            "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        statusp.write_text(json.dumps(obj, indent=2) + "\n")
        print(json.dumps(obj), flush=True)
        return 4

    while True:
        if stopp.exists():
            obj = {
                "schema": "a4d.guardian.v2",
                "run_id": a.run_id,
                "PASS": True,
                "reason": "stop requested by runner/operator after guardian checks",
                "n_raw_seen": nraw,
                "pending": len(pending),
                "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            statusp.write_text(json.dumps(obj, indent=2) + "\n")
            return 0

        if not process_alive(a.stream_pid_file):
            return fail("audit stream process is not alive")
        if not process_alive(a.podwatch_pid_file):
            return fail("pod-watch process is not alive")

        st = os.statvfs(str(audp.parent))
        free = st.f_bavail * st.f_frsize
        if free < a.min_free_gib * (1024**3):
            return fail("free disk below guardian threshold", {"free_bytes": free})

        if audp.exists():
            with audp.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(audpos)
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        e = json.loads(line)
                    except Exception as exc:
                        return fail("malformed audit-stream JSON", {"error": repr(exc)})
                    aid = e.get("auditID")
                    if aid and e.get("stage") == "ResponseComplete":
                        events.setdefault(aid, []).append(e)
                audpos = f.tell()

        if podp.exists():
            with podp.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(podpos)
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        e = json.loads(line)
                    except Exception as exc:
                        return fail("malformed pod-event JSON", {"error": repr(exc)})
                    typ = str(e.get("type", "")).upper()
                    if typ in ("GAP", "ERROR"):
                        return fail(f"pod-watch emitted {typ}", {"record": e})
                    if typ == "DELETED":
                        uid = e.get("uid")
                        rv = e.get("rv")
                        if not uid:
                            return fail("DELETED pod-event missing uid", {"record": e})
                        deleted_by_uid.setdefault(str(uid), []).append(rv)
                podpos = f.tell()

        # If a UID has already been consumed as fallback evidence, a later duplicate or malformed
        # DELETED event invalidates that fallback and stops the run immediately.
        for uid, info in list(fallback_used.items()):
            ds = deleted_by_uid.get(uid, [])
            if len(ds) != 1:
                return fail("fallback DELETE watch evidence became non-unique", {"uid": uid, "matches": len(ds), **info})
            if numeric_rv(ds[0]) is None:
                return fail("fallback DELETE watch resourceVersion became malformed", {"uid": uid, "rv": ds[0], **info})

        if rawp.exists():
            with rawp.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(rawpos)
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line)
                    except Exception as exc:
                        return fail("malformed RAW_TRIALS JSON", {"error": repr(exc)})
                    nraw += 1
                    if r.get("status") != "COMPLETE":
                        continue
                    de = r.get("delete") or {}
                    wr = r.get("writer") or {}
                    if wr.get("audit_id"):
                        pending[(nraw, "writer", wr["audit_id"])] = {"first": time.monotonic(), "raw": r, "role": "writer"}
                    if de.get("dispatched") and de.get("audit_id"):
                        pending[(nraw, "delete", de["audit_id"])] = {"first": time.monotonic(), "raw": r, "role": "delete"}
                rawpos = f.tell()

        now = time.monotonic()
        done = []
        for key, x in list(pending.items()):
            ok, why, fallback_uid, fatal = critical_ok(x, events.get(key[2], []), deleted_by_uid)
            if ok:
                if fallback_uid is not None:
                    fallback_used[fallback_uid] = {"trial_ordinal": key[0], "audit_id": key[2]}
                done.append(key)
            elif fatal:
                return fail("required audit/watch evidence is contradictory or malformed", {"trial_ordinal": key[0], "role": key[1], "audit_id": key[2], "problem": why})
            elif now - x["first"] > a.grace_seconds:
                return fail("required audit/watch evidence missing beyond grace interval", {"trial_ordinal": key[0], "role": key[1], "audit_id": key[2], "problem": why})
        for k in done:
            pending.pop(k, None)

        if now - last_report > 30:
            statusp.write_text(
                json.dumps(
                    {
                        "schema": "a4d.guardian.v2",
                        "run_id": a.run_id,
                        "PASS": True,
                        "state": "running",
                        "n_raw_seen": nraw,
                        "pending_within_grace": len(pending),
                        "audit_ids_seen": len(events),
                        "delete_fallbacks_used": len(fallback_used),
                        "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    },
                    indent=2,
                )
                + "\n"
            )
            last_report = now
        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
