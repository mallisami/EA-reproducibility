"""Bridge to the Go client-go incident-response client (e39-goclient).

Starts the long-lived Go process, sends one JSON command per line on stdin,
reads one JSON result per line on stdout, and maps the result into the
e39.trial.v1 schema fields with client_impl="client-go".

Intended to be interface-compatible with the Python client wrapper so the
paper authors can compare the two implementations on their disposable test
cluster.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, List, Optional

CLIENT_IMPL = "client-go"


class GoClientError(RuntimeError):
    pass


class GoClient:
    """Manage a long-lived e39-goclient subprocess."""

    def __init__(self, argv: List[str]):
        # argv is the full command, e.g.
        # [".../bin/e39-goclient", "--server", "https://127.0.0.1:6443",
        #  "--ca-file", ca, "--token-file", tok, "--namespace-prefix", "e39-"]
        self._argv = list(argv)
        self._proc: Optional[subprocess.Popen] = None

    def start(self) -> "GoClient":
        self._proc = subprocess.Popen(
            self._argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
        )
        return self

    def __enter__(self) -> "GoClient":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    def _require(self) -> subprocess.Popen:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise GoClientError("process not started")
        return self._proc

    def send(self, command: Dict[str, Any]) -> Dict[str, Any]:
        proc = self._require()
        proc.stdin.write(json.dumps(command) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if line == "":
            err = ""
            if proc.stderr is not None:
                err = proc.stderr.read()
            raise GoClientError(f"no response from client (stderr: {err.strip()})")
        return json.loads(line)

    # --- convenience wrappers ---

    def ping(self) -> Dict[str, Any]:
        return self.send({"op": "ping"})

    def gate_delete_uid(
        self,
        namespace: str,
        pod: str,
        expected_uid: str,
        cached: Dict[str, bool],
        configmaps: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if configmaps is None:
            configmaps = [
                "evidence-a", "evidence-c", "evidence-e",
                "response-scope", "application-policy",
            ]
        return self.send({
            "op": "gate_delete_uid",
            "namespace": namespace,
            "pod": pod,
            "configmaps": configmaps,
            "expected_uid": expected_uid,
            "cached": cached,
        })

    def gate_delete_rv(self, namespace: str, pod: str, expected_uid: str) -> Dict[str, Any]:
        return self.send({
            "op": "gate_delete_rv",
            "namespace": namespace,
            "pod": pod,
            "expected_uid": expected_uid,
        })

    # --- split ops: gate first (arm the writer at the true gate end), then delete ---

    def gate_uid(
        self,
        namespace: str,
        pod: str,
        cached: Dict[str, bool],
        expected_uid: str = "",
        configmaps: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if configmaps is None:
            configmaps = [
                "evidence-a", "evidence-c", "evidence-e",
                "response-scope", "application-policy",
            ]
        return self.send({
            "op": "gate_uid",
            "namespace": namespace,
            "pod": pod,
            "configmaps": configmaps,
            "expected_uid": expected_uid,
            "cached": cached,
        })

    def delete_uid(self, namespace: str, pod: str, expected_uid: str) -> Dict[str, Any]:
        return self.send({
            "op": "delete_uid",
            "namespace": namespace,
            "pod": pod,
            "expected_uid": expected_uid,
        })

    def delete_rv(self, namespace: str, pod: str, expected_uid: str, resource_version: str) -> Dict[str, Any]:
        return self.send({
            "op": "delete_rv",
            "namespace": namespace,
            "pod": pod,
            "expected_uid": expected_uid,
            "resource_version": resource_version,
        })

    def quit(self) -> None:
        if self._proc is None:
            return
        try:
            self.send({"op": "quit"})
        except Exception:
            pass

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            self.quit()
        finally:
            try:
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()
            self._proc = None


def map_to_trial(result: Dict[str, Any]) -> Dict[str, Any]:
    """Map a raw goclient result into e39.trial.v1 trial fields."""
    reads = result.get("reads", {}) or {}
    trial: Dict[str, Any] = {
        "schema": "e39.trial.v1",
        "client_impl": CLIENT_IMPL,
        "op": result.get("op"),
        "ok": bool(result.get("ok", False)),
        "error": result.get("error", ""),
        "gate_valid": bool(result.get("valid", False)),
        "gate_decision": result.get("gate_decision", ""),
        "gate_reason": result.get("gate_reason", ""),
        # CLOCK_MONOTONIC ns, comparable to the runner's time.monotonic_ns().
        "t_gate_end_mono_ns": result.get("t_gate_end_mono_ns"),
        "pod_uid": result.get("pod_uid", ""),
        "pod_resource_version": result.get("pod_resource_version", ""),
        "deleted": bool(result.get("deleted", False)),
        "delete_status": result.get("delete_status", 0),
        "delete_reason": result.get("delete_reason", ""),
        "delete_message": result.get("delete_message", ""),
        "delete_audit_id": result.get("delete_audit_id", ""),
        "deleted_resource_version": result.get("deleted_resource_version", ""),
        "t_send_mono_ns": result.get("t_send_mono_ns"),
        "t_resp_mono_ns": result.get("t_resp_mono_ns"),
        "reads": {
            name: {
                "resource_version": info.get("resourceVersion", ""),
                "value": info.get("value", ""),
                "allowed": info.get("allowed"),
                "t_send_ns": info.get("t_send"),
                "t_recv_ns": info.get("t_recv"),
                "audit_id": info.get("audit_id", ""),
            }
            for name, info in reads.items()
        },
    }
    return trial
