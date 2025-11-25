#!/usr/bin/env bash
set -euo pipefail

DOCKER_TTY_FLAG=""
if [[ -t 0 ]]; then
  DOCKER_TTY_FLAG="-it"
fi

# Désactive la suppression automatique des conteneurs lorsque
# KEEP_CONTAINER_LOGS=true (utile pour inspecter les logs après arrêt).
DOCKER_RM_FLAG="--rm"
if [[ "${KEEP_CONTAINER_LOGS:-false}" == "true" ]]; then
  DOCKER_RM_FLAG=""
fi

usage() {
  echo "Usage: $0 [orchestrator|client] [--self-signed] [--detach]" >&2
  echo "Exemples:" >&2
  echo "  $0 orchestrator                 # lance le serveur Flower" >&2
  echo "  $0 client                       # lance un client DGX (GPU requis)" >&2
  echo "  $0 orchestrator --self-signed   # génère et monte des certificats auto-signés" >&2
  echo "  $0 orchestrator --detach        # lance en arrière-plan (utile pour le déploiement)" >&2
  exit 1
}

SELF_SIGNED=${SELF_SIGNED:-false}
DETACH=${DETACH:-false}
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
    --detach)
      DETACH=true
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
    SERVERAPP_PORT=${FLOWER_SERVERAPP_PORT:-9091}
    HOST_SERVERAPP_PORT=${HOST_SERVERAPP_PORT_OVERRIDE:-$SERVERAPP_PORT}

    DOCKER_DETACH_FLAG=""
    if [[ "$DETACH" == "true" ]]; then
      DOCKER_DETACH_FLAG="-d"
    fi

    docker run ${DOCKER_RM_FLAG} ${DOCKER_TTY_FLAG} ${DOCKER_DETACH_FLAG} \
      --name fl-orchestrator \
      --env-file "$ORCH_ENV_FILE" \
      -e CA_CERT_PATH=${CA_CERT_PATH:-/certs/ca.crt} \
      -e SERVER_CERT_PATH=${SERVER_CERT_PATH:-/certs/server.crt} \
      -e SERVER_KEY_PATH=${SERVER_KEY_PATH:-/certs/server.key} \
      -p "${HOST_PORT}:${FLOWER_SERVER_PORT}" \
      -p "${HOST_SERVERAPP_PORT}:${SERVERAPP_PORT}" \
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
      if [[ -f "$CLIENT_ENV_FILE" ]]; then
        # Rendez la configuration disponible pour l'extraction de SERVER_ADDRESS
        set -a
        source "$CLIENT_ENV_FILE"
        set +a
      fi

      SERVER_SAN="${CERT_SERVER_SAN:-DNS:fl-orchestrator.local,DNS:localhost,IP:127.0.0.1}"
      if [[ -n "${SERVER_ADDRESS:-}" ]]; then
        server_host="${SERVER_ADDRESS%%:*}"
        if [[ "$server_host" =~ ^[0-9]+(\.[0-9]+){3}$ ]]; then
          san_entry="IP:${server_host}"
        else
          san_entry="DNS:${server_host}"
        fi

        if [[ "$SERVER_SAN" != *"$san_entry"* ]]; then
          SERVER_SAN+=",${san_entry}"
        fi
      fi

      CERT_SERVER_SAN="$SERVER_SAN" "$CERT_SCRIPT" --orch-dir "$(pwd)/certs/orchestrator" --client-dir "$CERTS_DIR"
      # TLS côté SuperLink reste désactivé (flower-superexec ne supporte pas encore TLS)
      export USE_TLS=false
    fi

    if [[ ! -f "$CLIENT_ENV_FILE" && -f "${CLIENT_ENV_FILE}.example" ]]; then
      echo "[run] fichier $CLIENT_ENV_FILE introuvable, utilisation de ${CLIENT_ENV_FILE}.example" >&2
      CLIENT_ENV_FILE="${CLIENT_ENV_FILE}.example"
    fi

    DOCKER_DETACH_FLAG=""
    if [[ "$DETACH" == "true" ]]; then
      DOCKER_DETACH_FLAG="-d"
    fi

    docker run ${DOCKER_RM_FLAG} ${DOCKER_TTY_FLAG} ${DOCKER_DETACH_FLAG} \
      --gpus all \
      --name fl-client-dgx \
      --env-file "$CLIENT_ENV_FILE" \
      -e USE_TLS=${USE_TLS:-true} \
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
