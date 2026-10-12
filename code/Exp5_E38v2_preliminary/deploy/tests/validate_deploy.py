#!/usr/bin/env python3
"""Offline validation of the E38v2 deploy templates. Touches no cluster.

  python validate_deploy.py            # needs PyYAML (venv_deploy) and openssl for the cert step
Checks: YAML parses; required fields; scoping of webhook/audit/RBAC; kind-config
mounts + kubeadm v1beta4 audit args; render.sh + gen_certs.sh run in a temp dir
and produce a caBundle equal to the generated CA; temp certs are deleted.
"""
import base64
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

DEPLOY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAIL = []
TEST_RUN = "valtest01"


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def load_all(path):
    with open(path, encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d is not None]


def by_kind(docs, kind):
    return [d for d in docs if d.get("kind") == kind]


def audit_level(policy, verb, resource, namespace, group=""):
    """Tiny re-implementation of first-match audit rule evaluation for the fields we use."""
    for rule in policy["rules"]:
        if "verbs" in rule and verb not in rule["verbs"]:
            continue
        if "namespaces" in rule and namespace not in rule["namespaces"]:
            continue
        if "resources" in rule:
            if not any(g.get("group", "") == group and resource in g.get("resources", []) for g in rule["resources"]):
                continue
        return rule["level"]
    return "None"


def validate_kind_config():
    docs = load_all(os.path.join(DEPLOY, "kind-config.yaml"))
    check(len(docs) == 1, "kind-config: one document")
    kc = docs[0]
    check(kc.get("kind") == "Cluster" and kc.get("apiVersion") == "kind.x-k8s.io/v1alpha4", "kind-config: Cluster v1alpha4")
    check(kc.get("name") == "e38v2", "kind-config: cluster name e38v2")
    nodes = kc.get("nodes", [])
    check(len(nodes) == 1 and nodes[0].get("role") == "control-plane", "kind-config: single control-plane node")
    n = nodes[0]
    check(n.get("image") == "kindest/node:v1.37.0", "kind-config: node image kindest/node:v1.37.0")
    mounts = {m["containerPath"]: m for m in n.get("extraMounts", [])}
    pol = mounts.get("/etc/kubernetes/e38v2/audit-policy.yaml")
    check(pol is not None and pol.get("readOnly") is True
          and pol["hostPath"] == "/home/azureuser/e38v2_20260926/deploy/audit-policy.yaml", "kind-config: audit policy mount (ro)")
    check(mounts.get("/var/log/kubernetes/e38v2-audit", {}).get("hostPath")
          == "/home/azureuser/e38v2_20260926/results/_audit_host", "kind-config: audit host log dir mount")
    check(mounts.get("/var/log/e38v2-webhook", {}).get("hostPath")
          == "/home/azureuser/e38v2_20260926/results/_webhook_host", "kind-config: webhook host log dir mount")
    patches = [yaml.safe_load(p) for p in n.get("kubeadmConfigPatches", [])]
    cc = [p for p in patches if p.get("kind") == "ClusterConfiguration"]
    check(len(cc) == 1 and cc[0].get("apiVersion") == "kubeadm.k8s.io/v1beta4", "kind-config: ClusterConfiguration v1beta4 patch")
    api = cc[0]["apiServer"]
    args = {a["name"]: a["value"] for a in api["extraArgs"]}
    check(all(isinstance(v, str) for v in args.values()), "kind-config: extraArgs values are strings")
    check(args.get("audit-policy-file") == "/etc/kubernetes/e38v2/audit-policy.yaml", "kind-config: audit-policy-file")
    check(args.get("audit-log-path") == "/var/log/kubernetes/e38v2-audit/audit.log", "kind-config: audit-log-path")
    for k in ("audit-log-maxsize", "audit-log-maxbackup", "audit-log-maxage"):
        check(args.get(k, "").isdigit(), "kind-config: " + k)
    vols = {v["mountPath"]: v for v in api["extraVolumes"]}
    check(vols.get(args["audit-policy-file"], {}).get("hostPath") == args["audit-policy-file"]
          and vols[args["audit-policy-file"]].get("pathType") == "File", "kind-config: policy extraVolume")
    check(vols.get("/var/log/kubernetes/e38v2-audit", {}).get("readOnly") is False, "kind-config: log extraVolume rw")
    # node paths of apiserver volumes must be node paths that are backed by extraMounts
    check(all(v["hostPath"] in mounts for v in api["extraVolumes"]), "kind-config: extraVolumes backed by extraMounts")


