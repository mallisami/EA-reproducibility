# Locks and hashes

## Experiment locks
Each locked experiment froze its code, deploy manifests, protocol, analysis plan and schedule with SHA-256 before the first trial. The analyses refuse to run when the LOCK does not verify. L2 re-verifies every locked file against `code`. The only differences are the documented substitutions in `LOCK_SUBSTITUTIONS.json`.

| Experiment | LOCK file | Status / created (UTC) | Schedule seed (stage 2) | Run ID | lock_sha256 | Schedule SHA-256 (stage 1 / stage 2) | Analysis plan SHA-256 | Locked file entries (identical in package) |
|---|---|---|---|---|---|---|---|---|
| E38v2 preliminary | `experiments/Exp5_E38v2_Actuation_Exposure/Preliminary/protocol/LOCK_E38v2.json` | LOCKED 2026-09-26T08:17:00Z | 1772720255 (1772720255) | e38v2-20260926t0813z | 15b29b6d...28dd2 | 478ecd47... / d58313bf... | 6397ad46... | 48 (47 + 1 redacted runbook) |
| E38v2 confirmatory | `.../Confirmatory/protocol/LOCK_E38v2.json` | LOCKED 2026-09-26T10:34:43Z | 154080643 (154080643) | e38v2r-20260926t1032z | 7672ddda...0ae5f | eb9262b4... / 3ededba8... | 3ea47ec2... | 50 (49 + 1 redacted runbook) |
| E39 A-G | `experiments/Exp6_E39_Commit_Bound_Enforcement/protocol/LOCK_E39.json` | LOCKED 2026-09-26T15:22:23Z | 972231175 (972231175) | e39-20260926t1515z | 36cec6d7...b0de0 | 295219f3... / 4a9df683... | 3c8115c3... | 80 (78 + redacted runbook + withheld Go binary) |
| E40 (exploratory) | `experiments/Exp7_E40_AKS_Replication_Exploratory/protocol/LOCK_E40.json` | LOCKED, created 2026-09-26T17:44:58Z | 40926017 | a20260926 | (plain file lock) | da04cb59... | 3c8115c3... (the E39 plan) | 65 (64 + withheld pylib.tgz) |

The full hashes are in the LOCK files. Lock verification records made during the runs:
- E38v2: `provenance/LOCK_VERIFY_STAGE1.txt`, `LOCK_VERIFY_STAGE2.txt`, `LOCK_VERIFY_AT_EXPORT.txt` (confirmatory: "OK: 53 hashes verified, 0 problem(s)").
- E39: `provenance/lock_verify_before_{A,G,E,F,B,D,C,A}_*.txt` (before each sub-experiment) and `LOCK_VERIFY_AT_EXPORT.txt` ("OK: 84 hashes verified, 0 problem(s)").
- E40: `protocol/COPY_SHA256_E39.txt` (the E40 code copy checked against the E39 lock), `provenance/INCLUSTER_SHA256.txt` (hashes of the code as mounted in the cluster).

## Raw exports
| Export | Original SHA-256 (authors' archive) | Package copy (redacted) | Member manifest |
|---|---|---|---|
| E38v2 preliminary | 1a3cb076d7e74fbd87442fe8a2d38ab0a8428da5c94d0b312aa2e219bf32f355 | see `RAW_DATA_HASHES.csv` | `Preliminary/provenance/REDACTION_MANIFEST_e38v2-20260926t0813z.csv` |
| E38v2 confirmatory | ed57dd4802931e326f76e41498c66c697990a17313b192df7bfc1096e5798aa0 | see `RAW_DATA_HASHES.csv` | `Confirmatory/provenance/REDACTION_MANIFEST_e38v2r-20260926t1032z.csv` |
| E39 | f8ae9c65fbbeffb8ab327df8f25e7bde8e8ca6ec4857af44c65c48980623b641 | see `RAW_DATA_HASHES.csv` | `Exp6.../provenance/REDACTION_MANIFEST_e39-20260926t1515z.csv` |
| E40 | fffe73c3bcdd6b5da5485cab12b94035ca5af4d96bc6e752d8dbf34db16ee047 | see `RAW_DATA_HASHES.csv` | `Exp7.../provenance/REDACTION_MANIFEST_E40_a20260926.csv` |

The redaction manifests list, for every member of each export, its original SHA-256 and the SHA-256 in the package copy. L0 recomputes them. Every member except those marked REDACTED, REPACKED_NESTED or DROPPED is byte-identical to the original export. Each export also contains its own `SHA256SUMS` written at export time; unchanged members verify against it directly.

## Frozen evidence (Experiments 1-4 and the supplementary studies)
The files were copied verbatim from the complete research archive (`Frozen_V29`). `FROZEN_EVIDENCE_MAP.csv` gives the original archive path, the SHA-256 and the package path of all 496 extracted files; all are included. The frozen studies carry their own manifests: for example `Exp1.../provenance/frozen_v8.1.1/CONTROLLED_RESULTS_LOCK.sha256.json` and `FROZEN_INPUTS.sha256.json`, the protocol hashes `*_PROTOCOL.sha256` and the input hashes `*_INPUTS.sha256` inside each study folder.

## Code bundles
`CODE_BUNDLE_MAP.csv` maps the authors' code bundles (`09_REPRODUCIBILITY_PACKAGE/code_*.tar.gz`, with their SHA-256) to the `code` folders, and lists the exclusions.

## Package checksums
`CHECKSUMS.sha256` at the package root lists every file (`sha256sum -c` format). Verify it with `python scripts/L0_verify_checksums.py`, with `sha256sum -c CHECKSUMS.sha256` (Linux) or with `scripts/L0_verify_checksums.ps1` (Windows, no Python needed).
