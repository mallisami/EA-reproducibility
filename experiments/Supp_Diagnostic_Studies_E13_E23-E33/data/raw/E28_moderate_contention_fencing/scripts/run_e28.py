#!/usr/bin/env python3
"""
E28 -- Concurrent Multi-Query Acquisition Coherence Under Moderate
(Realistic) Contention.

Identical mechanism, resources, and coherence oracle as E24, with a
bounded inter-toggle delay inserted into the mutator so it represents
plausible occasional configuration drift rather than adversarial
saturation. Tests whether the fenced double-collect mechanism is only
ever fail-safe (as E24's heavy-contention condition showed) or whether it
can actually deliver useful, coherent results at a realistic contention
rate. Reuses E24's namespace/ConfigMaps without modifying E24's own
frozen protocol or results. All HTTP calls are real, against a dedicated
`kind` namespace, through a live `kubectl proxy`. Nothing under
02_locked_methodology_v8.1.1/ or any other prior experiment is touched.
"""
import json, random, sys, threading, time
from pathlib import Path
from urllib.request import urlopen, Request

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL_PATH = ROOT / "E28_PROTOCOL.json"
PROTOCOL_HASH_PATH = ROOT / "E28_PROTOCOL.sha256"
PROXY = "http://127.0.0.1:8001"
NS = "flashir-e24"
RESOURCES = ["obs-a", "obs-c", "obs-e"]


def verify_protocol_hash():
    import hashlib
    actual = hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()
    recorded = PROTOCOL_HASH_PATH.read_text().split()[0]
    if actual != recorded:
        raise SystemExit(f"PROTOCOL HASH MISMATCH: file={actual} recorded={recorded} -- refusing to run")
    return json.loads(PROTOCOL_PATH.read_text())


def cm_url(name):
    return f"{PROXY}/api/v1/namespaces/{NS}/configmaps/{name}"


def get_obs(name, retries=3):
    last_err = None
    for attempt in range(retries):
        try:
            with urlopen(cm_url(name), timeout=5) as r:
                obj = json.loads(r.read())
            return obj["data"]["valid"] == "true", obj["metadata"]["resourceVersion"]
        except Exception as e:
            last_err = e
            time.sleep(0.1)
    raise RuntimeError(f"get_obs({name}) failed after {retries} attempts: {last_err}")


def set_obs(name, value, retries=3):
    body = json.dumps({"data": {"valid": "true" if value else "false"}}).encode()
    last_err = None
    for attempt in range(retries):
        try:
            req = Request(cm_url(name), data=body, method="PATCH",
                          headers={"Content-Type": "application/merge-patch+json"})
            with urlopen(req, timeout=5) as r:
                obj = json.loads(r.read())
            return obj["metadata"]["resourceVersion"]
        except Exception as e:
            last_err = e
            time.sleep(0.1)
    raise RuntimeError(f"set_obs({name}) failed after {retries} attempts: {last_err}")


class Mutator:
    """Continuously toggles all three resources concurrently, with a
    bounded delay between toggle rounds representing plausible occasional
    configuration drift rather than adversarial saturation. Every toggle
    is logged with a wall-clock timestamp for ground-truth
    reconstruction."""
    def __init__(self, rng, inter_tick_delay_s=0.0):
        self.rng = rng
        self.inter_tick_delay_s = inter_tick_delay_s
        self._stop = threading.Event()
        self._thread = None
        self.log = []  # list of (timestamp, resource, new_value, new_resource_version)
        self._lock = threading.Lock()
        self.state = {r: True for r in RESOURCES}

    def _loop(self):
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(RESOURCES)) as ex:
            while not self._stop.is_set():
                targets = {r: not self.state[r] for r in RESOURCES}

                def flip_one(r):
                    return r, set_obs(r, targets[r])

                futures = [ex.submit(flip_one, r) for r in RESOURCES]
                for fut in futures:
                    try:
                        r, rv = fut.result()
                        t = time.time()
                        with self._lock:
                            self.state[r] = targets[r]
                            self.log.append((t, r, targets[r], rv))
                    except Exception:
                        pass
                if self.inter_tick_delay_s > 0:
                    self._stop.wait(self.inter_tick_delay_s)

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def snapshot_log(self):
        with self._lock:
            return list(self.log)


def reset_all_true():
    for r in RESOURCES:
        set_obs(r, True)


def naive_sequential_acquire():
    t0 = time.time()
    result = {}
    for r in RESOURCES:
        val, rv = get_obs(r)
        result[r] = {"value": val, "resource_version": rv, "get_time": time.time()}
    t1 = time.time()
    return result, (t1 - t0) * 1000, {"retries": 0, "outcome": "delivered"}


def fenced_double_collect_acquire(max_retries):
    for attempt in range(max_retries + 1):
        t0 = time.time()
        round1 = {}
        for r in RESOURCES:
            val, rv = get_obs(r)
            round1[r] = {"value": val, "resource_version": rv, "get_time": time.time()}
        round2_rv = {}
        for r in RESOURCES:
            _, rv = get_obs(r)
            round2_rv[r] = rv
        t1 = time.time()
        if all(round1[r]["resource_version"] == round2_rv[r] for r in RESOURCES):
            return round1, (t1 - t0) * 1000, {"retries": attempt, "outcome": "delivered"}
    return None, None, {"retries": max_retries, "outcome": "UNKNOWN_COHERENCE"}


