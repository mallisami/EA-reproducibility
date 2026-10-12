#!/usr/bin/env python3
"""Offline validation of the E39 deploy templates. Touches no cluster.

  ~/e39_20260926/venv_deploy/bin/python deploy/tests/validate_deploy.py   # PyYAML + openssl
Checks: YAML parses; the previous experiment's cluster/tree name appears in no deploy file;
required fields; scoping of webhook/audit/RBAC; kind-config mounts + kubeadm v1beta4 audit args;
render.sh + gen_certs.sh run in a temp dir and produce a caBundle equal to the generated CA;
temp output is deleted.
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
E = "/home/azureuser/e39_20260926"
SYS = "e39-system"
LOAD_NS = "e39-load"
# Forbidden token (the live previous-experiment cluster); split so this file does not match itself.
FORBIDDEN = "e38" + "v2"
IDENTITIES = ("responder", "writer", "reset", "mirror", "load", "webhook")


def user(identity):
    return "system:serviceaccount:%s:e39-%s" % (SYS, identity)


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def load_all(path):
    with open(path, encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d is not None]


def by_kind(docs, kind):
    return [d for d in docs if d.get("kind") == kind]


def _res_match(listed, resource, subresource):
    full = resource + ("/" + subresource if subresource else "")
    for r in listed:
        if r == full or r == "*":
            return True
        if subresource and r == "*/" + subresource:
            return True
    return False


def audit_level(policy, verb, resource, namespace, group="", subresource=""):
    """Tiny re-implementation of first-match audit rule evaluation for the fields we use
    (verbs, namespaces, resources incl. "res/sub" and "*/sub" subresource forms)."""
    for rule in policy["rules"]:
        if "verbs" in rule and verb not in rule["verbs"]:
            continue
        if "namespaces" in rule and namespace not in rule["namespaces"]:
            continue
        if "resources" in rule:
            if not any(g.get("group", "") == group and _res_match(g.get("resources", []), resource, subresource)
                       for g in rule["resources"]):
                continue
        return rule["level"]
    return "None"


def validate_no_forbidden():
    hits = []
    for root, dirs, files in os.walk(DEPLOY):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            p = os.path.join(root, f)
            with open(p, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if FORBIDDEN in line.lower():
                        hits.append("%s:%d" % (os.path.relpath(p, DEPLOY), i))
    check(not hits, "no '%s' anywhere in deploy/ %s" % (FORBIDDEN, hits[:10]))


def validate_kind_config():
    docs = load_all(os.path.join(DEPLOY, "kind-config.yaml"))
    check(len(docs) == 1, "kind-config: one document")
    kc = docs[0]
    check(kc.get("kind") == "Cluster" and kc.get("apiVersion") == "kind.x-k8s.io/v1alpha4", "kind-config: Cluster v1alpha4")
    check(kc.get("name") == "e39", "kind-config: cluster name e39")
    nodes = kc.get("nodes", [])
    check(len(nodes) == 1 and nodes[0].get("role") == "control-plane", "kind-config: single control-plane node")
    n = nodes[0]
    check(n.get("image") == "kindest/node:v1.37.0", "kind-config: node image kindest/node:v1.37.0")
    mounts = {m["containerPath"]: m for m in n.get("extraMounts", [])}
    check(all(m["hostPath"].startswith(E + "/") for m in mounts.values()), "kind-config: every hostPath under " + E)
    pol = mounts.get("/etc/kubernetes/e39/audit-policy.yaml")
    check(pol is not None and pol.get("readOnly") is True
          and pol["hostPath"] == E + "/deploy/audit-policy.yaml", "kind-config: audit policy mount (ro)")
    check(mounts.get("/var/log/kubernetes/e39-audit", {}).get("hostPath")
          == E + "/results/_audit_host", "kind-config: audit host log dir mount")
    check(mounts.get("/var/log/e39-webhook", {}).get("hostPath")
          == E + "/results/_webhook_host", "kind-config: webhook host log dir mount")
    patches = [yaml.safe_load(p) for p in n.get("kubeadmConfigPatches", [])]
    cc = [p for p in patches if p.get("kind") == "ClusterConfiguration"]
    check(len(cc) == 1 and cc[0].get("apiVersion") == "kubeadm.k8s.io/v1beta4", "kind-config: ClusterConfiguration v1beta4 patch")
    api = cc[0]["apiServer"]
    args = {a["name"]: a["value"] for a in api["extraArgs"]}
    check(all(isinstance(v, str) for v in args.values()), "kind-config: extraArgs values are strings")
    check(args.get("audit-policy-file") == "/etc/kubernetes/e39/audit-policy.yaml", "kind-config: audit-policy-file")
    check(args.get("audit-log-path") == "/var/log/kubernetes/e39-audit/audit.log", "kind-config: audit-log-path")
    check(args.get("audit-log-format") == "json" and args.get("audit-log-mode") == "blocking", "kind-config: json, blocking")
    for k in ("audit-log-maxsize", "audit-log-maxbackup", "audit-log-maxage"):
        check(args.get(k, "").isdigit(), "kind-config: " + k)
    vols = {v["mountPath"]: v for v in api["extraVolumes"]}
    check(vols.get(args["audit-policy-file"], {}).get("hostPath") == args["audit-policy-file"]
          and vols[args["audit-policy-file"]].get("pathType") == "File", "kind-config: policy extraVolume")
    check(vols.get("/var/log/kubernetes/e39-audit", {}).get("readOnly") is False, "kind-config: log extraVolume rw")
    check(all(v["hostPath"] in mounts for v in api["extraVolumes"]), "kind-config: extraVolumes backed by extraMounts")
    check(args.get("v") == "3" and args.get("vmodule") == "store=6,round_trippers=6",
          "kind-config: apiserver --v=3 --vmodule=store=6,round_trippers=6")
    kl = [p for p in patches if p.get("kind") == "KubeletConfiguration"]
    check(len(kl) == 1 and kl[0].get("containerLogMaxSize") == "4Gi" and kl[0].get("containerLogMaxFiles") == 5,
          "kind-config: kubelet containerLogMaxSize 4Gi / MaxFiles 5")


def validate_audit_policy():
    docs = load_all(os.path.join(DEPLOY, "audit-policy.yaml"))
    p = docs[0]
    check(p.get("apiVersion") == "audit.k8s.io/v1" and p.get("kind") == "Policy", "audit: Policy v1")
    check(p.get("omitStages") == ["RequestReceived"], "audit: omit only RequestReceived")
    check(p.get("omitManagedFields") is True, "audit: omitManagedFields")
    NP = "networking.k8s.io"
    for ns in ("e39-" + TEST_RUN, "e39-smoke-e39-20260927a"):
        rr = [("patch", "configmaps", "", ""), ("update", "configmaps", "", ""),
              ("patch", "pods", "", ""), ("update", "pods", "", ""),
              ("patch", "pods", "", "status"), ("update", "pods", "", "status"),
              ("delete", "pods", "", ""),
              ("create", "networkpolicies", NP, ""), ("delete", "networkpolicies", NP, "")]
        for verb, res, grp, sub in rr:
            check(audit_level(p, verb, res, ns, grp, sub) == "RequestResponse",
                  "audit: %s %s %s%s RR" % (ns, verb, res, "/" + sub if sub else ""))
        none = [("get", "configmaps", "", ""), ("list", "pods", "", ""), ("create", "pods", "", ""),
                ("watch", "configmaps", "", ""), ("delete", "configmaps", "", ""), ("create", "configmaps", "", ""),
                ("get", "pods", "", ""), ("watch", "pods", "", ""), ("get", "pods", "", "log"),
                ("create", "pods", "", "eviction"), ("create", "pods", "", "binding"),
                ("patch", "pods", "", "ephemeralcontainers"),
                ("get", "networkpolicies", NP, ""), ("patch", "networkpolicies", NP, ""),
                ("create", "events", "", ""), ("create", "selfsubjectaccessreviews", "authorization.k8s.io", "")]
        for verb, res, grp, sub in none:
            check(audit_level(p, verb, res, ns, grp, sub) == "None",
                  "audit: %s %s %s%s None" % (ns, verb, res, "/" + sub if sub else ""))
    for other in ("kube-system", SYS, LOAD_NS, "default", "kube-node-lease", "kube-public", "local-path-storage"):
        for verb, res, grp, sub in (("patch", "configmaps", "", ""), ("create", "configmaps", "", ""),
                                    ("delete", "pods", "", ""), ("patch", "pods", "", ""),
                                    ("patch", "pods", "", "status"), ("create", "networkpolicies", NP, "")):
            check(audit_level(p, verb, res, other, grp, sub) == "None",
                  "audit: %s %s %s%s None" % (other, verb, res, "/" + sub if sub else ""))
    check(audit_level(p, "update", "leases", "kube-node-lease", "coordination.k8s.io") == "None", "audit: leases None")
    check(p["rules"][-1] == {"level": "None"}, "audit: catch-all None last")
    check(p["rules"][0].get("level") == "None" and {SYS, LOAD_NS} <= set(p["rules"][0].get("namespaces", [])),
          "audit: first rule excludes e39-system and e39-load by name")


def validate_templates(rendered_dir=None, run=None):
    base = rendered_dir or DEPLOY
    tag = "rendered" if rendered_dir else "template"
    nsname = "e39-" + (run or "RUNID")
    ns_docs = load_all(os.path.join(base, "namespace.yaml"))
    names = [d["metadata"]["name"] for d in by_kind(ns_docs, "Namespace")]
    check(names == [SYS, nsname, LOAD_NS], "%s namespace: %s + %s + %s" % (tag, SYS, nsname, LOAD_NS))
    rb = load_all(os.path.join(base, "rbac.yaml"))
    sas = by_kind(rb, "ServiceAccount")
    check({d["metadata"]["name"] for d in sas} == {"e39-" + i for i in IDENTITIES}
          and all(d["metadata"]["namespace"] == SYS for d in sas), tag + " rbac: six SAs in e39-system")
    check(all(d.get("automountServiceAccountToken") is (d["metadata"]["name"] == "e39-webhook") for d in sas),
          tag + " rbac: token automount only for the webhook SA")
    roles = {d["metadata"]["name"]: d for d in by_kind(rb, "Role")}
    check(all(r["metadata"]["namespace"] == (LOAD_NS if n == "e39-load" else nsname) for n, r in roles.items()),
          tag + " rbac: Roles only in test ns (load Role only in e39-load)")

    def verbs(role, res, group=""):
        out = set()
        for r in roles[role]["rules"]:
            if res in r["resources"] and group in r["apiGroups"]:
                out |= set(r["verbs"])
        return out
    NP = "networking.k8s.io"
    full = {"get", "list", "watch", "create", "update", "patch", "delete"}
    check(verbs("e39-responder", "configmaps") == {"get"}, tag + " rbac: responder CM get only")
    check(verbs("e39-responder", "pods") == {"get", "delete"}, tag + " rbac: responder pods get/delete")
    check(verbs("e39-responder", "networkpolicies", NP) == {"create", "delete"}, tag + " rbac: responder netpol create/delete")
    check(verbs("e39-writer", "configmaps") == {"get", "patch"} and verbs("e39-writer", "pods") == {"get", "patch"}
          and verbs("e39-writer", "networkpolicies", NP) == set(), tag + " rbac: writer CM get/patch, pods get/patch")
    check(full <= verbs("e39-reset", "pods") and full <= verbs("e39-reset", "configmaps")
          and full <= verbs("e39-reset", "networkpolicies", NP), tag + " rbac: reset full control CM/pods/netpol")
    check(verbs("e39-mirror", "configmaps") == {"get", "list", "watch"} and verbs("e39-mirror", "pods") == {"get", "patch"}
          and verbs("e39-mirror", "networkpolicies", NP) == set(), tag + " rbac: mirror CM get/list/watch, pods get/patch")
    check(verbs("e39-webhook-reader", "configmaps") == {"get"} and verbs("e39-webhook-reader", "pods") == set(),
          tag + " rbac: webhook CM get only (server reads no Pods)")
    wr = [r for r in roles["e39-webhook-reader"]["rules"] if "configmaps" in r["resources"]][0]
    check(len(wr["resourceNames"]) == 8 and "e38-epoch" not in wr["resourceNames"], tag + " rbac: webhook 8 resourceNames")
    check(verbs("e39-load", "configmaps") == {"get", "list", "patch", "create"} and len(roles["e39-load"]["rules"]) == 1,
          tag + " rbac: load get/list/patch/create CMs only")
    crs = by_kind(rb, "ClusterRole")
    check(len(crs) == 1 and crs[0]["rules"] == [{"apiGroups": ["authorization.k8s.io"],
          "resources": ["selfsubjectaccessreviews"], "verbs": ["create"]}], tag + " rbac: only cluster perm is SSAR create")
    bindings = by_kind(rb, "RoleBinding") + by_kind(rb, "ClusterRoleBinding")
    check(all(s["namespace"] == SYS for b in bindings for s in b["subjects"]), tag + " rbac: subjects in e39-system")
    check(all(b["roleRef"]["name"] in roles or b["roleRef"]["kind"] == "ClusterRole" for b in bindings), tag + " rbac: roleRefs resolve")
    rbs = {b["metadata"]["name"]: b for b in by_kind(rb, "RoleBinding")}
    check(all(rbs[n]["metadata"]["namespace"] == roles[n]["metadata"]["namespace"] for n in roles if n in rbs)
          and set(rbs) == set(roles), tag + " rbac: one RoleBinding per Role, same namespace")
    load_bound = [b for b in bindings if any(s["name"] == "e39-load" for s in b["subjects"])]
    check(len(load_bound) == 1 and load_bound[0]["metadata"].get("namespace") == LOAD_NS,
          tag + " rbac: load identity bound only in e39-load")
    crb = by_kind(rb, "ClusterRoleBinding")[0]
    check({s["name"] for s in crb["subjects"]} == {"e39-responder", "e39-reset"}, tag + " rbac: SSAR for responder/reset")

    wh = load_all(os.path.join(base, "webhook.yaml"))
    dep = by_kind(wh, "Deployment")[0]
    svc = by_kind(wh, "Service")[0]
    pod = dep["spec"]["template"]["spec"]
    c = pod["containers"][0]
    check(dep["metadata"]["namespace"] == SYS and svc["metadata"]["namespace"] == SYS
          and svc["metadata"]["name"] == "e39-webhook", tag + " webhook: Deployment/Service e39-webhook in e39-system")
    check(c["image"] == "python:3.12-slim" and c["imagePullPolicy"] == "Never", tag + " webhook: python:3.12-slim, no pulls")
    check(dep["spec"]["replicas"] == 1 and pod["serviceAccountName"] == "e39-webhook", tag + " webhook: 1 replica, SA")
    env = {e["name"]: e["value"] for e in c["env"]}
    check(env["WEBHOOK_NAMESPACE"] == nsname, tag + " webhook: WEBHOOK_NAMESPACE")
    check(env["WEBHOOK_EXEMPT_USERS"] == user("reset"), tag + " webhook: reset exempt in server too")
    check(env["WEBHOOK_LOG_PATH"] == "/var/log/e39-webhook/%s/webhook_log.jsonl" % (run or "RUNID"), tag + " webhook: log path")
    check(float(env["WEBHOOK_BUDGET_S"]) < 5 and float(env["WEBHOOK_API_TIMEOUT_S"]) <= float(env["WEBHOOK_BUDGET_S"]),
          tag + " webhook: budget < timeoutSeconds")
    vols = {v["name"]: v for v in pod["volumes"]}
    check(vols["code"]["configMap"]["name"] == "e39-webhook-code" and vols["tls"]["secret"]["secretName"] == "e39-webhook-tls",
          tag + " webhook: code ConfigMap + TLS Secret")
    check(vols["log"]["hostPath"]["path"] == "/var/log/e39-webhook", tag + " webhook: log hostPath = kind extraMount")
    check(dep["spec"]["selector"]["matchLabels"] == svc["spec"]["selector"], tag + " webhook: service selector matches")
    check(svc["spec"]["ports"][0]["port"] == 443 and svc["spec"]["ports"][0]["targetPort"] == 8443, tag + " webhook: svc 443->8443")

    vwc = load_all(os.path.join(base, "webhook-config.yaml"))[0]
    check(vwc["apiVersion"] == "admissionregistration.k8s.io/v1" and vwc["kind"] == "ValidatingWebhookConfiguration", tag + " vwc: kind")
    w = vwc["webhooks"][0]
    check(len(vwc["webhooks"]) == 1 and w["name"].count(".") >= 2, tag + " vwc: one webhook, qualified name")
    check(w["rules"] == [{"apiGroups": [""], "apiVersions": ["v1"], "operations": ["DELETE"], "resources": ["pods"],
                          "scope": "Namespaced"},
                         {"apiGroups": ["networking.k8s.io"], "apiVersions": ["v1"], "operations": ["CREATE"],
                          "resources": ["networkpolicies"], "scope": "Namespaced"}],
          tag + " vwc: DELETE pods + CREATE networkpolicies only")
    check(w["failurePolicy"] == "Fail" and w["sideEffects"] == "None" and w["timeoutSeconds"] == 5
          and w["admissionReviewVersions"] == ["v1"] and w["matchPolicy"] == "Exact", tag + " vwc: Fail/None/5s/v1/Exact")
    check(w["namespaceSelector"] == {"matchLabels": {"kubernetes.io/metadata.name": nsname}}, tag + " vwc: namespaceSelector exact")
    check(w["objectSelector"] == {"matchLabels": {"e39-webhook": "enforce"}}, tag + " vwc: objectSelector e39-webhook=enforce")
    cs = w["clientConfig"]["service"]
    check(cs == {"namespace": SYS, "name": "e39-webhook", "path": "/validate", "port": 443}, tag + " vwc: service ref")
    exprs = " ".join(m["expression"] for m in w["matchConditions"])
    check(user("reset") in exprs and "system:node:" in exprs and "namespace-controller" in exprs
          and "generic-garbage-collector" in exprs, tag + " vwc: matchConditions exempt reset/kubelet/controllers")
    check(len({m["name"] for m in w["matchConditions"]}) == len(w["matchConditions"]), tag + " vwc: unique matchCondition names")
    return w


def render_and_certs():
    tmp = tempfile.mkdtemp(prefix="e39_render_")
    out = os.path.join(tmp, "out")
    try:
        r = subprocess.run(["bash", os.path.join(DEPLOY, "render.sh"), TEST_RUN, out], capture_output=True, text=True)
        check(r.returncode == 0, "render.sh ok " + (r.stdout + r.stderr).strip())
        for badid in ("Bad_ID", "system", "load"):
            bad = subprocess.run(["bash", os.path.join(DEPLOY, "render.sh"), badid, os.path.join(tmp, "x")],
                                 capture_output=True, text=True)
            check(bad.returncode != 0, "render.sh rejects RUN_ID %r" % badid)
        with open(os.path.join(out, "RENDER_MANIFEST.txt"), encoding="utf-8") as fh:
            check("namespace=e39-" + TEST_RUN in fh.read(), "render manifest names e39-" + TEST_RUN)
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
        check("DNS:e39-webhook.e39-system.svc" in san and "DNS:e39-webhook.e39-system.svc.cluster.local" in san,
              "tls.crt SAN has service DNS name e39-webhook.e39-system.svc")
        mode = oct(os.stat(os.path.join(out, "certs", "tls.key")).st_mode & 0o777)
        check(mode == "0o600", "tls.key mode 0600")
        rendered_text = ""
        for f in os.listdir(out):
            if os.path.isfile(os.path.join(out, f)):
                with open(os.path.join(out, f), encoding="utf-8") as fh:
                    rendered_text += fh.read()
        check(FORBIDDEN not in rendered_text.lower() and "RUNID" not in rendered_text,
              "rendered output: no '%s', no RUNID" % FORBIDDEN)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check(not os.path.exists(tmp), "temp render/cert output deleted")


def main():
    for f in sorted(os.listdir(DEPLOY)):
        if f.endswith(".yaml"):
            try:
                n = len(load_all(os.path.join(DEPLOY, f)))
                check(n >= 1, "yaml.safe_load %s (%d docs)" % (f, n))
            except yaml.YAMLError as exc:
                check(False, "yaml.safe_load %s: %s" % (f, exc))
    validate_no_forbidden()
    validate_kind_config()
    validate_audit_policy()
    validate_templates()
    render_and_certs()
    print("\n%s: %d failure(s)" % ("OK" if not FAIL else "FAILED", len(FAIL)))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
