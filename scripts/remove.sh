#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE="${ROOT_DIR}/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[remove] Fichier .env introuvable." >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

REMOTE_PATH=${REMOTE_PATH:-"~/federatedlearning"}
PROJECT_DIR="$ROOT_DIR"
: "${PROXY_IP:?[remove] PROXY_IP doit être défini}"

IFS=',' read -ra SITES <<< "${CLIENT_SITES:-}"

echo "⚠️  SUPPRESSION TOTALE des conteneurs, volumes et images locaux/distants."
read -p "Confirmer ? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then exit 0; fi

# --- 1. NETTOYAGE CLIENTS ---
for SITE_ENTRY in "${SITES[@]}"; do
    SITE_NAME=${SITE_ENTRY%%:*}
    CONTEXT_NAME="ctx-${SITE_NAME}"
    [[ -z "$SITE_NAME" ]] && continue

    if docker context ls --format '{{.Name}}' | grep -q "^${CONTEXT_NAME}$"; then
        echo "[remove] Nettoyage site : $SITE_NAME..."
        # On exporte une valeur bidon pour éviter les warnings durant le 'down'
        export HUB_ADDRESS="0.0.0.0" 
        export HOST_PROJECT_PATH="."
        export SITE_NAME="$SITE_NAME"
        
        docker --context "$CONTEXT_NAME" compose \
            --project-directory "$PROJECT_DIR" \
            --profile client --profile monitor --profile monitor-gpu \
            down -v --rmi local --remove-orphans || true
    fi
done

# --- 2. NETTOYAGE PROXY ---
if docker context ls | grep -q "proxy-node"; then
    echo "[remove] Nettoyage Proxy..."
    export HOST_PROJECT_PATH="."
    docker --context proxy-node compose --profile hub --project-directory "$PROJECT_DIR" down -v --rmi local --remove-orphans || true
fi

# --- 3. NETTOYAGE LOCAL ET DISTANT ---
echo "[remove] Nettoyage des fichiers locaux..."
rm -f "${ROOT_DIR}/monitoring/prometheus/prometheus.yml"

read -p "Supprimer les dossiers distants (${REMOTE_PATH}) et les contextes Docker ? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    ssh -o BatchMode=yes "$PROXY_IP" "rm -rf ${REMOTE_PATH}" || true
    docker context rm -f proxy-node || true
    for SITE_ENTRY in "${SITES[@]}"; do
        SITE_IP=${SITE_ENTRY#*:}
        SITE_NAME=${SITE_ENTRY%%:*}
        ssh -o BatchMode=yes "$SITE_IP" "rm -rf ${REMOTE_PATH}" || true
        docker context rm -f "ctx-${SITE_NAME}" || true
    done
fi

echo "✅ Nettoyage terminé."
