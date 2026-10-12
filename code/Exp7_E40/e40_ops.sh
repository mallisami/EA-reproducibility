#!/usr/bin/env bash
# E40 operations on the control VM. Workspace ~/e40_20260926 only. Never touches kind clusters, docker or ~/.kube/config.
#   e40_ops.sh <step> [args]     every step appends to $W/ops/LOG_E40_OPERATIONS.txt
set -euo pipefail
W=$HOME/e40_20260926
E39=$HOME/e39_20260926
RG=mcb-experiments
CL=e40-aks
LOC=$(cat $HOME/e40_20260926/ops/REGION 2>/dev/null || echo southeastasia)
KCFG=$W/kubeconfig
LOG=$W/ops/LOG_E40_OPERATIONS.txt
mkdir -p $W/ops
k() { nice -n 10 kubectl --kubeconfig "$KCFG" "$@"; }
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG"; }

step="${1:?step}"; shift || true
case "$step" in
prep_code)
  # copy the E39 LOCKED files (as listed in LOCK_E39.json) and verify their hashes
  LOCKF="${1:?path to E39 lock}"
  rm -rf $W/code_e39_locked $W/code; mkdir -p $W/code_e39_locked
  python3 - "$LOCKF" "$E39" "$W/code_e39_locked" "$W/protocol/COPY_SHA256_E39.txt" <<'EOF'
import json, sys, hashlib, shutil, os
lock, root, dst, rep = sys.argv[1:5]
L = json.load(open(lock))
files = L.get('files')
ents = []
if isinstance(files, dict):
    ents = [(p, h) for p, h in files.items()]
else:
    for e in files + list(L.get('pinned') or []):
        if isinstance(e, dict) and e.get('path'):
            ents.append((e['path'], e.get('sha256')))
want = ('src/', 'webhook/', 'deploy/', 'analysis/', 'frozen_ref/', 'protocol/ANALYSIS_PLAN_E39.txt')
out, bad = [], []
for p, h in sorted(ents):
    if not p.startswith(want) or '/tests/' in p or p.startswith(('analysis/fixtures', 'src/tests_harness', 'src/goclient')):
        continue
    src = os.path.join(root, p)
    got = hashlib.sha256(open(src, 'rb').read()).hexdigest()
    if h and got != h:
        bad.append(p)
    os.makedirs(os.path.dirname(os.path.join(dst, p)), exist_ok=True)
    shutil.copy2(src, os.path.join(dst, p))
    out.append('%s  %s  lock=%s' % (got, p, 'match' if got == h else h))
open(rep, 'w').write('\n'.join(out) + '\n')
print('copied', len(out), 'files; mismatches:', bad)
sys.exit(1 if bad else 0)
EOF
  cp -a $W/code_e39_locked $W/code
  # (a) mechanical prefix rename e39- -> e40- (runner, webhook, deploy only; analysis untouched)
  find $W/code/src $W/code/webhook $W/code/deploy -type f \( -name '*.py' -o -name '*.yaml' -o -name '*.sh' \) \
    -exec sed -i -e 's/e39-/e40-/g' -e 's/|e39)-/|e40)-/g' {} +
  (cd $W && diff -ru code_e39_locked code > $W/protocol/RENAME_E39_TO_E40.diff || true)
  grep -rn 'e39' $W/code/src $W/code/webhook $W/code/deploy --include=*.py --include=*.yaml --include=*.sh \
    | grep -v 'e39_\|E39\|e39\.\|kind-e39\|e39run_\|e39self_\|e39proxy_' > $W/protocol/REMAINING_E39_TOKENS.txt || true
  # (g) jsonschema + deps from the E39 runner venv (same versions)
  SP=$E39/venv_runner/lib/python3.12/site-packages
  (cd $SP && tar czf $W/pylib.tgz --exclude=__pycache__ jsonschema referencing rpds attrs attr \
     jsonschema_specifications typing_extensions.py jsonschema-*.dist-info referencing-*.dist-info rpds_py-*.dist-info \
     attrs-*.dist-info jsonschema_specifications-*.dist-info typing_extensions-*.dist-info)
  ls -la $W/pylib.tgz; wc -l $W/protocol/RENAME_E39_TO_E40.diff $W/protocol/REMAINING_E39_TOKENS.txt
  log "prep_code done from $LOCKF"
  ;;
