# DCJI re-analysis and offline fixes — consolidated results (2026-09-26)

Workspace: VM `~/dcji_reanalysis_20260926` (Azure Standard_D4as_v5, 4 vCPU AMD EPYC 7763, 16 GB, Python 3.12.3).
Inputs read-only; SHA-256 verified (frozen_inputs.tgz 172230c0…, e38_inputs.tgz e43b5eb2…, E38 RAW_TRIALS 3202e352…).
No cluster was touched. All estimators beyond protocol endpoints are POST-HOC.
Detailed outputs: `latency/out/` (17 CSVs), `e38/out/` (16 CSV/JSON), `offline/out/offline/` (patch.diff, patched core, tests, CSVs, logs).

## 1. Latency (E21, E37, E35, E26, E25)

Definitions: ΔL = dcji_refresh_ms − full_refresh_ms (negative favours DCJI). Refresh timer includes json.loads; in parallel mode also ThreadPoolExecutor create/shutdown. Per-GET latency = urlopen+read.

Verification: all reported values reproduce (E21 cell means −11.739…−128.896; 487 reversals; E37 −22.203…−9.017, 78 reversals, pooled −14.405; E35 −16.806; E26 −66.32; all 24 V25 strata).

Recommended primary estimator: Hodges–Lehmann (HL) with 95% bootstrap CI (10,000 resamples, seed 20260926).
- E37 (no GET > 250 ms): sequential HL −15.4 … −20.3 ms; parallel HL −8.6 … −11.6 ms; all CIs exclude 0; pooled median −13.32, HL −13.35.
- E21: HL −10.91 … −59.49 ms, all CIs exclude 0; pooled median −21.49 vs mean −46.95 (idle/sequential means up to 3× HL).
- E35: HL −15.10 [−15.52, −14.72]. E26: HL −31.05 [−33.40, −28.73] (mean −66.3 stall-inflated).
- Stall exclusion (any GET > 250 ms): E21 drops 112/3,600 pairs; mean −46.95 → −30.62; median −21.49 → −21.20; per-cell HL shifts ≤ 2 ms. E37/E35 drop 0.
- Parallel-mode harness overhead = ~22% (E37) / ~24% (E21) of median saving; sequential overhead ~1%.
- Guard-order strata: E21 three strata CI span zero (all DCJI-first; one sign reversal +22.5 ms); medians all negative. E37: 0/24. Consistent first-position penalty (+1.3…+20.9 ms in 12/12 E21 cells; 10/12 E37). E25 (DCJI always first, 6 vs 6): HL +4.89 [−0.60, +10.25].

Cost model:
- Sequential: saving ≈ 3c (c = per-read latency); validated on E37/E35 (observed/predicted 1.02–1.07; residual ±0.4 ms).
- Parallel: i.i.d. order-statistic model under-predicts ~5× (per-GET latency rises with concurrency: 5.0 → 8.65 → 13.58 ms at 1/3/6-way). Bound: 0.31c (independent) ≤ saving ≤ 3c (serialized).
- Projection (model-based, not measured): c = 5/20/50/200/1000 ms → sequential 15/60/150/600/3000 ms (50% of FullRefresh time).

New disclosures required:
1. E21 restarted after cell 4; RNG re-seeded → cells 5–8 repeat order sequences of cells 1–4; restart not logged (infra_failures.jsonl empty). Manuscript "not replayed" statement is wrong.
2. E37 used the same seed as E21: 66.9% of trial positions share guard order; not independently randomized.
3. E21 load confounded with time (idle cells first and slower; 75/83 >1 s GETs in idle half): do not interpret load.
4. 13 E21 trials had refresh > 1 s with no GET > 1 s (overhead up to 430 ms).

## 2. E38 (56-trial smoke, run e3820260919094024)

Outcomes (reps agree in every cell): 8 valid controls; 22 correct aborts; 4 UID 409; 12 post-gate invalid accepts; 2 DCJI symmetric gate misses (FullRefresh aborts); UNKNOWN_COHERENCE = 8 (4 open-epoch, all use-time valid ⇒ unnecessary aborts; 4 during-acquisition, both methods would have executed invalidly without the fence).

