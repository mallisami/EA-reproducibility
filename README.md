# Freshness Is Not Binding — reproducibility package

This package accompanies **Freshness Is Not Binding: Evidence-to-Action Races and Conditional Enforcement in Kubernetes Incident Response**. It uses **Discover_Computing_Journal_Figures_2_5_2026-10-12_V3.zip** as the current paper reference and **V3(1).zip** as the source of experimental evidence. The paper and Supplement are not included and have not been edited.

The package preserves the supplied scientific source, inputs, protocols, schedules, raw observations, canonical results, and documented deviations. Editorial/reviewer audits, manuscript builders, obsolete publication snapshots, packaging history, Python caches and disposable console/test output are excluded. Numerical plotting sources and integrity metadata remain because they support reproduction.

**Kubernetes audit records are required experimental observations in this study.** The raw observations, `audit.log`, Pod-event records and webhook records are retained. The E40 export is repacked only to remove four compiled Python caches; its retained member bytes are unchanged and the source/clean archive hashes are recorded in `provenance/archive_cleanup.json`. Other raw export archives retain their supplied bytes. Removing these would prevent independent resourceVersion ordering and protected-boundary challenge attribution. These records are different from editorial or administrative audit reports.

## Layout

| Directory | Contents |
|---|---|
| `code/` | Frozen scientific engines, classifiers, analysis code, live runners, manifests and deployment protocols, grouped by campaign |
| `experiments/` | Supplied raw data, canonical processed outputs, schedules, protocols and experiment-specific provenance |
| `scripts/` | Portable package verification, fixed-data replay and current main-figure generation |
| `data/figure_inputs/` | Numeric inputs for current Figures 2–5 and chronological sensitivity Table S33 |
| `reference_figures/` | Exact supplied main and Supplement artwork, without manuscripts |
| `environment/` | Historical campaign settings, original requirements, and the environment used for this package's offline validation |
| `provenance/` | Raw/input hashes, frozen-source identities, documented redactions and lock substitutions |
| `docs/` | Claim-to-evidence mapping, campaign index, deviations, live-run instructions and compact verification status |
| `outputs/` | Created when running reproduction; not distributed |

## Install and run

Use Python **3.12**. Run these commands from the extracted package root. A virtual environment is recommended.

```bash
python -m venv .venv
```

Activate with `source .venv/bin/activate` on Linux/macOS, or `.venv\Scripts\activate` in Windows Command Prompt, then:

```bash
python -m pip install -r environment/requirements/requirements-analysis.txt
python scripts/reproduce.py
```

The default command verifies every distributed file and the raw archive members, checks current numerical figure/table inputs and scope-campaign accounting, then regenerates Figures 2–5. It needs no Kubernetes cluster, cloud account or network connection after dependencies are installed.

For all scripted fixed-data analysis replays:

```bash
python scripts/reproduce.py --full
```

Allow roughly **30–60 minutes** for a full sequential replay, depending on CPU and memory. Allow at least **4 GB free RAM and 4 GB working disk space** in addition to the extracted package. The E39 audit evidence is large. Linux was used for validation; Windows path resolution is supported by the integration wrappers but a full Windows replay was not executed. Extract to a short path, for example `C:\repro`.

Individual replays are also available:

| Command under `python scripts/` | Evidence reproduced |
|---|---|
| `L0_verify_checksums.py` | Complete inventory, raw export hashes and member-level redaction integrity |
| `verify_figure_inputs.py` | Current Tables S1/S8/S8a/S10a/S33 values versus canonical records, including raw deletion-marker revisions |
| `L2_rerun_deterministic.py` | E5 held-out and 68 follow-up W2 results under original and patched engines; independent T4/T5 checks; TTL sweep |
| `L2_rerun_analysis.py` | E38v2 preliminary/confirmatory, E39, E40 and physical-acquisition latency reanalysis |
| `L2_rerun_followups.py` | E41 two-host study and E42 benign churn |
| `L2_verify_a4d_r2.py` | A4d R2 confirmation and complete-observation evidence gate |
| `L2_verify_scope_strengthening_E43_E50.py` | Prospectively fixed E43–E50 campaign accounting and headline checks |
| `L2_regenerate_scope_strengthening_outputs.py` | All 22 E43–E50 summary/cell files from 11 trial directories |
| `L2_verify_E52_forced_contest.py` | E52 classification with the unchanged E39 oracle |
| `make_main_figures.py` | Current Figures 2–5, written to `outputs/figures/` as PDF and 600-dpi PNG |

To select a main-campaign step, use, for example, `python scripts/L2_rerun_analysis.py --steps e39`. Do not run the same output directory concurrently. Use `--output-dir outputs/my_run` for separate main-campaign replays.

