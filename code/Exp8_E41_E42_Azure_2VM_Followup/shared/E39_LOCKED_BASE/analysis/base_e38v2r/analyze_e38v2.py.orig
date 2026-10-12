#!/usr/bin/env python3
"""E38v2 pre-registered analysis (offline). See protocol/ANALYSIS_PLAN_E38v2.txt.

Exit codes: 0 results written; 2 REFUSED by the data-completeness gate (only REFUSED_E38v2.json is
written); 1 usage / unexpected error.

Usage:
  analyze_e38v2.py --run-dir results/<run_id> --lock protocol/LOCK_E38v2.json --stage 1 --out <dir>
  (stage 2: pass every schedule file, e.g. --schedule SCHEDULE.json --schedule SCHEDULE_STAGE2.json)
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import oracle as orc
from e38v2_common import (ARMS, ASYNC_CONDITIONS, CONDITIONS, DELTAS_MS, EXPOSURE_THRESHOLD,
                          FORBIDDEN_SEED, FORBIDDEN_SEEDS, N_TOPUP_TARGET, NOT_ANALYZABLE, OUTCOMES, RESULTS_SCHEMA,
                          SCHEDULE_SCHEMA, SPEC_VERSION, TRIAL_SCHEMA, cell_sort_key, delta_label,
                          deltas_equal, exposure_d_star, load_jsonl, norm_delta, sha256_file,
                          stage1_design_cells, wilson)
from stage2_topup import topup_from_cells

ANALYSIS_VERSION = "e38v2.analysis.v1.2"
DEFAULT_BOOT_REPS = 2000
DEFAULT_BOOT_SEED = 3802026          # pre-registered; unrelated to the schedule seed
HIGH_MISSINGNESS = 0.10
SCHEDULE_FIELDS = ("stage", "block", "arm", "condition", "delta_ms", "rep")


class Refusal(Exception):
    pass


# ----------------------------------------------------------------------------- schedule / lock
def load_schedule(path):
    """Accepts {"schema": "e38v2.schedule.v1", "run_id", "schedule_seed", "stage"?, "trials": [...]}
    or a bare list of trial entries. Each entry: trial_id, stage, block, arm, condition, delta_ms, rep,
    optional schedule_seed (defaults to the top-level one)."""
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    errs = []
    if isinstance(obj, list):
        top, trials = {}, obj
    elif isinstance(obj, dict):
        top, trials = obj, obj.get("trials")
        if obj.get("schema") not in (None, SCHEDULE_SCHEMA):
            errs.append("%s: schedule schema %r != %s" % (path, obj.get("schema"), SCHEDULE_SCHEMA))
    else:
        return [], None, ["%s: schedule is neither an object nor a list" % path]
    if not isinstance(trials, list):
        return [], None, ["%s: schedule has no 'trials' list" % path]
    out = []
    for i, e in enumerate(trials):
        if not isinstance(e, dict) or "trial_id" not in e:
            errs.append("%s: entry %d malformed" % (path, i))
            continue
        ent = {"trial_id": e["trial_id"],
               "stage": e.get("stage", top.get("stage")),
               "block": e.get("block"), "arm": e.get("arm"), "condition": e.get("condition"),
               "delta_ms": norm_delta(e.get("delta_ms")), "rep": e.get("rep"),
               "schedule_seed": e.get("schedule_seed", top.get("schedule_seed")),
               "_file": str(path)}
        out.append(ent)
    return out, top.get("run_id"), errs


def verify_lock(lock_path, lock_root, schedule_paths, plan_path):
    """Returns (errors, info). LOCK is JSON. Verified entries:
       status must start with 'LOCKED'; files: {path (absolute or relative to lock_root): sha256};
       schedule_sha256 (vs first --schedule file); analysis_plan_sha256 (vs protocol/ANALYSIS_PLAN_E38v2.txt);
       seed: schedule_seed | random_seed; optional stage2_schedule_seed, stage2_schedule_sha256, run_id."""
    errs, info = [], {"verified": [], "unverified_hash_fields": [], "seed": None, "stage2_seed": None}
    try:
        lock = json.loads(Path(lock_path).read_text(encoding="utf-8"))
    except Exception as ex:  # noqa: BLE001
        return ["LOCK unreadable: %s" % ex], info
    if not isinstance(lock, dict):
        return ["LOCK is not a JSON object"], info
    status = str(lock.get("status") or "")
    if not status.upper().startswith("LOCKED"):
        errs.append("LOCK status %r is not LOCKED" % status)
    files = lock.get("files") or lock.get("file_sha256") or {}
    if isinstance(files, list):
        # protocol/lock_hash.py format: [{"path", "sha256", ...}, ...] (+ "pinned" entries)
        conv = {}
        for ent in files + list(lock.get("pinned") or []):
            if not isinstance(ent, dict) or not ent.get("path"):
                errs.append("LOCK files entry malformed: %r" % (ent,))
                continue
            if ent.get("missing") or not ent.get("sha256"):
                errs.append("LOCK records missing file: %s" % ent.get("path"))
                continue
            conv[ent["path"]] = ent["sha256"]
        files = conv
    if not isinstance(files, dict):
        errs.append("LOCK 'files' is not an object")
        files = {}
    for rel, h in sorted(files.items()):
        p = Path(rel) if os.path.isabs(rel) else Path(lock_root) / rel
        if not p.is_file():
            errs.append("LOCK file missing: %s" % rel)
            continue
        got = sha256_file(p)
        if str(h).lower() != got:
            errs.append("LOCK hash mismatch: %s (lock %s, actual %s)" % (rel, h, got))
        else:
            info["verified"].append(rel)
    named = {"schedule_sha256": schedule_paths[0] if schedule_paths else None,
             "stage2_schedule_sha256": schedule_paths[1] if len(schedule_paths) > 1 else None,
             "analysis_plan_sha256": plan_path}
    for key, p in named.items():
        h = lock.get(key)
        if not h:
            continue
        if p is None or not Path(p).is_file():
            errs.append("LOCK %s set but file not available (%s)" % (key, p))
            continue
        got = sha256_file(p)
        if str(h).lower() != got:
            errs.append("LOCK hash mismatch: %s (lock %s, actual %s of %s)" % (key, h, got, p))
        else:
            info["verified"].append(key)
    for k, v in sorted(lock.items()):
        if k.endswith("_sha256") and v and k not in named:
            info["unverified_hash_fields"].append(k)
    if not info["verified"]:
        errs.append("LOCK contains no verifiable hashes (files / schedule_sha256 / analysis_plan_sha256)")
    seed = lock.get("schedule_seed", lock.get("random_seed"))
    if seed is None and isinstance(lock.get("design"), dict):
        seed = lock["design"].get("schedule_seed")
    if seed is None:
        errs.append("LOCK has no schedule_seed/random_seed")
    elif int(seed) in FORBIDDEN_SEEDS:
        errs.append("LOCK seed equals a forbidden reused seed %d" % int(seed))
    info["seed"] = None if seed is None else int(seed)
    s2 = lock.get("stage2_schedule_seed")
    info["stage2_seed"] = None if s2 is None else int(s2)
    info["run_id"] = lock.get("run_id")
    info["status"] = status
    return errs, info


def completeness_gate(trials, raw_errs, schedule_entries, sched_errs, sched_run_ids, lock_errs, lock_info,
                      stage, aux_errs):
    """Structural checks independent of the oracle. Returns (errors, planned, used_trials)."""
    errs = list(sched_errs) + list(lock_errs) + list(aux_errs)
    if raw_errs:
        errs.append("RAW_TRIALS has %d unparseable lines (first: line %d)" % (len(raw_errs), raw_errs[0][0]))
    bad_schema = [t.get("trial_id") for t in trials if t.get("schema") != TRIAL_SCHEMA]
    if bad_schema:
        errs.append("%d RAW records with schema != %s (e.g. trial %s)" % (len(bad_schema), TRIAL_SCHEMA, bad_schema[0]))
    run_ids = {t.get("run_id") for t in trials}
    if len(run_ids) > 1:
        errs.append("RAW contains several run_ids: %s" % sorted(map(str, run_ids)))
    for rid in sched_run_ids:
        if rid is not None and run_ids and rid not in run_ids:
            errs.append("SCHEDULE run_id %r != RAW run_id %s" % (rid, sorted(map(str, run_ids))))
    if lock_info.get("run_id") and run_ids and lock_info["run_id"] not in run_ids:
        errs.append("LOCK run_id %r != RAW run_id" % lock_info["run_id"])
    # planned trials
    planned = {}
    for e in schedule_entries:
        if e["stage"] not in (1, 2):
            errs.append("schedule trial %s has invalid stage %r" % (e["trial_id"], e["stage"]))
            continue
        if e["stage"] > stage:
            continue
        if e["trial_id"] in planned:
            errs.append("schedule lists trial_id %s more than once" % e["trial_id"])
        planned[e["trial_id"]] = e
    if not planned:
        errs.append("schedule contains no planned trials for stage <= %d" % stage)
    # seeds
    for e in planned.values():
        s = e["schedule_seed"]
        if s is None:
            errs.append("schedule trial %s has no schedule_seed" % e["trial_id"])
            break
    seeds_by_stage = defaultdict(set)
    for e in planned.values():
        if e["schedule_seed"] is not None:
            seeds_by_stage[e["stage"]].add(int(e["schedule_seed"]))
    for st, ss in sorted(seeds_by_stage.items()):
        if len(ss) > 1:
            errs.append("stage-%d schedule uses several seeds: %s" % (st, sorted(ss)))
        for fs in FORBIDDEN_SEEDS:
            if fs in ss:
                errs.append("stage-%d schedule seed equals forbidden seed %d" % (st, fs))
        want = lock_info.get("seed") if st == 1 else (lock_info.get("stage2_seed") or lock_info.get("seed"))
        if want is not None and ss and ss != {want}:
            errs.append("stage-%d schedule seed %s != LOCK seed %s" % (st, sorted(ss), want))
    # raw vs schedule
    used = [t for t in trials if t.get("stage") in (1, 2) and t.get("stage") <= stage]
    ignored_stage = [t for t in trials if not (t.get("stage") in (1, 2) and t.get("stage") <= stage)]
    bad_stage = [t for t in ignored_stage if t.get("stage") not in (1, 2)]
    if bad_stage:
        errs.append("%d RAW records with invalid stage" % len(bad_stage))
    ids = Counter(t.get("trial_id") for t in used)
    dups = sorted(str(k) for k, v in ids.items() if v > 1)
    if dups:
        errs.append("duplicate RAW trial_ids: %s" % ", ".join(dups[:10]))
    missing = sorted(set(planned) - set(ids), key=str)
    extra = sorted(set(ids) - set(planned), key=str)
    if missing:
        errs.append("%d planned trials missing from RAW (e.g. %s)" % (len(missing), missing[:10]))
    if extra:
        errs.append("%d RAW trials not in schedule (e.g. %s)" % (len(extra), extra[:10]))
    mism = []
    for t in used:
        e = planned.get(t.get("trial_id"))
        if e is None:
            continue
        for f in SCHEDULE_FIELDS:
            tv, ev = t.get(f), e[f]
            ok = deltas_equal(tv, ev) if f == "delta_ms" else tv == ev
            if not ok:
                mism.append("trial %s field %s raw=%r schedule=%r" % (t.get("trial_id"), f, tv, ev))
        if e["schedule_seed"] is not None and t.get("schedule_seed") != e["schedule_seed"]:
            mism.append("trial %s schedule_seed raw=%r schedule=%r" % (t.get("trial_id"), t.get("schedule_seed"), e["schedule_seed"]))
    if mism:
        errs.append("%d RAW/SCHEDULE mismatches (e.g. %s)" % (len(mism), "; ".join(mism[:5])))
    return errs, planned, used


def planned_cells(planned, stage_filter=None):
    c = Counter()
    for e in planned.values():
        if stage_filter is None or e["stage"] == stage_filter:
            c[(e["arm"], e["condition"], e["delta_ms"])] += 1
    return c


def design_check(planned, oracle_rows, stage):
    """Strict pre-registered design check. Returns (errors, expected_stage2_list or None)."""
    errs = []
    design = stage1_design_cells()
    got1 = planned_cells(planned, 1)
    for cell in sorted(set(design) | set(got1), key=cell_sort_key):
        if got1.get(cell, 0) != design.get(cell, 0):
            errs.append("stage-1 design mismatch in cell %s: planned %d, pre-registered %d"
                        % ((cell[0], cell[1], delta_label(cell[2])), got1.get(cell, 0), design.get(cell, 0)))
            if len(errs) > 20:
                break
    exp2 = None
    if stage == 2:
        rows1 = [r for r in oracle_rows if r["stage"] == 1]
        s1 = summarize_cells(rows1, {k: v for k, v in planned.items() if v["stage"] == 1})
        exp2 = topup_from_cells([{"arm": c["arm"], "condition": c["condition"], "delta_ms": c["delta_ms"],
                                  "n_planned": c["n_planned"], "p_hat": c["primary"]["p_hat"]} for c in s1])
        want = {(r["arm"], r["condition"], r["delta_ms"]): r["n_topup"] for r in exp2}
        got2 = planned_cells(planned, 2)
        for cell in sorted(set(want) | set(got2), key=cell_sort_key):
            if got2.get(cell, 0) != want.get(cell, 0):
                errs.append("stage-2 schedule mismatch vs pre-registered top-up in cell %s: planned %d, rule %d"
                            % ((cell[0], cell[1], delta_label(cell[2])), got2.get(cell, 0), want.get(cell, 0)))
    return errs, exp2


# ----------------------------------------------------------------------------- statistics
def _cls_counts(rows, col):
    c = Counter(r[col] for r in rows)
    return {k: int(c.get(k, 0)) for k in OUTCOMES}


FAIL_CLOSED_REASON = "gate_unknown_coherence"


def is_fail_closed(r, col, reason_col):
    """Gate UNKNOWN_COHERENCE (epoch fence / coherence failure): the DELETE was never dispatched, so the
    trial certainly produced no accepted effect."""
    return r[col] == "UNKNOWN" and r[reason_col] == FAIL_CLOSED_REASON


def _rule_block(rows, col, reason_col):
    """PRIMARY denominator (lead decision, 2026-09-26, recorded in ANALYSIS_PLAN v1.1): all completed,
    classifiable trials of the cell, with fail-closed (fenced, never dispatched) trials counted as
    non-events: n = n_analyzable + n_fail_closed. Other UNKNOWN and all INFRA_ERROR are excluded.
    Sensitivity S2 (the former primary) excludes fail-closed trials: n = n_analyzable."""
    counts = _cls_counts(rows, col)
    n_an = sum(v for k, v in counts.items() if k not in NOT_ANALYZABLE)
    k = counts["INVALID_EXECUTED"]
    nfc = sum(1 for r in rows if is_fail_closed(r, col, reason_col))
    n_prim = n_an + nfc
    p, lo, hi = wilson(k, n_prim)
    pe, loe, hie = wilson(k, n_an)
    nu = counts["UNKNOWN"]
    # S1 worst case: every UNKNOWN that could have executed counted as INVALID_EXECUTED; fail-closed
    # trials stay non-events (they were never dispatched)
    nu_open = nu - nfc
    pu, lou, hiu = wilson(k + nu_open, n_prim + nu_open)
    unk = Counter(r[reason_col] for r in rows if r[col] == "UNKNOWN")
    return {"counts": counts, "n_analyzable": n_an, "n_fail_closed": nfc, "n_primary_denominator": n_prim,
            "k_invalid_executed": k,
            "p_hat": p, "wilson_lo": lo, "wilson_hi": hi,
            "sensitivity_unknown_as_invalid": {"k": k + nu_open, "n": n_prim + nu_open, "p_hat": pu, "wilson_lo": lou, "wilson_hi": hiu},
            "sensitivity_exclude_failclosed": {"k": k, "n": n_an, "n_fail_closed_excluded": nfc,
                                               "p_hat": pe, "wilson_lo": loe, "wilson_hi": hie},
            "unknown_reasons": dict(sorted(unk.items()))}


def summarize_cells(rows, planned):
    by = defaultdict(list)
    for r in rows:
        by[(r["arm"], r["condition"], norm_delta(r["delta_ms"]) if r["delta_ms"] != "" else None)].append(r)
    pc = planned_cells(planned)
    pc1 = planned_cells(planned, 1)
    out = []
    for cell in sorted(set(pc) | set(by), key=cell_sort_key):
        rs = by.get(cell, [])
        prim = _rule_block(rs, "primary_class", "primary_reason")
        sec = _rule_block(rs, "secondary_class", "secondary_reason")
        ter = _rule_block(rs, "tertiary_class", "tertiary_reason")
        both = [r for r in rs if r["rules_agree"] != "NA"]
        n_agree = sum(1 for r in both if r["rules_agree"] == 1)
        both_t = [r for r in rs if r["rules_agree_tertiary"] != "NA"]
        n_agree_t = sum(1 for r in both_t if r["rules_agree_tertiary"] == 1)
        rs1 = [r for r in rs if r["stage"] == 1]
        s1 = _rule_block(rs1, "primary_class", "primary_reason")
        n_obs = len(rs)
        # fail-closed trials are in the primary denominator (non-events), so they are not missing
        miss = (prim["counts"]["UNKNOWN"] - prim["n_fail_closed"] + prim["counts"]["INFRA_ERROR"]) / n_obs if n_obs else None
        out.append({"arm": cell[0], "condition": cell[1], "delta_ms": cell[2],
                    "n_planned": int(pc.get(cell, 0)), "n_planned_stage1": int(pc1.get(cell, 0)),
                    "n_observed": n_obs, "primary": prim, "secondary": sec, "tertiary": ter,
                    "stage1_primary": {"k": s1["k_invalid_executed"], "n_analyzable": s1["n_analyzable"],
                                       "n_primary_denominator": s1["n_primary_denominator"],
                                       "p_hat": s1["p_hat"], "wilson_lo": s1["wilson_lo"], "wilson_hi": s1["wilson_hi"]},
                    "agreement": {"n_both_classified": len(both), "n_agree": n_agree,
                                  "prop": (n_agree / len(both)) if both else None},
                    "agreement_tertiary": {"n_both_classified": len(both_t), "n_agree": n_agree_t,
                                           "prop": (n_agree_t / len(both_t)) if both_t else None},
                    "missingness": miss,
                    "quality_flag": "HIGH_MISSINGNESS" if (miss is not None and miss > HIGH_MISSINGNESS) else ""})
    return out


def _order_stat_ci(vals, alpha=0.05):
    v = np.sort(np.asarray(vals, dtype=float))
    b = len(v)
    lo = v[max(0, int(math.floor(alpha / 2 * b)))]
    hi = v[min(b - 1, int(math.ceil((1 - alpha / 2) * b)) - 1)]
    f = lambda x: None if math.isinf(x) else float(x)
    return f(lo), f(hi)


def exposure_windows(cells, rows, reps, seed):
    by = defaultdict(list)
    for r in rows:
        if r["condition"] not in ASYNC_CONDITIONS:
            continue
        # primary population: analyzable trials plus fail-closed trials as non-events (0)
        if r["primary_class"] not in NOT_ANALYZABLE or is_fail_closed(r, "primary_class", "primary_reason"):
            by[(r["arm"], r["condition"], norm_delta(r["delta_ms"]))].append(1 if r["primary_class"] == "INVALID_EXECUTED" else 0)
    cmap = {(c["arm"], c["condition"], c["delta_ms"]): c for c in cells}
    out = []
    for ai, arm in enumerate(ARMS):
        for ci, cond in enumerate(CONDITIONS):
            if cond not in ASYNC_CONDITIONS:
                continue
            deltas = [d for d in DELTAS_MS if (arm, cond, d) in cmap]
            if not deltas:
                continue
            ph = [cmap[(arm, cond, d)]["primary"]["p_hat"] for d in deltas]
            hi = [cmap[(arm, cond, d)]["primary"]["wilson_hi"] for d in deltas]
            ph2 = [cmap[(arm, cond, d)]["secondary"]["p_hat"] for d in deltas]
            ph3 = [cmap[(arm, cond, d)]["tertiary"]["p_hat"] for d in deltas]
            d_star, last_exp = exposure_d_star(deltas, ph)
            d_star2, _ = exposure_d_star(deltas, ph2)
            d_star3, _ = exposure_d_star(deltas, ph3)
            hi_at = hi[deltas.index(d_star)] if d_star is not None else None
            max_hi = max(h for d, h in zip(deltas, hi) if d >= d_star) if d_star is not None else None
            # bootstrap: resample analyzable trials within each delta cell (stratified), seeded
            rng = np.random.default_rng([seed, ai, ci])
            mat = np.full((reps, len(deltas)), np.nan)
            for j, d in enumerate(deltas):
                x = np.asarray(by.get((arm, cond, d), []), dtype=float)
                if len(x):
                    idx = rng.integers(0, len(x), size=(reps, len(x)))
                    mat[:, j] = x[idx].mean(axis=1)
            dstars = []
            for b in range(reps):
                row = [None if np.isnan(v) else float(v) for v in mat[b]]
                ds, _ = exposure_d_star(deltas, row)
                dstars.append(math.inf if ds is None else ds)
            dist = Counter("not_closed" if math.isinf(v) else delta_label(v) for v in dstars)
            lo, hi_ci = _order_stat_ci(dstars)
            out.append({"arm": arm, "condition": cond, "threshold": EXPOSURE_THRESHOLD,
                        "d_star_ms": d_star, "closed_within_grid": d_star is not None,
                        "last_exposed_delta_ms": last_exp,
                        "wilson_hi_at_d_star": hi_at, "max_wilson_hi_at_or_above_d_star": max_hi,
                        "d_star_secondary_ms": d_star2, "d_star_tertiary_ms": d_star3,
                        "bootstrap": {"reps": reps, "seed": [seed, ai, ci], "ci95_lo_ms": lo, "ci95_hi_ms": hi_ci,
                                      "ci_hi_not_closed": math.isinf(sorted(dstars)[min(reps - 1, int(math.ceil(0.975 * reps)) - 1)]),
                                      "frac_not_closed": sum(1 for v in dstars if math.isinf(v)) / reps,
                                      "distribution": dict(sorted(dist.items()))}})
    return out


def hodges_lehmann(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    return float(np.median(np.subtract.outer(x, y)))


def webhook_latency(rows, reps, seed):
    def rtts(arm):
        return [float(r["delete_rtt_ms"]) for r in rows
                if r["arm"] == arm and r["condition"] == "no_write" and r["delete_rtt_ms"] != ""
                and str(r["delete_http_status"]) in ("200", "202") and r["primary_class"] not in NOT_ANALYZABLE]
    per_arm = {}
    for arm in ARMS:
        v = rtts(arm)
        per_arm[arm] = {"n": len(v), "median_ms": float(np.median(v)) if v else None,
                        "p05_ms": float(np.percentile(v, 5)) if v else None,
                        "p95_ms": float(np.percentile(v, 95)) if v else None}
    a3, a1 = rtts("A3_dcji_webhook"), rtts("A1_dcji")
    res = {"population": "no_write trials, DELETE dispatched and accepted (HTTP 200/202), analyzable",
           "per_arm": per_arm, "comparison": "A3_dcji_webhook - A1_dcji"}
    if a3 and a1:
        rng = np.random.default_rng([seed, 99])
        x, y = np.asarray(a3), np.asarray(a1)
        boots = np.empty(reps)
        for b in range(reps):
            boots[b] = hodges_lehmann(x[rng.integers(0, len(x), len(x))], y[rng.integers(0, len(y), len(y))])
        lo, hi = _order_stat_ci(boots)
        res.update({"median_difference_ms": float(np.median(x) - np.median(y)),
                    "hodges_lehmann_ms": hodges_lehmann(x, y),
                    "bootstrap": {"reps": reps, "seed": [seed, 99], "hl_ci95_lo_ms": lo, "hl_ci95_hi_ms": hi}})
    else:
        res.update({"median_difference_ms": None, "hodges_lehmann_ms": None, "bootstrap": None,
                    "note": "insufficient data"})
    return res


def availability(cells):
    out = []
    for c in cells:
        if c["condition"] != "no_write":
            continue
        k = c["primary"]["counts"]["FALSE_ABORT"]
        n = c["primary"]["n_analyzable"]
        p, lo, hi = wilson(k, n)
        out.append({"arm": c["arm"], "k_false_abort": k, "n_analyzable": n, "p_hat": p, "wilson_lo": lo, "wilson_hi": hi})
    return out


def _desc(v):
    a = np.asarray(v, float)
    if not len(a):
        return {"n": 0, "mean": None, "sd": None, "median": None, "p05": None, "p95": None, "min": None, "max": None}
    return {"n": int(len(a)), "mean": float(a.mean()), "sd": float(a.std(ddof=1)) if len(a) > 1 else None,
            "median": float(np.median(a)), "p05": float(np.percentile(a, 5)), "p95": float(np.percentile(a, 95)),
            "min": float(a.min()), "max": float(a.max())}


def writer_release(rows):
    by = defaultdict(list)
    real = defaultdict(list)
    for r in rows:
        if r["condition"] in ASYNC_CONDITIONS and r["release_error_us"] != "":
            key = (r["arm"], r["condition"], norm_delta(r["delta_ms"]))
            by[key].append(float(r["release_error_us"]))
            by[(r["arm"], "ALL", "ALL")].append(float(r["release_error_us"]))
            if r["realized_delta_ms"] != "":
                real[key].append(float(r["realized_delta_ms"]))
    out = []
    keys = sorted([k for k in by if k[1] != "ALL"], key=cell_sort_key) + \
        sorted([k for k in by if k[1] == "ALL"], key=lambda k: ARMS.index(k[0]) if k[0] in ARMS else 99)
    for k in keys:
        v = by[k]
        d = _desc(v)
        d.update({"arm": k[0], "condition": k[1], "delta_ms": k[2],
                  "frac_abs_gt_100us": sum(1 for x in v if abs(x) > 100) / len(v),
                  "frac_abs_gt_500us": sum(1 for x in v if abs(x) > 500) / len(v),
                  "realized_delta_median_ms": float(np.median(real[k])) if real.get(k) else None})
        out.append(d)
    return out


def agreement_overall(rows, other="secondary_class", agree_col="rules_agree"):
    both = [r for r in rows if r[agree_col] != "NA"]
    n = len(both)
    agree = sum(1 for r in both if r[agree_col] == 1)
    tab = Counter((r["primary_class"] == "INVALID_EXECUTED", r[other] == "INVALID_EXECUTED") for r in both)
    a, b, c, d = tab[(True, True)], tab[(True, False)], tab[(False, True)], tab[(False, False)]
    kappa = None
    if n:
        po = (a + d) / n
        pe = ((a + b) * (a + c) + (c + d) * (b + d)) / (n * n)
        kappa = None if pe == 1 else (po - pe) / (1 - pe)
    pairs = Counter((r["primary_class"], r[other]) for r in rows)
    return {"compared_with": other, "n_both_classified": n, "n_exact_agree": agree, "prop_exact_agree": (agree / n) if n else None,
            "invalid_executed_2x2": {"both": a, "primary_only": b, "secondary_only": c, "neither": d},
            "kappa_invalid_executed": kappa,
            "crosstab": [{"primary": k[0], "secondary": k[1], "n": v} for k, v in sorted(pairs.items())]}


def uid_control(cells):
    return [{"arm": c["arm"], "n_observed": c["n_observed"], "counts": c["primary"]["counts"]}
            for c in cells if c["condition"] == "uid_rebind_control"]


# ----------------------------------------------------------------------------- output
def _fmt(x):
    if x is None:
        return ""
    if isinstance(x, float):
        return "%.6g" % x
    return x


def write_csv(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: _fmt(r.get(k)) for k in cols})


def write_outputs(outd, res, oracle_rows):
    outd.mkdir(parents=True, exist_ok=True)
    if (outd / "REFUSED_E38v2.json").is_file():
        (outd / "REFUSED_E38v2.json").unlink()
    orc.write_csv(oracle_rows, outd / "oracle_trials_E38v2.csv")
    cols = ["arm", "condition", "delta_ms", "n_planned", "n_observed", "n_analyzable", "n_fail_closed",
            "n_primary_denominator"] + \
        ["n_" + k for k in OUTCOMES] + \
        ["k_invalid_executed", "p_hat", "wilson_lo", "wilson_hi", "p_hat_unknown_as_invalid", "wilson_hi_unknown_as_invalid",
         "p_hat_exclude_failclosed", "wilson_lo_exclude_failclosed", "wilson_hi_exclude_failclosed",
         "sec_n_analyzable", "sec_k_invalid_executed", "sec_p_hat", "sec_wilson_lo", "sec_wilson_hi",
         "stage1_k", "stage1_n_analyzable", "stage1_p_hat", "agree_n_both", "agree_n", "agree_prop",
         "ter_n_analyzable", "ter_k_invalid_executed", "ter_p_hat", "ter_wilson_lo", "ter_wilson_hi",
         "agree_ter_n_both", "agree_ter_n", "agree_ter_prop",
         "missingness", "quality_flag", "unknown_reasons"]
    flat = []
    for c in res["cells"]:
        p, s, t3 = c["primary"], c["secondary"], c["tertiary"]
        r = {"arm": c["arm"], "condition": c["condition"], "delta_ms": delta_label(c["delta_ms"]),
             "n_planned": c["n_planned"], "n_observed": c["n_observed"], "n_analyzable": p["n_analyzable"],
             "k_invalid_executed": p["k_invalid_executed"], "p_hat": p["p_hat"], "wilson_lo": p["wilson_lo"],
             "wilson_hi": p["wilson_hi"], "p_hat_unknown_as_invalid": p["sensitivity_unknown_as_invalid"]["p_hat"],
             "wilson_hi_unknown_as_invalid": p["sensitivity_unknown_as_invalid"]["wilson_hi"],
             "n_fail_closed": p["n_fail_closed"], "n_primary_denominator": p["n_primary_denominator"],
             "p_hat_exclude_failclosed": p["sensitivity_exclude_failclosed"]["p_hat"],
             "wilson_lo_exclude_failclosed": p["sensitivity_exclude_failclosed"]["wilson_lo"],
             "wilson_hi_exclude_failclosed": p["sensitivity_exclude_failclosed"]["wilson_hi"],
             "sec_n_analyzable": s["n_analyzable"], "sec_k_invalid_executed": s["k_invalid_executed"],
             "sec_p_hat": s["p_hat"], "sec_wilson_lo": s["wilson_lo"], "sec_wilson_hi": s["wilson_hi"],
             "stage1_k": c["stage1_primary"]["k"], "stage1_n_analyzable": c["stage1_primary"]["n_analyzable"],
             "stage1_p_hat": c["stage1_primary"]["p_hat"],
             "agree_n_both": c["agreement"]["n_both_classified"], "agree_n": c["agreement"]["n_agree"],
             "agree_prop": c["agreement"]["prop"],
             "ter_n_analyzable": t3["n_analyzable"], "ter_k_invalid_executed": t3["k_invalid_executed"],
             "ter_p_hat": t3["p_hat"], "ter_wilson_lo": t3["wilson_lo"], "ter_wilson_hi": t3["wilson_hi"],
             "agree_ter_n_both": c["agreement_tertiary"]["n_both_classified"],
             "agree_ter_n": c["agreement_tertiary"]["n_agree"], "agree_ter_prop": c["agreement_tertiary"]["prop"],
             "missingness": c["missingness"], "quality_flag": c["quality_flag"],
             "unknown_reasons": ";".join("%s=%d" % kv for kv in p["unknown_reasons"].items())}
        for k in OUTCOMES:
            r["n_" + k] = p["counts"][k]
        flat.append(r)
    write_csv(outd / "cells_E38v2.csv", cols, flat)
    ew = []
    for e in res["exposure_window"]:
        ew.append({"arm": e["arm"], "condition": e["condition"], "threshold": e["threshold"],
                   "d_star_ms": "not_closed" if e["d_star_ms"] is None else delta_label(e["d_star_ms"]),
                   "last_exposed_delta_ms": "" if e["last_exposed_delta_ms"] is None else delta_label(e["last_exposed_delta_ms"]),
                   "wilson_hi_at_d_star": e["wilson_hi_at_d_star"],
                   "max_wilson_hi_at_or_above_d_star": e["max_wilson_hi_at_or_above_d_star"],
                   "d_star_secondary_ms": "not_closed" if e["d_star_secondary_ms"] is None else delta_label(e["d_star_secondary_ms"]),
                   "d_star_tertiary_ms": "not_closed" if e["d_star_tertiary_ms"] is None else delta_label(e["d_star_tertiary_ms"]),
                   "boot_ci95_lo_ms": "not_closed" if e["bootstrap"]["ci95_lo_ms"] is None else delta_label(e["bootstrap"]["ci95_lo_ms"]),
                   "boot_ci95_hi_ms": "not_closed" if e["bootstrap"]["ci95_hi_ms"] is None else delta_label(e["bootstrap"]["ci95_hi_ms"]),
                   "boot_frac_not_closed": e["bootstrap"]["frac_not_closed"], "boot_reps": e["bootstrap"]["reps"]})
    write_csv(outd / "exposure_window_E38v2.csv", list(ew[0].keys()) if ew else ["arm"], ew)
    write_csv(outd / "availability_E38v2.csv", ["arm", "k_false_abort", "n_analyzable", "p_hat", "wilson_lo", "wilson_hi"],
              res["availability_cost"])
    wl = res["webhook_latency_cost"]
    lat = [{"arm": a, **v} for a, v in wl["per_arm"].items()]
    lat.append({"arm": "A3_minus_A1", "n": "", "median_ms": wl.get("median_difference_ms"),
                "hl_ms": wl.get("hodges_lehmann_ms"),
                "hl_ci95_lo_ms": (wl.get("bootstrap") or {}).get("hl_ci95_lo_ms"),
                "hl_ci95_hi_ms": (wl.get("bootstrap") or {}).get("hl_ci95_hi_ms")})
    write_csv(outd / "webhook_latency_E38v2.csv", ["arm", "n", "median_ms", "p05_ms", "p95_ms", "hl_ms", "hl_ci95_lo_ms", "hl_ci95_hi_ms"], lat)
    wr = [dict(r, delta_ms=r["delta_ms"] if r["delta_ms"] == "ALL" else delta_label(r["delta_ms"])) for r in res["writer_release_accuracy"]]
    write_csv(outd / "writer_release_E38v2.csv",
              ["arm", "condition", "delta_ms", "n", "mean", "sd", "median", "p05", "p95", "min", "max",
               "frac_abs_gt_100us", "frac_abs_gt_500us", "realized_delta_median_ms"], wr)
    write_csv(outd / "agreement_E38v2.csv", ["primary", "secondary", "n"], res["oracle_agreement"]["crosstab"])
    # plan v1.2: primary x tertiary (the v1/v1.1 secondary rule); column "secondary" holds the tertiary class
    write_csv(outd / "agreement_tertiary_E38v2.csv", ["primary", "secondary", "n"],
              res["oracle_agreement_tertiary"]["crosstab"])
    if res.get("stage2_topup") is not None:
        write_csv(outd / "stage2_candidates_E38v2.csv",
                  ["order", "arm", "condition", "delta_ms", "n_stage1_planned", "p_hat_stage1", "n_topup", "n_target", "flag"],
                  [dict(r, delta_ms=delta_label(r["delta_ms"])) for r in res["stage2_topup"]])
    (outd / "results_E38v2.json").write_text(json.dumps(res, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


OUTPUT_FILES = ("results_E38v2.json", "cells_E38v2.csv", "oracle_trials_E38v2.csv", "exposure_window_E38v2.csv",
                "availability_E38v2.csv", "webhook_latency_E38v2.csv", "writer_release_E38v2.csv",
                "agreement_E38v2.csv", "agreement_tertiary_E38v2.csv", "stage2_candidates_E38v2.csv")


def refuse(outd, reasons, meta):
    outd.mkdir(parents=True, exist_ok=True)
    for f in OUTPUT_FILES:          # never leave stale results next to a refusal
        p = outd / f
        if p.is_file():
            p.unlink()
    doc = {"schema": "e38v2.refusal.v1", "analysis_version": ANALYSIS_VERSION, "refused": True,
           "reasons": reasons, **meta}
    (outd / "REFUSED_E38v2.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("REFUSED: data-completeness gate failed (%d reasons):" % len(reasons), file=sys.stderr)
    for r in reasons[:40]:
        print("  - " + r, file=sys.stderr)
    return 2


# ----------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="E38v2 pre-registered analysis")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--raw")
    ap.add_argument("--webhook")
    ap.add_argument("--audit")
    ap.add_argument("--pod-events", help="pod_events.jsonl (plan v1.2); default: <run-dir>/pod_events.jsonl if present")
    ap.add_argument("--schedule", action="append", help="schedule file(s); default <run-dir>/SCHEDULE.json")
    ap.add_argument("--lock", required=True, help="LOCK JSON path (hashes + seed)")
    ap.add_argument("--lock-root", help="base dir for relative LOCK paths (default: parent of the LOCK's directory)")
    ap.add_argument("--analysis-plan", help="default <lock-root>/protocol/ANALYSIS_PLAN_E38v2.txt")
    ap.add_argument("--stage", type=int, choices=(1, 2), required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bootstrap-reps", type=int, default=DEFAULT_BOOT_REPS)
    ap.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOT_SEED)
    ap.add_argument("--design-check", choices=("strict", "off"), default="strict",
                    help="'off' only for pilots/smoke runs; results are then marked confirmatory=false")
    a = ap.parse_args(argv)

    rd = Path(a.run_dir)
    raw_p = Path(a.raw or rd / "RAW_TRIALS.jsonl")
    wh_p = Path(a.webhook or rd / "webhook_log.jsonl")
    au_p = Path(a.audit or rd / "audit.log")
    pe_p = Path(a.pod_events) if a.pod_events else rd.joinpath("pod_events.jsonl")
    if not a.pod_events and not pe_p.is_file():
        pe_p = None
    sched_ps = [Path(p) for p in (a.schedule or [str(rd / "SCHEDULE.json")])]
    lock_p = Path(a.lock)
    lock_root = Path(a.lock_root) if a.lock_root else lock_p.resolve().parent.parent
    plan_p = Path(a.analysis_plan) if a.analysis_plan else lock_root / "protocol" / "ANALYSIS_PLAN_E38v2.txt"
    outd = Path(a.out)
    meta = {"stage_analyzed": a.stage, "inputs": {}}

    ins = [raw_p, wh_p, au_p, lock_p, *sched_ps] + ([pe_p] if pe_p is not None else [])
    missing_inputs = [str(p) for p in ins if not p.is_file()]
    if missing_inputs:
        return refuse(outd, ["input file missing: %s" % p for p in missing_inputs], meta)
    meta["inputs"] = {str(p): sha256_file(p) for p in ins}

    trials, raw_errs = load_jsonl(raw_p)
    webhook, wh_errs = load_jsonl(wh_p)
    audit, au_errs = load_jsonl(au_p)
    aux = []
    if wh_errs:
        aux.append("webhook_log has %d unparseable lines (first: line %d)" % (len(wh_errs), wh_errs[0][0]))
    if au_errs:
        aux.append("audit.log has %d unparseable lines (first: line %d)" % (len(au_errs), au_errs[0][0]))
    pod_events = None
    if pe_p is not None:
        pod_events, pe_errs = load_jsonl(pe_p)
        if pe_errs:
            aux.append("pod_events has %d unparseable lines (first: line %d)" % (len(pe_errs), pe_errs[0][0]))
    sched, sched_errs, sched_rids = [], [], []
    for p in sched_ps:
        e, rid, er = load_schedule(p)
        sched += e
        sched_errs += er
        sched_rids.append(rid)
    lock_errs, lock_info = verify_lock(lock_p, lock_root, sched_ps, plan_p)
    errs, planned, used = completeness_gate(trials, raw_errs, sched, sched_errs, sched_rids, lock_errs, lock_info,
                                            a.stage, aux)
    oracle_rows = orc.run_oracle(used, audit, webhook, pod_events)
    exp2 = None
    if a.design_check == "strict" and planned:
        derrs, exp2 = design_check(planned, oracle_rows, a.stage)
        errs += derrs
    if a.bootstrap_reps < 100:
        errs.append("bootstrap reps < 100")
    if errs:
        return refuse(outd, errs, meta)

    cells = summarize_cells(oracle_rows, planned)
    res = {"schema": RESULTS_SCHEMA, "analysis_version": ANALYSIS_VERSION, "oracle_version": orc.ORACLE_VERSION,
           "spec": SPEC_VERSION, "run_id": next(iter({t.get("run_id") for t in used}), None),
           "stage_analyzed": a.stage, "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "inputs_sha256": meta["inputs"],
           "completeness_gate": {"passed": True, "n_planned": len(planned), "n_observed": len(used),
                                 "lock_verified": lock_info["verified"],
                                 "lock_unverified_hash_fields": lock_info["unverified_hash_fields"],
                                 "lock_seed": lock_info["seed"], "design_check": a.design_check},
           "confirmatory": a.design_check == "strict",
           "bootstrap": {"reps": a.bootstrap_reps, "seed": a.bootstrap_seed},
           "overall_counts_primary": _cls_counts(oracle_rows, "primary_class"),
           "overall_counts_secondary": _cls_counts(oracle_rows, "secondary_class"),
           "overall_counts_tertiary": _cls_counts(oracle_rows, "tertiary_class"),
           "pod_events": {"supplied": pe_p is not None, "n_events": len(pod_events or []),
                          "n_trials_delete_rv_from_pod_watch":
                              sum(1 for r in oracle_rows if "delete_rv_from_pod_watch" in r["flags"])},
           "unknown_reasons_primary": dict(sorted(Counter(r["primary_reason"] for r in oracle_rows
                                                          if r["primary_class"] == "UNKNOWN").items())),
           "infra_error_reasons": dict(sorted(Counter(r["primary_reason"] for r in oracle_rows
                                                      if r["primary_class"] == "INFRA_ERROR").items())),
           "cells": cells,
           "exposure_window": exposure_windows(cells, oracle_rows, a.bootstrap_reps, a.bootstrap_seed),
           "webhook_latency_cost": webhook_latency(oracle_rows, a.bootstrap_reps, a.bootstrap_seed),
           "availability_cost": availability(cells),
           "writer_release_accuracy": writer_release(oracle_rows),
           "oracle_agreement": agreement_overall(oracle_rows),
           "oracle_agreement_tertiary": agreement_overall(oracle_rows, "tertiary_class", "rules_agree_tertiary"),
           "uid_rebind_control": uid_control(cells),
           "stage2_topup": None, "stage2_topup_verified": exp2}
    if a.stage == 1:
        res["stage2_topup"] = topup_from_cells([{"arm": c["arm"], "condition": c["condition"], "delta_ms": c["delta_ms"],
                                                 "n_planned": c["n_planned"], "p_hat": c["primary"]["p_hat"]} for c in cells])
    write_outputs(outd, res, oracle_rows)
    print("E38v2 analysis written to %s (stage %d, %d trials, confirmatory=%s)"
          % (outd, a.stage, len(used), res["confirmatory"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
