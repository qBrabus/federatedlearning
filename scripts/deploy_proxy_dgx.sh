#!/usr/bin/env bash
# Automatisation du déploiement orchestrateur (proxy) + client (DGX)
# à lancer depuis votre poste avec une configuration SSH fonctionnelle.
#
# Par défaut, les hôtes SSH attendus sont nommés "PROXY" et "DGX"
# (cf. votre ~/.ssh/config fourni dans l'énoncé). Les chemins distants
# par défaut correspondent aux emplacements mentionnés :
#   - PROXY : /home/qladane/federated
#   - DGX   : /raid/workspace/qladane/federated
#
# Les étapes effectuées :
# 1. Cloner ou mettre à jour https://github.com/qBrabus/federatedlearning
#    sur les deux machines.
# 2. Copier les .env exemples (orchestrator côté proxy, client côté DGX).
# 3. Construire et lancer l'orchestrateur en mode certifs auto-signés.
# 4. Synchroniser le dossier certs/ depuis le proxy vers le DGX pour
#    partager la même autorité de certification (CA).
# 5. Construire et lancer le client DGX en réutilisant ces certificats.
#
# Usage :
#   ./scripts/deploy_proxy_dgx.sh
# Variables personnalisables :
#   PROXY_HOST, DGX_HOST, REPO_URL, PROXY_BASE, DGX_BASE
#
set -euo pipefail

PROXY_HOST=${PROXY_HOST:-PROXY}
DGX_HOST=${DGX_HOST:-DGX}
REPO_URL=${REPO_URL:-https://github.com/qBrabus/federatedlearning}
REPO_NAME=federatedlearning
PROXY_BASE=${PROXY_BASE:-/home/qladane/federated}
DGX_BASE=${DGX_BASE:-/raid/workspace/qladane/federated}

info() {
  echo "[deploy] $*" >&2
}

remote_exec() {
  local host="$1"
  local cmd="$2"
  info "${host}: $cmd"
  ssh "$host" "set -euo pipefail; $cmd"
}

clone_or_update() {
  local host="$1" base="$2"
  remote_exec "$host" "mkdir -p \"$base\" && cd \"$base\" && \
    if [ -d $REPO_NAME/.git ]; then \
      git -C $REPO_NAME fetch --all --tags && git -C $REPO_NAME pull --ff-only; \
    else \
      git clone $REPO_URL $REPO_NAME; \
    fi"
}

copy_env() {
  local host="$1" base="$2" env_path="$3" example_path="$4"
  remote_exec "$host" "cd \"$base/$REPO_NAME\" && cp -f \"$example_path\" \"$env_path\""
}

build_and_run() {
  local host="$1" base="$2" component="$3"
  remote_exec "$host" "cd \"$base/$REPO_NAME\" && ./build_docker_FL.sh $component --self-signed"
  remote_exec "$host" "cd \"$base/$REPO_NAME\" && ./run_docker_FL.sh $component --self-signed"
}

sync_certs_to_dgx() {
  local proxy_path="$PROXY_BASE/$REPO_NAME/certs"
  local dgx_path="$DGX_BASE/$REPO_NAME"

  info "Copie des certificats depuis $PROXY_HOST:$proxy_path vers $DGX_HOST:$dgx_path"

  tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' EXIT

  scp -r "${PROXY_HOST}:${proxy_path}" "$tmpdir/"
  scp -r "$tmpdir/certs" "${DGX_HOST}:${dgx_path}/"
}

info "Clonage/mise à jour du dépôt sur PROXY et DGX"
clone_or_update "$PROXY_HOST" "$PROXY_BASE"
clone_or_update "$DGX_HOST" "$DGX_BASE"

info "Copie des fichiers .env exemple"
copy_env "$PROXY_HOST" "$PROXY_BASE" orchestrator/.env orchestrator/.env.example
copy_env "$DGX_HOST" "$DGX_BASE" client/.env client/.env.example

info "Construction + run orchestrateur sur PROXY"
build_and_run "$PROXY_HOST" "$PROXY_BASE" orchestrator

info "Synchronisation des certificats (même CA pour tous)"
sync_certs_to_dgx

info "Construction + run client sur DGX en réutilisant les certificats"
build_and_run "$DGX_HOST" "$DGX_BASE" client

info "Déploiement terminé"
