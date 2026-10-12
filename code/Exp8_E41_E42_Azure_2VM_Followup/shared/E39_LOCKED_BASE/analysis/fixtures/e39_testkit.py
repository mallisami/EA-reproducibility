"""Helpers shared by the E39 analysis tests (synthetic data only)."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import oracle_e39 as orc  # noqa: E402
import synth_e39 as syn  # noqa: E402

SEED = 3926099
# deterministic timing: no jitter on commits
DET = dict(w_commit_sd=0.0, d_commit_sd=0.0, rel_jit=0.0)


def entry(experiment, arm, cond, impl="python", delta=None, lam=None, trial_id=1, **kw):
    raw_arm = syn.RAW_ARM[(arm, impl)]
    raw_cond = "poisson_scope" if experiment == "B" else syn.RAW_COND[cond]
    e = {"trial_id": trial_id, "stage": 1, "block": 1, "experiment": experiment, "arm": raw_arm,
         "condition": raw_cond, "delta_ms": delta, "rep": 0, "schedule_seed": SEED, "client_impl": impl,
         "lambda_per_s": lam, "action_type": "netpol_create" if experiment == "E" else "pod_delete"}
    e.update(kw)
    return syn._canon(e)


def one(experiment, arm, cond, impl="python", delta=None, lam=None, P=None, seed=1, **kw):
    """Simulate ONE trial and classify it. Returns (row, truth, raw, audit, webhook)."""
    ctx = syn.Ctx(seed=seed)
    rng = random.Random(seed)
    p = dict(DET)
    p.update(P or {})
    raw, audit, wh, tr = syn.simulate_trial(ctx, entry(experiment, arm, cond, impl, delta, lam, **kw), rng, p)
    row = classify(raw, audit, wh)
    return row, tr, raw, audit, wh


def classify(raw, audit, wh, pod_events=None):
    return orc.classify_trial(raw, orc.index_audit(audit), orc.index_webhook(wh), orc.index_pod_events(pod_events))


def make_run(tmp, experiments, mini=True, seed=SEED, P=None, plan_text="E39 test analysis plan\n", schedule=None):
    """Synthesize a run directory + minimal LOCK. Returns dict of paths."""
    tmp = Path(tmp)
    rd = tmp / "results" / "e39-synth"
    sch = schedule or syn.build_schedule(experiments, seed, mini=mini)
    raws, audit, whl, truth = syn.simulate(sch, seed, P=P)
    syn.write_run(rd, sch, raws, audit, whl, truth)
    plan = tmp / "protocol" / "ANALYSIS_PLAN_E39.txt"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(plan_text, encoding="utf-8")
    lock = syn.write_lock(tmp, rd / "SCHEDULE.json", plan, seed, sch["run_id"])
    return {"root": tmp, "run": rd, "lock": lock, "plan": plan, "schedule": sch, "raws": raws, "audit": audit,
            "webhook": whl, "truth": truth}


def rewrite_raw(run, raws):
    with open(Path(run) / "RAW_TRIALS.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for r in raws:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def analyze_args(paths, out, *extra, experiments=None, stage=1, reps=200, design="off"):
    a = ["--run-dir", str(paths["run"]), "--lock", str(paths["lock"]), "--lock-root", str(paths["root"]),
         "--analysis-plan", str(paths["plan"]), "--stage", str(stage), "--out", str(out),
         "--bootstrap-reps", str(reps), "--design-check", design]
    if experiments:
        a += ["--experiments", experiments]
    return a + list(extra)
