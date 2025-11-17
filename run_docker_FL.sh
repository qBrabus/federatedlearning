#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [orchestrator|client] [--self-signed]" >&2
  echo "Exemples:" >&2
  echo "  $0 orchestrator            # lance le serveur Flower" >&2
  echo "  $0 client                  # lance un client DGX (GPU requis)" >&2
  echo "  $0 orchestrator --self-signed   # génère et monte des certificats auto-signés" >&2
  exit 1
}

SELF_SIGNED=${SELF_SIGNED:-false}
COMPONENT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    orchestrator|client)
      COMPONENT="$1"
      shift
      ;;
    --self-signed)
      SELF_SIGNED=true
      shift
      ;;
    *)
      usage
      ;;
  esac
done

[[ -z "$COMPONENT" ]] && usage

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERT_SCRIPT="$SCRIPT_DIR/scripts/generate_self_signed_certs.sh"

case "$COMPONENT" in
  orchestrator)
    ORCH_ENV_FILE=${ORCH_ENV_FILE:-orchestrator/.env}
    CERTS_DIR=${ORCH_CERTS_DIR:-$(pwd)/certs/orchestrator}
    mkdir -p "$CERTS_DIR"

    if [[ "$SELF_SIGNED" == "true" ]]; then
      echo "[run] génération de certificats auto-signés pour l'orchestrateur..." >&2
      "$CERT_SCRIPT" --orch-dir "$CERTS_DIR" --client-dir "$(pwd)/certs/client"
    fi

    if [[ ! -f "$ORCH_ENV_FILE" && -f "${ORCH_ENV_FILE}.example" ]]; then
      echo "[run] fichier $ORCH_ENV_FILE introuvable, utilisation de ${ORCH_ENV_FILE}.example" >&2
      ORCH_ENV_FILE="${ORCH_ENV_FILE}.example"
    fi

    set -a
    source "$ORCH_ENV_FILE"
    set +a
    HOST_PORT=${HOST_PORT_OVERRIDE:-$FLOWER_SERVER_PORT}

    docker run --rm -it \
      --name fl-orchestrator \
      --env-file "$ORCH_ENV_FILE" \
      -e CA_CERT_PATH=${CA_CERT_PATH:-/certs/ca.crt} \
      -e SERVER_CERT_PATH=${SERVER_CERT_PATH:-/certs/server.crt} \
      -e SERVER_KEY_PATH=${SERVER_KEY_PATH:-/certs/server.key} \
      -p "${HOST_PORT}:${FLOWER_SERVER_PORT}" \
      -v "$CERTS_DIR:/certs:ro" \
      fl-orchestrator:latest
    ;;
  client)
    CLIENT_ENV_FILE=${CLIENT_ENV_FILE:-client/.env}
    CERTS_DIR=${CLIENT_CERTS_DIR:-$(pwd)/certs/client}
    DATA_DIR=${DATA_DIR:-$(pwd)/data}
    mkdir -p "$CERTS_DIR" "$DATA_DIR"

    if [[ "$SELF_SIGNED" == "true" ]]; then
      echo "[run] génération de certificats auto-signés pour le client..." >&2
      "$CERT_SCRIPT" --orch-dir "$(pwd)/certs/orchestrator" --client-dir "$CERTS_DIR"
    fi

    if [[ ! -f "$CLIENT_ENV_FILE" && -f "${CLIENT_ENV_FILE}.example" ]]; then
      echo "[run] fichier $CLIENT_ENV_FILE introuvable, utilisation de ${CLIENT_ENV_FILE}.example" >&2
      CLIENT_ENV_FILE="${CLIENT_ENV_FILE}.example"
    fi

    docker run --rm -it \
      --gpus all \
      --name fl-client-dgx \
      --env-file "$CLIENT_ENV_FILE" \
      -e CA_CERT_PATH=${CA_CERT_PATH:-/certs/ca.crt} \
      -e CLIENT_CERT_PATH=${CLIENT_CERT_PATH:-/certs/client.crt} \
      -e CLIENT_KEY_PATH=${CLIENT_KEY_PATH:-/certs/client.key} \
      -v "$CERTS_DIR:/certs:ro" \
      -v "$DATA_DIR:/data" \
      fl-client-dgx:latest
    ;;
  *)
    usage
    ;;
esac
