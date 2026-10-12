#!/bin/bash
set -euo pipefail
RUN_ID=$1
E=~/e38v2_20260926
KC=$E/.kubeconfig_e38v2
R=$E/results/$RUN_ID
k() { kubectl --kubeconfig $KC --context kind-e38v2 "$@"; }
cd $E
cp ~/e38v2_ops/LOG_E38v2_OPERATIONS.txt ~/e38v2_ops/DEVIATIONS_E38v2.txt $R/
cp protocol/LOCK_E38v2.json $R/LOCK_E38v2.json
mkdir -p $R/deploy_rendered $R/ops_scripts
cp results/_render_$RUN_ID/*.yaml results/_render_$RUN_ID/RENDER_MANIFEST.txt $R/deploy_rendered/
cp ~/e38v2_ops/*.sh ~/e38v2_ops/*.py $R/ops_scripts/
tar -czf $R/code_snapshot.tgz --exclude=__pycache__ --exclude=.pytest_cache src webhook deploy protocol analysis
{ echo "== kind"; kind version; echo "== kubectl"; kubectl version --kubeconfig $KC --context kind-e38v2 -o json;
  echo "== images"; docker image inspect --format '{{.Id}} {{.RepoTags}}' kindest/node:v1.37.0 python:3.12-slim;
  echo "== nodes"; k get nodes -o wide; echo "== vwc"; k get validatingwebhookconfiguration e38v2-delete-revalidation -o yaml;
  echo "== webhook deployment"; k -n e38v2-system get deploy e38v2-webhook -o wide; k -n e38v2-system get pods -o wide;
  echo "== apiserver audit flags"; docker exec e38v2-control-plane grep -E 'audit-' /etc/kubernetes/manifests/kube-apiserver.yaml;
  echo "== host"; uname -a; nproc; free -m; python3 --version; } > $R/ENVIRONMENT.txt 2>&1
for d in $R results/smoke-20260926a results/smoke-20260926b; do
  (cd $d && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
done
sha256sum $R/SHA256SUMS
T=$E/results/e38v2_EXPORT_$RUN_ID.tar.gz
tar -C $E/results -czf $T $RUN_ID smoke-20260926a smoke-20260926b
sha256sum $T | tee $T.sha256
ls -la $T
# never exported: tokens, certs
tar -tzf $T | grep -E '\.token|certs/|tls\.key|ca\.key' && echo "SECRET IN TARBALL" || echo "no tokens/keys in tarball"
