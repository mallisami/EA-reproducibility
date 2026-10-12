#!/usr/bin/env python3
"""EA follow-up experiment E43 (grace period) and shared harness for E46-E50.  Standalone harness; does not import the
original campaign code.

  python3 ea_exp.py probe
  python3 ea_exp.py lock    --plan plan_E43_grace.json    --out runs/E43
  python3 ea_exp.py run     --out runs/E43
  python3 ea_exp.py analyze --out runs/E43
"""
import argparse, csv, hashlib, json, math, os, random, sys, threading, time
from collections import defaultdict
from datetime import datetime, timezone

NS = "ea-followup"
ANN = {"scope": "ea.example/scope", "evidence": "ea.example/evidence",
       "policy": "ea.example/policy"}
CMS = {"scope": "ea-scope", "evidence": "ea-evidence", "policy": "ea-policy"}
IMAGES = {"prompt": ("registry.k8s.io/pause:3.10", None),
          "ignore-term": ("busybox:1.36",
                          ["sh", "-c", "trap '' TERM; while true; do sleep 1; done"])}
HERE = os.path.dirname(os.path.abspath(__file__))


sys.setswitchinterval(1e-4)   # keep GIL hand-offs short; timing threads must not starve each other


def now_ns():
    return time.perf_counter_ns()


def wait_until(target_ns):
    """Sleep to ~0.5 ms before the target, then yield-spin (releases the GIL each pass)."""
    rem = (target_ns - now_ns()) / 1e9
    if rem > 0.001:
        time.sleep(rem - 0.0005)
    while now_ns() < target_ns:
        time.sleep(0)


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


# --------------------------------------------------------------------------
# schedule + lock
# --------------------------------------------------------------------------
def build_schedule(plan):
    """One row per trial.  Cells are randomized within each repetition block."""
    rng = random.Random(plan["seed"])
    cells = []
    for blk in plan["blocks"]:
        for arm in blk["arms"]:
            for d in blk["delays_ms"]:
                cells.append(dict(block=blk["name"], arm=arm, grace=blk["grace_s"],
                                  workload=blk["workload"], hold_ms=blk.get("hold_ms", 0),
                                  cond=blk.get("cond", "scope"), delta_ms=d, n=blk["n"]))
            cells.append(dict(block=blk["name"], arm=arm, grace=blk["grace_s"],
                              workload=blk["workload"], hold_ms=blk.get("hold_ms", 0),
                              cond="no_write", delta_ms=None, n=blk.get("n_control", 39)))
    rows = []
    for rep in range(max(c["n"] for c in cells)):
        batch = [dict(c, rep=rep) for c in cells if rep < c["n"]]
        rng.shuffle(batch)
        rows.extend(batch)
    for i, r in enumerate(rows):
        r["trial"] = i
        r.pop("n")
    return rows


def cmd_lock(a):
    os.makedirs(a.out, exist_ok=True)
    plan = json.load(open(a.plan))
    sched = build_schedule(plan)
    json.dump(plan, open(os.path.join(a.out, "plan.json"), "w"), indent=1)
    with open(os.path.join(a.out, "schedule.jsonl"), "w") as f:
        for r in sched:
            f.write(json.dumps(r) + "\n")
    files = {"plan.json": os.path.join(a.out, "plan.json"),
             "schedule.jsonl": os.path.join(a.out, "schedule.jsonl"),
             "ea_exp.py": os.path.abspath(__file__),
             "PROTOCOL.md": os.path.join(HERE, "PROTOCOL.md")}
    lock = {"locked_utc": datetime.now(timezone.utc).isoformat(),
            "n_trials": len(sched),
            "sha256": {k: sha256_file(v) for k, v in files.items() if os.path.exists(v)}}
    json.dump(lock, open(os.path.join(a.out, "LOCK.json"), "w"), indent=1)
    print(f"locked {len(sched)} trials; rough duration {len(sched)*1.1/3600:.1f} h in batch mode; {a.out}/LOCK.json")


def verify_lock(out):
    lock = json.load(open(os.path.join(out, "LOCK.json")))
    paths = {"plan.json": os.path.join(out, "plan.json"),
             "schedule.jsonl": os.path.join(out, "schedule.jsonl"),
             "ea_exp.py": os.path.abspath(__file__),
             "PROTOCOL.md": os.path.join(HERE, "PROTOCOL.md")}
    for k, h in lock["sha256"].items():
        if sha256_file(paths[k]) != h:
            sys.exit(f"LOCK VIOLATION: {k} changed after lock")
    return lock


