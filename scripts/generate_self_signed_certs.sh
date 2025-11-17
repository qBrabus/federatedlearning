#!/usr/bin/env bash
# Génère un bundle auto-signé (CA + certificats serveur/client) pour les tests TLS/mTLS.
# Les certificats sont stockés dans certs/orchestrator et certs/client par défaut.

set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage: $0 [--orch-dir <path>] [--client-dir <path>] [--days <n>] [--server-san <val>] [--client-san <val>]
Options:
  --orch-dir      Répertoire des certificats orchestrateur (défaut: ./certs/orchestrator)
  --client-dir    Répertoire des certificats client (défaut: ./certs/client)
  --days          Validité en jours (défaut: 365)
  --server-san    SAN du certificat serveur (défaut: DNS:localhost,IP:127.0.0.1)
  --client-san    SAN du certificat client (défaut: DNS:fl-client.local)
USAGE
  exit 1
}

ORCH_DIR=$(pwd)/certs/orchestrator
CLIENT_DIR=$(pwd)/certs/client
CERTS_ROOT=$(pwd)/certs
DAYS=365
SERVER_SAN="DNS:localhost,IP:127.0.0.1"
CLIENT_SAN="DNS:fl-client.local"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --orch-dir)
      ORCH_DIR=$(realpath "$2")
      shift 2
      ;;
    --client-dir)
      CLIENT_DIR=$(realpath "$2")
      shift 2
      ;;
    --days)
      DAYS=$2
      shift 2
      ;;
    --server-san)
      SERVER_SAN=$2
      shift 2
      ;;
    --client-san)
      CLIENT_SAN=$2
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

command -v openssl >/dev/null 2>&1 || {
  echo "[certs] openssl est requis pour générer les certificats" >&2
  exit 1
}

mkdir -p "$ORCH_DIR" "$CLIENT_DIR" "$CERTS_ROOT"

CA_KEY="$CERTS_ROOT/ca.key"
CA_CERT="$CERTS_ROOT/ca.crt"
SERVER_KEY="$ORCH_DIR/server.key"
SERVER_CERT="$ORCH_DIR/server.crt"
CLIENT_KEY="$CLIENT_DIR/client.key"
CLIENT_CERT="$CLIENT_DIR/client.crt"

if [[ ! -f $CA_CERT || ! -f $CA_KEY ]]; then
  echo "[certs] génération d'une autorité de certification locale..." >&2
  openssl req -x509 -nodes -newkey rsa:4096 -days "$DAYS" -keyout "$CA_KEY" -out "$CA_CERT" \
    -subj "/CN=Flower Local CA" -sha256
else
  echo "[certs] CA existante détectée, réutilisation" >&2
fi

make_cert() {
  local cn="$1"
  local san="$2"
  local key_path="$3"
  local cert_path="$4"
  local csr tmpconf

  csr=$(mktemp)
  tmpconf=$(mktemp)

  cat >"$tmpconf" <<EOFCONF
[req]
distinguished_name=req
prompt=no
req_extensions=req_ext
[req_ext]
subjectAltName=${san}
EOFCONF

  openssl req -new -nodes -newkey rsa:4096 -keyout "$key_path" -out "$csr" -subj "/CN=${cn}" -config "$tmpconf"
  openssl x509 -req -in "$csr" -CA "$CA_CERT" -CAkey "$CA_KEY" -CAcreateserial -out "$cert_path" -days "$DAYS" -sha256 \
    -extfile "$tmpconf" -extensions req_ext

  rm -f "$csr" "$tmpconf"
}

if [[ ! -f $SERVER_CERT || ! -f $SERVER_KEY ]]; then
  echo "[certs] génération du certificat serveur auto-signé..." >&2
  make_cert "fl-orchestrator.local" "$SERVER_SAN" "$SERVER_KEY" "$SERVER_CERT"
else
  echo "[certs] certificats serveur déjà présents" >&2
fi

if [[ ! -f $CLIENT_CERT || ! -f $CLIENT_KEY ]]; then
  echo "[certs] génération du certificat client auto-signé..." >&2
  make_cert "fl-client.local" "$CLIENT_SAN" "$CLIENT_KEY" "$CLIENT_CERT"
else
  echo "[certs] certificats client déjà présents" >&2
fi

# Copie le CA dans chaque répertoire pour correspondre aux chemins .env par défaut
cp "$CA_CERT" "$ORCH_DIR/ca.crt"
cp "$CA_CERT" "$CLIENT_DIR/ca.crt"

echo "[certs] certificats auto-signés générés dans:\n  - $ORCH_DIR\n  - $CLIENT_DIR" >&2
