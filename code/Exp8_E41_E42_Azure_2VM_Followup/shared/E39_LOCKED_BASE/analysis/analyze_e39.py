#!/usr/bin/env python3
"""E39 pre-registered analysis (offline). See protocol/ANALYSIS_PLAN_E39.txt.

Exit codes: 0 results written; 2 REFUSED by the data-completeness gate (only REFUSED_E39.json is written,
stale result files removed); 1 usage / unexpected error.

Usage:
  analyze_e39.py --run-dir results/<run_id> --lock protocol/LOCK_E39.json --stage 1 --out <dir>
                 [--experiments A,B,C,D,E,F,G] [--schedule SCHEDULE.json --schedule SCHEDULE_STAGE2.json]
                 [--w-external A2=3.1]   (E39-B prediction windows in ms for arms without E39-A curves)
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

import oracle_e39 as orc
from e39_common import (ACQ_MODES, ARMS_A, ARMS_B, ARMS_D, ARMS_E, ARMS_F,
                        ASYNC_CONDITIONS, C_CONDITIONS, DELTAS_A, DELTAS_SHORT, EXPERIMENTS,
                        EXPOSURE_THRESHOLD, FORBIDDEN_SEEDS, G_CONDITIONS, G_OUT_OF_MODEL,
                        LAMBDAS_B, LAMBDAS_EXTRAPOLATE, LATENCIES_C, LOAD_LEVELS, N_TOPUP_TARGET,
                        NO_WRITE, PLANNING_MODES, RESULTS_SCHEMA,
                        SCHEDULE_SCHEMA, SPEC_VERSION, TRIAL_SCHEMA, UID_REBIND, canon_acq, canon_action,
                        canon_arm, canon_condition, canon_load, canon_planning, cell_key, cell_label,
                        cell_sort_key, delta_label, deltas_equal, design_cells, exact_unconditional_diff,
                        exposure_d_star, hodges_lehmann_paired, hodges_lehmann_two_sample, load_jsonl,
                        newcombe_diff, norm_delta, norm_float, order_stat_ci, poisson_window_mle,
                        sha256_file, topup_decision, wilson, window_area_ms)

ANALYSIS_VERSION = "e39.analysis.v1"
DEFAULT_BOOT_REPS = 2000
DEFAULT_BOOT_SEED = 3902026          # pre-registered; unrelated to the schedule seed
HIGH_MISSINGNESS = 0.10
OUTCOMES = orc.OUTCOMES
NOT_ANALYZABLE = orc.NOT_ANALYZABLE
SCHEDULE_FIELDS = ("experiment", "stage", "block", "arm", "condition", "delta_ms", "rep")
OPTIONAL_SCHEDULE_FIELDS = ("lambda_per_s", "injected_latency_ms", "acquisition_mode", "planning_mode",
                            "load_level", "action_type", "client_impl", "pair_id", "guard_order")
FLOAT_FIELDS = ("delta_ms", "lambda_per_s", "injected_latency_ms")
EXP_EXPOSURE = {"A": ARMS_A, "D": ARMS_D, "E": ARMS_E, "F": None}


class Refusal(Exception):
    pass


# ============================================================================= schedule / lock / gate
def canon_entry(e):
    """Schedule entry or raw trial -> dict with the canonical fields used by cell_key."""
    exp = e.get("experiment")
    if isinstance(exp, str):
        exp = exp.strip().upper().replace("E39-", "").replace("E39_", "") or None
    arm_c, impl = canon_arm(e.get("arm"), e.get("client_impl"))
    return {"experiment": exp, "arm_c": arm_c, "client_impl": impl,
            "condition_c": canon_condition(e.get("condition")), "delta_ms": norm_delta(e.get("delta_ms")),
            "lambda_per_s": norm_float(e.get("lambda_per_s")),
            "injected_latency_ms": norm_float(e.get("injected_latency_ms")),
            "acquisition_mode": canon_acq(e.get("acquisition_mode")),
            "planning_mode": canon_planning(e.get("planning_mode")),
            "load_level": canon_load(e.get("load_level")), "action_type": canon_action(e.get("action_type"))}


def load_schedule(path):
    """{"schema": "e39.schedule.v1", "run_id", "schedule_seed", "stage"?, "trials": [...]} or a bare list."""
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
        ent = {"trial_id": e["trial_id"], "stage": e.get("stage", top.get("stage")),
               "schedule_seed": e.get("schedule_seed", top.get("schedule_seed")), "_file": str(path),
               "_present": set(k for k in OPTIONAL_SCHEDULE_FIELDS if k in e)}
        for f in SCHEDULE_FIELDS + OPTIONAL_SCHEDULE_FIELDS:
            if f == "stage":
                continue
            ent[f] = norm_float(e.get(f)) if f in FLOAT_FIELDS else e.get(f)
        ent["_c"] = canon_entry(ent)
        out.append(ent)
    return out, top.get("run_id"), errs


def verify_lock(lock_path, lock_root, schedule_paths, plan_path):
    """LOCK verification (E38v2 logic + E39 forbidden-seed list). Returns (errors, info)."""
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
    files = lock.get("files") or {}
    if isinstance(files, list):
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
    if len(schedule_paths) > 1 and not lock.get("stage2_schedule_sha256"):
        errs.append("stage-2 schedule supplied but LOCK has no stage2_schedule_sha256 (run lock_hash_e39.py add-stage2)")
    for k, v in sorted(lock.items()):
        if k.endswith("_sha256") and v and k not in named and k not in ("lock_sha256", "lock_sha256_stage1"):
            info["unverified_hash_fields"].append(k)
    if not info["verified"]:
        errs.append("LOCK contains no verifiable hashes (files / schedule_sha256 / analysis_plan_sha256)")
    design = lock.get("design") if isinstance(lock.get("design"), dict) else {}
    seed = lock.get("schedule_seed", lock.get("random_seed", design.get("schedule_seed")))
    forbidden = set(FORBIDDEN_SEEDS) | {int(x) for x in (design.get("forbidden_schedule_seeds") or [])
                                        if isinstance(x, int) or (isinstance(x, str) and x.isdigit())}
    if seed is None:
        errs.append("LOCK has no schedule_seed/random_seed")
    elif int(seed) in forbidden:
        errs.append("LOCK seed equals a forbidden reused seed %d" % int(seed))
    info["seed"] = None if seed is None else int(seed)
    info["forbidden"] = sorted(forbidden)
    s2 = lock.get("stage2_schedule_seed")
    info["stage2_seed"] = None if s2 is None else int(s2)
    info["run_id"] = lock.get("run_id")
    info["status"] = status
    return errs, info


def completeness_gate(trials, raw_errs, schedule_entries, sched_errs, sched_run_ids, lock_errs, lock_info,
                      stage, aux_errs, experiments):
    """Structural checks independent of the oracle. Returns (errors, planned, used_trials)."""
    errs = list(sched_errs) + list(lock_errs) + list(aux_errs)
    forbidden = set(lock_info.get("forbidden") or FORBIDDEN_SEEDS)
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
    unknown_exp = sorted({str(e["_c"]["experiment"]) for e in schedule_entries if e["_c"]["experiment"] not in EXPERIMENTS})
    if unknown_exp:
        errs.append("schedule has unknown experiment ids: %s" % unknown_exp)
    planned = {}
    for e in schedule_entries:
        if e["stage"] not in (1, 2):
            errs.append("schedule trial %s has invalid stage %r" % (e["trial_id"], e["stage"]))
            continue
        if e["stage"] == 2 and e["_c"]["experiment"] != "A":
            errs.append("stage-2 schedule trial %s is not E39-A (top-up applies to E39-A only)" % e["trial_id"])
        if e["stage"] > stage or e["_c"]["experiment"] not in experiments:
            continue
        if e["trial_id"] in planned:
            errs.append("schedule lists trial_id %s more than once" % e["trial_id"])
        planned[e["trial_id"]] = e
    if not planned:
        errs.append("schedule contains no planned trials for stage <= %d and experiments %s" % (stage, ",".join(experiments)))
    for e in planned.values():
        if e["schedule_seed"] is None:
            errs.append("schedule trial %s has no schedule_seed" % e["trial_id"])
            break
    seeds_by_stage = defaultdict(set)
    for e in planned.values():
        if e["schedule_seed"] is not None:
            seeds_by_stage[e["stage"]].add(int(e["schedule_seed"]))
    for st, ss in sorted(seeds_by_stage.items()):
        if len(ss) > 1:
            errs.append("stage-%d schedule uses several seeds: %s" % (st, sorted(ss)))
        if ss & forbidden:
            errs.append("stage-%d schedule seed is forbidden: %s" % (st, sorted(ss & forbidden)))
        want = lock_info.get("seed") if st == 1 else (lock_info.get("stage2_seed") or lock_info.get("seed"))
        if want is not None and ss and ss != {want}:
            errs.append("stage-%d schedule seed %s != LOCK seed %s" % (st, sorted(ss), want))
    all_ids = {e["trial_id"] for e in schedule_entries}
    used = [t for t in trials if t.get("trial_id") in planned]
    stray = [t for t in trials if t.get("trial_id") not in all_ids]
    ignored_other = [t for t in trials if t.get("trial_id") in all_ids and t.get("trial_id") not in planned]
    del ignored_other
    bad_stage = [t for t in trials if t.get("stage") not in (1, 2)]
    if bad_stage:
        errs.append("%d RAW records with invalid stage" % len(bad_stage))
    ids = Counter(t.get("trial_id") for t in used)
    dups = sorted(str(k) for k, v in ids.items() if v > 1)
    if dups:
        errs.append("duplicate RAW trial_ids: %s" % ", ".join(dups[:10]))
    missing = sorted(set(planned) - set(ids), key=str)
    if missing:
        errs.append("%d planned trials missing from RAW (e.g. %s)" % (len(missing), missing[:10]))
    if stray:
        errs.append("%d RAW trials not in any schedule (e.g. %s)" % (len(stray), [t.get("trial_id") for t in stray[:10]]))
    mism = []
    for t in used:
        e = planned[t.get("trial_id")]
        for f in SCHEDULE_FIELDS + tuple(sorted(e["_present"])):
            ev = e["stage"] if f == "stage" else e.get(f)
            tv = t.get(f)
            ok = deltas_equal(norm_float(tv), ev) if f in FLOAT_FIELDS else tv == ev
            if not ok and f == "load_level":
                # E39-D load guard: the record may carry the EFFECTIVE (throttled) level; a drop below the
                # scheduled level is allowed only for a throttled trial (host.load_throttled) or when the
                # record's nominal field equals the schedule. Analysis is by the scheduled level (ITT).
                host = t.get("host") if isinstance(t.get("host"), dict) else {}
                thr = bool(host.get("load_throttled") or t.get("load_throttled"))
                nom = t.get("requested_level") or t.get("load_level_nominal") or t.get("load_level_requested")
                lv = ("idle", "moderate", "high")
                drop = tv in lv and ev in lv and lv.index(tv) < lv.index(ev)
                ok = (thr and drop) or nom == ev
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
            c[cell_key(e["_c"])] += 1
    return c


def design_check(planned, oracle_rows, stage, experiments):
    """Strict pre-registered design check per sub-experiment. Returns (errors, expected_stage2 or None)."""
    errs = []
    got1 = planned_cells(planned, 1)
    for x in experiments:
        design = design_cells(x)
        got = {k: v for k, v in got1.items() if k[0] == x}
        for cell in sorted(set(design) | set(got), key=cell_sort_key):
            if got.get(cell, 0) != design.get(cell, 0):
                errs.append("stage-1 design mismatch E39-%s cell %s: planned %d, pre-registered %d"
                            % (x, cell_label(cell), got.get(cell, 0), design.get(cell, 0)))
                if len(errs) > 30:
                    return errs, None
    if "C" in experiments:
        pairs = defaultdict(list)
        for e in planned.values():
            if e["_c"]["experiment"] == "C":
                pairs[e.get("pair_id")].append(e["_c"]["arm_c"])
        badp = [p for p, arms in pairs.items() if p is None or sorted(arms) != ["A1", "A2"]]
        if badp:
            errs.append("E39-C: %d pair_ids without exactly one A1 and one A2 trial (e.g. %s)" % (len(badp), badp[:5]))
    exp2 = None
    if stage == 2 and "A" in experiments:
        rows1 = [r for r in oracle_rows if r["experiment"] == "A" and r["stage"] == 1]
        pl1 = {k: v for k, v in planned.items() if v["stage"] == 1 and v["_c"]["experiment"] == "A"}
        s1 = summarize_cells(rows1, pl1)
        exp2 = topup_from_cells(s1)
        want = {tuple(r["cell"]): r["n_topup"] for r in exp2}
        got2 = planned_cells(planned, 2)
        for cell in sorted(set(want) | set(got2), key=cell_sort_key):
            if got2.get(cell, 0) != want.get(cell, 0):
                errs.append("stage-2 schedule mismatch vs pre-registered top-up in cell %s: planned %d, rule %d"
                            % (cell_label(cell), got2.get(cell, 0), want.get(cell, 0)))
    return errs, exp2


def topup_from_cells(cells):
    """E39-A only: async cells with 0.05 < stage-1 p_hat(primary) < 0.95 -> top up to 93."""
    out = []
    for c in cells:
        cell = tuple(c["cell"])
        if cell[0] != "A" or cell[3] not in ASYNC_CONDITIONS:
            continue
        p = c["stage1_primary"]["p_hat"] if "stage1_primary" in c else c.get("p_hat")
        if not topup_decision(p):
            continue
        n_add = N_TOPUP_TARGET - int(c["n_planned_stage1"] if "n_planned_stage1" in c else c["n_planned"])
        if n_add <= 0:
            continue
        out.append({"cell": list(cell), "arm": cell[1], "condition": cell[3], "delta_ms": cell[4],
                    "n_stage1_planned": N_TOPUP_TARGET - n_add, "p_hat_stage1": p, "n_topup": n_add,
                    "n_target": N_TOPUP_TARGET})
    out.sort(key=lambda r: cell_sort_key(tuple(r["cell"])))
    for i, r in enumerate(out):
        r["order"] = i
    return out


# ============================================================================= per-cell classification summaries
def _cls_counts(rows, col):
    c = Counter(r[col] for r in rows)
    return {k: int(c.get(k, 0)) for k in OUTCOMES}


def is_fail_closed(r, col, reason_col):
    return r[col] == "UNKNOWN" and r[reason_col] == orc.FAIL_CLOSED_REASON


def rule_block(rows, col="primary_class", reason_col="primary_reason", event="INVALID_EXECUTED"):
    """Primary denominator (plan v1.1/v1.2 carried over): analyzable + fail-closed (non-events)."""
    counts = _cls_counts(rows, col)
    n_an = sum(v for k, v in counts.items() if k not in NOT_ANALYZABLE)
    k = counts[event]
    nfc = sum(1 for r in rows if is_fail_closed(r, col, reason_col))
    n_prim = n_an + nfc
    p, lo, hi = wilson(k, n_prim)
    pe, loe, hie = wilson(k, n_an)
    nu_open = counts["UNKNOWN"] - nfc
    pu, lou, hiu = wilson(k + nu_open, n_prim + nu_open)
    unk = Counter(r[reason_col] for r in rows if r[col] == "UNKNOWN")
    return {"counts": counts, "n_analyzable": n_an, "n_fail_closed": nfc, "n_primary_denominator": n_prim,
            "k_invalid_executed": k, "p_hat": p, "wilson_lo": lo, "wilson_hi": hi,
            "sensitivity_unknown_as_invalid": {"k": k + nu_open, "n": n_prim + nu_open, "p_hat": pu,
                                               "wilson_lo": lou, "wilson_hi": hiu},
            "sensitivity_exclude_failclosed": {"k": k, "n": n_an, "p_hat": pe, "wilson_lo": loe, "wilson_hi": hie},
            "unknown_reasons": dict(sorted(unk.items()))}


def row_key(r):
    return cell_key({"experiment": r["experiment"], "arm_c": r["arm_c"], "client_impl": r["client_impl"],
                     "condition_c": r["condition_c"], "delta_ms": r["delta_ms"] if r["delta_ms"] != "" else None,
                     "lambda_per_s": r["lambda_per_s"] if r["lambda_per_s"] != "" else None,
                     "injected_latency_ms": r["injected_latency_ms"] if r["injected_latency_ms"] != "" else None,
                     "acquisition_mode": r["acquisition_mode"] or None, "planning_mode": r["planning_mode"] or None,
                     "load_level": r["load_level"] or None})


def summarize_cells(rows, planned):
    by = defaultdict(list)
    for r in rows:
        by[row_key(r)].append(r)
    pc, pc1 = planned_cells(planned), planned_cells(planned, 1)
    out = []
    for cell in sorted(set(pc) | set(by), key=cell_sort_key):
        rs = by.get(cell, [])
        prim = rule_block(rs)
        sec = rule_block(rs, "secondary_class", "secondary_reason")
        ter = rule_block(rs, "tertiary_class", "tertiary_reason")
        both = [r for r in rs if r["rules_agree"] != "NA"]
        n_agree = sum(1 for r in both if r["rules_agree"] == 1)
        s1 = rule_block([r for r in rs if r["stage"] == 1])
        n_obs = len(rs)
        miss = (prim["counts"]["UNKNOWN"] - prim["n_fail_closed"] + prim["counts"]["INFRA_ERROR"]) / n_obs if n_obs else None
        n409 = sum(1 for r in rs if r["decider"] == "rv_precondition")
        out.append({"cell": list(cell), "label": cell_label(cell), "experiment": cell[0],
                    "n_planned": int(pc.get(cell, 0)), "n_planned_stage1": int(pc1.get(cell, 0)),
                    "n_observed": n_obs, "primary": prim, "secondary": sec, "tertiary": ter,
                    "stage1_primary": {"k": s1["k_invalid_executed"], "n": s1["n_primary_denominator"],
                                       "p_hat": s1["p_hat"], "wilson_lo": s1["wilson_lo"], "wilson_hi": s1["wilson_hi"]},
                    "agreement": {"n_both_classified": len(both), "n_agree": n_agree,
                                  "prop": (n_agree / len(both)) if both else None},
                    "n_rv_precondition_409": n409,
                    "missingness": miss,
                    "quality_flag": "HIGH_MISSINGNESS" if (miss is not None and miss > HIGH_MISSINGNESS) else ""})
    return out


def _cells_of(cells, x):
    return [c for c in cells if c["experiment"] == x]


def _bootstrap_dstar(trial_lists, deltas, reps, seed_list):
    rng = np.random.default_rng(seed_list)
    mat = np.full((reps, len(deltas)), np.nan)
    for j, x in enumerate(trial_lists):
        x = np.asarray(x, dtype=float)
        if len(x):
            idx = rng.integers(0, len(x), size=(reps, len(x)))
            mat[:, j] = x[idx].mean(axis=1)
    ds = []
    for b in range(reps):
        d, _ = exposure_d_star(deltas, [None if np.isnan(v) else float(v) for v in mat[b]])
        ds.append(math.inf if d is None else d)
    lo, hi = order_stat_ci(ds)
    return {"reps": reps, "seed": list(seed_list), "ci95_lo_ms": lo, "ci95_hi_ms": hi,
            "frac_not_closed": sum(1 for v in ds if math.isinf(v)) / reps,
            "distribution": dict(sorted(Counter("not_closed" if math.isinf(v) else delta_label(v) for v in ds).items()))}


def _prim_indicator(rows):
    return [1 if r["primary_class"] == "INVALID_EXECUTED" else 0 for r in rows
            if r["primary_class"] not in NOT_ANALYZABLE or is_fail_closed(r, "primary_class", "primary_reason")]


def exposure_curves(cells, rows, experiment, group_fn, deltas, reps, seed, seed_tag):
    """d* per group (cells of `experiment` whose key maps to group via group_fn(cell) -> (group, delta))."""
    groups = defaultdict(dict)
    for c in _cells_of(cells, experiment):
        g, d = group_fn(tuple(c["cell"]))
        if g is None or d is None:
            continue
        groups[g][d] = c
    rows_by = defaultdict(list)
    for r in rows:
        if r["experiment"] == experiment:
            rows_by[row_key(r)].append(r)
    out = []
    for gi, g in enumerate(sorted(groups, key=lambda g: cell_sort_key(g))):
        dd = [d for d in deltas if d in groups[g]]
        if not dd:
            continue
        ph = [groups[g][d]["primary"]["p_hat"] for d in dd]
        hi = [groups[g][d]["primary"]["wilson_hi"] for d in dd]
        ph2 = [groups[g][d]["secondary"]["p_hat"] for d in dd]
        d_star, last_exp = exposure_d_star(dd, ph)
        d_star2, _ = exposure_d_star(dd, ph2)
        tl = [_prim_indicator(rows_by[tuple(groups[g][d]["cell"])]) for d in dd]
        boot = _bootstrap_dstar(tl, dd, reps, [seed, seed_tag, gi])
        out.append({"group": list(g), "label": cell_label(g), "threshold": EXPOSURE_THRESHOLD,
                    "deltas_ms": dd, "p_hat": ph, "wilson_hi": hi,
                    "d_star_ms": d_star, "closed_within_grid": d_star is not None, "last_exposed_delta_ms": last_exp,
                    "wilson_hi_at_d_star": hi[dd.index(d_star)] if d_star is not None else None,
                    "max_wilson_hi_at_or_above_d_star": max(h for d, h in zip(dd, hi) if d >= d_star) if d_star is not None else None,
                    "d_star_secondary_ms": d_star2, "window_area_ms": window_area_ms(dd, ph),
                    "bootstrap": boot})
    return out


def _desc(v):
    a = np.asarray([x for x in v if x is not None], float)
    if not len(a):
        return {"n": 0, "mean": None, "sd": None, "median": None, "p05": None, "p95": None, "min": None, "max": None}
    return {"n": int(len(a)), "mean": float(a.mean()), "sd": float(a.std(ddof=1)) if len(a) > 1 else None,
            "median": float(np.median(a)), "p05": float(np.percentile(a, 5)), "p95": float(np.percentile(a, 95)),
            "min": float(a.min()), "max": float(a.max())}


def _f(x):
    return None if x in ("", None) else float(x)


def agreement(rows, col2="secondary_class", agree_col="rules_agree"):
    both = [r for r in rows if r[agree_col] != "NA"]
    n = len(both)
    agree = sum(1 for r in both if r[agree_col] == 1)
    tab = Counter((r["primary_class"] == "INVALID_EXECUTED", r[col2] == "INVALID_EXECUTED") for r in both)
    a, b, c, d = tab[(True, True)], tab[(True, False)], tab[(False, True)], tab[(False, False)]
    kappa = None
    if n:
        po = (a + d) / n
        pe = ((a + b) * (a + c) + (c + d) * (b + d)) / (n * n)
        kappa = None if pe == 1 else (po - pe) / (1 - pe)
    return {"n_both_classified": n, "n_exact_agree": agree, "prop_exact_agree": (agree / n) if n else None,
            "invalid_executed_2x2": {"both": a, "primary_only": b, "other_only": c, "neither": d},
            "kappa_invalid_executed": kappa,
            "crosstab": [{"primary": k[0], "other": k[1], "n": v}
                         for k, v in sorted(Counter((r["primary_class"], r[col2]) for r in rows).items())]}


# ============================================================================= E39-A
def analyze_A(cells, rows, reps, seed):
    ca = _cells_of(cells, "A")
    rows_a = [r for r in rows if r["experiment"] == "A"]
    ew = exposure_curves(cells, rows, "A", lambda k: ((k[1], k[3]), k[4]) if k[3] in ASYNC_CONDITIONS else (None, None),
                         list(DELTAS_A), reps, seed, 1)
    # availability cost of commit-bound enforcement (409 / FALSE_ABORT), no_write and all async cells
    avail = []
    for arm in ARMS_A:
        nw = [r for r in rows_a if r["arm_c"] == arm and r["condition_c"] == NO_WRITE and r["primary_class"] not in NOT_ANALYZABLE]
        k_fa = sum(1 for r in nw if r["primary_class"] == "FALSE_ABORT")
        k_409 = sum(1 for r in nw if r["decider"] == "rv_precondition")
        asy = [r for r in rows_a if r["arm_c"] == arm and r["condition_c"] in ASYNC_CONDITIONS and r["primary_class"] not in NOT_ANALYZABLE]
        k_fa_async = sum(1 for r in asy if r["primary_class"] == "FALSE_ABORT")
        k409_fa_async = sum(1 for r in asy if r["primary_class"] == "FALSE_ABORT" and r["decider"] == "rv_precondition")
        disp = [r for r in rows_a if r["arm_c"] == arm and str(r["action_dispatched"]) == "True"]
        k409_all = sum(1 for r in disp if r["decider"] == "rv_precondition")
        attr = Counter((r["condition_c"] == NO_WRITE and "no_write" or "async", r["primary_class"], r["conflict_attribution"] or "NA")
                       for r in rows_a if r["arm_c"] == arm and r["decider"] == "rv_precondition")
        avail.append({"arm": arm, "rv_precondition_409_by_attribution": [{"population": k[0], "primary_class": k[1], "changed_by": k[2], "n": v} for k, v in sorted(attr.items())],
                      "no_write": dict(zip(("p_hat", "wilson_lo", "wilson_hi"), wilson(k_fa, len(nw))), k_false_abort=k_fa,
                                       k_409=k_409, n_analyzable=len(nw)),
                      "no_write_409": dict(zip(("p_hat", "wilson_lo", "wilson_hi"), wilson(k_409, len(nw))), k=k_409, n=len(nw)),
                      "async_false_abort": dict(zip(("p_hat", "wilson_lo", "wilson_hi"), wilson(k_fa_async, len(asy))),
                                                k=k_fa_async, k_via_409=k409_fa_async, n=len(asy)),
                      "rate_409_among_dispatched": dict(zip(("p_hat", "wilson_lo", "wilson_hi"), wilson(k409_all, len(disp))),
                                                        k=k409_all, n=len(disp))})
    # A4m mirror lag
    m = [r for r in rows_a if r["arm_c"] == "A4m" and r["mirror_lag_rv"] != ""]
    by_cls = defaultdict(list)
    for r in m:
        by_cls[r["primary_class"]].append(_f(r["mirror_lag_ms"]))
    per_delta = []
    for cond in ASYNC_CONDITIONS:
        for d in DELTAS_A:
            rs = [r for r in rows_a if r["arm_c"] == "A4m" and r["condition_c"] == cond and _f(r["delta_ms"]) == d]
            if not rs:
                continue
            acc_src = [r for r in rs if r["source_before_action"] in (1, "1")]
            per_delta.append({"condition": cond, "delta_ms": d, "n": len(rs),
                              "k_invalid_executed": sum(1 for r in rs if r["primary_class"] == "INVALID_EXECUTED"),
                              "n_accepted_source_before_action": len(acc_src),
                              "n_mirror_after_action_given_source_before": sum(1 for r in acc_src if r["mirror_before_action"] in (0, "0")),
                              "mirror_lag_ms": _desc([_f(r["mirror_lag_ms"]) for r in rs if r["mirror_lag_ms"] != ""]),
                              "mirror_lag_rv": _desc([_f(r["mirror_lag_rv"]) for r in rs if r["mirror_lag_rv"] != ""])})
    ew_a4m = {e["group"][1]: e for e in ew if e["group"][0] == "A4m"}
    lag_all = [_f(r["mirror_lag_ms"]) for r in m if r["mirror_lag_ms"] != ""]
    relation = [{"condition": c, "window_area_ms": e["window_area_ms"], "d_star_ms": e["d_star_ms"],
                 "mirror_lag_median_ms": float(np.median(lag_all)) if lag_all else None,
                 "mirror_lag_p95_ms": float(np.percentile(lag_all, 95)) if lag_all else None}
                for c, e in sorted(ew_a4m.items())]
    inv_a4m = [r for r in rows_a if r["arm_c"] == "A4m" and r["primary_class"] == "INVALID_EXECUTED"]
    mirror = {"n_with_mirror": len(m),
              "lag_rv": _desc([_f(r["mirror_lag_rv"]) for r in m]),
              "lag_ms": _desc(lag_all),
              "lag_ms_by_primary_class": {k: _desc(v) for k, v in sorted(by_cls.items())},
              "invalid_executed_explained_by_mirror_lag": {
                  "n_invalid_executed": len(inv_a4m),
                  "n_mirror_after_action": sum(1 for r in inv_a4m if r["mirror_before_action"] in (0, "0")),
                  "n_not_mirrored": sum(1 for r in inv_a4m if "mirror_not_mirrored" in r["flags"])},
              "per_delta": per_delta, "window_vs_lag": relation,
              "not_mirrored_trials": sum(1 for r in rows_a if r["arm_c"] == "A4m" and "mirror_not_mirrored" in r["flags"])}
    uid = [{"arm": c["cell"][1], "n_observed": c["n_observed"], "counts": c["primary"]["counts"]}
           for c in ca if c["cell"][3] == UID_REBIND]
    return {"exposure_window": ew, "availability_cost": avail, "mirror": mirror, "uid_rebind_control": uid,
            "agreement_secondary": agreement(rows_a), "agreement_tertiary": agreement(rows_a, "tertiary_class", "rules_agree_tertiary"),
            "writer_release_us": _desc([_f(r["release_error_us"]) for r in rows_a if r["release_error_us"] != ""])}


# ============================================================================= E39-B
def parse_w_external(items):
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        arm, _ = canon_arm(k.strip())
        if arm is None or not v:
            raise SystemExit("--w-external expects ARM=ms, got %r" % it)
        out[arm] = float(v)
    return out


def analyze_B(cells, rows, a_result, w_external):
    cb = _cells_of(cells, "B")
    rows_b = [r for r in rows if r["experiment"] == "B"]
    # E39-A window areas (async_scope) per arm, in ms
    w_a = {}
    for e in (a_result or {}).get("exposure_window", []):
        if e["group"][1] == "async_scope" and e["window_area_ms"] is not None:
            w_a[e["group"][0]] = e["window_area_ms"]
    w_src = {arm: ("E39-A async_scope curve area" if arm in w_a else None) for arm in ARMS_B}
    for arm, v in (w_external or {}).items():
        if arm not in w_a:
            w_a[arm] = v
            w_src[arm] = "external (--w-external, e.g. E38v2 rerun curve area)"
    per_cell, fits, extrap = [], [], []
    for arm in ARMS_B:
        ks, ns, lams = [], [], []
        gate_ms = [_f(r["gate_ms"]) for r in rows_b if r["arm_c"] == arm and r["gate_ms"] != ""]
        g_med = float(np.median(gate_ms)) if gate_ms else None
        for lam in LAMBDAS_B:
            c = next((c for c in cb if c["cell"][1] == arm and c["cell"][3] == lam), None)
            if c is None:
                continue
            p = c["primary"]
            k, n = p["k_invalid_executed"], p["n_primary_denominator"]
            ks.append(k)
            ns.append(n)
            lams.append(lam)
            ph, lo, hi = wilson(k, n)
            w_pred = w_a.get(arm)
            pred = None if w_pred is None else -math.expm1(-lam * w_pred / 1000.0)
            pred_adj = None if (pred is None or g_med is None) else math.exp(-lam * g_med / 1000.0) * pred
            rs = [r for r in rows_b if r["arm_c"] == arm and _f(r["lambda_per_s"]) == lam]
            n_arr = [_f(r["n_writes"]) for r in rs if r["n_writes"] != ""]
            fa = sum(1 for r in rs if r["primary_class"] == "FALSE_ABORT")
            k409 = sum(1 for r in rs if r["decider"] == "rv_precondition")
            per_cell.append({"arm": arm, "lambda_per_s": lam, "k_invalid_executed": k, "n_responses": n,
                             "per_1000": None if ph is None else 1000 * ph,
                             "per_1000_lo": None if lo is None else 1000 * lo,
                             "per_1000_hi": None if hi is None else 1000 * hi,
                             "unknown_as_invalid_per_1000": None if p["sensitivity_unknown_as_invalid"]["p_hat"] is None else 1000 * p["sensitivity_unknown_as_invalid"]["p_hat"],
                             "predicted_per_1000": None if pred is None else 1000 * pred,
                             "prediction_in_wilson_ci": None if pred is None or lo is None else bool(lo <= pred <= hi),
                             "predicted_gate_adjusted_per_1000": None if pred_adj is None else 1000 * pred_adj,
                             "W_prediction_ms": w_pred, "W_prediction_source": w_src.get(arm),
                             "per_lambda_W_inversion_ms": (None if (ph is None or ph >= 1.0 or lam <= 0) else -math.log1p(-ph) / lam * 1000.0),
                             "arrivals_per_trial": _desc(n_arr),
                             "false_abort_per_1000": 1000 * fa / n if n else None, "rv_precondition_409_per_1000": 1000 * k409 / n if n else None,
                             "counts": p["counts"]})
        mle = poisson_window_mle(ks, ns, lams) if ks else {"W_s": None, "lo_s": None, "hi_s": None, "boundary": "no_data"}
        fit = {"arm": arm, "W_hat_ms": None if mle["W_s"] is None else 1000 * mle["W_s"],
               "W_lo_ms": None if mle.get("lo_s") is None else 1000 * mle["lo_s"],
               "W_hi_ms": None if mle.get("hi_s") is None else 1000 * mle["hi_s"],
               "boundary": mle["boundary"], "method": "binomial MLE of W in p = 1 - exp(-lambda W), profile-likelihood 95% CI",
               "W_from_A_ms": w_a.get(arm), "W_from_A_source": w_src.get(arm), "median_gate_ms": g_med}
        fits.append(fit)
        for lam in LAMBDAS_EXTRAPOLATE:
            conv = lambda W: None if W is None else 1000 * -math.expm1(-lam * W / 1000.0)
            extrap.append({"arm": arm, "lambda_per_s": lam, "label": "MODEL-BASED EXTRAPOLATION (not observed)",
                           "per_1000_from_fitted_W": conv(fit["W_hat_ms"]), "per_1000_lo": conv(fit["W_lo_ms"]),
                           "per_1000_hi": conv(fit["W_hi_ms"]), "per_1000_from_A_window": conv(w_a.get(arm))})
    return {"per_cell": per_cell, "window_fit": fits, "extrapolation_model_based": extrap,
            "agreement_secondary": agreement(rows_b)}


# ============================================================================= E39-C
def _boot_paired_hl(d, reps, seed_list):
    d = np.asarray(d, float)
    if len(d) < 2:
        return None, None
    rng = np.random.default_rng(seed_list)
    bs = np.empty(reps)
    for b in range(reps):
        bs[b] = hodges_lehmann_paired(d[rng.integers(0, len(d), len(d))])
    return order_stat_ci(bs)


C_METRICS = ("refresh_ms", "gate_ms", "e2e_ms")
C_MODEL = {"sequential": (3.0, 3.0), "parallel": (0.31, 3.0)}


def analyze_C(rows, reps, seed):
    rows_c = [r for r in rows if r["experiment"] == "C"]
    pairs = defaultdict(dict)
    for r in rows_c:
        if r["status"] != "COMPLETE" or r["pair_id"] == "":
            continue
        pairs[(_f(r["injected_latency_ms"]), r["acquisition_mode"], r["condition_c"], r["planning_mode"], str(r["pair_id"]))][r["arm_c"]] = r
    out, hl0 = [], {}
    ci = 0
    for lat in LATENCIES_C:
        for acq in ACQ_MODES:
            for cond in C_CONDITIONS:
                for pl in PLANNING_MODES:
                    ci += 1
                    ps = [v for k, v in pairs.items() if k[:4] == (lat, acq, cond, pl) and "A1" in v and "A2" in v]
                    rec = {"injected_latency_ms": lat, "acquisition_mode": acq, "condition": cond, "planning_mode": pl,
                           "n_pairs_complete": len(ps)}
                    for mi, m in enumerate(C_METRICS):
                        d = [(_f(p["A2"][m]) - _f(p["A1"][m])) for p in ps if p["A2"][m] != "" and p["A1"][m] != ""]
                        hl = hodges_lehmann_paired(d) if d else None
                        lo, hi = _boot_paired_hl(d, reps, [seed, 7, ci, mi]) if d else (None, None)
                        rec[m] = {"n_pairs": len(d), "hl_A2_minus_A1_ms": hl, "boot_ci95_lo": lo, "boot_ci95_hi": hi,
                                  "median_A1": float(np.median([_f(p["A1"][m]) for p in ps if p["A1"][m] != ""])) if ps else None,
                                  "median_A2": float(np.median([_f(p["A2"][m]) for p in ps if p["A2"][m] != ""])) if ps else None}
                        if lat == 0.0:
                            hl0[(acq, cond, pl, m)] = hl
                    lo_f, hi_f = C_MODEL[acq]
                    pred = {"model": "saving(c) = A2 - A1 refresh latency; sequential = 3c; parallel bounds [0.31c, 3c]",
                            "pred_lo_ms": lo_f * lat, "pred_hi_ms": hi_f * lat}
                    for m in ("refresh_ms", "gate_ms"):
                        base = hl0.get((acq, cond, pl, m))
                        hl = rec[m]["hl_A2_minus_A1_ms"]
                        exc = None if (hl is None or base is None) else hl - base
                        pred[m + "_excess_over_c0_ms"] = exc
                        pred[m + "_excess_within_model_bounds"] = None if exc is None else bool(lo_f * lat - 1e-9 <= exc <= hi_f * lat + 1e-9)
                        pred[m + "_ratio_excess_to_c"] = None if (exc is None or lat == 0) else exc / lat
                    rec["cost_model"] = pred
                    dec = Counter((p[a]["arm_c"], p[a]["gate_decision"]) for p in ps for a in ("A1", "A2"))
                    rec["gate_decisions"] = [{"arm": k[0], "decision": k[1], "n": v} for k, v in sorted(dec.items())]
                    out.append(rec)
    # break-even: e2e(A2 - A1) = alpha + beta * c per (acq, cond, planning), OLS over pair differences
    be = []
    for acq in ACQ_MODES:
        for cond in C_CONDITIONS:
            for pl in PLANNING_MODES:
                xs, ys = [], []
                for k, v in pairs.items():
                    if k[1:4] == (acq, cond, pl) and "A1" in v and "A2" in v and v["A1"]["e2e_ms"] != "" and v["A2"]["e2e_ms"] != "":
                        xs.append(k[0])
                        ys.append(_f(v["A2"]["e2e_ms"]) - _f(v["A1"]["e2e_ms"]))
                rec = {"acquisition_mode": acq, "condition": cond, "planning_mode": pl, "n": len(xs)}
                if len(set(xs)) >= 2:
                    beta, alpha = np.polyfit(np.asarray(xs, float), np.asarray(ys, float), 1)
                    rec.update({"alpha_ms": float(alpha), "beta_per_ms_c": float(beta),
                                "break_even_c_ms": (float(-alpha / beta) if beta != 0 else None),
                                "interpretation": "c at which FullRefresh and DCJI end-to-end latencies are equal (linear fit; "
                                                  "DCJI faster for c above it when alpha < 0 < beta)"})
                pa1 = [_f(v["A1"]["planning_ms"]) for k, v in pairs.items() if k[1:4] == (acq, cond, pl) and "A1" in v and v["A1"]["planning_ms"] != ""]
                pa2 = [_f(v["A2"]["planning_ms"]) for k, v in pairs.items() if k[1:4] == (acq, cond, pl) and "A2" in v and v["A2"]["planning_ms"] != ""]
                ovh = (float(np.median(pa1)) if pa1 else 0.0) - (float(np.median(pa2)) if pa2 else 0.0)
                rec["planning_overhead_A1_minus_A2_ms"] = ovh if (pa1 or pa2) else None
                if acq == "sequential" and (pa1 or pa2):
                    rec["model_break_even_c_ms"] = ovh / 3.0
                be.append(rec)
    go = Counter((r["arm_c"], r["guard_order"]) for r in rows_c)
    return {"cells": out, "break_even": be, "guard_order_counts": [{"arm": k[0], "guard_order": k[1], "n": v} for k, v in sorted(go.items(), key=str)],
            "n_trials": len(rows_c), "n_not_complete": sum(1 for r in rows_c if r["status"] != "COMPLETE")}


# ============================================================================= E39-D
def analyze_D(cells, rows, reps, seed):
    rows_d = [r for r in rows if r["experiment"] == "D"]
    ew = exposure_curves(cells, rows, "D", lambda k: ((k[1], k[2]), k[4]), list(DELTAS_SHORT), reps, seed, 4)
    host = []
    for lv in LOAD_LEVELS:
        rs = [r for r in rows_d if r["load_level"] == lv]
        host.append({"load_level": lv, "n": len(rs),
                     "host_cpu_pct": _desc([_f(r["host_cpu_pct"]) for r in rs if r["host_cpu_pct"] != ""]),
                     "free_ram_mb": _desc([_f(r["free_ram_mb"]) for r in rs if r["free_ram_mb"] != ""]),
                     "n_cpu_gt_85": sum(1 for r in rs if r["host_cpu_pct"] != "" and _f(r["host_cpu_pct"]) > 85),
                     "n_free_ram_lt_2048mb": sum(1 for r in rs if r["free_ram_mb"] != "" and _f(r["free_ram_mb"]) < 2048),
                     "n_throttled": sum(1 for r in rs if str(r["load_throttled"]) == "1"),
                     "effective_levels": dict(Counter(r["load_level_effective"] or "NA" for r in rs))})
    # per-protocol sensitivity: exclude throttled trials
    pp = []
    for c in _cells_of(cells, "D"):
        k = tuple(c["cell"])
        rs = [r for r in rows_d if row_key(r) == k and str(r["load_throttled"]) != "1"]
        b = rule_block(rs)
        pp.append({"cell": list(k), "label": c["label"], "n": b["n_primary_denominator"], "k": b["k_invalid_executed"],
                   "p_hat": b["p_hat"], "wilson_lo": b["wilson_lo"], "wilson_hi": b["wilson_hi"]})
    return {"exposure_window_by_load": ew, "host_trace": host, "sensitivity_exclude_throttled": pp,
            "gate_ms_by_load": {lv: _desc([_f(r["gate_ms"]) for r in rows_d if r["load_level"] == lv and r["gate_ms"] != ""]) for lv in LOAD_LEVELS},
            "delete_rtt_ms_by_load": {lv: _desc([_f(r["delete_rtt_ms"]) for r in rows_d if r["load_level"] == lv and r["delete_rtt_ms"] != ""]) for lv in LOAD_LEVELS}}


# ============================================================================= E39-E
def analyze_E(cells, rows, reps, seed):
    ce = _cells_of(cells, "E")
    ew = exposure_curves(cells, rows, "E", lambda k: ((k[1], k[2]), k[3]), list(DELTAS_SHORT), reps, seed, 5)
    diff = []
    for cond in ("async_scope", "async_evidence_last_literal"):
        for d in DELTAS_SHORT:
            c1 = next((c for c in ce if c["cell"][1:] == ["A1", cond, d]), None)
            c3 = next((c for c in ce if c["cell"][1:] == ["A3", cond, d]), None)
            if not c1 or not c3:
                continue
            p1, p3 = c1["primary"], c3["primary"]
            dd, lo, hi = newcombe_diff(p3["k_invalid_executed"], p3["n_primary_denominator"],
                                       p1["k_invalid_executed"], p1["n_primary_denominator"])
            diff.append({"condition": cond, "delta_ms": d, "A3_minus_A1": dd, "newcombe_lo": lo, "newcombe_hi": hi})
    return {"exposure_window": ew, "A3_minus_A1_by_delta": diff,
            "structural_limitation": "A4 (commit-bound) not applicable: CREATE carries no resourceVersion precondition on the evidence."}


# ============================================================================= E39-F
def _two_sample(x, y, reps, seed_list):
    if not x or not y:
        return {"hl_ms": None, "boot_ci95_lo": None, "boot_ci95_hi": None, "median_diff_ms": None}
    x, y = np.asarray(x, float), np.asarray(y, float)
    rng = np.random.default_rng(seed_list)
    bs = np.empty(reps)
    for b in range(reps):
        bs[b] = hodges_lehmann_two_sample(x[rng.integers(0, len(x), len(x))], y[rng.integers(0, len(y), len(y))])
    lo, hi = order_stat_ci(bs)
    return {"hl_ms": hodges_lehmann_two_sample(x, y), "boot_ci95_lo": lo, "boot_ci95_hi": hi,
            "median_diff_ms": float(np.median(x) - np.median(y)), "n_x": int(len(x)), "n_y": int(len(y))}


def analyze_F(cells, rows, reps, seed):
    rows_f = [r for r in rows if r["experiment"] == "F"]
    ew = exposure_curves(cells, rows, "F", lambda k: ((k[1], k[2]), k[4]), list(DELTAS_SHORT), reps, seed, 6)

    def lat(rs, m):
        return [_f(r[m]) for r in rs if r[m] != "" and str(r["action_http_status"]) in ("200", "202")
                and r["primary_class"] not in NOT_ANALYZABLE]

    def grp(rs, arm, impl):
        return [r for r in rs if r["arm_c"] == arm and r["client_impl"] == impl]

    per = []
    for arm, impl in ARMS_F:
        g = grp(rows_f, arm, impl)
        per.append({"arm": arm, "client_impl": impl, "delete_rtt_ms": _desc(lat(g, "delete_rtt_ms")),
                    "gate_end_to_send_ms": _desc(lat(g, "gate_end_to_send_ms"))})
    comps = []
    spec = [("A1/client-go vs A1/python (within F)", grp(rows_f, "A1", "client-go"), grp(rows_f, "A1", "python"), 1),
            ("A4d/client-go vs A1/client-go (within F)", grp(rows_f, "A4d", "client-go"), grp(rows_f, "A1", "client-go"), 2),
            ("A4d/client-go (F) vs A4d/python async_scope (E39-A, cross-block)",
             grp(rows_f, "A4d", "client-go"),
             [r for r in rows if r["experiment"] == "A" and r["arm_c"] == "A4d" and r["condition_c"] == "async_scope"
              and _f(r["delta_ms"]) in DELTAS_SHORT], 3)]
    for name, x, y, tag in spec:
        comps.append({"comparison": name,
                      "delete_rtt_ms": _two_sample(lat(x, "delete_rtt_ms"), lat(y, "delete_rtt_ms"), reps, [seed, 60, tag, 1]),
                      "gate_end_to_send_ms": _two_sample(lat(x, "gate_end_to_send_ms"), lat(y, "gate_end_to_send_ms"), reps, [seed, 60, tag, 2])})
    diffs = []
    cf = _cells_of(cells, "F")
    for d in DELTAS_SHORT:
        cg = next((c for c in cf if c["cell"][1:3] == ["A1", "client-go"] and c["cell"][4] == d), None)
        cp = next((c for c in cf if c["cell"][1:3] == ["A1", "python"] and c["cell"][4] == d), None)
        if cg and cp:
            dd, lo, hi = newcombe_diff(cg["primary"]["k_invalid_executed"], cg["primary"]["n_primary_denominator"],
                                       cp["primary"]["k_invalid_executed"], cp["primary"]["n_primary_denominator"])
            diffs.append({"delta_ms": d, "go_minus_python": dd, "newcombe_lo": lo, "newcombe_hi": hi})
    versions = sorted({str(r.get("go_client_version") or "") for r in rows_f if r["client_impl"] == "client-go"} - {""})
    return {"exposure_window_by_impl": ew, "latency_per_arm": per, "latency_comparisons": comps,
            "A1_go_minus_python_by_delta": diffs, "go_client_versions": versions}


# ============================================================================= E39-G
def analyze_G(cells, rows):
    cg = _cells_of(cells, "G")
    out = []
    for cond in G_CONDITIONS:
        c1 = next((c for c in cg if c["cell"][1:] == ["A1", cond]), None)
        c2 = next((c for c in cg if c["cell"][1:] == ["A2", cond]), None)
        rec = {"condition": cond,
               "role": "out-of-model mutation (b,d,f cut before gate)" if cond == G_OUT_OF_MODEL else "in-model control (a,c,e cut before gate)"}
        for nm, c in (("A1_dcji", c1), ("A2_fullrefresh", c2)):
            if c:
                p = c["primary"]
                rec[nm] = {"k": p["k_invalid_executed"], "n": p["n_primary_denominator"], "p_hat": p["p_hat"],
                           "wilson_lo": p["wilson_lo"], "wilson_hi": p["wilson_hi"], "counts": p["counts"]}
        if c1 and c2:
            a, b = c1["primary"], c2["primary"]
            d, lo, hi = newcombe_diff(a["k_invalid_executed"], a["n_primary_denominator"], b["k_invalid_executed"], b["n_primary_denominator"])
            rec["dcji_minus_fullrefresh"] = {"diff": d, "newcombe_lo": lo, "newcombe_hi": hi}
            d2, lo2, hi2 = exact_unconditional_diff(a["k_invalid_executed"], a["n_primary_denominator"],
                                                    b["k_invalid_executed"], b["n_primary_denominator"])
            rec["dcji_minus_fullrefresh_exact_unconditional"] = {"diff": d2, "lo": lo2, "hi": hi2,
                                                                 "method": "Santner-Snell exact unconditional (sensitivity)"}
        out.append(rec)
    return {"by_condition": out}


# ============================================================================= output
def _fmt(x):
    if x is None:
        return ""
    if isinstance(x, float):
        return "%.6g" % x
    if isinstance(x, (list, dict)):
        return json.dumps(x, sort_keys=True)
    return x


def write_csv(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: _fmt(r.get(k)) for k in cols})


OUTPUT_FILES = ("results_E39.json", "cells_E39.csv", "oracle_trials_E39.csv", "exposure_E39.csv",
                "B_rates_E39.csv", "C_latency_E39.csv", "stage2_candidates_E39.csv")


def write_outputs(outd, res, oracle_rows):
    outd.mkdir(parents=True, exist_ok=True)
    if (outd / "REFUSED_E39.json").is_file():
        (outd / "REFUSED_E39.json").unlink()
    orc.write_csv(oracle_rows, outd / "oracle_trials_E39.csv")
    flat = []
    for c in res["cells"]:
        p, s, t = c["primary"], c["secondary"], c["tertiary"]
        r = {"experiment": c["experiment"], "cell": c["label"], "n_planned": c["n_planned"], "n_observed": c["n_observed"],
             "n_analyzable": p["n_analyzable"], "n_fail_closed": p["n_fail_closed"], "n_primary_denominator": p["n_primary_denominator"],
             "k_invalid_executed": p["k_invalid_executed"], "p_hat": p["p_hat"], "wilson_lo": p["wilson_lo"], "wilson_hi": p["wilson_hi"],
             "S1_p_hat_unknown_as_invalid": p["sensitivity_unknown_as_invalid"]["p_hat"],
             "S2_p_hat_exclude_failclosed": p["sensitivity_exclude_failclosed"]["p_hat"],
             "sec_k": s["k_invalid_executed"], "sec_n": s["n_primary_denominator"], "sec_p_hat": s["p_hat"],
             "ter_k": t["k_invalid_executed"], "ter_p_hat": t["p_hat"],
             "stage1_k": c["stage1_primary"]["k"], "stage1_n": c["stage1_primary"]["n"], "stage1_p_hat": c["stage1_primary"]["p_hat"],
             "n_rv_precondition_409": c["n_rv_precondition_409"], "agree_prop": c["agreement"]["prop"],
             "missingness": c["missingness"], "quality_flag": c["quality_flag"],
             "unknown_reasons": ";".join("%s=%d" % kv for kv in p["unknown_reasons"].items())}
        for k in OUTCOMES:
            r["n_" + k] = p["counts"][k]
        flat.append(r)
    cols = ["experiment", "cell", "n_planned", "n_observed", "n_analyzable", "n_fail_closed", "n_primary_denominator"] + \
        ["n_" + k for k in OUTCOMES] + ["k_invalid_executed", "p_hat", "wilson_lo", "wilson_hi", "S1_p_hat_unknown_as_invalid",
                                        "S2_p_hat_exclude_failclosed", "sec_k", "sec_n", "sec_p_hat", "ter_k", "ter_p_hat",
                                        "stage1_k", "stage1_n", "stage1_p_hat", "n_rv_precondition_409", "agree_prop",
                                        "missingness", "quality_flag", "unknown_reasons"]
    write_csv(outd / "cells_E39.csv", cols, flat)
    ex = []
    for x in ("A", "D", "E", "F"):
        blk = (res.get("E39_" + x) or {})
        for e in blk.get("exposure_window") or blk.get("exposure_window_by_load") or blk.get("exposure_window_by_impl") or []:
            ex.append({"experiment": x, "group": e["label"],
                       "d_star_ms": "not_closed" if e["d_star_ms"] is None else delta_label(e["d_star_ms"]),
                       "last_exposed_delta_ms": "" if e["last_exposed_delta_ms"] is None else delta_label(e["last_exposed_delta_ms"]),
                       "wilson_hi_at_d_star": e["wilson_hi_at_d_star"], "d_star_secondary_ms":
                       "not_closed" if e["d_star_secondary_ms"] is None else delta_label(e["d_star_secondary_ms"]),
                       "window_area_ms": e["window_area_ms"],
                       "boot_ci95_lo_ms": "not_closed" if e["bootstrap"]["ci95_lo_ms"] is None else delta_label(e["bootstrap"]["ci95_lo_ms"]),
                       "boot_ci95_hi_ms": "not_closed" if e["bootstrap"]["ci95_hi_ms"] is None else delta_label(e["bootstrap"]["ci95_hi_ms"]),
                       "boot_frac_not_closed": e["bootstrap"]["frac_not_closed"]})
    write_csv(outd / "exposure_E39.csv", ["experiment", "group", "d_star_ms", "last_exposed_delta_ms", "wilson_hi_at_d_star",
                                          "d_star_secondary_ms", "window_area_ms", "boot_ci95_lo_ms", "boot_ci95_hi_ms",
                                          "boot_frac_not_closed"], ex)
    if res.get("E39_B"):
        write_csv(outd / "B_rates_E39.csv", ["arm", "lambda_per_s", "k_invalid_executed", "n_responses", "per_1000", "per_1000_lo",
                                             "per_1000_hi", "predicted_per_1000", "prediction_in_wilson_ci",
                                             "predicted_gate_adjusted_per_1000", "W_prediction_ms", "per_lambda_W_inversion_ms",
                                             "false_abort_per_1000", "rv_precondition_409_per_1000"], res["E39_B"]["per_cell"])
    if res.get("E39_C"):
        flatc = []
        for c in res["E39_C"]["cells"]:
            for m in C_METRICS:
                flatc.append({"injected_latency_ms": c["injected_latency_ms"], "acquisition_mode": c["acquisition_mode"],
                              "condition": c["condition"], "planning_mode": c["planning_mode"], "metric": m,
                              "n_pairs": c[m]["n_pairs"], "hl_A2_minus_A1_ms": c[m]["hl_A2_minus_A1_ms"],
                              "boot_ci95_lo": c[m]["boot_ci95_lo"], "boot_ci95_hi": c[m]["boot_ci95_hi"],
                              "pred_lo_ms": c["cost_model"]["pred_lo_ms"], "pred_hi_ms": c["cost_model"]["pred_hi_ms"],
                              "excess_over_c0_ms": c["cost_model"].get(m + "_excess_over_c0_ms")})
        write_csv(outd / "C_latency_E39.csv", list(flatc[0].keys()) if flatc else ["metric"], flatc)
    if res.get("stage2_topup") is not None:
        write_csv(outd / "stage2_candidates_E39.csv", ["order", "arm", "condition", "delta_ms", "n_stage1_planned",
                                                       "p_hat_stage1", "n_topup", "n_target"], res["stage2_topup"])
    (outd / "results_E39.json").write_text(json.dumps(res, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def refuse(outd, reasons, meta):
    outd.mkdir(parents=True, exist_ok=True)
    for f in OUTPUT_FILES:
        p = outd / f
        if p.is_file():
            p.unlink()
    doc = {"schema": "e39.refusal.v1", "analysis_version": ANALYSIS_VERSION, "refused": True, "reasons": reasons, **meta}
    (outd / "REFUSED_E39.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("REFUSED: data-completeness gate failed (%d reasons):" % len(reasons), file=sys.stderr)
    for r in reasons[:40]:
        print("  - " + r, file=sys.stderr)
    return 2


def _sanitize(o):
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict):
        return {str(k): _sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize(v) for v in o]
    if isinstance(o, (np.floating,)):
        return _sanitize(float(o))
    if isinstance(o, (np.integer,)):
        return int(o)
    return o


# ============================================================================= main
def main(argv=None):
    ap = argparse.ArgumentParser(description="E39 pre-registered analysis")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--raw")
    ap.add_argument("--webhook")
    ap.add_argument("--audit")
    ap.add_argument("--pod-events", help="default <run-dir>/pod_events.jsonl if present")
    ap.add_argument("--schedule", action="append", help="schedule file(s); default <run-dir>/SCHEDULE.json")
    ap.add_argument("--lock", required=True)
    ap.add_argument("--lock-root", help="base dir for relative LOCK paths (default: parent of the LOCK's directory)")
    ap.add_argument("--analysis-plan", help="default <lock-root>/protocol/ANALYSIS_PLAN_E39.txt")
    ap.add_argument("--stage", type=int, choices=(1, 2), required=True)
    ap.add_argument("--experiments", default=",".join(EXPERIMENTS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--bootstrap-reps", type=int, default=DEFAULT_BOOT_REPS)
    ap.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOT_SEED)
    ap.add_argument("--w-external", action="append", help="ARM=ms window for E39-B prediction (e.g. A2 from E38v2)")
    ap.add_argument("--design-check", choices=("strict", "off"), default="strict")
    a = ap.parse_args(argv)

    exps = [x.strip().upper() for x in a.experiments.split(",") if x.strip()]
    rd = Path(a.run_dir)
    raw_p = Path(a.raw or rd / "RAW_TRIALS.jsonl")
    wh_p = Path(a.webhook or rd / "webhook_log.jsonl")
    au_p = Path(a.audit or rd / "audit.log")
    pe_p = Path(a.pod_events) if a.pod_events else (rd / "pod_events.jsonl")
    sched_ps = [Path(p) for p in (a.schedule or [str(rd / "SCHEDULE.json")])]
    lock_p = Path(a.lock)
    lock_root = Path(a.lock_root) if a.lock_root else lock_p.resolve().parent.parent
    plan_p = Path(a.analysis_plan) if a.analysis_plan else lock_root / "protocol" / "ANALYSIS_PLAN_E39.txt"
    outd = Path(a.out)
    meta = {"stage_analyzed": a.stage, "experiments": exps, "inputs": {}}
    bad = [x for x in exps if x not in EXPERIMENTS]
    if bad:
        return refuse(outd, ["unknown experiment(s): %s" % bad], meta)
    if a.stage == 2 and "A" not in exps:
        return refuse(outd, ["stage 2 exists only for E39-A"], meta)
    missing_inputs = [str(p) for p in [raw_p, wh_p, au_p, lock_p, *sched_ps] if not p.is_file()]
    if missing_inputs:
        return refuse(outd, ["input file missing: %s" % p for p in missing_inputs], meta)
    ins = [raw_p, wh_p, au_p, lock_p, *sched_ps] + ([pe_p] if pe_p.is_file() else [])
    meta["inputs"] = {str(p): sha256_file(p) for p in ins}

    trials, raw_errs = load_jsonl(raw_p)
    webhook, wh_errs = load_jsonl(wh_p)
    audit, au_errs = load_jsonl(au_p)
    pod_events = None
    aux = []
    if pe_p.is_file():
        pod_events, pe_errs = load_jsonl(pe_p)
        if pe_errs:
            aux.append("pod_events has %d unparseable lines (first: line %d)" % (len(pe_errs), pe_errs[0][0]))
    if wh_errs:
        aux.append("webhook_log has %d unparseable lines (first: line %d)" % (len(wh_errs), wh_errs[0][0]))
    if au_errs:
        aux.append("audit.log has %d unparseable lines (first: line %d)" % (len(au_errs), au_errs[0][0]))
    sched, sched_errs, sched_rids = [], [], []
    for p in sched_ps:
        e, rid, er = load_schedule(p)
        sched += e
        sched_errs += er
        sched_rids.append(rid)
    lock_errs, lock_info = verify_lock(lock_p, lock_root, sched_ps, plan_p)
    errs, planned, used = completeness_gate(trials, raw_errs, sched, sched_errs, sched_rids, lock_errs, lock_info,
                                            a.stage, aux, exps)
    oracle_rows = orc.run_oracle(used, audit, webhook, pod_events)
    for r in oracle_rows:            # E39-D: cell assignment by the SCHEDULED (randomised) load level
        e = planned.get(r["trial_id"])
        if e is not None and e.get("load_level") is not None:
            nom = canon_load(e.get("load_level"))
            if nom and r["load_level"] != nom:
                if not r["load_level_effective"] or r["load_level_effective"] == r["load_level"]:
                    r["load_level_effective"] = r["load_level"]
                r["load_level"] = nom
    exp2 = None
    if a.design_check == "strict" and planned:
        derrs, exp2 = design_check(planned, oracle_rows, a.stage, exps)
        errs += derrs
    if a.bootstrap_reps < 100:
        errs.append("bootstrap reps < 100")
    if errs:
        return refuse(outd, errs, meta)

    cells = summarize_cells(oracle_rows, planned)
    reps, seed = a.bootstrap_reps, a.bootstrap_seed
    res = {"schema": RESULTS_SCHEMA, "analysis_version": ANALYSIS_VERSION, "oracle_version": orc.ORACLE_VERSION,
           "spec": SPEC_VERSION, "run_id": next(iter({t.get("run_id") for t in used}), None),
           "stage_analyzed": a.stage, "experiments": exps,
           "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "inputs_sha256": meta["inputs"],
           "completeness_gate": {"passed": True, "n_planned": len(planned), "n_observed": len(used),
                                 "lock_verified": lock_info["verified"], "lock_unverified_hash_fields": lock_info["unverified_hash_fields"],
                                 "lock_seed": lock_info["seed"], "design_check": a.design_check,
                                 "pod_events_supplied": pod_events is not None},
           "confirmatory": a.design_check == "strict",
           "bootstrap": {"reps": reps, "seed": seed},
           "overall_counts_primary": _cls_counts(oracle_rows, "primary_class"),
           "unknown_reasons_primary": dict(sorted(Counter(r["primary_reason"] for r in oracle_rows if r["primary_class"] == "UNKNOWN").items())),
           "infra_error_reasons": dict(sorted(Counter(r["primary_reason"] for r in oracle_rows if r["primary_class"] == "INFRA_ERROR").items())),
           "cells": cells, "stage2_topup": None, "stage2_topup_verified": exp2}
    if "A" in exps:
        res["E39_A"] = analyze_A(cells, oracle_rows, reps, seed)
        if a.stage == 1:
            res["stage2_topup"] = topup_from_cells(_cells_of(cells, "A"))
    if "B" in exps:
        res["E39_B"] = analyze_B(cells, oracle_rows, res.get("E39_A"), parse_w_external(a.w_external))
    if "C" in exps:
        res["E39_C"] = analyze_C(oracle_rows, reps, seed)
    if "D" in exps:
        res["E39_D"] = analyze_D(cells, oracle_rows, reps, seed)
    if "E" in exps:
        res["E39_E"] = analyze_E(cells, oracle_rows, reps, seed)
    if "F" in exps:
        res["E39_F"] = analyze_F(cells, oracle_rows, reps, seed)
    if "G" in exps:
        res["E39_G"] = analyze_G(cells, oracle_rows)
    res = _sanitize(res)
    write_outputs(outd, res, oracle_rows)
    print("E39 analysis written to %s (stage %d, experiments %s, %d trials, confirmatory=%s)"
          % (outd, a.stage, ",".join(exps), len(used), res["confirmatory"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
