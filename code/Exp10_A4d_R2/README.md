# Experiment 10 code provenance

- `frozen_execution/` is the exact extracted prospective R2 execution package used for `a4dcr-r2-20261001` (source archive SHA-256 `2b583766fed2d7a3a5770ccf43603a488649cfb44844f28179abf5e7076009d5`). Its scientific locks remain immutable.
- `postrun_maintenance/` contains only the later utility fixes for `summarize_a4d.py` and `make_return_bundle.sh`, their diffs, and the regression check. These fixes do not alter the runner, oracle, audit-evidence rule, schedules, locks, or trial data.

For offline replay use `05_Reproduce/L2_verify_a4d_r2.py`; it deliberately analyzes raw results with the frozen execution code and uses the maintenance summary utility only for the corrected derived summary.
