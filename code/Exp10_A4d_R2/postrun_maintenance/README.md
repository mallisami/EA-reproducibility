# A4d R2 post-run maintenance fix

This is a **post-run utility correction**, not a replacement prospective experiment package and not a new scientific lock.

It contains only two corrected utility scripts from the R2 package:

1. `A4D_CONFIRMATION/analysis/summarize_a4d.py`
   - selects normalized analyzer cells using parsed arm `A4d` (and remains compatible with `A4d_commit_direct`);
   - prevents the derived summary from silently reporting zero trials.

2. `ops/make_return_bundle.sh`
   - excludes `RETURN_MANIFEST.sha256` from its own checksum enumeration;
   - prevents the manifest self-hash defect caused by shell redirection creating an empty manifest before enumeration.

The original R2 execution package and its prospective locks must remain immutable as the package actually used for the completed run. Apply these script fixes only to a maintenance/reproducibility copy when regenerating derived summaries or future return bundles.
