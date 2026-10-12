#!/usr/bin/env python3
"""EA add-on experiments E46-E50.  Imports ea_exp.py without modifying it, so runs that were
locked with ea_exp.py stay valid.

  python3 ea_exp2.py vap-probe
  python3 ea_exp2.py lock    --plan plan_E46_vap.json --out runs/E46
  python3 ea_exp2.py run     --out runs/E46
  python3 ea_exp2.py analyze --out runs/E46
"""
import argparse, csv, itertools, json, os, random, sys, threading, time
from collections import defaultdict
from datetime import datetime, timezone
import ea_exp as B

NS, ANN, CMS = B.NS, B.ANN, B.CMS
BENIGN = "ea.example/benign"
VAP_LABEL = "ea-vap"
POD_STATE_ARMS = {"A4d", "A5v", "A6b", "A6u"}     # validity state lives on the target Pod
LABEL_ARMS = {"A6b", "A6u"}                       # action is a quarantine label PATCH, not DELETE
HERE = os.path.dirname(os.path.abspath(__file__))
LOCKED = ["ea_exp2.py", "ea_exp.py", "PROTOCOL_ADDENDUM.md", "vap.yaml"]
MAX_ATTEMPTS = 5


# ------------------------------------------------------------------ schedule + lock
def build_schedule(plan):
    rng = random.Random(plan["seed"])
    cells = []
    for blk in plan["blocks"]:
        base = dict(block=blk["name"], grace=blk.get("grace_s", 0), workload=blk.get("workload", "prompt"),
                    hold_ms=blk.get("hold_ms", 0))
        for arm, pol, hz in itertools.product(blk["arms"], blk.get("policies", ["none"]),
                                              blk.get("churn_hz", [0])):
            for d in blk.get("delays_ms", []):
                cells.append(dict(base, arm=arm, policy=pol, churn_hz=hz, cond=blk.get("cond", "scope"),
                                  delta_ms=d, n=blk["n"]))
            cells.append(dict(base, arm=arm, policy=pol, churn_hz=hz, cond="no_write", delta_ms=None,
                              n=blk.get("n_control", 39)))
    rows = []
    for rep in range(max(c["n"] for c in cells)):
        batch = [dict(c, rep=rep) for c in cells if rep < c["n"]]
        rng.shuffle(batch)
        rows.extend(batch)
    for i, r in enumerate(rows):
        r["trial"] = i
        r.pop("n")
    return rows


def lock_paths(out):
    p = {k: os.path.join(HERE, k) for k in LOCKED}
    p["plan.json"] = os.path.join(out, "plan.json")
    p["schedule.jsonl"] = os.path.join(out, "schedule.jsonl")
    return p


def cmd_lock(a):
    os.makedirs(a.out, exist_ok=True)
    plan = json.load(open(a.plan))
    sched = build_schedule(plan)
    json.dump(plan, open(os.path.join(a.out, "plan.json"), "w"), indent=1)
    with open(os.path.join(a.out, "schedule.jsonl"), "w") as f:
        for r in sched:
            f.write(json.dumps(r) + "\n")
    lock = {"locked_utc": datetime.now(timezone.utc).isoformat(), "n_trials": len(sched),
            "sha256": {k: B.sha256_file(v) for k, v in lock_paths(a.out).items() if os.path.exists(v)}}
    json.dump(lock, open(os.path.join(a.out, "LOCK.json"), "w"), indent=1)
    print(f"locked {len(sched)} trials; rough duration {len(sched)*1.1/3600:.1f} h; {a.out}/LOCK.json")


def verify_lock(out):
    lock = json.load(open(os.path.join(out, "LOCK.json")))
    paths = lock_paths(out)
    for k, h in lock["sha256"].items():
        if B.sha256_file(paths[k]) != h:
            sys.exit(f"LOCK VIOLATION: {k} changed after lock")


