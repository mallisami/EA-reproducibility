#!/usr/bin/env python3
"""Offline synthetic tests for the A4d audit/watch completeness validator."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VAL = ROOT / "A4D_CONFIRMATION/analysis/validate_audit_evidence.py"


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")


def run_case(td, raw_rows, audit_rows, pod_rows, expect_pass, label):
    raw = td / f"{label}.raw.jsonl"
    audit = td / f"{label}.audit.jsonl"
    pod = td / f"{label}.pod.jsonl"
    out = td / f"{label}.out.json"
    write_jsonl(raw, raw_rows)
    write_jsonl(audit, audit_rows)
    write_jsonl(pod, pod_rows)
    p = subprocess.run(
        ["python3", str(VAL), "--raw", str(raw), "--audit", str(audit), "--pod-events", str(pod), "--expected", str(len(raw_rows)), "--out", str(out)],
        capture_output=True,
        text=True,
    )
    passed = p.returncode == 0
    if passed != expect_pass:
        raise SystemExit(f"{label}: expected PASS={expect_pass}, rc={p.returncode}\n{p.stdout}\n{p.stderr}")


def main():
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        base_raw = {
            "trial_id": 1,
            "arm": "A4d_commit_direct",
            "status": "COMPLETE",
            "condition": "async_scope",
            "writer": {"audit_id": "w1", "http_status": 200, "resulting_resource_version": "11"},
            "delete": {"dispatched": True, "audit_id": "d1", "http_status": 409, "expected_uid": "uid-1"},
        }
        writer_ok = {"auditID": "w1", "stage": "ResponseComplete", "responseObject": {"metadata": {"resourceVersion": "11"}}}
        delete_409 = {"auditID": "d1", "stage": "ResponseComplete", "responseStatus": {"code": 409, "message": "Precondition failed"}}
        run_case(td, [base_raw], [writer_ok, delete_409], [{"type": "START"}], True, "positive_409")

        writer_bad = {"auditID": "w1", "stage": "ResponseComplete"}
        run_case(td, [base_raw], [writer_bad, delete_409], [{"type": "START"}], False, "missing_writer_rv")

        success_raw = json.loads(json.dumps(base_raw))
        success_raw["delete"] = {
            "dispatched": True,
            "audit_id": "d1",
            "http_status": 200,
            "expected_uid": "uid-1",
            "deleted_object_resource_version": None,
        }
        delete_no_rv = {"auditID": "d1", "stage": "ResponseComplete", "responseStatus": {"code": 200}, "responseObject": {"kind": "Pod", "metadata": {"uid": "uid-1"}}}
        watch_ok = [{"type": "START"}, {"type": "DELETED", "uid": "uid-1", "rv": "21"}]
        run_case(td, [success_raw], [writer_ok, delete_no_rv], watch_ok, True, "delete_watch_fallback")
        run_case(td, [success_raw], [writer_ok, delete_no_rv], [{"type": "START"}], False, "delete_watch_missing")
        run_case(td, [success_raw], [writer_ok, delete_no_rv], watch_ok + [{"type": "DELETED", "uid": "uid-1", "rv": "21"}], False, "delete_watch_duplicate")
        run_case(td, [success_raw], [writer_ok, delete_no_rv], [{"type": "DELETED", "uid": "uid-1", "rv": "not-an-rv"}], False, "delete_watch_malformed")

        raw_with_rv = json.loads(json.dumps(success_raw))
        raw_with_rv["delete"]["deleted_object_resource_version"] = "22"
        run_case(td, [raw_with_rv], [writer_ok, delete_no_rv], watch_ok, False, "delete_raw_watch_conflict")

        delete_audit_rv = {"auditID": "d1", "stage": "ResponseComplete", "responseStatus": {"code": 200}, "responseObject": {"kind": "Pod", "metadata": {"uid": "uid-1", "resourceVersion": "20"}}}
        raw_audit = json.loads(json.dumps(success_raw))
        raw_audit["delete"]["deleted_object_resource_version"] = "20"
        run_case(td, [raw_audit], [writer_ok, delete_audit_rv], watch_ok, False, "delete_audit_watch_conflict")

        run_case(td, [success_raw], [writer_ok, delete_no_rv], [{"type": "GAP", "rv": "20"}, {"type": "DELETED", "uid": "uid-1", "rv": "21"}], False, "delete_watch_gap")

    print("instrumentation self-test PASS (audit + UID-matched DELETE watch fallback)")


if __name__ == "__main__":
    main()
