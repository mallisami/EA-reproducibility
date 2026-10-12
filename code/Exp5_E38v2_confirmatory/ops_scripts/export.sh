#!/bin/bash
# Export run dir + smoke dir (per-dir SHA256SUMS) as one tarball. Usage: export.sh RUN_ID SMOKE_ID
set -euo pipefail
RUN_ID=$1; SMOKE=$2
E=~/e38v2r_20260926
KC=$E/.kubeconfig_e38v2
R=$E/results/$RUN_ID
k() { kubectl --kubeconfig $KC --context kind-e38v2 "$@"; }
cd $E
cp ~/e38v2r_ops/LOG_E38v2R_OPERATIONS.txt ~/e38v2r_ops/DEVIATIONS_E38v2R.txt $R/
[ -f ~/e38v2r_ops/HEADLINE_E38v2R.txt ] && cp ~/e38v2r_ops/HEADLINE_E38v2R.txt $R/
cp protocol/LOCK_E38v2.json $R/LOCK_E38v2.json
mkdir -p $R/deploy_rendered $R/ops_scripts
cp results/_render_$RUN_ID/*.yaml results/_render_$RUN_ID/RENDER_MANIFEST.txt $R/deploy_rendered/
cp ~/e38v2r_ops/*.sh ~/e38v2r_ops/*.py $R/ops_scripts/
rm -f $R/.podwatch.pid
tar -czf $R/code_snapshot.tgz --exclude=__pycache__ --exclude=.pytest_cache src webhook deploy protocol analysis
{ echo "== kind"; kind version; echo "== kubectl"; kubectl version --kubeconfig $KC --context kind-e38v2 -o json;
  echo "== images"; docker image inspect --format '{{.Id}} {{.RepoTags}}' kindest/node:v1.37.0 python:3.12-slim;
  echo "== node images (crictl)"; docker exec e38v2-control-plane crictl images;
  echo "== nodes"; k get nodes -o wide; echo "== vwc"; k get validatingwebhookconfiguration e38v2-delete-revalidation -o yaml;
  echo "== webhook deployment"; k -n e38v2-system get deploy e38v2-webhook -o wide; k -n e38v2-system get pods -o wide;
  echo "== apiserver flags"; docker exec e38v2-control-plane grep -E 'audit-|--v=|vmodule' /etc/kubernetes/manifests/kube-apiserver.yaml;
  echo "== kubelet log settings"; docker exec e38v2-control-plane grep -E 'containerLogMax' /var/lib/kubelet/config.yaml;
  echo "== host"; uname -a; nproc; free -m; python3 --version; } > $R/ENVIRONMENT.txt 2>&1
for d in $R results/$SMOKE; do
  (cd $d && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
done
sha256sum $R/SHA256SUMS
T=$E/results/e38v2r_EXPORT_$RUN_ID.tar.gz
tar -C $E/results -czf $T $RUN_ID $SMOKE
sha256sum $T | tee $T.sha256
ls -la $T
tar -tzf $T | grep -E '\.token|certs/|tls\.key|ca\.key|kubeconfig' && echo "SECRET IN TARBALL" || echo "no tokens/keys in tarball"