# --------------------------------------------------------------------------
# cluster helpers
# --------------------------------------------------------------------------
def k8s():
    from kubernetes import client, config
    config.load_kube_config()
    return client, client.CoreV1Api(client.ApiClient())


def close_client(v1):
    """Release the client's keep-alive sockets (thousands of per-trial clients would exhaust fds)."""
    try:
        v1.api_client.rest_client.pool_manager.clear()
    except Exception:
        pass


def call(fn, *args, **kw):
    """Run an API call; return (status, parsed_json_or_text, t_send_ns, t_resp_ns)."""
    from kubernetes.client.rest import ApiException
    if fn.__name__.startswith("patch_"):   # the client would otherwise send a dict as json-patch
        kw.setdefault("_content_type", "application/merge-patch+json")
    t0 = now_ns()
    try:
        r = fn(*args, _preload_content=False, **kw)
        t1 = now_ns()
        status, body = r.status, r.data
    except ApiException as e:
        t1 = now_ns()
        status, body = e.status, e.body
    try:
        body = json.loads(body)
    except Exception:
        body = {"raw": str(body)[:300]}
    return status, body, t0, t1


def meta(obj):
    m = (obj or {}).get("metadata", {}) or {}
    rv = m.get("resourceVersion")
    return {"rv": int(rv) if rv and str(rv).isdigit() else None,
            "uid": m.get("uid"), "dts": m.get("deletionTimestamp"),
            "ann": {k: (m.get("annotations") or {}).get(v) for k, v in ANN.items()}}


def ensure_namespace(client, v1):
    from kubernetes.client.rest import ApiException
    try:
        v1.create_namespace(client.V1Namespace(metadata=client.V1ObjectMeta(name=NS)))
    except ApiException as e:
        if e.status != 409:
            raise
    for _ in range(120):          # Pods cannot be created until the namespace's default service account exists
        st, _, _, _ = call(v1.read_namespaced_service_account, "default", NS)
        if st == 200:
            break
        time.sleep(0.5)
    for cm in CMS.values():
        body = client.V1ConfigMap(metadata=client.V1ObjectMeta(name=cm), data={"value": "valid"})
        try:
            v1.create_namespaced_config_map(NS, body)
        except ApiException as e:
            if e.status != 409:
                raise


def make_pod(client, name, workload):
    image, cmd = IMAGES[workload]
    return client.V1Pod(
        metadata=client.V1ObjectMeta(name=name, annotations={v: "valid" for v in ANN.values()}),
        spec=client.V1PodSpec(
            restart_policy="Never", termination_grace_period_seconds=30,
            containers=[client.V1Container(name="c", image=image, command=cmd,
                                           image_pull_policy="IfNotPresent")]))


def wait_ready_quiet(v1, name, timeout=60.0, quiet_s=0.5):
    """Running+Ready, not terminating, and resourceVersion unchanged for quiet_s."""
    t_end = time.time() + timeout
    last_rv, last_change = None, time.time()
    while time.time() < t_end:
        st, p, _, _ = call(v1.read_namespaced_pod, name, NS)
        if st == 200:
            m = meta(p)
            cs = (p.get("status", {}).get("containerStatuses") or [{}])[0]
            ready = cs.get("ready") and "running" in (cs.get("state") or {}) and not m["dts"]
            if m["rv"] != last_rv:
                last_rv, last_change = m["rv"], time.time()
            if ready and time.time() - last_change >= quiet_s:
                return p
        time.sleep(0.05)
    return None


class Watcher(threading.Thread):
    def __init__(self, name, rv, timeout_s):
        super().__init__(daemon=True)
        self.name_, self.rv, self.timeout_s = name, rv, timeout_s
        self.events, self.done, self.error = [], threading.Event(), None

    def run(self):
        from kubernetes import watch
        _, v1 = k8s()
        try:
            w = watch.Watch()
            for ev in w.stream(v1.list_namespaced_pod, NS,
                               field_selector=f"metadata.name={self.name_}",
                               resource_version=str(self.rv),
                               timeout_seconds=int(self.timeout_s)):
                o = ev["raw_object"]
                m = meta(o)
                cs = (o.get("status", {}).get("containerStatuses") or [{}])[0]
                self.events.append({"t": now_ns(), "type": ev["type"], **m,
                                    "running": "running" in (cs.get("state") or {})})
                if ev["type"] == "DELETED":
                    w.stop()
                    break
        except Exception as e:  # recorded; trial becomes UNKNOWN in analysis
            self.error = repr(e)
        close_client(v1)
        self.done.set()


