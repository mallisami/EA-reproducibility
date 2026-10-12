#!/usr/bin/env python3
"""DCJI vs FullRefresh physical-latency re-analysis (no new experiments).

Inputs are read-only from inputs/frozen. Outputs go to out/latency.
Delta L (dL) = dcji_refresh_ms - full_refresh_ms  (negative => DCJI faster).
"""
import json, os, sys, hashlib, time
import numpy as np
import pandas as pd
from scipy import stats

BASE = os.path.expanduser("~/dcji_reanalysis_20260926")
IN = os.path.join(BASE, "inputs/frozen")
OUT = os.path.join(BASE, "out/latency")
os.makedirs(OUT, exist_ok=True)
SEED = 20260926
NBOOT = 10000
T0 = time.time()

DS = {
    "E21": "03_extended_study_v9/e21_physical_cost/results",
    "E37": "20_external_host_validation/E37_full_e21_independent_host/results",
    "E35": "20_external_host_validation/E35_independent_host_e21_replication/results",
    "E26": "03_extended_study_v9/e26_independent_cluster_replication/results",
    "E25": "03_extended_study_v9/e25_physical_multicut/results",
}
V25 = os.path.join(IN, "18_revision_v25/E21_GUARD_ORDER_SENSITIVITY_V25.csv")


def log(*a):
    print(f"[{time.time()-T0:7.1f}s]", *a, flush=True)


# ---------------------------------------------------------------- statistics
def pct(a):
    return float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))


def hl(x):
    x = np.asarray(x, float)
    n = len(x)
    i, j = np.triu_indices(n)  # includes i==j (Walsh averages incl. singletons)
    return float(np.median((x[i] + x[j]) * 0.5))


