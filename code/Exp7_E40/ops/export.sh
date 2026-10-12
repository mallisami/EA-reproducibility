#!/usr/bin/env bash
set -euo pipefail
W=$HOME/e40_20260926; cd $W
cp ops/DEVIATIONS_E40.txt results/a20260926/DEVIATIONS_E40.txt
cp ops/LOG_E40_OPERATIONS.txt results/a20260926/LOG_E40_OPERATIONS.txt
rm -f results/a20260926/pod_out.tgz results/smoke1/pod_out.tgz results/probes.tgz
X=E40_EXPORT_a20260926
tar czf /tmp/$X.tar.gz --exclude='*/certs' --exclude='*/certs/*' --exclude=kubeconfig --exclude=tokens \
   --exclude='ops/attempt1/aks_create.json' \
   results protocol code e40 k8s e40_ops.sh pylib.tgz render ops
mkdir -p exports; mv /tmp/$X.tar.gz exports/; (cd exports && sha256sum $X.tar.gz > $X.tar.gz.sha256)
tar tzf exports/$X.tar.gz | grep -iE 'token|kubeconfig|\.key$' || echo "no secrets in tarball"
cat exports/$X.tar.gz.sha256; ls -la exports