create)
  # retry list approved by the user: region:size, first success wins
  for combo in ${E40_COMBOS:-koreasouth:Standard_D2as_v5 koreasouth:Standard_D2s_v5 southindia:Standard_D2as_v5 southindia:Standard_D2s_v5}; do
    LOC=${combo%%:*}; SIZE=${combo##*:}
    VER=$(az aks get-versions -l $LOC --query "values[?starts_with(version,'1.36')].patchVersions.keys(@)[]" -o tsv | sort -V | tail -1)
    log "CLUSTER CREATE START region=$LOC size=$SIZE version=$VER"
    date -u +%s > $W/ops/CLUSTER_CREATE_START_EPOCH
    if az aks create -g $RG -n $CL -l $LOC --tier free --node-count 1 --node-vm-size $SIZE \
         --kubernetes-version "$VER" --network-plugin azure --network-plugin-mode overlay --no-ssh-key \
         --tags purpose=e40 expires=2026-09-27 -o json > $W/ops/aks_create.json 2> $W/ops/aks_create_err_${LOC}_${SIZE}.log; then
      echo $LOC > $W/ops/REGION; echo $SIZE > $W/ops/SIZE; echo $VER > $W/ops/K8S_VERSION
      date -u +%s > $W/ops/CLUSTER_CREATED_EPOCH
      log "CLUSTER CREATE END region=$LOC size=$SIZE provisioningState=$(python3 -c "import json;print(json.load(open('$W/ops/aks_create.json'))['provisioningState'])")"
      exit 0
    fi
    log "CLUSTER CREATE REJECTED region=$LOC size=$SIZE: $(tail -c 400 $W/ops/aks_create_err_${LOC}_${SIZE}.log | tr '\n' ' ')"
    log "  aks in RG after rejection: $(az aks list -g $RG --query '[].name' -o tsv | tr '\n' ' ')"
    if [ -n "$(az aks list -g $RG --query '[].name' -o tsv)" ]; then echo $LOC > $W/ops/REGION; log "partial cluster exists -> stop"; exit 3; fi
  done
  log "ALL FOUR COMBINATIONS REJECTED"; exit 4
  ;;
creds)
  rm -f $KCFG
  az aks get-credentials -g $RG -n $CL --file $KCFG --overwrite-existing -o none
  chmod 600 $KCFG
  k version -o json > $W/ops/k_version.json; k get nodes -o wide | tee -a $LOG
  ;;
deploy)
  RUN="${1:?run id}"
  R=$W/render/$RUN; rm -rf $R
  bash $W/code/deploy/render.sh $RUN $R >/dev/null
  # (e) webhook log: hostPath -> emptyDir (AKS node filesystem not accessible); collected with kubectl cp
  sed -i 's#hostPath: {path: /var/log/e40-webhook, type: Directory}#emptyDir: {}#' $R/webhook.yaml
  grep -q 'emptyDir: {}' $R/webhook.yaml
  bash $W/code/deploy/gen_certs.sh $R >/dev/null
  k apply -f $R/namespace.yaml
  k apply -f $R/rbac.yaml
  k -n e40-system create configmap e40-webhook-code --from-file=$W/code/webhook/server.py \
      --from-file=$W/code/webhook/policy.py --dry-run=client -o yaml | k apply -f -
  k -n e40-system create secret tls e40-webhook-tls --cert=$R/certs/tls.crt --key=$R/certs/tls.key \
      --dry-run=client -o yaml | k apply -f -
  k apply -f $R/webhook.yaml
  k -n e40-system rollout restart deployment/e40-webhook
  k -n e40-system rollout status deployment/e40-webhook --timeout=240s
  k apply -f $R/webhook-config.yaml
  NS=e40-$RUN
  for f in a b c d e f; do k -n $NS create configmap evidence-$f --from-literal=value=true --dry-run=client -o yaml | k apply -f -; done
  k -n $NS create configmap response-scope --from-literal=allowed=true --dry-run=client -o yaml | k apply -f -
  k -n $NS create configmap application-policy --from-literal=allowed=true --dry-run=client -o yaml | k apply -f -
  k -n $NS create configmap e38-epoch --from-literal=sequence=0 --dry-run=client -o yaml | k apply -f -
  (cd $R && sha256sum *.yaml > RENDERED_SHA256.txt)
  for pair in "delete pods responder yes" "patch configmaps responder no" "patch pods writer yes" "delete pods writer no" \
              "patch pods mirror yes" "watch pods reset yes"; do
    set -- $pair; echo "can-i $1 $2 as $3: $(k auth can-i $1 $2 -n $NS --as=system:serviceaccount:e40-system:e40-$3) (expect $4)"
  done | tee -a $LOG
  log "deploy $RUN done"
  ;;