# ------------------------------------------------------------------ threads
class Writer(threading.Thread):
    """Timed writer.  cond 'benign' changes a security-irrelevant annotation on the target Pod."""
    def __init__(self, arm, cond, pod, delta_ms):
        super().__init__(daemon=True)
        self.arm, self.cond, self.pod, self.delta_ms = arm, cond, pod, delta_ms
        self.anchor_ns, self.go, self.res = None, threading.Event(), None
        _, self.v1 = B.k8s()
        B.call(self.v1.read_namespaced_pod, pod, NS)

    def run(self):
        self.go.wait()
        target = self.anchor_ns + int(self.delta_ms * 1e6)
        B.wait_until(target)
        if self.cond == "benign":
            body = {"metadata": {"annotations": {BENIGN: "w"}}}
            st, o, t0, t1 = B.call(self.v1.patch_namespaced_pod, self.pod, NS, body)
        elif self.arm in POD_STATE_ARMS:
            body = {"metadata": {"annotations": {ANN[self.cond]: "invalid"}}}
            st, o, t0, t1 = B.call(self.v1.patch_namespaced_pod, self.pod, NS, body)
        else:
            st, o, t0, t1 = B.call(self.v1.patch_namespaced_config_map,
                                   B.cm_name(self.pod, self.cond), NS, {"data": {"value": "invalid"}})
        self.res = {"status": st, "t_send": t0, "t_resp": t1, "target": target, **B.meta(o)}


class Churner(threading.Thread):
    """Poisson stream of security-irrelevant annotation changes on the target Pod."""
    def __init__(self, pod, hz, seed):
        super().__init__(daemon=True)
        self.pod, self.hz, self.rng = pod, hz, random.Random(seed)
        self.stop, self.count = threading.Event(), 0
        _, self.v1 = B.k8s()
        B.call(self.v1.read_namespaced_pod, pod, NS)

    def run(self):
        while not self.stop.is_set():
            st, _, _, _ = B.call(self.v1.patch_namespaced_pod, self.pod, NS,
                                 {"metadata": {"annotations": {BENIGN: str(self.count)}}})
            if st != 200:
                break
            self.count += 1
            self.stop.wait(self.rng.expovariate(self.hz))


def make_pod(client, name, row):
    p = B.make_pod(client, name, row["workload"])
    if row["arm"] == "A5v":
        p.metadata.labels = {VAP_LABEL: "on"}
    return p


def prepare_batch(client, v1, rows):
    ctxs = []
    for row in rows:
        name = f"t{row['trial']:06d}"
        if row["arm"] == "A1":
            for k in CMS:
                B.call(v1.create_namespaced_config_map, NS, client.V1ConfigMap(
                    metadata=client.V1ObjectMeta(name=B.cm_name(name, k)), data={"value": "valid"}))
        B.call(v1.create_namespaced_pod, NS, make_pod(client, name, row))
        ctxs.append({"row": row, "name": name,
                     "rec": dict(row, pod=name, utc=datetime.now(timezone.utc).isoformat())})
    pods = B.wait_all_ready_quiet(v1, [c["name"] for c in ctxs])
    for c in ctxs:
        p = pods.get(c["name"])
        c["ready"] = p is not None
        if not c["ready"]:
            continue
        row = c["row"]
        c["watcher"] = B.Watcher(c["name"], B.meta(p)["rv"], row["grace"] + 90)
        c["watcher"].start()
        c["writer"] = c["churner"] = None
        if row["cond"] != "no_write":
            c["writer"] = Writer(row["arm"], row["cond"], c["name"], row["delta_ms"])
            c["writer"].start()
        if row["churn_hz"]:
            c["churner"] = Churner(c["name"], row["churn_hz"], row["trial"])
    time.sleep(0.3)
    return ctxs


def gate_read(v1, arm, name):
    """Full revalidation.  Returns (ok, meta)."""
    ok = True
    if arm == "A1":
        for k in CMS:
            s, o, _, _ = B.call(v1.read_namespaced_config_map, B.cm_name(name, k), NS)
            ok &= s == 200 and (o.get("data") or {}).get("value") == "valid"
    s, p, _, _ = B.call(v1.read_namespaced_pod, name, NS)
    g = B.meta(p)
    ok &= s == 200 and not g["dts"]
    if arm in POD_STATE_ARMS:
        ok &= all(v == "valid" for v in g["ann"].values())
    return bool(ok), g


def act(client, v1, row, name, uid, rv):
    """One action attempt bound (where the arm binds) to resourceVersion rv."""
    arm = row["arm"]
    if arm in LABEL_ARMS:
        body = {"metadata": {"labels": {"ea-quarantine": "true"}}}
        if arm == "A6b":
            body["metadata"]["resourceVersion"] = str(rv)
        s, o, t0, t1 = B.call(v1.patch_namespaced_pod, name, NS, body)
    else:
        pre = client.V1Preconditions(uid=uid, resource_version=str(rv) if arm == "A4d" else None)
        s, o, t0, t1 = B.call(v1.delete_namespaced_pod, name, NS, body=client.V1DeleteOptions(
            grace_period_seconds=row["grace"], preconditions=pre))
    return {"status": s, "t_send": t0, "t_resp": t1, "bound_rv": rv, **B.meta(o),
            "message": (o.get("message") or "")[:240] if s >= 400 else ""}