Latency (exclude during_acquisition, n = 24/method): refresh DCJI 15.19 vs FullRefresh 21.06 ms (median 14.46 vs 19.85); gate 16.78 vs 22.78 ms. Condition-matched ΔL refresh −5.87 [−6.63, −5.10], gate −6.00 [−6.78, −5.21]. DCJI faster in 23/24 rep pairs. Max guard-start→effect excluding injector: 48.04 ms (trial 008), not 69.62.

Planning cost: median ~34 ms (VM) vs DCJI gate 16.8 ms ⇒ DCJI advantage requires a precomputed/amortized plan.

Horizon: 25 goals, certificate {a,c,e,uid,scope} at 5/10/30/60 s (saturated; 19 goals at 2 s, 2 at 1 s, 0 at 0.5 s).

Exposure windows (natural EXECUTE trials, mean ms, DCJI / FullRefresh): gate end→DELETE send 0.009 / 0.016; last evidence GET→DELETE send 9.26 / 9.16; scope GET→DELETE send 3.49 / 3.77; app-policy GET→DELETE send 5.26 / 5.40; DELETE round trip 12.28 / 17.34; DELETE response→effect 2.10. 409s return in ~2.3 ms (UID only is server-enforced).

E38v2 sizing: δ ∈ {−5,−2,−1,0,0.5,1,2,3,5,10,20,50} ms after gate end; arms DCJI, FullRefresh, DCJI+validating admission webhook; 3 single-PATCH async-writer conditions + no-write + UID controls; oracle from etcd resourceVersion + apiserver audit timestamps. Two-stage n = 39 per cell, top up to 93 at transition δ (pre-registered 0.05 < p̂ < 0.95): ~5,877 trials, ~3.5–4 h. Observed 2.347 s/trial (2.07 non-UID, 4.01 UID).

## 3. Offline planner fixes and tests

Baseline: 15/15 tests pass; E1, E3, E4, E5 (1,856 rows), E10, E11 and pilot-68 regenerate identical to frozen. Dev matrix differs only in scenario hash (`scenario_role` key added after generation). No runner for e3_long_horizon.json or E12 harness.

Patch (patch.diff, 538 lines): (a) fail-closed — FullRefresh fallback only if enumeration complete AND FullRefresh provably rejects all goals, else ABORT_REPLAN; (b) lazy best-first subset enumeration, cap enforced during generation, exact-fraction costs, identical tie-break; (c) optional zero-cost observability validator (0 violations in all 98 specs).

Regression: every frozen artefact identical under patched core; 23/23 tests (8 new). New test reproduces the C1 fail-open (locked core EXECUTEs an attack goal; patched aborts). Cap: n = 24, cap 1000 → 1000 evals, < 5 s, < 64 MiB (locked core: MemoryError). Lazy = exhaustive on 600 random cost vectors and 528 + 200 random scenarios.

Minimality (independent evaluator, full query universe incl. zero-cost): 0 cheaper feasible subsets in 192 held-out, 136 pilot, 12 dev rows and 73 multi-goal F5 variants (55 with >1 goal, up to 81 goals). P1–P4 confirmed identical ⇒ 24 independent held-out cases.

Stronger tests (unsafe executes / false aborts):
- All reachable states: DCJI 0/0 (held-out 85, pilot 264); FullRefresh 0/0; OneShot 23 and 68 unsafe.
- Out-of-model positive-cost mutations: DCJI unsafe 91/120 (held-out), 285/390 (pilot); FullRefresh 0. Zero-cost mutations: both equally blind.
- Paper counterexample (16 hypothetical states): DCJI 3 unsafe / 2 false aborts; FullRefresh 0/0.
- Pilot W0–W3: 272/272 correct.
TTL sweep (staggered ages): ≤ 2 s safe but cost ≈ FullRefresh (1.53–1.88 vs DCJI 1.21); 5 s misses 47–177/1,150; 10 s misses 643–798/1,150.
Scaling (new harness, VM): E12c n = 16 locked 5.83 s vs patched 7.01 s; n = 20 cap 1000 locked 4.2 s/312 MB vs patched 0.12 s/16 MB; n = 24 locked MemoryError vs patched 0.09 s. E12 linear sweep super-linear ⇒ O(|S|+|T|) unsupported.

Supportable claims: fail-closed (patched core, relative to the model); minimality over the full candidate universe on all tested instances; bounded search under cap.
Not supportable: O(|S|+|T|); robustness to out-of-model writes; authorization re-check; TTL comparison beyond degenerate setting.