def boot(x, do_hl=True, seed=SEED, nboot=NBOOT):
    """Percentile bootstrap; same resample indices for mean, median, HL.
    A fresh generator seeded with `seed` is used for every call, so each
    cell's CI is reproducible independently of processing order."""
    x = np.asarray(x, float)
    n = len(x)
    rng = np.random.default_rng(seed)
    means = np.empty(nboot); meds = np.empty(nboot)
    hls = np.empty(nboot) if (do_hl and n <= 1000) else None
    if hls is not None:
        I, J = np.triu_indices(n)
    chunk = max(1, min(1000, 2_000_000 // max(n, 1)))
    b = 0
    while b < nboot:
        m = min(chunk, nboot - b)
        X = x[rng.integers(0, n, size=(m, n))]
        means[b:b + m] = X.mean(1)
        meds[b:b + m] = np.median(X, 1)
        if hls is not None:
            for s in range(0, m, 50):
                Xs = X[s:s + 50]
                hls[b + s:b + s + len(Xs)] = np.median((Xs[:, I] + Xs[:, J]) * 0.5, 1)
        b += m
    r = {"mean_lo": pct(means)[0], "mean_hi": pct(means)[1],
         "median_lo": pct(meds)[0], "median_hi": pct(meds)[1]}
    if hls is not None:
        r["hl_lo"], r["hl_hi"] = pct(hls)
    else:
        r["hl_lo"] = r["hl_hi"] = np.nan
    return r


def summ(x, do_boot=True, do_hl_ci=True):
    x = np.asarray(x, float)
    n = len(x)
    r = {"n": n, "mean": x.mean() if n else np.nan, "median": np.median(x) if n else np.nan,
         "hl": hl(x) if 0 < n <= 8000 else np.nan,
         "trim10_mean": stats.trim_mean(x, 0.1) if n else np.nan,
         "n_reversed_gt0": int((x > 0).sum()), "pct_reversed": 100 * (x > 0).mean() if n else np.nan,
         "n_ties_eq0": int((x == 0).sum())}
    if do_boot and n > 1:
        r.update(boot(x, do_hl=do_hl_ci))
    return r


def fmt(v, d=3):
    if isinstance(v, (float, np.floating)):
        if np.isnan(v):
            return "NA"
        return f"{v:.{d}f}"
    return str(v)


# ---------------------------------------------------------------- loading
def load_trials(ds):
    rows = [json.loads(l) for l in open(os.path.join(IN, DS[ds], "raw_trials.jsonl"), encoding="utf-8")]
    df = pd.DataFrame(rows)
    df["dataset"] = ds
    if ds in ("E21", "E37"):
        df["cell"] = df["load"] + "/" + df["mode"] + "/" + df["condition"]
    else:
        df["cell"] = "pooled(all conditions)"
        if "load" not in df:
            df["load"] = "idle(no bg load)"
        if "mode" not in df:
            df["mode"] = "sequential"
    if ds == "E25":
        df["dL"] = df["dcji_latency_ms"] - df["full_latency_ms"]
    else:
        df["dL"] = df["dcji_refresh_ms"] - df["full_refresh_ms"]
    return df


def load_req(ds):
    p = os.path.join(IN, DS[ds], "per_request_log.csv")
    if not os.path.exists(p):
        return None
    r = pd.read_csv(p)
    r["guard"] = r["guard"].replace({"FullRefresh": "FULL", "FULL_REFRESH": "FULL"})
    r["pos_in_guard"] = r.groupby(["trial_id", "guard"]).cumcount() + 1
    return r


T = {ds: load_trials(ds) for ds in DS}
R = {ds: load_req(ds) for ds in ("E21", "E37", "E35")}
CELLS = {}
notes = []

# integrity checks + guard-level GET aggregates
for ds, r in R.items():
    t = T[ds]
    cnt = r.groupby(["trial_id", "guard"]).size().unstack()
    notes.append(f"{ds}: per_request rows={len(r)}, trials in log={cnt.shape[0]}, trials in raw={len(t)}, "
                 f"DCJI GETs/trial set={sorted(cnt['DCJI'].unique().tolist())}, FULL GETs/trial set={sorted(cnt['FULL'].unique().tolist())}, "
                 f"non-200 statuses={int((r['status']!=200).sum())}")
    agg = r.groupby(["trial_id", "guard"])["latency_ms"].agg(["sum", "max"]).unstack()
    agg.columns = [f"{g.lower()}_get_{s}" for s, g in agg.columns]
    anyx = r.groupby("trial_id")["latency_ms"].max().rename("max_get_any")
    bdf = r[(r.guard == "FULL") & r.resource.isin(["obs-b", "obs-d", "obs-f"])].groupby("trial_id")["latency_ms"].sum().rename("full_get_sum_bdf")
    ace = r[(r.guard == "FULL") & r.resource.isin(["obs-a", "obs-c", "obs-e"])].groupby("trial_id")["latency_ms"].sum().rename("full_get_sum_ace")
    t = t.merge(agg, left_on="trial_id", right_index=True, how="left").merge(anyx, left_on="trial_id", right_index=True, how="left")
    t = t.merge(bdf, left_on="trial_id", right_index=True, how="left").merge(ace, left_on="trial_id", right_index=True, how="left")
    seq = t["mode"] == "sequential"
    t["dcji_get_agg"] = np.where(seq, t["dcji_get_sum"], t["dcji_get_max"])
    t["full_get_agg"] = np.where(seq, t["full_get_sum"], t["full_get_max"])
    t["dcji_overhead"] = t["dcji_refresh_ms"] - t["dcji_get_agg"]
    t["full_overhead"] = t["full_refresh_ms"] - t["full_get_agg"]
    t["dL_get"] = t["dcji_get_agg"] - t["full_get_agg"]
    t["dL_overhead"] = t["dL"] - t["dL_get"]
    T[ds] = t
    # attach load/mode to requests
    R[ds] = r.merge(t[["trial_id", "load", "mode", "condition", "cell", "guard_order"]], on="trial_id", how="left")

# ---------------------------------------------------------------- execution order / RNG audit
ex_rows = []
for ds in ("E21", "E37"):
    t = T[ds]
    g = t.groupby("cell")["trial_id"].agg(["min", "max", "count"]).sort_values("min")
    for k, (cell, row) in enumerate(g.iterrows(), 1):
        seqstr = "".join("D" if o == "DCJI_FIRST" else "F" for o in t[t.cell == cell].sort_values("trial_id").guard_order)
        ex_rows.append({"dataset": ds, "exec_order": k, "cell": cell, "first_trial_id": int(row["min"]),
                        "last_trial_id": int(row["max"]), "n": int(row["count"]),
                        "n_DCJI_FIRST": seqstr.count("D"), "guard_order_seq_sha1_12": hashlib.sha1(seqstr.encode()).hexdigest()[:12]})
ex = pd.DataFrame(ex_rows)
e21s = ex[ex.dataset == "E21"].set_index("exec_order")["guard_order_seq_sha1_12"]
e37s = ex[ex.dataset == "E37"].set_index("exec_order")["guard_order_seq_sha1_12"]
ex["same_seq_as_other_dataset_same_position"] = ex.apply(
    lambda r: (e21s.get(r.exec_order) == e37s.get(r.exec_order)), axis=1)
ex["cells_sharing_this_seq_within_dataset"] = ex.apply(
    lambda r: int(((ex.dataset == r.dataset) & (ex.guard_order_seq_sha1_12 == r.guard_order_seq_sha1_12)).sum()), axis=1)
ex.to_csv(os.path.join(OUT, "A0_execution_order_and_rng_audit.csv"), index=False)
# full sequence identity
seq_e21 = "".join(T["E21"].sort_values("trial_id").guard_order.str[0])
seq_e37 = "".join(T["E37"].sort_values("trial_id").guard_order.str[0])
notes.append(f"Guard-order sequence (all 3600 trials) E21 == E37: {seq_e21 == seq_e37}; "
             f"agreement fraction={np.mean([a == b for a, b in zip(seq_e21, seq_e37)]):.4f}")
log("A0 done")

# ---------------------------------------------------------------- A1 per-cell estimators
a1 = []
REUSE = os.environ.get("REUSE_A1A2") == "1"
for ds in (() if REUSE else ("E21", "E37", "E35", "E26")):
    t = T[ds]
    order = t.groupby("cell")["trial_id"].min().sort_values().index
    for cell in order:
        s = t[t.cell == cell]
        r = {"dataset": ds, "cell": cell}
        r.update(summ(s["dL"].values))
        a1.append(r)
        log("A1", ds, cell)
    if ds in ("E21", "E37"):
        r = {"dataset": ds, "cell": "POOLED(12 cells; descriptive only)"}
        r.update(summ(t["dL"].values, do_hl_ci=False))
        a1.append(r)
if not REUSE:
    A1 = pd.DataFrame(a1)
    A1.to_csv(os.path.join(OUT, "A1_cell_estimators.csv"), index=False)

# ---------------------------------------------------------------- A2 stall sensitivity (POST-HOC)
a2 = []
for ds in (() if REUSE else ("E21", "E37", "E35")):
    t = T[ds]
    order = t.groupby("cell")["trial_id"].min().sort_values().index
    for thr in (100, 250, 1000):
        for cell in list(order) + (["POOLED"] if ds != "E35" else []):
            s = t if cell == "POOLED" else t[t.cell == cell]
            keep = s["max_get_any"] <= thr
            x = s.loc[keep, "dL"].values
            r = {"dataset": ds, "threshold_ms": thr, "cell": cell, "n_total": len(s), "n_excluded": int((~keep).sum())}
            sm = summ(x, do_boot=(cell != "POOLED"), do_hl_ci=(thr == 250))
            sm["n_kept"] = sm.pop("n")
            r.update(sm)
            a2.append(r)
        log("A2", ds, thr)
if not REUSE:
    A2 = pd.DataFrame(a2)
    A2.to_csv(os.path.join(OUT, "A2_stall_sensitivity_POSTHOC.csv"), index=False)

# ---------------------------------------------------------------- A3 overhead decomposition
a3a = []
for ds in ("E21", "E37", "E35"):
    t = T[ds]
    for (load, mode), s in t.groupby(["load", "mode"]):
        for guard, col in (("DCJI", "dcji_overhead"), ("FULL", "full_overhead")):
            x = s[col].values
            a3a.append({"dataset": ds, "load": load, "mode": mode, "guard": guard, "n": len(x),
                        "overhead_median_ms": np.median(x), "overhead_mean_ms": x.mean(),
                        "overhead_p90_ms": np.percentile(x, 90), "overhead_p99_ms": np.percentile(x, 99),
                        "overhead_max_ms": x.max(), "overhead_min_ms": x.min()})
A3a = pd.DataFrame(a3a)
A3a.to_csv(os.path.join(OUT, "A3a_harness_overhead_by_mode_guard.csv"), index=False)

a3b = []
for ds in ("E21", "E37", "E35"):
    t = T[ds]
    order = t.groupby("cell")["trial_id"].min().sort_values().index
    groups = [(c, t[t.cell == c]) for c in order]
    if ds in ("E21", "E37"):
        for (load, mode), s in t.groupby(["load", "mode"]):
            groups.append((f"POOLED {load}/{mode}", s))
    for cell, s in groups:
        tot = s["dL"].values; get = s["dL_get"].values
        b = boot(get, do_hl=False) if not cell.startswith("POOLED") else {}
        r = {"dataset": ds, "cell": cell, "n": len(s),
             "dL_total_mean": tot.mean(), "dL_total_median": np.median(tot), "dL_total_hl": hl(tot) if len(tot) <= 1000 else np.nan,
             "dL_get_mean": get.mean(), "dL_get_median": np.median(get), "dL_get_hl": hl(get) if len(get) <= 1000 else np.nan,
             "dL_get_mean_lo": b.get("mean_lo", np.nan), "dL_get_mean_hi": b.get("mean_hi", np.nan),
             "dL_get_median_lo": b.get("median_lo", np.nan), "dL_get_median_hi": b.get("median_hi", np.nan),
             "dL_overhead_mean": s["dL_overhead"].mean(), "dL_overhead_median": s["dL_overhead"].median(),
             "overhead_share_of_mean_saving": (tot.mean() - get.mean()) / tot.mean(),
             "overhead_share_of_median_saving": (np.median(tot) - np.median(get)) / np.median(tot),
             "n_reversed_get_only": int((get > 0).sum())}
        a3b.append(r)
    log("A3", ds)
A3b = pd.DataFrame(a3b)
A3b.to_csv(os.path.join(OUT, "A3b_get_only_deltaL_and_overhead_share.csv"), index=False)

# ---------------------------------------------------------------- A4 guard-order strata
a4 = []
for ds in ("E21", "E37", "E35", "E26"):
    t = T[ds]
    order = t.groupby("cell")["trial_id"].min().sort_values().index
    for cell in order:
        for go in ("DCJI_FIRST", "FULL_FIRST"):
            s = t[(t.cell == cell) & (t.guard_order == go)]
            r = {"dataset": ds, "cell": cell, "guard_order": go}
            sm = summ(s["dL"].values, do_hl_ci=False)
            r.update({k: sm[k] for k in ("n", "mean", "median", "hl", "trim10_mean", "n_reversed_gt0", "pct_reversed",
                                         "mean_lo", "mean_hi", "median_lo", "median_hi")})
            r["mean_ci_spans_zero"] = r["mean_lo"] <= 0 <= r["mean_hi"]
            r["median_ci_spans_zero"] = r["median_lo"] <= 0 <= r["median_hi"]
            r["mean_positive"] = r["mean"] > 0
            r["median_positive"] = r["median"] > 0
            a4.append(r)
    log("A4", ds)
A4 = pd.DataFrame(a4)
v25 = pd.read_csv(V25)
v25 = v25[v25.scope == "cell_order"].copy()
v25["cell"] = v25["load"] + "/" + v25["mode"] + "/" + v25["condition"]
v25 = v25.rename(columns={"n": "v25_n", "mean_delta_refresh_ms": "v25_mean", "bootstrap_lo_ms": "v25_lo", "bootstrap_hi_ms": "v25_hi"})
A4 = A4.merge(v25[["cell", "guard_order", "v25_n", "v25_mean", "v25_lo", "v25_hi"]].assign(dataset="E21"),
              on=["dataset", "cell", "guard_order"], how="left")
A4["v25_mean_absdiff"] = (A4["mean"] - A4["v25_mean"]).abs()
A4["v25_ci_spans_zero"] = np.where(A4.v25_mean.isna(), "", ((A4["v25_lo"] <= 0) & (0 <= A4["v25_hi"])).astype(str))
A4.to_csv(os.path.join(OUT, "A4_guard_order_strata.csv"), index=False)
# order effect per cell
a4b = []
for ds in ("E21", "E37", "E35", "E26"):
    for cell, s in A4[A4.dataset == ds].groupby("cell", sort=False):
        d = s.set_index("guard_order")
        a4b.append({"dataset": ds, "cell": cell,
                    "mean_DCJI_FIRST_minus_FULL_FIRST": d.loc["DCJI_FIRST", "mean"] - d.loc["FULL_FIRST", "mean"],
                    "median_DCJI_FIRST_minus_FULL_FIRST": d.loc["DCJI_FIRST", "median"] - d.loc["FULL_FIRST", "median"]})
pd.DataFrame(a4b).to_csv(os.path.join(OUT, "A4b_guard_order_effect_by_cell.csv"), index=False)

# ---------------------------------------------------------------- A5 per-GET tables + time blocks
def get_stats(x):
    x = np.asarray(x, float)
    return {"n": len(x), "median": np.median(x), "mean": x.mean(), "p90": np.percentile(x, 90),
            "p99": np.percentile(x, 99), "max": x.max(), "n_gt250": int((x > 250).sum()), "n_gt1000": int((x > 1000).sum())}


a5 = []
for ds, r in R.items():
    for (load, mode, guard), s in r.groupby(["load", "mode", "guard"]):
        d = {"dataset": ds, "load": load, "mode": mode, "guard": guard}
        d.update(get_stats(s["latency_ms"]))
        a5.append(d)
    d = {"dataset": ds, "load": "ALL", "mode": "ALL", "guard": "ALL"}
    d.update(get_stats(r["latency_ms"]))
    a5.append(d)
pd.DataFrame(a5).to_csv(os.path.join(OUT, "A5a_per_get_latency.csv"), index=False)

# per-GET by guard position within trial (guard ran first vs second)
a5p = []
for ds, r in R.items():
    r = r.copy()
    r["guard_ran"] = np.where(((r.guard == "DCJI") & (r.guard_order == "DCJI_FIRST")) | ((r.guard == "FULL") & (r.guard_order == "FULL_FIRST")), "first", "second")
    for (load, mode, guard, ran), s in r.groupby(["load", "mode", "guard", "guard_ran"]):
        d = {"dataset": ds, "load": load, "mode": mode, "guard": guard, "guard_ran": ran}
        d.update(get_stats(s["latency_ms"]))
        a5p.append(d)
    if ds in ("E21", "E37"):
        seqr = r[r["mode"] == "sequential"]
        for (guard, pos), s in seqr.groupby(["guard", "pos_in_guard"]):
            d = {"dataset": ds, "load": "ALL", "mode": "sequential", "guard": guard, "guard_ran": f"pos_in_guard={pos}"}
            d.update(get_stats(s["latency_ms"]))
            a5p.append(d)
pd.DataFrame(a5p).to_csv(os.path.join(OUT, "A5b_per_get_latency_by_position.csv"), index=False)

a5c = []
for ds in ("E21", "E37"):
    r = R[ds]; t = T[ds]
    r = r.assign(block=(r.trial_id - 1) // 100 + 1)
    t = t.assign(block=(t.trial_id - 1) // 100 + 1)
    for blk, s in r.groupby("block"):
        tt = t[t.block == blk]
        d = {"dataset": ds, "block100": blk, "trial_ids": f"{s.trial_id.min()}-{s.trial_id.max()}",
             "cell": "|".join(sorted(tt.cell.unique())), "get_median": s.latency_ms.median(), "get_mean": s.latency_ms.mean(),
             "get_p99": s.latency_ms.quantile(.99), "get_max": s.latency_ms.max(),
             "n_gt250": int((s.latency_ms > 250).sum()), "n_gt1000": int((s.latency_ms > 1000).sum()),
             "dL_median": tt.dL.median(), "dL_mean": tt.dL.mean()}
        a5c.append(d)
pd.DataFrame(a5c).to_csv(os.path.join(OUT, "A5c_per_get_latency_time_blocks.csv"), index=False)
log("A5 done")

# ---------------------------------------------------------------- A6 cost model
# (a) sequential
a6a = []
for ds in ("E21", "E37", "E35"):
    t = T[ds]; r = R[ds]
    order = t.groupby("cell")["trial_id"].min().sort_values().index
    for cell in order:
        s = t[(t.cell == cell)]
        if s["mode"].iloc[0] != "sequential":
            continue
        rr = r[r.cell == cell]
        a6a.append({"dataset": ds, "cell": cell, "n": len(s),
                    "pred_extra3_mean_ms": -s["full_get_sum_bdf"].mean(),
                    "pred_3x_mean_perGET_ms": -3 * rr.latency_ms.mean(),
                    "pred_3x_median_perGET_ms": -3 * rr.latency_ms.median(),
                    "obs_dL_mean": s.dL.mean(), "obs_dL_median": s.dL.median(), "obs_dL_hl": hl(s.dL.values),
                    "obs_dL_get_mean": s.dL_get.mean(), "obs_dL_get_median": s.dL_get.median(),
                    "shared_ace_diff_mean(DCJI_ace-FULL_ace)": (s.dcji_get_sum - s.full_get_sum_ace).mean(),
                    "shared_ace_diff_median": (s.dcji_get_sum - s.full_get_sum_ace).median(),
                    "overhead_diff_mean": s.dL_overhead.mean(),
                    "obs_mean_minus_pred_extra3": s.dL.mean() + s["full_get_sum_bdf"].mean(),
                    "obs_median_div_pred_3xmedian": s.dL.median() / (-3 * rr.latency_ms.median())})
A6a = pd.DataFrame(a6a)
A6a.to_csv(os.path.join(OUT, "A6a_costmodel_sequential.csv"), index=False)

# (b) parallel
NDRAW = 100_000


def max_sim(pool, seed=SEED, ndraw=NDRAW):
    rng = np.random.default_rng(seed)
    D = rng.choice(np.asarray(pool, float), size=(ndraw, 6), replace=True)
    m6 = D.max(1); m3 = D[:, :3].max(1)
    return m6, m3


a6b = []
for ds in ("E21", "E37"):
    t = T[ds]; r = R[ds]
    for load in ("idle", "moderate"):
        par = t[(t.load == load) & (t["mode"] == "parallel")]
        for src in ("sequential", "parallel"):
            pool = r[(r.load == load) & (r["mode"] == src)].latency_ms.values
            m6, m3 = max_sim(pool)
            a6b.append({"dataset": ds, "load": load, "perGET_source_mode": src, "n_pool": len(pool),
                        "pool_median": np.median(pool),
                        "E_max6": m6.mean(), "E_max3": m3.mean(), "pred_saving_mean": m6.mean() - m3.mean(),
                        "median_max6": np.median(m6), "median_max3": np.median(m3),
                        "pred_saving_median": np.median(m6) - np.median(m3),
                        "obs_par_n": len(par),
                        "obs_saving_get_mean": -par.dL_get.mean(), "obs_saving_get_median": -par.dL_get.median(),
                        "obs_saving_total_mean": -par.dL.mean(), "obs_saving_total_median": -par.dL.median(),
                        "obs_saving_total_hl": -hl(par.dL.values) if len(par) <= 1000 else np.nan,
                        "obs_FULL_get_max_mean": par.full_get_max.mean(), "obs_DCJI_get_max_mean": par.dcji_get_max.mean()})
A6b = pd.DataFrame(a6b)
A6b.to_csv(os.path.join(OUT, "A6b_costmodel_parallel.csv"), index=False)

# (c) projection
shapes = {}
for ds, load in (("E37", "idle"), ("E37", "moderate"), ("E21", "idle"), ("E21", "moderate"), ("E35", "idle(no bg load)")):
    r = R[ds]
    pool = r[(r.load == load) & (r["mode"] == "sequential")].latency_ms.values
    X = pool / np.median(pool)
    m6, m3 = max_sim(X)
    shapes[(ds, load)] = {"k_mean": m6.mean() - m3.mean(), "Emax6": m6.mean(), "Emax3": m3.mean(),
                          "k_median": np.median(m6) - np.median(m3), "cv": X.std() / X.mean(),
                          "p99_over_median": np.percentile(X, 99)}
pd.DataFrame([dict(dataset=k[0], load=k[1], **v) for k, v in shapes.items()]).to_csv(
    os.path.join(OUT, "A6c_shape_parameters.csv"), index=False)
ovh = A3a.set_index(["dataset", "load", "mode", "guard"])["overhead_median_ms"]
a6c = []
for shape_key in (("E37", "idle"), ("E37", "moderate"), ("E21", "idle")):
    sh = shapes[shape_key]
    o_seq = ovh[(shape_key[0], shape_key[1], "sequential", "FULL")]
    o_par = ovh[(shape_key[0], shape_key[1], "parallel", "FULL")]
    for c in (5, 20, 50, 200, 1000):
        a6c.append({"shape_from": f"{shape_key[0]}/{shape_key[1]} sequential per-GET", "c_ms(median per-GET)": c,
                    "seq_saving_ms(3c)": 3 * c, "seq_FULL_time_ms(6c)": 6 * c, "seq_frac_saved_no_overhead": 0.5,
                    "seq_frac_saved_with_obs_overhead": 3 * c / (6 * c + o_seq), "obs_seq_FULL_overhead_median_ms": o_seq,
                    "par_k=(Emax6-Emax3)/median": sh["k_mean"], "par_saving_ms(c*k)": c * sh["k_mean"],
                    "par_FULL_time_ms(c*Emax6/median)": c * sh["Emax6"],
                    "par_frac_saved_no_overhead": sh["k_mean"] / sh["Emax6"],
                    "par_frac_saved_with_obs_overhead": c * sh["k_mean"] / (c * sh["Emax6"] + o_par),
                    "obs_par_FULL_overhead_median_ms": o_par,
                    "par_saving_ms_median_based(c*k_median)": c * sh["k_median"]})
A6c = pd.DataFrame(a6c)
A6c.to_csv(os.path.join(OUT, "A6c_projection_MODEL_BASED.csv"), index=False)
# (d) empirical parallel saving expressed in units of the sequential per-GET median
a6d = []
for ds in ("E21", "E37"):
    t = T[ds]; r = R[ds]
    for load in ("idle", "moderate"):
        med = r[(r.load == load) & (r["mode"] == "sequential")].latency_ms.median()
        par = t[(t.load == load) & (t["mode"] == "parallel")]
        sq = t[(t.load == load) & (t["mode"] == "sequential")]
        a6d.append({"dataset": ds, "load": load, "seq_perGET_median": med,
                    "k_iid_orderstat(from A6c)": shapes[(ds, load)]["k_mean"],
                    "k_emp_parallel_total_median": -par.dL.median() / med,
                    "k_emp_parallel_getonly_median": -par.dL_get.median() / med,
                    "k_emp_sequential_total_median": -sq.dL.median() / med,
                    "par_FULL_perGET_median": r[(r.load == load) & (r["mode"] == "parallel") & (r.guard == "FULL")].latency_ms.median(),
                    "par_DCJI_perGET_median": r[(r.load == load) & (r["mode"] == "parallel") & (r.guard == "DCJI")].latency_ms.median()})
pd.DataFrame(a6d).to_csv(os.path.join(OUT, "A6d_empirical_k_vs_model.csv"), index=False)
log("A6 done")

# ---------------------------------------------------------------- A7 E25 position effect
t = T["E25"]
a7 = []
for cond in ["ALL"] + list(dict.fromkeys(t.condition)):
    s = t if cond == "ALL" else t[t.condition == cond]
    r = {"dataset": "E25", "condition": cond}
    r.update(summ(s.dL.values))
    a7.append(r)
pd.DataFrame(a7).to_csv(os.path.join(OUT, "A7_E25_position_effect.csv"), index=False)
log("A7 done")

# ---------------------------------------------------------------- extra verification facts
facts = {}
for ds in ("E21", "E37", "E35"):
    r = R[ds]
    facts[ds] = {"n_get_gt1000": int((r.latency_ms > 1000).sum()), "n_get_gt250": int((r.latency_ms > 250).sum()),
                 "max_get_ms": float(r.latency_ms.max()),
                 "n_trials_any_get_gt250": int((T[ds].max_get_any > 250).sum()),
                 "n_trials_any_get_gt1000": int((T[ds].max_get_any > 1000).sum()),
                 "n_reversed_total": int((T[ds].dL > 0).sum()), "n_trials": len(T[ds]),
                 "cell_mean_min": float(T[ds].groupby("cell").dL.mean().min()),
                 "cell_mean_max": float(T[ds].groupby("cell").dL.mean().max()),
                 "n_trials_refresh_gt1000_but_no_get_gt1000": int((((T[ds].dcji_refresh_ms > 1000) | (T[ds].full_refresh_ms > 1000)) & (T[ds].max_get_any <= 1000)).sum()),
                 "max_overhead_ms": float(max(T[ds].dcji_overhead.max(), T[ds].full_overhead.max()))}
for ds in ("E26", "E25"):
    facts[ds] = {"n_reversed_total": int((T[ds].dL > 0).sum()), "n_trials": len(T[ds])}
facts["notes"] = notes
facts["fields"] = {ds: list(T[ds].columns) for ds in T}
json.dump(facts, open(os.path.join(OUT, "A0_facts.json"), "w"), indent=2, default=float)
log("ALL DONE")