def critical(client, v1, c):
    row, name, rec, writer, churner = c["row"], c["name"], c["rec"], c["writer"], c["churner"]
    if churner:
        churner.start()
        time.sleep(0.03)                                   # churn is already under way at the gate
    t0 = B.now_ns()
    ok, g = gate_read(v1, row["arm"], name)
    t1 = B.now_ns()
    if writer:
        writer.anchor_ns = t1
        writer.go.set()
    rec.update(gate_ok=ok, gate_rv=g["rv"], gate_uid=g["uid"], t_gate0=t0, t_gate1=t1, attempts=[],
               retry_abort=False)
    if ok:
        B.wait_until(t1 + int(row["hold_ms"] * 1e6))
        rv = g["rv"]
        for _ in range(MAX_ATTEMPTS):
            a = act(client, v1, row, name, g["uid"], rv)
            rec["attempts"].append(a)
            conflict = a["status"] == 409 and "uid in precondition" not in a["message"].lower()
            if not conflict or row["policy"] == "none":
                break
            if row["policy"] == "revalidate":              # full decision is recomputed
                ok2, g2 = gate_read(v1, row["arm"], name)
                if not ok2:
                    rec["retry_abort"] = True
                    break
            else:                                          # blind: only the version is refreshed
                s, p, _, _ = B.call(v1.read_namespaced_pod, name, NS)
                g2 = B.meta(p)
                if s != 200:
                    break
            rv = g2["rv"]
    if churner:
        churner.stop.set()
        rec["churn_writes"] = churner.count
    last = rec["attempts"][-1] if rec["attempts"] else None
    c["deleted"] = bool(last and last["status"] in (200, 202) and row["arm"] not in LABEL_ARMS)
    if writer and row["delta_ms"] <= B.SHORT_MS:
        writer.join(timeout=10)


def finalize(c, sink):
    client, v1 = B.k8s()
    row, name, rec, writer = c["row"], c["name"], c["rec"], c["writer"]
    if writer:
        writer.join(timeout=(row["delta_ms"] or 0) / 1000 + 30)
        rec["write"] = writer.res
        B.close_client(writer.v1)
    if c["churner"]:
        c["churner"].join(timeout=5)
        B.close_client(c["churner"].v1)
    if not c["deleted"]:
        if row["arm"] == "A5v":                            # take the Pod out of the policy's scope first
            B.call(v1.patch_namespaced_pod, name, NS, {"metadata": {"labels": {VAP_LABEL: None}}})
        B.call(v1.delete_namespaced_pod, name, NS, body=client.V1DeleteOptions(grace_period_seconds=0))
    c["watcher"].done.wait(timeout=row["grace"] + 90)
    rec["events"], rec["watch_error"] = c["watcher"].events, c["watcher"].error
    rec["cleanup_delete"] = not c["deleted"]
    if row["arm"] == "A1":
        for k in CMS:
            B.call(v1.delete_namespaced_config_map, B.cm_name(name, k), NS)
    B.close_client(v1)
    c.clear()
    sink(rec)


def cmd_run(a):
    verify_lock(a.out)
    client, v1 = B.k8s()
    B.ensure_namespace(client, v1)
    sched = [json.loads(l) for l in open(os.path.join(a.out, "schedule.jsonl"))]
    path = os.path.join(a.out, "trials.jsonl")
    done = {json.loads(l)["trial"] for l in open(path)} if os.path.exists(path) else set()
    todo = [r for r in sched if r["trial"] not in done]
    env = {"version": client.VersionApi().get_code().to_dict(), "batch": a.batch, "gap_s": a.gap,
           "started_utc": datetime.now(timezone.utc).isoformat(), "resumed_after": len(done)}
    with open(os.path.join(a.out, "environment.jsonl"), "a") as f:
        f.write(json.dumps(env) + "\n")
    out = open(path, "a")
    wlock, slots, tails = threading.Lock(), threading.Semaphore(40), []

    def sink(rec):
        with wlock:
            out.write(json.dumps(rec) + "\n")
            out.flush()
        slots.release()

    for i in range(0, len(todo), a.batch):
        verify_lock(a.out)
        for c in prepare_batch(client, v1, todo[i:i + a.batch]):
            slots.acquire()
            if not c["ready"]:
                B.call(v1.delete_namespaced_pod, c["name"], NS,
                       body=client.V1DeleteOptions(grace_period_seconds=0))
                sink(dict(c["rec"], infra_error="pod_not_ready"))
                continue
            try:
                critical(client, v1, c)
            except Exception as e:
                B.call(v1.patch_namespaced_pod, c["name"], NS, {"metadata": {"labels": {VAP_LABEL: None}}})
                B.call(v1.delete_namespaced_pod, c["name"], NS,
                       body=client.V1DeleteOptions(grace_period_seconds=0))
                sink(dict(c["rec"], infra_error=repr(e)))
                continue
            th = threading.Thread(target=finalize, args=(c, sink), daemon=True)
            th.start()
            tails.append(th)
            time.sleep(a.gap)
        tails = [x for x in tails if x.is_alive()]
        print(f"{min(i + a.batch, len(todo))}/{len(todo)} trials started", flush=True)
    for th in tails:
        th.join()
    out.close()