## Evidence coverage

| Campaign | Retained scope |
|---|---|
| Exp1 | Designed-state verification, E5 held-out specifications, 68 W2 follow-up specifications, T4/T5, TTL and recorded planner scaling |
| Exp2 | E21/E25/E26/E35/E37 physical-acquisition measurements and robust latency reanalysis |
| Exp3–4 | E34/E36 running-Pod decisions and constructive E38 smoke study |
| Exp5 | Both E38v2 runs; the confirmatory campaign has 8,728 trials |
| Exp6 | E39 A–G, 20,734 trials and frozen resourceVersion classifier |
| Exp7 | E40 managed-control-plane exploratory study, 2,019 trials |
| Exp8–9 | E41 two-host study, 2,955 trials; E42 benign churn, 702 trials |
| Exp10 | A4d R2 main run, 985 trials and 1,921 required critical audit records |
| Exp11–16 | E43/E46/E47/E48/E49/E50, 12,127 primary trials plus a separate 30-trial serial sensitivity check |
| Exp17 | E52, 10,144 trials, including 9,360 asynchronous trials and 271 writer-attributed challenges |
| Supplement diagnostics | Retained E13 and E23–E33 diagnostic studies, runners and results |

Exact claim locations and evidence paths are in `docs/claims_to_evidence.csv`. `docs/campaigns.csv` identifies the code and result directories. Internal historical filenames are retained where changing them would break frozen imports or lock verification.

## Interpreting exact reproduction

**Fixed-data replay** recomputes results from the supplied observations. Counts, decisions, certificates and scientific summary values are compared with the canonical files. CSV comparisons are exact where possible, otherwise use the original numerical tolerance of 1e-9. Machine-specific paths, generation timestamps and measured execution times are not scientific identity requirements; selected JSON comparisons state which scientific fields are checked. Original and regenerated data are kept separate.

**New live experiments** collect new observations. Race timing and latency depend on scheduling, machine load, topology and Kubernetes versions; identical new numerical samples cannot be promised. This package does not change the published measurements to match a new execution. Live rerun instructions are in `docs/live_reexecution.md`; no live clusters were created while preparing this release.

Current Figures 2–5 use the updated paper's plotting/statistical logic with only input/output adaptation: standalone CSVs replace Supplement parsing, and fresh output files replace writes into the paper folder. All four regenerated PNGs matched the supplied updated PNGs pixel-for-pixel. Figures 1 and Supplement schematics remain supplied artwork; this package does not claim a programmatic regeneration path for every illustration. Historical statistical plotting sources are retained in `code/supplement_plot_sources/` with their original input descriptions.

The E39 wrapper hashes the complete extracted audit member before analysis and stages it under `audit.jsonl`. Its readers are pointed to that file without changing the frozen classifier or record bytes. This avoids relying on a truncated temporary log copy and fails if extraction is incomplete. The compressed source archive is unchanged.

## Recorded boundaries

- E40 lacks API-server audit input and remains exploratory. E41 has an audit-retention limitation; its separately labelled Pod-event sensitivity does not reconstruct the missing audit responseObjects. The separately packaged A4d R2 complete-observation campaign remains distinct.
- E52's original timing-only diagnostic and frozen-oracle reconciliation are both preserved. E52 is reported separately from the primary E39 challenge set and is not pooled with it.
- E51/E53 were execution-limited attempts; E55 is a read-back diagnostic. Their supplied summaries are retained under `experiments/Supp_Execution_Limited_Attempts_E51_E53_E55/`; their bounded disposition is retained under the Exp17 provenance directory. This package does not present them as completed confirmatory campaigns or invent absent raw exports.
- The supplied Go binary and E40 third-party wheel bundle were withheld in the source package. Their source/build requirements and historical hashes remain; neither is needed for offline replay. Original runbooks contain documented privacy redactions. See `provenance/LOCK_SUBSTITUTIONS.json` and `REDACTION_REGISTER.csv`.
- Recorded planner timing and the earlier supplemental live/diagnostic studies are retained. The portable default does not rerun expensive uncapped planner-scaling cells or reconstruct new physical measurements. Historical scaling scripts describe their host-specific setup.
- Historical validation scripts may reference older, broader frozen-result inventories. Current package checks and paper-relevant replay commands above define this release's verified scope; see `docs/verification_summary.json`.

No published scientific claim, input observation or canonical result was altered during cleanup. License selection and publication identifiers remain author-controlled; the supplied private-review licensing status is preserved in `LICENSE_STATUS.txt`.