class Writer(threading.Thread):
    """Pre-armed invalidating writer released delta_ms after the gate-end anchor."""
    def __init__(self, arm, cond, pod, delta_ms):
        super().__init__(daemon=True)
        self.arm, self.cond, self.pod, self.delta_ms = arm, cond, pod, delta_ms
        self.anchor_ns, self.go, self.res = None, threading.Event(), None
        _, self.v1 = k8s()
        call(self.v1.read_namespaced_pod, pod, NS)            # warm the connection

    def run(self):
        self.go.wait()
        target = self.anchor_ns + int(self.delta_ms * 1e6)
        wait_until(target)
        if self.arm == "A4d":
            body = {"metadata": {"annotations": {ANN[self.cond]: "invalid"}}}
            st, o, t0, t1 = call(self.v1.patch_namespaced_pod, self.pod, NS, body)
        else:
            st, o, t0, t1 = call(self.v1.patch_namespaced_config_map,
                                 cm_name(self.pod, self.cond), NS, {"data": {"value": "invalid"}})
        self.res = {"status": st, "t_send": t0, "t_resp": t1, "target": target, **meta(o)}


def cm_name(pod, key):
    return f"{pod}-{key}"


def wait_all_ready_quiet(v1, names, timeout=120.0, quiet_s=0.5):
    """All Pods Running+Ready, not terminating, each with unchanged resourceVersion for quiet_s."""
    t_end = time.time() + timeout
    last = {n: (None, time.time()) for n in names}
    pods = {}
    while time.time() < t_end:
        st, lst, _, _ = call(v1.list_namespaced_pod, NS)
        ok = st == 200
        if ok:
            items = {p["metadata"]["name"]: p for p in lst.get("items", [])}
            for n in names:
                p = items.get(n)
                if p is None:
                    ok = False
                    continue
                m = meta(p)
                if m["rv"] != last[n][0]:
                    last[n] = (m["rv"], time.time())
                cs = (p.get("status", {}).get("containerStatuses") or [{}])[0]
                ready = cs.get("ready") and "running" in (cs.get("state") or {}) and not m["dts"]
                ok &= bool(ready) and time.time() - last[n][1] >= quiet_s
                pods[n] = p
        if ok:
            return pods
        time.sleep(0.05)
    return {}


def prepare_batch(client, v1, rows):
    """Create the batch's Pods (and per-trial ConfigMaps), wait until all are quiet, arm threads.
    Nothing is created while trials are being measured."""
    ctxs = []
    for row in rows:
        name = f"t{row['trial']:06d}"
        if row["arm"] == "A1":
            for k in CMS:
                call(v1.create_namespaced_config_map, NS, client.V1ConfigMap(
                    metadata=client.V1ObjectMeta(name=cm_name(name, k)), data={"value": "valid"}))
        call(v1.create_namespaced_pod, NS, make_pod(client, name, row["workload"]))
        ctxs.append({"row": row, "name": name,
                     "rec": dict(row, pod=name, utc=datetime.now(timezone.utc).isoformat())})
    pods = wait_all_ready_quiet(v1, [c["name"] for c in ctxs])
    for c in ctxs:
        p = pods.get(c["name"])
        c["ready"] = p is not None
        if not c["ready"]:
            continue
        c["watcher"] = Watcher(c["name"], meta(p)["rv"], c["row"]["grace"] + 90)
        c["watcher"].start()
        c["writer"] = None
        if c["row"]["cond"] != "no_write":
            c["writer"] = Writer(c["row"]["arm"], c["row"]["cond"], c["name"], c["row"]["delta_ms"])
            c["writer"].start()
    time.sleep(0.3)                                           # let watches establish
    return ctxs


SHORT_MS = 300      # writers released within this delay are joined inside the measured section


