#!/usr/bin/env python3
"""
E21 preflight: verifies the namespace/ConfigMaps exist, the kubectl proxy is
reachable, and captures the environment (versions, node info) BEFORE any
trial runs, per E21_PROTOCOL.json's requirement not to upgrade infrastructure
mid-benchmark.
"""
import json, subprocess, sys, platform
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
NS = "flashir-e21"
PROXY = "http://127.0.0.1:8001"


def run(args):
    p = subprocess.run(args, text=True, capture_output=True)
    return {"cmd": " ".join(args), "returncode": p.returncode, "stdout": p.stdout.strip(), "stderr": p.stderr.strip()}


def main():
    rec = {"platform": platform.platform(), "python": platform.python_version()}
    for label, args in [
        ("kubectl_version", ["kubectl", "version", "-o", "yaml"]),
        ("kind_version", ["kind", "version"]),
        ("docker_version", ["docker", "version"]),
        ("nodes", ["kubectl", "get", "nodes", "-o", "wide"]),
        ("cluster_info", ["kubectl", "cluster-info"]),
        ("configmaps", ["kubectl", "-n", NS, "get", "configmaps"]),
    ]:
        rec[label] = run(args)

    proxy_ok = False
    proxy_err = None
    try:
        with urlopen(f"{PROXY}/api/v1/namespaces/{NS}/configmaps/obs-a", timeout=5) as resp:
            proxy_ok = resp.status == 200
    except Exception as e:
        proxy_err = str(e)
    rec["proxy_reachable"] = proxy_ok
    rec["proxy_error"] = proxy_err

    all_cm_present = all(f"obs-{c}" in rec["configmaps"]["stdout"] for c in "abcdef")
    rec["all_configmaps_present"] = all_cm_present

    status = "PASS" if (rec["nodes"]["returncode"] == 0 and proxy_ok and all_cm_present) else "FAIL"
    rec["status"] = status

    out = ROOT / "logs" / "e21_preflight.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, indent=2))
    print(json.dumps({k: v for k, v in rec.items() if k not in ("kubectl_version", "docker_version")}, indent=2))
    print("\nSTATUS:", status)
    if status != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