def validate_audit_policy():
    docs = load_all(os.path.join(DEPLOY, "audit-policy.yaml"))
    p = docs[0]
    check(p.get("apiVersion") == "audit.k8s.io/v1" and p.get("kind") == "Policy", "audit: Policy v1")
    check(p.get("omitStages") == ["RequestReceived"], "audit: omit only RequestReceived")
    ns = "e38v2-" + TEST_RUN
    check(audit_level(p, "patch", "configmaps", ns) == "RequestResponse", "audit: test ns configmap patch RR")
    check(audit_level(p, "update", "configmaps", ns) == "RequestResponse", "audit: test ns configmap update RR")
    check(audit_level(p, "delete", "pods", ns) == "RequestResponse", "audit: test ns pod delete RR")
    for verb, res in (("get", "configmaps"), ("list", "pods"), ("create", "pods"), ("watch", "configmaps"),
                      ("delete", "configmaps"), ("create", "selfsubjectaccessreviews"), ("patch", "pods")):
        check(audit_level(p, verb, res, ns) == "None", "audit: test ns %s %s None" % (verb, res))
    for other in ("kube-system", "e38v2-system", "default", "kube-node-lease", "local-path-storage"):
        check(audit_level(p, "patch", "configmaps", other) == "None", "audit: %s configmap patch None" % other)
        check(audit_level(p, "delete", "pods", other) == "None", "audit: %s pod delete None" % other)
    check(audit_level(p, "update", "leases", "kube-node-lease", "coordination.k8s.io") == "None", "audit: leases None")
    check(p["rules"][-1] == {"level": "None"}, "audit: catch-all None last")


def validate_templates(rendered_dir=None, run=None):
    base = rendered_dir or DEPLOY
    tag = "rendered" if rendered_dir else "template"
    nsname = "e38v2-" + (run or "RUNID")
    ns_docs = load_all(os.path.join(base, "namespace.yaml"))
    names = [d["metadata"]["name"] for d in by_kind(ns_docs, "Namespace")]
    check(names == ["e38v2-system", nsname], "%s namespace: e38v2-system + %s" % (tag, nsname))
    rb = load_all(os.path.join(base, "rbac.yaml"))
    sas = {d["metadata"]["name"] for d in by_kind(rb, "ServiceAccount")}
    check(sas == {"e38v2-responder", "e38v2-writer", "e38v2-reset", "e38v2-webhook"}, tag + " rbac: four SAs")
    roles = {d["metadata"]["name"]: d for d in by_kind(rb, "Role")}
    check(all(r["metadata"]["namespace"] == nsname for r in roles.values()), tag + " rbac: Roles only in test ns")

    def verbs(role, res):
        out = set()
        for r in roles[role]["rules"]:
            if res in r["resources"]:
                out |= set(r["verbs"])
        return out
    check(verbs("e38v2-responder", "configmaps") == {"get"}, tag + " rbac: responder CM get only")
    check(verbs("e38v2-responder", "pods") == {"get", "delete"}, tag + " rbac: responder pods get/delete")
    check(verbs("e38v2-writer", "configmaps") == {"get", "patch"} and verbs("e38v2-writer", "pods") == set(),
          tag + " rbac: writer CM get/patch only")
    check({"create", "delete", "patch", "update"} <= verbs("e38v2-reset", "pods")
          and {"create", "delete", "patch", "update"} <= verbs("e38v2-reset", "configmaps"), tag + " rbac: reset full fixture")
    check(verbs("e38v2-webhook-reader", "configmaps") == {"get"} and verbs("e38v2-webhook-reader", "pods") == set(),
          tag + " rbac: webhook CM get only")
    wr = [r for r in roles["e38v2-webhook-reader"]["rules"] if "configmaps" in r["resources"]][0]
    check(len(wr["resourceNames"]) == 8 and "e38-epoch" not in wr["resourceNames"], tag + " rbac: webhook 8 resourceNames")
    crs = by_kind(rb, "ClusterRole")
    check(len(crs) == 1 and crs[0]["rules"] == [{"apiGroups": ["authorization.k8s.io"],
          "resources": ["selfsubjectaccessreviews"], "verbs": ["create"]}], tag + " rbac: only cluster perm is SSAR create")
    bindings = by_kind(rb, "RoleBinding") + by_kind(rb, "ClusterRoleBinding")
    check(all(s["namespace"] == "e38v2-system" for b in bindings for s in b["subjects"]), tag + " rbac: subjects in e38v2-system")
    check(all(b["roleRef"]["name"] in roles or b["roleRef"]["kind"] == "ClusterRole" for b in bindings), tag + " rbac: roleRefs resolve")

    wh = load_all(os.path.join(base, "webhook.yaml"))
    dep = by_kind(wh, "Deployment")[0]
    svc = by_kind(wh, "Service")[0]
    pod = dep["spec"]["template"]["spec"]
    c = pod["containers"][0]
    check(c["image"] == "python:3.12-slim" and c["imagePullPolicy"] == "Never", tag + " webhook: python:3.12-slim, no pulls")
    check(dep["spec"]["replicas"] == 1 and pod["serviceAccountName"] == "e38v2-webhook", tag + " webhook: 1 replica, SA")
    env = {e["name"]: e["value"] for e in c["env"]}
    check(env["WEBHOOK_NAMESPACE"] == nsname, tag + " webhook: WEBHOOK_NAMESPACE")
    check("e38v2-system:e38v2-reset" in env["WEBHOOK_EXEMPT_USERS"], tag + " webhook: reset exempt in server too")
    check(float(env["WEBHOOK_BUDGET_S"]) < 5 and float(env["WEBHOOK_API_TIMEOUT_S"]) <= float(env["WEBHOOK_BUDGET_S"]),
          tag + " webhook: budget < timeoutSeconds")
    vols = {v["name"]: v for v in pod["volumes"]}
    check(vols["code"]["configMap"]["name"] == "e38v2-webhook-code" and vols["tls"]["secret"]["secretName"] == "e38v2-webhook-tls",
          tag + " webhook: code ConfigMap + TLS Secret")
    check(dep["spec"]["selector"]["matchLabels"] == svc["spec"]["selector"], tag + " webhook: service selector matches")
    check(svc["spec"]["ports"][0]["port"] == 443 and svc["spec"]["ports"][0]["targetPort"] == 8443, tag + " webhook: svc 443->8443")

    vwc = load_all(os.path.join(base, "webhook-config.yaml"))[0]
    check(vwc["apiVersion"] == "admissionregistration.k8s.io/v1" and vwc["kind"] == "ValidatingWebhookConfiguration", tag + " vwc: kind")
    w = vwc["webhooks"][0]
    check(len(vwc["webhooks"]) == 1 and w["name"].count(".") >= 2, tag + " vwc: one webhook, qualified name")
    check(w["rules"] == [{"apiGroups": [""], "apiVersions": ["v1"], "operations": ["DELETE"], "resources": ["pods"],
                          "scope": "Namespaced"}], tag + " vwc: DELETE pods only")
    check(w["failurePolicy"] == "Fail" and w["sideEffects"] == "None" and w["timeoutSeconds"] == 5
          and w["admissionReviewVersions"] == ["v1"], tag + " vwc: Fail/None/5s/v1")
    check(w["namespaceSelector"] == {"matchLabels": {"kubernetes.io/metadata.name": nsname}}, tag + " vwc: namespaceSelector exact")
    check(w["objectSelector"] == {"matchLabels": {"e38v2-webhook": "enforce"}}, tag + " vwc: objectSelector arm label")
    cs = w["clientConfig"]["service"]
    check(cs == {"namespace": "e38v2-system", "name": "e38v2-webhook", "path": "/validate", "port": 443}, tag + " vwc: service ref")
    exprs = " ".join(m["expression"] for m in w["matchConditions"])
    check("system:serviceaccount:e38v2-system:e38v2-reset" in exprs and "system:node:" in exprs
          and "namespace-controller" in exprs, tag + " vwc: matchConditions exempt reset/kubelet/controllers")
    check(len({m["name"] for m in w["matchConditions"]}) == len(w["matchConditions"]), tag + " vwc: unique matchCondition names")
    return w