def critical(client, v1, c):
    """The timed section: gate, optional hold, DELETE.  Run strictly one at a time."""
    row, name, rec, writer = c["row"], c["name"], c["rec"], c["writer"]
    arm, grace = row["arm"], row["grace"]
    t_gate0 = now_ns()
    ok = True
    if arm == "A1":       # client revalidation of externally authoritative state
        for k in CMS:
            s, o, _, _ = call(v1.read_namespaced_config_map, cm_name(name, k), NS)
            ok &= s == 200 and (o.get("data") or {}).get("value") == "valid"
    s, p, _, _ = call(v1.read_namespaced_pod, name, NS)
    g = meta(p)
    ok &= s == 200 and not g["dts"]
    if arm == "A4d":      # target-resident authoritative state
        ok &= all(v == "valid" for v in g["ann"].values())
    t_gate1 = now_ns()
    if writer:
        writer.anchor_ns = t_gate1
        writer.go.set()
    rec.update(gate_ok=bool(ok), gate_rv=g["rv"], gate_uid=g["uid"],
               t_gate0=t_gate0, t_gate1=t_gate1)
    if ok:
        wait_until(t_gate1 + int(row["hold_ms"] * 1e6))
        pre = client.V1Preconditions(uid=g["uid"],
                                     resource_version=str(g["rv"]) if arm == "A4d" else None)
        s, o, t0, t1 = call(v1.delete_namespaced_pod, name, NS,
                            body=client.V1DeleteOptions(grace_period_seconds=grace,
                                                        preconditions=pre))
        rec["delete"] = {"status": s, "t_send": t0, "t_resp": t1, **meta(o),
                         "message": (o.get("message") or "")[:200] if s >= 400 else ""}
    c["accepted"] = bool(ok and rec["delete"]["status"] in (200, 202))
    if writer and row["delta_ms"] <= SHORT_MS:
        writer.join(timeout=10)


def finalize(c, sink):
    """Untimed tail: wait for late writer and for removal, clean up, store the record."""
    _, v1 = k8s()
    row, name, rec, writer = c["row"], c["name"], c["rec"], c["writer"]
    if writer:
        writer.join(timeout=(row["delta_ms"] or 0) / 1000 + 30)
        rec["write"] = writer.res
    if not c["accepted"]:      # clean up the surviving Pod (unconditioned, grace 0)
        call(v1.delete_namespaced_pod, name, NS, body=client_mod().V1DeleteOptions(grace_period_seconds=0))
    c["watcher"].done.wait(timeout=row["grace"] + 90)
    rec["events"], rec["watch_error"] = c["watcher"].events, c["watcher"].error
    rec["cleanup_delete"] = not c["accepted"]
    if row["arm"] == "A1":
        for k in CMS:
            call(v1.delete_namespaced_config_map, cm_name(name, k), NS)
    close_client(v1)
    if writer:
        close_client(writer.v1)
    c.clear()                                                 # drop references to threads and clients
    sink(rec)


def client_mod():
    from kubernetes import client
    return client


def cmd_run(a):
    verify_lock(a.out)
    client, v1 = k8s()
    ensure_namespace(client, v1)
    sched = [json.loads(l) for l in open(os.path.join(a.out, "schedule.jsonl"))]
    path = os.path.join(a.out, "trials.jsonl")
    done = set()
    if os.path.exists(path):
        done = {json.loads(l)["trial"] for l in open(path)}
    todo = [r for r in sched if r["trial"] not in done]
    batch = 1 if a.serial else a.batch
    env = {"version": client.VersionApi().get_code().to_dict(), "batch": batch, "serial": a.serial, "gap_s": a.gap,
           "started_utc": datetime.now(timezone.utc).isoformat(), "resumed_after": len(done)}
    with open(os.path.join(a.out, "environment.jsonl"), "a") as f:
        f.write(json.dumps(env) + "\n")
    out = open(path, "a")
    wlock, slots, tails = threading.Lock(), threading.Semaphore(a.max_outstanding), []

    def sink(rec):
        with wlock:
            out.write(json.dumps(rec) + "\n")
            out.flush()
        slots.release()

    for i in range(0, len(todo), batch):
        verify_lock(a.out)
        ctxs = prepare_batch(client, v1, todo[i:i + batch])
        for c in ctxs:
            slots.acquire()
            if not c["ready"]:
                call(v1.delete_namespaced_pod, c["name"], NS,
                     body=client.V1DeleteOptions(grace_period_seconds=0))
                sink(dict(c["rec"], infra_error="pod_not_ready"))
                continue
            try:
                critical(client, v1, c)
            except Exception as e:
                call(v1.delete_namespaced_pod, c["name"], NS,
                     body=client.V1DeleteOptions(grace_period_seconds=0))
                sink(dict(c["rec"], infra_error=repr(e)))
                continue
            th = threading.Thread(target=finalize, args=(c, sink), daemon=True)
            th.start()
            if a.serial:
                th.join()
            else:
                tails.append(th)
                time.sleep(a.gap)      # lets the previous Pod's teardown settle before the next timed section
        tails = [x for x in tails if x.is_alive()]
        print(f"{min(i + batch, len(todo))}/{len(todo)} trials started", flush=True)
    for th in tails:
        th.join()
    out.close()


