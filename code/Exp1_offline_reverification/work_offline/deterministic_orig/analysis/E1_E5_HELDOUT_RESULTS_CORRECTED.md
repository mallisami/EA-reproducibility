# FlashIR E1/E5 — Corrected Authoritative Held-Out Report

This file supersedes the original incident-level confirmatory report archived under `provenance/superseded_statistics/`. Raw E1/E5 rows were never changed.

## Population
- 23 positive held-out scenario templates (92 nested P1-P4 incident variants)
- F5.6 is the negative control
- scenario template is the inferential cluster

## E1
OneShot is unsafe on **23/23** positive held-out W2 templates (92/92 nested variants). The template-level Wilson 95% interval is approximately **[0.857, 1.000]**. This is designed-positive benchmark vulnerability, not real-world prevalence.

## E5 primary W2
- B0: 23/23 unsafe templates
- B2 TTLGuard: 0/23 unsafe
- B3 BindingGuard: 0/15 unsafe where applicable
- B4 FullRefresh: 0/23 unsafe
- B5 NativePrecondition: 0/5 unsafe where applicable
- B6 DCJI-Gate: 0/23 unsafe

## Correct confirmatory inference
- C1 B6 vs B0: exact paired-template p = **2.384185791015625e-07**, Holm-adjusted **7.152557373046875e-07**.
- C2 B6 vs B2: p = **1.0**.
- C3 B6 vs B3 on 15 applicable templates: p = **1.0**.
- C4 B6 vs B4: identical observed safety, but **the preregistered absolute 5% noninferiority margin is NOT established**. A conservative one-sided 95% upper bound for B6-only unsafe-template discordance is **0.1221**, above 0.05.
- C5 B6 vs B4 query cost: mean paired difference **-0.6522** normalized queries; one-sided template-level Wilcoxon p = **0.0048269374**; bootstrap mean-difference 95% interval **[-1.1739, -0.2609]**.

Authoritative machine-readable statistics: `analysis/confirmatory_comparisons_cluster_corrected.json`.
