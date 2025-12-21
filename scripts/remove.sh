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

echo "⚠️  ATTENTION : Ce script va supprimer tous les conteneurs, volumes et images liés à ce projet sur le Proxy et le DGX."
read -p "Voulez-vous continuer ? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "[remove] Opération annulée."
    exit 0
fi

# --- NETTOYAGE DGX (Client + Monitor) ---
if docker context ls | grep -q "dgx-node"; then
    echo "[remove] Arrêt et suppression (conteneurs, volumes, images) sur le DGX..."
    # -v : supprime les volumes (grafana-storage, etc.)
    # --rmi local : supprime les images construites (clientapp)
    # --remove-orphans : nettoie les services qui auraient pu être renommés
    docker --context dgx-node compose --profile client --profile monitor --project-directory "$PROJECT_DIR" down -v --rmi local --remove-orphans || echo "Échec partiel sur DGX, continuant..."
else
    echo "[remove] Contexte dgx-node non trouvé, passage."
fi

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
    ssh proxy-data "rm -rf ${REMOTE_PATH}" || echo "Échec suppression dossier sur proxy"
    ssh dgx "rm -rf ${REMOTE_PATH}" || echo "Échec suppression dossier sur dgx"
fi

# --- SUPPRESSION DES CONTEXTES DOCKER ---
read -p "Voulez-vous supprimer les Docker Contexts (proxy-node, dgx-node) ? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    docker context rm proxy-node dgx-node || true
fi

echo "✅ Nettoyage terminé."