def cmd_probe(a):
    """P1 revision order across kinds; P2 stale-RV DELETE is rejected; P3 kubelet quiescence."""
    client, v1 = k8s()
    ensure_namespace(client, v1)
    out = {}
    call(v1.create_namespaced_pod, NS, make_pod(client, "probe", "prompt"))
    p = wait_ready_quiet(v1, "probe")
    assert p, "probe pod not ready"
    rvs = []
    for i in range(50):
        _, o, _, _ = call(v1.patch_namespaced_config_map, CMS["scope"], NS, {"data": {"value": f"p{i}"}})
        rvs.append(meta(o)["rv"])
        _, o, _, _ = call(v1.patch_namespaced_pod, "probe", NS,
                          {"metadata": {"annotations": {"ea.example/probe": str(i)}}})
        rvs.append(meta(o)["rv"])
    out["P1_strictly_increasing_cross_kind"] = all(x < y for x, y in zip(rvs, rvs[1:]))
    stale = rvs[-3]
    s, o, _, _ = call(v1.delete_namespaced_pod, "probe", NS, body=client.V1DeleteOptions(
        grace_period_seconds=0, preconditions=client.V1Preconditions(resource_version=str(stale))))
    out["P2_stale_rv_delete_status"] = s
    out["P2_message"] = (o.get("message") or "")[:200]
    p = wait_ready_quiet(v1, "probe")
    rv0 = meta(p)["rv"]
    time.sleep(60)
    _, p2, _, _ = call(v1.read_namespaced_pod, "probe", NS)
    out["P3_rv_changes_in_60s"] = int(meta(p2)["rv"] != rv0)
    call(v1.delete_namespaced_pod, "probe", NS, body=client.V1DeleteOptions(grace_period_seconds=0))
    call(v1.patch_namespaced_config_map, CMS["scope"], NS, {"data": {"value": "valid"}})
    print(json.dumps(out, indent=1))
    ok = out["P1_strictly_increasing_cross_kind"] and s == 409 and out["P3_rv_changes_in_60s"] == 0
    print("PROBES", "PASSED" if ok else "FAILED - do not run")


# --------------------------------------------------------------------------
# analysis (pure; no cluster needed)
# --------------------------------------------------------------------------
def wilson(k, n, z=1.959964):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def classify(r):
    """Return a dict of outcome flags for one trial record (see PROTOCOL.md section 4)."""
    o = dict(outcome="UNKNOWN", pre_mark=0, pre_removal=0, contested=0, stale_accept=0,
             postmark_patch=0, postmark_persisted=0, premark_patch=0, premark_persisted=0)
    if r.get("infra_error"):
        o["outcome"] = "INFRA_ERROR"
        return o
    w = r.get("write")
    if not r["gate_ok"]:
        o["outcome"] = "GATE_ABORT"
        return o
    d = r["delete"]
    if d["status"] == 409:
        rvc = "resourceversion" in d["message"].lower()
        o["outcome"] = "RV_CONFLICT" if rvc else "UID_CONFLICT"
        if rvc and w and w["status"] == 200 and w["rv"] and w["rv"] > r["gate_rv"] and not w["dts"]:
            o["contested"] = 1
        elif rvc:
            o["outcome"] = "RV_CONFLICT_UNATTRIBUTED"   # counts as FALSE_ABORT in no_write cells
        return o
    if d["status"] not in (200, 202):
        o["outcome"] = "DELETE_ERROR"
        return o
    ev = [e for e in r.get("events", []) if e.get("uid") == r["gate_uid"]]
    deleted = [e for e in ev if e["type"] == "DELETED"]
    if r.get("watch_error") or not deleted:
        return o                                          # UNKNOWN: effect not observed
    removed_rv = deleted[0]["rv"]
    marks = [e["rv"] for e in ev if e["dts"]]
    mark_rv = min(marks) if marks else removed_rv
    o["outcome"] = "EXECUTED"
    if not w or w["status"] != 200 or w["rv"] is None:
        return o                                          # no committed write
    cond = r["cond"]
    if r["arm"] == "A1":
        o["pre_mark"] = int(w["rv"] < mark_rv)
        o["pre_removal"] = int(w["rv"] < removed_rv)
        return o
    # A4d: write targets the Pod itself
    persisted = any(e["ann"].get(cond) == "invalid" for e in ev)
    if w["dts"]:                                          # PATCH answered by a Pod already in deletion
        o["postmark_patch"], o["postmark_persisted"] = 1, int(persisted)
        o["pre_removal"] = int(persisted)
    elif w["rv"] > r["gate_rv"]:                          # committed before the mark yet DELETE accepted
        o["premark_patch"], o["premark_persisted"] = 1, int(persisted)
        o["stale_accept"] = o["pre_mark"] = o["pre_removal"] = 1
    return o


