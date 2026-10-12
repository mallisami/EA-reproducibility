# Experiment 8: E41 Azure two-VM multi-host follow-up

Current publication mapping: `docs/claims_to_evidence.csv` and the package README. The campaign description below retains the historical study context and may use earlier figure/section numbers.


> **Current-paper mapping (2026-10-02):** This study is reported as the **two-host mechanism/transportability study** supporting current RQ1-RQ2 and Supplement S16. It is not pooled with the primary campaigns.

**Role:** prospective multi-host follow-up supporting RQ1-RQ2. **Analyzed trials:** 2,955.

The study repeats client revalidation, admission recheck, and direct target binding on two distinct K3s hosts across scope, application-policy, and evidence invalidations over 0-200 ms. The frozen primary classifier reported 233/936 asynchronous invalid executions for the client gate and 199/936 for admission rechecking. For direct binding, 110/936 cases could not be fully resolved on the primary audit path because writer responseObjects had rotated out. The independent Pod-event recorder matched all 110 to the pre-specified terminating-Pod non-commit signature; the secondary sensitivity therefore classified 0/936 direct-binding asynchronous trials as invalid, with 105 correct resourceVersion aborts and six UID-blocked controls.

This experiment is **supporting evidence**, not a pooled replication of the primary zero-event estimate. The sensitivity does not recreate missing audit responseObjects.

`data/raw/E41_RUNTIME_PUBLIC_REDACTED.tar.gz` is reconstructed from the original source runtime archive (SHA-256 recorded in `provenance/SOURCE_RUNTIME.sha256`) by dropping exactly 18 credential/private-key members; 59 runtime members are retained byte-for-byte. `data/processed/` is regenerated from that public-safe archive and matches the October-1 paper-facing E41 cell/exposure and sensitivity outputs byte-for-byte.