def render_and_certs():
    tmp = tempfile.mkdtemp(prefix="e38v2_render_")
    out = os.path.join(tmp, "out")
    try:
        r = subprocess.run(["bash", os.path.join(DEPLOY, "render.sh"), TEST_RUN, out], capture_output=True, text=True)
        check(r.returncode == 0, "render.sh ok " + r.stderr.strip())
        bad = subprocess.run(["bash", os.path.join(DEPLOY, "render.sh"), "Bad_ID", os.path.join(tmp, "x")],
                             capture_output=True, text=True)
        check(bad.returncode != 0, "render.sh rejects invalid RUN_ID")
        r = subprocess.run(["bash", os.path.join(DEPLOY, "gen_certs.sh"), out], capture_output=True, text=True)
        check(r.returncode == 0, "gen_certs.sh ok " + (r.stdout + r.stderr).strip())
        w = validate_templates(out, TEST_RUN)
        with open(os.path.join(out, "certs", "ca.crt"), "rb") as fh:
            ca = fh.read()
        check(base64.b64decode(w["clientConfig"]["caBundle"]) == ca, "rendered vwc: caBundle == generated CA")
        again = subprocess.run(["bash", os.path.join(DEPLOY, "gen_certs.sh"), out], capture_output=True, text=True)
        check(again.returncode != 0, "gen_certs.sh refuses to overwrite existing certs")
        san = subprocess.run(["openssl", "x509", "-in", os.path.join(out, "certs", "tls.crt"), "-noout", "-ext",
                              "subjectAltName"], capture_output=True, text=True).stdout
        check("DNS:e38v2-webhook.e38v2-system.svc" in san, "tls.crt SAN has service DNS name")
        mode = oct(os.stat(os.path.join(out, "certs", "tls.key")).st_mode & 0o777)
        check(mode == "0o600", "tls.key mode 0600")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check(not os.path.exists(tmp), "temp certs deleted")


def main():
    for f in sorted(os.listdir(DEPLOY)):
        if f.endswith(".yaml"):
            try:
                n = len(load_all(os.path.join(DEPLOY, f)))
                check(n >= 1, "yaml.safe_load %s (%d docs)" % (f, n))
            except yaml.YAMLError as exc:
                check(False, "yaml.safe_load %s: %s" % (f, exc))
    validate_kind_config()
    validate_audit_policy()
    validate_templates()
    render_and_certs()
    print("\n%s: %d failure(s)" % ("OK" if not FAIL else "FAILED", len(FAIL)))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
