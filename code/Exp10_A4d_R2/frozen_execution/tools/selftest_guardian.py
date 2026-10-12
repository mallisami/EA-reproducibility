#!/usr/bin/env python3
"""Synthetic process/file tests for the online A4d audit/watch guardian."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "ops/audit_guardian.py"


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")


def run_case(label, pod_rows, expect_pass, grace=0.4):
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        raw = td / "raw.jsonl"
        audit = td / "audit.jsonl"
        pod = td / "pod.jsonl"
        stop = td / "STOP"
        status = td / "status.json"
        apid = td / "audit.pid"
        ppid = td / "pod.pid"
        raw_row = {
            "trial_id": 1,
            "status": "COMPLETE",
            "writer": {"audit_id": "w1", "http_status": 200, "resulting_resource_version": "11"},
            "delete": {"dispatched": True, "audit_id": "d1", "http_status": 200, "expected_uid": "uid-1", "deleted_object_resource_version": None},
        }
        write_jsonl(raw, [raw_row])
        write_jsonl(
            audit,
            [
                {"auditID": "w1", "stage": "ResponseComplete", "responseObject": {"metadata": {"resourceVersion": "11"}}},
                {"auditID": "d1", "stage": "ResponseComplete", "responseStatus": {"code": 200}, "responseObject": {"kind": "Pod", "metadata": {"uid": "uid-1"}}},
            ],
        )
        write_jsonl(pod, pod_rows)
        keep1 = subprocess.Popen(["sleep", "30"])
        keep2 = subprocess.Popen(["sleep", "30"])
        apid.write_text(str(keep1.pid))
        ppid.write_text(str(keep2.pid))
        try:
            g = subprocess.Popen(
                [
                    "python3",
                    str(GUARD),
                    "--run-id",
                    label,
                    "--raw",
                    str(raw),
                    "--audit-stream",
                    str(audit),
                    "--stream-pid-file",
                    str(apid),
                    "--pod-events",
                    str(pod),
                    "--podwatch-pid-file",
                    str(ppid),
                    "--stop-file",
                    str(stop),
                    "--status",
                    str(status),
                    "--grace-seconds",
                    str(grace),
                    "--min-free-gib",
                    "0",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if expect_pass:
                time.sleep(0.35)
                stop.touch()
            try:
                out, err = g.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                g.kill()
                out, err = g.communicate()
                raise SystemExit(f"{label}: guardian timed out\n{out}\n{err}")
            passed = g.returncode == 0
            if passed != expect_pass:
                st = status.read_text() if status.exists() else "<no status>"
                raise SystemExit(f"{label}: expected PASS={expect_pass}, rc={g.returncode}\nstdout={out}\nstderr={err}\nstatus={st}")
            if not expect_pass and not stop.exists():
                raise SystemExit(f"{label}: guardian failed but did not create STOP")
        finally:
            keep1.terminate()
            keep2.terminate()
            keep1.wait(timeout=2)
            keep2.wait(timeout=2)


def main():
    run_case("fallback_ok", [{"type": "START"}, {"type": "DELETED", "uid": "uid-1", "rv": "21"}], True)
    run_case("fallback_missing", [{"type": "START"}], False, grace=0.15)
    run_case("fallback_duplicate", [{"type": "DELETED", "uid": "uid-1", "rv": "21"}, {"type": "DELETED", "uid": "uid-1", "rv": "21"}], False)
    run_case("fallback_gap", [{"type": "GAP", "rv": "20"}, {"type": "DELETED", "uid": "uid-1", "rv": "21"}], False)
    print("guardian self-test PASS (fallback happy-path + fail-closed cases)")


if __name__ == "__main__":
    main()