def cmd_vap_probe(a):
    """The policy must deny DELETE of a labelled Pod with an invalid annotation and allow a valid one."""
    client, v1 = B.k8s()
    B.ensure_namespace(client, v1)
    res = {}

    def mk(name, val):
        p = B.make_pod(client, name, "prompt")
        p.metadata.labels = {VAP_LABEL: "on"}
        p.metadata.annotations[ANN["scope"]] = val
        B.call(v1.create_namespaced_pod, NS, p)
        assert B.wait_ready_quiet(v1, name), "probe pod not ready"

    for i in range(9):                           # a new policy can take a while to become active
        name = f"vap-bad-{i}"
        mk(name, "invalid")
        s, o, _, _ = B.call(v1.delete_namespaced_pod, name, NS,
                            body=client.V1DeleteOptions(grace_period_seconds=0))
        res["invalid_pod_delete_status"], res["message"] = s, (o.get("message") or "")[:200]
        if s in (403, 422):
            B.call(v1.patch_namespaced_pod, name, NS, {"metadata": {"labels": {VAP_LABEL: None}}})
            B.call(v1.delete_namespaced_pod, name, NS, body=client.V1DeleteOptions(grace_period_seconds=0))
            break
        time.sleep(10)
    mk("vap-good", "valid")
    s2, _, _, _ = B.call(v1.delete_namespaced_pod, "vap-good", NS,
                         body=client.V1DeleteOptions(grace_period_seconds=0))
    res["valid_pod_delete_status"] = s2
    print(json.dumps(res, indent=1))
    ok = res["invalid_pod_delete_status"] in (403, 422) and s2 in (200, 202)
    print("VAP PROBE", "PASSED" if ok else "FAILED")
    sys.exit(0 if ok else 1)


# ------------------------------------------------------------------ analysis
FLAGS = ("pre_mark", "pre_removal", "contested", "stale_accept", "benign_abort",
         "postmark_patch", "postmark_persisted")


def classify(r):
    o = dict(outcome="UNKNOWN", attempts=len(r.get("attempts", [])), **{f: 0 for f in FLAGS})
    if r.get("infra_error"):
        o["outcome"] = "INFRA_ERROR"
        return o
    if not r["gate_ok"]:
        o["outcome"] = "GATE_ABORT"
        return o
    w, d, arm, cond = r.get("write"), r["attempts"][-1], r["arm"], r["cond"]
    committed = bool(w and w["status"] == 200 and w["rv"] is not None)
    premark_write = committed and not w["dts"] and w["rv"] > r["gate_rv"]   # landed on a live Pod
    if r.get("retry_abort"):
        o["outcome"] = "REVALIDATE_ABORT"
        o["contested"] = int(premark_write and cond != "benign")
        return o
    msg = d["message"].lower()
    denied = None
    if d["status"] == 409:
        denied = "UID_CONFLICT" if "uid in precondition" in msg else "RV_CONFLICT"
        if denied == "RV_CONFLICT" and o["attempts"] >= MAX_ATTEMPTS and r["policy"] != "none":
            denied = "EXHAUSTED"
    elif d["status"] in (403, 422) and ("ea-target-state" in msg or "validatingadmissionpolicy" in msg):
        denied = "POLICY_DENY"
    if denied:
        o["outcome"] = denied
        if denied in ("RV_CONFLICT", "POLICY_DENY", "EXHAUSTED"):
            if premark_write and cond != "benign":
                o["contested"] = 1
            elif cond == "benign" or r.get("churn_writes") or not committed:
                o["benign_abort"] = 1                     # rejected although validity was unchanged
        return o
    if d["status"] not in (200, 202):
        o["outcome"] = "ACTION_ERROR"
        return o
    if arm in LABEL_ARMS:                                 # effect is the label commit itself
        o["outcome"] = "EXECUTED"
        if committed and cond != "benign" and d["rv"] is not None and w["rv"] < d["rv"]:
            o["stale_accept"] = o["pre_mark"] = o["pre_removal"] = 1
        return o
    ev = [e for e in r.get("events", []) if e.get("uid") == r["gate_uid"]]
    deleted = [e for e in ev if e["type"] == "DELETED"]
    if r.get("watch_error") or not deleted:
        return o
    removed_rv = deleted[0]["rv"]
    marks = [e["rv"] for e in ev if e["dts"]]
    mark_rv = min(marks) if marks else removed_rv
    o["outcome"] = "EXECUTED"
    if not committed or cond == "benign":
        return o
    if arm == "A1":
        o["pre_mark"], o["pre_removal"] = int(w["rv"] < mark_rv), int(w["rv"] < removed_rv)
        return o
    persisted = any(e["ann"].get(cond) == "invalid" for e in ev)
    if w["dts"]:
        o["postmark_patch"], o["postmark_persisted"] = 1, int(persisted)
        o["pre_removal"] = int(persisted)
    elif premark_write:                                   # invalidated before the mark, yet executed
        o["stale_accept"] = o["pre_mark"] = o["pre_removal"] = 1
    return o