tokens)
  T=$W/tokens; rm -rf $T; mkdir -m 700 $T
  for i in responder writer reset mirror; do k -n e40-system create token e40-$i --duration=6h > $T/$i.token; chmod 600 $T/$i.token; done
  k -n e40-system create secret generic e40-tokens --from-file=$T --dry-run=client -o yaml | k apply --server-side --force-conflicts -f - >/dev/null
  log "tokens created (6h) for responder writer reset mirror"
  ;;
code_cms)
  C=$W/code
  k -n e40-system create configmap e40-code-src $(for f in $C/src/*.py; do printf -- '--from-file=%s ' $f; done) --dry-run=client -o yaml | k apply --server-side --force-conflicts -f - >/dev/null
  k -n e40-system create configmap e40-code-core $(for f in $C/src/flashir_core/*.py; do printf -- '--from-file=%s ' $f; done) --dry-run=client -o yaml | k apply --server-side --force-conflicts -f - >/dev/null
  k -n e40-system create configmap e40-code-f5 --from-file=$C/frozen_ref/source_provenance/E37_F5_PHYS_ORIGINAL.json --dry-run=client -o yaml | k apply --server-side --force-conflicts -f - >/dev/null
  k -n e40-system create configmap e40-code-e40 $(for f in $W/e40/e40_main.py $W/e40/e40_podwatch.py; do printf -- '--from-file=%s ' $f; done) --dry-run=client -o yaml | k apply --server-side --force-conflicts -f - >/dev/null
  k -n e40-system create configmap e40-pylib --from-file=pylib.tgz=$W/pylib.tgz --dry-run=client -o yaml | k apply --server-side --force-conflicts -f - >/dev/null
  log "code configmaps applied"
  ;;
job)
  SEED39="${1:?e39 seed}"
  sed "s/E39SEED/$SEED39/" $W/k8s/runner-job.yaml > $W/k8s/runner-job.rendered.yaml
  k apply -f $W/k8s/runner-job.rendered.yaml
  k -n e40-system wait --for=condition=Ready pod -l app=e40-runner --timeout=300s
  log "runner job ready: $(k -n e40-system get pod -l app=e40-runner -o jsonpath='{.items[0].metadata.name}')"
  ;;
delete)
  log "CLUSTER DELETE START"
  az aks delete -g $RG -n $CL --yes || true
  date -u +%s > $W/ops/CLUSTER_DELETED_EPOCH
  log "CLUSTER DELETE END; aks list: $(az aks list -g $RG --query '[].name' -o tsv | tr '\n' ' ')"
  # VM identity cannot read MC_ groups (Forbidden); record what is visible and the resources of the MC_ group if readable
  log "MC_ check (az group exists): $(az group exists -n MC_${RG}_${CL}_${LOC} 2>&1 | tail -1 | cut -c1-160)"
  log "aks show after delete: $(az aks show -g $RG -n $CL --query provisioningState -o tsv 2>&1 | tail -1 | cut -c1-160)"
  ;;
*) echo "unknown step $step" >&2; exit 2 ;;
esac
