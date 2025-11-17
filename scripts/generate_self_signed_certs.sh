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
# Permettre la personnalisation via des variables d'environnement pour éviter
# les erreurs de validation lors de connexions inter-machines (ex: IP publique
# ou FQDN différent de localhost).
SERVER_SAN="${CERT_SERVER_SAN:-DNS:localhost,IP:127.0.0.1}"
CLIENT_SAN="${CERT_CLIENT_SAN:-DNS:fl-client.local}"

append_host_ips_to_san() {
  local san="$1"
  local ips

  # hostname -I renvoie les IP connues de l'hôte (inclut l'IP publique/privée
  # utilisée par les clients pour se connecter). On les ajoute aux SAN si elles
  # ne sont pas déjà présentes afin d'éviter les erreurs "peer name ... is not
  # in peer certificate" lors de connexions inter-machines.
  ips=$(hostname -I 2>/dev/null || true)
  for ip in $ips; do
    # Nettoyage d'éventuels espaces ou retours à la ligne
    ip="${ip//[[:space:]]/}"
    [[ -z "$ip" ]] && continue

    if [[ "$san" != *"IP:${ip}"* && "$san" != *"IP Address:${ip}"* ]]; then
      san+="${san:+,}IP:${ip}"
    fi
  done

  echo "$san"
}

SERVER_SAN=$(append_host_ips_to_san "$SERVER_SAN")

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
# Rendre les répertoires traversables par les utilisateurs non-root dans les
# conteneurs (sinon le montage en read-only bloque la lecture des fichiers
# malgré les permissions 644 sur les certificats).
chmod 755 "$CERTS_ROOT" "$ORCH_DIR" "$CLIENT_DIR"

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

has_all_sans() {
  local cert_path="$1"
  local desired_sans="$2"

  [[ ! -f "$cert_path" ]] && return 1

  # openssl renvoie les SAN sur une ou plusieurs lignes sous la forme
  # "DNS:localhost, IP Address:127.0.0.1". On se contente de vérifier que
  # chaque entrée demandée est présente en texte brut.
  local san_output
  san_output=$(openssl x509 -in "$cert_path" -noout -ext subjectAltName 2>/dev/null || true)

  IFS=',' read -ra entries <<<"$desired_sans"
  for entry in "${entries[@]}"; do
    local trimmed="${entry//[[:space:]]/}"
    [[ -z "$trimmed" ]] && continue
    # openssl remplace "IP:" par "IP Address:" dans le rendu texte
    local normalized="$trimmed"
    if [[ $trimmed == IP:* ]]; then
      normalized="IP Address:${trimmed#IP:}"
    fi
    if [[ $trimmed == DNS:* ]]; then
      normalized="DNS:${trimmed#DNS:}"
    fi

    if [[ $san_output != *"$normalized"* ]]; then
      return 1
    fi
  done

  return 0
}

if [[ ! -f $SERVER_CERT || ! -f $SERVER_KEY ]] || ! has_all_sans "$SERVER_CERT" "$SERVER_SAN"; then
  if [[ -f $SERVER_CERT || -f $SERVER_KEY ]]; then
    echo "[certs] SAN demandé différent, régénération du certificat serveur..." >&2
  else
    echo "[certs] génération du certificat serveur auto-signé..." >&2
  fi
  make_cert "fl-orchestrator.local" "$SERVER_SAN" "$SERVER_KEY" "$SERVER_CERT"
else
  echo "[certs] certificats serveur déjà présents" >&2
fi

if [[ ! -f $CLIENT_CERT || ! -f $CLIENT_KEY ]] || ! has_all_sans "$CLIENT_CERT" "$CLIENT_SAN"; then
  if [[ -f $CLIENT_CERT || -f $CLIENT_KEY ]]; then
    echo "[certs] SAN demandé différent, régénération du certificat client..." >&2
  else
    echo "[certs] génération du certificat client auto-signé..." >&2
  fi
  make_cert "fl-client.local" "$CLIENT_SAN" "$CLIENT_KEY" "$CLIENT_CERT"
else
  echo "[certs] certificats client déjà présents" >&2
fi

# Copie le CA dans chaque répertoire pour correspondre aux chemins .env par défaut
cp "$CA_CERT" "$ORCH_DIR/ca.crt"
cp "$CA_CERT" "$CLIENT_DIR/ca.crt"

# Rendre les certificats lisibles par les utilisateurs non root dans les conteneurs
chmod 644 "$CA_CERT" "$ORCH_DIR/ca.crt" "$CLIENT_DIR/ca.crt" \
  "$SERVER_CERT" "$CLIENT_CERT" "$SERVER_KEY" "$CLIENT_KEY"

echo "[certs] certificats auto-signés générés dans:\n  - $ORCH_DIR\n  - $CLIENT_DIR" >&2