OUTCOMES = ["EXECUTED", "RV_CONFLICT", "POLICY_DENY", "UID_CONFLICT", "REVALIDATE_ABORT", "EXHAUSTED",
            "GATE_ABORT", "UNKNOWN", "INFRA_ERROR", "ACTION_ERROR"]


def cmd_analyze(a):
    recs = [json.loads(l) for l in open(os.path.join(a.out, "trials.jsonl"))]
    cells = defaultdict(lambda: defaultdict(int))
    for r in recs:
        c = classify(r)
        key = (r["block"], r["arm"], r["policy"], r["churn_hz"], r["grace"], r["hold_ms"], r["cond"],
               "" if r["delta_ms"] is None else r["delta_ms"])
        v = cells[key]
        v["n"] += 1
        v[c["outcome"]] += 1
        v["attempts_sum"] += c["attempts"]
        for f in FLAGS:
            v[f] += c[f]
    with open(os.path.join(a.out, "cells.csv"), "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["block", "arm", "policy", "churn_hz", "grace_s", "hold_ms", "cond", "delta_ms", "n"] +
                    OUTCOMES + list(FLAGS) + ["mean_attempts", "p_stale_accept", "wilson_hi_stale",
                                              "p_executed", "p_benign_abort"])
        for key in sorted(cells, key=lambda k: tuple(str(x) for x in k[:7]) + (float(k[7] or -1),)):
            v = cells[key]
            den = v["n"] - v["UNKNOWN"] - v["INFRA_ERROR"] - v["ACTION_ERROR"]
            wr.writerow(list(key) + [v["n"]] + [v[c] for c in OUTCOMES] + [v[f] for f in FLAGS] +
                        [round(v["attempts_sum"] / v["n"], 3),
                         round(v["stale_accept"] / den, 4) if den else "",
                         round(B.wilson(v["stale_accept"], den)[1], 4) if den else "",
                         round(v["EXECUTED"] / den, 4) if den else "",
                         round(v["benign_abort"] / den, 4) if den else ""])
    tot = defaultdict(lambda: defaultdict(int))
    for (blk, arm, pol, *_), v in cells.items():
        t = tot[f"{arm}/{pol}"]
        for f in ("n", "contested", "stale_accept", "benign_abort", "EXECUTED", "UNKNOWN", "INFRA_ERROR"):
            t[f] += v[f]
    s = {"trials": len(recs), "by_arm_policy": {
        k: dict(v, stale_upper_among_contested=round(
            B.wilson(v["stale_accept"], v["contested"] + v["stale_accept"])[1], 4)
            if v["contested"] + v["stale_accept"] else None) for k, v in sorted(tot.items())}}
    json.dump(s, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("vap-probe")
    p = sub.add_parser("lock"); p.add_argument("--plan", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("run"); p.add_argument("--out", required=True)
    p.add_argument("--batch", type=int, default=8); p.add_argument("--gap", type=float, default=0.3)
    p = sub.add_parser("analyze"); p.add_argument("--out", required=True)
    a = ap.parse_args()
    {"vap-probe": cmd_vap_probe, "lock": cmd_lock, "run": cmd_run, "analyze": cmd_analyze}[a.cmd](a)
