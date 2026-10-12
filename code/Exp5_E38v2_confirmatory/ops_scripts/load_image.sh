#!/bin/bash
set -uo pipefail
docker version --format 'docker {{.Server.Version}}'
docker info --format '{{.DriverStatus}}' 2>/dev/null | head -2
docker save --platform linux/amd64 python:3.12-slim -o /tmp/e38v2_py312slim.tar 2>&1 || docker save python:3.12-slim -o /tmp/e38v2_py312slim.tar
ls -la /tmp/e38v2_py312slim.tar
docker exec -i e38v2-control-plane ctr --namespace=k8s.io images import --digests --snapshotter=overlayfs --platform linux/amd64 - < /tmp/e38v2_py312slim.tar
echo "import rc=$?"
docker exec e38v2-control-plane crictl images | grep -E 'python'
rm -f /tmp/e38v2_py312slim.tar
