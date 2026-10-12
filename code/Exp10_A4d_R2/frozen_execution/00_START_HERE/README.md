# A4d audit-safe confirmation R2 — start here

This package is a **targeted confirmation only**. It exists because the earlier E41 multi-host run completed scientifically, but kube-apiserver audit rotation removed part of the primary A4d responseObject history before collection.

This package does **not** rerun A1 or A3. It runs only direct target binding (A4d):

- 936 asynchronous main trials;
- 39 no-write controls;
- 10 UID-rebinding controls;
- **985 main trials total**;
- a separate 26-trial smoke run, excluded from the main analysis.

The primary fix is instrumentation, not a changed scientific hypothesis:

1. kube-apiserver source audit retention is raised to 1024 MiB/file, 20 backups, 30 days;
2. an independent append-only `tail -F` audit stream starts before the live preflight and remains active through the final trial;
3. an online guardian stops the runner if the audit stream or Pod-watch recorder disappears, disk becomes unsafe, or required evidence is still missing after the grace interval;
4. after the run, **every required writer and DELETE Audit-ID must pass the evidence-completeness gate before analysis**;
5. a successful DELETE always requires its Audit-ID ResponseComplete record; if that response omits `metadata.resourceVersion`, exactly one UID-matched `DELETED` Pod-watch resourceVersion is the only accepted fallback. Missing, duplicate, malformed, GAP/ERROR, or conflicting fallback evidence fails the gate.

Use `AZURE_TWO_VM_QUICKSTART.md` exactly. Do not change schedules, seeds, sample counts, delays, mechanisms, classifiers, or analysis code after main data begins.


## R2 correction

The first audit-safe package incorrectly required every successful DELETE ResponseComplete object to carry `metadata.resourceVersion`, even though the inherited locked oracle and Kubernetes behavior already account for a 200/202 response that can omit it. R2 changes only the instrumentation acceptance path: it preserves the mandatory DELETE ResponseComplete record and permits the pre-existing UID-matched single `DELETED` watch-event resourceVersion fallback. The A4d mechanism, conditions, delay grid, repetitions, controls, and classifier semantics are unchanged. R2 uses new run IDs so no stopped-run artifacts can be mixed with the final rerun.