def cmd_analyze(a):
    recs = [json.loads(l) for l in open(os.path.join(a.out, "trials.jsonl"))]
    cells = defaultdict(lambda: defaultdict(int))
    for r in recs:
        c = classify(r)
        key = (r["block"], r["arm"], r["grace"], r["workload"], r["hold_ms"], r["cond"],
               "" if r["delta_ms"] is None else r["delta_ms"])
        cells[key]["n"] += 1
        cells[key][c["outcome"]] += 1
        for f in ("pre_mark", "pre_removal", "contested", "stale_accept", "postmark_patch",
                  "postmark_persisted", "premark_patch", "premark_persisted"):
            cells[key][f] += c[f]
    cols = ["n", "EXECUTED", "RV_CONFLICT", "RV_CONFLICT_UNATTRIBUTED", "UID_CONFLICT",
            "GATE_ABORT", "UNKNOWN", "INFRA_ERROR", "DELETE_ERROR", "pre_mark", "pre_removal",
            "contested", "stale_accept", "postmark_patch", "postmark_persisted"]
    with open(os.path.join(a.out, "cells.csv"), "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["block", "arm", "grace_s", "workload", "hold_ms", "cond", "delta_ms"] + cols +
                    ["p_pre_mark", "p_pre_removal", "wilson_lo_removal", "wilson_hi_removal"])
        for key in sorted(cells, key=lambda k: tuple(str(x) for x in k[:6]) + (float(k[6] or -1),)):
            v = cells[key]
            den = v["n"] - v["UNKNOWN"] - v["INFRA_ERROR"] - v["DELETE_ERROR"]
            lo, hi = wilson(v["pre_removal"], den)
            wr.writerow(list(key) + [v[c] for c in cols] +
                        [round(v["pre_mark"] / den, 4) if den else "",
                         round(v["pre_removal"] / den, 4) if den else "",
                         round(lo, 4), round(hi, 4)])
    tot = defaultdict(int)
    for (blk, arm, *_), v in cells.items():
        if arm == "A4d":
            for f in ("contested", "stale_accept", "postmark_patch", "postmark_persisted", "n"):
                tot[f] += v[f]
    s = {"trials": len(recs),
         "A4d_contested_and_rejected": tot["contested"],
         "A4d_stale_accept": tot["stale_accept"],
         "A4d_stale_accept_wilson_upper_among_contested":
             round(wilson(tot["stale_accept"], tot["contested"] + tot["stale_accept"])[1], 4),
         "readback_postmark_patches": tot["postmark_patch"],
         "readback_postmark_persisted": tot["postmark_persisted"],
         "readback_persist_wilson_upper":
             round(wilson(tot["postmark_persisted"], tot["postmark_patch"])[1], 4)}
    json.dump(s, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    print(json.dumps(s, indent=1))
    print("cells ->", os.path.join(a.out, "cells.csv"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    p = sub.add_parser("lock"); p.add_argument("--plan", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("run"); p.add_argument("--out", required=True)
    p.add_argument("--batch", type=int, default=8, help="Pods prepared together before measuring")
    p.add_argument("--serial", action="store_true", help="one trial at a time, waiting for removal (slow)")
    p.add_argument("--max-outstanding", type=int, default=40, dest="max_outstanding")
    p.add_argument("--gap", type=float, default=0.3, help="seconds between timed sections")
    p = sub.add_parser("analyze"); p.add_argument("--out", required=True)
    a = ap.parse_args()
    {"probe": cmd_probe, "lock": cmd_lock, "run": cmd_run, "analyze": cmd_analyze}[a.cmd](a)
