#!/bin/bash
# Export run dir + smoke dir (per-dir SHA256SUMS) as one tarball. Usage: export.sh RUN_ID SMOKE_ID
set -euo pipefail
RUN_ID=$1; SMOKE=$2
E=~/e39_20260926
KC=$E/.kubeconfig_e39
R=$E/results/$RUN_ID
k() { kubectl --kubeconfig $KC --context kind-e39 "$@"; }
cd $E
cp ~/e39_ops/LOG_E39_OPERATIONS.txt ~/e39_ops/DEVIATIONS_E39.txt ~/e39_ops/HEADLINE_E39.txt $R/
mkdir -p $R/deploy_rendered $R/ops_scripts $R/smoke_setup_logs
cp results/_render_$RUN_ID/*.yaml results/_render_$RUN_ID/RENDER_MANIFEST.txt $R/deploy_rendered/
cp ~/e39_ops/*.sh ~/e39_ops/*.py $R/ops_scripts/
cp ~/e39_ops/create_cluster.log ~/e39_ops/setup_smoke.log ~/e39_ops/setup_main.log ~/e39_ops/probes_smoke_*.log \
   ~/e39_ops/probes_main.log ~/e39_ops/smoke_analyze_*.log ~/e39_ops/tests_*.log ~/e39_ops/final.log $R/smoke_setup_logs/ 2>/dev/null || true
rm -f $R/.podwatch.pid $R/.runner.pid
tar -czf $R/code_snapshot.tgz --exclude=__pycache__ --exclude=.pytest_cache --exclude=src/goclient/bin \
    src webhook deploy protocol analysis/*.py analysis/tests analysis/fixtures analysis/run_tests.sh
sha256sum src/goclient/bin/e39-goclient > $R/GO_BINARY_SHA256.txt
{ echo "== kind"; kind version; echo "== kubectl"; kubectl version --kubeconfig $KC --context kind-e39 -o json;
  echo "== images"; docker image inspect --format '{{.Id}} {{.RepoTags}}' kindest/node:v1.37.0 python:3.12-slim;
  echo "== node images (crictl)"; docker exec e39-control-plane crictl images;
  echo "== nodes"; k get nodes -o wide; echo "== vwc"; k get validatingwebhookconfiguration -o yaml;
  echo "== webhook deployment"; k -n e39-system get deploy -o wide; k -n e39-system get pods -o wide;
  echo "== apiserver flags"; docker exec e39-control-plane grep -E 'audit-|--v=|vmodule' /etc/kubernetes/manifests/kube-apiserver.yaml;
  echo "== kubelet log settings"; docker exec e39-control-plane grep -E 'containerLogMax' /var/lib/kubelet/config.yaml;
  echo "== go client"; cat src/goclient/BUILD_INFO.txt;
  echo "== host"; uname -a; nproc; free -m; python3 --version; } > $R/ENVIRONMENT.txt 2>&1
for d in $R results/$SMOKE; do
  (cd $d && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
done
sha256sum $R/SHA256SUMS
T=$E/results/e39_EXPORT_$RUN_ID.tar.gz
tar -C $E/results -czf $T $RUN_ID $SMOKE
sha256sum $T | tee $T.sha256
ls -la $T
if tar -tzf $T | grep -E '\.token|certs/|tls\.key|ca\.key|kubeconfig'; then echo "SECRET IN TARBALL"; exit 9; else echo "no tokens/keys in tarball"; fi
