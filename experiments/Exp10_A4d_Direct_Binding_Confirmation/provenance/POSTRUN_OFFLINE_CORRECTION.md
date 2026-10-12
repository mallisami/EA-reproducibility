# A4d R2 post-run offline correction record

Run: `a4dcr-r2-20261001`

This corrected return bundle preserves the completed experimental evidence and applies two offline packaging/derived-summary corrections only. No trial, raw record, audit record, Pod-watch record, schedule, protocol, or primary analysis output was changed.

## 1. Derived confirmation summary

The original `A4D_CONFIRMATION_SUMMARY.json` selected cells using the stale literal `/A4d_commit_direct/`. The inherited analyzer emits normalized labels such as `A/A4d/python/async_scope/0`, so the derived summary incorrectly reported `n=0` and an empty cell list.

The corrected summarizer parses the cell label and accepts the normalized `A4d` arm (while retaining compatibility with `A4d_commit_direct`). Regeneration from the already-produced `analysis_a4d/cells_E39.csv` yields:

- 985 primary-denominator trials across 26 cells;
- 0 invalid executions;
- 0 correct aborts;
- 0 false aborts;
- 0 unknown outcomes;
- 0 resourceVersion-precondition 409s in the main A4d schedule.

The authoritative primary counts remain 975 `VALID_EXECUTED` and 10 `UID_BLOCKED`, with 0 `INFRA_ERROR`.

The original buggy summary is retained as `A4D_CONFIRMATION_SUMMARY_ORIGINAL_BUGGY.json`.

## 2. Return manifest

The original return-bundle script redirected output into `RETURN_MANIFEST.sha256` while simultaneously enumerating all files, causing the manifest to hash its own initially empty state. The corrected generator excludes `RETURN_MANIFEST.sha256` from its payload list. The original manifest is retained as `RETURN_MANIFEST_ORIGINAL_BUGGY.sha256`.

All original payload entries other than the known self-entry and the intentionally regenerated canonical summary were rechecked against the original manifest and passed. The preserved original summary also matches its original recorded digest.

## Evidence gate and fallback usage

`AUDIT_EVIDENCE_COMPLETENESS.json` remains unchanged and reports 1921/1921 required critical audit records validated, zero errors, and PASS. Successful DELETE resourceVersions were sourced 958 times from audit responses and 17 times from the UID-matched Pod-watch DELETED fallback.

## Provenance hashes

- Original returned TAR.GZ: `33a8844542f546c13cc091d83abf1ec808e17ccbcaa89d6f8cd2f21008912cb5`
- Original buggy summary: `9d4809ec4f2595d9cb20dea6ed68b85516c9d4b93eff5e69ee402fcba725ec39`
- Corrected summary: `d0ebf8c10e8d10465191b9da0832eb906c4522e18294727eb9ea39287b54e3b7`
- Authoritative `results_E39.json`: `b31afd0aefe12b2e50afa7b779349fad3f798da14a32fb19d3011999beb32d6a`
- Authoritative `cells_E39.csv`: `2f5bafa63a9c65f56ba049d6fff3c03c452b4cef99e2d67ffb35d8a04cd9f840`
- Raw trials: `c108beaf23b3fb6fcc35b8f6161a3615425e74a281a7c9aea4bcf3229e9553c1`

These corrections change derived packaging artifacts only and do not constitute a rerun or a new scientific analysis.
