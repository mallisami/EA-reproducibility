# Experiment 9: E42 benign target-churn follow-up

Current publication mapping: `docs/claims_to_evidence.csv` and the package README. The campaign description below retains the historical study context and may use earlier figure/section numbers.


> **Current-paper mapping (2026-10-02):** This study is reported as the **benign target-churn conflict-cost study** supporting current RQ2 and Supplement S16/Table S30.

**Role:** prospective availability-cost stress test supporting RQ2. **Analyzed trials:** 702.

Across the fixed benign-churn grid, UID/resourceVersion direct binding produced 81/312 resourceVersion aborts (26.0%; Wilson 95% CI 21.4-31.1%) after security-irrelevant target annotation mutations, whereas UID-only deletion produced 0/312. Both arms had 0/39 resourceVersion aborts in no-write controls. The pooled 26.0% is a descriptive fixed-grid stress summary, not a production conflict-rate estimate.

`data/raw/E42_RUNTIME_PUBLIC_REDACTED.tar.gz` is reconstructed from the original source runtime archive by dropping exactly 18 credential/private-key members; 46 runtime members are retained byte-for-byte. Re-running the frozen E42 analysis from the public-safe raw archive regenerates both packaged processed outputs byte-for-byte.
