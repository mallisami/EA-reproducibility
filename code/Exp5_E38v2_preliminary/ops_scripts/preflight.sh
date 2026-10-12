#!/bin/bash
E=~/e38v2_20260926
cd $E
date -u +%FT%TZ
kind get clusters | tee /tmp/e38v2_clusters_before.txt
docker ps --format '{{.Names}} {{.Status}}' | sort | tee /tmp/e38v2_docker_before.txt
df -h ~ | tail -1
free -m | head -2
nproc; uptime
docker image inspect --format '{{.Id}} {{.RepoTags}}' kindest/node:v1.37.0 python:3.12-slim
kind version; kubectl version --client 2>/dev/null | head -1
sha256sum frozen_ref/source_provenance/E37_F5_PHYS_ORIGINAL.json
ls -ld results results/_audit_host results/_webhook_host 2>&1
ls ~/.kube/ 2>/dev/null; kubectl config get-contexts 2>/dev/null
docker network ls | grep kind
