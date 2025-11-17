#!/usr/bin/env bash
set -euo pipefail

# Test de fumée end-to-end : lance un orchestrateur en arrière-plan puis un client.
# Prérequis : images déjà construites, fichiers .env renseignés.

ORCH_ENV_FILE=${ORCH_ENV_FILE:-orchestrator/.env}
CLIENT_ENV_FILE=${CLIENT_ENV_FILE:-client/.env}
ORCH_CERTS_DIR=${ORCH_CERTS_DIR:-$(pwd)/certs/orchestrator}
CLIENT_CERTS_DIR=${CLIENT_CERTS_DIR:-$(pwd)/certs/client}
DATA_DIR=${DATA_DIR:-$(pwd)/data}
SELF_SIGNED=${SELF_SIGNED:-false}

mkdir -p "$ORCH_CERTS_DIR" "$CLIENT_CERTS_DIR" "$DATA_DIR"

if [[ "$SELF_SIGNED" == "true" ]]; then
  echo "[test] génération de certificats auto-signés..." >&2
  "$(pwd)/scripts/generate_self_signed_certs.sh" --orch-dir "$ORCH_CERTS_DIR" --client-dir "$CLIENT_CERTS_DIR"
fi

set -a
source "$ORCH_ENV_FILE"
set +a
HOST_PORT=${HOST_PORT_OVERRIDE:-$FLOWER_SERVER_PORT}

# Démarre l'orchestrateur en détaché
ORCH_ID=$(docker run -d \
  --env-file "$ORCH_ENV_FILE" \
  -p "${HOST_PORT}:${FLOWER_SERVER_PORT}" \
  -v "$ORCH_CERTS_DIR:/certs:ro" \
  --name fl-orchestrator-e2e \
  fl-orchestrator:latest)

cleanup() {
  docker rm -f "$ORCH_ID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Laisse le temps au serveur de monter
sleep 5

echo "[test] lancement du client contre l'orchestrateur local..."
docker run --rm \
  --gpus all \
  --env-file "$CLIENT_ENV_FILE" \
  -v "$CLIENT_CERTS_DIR:/certs:ro" \
  -v "$DATA_DIR:/data" \
  --name fl-client-dgx-e2e \
  fl-client-dgx:latest

echo "[test] terminé : client exécuté et orchestrateur arrêté."