def check_coherence(acquired, mutator_log, trial_start):
    """Reconstructs each resource's value-over-time step function from the
    mutator log (plus the known initial True state at trial_start) and
    checks whether an instant exists at which every resource simultaneously
    held the value the acquisition returned for it."""
    per_resource_events = {r: [(trial_start, True)] for r in RESOURCES}
    for (t, r, val, rv) in mutator_log:
        per_resource_events[r].append((t, val))
    for r in RESOURCES:
        per_resource_events[r].sort()

    def value_at(r, t):
        events = per_resource_events[r]
        v = events[0][1]
        for (et, ev) in events:
            if et <= t:
                v = ev
            else:
                break
        return v

    def intervals_for(r, wanted_value):
        events = per_resource_events[r]
        out = []
        for i in range(len(events)):
            start = events[i][0]
            end = events[i + 1][0] if i + 1 < len(events) else float("inf")
            if events[i][1] == wanted_value:
                out.append((start, end))
        return out

    interval_sets = []
    for r in RESOURCES:
        wanted = acquired[r]["value"]
        interval_sets.append(intervals_for(r, wanted))

    current = interval_sets[0]
    for nxt in interval_sets[1:]:
        merged = []
        for (s1, e1) in current:
            for (s2, e2) in nxt:
                lo, hi = max(s1, s2), min(e1, e2)
                if lo < hi:
                    merged.append((lo, hi))
        current = merged
        if not current:
            return False
    return len(current) > 0


def main():
    protocol = verify_protocol_hash()
    print("Protocol hash verified OK.")
    rng = random.Random(protocol["random_seed"])

    out_dir = ROOT / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "raw_trials.jsonl"

    completed_trials = set()
    all_rows = []
    if raw_path.exists():
        for line in open(raw_path, encoding="utf-8"):
            r = json.loads(line)
            all_rows.append(r)
            completed_trials.add((r["method"], r["trial"]))
        print(f"RESUMING: {len(all_rows)} existing rows, "
              f"{len(completed_trials)} (method,trial) pairs already attempted: skipping those.")

    window_s = protocol["mutator"]["trial_window_s"]
    attempts_per_trial = protocol["attempts_per_method_per_trial"]
    n_trials = protocol["trials_per_method"]
    max_retries = protocol["fenced_max_retries"]

    with open(raw_path, "a", encoding="utf-8") as f:
        for method in protocol["acquisition_methods"]:
            for trial in range(1, n_trials + 1):
                if (method, trial) in completed_trials:
                    print(f"SKIP (already attempted): {method} trial {trial}")
                    continue
                reset_all_true()
                time.sleep(0.05)
                mutator = Mutator(rng, inter_tick_delay_s=protocol["mutator"]["inter_tick_delay_s"])
                trial_start = time.time()
                mutator.start()

                attempts = []
                deadline = trial_start + window_s
                i = 0
                while time.time() < deadline and i < attempts_per_trial * 3:
                    i += 1
                    if method == "naive_sequential":
                        acquired, latency_ms, meta = naive_sequential_acquire()
                    else:
                        acquired, latency_ms, meta = fenced_double_collect_acquire(max_retries)
                    attempts.append((time.time(), acquired, latency_ms, meta))
                    if len(attempts) >= attempts_per_trial:
                        break

                mutator.stop()
                mlog = mutator.snapshot_log()

                for (attempt_time, acquired, latency_ms, meta) in attempts:
                    if acquired is None:
                        coherent = None  # UNKNOWN_COHERENCE, not delivered, cannot be incoherent
                    else:
                        coherent = check_coherence(acquired, mlog, trial_start)
                    row = {
                        "method": method, "trial": trial,
                        "outcome": meta["outcome"], "retries": meta["retries"],
                        "latency_ms": latency_ms,
                        "acquired": {r: acquired[r]["value"] for r in RESOURCES} if acquired else None,
                        "coherent": coherent,
                        "mutator_toggles_in_trial": len(mlog),
                    }
                    all_rows.append(row)
                    f.write(json.dumps(row) + "\n")
                    f.flush()
                print(f"{method} trial {trial}/{n_trials}: {len(attempts)} attempts, "
                      f"{len(mlog)} mutator toggles, "
                      f"incoherent={sum(r['coherent'] is False for r in all_rows if r['trial']==trial and r['method']==method)}")

    reset_all_true()

    summary = {}
    for method in protocol["acquisition_methods"]:
        rows = [r for r in all_rows if r["method"] == method]
        delivered = [r for r in rows if r["outcome"] == "delivered"]
        incoherent = sum(r["coherent"] is False for r in delivered)
        unknown = sum(r["outcome"] == "UNKNOWN_COHERENCE" for r in rows)
        summary[method] = {
            "n_attempts": len(rows),
            "n_delivered": len(delivered),
            "n_incoherent_delivered": incoherent,
            "incoherent_rate_of_delivered": incoherent / len(delivered) if delivered else None,
            "n_unknown_coherence": unknown,
            "mean_retries": sum(r["retries"] for r in rows) / len(rows) if rows else None,
            "max_retries": max((r["retries"] for r in rows), default=None),
            "mean_latency_ms": sum(r["latency_ms"] for r in rows if r["latency_ms"] is not None) /
                                max(1, sum(1 for r in rows if r["latency_ms"] is not None)),
        }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print("\nSUMMARY:", json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
