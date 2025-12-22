#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE="${ROOT_DIR}/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[remove] Fichier .env introuvable. Impossible de déterminer les cibles." >&2
  exit 1
fi

# Charger la configuration pour avoir accès aux noms de contextes si besoin
set -a
source "$ENV_FILE"
set +a

# Variables de nettoyage
REMOTE_PATH=${REMOTE_PATH:-"~/federatedlearning"}
PROJECT_DIR="$ROOT_DIR"
: "${PROXY_IP:?[remove] PROXY_IP doit être défini dans .env}"

CLIENT_SITES=${CLIENT_SITES:-""}
IFS=',' read -ra SITES <<< "$CLIENT_SITES"

context_accessible() {
  local context=$1
  docker --context "$context" info >/dev/null 2>&1
}

echo "⚠️  ATTENTION : Ce script va supprimer tous les conteneurs, volumes et images liés à ce projet sur le Proxy et les sites clients."
read -p "Voulez-vous continuer ? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "[remove] Opération annulée."
    exit 0
fi

# --- NETTOYAGE CLIENTS (Client + Monitor) ---
for SITE_ENTRY in "${SITES[@]}"; do
    SITE_NAME=${SITE_ENTRY%%:*}
    CONTEXT_NAME="ctx-${SITE_NAME}"

    if [[ -z "$SITE_NAME" ]]; then
        continue
    fi

    if docker context ls --format '{{.Name}}' | grep -q "^${CONTEXT_NAME}$"; then
        if ! context_accessible "$CONTEXT_NAME"; then
            echo "[remove] Contexte ${CONTEXT_NAME} inaccessible ou pointant vers un hôte obsolète : suppression du contexte pour un prochain déploiement propre."
            docker context rm -f "$CONTEXT_NAME" >/dev/null 2>&1 || true
            continue
        fi

        echo "[remove] Arrêt et suppression sur ${SITE_NAME} (${CONTEXT_NAME})..."
        docker --context "$CONTEXT_NAME" compose --profile client --profile monitor --project-directory "$PROJECT_DIR" down -v --rmi local --remove-orphans || echo "Échec partiel sur ${SITE_NAME}, continuant..."
    else
        echo "[remove] Contexte ${CONTEXT_NAME} non trouvé, passage."
    fi
done

# --- NETTOYAGE PROXY (Hub) ---
if docker context ls | grep -q "proxy-node"; then
    echo "[remove] Arrêt et suppression (conteneurs, volumes, images) sur le Proxy..."
    docker --context proxy-node compose --profile hub --project-directory "$PROJECT_DIR" down -v --rmi local --remove-orphans || echo "Échec partiel sur Proxy, continuant..."
else
    echo "[remove] Contexte proxy-node non trouvé, passage."
fi

# --- NETTOYAGE DES FICHIERS LOCAUX GÉNÉRÉS ---
echo "[remove] Nettoyage des fichiers générés localement..."
rm -f "${ROOT_DIR}/monitoring/prometheus/prometheus.yml"
# Optionnel : décommenter si vous voulez supprimer aussi les certificats
# rm -rf "${ROOT_DIR}/certs" 

# --- NETTOYAGE DES RÉPERTOIRES DISTANTS (Facultatif) ---
read -p "Voulez-vous supprimer les fichiers sources sur les serveurs distants (${REMOTE_PATH}) ? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "[remove] Suppression des répertoires distants..."
    ssh "$PROXY_IP" "rm -rf ${REMOTE_PATH}" || echo "Échec suppression dossier sur proxy"
    for SITE_ENTRY in "${SITES[@]}"; do
        SITE_IP=${SITE_ENTRY#*:}
        [[ -z "$SITE_IP" ]] && continue
        ssh "$SITE_IP" "rm -rf ${REMOTE_PATH}" || echo "Échec suppression dossier sur ${SITE_IP}"
    done
fi

# --- SUPPRESSION DES CONTEXTES DOCKER ---
read -p "Voulez-vous supprimer les Docker Contexts (proxy-node, ctx-<site>) ? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    CONTEXTS=(proxy-node)
    for SITE_ENTRY in "${SITES[@]}"; do
        SITE_NAME=${SITE_ENTRY%%:*}
        [[ -z "$SITE_NAME" ]] && continue
        CONTEXTS+=("ctx-${SITE_NAME}")
    done
    docker context rm "${CONTEXTS[@]}" || true
fi

echo "✅ Nettoyage terminé."
