#!/usr/bin/env bash
# One command for the six frozen scope-strengthening experiments: E43, E46, E47, E48, E49 (x5 clusters), E50.
# Safe to re-run after an interruption: finished steps are skipped and runs resume.
set -euo pipefail
cd "$(dirname "$0")"
P=.venv/bin/python
ulimit -n 65535 2>/dev/null || ulimit -n 4096 2>/dev/null || true
mkdir -p runs
[ -x "$P" ] && kubectl get nodes >/dev/null 2>&1 || ./setup.sh

wait_ns() {
  kubectl get ns ea-followup >/dev/null 2>&1 || kubectl create ns ea-followup
  until kubectl -n ea-followup get serviceaccount default >/dev/null 2>&1; do sleep 1; done
}
recreate() {  # $1 = kind config, or empty for the standard single-node cluster
  sudo kind delete cluster --name ea-followup >/dev/null 2>&1 || true
  if [ -n "${1:-}" ]; then sudo kind create cluster --config "$1"
  else sudo kind create cluster --config kind-config.yaml || { sudo kind delete cluster --name ea-followup >/dev/null 2>&1 || true; sudo kind create cluster --name ea-followup; }
  fi
  sudo kind get kubeconfig --name ea-followup > "$HOME/.kube/config"
  for img in registry.k8s.io/pause:3.10 busybox:1.36; do sudo kind load docker-image "$img" --name ea-followup; done
  kubectl wait --for=condition=Ready nodes --all --timeout=300s
  wait_ns
}
check_cells() {  # $1 = run dir; fails on any error-class trial
  $P - "$1" <<'PY'
import csv, sys
d = sys.argv[1]
cols = ("INFRA_ERROR", "UNKNOWN", "DELETE_ERROR", "ACTION_ERROR")
bad = sum(int(r.get(c) or 0) for r in csv.DictReader(open(d + "/cells.csv")) for c in cols)
if bad:
    sys.exit(f"Smoke test {d} has {bad} error/unknown trials. Stop and send that folder and the log.")
open(d + "/OK", "w").write("ok\n")
print("smoke OK:", d)
PY
}
smoke() {     # $1 = harness, $2 = plan; two trials per cell
  local d="runs/smoke_$(basename "$2" .json)"
  [ -f "$d/OK" ] && return 0
  rm -rf "$d"; mkdir -p "$d"
  sed -E 's/"n": [0-9]+/"n": 2/g; s/"n_control": [0-9]+/"n_control": 2/g; s/\[0, 5, 100, 1000, 15000, 29000, 33000\]/[0, 1000]/' "$2" > "$d/plan_smoke.json"
  $P "$1" lock --plan "$d/plan_smoke.json" --out "$d"
  $P "$1" run --out "$d"; $P "$1" analyze --out "$d" >/dev/null
  check_cells "$d"
}
run_one() {   # $1 = harness, $2 = run id, $3 = plan
  echo "== $2 =="
  [ -f "runs/$2/LOCK.json" ] || $P "$1" lock --plan "$3" --out "runs/$2"
  $P "$1" run --out "runs/$2"
  $P "$1" analyze --out "runs/$2"
}
run_one_serial() {   # $1 = harness, $2 = run id, $3 = plan
  echo "== $2 (serial cross-check) =="
  [ -f "runs/$2/LOCK.json" ] || $P "$1" lock --plan "$3" --out "runs/$2"
  $P "$1" run --serial --out "runs/$2"
  $P "$1" analyze --out "runs/$2"
}

wait_ns
if [ ! -f runs/probe.txt ] || ! grep -q "PROBES PASSED" runs/probe.txt; then
  $P ea_exp.py probe | tee runs/probe.txt
  grep -q "PROBES PASSED" runs/probe.txt || { echo "Probes failed. Stop and send runs/probe.txt."; exit 1; }
fi

# ---- E43: grace period ----
# First run a small non-pooled serial harness-sensitivity check. It is descriptive only.
[ -f runs/E43_serial_check/summary.json ] || run_one_serial ea_exp.py E43_serial_check plan_E43_serial_crosscheck.json
[ -f runs/E43/summary.json ] || { smoke ea_exp.py plan_E43_grace.json; run_one ea_exp.py E43 plan_E43_grace.json; }

# ---- E46: target-state admission policy ----
if [ ! -f runs/E46/summary.json ] && [ ! -f runs/E46_SKIPPED ]; then
  kubectl apply -f vap.yaml; sleep 15
  if $P ea_exp2.py vap-probe | tee runs/vap_probe.txt; then
    smoke ea_exp2.py plan_E46_vap.json; run_one ea_exp2.py E46 plan_E46_vap.json
  else
    echo "VAP probe failed: E46 skipped (see runs/vap_probe.txt)."; touch runs/E46_SKIPPED
  fi
fi
# ---- E47 retry policy, E48 label action ----
[ -f runs/E47/summary.json ] || { smoke ea_exp2.py plan_E47_retry.json; run_one ea_exp2.py E47 plan_E47_retry.json; }
[ -f runs/E48/summary.json ] || { smoke ea_exp2.py plan_E48_label.json; run_one ea_exp2.py E48 plan_E48_label.json; }
# ---- E49: five freshly created clusters ----
for r in 1 2 3 4 5; do
  [ -f "runs/E49_rep$r/summary.json" ] && continue
  [ -f "runs/E49_rep$r/trials.jsonl" ] || recreate ""
  run_one ea_exp2.py "E49_rep$r" plan_REP.json
done
# ---- E50: three control-plane nodes ----
if [ ! -f runs/E50_ha/summary.json ] && [ ! -f runs/E50_SKIPPED ]; then
  [ -f runs/E50_ha/trials.jsonl ] || recreate kind-config-ha.yaml
  $P ea_exp.py probe | tee runs/probe_ha.txt
  if grep -q "PROBES PASSED" runs/probe_ha.txt; then run_one ea_exp2.py E50_ha plan_REP.json
  else echo "Probes failed on the multi-control-plane cluster; E50 skipped."; touch runs/E50_SKIPPED; fi
fi

tar czf results_to_send.tar.gz $(ls -d runs/probe.txt runs/vap_probe.txt runs/probe_ha.txt runs/E46_SKIPPED runs/E50_SKIPPED \
  runs/E4[36789]*/cells.csv runs/E4[36789]*/summary.json runs/E4[36789]*/LOCK.json runs/E4[36789]*/environment.jsonl \
  runs/E4[36789]*/plan.json runs/E4[36789]*/trials.jsonl runs/E50_ha/cells.csv runs/E50_ha/summary.json \
  runs/E50_ha/LOCK.json runs/E50_ha/environment.jsonl runs/E50_ha/plan.json runs/E50_ha/trials.jsonl 2>/dev/null)
echo "ALL DONE. Send back: $(pwd)/results_to_send.tar.gz"
