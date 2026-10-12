#!/usr/bin/env bash
# Self-signed CA + serving certificate for the E39 webhook Service (e40-webhook.e40-system.svc), and
# caBundle injection into the rendered ValidatingWebhookConfiguration. Applies nothing.
#   usage: deploy/gen_certs.sh <OUTDIR> [SERVICE] [NAMESPACE]
# Reads  <OUTDIR>/webhook-config.yaml   (from render.sh; must contain CA_BUNDLE_PLACEHOLDER)
# Writes <OUTDIR>/certs/{ca.key,ca.crt,tls.key,tls.crt}  (mode 0600/0644)
#        <OUTDIR>/webhook-config.yaml   (placeholder replaced by base64(ca.crt))
set -euo pipefail
OUT="${1:?usage: gen_certs.sh <OUTDIR> [SERVICE] [NAMESPACE]}"
SVC="${2:-e40-webhook}"
NS="${3:-e40-system}"
DAYS="${E39_CERT_DAYS:-30}"
CFG="$OUT/webhook-config.yaml"

command -v openssl >/dev/null || { echo "openssl not found" >&2; exit 2; }
[[ -f "$CFG" ]] || { echo "missing $CFG (run render.sh first)" >&2; exit 2; }
[[ "$(grep -c 'CA_BUNDLE_PLACEHOLDER' "$CFG")" == "1" ]] || { echo "$CFG must contain exactly one CA_BUNDLE_PLACEHOLDER" >&2; exit 2; }
C="$OUT/certs"
[[ -e "$C" ]] && { echo "refusing to overwrite existing $C" >&2; exit 2; }
mkdir -p "$C"
umask 077

DNS1="$SVC"; DNS2="$SVC.$NS"; DNS3="$SVC.$NS.svc"; DNS4="$SVC.$NS.svc.cluster.local"
cat > "$C/server.cnf" <<EOF
[req]
distinguished_name = dn
prompt = no
[dn]
CN = $DNS3
[v3_server]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:$DNS1,DNS:$DNS2,DNS:$DNS3,DNS:$DNS4
EOF

openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days "$DAYS" \
  -keyout "$C/ca.key" -out "$C/ca.crt" -subj "/CN=e40-webhook-ca" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
openssl req -new -newkey rsa:2048 -nodes -sha256 -keyout "$C/tls.key" -out "$C/tls.csr" \
  -config "$C/server.cnf" 2>/dev/null
openssl x509 -req -sha256 -days "$DAYS" -in "$C/tls.csr" -CA "$C/ca.crt" -CAkey "$C/ca.key" \
  -CAcreateserial -out "$C/tls.crt" -extfile "$C/server.cnf" -extensions v3_server 2>/dev/null
chmod 0644 "$C/ca.crt" "$C/tls.crt"
chmod 0600 "$C/ca.key" "$C/tls.key"
rm -f "$C/tls.csr" "$C/ca.srl"

# Self-checks: chain verifies for serverAuth, SAN carries the Service DNS name, key matches cert.
openssl verify -purpose sslserver -CAfile "$C/ca.crt" "$C/tls.crt" >/dev/null
openssl x509 -in "$C/tls.crt" -noout -ext subjectAltName | grep -q "DNS:$DNS3" \
  || { echo "SAN check failed" >&2; exit 3; }
[[ "$(openssl x509 -in "$C/tls.crt" -noout -pubkey | sha256sum)" == "$(openssl pkey -in "$C/tls.key" -pubout | sha256sum)" ]] \
  || { echo "key/cert mismatch" >&2; exit 3; }

CA_B64="$(base64 -w0 < "$C/ca.crt")"
sed -i "s#CA_BUNDLE_PLACEHOLDER#${CA_B64}#" "$CFG"
grep -q 'CA_BUNDLE_PLACEHOLDER' "$CFG" && { echo "caBundle substitution failed" >&2; exit 3; }
echo "certs in $C (SAN $DNS1,$DNS2,$DNS3,$DNS4; ${DAYS}d); caBundle written to $CFG (not applied)"
